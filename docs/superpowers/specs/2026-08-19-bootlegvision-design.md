# BootlegVision — Design

**Date:** 2026-08-19
**Status:** Approved, ready for implementation planning

---

## 1. Overview

A local tennis video tool. Drop a session recording in, get it automatically split into rallies, review them in a fast keyboard-driven pass, star the good ones, and compile starred rallies into highlight reels.

Deliberately a small fraction of what SwingVision does. No ball tracking, no in/out calls, no stroke classification, no court homography, no analytics. One hard problem — rally segmentation — wrapped in straightforward CRUD.

### Goals

1. Ingest an hour of phone footage without manual scrubbing.
2. Automatically propose rally boundaries with high recall.
3. Review and star an entire session in under 5 minutes.
4. Compile starred rallies from any session into named reels.
5. Run on whatever machine is nearby, with the library on an external drive.

### Non-goals (explicit)

Ball tracking · in/out calls · stroke classification · score tracking · vertical 9:16 export · authentication · sharing · multi-user · cloud services · doubles optimization · native mobile app · reel music, titles, or transitions · live streaming.

**Approach C (a learned segmenter) is out of scope but designed for.** The schema and feature logs accumulate its training set from the first session; nothing needs backfilling later.

---

## 2. Runtime and topology

One Python process. Web server and job worker in the same app.

```
bootleg serve --library /Volumes/BootlegVision

  FastAPI (uvicorn)          :8420
   ├── /api/*                REST
   ├── /media/*              video over HTTP range requests (206)
   └── /                     built Svelte SPA (static)

  Worker thread              claims one job at a time from the jobs table
  Inbox watcher thread       watchdog on <library>/_inbox/
```

No Docker, no Redis, no Celery. `^C` stops it.

A future `bootleg worker --library ...` on a second machine is the same code with the web server disabled. Designed for, not built.

### Hardware abstraction

A single module, `accel.py`, detects the host once at startup. Every other module calls `accel.encoder()` / `accel.device()`; no hardware branching exists anywhere else.

| Host | ffmpeg | torch |
|---|---|---|
| M2 Mac | `-hwaccel videotoolbox`, `h264_videotoolbox` | `mps` |
| Beelink N100 | `-hwaccel qsv`, `h264_qsv` | `cpu` |
| RTX 4070Ti | `-hwaccel cuda`, `h264_nvenc` | `cuda` |

### Stack

**Backend** — Python 3.12 (pinned; 3.13 torch wheels are still unreliable), FastAPI, uvicorn, stdlib `sqlite3` with explicit SQL, ultralytics (YOLO11), opencv-python-headless, numpy + scipy (audio onset detection), ffmpeg via subprocess.

Environment: `conda create -n bootleg python=3.12`.

**Frontend** — Vite, Svelte 5, TypeScript, Tailwind v4, hand-rolled hash router, raw `<video>` element.

**Svelte 5 runes only.** `$state` / `$derived` / `$effect` / `$props`. No `export let`, no `$:`, no legacy stores. A file that drifts to Svelte 4 idioms is a defect, treated the same as a type error.

**No SvelteKit** — four routes does not justify SSR, adapters, or load functions.
**No TanStack Query** — a local app with ~8 endpoints does not need cache-invalidation machinery. Plain `fetch` wrapper plus `$state` stores.
**No video player library** — video.js, Plyr, and react-player all exist to supply a chrome that would be deleted. A raw `<video>` with custom controls is less code here, not more.

**External dependency:** ffmpeg must be installed (`brew install ffmpeg`).

---

## 3. Storage

The external drive *is* the library — a single self-contained portable folder. Unplug it from the Mac, plug it into the mini PC, run the same command.

```
/Volumes/BootlegVision/
  library.db                       SQLite, WAL, synchronous=FULL
  _inbox/                          drop videos here
  sessions/<id>/                   one calendar date of play
    sources/01/
      original.mov                 HEVC from phone; deletable
      proxy.mp4                    1080p H.264, GOP 30; permanent
      thumbs.jpg                   sprite sheet
      features.jsonl               per-frame visual + audio features
    sources/02/ ...                further recordings from the same day
    clips/rally-007.mp4            starred rallies only, 4K, fixed profile
  reels/<slug>.mp4
```

