#!/bin/bash
# One command from a clean checkout to a .dmg. Every step lives here rather
# than in a README, because a build nobody can reproduce is a build that
# breaks silently.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=~/miniconda3/envs/splitstep/bin
CARGO=~/.cargo/bin

echo "==> third-party assets"
./packaging/fetch_assets.sh

echo "==> web bundles"
(cd web && npm run build && npm run build:launcher)

echo "==> icon"
"$PY/python" packaging/make_icon.py

echo "==> freeze the server"
(cd packaging && "$PY/pyinstaller" --noconfirm --distpath dist --workpath build splitstep.spec)

# Tauri's beforeBuildCommand would rebuild the web bundles a second time; the
# freeze above already copied web/dist into the bundle, so it is skipped here
# rather than run twice against a payload that is already sealed.
echo "==> app"
(cd src-tauri && "$CARGO/cargo" tauri build --no-bundle --target aarch64-apple-darwin \
  && "$CARGO/cargo" tauri bundle --target aarch64-apple-darwin)

echo "==> done"
ls -lh src-tauri/target/aarch64-apple-darwin/release/bundle/dmg/*.dmg
