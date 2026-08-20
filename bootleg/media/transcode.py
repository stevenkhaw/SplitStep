import subprocess
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
