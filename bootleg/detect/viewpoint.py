import statistics
from dataclasses import dataclass
from typing import Literal

from bootleg.detect.features import FeatureFrame

# Median |near.foot - far.foot| below which the two largest boxes are sharing
# a horizon line rather than standing at different depths. A camera propped a
# foot off the ground measures 0.006 (p90 0.010) on real footage; an elevated
# camera puts the far player near the service line, 0.15-0.35 away. 0.05 is
# the midpoint of the empty band between them -- 5x the observed ground-level
# p90, and 3x below the expected elevated minimum.
GROUND_FOOT_SEPARATION = 0.05

# Below this many frames carrying a near box, the median is noise. Subject is
# the safe default: it gates on your own player's box size, so guessing wrong
# cannot manufacture rallies out of people on the next court.
MIN_FRAMES_FOR_CONFIDENCE = 50

SUBJECT_H_FRACTION = 0.5
MIN_SUBJECT_H = 0.02

Profile = Literal["pair", "subject"]


@dataclass(frozen=True)
class ViewGeometry:
    profile: Profile
    foot_separation: float
    subject_min_h: float
    frames_measured: int
    low_confidence: bool


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

    if measured < MIN_FRAMES_FOR_CONFIDENCE:
        return ViewGeometry("subject", 0.0, subject_min_h, measured, True)

    pairs = [(f.near, f.far) for f in frames
             if f.near is not None and f.far is not None]
    if not pairs:
        # A far player that is never seen at all is not a low-confidence
        # reading -- it is the clearest possible subject-mode signal.
        return ViewGeometry("subject", 0.0, subject_min_h, measured, False)

    separation = statistics.median(abs(n.foot - f.foot) for n, f in pairs)
    profile: Profile = "subject" if separation < GROUND_FOOT_SEPARATION else "pair"
    return ViewGeometry(profile, round(separation, 4), subject_min_h, measured, False)
