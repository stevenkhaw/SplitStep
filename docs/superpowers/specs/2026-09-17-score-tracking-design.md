# Score tracking, un-rejecting, and a timeline that fits — design

Date: 2026-09-17. Status: proposed.

Four asks from one review session, in the order they arrived:

1. An optional match-score tracker in the review queue, with the live score
   burned onto numbered reel renders.
2. A way to see rejected rallies again and bring one back.
3. "I don't see re-segment any more" — not a bug: the app config is in friend
   mode, which hides the tuning tools. Settings → *Advanced tools* restores
   it. No change proposed.
4. Timeline mode's video is taller than the screen, so trimming means
   scrolling between the deck and the bands.

Items 2 and 4 are bounded changes to flows that already exist. Item 1 is a
new subsystem and gets the bulk of this document.

## 1. Score tracking

### What it is

Per session, off by default. When on, a scoreboard sits beside the video in
queue mode showing the score *entering* the rally on screen. Marking a point
(`P`) asks who won it; `1` / `2` answer. The numbered reel render burns the
same scoreboard, broadcast style, bottom-left of every clip that belongs to a
tracked session.

### Decisions

**Store the winner, derive the score.** The only new per-rally fact is
`winner`. The score at any rally is a replay of the winners before it, in
`idx` order, through a pure scoring function. Nothing is cached, so a
corrected winner, an undo, a re-segment, or a split all recompute for free.
The alternative — writing the scoreline onto the rally at verdict time — goes
stale on the first mid-match correction and every rally after it.

**Two engines, one fixture.** The UI needs the score at keypress time (the
queue confirms verdicts at the keystroke, before the round trip; see
`QueueMode.apply`), so the engine has to exist in TypeScript. The overlay is
rendered by Python in the `reel` job. Rather than ship the score across the
API, both languages carry the same pure function — `web/src/lib/score.ts`
and `splitstep/score.py` — and both are asserted against one case file,
`tests/fixtures/score_cases.json`, the way `tests/test_icon.py` pins
`make_icon.py` to `lib/mark.ts`. A case is `{rules, winners, expect}`;
either test failing on a case the other passes is the drift signal.

**"Score entering this rally" is the one number.** The panel, the overlay,
and any future export all show the state *before* the point on screen,
matching a broadcast scoreboard. That makes the panel consistent when the
reviewer arrows backwards, and it means the overlay for clip *k* needs only
rallies with `idx < k`.

**Points without a winner are skipped, not guessed.** A rally with `point=1`
and `winner=''` is a gap: it contributes nothing to the replay and the panel
counts it as *unscored* so the reviewer can see the score is provisional.

**Per session, not per library.** A match is a session. The switch, the
rules and the player names live on the session row.

### Data — migration `013_score_tracking.sql`

```sql
ALTER TABLE rallies  ADD COLUMN winner  TEXT NOT NULL DEFAULT '';   -- '', 'a', 'b'
ALTER TABLE sessions ADD COLUMN scoring TEXT NOT NULL DEFAULT '';   -- '' = off, else JSON rules
```

`winner` follows `note`'s rule: absence has one representation (`''`), so
no reader handles `NULL` and `''` as two cases. `'a'` and `'b'` are
positional — player A is the first name in `rules.players` — so renaming a
player never touches a rally row.

`scoring` is the rules blob when tracking is on:

```json
{"players": ["Me", "Opp"], "sets": 3, "ad": true, "tiebreak": "at6", "tiebreakTo": 7}
```

- `sets`: best of 1, 3 or 5.
- `ad`: `true` = deuce/advantage; `false` = no-ad (sudden death at 40-40).
- `tiebreak`: `"at6"` (tiebreak at 6-6), `"none"` (advantage set), or
  `"only"` — the whole session is one tiebreak, which is what the first
  real session (a filmed tiebreaker) was. `tiebreakTo` is the tiebreak's
  target (7 or 10), win by two.