The app refuses to start if the library path is not mounted and writable, naming the path in the error. It never auto-creates the tree — doing so would silently build a second empty library on internal storage.

### Retention

**Keep everything by default. Nothing auto-deletes.** A 2TB drive removes the storage pressure that motivated an automatic prune policy.

Per hour of footage, regardless of how many source files it arrived in:

| | 4K30 | 4K60 |
|---|---|---|
| original | 10 GB | 26 GB |
| 1080p proxy | 4 GB | 4 GB |
| starred 4K clips | ~2 GB | ~2 GB |
| **total** | **~16 GB** | **~32 GB** |

2TB usable (~1900 GB) holds roughly **115 sessions at 4K30** or **59 at 4K60** keeping everything, and **~315** once originals are reclaimed.

**Reclaim Space** is a button, not a policy. It deletes `original.mov` for a source and sets `sources.has_original = 0`. It refuses to run if any starred rally from that source lacks an exported clip — otherwise it would destroy the only 4K source for a rally that was explicitly marked worth keeping. Proxy and clips are never touched, so a reclaimed session stays fully browsable and re-editable at 1080p forever.

The 1080p proxy is the insurance copy. Detection already runs on the proxy, so reclaiming an original costs only one thing: the ability to export a 4K clip from a rally that was never starred. Everything else — browsing, re-segmenting, re-cutting, 1080p export — still works.

---

## 4. Data model

SQLite, WAL mode, `synchronous=FULL`. Explicit SQL, numbered migration files, no ORM.

`synchronous=FULL` is normally over-cautious, but this database lives on a bus-powered drive that can be bumped loose. Write volume is a few rows per rally, so the durability costs nothing measurable and prevents losing a whole review pass.

```sql
CREATE TABLE sessions (
  id          TEXT PRIMARY KEY,            -- '2026-08-19', or '2026-08-19-b' if split
  title       TEXT NOT NULL,
  played_on   TEXT NOT NULL,               -- calendar date; the grouping key
  status      TEXT NOT NULL,               -- ingesting|detecting|ready|reviewed|failed
  created_at  TEXT NOT NULL
);

-- One recording. A session has one or more, ordered by recorded_at.
CREATE TABLE sources (
  id              TEXT PRIMARY KEY,
  session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  idx             INTEGER NOT NULL,        -- 1-based within session
  recorded_at     TEXT NOT NULL,           -- container metadata, falls back to mtime
  offset_ms       INTEGER NOT NULL,        -- position on the virtual session timeline
  duration_ms     INTEGER NOT NULL,
  width           INTEGER NOT NULL,
  height          INTEGER NOT NULL,
  fps             REAL    NOT NULL,
  original_name   TEXT,                    -- NULL once space is reclaimed
  has_original    INTEGER NOT NULL DEFAULT 1,
  court_preset_id TEXT REFERENCES court_presets(id),
  status          TEXT NOT NULL,           -- ingesting|ingested|detecting|ready|failed
  UNIQUE(session_id, idx)
);

CREATE TABLE rallies (
  id            TEXT PRIMARY KEY,
  session_id    TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  source_id     TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  idx           INTEGER NOT NULL,          -- 1-based order within session, across sources
  start_ms      INTEGER NOT NULL,          -- LOCAL to source; possibly trimmed by hand
  end_ms        INTEGER NOT NULL,
  det_start_ms  INTEGER NOT NULL,          -- detector's original output, NEVER mutated
  det_end_ms    INTEGER NOT NULL,
  confidence    REAL    NOT NULL,
  starred       INTEGER NOT NULL DEFAULT 0,
  rejected      INTEGER NOT NULL DEFAULT 0,  -- soft delete
  reviewed_at   TEXT,
  clip_path     TEXT,                      -- set once the 4K clip is exported
  UNIQUE(session_id, idx)
);

CREATE TABLE court_presets (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL,                -- 'Memorial Park court 3, near baseline'
  quad       TEXT NOT NULL,                -- JSON [[x,y] x4], normalized 0-1
  created_at TEXT NOT NULL
);

CREATE TABLE reels (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  slug          TEXT NOT NULL UNIQUE,
  rendered_path TEXT,
  rendered_at   TEXT,
  dirty         INTEGER NOT NULL DEFAULT 1,  -- contents changed since last render
  created_at    TEXT NOT NULL
);

CREATE TABLE reel_items (
  reel_id  TEXT NOT NULL REFERENCES reels(id) ON DELETE CASCADE,
  rally_id TEXT NOT NULL REFERENCES rallies(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  PRIMARY KEY (reel_id, rally_id)
);

CREATE TABLE jobs (
  id           TEXT PRIMARY KEY,
  type         TEXT NOT NULL,              -- ingest|detect|clip|reel
  payload      TEXT NOT NULL,              -- JSON
  status       TEXT NOT NULL,              -- queued|running|done|failed
  progress     REAL NOT NULL DEFAULT 0,
  error        TEXT,
  heartbeat_at TEXT,
  created_at   TEXT NOT NULL,
  finished_at  TEXT
);

CREATE INDEX idx_rallies_session ON rallies(session_id, idx);
CREATE INDEX idx_rallies_source  ON rallies(source_id);
CREATE INDEX idx_sources_session ON sources(session_id, idx);
CREATE INDEX idx_rallies_starred ON rallies(starred) WHERE starred = 1;
CREATE INDEX idx_jobs_queued     ON jobs(status, created_at);
```

