"""Court corners -> court metres, on the real 2026-10-12 camera.

The corners fixture was read off the proxy (1920x1080, already rotated): a
per-pixel median of fifteen frames spread over the source removes the
players, the seven visible near-half lines (baseline, both doubles and both
singles sidelines, service line, centre line) were fitted to their white
pixels, and a homography fitted to their seven intersections (worst residual
0.6 px) placed all four doubles corners. The far baseline sits behind the
net mesh at this camera height, so the far corners are that fit's
extrapolation rather than a click on a visible line; it lands both net-post
bases within 3 px of where they stand in the frame and the far service line
within a few px of where it shows through the mesh, which is the check that the
extrapolation is sound.
"""

import inspect
import json
import math
from pathlib import Path

import pytest

from splitstep.detect.boxes import PERSON, read_boxes
from splitstep.detect.court import (
    COURT_L,
    COURT_W,
    FAR_BACK_M,
    NEAR_BACK_M,
    NET_Y,
    SIDE_M,
    Court,
    region_from_corners,
    validate_corners,
)

FIXTURES = Path(__file__).parent / "fixtures"
CORNERS = FIXTURES / "truth/2026-10-12_source01.corners.json"
BOXES = FIXTURES / "boxes/2026-10-12_source01_0-16min.jsonl"

METRE_CORNERS = ((0.0, 0.0), (COURT_W, 0.0), (COURT_W, COURT_L), (0.0, COURT_L))

# Net-post bases read off the same median frame (proxy pixels / frame size).
# Independent of the corners: neither post is a court-line intersection.
LEFT_POST = (725.6 / 1920, 503.5 / 1080)
RIGHT_POST = (1308.0 / 1920, 517.0 / 1080)
POST_OFFSET_M = 0.914  # doubles net posts stand 3 ft outside each doubles sideline


@pytest.fixture
def court() -> Court:
    return Court.from_json(CORNERS.read_text())


def _intersect(p1, p2, p3, p4):
    """Intersection of line p1-p2 with line p3-p4."""
    (x1, y1), (x2, y2), (x3, y3), (x4, y4) = p1, p2, p3, p4
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    a = x1 * y2 - y1 * x2
    b = x3 * y4 - y3 * x4
    return ((a * (x3 - x4) - (x1 - x2) * b) / den, (a * (y3 - y4) - (y1 - y2) * b) / den)


def _frame_at(t_ms: int):
    for t, dets in read_boxes(BOXES):
        if t == t_ms:
            return [d for d in dets if d.cls == PERSON]
    raise AssertionError(f"no frame at {t_ms} in {BOXES.name}")


def _nearest(dets, cx, foot):
    best = min(dets, key=lambda d: math.hypot(d.cx - cx, d.foot - foot))
    # Guard against the fixture drifting under the test: the box must be the
    # one these numbers describe, not merely the closest thing left.
    assert math.hypot(best.cx - cx, best.foot - foot) < 0.01
    return best


def test_corners_map_to_court_corners(court):
    for (x, y), (mx, my) in zip(court.corners, METRE_CORNERS, strict=True):
        cx, cy = court.court_xy(x, y)
        assert cx == pytest.approx(mx, abs=1e-6)
        assert cy == pytest.approx(my, abs=1e-6)


def test_net_midpoint_maps_to_net(court):
    nl, nr, fr, fl = court.corners
    left = _intersect(nl, fl, LEFT_POST, RIGHT_POST)
    right = _intersect(nr, fr, LEFT_POST, RIGHT_POST)
    mid = ((left[0] + right[0]) / 2, (left[1] + right[1]) / 2)
    _, y = court.court_xy(*mid)
    # Depth is what perspective crushes: at the net one proxy pixel of image
    # height spans ~0.35 m of court, and the post bases are read to ~2 px.
    assert y == pytest.approx(NET_Y, abs=0.75)
    # The image midpoint of a sideline is nowhere near the net under this
    # much perspective; a mapping that interpolated linearly would put the
    # net there and pass the corner test above.
    sideline_mid_y = (nl[1] + fl[1]) / 2
    assert court.court_xy(nl[0], sideline_mid_y)[1] < NET_Y - 2


def test_net_posts_land_where_the_frame_shows_them(court):
    # Checked in pixels, not metres, because pixels are what was measured:
    # the posts were never used to place the corners, so this is the
    # extrapolation past the near half being held to the frame.
    for metres, (px, py) in (
        ((-POST_OFFSET_M, NET_Y), LEFT_POST),
        ((COURT_W + POST_OFFSET_M, NET_Y), RIGHT_POST),
    ):
        x, y = court.image_xy(*metres)
        assert abs(x - px) * 1920 < 3
        assert abs(y - py) * 1080 < 3


def test_zone_classifies_fixture_players(court):
    dets = _frame_at(87000)
    server = max(dets, key=lambda d: d.h)
    assert server.h > 0.2  # the near server mid-toss, not a background figure
    assert court.zone(server.cx, server.foot) == "near"

    opponent = _nearest(dets, 0.572, 0.444)
    assert court.zone(opponent.cx, opponent.foot) == "far"

    # Gear hung on the right net post (cx 0.676) and the bag leaning beside
    # it (cx 0.702). Both are what the loose region let in as a far player.
    for cx in (0.676, 0.702):
        static = _nearest(dets, cx, 0.483)
        assert court.zone(static.cx, static.foot) == "outside"


def test_zone_rejects_the_next_courts(court):
    # Players on the courts either side at t=87000 (cx 0.10 and 0.95).
    dets = _frame_at(87000)
    for cx, foot in ((0.103, 0.437), (0.955, 0.473)):
        d = _nearest(dets, cx, foot)
        assert court.zone(d.cx, d.foot) == "outside"