`replace_rallies` carries `winner` across a re-segment by the same >50%
overlap rule it uses for `point`, in the same read-back query. A rally that
keeps its `point` keeps its `winner`; a rally that loses `point` loses
`winner` too, so the two columns cannot disagree about whether a point was
scored.

### Engine — `lib/score.ts` and `splitstep/score.py`

```ts
type Player = 'a' | 'b'
interface ScoreRules { players: [string, string]; sets: 1|3|5; ad: boolean;
                       tiebreak: 'at6'|'none'|'only'; tiebreakTo: 7|10 }
interface ScoreState {
  sets: [number, number][]      // completed sets, e.g. [[6,4],[3,6]]
  games: [number, number]       // current set
  points: [string, string]      // '0','15','30','40','Ad' — or tiebreak counts as strings
  inTiebreak: boolean
  finished: Player | null       // who won the match, if over
}
function score(winners: Player[], rules: ScoreRules): ScoreState
```

Winners after `finished` is set are ignored: a match cannot un-finish, and
the reviewer can see the extra points as *unscored* in the panel (they are
still points; they just fall after match point). Python mirrors the shape
as a dataclass with the same field names.

### API

- `POST /api/sessions/{id}/scoring` — body is the rules object, or `null`
  to turn tracking off. Validated by a pydantic model; turning off leaves
  every `winner` in place, so turning it back on restores the score.
- `POST /api/rallies/{id}/winner` — body `{"winner": "a" | "b" | ""}`. A
  non-empty winner also sets `point = 1`; it stamps `reviewed_at` and
  `seen_at` and refreshes the session's review status, exactly as `/point`
  does. Refuses (409) if the rally's session has tracking off, so a stale
  tab cannot write winners nobody can see.
- `GET /api/sessions/{id}` gains `scoring` on the session and `winner` on
  each rally.

The `clip` and `reel` handlers do not change their signatures; the numbered
render reads what it needs through `resolve_items` (below).

### Queue UI

- A **Track score** checkbox in QueueMode's header. Ticking it opens a small
  options card (two names, sets, ad / no-ad, tiebreak mode and target) with
  a **Start** button; that saves the rules and the panel appears. Unticking
  turns tracking off after a confirm naming how many points are scored.
- The **score panel** is a `surface` card to the right of the video on wide
  layouts and beneath the status row on narrow ones. Two rows: name · sets
  won · games · points, all in `font-data`. Two buttons, one per player,
  labelled *«name» won this point*, and a line reading *n unscored points*
  when the replay skipped any. Match over shows *«name» wins* in place of
  the points column.
- **Keys.** `P` still marks a point. With tracking on it also puts the panel
  into a *Who won? 1 «A» · 2 «B» · Esc* prompt. `1` / `2` record the winner
  and advance, like a verdict. `Esc` keeps the point and records no winner.
  `1` / `2` also work on any rally without the prompt — pressing `1` on a
  rally that is not yet a point makes it one — which is how a wrong winner
  gets corrected on the way back through. `shortcuts.ts` gains both keys in
  the queue table, rendered only while tracking is on.
- **Undo.** A new `QueueAction` kind `'winner'` carries the previous
  `point` and `winner`, so `U` restores both. `persist.ts` maps it to
  `api.winner`.
- `QueueController` holds a `winners: Map<id, Player>` beside its three
  Sets, exposes `scoreBefore(rallyId)` built from the engine, and
  `liveSnapshot` writes `winner` back onto the rally the way it writes the
  three flags.

### Overlay — `media/numbered.py`

`render_overlay_png` gains an optional `scoreboard: ScoreState | None` and a
`players` pair. When present it draws a two-row board bottom-left, in the
same translucent rounded pill as the counter, `font-data`-equivalent
(the bundled Roboto Condensed Bold): name, sets, games, points. The counter
stays top-left and the note beneath it, so a reel that mixes tracked and
untracked sessions reads consistently — the board simply appears where
there is one to show.

