import logging
import statistics
from dataclasses import dataclass, replace

from bootleg.detect.features import FeatureFrame, Player
from bootleg.detect.viewpoint import Profile, ViewGeometry, analyze_view

log = logging.getLogger(__name__)

# Body-lengths/sec that counts as "fully moving". Measured, not guessed:
# on the first real source the near player's speed runs p50=0.11, p75=0.25,
# p95=0.69, so 0.7 is where a genuine sprint to a wide ball saturates the
# term. The old value of 4.0 came from the synthetic fixtures (which hand
# every player v=2.0) and was ~6x too large, which collapsed the speed term
# to ~0.01 of its 0.27 possible contribution and left rallying frames
# scoring 0.408 against a 0.45 threshold -- so only the ~1 s window around
# an audio impact ever cleared it, and every clip came out one hit long.
MAX_SPEED = 0.7
MAX_HIT_RATE = 2.0  # impacts in the trailing second that counts as "full"

# PLACEHOLDER, not a validated default -- read
# docs/superpowers/plans/2026-08-20-camera-viewpoint-validation.md before
# touching this. 0.25 came from fitting against audio-impact clusters on the
# one real ground-level source (59 clusters, median 8.0 s, 59% coverage over
# the full 19.5-minute recording), which produced 61 intervals at median
# 7.6 s, 63% coverage on that same source -- a close match that looked like
# confirmation. (test_real_ground_footage_segments_into_rallies pins 16
# intervals, median 7.0 s, on the 4-minute fixture slice in
# tests/fixtures/ground_level_source01.jsonl -- a different, smaller corpus,
# not a contradiction.)
#
# The validation task then frame-inspected six of those 61 clips and found
# the fitting target itself was wrong: audio impacts fire at 0.62/s when
# nobody is playing on our court versus 0.65/s during a confirmed rally --
# the detector measures a busy multi-court venue, not this player.
# Confidence came out inverted as a result: the two clips that were clearly
# false (camera setup, camera teardown) scored 0.40 and 0.46, higher than
# the two clips that were clearly true (a serve, a rally) at 0.36 and 0.39.
# No threshold separates true from false on this footage. Subject mode ships
# enabled anyway as a deliberate decision -- do not re-fit against
# audio-impact clusters; any future refit needs footage labelled by
# something other than the audio detector itself.
SUBJECT_THRESHOLD = 0.25
SUBJECT_CLOSE_GAP_S = 2.0


@dataclass(frozen=True)
class SegmentParams:
    w_both: float = 1.0
    w_speed: float = 0.9
    w_lateral: float = 0.3
    w_hits: float = 0.7
    w_regularity: float = 0.4
    w_outside: float = 1.2

    # UNVALIDATED. This is the pair-camera threshold and no footage exists
    # that the pair model is actually correct for -- the only real source is
    # ground-level, where the "far player" was people on adjacent courts (see
    # docs/superpowers/specs/2026-08-20-camera-viewpoint-design.md). A 0.42
    # fitted against that source was fitted against strangers, so this stays
    # at its original value until genuine elevated footage exists.
    threshold: float = 0.45
    smooth_window_s: float = 1.0
    # 2.0, not 1.5: `both` going false zeroes the score outright, so any
    # frame where the far player is occluded -- by the net cord, by the near
    # player crossing in front, by a lunge that clips the box -- reads exactly
    # like the end of a rally. 2.0 s bridges those without merging separate
    # points, which are seconds apart at minimum. Also UNVALIDATED: the one
    # real source is ground-level, where far-player absence was never
    # occlusion at all (see the spec referenced above).
    close_gap_s: float = 2.0
    min_duration_s: float = 1.5
    pad_start_s: float = 0.3
    pad_end_s: float = 0.5

    # Which scoring shape to use. "pair" is the original two-player model.
    # "subject" is for a camera low enough that the far half of the court
    # collapses onto the horizon, where the second-largest box is not the
    # opponent but whoever is on the next court -- see
    # docs/superpowers/specs/2026-08-20-camera-viewpoint-design.md.
    profile: Profile = "pair"
    # Minimum box height that counts as your player, in subject mode. Derived
    # per source by analyze_view; 0.0 here because pair mode never reads it.
    subject_min_h: float = 0.0

    @property
    def weight_total(self) -> float:
        # Subject mode gates on presence instead of scoring it, so w_both is
        # not part of the sum. This is why the two profiles' thresholds are
        # not comparable numbers: 0.25 subject, 0.45 pair.
        if self.profile == "subject":
            return self.w_speed + self.w_lateral + self.w_hits + self.w_regularity
        # Not `base + self.w_both` reusing the sum above: float addition is
        # not associative, so re-associating this sum would shift every
        # pair-mode score by an ULP for no benefit. This term order matches
        # the original pair-mode expression bit-for-bit.
        return self.w_both + self.w_speed + self.w_lateral + self.w_hits + self.w_regularity


