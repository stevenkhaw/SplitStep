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

# Same class of failure as the migrations: read at runtime by path, invisible
# until a numbered render is attempted, and it would silently fall back to a
# different typeface rather than erroring.
if ! find packaging/dist/splitstep-server -name "font.ttf" | grep -q .; then
  echo "FATAL: the freeze carries no overlay font" >&2
  exit 1
fi
echo "    overlay font present"

# Third of the same family, and the one that has actually shipped broken:
# matplotlib is imported at module level by ultralytics.models, which
# ultralytics resolves lazily, so a freeze without it starts, serves, and
# fails only inside the first detect job -- fifteen minutes in, on the user's
# machine. mpl-data is checked rather than the package directory because it
# is the half PyInstaller collects through a hook: if the hook stops firing
# the modules can be present and the runtime still dies looking for
# matplotlibrc. `tests/test_freeze_spec.py` guards the source side of this.
if ! find packaging/dist/splitstep-server -type d -name "mpl-data" | grep -q .; then
  echo "FATAL: the freeze carries no matplotlib -- detect would die mid-job" >&2
  exit 1
fi
echo "    matplotlib present"

echo "==> app"
(cd src-tauri && "$CARGO/cargo" tauri build --target aarch64-apple-darwin)

# A dmg built from a stale binary is indistinguishable from a good one by
# eye, and it cost two rounds of "the fix isn't working" -- the fix was in the
# source and not in the artifact. Cargo's own freshness tracking missed it
# because tauri's build script output is not always invalidated by an edit to
# build.rs or capabilities/.
echo "==> verifying the app is newer than the sources that built it"
APP_BIN="src-tauri/target/aarch64-apple-darwin/release/bundle/macos/SplitStep.app/Contents/MacOS/splitstep-app"
[ -f "$APP_BIN" ] || APP_BIN="src-tauri/target/aarch64-apple-darwin/release/splitstep-app"
for src in src-tauri/build.rs src-tauri/tauri.conf.json src-tauri/src/*.rs src-tauri/capabilities/*.json; do
  if [ "$src" -nt "$APP_BIN" ]; then
    echo "FATAL: $src is newer than the built app -- the bundle would ship stale code." >&2
    echo "       Run: touch src-tauri/build.rs && cargo tauri build" >&2
    exit 1
  fi
done
echo "    app is current"

# This guard watches the payload; the one above watches the binary, and the
# two go stale independently. On 2026-08-30, twice, nothing in the Rust crate
# had changed, so cargo short-circuited and tauri's bundler reused the
# *previous* .app's copy of the "splitstep-server" bundle resource wholesale.
# `npm run build` and the PyInstaller freeze were both correct -- the guard
# above printed "app is current" truthfully both times, because the
# executable genuinely was current -- and the shipped dmg still carried the
# previous run's web_dist regardless:
#   freeze at 19:27 produced   web_dist/assets/index-DPX1KXY-.js  (correct)
#   dmg   at 19:30 contained   web_dist/assets/index-Bh4QEhcS.js  (stale)
# Comparing the freeze in packaging/dist/ against web/dist would not have
# caught this -- both were already correct at that point; the divergence
# only exists inside the bundler's copy. And bundle/macos/SplitStep.app is
# not a place to look either: tauri deletes it on its way out ("Cleaning
# .../bundle/macos/SplitStep.app"), so the mounted dmg is the only place the
# truth still lives. Filenames alone would usually show this -- Vite hashes
# them by content -- but the check has to compare bytes, not names, or it
# would miss the rarer case of a same-named file with different content.
echo "==> verifying the dmg's web bundle matches what this build just produced"
DMG_PATH=$(ls src-tauri/target/aarch64-apple-darwin/release/bundle/dmg/*.dmg)
DMG_MOUNT=$(mktemp -d)
cleanup_dmg_mount() {
  hdiutil detach "$DMG_MOUNT" -quiet >/dev/null 2>&1 || true
  rmdir "$DMG_MOUNT" 2>/dev/null || true
}
trap cleanup_dmg_mount EXIT
hdiutil attach "$DMG_PATH" -nobrowse -readonly -mountpoint "$DMG_MOUNT" >/dev/null
SHIPPED_WEBDIST="$DMG_MOUNT/SplitStep.app/Contents/Resources/splitstep-server/_internal/web_dist"
if ! DIFF_OUT=$(diff -rq "web/dist" "$SHIPPED_WEBDIST" 2>&1); then
  echo "FATAL: the dmg's web bundle is not byte-identical to the one this build just produced in web/dist" >&2
  echo "$DIFF_OUT" | sed 's/^/       /' >&2
  echo "       Run: touch src-tauri/build.rs && rm -rf src-tauri/target/aarch64-apple-darwin/release/bundle && cargo tauri build" >&2
  exit 1
fi
echo "    dmg web bundle matches web/dist byte-for-byte"

echo "==> done"
ls -lh src-tauri/target/aarch64-apple-darwin/release/bundle/dmg/*.dmg
