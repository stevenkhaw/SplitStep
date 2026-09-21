from pathlib import Path

from PIL import Image

from splitstep.media.numbered import render_overlay_png
from splitstep.media.transcode import (
    CLIP_HEIGHT,
    CLIP_PORTRAIT_HEIGHT,
    CLIP_PORTRAIT_WIDTH,
    CLIP_WIDTH,
)
from splitstep.resources import overlay_font

LANDSCAPE = (CLIP_WIDTH, CLIP_HEIGHT)
PORTRAIT = (CLIP_PORTRAIT_WIDTH, CLIP_PORTRAIT_HEIGHT)


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


def _ink_in_region(png: Path, box: tuple[int, int, int, int]) -> int:
    with Image.open(png) as img:
        return sum(img.crop(box).getchannel("A").histogram()[1:])


def _ink_box(png: Path) -> tuple[int, int, int, int]:
    """The rectangle every painted pixel fits inside, or a zero box when the
    canvas is blank. `getbbox()` on the alpha channel -- the same measurement
    `_ink_pixels` makes, read as an extent rather than a count."""
    with Image.open(png) as img:
        return img.getchannel("A").getbbox() or (0, 0, 0, 0)


def _ink_bottom(png: Path) -> int:
    return _ink_box(png)[3]


def _ink_right(png: Path) -> int:
    return _ink_box(png)[2]


def test_overlay_png_is_a_full_frame_transparent_rgba_canvas(tmp_path):
    dst = tmp_path / "overlay.png"
    render_overlay_png(
        dst, counter="3/20", note="match point", font=overlay_font(), frame=LANDSCAPE
    )

    with Image.open(dst) as img:
        # Full-frame at the locked clip size, RGBA so overlay=0:0 needs no
        # position math and unpainted pixels stay transparent over the footage.
        assert img.size == (3840, 2160)
        assert img.mode == "RGBA"
    assert _ink_pixels(dst) > 0


def test_overlay_png_is_drawn_at_the_frame_it_is_given(tmp_path):
    """The overlay is composited with `overlay=0:0` and nothing checks the
    sizes match. Measured on ffmpeg 9.0.1: `overlay` keeps the MAIN input's
    frame and silently clips whatever hangs off the edge -- so a landscape
    overlay over a portrait clip would burn the scoreboard halfway down the
    picture and never say a word. The canvas has to be the clip's own."""
    dst = tmp_path / "portrait.png"
    render_overlay_png(
        dst, counter="3/20", note="match point", font=overlay_font(), frame=PORTRAIT
    )

    with Image.open(dst) as img:
        assert img.size == (2160, 3840)
    assert _ink_pixels(dst) > 0


def test_a_portrait_overlay_puts_the_board_at_its_own_bottom(tmp_path):
    """The one thing a wrong-frame canvas got wrong that a wrong-frame
    CANVAS SIZE test cannot see on its own: the board is positioned from the
    frame's height, so drawing it on a 3840-tall canvas must put it 1680px
    lower than the landscape frame would have."""
    dst = tmp_path / "portrait.png"
    render_overlay_png(
        dst, counter="1/1", note="", font=overlay_font(), frame=PORTRAIT,
        scoreboard=[["Me", "6", "3", "30"], ["Opp", "4", "2", "15"]],
    )

    # Where the landscape frame would have put it -- must be empty now.
    assert _ink_in_region(dst, (0, CLIP_HEIGHT - 400, 1080, CLIP_HEIGHT)) == 0
    assert _ink_in_region(
        dst, (0, CLIP_PORTRAIT_HEIGHT - 400, 1080, CLIP_PORTRAIT_HEIGHT)
    ) > 0


def test_a_portrait_note_wraps_narrower_than_a_landscape_one(tmp_path):
    """A note is wrapped to roughly half the frame either way up, so the same
    sentence takes more lines in the narrower frame. Measured as ink height:
    more lines means the note reaches further down the canvas."""
    note = "kept the rally alive off the back foot then stepped in on the short ball"
    land = tmp_path / "land.png"
    port = tmp_path / "port.png"
    render_overlay_png(land, counter="1/1", note=note, font=overlay_font(), frame=LANDSCAPE)
    render_overlay_png(port, counter="1/1", note=note, font=overlay_font(), frame=PORTRAIT)

    assert _ink_bottom(port) > _ink_bottom(land)
    # And it still fits inside the frame it was drawn for.
    assert _ink_right(port) < CLIP_PORTRAIT_WIDTH


def test_no_note_means_less_ink_than_with_one(tmp_path):
    counter_only = tmp_path / "counter_only.png"
    with_note = tmp_path / "with_note.png"
    render_overlay_png(
        counter_only, counter="3/20", note="", font=overlay_font(), frame=LANDSCAPE
    )
    render_overlay_png(
        with_note, counter="3/20", note="match point", font=overlay_font(), frame=LANDSCAPE
    )

    assert _ink_pixels(counter_only) > 0
    assert _ink_pixels(counter_only) < _ink_pixels(with_note)


def test_special_characters_need_no_escaping(tmp_path):
    # The whole point of compositing a PNG instead of drawtext: none of this
    # ever enters ffmpeg's filtergraph parser as text, so nothing here needs
    # escaping and the render must simply succeed.
    dst = tmp_path / "overlay.png"
    render_overlay_png(
        dst, counter="1/2", note="it's 50%: a,b\\c", font=overlay_font(), frame=LANDSCAPE
    )

    assert _ink_pixels(dst) > 0


BOTTOM_LEFT = (0, 1080, 1920, 2160)


def test_scoreboard_draws_bottom_left_and_nothing_there_without_one(tmp_path):
    plain = tmp_path / "plain.png"
    board = tmp_path / "board.png"
    render_overlay_png(plain, counter="3/20", note="", font=overlay_font(), frame=LANDSCAPE)
    render_overlay_png(
        board, counter="3/20", note="", font=overlay_font(), frame=LANDSCAPE,
        scoreboard=[["Me", "6", "3", "30"], ["Opp", "4", "2", "15"]],
    )
    assert _ink_in_region(plain, BOTTOM_LEFT) == 0
    assert _ink_in_region(board, BOTTOM_LEFT) > 0
    # The counter is untouched by the board: same ink top-left either way.
    top_left = (0, 0, 1920, 1080)
    assert _ink_in_region(plain, top_left) == _ink_in_region(board, top_left)


def test_scoreboard_with_uneven_row_lengths_still_renders(tmp_path):
    # A finished match has a "W" and a "" in the last column; the grid must
    # size columns from the longest cell and not choke on an empty one.
    dst = tmp_path / "w.png"
    render_overlay_png(
        dst, counter="1/1", note="", font=overlay_font(), frame=LANDSCAPE,
        scoreboard=[["Me", "6", "6", "W"], ["Opponent", "0", "0", ""]],
    )
    assert _ink_in_region(dst, BOTTOM_LEFT) > 0
