# SplitStep — Import Setup Wizard and Source Rotation

**Date:** 2026-08-20
**Status:** Approved and implemented — `splitstep/setup.py::queue_setup`
(the one function both the API route and the CLI call) and
`web/src/routes/Setup.svelte`.
**Extends:** `docs/superpowers/specs/2026-08-19-splitstep-design.md` (§3 pipeline, §5 detection, §6 UI)

---

## 1. Problem

The first real session through the pipeline produced zero rallies. The cause was orientation, not segmentation.

`IMG_2373.MOV`, filmed horizontally with the phone propped a foot off the ground behind the baseline:

```
original.mov   3840x2160 coded, HEVC, Display Matrix rotation=90
proxy.mp4       608x1080
```

ffmpeg applies a stream's display matrix by default. `make_proxy` scales with `-vf scale=-2:1080`, which sized the *rotated* frame: the scene's long axis went from 3840 px to 608 px, roughly a third of the pixel count of a correct 1920x1080 proxy.

Detection on that proxy, measured:

| Metric | Value |
|---|---|
| Sampled frames (5 fps over 19.6 min) | 5875 |
| Frames with >= 1 person | 668 (11%) |
| Frames with >= 2 people | 55 (0.9%) |
| Rallies produced | 0 |
| Audio frame-hits | 2833 (~570 real contacts, ~0.5/s) |

`w_both` never fires, so `score(t)` never approaches the 0.45 threshold. Audio was unaffected — it reads the original, not the proxy.

Two deeper problems sit behind the immediate one:

1. **Orientation is decided by an ffmpeg default, not by the library.** Nothing in the schema records what orientation a source was encoded at, so nothing can be verified, tested, or corrected without editing code.
2. **The pipeline commits to an expensive transcode before a human has seen a single frame.** A dropped file transcodes (~7 min for 19.6 min of 4K) and then runs detection (~5 min) before the session page shows anything reviewable. A wrong orientation, a misframed camera, or a clip that turns out to be the wrong recording all cost that full round trip.

## 2. Goals

1. A human confirms orientation and play region **before** any transcode or GPU work happens.
2. Orientation is an explicit stored property of a source, seeded from metadata, overridable, and applied deterministically at transcode.
3. Preview frames come from the original file, sampled across the clip, so the first thing shown is never the black frame a phone records when the shutter is hit.
4. The same setup is reachable headlessly from the CLI, so a session can be rebuilt without the browser.

### Non-goals

- Uploading video through the browser. Files arrive in `<library>/_inbox/` exactly as they do today.
- Arbitrary rotation angles or perspective correction. Four right angles only.
- Re-encoding the original. Rotation applies to the proxy (and to preview frames); `original.mov` is never rewritten.
- Court homography, or any use of the quad beyond the existing `w_outside` term.

## 3. Pipeline

### Today

```
watcher  ->  ingest job                                    ->  detect job
             probe, rows, make_proxy, make_thumbs, move        features, segment, rallies
```

### Proposed

```
watcher  ->  ingest job                ->  [ human: setup wizard ]  ->  build_proxy job      ->  detect job
             probe, rows, move             rotation + court quad       make_proxy(rotation)     unchanged
             status: needs_setup                                       make_thumbs
                                                                       status: ingested
```

`ingest` becomes register-only and finishes in seconds. `build_proxy` is a new job type holding everything `ingest` used to do after probing. `detect` is unchanged and is still enqueued by whatever produced the proxy, so a re-detect from the session page keeps working exactly as it does now.

### Source status vocabulary

| Status | Meaning |
|---|---|
| `ingesting` | `ingest` job running: probing and moving the original |
| `needs_setup` | Registered, original in place, no proxy. Waiting on the wizard. **New.** |
| `building` | `build_proxy` job running: transcode and sprite sheet. **New.** |
| `ingested` | Proxy exists, detection queued |
| `detecting` | `detect` job running |
| `ready` | Rallies available |
| `failed` | Any job raised |

A session's status is `needs_setup` when any of its sources is; the existing "all sources ready -> session ready" rollup is untouched.

### Idempotency

`ingest` currently encodes from the inbox path and moves the original only on success, because a crash mid-encode would otherwise leave the payload naming a path that no longer exists. With the encode gone, `ingest` moves first, and a requeued job whose inbox path is missing resolves the source by `original_name` (as it already does) and treats an original already in place as success.

`build_proxy` is idempotent by overwrite: it re-runs `make_proxy` and `make_thumbs` unconditionally, since a partially written proxy from a killed worker is worthless and cheap to discard.

## 4. Rotation

### Storage

```sql
ALTER TABLE sources ADD COLUMN rotation_deg INTEGER NOT NULL DEFAULT 0;
```

Migration `002_rotation.sql`. Legal values 0, 90, 180, 270, enforced at every write site (API, CLI, handler) rather than by a CHECK constraint, so an out-of-range value produces a 400 with a message instead of an opaque `IntegrityError`.

