# Hard Court UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the app's flat near-black ground with a hard-court palette and a real tennis court, delete the accent colour from the chrome, give the app a split-ball mark that doubles as its loader, regenerate the icon from it, and stop wasting two thirds of an ultrawide monitor.

**Architecture:** All colour continues to resolve through `web/src/app.css`'s `@theme` block. Three new Svelte components (`AppBar`, `CourtGround`, `Mark`) sit above or behind the existing router, which is untouched. Geometry for the court and the mark lives in pure, tested TypeScript under `web/src/lib/`, following this codebase's rule that a `.svelte` file holds no logic. The Python icon generator reads both its colours and its geometry constants from those same sources, with a test that fails if the two drawings of the ball diverge.

**Tech Stack:** Svelte 5 (runes), Tailwind v4 (`@theme`), Vitest + jsdom, Python 3.12 + Pillow, Tauri v2.

**Spec:** `docs/superpowers/specs/2026-08-30-ui-hard-court-design.md`

## Global Constraints

- **Contrast floors, non-negotiable:** `fg`, `dim`, `faint` ≥ 4.5:1 against `bg`, `surface` and `surface-2`. `star`, `point`, `danger` ≥ 3.0:1 against all three. Task 1 makes this a test; no later task may weaken it.
- **`dim` is 2.41:1 and `faint` 2.09:1 over bare `court`.** Secondary text never sits on exposed court — it sits on a `surface` card, or the ground is scrimmed beneath it. Only `fg` (5.27:1) may cross bare court.
- **`npm run check` must stay at `0 ERRORS 0 WARNINGS`.** Baseline verified 2026-08-30: `275 FILES 0 ERRORS 0 WARNINGS`.
- **No raw Tailwind palette steps or arbitrary sizes.** `bg-neutral-800`, `text-blue-300`, `text-[11px]` are all escapes from the system. Every value goes through a token.
- **Motion is `transform` and `opacity` only**, and always behind `motion-safe:`. Nothing continuous may run on the review tier.
- **Tailwind v4's duration namespace is `--transition-duration-*`, not `--duration-*`**, and the block must be `@theme static`. Verified against the installed Tailwind 4.3.3: a `--duration-quick` token generates no `duration-quick` utility *and is silently dropped from the output* — no error, no class, nothing to notice. `static` is required because Tailwind tree-shakes theme variables no utility references, which would remove the variable that the `animate-[…]` arbitrary values reference by name. Also verified as compiling: `h-13`, `w-38`, `w-70`, `accent-fg`, `outline-fg`, `bg-fg/70`, `min-[1800px]:grid-cols-2`, `max-w-[min(2600px,92vw)]`, `max-w-[1920px]`, and the arbitrary `shadow-[…]`.
- **Nothing may sit between a keypress and its response** in queue or timeline mode.
- **Reject stays colourless** (`text-faint` + strikethrough). The video letterbox stays literal `bg-black`.
- **Python is invoked by path:** `~/miniconda3/envs/splitstep/bin/pytest`. In a git worktree use `~/miniconda3/envs/splitstep/bin/python -m pytest` — a bare `pytest` resolves to master's installed package, not the worktree's.
- **`pytest` runs with `filterwarnings = ["error"]`.** A new warning fails the suite.
- Frontend commands run from `web/`. `ruff` line length is 100.

---

## File Structure

**Created**

| Path | Responsibility |
| --- | --- |
| `web/tests/tokens.test.ts` | Parses `app.css`, computes WCAG ratios, enforces the floors above |
| `web/src/lib/court.ts` | Pure perspective projection → SVG path strings |
| `web/tests/court.test.ts` | Court geometry invariants |
| `web/src/lib/mark.ts` | The split-ball's geometry constants, single source of truth |
| `web/src/components/CourtGround.svelte` | Fixed viewport-filling court, two tiers |
| `web/src/components/Mark.svelte` | The mark; `still` and `loading` states |
| `web/src/components/AppBar.svelte` | Persistent bar: mark, wordmark, tabs, drive chip, Settings |
| `tests/test_icon.py` | Drift guard: `make_icon.py`'s geometry must equal `lib/mark.ts`'s |

**Modified**

| Path | Change |
| --- | --- |
| `web/src/app.css` | `@theme` rewritten: new palette, court/ball tokens, motion tokens, `accent` deleted |
| `web/src/App.svelte` | Mounts `AppBar` + `CourtGround`; layout container replaces `max-w-6xl` |
| `web/src/routes/Library.svelte` | Header moves to `AppBar`; cards redesigned; two-up grid |
| `web/src/routes/Reels.svelte` | Header moves to `AppBar`; same card treatment |
| `web/src/routes/Session.svelte` | Header becomes a breadcrumb; review-tier width cap |
| `web/src/routes/Setup.svelte`, `web/src/routes/Reel.svelte` | Accent removal, breadcrumb |
| 13 components listed in Task 2 | Accent removal |
| `web/src/components/StatusBadge.svelte` | `active` tone becomes an outlined pill |
| `packaging/make_icon.py` | Draws the split ball; reads geometry from `lib/mark.ts` |
| `CLAUDE.md` | Design-tokens section rewritten |
| `docs/SMOKE.md` | New rows for the redesign |

---

### Task 1: The palette, as a tested artifact

The `faint` token has now failed its contrast floor twice — `#74747F` in the 2026-08-23 pass, `#7186a0` in this one. A comment claiming a ratio cannot fail; a test can. This task makes the palette checkable before it changes it.

**Files:**
- Create: `web/tests/tokens.test.ts`
- Modify: `web/src/app.css`

**Interfaces:**
- Consumes: nothing.
- Produces: the token names every later task uses — `--color-bg`, `--color-surface`, `--color-surface-2`, `--color-line`, `--color-fg`, `--color-dim`, `--color-faint`, `--color-star`, `--color-point`, `--color-danger`, `--color-court`, `--color-court-run`, `--color-court-line`, `--color-ball`. Exports `readTokens(): Record<string,string>` and `contrast(a: string, b: string): number` from the test file for reuse by Task 9's guard is **not** done — the Python guard reads `lib/mark.ts`, not this.

- [ ] **Step 1: Write the failing test**

Create `web/tests/tokens.test.ts`:

```ts
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const CSS = readFileSync(
  fileURLToPath(new URL('../src/app.css', import.meta.url)),
  'utf8',
)

/** Every `--color-<name>: #rrggbb` declared in the theme block. */
function tokens(): Record<string, string> {
  const out: Record<string, string> = {}
  for (const m of CSS.matchAll(/--color-([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})/g)) {
    out[m[1]] = m[2].toLowerCase()
  }
  return out
}

function channel(v: number): number {
  const c = v / 255
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
}

