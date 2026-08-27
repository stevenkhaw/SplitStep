# Phase 3 — Tauri shell, PyInstaller sidecar, `.dmg`

Design settled 2026-08-27 with Steven, in four question rounds. This document
records what was decided and, where the decision was not obvious, why the
alternative lost. The parent spec is
`2026-08-26-mac-app-distribution-design.md`; this refines its Phase 3 section
and supersedes it where the two disagree.

## Goal

A `.dmg` a non-technical friend can be handed on a drive, double-click, point
at a folder, and use — with no terminal, no Homebrew, no conda, no `npm`.

## The decision that shapes everything else: the front layer

The parent spec assumed the shell would pick a library with a native dialog
and then start the server. In practice the library chooser is not a dialog,
it is **a screen with an argument to make** — where should ~100 GB of video
live, and why would you put it somewhere other than the default. That copy
does not fit in a system dialog.

It also cannot be a route in the existing SPA, and the reason is structural:
`Library.open()` refuses to run without a `library.db`, so there is no Python
server to serve a page whose entire purpose is to find out which library the
server should open. Chicken and egg.

So the front layer is **rendered by Tauri from its own asset bundle, before
the sidecar exists**. Only once a library is chosen does the shell spawn
`serve --library <path>` (with `--create` if the folder is new) and swap the
window to `http://127.0.0.1:<port>`.

```
launch
  |
  +-- config has a library, and it is reachable? ---- yes --> spawn sidecar --> app
  |                                                             (window points at 127.0.0.1)
  no
  |
  v
front layer (Tauri asset protocol, no Python running)
  - known libraries, unreachable ones greyed out
  - Choose Folder (native picker)
  - Create new, path pre-filled ~/Movies/SplitStep, free space shown
  |
  v
library chosen --> config written --> spawn sidecar --> app
```

**It is skipped on a normal launch.** Configured library, drive plugged in,
straight into the app. The chooser is for the first run, for a missing drive,
and for a deliberate switch — not a toll booth on every launch.

### Why a second Vite entry, and not something simpler

The front layer is built from `web/src/launcher/` as a second Vite entry
sharing `app.css`. A hand-written standalone HTML file would have been fewer
moving parts, but it would duplicate the design tokens, which CLAUDE.md
forbids in as many words — and a duplicated token set drifts. Native Rust UI
would put a third UI technology in the repo and turn every copy change into a
Rust edit and a rebuild.

### Not porting to Rust

Raised and rejected during design. Recorded here because it will be raised
again.

Tauri *is* a webview — that is its premise — so "port to Rust and drop the
web tier" means abandoning Tauri too, for egui/iced/slint. It would discard
~3,900 lines of tested pure-TS logic, ~5,900 lines of components, and ~9,900
lines of tests covering the queue state machine, undo stack, timeline math,
quad geometry, `split.ts` and `LabelWriter`. It would require rebuilding
`VideoDeck` — playing a source between in and out points, preloading the next
span across sources — against a media stack with no `<video>` and no `/media`
206 range support to seek against. And it would not remove Python: YOLO,
ffmpeg orchestration, sqlite and the segmenter stay, so a native UI would
still be an HTTP client of a Python sidecar.

There is no lock-in cliff to beat. The frontend talks to the API over HTTP;
that seam stays open. Shipping Tauri forecloses nothing.

## Decisions

| | Decision | Alternative rejected |
|---|---|---|
| Architecture | arm64 only | Universal doubles the freeze and cross-builds torch for x86_64 on an arm64 host |
| Signing | Unsigned, Gatekeeper workaround documented | $99/yr account is an errand, not a code problem; can be added later as config |
| Frontend | Keep Svelte, Tauri as shell | See above |
| Front layer | Second Vite entry, Tauri asset protocol | Standalone HTML duplicates tokens; native Rust adds a third UI stack |
| Front layer shown | Only when no reachable library | Always-show adds a click to every launch forever |
| Known libraries | One current + a list | Re-picking a path by hand every switch |
| Changing library | Re-point only, never move files | Moving ~100 GB across an external drive has real mid-copy failure modes |
| Default path | `~/Movies/SplitStep` pre-filled, free space shown, Continue required | A silent default fills a 256 GB internal disk without anyone seeing it |
| First run | Two screens: choose library, then add video | Merging them couples the drop to a server that is not running yet |
| ffmpeg | Static arm64 from evermeet.cx + credits note | Homebrew's are dynamically linked; building from source costs hours and disk |
| Icon | Generated mark (Pillow) | Tauri's default logo reads as unfinished |
| Switch in-app | Settings → Change library… → front layer | A direct picker bypasses the known-libraries list |
| Delivery | The file itself, AirDrop or a drive, plus a README | No hosting needed for an audience of one |
| Update story | Re-download. Migrations are forward-only | Unchanged from the parent spec |

