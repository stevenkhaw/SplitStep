import contextlib
import os
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path

from bootleg.accel import Accel, detect_accel
from bootleg.media.probe import MediaInfo, probe


class TranscodeError(Exception):
    """An ffmpeg invocation failed."""


ProgressFn = Callable[[float], None]


def _failed(returncode: int, args: list[str], stderr: str) -> TranscodeError:
    """The one place a non-zero exit is turned into an exception.

    Shared by both run paths below so they cannot drift on what the message
    says: ffmpeg's stderr lands verbatim in `jobs.error`, and losing it is
    what turns a failed encode into a zero-byte file that looks like a
    decode bug three days later.
    """
    return TranscodeError(
        f"ffmpeg failed (exit {returncode})\nargs: {' '.join(args)}\n{stderr.strip()}"
    )


def _stream_progress(
    args: list[str], on_progress: ProgressFn, total_ms: int
) -> None:
    """Run ffmpeg, reporting completed fraction as it goes.

    `-progress pipe:1` makes ffmpeg write machine-readable key=value blocks
    to stdout, which is free to use because every call here writes its real
    output to a file. stderr goes to a temp file rather than a second pipe:
    reading one pipe while the other fills is the classic deadlock, and a
    failing encode can emit far more than a pipe buffer holds.

    `readline` rather than `for line in stdout`: iterating a text stream
    reads ahead in block-sized chunks, so the progress a caller is watching
    would arrive in bursts minutes apart instead of as ffmpeg emits it.
    """
    cmd = ["ffmpeg", "-v", "error", "-y", "-nostats", "-progress", "pipe:1", *args]
    last = -1.0
    with tempfile.TemporaryFile("w+") as stderr:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=stderr, text=True)
        try:
            assert proc.stdout is not None
            for line in iter(proc.stdout.readline, ""):
                key, _, value = line.strip().partition("=")
                fraction = _progress_fraction(key, value, total_ms)
                if fraction is None:
                    continue
                # One callback per whole percent. Each one ends up as a
                # committed row (see the Worker), ffmpeg emits a block twice
                # a second, and a bar nobody can see move is not worth a
                # write on the connection the heartbeat thread shares.
                if round(fraction, 2) > round(last, 2):
                    last = fraction
                    on_progress(fraction)
            returncode = proc.wait()
        except BaseException:
            # Never leave an orphaned encode behind writing to a temp path
            # nothing will ever clean up.
            proc.kill()
            proc.wait()
            raise
        finally:
            proc.stdout.close()
        if returncode != 0:
            stderr.seek(0)
            raise _failed(returncode, args, stderr.read())


def _progress_fraction(key: str, value: str, total_ms: int) -> float | None:
    """The fraction a `-progress` line reports, or None if it reports none.

    `out_time_us` and `out_time_ms` are both MICROSECONDS -- the `_ms` name
    is a long-standing ffmpeg wart, not a unit. Both are read so this works
    across versions; within one block they carry the same value, and the
    caller's per-percent filter drops the duplicate.

    Values before the first frame are literally "N/A", which is a normal
    state and not something to raise over.
    """
    if key == "progress" and value == "end":
        # The final block is the only place a completed encode says so. A
        # bar that stops at 0.97 and then vanishes reads as a failure.
        return 1.0
    if key not in ("out_time_us", "out_time_ms"):
        return None
    try:
        elapsed_us = int(value)
    except ValueError:
        return None
    return min(1.0, max(0.0, elapsed_us / (total_ms * 1000)))


