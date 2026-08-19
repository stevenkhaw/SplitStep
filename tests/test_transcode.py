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
    # The actual constraint this test exists to guard: browser HEVC support
    # is a coin flip (see make_proxy's docstring). Height and duration alone
    # cannot tell H.264 from HEVC -- swapping the encoder passes both of
    # those unchanged while producing exactly the black-rectangle-in-Chrome
    # failure this constraint prevents.
    assert info.codec_name == "h264"
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


@pytest.fixture
def short_video(tmp_path):
    """2 second clip -- shorter than make_thumbs' 10s default sampling interval."""
    out = tmp_path / "short.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=640x360:rate=30:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
        check=True, capture_output=True,
    )
    return out


def test_make_thumbs_handles_a_clip_shorter_than_the_interval(short_video, tmp_path):
    """A clip shorter than every_s must not crash make_thumbs.

    On ffmpeg 9.0.1, asking the `fps` filter for one frame every 10s from a
    2s clip forces it to emit its single frame from an end-of-stream flush.
    That flush frame reaches the mjpeg encoder tagged in a way it refuses,
    surfacing as "Non full-range YUV is non-standard" -- a color-range
    message that is misleading; the real cause is the sampling interval
    exceeding the clip's duration, not chroma range. make_thumbs clamps its
    internal interval to the clip's own duration to route around this. Do
    not "simplify" that clamp away -- it is load-bearing for any clip
    shorter than the requested every_s, not just this test's fixture.
    """
    dst = tmp_path / "thumbs.jpg"
    make_thumbs(short_video, dst, every_s=10)
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
