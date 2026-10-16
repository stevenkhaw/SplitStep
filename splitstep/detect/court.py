"""Four court corners -> a homography between proxy space and court metres.

Fence mode asks "near half, far half, or not our court?" of every person box.
The 2026-10-16 spike answered it with hand-picked image bands that fit one
clip-on position; four corners the reviewer drags onto the doubles court
lines answer it for any position, because a homography from those corners
puts every foot point into metres on the court plane.

Everything here is in normalized (0-1) **proxy** space. Boxes come from the
proxy, which is already rotated, and the corners are drawn on the rotated
preview, so the two share one space. Nothing in this module takes or applies
a rotation: re-applying `rotation_deg` to either would turn the court.

Court metres: x runs along the near baseline from the near-left doubles
corner (0) to the near-right one (COURT_W); y runs from the near baseline (0)
to the far baseline (COURT_L). Near-left/right are as seen from the camera.
"""

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from splitstep.detect.geometry import Quad

COURT_W = 10.97  # doubles width
COURT_L = 23.77  # baseline to baseline
NET_Y = COURT_L / 2  # 11.885

# Margins, in metres, of what still counts as our court around the lines.
#
# NEAR_BACK_M: in fence mode the camera hangs on our own back fence, so
# everything in frame behind the near baseline is our runoff -- there is no
# neighbouring court to exclude there. It has to reach the bottom of the
# frame (4.1-4.4 m behind the baseline on 2026-10-12), or a near player whose
# feet the frame clips reads as "outside" -- the spike's 4 m would do that to
# the largest near box in 158 of the fixture's 4156 frames.
# It also has to stop in front of the camera: ground behind the camera's own
# plane (6.3-6.9 m back here, about where the fence stands) goes through
# infinity under the homography and would come back mirrored. 5 m sits
# between the two with room either side for the next clip-on position.
#
# FAR_BACK_M: the spike's band, about 4 m behind the far baseline. A returner
# stands 1-3 m back; at this distance a metre is a couple of pixels, so the
# margin costs nothing in clutter.
#
# SIDE_M: deliberately far tighter than the spike's ~1.5 m. The net posts
# stand 0.914 m outside each doubles sideline, and on 2026-10-12 the gear
# hung on the right post maps to 0.25-0.9 m outside the line. Over the
# 16-minute fixture, widening 0.2 -> 1.5 m admits 334 more person boxes
# (conf >= 0.25) into the far half, nearly all that post cluster -- the
# identity-swap clutter the spec measured -- while keeping the largest near
# box on court in only 0.75% more frames. 0.2 m still admits a foot planted
# on the line.
NEAR_BACK_M = 5.0
FAR_BACK_M = 4.0
SIDE_M = 0.2

# Fixed storage order, matching sources.court_corners (migration 018).
CORNER_NAMES = ("near-left", "near-right", "far-right", "far-left")
_METRE_CORNERS = ((0.0, 0.0), (COURT_W, 0.0), (COURT_W, COURT_L), (0.0, COURT_L))

_ORDER_HINT = "place them in order near-left, near-right, far-right, far-left"


def validate_corners(points) -> tuple[tuple[float, float], ...]:
    """Four proxy-space corners in storage order, or a ValueError sentence.

    Refuses rather than reorders. A homography is defined for any four points
    in general position, including crossed or mirrored ones, and solves to a
    map that is exactly right at the four corners and wrong everywhere else
    (left and right swapped, or near and far) -- nothing downstream could
    tell. The wizard labels every corner, so an out-of-order set is a mistake
    to show the reviewer, not something to guess about.
    """
    try:
        pts = list(points)
    except TypeError:
        raise ValueError("Court corners must be a list of 4 (x, y) points.") from None
    if len(pts) != 4:
        raise ValueError(
            f"Court corners need exactly 4 points ({', '.join(CORNER_NAMES)}), got {len(pts)}."
        )
    out = []
    for name, p in zip(CORNER_NAMES, pts, strict=True):
        try:
            px, py = p
            x, y = float(px), float(py)
        except (TypeError, ValueError):
            raise ValueError(f"The {name} corner must be an (x, y) pair, got {p!r}.") from None
        # `not (0 <= v <= 1)` rather than `v < 0 or v > 1`, so NaN fails too.
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ValueError(
                f"The {name} corner is at ({x}, {y}), outside the frame; "
                "corners are normalized to 0-1."
            )
        out.append((x, y))

    # z of the cross product at each vertex. Image y points down, so the
    # stored order (near-left at bottom-left, round to far-left) turns the
    # same way at every corner and every z is negative. A court seen in
    # perspective is still a convex quad, so anything else is a mistake.
    turns = []
    for i in range(4):
        (ax, ay), (bx, by), (cx, cy) = out[i - 1], out[i], out[(i + 1) % 4]
        turns.append((bx - ax) * (cy - by) - (by - ay) * (cx - bx))
    if any(abs(z) < 1e-9 for z in turns):
        raise ValueError(
            "Three of the court corners lie on one line; " + _ORDER_HINT + " on the court."
        )
    if any(z > 0 for z in turns) and any(z < 0 for z in turns):
        raise ValueError(
            "The court corners cross over each other or fold inward; " + _ORDER_HINT + "."
        )

    near_y = (out[0][1] + out[1][1]) / 2
    far_y = (out[2][1] + out[3][1]) / 2
    if not far_y < near_y:
        raise ValueError(
            "The far edge of the court (far-right, far-left) must sit above the near "
            "edge (near-left, near-right) in the frame; " + _ORDER_HINT + "."
        )
    if turns[0] > 0:
        raise ValueError(
            "The court corners have left and right swapped, which would mirror the "
            "court; " + _ORDER_HINT + ", left and right as seen from the camera."
        )
    return tuple(out)


