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

# beforeBuildCommand is empty in tauri.conf.json, so this does not rebuild
# the web bundles behind us -- the freeze above has already copied web/dist
# into the payload, and a second build would race a sealed bundle.
# A frozen bundle missing its migrations still starts, still serves
# /api/config, and only fails on the first route that touches the database --
# so it must be checked here, not noticed later.
echo "==> verifying the freeze carries its migrations"
SQL_COUNT=$(find packaging/dist/splitstep-server -name "*.sql" | wc -l | tr -d ' ')
SRC_COUNT=$(find splitstep/db/migrations -name "*.sql" | wc -l | tr -d ' ')
if [ "$SQL_COUNT" != "$SRC_COUNT" ]; then
  echo "FATAL: bundle has $SQL_COUNT migrations, source has $SRC_COUNT" >&2
  exit 1
fi
echo "    $SQL_COUNT migrations present"

echo "==> app"
(cd src-tauri && "$CARGO/cargo" tauri build --target aarch64-apple-darwin)

echo "==> done"
ls -lh src-tauri/target/aarch64-apple-darwin/release/bundle/dmg/*.dmg