def run_ffmpeg(
    args: list[str],
    timeout: float | None = None,
    on_progress: ProgressFn | None = None,
    total_ms: int | None = None,
) -> None:
    """Run ffmpeg with the given args.

    `timeout` is None by default so the long-running background-job call
    sites (`make_proxy`, `make_thumbs` -- up to an hour for a full
    transcode) are unaffected. Request-handling call sites (`extract_frame`)
    pass a short timeout instead: every route runs on Starlette's shared
    anyio worker-thread pool, so a hung ffmpeg there would tie up a
    request-handling thread indefinitely.

    `on_progress` (with `total_ms`, the expected output duration) switches to
    a streaming run that reports completed fraction as ffmpeg works. The two
    options are mutually exclusive on purpose rather than by omission: the
    streaming path blocks on ffmpeg's stdout, so a wedged encode producing no
    output would sit there forever and a `timeout` passed alongside would be
    a guarantee this does not actually make. Nothing needs both -- progress
    is for background jobs, timeouts are for request handlers -- so the
    combination raises instead of quietly weakening.
    """
    if on_progress is not None and total_ms:
        if timeout is not None:
            raise ValueError("run_ffmpeg cannot both stream progress and enforce a timeout")
        _stream_progress(args, on_progress, total_ms)
        return
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", *args],
            capture_output=True, text=True, check=False, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise TranscodeError(
            f"ffmpeg timed out after {timeout}s\nargs: {' '.join(args)}"
        ) from exc
    if proc.returncode != 0:
        raise _failed(proc.returncode, args, proc.stderr)


# transpose=1 is 90 degrees clockwise, transpose=2 is 90 counter-clockwise.
# 180 is two clockwise quarter turns rather than hflip,vflip: identical
# result, one filter name to reason about instead of two.
_TRANSPOSE = {
    0: "",
    90: "transpose=1",
    180: "transpose=1,transpose=1",
    270: "transpose=2",
}


def rotation_filter(deg: int) -> str:
    """ffmpeg filter chain rotating a coded frame `deg` degrees clockwise."""
    try:
        return _TRANSPOSE[deg]
    except KeyError:
        raise ValueError(f"rotation must be 0, 90, 180 or 270, got {deg!r}") from None


def make_proxy(src: Path, dst: Path, accel: Accel | None = None, rotation_deg: int = 0) -> None:
    """1080p H.264 with a 1-second GOP. H.264 because browser HEVC is a coin flip.

    Orientation comes from `rotation_deg`, never from the source's display
    matrix: `-noautorotate` disables ffmpeg's default so a rotation this
    library did not choose can never reach the scale filter. It reached it
    once -- a 3840x2160 clip tagged rotation=90 scaled to 608x1080, losing
    two thirds of the scene's pixels and with them every person detection.
    """
    accel = accel or detect_accel()
    dst.parent.mkdir(parents=True, exist_ok=True)
    vf = ",".join(f for f in (rotation_filter(rotation_deg), "scale=-2:1080:flags=bicubic") if f)

    args: list[str] = ["-noautorotate"]
    if accel.hwaccel:
        args += ["-hwaccel", accel.hwaccel]
    args += [
        # Strip stale Display Matrix side data so rotation-aware players
        # (e.g. browser <video> elements in the review UI) don't double-rotate.
        "-display_rotation", "0",
        "-i", str(src),
        "-vf", vf,
        "-c:v", accel.h264_encoder,
        "-g", "30",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
    ]
    if accel.h264_encoder == "libx264":
        args += ["-crf", "21", "-preset", "veryfast"]
    else:
        args += ["-b:v", "8M"]
    args.append(str(dst))

    run_ffmpeg(args)


# The locked clip profile. CHANGING ANY OF THESE BREAKS `-c copy` AGAINST
# EVERY CLIP EVER CUT. Not loudly, which is the trouble: measured on ffmpeg
# 9.0.1, the concat demuxer does not refuse a mismatched clip -- it exits 0
# without a word and reads every input through the FIRST clip's parameters,
# so a differing sample aspect is ignored and a clip with no audio stream
# simply contributes no audio, leaving the reel's track to stop early. See
# test_clips_from_mismatched_sources_concat_with_c_copy, which asserts the
# two things -c copy will not.
#
# Sources that do not match are therefore conformed at cut time rather than
# at concat time -- an upscale is a smaller price than a clip library that
# cannot be concatenated.
CLIP_WIDTH = 3840
CLIP_HEIGHT = 2160
CLIP_FPS = 30
CLIP_CRF = 20

