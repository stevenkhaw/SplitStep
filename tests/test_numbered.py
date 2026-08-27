from pathlib import Path

from PIL import Image

from splitstep.media.numbered import render_overlay_png
from splitstep.resources import overlay_font


def _ink_pixels(png: Path) -> int:
    """Count of non-fully-transparent pixels -- a cheap proxy for how much
    text is on the canvas. Read via the alpha channel's histogram (256
    buckets) rather than iterating all 8.3M pixels in Python.

    `with Image.open(...)` rather than a bare open: PIL's lazy loader keeps
    the file handle open until closed, and this suite's
    filterwarnings=["error"] turns the ResourceWarning a bare open leaves for
    the garbage collector into a test failure.
    """
    with Image.open(png) as img:
        alpha = img.getchannel("A")
        return sum(alpha.histogram()[1:])


def test_overlay_png_is_a_full_frame_transparent_rgba_canvas(tmp_path):
    dst = tmp_path / "overlay.png"
    render_overlay_png(dst, counter="3/20", note="match point", font=overlay_font())

    with Image.open(dst) as img:
        # Full-frame at the locked clip size, RGBA so overlay=0:0 needs no
        # position math and unpainted pixels stay transparent over the footage.
        assert img.size == (3840, 2160)
        assert img.mode == "RGBA"
    assert _ink_pixels(dst) > 0


def test_no_note_means_less_ink_than_with_one(tmp_path):
    counter_only = tmp_path / "counter_only.png"
    with_note = tmp_path / "with_note.png"
    render_overlay_png(counter_only, counter="3/20", note="", font=overlay_font())
    render_overlay_png(with_note, counter="3/20", note="match point", font=overlay_font())

    assert _ink_pixels(counter_only) > 0
    assert _ink_pixels(counter_only) < _ink_pixels(with_note)


def test_special_characters_need_no_escaping(tmp_path):
    # The whole point of compositing a PNG instead of drawtext: none of this
    # ever enters ffmpeg's filtergraph parser as text, so nothing here needs
    # escaping and the render must simply succeed.
    dst = tmp_path / "overlay.png"
    render_overlay_png(dst, counter="1/2", note="it's 50%: a,b\\c", font=overlay_font())

    assert _ink_pixels(dst) > 0
