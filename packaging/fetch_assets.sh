#!/bin/bash
# Fetch the third-party binaries the bundle ships. Not committed to git --
# they are large, and their licences are satisfied by the in-app credits note
# plus the public repo, not by vendoring.
set -euo pipefail
cd "$(dirname "$0")"
PYTHON_BIN="${PYTHON_BIN:-$HOME/miniconda3/envs/splitstep/bin/python}"
mkdir -p vendor && cd vendor

# evermeet.cx publishes static macOS builds. -display_rotation needs 7.0+;
# make_proxy passes it on every transcode, so an older build breaks rotation
# silently rather than loudly.
for tool in ffmpeg ffprobe; do
  if [ ! -x "$tool" ]; then
    echo "fetching $tool"
    curl -fL "https://evermeet.cx/ffmpeg/getrelease/$tool/zip" -o "$tool.zip"
    unzip -oq "$tool.zip" && rm "$tool.zip"
    chmod +x "$tool"
  fi
done

# An OFL face for the numbered-reel overlay. resources.overlay_font() looks
# for `font.ttf` in the bundle first and falls back to macOS system faces --
# the fallback works today, but a bundled face is what makes the render
# identical on a machine whose Supplemental fonts were never installed.
# Google Fonts ships Roboto Condensed only as a variable font now, and
# media/numbered.py calls ImageFont.truetype with no variation selected --
# which would silently take the Regular instance and lighten a burn that was
# verified frame-by-frame on 2026-08-26 at Bold. Instancing to wght=700 here
# means the shipped file IS Bold, so numbered.py needs no change and the
# render is identical on a Mac that has no Arial installed.
if [ ! -f font.ttf ]; then
  echo "fetching font"
  curl -fL "https://github.com/google/fonts/raw/main/ofl/robotocondensed/RobotoCondensed%5Bwght%5D.ttf" -o font-variable.ttf
  "$PYTHON_BIN" - <<'PYEOF'
from fontTools import ttLib
from fontTools.varLib import instancer

font = ttLib.TTFont("font-variable.ttf")
instancer.instantiateVariableFont(font, {"wght": 700}, inplace=True)
font.save("font.ttf")
print("instanced Roboto Condensed at wght=700")
PYEOF
  rm -f font-variable.ttf
fi

# The detector weights, from the dev checkout (gitignored at the repo root).
if [ ! -f yolo11n.pt ]; then
  cp "$(git -C .. rev-parse --show-toplevel)/yolo11n.pt" . 2>/dev/null \
    || cp /Users/stevenkhaw/Documents/GitHub/SplitStep/yolo11n.pt .
fi

echo "vendor/ ready:"
ls -la
./ffmpeg -version | head -1