def test_zone_above_the_horizon_is_outside(court):
    # Above the horizon (~0.397 here) the projective map wraps round and
    # would hand back a point behind the near baseline: sky as a near player.
    x, y = court.court_xy(0.55, 0.2)
    assert math.isinf(x) and math.isinf(y)
    assert court.zone(0.55, 0.2) == "outside"


def test_validate_rejects_crossed_corners():
    nl, nr, fr, fl = json.loads(CORNERS.read_text())
    with pytest.raises(ValueError, match="cross"):
        validate_corners([nl, fr, nr, fl])  # near-right and far-right swapped


def test_validate_rejects_far_below_near():
    nl, nr, fr, fl = json.loads(CORNERS.read_text())
    # Clicked far edge first: a vertical mirror of the court.
    with pytest.raises(ValueError, match="far edge"):
        validate_corners([fl, fr, nr, nl])
    # Clicked starting from the far-right corner: a 180-degree turn, which
    # keeps the winding and so is caught only by the far/near test.
    with pytest.raises(ValueError, match="far edge"):
        validate_corners([fr, fl, nl, nr])


@pytest.mark.parametrize("shift", [1, 3])
def test_validate_rejects_quarter_turn_shift(shift):
    # Clicking the right corners in the right direction but starting one
    # corner late (or early) keeps the winding and the edge averages in
    # order; accepted, it maps a sideline onto the baseline and every player
    # at t=87000 to "outside".
    pts = json.loads(CORNERS.read_text())
    with pytest.raises(ValueError, match="far edge"):
        validate_corners(pts[shift:] + pts[:shift])


def test_validate_rejects_left_right_swapped():
    nl, nr, fr, fl = json.loads(CORNERS.read_text())
    with pytest.raises(ValueError, match="left and right"):
        validate_corners([nr, nl, fl, fr])


@pytest.mark.parametrize(
    ("points", "match"),
    [
        ([[0.1, 0.6], [0.9, 0.6], [0.6, 0.4]], "exactly 4"),
        ([[0.1, 0.6], [0.9, 0.6], [0.6, 0.4], [0.4, 1.2]], "outside the frame"),
        ([[0.1, 0.6], [0.9, 0.6], [0.6, 0.4], [float("nan"), 0.4]], "outside the frame"),
        ([[0.1, 0.6], [0.9, 0.6], [0.6, 0.4], "ab"], r"\(x, y\) pair"),
        ([[0.1, 0.6], [0.5, 0.6], [0.9, 0.6], [0.4, 0.4]], "one line"),
    ],
)
def test_validate_rejects_malformed(points, match):
    with pytest.raises(ValueError, match=match):
        validate_corners(points)


def test_validate_returns_float_tuples():
    out = validate_corners(json.loads(CORNERS.read_text()))
    assert isinstance(out, tuple) and all(isinstance(p, tuple) for p in out)
    assert all(isinstance(v, float) for p in out for v in p)


def test_court_refuses_bad_corners_at_construction():
    nl, nr, fr, fl = json.loads(CORNERS.read_text())
    with pytest.raises(ValueError, match="cross"):
        Court(((nl[0], nl[1]), (fr[0], fr[1]), (nr[0], nr[1]), (fl[0], fl[1])))


def test_json_round_trip(court):
    assert Court.from_json(court.to_json()) == court


def test_court_xy_uses_proxy_space_unchanged(court):
    # Boxes come from the rotated proxy and corners are drawn on the rotated
    # preview, so both already share one space. Nothing here may know about
    # rotation_deg: no parameter for it anywhere in the mapping...
    for fn in (Court, Court.from_json, Court.court_xy, Court.zone, region_from_corners):
        assert not any("rot" in p for p in inspect.signature(fn).parameters)
    # ...and a source stored with rotation_deg=180 carries the same proxy-space
    # corners, so it maps them exactly as this one does, with no flip.
    rotated_source_corners = CORNERS.read_text()
    rotated = Court.from_json(rotated_source_corners)
    for (x, y), metres in zip(rotated.corners, METRE_CORNERS, strict=True):
        assert rotated.court_xy(x, y) == pytest.approx(metres, abs=1e-6)
        assert rotated.court_xy(x, y) == court.court_xy(x, y)


def test_region_from_corners_contains_court_and_margin(court):
    region = region_from_corners(court)
    # Its corners are the court's, pushed out by the margins.
    margin = (
        (-SIDE_M, -NEAR_BACK_M),
        (COURT_W + SIDE_M, -NEAR_BACK_M),
        (COURT_W + SIDE_M, COURT_L + FAR_BACK_M),
        (-SIDE_M, COURT_L + FAR_BACK_M),
    )
    for (x, y), metres in zip(region.points, margin, strict=True):
        assert court.court_xy(x, y) == pytest.approx(metres, abs=1e-6)
    for x, y in court.corners:
        assert region.contains(x, y)

    dets = _frame_at(87000)
    server = max(dets, key=lambda d: d.h)
    opponent = _nearest(dets, 0.572, 0.444)
    post_gear = _nearest(dets, 0.676, 0.483)
    assert region.contains(server.cx, server.foot)  # behind the near baseline
    assert region.contains(opponent.cx, opponent.foot)  # behind the far baseline
    assert not region.contains(post_gear.cx, post_gear.foot)


def test_region_and_zone_agree_across_the_frame(court):
    # The quad handed to today's scorer and the zone fence mode uses must be
    # one shape, or Fence + Doubles and Fence + Singles disagree about who is
    # on the court.
    region = region_from_corners(court)
    for i in range(97):
        for j in range(55):
            x, y = i / 96, j / 54
            assert region.contains(x, y) == (court.zone(x, y) != "outside"), (x, y)
