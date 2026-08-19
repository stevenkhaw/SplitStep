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
    width: int
    height: int
    fps: float
    recorded_at: str | None
    has_audio: bool
    codec_name: str  # e.g. "h264", "hevc" -- ffprobe's video stream codec_name


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


def probe(path: Path) -> MediaInfo:
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError as exc:
        raise ProbeError(
            "ffprobe not found on PATH. Install it: brew install ffmpeg"
        ) from exc
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
    )
