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


def _ink_in_region(png: Path, box: tuple[int, int, int, int]) -> int:
    with Image.open(png) as img:
        return sum(img.crop(box).getchannel("A").histogram()[1:])


BOTTOM_LEFT = (0, 1080, 1920, 2160)


def test_scoreboard_draws_bottom_left_and_nothing_there_without_one(tmp_path):
    plain = tmp_path / "plain.png"
    board = tmp_path / "board.png"
    render_overlay_png(plain, counter="3/20", note="", font=overlay_font())
    render_overlay_png(
        board, counter="3/20", note="", font=overlay_font(),
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
        dst, counter="1/1", note="", font=overlay_font(),
        scoreboard=[["Me", "6", "6", "W"], ["Opponent", "0", "0", ""]],
    )
    assert _ink_in_region(dst, BOTTOM_LEFT) > 0
