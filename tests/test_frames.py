import subprocess

import pytest

from bootleg.media.frames import extract_frame
from bootleg.media.probe import probe


@pytest.fixture
def clip(tmp_path):
    out = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=1920x1080:rate=30:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
        check=True, capture_output=True,
    )
    return out


def test_extract_frame_writes_a_jpeg(clip, tmp_path):
    dst = tmp_path / "frame.jpg"
    extract_frame(clip, dst)
    assert dst.exists()
    assert dst.stat().st_size > 0


def test_extract_frame_scales_to_requested_width(clip, tmp_path):
    dst = tmp_path / "frame.jpg"
    extract_frame(clip, dst, width=640)
    assert probe(dst).width == 640


def test_extract_frame_preserves_aspect_ratio(clip, tmp_path):
    """The quad is normalized 0-1, so a squashed still would misplace corners."""
    dst = tmp_path / "frame.jpg"
    extract_frame(clip, dst, width=640)
    info = probe(dst)
    assert info.height == 360  # 1920x1080 -> 640x360


def test_extract_frame_creates_parent_directories(clip, tmp_path):
    dst = tmp_path / "a" / "b" / "frame.jpg"
    extract_frame(clip, dst)
    assert dst.exists()
