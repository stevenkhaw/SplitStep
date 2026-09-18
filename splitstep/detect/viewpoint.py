import statistics
from dataclasses import dataclass
from typing import Literal

from splitstep.detect.features import FeatureFrame

# Median |near.foot - far.foot| below which the two largest boxes are sharing
# a horizon line rather than standing at different depths. A camera propped a
# foot off the ground measures 0.006 (p90 0.010) on real footage; an elevated
# camera puts the far player near the service line, 0.15-0.35 away. 0.05 is
# the midpoint of the empty band between them -- 5x the observed ground-level
# p90, and 3x below the expected elevated minimum.
GROUND_FOOT_SEPARATION = 0.05

# Below this many frames carrying a near box, there is not enough footage to
# tell "far player genuinely never appears" (a confident subject-mode signal)
# from "too short a clip to know either way." Only gates the no-pairs case
# below -- it says nothing about whether a *measured* separation can be
# trusted, which is what MIN_PAIRS_FOR_CONFIDENCE is for.
MIN_FRAMES_FOR_CONFIDENCE = 50

# Below this many paired observations, the median separation the classifier
# actually decides on is noise. The whole decision is a median over paired
# frames, so how many pairs exist -- not how many near-boxes exist -- is what
# determines whether that median can be trusted. Gating on near-box count
# instead (MIN_FRAMES_FOR_CONFIDENCE) measures a different quantity: 40 clean
# paired samples at an unambiguous separation are plenty to classify even
# though 40 is below that near-box floor of 50. 20 is 4 s of paired
# observation at 5 fps -- short, but long enough that a real elevated-camera
# separation (0.15-0.35) or a real ground-level one (~0.006) will already
# dominate a handful of noisy frames.
MIN_PAIRS_FOR_CONFIDENCE = 20

# Fraction of frames carrying a near-player box that must ALSO carry a far
# box before `pair` is allowed, however deep the separation in those frames
# looks.
#
# Pair mode assumes two players rallying across the net, and in that state
# both are visible essentially always: the three genuinely two-player sources
# in the library measure 100%, 99% and 90%. A vertical phone framing breaks
# the assumption without touching the separation -- whenever both players ARE
# in shot they are at different depths, so the paired frames look like
# textbook pair footage, but they are a third of the frames carrying anyone.
# 2026-09-16 source 01 measures 34% and was classified pair on the strength
# of that third, which left it scoring a hard 0.000 across four of the seven
# windows a human said held play.
#
# 0.5 states something meaningful rather than splitting an observed gap: a
# source where a pair is present less often than not is not two-player
# footage in any sense the profile can use. It happens to sit in a wide empty
# band (34% against 100%), but that band is two data points and should not be
# mistaken for a fitted boundary -- see
# docs/superpowers/plans/2026-09-18-first-measured-recall.md.
#
# The denominator is frames carrying a near box, not every frame. An empty
# court between points is not evidence against pairing, and pair mode scores
# those frames zero correctly. The frames that matter are the ones where a
# player IS visible and unpaired -- exactly where the profile drops a rally.
MIN_PAIR_RATE = 0.5

SUBJECT_H_FRACTION = 0.5
MIN_SUBJECT_H = 0.02

Profile = Literal["pair", "subject"]


@dataclass(frozen=True)
class ViewGeometry:
    profile: Profile
    foot_separation: float
    subject_min_h: float
    frames_measured: int
    pairs_measured: int
    low_confidence: bool
    # pairs_measured / frames_measured: how often a far player is there at
    # all, as opposed to how far away they are when they are. Carried so the
    # detect log can say which of the two reasons decided the profile.
    pair_rate: float = 0.0


def analyze_view(frames: list[FeatureFrame]) -> ViewGeometry:
    """Classify a source's camera viewpoint from its own feature stream.

    Pure and cheap (~1 ms), so it is recomputed on every segment run rather
    than stored: no migration, and the re-segment slider stays instant.

    The discriminator is vertical separation, not box size. Size ratio also
    varies with camera height, but it varies with lens and court length too;
    where the feet land is a direct consequence of the camera's height above
    the surface and nothing else.
    """
    near_heights = [f.near.h for f in frames if f.near is not None]
    subject_min_h = max(
        SUBJECT_H_FRACTION * statistics.median(near_heights) if near_heights else 0.0,
        MIN_SUBJECT_H,
    )
    measured = len(near_heights)

    pairs = [(f.near, f.far) for f in frames
             if f.near is not None and f.far is not None]

    if not pairs:
        # A far player that is never seen at all is not a low-confidence
        # reading -- it is the clearest possible subject-mode signal. Unless
        # the source is too short to have shown one yet: MIN_FRAMES_FOR_CONFIDENCE
        # frames is the line between "genuinely absent" and "haven't looked
        # long enough to say."
        return ViewGeometry(
            "subject", 0.0, subject_min_h, measured, 0,
            measured < MIN_FRAMES_FOR_CONFIDENCE, 0.0
        )

    pair_rate = len(pairs) / measured if measured else 0.0

    if len(pairs) < MIN_PAIRS_FOR_CONFIDENCE:
        # Pairs exist but too few to trust their median -- distinct from the
        # no-pairs case above, which is a confident reading in its own right.
        return ViewGeometry(
            "subject", 0.0, subject_min_h, measured, len(pairs), True, pair_rate
        )

    separation = statistics.median(abs(n.foot - f.foot) for n, f in pairs)
    # Two independent conditions, and the rate is a veto rather than a vote:
    # it can only take `pair` away, never grant it. A ground-level camera
    # pairs constantly -- both players share one horizon line -- and must
    # still classify subject, so separation remains what decides and the rate
    # only asks whether that decision describes enough of the footage to
    # apply to all of it.
    deep_enough = separation >= GROUND_FOOT_SEPARATION
    paired_often_enough = pair_rate >= MIN_PAIR_RATE
    profile: Profile = "pair" if (deep_enough and paired_often_enough) else "subject"
    # Not low_confidence: there is plenty of observation here and it says
    # something definite -- this is not two-player footage. low_confidence
    # means "not enough observation to say", which is a different state.
    return ViewGeometry(
        profile, round(separation, 4), subject_min_h, measured, len(pairs), False, pair_rate
    )
