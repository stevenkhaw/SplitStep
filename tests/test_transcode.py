import subprocess

import pytest

from bootleg.media.probe import probe
from bootleg.media.transcode import (
    TranscodeError,
    make_proxy,
    make_thumbs,
    rotation_filter,
    run_ffmpeg,
)


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


def test_rotation_filter_maps_every_right_angle():
    assert rotation_filter(0) == ""
    assert rotation_filter(90) == "transpose=1"
    assert rotation_filter(180) == "transpose=1,transpose=1"
    assert rotation_filter(270) == "transpose=2"


def test_rotation_filter_rejects_anything_else():
    with pytest.raises(ValueError, match="0, 90, 180 or 270"):
        rotation_filter(45)


def test_make_proxy_at_zero_rotation_ignores_the_display_matrix(tmp_path, big_video):
    """A tagged clip transcoded at rotation 0 keeps its coded orientation.

    This is the whole point of -noautorotate: the stored rotation decides,
    not ffmpeg's default.
    """
    tagged = tmp_path / "tagged.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-display_rotation", "90", "-i", str(big_video),
         "-c", "copy", str(tagged)],
        check=True, capture_output=True,
    )
    out = tmp_path / "proxy.mp4"
    make_proxy(tagged, out, rotation_deg=0)
    info = probe(out)
    assert info.width > info.height


def test_make_proxy_at_the_probed_rotation_matches_ffmpeg_autorotate(tmp_path, big_video):
    """Settles the sign convention: probe + rotation_filter must agree with
    what every other player would show.

    Frames are compared as raw rgb24 pixels rather than encoded PNG bytes.
    A PNG produced through -noautorotate carries an extra ~92-byte eXIf
    chunk that an autorotate-produced PNG does not: ffmpeg's own autorotate
    consumes the frame's Display Matrix side data before the frame reaches
    an encoder, so there is nothing left to serialize; -noautorotate leaves
    that side data attached to the frame even after this module's own
    transpose has already corrected the orientation, and the PNG encoder
    serializes it into an eXIf chunk regardless. That chunk is unrelated to
    orientation -- confirmed by decoding both legs to rgb24 rawvideo, where
    the correct transpose direction is byte-identical to ffmpeg's autorotate
    and the wrong one differs in ~95% of bytes -- so it must not affect
    whether this test can settle the sign convention.
    """
    tagged = tmp_path / "tagged.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-display_rotation", "90", "-i", str(big_video),
         "-c", "copy", str(tagged)],
        check=True, capture_output=True,
    )
    ours = tmp_path / "ours.rgb"
    theirs = tmp_path / "theirs.rgb"
    deg = probe(tagged).rotation_deg
    vf = ",".join(f for f in (rotation_filter(deg), "scale=-2:120") if f)
    subprocess.run(
        ["ffmpeg", "-y", "-noautorotate", "-i", str(tagged), "-vf", vf,
         "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", str(ours)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(tagged), "-vf", "scale=-2:120",
         "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", str(theirs)],
        check=True, capture_output=True,
    )
    assert ours.read_bytes() == theirs.read_bytes()


def test_run_ffmpeg_converts_a_timeout_to_transcode_error(monkeypatch):
    """extract_frame passes a short timeout since it runs inside a request
    handler (every route shares Starlette's anyio worker-thread pool, so a
    hung ffmpeg there would tie one up indefinitely); callers must see one
    exception type regardless of whether ffmpeg failed outright or hung.
    """
    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=kwargs.get("timeout"))

    monkeypatch.setattr(subprocess, "run", _raise_timeout)
    with pytest.raises(TranscodeError) as exc:
        run_ffmpeg(["-i", "whatever"], timeout=0.01)
    assert "timed out" in str(exc.value)


def test_make_proxy_output_carries_no_rotation_side_data_at_zero_rotation(tmp_path, big_video):
    """The proxy output must not carry the source's Display Matrix side data.

    A rotation-aware player (including every browser <video> element, which is
    how this proxy is played back in the review UI) would apply the matrix on
    top of already-correct pixels and show the footage sideways. The output
    must carry rotation=0 (no rotation), not the source's original matrix.
    """
    tagged = tmp_path / "tagged.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-display_rotation", "90", "-i", str(big_video),
         "-c", "copy", str(tagged)],
        check=True, capture_output=True,
    )
    out = tmp_path / "proxy.mp4"
    make_proxy(tagged, out, rotation_deg=0)
    side_data = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream_side_data=rotation", "-of", "default=nw=1",
         str(out)],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "rotation=" not in side_data


def test_make_proxy_output_carries_no_rotation_side_data_at_nonzero_rotation(tmp_path, big_video):
    """The proxy output must strip the source's Display Matrix even with rotation.

    When the output has been physically transposed (pixels rotated), the
    Display Matrix must not be present -- rotation-aware players would apply
    the matrix on top of the already-transposed pixels, showing sideways video.
    """
    tagged = tmp_path / "tagged.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-display_rotation", "90", "-i", str(big_video),
         "-c", "copy", str(tagged)],
        check=True, capture_output=True,
    )
    out = tmp_path / "proxy.mp4"
    make_proxy(tagged, out, rotation_deg=90)
    side_data = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream_side_data=rotation", "-of", "default=nw=1",
         str(out)],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "rotation=" not in side_data


# -- progress reporting ------------------------------------------------------


def test_run_ffmpeg_reports_progress_against_the_output_duration(tmp_path):
    """A 4K clip encode runs for minutes, and until now the only feedback
    anywhere was the jobs badge's "N jobs running" -- true from the first
    second to the last. ffmpeg already emits its position; this reads it.
    """
    out = tmp_path / "out.mp4"
    seen: list[float] = []
    run_ffmpeg(
        ["-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
        on_progress=seen.append, total_ms=2000,
    )
    assert out.exists()
    assert seen, "ffmpeg's -progress stream produced nothing"
    assert all(0.0 <= f <= 1.0 for f in seen), seen
    assert seen == sorted(seen), f"progress went backwards: {seen}"
    # ffmpeg's final block reports the full output duration, so a completed
    # encode must land on exactly 1.0 -- a bar that stops at 0.97 and then
    # vanishes reads as a failure.
    assert seen[-1] == 1.0


def test_run_ffmpeg_reports_progress_at_most_once_per_percent(tmp_path):
    """The callback ends up committing a row per call. ffmpeg emits a
    progress block far more often than a percent changes, and a commit per
    block would put hundreds of writes through the worker's connection --
    the one the heartbeat thread shares -- for a bar nobody can see move.
    """
    out = tmp_path / "out.mp4"
    seen: list[float] = []
    run_ffmpeg(
        ["-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
        on_progress=seen.append, total_ms=2000,
    )
    assert len(seen) == len({round(f, 2) for f in seen})


def test_run_ffmpeg_with_progress_still_raises_with_stderr_attached(tmp_path):
    """The streaming path must fail exactly like the plain one. ffmpeg's
    stderr is what lands verbatim in jobs.error, and a zero-byte clip that
    looks like a decode bug three days later is what losing it costs.
    """
    with pytest.raises(TranscodeError, match="ffmpeg failed") as exc:
        run_ffmpeg(
            ["-i", str(tmp_path / "does-not-exist.mp4"), str(tmp_path / "out.mp4")],
            on_progress=lambda _f: None, total_ms=1000,
        )
    assert "No such file or directory" in str(exc.value)


def test_run_ffmpeg_without_a_callback_is_unchanged(tmp_path):
    out = tmp_path / "out.mp4"
    run_ffmpeg(["-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=1",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)])
    assert out.exists()