@dataclass(frozen=True)
class Interval:
    start_ms: int
    end_ms: int
    confidence: float


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (min(x, 1.0))


def _lateral_fraction(near: Player | None, prev_near: Player | None) -> float:
    """Horizontal share of the near player's displacement since the
    previous sampled frame.

    0 when the movement was purely toward/away from the camera
    (longitudinal, e.g. walking to the fence) or the player did not move
    at all; 1 when purely sideways (lateral, e.g. rallying). Position
    alone -- distance from frame centre -- cannot tell the two apart, only
    displacement between consecutive frames can: a player standing still
    at the sideline has no motion at all, let alone lateral motion.
    """
    if near is None or prev_near is None:
        return 0.0
    dx = abs(near.cx - prev_near.cx)
    dy = abs(near.foot - prev_near.foot)
    total = dx + dy
    if total < 1e-9:
        # No meaningful displacement -- a stationary player has no
        # direction, so the term is 0, not a division by zero.
        return 0.0
    return dx / total


def _subject_score(f: FeatureFrame, p: SegmentParams, lateral: float) -> float:
    """Score a frame where only your own player is reliably visible.

    Presence is a gate, not a term. Your player is on court 83% of the time on
    real footage -- including between points, walking to the baseline, picking
    up balls -- so an additive presence term handed out a third of the score
    for free on nearly every frame. As a gate it earns nothing and the audio
    and motion terms have to carry the frame on their own.

    The gate also replaces the `if not both` guard that keeps audio from
    segmenting an empty court: a box has to be your player's size before any
    impact counts, and people on adjacent courts sit at the horizon at roughly
    a quarter of that height.
    """
    if f.near is None or f.near.h < p.subject_min_h:
        return 0.0

    speed = _clamp01(f.near.v / MAX_SPEED)
    hits = _clamp01(f.hits / MAX_HIT_RATE)
    reg = _clamp01(f.hit_reg)

    score = (
        p.w_speed * speed
        + p.w_lateral * lateral
        + p.w_hits * hits
        + p.w_regularity * reg
    )
    return _clamp01(score / p.weight_total)


def _raw_score(f: FeatureFrame, p: SegmentParams, lateral: float) -> float:
    if p.profile == "subject":
        return _subject_score(f, p, lateral)

    both = 1.0 if (f.near is not None and f.far is not None) else 0.0

    if both:
        slower = min(f.near.v, f.far.v)
        speed = _clamp01(slower / MAX_SPEED)
    else:
        speed = 0.0
        lateral = 0.0

    hits = _clamp01(f.hits / MAX_HIT_RATE)
    reg = _clamp01(f.hit_reg)

    # Audio alone must never carry a frame — an adjacent court would segment.
    if not both:
        hits = 0.0
        reg = 0.0

    outside = 1.0 if f.n > 0 and not both else 0.0

    score = (
        p.w_both * both
        + p.w_speed * speed
        + p.w_lateral * lateral
        + p.w_hits * hits
        + p.w_regularity * reg
        - p.w_outside * outside
    )
    return _clamp01(score / p.weight_total)


def sample_interval_ms(frames: list[FeatureFrame]) -> int:
    if len(frames) < 2:
        return 200
    return max(1, frames[1].t_ms - frames[0].t_ms)