`rotation_deg` is **clockwise degrees applied to the coded frame** to produce the intended upright image — the same convention as the display matrix's `rotation` field, negated where ffprobe reports counter-clockwise.

### Seeding

`MediaInfo` gains `rotation_deg: int`, read from the video stream's Display Matrix side data (`side_data_list[].rotation`), normalized into {0, 90, 180, 270}. `ingest` stores it. A source therefore defaults to what QuickTime and Photos show, which is familiar even when — as here — it is wrong. Fixing it is one click in the wizard.

**Convention check, to be settled empirically during implementation.** ffprobe's `rotation` sign convention has changed across ffmpeg releases, and both `transpose=1` and `transpose=2` turn a 3840x2160 frame into 2160x3840 — dimensions alone cannot tell the two apart, only the image can. The seeding rule is therefore defined by behaviour, not by a sign: `rotation_deg` seeded from `IMG_2373.MOV` must reproduce, pixel for pixel, what ffmpeg's own autorotate produced for that file (a portrait proxy). Verify by eye against the original once, then lock it in as a fixture test using that file's probe JSON.

`MediaInfo` also keeps reporting coded `width`/`height`. `sources.width`/`height` store the **display** dimensions after `rotation_deg` is applied, so the row matches the proxy the library will build. Today they store coded dimensions and disagree with a rotated proxy.

### Application

`make_proxy` gains a `rotation_deg: int = 0` parameter and always passes `-noautorotate` before `-i`, so ffmpeg's default behaviour stops participating:

| `rotation_deg` | filter prefix |
|---|---|
| 0 | (none) |
| 90 | `transpose=1` |
| 180 | `transpose=1,transpose=1` |
| 270 | `transpose=2` |

The prefix is composed ahead of the existing `scale=-2:1080`, so scaling always sees the upright frame. A pure function `rotation_filter(deg) -> str` does the mapping and is unit-tested directly; `make_proxy` just concatenates.

## 5. Preview frames

### Endpoint

```
GET /media/{session_id}/{idx}/preview.jpg?at_ms=<int>&rot=<0|90|180|270>
```

Reads `original.*`, not `proxy.mp4` — during setup the proxy does not exist. Decodes with `-hwaccel <accel.hwaccel> -noautorotate`, applies `rotation_filter(rot)`, scales to 1280 wide, writes JPEG.

Differences from the existing `frame.jpg` route:

- **Source file.** `original.*` via the same glob `_audio_source` uses. 404 if the original is no longer on disk (`has_original = 0`).
- **Cache key includes rotation:** `preview-{rot}-{at_ms}.jpg`, under the same LRU eviction, generalized to take a glob pattern so both routes share one sweep.
- **Concurrency cap.** A module-level `threading.Semaphore(2)` around the extraction. Nine simultaneous 4K HEVC decodes on an 8 GB M2 Air is the difference between a responsive grid and a swap storm. Waiting on the semaphore happens on Starlette's worker thread, which is why the cap is 2 and the ffmpeg timeout is 20 s: worst case two slots and a queue, not 40 wedged threads.
- **Clamping** reuses the existing `duration_ms` / `fps` margin logic, extracted into a shared helper rather than duplicated.

`frame.jpg` keeps serving the proxy for the existing session-page editor.

### Sampling

The grid is nine frames at 10%, 20% … 90% of `duration_ms`, computed client-side by a pure `previewTimestamps(durationMs, n)` and clamped by the existing `lastSafeFrameMs`. Even spacing rather than random: reproducible across reloads, and it visibly covers the whole session, which random sampling only appears to do.

## 6. Wizard

### Route and entry

New SPA route `#/setup/<source_id>`, added to `parseHash` alongside `library` and `session`. The Library page renders a "Set up" card for every source in `needs_setup` (they have no proxy, so they cannot appear in the normal session review flow); the Session page shows the same card for its own sources.

### Steps

1. **Rotate.** The nine-frame grid, with ⟲ / ⟳ buttons. Rotation is client state until Confirm — the grid re-renders by changing `rot` in the image URLs, costing one extraction per frame per orientation, all cached. Clicking a grid frame promotes it to the working frame.
2. **Align.** The working frame at full width with the four-corner quad on top, plus the frame scrubber (`« 1s / ‹ fr / slider / fr › / 1s »`) already built for the session-page editor, pointed at `preview.jpg`. Existing presets are listed for one-click reuse.
3. **Confirm.** A single "Start detection" button. Disabled until a quad is set — either dragged or reused. It creates the preset if new, then POSTs the setup.

Rotation and quad both persist to the source on Confirm and not before, so abandoning the wizard leaves the source exactly as `ingest` left it.

### Component split

