import subprocess
from pathlib import Path

import pytest

from bootleg.media.clips import clip_relpath, parse_clip_name
from bootleg.media.probe import probe
from bootleg.media.transcode import (
    CLIP_COLOR_PRIMARIES,
    CLIP_COLOR_RANGE,
    CLIP_COLOR_SPACE,
    CLIP_COLOR_TRC,
    CLIP_CRF,
    CLIP_FPS,
    CLIP_HEIGHT,
    CLIP_WIDTH,
    TranscodeError,
    make_clip,
)


@pytest.fixture
def source_4k(tmp_path, hlg_setparams):
    """6 seconds of 4K30 with a tone, so a 2-second cut has room either side."""
    out = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=3840x2160:rate=30:duration=6",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
         "-vf", hlg_setparams,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         str(out)],
        check=True, capture_output=True,
    )
    assert _stream_field(out, "v:0", "color_range,color_space,color_transfer,color_primaries") == (
        "tv,bt2020nc,arib-std-b67,bt2020"
    ), "fixture did not come out tagged as the locked profile"
    return out


def test_the_locked_profile_constants_are_what_they_have_always_been():
    """Every other assertion in this file compares encoded output against
    CLIP_WIDTH/CLIP_HEIGHT/CLIP_FPS imported from transcode.py -- which means
    those assertions would silently move with the constants if someone
    changed them there, and the whole file would keep passing while
    concat-breaking drift shipped. This test is the one place the numbers
    are written down as literals instead of re-derived from the module under
    test, so a change to the constants shows up here as a failure no matter
    what the rest of the suite does.

    If this fails because you changed CLIP_WIDTH, CLIP_HEIGHT, CLIP_FPS or
    CLIP_CRF in transcode.py: that change breaks `-c copy` concat against
    every clip already cut at the old profile (see the comment on those
    constants). The fix is to put the constant back, not to edit this
    literal to match it -- this test is a deliberate speed bump, not a
    mirror of the code.
    """
    assert (CLIP_WIDTH, CLIP_HEIGHT, CLIP_FPS, CLIP_CRF) == (3840, 2160, 30, 20)
    # Colour is the fifth property -- see the colour-metadata spec. The 24
    # clips already on the drive carry exactly these, inherited by accident
    # from a 10-bit HLG iPhone source; pinning them is what makes the
    # agreement deliberate. Changing any of them breaks -c copy against
    # every one of those clips.
    assert (CLIP_COLOR_RANGE, CLIP_COLOR_SPACE, CLIP_COLOR_TRC, CLIP_COLOR_PRIMARIES) == (
        "tv", "bt2020nc", "arib-std-b67", "bt2020"
    )


def test_make_clip_writes_the_locked_colour_metadata(source_4k, tmp_path):
    """Colour is the fifth property `-c copy` assumes every clip shares, after
    the frame, the rate, the sample aspect and the audio track.

    This assertion holds today even with the flags removed, because ffmpeg
    copies an input's colour properties forward and every source that gets
    this far is already tagged as the profile (make_clip refuses the ones
    that are not). That is exactly why it is worth pinning AND asserting:
    the agreement is currently an accident of ffmpeg's defaults, and this
    test is what notices if a future version stops propagating them.
    """
    dst = tmp_path / "clip.mp4"
    make_clip(source_4k, dst, start_ms=1000, end_ms=3000)
    assert _stream_field(
        dst, "v:0", "color_range,color_space,color_transfer,color_primaries"
    ) == f"{CLIP_COLOR_RANGE},{CLIP_COLOR_SPACE},{CLIP_COLOR_TRC},{CLIP_COLOR_PRIMARIES}"


