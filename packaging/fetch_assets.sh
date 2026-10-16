#!/bin/bash
# Fetch the third-party binaries the bundle ships. Not committed to git --
# they are large, and vendoring them is not what their licences ask for.
#
# What the licences DO ask for is not fully satisfied yet, so do not read the
# absence of these files as the question being closed. ffmpeg here is a
# GPL-3.0 build (--enable-gpl --enable-version3) and the .dmg redistributes
# it, which obliges whoever hands over that .dmg to also offer the
# corresponding source. This comment used to say the obligation was covered by
# "the in-app credits note plus the public repo": the credits note is real, but
# the repo is private, so that half was never true. See the FFmpeg section of
# LICENSE-THIRD-PARTY.md.
set -euo pipefail
cd "$(dirname "$0")"
PYTHON_BIN="${PYTHON_BIN:-$HOME/miniconda3/envs/splitstep/bin/python}"
mkdir -p vendor && cd vendor

# Martin Riedl publishes static arm64 macOS builds. The release is pinned by
# its versioned URL and each zip by its SHA256, rather than following a
# "latest" redirect: the previous provider's getrelease API (evermeet.cx)
# started answering HTTP 410 one day with no warning, and an unpinned URL
# would just as quietly have handed a different build to the next fresh Mac.
# Bumping the release means changing all three values below together.
# evermeet's builds were also x86_64, so the arm64-only app ran its video
# work under Rosetta -- and on a Mac without Rosetta installed, not at all.
#
# -display_rotation needs 7.0+; make_proxy passes it on every transcode, so an
# older build breaks rotation silently rather than loudly.
RELEASE="https://ffmpeg.martin-riedl.de/download/macos/arm64/1789931890_9.0.2"
sha256_for() {
  case "$1" in
    ffmpeg)  echo c8ed4c4e6978a03c485edbfe4e0a5dc2380f8a30bba5150531b31b094492d924 ;;
    ffprobe) echo fcbe839537485eaee7a7a8bc5cbc0f90d53617e80943e8a5b2e31cb851197ea6 ;;
  esac
}
for tool in ffmpeg ffprobe; do
  if [ ! -x "$tool" ]; then
    echo "fetching $tool"
    curl -fL "$RELEASE/$tool.zip" -o "$tool.zip"
    got=$(shasum -a 256 "$tool.zip" | cut -d' ' -f1)
    if [ "$got" != "$(sha256_for "$tool")" ]; then
      echo "FATAL: $tool.zip sha256 is $got, expected $(sha256_for "$tool")" >&2
      rm -f "$tool.zip"
      exit 1
    fi
    unzip -oq "$tool.zip" "$tool" && rm "$tool.zip"
    chmod +x "$tool"
  fi
done

# Checked every run, not only after a fetch, because a binary already sitting
# in vendor/ is exactly how the x86_64 copies survived onto a new machine.
# Two ways a binary can be wrong and still run on the build Mac:
#  - not arm64: it works under Rosetta, so nothing here notices, and the app
#    then fails on any Mac that never installed it;
#  - linked against anything outside the OS (a Homebrew dylib, say): it works
#    wherever that dylib happens to exist, which is this machine and nowhere
#    the .dmg is going.
for tool in ffmpeg ffprobe; do
  archs=$(lipo -archs "$tool")
  if [ "$archs" != "arm64" ]; then
    echo "FATAL: vendor/$tool is '$archs', not arm64 -- delete it and re-run" >&2
    exit 1
  fi
  foreign=$(otool -L "$tool" | tail -n +2 | awk '{print $1}' \
    | grep -v -e '^/usr/lib/' -e '^/System/' || true)
  if [ -n "$foreign" ]; then
    echo "FATAL: vendor/$tool links libraries outside the OS:" >&2
    echo "$foreign" | sed 's/^/       /' >&2
    exit 1
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