function luminance(hex: string): number {
  const n = hex.replace('#', '')
  const [r, g, b] = [0, 2, 4].map((i) => channel(parseInt(n.slice(i, i + 2), 16)))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

/** WCAG 2.x contrast ratio. */
export function contrast(a: string, b: string): number {
  const [x, y] = [luminance(a), luminance(b)]
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
}

const GROUNDS = ['bg', 'surface', 'surface-2'] as const

describe('the palette', () => {
  it('declares every token the app depends on', () => {
    const t = tokens()
    for (const name of [
      'bg', 'surface', 'surface-2', 'line',
      'fg', 'dim', 'faint',
      'star', 'point', 'danger',
      'court', 'court-run', 'court-line', 'ball',
    ]) {
      expect(t[name], `--color-${name} is missing`).toBeDefined()
    }
  })

  it('has no accent token — the chrome carries no hue', () => {
    expect(tokens()['accent']).toBeUndefined()
  })

  // Text must clear 4.5:1 on every ground it can land on. `faint` carries the
  // keyboard legend, which is functional text, so it is held to the same bar
  // as body copy -- this is the check that would have caught #74747F in the
  // last redesign and #7186a0 in this one.
  it.each(['fg', 'dim', 'faint'])('%s clears 4.5:1 on every ground', (text) => {
    const t = tokens()
    for (const ground of GROUNDS) {
      expect(
        contrast(t[text], t[ground]),
        `--color-${text} on --color-${ground}`,
      ).toBeGreaterThanOrEqual(4.5)
    }
  })

  it.each(['star', 'point', 'danger'])('%s clears 3:1 as a graphical mark', (mark) => {
    const t = tokens()
    for (const ground of GROUNDS) {
      expect(contrast(t[mark], t[ground])).toBeGreaterThanOrEqual(3.0)
    }
  })

  // Two cyans, one meaning "interactive" and one meaning "this rally was a
  // point", are indistinguishable in a status row read hundreds of times a
  // session. This is what ruled out a cyan accent; it also stops `point`
  // drifting toward whatever replaces it.
  it('keeps star and point apart', () => {
    const t = tokens()
    expect(contrast(t['star'], t['point'])).toBeGreaterThanOrEqual(1.4)
  })

  // fg is the only token permitted over bare court (5.27:1). dim and faint
  // measure 2.41 and 2.09 there, which is why the layout rule exists.
  it('lets fg cross bare court, and only fg', () => {
    const t = tokens()
    expect(contrast(t['fg'], t['court'])).toBeGreaterThanOrEqual(4.5)
    expect(contrast(t['dim'], t['court'])).toBeLessThan(4.5)
  })

  it('keeps the ball legible on the icon ground', () => {
    const t = tokens()
    expect(contrast(t['ball'], t['court'])).toBeGreaterThanOrEqual(3.0)
    expect(contrast(t['ball'], t['bg'])).toBeGreaterThanOrEqual(3.0)
  })
})
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd web && npx vitest run tests/tokens.test.ts
```

Expected: FAIL. `--color-court` is missing, and `has no accent token` fails because `--color-accent` is still declared.

- [ ] **Step 3: Rewrite the theme block**

In `web/src/app.css`, replace the colour declarations inside `@theme` (keep the type, font and `@keyframes` sections untouched for now) with:

```css
  /* Base. The ground is a night hard court: navy rather than the previous
     violet-black, because the app's own backdrop is now a court rendered in
     perspective and the chrome has to sit in the same world as it. */
  --color-bg: #080e16;
  --color-surface: #101b28;
  --color-surface-2: #16273a;
  --color-line: #1e3247;

  /* Text. Named for their job, not their lightness. All three clear 4.5:1
     against all three grounds -- enforced by tests/tokens.test.ts, not by
     this comment, because this exact token has silently fallen under the
     line twice: #74747F in the 2026-08-23 pass and #7186a0 in this one. */
  --color-fg: #e6eef8;
  --color-dim: #8fa4bd;
  --color-faint: #8598b0;

  /* Semantic. Three, and *neither reject nor "interactive" is one of them*.

     Reject is the most frequent action in a recall-biased review loop, so
     colouring it red would state "error" about normal work; it stays
     `text-faint` plus a strikethrough.

     There is no accent. A blue button, a blue tab and a blue focus ring on
     every screen state "look here" about chrome that is never the point --
     the footage is the colour. State is fill, outline and weight instead,
     and `fg` as a focus ring measures 14.84:1 on surface, far past the 3:1
     an indicator needs. The attempt that shifted accent to cyan died on
     measurement rather than taste: against `point` it sat at a 1.14
     luminance ratio, two cyans a reviewer would have to tell apart.

     star and point are different axes, not two grades of one: a rally can be
     either, both or neither, so they are warm against cool. */
  --color-star: #f0a94c; /* sodium floodlight, off the footage's own lights */
  --color-point: #6fd0e8; /* ice; counted, clinical, not a rating */
  --color-danger: #f0666b; /* real failure only */

  /* The court. These are the ground's own colours, and make_icon.py reads
     them out of this file by regex so the app icon cannot drift from the
     chrome it sits beside. */
  --color-court: #2d6595; /* the playing surface */
  --color-court-run: #1c5c4f; /* the run-off apron */
  --color-court-line: #f4f9ff; /* court markings, and the ball's seam */
  --color-ball: #d6e02c; /* optic yellow; the mark and the icon */
```

Delete the `--color-accent` line entirely.

- [ ] **Step 4: Run the token test**

```bash
cd web && npx vitest run tests/tokens.test.ts
```

Expected: PASS, all cases.

- [ ] **Step 5: Confirm nothing else broke yet**

```bash
cd web && npx vitest run && npm run check && npm run build
```

Expected: vitest passes; `npm run check` reports `0 ERRORS 0 WARNINGS`; the build succeeds. `bg-accent` classes still exist in components and now resolve to nothing — the app will look wrong until Task 2. That is expected and is why these two tasks are adjacent.

- [ ] **Step 6: Commit**

```bash
git add web/src/app.css web/tests/tokens.test.ts
git commit -m "feat(web): the palette becomes a hard court, and a tested one

The faint token has now fallen under 4.5:1 twice, once per redesign, each
time caught by hand and each time carrying the keyboard legend. A comment
claiming a ratio cannot fail. tokens.test.ts parses the theme block and
measures it, so the next one fails in CI instead of in someone's eyes.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Delete the accent

62 usages across 18 files. The replacements are mechanical but not uniform — a filled button, a focus border, a progress fill and a native `accent-color` each want a different answer.

**Files:**
- Modify: `web/src/components/JobsBadge.svelte`, `FirstRun.svelte`, `ScoreCurve.svelte`, `QuadEditor.svelte`, `ResegmentPanel.svelte`, `QueueMode.svelte`, `AddRalliesPicker.svelte`, `StatusBadge.svelte`, `ClipsPanel.svelte`, `ZoomBand.svelte`, `OverviewBand.svelte`, `LabelMode.svelte`, `QuadCanvas.svelte`
- Modify: `web/src/routes/Setup.svelte`, `Session.svelte`, `Library.svelte`, `Reel.svelte`
- Modify: `web/src/launcher/Launcher.svelte`
- Modify: `web/tests/timeline-drag-gain.test.ts`, `web/tests/zoomband-handle-hit-target.test.ts`

**Interfaces:**
- Consumes: the token set from Task 1.
- Produces: an app with no `accent` reference anywhere. Later tasks may assume `bg-fg text-bg` is the filled-button idiom and `border-fg text-fg` the selected-control idiom.

- [ ] **Step 1: Write the failing guard test**

Append to `web/tests/tokens.test.ts`:

```ts
import { readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

function sourceFiles(dir: string, acc: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) sourceFiles(full, acc)
    else if (/\.(svelte|ts)$/.test(entry)) acc.push(full)
  }
  return acc
}

describe('no callsite still reaches for accent', () => {
  // Deleting the token is only half of it: a `bg-accent` left behind resolves
  // to nothing and fails at runtime as an unstyled element, not at build.
  it('has no accent class anywhere in src', () => {
    const root = fileURLToPath(new URL('../src', import.meta.url))
    const guilty: string[] = []
    for (const file of sourceFiles(root)) {
      const text = readFileSync(file, 'utf8')
      if (/\b(bg|text|border|ring|accent|fill|stroke)-accent\b/.test(text)) {
        guilty.push(file.slice(root.length + 1))
      }
    }
    expect(guilty).toEqual([])
  })
})
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd web && npx vitest run tests/tokens.test.ts -t 'accent class'
```

Expected: FAIL, listing 18 files.

- [ ] **Step 3: Apply the replacements**

Work through this table exactly. The left column is what to search for; the right is what replaces it.

| Where | From | To |
| --- | --- | --- |
| `Launcher.svelte:111`, `FirstRun.svelte:68,89`, `QuadEditor.svelte:241`, `ResegmentPanel.svelte:209`, `Setup.svelte:247`, `Session.svelte:255`, `Reel.svelte:408` | `bg-accent` (filled button, already has `text-bg`) | `bg-fg` |
| the same buttons | `hover:brightness-110` | `hover:bg-fg/90` |
| `Launcher.svelte:117`, `FirstRun.svelte:82,138`, `QuadEditor.svelte:266,270`, `ClipsPanel.svelte:181,218`, `JobsBadge.svelte:166,174`, `Library.svelte:177`, `Reel.svelte:473` | `text-accent` (text button or link) | `text-fg` |
| `QueueMode.svelte:620`, `LabelMode.svelte:316`, `JobsBadge.svelte:131` | `bg-accent` (progress fill) | `bg-fg` |
| `StatusBadge.svelte:12` | `rounded-full border border-accent/30 bg-accent/10 px-2 py-0.5 text-accent` | `rounded-full border border-fg/50 px-2 py-0.5 text-fg` |
| `JobsBadge.svelte:98` | `border-accent/30 bg-accent/10 text-accent` | `border-fg/50 text-fg` |
| `QueueMode.svelte:671`, `LabelMode.svelte:339,354` | `border-accent/35 bg-accent/15 text-accent` / `border-accent bg-accent/20 text-accent` | `border-fg bg-surface-2 text-fg` |
| `AddRalliesPicker.svelte:140`, `FirstRun.svelte:102`, `Session.svelte:280` | `border-accent text-accent` / `border-accent text-fg` | `border-fg text-fg` |
| `ZoomBand.svelte:161` | `bg-accent/30` | `bg-fg/25` |
| `ZoomBand.svelte:184` | `border-2 border-accent bg-accent/25` | `border-2 border-fg bg-fg/20` |
| `ZoomBand.svelte:193,194` | `bg-accent` (drag handles) | `bg-fg` |
| `OverviewBand.svelte:34` | `bg-accent` (a plain rally) | `bg-dim` |
| `ScoreCurve.svelte:47` | `text-accent` (stroke inherits `currentColor`) | `text-fg` |
| `QuadCanvas.svelte:130` | `bg-accent/20` | `bg-fg/15` |
| `QuadCanvas.svelte:136` | `border-2 border-fg bg-accent` | `border-2 border-bg bg-fg` |
| `QuadCanvas.svelte:181`, `Library.svelte:159` | `accent-accent` (native control tint) | `accent-fg` |
| `Session.svelte:245` | `border-accent/50 bg-accent/5` | `border-line bg-surface` |
| `Session.svelte:246` | `text-body font-semibold text-accent` | `text-body font-semibold text-fg` |
| `Session.svelte:247` | `text-caption text-accent/80` | `text-caption text-dim` |

`OverviewBand` is the one that is not a like-for-like swap: its plain rallies were `accent` and the current rally is marked with `ring-2 ring-fg`. Making plain rallies `fg` would erase that distinction, so they become `dim`.

Two comments now describe a colour that no longer exists and must be rewritten rather than left:

`ClipsPanel.svelte:177` — replace with:

```svelte
      <!-- text-fg, not text-dim: gray text-buttons disappear next to the
           filenames beside them. There is no accent any more, so contrast
           rather than hue is what makes a text button look pressable. -->
```

`Reel.svelte:404-405` — replace with:

```svelte
      <!-- The filled-button idiom, app-wide: bg-fg with an explicit text-bg.
           It replaces a filled accent that needed the same explicit text-bg
           for a different reason (a white label on that blue measured ~2:1);
           here the pairing is correct by construction. -->
```

- [ ] **Step 4: Retarget the two token-asserting tests**

`web/tests/timeline-drag-gain.test.ts` and `web/tests/zoomband-handle-hit-target.test.ts` select elements by accent class names. Replace every `bg-accent` selector with `bg-fg` and every `border-accent` with `border-fg`. Find them with:

```bash
cd web && grep -n "accent" tests/timeline-drag-gain.test.ts tests/zoomband-handle-hit-target.test.ts
```

- [ ] **Step 5: Run everything**

```bash
cd web && npx vitest run && npm run check && npm run build
```

Expected: all tests pass including the new guard, `0 ERRORS 0 WARNINGS`, build clean.

- [ ] **Step 6: Look at it**

```bash
cd web && npm run build
```

Then load `http://localhost:8420` (with `splitstep serve` running) and confirm: filled buttons are light-on-dark, the queue progress bar is white, the ZoomBand window is outlined in white, and the jobs badge reads as a bordered pill rather than a blue one.

- [ ] **Step 7: Commit**

```bash
git add web/src web/tests
git commit -m "feat(web): the chrome stops having an opinion in blue

62 callsites across 18 files. Most became fg -- a filled button is now
bg-fg with text-bg, a selected control is border-fg, a progress fill is
solid fg -- which also fixes the four canvas draws that read the variable
at runtime, where periwinkle sat badly over the score curve.

OverviewBand is the one that is not a swap: its plain rallies were accent
and its current rally is ring-fg, so plain rallies became dim rather than
erasing the distinction.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Court geometry

**Files:**
- Create: `web/src/lib/court.ts`
- Create: `web/tests/court.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: `courtPaths(opts: CourtOptions): CourtGeometry`, where
  `CourtOptions = { width: number; height: number; fill?: number; baseline?: number; runoffWidthFt?: number }`
  and `CourtGeometry = { runoff: string; surface: string; lines: string[]; net: string; posts: string[] }`.
  Task 4 is the only consumer.

- [ ] **Step 1: Write the failing test**

Create `web/tests/court.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { courtPaths, project } from '../src/lib/court'

/** Pull every "x y" pair out of an SVG path's d attribute. */
function points(d: string): Array<[number, number]> {
  return [...d.matchAll(/(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)/g)].map(
    (m) => [Number(m[1]), Number(m[2])] as [number, number],
  )
}

describe('the court projection', () => {
  it('puts the far baseline above the near one', () => {
    // Screen y grows downward, so "further away" must mean a smaller y.
    expect(project(0, 78)[1]).toBeLessThan(project(0, 0)[1])
  })

  it('narrows with distance', () => {
    const near = project(18, 0)[0] - project(-18, 0)[0]
    const far = project(18, 78)[0] - project(-18, 78)[0]
    expect(far).toBeLessThan(near)
    expect(far).toBeGreaterThan(0)
  })

  it('is symmetric about the centre line', () => {
    for (const z of [0, 21, 39, 60, 78]) {
      expect(project(-13.5, z)[0]).toBeCloseTo(-project(13.5, z)[0], 6)
      expect(project(-13.5, z)[1]).toBeCloseTo(project(13.5, z)[1], 6)
    }
  })
})

describe('courtPaths', () => {
  const geo = courtPaths({ width: 1200, height: 520 })

  it('draws every marking a doubles court has', () => {
    // 2 baselines, 2 doubles sidelines, 2 singles sidelines, 2 service
    // lines, 1 centre service line, 2 centre marks.
    expect(geo.lines).toHaveLength(11)
    expect(geo.posts).toHaveLength(2)
  })

  it('closes the surface and the run-off', () => {
    expect(geo.surface.endsWith(' Z')).toBe(true)
    expect(geo.runoff.endsWith(' Z')).toBe(true)
  })

  it('centres the court horizontally', () => {
    const xs = points(geo.surface).map((p) => p[0])
    const mid = (Math.min(...xs) + Math.max(...xs)) / 2
    expect(mid).toBeCloseTo(600, 0)
  })

  // The run-off has to reach past the frame at every aspect ratio, or an
  // ultrawide viewport shows a void beside the court where the ground
  // simply stops.
  it.each([
    [1200, 520],
    [3440, 1000],
    [1024, 1400],
  ])('covers %ix%i with run-off', (width, height) => {
    const g = courtPaths({ width, height, runoffWidthFt: 150 })
    const xs = points(g.runoff).map((p) => p[0])
    expect(Math.min(...xs)).toBeLessThan(0)
    expect(Math.max(...xs)).toBeGreaterThan(width)
  })

  it('emits no NaN', () => {
    const all = [geo.runoff, geo.surface, geo.net, ...geo.lines, ...geo.posts]
    expect(all.some((d) => d.includes('NaN'))).toBe(false)
  })
})
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd web && npx vitest run tests/court.test.ts
```

Expected: FAIL — `Failed to resolve import "../src/lib/court"`.

- [ ] **Step 3: Write the implementation**

Create `web/src/lib/court.ts`:

```ts
/**
 * A doubles tennis court in perspective, as SVG path data.
 *
 * The camera is the one the app's own filming tips ask for: mounted on the
 * fence behind the baseline, up high, tilted down the court. Fixing it here
 * rather than parameterising it is deliberate -- the ground is meant to read
 * as *the* view SplitStep is built around, and a knob would invite drifting
 * off it.
 *
 * Pure and synchronous: CourtGround calls this once per resize, never per
 * frame.
 */

const CAMERA_HEIGHT_FT = 14
const CAMERA_Z_FT = -18 // behind the near baseline
const PITCH_RAD = (9 * Math.PI) / 180 // tilted down the court
const FOCAL = 900

// ITF dimensions, in feet.
const DOUBLES_HALF_W = 18
const SINGLES_HALF_W = 13.5
const LENGTH = 78
const NET_Z = 39
const SERVICE_OFFSET = 21
const NET_HALF_W = 21
const NET_HEIGHT_FT = 3.5
const CENTRE_MARK_FT = 1.5

const RUNOFF_BACK_FT = 102
const RUNOFF_FWD_FT = -12

/** World (x across, z down the court) to unscaled screen coordinates. */
export function project(x: number, z: number): [number, number] {
  const yc = -CAMERA_HEIGHT_FT
  const zc = z - CAMERA_Z_FT
  const y2 = yc * Math.cos(PITCH_RAD) + zc * Math.sin(PITCH_RAD)
  const z2 = -yc * Math.sin(PITCH_RAD) + zc * Math.cos(PITCH_RAD)
  return [(FOCAL * x) / z2, (-FOCAL * y2) / z2]
}

export interface CourtOptions {
  width: number
  height: number
  /** Court width as a fraction of the viewport. */
  fill?: number
  /** Where the near baseline sits, as a fraction of the height. */
  baseline?: number
  /** How far the apron reaches sideways, in feet. */
  runoffWidthFt?: number
}

export interface CourtGeometry {
  runoff: string
  surface: string
  lines: string[]
  net: string
  posts: string[]
}

/**
 * A straight line in the world is still straight after this projection, but
 * subdividing costs nothing and keeps the paths robust if the camera ever
 * gains lens distortion.
 */
function segment(
  a: [number, number],
  b: [number, number],
  n = 24,
): Array<[number, number]> {
  const out: Array<[number, number]> = []
  for (let i = 0; i <= n; i++) {
    out.push(project(a[0] + ((b[0] - a[0]) * i) / n, a[1] + ((b[1] - a[1]) * i) / n))
  }
  return out
}

function ring(
  x0: number,
  z0: number,
  x1: number,
  z1: number,
): Array<[number, number]> {
  return [
    ...segment([x0, z0], [x1, z0]),
    ...segment([x1, z0], [x1, z1]),
    ...segment([x1, z1], [x0, z1]),
    ...segment([x0, z1], [x0, z0]),
  ]
}

export function courtPaths(opts: CourtOptions): CourtGeometry {
  const { width, height, fill = 0.8, baseline = 1.06, runoffWidthFt = 150 } = opts

  // Framed on the court, not on the apron: the apron's near edge is a few
  // feet from the lens and would otherwise fill the entire frame.
  const corners = [
    project(-DOUBLES_HALF_W, 0),
    project(DOUBLES_HALF_W, 0),
    project(-DOUBLES_HALF_W, LENGTH),
    project(DOUBLES_HALF_W, LENGTH),
  ]
  const xs = corners.map((p) => p[0])
  const ys = corners.map((p) => p[1])
  const scale = (width * fill) / (Math.max(...xs) - Math.min(...xs))
  const ox = width / 2 - ((Math.min(...xs) + Math.max(...xs)) / 2) * scale
  const oy = height * baseline - Math.max(...ys) * scale

  const d = (pts: Array<[number, number]>, close = false): string =>
    'M' +
    pts.map(([x, y]) => `${(x * scale + ox).toFixed(1)} ${(y * scale + oy).toFixed(1)}`).join(' L') +
    (close ? ' Z' : '')

  const s = SINGLES_HALF_W
  const dw = DOUBLES_HALF_W

  return {
    runoff: d(ring(-runoffWidthFt, RUNOFF_FWD_FT, runoffWidthFt, RUNOFF_BACK_FT), true),
    surface: d(ring(-dw, 0, dw, LENGTH), true),
    lines: [
      d(segment([-dw, 0], [dw, 0])), // near baseline
      d(segment([-dw, LENGTH], [dw, LENGTH])), // far baseline
      d(segment([-dw, 0], [-dw, LENGTH])), // doubles sidelines
      d(segment([dw, 0], [dw, LENGTH])),
      d(segment([-s, 0], [-s, LENGTH])), // singles sidelines
      d(segment([s, 0], [s, LENGTH])),
      d(segment([-s, NET_Z - SERVICE_OFFSET], [s, NET_Z - SERVICE_OFFSET])),
      d(segment([-s, NET_Z + SERVICE_OFFSET], [s, NET_Z + SERVICE_OFFSET])),
      d(segment([0, NET_Z - SERVICE_OFFSET], [0, NET_Z + SERVICE_OFFSET])),
      d(segment([0, 0], [0, CENTRE_MARK_FT])), // centre marks
      d(segment([0, LENGTH - CENTRE_MARK_FT], [0, LENGTH])),
    ],
    net: d(segment([-NET_HALF_W, NET_Z], [NET_HALF_W, NET_Z])),
    posts: [-NET_HALF_W, NET_HALF_W].map((x) => {
      const [bx, by] = project(x, NET_Z)
      const top = by - (NET_HEIGHT_FT * FOCAL) / ((NET_Z - CAMERA_Z_FT) * Math.cos(PITCH_RAD))
      const px = (bx * scale + ox).toFixed(1)
      return `M${px} ${(by * scale + oy).toFixed(1)} L${px} ${(top * scale + oy).toFixed(1)}`
    }),
  }
}
```

- [ ] **Step 4: Run the test**

```bash
cd web && npx vitest run tests/court.test.ts
```

Expected: PASS, all cases.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/court.ts web/tests/court.test.ts
git commit -m "feat(web): a doubles court, in perspective, as a pure function

The camera is fixed rather than parameterised: it is the fence mount the
app's own filming tips ask for, and the ground is meant to read as the view
SplitStep is built around. A knob would only invite drifting off it.

Framed on the court rather than on the apron -- the apron's near edge sits
a few feet from the lens and fills the whole frame if you fit to it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: The ground

**Files:**
- Create: `web/src/components/CourtGround.svelte`
- Modify: `web/src/App.svelte`

**Interfaces:**
- Consumes: `courtPaths` from Task 3.
- Produces: `<CourtGround tier={'browse' | 'review'} />`. Task 6 sets `tier` from the route.

- [ ] **Step 1: Write the failing test**

Create `web/tests/court-ground.test.ts`:

```ts
import { mount, unmount } from 'svelte'
import { afterEach, describe, expect, it } from 'vitest'
import CourtGround from '../src/components/CourtGround.svelte'

let host: HTMLElement | null = null
let app: Record<string, unknown> | null = null

function render(props: { tier: 'browse' | 'review' }) {
  host = document.createElement('div')
  document.body.appendChild(host)
  app = mount(CourtGround, { target: host, props })
  return host
}

afterEach(() => {
  if (app) unmount(app)
  host?.remove()
  app = null
  host = null
})

describe('CourtGround', () => {
  it('draws a court', () => {
    const el = render({ tier: 'browse' })
    // 11 markings + net + 2 posts + surface + run-off.
    expect(el.querySelectorAll('path').length).toBe(16)
  })

  it('hides itself from assistive tech', () => {
    const el = render({ tier: 'browse' })
    expect(el.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true')
  })

  // The review tier is the whole point of having tiers: atmosphere behind
  // footage you are judging competes with the footage.
  it('recedes on the review tier', () => {
    const browse = render({ tier: 'browse' })
    const browseOpacity = Number(getComputedStyle(browse.firstElementChild!).opacity)
    if (app) unmount(app)
    host?.remove()
    const review = render({ tier: 'review' })
    const reviewOpacity = Number(getComputedStyle(review.firstElementChild!).opacity)
    expect(reviewOpacity).toBeLessThan(browseOpacity)
  })
})
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd web && npx vitest run tests/court-ground.test.ts
```

Expected: FAIL — cannot resolve `CourtGround.svelte`.

- [ ] **Step 3: Write the component**

Create `web/src/components/CourtGround.svelte`:

```svelte
<script lang="ts">
  import { courtPaths } from '../lib/court'

  interface Props {
    /** browse: full strength. review: frozen, dropped back, scrimmed hard. */
    tier: 'browse' | 'review'
  }
  let { tier }: Props = $props()

  // Fixed to the viewport rather than to the page, so a long timeline does
  // not scroll the court away and leave bare bg behind the content.
  let w = $state(1600)
  let h = $state(900)

  const geo = $derived(courtPaths({ width: w, height: h }))

  // The scrim is not decoration: `dim` measures 2.41:1 and `faint` 2.09:1
  // over bare court, so the ground has to be darkened wherever the layout
  // might put secondary text over it. Browse keeps the middle band open
  // because cards carry their own `surface`; review darkens throughout.
  const scrim = $derived(
    tier === 'review'
      ? { top: 0.92, mid: 0.74, bottom: 0.9 }
      : { top: 0.82, mid: 0.18, bottom: 0.62 },
  )
</script>

<svelte:window bind:innerWidth={w} bind:innerHeight={h} />

<div
  class="pointer-events-none fixed inset-0 -z-10 motion-safe:transition-opacity motion-safe:duration-slow"
  style="opacity: {tier === 'review' ? 0.55 : 1}"
>
  <!-- aria-hidden: this is wallpaper. It carries no information a reader
       needs and announcing sixteen paths would be noise. -->
  <svg
    class="h-full w-full"
    viewBox="0 0 {w} {h}"
    preserveAspectRatio="xMidYMax slice"
    aria-hidden="true"
  >
    <defs>
      <linearGradient id="court-scrim" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="var(--color-bg)" stop-opacity={scrim.top} />
        <stop offset="45%" stop-color="var(--color-bg)" stop-opacity={scrim.mid} />
        <stop offset="100%" stop-color="var(--color-bg)" stop-opacity={scrim.bottom} />
      </linearGradient>
    </defs>
    <path d={geo.runoff} fill="var(--color-court-run)" />
    <path d={geo.surface} fill="var(--color-court)" />
    {#each geo.lines as line (line)}
      <path
        d={line}
        fill="none"
        stroke="var(--color-court-line)"
        stroke-opacity="0.7"
        stroke-width="2"
        stroke-linecap="square"
      />
    {/each}
    <path d={geo.net} fill="none" stroke="var(--color-court-line)" stroke-opacity="0.45" stroke-width="2" />
    {#each geo.posts as post (post)}
      <path d={post} fill="none" stroke="var(--color-court-line)" stroke-opacity="0.5" stroke-width="3" />
    {/each}
    <rect width={w} height={h} fill="url(#court-scrim)" />
  </svg>
</div>
```

- [ ] **Step 4: Add the motion token the component just used**

In `web/src/app.css`, add a **second** theme block after the existing `@theme { … }` block:

```css
/* Motion. Durations are named for what they are for, not for their length,
   because the constraint they encode is behavioural: `quick` is what a
   confirmation gets in the review loop, where a reviewer presses a key
   hundreds of times a session and anything slower is still on screen when
   the next one lands. `slow` is chrome only and never fires in that loop.

   `--transition-duration-*` and not `--duration-*`: that is the namespace
   Tailwind v4 generates `duration-…` utilities from, and a token in the
   wrong one produces no utility *and* vanishes from the output without an
   error -- verified against Tailwind 4.3.3.

   `static` because Tailwind tree-shakes theme variables no utility
   references, and the `animate-[…]` arbitrary values below name these
   variables directly rather than through a utility. */
@theme static {
  --transition-duration-quick: 160ms;
  --transition-duration-calm: 260ms;
  --transition-duration-slow: 420ms;
  --ease-out-soft: cubic-bezier(0.22, 0.61, 0.36, 1);
}
```

- [ ] **Step 5: Mount it**

In `web/src/App.svelte`, add the import and render it above `<main>`:

```svelte
  import CourtGround from './components/CourtGround.svelte'
```

```svelte
<CourtGround tier={router.current.name === 'session' ? 'review' : 'browse'} />

<main class="mx-auto max-w-6xl p-6">
```

Leave `max-w-6xl` for now — Task 6 replaces it.

- [ ] **Step 6: Run the tests**

```bash
cd web && npx vitest run tests/court-ground.test.ts && npx vitest run && npm run check
```

Expected: PASS, `0 ERRORS 0 WARNINGS`.

- [ ] **Step 7: Look at it**

```bash
cd web && npm run build
```

Load the app. Confirm the court is visible behind the Library list, that scrolling a session's timeline does not move it, and that opening a session visibly quiets it.

- [ ] **Step 8: Commit**

```bash
git add web/src/components/CourtGround.svelte web/src/App.svelte web/src/app.css web/tests/court-ground.test.ts
git commit -m "feat(web): the ground becomes a court, and knows when to shut up

Two tiers. Browse renders it at full strength; review freezes it, drops it
to 55% and raises the scrim, because atmosphere behind footage you are
judging competes with the footage -- night-court video is already violet
with blown floodlights and glowing chrome muddies the rally boundaries you
are there to read.

The scrim is a contrast device, not a mood: dim measures 2.41:1 and faint
2.09:1 over bare court, so the ground is darkened wherever the layout can
put secondary text over it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: The mark

**Files:**
- Create: `web/src/lib/mark.ts`
- Create: `web/src/components/Mark.svelte`
- Create: `web/tests/mark.test.ts`
- Modify: `web/src/routes/Library.svelte:200`, `Reels.svelte:81`, `Reel.svelte:356`, `Session.svelte:242`, `Setup.svelte:143`, `components/AddRalliesPicker.svelte:156`, `components/LabelMode.svelte:249`

**Interfaces:**
- Consumes: the `ball` and `court-line` tokens from Task 1.
- Produces: `MARK` (the geometry constants, read by Task 9's Python guard) and `<Mark size={number} state={'still' | 'loading'} />`.

- [ ] **Step 1: Write the failing test**

Create `web/tests/mark.test.ts`:

```ts
import { mount, unmount } from 'svelte'
import { afterEach, describe, expect, it } from 'vitest'
import Mark from '../src/components/Mark.svelte'
import { MARK } from '../src/lib/mark'

let host: HTMLElement | null = null
let app: Record<string, unknown> | null = null

function render(props: { size: number; state?: 'still' | 'loading' }) {
  host = document.createElement('div')
  document.body.appendChild(host)
  app = mount(Mark, { target: host, props })
  return host
}

afterEach(() => {
  if (app) unmount(app)
  host?.remove()
  app = null
  host = null
})

describe('the mark', () => {
  it('is a ball split into two halves', () => {
    const el = render({ size: 32 })
    expect(el.querySelectorAll('circle').length).toBe(2)
  })

  it('carries both seams in each half, or it is not a tennis ball', () => {
    // Four arcs: two seams, drawn once per half. Dropping them is what made
    // the first draft read as two plain half-discs.
    const el = render({ size: 32 })
    expect(el.querySelectorAll('path').length).toBe(4)
  })

  it('honours the size it is given', () => {
    const el = render({ size: 19 })
    const svg = el.querySelector('svg')!
    expect(svg.getAttribute('width')).toBe('19')
    expect(svg.getAttribute('height')).toBe('19')
  })

  it('says nothing to a screen reader when decorative', () => {
    const el = render({ size: 19 })
    expect(el.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true')
  })

  it('announces itself when it is the loading state', () => {
    const el = render({ size: 32, state: 'loading' })
    expect(el.querySelector('[role="status"]')).not.toBeNull()
    expect(el.textContent).toContain('Loading')
  })
})

describe('MARK constants', () => {
  // packaging/make_icon.py draws the same ball in Pillow and asserts against
  // these numbers (tests/test_icon.py). Two drawings of one shape drift, so
  // this object is the single source and the Python side is the follower.
  it('is complete', () => {
    for (const key of [
      'viewBox', 'radius', 'gap', 'step',
      'seamWidth', 'seamRx', 'seamRy', 'seamTopY', 'seamBottomY',
      'seamLeftX', 'seamRightX',
    ] as const) {
      expect(typeof MARK[key], key).toBe('number')
    }
  })

  it('splits the ball without detaching it', () => {
    // A gap wider than a quarter of the radius stops reading as one ball --
    // which is exactly the note that killed the widest icon variant.
    expect(MARK.gap).toBeGreaterThan(0)
    expect(MARK.gap).toBeLessThan(MARK.radius / 4)
  })
})
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd web && npx vitest run tests/mark.test.ts
```

Expected: FAIL — cannot resolve `../src/lib/mark`.

- [ ] **Step 3: Write the constants**

Create `web/src/lib/mark.ts`:

```ts
/**
 * The SplitStep mark: a tennis ball cut in two, the halves stepped apart.
 * The product's name, drawn.
 *
 * These numbers are the single source of truth for the shape. The mark is
 * drawn twice -- as SVG here for the app, and in Pillow by
 * packaging/make_icon.py for the .icns -- and two drawings of one shape
 * drift silently until the Dock icon and the header are different logos.
 * tests/test_icon.py parses this file and asserts the Python side matches.
 *
 * All values are in the 32-unit viewBox.
 */
export const MARK = {
  viewBox: 32,
  radius: 13,
  /** Horizontal half-gap between the two halves. */
  gap: 0.9,
  /** Vertical offset of each half: the "step". */
  step: 1.5,
  seamWidth: 2.6,
  seamRx: 13.8,
  seamRy: 13.3,
  seamTopY: 4.5,
  seamBottomY: 27.5,
  seamLeftX: 6.8,
  seamRightX: 25.2,
} as const
```

- [ ] **Step 4: Write the component**

Create `web/src/components/Mark.svelte`:

```svelte
<script lang="ts">
  import { MARK } from '../lib/mark'

  interface Props {
    size?: number
    /** `loading` steps the halves apart and back; it replaces "Loading…". */
    state?: 'still' | 'loading'
    /** What the loading state announces. */
    label?: string
  }
  let { size = 19, state = 'still', label = 'Loading…' }: Props = $props()

  const c = MARK.viewBox / 2
  const seam = `M${MARK.seamLeftX} ${MARK.seamTopY} A${MARK.seamRx} ${MARK.seamRy} 0 0 1 ${MARK.seamLeftX} ${MARK.seamBottomY}`
  const seam2 = `M${MARK.seamRightX} ${MARK.seamTopY} A${MARK.seamRx} ${MARK.seamRy} 0 0 0 ${MARK.seamRightX} ${MARK.seamBottomY}`
  // Unique per instance: two marks on one page would otherwise share clip
  // paths, and the second would clip against the first's rects.
  const uid = $props.id()
</script>

<span class="inline-flex items-center" role={state === 'loading' ? 'status' : undefined}>
  <svg
    width={size}
    height={size}
    viewBox="0 0 {MARK.viewBox} {MARK.viewBox}"
    aria-hidden="true"
    class:mark-loading={state === 'loading'}
  >
    <defs>
      <clipPath id="mk-l-{uid}"><rect x="0" y="0" width={c - 0.5} height={MARK.viewBox} /></clipPath>
      <clipPath id="mk-r-{uid}"><rect x={c + 0.5} y="0" width={c - 0.5} height={MARK.viewBox} /></clipPath>
      <clipPath id="mk-d-{uid}"><circle cx={c} cy={c} r={MARK.radius} /></clipPath>
    </defs>
    <g class="mark-half mark-left" clip-path="url(#mk-l-{uid})">
      <g clip-path="url(#mk-d-{uid})">
        <circle cx={c} cy={c} r={MARK.radius} fill="var(--color-ball)" />
        <path d={seam} fill="none" stroke="var(--color-court-line)" stroke-width={MARK.seamWidth} />
        <path d={seam2} fill="none" stroke="var(--color-court-line)" stroke-width={MARK.seamWidth} />
      </g>
    </g>
    <g class="mark-half mark-right" clip-path="url(#mk-r-{uid})">
      <g clip-path="url(#mk-d-{uid})">
        <circle cx={c} cy={c} r={MARK.radius} fill="var(--color-ball)" />
        <path d={seam} fill="none" stroke="var(--color-court-line)" stroke-width={MARK.seamWidth} />
        <path d={seam2} fill="none" stroke="var(--color-court-line)" stroke-width={MARK.seamWidth} />
      </g>
    </g>
  </svg>
  {#if state === 'loading'}
    <span class="sr-only">{label}</span>
  {/if}
</span>

<style>
  /* The halves' resting offset. Written here rather than as a transform
     attribute so the loading animation has something to animate away from. */
  .mark-left {
    transform: translate(calc(var(--mk-gap) * -1), calc(var(--mk-step) * -1));
  }
  .mark-right {
    transform: translate(var(--mk-gap), var(--mk-step));
  }
  svg {
    --mk-gap: 0.9px;
    --mk-step: 1.5px;
  }
  .mark-half {
    transform-box: view-box;
  }

  /* transform only, so the compositor owns it: this can be on screen while
     a 4K proxy decodes and a Python worker runs YOLO on the same laptop. */
  @media (prefers-reduced-motion: no-preference) {
    .mark-loading .mark-left {
      animation: mark-step-left 1400ms var(--ease-out-soft) infinite alternate;
    }
    .mark-loading .mark-right {
      animation: mark-step-right 1400ms var(--ease-out-soft) infinite alternate;
    }
  }

  @keyframes mark-step-left {
    from {
      transform: translate(-0.9px, -1.5px);
    }
    to {
      transform: translate(-2.4px, -4px);
    }
  }
  @keyframes mark-step-right {
    from {
      transform: translate(0.9px, 1.5px);
    }
    to {
      transform: translate(2.4px, 4px);
    }
  }
</style>
```

- [ ] **Step 5: Run the test**

```bash
cd web && npx vitest run tests/mark.test.ts
```

Expected: PASS.

- [ ] **Step 6: Replace every bare "Loading…"**

In each of these files, replace the loading paragraph with the mark. The exact current line is `<p class="text-body text-dim">Loading…</p>` in `Library.svelte:200`, `Reels.svelte:81`, `Reel.svelte:356`, `Session.svelte:242`, `Setup.svelte:143`, `AddRalliesPicker.svelte:156`; `LabelMode.svelte:249` reads `Loading labels…`.

Replace with (adjusting the label for LabelMode to `Loading labels…`):

```svelte
<div class="flex justify-center py-12">
  <Mark size={40} state="loading" />
</div>
```

and add to each file's script block:

```svelte
  import Mark from '../components/Mark.svelte'
```

(For files already in `components/`, the path is `./Mark.svelte`.)

- [ ] **Step 7: Run everything**

```bash
cd web && npx vitest run && npm run check && npm run build
```

Expected: all pass, `0 ERRORS 0 WARNINGS`. Note `npm run check` will catch a wrong relative import path.

- [ ] **Step 8: Commit**

```bash
git add web/src/lib/mark.ts web/src/components/Mark.svelte web/tests/mark.test.ts web/src
git commit -m "feat(web): the app gets a mark, and it is the loading state

A tennis ball cut in two, halves stepped apart -- split, and step. It
replaces seven bare 'Loading…' strings, which is the right home for it:
an indeterminate spinner needs exactly the two states the name already
describes.

The seams are drawn in both halves and are not optional. Without them the
shape reads as two plain half-discs and not as a ball at all, which is the
note the first draft earned.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: The bar, the breadcrumb, and the ultrawide

At 3440px the app occupies 33% of the width with 1144px dead on each side, and Reels is reachable only from Library. Both are fixed here.

**Files:**
- Create: `web/src/components/AppBar.svelte`
- Create: `web/tests/app-bar.test.ts`
- Modify: `web/src/App.svelte`
- Modify: `web/src/routes/Library.svelte` (header block, lines ~128-197), `Reels.svelte` (header block, ~55-60), `Session.svelte`, `Reel.svelte`, `Setup.svelte`

**Interfaces:**
- Consumes: `Mark` (Task 5).
- Produces: `<AppBar />`, self-contained — it reads the route itself. Library's Settings popover, `librarySize`, `JobsBadge` and the friend-mode toggle all move into it unchanged.

- [ ] **Step 1: Write the failing test**

Create `web/tests/app-bar.test.ts`:

```ts
import { mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AppBar from '../src/components/AppBar.svelte'

// The bar reads the drive figure from a store, not from `api` directly --
// librarysize.svelte.ts is stale-while-revalidate so the header does not
// blank on every navigation.
vi.mock('../src/lib/librarysize.svelte', () => ({
  librarySize: { bytes: 35_433_480_192, refresh: vi.fn(async () => {}) },
}))

vi.mock('../src/lib/api', () => ({
  api: { jobs: vi.fn(async () => []), config: vi.fn(async () => ({ mode: 'dev' })) },
}))

vi.mock('../src/lib/shell', () => ({
  inShell: () => false,
  changeLibrary: vi.fn(async () => {}),
}))

let host: HTMLElement | null = null
let app: Record<string, unknown> | null = null

beforeEach(() => {
  window.location.hash = '#/'
})

afterEach(() => {
  if (app) unmount(app)
  host?.remove()
  app = null
  host = null
})

function render() {
  host = document.createElement('div')
  document.body.appendChild(host)
  app = mount(AppBar, { target: host, props: {} })
  return host
}

describe('AppBar', () => {
  // Reels used to be reachable only from Library -- a link inside one
  // route's header, sitting at the same weight as a storage figure.
  it('offers Sessions and Reels as peers', () => {
    const el = render()
    const labels = [...el.querySelectorAll('nav a, nav button')].map((n) => n.textContent?.trim())
    expect(labels).toContain('Sessions')
    expect(labels).toContain('Reels')
  })

  it('marks the current route', () => {
    const el = render()
    const current = el.querySelector('[aria-current="page"]')
    expect(current?.textContent?.trim()).toBe('Sessions')
  })

  it('carries the mark', () => {
    const el = render()
    expect(el.querySelector('svg circle')).not.toBeNull()
  })
})
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd web && npx vitest run tests/app-bar.test.ts
```

Expected: FAIL — cannot resolve `AppBar.svelte`.

- [ ] **Step 3: Build the bar**

Create `web/src/components/AppBar.svelte`. Move into it, unchanged in behaviour, the whole `<header>` block currently in `Library.svelte` (lines ~128-197): the `librarySize` display, the Settings popover with its Escape handler, transparent backdrop, friend-mode checkbox, `Change library…` and `<Credits />`, and `<JobsBadge />`. The only changes are structural.

```svelte
<script lang="ts">
  import Credits from './Credits.svelte'
  import JobsBadge from './JobsBadge.svelte'
  import Mark from './Mark.svelte'
  import { appmode } from '../lib/appmode.svelte'
  import { librarySize } from '../lib/librarysize.svelte'
  import { formatBytes } from '../lib/reels'
  import { createRouter, navigate } from '../lib/router.svelte'
  import { changeLibrary, inShell } from '../lib/shell'

  const router = createRouter()
  let settingsOpen = $state(false)
  let switchError = $state<string | null>(null)

  // Moved verbatim from Library.svelte. Stale-while-revalidate: the store
  // keeps the last figure across route remounts, so the bar does not blank
  // and the server does not re-walk the drive on every navigation.
  void librarySize.refresh()

  async function switchLibrary() {
    switchError = null
    try {
      await changeLibrary()
      // No success branch: the shell navigates the window away to the
      // chooser, so reaching the next line means it did not.
    } catch (e) {
      switchError = e instanceof Error ? e.message : String(e)
    }
  }

  // Two tabs, and the route decides which is lit. `reel` counts as Reels:
  // a reel's own page is inside that section, not a third place.
  const TABS = [
    { label: 'Sessions', to: '/', matches: ['library', 'session', 'setup'] },
    { label: 'Reels', to: '/reels', matches: ['reels', 'reel'] },
  ] as const
</script>
```

The markup — full-bleed, `bg` at 72% with a blur so the court reads through it without taking any text with it:

```svelte
<header
  class="sticky top-0 z-30 flex h-13 items-center gap-4 border-b border-line
         bg-bg/70 px-5 backdrop-blur-lg"
>
  <a href="#/" class="flex items-center gap-2">
    <Mark size={19} />
    <span class="text-body font-semibold tracking-tight">SplitStep</span>
  </a>

  <nav class="flex gap-0.5">
    {#each TABS as tab (tab.to)}
      {@const active = tab.matches.includes(router.current.name)}
      <button
        class="rounded-lg border px-3 py-1.5 text-body motion-safe:transition-colors
               motion-safe:duration-quick
               {active
          ? 'border-line bg-surface-2 text-fg'
          : 'border-transparent text-dim hover:text-fg'}"
        aria-current={active ? 'page' : undefined}
        onclick={() => navigate(tab.to)}
      >
        {tab.label}
      </button>
    {/each}
  </nav>

  <div class="ml-auto flex items-center gap-3">
    <!-- The keep-everything policy's one disk affordance: informational,
         no action attached (spec 2026-08-26). It sits with Settings rather
         than beside the nav, because it is status and not a destination --
         which is exactly what it looked like before. -->
    {#if librarySize.bytes !== null}
      <span class="rounded-full border border-line px-2.5 py-1 font-data text-caption text-faint">
        {formatBytes(librarySize.bytes)} free
      </span>
    {/if}
    <!-- …Settings popover, moved verbatim from Library.svelte… -->
    <JobsBadge />
  </div>
</header>
```

Then in `Library.svelte`, `Reels.svelte`, `Session.svelte`, `Reel.svelte` and `Setup.svelte`, delete the header blocks that duplicate this and replace the page identity with a heading plus breadcrumb. For `Session.svelte`, the current `← library  {id}` becomes:

```svelte
<div class="mb-5">
  <button class="font-data text-caption text-faint hover:text-dim" onclick={() => navigate('/')}>
    Sessions
  </button>
  <span class="font-data text-caption text-faint"> › </span>
  <h1 class="mt-1 text-display font-semibold">{id}</h1>
</div>
```

- [ ] **Step 4: Replace the layout container**

In `web/src/App.svelte`:

```svelte
<CourtGround tier={isReview ? 'review' : 'browse'} />
<AppBar />

<!--
  Was `max-w-6xl` -- a 1152px column centred in whatever the monitor is,
  which on a 3440px ultrawide left 1144px dead on each side and capped the
  review video at 1152 against a proxy that is 1920x1080.

  Review is capped at 1920 rather than at the container, because past that
  the video is upscaling and gains nothing. The width it does not use shows
  the quieted court and nothing else: queue review is a keyboard loop with
  the eyes on one rectangle, and a panel beside it is something to look at
  that is not the rally.
-->
<main
  class="mx-auto w-full p-6 {isReview
    ? 'max-w-[1920px]'
    : 'max-w-[min(2600px,92vw)]'}"
>
```

with, in the script:

```ts
  const isReview = $derived(router.current.name === 'session')
```

- [ ] **Step 5: Run the tests**

```bash
cd web && npx vitest run && npm run check
```

Expected: all pass, `0 ERRORS 0 WARNINGS`. `route-effect-staleness.test.ts` and `library-setup-card.test.ts` both mount routes whose headers just changed — if either fails on a removed element, update the selector, not the component.

- [ ] **Step 6: Look at it at both widths**

Build, then load the app and check at a normal window and at full ultrawide width: the bar spans the full width at both, Reels is reachable from a session page, and the session heading reads `Sessions › 2026-08-18`.

- [ ] **Step 7: Commit**

```bash
git add web/src web/tests
git commit -m "feat(web): a bar that exists on every route, and an ultrawide that is used

Reels was reachable from exactly one screen, as a link inside Library's
header sitting at the same weight as a storage figure. It is now a tab
beside Sessions, on every route.

max-w-6xl was a 1152px column centred in whatever the monitor is: at 3440
that is 33% of the width, 1144px dead each side, and a review video capped
at 1152 against a 1920x1080 proxy. Browse now grows to min(2600px, 92vw);
review caps at 1920 because past that it upscales, and the width it does
not use shows the quieted court rather than a panel -- the eyes are on one
rectangle and anything beside it is something else to look at.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Cards

**Files:**
- Modify: `web/src/routes/Library.svelte` (list block, ~204-232), `web/src/routes/Reels.svelte` (list block, ~85-113)
- Modify: `web/src/components/StatusBadge.svelte`
- Modify: `web/src/components/Thumb.svelte`
- Create: `web/tests/session-card.test.ts`

**Interfaces:**
- Consumes: the bar from Task 6.
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Write the failing test**

Create `web/tests/session-card.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const LIBRARY = readFileSync(
  fileURLToPath(new URL('../src/routes/Library.svelte', import.meta.url)),
  'utf8',
)

describe('the session list', () => {
  // A 152px thumbnail alone on a 3400px row is the ultrawide complaint in
  // miniature: the card has to fill the space or stop claiming it.
  it('goes two-up once there is room', () => {
    expect(LIBRARY).toMatch(/grid-cols-1/)
    expect(LIBRARY).toMatch(/min-\[1800px\]:grid-cols-2/)
  })

  it('keeps the star and point counts coloured', () => {
    expect(LIBRARY).toMatch(/text-point/)
    expect(LIBRARY).toMatch(/text-star/)
  })
})
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd web && npx vitest run tests/session-card.test.ts
```

Expected: FAIL — no grid classes.

- [ ] **Step 3: Rework the list**

In `Library.svelte`, replace `<ul class="space-y-2">` with:

```svelte
  <ul class="grid grid-cols-1 gap-3 min-[1800px]:grid-cols-2">
```

and the card button's class with:

```svelte
          class="flex w-full items-center gap-4 rounded-xl border border-line bg-surface p-2.5
                 text-left shadow-[0_1px_0_rgba(255,255,255,0.05)_inset,0_12px_28px_-18px_#000]
                 hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-offset-2
                 focus-visible:outline-fg motion-safe:transition-colors
                 motion-safe:duration-quick"
```

Apply the same two changes to `Reels.svelte`.

- [ ] **Step 4: Grow the thumbnail**

In `Thumb.svelte`, change `w-32` to `w-38 min-[1800px]:w-70`.

- [ ] **Step 5: Outline the active badge**

`StatusBadge.svelte`'s `TONE` should already read `border-fg/50 … text-fg` for `active` after Task 2. Confirm, and update the surrounding comment so it explains the outline rather than the fill:

```svelte
  // `quiet` is bare text rather than a pill: a settled state is the common
  // case, and giving every row a chip makes the two that actually want
  // attention indistinguishable from the rest -- the same failure the raw
  // status string had. `active` is an outline rather than a fill because
  // the chrome carries no hue: contrast and a border do the work a coloured
  // background used to.
```

- [ ] **Step 6: Run the tests**

```bash
cd web && npx vitest run && npm run check
```

Expected: all pass. `thumb.test.ts` asserts on Thumb's classes — update it to the new width if it fails.

- [ ] **Step 7: Commit**

```bash
git add web/src web/tests
git commit -m "feat(web): cards with room to breathe, and two of them across

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Confirmations move

**Files:**
- Modify: `web/src/app.css` (keyframes)
- Modify: `web/src/components/QueueMode.svelte`
- Create: `web/tests/confirmation-motion.test.ts`

**Interfaces:**
- Consumes: the motion tokens added in Task 4.
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Write the failing test**

Create `web/tests/confirmation-motion.test.ts`:

```ts
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const CSS = readFileSync(fileURLToPath(new URL('../src/app.css', import.meta.url)), 'utf8')

describe('motion', () => {
  // The namespace matters: a token declared as `--duration-quick` generates
  // no utility and is dropped from the compiled output without an error,
  // which is a failure with nothing to notice.
  it('names its durations in the namespace Tailwind actually reads', () => {
    expect(CSS).toMatch(/--transition-duration-quick:/)
    expect(CSS).toMatch(/--transition-duration-calm:/)
    expect(CSS).toMatch(/--transition-duration-slow:/)
    expect(CSS).toMatch(/--ease-out-soft:/)
    expect(CSS).not.toMatch(/--duration-(quick|calm|slow):/)
  })

  // Tailwind drops theme variables no utility references, and the counter's
  // animate-[…] names these directly rather than through one.
  it('declares them statically so the animations can name them', () => {
    expect(CSS).toMatch(/@theme static/)
  })

  // Star, point and reject are pressed hundreds of times a session. A
  // confirmation still on screen when the next keypress lands reads as one
  // long event rather than two, which is the bug the original verdict
  // keyframe was written to avoid.
  it('keeps every confirmation under 200ms', () => {
    const quick = CSS.match(/--transition-duration-quick:\s*(\d+)ms/)
    expect(Number(quick?.[1])).toBeLessThan(200)
  })

  it('gives the counter its own keyframe', () => {
    expect(CSS).toMatch(/@keyframes counter-roll/)
  })
})
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd web && npx vitest run tests/confirmation-motion.test.ts
```

Expected: FAIL on `counter-roll`.

- [ ] **Step 3: Add the keyframe**

In `web/src/app.css`, below the existing `@keyframes verdict`:

```css
/* The rally counter, when the queue advances. It travels a few pixels rather
   than cross-fading, so a run of fast presses reads as a series of discrete
   advances instead of one continuous blur -- the same reasoning the verdict
   flash is built on, and the same 200ms ceiling, because both fire on a key
   the reviewer presses hundreds of times a session. */
@keyframes counter-roll {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
```

- [ ] **Step 4: Apply it in QueueMode**

In `QueueMode.svelte`, find the rally position display (the `rally N / M` text in the status row) and key it on the rally id so it re-animates per advance:

```svelte
{#key rally.id}
  <span
    class="motion-safe:animate-[counter-roll_var(--transition-duration-quick)_var(--ease-out-soft)]"
  >
    rally {index + 1} / {total}
  </span>
{/key}
```

Use whatever the local variable names actually are at that site — read the surrounding lines rather than assuming `rally`, `index` and `total`.

- [ ] **Step 5: Run the tests**

```bash
cd web && npx vitest run && npm run check
```

Expected: all pass.

- [ ] **Step 6: Verify it does not delay a keypress**

With `splitstep serve` running and a session with unseen rallies, hold `→` for several seconds. The counter must keep up with the key repeat and never queue behind an animation. If it visibly lags, the animation is wrong and the `{#key}` block should be reverted — the constraint outranks the effect.

- [ ] **Step 7: Commit**

```bash
git add web/src/app.css web/src/components/QueueMode.svelte web/tests/confirmation-motion.test.ts
git commit -m "feat(web): the counter advances visibly, and still under 200ms

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: The icon, and a guard against two logos

**Files:**
- Modify: `packaging/make_icon.py`
- Create: `tests/test_icon.py`

**Interfaces:**
- Consumes: `MARK` from `web/src/lib/mark.ts` (Task 5) and the `--color-ball` / `--color-court` / `--color-court-line` / `--color-bg` tokens (Task 1).
- Produces: `src-tauri/icons/icon.icns` and the PNG siblings Tauri's bundler wants.

- [ ] **Step 1: Write the failing test**

Create `tests/test_icon.py`:

```python
"""The mark is drawn twice -- SVG in Mark.svelte, Pillow in make_icon.py --
and two drawings of one shape drift until the Dock icon and the header are
different logos. web/src/lib/mark.ts is the source; this is the follower's
receipt."""

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MARK_TS = REPO / "web" / "src" / "lib" / "mark.ts"
MAKE_ICON = REPO / "packaging" / "make_icon.py"


def ts_constants() -> dict[str, float]:
    """Every `name: number` inside mark.ts's MARK object."""
    body = MARK_TS.read_text()
    block = re.search(r"export const MARK = \{(.*?)\} as const", body, re.S)
    assert block, "MARK object not found in mark.ts"
    return {
        m.group(1): float(m.group(2))
        for m in re.finditer(r"^\s*(\w+):\s*(-?[\d.]+),", block.group(1), re.M)
    }


def test_the_two_drawings_of_the_ball_agree():
    from packaging.make_icon import MARK as py_mark

    ts = ts_constants()
    assert ts, "mark.ts declared no constants"
    # Every constant the TypeScript declares must exist in Python with the
    # same value. Python may not declare extras it does not use, either --
    # an unused constant is one that silently stopped matching.
    assert py_mark == pytest.approx(ts)


def test_the_icon_reads_its_colours_from_the_theme():
    # The existing guarantee, restated for the new tokens: make_icon.py must
    # not hardcode a hex, or the icon drifts off the chrome beside it.
    source = MAKE_ICON.read_text()
    body = source.split("def main")[0]
    hexes = re.findall(r'"#[0-9a-fA-F]{6}"', body)
    # Fallbacks inside token() calls are permitted; bare literals are not.
    for literal in hexes:
        assert f'token(' in source, f"bare colour literal {literal} in make_icon.py"


def test_every_size_renders_without_error():
    from packaging.make_icon import render

    for size in (16, 32, 128, 512, 1024):
        img = render(size)
        assert img.size == (size, size)
        assert img.mode == "RGBA"
```

- [ ] **Step 2: Run it and watch it fail**

```bash
~/miniconda3/envs/splitstep/bin/pytest tests/test_icon.py -q
```

Expected: FAIL — `make_icon` has no `MARK`, and `packaging` is not importable as a package.

- [ ] **Step 3: Make `packaging` importable**

```bash
touch packaging/__init__.py
```

- [ ] **Step 4: Rewrite the generator**

Replace the drawing half of `packaging/make_icon.py` (keep `token()`, `main()` and the `SIZES` tuple):

```python
"""Generate the app icon from the design tokens.

Pillow is already a dependency (media/numbered.py renders the overlay PNG
with it), so this adds nothing to the bundle. The mark is a tennis ball cut
in two with the halves stepped apart -- split, and step, which is the
product's name drawn -- and the colours are read from app.css's @theme
rather than picked here, so the icon cannot drift from the chrome.

The *geometry* has the same drift problem and the same answer: these
constants mirror web/src/lib/mark.ts, and tests/test_icon.py fails if they
stop matching. Without that guard the Dock icon and the header mark become
two different logos, silently, and only on a machine that has installed the
.dmg.
"""

import math
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

REPO = Path(__file__).resolve().parent.parent
APP_CSS = REPO / "web" / "src" / "app.css"
SIZES = (16, 32, 64, 128, 256, 512, 1024)

# Mirrors web/src/lib/mark.ts. Guarded by tests/test_icon.py.
MARK = {
    "viewBox": 32.0,
    "radius": 13.0,
    "gap": 0.9,
    "step": 1.5,
    "seamWidth": 2.6,
    "seamRx": 13.8,
    "seamRy": 13.3,
    "seamTopY": 4.5,
    "seamBottomY": 27.5,
    "seamLeftX": 6.8,
    "seamRightX": 25.2,
}

SS = 8  # supersample; PIL has no antialiased primitives


def token(name: str, fallback: str) -> tuple[int, int, int]:
    """Read a --color-* hex value straight out of the theme block."""
    match = re.search(rf"--color-{name}:\s*(#[0-9a-fA-F]{{6}})", APP_CSS.read_text())
    value = match.group(1) if match else fallback
    return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5))


def render(size: int) -> Image.Image:
    ball = token("ball", "#d6e02c")
    seam_colour = token("court-line", "#f4f9ff")
    # The ground darkens at the two smallest sizes. On court blue the ball
    # measures 4.27:1, which is fine for a graphical mark at 128px and
    # marginal in a Finder list; on bg navy the same ball measures 13.42:1.
    # Same drawing, legible at both ends.
    ground = token("bg", "#080e16") if size <= 32 else token("court", "#2d6595")

    big = size * SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # macOS squircle: content fills the tile, corners at ~22.5%.
    draw.rounded_rectangle([(0, 0), (big - 1, big - 1)], radius=big * 0.225, fill=(*ground, 255))

    unit = big / MARK["viewBox"]
    centre = big / 2
    radius = MARK["radius"] * unit

    disc = Image.new("L", (big, big), 0)
    ImageDraw.Draw(disc).ellipse(
        [centre - radius, centre - radius, centre + radius, centre + radius], fill=255
    )

    layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.ellipse(
        [centre - radius, centre - radius, centre + radius, centre + radius],
        fill=(*ball, 255),
    )

    # A light from above, the way every macOS icon is lit -- clipped to the
    # disc, because an unclipped blur throws a halo onto the ground and at
    # 16px the halo is most of what you see.
    highlight = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(highlight).ellipse(
        [
            centre - radius * 0.92,
            centre - radius * 1.16,
            centre + radius * 0.92,
            centre + radius * 0.44,
        ],
        fill=(min(ball[0] + 22, 255), min(ball[1] + 20, 255), min(ball[2] + 60, 255), 150),
    )
    highlight = highlight.filter(ImageFilter.GaussianBlur(radius * 0.22))
    highlight.putalpha(
        Image.composite(highlight.getchannel("A"), Image.new("L", (big, big), 0), disc)
    )
    layer.alpha_composite(highlight)

    # The seam. Two arcs bulging toward each other: this is the shape that
    # makes a yellow circle read as a tennis ball, and nothing else does.
    seam = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    sd = ImageDraw.Draw(seam)
    width = max(2, int(MARK["seamWidth"] * unit))
    rx, ry = MARK["seamRx"] * unit, MARK["seamRy"] * unit
    for cx, start, end in (
        (MARK["seamLeftX"] * unit - rx, 300, 60),
        (MARK["seamRightX"] * unit + rx, 120, 240),
    ):
        sd.arc(
            [cx - rx, centre - ry, cx + rx, centre + ry],
            start,
            end,
            fill=(*seam_colour, 255),
            width=width,
        )
    seam.putalpha(Image.composite(seam.getchannel("A"), Image.new("L", (big, big), 0), disc))
    layer.alpha_composite(seam)

    # Split, and step.
    gap = int(MARK["gap"] * unit)
    step = int(MARK["step"] * unit)
    half = big // 2
    img.alpha_composite(layer.crop((0, 0, half, big)), (-gap, -step))
    img.alpha_composite(layer.crop((half, 0, big, big)), (half + gap, step))
    return img.resize((size, size), Image.LANCZOS)
```

Note the unused `math` import if nothing needs it — remove it rather than leaving it, since `ruff` will flag it.

- [ ] **Step 5: Run the test**

```bash
~/miniconda3/envs/splitstep/bin/pytest tests/test_icon.py -q
```

Expected: PASS.

- [ ] **Step 6: Generate the icon and look at it**

```bash
~/miniconda3/envs/splitstep/bin/python packaging/make_icon.py
open src-tauri/icons/icon.iconset
```

Confirm by eye at 16px and 32px that the ball reads as a ball, that the seams are visible at 128 and above, and that the two smallest sizes sit on navy rather than court blue.

- [ ] **Step 7: Full suite and lint**

```bash
~/miniconda3/envs/splitstep/bin/pytest -q
~/miniconda3/envs/splitstep/bin/ruff check splitstep tests packaging
```

Expected: both clean. `filterwarnings = ["error"]` means a Pillow deprecation would fail the run — if one appears, fix the call rather than silencing it.

- [ ] **Step 8: Commit**

```bash
git add packaging/make_icon.py packaging/__init__.py tests/test_icon.py src-tauri/icons
git commit -m "feat(icon): the mark, in the Dock, guarded against becoming a second logo

The ball is drawn twice -- SVG in Mark.svelte, Pillow here -- and two
drawings of one shape drift silently until they are different logos, on a
machine that has installed the dmg. make_icon.py already read its colours
out of app.css so the icon could not drift from the chrome; the geometry
now mirrors lib/mark.ts under the same kind of guard.

The ground darkens at 16 and 32. On court blue the ball measures 4.27:1,
fine at 128px and marginal in a Finder list; on bg navy the same ball is
13.42:1. One drawing, legible at both ends.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: The documentation that is now wrong

**Files:**
- Modify: `CLAUDE.md` (Design tokens section)
- Modify: `docs/SMOKE.md`
- Modify: `docs/superpowers/specs/2026-08-30-ui-hard-court-design.md` (status line)

- [ ] **Step 1: Rewrite the Design tokens section**

`CLAUDE.md`'s **Design tokens** section describes a violet-shifted ramp, four semantic tokens and an `accent` that no longer exists. Replace its colour paragraphs with:

```markdown
Colour: `bg` / `surface` / `surface-2` / `line`, text `fg` / `dim` / `faint`,
three semantic tokens — `star`, `point`, `danger` — and four court tokens:
`court`, `court-run`, `court-line`, `ball`. The base ramp is a night hard
court in navy, because the app's ground is now a doubles court rendered in
perspective (`lib/court.ts` → `CourtGround.svelte`) and the chrome has to
sit in the same world as it.

**There is no accent, deliberately.** A blue button, a blue tab and a blue
focus ring on every screen state "look here" about chrome that is never the
point — the footage is the colour. State is fill, outline and weight: a
filled button is `bg-fg` with an explicit `text-bg`, a selected control is
`border-fg`, a focus ring is `outline-fg` (14.84:1 on surface, far past the
3:1 an indicator needs). The attempt to keep an accent and shift it to cyan
died on measurement, not taste: against `point` it sat at a 1.14 luminance
ratio, two cyans a reviewer would have to tell apart in a status row.

**Reject still has no colour** for the reason it never did: detection is
recall-biased, rejecting is the most frequent action in the app, and red
would state "error" about the routine case.

**Contrast is a test, not a comment.** `web/tests/tokens.test.ts` parses the
`@theme` block and enforces 4.5:1 for the three text tokens against all
three grounds and 3:1 for the semantic ones. This exists because `faint` has
now silently fallen under the line twice — `#74747F` in the 2026-08-23 pass,
`#7186a0` in this one — both times while carrying the keyboard legend.

**The court imposes a layout rule.** Over bare `court`, `dim` measures
2.41:1 and `faint` 2.09:1. Secondary text therefore never sits on exposed
ground: it sits on a `surface` card, or `CourtGround`'s scrim brings the
ground back down beneath it. Only `fg` (5.27:1) may cross bare court.

**Two tiers.** Browse routes render the court at full strength; the session
route freezes it, drops it to 55% and raises the scrim, because atmosphere
behind footage you are judging competes with the footage.

**The mark is one drawing in two languages.** `lib/mark.ts` holds the
geometry, `Mark.svelte` renders it for the app (and is the loading state),
and `packaging/make_icon.py` mirrors both the constants and the tokens for
the `.icns`. `tests/test_icon.py` fails if the two stop agreeing.
```

- [ ] **Step 2: Add the smoke rows**

In `docs/SMOKE.md`, under **Still open**, add:

```markdown
## The redesign (2026-08-30)

- [ ] The court is visible behind Library and quiets on a session page
- [ ] Reels is reachable from a session page, not only from Library
- [ ] At full ultrawide width the cards go two-up and the review video is
      centred at 1920, not stretched
- [ ] The mark animates while a detect job is running, and stops when it lands
- [ ] Every interactive element shows a visible focus ring with the mouse
      untouched
- [ ] The Dock icon is the split ball, and is legible in a Finder list view
```

- [ ] **Step 3: Update the spec's status line**

Change the spec's header line to `Status: implemented 2026-08-30.`

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/SMOKE.md docs/superpowers/specs/2026-08-30-ui-hard-court-design.md
git commit -m "docs: the token guidance catches up with a palette that moved

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: Verify the artifact, not the source

Four bugs reached a real install during Phase 3 while every automated check passed, and they share one shape: what was checked was what had been changed, not what would run. A `.dmg` was once built from a binary older than the fix in it.

**Files:** none modified.

- [ ] **Step 1: Full suite, both languages**

```bash
~/miniconda3/envs/splitstep/bin/pytest -q
~/miniconda3/envs/splitstep/bin/ruff check splitstep tests packaging
cd web && npx vitest run && npm run check && npm run build
```

Expected: all clean, `0 ERRORS 0 WARNINGS`.

- [ ] **Step 2: Check there is disk headroom**

```bash
df -h /Users/stevenkhaw
```

Expected: comfortably more than 12.5 GB free. `bundle_dmg.sh` needs ~2.5 GB beyond the ~10 GB output and fails with an unhelpful error without it.

- [ ] **Step 3: Build**

```bash
./packaging/build_app.sh
```

Expected: ~12 minutes, ending in a `.dmg`. The script's three guards run here — migration count, overlay font, and every Rust source older than the built executable.

- [ ] **Step 4: Verify the icon inside the mounted image**

```bash
hdiutil attach -nobrowse target/release/bundle/dmg/SplitStep_0.1.0_aarch64.dmg
sips -g pixelWidth -g pixelHeight "/Volumes/SplitStep/SplitStep.app/Contents/Resources/icon.icns"
open "/Volumes/SplitStep"
```

Look at the mounted volume in icon view and in list view. The icon must be the split ball at both. If it is the old two bars, the build did not pick up `make_icon.py`'s output and nothing below is meaningful.

- [ ] **Step 5: Run the installed app**

Drag to Applications, launch, and confirm against the SMOKE rows added in Task 10: the court renders, a session quiets it, Reels is in the bar, the mark spins during a job.

```bash
hdiutil detach "/Volumes/SplitStep"
```

- [ ] **Step 6: Commit the smoke results**

Tick the rows in `docs/SMOKE.md` that actually passed. Leave the rest unticked — an unticked row is information, and a ticked one that was never run is the failure mode this whole task exists to prevent.

```bash
git add docs/SMOKE.md
git commit -m "docs: what the redesign's dmg actually proved

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage.** §3 token layer → Task 1. §3 accent deletion → Task 2. §4 `CourtGround` → Tasks 3–4. §4 `Mark` → Task 5. §4 `AppBar` and layout container → Task 6. §4 cards → Task 7. §5 motion → Tasks 4 (tokens) and 8 (confirmations). §4 icon and the drift guard → Task 9. §7 verification → Tasks 9 and 11. §8 risks: the 62-callsite risk is Task 2's guard test, the stale `CLAUDE.md` is Task 10, the build cost is Task 11, the two-drawings risk is Task 9's `test_icon.py`.

Two spec items are deliberately partial, and both are called out where they land: the spec's "route transitions and list entrances" (§5) are not given their own task, because both are one-line `motion-safe:` additions that belong to whichever task touches the markup — Task 6 for routes, Task 7 for lists. If they slip, they are cosmetic and independently addable.

**Placeholders.** None. Every code step carries the code. The one place the plan says "read the surrounding lines rather than assuming" (Task 8, Step 4) is deliberate: `QueueMode.svelte` is 702 lines and the status row's local variable names should be read, not guessed from this document.

**Type consistency.** `courtPaths`/`CourtGeometry`/`CourtOptions` are declared in Task 3 and consumed with those exact names in Task 4. `MARK` is declared in Task 5 and consumed by name in Task 9 both in TypeScript and in the Python mirror. `<Mark size state label>` matches between Task 5's component and Task 5's Step 6 usage. `<CourtGround tier>` matches between Tasks 4 and 6. `contrast()` is exported from `tokens.test.ts` in Task 1 and never imported elsewhere, which is noted in that task's Interfaces block rather than left implied.