def test_make_clip_passes_the_colour_flags_to_ffmpeg(sample_video, tmp_path, monkeypatch):
    """test_make_clip_writes_the_locked_colour_metadata cannot tell "we wrote
    it" from "we inherited it": ffmpeg copies an input's colour properties
    forward on its own, and sample_video is already tagged (via
    hlg_setparams) as exactly the locked profile, so deleting the four
    -color_range/-colorspace/-color_primaries/-color_trc flag pairs from
    make_clip would leave that test green -- the encoded output looks
    identical either way, as its own docstring says. The only way to catch a
    dropped flag is to stop looking at the output and look at what make_clip
    actually told ffmpeg to do.

    monkeypatch replaces run_ffmpeg exactly as
    test_make_clip_does_not_expose_dst_until_ffmpeg_succeeds does, so this
    runs in milliseconds rather than the several seconds a real encode takes
    -- the source still has to be real because make_clip probes it for SAR
    and audio-stream conforming before it ever builds the argument list.
    """
    src = sample_video
    dst = tmp_path / "clip.mp4"
    captured_args: list[str] = []

    def fake_run_ffmpeg(args, timeout=None, on_progress=None, total_ms=None):
        captured_args.extend(args)
        Path(args[-1]).write_bytes(b"encoded output")

    monkeypatch.setattr("bootleg.media.transcode.run_ffmpeg", fake_run_ffmpeg)
    make_clip(src, dst, start_ms=500, end_ms=1500)

    # Checked pairwise -- a flag present but paired with the wrong value
    # (e.g. a copy-paste from the wrong constant) is exactly the bug a bare
    # "flag in captured_args" check would miss.
    for flag, value in (
        ("-color_range", CLIP_COLOR_RANGE),
        ("-colorspace", CLIP_COLOR_SPACE),
        ("-color_primaries", CLIP_COLOR_PRIMARIES),
        ("-color_trc", CLIP_COLOR_TRC),
    ):
        assert flag in captured_args, f"{flag} missing from make_clip's ffmpeg args"
        assert captured_args[captured_args.index(flag) + 1] == value


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


def _rgb24_frame(path: Path, raw_path: Path, width: int, height: int) -> bytes:
    """Decode `path`'s first frame to raw rgb24 and return it as bytes.

    Raw rgb24 rather than a re-encoded PNG: see
    test_make_proxy_at_the_probed_rotation_matches_ffmpeg_autorotate in
    test_transcode.py for why a PNG's own side-data handling can obscure
    what the pixels actually are.
    """
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(path), "-frames:v", "1",
         "-pix_fmt", "rgb24", "-f", "rawvideo", str(raw_path)],
        check=True, capture_output=True,
    )
    data = raw_path.read_bytes()
    assert len(data) == width * height * 3
    return data


def _pixel(frame: bytes, width: int, x: int, y: int) -> bytes:
    offset = (y * width + x) * 3
    return frame[offset : offset + 3]