## Shell responsibilities

**v1:**

- Spawn the sidecar, health-check it, point the window at it.
- Single instance (Tauri plugin) and graceful SIGTERM on quit. Two workers on
  one `library.db` is the enqueue race the parent spec names; the plugin is
  the first line of defence and Phase 1's fix is the second.
- Native folder picker, driven from the front layer's button.
- Power assertion while jobs are pending, so App Nap does not throttle a
  fifteen-minute detect under a hidden window.
- Relaunch dialog when the sidecar dies. Without it a crashed Python process
  leaves a blank window and no explanation.
- Native notification when detection finishes, by polling `/api/jobs`. No
  server change needed. It shares one poller with the power assertion, which
  needs the same answer to the same question — are any jobs pending.

**Deferred:** dock-icon drop into `_inbox/`. The in-app drop zone already
reaches the same place, so this is a second path to one destination.

## Port handshake

The shell binds `127.0.0.1:0`, reads the port the OS assigns, drops the
listener, and passes it as `--port`. If the health check does not answer, it
retries with a freshly assigned port.

Two alternatives lost. A fixed port collides with a dev `splitstep serve` on
8420, which is the one machine where the app is guaranteed to be tested. A
stdout handshake — sidecar prints its port, shell parses — is more airtight
against the microsecond race between dropping the listener and the child
binding, but it requires a Python change to a startup path that currently has
no shell-specific behaviour in it at all, and the retry covers the race.

Health check is `GET /api/config`. It already exists, it is cheap, and it
answers as soon as the app is serving.

## Sidecar

PyInstaller **onedir** from a locked requirements file. Pinned, not
re-resolved: a fresh dependency resolve risks versions the detector was never
validated against.

Inside the bundle, flat at `sys._MEIPASS`, which is the layout
`splitstep/resources.py` already assumes and is the only place it is written
down: `ffmpeg`, `ffprobe`, `yolo11n.pt`, `web_dist/`.

Expected size 1.5–2 GB; torch dominates.

`resources.py` needs no change. Its bundle-first-with-PATH-fallback shape was
built in Phase 1 for exactly this.

## Config schema

`~/Library/Application Support/splitstep/config.json` gains one key. It is
already the file holding `library` and `mode`, and `appconfig.save_config`
already writes it atomically via temp-file + `os.replace`.

```json
{
  "library": "/Volumes/SanDisk_2TB/SplitStep",
  "libraries": ["/Volumes/SanDisk_2TB/SplitStep", "/Users/x/Movies/SplitStep"],
  "mode": "friend"
}
```

`libraries` is a most-recent-first list of paths opened before. Unreachable
entries are shown greyed rather than pruned — a drive that is merely
unplugged must not be forgotten. `library` stays the single source of truth
for which one is current, so `resolve_library` is untouched and every CLI
path behaves exactly as it does today.

## Error states

| State | What happens |
|---|---|
| No library configured | Front layer, first-run copy |
| Configured library unreachable | Front layer with a "not connected" banner naming the path, Retry, and the picker |
| Chosen folder already holds a `library.db` | Opened, not created. `--create` is never passed |
| Chosen folder is new | `--create` passed, alongside the freshly-picked `--library` |
| Sidecar dies | Native dialog offering relaunch |
| Sidecar never answers health check | Three attempts, each on a freshly assigned port, 20 s apiece; then a dialog with the captured stderr |

## Testing

- Existing suites untouched: 837 pytest, 628 vitest.
- New vitest over the launcher's pure logic in `web/src/lib/` per the house
  rule — known-library list ordering and reachability, path validation,
  free-space formatting (reusing `reels.formatBytes`), first-run vs
  switch-library copy selection.
- New pytest for the `libraries` list in `appconfig` — append, dedupe,
  most-recent-first, and tolerance of a corrupt or absent key.
- Rust unit tests for port selection and the health-check retry loop.
- The `.app` itself: a manual smoke checklist in a fresh macOS user account
  (poor man's clean machine) — copy → open → Gatekeeper → pick folder →
  drop file → wizard → review → export clip → build reel.

## Licensing

Two obligations ship in an in-app credits note: **Ultralytics is AGPL-3.0**
and the **evermeet.cx ffmpeg builds are GPL**. The public repo satisfies the
source obligation if distribution ever widens beyond handing someone a file.

## Out of scope

Auto-update, a Windows or Linux build, code signing and notarization,
dock-icon drop, and any change to detection. Gate 0's human half is
independent of this phase and remains open.
