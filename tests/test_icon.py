"""The mark is drawn twice -- SVG in Mark.svelte, Pillow in make_icon.py --
and two drawings of one shape drift until the Dock icon and the header are
different logos. web/src/lib/mark.ts is the source; this is the follower's
receipt."""

import importlib.util
import math
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MARK_TS = REPO / "web" / "src" / "lib" / "mark.ts"
MAKE_ICON = REPO / "packaging" / "make_icon.py"


def load_make_icon():
    """Loaded by path, deliberately: `packaging` is also a real installed
    distribution that setuptools and pip import, so making this repo's
    packaging/ directory a top-level package would shadow it."""
    spec = importlib.util.spec_from_file_location("splitstep_make_icon", MAKE_ICON)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ts_constants() -> dict[str, float]:
    """Every `name: number` inside mark.ts's MARK object."""
    body = MARK_TS.read_text()
    block = re.search(r"export const MARK = \{(.*?)\} as const", body, re.DOTALL)
    assert block, "MARK object not found in mark.ts"
    return {
        m.group(1): float(m.group(2))
        for m in re.finditer(r"^\s*(\w+):\s*(-?[\d.]+),", block.group(1), re.MULTILINE)
    }


def test_the_two_drawings_of_the_ball_agree():
    py_mark = load_make_icon().MARK

    ts = ts_constants()
    assert ts, "mark.ts declared no constants"
    # Every constant the TypeScript declares must exist in Python with the
    # same value. Python may not declare extras it does not use, either --
    # an unused constant is one that silently stopped matching.
    assert py_mark == pytest.approx(ts)


def test_the_icon_reads_its_colours_from_the_theme():
    # The existing guarantee, restated for the new tokens: make_icon.py must
    # not hardcode a hex -- every hex literal in the drawing code must be a
    # token()'s fallback argument, not a bare literal used directly. A
    # literal sitting anywhere else (passed straight to fill=, or bound to a
    # variable outside a token() call) means the icon can drift off the
    # chrome beside it, and this must catch that: if someone changes
    # `token("ball", "#d6e02c")` to a bare `ball = "#d6e02c"`, the literal
    # disappears from the fallback set but remains in the full set, and the
    # difference below is non-empty.
    source = MAKE_ICON.read_text()
    body = source.split("def main")[0]

    all_hexes = set(re.findall(r'"#[0-9a-fA-F]{6}"', body))
    token_fallbacks = set(re.findall(r'token\(\s*"[^"]+"\s*,\s*("#[0-9a-fA-F]{6}")\s*\)', body))

    assert all_hexes, "expected at least one colour literal (as a token() fallback)"
    stray = all_hexes - token_fallbacks
    assert not stray, f"bare colour literal(s) outside token() fallbacks: {stray}"


def test_every_size_renders_without_error():
    render = load_make_icon().render

    for size in (16, 32, 128, 512, 1024):
        img = render(size)
        assert img.size == (size, size)
        assert img.mode == "RGBA"


def _svg_arc_to_center(x1, y1, x2, y2, rx, ry, large_arc, sweep):
    """SVG 1.1 spec F.6.5, independently re-derived here rather than imported
    from make_icon.py. If this test called make_icon.py's own conversion to
    build its "expected" curve, a bug in that conversion would produce the
    same wrong answer on both sides of the comparison and this test would
    pass while the Dock icon was still wrong -- which is exactly the bug
    that shipped and the constants-only test above could not see."""
    x1p, y1p = (x1 - x2) / 2, (y1 - y2) / 2
    lam = (x1p / rx) ** 2 + (y1p / ry) ** 2
    if lam > 1:
        scale = math.sqrt(lam)
        rx, ry = rx * scale, ry * scale

    sign = 1 if large_arc != sweep else -1
    num = rx**2 * ry**2 - rx**2 * y1p**2 - ry**2 * x1p**2
    den = rx**2 * y1p**2 + ry**2 * x1p**2
    co = sign * math.sqrt(max(num, 0) / den)
    cxp, cyp = co * rx * y1p / ry, co * -ry * x1p / rx
    cx, cy = cxp + (x1 + x2) / 2, cyp + (y1 + y2) / 2

    def angle(ux, uy, vx, vy):
        dot = ux * vx + uy * vy
        mag = math.hypot(ux, uy) * math.hypot(vx, vy)
        rad = math.acos(max(-1.0, min(1.0, dot / mag)))
        return math.degrees(rad) if ux * vy - uy * vx >= 0 else -math.degrees(rad)

    ux, uy = (x1p - cxp) / rx, (y1p - cyp) / ry
    vx, vy = (-x1p - cxp) / rx, (-y1p - cyp) / ry
    theta1 = angle(1, 0, ux, uy)
    dtheta = angle(ux, uy, vx, vy)
    if not sweep and dtheta > 0:
        dtheta -= 360
    elif sweep and dtheta < 0:
        dtheta += 360

    start, end = sorted((theta1, theta1 + dtheta))
    return cx, cy, rx, ry, start, end


def _sample_ellipse(cx, cy, rx, ry, start_deg, end_deg, n=200):
    """n+1 points along an ellipse arc. Generic trigonometry, not the thing
    under test -- shared between the "expected" and "actual" sides below
    without weakening the guard, because what can disagree between them is
    the (cx, cy, rx, ry, start, end) each side computes, not how a point is
    read off an ellipse once you have those six numbers."""
    return [
        (
            cx + rx * math.cos(math.radians(start_deg + (end_deg - start_deg) * i / n)),
            cy + ry * math.sin(math.radians(start_deg + (end_deg - start_deg) * i / n)),
        )
        for i in range(n + 1)
    ]


def test_the_seams_trace_the_same_curve():
    """What test_the_two_drawings_of_the_ball_agree cannot see: Mark.svelte's
    seams are SVG `A` commands (endpoint parameterisation -- two radii and a
    destination point) and PIL's arc() takes centre parameterisation (a
    bounding box and two angles). Feeding identical MARK constants through a
    wrong conversion between the two draws a different curve -- this
    shipped once as fat white crescents clipped against the ball's own edge
    instead of thin arcs curving toward its centre -- while every constant
    still matched exactly. This independently derives the true SVG curve
    from mark.ts's numbers, samples it, samples the curve
    packaging/make_icon.py's seam_arc() will actually hand to
    ImageDraw.arc(), and checks the two point sets agree."""
    make_icon = load_make_icon()
    mark = ts_constants()

    for side, sweep in (("left", 1), ("right", 0)):
        x = mark["seamLeftX"] if side == "left" else mark["seamRightX"]
        cx, cy, rx, ry, start, end = _svg_arc_to_center(
            x, mark["seamTopY"], x, mark["seamBottomY"],
            mark["seamRx"], mark["seamRy"], large_arc=0, sweep=sweep,
        )
        expected = _sample_ellipse(cx, cy, rx, ry, start, end)

        acx, acy, arx, ary, astart, aend = make_icon.seam_arc(side)
        actual = _sample_ellipse(acx, acy, arx, ary, astart, aend)

        assert len(expected) == len(actual)
        for (ex, ey), (ax, ay) in zip(expected, actual):
            assert ex == pytest.approx(ax, abs=1e-6)
            assert ey == pytest.approx(ay, abs=1e-6)
