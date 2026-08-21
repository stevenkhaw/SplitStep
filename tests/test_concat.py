import subprocess

import pytest

from bootleg.media.concat import (
    ConcatError,
    clip_params,
    concat_clips,
    divergences,
    tolerance_ms,
)
from bootleg.media.probe import probe


def _clip(path, seconds=1.0, size="320x240", sar=None, silent=False, crf=23):
    """A clip that shares one profile with its siblings unless told otherwise.

    Not the locked 4K profile: these tests are about the concat mechanism,
    and encoding 3840x2160 repeatedly would make the suite unusable. What
    matters is that the inputs match EACH OTHER, which is exactly the
    precondition -c copy has in production.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    args = ["ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc=size={size}:rate=30:duration={seconds}"]
    if not silent:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    vf = f"setsar={sar}" if sar else "setsar=1"
    args += ["-vf", vf, "-c:v", "libx264", "-profile:v", "high",
             "-pix_fmt", "yuv420p", "-crf", str(crf), "-r", "30"]
    if not silent:
        args += ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]
    args += ["-shortest", str(path)]
    subprocess.run(args, check=True, capture_output=True)
    return path


def test_clip_params_reads_the_parameters_c_copy_assumes(tmp_path):
    params = clip_params(_clip(tmp_path / "a.mp4"))
    assert (params.codec, params.width, params.height) == ("h264", 320, 240)
    assert params.sample_aspect_ratio == "1:1"
    assert params.pix_fmt == "yuv420p"
    assert (params.audio_codec, params.audio_sample_rate, params.audio_channels) == (
        "aac", "48000", 2,
    )


def test_clip_params_reports_a_missing_audio_stream(tmp_path):
    params = clip_params(_clip(tmp_path / "a.mp4", silent=True))
    assert params.audio_codec is None
    assert params.audio_channels is None


def test_matching_clips_have_no_divergences(tmp_path):
    parts = [_clip(tmp_path / f"{i}.mp4") for i in range(3)]
    assert divergences(parts) == []


def test_a_single_clip_cannot_diverge(tmp_path):
    assert divergences([_clip(tmp_path / "a.mp4")]) == []


def test_divergences_names_a_mismatched_sample_aspect(tmp_path):
    # The failure ffmpeg swallows: the clip plays at the wrong shape for its
    # whole duration and the output's length is unchanged, so nothing
    # downstream can see it.
    parts = [_clip(tmp_path / "a.mp4"), _clip(tmp_path / "b.mp4", sar="2/1")]
    reported = divergences(parts)
    assert len(reported) == 1
    assert "b.mp4" in reported[0]
    assert "sample_aspect_ratio" in reported[0]


def test_divergences_names_a_missing_audio_stream(tmp_path):
    # The other silent failure: the reel's audio stops early while the
    # picture runs on.
    parts = [_clip(tmp_path / "a.mp4"), _clip(tmp_path / "b.mp4", silent=True)]
    reported = divergences(parts)
    assert any("audio_codec" in r for r in reported)


def test_divergences_names_a_mismatched_frame_size(tmp_path):
    parts = [_clip(tmp_path / "a.mp4"), _clip(tmp_path / "b.mp4", size="640x480")]
    reported = divergences(parts)
    assert any("width" in r for r in reported)


def test_divergences_compares_against_the_first_clip(tmp_path):
    # Against the FIRST input, not against the locked profile's constants:
    # the first clip is what ffmpeg actually reads every other input
    # through. A library cut uniformly at some other profile concatenates
    # correctly, and refusing it would be a rule about our constants rather
    # than about the output.
    parts = [_clip(tmp_path / "a.mp4", size="640x480"),
             _clip(tmp_path / "b.mp4", size="640x480")]
    assert divergences(parts) == []


def test_concat_sums_the_durations(tmp_path):
    parts = [_clip(tmp_path / f"{i}.mp4") for i in range(3)]
    dst = tmp_path / "reel.mp4"

    assert concat_clips(parts, dst) == "copy"

    assert dst.exists()
    total = sum(probe(p).duration_ms for p in parts)
    assert abs(probe(dst).duration_ms - total) <= tolerance_ms(3)


def test_a_single_input_still_concatenates(tmp_path):
    # A one-item reel is a real thing a user can build.
    dst = tmp_path / "reel.mp4"
    assert concat_clips([_clip(tmp_path / "a.mp4")], dst) == "copy"
    assert dst.exists()


def test_no_inputs_is_refused(tmp_path):
    with pytest.raises(ConcatError):
        concat_clips([], tmp_path / "reel.mp4")


def test_a_missing_input_is_refused(tmp_path):
    with pytest.raises(ConcatError):
        concat_clips([tmp_path / "nope.mp4"], tmp_path / "reel.mp4")


def test_a_nonconforming_input_skips_the_copy_entirely(tmp_path, caplog):
    """The pre-flight, end to end.

    -c copy is never attempted, because on ffmpeg 9.0.1 it would SUCCEED
    and produce a reel whose second clip plays at the wrong shape -- exit 0,
    empty stderr, correct duration. Going straight to a re-encode is the
    only outcome that yields a correct file.
    """
    parts = [_clip(tmp_path / "a.mp4"), _clip(tmp_path / "b.mp4", sar="2/1")]
    dst = tmp_path / "reel.mp4"

    with caplog.at_level("WARNING"):
        assert concat_clips(parts, dst) == "reencode"

    assert dst.exists()
    assert any("sample_aspect_ratio" in r.getMessage() for r in caplog.records)
    # The re-encode's own output is uniform, which is the point.
    assert clip_params(dst).sample_aspect_ratio == "1:1"


def test_a_short_copy_falls_back_to_a_reencode(tmp_path, monkeypatch):
    """The other half: inputs that DO conform, but a copy that came out short.

    Simulated by forcing the copy pass to write only the first input --
    what is under test is the DECISION, which cannot be provoked on demand
    with real ffmpeg.
    """
    import bootleg.media.concat as concat_mod

    parts = [_clip(tmp_path / f"{i}.mp4") for i in range(3)]
    dst = tmp_path / "reel.mp4"
    real_run = concat_mod.run_ffmpeg
    calls = []

    def fake_run(args, timeout=None, on_progress=None, total_ms=None):
        calls.append(args)
        if "copy" in args:
            real_run(["-i", str(parts[0]), "-c", "copy", args[-1]])
            return
        real_run(args)

    monkeypatch.setattr(concat_mod, "run_ffmpeg", fake_run)

    assert concat_clips(parts, dst) == "reencode"

    assert len(calls) == 2
    total = sum(probe(p).duration_ms for p in parts)
    assert abs(probe(dst).duration_ms - total) <= tolerance_ms(3) * 4


def test_a_failed_copy_leaves_no_partial_output(tmp_path, monkeypatch):
    import bootleg.media.concat as concat_mod

    parts = [_clip(tmp_path / "a.mp4")]
    dst = tmp_path / "reel.mp4"

    def fake_run(args, timeout=None, on_progress=None, total_ms=None):
        raise concat_mod.TranscodeError("boom")

    monkeypatch.setattr(concat_mod, "run_ffmpeg", fake_run)

    with pytest.raises(concat_mod.TranscodeError):
        concat_clips(parts, dst)

    # Not merely absent: no temp and no concat listing left behind either.
    assert not dst.exists()
    assert list(tmp_path.glob(".*")) == []


def test_an_existing_reel_is_overwritten(tmp_path):
    # Idempotent by overwrite, like every other handler: re-rendering after
    # a reorder replaces the file rather than refusing or appending.
    dst = tmp_path / "reel.mp4"
    dst.write_bytes(b"stale")
    concat_clips([_clip(tmp_path / "a.mp4")], dst)
    assert dst.read_bytes()[:4] != b"stal"


def test_a_path_containing_a_quote_is_escaped(tmp_path):
    # The library root is a user-chosen path. The concat demuxer's
    # `file '...'` directive ends at the first unescaped quote.
    odd = tmp_path / "it's a drive"
    odd.mkdir()
    dst = tmp_path / "reel.mp4"
    assert concat_clips([_clip(odd / "a.mp4")], dst) == "copy"
    assert dst.exists()


def test_progress_is_reported_during_a_reencode(tmp_path):
    # The re-encode is the slow path -- minutes on a real reel -- and it is
    # the one worth a badge. handle_reel passes the job's reporter through.
    parts = [_clip(tmp_path / "a.mp4"), _clip(tmp_path / "b.mp4", sar="2/1")]
    seen: list[float] = []
    concat_clips(parts, tmp_path / "reel.mp4", on_progress=seen.append)
    assert seen
    assert seen == sorted(seen)
    assert seen[-1] == pytest.approx(1.0)


def test_tolerance_grows_with_the_input_count():
    assert tolerance_ms(1) < tolerance_ms(24)
    # Still far below the failure being caught: a reel silently missing even
    # its shortest point is seconds short, not milliseconds.
    assert tolerance_ms(24) < 2000
