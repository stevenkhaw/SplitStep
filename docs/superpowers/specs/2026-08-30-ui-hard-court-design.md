# Hard court — ground, chrome, mark and icon

Status: implemented 2026-08-30. **Amended 2026-08-30** after the whole-branch
review found three places where this document described behaviour that did
not ship: the launcher (§2, §4 — deliberately deferred, not implemented),
per-verdict motion and the progress bar's easing (§2, §5 — only the counter
roll shipped), and the `accent`-removal risk's "canvas draws" (§8 — there is
no `<canvas>` anywhere in `web/src`). See the amendment notes inline.

The 2026-08-23 audit fixed what was *wrong* with the frontend: no tokens, a
collapsed type scale, an unreadable status row, a wall of a keyboard legend,
list pages of bare text, raw API errors. All of it shipped. What it did not do
is give the app a look — it made a correct greyscale application on a flat
`#0b0b0e` fill, and a flat fill is what it still is.

This document is the design for the second pass, which is about identity and
layout rather than defects: a ground made of an actual tennis court, chrome
with no accent colour in it at all, a mark that finally exists inside the app,
a new icon, and a layout that stops wasting two thirds of an ultrawide monitor.

## 1. What was measured first

Driven against the real library on `/Volumes/SanDisk_2TB/SplitStep`, plus a
scratch copy (`sqlite3 .backup`, `sessions/` symlinked, `seen_at` cleared) so
the queue screen could be seen live — every session in the real library is
already reviewed, and the queue only renders for an unseen rally.

