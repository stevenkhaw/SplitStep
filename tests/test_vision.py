from bootleg.detect.geometry import Quad
from bootleg.detect.vision import Box, build_features, split_near_far

FULL = Quad(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)))


def test_split_picks_largest_box_as_near():
    small = Box(cx=0.5, cy=0.40, w=0.03, h=0.09)
    big = Box(cx=0.4, cy=0.85, w=0.10, h=0.30)
    near, far = split_near_far([small, big], FULL)
    assert near == big
    assert far == small


def test_split_excludes_boxes_outside_the_quad():
    court = Quad(((0.3, 0.3), (0.7, 0.3), (0.7, 1.0), (0.3, 1.0)))
    inside = Box(cx=0.5, cy=0.85, w=0.10, h=0.30)
    adjacent_court = Box(cx=0.05, cy=0.85, w=0.10, h=0.30)
    near, far = split_near_far([inside, adjacent_court], court)
    assert near == inside
    assert far is None


def test_split_returns_none_for_empty_input():
    assert split_near_far([], FULL) == (None, None)


def test_split_with_one_box_assigns_near_only():
    near, far = split_near_far([Box(0.5, 0.85, 0.1, 0.3)], FULL)
    assert near is not None
    assert far is None


def test_build_features_computes_speed_in_body_lengths():
    # near player moves 0.15 in x over 200 ms with a body height of 0.30
    a = [Box(cx=0.40, cy=0.85, w=0.10, h=0.30)]
    b = [Box(cx=0.55, cy=0.85, w=0.10, h=0.30)]
    frames = build_features([a, b], FULL, audio_grid=[(0, 0.0), (0, 0.0)], step_ms=200)
    assert len(frames) == 2
    # 0.15 normalized units / 0.30 body height = 0.5 body-lengths over 0.2 s = 2.5 /s
    assert frames[1].near.v == 2.5


def test_build_features_first_frame_has_zero_speed():
    boxes = [[Box(0.5, 0.85, 0.1, 0.3)]] * 2
    frames = build_features(boxes, FULL, audio_grid=[(0, 0.0)] * 2, step_ms=200)
    assert frames[0].near.v == 0.0


def test_build_features_attaches_audio_grid():
    boxes = [[], []]
    frames = build_features(boxes, FULL, audio_grid=[(2, 0.8), (0, 0.0)], step_ms=200)
    assert frames[0].hits == 2
    assert frames[0].hit_reg == 0.8
    assert frames[1].hits == 0


def test_build_features_tolerates_short_audio_grid():
    boxes = [[], [], []]
    frames = build_features(boxes, FULL, audio_grid=[(1, 0.5)], step_ms=200)
    assert len(frames) == 3
    assert frames[2].hits == 0


def test_build_features_timestamps_step_correctly():
    boxes = [[], [], []]
    frames = build_features(boxes, FULL, audio_grid=[], step_ms=200)
    assert [f.t_ms for f in frames] == [0, 200, 400]


def test_build_features_counts_only_in_region():
    court = Quad(((0.3, 0.3), (0.7, 0.3), (0.7, 1.0), (0.3, 1.0)))
    boxes = [[Box(0.5, 0.85, 0.1, 0.3), Box(0.05, 0.85, 0.1, 0.3)]]
    frames = build_features(boxes, court, audio_grid=[], step_ms=200)
    assert frames[0].n == 1
