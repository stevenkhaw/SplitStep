"""Where bundled binaries and assets live, bundle-first with dev fallbacks.

Under PyInstaller, `sys._MEIPASS` is the unpacked bundle root and everything
we ship (ffmpeg, ffprobe, yolo11n.pt, web_dist/) sits directly inside it --
this module is the only place that layout is written down. Unfrozen, each
function falls back to exactly what the code did before it existed: PATH for
the ffmpeg pair, ultralytics' cwd auto-download for the weights, the source
tree for the SPA. That keeps the dev loop byte-identical while making a
frozen app self-contained.
"""

import os
import platform
import shutil
import sys
from pathlib import Path


def bundle_dir() -> Path | None:
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else None


def _bundled(name: str) -> Path | None:
    base = bundle_dir()
    if base is not None:
        candidate = base / name
        if candidate.exists():
            return candidate
    return None


def _install_hint() -> str:
    if platform.system() == "Darwin":
        return "Install it: brew install ffmpeg"
    return "Install ffmpeg with your package manager (e.g. sudo apt install ffmpeg)."


def _exe(name: str) -> str:
    bundled = _bundled(name)
    if bundled is not None:
        return str(bundled)
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"{name} not found on PATH. {_install_hint()}")
    return found


def ffmpeg_exe() -> str:
    return _exe("ffmpeg")


def ffprobe_exe() -> str:
    return _exe("ffprobe")


def yolo_weights() -> str:
    bundled = _bundled("yolo11n.pt")
    if bundled is not None:
        return str(bundled)
    # Unfrozen: ultralytics resolves a bare name by downloading into cwd on
    # first use, which is fine for the dev checkout and wrong for an app
    # whose cwd may be / or a read-only translocated path -- hence the
    # bundle-first branch above.
    return "yolo11n.pt"


def spa_dist() -> Path:
    bundled = bundle_dir()
    if bundled is not None and (bundled / "web_dist" / "index.html").is_file():
        return bundled / "web_dist"
    return Path(__file__).parent.parent / "web" / "dist"


# macOS system faces for the numbered-reel overlay, most specific first.
# PIL's ImageFont.truetype loads both .ttf and .ttc (a collection loads its
# first, regular-weight face by default) -- unlike the old drawtext path,
# there is no fontfile= quoting to worry a .ttc's format. The bundled font
# (Phase 3 ships an OFL face) wins when present; the env var is the escape
# hatch for a Mac without these paths; Helvetica.ttc is last because the two
# Arial faces above it are more specific matches for what drawtext used to
# ship, and .ttc stays the final fallback rather than the first choice.
_SYSTEM_FONTS = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)


def overlay_font() -> str:
    bundled = _bundled("font.ttf")
    if bundled is not None:
        return str(bundled)
    env = os.environ.get("SPLITSTEP_FONT")
    if env and Path(env).is_file():
        return env
    for candidate in _SYSTEM_FONTS:
        if Path(candidate).is_file():
            return candidate
    raise RuntimeError(
        "No font for the numbered overlay. Set SPLITSTEP_FONT to a .ttf or .ttc path."
    )
