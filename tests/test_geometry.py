import pytest

from splitstep.detect.geometry import Quad


@pytest.fixture
def unit_quad():
    return Quad(((0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)))


def test_contains_point_inside(unit_quad):
    assert unit_quad.contains(0.5, 0.5) is True


def test_contains_point_outside(unit_quad):
    assert unit_quad.contains(0.1, 0.5) is False
    assert unit_quad.contains(0.5, 0.95) is False


def test_contains_handles_trapezoid():
    # narrow at the top (far baseline), wide at the bottom (near baseline)
    q = Quad(((0.4, 0.3), (0.6, 0.3), (0.95, 1.0), (0.05, 1.0)))
    assert q.contains(0.5, 0.35) is True
    assert q.contains(0.1, 0.35) is False
    assert q.contains(0.1, 0.95) is True


def test_contains_trapezoid_sloped_edge_is_inclusive():
    # The play region is a trapezoid in practice (the court converges
    # toward the far baseline), so boundary inclusion has to hold on
    # sloped edges too, not just axis-aligned ones. This point sits
    # exactly on the right side of the trapezoid, which slopes from
    # (0.6, 0.3) to (0.95, 1.0) - halfway along it is (0.775, 0.65).
    q = Quad(((0.4, 0.3), (0.6, 0.3), (0.95, 1.0), (0.05, 1.0)))
    assert q.contains(0.775, 0.65) is True


def test_json_round_trip(unit_quad):
    assert Quad.from_json(unit_quad.to_json()) == unit_quad


def test_rejects_wrong_point_count():
    with pytest.raises(ValueError):
        Quad(((0.0, 0.0), (1.0, 1.0)))


def test_coerces_points_to_immutable_tuple_of_float_pairs():
    # A list of int pairs is accepted at construction, but must be normalized
    # to a tuple of float pairs so the "frozen" invariant actually holds.
    q = Quad([(0, 0), (1, 0), (1, 1), (0, 1)])
    assert isinstance(q.points, tuple)
    assert q.points == ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    assert all(isinstance(v, float) for p in q.points for v in p)
    # points itself is a tuple, so there is no .append/.extend to mutate it
    assert not hasattr(q.points, "append")


def test_rejects_malformed_points():
    # Four bare floats has the right *count* but each "point" is not an
    # (x, y) pair - this must fail loudly here, not deep inside contains().
    with pytest.raises(ValueError):
        Quad((0.0, 0.0, 0.0, 0.0))


def test_contains_boundary_is_inclusive():
    # contains() is inclusive on every edge and vertex. There is only one
    # play-region quad per video source, so there is never a second,
    # adjacent quad to disambiguate a boundary point against - the old
    # half-open convention was pinned on a hypothetical that doesn't apply
    # here, and it silently dropped points that legitimately sit on an edge.
    q = Quad(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)))
    assert q.contains(0.0, 0.5) is True   # left edge -> inside
    assert q.contains(0.5, 0.0) is True   # top edge -> inside
    assert q.contains(1.0, 0.5) is True   # right edge -> inside
    assert q.contains(0.5, 1.0) is True   # bottom edge -> inside
    assert q.contains(0.0, 0.0) is True   # vertex -> inside
    # a point actually outside the quad must still be rejected, so this
    # test can't be satisfied by making contains() unconditionally True.
    assert q.contains(1.05, 0.5) is False


def test_contains_near_player_clipped_at_frame_bottom():
    # The play region is deliberately drawn to the bottom of the frame so
    # the near player's feet stay inside it when they stand between the
    # camera and the baseline. The camera sits about 1 ft off the ground,
    # so the near player - the closest one to the camera, not an edge case
    # to be discarded - has their bounding box clipped by the frame bottom,
    # putting their foot point (cy + h/2) at exactly 1.0.
    q = Quad(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)))
    cy, h = 0.9, 0.2
    foot_y = cy + h / 2
    assert foot_y == 1.0
    assert q.contains(0.5, foot_y) is True
