# Friend-Mode UI (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A non-technical friend can use the app without meeting the tuning tools,
the terminal, or a dead end: friend/dev mode gates label mode and re-segment, a
first-run flow replaces the `_inbox/` copy, failed jobs get retry/dismiss, the
wizard says why and how long, and library size is visible.

**Architecture:** `mode` lives in the existing appconfig JSON (`splitstep/appconfig.py`
already documents it: "The config file also carries `mode` (friend/dev, read in
Phase 2)"). One new GET/POST route pair exposes it; a `.svelte.ts` store mirrors it
client-side; every gate reads that store. All new logic lands in `web/src/lib/` with
vitest coverage — components stay thin shells (house rule; jsdom has no `<video>`).

**Tech Stack:** FastAPI routes (sync `def`, per CLAUDE.md), Svelte 5 runes,
vitest, pytest.

## Global Constraints

- Python tests: `~/miniconda3/envs/splitstep/bin/python -m pytest -q` — the `-m`
  form always. `filterwarnings = ["error"]`; ruff line-length 100.
- Web: `cd web && npx vitest run` (read the summary line in full), `npm run check`,
  `npm run build`. Worktrees need `npm install` first.
- Design tokens only (`web/src/app.css`); `font-data` for every count/size/timecode;
  no raw palette steps, no `text-[11px]`.
- Inline text actions are `text-accent`, never gray. One filled accent button per
  page. Reject/dismiss-like actions carry no colour.
- Comments explain why, not what — match the density already in the file.
- New logic in `web/src/lib/*.ts` + `web/tests/*.test.ts`, never in `.svelte` files.
- Migrations: none needed in this phase (mode is config-file, not DB).
- Commit per task, feature branch `phase2-friend-mode`, fast-forward merge when done.

---

### Task 1: `mode` in appconfig + `/api/config` routes

**Files:**
- Modify: `splitstep/appconfig.py`
- Modify: `splitstep/api/routes.py` (near `api_jobs`, ~line 880)
- Test: `tests/test_appconfig.py`, `tests/test_api.py`

**Interfaces:**
- Produces: `appconfig.get_mode() -> str` ('friend' | 'dev', default 'dev'),
  `appconfig.set_mode(mode: str) -> None` (read-modify-write, preserves other keys).
  `GET /api/config` → `{"mode": "dev"}`. `POST /api/config/mode` body
  `{"mode": "friend"}` → the updated `{"mode": ...}`; 422 on any other value.

- [ ] **Step 1: Write the failing tests**

In `tests/test_appconfig.py` (this file already monkeypatches `config_path` to a
tmp dir — follow its existing fixture):

```python
def test_mode_defaults_to_dev(tmp_config):
    assert appconfig.get_mode() == "dev"

def test_set_mode_preserves_other_keys(tmp_config):
    appconfig.save_config({"library": "/some/path"})
    appconfig.set_mode("friend")
    assert appconfig.get_mode() == "friend"
    assert appconfig.load_config()["library"] == "/some/path"
```

