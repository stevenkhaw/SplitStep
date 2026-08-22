import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
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
    sar: float                          # sample (pixel) aspect ratio; 1.0 for square pixels
    color_range: str | None             # ffprobe's spelling, e.g. "tv"; None when absent
    color_space: str | None             # matrix coefficients, e.g. "bt2020nc"
    color_primaries: str | None         # e.g. "bt2020"
    color_transfer: str | None          # e.g. "arib-std-b67" (HLG)


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


def _sar(video: dict) -> float:
    """The sample (pixel) aspect ratio, reading anything unusable as square.

    ffprobe spells an unrecorded SAR "0:1", and omits the field entirely for
    some containers. Both mean "assume square pixels", which is what every
    player does -- and it matters more than usual here, because `make_clip`
    multiplies a scale target by this number to conform an anamorphic source:
    a literal 0.0 would ask ffmpeg for a zero-width frame and fail the encode.
    """
    raw = video.get("sample_aspect_ratio")
    if not raw:
        return 1.0
    try:
        frac = Fraction(str(raw).replace(":", "/"))
    except (ZeroDivisionError, ValueError):
        return 1.0
    return float(frac) if frac > 0 else 1.0


def _color_tag(video: dict, key: str) -> str | None:
    """One of ffprobe's colour fields, with both spellings of "missing" as None.

    ffprobe has two: its CSV writer prints the literal string "unknown" for a
    stream carrying no colour metadata, and its JSON writer -- the one this
    module reads -- omits the key outright. A third case, a value ffprobe
    does not recognise, also arrives as "unknown".

    All three mean the same thing to `make_clip`: this source's colour is not
    the locked profile's, and a clip cut from it could not be concatenated
    with the ones already cut. Collapsing them here is what lets that check
    stay a plain equality test instead of a special-case ladder -- None
    matches no pinned string, so an untagged source refuses exactly as a
    mislabelled one does.
    """
    value = video.get(key)
    if not value or value == "unknown":
        return None
    return str(value)


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


def _parse_timestamp(raw: str) -> datetime | None:
    """Parse one of ffprobe's timestamp spellings, or give up quietly.

    Both spellings this handles are already valid `fromisoformat` input on
    3.11+: the trailing "Z" of `creation_time`, and the colon-less "-0400"
    offset Apple writes. A file that has been through an editor can carry
    anything at all in these tags, so an unparseable value is a normal
    condition, not an error -- the caller falls back to the file's mtime.
    """
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def _recorded_at(tags: dict) -> str | None:
    """When the recording was made, in the wall-clock time of wherever it was.

    `creation_time` is always UTC, and a tennis session played at 20:39
    Eastern reads as 00:39 the NEXT day in UTC -- so dating a session by
    slicing that string files every evening session a day late. iPhones also
    write `com.apple.quicktime.creationdate`, which carries local time plus
    the offset it was shot in, and that is what "which evening did I play"
    means. Prefer it; it also stays right for footage shot in another
    timezone, where the ingesting machine's clock would not be.

    The result is re-emitted through `isoformat()` rather than passed
    through, because Apple writes the offset as "-0400" while ECMAScript's
    Date.parse only guarantees "+HH:MM" -- and web/src/lib/timeline.ts parses
    this value to lay sources out on one timeline.
    """
    local = _parse_timestamp(tags.get("com.apple.quicktime.creationdate", ""))
    if local is not None:
        return local.isoformat()
    utc = _parse_timestamp(tags.get("creation_time", ""))
    if utc is not None:
        # No offset to preserve, so the best available guess at local time is
        # the machine doing the ingest.
        return utc.astimezone().isoformat()
    return None


def ffprobe_json(path: Path, timeout: float | None = None) -> dict:
    """ffprobe's `-show_format -show_streams` document for `path`.

    Public and separate from `probe()` because two callers ask different
    questions of the same document. `probe()` answers media-level facts
    (duration, display size, rotation) and normalises them into MediaInfo;
    `concat.clip_params` needs raw stream-level codec parameters that
    MediaInfo deliberately does not carry, because only the concat demuxer
    cares about them. One subprocess implementation, one place where a
    missing ffprobe or a spun-down drive is turned into a ProbeError.
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
    return json.loads(proc.stdout or "{}")


def probe(path: Path, timeout: float | None = None) -> MediaInfo:
    """Read a media file's format/stream info via ffprobe.

    `timeout` is None by default so today's background-job call sites
    (`make_thumbs`) are unaffected. A caller inside a request handler should
    pass a short timeout instead -- every route runs on Starlette's shared
    anyio worker-thread pool, so a wedged ffprobe there (e.g. against a
    spun-down external drive) would otherwise tie up a request-handling
    thread indefinitely. Mirrors `run_ffmpeg`'s handling in transcode.py.
    """
    data = ffprobe_json(path, timeout)
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
    recorded_at = _recorded_at(tags)

    return MediaInfo(
        duration_ms=round(duration_s * 1000),
        width=int(video["width"]),
        height=int(video["height"]),
        fps=fps,
        recorded_at=recorded_at,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        codec_name=video.get("codec_name", ""),
        rotation_deg=_display_rotation(video),
        sar=_sar(video),
        color_range=_color_tag(video, "color_range"),
        color_space=_color_tag(video, "color_space"),
        color_primaries=_color_tag(video, "color_primaries"),
        color_transfer=_color_tag(video, "color_transfer"),
    )
