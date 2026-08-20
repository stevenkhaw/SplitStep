import subprocess

import pytest

from bootleg.media.frames import extract_frame
from bootleg.media.probe import ProbeError, probe


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


@pytest.fixture
def rotated_video(tmp_path, sample_video):
    """The sample re-muxed with a 90 degree display matrix, the way a phone
    tags portrait footage. extract_frame must not let this rotation reach
    the output on top of the pixel rotation it already applied via
    rotation_filter -- see test_extract_frame_strips_the_sources_display_matrix.
    """
    out = tmp_path / "rotated.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-display_rotation", "90", "-i", str(sample_video),
         "-c", "copy", str(out)],
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


def test_extract_frame_applies_a_rotation(tmp_path, sample_video):
    out = tmp_path / "rot.jpg"
    extract_frame(sample_video, out, at_ms=500, width=160, rotation_deg=90)
    info = probe(out)
    # 320x240 coded, scaled to 160 wide before rotation would give 160x120;
    # rotating first and then scaling to width 160 gives a tall frame.
    assert info.height > info.width


def test_extract_frame_rejects_a_bad_rotation(tmp_path, sample_video):
    with pytest.raises(ValueError, match="0, 90, 180 or 270"):
        extract_frame(sample_video, tmp_path / "x.jpg", rotation_deg=45)


def test_extract_frame_strips_the_sources_display_matrix(tmp_path, rotated_video):
    """extract_frame's own rotation_filter already rotates the pixels, so
    the output must carry no rotation metadata of its own -- a preview the
    browser silently re-rotated on top of that would disagree with the
    quad the user drags over it (see make_proxy's -display_rotation 0 for
    the same fix against the same failure mode).
    """
    out = tmp_path / "preview.jpg"
    extract_frame(rotated_video, out, at_ms=500, rotation_deg=90)
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream_side_data=rotation", "-of", "default=nw=1", str(out)],
        capture_output=True, text=True, check=True,
    )
    assert "rotation=" not in proc.stdout


def test_probe_timeout_is_converted_to_probe_error(tmp_path, monkeypatch):
    """probe() takes an optional timeout, the same pattern run_ffmpeg
    already uses in transcode.py, so a caller inside a request handler is
    never exposed to a raw subprocess.TimeoutExpired -- only a ProbeError
    it already knows how to handle.
    """
    def _timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout"))

    monkeypatch.setattr("bootleg.media.probe.subprocess.run", _timeout)

    with pytest.raises(ProbeError):
        probe(tmp_path / "wedged.mp4", timeout=0.01)