`resolve_items` already joins each item to its rally (or none, for an
orphan). It adds `winner` and the session's `scoring` to `ResolvedItem`. The
`reel` handler, for a numbered render, groups items by session, replays
that session's rallies through `splitstep.score.score` up to each item's
rally, and passes the state to `render_overlay_png`. An orphan item, or one
in an untracked session, gets no board. The reel job's own rules do not
change — every intermediate is still encoded at the locked profile, and the
guarded concat runs over them unchanged.

### Tests

- `tests/test_score.py` and `web/tests/score.test.ts` both walk
  `tests/fixtures/score_cases.json`: deuce/ad cycles, no-ad, a 7-point and
  a 10-point tiebreak, a set won 7-5 with `tiebreak: "none"`, a tiebreak
  entered at 6-6, a full best-of-3, and play after match point.
- `web/tests/queue.test.ts`: `winner` action, undo restoring both columns,
  `scoreBefore` skipping unscored points, `liveSnapshot` carrying `winner`.
- `tests/test_rallies_point.py` (or a sibling): `replace_rallies` carries
  `winner` with `point` and drops it when `point` drops.
- `tests/test_api_review.py`: `/winner` sets `point`, refuses on an
  untracked session; `/scoring` round-trips and `null` turns off.
- `tests/test_numbered.py`: a scoreboard PNG has ink in the bottom-left
  quadrant and none there without one.
- `tests/test_handler_reel.py`: a numbered render over a tracked session
  calls the overlay with the score entering each item.

### Out of scope

Serve indicator, a starting score for a session that begins mid-match
(`tiebreak: "only"` covers the filmed-tiebreak case), score in timeline or
label modes, and score in the plain (non-numbered) render.

## 2. Seeing rejected rallies again

`QueueController` filters `rejected` rallies out at construction, so after a
reload they are gone from the queue and nothing else in the UI offers to
un-reject. The rows are intact in `rallies`; only the view hides them.

- A **Show rejected** toggle in QueueMode's header, key `H`, off by default.
  On, the controller is rebuilt from the full list, and rejected rallies
  take their place in `idx` order with a *rejected* badge in the status row
  (the row already renders one for a rally rejected during this open).
  `X` on one un-rejects it, which is what the shortcut label has always
  promised. The counts line keeps reading *n rejected* either way.
- The toggle lives in `QueueController` as a constructor option
  (`{ includeRejected }`) so the filter and its inverse are one line apart
  and `queue.test.ts` can cover both.
- `OverviewBand` draws a rejected rally at reduced opacity with a hatched
  fill, so a gap in the band reads as *rejected*, not *nothing here*.
  Timeline mode gains no reject key; the queue is where verdicts are given.
- No API change: `POST /api/rallies/{id}/reject {rejected: false}` exists.

## 3. Re-segment panel

Hidden by design in friend mode (spec 2026-08-26). The installed config is
`"mode": "friend"`. Settings → *Advanced tools — label mode and re-segment*
turns it back on. Nothing to build.

## 4. Timeline mode fits the viewport

`VideoDeck` renders `aspect-video w-full`, and the review column is up to
1920px wide, so on a laptop the deck alone is taller than the screen and
the bands sit below the fold. Trimming is a deck-and-band gesture; both
have to be visible at once.

- `TimelineMode` wraps the deck in `mx-auto w-full max-w-[calc((100vh-28rem)*16/9)]`
  — the width at which a 16:9 frame leaves room for the header, the status
  line, both bands and the threshold row. The deck stays 16:9 and centred;
  on a tall display nothing changes.
- Queue mode is left as it is: its single scrub track sits directly under
  the video and the export buttons are fine below the fold. It gets the same
  treatment if the score panel makes it tall.
- Verified in the browser at 1440×900 and 1920×1080, not by a test: jsdom
  has no layout.
