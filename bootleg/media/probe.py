import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


class ProbeError(Exception):
    """ffprobe could not read the file."""


@dataclass(frozen=True)
class MediaInfo:
    duration_ms: int
    width: int                          # coded width, before any display matrix is applied
    height: int                         # coded height, likewise
    fps: float
    recorded_at: str | None
    has_audio: bool
    codec_name: str                     # e.g. "h264", "hevc" -- ffprobe's video stream codec_name
    rotation_deg: int                   # clockwise degrees to apply to the coded frame


def _pick_fps(*rates: str | None) -> float:
    """Return the first usable frame-rate string as a float, else 0.0.

    ffprobe reports an unknown ``avg_frame_rate`` as the string "0/0" rather
    than an empty value, so each candidate must be parsed and checked for a
    zero value (and a zero denominator, which raises ZeroDivisionError)
    before falling through to the next one.
    """
    for rate in rates:
        if not rate:
            continue
        try:
            frac = Fraction(rate)
        except (ZeroDivisionError, ValueError):
            continue
        if frac == 0:
            continue
        return float(frac)
    return 0.0


def _display_rotation(video: dict) -> int:
    """Clockwise degrees a player would rotate this stream by to display it.

    ffprobe reports the Display Matrix angle counter-clockwise, so the sign
    flips here. Anything that is not a quarter turn (a matrix carrying a
    flip, or a stream with no matrix at all) reads as 0: BootlegVision only
    ever encodes right angles, and a bogus value must not reach
    `rotation_filter`, which raises on one.
    """
    for side in video.get("side_data_list", []):
        raw = side.get("rotation")
        if raw is None:
            continue
        try:
            deg = round(float(raw))
        except (TypeError, ValueError):
            continue
        deg = (-deg) % 360
        return deg if deg in (0, 90, 180, 270) else 0
    return 0


def display_size(width: int, height: int, rotation_deg: int) -> tuple[int, int]:
    """Dimensions after `rotation_deg` is applied to a coded `width x height`."""
    return (height, width) if rotation_deg in (90, 270) else (width, height)


def probe(path: Path, timeout: float | None = None) -> MediaInfo:
    """Read a media file's format/stream info via ffprobe.

    `timeout` is None by default so today's background-job call sites
    (`make_thumbs`) are unaffected. A caller inside a request handler should
    pass a short timeout instead -- every route runs on Starlette's shared
    anyio worker-thread pool, so a wedged ffprobe there (e.g. against a
    spun-down external drive) would otherwise tie up a request-handling
    thread indefinitely. Mirrors `run_ffmpeg`'s handling in transcode.py.
    """
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, check=False, timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise ProbeError(
            "ffprobe not found on PATH. Install it: brew install ffmpeg"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"ffprobe timed out after {timeout}s for {path}") from exc
    if proc.returncode != 0:
        raise ProbeError(f"ffprobe failed for {path}: {proc.stderr.strip()}")

    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ProbeError(f"No video stream in {path}")

    fmt = data.get("format", {})
    duration_s = float(fmt.get("duration") or video.get("duration") or 0.0)
    if duration_s <= 0:
        raise ProbeError(f"Could not determine duration for {path}")

    fps = _pick_fps(video.get("avg_frame_rate"), video.get("r_frame_rate"))

    tags = fmt.get("tags", {})
    recorded_at = tags.get("creation_time")

    return MediaInfo(
        duration_ms=round(duration_s * 1000),
        width=int(video["width"]),
        height=int(video["height"]),
        fps=fps,
        recorded_at=recorded_at,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        codec_name=video.get("codec_name", ""),
        rotation_deg=_display_rotation(video),
    )