### Decisions embedded in the schema

**`det_start_ms` / `det_end_ms` are immutable.** Every hand-dragged boundary permanently records the delta between the detector's guess and the correct answer. That is the training set for a learned segmenter, accumulating from day one with no extra UI and no extra work.

**`rejected` is a soft delete.** A false positive is a labeled negative — the most valuable data for improving detection. Rejected rallies disappear from the UI and stay in the database.

**Sessions group by calendar date; sources hold the files.** Play is often split across several recordings in one outing. Rallies never span a recording gap, so each rally belongs to exactly one source and stores local timestamps; a virtual session timeline is derived from cumulative `offset_ms`. Both views are available without duplicating time data.

**Court preset is per source, not per session.** The phone gets repositioned between recordings.

**No users table, no authentication.** A library on a drive that travels with you removes the multi-user story. If remote access happens later, it is one middleware and one table.

### Deliberately cut

**Per-reel trim overrides** (`reel_items.trim_start_ms`). Rally bounds are the bounds. Fixing a bad cut once improves every reel that uses it.

**`shot_count`.** Would need shot detection that is out of scope, and counting player direction reversals is a poor proxy. Duration is a strong enough filter — a 30-second rally *was* a long rally.

---

## 5. Detection pipeline

### The structural decision

Expensive and cheap work are separated into two stages:

```
detect   (minutes, GPU)      proxy.mp4 → features.jsonl      run once, cached forever
segment  (~200 ms, pure fn)  features.jsonl + params → rallies
```

The segmentation weights will be wrong on the first attempt. They are being chosen for a camera angle, a court, and two players nobody has measured. Two or three tuning rounds on real footage should be expected.

This split makes each round cost 200 ms instead of 15 minutes, which is the difference between converging and giving up. It also makes a learned segmenter a drop-in replacement over the same feature files, applied retroactively to every session ever ingested.

### Stage 0 — Ingest

```
ffprobe                              → duration, fps, dimensions, recorded_at
ffmpeg -hwaccel <accel> ...          → proxy.mp4   1080p H.264, -g 30, CRF ~21
ffmpeg -vf fps=1/10,tile=...         → thumbs.jpg  sprite sheet
```

