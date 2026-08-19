import subprocess
from pathlib import Path

from bootleg.accel import Accel, detect_accel


class TranscodeError(Exception):
    """An ffmpeg invocation failed."""


def run_ffmpeg(args: list[str]) -> None:
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", *args],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise TranscodeError(
            f"ffmpeg failed (exit {proc.returncode})\n"
            f"args: {' '.join(args)}\n{proc.stderr.strip()}"
        )


def make_proxy(src: Path, dst: Path, accel: Accel | None = None) -> None:
    """1080p H.264 with a 1-second GOP. H.264 because browser HEVC is a coin flip."""
    accel = accel or detect_accel()
    dst.parent.mkdir(parents=True, exist_ok=True)

    args: list[str] = []
    if accel.hwaccel:
        args += ["-hwaccel", accel.hwaccel]
    args += [
        "-i", str(src),
        "-vf", "scale=-2:1080:flags=bicubic",
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
    dst.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "-i", str(src),
        "-vf", f"fps=1/{every_s},scale={tile_w}:-2,tile={cols}x{cols}",
        "-frames:v", "1",
        "-q:v", "4",
        str(dst),
    ])