# Colour is the fifth property of a source the profile relied on and never
# stated -- after the frame, the rate, the sample aspect and the audio track.
# `-pix_fmt yuv420p` was pinned and the colour tags were not, so every clip
# inherited them from whatever it was cut from. It happens to agree today:
# all 24 clips on the drive, and the proxies, are tv/bt2020nc/arib-std-b67
# /bt2020, inherited from a 10-bit HLG iPhone source. That agreement was an
# accident, and the first source that disagreed would have broken it
# silently -- the concat demuxer reads every input through the FIRST clip's
# parameters, so a mixed reel gets wrong colour on part of its footage with
# no error anywhere.
#
# Pinned to what the library already contains rather than converted to
# anything: this ffmpeg has neither libzimg nor libplacebo, so no correct
# tonemap exists here in either direction, and writing bt709 tags onto HLG
# pixels would make the file look right while being wrong. A source that
# does not match is refused instead -- see _require_locked_color.
CLIP_COLOR_RANGE = "tv"
CLIP_COLOR_SPACE = "bt2020nc"
CLIP_COLOR_PRIMARIES = "bt2020"
CLIP_COLOR_TRC = "arib-std-b67"


def _require_locked_color(info: MediaInfo, src: Path) -> None:
    """Refuse a source whose colour metadata is not the locked profile's.

    Strict equality on all four fields, and `None` -- an untagged source --
    fails it exactly as a bt709 one does. That is deliberate: untagged pixels
    are not HLG pixels, so applying the profile's tags to them would be a
    relabel without a conversion, which produces a file that looks correct
    while being wrong. Harder to find later than an honest mismatch.

    Refused rather than converted because this ffmpeg cannot convert:
    measured on 9.0.1 with neither libzimg nor libplacebo, `zscale` is absent
    so no linear-light stage exists to feed `tonemap`, and the built-in
    `colorspace` filter's transfer list contains no arib-std-b67 at all --
    it takes HLG neither in nor out. Both directions are blocked, not just
    the one.

    There is deliberately no override. An override is a way to write a
    permanently wrong clip, and the clip is the artifact that has to stay
    concat-compatible for years; the reviewer is the part that can be
    corrected later.
    """
    actual = (info.color_range, info.color_space, info.color_transfer, info.color_primaries)
    wanted = (CLIP_COLOR_RANGE, CLIP_COLOR_SPACE, CLIP_COLOR_TRC, CLIP_COLOR_PRIMARIES)
    if actual == wanted:
        return

    shown = tuple(field or "unset" for field in actual)
    raise TranscodeError(
        f"{src.name} does not carry the locked profile's colour metadata, so a clip "
        f"cut from it could not be concatenated with the ones already cut.\n"
        f"  source:  range={shown[0]} space={shown[1]} transfer={shown[2]} primaries={shown[3]}\n"
        f"  profile: range={wanted[0]} space={wanted[1]} transfer={wanted[2]} "
        f"primaries={wanted[3]}\n"
        f"No conversion was attempted: relabelling one as the other makes the file look "
        f"correct while being wrong, and this ffmpeg has no working tonemap in either "
        f"direction. If this came from the usual iPhone, check Settings > Camera > "
        f"Record Video -- HDR Video turned off records bt709 SDR."
    )