Ingest runs per source file. A new file is assigned to the session matching its recording date, creating one if none exists, and `offset_ms` is set to the cumulative duration of prior sources.

**The proxy must be H.264, not HEVC.** Browser HEVC support is inconsistent — Safari yes, Chrome only with hardware decode on some platforms, Firefox largely no. The originals stay HEVC for storage; the proxy is H.264 so the review UI is never a black rectangle on some machine.

**The proxy uses a short GOP** (`-g 30`, a keyframe every second at 30 fps). Roughly 15% larger, and scrubbing snaps instead of lurching. Queue mode seeks constantly and the timeline drags boundaries; long GOPs make both feel broken.

### Stage 0.5 — Play region

The user drags four corners over frame 1. Not the court lines — the region both players actually move in, extended down to the bottom of frame so the near player's feet stay inside when standing between the camera and the baseline.

Stored as a `court_preset`, normalized 0-1, reusable across every session shot from the same spot. This one manual step is what makes adjacent courts disappear from detection.

Automatic court-line detection was rejected: it is a substantial project on its own, and at roughly 1 ft camera height the lines converge into a sliver.

### Stage 1 — Detect

```
ffmpeg -hwaccel X -i proxy.mp4 -vf fps=5,scale=960:-2 -f rawvideo -pix_fmt rgb24 -
  → YOLO11n, classes=[0] (person), imgsz=960
```

**5 fps sampling.** Rally boundaries need roughly 200 ms precision and are padded anyway. One hour is 18,000 frames instead of 108,000.

| | throughput | 1 hr session |
|---|---|---|
| M2, MPS, yolo11n | ~40 fps | ~8 min |
| M2, MPS, yolo11s | ~20 fps | ~15 min |
| 4070Ti, yolo11s | ~200 fps | ~90 s |

Start with `yolo11n`; escalate to `yolo11s` only if far-player recall is poor.

**No object tracker.** ByteTrack was considered and rejected — at 5 fps a sprinting player moves one to two body widths between samples and track IDs go unstable. Instead, detections are split into near-half and far-half **by apparent bounding-box height**; at this camera angle the near player's box is 2–4× the far player's. The largest box in each half is taken. No tracker state, robust to a spectator wandering through.

### Stage 1b — Audio impacts

Ball contact is audible on this footage; it is noisy, not absent. Audio is therefore a **secondary feature channel**, never a primary detector.

```
ffmpeg -vn -ac 1 -ar 22050 -f s16le      raw PCM, streamed
  → butterworth highpass @ 800 Hz        wind is low-frequency; ball contact is a broadband transient
  → 10 ms RMS envelope
  → adaptive peak pick                   local max above median + k·MAD over a 2 s window
  → hit timestamps + strengths           aggregated onto the same 5 Hz grid
```

numpy and scipy only — no librosa. Seconds per hour of audio.

Two derived features:

- `hit_rate(t)` — impacts in the trailing 1 s
- `hit_regularity(t)` — inverse standard deviation of the last four inter-hit intervals. A rally has a metronomic quality that nothing else on a public court produces

**Why this earns its place:** it is strongest exactly where the visual features are weakest — the *end* of a rally. Players keep moving for a second or two after a point dies, so visual activity decays slowly; the last ball contact followed by ~1 s of silence is a sharp boundary. Adjacent courts are farther from the mic and gate out on amplitude.

**Downside is bounded.** If audio proves useless on real footage, its weights fit to zero and nothing is lost — the two-stage split means discovering that costs 200 ms, not a re-run.

### Feature record

One line per sampled frame into `features.jsonl`:

```json
{"t":41200,"n":2,"near":{"cx":0.42,"foot":0.88,"h":0.31,"v":1.8},
                "far":{"cx":0.55,"foot":0.41,"h":0.09,"v":2.1},
                "hits":2,"hit_reg":0.81}
```

`v` is speed in **body-lengths per second** — normalizing by box height makes near and far players directly comparable despite perspective.

### Stage 2 — Segment

