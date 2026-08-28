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
# The overlay font is NOT fetched here: it lives in splitstep/assets/font.ttf
# as package data, so a dev checkout and the frozen app burn the same face.
# It was instanced to wght=700 once and committed -- see that directory's
# README for why it is not the variable file.

# The detector weights, from the dev checkout (gitignored at the repo root).
if [ ! -f yolo11n.pt ]; then
  cp "$(git -C .. rev-parse --show-toplevel)/yolo11n.pt" . 2>/dev/null \
    || cp /Users/stevenkhaw/Documents/GitHub/SplitStep/yolo11n.pt .
fi

echo "vendor/ ready:"
ls -la
./ffmpeg -version | head -1
