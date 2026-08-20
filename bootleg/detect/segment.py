import statistics
from dataclasses import dataclass

from bootleg.detect.features import FeatureFrame, Player

MAX_SPEED = 4.0   # body-lengths/sec that counts as "fully moving"
MAX_HIT_RATE = 2.0  # impacts in the trailing second that counts as "full"


@dataclass(frozen=True)
class SegmentParams:
    w_both: float = 1.0
    w_speed: float = 0.9
    w_lateral: float = 0.3
    w_hits: float = 0.7
    w_regularity: float = 0.4
    w_outside: float = 1.2

    threshold: float = 0.45
    smooth_window_s: float = 1.0
    close_gap_s: float = 1.5
    min_duration_s: float = 1.5
    pad_start_s: float = 0.3
    pad_end_s: float = 0.5

    @property
    def weight_total(self) -> float:
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


def _raw_score(f: FeatureFrame, p: SegmentParams, lateral: float) -> float:
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
