import subprocess

import pytest

from bootleg.accel import detect_accel
from bootleg.media.probe import ProbeError, _pick_fps, probe


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
def audio_only_file(tmp_path):
    """1 second 440Hz tone with no video stream."""
    out = tmp_path / "audio_only.m4a"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-c:a", "aac", str(out)],
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


def test_probe_reads_codec_name(sample_video):
    assert probe(sample_video).codec_name == "h264"


def test_probe_raises_on_non_media(tmp_path):
    junk = tmp_path / "notavideo.mp4"
    junk.write_bytes(b"this is not a video")
    with pytest.raises(ProbeError):
        probe(junk)


def test_probe_raises_on_no_video_stream(audio_only_file):
    with pytest.raises(ProbeError):
        probe(audio_only_file)


def test_probe_fps_close_to_30(sample_video):
    info = probe(sample_video)
    assert abs(info.fps - 30.0) < 1.0


def test_pick_fps_falls_back_when_avg_frame_rate_is_unusable():
    # ffprobe reports "0/0" for avg_frame_rate when it cannot determine an
    # average; the picker must skip it and use r_frame_rate instead, rather
    # than short-circuiting on the truthy-but-useless "0/0" string.
    assert _pick_fps("0/0", "30/1") == 30.0


def test_pick_fps_returns_zero_when_all_candidates_unusable():
    assert _pick_fps("0/0", "0/0") == 0.0
    assert _pick_fps(None, None) == 0.0


@pytest.fixture
def rotated_video(tmp_path, sample_video):
    """The 320x240 sample re-muxed with a 90 degree display matrix."""
    out = tmp_path / "rotated.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-display_rotation", "90", "-i", str(sample_video),
         "-c", "copy", str(out)],
        check=True, capture_output=True,
    )
    return out


def test_probe_reports_zero_rotation_for_an_untagged_clip(sample_video):
    assert probe(sample_video).rotation_deg == 0


def test_probe_reads_a_display_matrix_rotation(rotated_video):
    assert probe(rotated_video).rotation_deg in (90, 270)


def test_probe_still_reports_coded_dimensions_for_a_rotated_clip(rotated_video):
    info = probe(rotated_video)
    assert (info.width, info.height) == (320, 240)


def test_display_size_swaps_the_axes_on_a_quarter_turn():
    from bootleg.media.probe import display_size
    assert display_size(3840, 2160, 90) == (2160, 3840)
    assert display_size(3840, 2160, 270) == (2160, 3840)


def test_display_size_is_unchanged_on_a_half_turn():
    from bootleg.media.probe import display_size
    assert display_size(3840, 2160, 0) == (3840, 2160)
    assert display_size(3840, 2160, 180) == (3840, 2160)


# --- session dating -------------------------------------------------------
#
# A night session is the case that breaks naive dating: play at 20:39 local
# and `creation_time` (always UTC) already reads as the next day.

def test_recorded_at_prefers_apples_local_creationdate():
    from bootleg.media.probe import _recorded_at

    tags = {
        "creation_time": "2026-08-19T00:39:16.000000Z",
        "com.apple.quicktime.creationdate": "2026-08-18T20:39:15-0400",
    }
    # Kept in the offset it was shot in, not translated to the ingesting
    # machine's timezone -- the evening it was played is a property of the
    # recording, not of where it was later imported.
    assert _recorded_at(tags).startswith("2026-08-18T20:39:15")


def test_recorded_at_normalizes_the_offset_for_javascript():
    from bootleg.media.probe import _recorded_at

    # web/src/lib/timeline.ts calls Date.parse on this value. ECMAScript only
    # guarantees +HH:MM, so Apple's "-0400" has to be re-emitted with a colon.
    tags = {"com.apple.quicktime.creationdate": "2026-08-18T20:39:15-0400"}
    assert _recorded_at(tags).endswith("-04:00")


def test_recorded_at_converts_a_utc_only_clip_to_local():
    from datetime import datetime

    from bootleg.media.probe import _recorded_at

    tags = {"creation_time": "2026-08-19T00:39:16.000000Z"}
    got = _recorded_at(tags)
    expected = datetime.fromisoformat("2026-08-19T00:39:16+00:00").astimezone()
    assert got == expected.isoformat()


def test_recorded_at_is_none_without_any_timestamp():
    from bootleg.media.probe import _recorded_at

    assert _recorded_at({}) is None


def test_recorded_at_ignores_an_unparseable_timestamp():
    from bootleg.media.probe import _recorded_at

    assert _recorded_at({"creation_time": "not a date"}) is None


def test_recorded_at_falls_back_when_the_apple_tag_is_malformed():
    from bootleg.media.probe import _recorded_at

    tags = {
        "creation_time": "2026-08-19T00:39:16.000000Z",
        "com.apple.quicktime.creationdate": "garbage",
    }
    assert _recorded_at(tags) is not None
