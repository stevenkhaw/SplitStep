import subprocess

import pytest

from bootleg.media.clips import clip_relpath
from bootleg.media.probe import probe
from bootleg.media.transcode import CLIP_FPS, CLIP_HEIGHT, CLIP_WIDTH, make_clip


@pytest.fixture
def source_4k(tmp_path):
    """6 seconds of 4K30 with a tone, so a 2-second cut has room either side."""
    out = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=3840x2160:rate=30:duration=6",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         str(out)],
        check=True, capture_output=True,
    )
    return out


def test_clip_relpath_is_derived_from_the_span(tmp_path):
    # Span-derived, never idx-derived: _renumber reassigns rallies.idx across a
    # whole session on every replace_rallies, so a name built from idx points
    # at a different rally after any threshold sweep.
    assert clip_relpath(1, 738500, 745500) == "01-738500-745500.mp4"
    assert clip_relpath(12, 0, 100) == "12-0-100.mp4"


def test_clip_relpath_is_stable_for_the_same_span(tmp_path):
    assert clip_relpath(1, 1000, 5000) == clip_relpath(1, 1000, 5000)


def test_make_clip_hits_the_locked_profile(source_4k, tmp_path):
    """The most important test in this plan.

    Encode-profile drift is the one silent failure that breaks `-c copy`
    against every clip ever cut, and it would not surface until a reel
    rendered wrong -- long after the clips were made.
    """
    dst = tmp_path / "clip.mp4"
    make_clip(source_4k, dst, start_ms=1000, end_ms=3000)
    info = probe(dst)
    assert (info.width, info.height) == (CLIP_WIDTH, CLIP_HEIGHT)
    assert info.codec_name == "h264"
    assert round(info.fps) == CLIP_FPS
    assert info.has_audio
    assert 1900 <= info.duration_ms <= 2100


def test_make_clip_uses_high_profile_and_yuv420p(source_4k, tmp_path):
    # probe() does not expose these, so read them directly -- they are part of
    # the locked profile and a mismatch breaks concat.
    dst = tmp_path / "clip.mp4"
    make_clip(source_4k, dst, start_ms=1000, end_ms=3000)
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=profile,pix_fmt", "-of", "csv=p=0", str(dst)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert "High" in out
    assert "yuv420p" in out


def test_make_clip_audio_is_aac_48k_stereo(source_4k, tmp_path):
    dst = tmp_path / "clip.mp4"
    make_clip(source_4k, dst, start_ms=1000, end_ms=3000)
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name,sample_rate,channels",
         "-of", "csv=p=0", str(dst)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert out.startswith("aac")
    assert "48000" in out
    assert out.endswith("2")


def test_make_clip_bakes_rotation_rather_than_flagging_it(source_4k, tmp_path):
    """A rotation left as a container flag would concat into a reel that flips
    halfway through, and rotation-aware players would double-rotate it."""
    dst = tmp_path / "clip.mp4"
    make_clip(source_4k, dst, start_ms=1000, end_ms=3000, rotation_deg=180)
    info = probe(dst)
    assert info.rotation_deg == 0
    # 180 does not swap the axes, so the locked frame size is unchanged.
    assert (info.width, info.height) == (CLIP_WIDTH, CLIP_HEIGHT)


def test_make_clip_conforms_a_quarter_turn_to_the_locked_frame(tmp_path):
    """At 90 the source's axes swap, so rotation must be applied BEFORE scale
    and pad -- scaling first pads against the wrong axis."""
    src = tmp_path / "portrait.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=2160x3840:rate=30:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)],
        check=True, capture_output=True,
    )
    dst = tmp_path / "clip.mp4"
    make_clip(src, dst, start_ms=0, end_ms=2000, rotation_deg=90)
    info = probe(dst)
    assert (info.width, info.height) == (CLIP_WIDTH, CLIP_HEIGHT)


def test_make_clip_upscales_and_pads_a_1080p_source(tmp_path):
    """A reclaimed source is cut from the 1080p proxy. The clip library cannot
    hold mixed parameters -- `-c copy` refuses them -- so it is conformed."""
    src = tmp_path / "small.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=1920x1080:rate=30:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)],
        check=True, capture_output=True,
    )
    dst = tmp_path / "clip.mp4"
    make_clip(src, dst, start_ms=0, end_ms=2000)
    info = probe(dst)
    assert (info.width, info.height) == (CLIP_WIDTH, CLIP_HEIGHT)


def test_make_clip_cuts_from_the_requested_in_point(source_4k, tmp_path):
    """Cut accuracy is not optional: an in-point landing on the previous
    keyframe would put a second of the wrong footage at the head of a clip,
    and these boundaries were trimmed by hand."""
    dst = tmp_path / "clip.mp4"
    make_clip(source_4k, dst, start_ms=2000, end_ms=4000)
    assert 1900 <= probe(dst).duration_ms <= 2100