def test_make_clip_conforms_a_quarter_turn_to_the_locked_frame(tmp_path, hlg_setparams):
    """At 90 the source's axes swap, so rotation must be applied BEFORE scale
    and pad -- scaling first pads against the wrong axis.

    Dimensions alone cannot tell the two orders apart: this fixture's 2160x3840
    is an exact aspect match for the locked 3840x2160 frame once rotated, so
    BOTH orders land on 3840x2160 -- rotate-then-scale because scale/pad are
    then a no-op, scale-then-rotate because scale shrinks to a 1215x2160
    intermediate that pad then stretches back out with black bars. The
    padding is the discriminating signal: sample a few pixels inset from the
    left and right edges (inset to dodge encoder ringing right at the
    boundary) and require real picture there, not letterboxing.
    """
    src = tmp_path / "portrait.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=2160x3840:rate=30:duration=3",
         "-vf", hlg_setparams,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)],
        check=True, capture_output=True,
    )
    dst = tmp_path / "clip.mp4"
    make_clip(src, dst, start_ms=0, end_ms=2000, rotation_deg=90)
    info = probe(dst)
    assert (info.width, info.height) == (CLIP_WIDTH, CLIP_HEIGHT)

    frame = _rgb24_frame(dst, tmp_path / "frame.rgb", CLIP_WIDTH, CLIP_HEIGHT)
    inset = 5
    edge_xs = (inset, CLIP_WIDTH - 1 - inset)
    rows = (CLIP_HEIGHT // 4, CLIP_HEIGHT // 2, 3 * CLIP_HEIGHT // 4)
    for y in rows:
        for x in edge_xs:
            assert max(_pixel(frame, CLIP_WIDTH, x, y)) > 40, (
                f"pixel at ({x}, {y}) is black: scale ran before rotate and "
                "letterboxed the locked frame instead of filling it"
            )


def test_make_clip_upscales_and_pads_a_1080p_source(tmp_path, hlg_setparams):
    """A reclaimed source is cut from the 1080p proxy. The clip library cannot
    hold mixed parameters -- `-c copy` refuses them -- so it is conformed."""
    src = tmp_path / "small.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=1920x1080:rate=30:duration=3",
         "-vf", hlg_setparams,
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


def test_make_clip_rejects_a_non_positive_duration(tmp_path):
    """end_ms <= start_ms must raise before ffmpeg ever runs -- `-t` given a
    zero or negative duration is a caller bug, not something to hand to
    ffmpeg and hope it errors sensibly. The source path is never created:
    a real ffmpeg invocation would fail on the missing file too, so the
    only way to know the check ran first is that nothing was written."""
    src = tmp_path / "does-not-exist.mp4"
    dst = tmp_path / "clip.mp4"
    with pytest.raises(ValueError, match="positive duration"):
        make_clip(src, dst, start_ms=2000, end_ms=2000)
    assert not dst.exists()

    with pytest.raises(ValueError, match="positive duration"):
        make_clip(src, dst, start_ms=3000, end_ms=1000)
    assert not dst.exists()


def test_make_clip_does_not_expose_dst_until_ffmpeg_succeeds(sample_video, tmp_path, monkeypatch):
    """H1's core bug, reproduced without a real encode: a previous version
    passed dst itself as ffmpeg's -y output, so a file existed at the
    span-derived path from the first byte ffmpeg wrote -- a live encode
    read as done by plan_export's `.exists()` check. make_clip must instead
    write to a temp path in the same directory and only os.replace() onto
    dst after ffmpeg exits 0, so dst does not exist while "ffmpeg" (faked
    here) is still working.

    The encode is faked but the source is real: make_clip probes it for the
    SAR and audio-stream conforming, so a stand-in of arbitrary bytes (what
    this used to pass) now dies in ffprobe before reaching the code under
    test here.
    """
    src = sample_video
    dst = tmp_path / "clip.mp4"
    dst_existed_mid_encode = []

    def fake_run_ffmpeg(args, timeout=None, on_progress=None, total_ms=None):
        out = Path(args[-1])
        assert out != dst, "make_clip must encode to a temp path, not straight to dst"
        assert out.parent == dst.parent, "the temp path must be a sibling of dst (same filesystem)"
        dst_existed_mid_encode.append(dst.exists())
        out.write_bytes(b"encoded output")

    monkeypatch.setattr("bootleg.media.transcode.run_ffmpeg", fake_run_ffmpeg)
    make_clip(src, dst, start_ms=1000, end_ms=3000)

    assert dst_existed_mid_encode == [False]
    assert dst.exists()
    assert dst.read_bytes() == b"encoded output"
    # No stray temp file left behind after a successful replace.
    assert [p for p in tmp_path.iterdir() if ".part." in p.name] == []


def test_make_clip_leaves_no_temp_file_and_reraises_on_ffmpeg_failure(
    sample_video, tmp_path, monkeypatch
):
    """H1's second consequence -- a killed/failed encode permanently
    corrupting the span-derived path -- is impossible by construction once
    dst is only ever created by a post-success os.replace(): a failed
    run_ffmpeg call raises before that replace ever runs, so dst is never
    created. Cleanup of the temp file must not swallow the original error.
    """
    src = sample_video
    dst = tmp_path / "clip.mp4"

    def boom(args, timeout=None, on_progress=None, total_ms=None):
        raise TranscodeError("ffmpeg exploded")

    monkeypatch.setattr("bootleg.media.transcode.run_ffmpeg", boom)

    with pytest.raises(TranscodeError, match="exploded"):
        make_clip(src, dst, start_ms=1000, end_ms=3000)

    assert not dst.exists()
    assert [p for p in tmp_path.iterdir() if ".part." in p.name] == []


def _stream_field(path: Path, stream: str, field: str) -> str:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", stream,
         "-show_entries", f"stream={field}", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return out


def test_make_clip_pins_square_pixels(tmp_path, hlg_setparams):
    """A non-square SAR is a property of the SOURCE, not of the locked
    profile, so nothing in the profile constrained it -- and libx264 writes
    a non-1:1 sample aspect ratio into the SPS VUI. That makes the clip's
    codec parameters differ from every square-pixel clip already cut, which
    is exactly what the concat demuxer refuses. Permanent, and invisible
    until a reel renders wrong.
    """
    src = tmp_path / "anamorphic.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=1920x1080:rate=30:duration=3",
         "-vf", f"setsar=2/1,{hlg_setparams}", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)],
        check=True, capture_output=True,
    )
    assert _stream_field(src, "v:0", "sample_aspect_ratio") == "2:1", "fixture is not anamorphic"

    dst = tmp_path / "clip.mp4"
    make_clip(src, dst, start_ms=0, end_ms=2000)
    assert _stream_field(dst, "v:0", "sample_aspect_ratio") == "1:1"
    info = probe(dst)
    assert (info.width, info.height) == (CLIP_WIDTH, CLIP_HEIGHT)


