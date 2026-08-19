import subprocess

import pytest

from bootleg.media.probe import probe
from bootleg.media.transcode import TranscodeError, make_proxy, make_thumbs, run_ffmpeg


@pytest.fixture
def big_video(tmp_path):
    """3 second 1920x1080 clip, so the proxy has something to scale down."""
    out = tmp_path / "big.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=1920x1080:rate=30:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
        check=True, capture_output=True,
    )
    return out


def test_make_proxy_outputs_1080p_h264(big_video, tmp_path):
    dst = tmp_path / "proxy.mp4"
    make_proxy(big_video, dst)
    assert dst.exists()
    info = probe(dst)
    assert info.height == 1080
    assert 2900 <= info.duration_ms <= 3100


def test_make_proxy_uses_short_gop(big_video, tmp_path):
    """Keyframe every ~1s so scrubbing snaps. At 30fps that is >= 3 in 3s."""
    dst = tmp_path / "proxy.mp4"
    make_proxy(big_video, dst)
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "frame=key_frame", "-of", "csv=p=0", str(dst)],
        capture_output=True, text=True, check=True,
    ).stdout
    assert out.count("1") >= 3


def test_make_thumbs_writes_a_sprite_sheet(big_video, tmp_path):
    dst = tmp_path / "thumbs.jpg"
    make_thumbs(big_video, dst, every_s=1)
    assert dst.exists()
    assert dst.stat().st_size > 0


def test_run_ffmpeg_raises_with_stderr_on_failure(tmp_path):
    with pytest.raises(TranscodeError) as exc:
        run_ffmpeg(["-i", str(tmp_path / "nope.mp4"), str(tmp_path / "out.mp4")])
    assert "nope.mp4" in str(exc.value)


def test_make_proxy_creates_parent_directories(big_video, tmp_path):
    dst = tmp_path / "a" / "b" / "proxy.mp4"
    make_proxy(big_video, dst)
    assert dst.exists()
