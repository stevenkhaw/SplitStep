"""Burned-in counter and note for a numbered reel render.

Each reel item gets one drawtext re-encode into a temp intermediate at the
library's locked colour profile; the existing concat pipeline (pre-flight
parameter check, -c copy, duration probe) then runs over the intermediates
unchanged -- every intermediate is encoded identically, so stream-copy
concat of them stays valid and both guards keep doing their jobs. Shared
clip files are never modified: the same clip can be #3 in one reel and #11
in another.
"""

from pathlib import Path

from splitstep.media.transcode import CLIP_CRF, CLIP_FPS, ProgressFn, run_ffmpeg

# Sized against the locked 3840x2160 frame: legible on a phone screen
# without shouting over the footage.
_MARGIN = 64
_COUNTER_SIZE = 120
_NOTE_SIZE = 72
_BOX = "box=1:boxcolor=black@0.45:boxborderw=24"


def _escape(text: str) -> str:
    """Escape a literal for a drawtext option value.

    expansion=none already keeps %-sequences literal; what remains is the
    filtergraph parser itself: backslash first (it is the escape), then the
    quote that would end the value, then the option and filter separators.
    """
    for ch in ("\\", "'", ":", ","):
        text = text.replace(ch, "\\" + ch)
    return text


def drawtext_filters(counter: str, note: str, font: str) -> str:
    common = f"fontfile='{_escape(font)}':fontcolor=white:{_BOX}:expansion=none"
    counter_filter = (
        f"drawtext=text='{_escape(counter)}':{common}"
        f":fontsize={_COUNTER_SIZE}:x={_MARGIN}:y={_MARGIN}"
    )
    filters = [counter_filter]
    if note:
        filters.append(
            f"drawtext=text='{_escape(note)}':{common}"
            f":fontsize={_NOTE_SIZE}:x={_MARGIN}:y={_MARGIN + _COUNTER_SIZE + 48}"
        )
    return ",".join(filters)


def make_numbered_intermediate(
    src: Path,
    dst: Path,
    *,
    counter: str,
    note: str,
    color_profile: tuple[str, str, str, str],
    font: str,
    on_progress: ProgressFn | None = None,
    duration_ms: int | None = None,
) -> None:
    """One clip, re-encoded whole with the overlay, at the locked profile.

    Video settings mirror make_clip's encode exactly -- same encoder, rate,
    CRF, pixel format, colour flags -- so every intermediate carries
    identical codec parameters and concat's pre-flight sees no divergence.
    Audio is stream-copied: the AAC track is already at the profile
    (make_clip wrote it), re-encoding it would only generation-loss the one
    part of the clip the overlay does not touch.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "-i", str(src),
        "-vf", drawtext_filters(counter, note, font),
        "-r", str(CLIP_FPS),
        "-c:v", "libx264",
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-color_range", color_profile[0],
        "-colorspace", color_profile[1],
        "-color_primaries", color_profile[3],
        "-color_trc", color_profile[2],
        "-crf", str(CLIP_CRF),
        "-preset", "medium",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(dst),
    ], on_progress=on_progress, total_ms=duration_ms)