**A rally is any continuous stretch of hitting**, from the first ball struck to the last. Warmup counts. Points count. The detector does not distinguish them, and does not need to.

```
score(t) = w1·both_present
         + w2·min(near_v, far_v)        both moving, not one player retrieving a ball
         + w3·lateral_fraction(near)    rallies move you sideways; walking to the fence is longitudinal
         + w5·hit_rate(t)               audio: ball contact
         + w6·hit_regularity(t)         audio: the metronome of a rally
         - w4·either_outside_region
```

1. Rolling median, ~1 s window
2. Threshold → binary
3. **Close gaps < 1.5 s** — lobs and lulls are not rally ends
4. **Drop segments < 1.5 s** — low enough to keep aces. An ace runs about 2 s and is precisely the kind of point worth starring; a 3 s floor would silently discard them
5. **Pad −0.3 s / +0.5 s** — tight cuts. Start near the serve strike, cut shortly after the point dies
6. `confidence` = mean score over the segment

**The threshold is set deliberately low. Over-segmentation is the intended behavior.** Rejecting a false rally costs one keystroke; a missed rally is invisible forever and unrecoverable without rescrubbing an hour of footage. 60 segments of which 42 are real is a better outcome than 38 clean ones.

This is affordable only because the review pass already exists — every rally is watched anyway in order to star it.

### Known limitations, stated plainly

**Warmup is not a limitation.** An earlier draft treated warmup as noise to be filtered. It is not — a rally is defined as continuous hitting, so warmup rallies are wanted output. This removed the hardest open problem in the pipeline by correcting the requirement rather than the algorithm.

**Doubles will be mediocre.** The pipeline assumes two players. With four bodies it takes the largest in each half and the speed features get noisy.

---

## 6. Review UI

Two modes, one keypress apart. Queue is the default; timeline is where you go when a boundary is genuinely wrong.

### Queue mode

**Dual `<video>` elements, A/B swap.** A plays rally N while B is already pointed at the proxy, seeked to rally N+1's start, paused and buffered. On advance, visibility swaps, B plays, and A is repointed at rally N+2.

This is what makes queue mode work at all. A single element seeking between rallies stalls roughly 400 ms on every advance, and gaps between points can be a minute or more. Preloaded, advance is instant and the review rhythm never breaks. Because each `<video>` carries its own `src`, a rally from source 02 preloads behind one from source 01 with no extra machinery.

**Boundary detection uses `requestAnimationFrame`, not `timeupdate`.** `timeupdate` fires about four times a second, which would overshoot each cut by up to 250 ms — visibly. The RAF loop checks `currentTime >= end_ms` and lands within a frame. The same loop drives the progress bar by writing `style.transform` directly, never through Svelte state.

**Auto-advance is the default action.** Rally ends, next one starts. Doing nothing means "seen, not starred, not rejected" — the correct outcome for most rallies, at zero keystrokes.

| Key | Action |
|---|---|
| `S` | toggle star |
| `X` | reject (soft delete) |
| `R` | replay current |
| `→` | advance now |
| `←` | back one |
| `U` | undo last star / reject / skip |
| `1` `2` `3` | 1× / 1.5× / 2× |
| `space` | play / pause |
| `T` | open timeline on current rally |

**Undo is required, not optional.** When the primary interaction is a single keystroke at 2× speed, mis-keys are certain. Session-scoped undo stack, in memory.

**Resume is free.** `reviewed_at` per rally means reopening a session lands on the first unreviewed one. Every exit path from a rally sets `reviewed_at` — starring, rejecting, skipping with the arrow key, and auto-advance all count as seen. A session's `status` becomes `reviewed` once no rally in it has a NULL `reviewed_at`.

**End of queue:** a summary — seen, starred, rejected — with two actions: *Export starred clips (4K)* and *Add all starred to a reel*.

### Timeline mode

Three stacked bands, because one scale cannot do the job:

1. **Overview** — the whole session, every rally a block, starred ones highlighted. For orientation and jumping. A rally is about 3 px wide here, which is fine because no editing happens at this scale. Source boundaries appear as ticks labelled with the real elapsed gap (`+22 min`), but sources are laid out **contiguously** — rendering a 22-minute break to scale would waste half the overview on dead space.
2. **Zoomed band** — ±20 s around the current rally. Handles are ~9 px and grabbable. All editing happens here.
3. **Score curve** — the detector's `score(t)` beneath the zoomed band, with the threshold drawn as a dashed line.

The score curve is nearly free — the data is already in `features.jsonl` — and it converts "the detector is bad" into "the threshold is 0.1 too high, and I can see it." That is what makes the retuning loop converge instead of feeling like guesswork.

Edit keys: `[` `]` set in/out at the playhead, `,` `.` step one frame (`currentTime ± 1/fps`; there is no native frame-stepping API, but the short GOP makes this accurate), `esc` back to the queue.

### Re-segment

The session view has a threshold slider and a **Re-segment** button. It runs the pure segmentation function over cached `features.jsonl` — about 200 ms, no YOLO — and the rally count updates live as the slider moves.

**Stars survive re-segmentation.** A new segment overlapping an old starred segment by more than 50% inherits the star. Roughly ten lines, and without it retuning would destroy prior work, so retuning would stop happening.

**Manual boundary edits do not survive.** They are discarded, behind a confirmation naming how many would be lost.

---

## 7. Library, reels, export

### Routes

```
/                library — sessions, status, star counts
/s/:id           session — queue and timeline review
/rallies         starred rally browser, cross-session
/reels/:slug     reel builder
```

### Ingest UX

A watchdog observes `<library>/_inbox/`. When a file appears it **waits for the size to stabilize** — polling until unchanged for 3 seconds — before touching it. Skipping that check means eventually ingesting a half-copied 10 GB file and producing a corrupt session; it is the single most likely bug in the ingest path.

Then: read `recorded_at`, find or create the session for that calendar date, append a `sources` row with `offset_ms` set to the cumulative duration of prior sources, move the file into `sessions/<id>/sources/<idx>/`, and enqueue `ingest`.

A session `id` is the ISO date (`2026-08-19`), suffixed if you play twice in one day and want them separate (`2026-08-19-b`). Splitting a session and merging two are library-view actions; ingest never guesses.

Primary path is a local folder, optionally exported as a Samba share by the host OS (outside the app; the app only watches a directory) — iOS Files can write to SMB directly from the phone, and desktop is a drag and drop. A resumable browser upload (tus) posts into the same inbox; the watcher does not care how the file arrived. Browser upload is deferred until remote access actually exists.

### Encode profiles

Concat with `-c copy` only works when every clip has identical codec parameters. That constraint drives the encoder split:

| Stage | Encoder | Reason |
|---|---|---|
| proxy | hardware (VideoToolbox / QSV / NVENC) | an hour of video; speed matters, determinism does not |
| **clip** | **libx264, software** | 20 seconds of video; determinism matters, speed does not |
| reel | none — `-c copy` | instant |

**Clips are software-encoded on purpose.** Hardware encoders emit different SPS/PPS headers per vendor, so a clip cut on the Mac and one cut on the 4070Ti would fail to concat cleanly, or concat with artifacts. libx264 produces identical headers on every machine, permanently. A 20-second 4K clip takes roughly 30 seconds to encode in the background.

Locked clip profile — **changing it breaks `-c copy` against every previously exported clip**:

```
mp4 · H.264 High · yuv420p · 3840×2160 · 30 fps CFR · CRF 20 · AAC 128k 48 kHz stereo
```

Audio is kept. Ball contact and shoe squeaks carry a highlight reel.

Because the profile is fixed, sources that do not match it are conformed at clip time: 4K60 footage is decimated to 30 fps, and sub-4K footage is upscaled to 3840x2160 with aspect preserved and padded. This is intentional — a mixed-parameter clip library cannot be concatenated with `-c copy`, and that guarantee is worth more than avoiding an upscale.

