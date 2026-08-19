import subprocess

import pytest

from bootleg.accel import detect_accel
from bootleg.media.probe import ProbeError, probe


@pytest.fixture
def sample_video(tmp_path):
    """2 second 320x240 30fps clip with a 440Hz tone."""
    out = tmp_path / "sample.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:v", "libx264", "-c:a", "aac", "-shortest", str(out)],
        check=True, capture_output=True,
    )
    return out


def test_detect_accel_returns_usable_values():
    a = detect_accel()
    assert a.h264_encoder
    assert a.torch_device in {"cuda", "mps", "cpu"}


def test_probe_reads_dimensions_and_duration(sample_video):
    info = probe(sample_video)
    assert info.width == 320
    assert info.height == 240
    assert 1900 <= info.duration_ms <= 2100
    assert 29.0 <= info.fps <= 31.0
    assert info.has_audio is True


def test_probe_raises_on_non_media(tmp_path):
    junk = tmp_path / "notavideo.mp4"
    junk.write_bytes(b"this is not a video")
    with pytest.raises(ProbeError):
        probe(junk)