In `tests/test_api.py` (fixtures `client`, plus monkeypatch `appconfig.config_path`
to tmp — the route must never write the developer's real config during tests):

```python
def test_config_roundtrip(client, tmp_path, monkeypatch):
    monkeypatch.setattr(appconfig, "config_path", lambda: tmp_path / "config.json")
    assert client.get("/api/config").json() == {"mode": "dev"}
    r = client.post("/api/config/mode", json={"mode": "friend"})
    assert r.status_code == 200 and r.json() == {"mode": "friend"}
    assert client.get("/api/config").json() == {"mode": "friend"}
    assert client.post("/api/config/mode", json={"mode": "expert"}).status_code == 422
```

- [ ] **Step 2: Run to verify failure** — `... -m pytest tests/test_appconfig.py tests/test_api.py -q`, expect AttributeError/404.
- [ ] **Step 3: Implement**

`appconfig.py`:

```python
MODES = ("friend", "dev")

def get_mode() -> str:
    # 'dev' when absent: a dev checkout never wrote the key, and the Tauri
    # shell (Phase 3) writes 'friend' on first run — so absence itself is
    # the dev signal, no second flag needed.
    mode = load_config().get("mode", "dev")
    return mode if mode in MODES else "dev"

def set_mode(mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    cfg = load_config()
    cfg["mode"] = mode
    save_config(cfg)
```

`routes.py` (import `from splitstep import appconfig`; body model beside the
other pydantic models):

```python
class ModeBody(BaseModel):
    mode: Literal["friend", "dev"]

@router.get("/api/config")
def api_config(request: Request):
    return {"mode": appconfig.get_mode()}

@router.post("/api/config/mode")
def api_set_mode(body: ModeBody, request: Request):
    appconfig.set_mode(body.mode)
    return {"mode": appconfig.get_mode()}
```

- [ ] **Step 4: Run tests, expect pass; run ruff.**
- [ ] **Step 5: Commit** — `feat(api): expose friend/dev mode from the config file`

---

### Task 2: `appmode` store + api client entries

**Files:**
- Create: `web/src/lib/appmode.svelte.ts`
- Modify: `web/src/lib/api.ts`, `web/src/lib/types.ts`, `web/src/App.svelte`
- Test: `web/tests/appmode.test.ts`

**Interfaces:**
- Produces: `type AppMode = 'friend' | 'dev'` (types.ts);
  `api.config(): Promise<AppConfig>`, `api.setMode(mode): Promise<AppConfig>`;
  store `appmode` with `.current: AppMode` (reactive), `.load(): Promise<void>`,
  `.set(mode): Promise<void>`. Every later task gates on `appmode.current`.

- [ ] **Step 1: Failing test** (`web/tests/appmode.test.ts`; mock `api` the way
  `router.test.ts` mocks its collaborators):

```ts
import { describe, expect, it, vi } from 'vitest'
vi.mock('../src/lib/api', () => ({
  api: { config: vi.fn(async () => ({ mode: 'friend' })), setMode: vi.fn(async (m) => ({ mode: m })) },
}))
import { appmode } from '../src/lib/appmode.svelte'
import { api } from '../src/lib/api'

describe('appmode', () => {
  it('defaults to dev before load, adopts the server value after', async () => {
    expect(appmode.current).toBe('dev')
    await appmode.load()
    expect(appmode.current).toBe('friend')
  })
  it('set() is optimistic and posts', async () => {
    await appmode.set('dev')
    expect(appmode.current).toBe('dev')
    expect(api.setMode).toHaveBeenCalledWith('dev')
  })
})
```

- [ ] **Step 2: Run, expect module-not-found.**
- [ ] **Step 3: Implement**

`types.ts`: `export interface AppConfig { mode: 'friend' | 'dev' }` and
`export type AppMode = AppConfig['mode']`.

`api.ts` additions inside the `api` object:

```ts
config: () => req<AppConfig>('/api/config'),
setMode: (mode: AppMode) =>
  req<AppConfig>('/api/config/mode', { method: 'POST', body: JSON.stringify({ mode }) }),
```

`appmode.svelte.ts`:

```ts
import { api } from './api'
import type { AppMode } from './types'

// 'dev' until the config request lands: a dev checkout is the only place the
// UI runs without the config having been written, and hiding the tuning
// tools for one round-trip would flash the chrome at the one person who
// notices. A friend's config says 'friend' before the app ever opens.
let current = $state<AppMode>('dev')

export const appmode = {
  get current() {
    return current
  },
  async load() {
    try {
      current = (await api.config()).mode
    } catch {
      // Unreachable server surfaces through the routes' own error notes;
      // the mode flag failing must not add a second banner.
    }
  },
  async set(mode: AppMode) {
    current = mode
    await api.setMode(mode)
  },
}
```

`App.svelte`: call `appmode.load()` once at top level (fire-and-forget, same
pattern as any boot fetch already there).

- [ ] **Step 4: `npx vitest run tests/appmode.test.ts` + `npm run check`, expect pass.**
- [ ] **Step 5: Commit** — `feat(web): app-mode store mirrors the server config`

---

### Task 3: shortcuts gain a dev-only tag; strip and overlay stay truthful

**Files:**
- Modify: `web/src/lib/shortcuts.ts`, `web/src/components/KeyHints.svelte`,
  `web/src/routes/Session.svelte`
- Test: `web/tests/shortcuts.test.ts`

**Interfaces:**
- Consumes: `appmode.current` (Task 2).
- Produces: `Shortcut.devOnly?: true`;
  `shortcutGroups(mode: ShortcutMode, appMode: AppMode = 'dev')` and
  `primaryShortcuts(mode: ShortcutMode, appMode: AppMode = 'dev')` filter
  `devOnly` entries out in friend mode and drop groups left empty.

- [ ] **Step 1: Failing tests** (extend `web/tests/shortcuts.test.ts`):

```ts
it('friend mode hides dev-only shortcuts everywhere', () => {
  const flat = (gs: ShortcutGroup[]) => gs.flatMap((g) => g.items.flatMap((s) => s.keys))
  expect(flat(shortcutGroups('queue', 'dev'))).toContain('L')
  expect(flat(shortcutGroups('queue', 'friend'))).not.toContain('L')
  // The strip stays a subset of the overlay in both modes.
  for (const appMode of ['friend', 'dev'] as const)
    for (const mode of MODES)
      for (const s of primaryShortcuts(mode, appMode))
        expect(shortcutGroups(mode, appMode).flatMap((g) => g.items)).toContain(s)
})
```

- [ ] **Step 2: Run, expect signature/filter failures.**
- [ ] **Step 3: Implement** — add `devOnly?: true` to `Shortcut`; tag the queue
  "Open" entry `{ keys: ['L'], label: 'Label mode, to judge the detector', devOnly: true }`.
  Filter:

```ts
function visible(groups: ShortcutGroup[], appMode: AppMode): ShortcutGroup[] {
  if (appMode === 'dev') return groups
  return groups
    .map((g) => ({ ...g, items: g.items.filter((s) => !s.devOnly) }))
    .filter((g) => g.items.length > 0)
}

export function shortcutGroups(mode: ShortcutMode, appMode: AppMode = 'dev'): ShortcutGroup[] {
  return visible(BY_MODE[mode], appMode)
}

export function primaryShortcuts(mode: ShortcutMode, appMode: AppMode = 'dev'): Shortcut[] {
  return PRIMARY[mode].filter((s) => appMode === 'dev' || !s.devOnly)
}
```

  (The strip-subset invariant holds because both filters drop exactly the
  `devOnly` entries — keep the existing identity-based subset test passing.)

  `KeyHints.svelte`: pass `appmode.current` through to both calls.
  `Session.svelte`: `openLabel` becomes a no-op in friend mode —
  `if (appmode.current === 'friend') return` — so the keybinding an old
  muscle-memory presses cannot open a hidden mode. Label mode is hidden, not
  deleted: dev mode is untouched.

- [ ] **Step 4: `npx vitest run tests/shortcuts.test.ts tests/keyhints-overlay.test.ts` + `npm run check`.**
- [ ] **Step 5: Commit** — `feat(web): shortcuts carry a dev-only tag and filter per app mode`

---

### Task 4: hide the re-segment panel; Advanced toggle on the Library page

**Files:**
- Modify: `web/src/routes/Session.svelte` (the `ResegmentPanel` block, ~line 341),
  `web/src/routes/Library.svelte`
- Test: `web/tests/appmode.test.ts` (gating helper), hand-verify the pages

**Interfaces:**
- Consumes: `appmode` store.
- Produces: none new — this task is the two visible gates.

- [ ] **Step 1:** In `Session.svelte`, wrap the `ResegmentPanel` render in
  `{#if appmode.current === 'dev'}` (same `{#if}` that already checks ready
  sources — add the condition, keep the existing rationale comment).
- [ ] **Step 2:** In `Library.svelte`'s header, next to the Reels button, add a
  `Settings` text button (`font-data text-data text-dim hover:text-fg`, matching
  Reels) toggling a small `bg-surface border-line` popover with one labelled
  checkbox: **"Advanced tools — label mode and re-segment"**, checked when
  `appmode.current === 'dev'`, `onchange` → `appmode.set(checked ? 'dev' : 'friend')`.
  Sub-caption in `text-faint text-caption`: "Hidden in friend mode to protect
  the training corpus from stray keypresses."
- [ ] **Step 3:** `npm run check`; hand-verify: toggle off → session page loses
  re-segment panel, `?` overlay loses L, `L` key inert; toggle back on → all return.
- [ ] **Step 4: Commit** — `feat(web): friend mode hides the tuning tools behind an Advanced toggle`

---

### Task 5: failed jobs get retry and dismiss; the empty queue stops lying

**Files:**
- Modify: `web/src/lib/jobs.ts`, `web/src/lib/api.ts`,
  `web/src/components/JobsBadge.svelte`, `web/src/components/QueueMode.svelte` (~line 469)
- Test: `web/tests/jobs-label.test.ts` (or a new `web/tests/jobs-failed.test.ts`)

**Interfaces:**
- Consumes: `POST /api/jobs/{id}/retry` (Phase 1, already live), `Job.error_detail`.
- Produces: `api.retryJob(id): Promise<{ok: boolean}>`;
  `jobRows(jobs, nowMs, dismissed?: ReadonlySet<string>)` — failed rows in
  `dismissed` are omitted; `JobRow.detail: string | null` (the expandable
  traceback), `JobRow.canRetry: boolean`.

- [ ] **Step 1: Failing tests**

```ts
it('dismissed failed jobs leave the panel; active jobs cannot be dismissed away', () => {
  const rows = jobRows([failedJob('a'), runningJob('b')], NOW, new Set(['a', 'b']))
  expect(rows.map((r) => r.id)).toEqual(['b'])
})
it('failed rows carry retryability and the detail text', () => {
  const [row] = jobRows([failedJob('a')], NOW)
  expect(row.canRetry).toBe(true)
  expect(row.detail).toBe('Traceback ...')
})
```

- [ ] **Step 2: Run, expect signature failures.**
- [ ] **Step 3: Implement** — `jobs.ts`: add the parameter
  (`dismissed: ReadonlySet<string> = new Set()`), filter only the `failed` list
  by it (a dismissed id that is somehow running again must still show — the
  filter applies to failures alone), extend the row mapping with
  `detail: j.error_detail ?? null` and `canRetry: j.status === 'failed'`.
  Ensure `Job` in `types.ts` carries `error_detail: string | null` (add if Phase 1
  didn't). `api.ts`:

```ts
retryJob: (id: string) => req<{ ok: boolean }>(`/api/jobs/${id}/retry`, { method: 'POST' }),
```

  `JobsBadge.svelte`: failed rows get two inline text actions — **Retry**
  (`text-accent`, calls `api.retryJob` then forces the next poll tick) and
  **Dismiss** (`text-accent`, adds the id to a `$state` `Set` local to the
  component — in-memory on purpose: a dismissal is "stop showing me this",
  and a restart re-listing old failures is honest, not a bug). The `error`
  sentence stays; `detail` renders in a `<details>` block, `font-data
  text-caption`, `overflow-x-auto`.

- [ ] **Step 4:** `QueueMode.svelte:469` — the empty-queue copy currently reads
  "…Re-segment at a lower threshold below, or wait for detection to finish."
  Make it status- and mode-truthful. Add to `web/src/lib/status.ts`:

```ts
/** The empty-queue sentence. A failed source must not promise progress
 *  ("wait for detection to finish" when nothing is coming), and friend mode
 *  must not point at a re-segment panel it cannot see. */
export function emptyQueueCopy(sourceStatus: string, appMode: AppMode): string {
  if (sourceStatus === 'failed')
    return 'Detection failed for this video — open the jobs badge above to retry it.'
  if (sourceStatus !== 'ready') return 'No rallies yet — detection is still running.'
  return appMode === 'dev'
    ? 'No rallies made the cut. Re-segment at a lower threshold below.'
    : 'No rallies were found in this video.'
}
```

  with a four-way unit test (failed / detecting / ready+dev / ready+friend), and
  render it in `QueueMode.svelte` in place of the hardcoded sentence.

- [ ] **Step 5: Full web suite + `npm run check`.**
- [ ] **Step 6: Commit** — `feat(web): failed jobs retry and dismiss, and the empty queue tells the truth`

---

### Task 6: first-run flow on the empty library

**Files:**
- Create: `web/src/lib/firstrun.ts`, `web/src/components/FirstRun.svelte`
- Modify: `web/src/routes/Library.svelte` (replace the `sessions.length === 0` copy),
  `web/src/lib/api.ts`
- Test: `web/tests/firstrun.test.ts`

**Interfaces:**
- Consumes: `POST /api/import` (multipart, Phase 1), `api.listSessions` polling
  via the existing `polling.ts` helper.
- Produces: `type FirstRunStep = 'welcome' | 'tips' | 'drop' | 'waiting'`;
  `nextStep/prevStep(s: FirstRunStep)`; `firstRunVisible(sessionCount, loading)`;
  `api.importFile(file: File): Promise<{name: string}>`.

- [ ] **Step 1: Failing tests**

```ts
it('walks welcome → tips → drop and back', () => {
  expect(nextStep('welcome')).toBe('tips')
  expect(nextStep('tips')).toBe('drop')
  expect(nextStep('drop')).toBe('waiting')
  expect(nextStep('waiting')).toBe('waiting')
  expect(prevStep('welcome')).toBe('welcome')
  expect(prevStep('drop')).toBe('tips')
})
it('shows only on a loaded, empty library', () => {
  expect(firstRunVisible(0, true)).toBe(false)
  expect(firstRunVisible(0, false)).toBe(true)
  expect(firstRunVisible(3, false)).toBe(false)
})
```

- [ ] **Step 2: Run, expect module-not-found.**
- [ ] **Step 3: Implement `firstrun.ts`** (pure step machine exactly as tested;
  `ORDER: FirstRunStep[] = ['welcome', 'tips', 'drop', 'waiting']`, next/prev
  clamp at the ends).
- [ ] **Step 4: `api.importFile`.** `req()` sets a JSON content-type whenever a
  body exists — wrong for multipart, where the browser must write its own
  boundary header. Teach it the one exception rather than forking the error
  handling:

```ts
headers: init?.body && !(init.body instanceof FormData)
  ? { 'content-type': 'application/json' }
  : undefined,
```

```ts
importFile: (file: File) => {
  const body = new FormData()
  body.append('file', file)
  return req<{ name: string }>('/api/import', { method: 'POST', body })
},
```

- [ ] **Step 5: `FirstRun.svelte`** — a single centered `bg-surface` card, thin
  shell over the step machine. Copy, verbatim (spec §Phase 2):
  - **welcome:** "SplitStep turns a phone recording of your tennis session into
    reviewed rally clips." / Next.
  - **tips:** "Best results: mount the phone on the fence behind the court when
    the court allows it. Record 4K at 30 fps, and leave **HDR on**." / Next.
  - **drop:** a drag-drop target + file input calling `api.importFile`, copy:
    "Drop your video here. We'll ask you to outline your court (~20 seconds),
    then processing takes ~20 minutes." On a successful upload → `waiting`.
  - **waiting:** "Reading your footage — this list will update on its own."
    (the Library page's session poll does the rest; the card hides as soon as
    `firstRunVisible` flips false because a session appeared).
  Drag-over state flips a border token (`border-accent`); rejected uploads
  surface through the existing `ErrorNote` pattern with the server's 415
  sentence.
- [ ] **Step 6: Wire into `Library.svelte`** — replace the current empty-state
  `<p>` ("Drop a video into `_inbox/`…") with `<FirstRun />` gated on
  `firstRunVisible(sessions.length, loading)`. Dev checkouts see it too on an
  empty library — it is a better empty state, and the `_inbox/` sentence moves
  into the drop step's `text-faint` sub-caption ("or drop files into the
  library's `_inbox/` folder") so the dev affordance survives.
- [ ] **Step 7: Full web suite, `npm run check`, `npm run build`; hand-verify
  against the dev server with an empty scratch library
  (`splitstep serve --create --library /tmp/fr-test`).**
- [ ] **Step 8: Commit** — `feat(web): first-run flow replaces the inbox-path empty state`

---

### Task 7: wizard copy — the why, and the cost at the moment of commitment

**Files:**
- Modify: `web/src/routes/Setup.svelte` (Start Detection block), `web/src/components/QuadEditor.svelte` (only if the sentence lives there)
- Test: copy-only; `npm run check` + hand-verify

- [ ] **Step 1:** Locate the Start Detection affordance in `Setup.svelte`
  (`grep -n "Start" web/src/routes/Setup.svelte`). Directly beneath the button,
  add (`text-caption text-dim`): "Outlining your court keeps players on the
  next court out of your rallies." — the adjacent-court *why* QuadEditor
  already knows, now stated where the decision happens.
- [ ] **Step 2:** Add the duration warning to the same block, `text-caption
  text-faint`: "Detection takes roughly 20 minutes for an hour of 4K footage;
  you can keep using the app while it runs."
- [ ] **Step 3:** `npm run check`; hand-verify both sentences render in the
  wizard without pushing the primary button below the fold at laptop height.
- [ ] **Step 4: Commit** — `feat(web): the wizard says why the quad matters and what detection costs`

---

### Task 8: library size, surfaced unobtrusively

**Files:**
- Modify: `splitstep/api/routes.py`, `web/src/lib/api.ts`, `web/src/lib/types.ts`,
  `web/src/routes/Library.svelte`
- Create: `web/src/lib/size.ts`
- Test: `tests/test_api.py`, `web/tests/size.test.ts`

**Interfaces:**
- Produces: `GET /api/library/stats` → `{"bytes": <int>}`;
  `formatBytes(n: number): string` — `0 B`, `412 MB`, `1.4 GB`, `2.0 TB`
  (one decimal below 10 of a unit, none above).

- [ ] **Step 1: Failing tests**

```python
def test_library_stats_sums_the_tree(client, library):
    (library.root / "sessions").mkdir(exist_ok=True)
    (library.root / "sessions" / "blob.bin").write_bytes(b"x" * 2048)
    assert client.get("/api/library/stats").json()["bytes"] >= 2048
```

```ts
it('formats at the right unit with one decimal under ten', () => {
  expect(formatBytes(0)).toBe('0 B')
  expect(formatBytes(412 * 1024 ** 2)).toBe('412 MB')
  expect(formatBytes(1.44 * 1024 ** 3)).toBe('1.4 GB')
  expect(formatBytes(2.04 * 1024 ** 4)).toBe('2.0 TB')
})
```

- [ ] **Step 2: Run, expect 404 / module-not-found.**
- [ ] **Step 3: Implement route** (sync `def` like every other route; a walk over
  the library tree is file-count-bound, not byte-bound — a few hundred stats,
  fine per request, and only the Library page calls it, once per visit, never
  from a poll):

```python
@router.get("/api/library/stats")
def api_library_stats(request: Request):
    library = _library(request)
    total = 0
    for root, _dirs, files in os.walk(library.root):
        for name in files:
            try:
                total += os.stat(os.path.join(root, name)).st_size
            except OSError:
                continue  # a file deleted mid-walk is not an error
    return {"bytes": total}
```

- [ ] **Step 4: Implement `size.ts`** exactly to the tested format; add
  `api.libraryStats: () => req<{bytes: number}>('/api/library/stats')` and
  `LibraryStats` if a named type reads better in `types.ts`.
- [ ] **Step 5:** `Library.svelte` header, beside Settings:
  `<span class="font-data text-data text-faint">{formatBytes(stats.bytes)}</span>`
  — fetched once in the existing mount effect, rendered only when loaded
  (no placeholder churn). Keep-everything policy makes this the single disk
  affordance (spec §decisions); no action attaches to it.
- [ ] **Step 6: Both suites + `npm run check`.**
- [ ] **Step 7: Commit** — `feat: library size on the sessions page`

---

### Task 9: Phase-1 debt fold-in

The items `.superpowers/sdd/progress-phase1-friend-readiness.md` carried forward,
done as one hygiene task so they stop being loose.

**Files:**
- Modify: `splitstep/watcher.py`, `splitstep/api/routes.py`
- Test: `tests/test_handlers.py` or `tests/test_jobs.py` (wherever `_error_summary`'s
  existing tests sit — `grep -rn "_error_summary" tests/`), `tests/test_api.py`,
  `tests/test_export.py` (non-HLG e2e)

- [ ] **Step 1: Shared ingestibility predicate.** In `watcher.py`, beside
  `VIDEO_SUFFIXES`:

```python
def is_ingestible_name(name: str) -> bool:
    """The one definition of "the watcher will pick this up": not
    dot-prefixed, and a video suffix. The inbox listing and the import
    route both mirror the watcher's rule; three call sites drifting apart
    is how ".hidden.mp4" got stuck invisible in Phase 1."""
    return not name.startswith(".") and Path(name).suffix.lower() in VIDEO_SUFFIXES
```

  Use it at `watcher.py:60-62` (keep the `is_file()` check separate — it is
  about the path, not the name), in `api_import`'s 415 check (routes.py:926 —
  note the route lstrips dots first, so it passes the *stripped* name), and in
  `api_inbox`'s unsupported-listing comprehension (routes.py:958-960, inverted).
  Test: parametrized cases — `IMG.MOV` true, `.hidden.mp4` false, `notes.txt`
  false, `mp4` false.
- [ ] **Step 2: `_error_summary` edge tests** (`splitstep/jobs/worker.py:34`):
  an exception whose `str()` is empty falls back to the class name; a
  multi-line message keeps only the first line; >300 chars truncates with an
  ellipsis. Three asserts, table-driven, in the file that already tests the
  worker. (Adjust expectations to the function's actual contract on reading
  it — the tests document it, they do not redesign it.)
- [ ] **Step 3: `api_import` temp-cleanup regression test:** monkeypatch
  `os.replace` to raise `OSError`, POST a file, assert the route errors AND
  no `.upload-*` file remains in `library.inbox`.
- [ ] **Step 4: Non-HLG end-to-end encode test:** in the clip-export tests,
  build a synthetic source tagged `tv/bt709/bt709/bt709` via the same
  mechanism `hlg_setparams` uses (a `-vf setparams` variant — lavfi `-color_*`
  output flags silently drop primaries/transfer, per CLAUDE.md), lock a
  library profile to bt709, export a clip, and probe the output tags equal
  the locked profile. This is the test the colour-profile work was missing
  before Phase 3 leans on `color_profile`.
- [ ] **Step 5: Full python suite + ruff.**
- [ ] **Step 6: Commit** — `chore: fold in the phase-1 review debt`

---

## Explicitly not in this plan

- The native folder picker, `--create` first-run, sidecar, power assertions —
  Phase 3 (Tauri). The first-run flow here is the browser-tier version; the
  folder was picked before the server started.
- The 503 no-UI page branching on `resources.bundle_dir()` — Phase 3, noted in
  the progress file.
- Any change to detection, thresholds, or the label corpus — Gate 0's findings
  doc (`2026-08-27-gate0-fence-mount-first-look.md`) owns that thread.

## Self-review notes

- Spec coverage: mode flag + endpoint (T1–2), shortcuts mode tags (T3), hide
  label/re-segment + Advanced toggle (T3–4), first-run flow minus native picker
  (T6), failed-state truth (T5), wizard copy (T7), library size (T8). Testing
  section's "mode filtering in shortcuts.ts, first-run state machine,
  failed-state copy selection — all as pure lib/ modules": T3, T6, T5.
- Types used across tasks: `AppMode` defined in T2, consumed T3–T5;
  `jobRows` third parameter introduced once (T5); `firstRunVisible` naming
  consistent between T6 steps.
- No placeholders: the two "locate with grep" steps (T7 step 1, T9 step 2)
  name the exact file, line anchor or symbol, and the copy/asserts to land —
  the grep is for drift since this plan was written, not for missing content.
