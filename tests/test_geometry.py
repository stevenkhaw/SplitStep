import pytest

from bootleg.detect.geometry import Quad


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


def test_contains_boundary_is_half_open():
    # contains() is intentionally half-open: a point exactly on the
    # left/top edge is inside (True), a point exactly on the right/bottom
    # edge is outside (False). This is what makes court assignment
    # unambiguous - a foot point on a line shared by two adjacent courts
    # lands in exactly one of them, never both and never neither.
    q = Quad(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)))
    assert q.contains(0.0, 0.5) is True   # left edge -> inside
    assert q.contains(0.5, 0.0) is True   # top edge -> inside
    assert q.contains(1.0, 0.5) is False  # right edge -> outside
    assert q.contains(0.5, 1.0) is False  # bottom edge -> outside
