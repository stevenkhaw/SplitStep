from pathlib import Path

from splitstep.detect.boxes import (
    BALL,
    PERSON,
    Detection,
    read_boxes,
    write_boxes,
)
from splitstep.detect.vision import Box, persons_as_boxes

FIXTURE = Path(__file__).parent / "fixtures/boxes/2026-10-12_source01_0-16min.jsonl"


def test_fixture_round_trips_byte_stable(tmp_path):
    first = tmp_path / "a.jsonl"
    write_boxes(first, read_boxes(FIXTURE))
    second = tmp_path / "b.jsonl"
    write_boxes(second, read_boxes(first))
    assert first.read_bytes() == second.read_bytes()


def test_detection_geometry():
    d = Detection(PERSON, 0.9, 0.2, 0.1, 0.4, 0.7)
    assert d.cx == 0.30000000000000004 or abs(d.cx - 0.3) < 1e-9
    assert d.foot == 0.7 and d.top == 0.1
    assert abs(d.h - 0.6) < 1e-9 and abs(d.w - 0.2) < 1e-9


def test_persons_as_boxes_drops_balls_and_low_conf():
    dets = [
        Detection(BALL, 0.9, 0.5, 0.5, 0.52, 0.52),
        Detection(PERSON, 0.2, 0.1, 0.1, 0.2, 0.5),
        Detection(PERSON, 0.3, 0.2, 0.1, 0.4, 0.7),
    ]
    boxes = persons_as_boxes(dets)
    assert len(boxes) == 1
    b = boxes[0]
    assert isinstance(b, Box)
    assert abs(b.cx - 0.3) < 1e-9
    assert abs(b.foot - 0.7) < 1e-9
    assert abs(b.h - 0.6) < 1e-9


def test_persons_as_boxes_over_the_real_fixture_never_returns_a_ball_or_faint_person():
    frames = read_boxes(FIXTURE)
    assert any(d.cls == BALL for _, dets in frames for d in dets)
    for _, dets in frames:
        assert len(persons_as_boxes(dets)) == sum(
            1 for d in dets if d.cls == PERSON and d.conf >= 0.25)
