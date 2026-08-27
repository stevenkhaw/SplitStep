"""Generate the app icon from the design tokens.

Pillow is already a dependency (media/numbered.py renders the overlay PNG
with it), so this adds nothing to the bundle. The mark is two offset bars --
a split, and a step -- in the app's own accent over its own ground, chosen
because it stays legible at 16px where anything representational turns to
mud. The colours are read from app.css's @theme rather than picked here, so
the icon cannot drift from the chrome it sits beside.
"""

import re
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parent.parent
APP_CSS = REPO / "web" / "src" / "app.css"
SIZES = (16, 32, 64, 128, 256, 512, 1024)


def token(name: str, fallback: str) -> tuple[int, int, int]:
    """Read a --color-* hex value straight out of the theme block."""
    match = re.search(rf"--color-{name}:\s*(#[0-9a-fA-F]{{6}})", APP_CSS.read_text())
    value = match.group(1) if match else fallback
    return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5))


def render(size: int) -> Image.Image:
    bg = token("bg", "#0b0b0e")
    accent = token("accent", "#6e9bff")
    dim = token("dim", "#9a9aa8")

    # Supersample and downscale: PIL has no antialiased rounded rectangle, and
    # at 16px the difference between a jagged bar and a smooth one is the
    # difference between a mark and a smudge.
    scale = 8
    big = size * scale
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    unit = big / 16

    draw.rounded_rectangle(
        [(0, 0), (big - 1, big - 1)], radius=unit * 2.6, fill=(*bg, 255)
    )
    bar_w, bar_h = unit * 3.4, unit * 7.2
    # Lower-left bar dim, upper-right bar accent, offset vertically. The gap
    # between them is the split; the offset is the step.
    draw.rounded_rectangle(
        [(unit * 3.6, unit * 5.6), (unit * 3.6 + bar_w, unit * 5.6 + bar_h)],
        radius=unit * 0.9,
        fill=(*dim, 255),
    )
    draw.rounded_rectangle(
        [(unit * 9.0, unit * 3.2), (unit * 9.0 + bar_w, unit * 3.2 + bar_h)],
        radius=unit * 0.9,
        fill=(*accent, 255),
    )
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
