# SplitStep

Local tennis rally cutter. Ingests phone footage, segments it into rallies
using motion + audio detection, and serves a review UI for starring/rejecting
rallies and tuning the segmentation. Everything — the API, the media server,
and the review UI — runs as one process: `splitstep serve`.

## Setup

```bash
conda create -n splitstep python=3.12 -y
conda activate splitstep
pip install -e ".[dev]"
brew install ffmpeg
```

The review UI is a Svelte app that gets built once and served by the Python
backend — it needs Node (developed against Node 25) only for the build step,
not at runtime:

```bash
cd web
npm install
npm run build          # output goes to web/dist and is served by `splitstep serve`
```

## Library

The library is a self-contained folder, normally on an external drive:

```
/Volumes/SplitStep/
  library.db
  _inbox/               drop videos here; failed ones land in _inbox/failed/
  sessions/<date>/sources/NN/{original,proxy.mp4,thumbs.jpg,features.jsonl}
  reels/
```

Create it once with `splitstep init` — the app never creates a library
implicitly:

```bash
splitstep --library /Volumes/SplitStep init
```

`Library.open` (used by every other command) refuses to start unless
`library.db` already exists at that path. This is deliberate: a leftover,
empty mountpoint after an unclean eject of the external drive looks exactly
like a valid, empty library root — `os.access`/`is_dir` both pass — and
without this guard the app would silently create a second library on the
internal SSD instead of erroring. `init` itself also refuses if a library
already exists at the path, so it can't clobber one by accident.

## Running it

```bash
splitstep --library /Volumes/SplitStep doctor      # check hardware + paths
splitstep --library /Volumes/SplitStep serve       # http://127.0.0.1:8420
```

`serve` starts the FastAPI app, the background job worker, and the inbox
watcher in one process, and serves the built `web/dist` bundle at `/` — open
`http://127.0.0.1:8420` and the UI, the `/api/*` routes, and `/media/*`
(proxy video, frame thumbnails) all come from that one port. If `web/dist`
hasn't been built yet, `serve` still runs — you just get the API with no UI,
and a log warning telling you to run `npm run build`.

Full CLI surface (`splitstep --help`):

```
init                create a new library tree and database
doctor              show detected hardware and library state
serve               run the web server, worker and inbox watcher
ingest              queue a video file for ingest
detect              queue detection for a source
segment             re-segment cached features
preset add/list      manage court presets
source set-preset    manage sources
```

## The ingest -> detect -> review -> tune loop

1. **Ingest.** Drop a video in `_inbox/`. The watcher picks it up within 5
   seconds of the file settling (no more size growth), transcodes it to a
   1080p proxy, extracts a thumbnail sheet, and queues detection
   automatically. You can also queue (or force) it from the CLI:

   ```bash
   splitstep --library /Volumes/SplitStep ingest /path/to/clip.mov --now
   ```

2. **Detect.** Runs YOLO person-detection + audio impact detection over the
   proxy, builds a feature stream, and segments it into rally intervals. If
   it's already run once, cached features let you re-segment instantly (see
   step 4) without touching YOLO again.

3. **Review.** Open the session in the UI. Queue mode plays each rally in
   order — star the good ones, reject the junk, keep going.

4. **Tune.** Detection caches per-frame features to `features.jsonl`, so
   re-segmenting at a different threshold costs milliseconds and needs no
   GPU:

   ```bash
   splitstep --library /Volumes/SplitStep segment <source_id> --threshold 0.35 --dry-run
   ```

   Drop `--dry-run` to write the new intervals as the source's rallies (this
   carries starred/rejected flags across via the >50% overlap rule). The
   Timeline mode's re-segment panel does the same thing from the UI, with a
   live rally-count and score-curve preview as you drag the slider.

### Failed ingests

A file that fails to ingest (bad codec, corrupt container, ffmpeg error, disk
full, etc.) is moved to `<library>/_inbox/failed/`, alongside a
`<name>.error.txt` with the exception. Its source and session rows (if they'd
already been created) are marked `status = "failed"`. It is **not** silently
retried on every watcher scan — a file in `_inbox/failed/` needs a human to
look at it and re-drop it (or a fixed copy) into `_inbox/` to try again.

## The play-region step

Without a play region, detection runs on the whole frame — if the shot
includes an adjacent court, players on it get picked up as if they were on
yours. Setting a region is one manual step per source, not automatic.

**From the UI:** open a session, scroll to "Play region," drag the four
corner handles over the area both players move in (extended to the bottom of
frame, so the near player's feet stay inside even when they're partly out of
shot). Click "Save & assign," or reuse a preset you already made for another
source from the same camera setup.

**From the CLI:**

```bash
splitstep --library /Volumes/SplitStep preset add --name "court 3" \
  --quad "0.1,0.3 0.9,0.3 1.0,1.0 0.0,1.0"
splitstep --library /Volumes/SplitStep preset list
splitstep --library /Volumes/SplitStep source set-preset <source_id> <preset_id>
```

`--quad` takes four normalized (0-1) points as `x,y` pairs, space-separated.

**Either way, detection must be re-run after assigning a preset.** Existing
rallies on that source were computed against the old region (or the default
whole-frame region) and do not change retroactively. Re-run detect without
`--reuse-features` — that flag skips straight to re-segmenting the *old*
cached features and will not pick up the new region:

```bash
splitstep --library /Volumes/SplitStep detect <source_id> --now
```

## Keybindings

Queue mode (`web/src/components/QueueMode.svelte`):

