import contextlib
import os
import subprocess
import uuid
from pathlib import Path

from bootleg.accel import Accel, detect_accel
from bootleg.media.probe import probe


class TranscodeError(Exception):
    """An ffmpeg invocation failed."""


def run_ffmpeg(args: list[str], timeout: float | None = None) -> None:
    """Run ffmpeg with the given args.

    `timeout` is None by default so the long-running background-job call
    sites (`make_proxy`, `make_thumbs` -- up to an hour for a full
    transcode) are unaffected. Request-handling call sites (`extract_frame`)
    pass a short timeout instead: every route runs on Starlette's shared
    anyio worker-thread pool, so a hung ffmpeg there would tie up a
    request-handling thread indefinitely.
    """
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
        raise TranscodeError(
            f"ffmpeg failed (exit {proc.returncode})\n"
            f"args: {' '.join(args)}\n{proc.stderr.strip()}"
        )


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
# EVERY CLIP EVER CUT: the concat demuxer refuses streams whose codec
# parameters differ, so a reel mixing an old clip and a new one either fails
# or produces artifacts. Sources that do not match are conformed at cut time
# rather than at concat time -- an upscale is a smaller price than a clip
# library that cannot be concatenated.
CLIP_WIDTH = 3840
CLIP_HEIGHT = 2160
CLIP_FPS = 30
CLIP_CRF = 20


def make_clip(
    src: Path,
    dst: Path,
    *,
    start_ms: int,
    end_ms: int,
    rotation_deg: int = 0,
) -> None:
    """Cut one span to the locked clip profile.

    Software libx264 on purpose, never a hardware encoder: those emit
    vendor-specific SPS/PPS headers, so a clip cut on the Mac and one cut on
    the 4070Ti would fail to concat cleanly or concat with artifacts. libx264
    produces identical headers on every machine, permanently. Roughly 30
    seconds per 20-second 4K clip, which is the right trade for an artifact
    that must stay byte-compatible for years.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    duration_ms = end_ms - start_ms
    if duration_ms <= 0:
        raise ValueError(f"clip needs a positive duration, got {duration_ms}ms")

    # Rotation FIRST, then scale, then pad. At 90 and 270 the rotation swaps
    # the frame's axes, so scaling before rotating pads against the wrong one.
    # Irrelevant at 0 and 180, wrong the moment the camera is mounted sideways.
    vf = ",".join(
        f
        for f in (
            rotation_filter(rotation_deg),
            f"scale={CLIP_WIDTH}:{CLIP_HEIGHT}:force_original_aspect_ratio=decrease",
            f"pad={CLIP_WIDTH}:{CLIP_HEIGHT}:(ow-iw)/2:(oh-ih)/2",
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
            "-t", f"{duration_ms / 1000:.3f}",
            "-vf", vf,
            # CFR at the locked rate. The first real source runs at 29.964 fps, so
            # this duplicates roughly one frame in 830 -- imperceptible, and
            # required, because mismatched frame rates break `-c copy`.
            "-r", str(CLIP_FPS),
            "-c:v", "libx264",
            "-profile:v", "high",
            "-pix_fmt", "yuv420p",
            "-crf", str(CLIP_CRF),
            "-preset", "medium",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
            str(tmp),
        ])
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