def score_series(frames: list[FeatureFrame], params: SegmentParams) -> list[float]:
    """Raw per-frame score, smoothed with a rolling median."""
    raw = []
    prev_near: Player | None = None
    for f in frames:
        raw.append(_raw_score(f, params, _lateral_fraction(f.near, prev_near)))
        prev_near = f.near
    if not raw:
        return []

    step = sample_interval_ms(frames)
    half = max(0, int((params.smooth_window_s * 1000) / step) // 2)
    if half == 0:
        return raw

    out = []
    for i in range(len(raw)):
        lo = max(0, i - half)
        hi = min(len(raw), i + half + 1)
        out.append(statistics.median(raw[lo:hi]))
    return out


def segment(frames: list[FeatureFrame], params: SegmentParams) -> list[Interval]:
    if not frames:
        return []

    scores = score_series(frames, params)
    step = sample_interval_ms(frames)

    # 1. threshold to runs of indices
    runs: list[list[int]] = []
    current: list[int] = []
    for i, s in enumerate(scores):
        if s >= params.threshold:
            current.append(i)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    if not runs:
        return []

    # 2. close short gaps
    gap_samples = params.close_gap_s * 1000 / step
    merged: list[list[int]] = [runs[0]]
    for run in runs[1:]:
        if run[0] - merged[-1][-1] - 1 <= gap_samples:
            merged[-1] = merged[-1] + run
        else:
            merged.append(run)

    # 3. drop shorts, 4. pad, 5. score
    min_samples = params.min_duration_s * 1000 / step
    pad_start = int(params.pad_start_s * 1000)
    pad_end = int(params.pad_end_s * 1000)
    last_t = frames[-1].t_ms + step

    out: list[Interval] = []
    for run in merged:
        span = run[-1] - run[0] + 1
        if span < min_samples:
            continue
        start = frames[run[0]].t_ms
        end = frames[run[-1]].t_ms + step
        confidence = sum(scores[i] for i in run) / len(run)
        out.append(Interval(
            start_ms=max(0, start - pad_start),
            end_ms=min(last_t, end + pad_end),
            confidence=round(confidence, 4),
        ))
    return out


def params_for_frames(
    frames: list[FeatureFrame], *, threshold: float | None = None
) -> SegmentParams:
    """Build the right SegmentParams for a source, from the source itself.

    Every caller that segments -- the detect handler, the CLI, both API routes
    -- goes through here, for the same reason setup.py::queue_setup exists:
    HTTP and terminal must not be able to drift on which model a source gets.

    `threshold=None` means "use the profile's default", which is what the UI
    wants on first load; the two profiles' thresholds are on different scales
    and hardcoding either one in a caller is a bug.
    """
    view: ViewGeometry = analyze_view(frames)
    if view.low_confidence:
        # analyze_view sets this for two independent reasons, and only the
        # two counts it returns tell them apart: too few frames carried a
        # near-player box to say a far player is genuinely absent
        # (frames_measured under MIN_FRAMES_FOR_CONFIDENCE), or a far player
        # did show up but too rarely for the paired-frame median to be
        # trusted (pairs_measured under MIN_PAIRS_FOR_CONFIDENCE, which can
        # fire even with frames_measured well past its own floor). Either
        # way analyze_view assumed subject mode as the safe default, and
        # either way it can also mean a wrong court quad: with the play
        # region misplaced, near/far boxes go missing for the same reason,
        # so a source landing here is worth a human glance at its preset,
        # not just a shrug that it happens to be ground-level footage.
        log.warning(
            "low-confidence viewpoint classification (%d frames carried a "
            "near-player box, %d of those were paired with a far box); "
            "assuming subject mode -- check the source's court quad if it "
            "is not actually ground-level footage",
            view.frames_measured,
            view.pairs_measured,
        )
    if view.profile == "subject":
        params = SegmentParams(
            profile="subject",
            subject_min_h=view.subject_min_h,
            threshold=SUBJECT_THRESHOLD,
            close_gap_s=SUBJECT_CLOSE_GAP_S,
        )
    else:
        params = SegmentParams()
    # Profile choice changes segment() output more than any weight does, and
    # it is picked per-source from footage the operator never looks at
    # directly -- so a detect job's log needs to say which model it used and
    # why, not just that it ran. foot_separation is the number the pair/
    # subject decision turns on; subject_min_h is the derived gate that only
    # matters when the decision comes out "subject".
    log.info(
        "segmenting with profile=%s foot_separation=%.4f subject_min_h=%.4f",
        params.profile,
        view.foot_separation,
        params.subject_min_h,
    )
    if threshold is not None:
        params = replace(params, threshold=threshold)
    return params
