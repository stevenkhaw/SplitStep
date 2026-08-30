"""Generate the app icon from the design tokens.

Pillow is already a dependency (media/numbered.py renders the overlay PNG
with it), so this adds nothing to the bundle. The mark is a tennis ball cut
in two with the halves stepped apart -- split, and step, which is the
product's name drawn -- and the colours are read from app.css's @theme
rather than picked here, so the icon cannot drift from the chrome.

The *geometry* has the same drift problem and the same answer: these
constants mirror web/src/lib/mark.ts, and tests/test_icon.py fails if they
stop matching. Without that guard the Dock icon and the header mark become
two different logos, silently, and only on a machine that has installed the
.dmg.

Matching constants is necessary but was not sufficient. Mark.svelte's seams
are SVG `A` commands -- *endpoint* parameterisation: two radii and a
destination point. PIL's ImageDraw.arc() is *centre* parameterisation: a
bounding box and two angles. An earlier version of this file fed the same
MARK numbers through a different, wrong conversion (treating seamLeftX as
the arc's peak rather than its endpoint), and drew a materially different
curve -- fat crescents clipped against the ball's own edge instead of thin
arcs curving toward the centre -- while every constant still matched
exactly. `_svg_arc_to_center` is the real conversion (SVG 1.1 spec F.6.5),
so this file draws Mark.svelte's actual path instead of a second
interpretation of its numbers, and `seam_arc` exists as its own function so
tests/test_icon.py can sample precisely what gets handed to
ImageDraw.arc() -- a test that only re-checks MARK's values cannot see this
class of bug, because the constants were never what was wrong.
"""

import math
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

REPO = Path(__file__).resolve().parent.parent
APP_CSS = REPO / "web" / "src" / "app.css"
SIZES = (16, 32, 64, 128, 256, 512, 1024)

# Mirrors web/src/lib/mark.ts. Guarded by tests/test_icon.py.
MARK = {
    "viewBox": 32.0,
    "radius": 13.0,
    "gap": 0.9,
    "step": 1.5,
    "splitInset": 0.5,
    "seamWidth": 2.6,
    "seamRx": 13.8,
    "seamRy": 13.3,
    "seamTopY": 4.5,
    "seamBottomY": 27.5,
    "seamLeftX": 6.8,
    "seamRightX": 25.2,
}

SS = 8  # supersample; PIL has no antialiased primitives


def token(name: str, fallback: str) -> tuple[int, int, int]:
    """Read a --color-* hex value straight out of the theme block."""
    match = re.search(rf"--color-{name}:\s*(#[0-9a-fA-F]{{6}})", APP_CSS.read_text())
    value = match.group(1) if match else fallback
    return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5))


def _svg_arc_to_center(
    x1: float, y1: float, x2: float, y2: float, rx: float, ry: float, large_arc: int, sweep: int
) -> tuple[float, float, float, float, float, float]:
    """SVG 1.1 spec F.6.5: convert an elliptical arc's endpoint
    parameterisation -- start point, end point, two radii, the large-arc and
    sweep flags, exactly what an `A` command carries -- to the centre
    parameterisation PIL's arc() wants: a centre, the (possibly corrected)
    radii, and a start/end angle pair. x-axis-rotation is always 0 for every
    path Mark.svelte draws, so it is left out of the signature rather than
    threaded through unused.

    Returns (cx, cy, rx, ry, start_deg, end_deg) with start_deg <= end_deg.
    PIL's arc() sweeps from start to end through increasing angle only --
    confirmed empirically, not assumed -- and draws a much longer, wrong arc
    if given the raw (theta1, theta1 + dtheta) pair unsorted for a sweep
    that runs the other way (this mark's right-hand seam has sweep=0, so it
    always needs the swap; the left-hand one with sweep=1 never does, which
    is exactly the kind of asymmetry that stays invisible until you check).
    """
    x1p, y1p = (x1 - x2) / 2, (y1 - y2) / 2

    # F.6.6.2: scale up radii too small for the given endpoints. A no-op for
    # every value MARK actually holds, but it is part of the spec's
    # conversion, not an optional extra -- leaving it out would make this a
    # partial implementation trusted as a complete one.
    lam = (x1p / rx) ** 2 + (y1p / ry) ** 2
    if lam > 1:
        scale = math.sqrt(lam)
        rx, ry = rx * scale, ry * scale

    sign = 1 if large_arc != sweep else -1
    num = rx**2 * ry**2 - rx**2 * y1p**2 - ry**2 * x1p**2
    den = rx**2 * y1p**2 + ry**2 * x1p**2
    # max(num, 0): floating-point fuzz can push a mathematically-zero
    # numerator a hair negative; this is not a substitute for the geometry
    # being right, which the sampled-curve test checks separately.
    co = sign * math.sqrt(max(num, 0) / den)
    cxp, cyp = co * rx * y1p / ry, co * -ry * x1p / rx
    cx, cy = cxp + (x1 + x2) / 2, cyp + (y1 + y2) / 2

    def angle(ux: float, uy: float, vx: float, vy: float) -> float:
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