def test_make_clip_conforms_an_anamorphic_source_without_stretching_it(tmp_path, hlg_setparams):
    """Pinning SAR to 1:1 is only half the job. A 1920x1080 source at SAR 2:1
    displays as 3840x1080; scaling its *coded* 16:9 frame to the locked frame
    and then declaring the pixels square would fill 3840x2160 with a picture
    stretched to twice its real height. The chain must de-anamorphize first,
    so the picture keeps its shape and pad supplies the letterbox.

    Dimensions cannot tell the two apart -- both land on 3840x2160 -- so
    sample the top and bottom edges: conformed correctly they are black bars,
    stretched they are picture. Same discriminating trick as the quarter-turn
    test above, and the same inset to dodge encoder ringing at the boundary.
    """
    src = tmp_path / "anamorphic.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=1920x1080:rate=30:duration=3",
         "-vf", f"setsar=2/1,{hlg_setparams}", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)],
        check=True, capture_output=True,
    )
    dst = tmp_path / "clip.mp4"
    make_clip(src, dst, start_ms=0, end_ms=2000)

    frame = _rgb24_frame(dst, tmp_path / "frame.rgb", CLIP_WIDTH, CLIP_HEIGHT)
    inset = 5
    for y in (inset, CLIP_HEIGHT - 1 - inset):
        for x in (CLIP_WIDTH // 4, CLIP_WIDTH // 2, 3 * CLIP_WIDTH // 4):
            assert max(_pixel(frame, CLIP_WIDTH, x, y)) < 40, (
                f"pixel at ({x}, {y}) is picture, not letterbox: the 2:1 source "
                "was stretched to fill the locked frame instead of de-anamorphized"
            )


def test_make_clip_synthesizes_silence_for_a_source_with_no_audio(tmp_path, hlg_setparams):
    """The presence of an audio stream is a property of the source too, and
    the locked profile does not survive its absence: a silent source yields a
    video-only clip, and concat against clips that do carry audio either
    refuses or drops the track. Silence at the profile's own rate and layout
    is what makes the streams line up.
    """
    src = tmp_path / "silent.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=1920x1080:rate=30:duration=3",
         "-vf", hlg_setparams,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)],
        check=True, capture_output=True,
    )
    assert not probe(src).has_audio, "fixture is not silent"

    dst = tmp_path / "clip.mp4"
    make_clip(src, dst, start_ms=0, end_ms=2000)
    info = probe(dst)
    assert info.has_audio
    assert _stream_field(dst, "a:0", "codec_name,sample_rate,channels") == "aac,48000,2"
    # The synthesized track must not outlive the picture: anullsrc is
    # infinite, so an unbounded audio input would mux a clip whose audio
    # runs on past its last frame and desynchronize a concat.
    assert 1900 <= info.duration_ms <= 2100


def test_parse_clip_name_reads_back_what_clip_relpath_wrote():
    """The inverse of clip_relpath, and the reason a stray clip on disk is
    self-identifying rather than guessed at: the span is in the name."""
    assert parse_clip_name(clip_relpath(1, 738500, 745500)) == (1, 738500, 745500)
    assert parse_clip_name(clip_relpath(12, 0, 100)) == (12, 0, 100)


def test_parse_clip_name_rejects_anything_this_library_did_not_name():
    """Deliberately strict, because the orphan sweep deletes what this
    function claims. Anything that is not exactly a clip_relpath name is not
    a clip we cut, and something else's file must never be swept up with
    ours -- so a near miss reads as None rather than as a best guess.
    """
    assert parse_clip_name("rally-007.mp4") is None       # the pre-span naming
    assert parse_clip_name("01-738500.mp4") is None       # one bound, not two
    assert parse_clip_name("01-1-2-3.mp4") is None        # one segment too many
    assert parse_clip_name("aa-1-2.mp4") is None          # idx is not a number
    assert parse_clip_name("01-1-2.mov") is None          # not our container
    assert parse_clip_name("01--100-2.mp4") is None       # no negative bounds
    assert parse_clip_name("notes.txt") is None


