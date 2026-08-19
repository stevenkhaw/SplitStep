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


def probe(path: Path) -> MediaInfo:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=False,
    )
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

    rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"
    fps = float(Fraction(rate)) if not rate.startswith("0/") else 0.0

    tags = fmt.get("tags", {})
    recorded_at = tags.get("creation_time")

    return MediaInfo(
        duration_ms=round(duration_s * 1000),
        width=int(video["width"]),
        height=int(video["height"]),
        fps=fps,
        recorded_at=recorded_at,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )
