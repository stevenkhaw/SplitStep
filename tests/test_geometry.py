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