def _solve_homography(src, dst) -> np.ndarray:
    """The 3x3 H with H @ (x, y, 1) ~ (u, v, 1) for four exact correspondences.

    DLT with h33 fixed at 1: eight unknowns, two equations per point. h33 is
    0 only when the map sends src's origin to infinity, so this is only ever
    solved court -> image, where the origin is the near-left corner and lands
    on a validated (finite, in-frame) point. The image -> court direction is
    its inverse rather than a second solve: there the origin is the frame's
    top-left pixel, which nothing stops from sitting on the horizon.
    """
    a = np.zeros((8, 8))
    b = np.zeros(8)
    for k, ((x, y), (u, v)) in enumerate(zip(src, dst, strict=True)):
        a[2 * k] = [x, y, 1, 0, 0, 0, -u * x, -u * y]
        a[2 * k + 1] = [0, 0, 0, x, y, 1, -v * x, -v * y]
        b[2 * k], b[2 * k + 1] = u, v
    return np.append(np.linalg.solve(a, b), 1.0).reshape(3, 3)


def _oriented(h: np.ndarray, inside) -> np.ndarray:
    """Scale h so the homogeneous w is positive at a point known to be on court.

    w is zero on the horizon and changes sign across it. Fixing its sign on
    the court side is what lets `_apply` tell a point beyond the horizon --
    which the projective map would otherwise wrap round to a plausible-looking
    spot behind the near baseline -- from a real one.
    """
    w = h[2] @ np.array([inside[0], inside[1], 1.0])
    return h if w > 0 else -h


def _apply(h: np.ndarray, x: float, y: float) -> tuple[float, float]:
    u, v, w = h @ np.array([x, y, 1.0])
    if w <= 1e-12:
        return (math.inf, math.inf)
    return (float(u / w), float(v / w))


@dataclass(frozen=True)
class Court:
    """Four doubles corners in proxy space: near-left, near-right, far-right, far-left."""

    corners: tuple[tuple[float, float], ...]
    _to_court: np.ndarray = field(init=False, repr=False, compare=False)
    _to_image: np.ndarray = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        corners = validate_corners(self.corners)
        object.__setattr__(self, "corners", corners)
        cx = sum(p[0] for p in corners) / 4
        cy = sum(p[1] for p in corners) / 4
        to_image = _solve_homography(_METRE_CORNERS, corners)
        to_court = np.linalg.inv(to_image)
        object.__setattr__(self, "_to_court", _oriented(to_court, (cx, cy)))
        object.__setattr__(self, "_to_image", _oriented(to_image, (COURT_W / 2, NET_Y)))

    @classmethod
    def from_json(cls, s: str) -> "Court":
        return cls(tuple(tuple(p) for p in json.loads(s)))

    def to_json(self) -> str:
        return json.dumps([list(p) for p in self.corners])

    def court_xy(self, x: float, y: float) -> tuple[float, float]:
        """Proxy-space point -> court-plane metres.

        A point on or above the horizon has no place on the court plane and
        comes back as (inf, inf): infinitely far, which every margin test
        reads as outside. Callers wanting finite numbers filter by `zone`.
        """
        return _apply(self._to_court, x, y)

    def image_xy(self, mx: float, my: float) -> tuple[float, float]:
        """Court-plane metres -> proxy-space point; the inverse of court_xy."""
        return _apply(self._to_image, mx, my)

    def zone(self, x: float, y: float) -> Literal["near", "far", "outside"]:
        mx, my = self.court_xy(x, y)
        if not (-SIDE_M <= mx <= COURT_W + SIDE_M):
            return "outside"
        if not (-NEAR_BACK_M <= my <= COURT_L + FAR_BACK_M):
            return "outside"
        return "near" if my < NET_Y else "far"


def region_from_corners(court: Court) -> Quad:
    """The court plus its margins as a play-region quad in proxy space.

    What Fence + Doubles / Drills hand today's scorer, so it and `zone` must
    be one shape: the margin rectangle in metres, mapped back through the
    same homography. The points may fall outside 0-1 -- the near sidelines
    diverge out of the bottom corners of a fence-mounted frame -- and are
    returned as-is rather than clamped, because clamping one corner of a quad
    is not clipping it: it would change which in-frame points it contains.
    """
    margin: Sequence[tuple[float, float]] = (
        (-SIDE_M, -NEAR_BACK_M),
        (COURT_W + SIDE_M, -NEAR_BACK_M),
        (COURT_W + SIDE_M, COURT_L + FAR_BACK_M),
        (-SIDE_M, COURT_L + FAR_BACK_M),
    )
    points = [court.image_xy(mx, my) for mx, my in margin]
    if any(math.isinf(p[0]) for p in points):
        raise ValueError(
            f"The play region's margins ({FAR_BACK_M:g} m behind the far baseline, "
            f"{NEAR_BACK_M:g} m behind the near one) run past the horizon or behind the "
            "camera from these corners; check that they sit on the doubles court lines."
        )
    return Quad(tuple(points))
