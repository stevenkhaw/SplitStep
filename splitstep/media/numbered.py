"""Burned-in counter and note for a numbered reel render.

Composited as a PNG overlay, not drawtext: this machine's ffmpeg (Homebrew's
default `ffmpeg` formula) has no libfreetype/libfontconfig, so `drawtext`
does not exist in `-filters` at all -- not a font problem, a missing filter.
`overlay`, unlike `drawtext`, has no optional font/text dependency and ships
in the barest ffmpeg builds, so this survives any machine or bundle the same
way -- including whatever ffmpeg ends up bundled with the distributed app.
PIL (already a conda-env dependency via ultralytics) renders the typography
instead and hands ffmpeg a plain image to composite. That also removes every
text-escaping concern drawtext's filtergraph syntax used to need: a note can
contain a colon, a comma, a quote, a backslash, anything, because none of it
ever enters ffmpeg's argument parsing as text.

Each reel item still gets one re-encode into a temp intermediate at the
library's locked colour profile; the existing concat pipeline (pre-flight
parameter check, -c copy, duration probe) then runs over the intermediates
unchanged -- every intermediate is encoded identically, so stream-copy
concat of them stays valid and both guards keep doing their jobs. Shared
clip files are never modified: the same clip can be #3 in one reel and #11
in another.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from splitstep.media.transcode import CLIP_CRF, CLIP_FPS, ProgressFn, run_ffmpeg

# Sized against the locked 3840x2160 frame: legible on a phone screen
# without shouting over the footage.
_FRAME_WIDTH = 3840
_FRAME_HEIGHT = 2160
_MARGIN = 64
_COUNTER_SIZE = 120
_NOTE_SIZE = 72
_BOX_PAD = 24
_BOX_FILL = (0, 0, 0, 115)  # black at ~45% alpha (115/255)
_TEXT_FILL = (255, 255, 255, 255)


def _draw_line(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, y: int
) -> None:
    """One line of text on its own translucent box, boxed by its real ink extent.

    `textbbox` rather than a guessed box size: font metrics vary between the
    counter's and the note's size (and between whatever font ends up
    resolved), so a fixed box would either clip descenders or waste space.
    This is the same measurement drawtext's own `box=1` used to do
    internally -- PIL just does it explicitly now.
    """
    left, top, right, bottom = draw.textbbox((_MARGIN, y), text, font=font)
    draw.rectangle(
        (left - _BOX_PAD, top - _BOX_PAD, right + _BOX_PAD, bottom + _BOX_PAD),
        fill=_BOX_FILL,
    )
    draw.text((_MARGIN, y), text, font=font, fill=_TEXT_FILL)


def render_overlay_png(dst: Path, *, counter: str, note: str, font: str) -> None:
    """A full-frame transparent PNG carrying the counter and (optional) note.

    Full-frame and RGBA rather than a cropped label image: overlay is then
    always `0:0` with no position math on the ffmpeg side, and a mostly-
    transparent 4K PNG compresses to a few KB, so the size cost of not
    cropping is negligible next to a video re-encode.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    canvas = Image.new("RGBA", (_FRAME_WIDTH, _FRAME_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    # ImageFont.truetype loads both .ttf and .ttc (collection index 0, the
    # regular weight, by default) -- see resources.overlay_font.
    _draw_line(draw, counter, ImageFont.truetype(font, _COUNTER_SIZE), _MARGIN)
    if note:
        _draw_line(
            draw, note, ImageFont.truetype(font, _NOTE_SIZE), _MARGIN + _COUNTER_SIZE + 48
        )

    canvas.save(dst)


def make_numbered_intermediate(
    src: Path,
    dst: Path,
    *,
    overlay_png: Path,
    color_profile: tuple[str, str, str, str],
    on_progress: ProgressFn | None = None,
    duration_ms: int | None = None,
) -> None:
    """One clip, re-encoded whole with a pre-rendered overlay composited in.

    Takes an already-rendered `overlay_png` rather than rendering one itself:
    the caller (`handle_reel`) already owns the per-item temp directory and
    names each item's intermediate `NNN.mp4`, so having it name and render
    `NNN.png` there too keeps every per-item filename decision in one place
    instead of splitting it between this module and the handler. It also
    keeps this function's own contract -- and its tests -- purely about the
    ffmpeg composition, with no PIL/font involvement to mock or skip.

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
        "-i", str(overlay_png),
        "-filter_complex", "[0:v][1:v]overlay=0:0",
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