| Key | Action |
|---|---|
| `S` | star |
| `X` | reject |
| `R` | replay |
| `→` | skip to next rally |
| `←` | back to previous rally |
| `U` | undo |
| `1` `2` `3` | playback speed 1x / 1.5x / 2x |
| `space` | play / pause |
| `T` | open timeline mode on the current rally |

Timeline mode (`web/src/components/TimelineMode.svelte`):

| Key | Action |
|---|---|
| `[` | set the rally's in-point to the current playhead |
| `]` | set the rally's out-point to the current playhead |
| `,` | step back one frame |
| `.` | step forward one frame |
| `space` | play / pause |
| `esc` | close timeline, back to queue |

## Development workflow

For UI development, run the backend and Vite separately — Vite's dev server
proxies `/api` and `/media` through to the backend (see `web/vite.config.ts`),
so both data and video work with hot reload, and you don't have to rebuild
the bundle after every change:

```bash
splitstep --library /Volumes/SplitStep serve   # terminal 1: API + media on :8420
cd web && npm run dev                            # terminal 2: UI on http://localhost:5173
```

For anything that isn't UI iteration (checking the real integration, a demo,
normal use), build once and run `serve` alone — that's the FastAPI-served
path described above, and it's what `splitstep serve` gives you without Vite
running at all.

## Tests

Backend (pytest; YOLO is never run in tests — detector output is fixtured or
mocked throughout):

```bash
~/miniconda3/envs/splitstep/bin/pytest -q
~/miniconda3/envs/splitstep/bin/ruff check splitstep tests
```

Frontend:

```bash
cd web
npx vitest run     # unit tests: queue state machine, timeline math, time
                    # formatting, undo stack, persistence, quad geometry — all
                    # pure TypeScript. Components are thin shells over them and
                    # are verified by hand, since jsdom has no <video> element
                    # implementation.
npm run check       # svelte-check: type errors and a11y warnings
```

## Current state

**Plan 1 (backend core), Plan 2 (review UI) and 4K clip export are complete.**
Ingest, transcode, detection (vision + audio), segmentation, the job queue, the
inbox watcher, the REST API, the full review UI (queue mode, timeline mode,
re-segment tuning, play-region editor), and cutting starred/point rallies to 4K
clips at the locked libx264 profile all work end to end and are served from a
single `splitstep serve` process.

**Still deferred:** reel building via `-c copy` concat and the cross-session
rally browser with filters. The `reels`/`reel_items` tables exist unused.

**Rejected, not deferred:** Reclaim Space (deleting originals once clips are
cut). The library lives on a 2TB external drive that holds ~110 hours of play
keeping everything, so the space it frees is not scarce, and the 4K source it
destroys is the one thing the tree cannot rebuild.

**The most important caveat: the detector does not currently produce
trustworthy rally boundaries on ground-level footage.** One real 19.5-minute
session was ingested and the detector validated against it on 2026-08-20. The
result was negative, and the reasons are now measured rather than suspected:

- **Camera height decides everything.** With the phone about a foot off the
  ground, the far half of the court compresses into a ~2%-tall band at the
  horizon, so the "far player" the two-player model scored was whoever happened
  to be standing on an adjacent court. `detect/viewpoint.py` now classifies each
  source's viewpoint and routes low cameras to a different scoring profile.
- **That replacement profile failed its own validation.** Camera setup and
  teardown score as rallies, and confidence is *inverted* — the known-false
  clips scored higher than the known-true ones — so no threshold separates them.
- **Audio impact detection works; it just measures the wrong thing.** Not wind —
  neighbours. Impacts fire at 0.56/sec across windows verified swing-free by a
  pose track, against 0.67/sec while actually playing — and two of those idle
  windows individually beat a confirmed rally's rate. Stereo direction and
  spectral timbre were tested too, and both fail.
- Still genuinely unverified: playback of a real 4K-derived 1080p proxy end to
  end in the UI.

Tuning constants are now calibrated against `tests/fixtures/ground_level_source01.jsonl`,
a committed slice of real footage — calibrating against synthetics is what
produced the bug where every clip came out one hit long. Full findings:
`docs/superpowers/plans/2026-08-20-camera-viewpoint-validation.md`.

**The next step is a fence-mounted session**, not more tuning. Mounting the
phone high enough to see both players at different depths is the only change
that gives the detector a real signal; the viewpoint classifier will switch
profiles on its own.

## First real use

1. Shoot a session — 4K30, HDR off, AE/AF locked, phone **as high as you can**
   (a fence mount, not propped on the court surface — this is the single
   biggest determinant of whether detection works at all)
   mount it.
2. Drop it in `_inbox/`, wait for ingest and detect.
3. Open the session, drag the play region over the court, save it.
4. Re-run detect from the CLI so it uses the region (see "The play-region
   step" above).
5. Review in queue mode. Note whether rallies are being missed (raise
   recall — lower the threshold) or whether there is too much junk (raise
   it).
6. Use the re-segment slider to sweep, watching the rally count and the
   score curve.
7. Copy that source's `features.jsonl` to `tests/fixtures/` — done once already
   as `ground_level_source01.jsonl`, a 1200-frame slice, which is what the
   detector's constants are now calibrated against. **Hand-labelled intervals
   are still missing**, and their absence is exactly what let the 2026-08-20
   validation go wrong: audio-impact clusters were used as a stand-in for
   ground truth, and they turned out to measure the venue rather than the
   player. Labelling even a few minutes by hand would be worth more than any
   further threshold sweep.