def make_clip(
    src: Path,
    dst: Path,
    *,
    start_ms: int,
    end_ms: int,
    rotation_deg: int = 0,
    on_progress: ProgressFn | None = None,
) -> None:
    """Cut one span to the locked clip profile.

    Software libx264 on purpose, never a hardware encoder: those emit
    vendor-specific SPS/PPS headers, so a clip cut on the Mac and one cut on
    the 4070Ti would fail to concat cleanly or concat with artifacts. libx264
    produces identical headers on every machine, permanently. It runs at
    roughly 4-8x realtime on the Mac -- a 20-second clip takes 80-160
    seconds -- which is the right trade for an artifact that must stay
    byte-compatible for years.

    Conforming is not only about the frame: the source is probed first
    because two of the things the encoded stream carries come from it rather
    than from the profile, and each breaks `-c copy` on its own. See the
    comments on the filter chain and on the audio input below.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    duration_ms = end_ms - start_ms
    if duration_ms <= 0:
        raise ValueError(f"clip needs a positive duration, got {duration_ms}ms")

    info = probe(src)

    # Before anything is written, and before the minutes of encoding: colour
    # is the one profile property that cannot be conformed here, only
    # checked. See _require_locked_color.
    _require_locked_color(info, src)

    # A non-square SAR is a property of the source, and the locked profile
    # never pinned it: libx264 writes the sample aspect ratio into the SPS
    # VUI, so an anamorphic source yields a clip whose codec parameters
    # differ from every square-pixel clip already cut -- exactly what the
    # concat demuxer refuses, and permanent once the clip exists.
    #
    # `setsar=1` alone would fix the parameter and wreck the picture: it
    # declares the pixels square without making them square, so a 1920x1080
    # source at SAR 2:1 (which displays as 3840x1080) would come out
    # stretched to twice its real height. Scaling to the source's own
    # display width first is what conforms it honestly, and it goes BEFORE
    # rotation because SAR describes the coded frame and `transpose` inverts
    # it. Even width because yuv420p subsamples horizontally: an odd one is
    # not representable and ffmpeg refuses the scale outright.
    unsquish = ""
    if info.sar != 1.0:
        unsquish = f"scale={max(2, round(info.width * info.sar / 2) * 2)}:{info.height}"

    # Rotation FIRST, then scale, then pad. At 90 and 270 the rotation swaps
    # the frame's axes, so scaling before rotating pads against the wrong one.
    # Irrelevant at 0 and 180, wrong the moment the camera is mounted sideways.
    #
    # `setsar=1` goes LAST rather than next to the unsquish: scale and pad
    # each recompute the output SAR to preserve display aspect, so a
    # rounding remainder in either could otherwise put a fractional ratio
    # back into the stream. Last is the only position that is a guarantee.
    vf = ",".join(
        f
        for f in (
            unsquish,
            rotation_filter(rotation_deg),
            f"scale={CLIP_WIDTH}:{CLIP_HEIGHT}:force_original_aspect_ratio=decrease",
            f"pad={CLIP_WIDTH}:{CLIP_HEIGHT}:(ow-iw)/2:(oh-ih)/2",
            "setsar=1",
        )
        if f
    )

    # Encode to a sibling temp path and os.replace() onto the final name only
    # once ffmpeg exits 0 -- never write `dst` directly. plan_export's
    # incrementality (clip_relpath's docstring: "a clip either exists at the
    # path its bounds imply, or it does not") silently assumed exists() means
    # complete; with -y ffmpeg writing straight to dst, a *running* encode's
    # partial output already satisfies exists(), so a second export mid-encode
    # reported it as already cut, and a job killed mid-write (disk full,
    # server restart) left a permanently truncated file at exactly the
    # span-derived path -- not queued, not running, so never re-cut, and a
    # future -c copy concat would splice in a broken clip. os.replace() is
    # atomic within a filesystem, and the temp path is a sibling of dst (same
    # dir, hence same filesystem) so the replace can never straddle a mount.
    #
    # The temp name keeps dst's .mp4 suffix -- ffmpeg picks its output muxer
    # from the extension, and a suffix-less name (tried first here) fails
    # with "Unable to choose an output format" before it ever gets to encode
    # a frame. Hidden (dot-prefixed) so a plain directory listing or a future
    # glob("*.mp4") over clips_dir does not turn it up -- pathlib/glob, like
    # the shell, does not match a leading dot against a bare "*". The uuid4
    # plus ".part" infix before that suffix is what keeps it from colliding
    # with or being mistaken for a real clip_relpath name, which is always
    # exactly "NN-START-END.mp4" with no extra segments.
    tmp = dst.with_name(f".{dst.stem}.{uuid.uuid4().hex}.part{dst.suffix}")

    # Whether the source has an audio stream at all is the second property
    # the profile never pinned. A silent source produces a video-only clip,
    # and concatenating that against clips that carry audio either fails or
    # silently drops the track for the rest of the reel. Synthesized silence
    # at the profile's own rate and layout rather than a refusal: a source
    # with no microphone is still perfectly good footage, and the clip that
    # cannot be concatenated is the failure worth preventing.
    #
    # anullsrc is an infinite input, so it is `-t` (an output option, already
    # bounding the cut) that stops it -- without an explicit end the muxer
    # would happily write audio on past the last frame. The explicit -map is
    # required as soon as there are two inputs: ffmpeg's default stream
    # selection would take the video and then look for the "best" audio,
    # which is the synthesized one either way, but relying on that leaves the
    # pairing up to a heuristic rather than to us.
    silence: list[str] = []
    mapping: list[str] = []
    if not info.has_audio:
        silence = ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
        mapping = ["-map", "0:v:0", "-map", "1:a:0"]

    try:
        run_ffmpeg([
            # -noautorotate before the input, exactly as make_proxy does: a
            # rotation this library did not choose must never reach the filter
            # chain. -display_rotation 0 then strips stale Display Matrix side
            # data, so a rotation-aware player cannot double-rotate a clip whose
            # rotation is already baked into the pixels.
            "-noautorotate",
            "-display_rotation", "0",
            # -ss before -i is both fast and frame-accurate here, because the
            # output is always re-encoded. Accuracy is not optional: an in-point
            # landing on the previous keyframe would put a second of the wrong
            # footage at the head of the clip, and these boundaries were trimmed
            # by hand.
            "-ss", f"{start_ms / 1000:.3f}",
            "-i", str(src),
            *silence,
            "-t", f"{duration_ms / 1000:.3f}",
            *mapping,
            "-vf", vf,
            # CFR at the locked rate. The first real source runs at 29.964 fps, so
            # this duplicates roughly one frame in 830 -- imperceptible, and
            # required, because mismatched frame rates break `-c copy`.
            "-r", str(CLIP_FPS),
            "-c:v", "libx264",
            "-profile:v", "high",
            "-pix_fmt", "yuv420p",
            # Written explicitly rather than left to ffmpeg's copy-forward of
            # the input's properties, which is all that has ever put them
            # there. These are output flags rather than a `setparams` in the
            # filter chain because the input is always a real file here, and
            # measured on ffmpeg 9.0.1 the flags stick for a file input --
            # they do NOT for a lavfi one, where primaries and transfer are
            # silently dropped, which is why the test fixtures tag with
            # setparams instead.
            "-color_range", CLIP_COLOR_RANGE,
            "-colorspace", CLIP_COLOR_SPACE,
            "-color_primaries", CLIP_COLOR_PRIMARIES,
            "-color_trc", CLIP_COLOR_TRC,
            "-crf", str(CLIP_CRF),
            "-preset", "medium",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
            str(tmp),
        ], on_progress=on_progress, total_ms=duration_ms)
        os.replace(tmp, dst)
    except BaseException:
        # Best-effort only: a failure here (e.g. permissions) must never
        # replace the TranscodeError/other exception already propagating with
        # some unrelated error about the temp file. missing_ok=True alone
        # would not save us if unlink() raises something other than
        # FileNotFoundError, hence the explicit suppress.
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


def make_thumbs(
    src: Path, dst: Path, every_s: int = 10, cols: int = 10, tile_w: int = 160
) -> None:
    """Write a COLS x COLS sprite sheet, sampling one frame every_s apart.

    ffmpeg 9.0.1's `fps` filter feeding `tile` fails when the requested
    interval exceeds the clip's own duration: with no real frame available
    at that spacing, `fps` can only emit its one frame from an end-of-stream
    flush, and that frame reaches the mjpeg encoder tagged in a way it
    refuses -- surfacing as a misleading "Non full-range YUV is
    non-standard" encoder error that has nothing to do with color range.
    Clamp the actual sampling interval to at most half the clip's duration
    (floor 0.5s) so `fps` always has a real mid-stream frame to sample,
    regardless of what the caller asked for.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    duration_s = probe(src).duration_ms / 1000
    effective_every_s = min(every_s, max(0.5, duration_s / 2))
    run_ffmpeg([
        "-i", str(src),
        "-vf", f"fps=1/{effective_every_s},scale={tile_w}:-2,tile={cols}x{cols}",
        "-frames:v", "1",
        "-q:v", "4",
        str(dst),
    ])