def seam_arc(side: str, unit: float = 1.0) -> tuple[float, float, float, float, float, float]:
    """The (cx, cy, rx, ry, start_deg, end_deg) PIL needs to draw one seam,
    at the given scale (unit=1.0 is mark.ts's own 32-unit viewBox). Split
    out of render() so tests/test_icon.py can call it directly and sample
    exactly what this file will hand to ImageDraw.arc(), rather than having
    to re-derive it from render()'s pixel output."""
    x = MARK["seamLeftX"] if side == "left" else MARK["seamRightX"]
    sweep = 1 if side == "left" else 0
    cx, cy, rx, ry, start, end = _svg_arc_to_center(
        x,
        MARK["seamTopY"],
        x,
        MARK["seamBottomY"],
        MARK["seamRx"],
        MARK["seamRy"],
        large_arc=0,
        sweep=sweep,
    )
    return cx * unit, cy * unit, rx * unit, ry * unit, start, end


def render(size: int) -> Image.Image:
    ball = token("ball", "#d6e02c")
    seam_colour = token("court-line", "#f4f9ff")
    # The ground darkens at the two smallest sizes. On court blue the ball
    # measures 4.27:1, which is fine for a graphical mark at 128px and
    # marginal in a Finder list; on bg navy the same ball measures 13.42:1.
    # Same drawing, legible at both ends.
    ground = token("bg", "#080e16") if size <= 32 else token("court", "#2d6595")

    big = size * SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # macOS squircle: content fills the tile, corners at ~22.5%.
    draw.rounded_rectangle([(0, 0), (big - 1, big - 1)], radius=big * 0.225, fill=(*ground, 255))

    unit = big / MARK["viewBox"]
    centre = big / 2
    radius = MARK["radius"] * unit

    disc = Image.new("L", (big, big), 0)
    ImageDraw.Draw(disc).ellipse(
        [centre - radius, centre - radius, centre + radius, centre + radius], fill=255
    )

    layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.ellipse(
        [centre - radius, centre - radius, centre + radius, centre + radius],
        fill=(*ball, 255),
    )

    # A light from above, the way every macOS icon is lit -- clipped to the
    # disc, because an unclipped blur throws a halo onto the ground and at
    # 16px the halo is most of what you see.
    highlight = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(highlight).ellipse(
        [
            centre - radius * 0.92,
            centre - radius * 1.16,
            centre + radius * 0.92,
            centre + radius * 0.44,
        ],
        fill=(min(ball[0] + 22, 255), min(ball[1] + 20, 255), min(ball[2] + 60, 255), 150),
    )
    highlight = highlight.filter(ImageFilter.GaussianBlur(radius * 0.22))
    highlight.putalpha(
        Image.composite(highlight.getchannel("A"), Image.new("L", (big, big), 0), disc)
    )
    layer.alpha_composite(highlight)

    # The seam. Two arcs bulging toward each other: this is the shape that
    # makes a yellow circle read as a tennis ball, and nothing else does.
    # seam_arc() does the endpoint-to-centre conversion; this loop only
    # scales its result into pixel space and draws it.
    seam = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    sd = ImageDraw.Draw(seam)
    width = max(2, int(MARK["seamWidth"] * unit))
    for side in ("left", "right"):
        cx, cy, rx, ry, start, end = seam_arc(side, unit)
        sd.arc(
            [cx - rx, cy - ry, cx + rx, cy + ry],
            start,
            end,
            fill=(*seam_colour, 255),
            width=width,
        )
    seam.putalpha(Image.composite(seam.getchannel("A"), Image.new("L", (big, big), 0), disc))
    layer.alpha_composite(seam)

    # Split, and step. The crop line sits `splitInset` short of dead centre
    # on each side, not at plain `half` -- mirroring Mark.svelte's clip
    # rects, which stop the same distance short (mark.ts's `splitInset`).
    # The visible gap this produces is 2*(splitInset + gap), and it is
    # exactly the piece that used to be missing here: this file cropped at
    # `half` with no inset while the SVG clipped inset from centre, so the
    # two drawings' splits differed by a full unit at the 32-unit scale.
    gap = int(MARK["gap"] * unit)
    step = int(MARK["step"] * unit)
    inset = int(MARK["splitInset"] * unit)
    half = big // 2
    left_edge = half - inset
    right_edge = half + inset
    img.alpha_composite(layer.crop((0, 0, left_edge, big)), (-gap, -step))
    img.alpha_composite(layer.crop((right_edge, 0, big, big)), (right_edge + gap, step))
    return img.resize((size, size), Image.LANCZOS)


def main() -> int:
    out = REPO / "src-tauri" / "icons"
    out.mkdir(parents=True, exist_ok=True)
    iconset = out / "icon.iconset"
    iconset.mkdir(exist_ok=True)
    for size in SIZES:
        render(size).save(iconset / f"icon_{size}x{size}.png")
        # iconutil wants @2x variants. Rendered natively rather than
        # upscaled, so the 16px mark is designed at 32px too.
        if size <= 512:
            render(size * 2).save(iconset / f"icon_{size}x{size}@2x.png")
    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(out / "icon.icns")],
        check=True,
    )
    # Tauri's bundler also wants plain PNGs for non-macOS targets and for the
    # .dmg window; 128 and 1024 cover both.
    render(1024).save(out / "icon.png")
    render(128).save(out / "128x128.png")
    render(256).save(out / "128x128@2x.png")
    print(f"wrote {out / 'icon.icns'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