`QuadEditor.svelte` currently owns both the drag surface and the save/assign panel. The drag surface extracts into **`QuadCanvas.svelte`** — an image, four handles, the shaded overlay, and the scrub controls — taking `src`, `points`, and an `onchange` callback. Both the wizard and the existing session panel consume it. Pure geometry stays in `lib/quad.ts` untouched.

## 7. API

| Method | Path | Body / Query | Purpose |
|---|---|---|---|
| `GET` | `/api/sources/{id}` | — | One source row, for the wizard to load without fetching the whole session |
| `POST` | `/api/sources/{id}/setup` | `{rotation_deg, preset_id}` | Store both, enqueue `build_proxy`. 409 unless status is `needs_setup` or `ready` |
| `GET` | `/media/{session}/{idx}/preview.jpg` | `at_ms`, `rot` | Preview frame from the original |

`POST /api/sources/{id}/setup` validates `rotation_deg ∈ {0,90,180,270}` (400 otherwise) and that `preset_id` exists (404 otherwise). It is the only write path for `rotation_deg`; the existing `/preset` route is unchanged and keeps working for a source that already has a proxy.

Re-running setup on a `ready` source is explicitly allowed: that is how a source ingested before this change gets a correctly oriented proxy. It rebuilds the proxy and re-detects, replacing rallies. Because `replace_rallies` discards hand-edited boundaries, the wizard warns before confirming on a source that already has rallies.

## 8. CLI

```
splitstep setup <source_id> --rotation <0|90|180|270> [--preset <preset_id>]
```

Sets both fields and enqueues `build_proxy`, printing the queued job id. Same validation as the API route, sharing one function rather than duplicating the checks. This ships before the wizard UI, so the existing session can be rebuilt while the frontend is still being written.

`splitstep doctor` additionally prints each source's `rotation_deg` and proxy dimensions, since a mismatch between them is now the single most diagnostic fact about a broken session.

## 9. Existing data

Migration `002` adds the column with `DEFAULT 0`. It deliberately does **not** try to infer orientation for already-ingested sources: their proxies were built under autorotate and the column would then describe an intent that the file on disk does not match.

The one existing source (2026-08-19 / 01, the 608x1080 proxy) is corrected by re-running setup on it, which is exactly the flow a new source takes. Its `features.jsonl` and its zero rallies are replaced by the re-detect.

## 10. Failure handling

| Failure | Behaviour |
|---|---|
| Original missing at preview time | 404 with "original not available"; the wizard shows it inline rather than a broken image |
| ffmpeg fails or times out extracting a preview | 409 "source is still being processed" (matching `frame.jpg`), grid cell shows a retry affordance |
| `build_proxy` raises | Source status `failed`, error stored on the job row, session page surfaces it. The original stays in place — no quarantine, since the file already left the inbox |
| Confirm on a source whose status is `building` or `detecting` | 409; the wizard polls `/api/jobs` and shows progress instead |
| Disk space | `library.require_free(size * 1.5)` moves from `ingest` to `build_proxy`, where the transcode actually happens |

## 11. Testing

**Python**

- `rotation_filter` mapping for all four angles, and rejection of anything else.
- `probe` reads `rotation` from Display Matrix side data; normalizes -90 to 270; defaults to 0 when absent. Fixture JSON, no real media.
- `sources.width`/`height` store display dimensions for a 90° source.
- `handle_ingest` writes a `needs_setup` row, moves the original, and enqueues **nothing**. Asserted by a `make_proxy` spy that must not be called.
- `handle_ingest` retry with the inbox path already gone resolves the existing row instead of failing.
- `handle_build_proxy` calls `make_proxy` with the stored rotation and enqueues `detect`.
- `POST /setup` validation matrix: bad angle 400, unknown preset 404, wrong status 409, happy path enqueues one job.
- `preview.jpg` route: cache key includes rotation, clamping matches `frame.jpg`, missing original 404s.
- Migration applies to a database created at version 001 without data loss.

**TypeScript**

- `previewTimestamps` spacing, clamping, and behaviour on a clip shorter than the sample count.
- `parseHash` for `#/setup/<id>`.
- Wizard: rotation buttons change the requested `rot`; Confirm is disabled without a quad; Confirm posts `{rotation_deg, preset_id}` once and only once (double-click guarded, as `QuadEditor.save` already is).
- `QuadCanvas` extraction does not regress the existing scrub and drag tests.

No YOLO and no real ffmpeg in either suite, per the standing constraint.

## 12. Deferred

- Rotating the 4K clip export (Plan 3) to match. The export path does not exist yet; when it does it reads `rotation_deg` the same way.
- Auto-detecting a wrong orientation (e.g. running person detection on one preview frame per orientation and picking the best). The wizard makes it a single click; automation can come after there is data on how often the metadata lies.
- Re-running setup without discarding hand-edited rally boundaries. Requires boundary re-mapping across a re-detect, which is a Plan 3 concern.