def test_parse_clip_name_rejects_an_in_flight_temp_file():
    """make_clip's temp path is a dot-prefixed sibling with a .part infix, so
    a live encode's output sits in clips/ under a name that must never read
    as a clip -- counting one as an orphan would delete an encode in
    progress, and counting one as a clip would report a partial file as cut.
    """
    assert parse_clip_name(".01-738500-745500.deadbeef.part.mp4") is None


def test_make_clip_reports_progress_while_it_encodes(source_4k, tmp_path):
    """A 4K cut runs at 4-8x realtime, so a 20-second clip is minutes of
    silence. make_clip knows the span's duration, which is exactly what
    turns ffmpeg's position into a fraction."""
    dst = tmp_path / "clip.mp4"
    seen: list[float] = []
    make_clip(source_4k, dst, start_ms=1000, end_ms=3000, on_progress=seen.append)
    assert dst.exists()
    assert seen, "no progress reported"
    assert seen == sorted(seen), f"progress went backwards: {seen}"
    assert seen[-1] == 1.0


def test_make_clip_without_a_progress_callback_still_cuts(source_4k, tmp_path):
    # Progress is optional: every existing caller passes nothing, and the
    # CLI has no bar to feed.
    dst = tmp_path / "clip.mp4"
    make_clip(source_4k, dst, start_ms=1000, end_ms=3000)
    assert probe(dst).duration_ms >= 1900


def test_clips_from_mismatched_sources_concat_with_c_copy(source_4k, tmp_path, hlg_setparams):
    """The property every conforming rule in make_clip exists to protect,
    asserted end to end rather than parameter by parameter.

    Three clips from three sources that agree on nothing -- 4K with audio,
    anamorphic 1080p, silent 1080p -- concatenated with `-c copy`, which is
    how a reel is built (§5.1) and which refuses streams whose codec
    parameters differ. Before SAR was pinned and silence synthesized this
    could not work: the anamorphic clip carried a 2:1 sample aspect into its
    SPS VUI, and the silent one had no audio stream for the demuxer to
    continue. Both failures are permanent once a clip is cut, and neither is
    visible until a reel renders wrong.
    """
    anamorphic = tmp_path / "anamorphic.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=3",
         "-f", "lavfi", "-i", "sine=frequency=330:duration=3",
         "-vf", f"setsar=2/1,{hlg_setparams}",   # the anamorphic one
         "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(anamorphic)],
        check=True, capture_output=True,
    )
    silent = tmp_path / "silent.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=3",
         "-vf", hlg_setparams,                   # the silent one
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(silent)],
        check=True, capture_output=True,
    )

    parts = []
    for i, src in enumerate((source_4k, anamorphic, silent)):
        part = tmp_path / f"part{i}.mp4"
        make_clip(src, part, start_ms=0, end_ms=1000)
        parts.append(part)

    listing = tmp_path / "concat.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in parts))
    reel = tmp_path / "reel.mp4"
    proc = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", str(listing), "-c", "copy", str(reel)],
        check=False, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert probe(reel).duration_ms >= 2900

    # The exit code proves almost nothing, which is the point. ffmpeg 9.0.1
    # concatenates all three of these without a word of complaint even when
    # they disagree -- it takes the FIRST clip's parameters for the output and
    # the rest are read through them. Both real failures are silent:
    #
    #   - the anamorphic clip's SAR 2:1 is simply ignored, so a reel starting
    #     with a square-pixel clip plays it at the wrong shape;
    #   - the silent clip contributes no audio packets at all, so the reel's
    #     audio stream ends a second before its video does.
    #
    # So assert the two things `-c copy` will not check: that every part
    # carries the same sample aspect, and that the audio runs the whole way.
    for part in parts:
        assert _stream_field(part, "v:0", "sample_aspect_ratio") == "1:1", (
            f"{part.name} carries a sample aspect the other clips do not"
        )
    video_s = float(_stream_field(reel, "v:0", "duration"))
    audio_s = float(_stream_field(reel, "a:0", "duration"))
    assert abs(video_s - audio_s) < 0.15, (
        f"reel audio is {audio_s:.2f}s against {video_s:.2f}s of video: "
        "a clip contributed no audio and the track stops early"
    )