| Finding | Measurement |
| --- | --- |
| Two depth planes, app-wide | `bg` and `surface`; no shadow, gradient or blur anywhere in 6099 lines of Svelte |
| No mark in the UI | The two-bar logo exists only in `packaging/make_icon.py`, drawn at build time; no component renders it |
| Motion is three things | `transition-colors` (58 uses, Tailwind's unnamed 150ms), one `animate-pulse`, one `@keyframes verdict`. Zero Svelte transitions |
| Nav hides inside status text | `33 GB  Reels  Settings`, all one weight, far right — and Reels is reachable **only** from Library |
| Ultrawide is mostly unused | At 3440px the app occupies **33%** of the width; 1144px dead on each side, and the review video capped at 1152 against a 1920×1080 proxy |

The last two are layout defects that predate any visual question, and they are
in scope because the redesign has to answer them anyway.

## 2. Decisions

**Two tiers, not one treatment.** Browse screens (Library, Reels, Setup, the
launcher, the reviewed card) carry the ground at full strength. Review screens
(queue, timeline) freeze it, drop it to 55% and scrim it hard. Atmosphere
behind footage you are judging competes with the footage: night-court video is
already violet-cast with blown floodlights, and glowing chrome around it muddies
the rally boundaries you are there to read.

**The ground is a real court.** A doubles court in perspective — alleys,
service boxes, centre line, net and posts — projected from a camera 14ft up and
18ft behind the baseline at 9° down, which is the fence mount `FirstRun`'s
filming tips already ask for. Not an abstract grid: the app is about one sport
and it can say so.

**The chrome carries no hue.** No accent on a button, a tab, a badge, a border
or a hover. State is fill, outline and weight. `accent` is **deleted as a
token**, not recoloured — 61 usages across 19 files. Colour survives in exactly
three places, all of them data rather than decoration: `star` and `point`,
which are a rally's verdict and are read at a glance hundreds of times a
session, and `danger`, which means something has actually failed.

This follows the same reasoning the audit used to leave reject colourless.
Reject is the routine case, so red would state "error" about normal work; by the
same argument, a blue button, a blue tab underline and a blue focus ring on
every screen state "look here" about chrome that is never the point. The
footage is the colour.

**The mark is the loader.** One SVG — a tennis ball cut in two, halves stepped
apart, seams inside each half. It sits in the app bar at 19px, large on the
launcher, and its animated form replaces every bare `Loading…` in the app. The
split and the step are the product's name drawn literally, and they are also
the only two states an indeterminate spinner needs.

> **Amendment note (2026-08-30).** "Large on the launcher" did not ship, and
> is deliberately deferred rather than missing by accident. `Library.open()`
> refuses to run without a `library.db`, so the launcher is rendered by Tauri
> from its own bundle before any Python process exists — it is not on the
> browse court tier this document describes, and does not import `Mark` or
> `CourtGround`. It was left alone because a working `.dmg` built from this
> branch was already in the user's hands for testing before this review; touching
> the launcher would have silently invalidated that build. Its boot state is
> still the bare string `busy ? 'Starting…' : …` in `web/src/launcher/Launcher.svelte`.
> Everything else in this bullet — the app bar mark, the loading replacement
> across the routed app — shipped as written.

**The icon becomes that mark.** The ball on court blue, ad-hoc signed and
regenerated through `make_icon.py` as today.

**Motion goes as deep as confirmations.** Chrome gets the full treatment. In
the review loop only the confirmations move — star, point and reject each get
their own sub-200ms motion, the counter rolls, the progress bar eases. Rally
advance stays instant. Nothing may sit between a key and its response.

> **Amendment note (2026-08-30).** Only the counter roll shipped as written.
> The verdict flash (`QueueMode.svelte`) is one shared
> `motion-safe:animate-[verdict_700ms_ease-out_forwards]` for all three
> verdicts — star, point and reject differ only in `FLASH_TONE`'s colour
> class, not in motion, so "each get their own sub-200ms motion" did not
> ship. The progress bar is a direct `progressBar.style.transform =
> \`scaleX(${fraction})\`` write, not an eased transition — see §5's
> amendment note for why that is arguably the correct choice rather than a
> gap to close.

## 3. Token layer

`web/src/app.css`'s `@theme` block is rewritten. It remains the only place a
colour or a type size is chosen; a component reaching for `bg-neutral-800` or
`text-[11px]` has still escaped the system.

```
--color-bg          #080e16     --color-fg     #e6eef8
--color-surface     #101b28     --color-dim    #8fa4bd
--color-surface-2   #16273a     --color-faint  #8598b0
--color-line        #1e3247

--color-star   #f0a94c    --color-point  #6fd0e8    --color-danger #f0666b

--color-court       #2d6595    the court surface
--color-court-run   #1c5c4f    the run-off apron
--color-court-line  #f4f9ff    court markings, and the ball's seam
--color-ball        #d6e02c    optic yellow; the mark and the icon
```

`--color-accent` is removed. The type scale, the five roles, the two font
stacks and `font-data`'s `tabular-nums` are unchanged.

### Contrast, measured

Against all three grounds, worst case shown:

| Token | bg | surface | surface-2 | Requirement |
| --- | --- | --- | --- | --- |
| `fg` | 16.55 | 14.84 | 12.96 | 4.5 ✓ |
| `dim` | 7.57 | 6.79 | 5.93 | 4.5 ✓ |
| `faint` | 6.56 | 5.88 | 5.14 | 4.5 ✓ |
| `star` | 9.67 | 8.67 | 7.57 | 3.0 ✓ |
| `point` | 10.94 | 9.81 | 8.56 | 3.0 ✓ |
| `danger` | 6.29 | 5.64 | 4.92 | 3.0 ✓ |

`fg` as a focus ring on `surface` is 14.84 — far past the 3.0 a focus indicator
needs, which is what makes dropping the blue ring safe.

Two results are load-bearing and both cost a redesign of the first attempt:

**`faint` had to move.** At `#7186a0` it measured 4.06 on `surface-2` — under
4.5, on the token that carries the keyboard legend, which is functional text
and not decoration. `#8598b0` restores the margin. This is the second time this
exact token has failed this exact way; the 2026-08-23 pass moved it from
`#74747F` to `#82828E` for the same reason.

**A cyan accent was rejected on measurement.** The first palette shifted accent
to `#4cc4e8`, which against `point` `#6fd0e8` is a luminance ratio of **1.14** —
two cyans, one meaning "interactive" and one meaning "this rally was a point".
That is what killed the accent rather than taste alone; deleting the token
resolves it outright.

### The rule the court imposes

Over bare `court` blue, `dim` measures **2.41** and `faint` **2.09**. Both fail
badly. Therefore:

> Secondary text never sits on exposed court. It sits on a `surface` card, or
> the ground is scrimmed back to at least `surface` darkness beneath it.

Only `fg` may cross bare court (5.27), and only for headings. This is a
constraint on layout, not a preference — a card that grows past its scrim, or a
future screen that puts a caption over the ground, breaks accessibility rather
than merely looking worse.

## 4. Components

### `AppBar.svelte` (new)

Rendered by `App.svelte` above the router outlet, so it exists on all five
routes. Full-bleed, `bg` at 72% with a backdrop blur, one `line` bottom border.

- Left: `Mark` at 19px, then the wordmark.
- Then `Sessions` / `Reels` as tabs. Active is `surface-2` fill plus a `line`
  border and `fg` text; inactive is `dim` text on nothing.
- Right: the drive chip (bytes the library occupies, not free space on the
  drive) and Settings.

Detail routes put the breadcrumb in the page heading (`Sessions › 2026-08-18`),
replacing the current bare `← library`. Reels stops being reachable from one
screen only.

### `CourtGround.svelte` (new)

Fixed to the viewport behind everything (`position: fixed; inset: 0; z-index: -1`)
so it does not scroll away under a long timeline.

Geometry comes from `lib/court.ts` — a pure function taking viewport size and
returning SVG path strings, following this codebase's rule that logic lives in
`lib/` and is tested there. It runs once per resize, not per frame.

`preserveAspectRatio="xMidYMax slice"`, and the run-off apron is generated wide
enough (±150ft) that no viewport ratio reveals a void beside the court. A first
attempt painted a full-bleed run-off rectangle instead and turned the sky green;
the apron must be part of the projection, below the horizon, or it is not a
court.

A `tier` prop, derived from the route: `browse` renders at full strength;
`review` freezes any motion, drops opacity to 55% and raises the scrim.

### `Mark.svelte` (new)

One SVG, `size` prop. Two clipped halves of a circle, each carrying both seam
arcs, offset by `gap` horizontally and `step` vertically. Geometry constants
live in `lib/mark.ts`.

States: `still` (default), and `loading`, where the halves ease apart and back
on a slow loop. `loading` replaces every bare `Loading…` string in the app, and
is what the launcher shows during the sidecar's 3–20s boot.

> **Amendment note (2026-08-30).** The launcher half of that last sentence did
> not ship — see the amendment note on §2's "The mark is the loader" for why,
> and why it stays that way for now. Everywhere else in the routed app,
> `loading` did replace the bare string as written.

**Guard against drift.** The mark is drawn twice — SVG in Svelte for the app,
PIL in `make_icon.py` for the `.icns` — and two drawings of one shape drift.
`make_icon.py` already reads its colours out of `app.css` by regex so the icon
cannot drift from the chrome; the same trick extends to geometry. A test parses
the constants out of `lib/mark.ts` and asserts `make_icon.py`'s match. Without
it, the Dock icon and the header mark quietly become two different logos.

### Layout container

`App.svelte:16`'s `mx-auto max-w-6xl p-6` is the ultrawide defect. Replaced by:

- The bar is full-bleed.
- Content column: `max-w-[min(2600px,92vw)]`, so 1440 and 1920 monitors gain a
  little and 3440 gains a lot.
- Browse lists become a two-column grid at ≥1800px. A 152px thumbnail alone on
  a 3400px row is the "funky" — the card has to fill the space or stop claiming
  it.
- **Review is capped at 1920px and centred**, because `proxy.mp4` is 1920×1080
  and every pixel past that is upscaling. The remaining width shows the quieted
  court and nothing else: no rail, no panel, no new state. Queue review is a
  keyboard loop with the eyes on one rectangle, and a list beside it is
  something to look at that is not the rally.

### Cards

Thumbnail 152×85 (280×158 in the two-up grid), title at the title role, counts
as `star`/`point` glyphs, status as an outlined pill — `fg` border for the live
one, `line` for the rest. Elevation is a 1px inset top highlight and a soft
shadow, the app's first use of either.

## 5. Motion

Duration and easing become tokens; today every transition is Tailwind's
unnamed default.

- **Chrome:** route transitions, list entrances, the mark's loader loop, eased
  hovers.
- **Review loop:** star, point and reject each get a distinct sub-200ms motion;
  the rally counter rolls; the progress bar eases. Rally advance stays instant.
- `motion-safe:` throughout, as `@keyframes verdict` already does — under
  `prefers-reduced-motion` every state still appears and clears, it simply does
  not travel.

> **Amendment note (2026-08-30).** Of this list, only the mark's loader loop
> and the rally counter roll shipped as distinct, named-token motion. Route
> transitions and list entrances did not ship at all — there is still no
> `svelte/transition` or `svelte/animate` import anywhere in `web/src`, the
> same "zero Svelte transitions" §1 measured before this redesign. Most
> hovers (54 call sites) are still bare `motion-safe:transition-colors`,
> Tailwind's unnamed default the opening paragraph above says this section
> replaces; only a handful of animations (the mark, the counter roll,
> `CourtGround`'s tier fade) actually reference `--transition-duration-*` /
> `--ease-out-soft`. Star, point and reject share one `verdict_700ms`
> keyframe, distinguished only by `FLASH_TONE`'s colour — not three
> distinct motions. The progress bar writes `style.transform` directly
> rather than transitioning it, which is arguably the *right* call rather
> than a gap: it is redrawn on every video `timeupdate` (≈4-60 times a
> second depending on the source), so a CSS transition racing a value that
> changes faster than the transition itself would either be invisible or
> introduce lag behind the actual playhead. Tightening "the progress bar
> eases" to name that reasoning is on the list; widening the motion pass
> to the rest of this bullet list is not, and is out of scope for this
> fix wave.

**Compositor only.** This app runs beside 4K proxy decode and a Python worker
doing YOLO and ffmpeg on the same laptop. Anything continuous animates
`transform` and `opacity` and nothing else. The court ground never animates on
the review tier at all.

## 6. What does not change

The router, every module under `lib/`, the API surface, every keybinding, the
queue state machine and its undo stack, the label corpus and its writers, the
`{#key}` remount rules, and the route-effect staleness guards.

Accessibility is a hard floor, not a goal: `svelte-check` reports
`275 FILES 0 ERRORS 0 WARNINGS` today (verified 2026-08-30) and must still
report zero of both. The queue progress bar stays a real `role="slider"` with
its pointer-event implementation.

Reject stays colourless. The video letterbox stays literal `bg-black` — `bg` is
not black and shows as a seam around the frame.

## 7. Verification

Automated: `pytest -q`, `ruff check`, `npx vitest run`, `npm run check`,
`npm run build`. The two tests that assert on token class names
(`timeline-drag-gain`, `zoomband-handle-hit-target`) get retargeted, as four did
in the last migration.

New tests: `lib/court.ts` (projection is symmetric about the centre line, the
far baseline is above the near one, the apron encloses the court at every
aspect ratio tested), and the mark-geometry drift guard described above.

By hand, because jsdom has no `<video>`: the queue at 1440 and at 3440, the
review tier's court actually quieting, the split-ball mark appearing on a
page's own data load rather than for a running detect job (that indicator
lives in the jobs badge, a different component the mark does not drive and
is not driven by), and the focus ring being visible on every interactive
element with the mouse untouched.

**And the artifact, not the source.** The icon is not real until
`./packaging/build_app.sh` produces a `.dmg`, it is mounted, and the icon inside
it is the new one — the failure mode this repo has already shipped four times is
verifying what changed rather than what runs.

## 8. Risks

- **`accent` removal is 61 edits across 19 files**, four of which are canvas
  draws reading the variable at runtime (ZoomBand's playhead, ScoreCurve's line,
  QuadCanvas's handles, OverviewBand). Those become `fg`, which is also more
  legible over the score curve than periwinkle was. A missed callsite fails at
  runtime as an unstyled element, not at build.

  > **Amendment note (2026-08-30).** This anticipated risk described the wrong
  > mechanism. There is no `<canvas>` element anywhere in `web/src`, and no
  > `getComputedStyle`/`getPropertyValue` call that would read a CSS custom
  > property at runtime. ZoomBand's playhead, ScoreCurve's line, QuadCanvas's
  > handles and OverviewBand are all plain SVG/DOM elements styled with
  > ordinary Tailwind classes (`text-fg`, `border-fg`, `bg-fg`), compiled at
  > build time exactly like every other colour callsite in this list. They
  > did become `fg`, and the "more legible over the score curve than
  > periwinkle" observation still holds — only the "canvas" and "at runtime"
  > framing was wrong, which also means the "fails at runtime as an unstyled
  > element" risk applies to all 61 callsites equally, not especially to
  > these four.
- **`CLAUDE.md`'s "Design tokens" section is wrong the moment this lands** and
  is rewritten in the same change, including the reject-has-no-colour rationale,
  which survives, and the accent paragraph, which does not.
- **The icon costs a ~12 minute, ~10 GB build** to verify, and `bundle_dmg.sh`
  needs ~2.5 GB of headroom beyond the output.
- **The ball is drawn twice**, in two languages. The drift guard is the whole
  mitigation; if it is skipped, this is the bug that ships.

## 9. Explicitly not in this change

Detector tuning. Gate 0 is unresolved and environmental, and none of this
touches it — a court-blue background does not stop the audio detector measuring
the neighbouring court.