Clips are cut from `sources/<idx>/original.mov` when the original is present, and from `proxy.mp4` when it has been reclaimed — in which case the clip is upscaled to the locked profile and flagged in the UI as 1080p-sourced.

**Safety net:** after concat, probe the output. If duration does not match the sum of the inputs, fall back to a full re-encode and log it. Silent `-c copy` failure is a known ffmpeg trap.

### Rally browser

Cross-session list of starred rallies. Filters: session, date range, minimum duration, confidence. Each row shows a thumbnail, duration, and session; hover scrubs, click previews. Select any set and add to a reel.

### Reel builder

Ordered list, drag to reorder, remove. **Preview plays the reel in-browser** by chaining clip files through the same dual-`<video>` component queue mode uses — second use of the same code. The actual cut is watched before rendering.

**Render** enqueues a `reel` job producing `reels/<slug>.mp4` and clears `dirty`. Changing contents sets `dirty` again.

---

## 8. Error handling

**The drive is the application.** Startup verifies the library path is mounted and writable, naming the path on failure. Mid-run unmount is caught by the worker, which pauses the queue and surfaces a banner; jobs resume on remount rather than cascading into failures.

**Jobs carry a heartbeat.** On startup, any `running` row with a stale heartbeat is requeued. This is safe because all four job types are idempotent — `ingest` overwrites the proxy, `detect` overwrites features, `clip` overwrites the clip, `reel` overwrites the mp4. No job appends, so no job double-applies.

**ffmpeg stderr is always captured.** A non-zero exit marks the job `failed`, stores stderr in `jobs.error`, and displays it verbatim. The failure to avoid is a job exiting non-zero and being marked done, leaving a zero-byte proxy that looks like a decode bug three days later.

**Free space is checked before any job that writes.** A 4K clip that dies at 90% is worse than a job that refuses to start.

**Bad input** — `ffprobe` failure marks the session `failed` and leaves the file untouched. Nothing is deleted.

---

## 9. Testing

**The segmenter is the only genuinely interesting thing to test,** and it is conveniently a pure function: `features.jsonl + params → intervals`. Everything else is glue.

- **Golden fixture** — one real 5-minute `features.jsonl` from the actual court, with rallies hand-labeled. The test asserts recall stays above a threshold. This is the regression guard that makes retuning safe: change the weights, immediately see whether a previously-fixed case broke.
- **Synthetic fixtures** — hand-written feature streams for edge cases: a lob pause mid-rally, a ball retrieval between points, one player leaving the region, a 2-second false blip.
- **ffmpeg wrappers** — generate a tiny clip with `lavfi testsrc`, run each wrapper, assert output parameters via `ffprobe`. Catches encode-profile drift, the one silent failure that would break concat across every clip ever made.
- **Concat** — build three clips, concat, assert the duration equals the sum.
- **API** — FastAPI `TestClient` against a temporary library directory.
- **Frontend** — Vitest on time-math helpers only. No E2E; not worth the maintenance for a single-user tool.
- **No YOLO in tests.** Detection output is mocked. Model inference in CI is slow, non-deterministic across devices, and tests nothing written here.

---

## 10. Recording guidance

Capture resolution does not affect detection. YOLO downsamples every frame to a fixed inference size regardless of input, so a player occupying 9% of frame height is 9% of frame height at 720p or 4K. Resolution is chosen for how the reels look.

**Record 4K30, HEVC.** 4K provides crop-in headroom — a 2× digital zoom on a rally still exports clean 1080p, which is how a far player becomes watchable in a highlight reel shot wide. At ~10 GB/hr, 2TB holds roughly 190 hours of originals.

**Not 60 fps.** It doubles storage and upload time and buys nothing for person detection.

Settings that matter more than resolution:

- **HDR off** — the iPhone records Dolby Vision by default, which transcodes to washed-out grey without tone-mapping work that benefits nothing here.
- **AE/AF lock** — tap and hold before recording. Otherwise focus hunts whenever someone crosses frame and exposure pumps as the sun moves.
- **Do not zoom** — at 2× the far player looks good and the near player does not fit in frame at all. A low baseline camera forces a wide shot.
- **Keep the microphone clear** — no case flap, no bag against the phone. Ball contact is now a detection feature, not only reel audio. A windscreen on the mic would help more than it sounds like it would.

**The highest-leverage change is camera height.** At ~1 ft the far player overlaps the near player, the net, and everyone on the adjacent court. At 6–8 ft — a fence mount, a cheap tripod, a bag on a bench — the players separate cleanly, occlusion drops, detection gets easier, and the footage is better to watch. A $15 fence mount beats every capture setting listed above.

Geometry at the current angle, 1× lens, far player ~80 ft away:

| Capture | Far player height | Size/hr |
|---|---|---|
| 720p30 | 64 px | 1.8 GB |
| 1080p30 | 96 px | 3.9 GB |
| **4K30** | **192 px** | **10 GB** |
| 4K60 | 192 px | 26 GB |

---

## 11. Decision log

| Decision | Reasoning |
|---|---|
| Player detection, not ball tracking | Ball-tracking models assume elevated broadcast angles; at ~1 ft the ball crosses at camera height against adjacent-court players |
| Audio used as a secondary feature | Reversed an earlier call. Ball contact is audible, just noisy; a highpass kills wind, and impact rate plus regularity give a sharp rally-end signal where vision is weakest. Weights fit to zero if it fails, so the downside is bounded |
| Sessions group by date, sources hold files | Play is split across several recordings per outing; rallies never span a recording gap |
| Min segment 1.5 s, not 3 s | A 3 s floor silently discards aces, which are among the most star-worthy points |
| Tight padding, −0.3 s / +0.5 s | User preference for punchy cuts over complete ones |
| Warmup not filtered | A rally is continuous hitting; warmup qualifies. Corrected the requirement instead of the algorithm |
| Manual play-region quad | Automatic court-line detection is a project on its own and degenerates at this camera height |
| Recall-biased threshold | Rejecting costs one keystroke; a missed rally is unrecoverable |
| Cached features, cheap re-segment | Retuning must cost 200 ms, not 15 minutes, or it will not happen |
| No object tracker | Track IDs are unstable at 5 fps sampling; box size separates near from far reliably |
| Proxy H.264, not HEVC | Browser HEVC support is inconsistent enough to produce a black player |
| Clips via libx264, not hardware | Vendor SPS/PPS differences would break `-c copy` across machines |
| Rallies as timestamp ranges | No duplicated storage, no re-encode until export |
| Svelte over React | User preference; better fit for stateful video and keyboard code |
| No auth, no multi-user | The portable-drive library removed the deployment those features served |
| Local inference, no cloud vision API | A 4070Ti already owned beats per-frame API cost and latency |
| No LLM | Rally segmentation is a temporal vision problem; an LLM adds nothing and consumes VRAM |

---

## 12. Open questions

1. **Segmentation weights** (`w1`–`w6`) and the threshold are unset. No separate labeling tool will be built: ship guessed weights, do one ordinary review pass, and fit from the result. The `det_start_ms`/`det_end_ms` deltas and `rejected` flags already record exactly the supervision needed, as a side effect of work being done anyway. That first corrected session also becomes the golden test fixture.
2. **`yolo11n` vs `yolo11s`** depends on far-player recall on actual footage. Start with `n`.
3. **Sprite-sheet thumbnail interval** (currently 1 per 10 s) may need adjusting once the library view exists.
4. **Whether audio carries real signal** on public courts with adjacent play. Answered empirically by fitting `w5`/`w6` on the first labeled session.

## 13. Build order

A thin end-to-end slice first: ingest one file, detect, review in an unstyled queue, export one reel. Polish afterwards.

The riskiest unknown is whether detection works at all on this camera angle. A backend-complete-then-frontend order would spend weeks refining a pipeline that may need rethinking; the thin slice reaches that answer in the first milestone.
