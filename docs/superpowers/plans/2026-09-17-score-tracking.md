# Score Tracking, Show Rejected, Timeline Fit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An optional per-session tennis scoreboard driven from the review queue and burned onto numbered reels; a way to see and un-reject rejected rallies; a timeline mode that fits one screen.

**Architecture:** Only `winner` is stored per rally; the score at any rally is a replay of earlier winners through one pure scoring function that exists twice (`splitstep/score.py`, `web/src/lib/score.ts`) and is pinned to one shared case file. The queue's `QueueController` gains a winners map and a `'winner'` action; the `reel` job replays each item's session to burn the score entering that clip. Show-rejected is a constructor option on `QueueController` plus a remount; timeline fit is one wrapper class.

**Tech Stack:** Python 3.12 (FastAPI, sqlite3, PIL), Svelte 5 runes, TypeScript, vitest 2 + jsdom, pytest.

**Spec:** `docs/superpowers/specs/2026-09-17-score-tracking-design.md`

## Global Constraints

- Python interpreter is `~/miniconda3/envs/splitstep/bin/python` / `pytest` / `ruff` (conda env, not on PATH). ruff line-length 100. `pytest` runs with `filterwarnings = ["error"]`.
- Frontend commands run from `web/`: `npx vitest run <file>`, `npm run check`, `npm run build`.
- Migrations are numbered `.sql` files in `splitstep/db/migrations/`; never edit an applied one. New file is `013_score_tracking.sql`.
- Absence has one representation: `winner` is `''`, never NULL; `sessions.scoring` is `''` when off.
- Design tokens only: no raw palette steps (`bg-neutral-800`) or arbitrary type sizes. Numbers and timecodes use `font-data`. Reject has no colour (`text-faint`).
- Put logic in `web/src/lib/`, not in `.svelte`; components are thin shells.
- Comments explain *why*. Match the codebase's rationale-comment density.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- **Deviation from the spec, decided during planning:** winner keys are `A` / `B` (player A = first name in `rules.players`, player B = second), not `1` / `2` — queue mode already binds `` ` ``, `1`, `2`, `3` to playback speed. Task 12 corrects the spec.
- Score is replayed over the **whole session's** rallies in `idx` order, not the source-scoped list `QueueMode` receives (`Session.svelte` scopes `detail` to the selected video tab). `QueueMode` therefore takes a separate `sessionRallies` prop.

---

### Task 1: Python scoring engine and the shared case file

**Files:**
- Create: `splitstep/score.py`
- Create: `tests/fixtures/score_cases.json`
- Create: `tests/test_score.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class ScoreRules:
      players: tuple[str, str]
      sets: int            # 1 | 3 | 5
      ad: bool
      tiebreak: str        # "at6" | "none" | "only"
      tiebreak_to: int     # 7 | 10
  @dataclass(frozen=True)
  class ScoreState:
      sets: tuple[tuple[int, int], ...]
      games: tuple[int, int]
      points: tuple[str, str]
      in_tiebreak: bool
      finished: str | None   # "a" | "b" | None
  DEFAULT_RULES: ScoreRules
  def rules_from_dict(d: dict) -> ScoreRules
  def rules_to_dict(r: ScoreRules) -> dict          # camelCase keys, what the JSON column holds
  def score(winners: Sequence[str], rules: ScoreRules) -> ScoreState
  def score_before(rallies: Sequence[Mapping], rally_id: str, rules: ScoreRules) -> tuple[ScoreState, int]
  def scoreboard_rows(state: ScoreState, rules: ScoreRules) -> list[list[str]]
  ```
  `rallies` rows need `id`, `idx`, `rejected`, `point`, `winner`. `score_before` returns the state entering `rally_id` and the count of unscored points (point=1, winner='') before it.

- [ ] **Step 1: Write the case file**

The JSON column and the fixture use camelCase (`tiebreakTo`) because the TypeScript side reads the same object verbatim; Python converts at the boundary. `winners` is a string of `a`/`b` characters; both tests split it.

```json
{
  "defaultRules": {"players": ["Me", "Opp"], "sets": 3, "ad": true, "tiebreak": "at6", "tiebreakTo": 7},
  "cases": [
    {"name": "empty", "winners": "",
     "expect": {"sets": [], "games": [0, 0], "points": ["0", "0"], "inTiebreak": false, "finished": null}},
    {"name": "love game", "winners": "aaaa",
     "expect": {"sets": [], "games": [1, 0], "points": ["0", "0"], "inTiebreak": false, "finished": null}},
    {"name": "40-15", "winners": "aaab",
     "expect": {"sets": [], "games": [0, 0], "points": ["40", "15"], "inTiebreak": false, "finished": null}},
    {"name": "deuce", "winners": "aaabbb",
     "expect": {"sets": [], "games": [0, 0], "points": ["40", "40"], "inTiebreak": false, "finished": null}},
    {"name": "ad in", "winners": "aaabbba",
     "expect": {"sets": [], "games": [0, 0], "points": ["Ad", "40"], "inTiebreak": false, "finished": null}},
    {"name": "back to deuce", "winners": "aaabbbab",
     "expect": {"sets": [], "games": [0, 0], "points": ["40", "40"], "inTiebreak": false, "finished": null}},
    {"name": "ad out", "winners": "aaabbbabb",
     "expect": {"sets": [], "games": [0, 0], "points": ["40", "Ad"], "inTiebreak": false, "finished": null}},
    {"name": "game from ad out", "winners": "aaabbbabbb",
     "expect": {"sets": [], "games": [0, 1], "points": ["0", "0"], "inTiebreak": false, "finished": null}},
    {"name": "no-ad deuce is sudden death", "rules": {"ad": false}, "winners": "aaabbbb",
     "expect": {"sets": [], "games": [0, 1], "points": ["0", "0"], "inTiebreak": false, "finished": null}},
    {"name": "no-ad 40-40 still reads 40-40", "rules": {"ad": false}, "winners": "aaabbb",
     "expect": {"sets": [], "games": [0, 0], "points": ["40", "40"], "inTiebreak": false, "finished": null}},
    {"name": "set 6-4", "winners": "aaaabbbbaaaabbbbaaaabbbbaaaabbbbaaaaaaaa",
     "expect": {"sets": [[6, 4]], "games": [0, 0], "points": ["0", "0"], "inTiebreak": false, "finished": null}},
    {"name": "set 7-5", "winners": "aaaabbbbaaaabbbbaaaabbbbaaaabbbbaaaabbbbaaaaaaaa",
     "expect": {"sets": [[7, 5]], "games": [0, 0], "points": ["0", "0"], "inTiebreak": false, "finished": null}},
    {"name": "6-6 enters a tiebreak", "winners": "aaaabbbbaaaabbbbaaaabbbbaaaabbbbaaaabbbbaaaabbbb",
     "expect": {"sets": [], "games": [6, 6], "points": ["0", "0"], "inTiebreak": true, "finished": null}},
    {"name": "tiebreak 7-0 wins the set 7-6", "winners": "aaaabbbbaaaabbbbaaaabbbbaaaabbbbaaaabbbbaaaabbbbaaaaaaa",
     "expect": {"sets": [[7, 6]], "games": [0, 0], "points": ["0", "0"], "inTiebreak": false, "finished": null}},
    {"name": "tiebreak 6-6 is not over", "winners": "aaaabbbbaaaabbbbaaaabbbbaaaabbbbaaaabbbbaaaabbbbaaaaaabbbbbb",
     "expect": {"sets": [], "games": [6, 6], "points": ["6", "6"], "inTiebreak": true, "finished": null}},
    {"name": "tiebreak needs two clear", "winners": "aaaabbbbaaaabbbbaaaabbbbaaaabbbbaaaabbbbaaaabbbbaaaaaabbbbbbaa",
     "expect": {"sets": [[7, 6]], "games": [0, 0], "points": ["0", "0"], "inTiebreak": false, "finished": null}},
    {"name": "advantage set plays on at 6-6", "rules": {"tiebreak": "none"},
     "winners": "aaaabbbbaaaabbbbaaaabbbbaaaabbbbaaaabbbbaaaabbbbaaaaaaaa",
     "expect": {"sets": [[8, 6]], "games": [0, 0], "points": ["0", "0"], "inTiebreak": false, "finished": null}},
    {"name": "best of 3 ends at two sets", "winners": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
     "expect": {"sets": [[6, 0], [6, 0]], "games": [0, 0], "points": ["0", "0"], "inTiebreak": false, "finished": "a"}},
    {"name": "points after match point are ignored", "winners": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaabbb",
     "expect": {"sets": [[6, 0], [6, 0]], "games": [0, 0], "points": ["0", "0"], "inTiebreak": false, "finished": "a"}},
    {"name": "best of 1", "rules": {"sets": 1}, "winners": "aaaaaaaaaaaaaaaaaaaaaaaa",
     "expect": {"sets": [[6, 0]], "games": [0, 0], "points": ["0", "0"], "inTiebreak": false, "finished": "a"}},
    {"name": "tiebreak-only to 7", "rules": {"tiebreak": "only"}, "winners": "aaaaaaa",
     "expect": {"sets": [], "games": [0, 0], "points": ["7", "0"], "inTiebreak": true, "finished": "a"}},
    {"name": "tiebreak-only 6-6 is not over", "rules": {"tiebreak": "only"}, "winners": "aaaaaabbbbbb",
     "expect": {"sets": [], "games": [0, 0], "points": ["6", "6"], "inTiebreak": true, "finished": null}},
    {"name": "tiebreak-only to 10", "rules": {"tiebreak": "only", "tiebreakTo": 10}, "winners": "aaaaaaaaab",
     "expect": {"sets": [], "games": [0, 0], "points": ["9", "1"], "inTiebreak": true, "finished": null}},
    {"name": "tiebreak-only to 10 finishes", "rules": {"tiebreak": "only", "tiebreakTo": 10}, "winners": "aaaaaaaaaba",
     "expect": {"sets": [], "games": [0, 0], "points": ["10", "1"], "inTiebreak": true, "finished": "a"}}
  ]
}
```

Notes on the hand-checks: "set 6-4" is four love games each, alternating (4-4), then two more for a. "set 7-5" is five each then two. "6-6" is six each. The 40-char and 48-char strings must be counted, not eyeballed — `python -c "print(len('...'))"` before committing.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_score.py
import json
from pathlib import Path

import pytest

from splitstep.score import (
    DEFAULT_RULES,
    ScoreRules,
    rules_from_dict,
    rules_to_dict,
    score,
    score_before,
    scoreboard_rows,
)

CASES = json.loads((Path(__file__).parent / "fixtures" / "score_cases.json").read_text())


def _rules(case: dict) -> ScoreRules:
    return rules_from_dict({**CASES["defaultRules"], **case.get("rules", {})})


@pytest.mark.parametrize("case", CASES["cases"], ids=[c["name"] for c in CASES["cases"]])
def test_engine_matches_the_shared_cases(case):
    # One case file, two engines (see web/tests/score.test.ts). A case that
    # passes here and fails there is the drift the shared file exists to catch.
    state = score(list(case["winners"]), _rules(case))
    e = case["expect"]
    assert [list(s) for s in state.sets] == e["sets"]
    assert list(state.games) == e["games"]
    assert list(state.points) == e["points"]
    assert state.in_tiebreak == e["inTiebreak"]
    assert state.finished == e["finished"]


def test_rules_round_trip_through_the_json_shape():
    d = {"players": ["Ann", "Bob"], "sets": 5, "ad": False, "tiebreak": "only", "tiebreakTo": 10}
    assert rules_to_dict(rules_from_dict(d)) == d


def test_rules_from_dict_rejects_bad_values():
    with pytest.raises(ValueError):
        rules_from_dict({**rules_to_dict(DEFAULT_RULES), "sets": 4})
    with pytest.raises(ValueError):
        rules_from_dict({**rules_to_dict(DEFAULT_RULES), "tiebreak": "sometimes"})
    with pytest.raises(ValueError):
        rules_from_dict({**rules_to_dict(DEFAULT_RULES), "tiebreakTo": 5})
    with pytest.raises(ValueError):
        rules_from_dict({**rules_to_dict(DEFAULT_RULES), "players": ["", "Bob"]})


def _rally(idx, *, point=1, winner="", rejected=0):
    return {"id": f"r{idx}", "idx": idx, "point": point, "winner": winner, "rejected": rejected}


def test_score_before_replays_only_earlier_scored_points_in_idx_order():
    rallies = [
        _rally(3, winner="b"),          # out of order on purpose
        _rally(1, winner="a"),
        _rally(2, winner="a"),
        _rally(4, winner="a"),          # the rally asked about: excluded
        _rally(5, winner="b"),          # after it: excluded
    ]
    state, unscored = score_before(rallies, "r4", DEFAULT_RULES)
    assert state.points == ("40", "15")
    assert unscored == 0


def test_score_before_skips_unscored_and_rejected_points_but_counts_the_unscored():
    rallies = [
        _rally(1, winner="a"),
        _rally(2, winner=""),                    # a point nobody scored
        _rally(3, winner="b", rejected=1),       # rejected: not a rally at all
        _rally(4, point=0, winner=""),           # not a point
        _rally(5),
    ]
    state, unscored = score_before(rallies, "r5", DEFAULT_RULES)
    assert state.points == ("15", "0")
    assert unscored == 1


def test_score_before_unknown_rally_is_the_full_replay():
    # An orphaned reel item has no rally; the caller passes an id no row
    # holds and gets the state after every scored point -- never a crash.
    rallies = [_rally(1, winner="a"), _rally(2, winner="a")]
    state, _ = score_before(rallies, "nope", DEFAULT_RULES)
    assert state.points == ("30", "0")


def test_scoreboard_rows_name_sets_games_points():
    state = score(list("aaaabbbbaaaabbbbaaaabbbbaaaabbbbaaaaaaaa" + "aaaab"), DEFAULT_RULES)
    rows = scoreboard_rows(state, DEFAULT_RULES)
    assert rows == [["Me", "6", "0", "40"], ["Opp", "4", "0", "15"]]


def test_scoreboard_rows_tiebreak_only_has_no_games_column():
    rules = rules_from_dict({**rules_to_dict(DEFAULT_RULES), "tiebreak": "only"})
    rows = scoreboard_rows(score(list("aab"), rules), rules)
    assert rows == [["Me", "2"], ["Opp", "1"]]


def test_scoreboard_rows_marks_the_winner_when_finished():
    state = score(list("a" * 48), DEFAULT_RULES)
    rows = scoreboard_rows(state, DEFAULT_RULES)
    assert rows == [["Me", "6", "6", "W"], ["Opp", "0", "0", ""]]
```

- [ ] **Step 3: Run to verify failure**

Run: `~/miniconda3/envs/splitstep/bin/pytest tests/test_score.py -q`
Expected: `ModuleNotFoundError: No module named 'splitstep.score'`

- [ ] **Step 4: Implement `splitstep/score.py`**

```python
"""Tennis scoring as a pure function of who won each point.

Only the winner of each point is stored (rallies.winner); everything a
scoreboard shows is replayed from that. Nothing is cached, so a corrected
winner, an undo, a re-segment or a split recomputes for free. The same
function exists in web/src/lib/score.ts for the queue, and both are pinned
to tests/fixtures/score_cases.json -- a case that passes on one side and
fails on the other is the drift that file exists to catch. Change the
rules here and there together, and add a case.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

_POINT_LABELS = ("0", "15", "30", "40")


@dataclass(frozen=True)
class ScoreRules:
    players: tuple[str, str]
    sets: int
    ad: bool
    tiebreak: str
    tiebreak_to: int


@dataclass(frozen=True)
class ScoreState:
    sets: tuple[tuple[int, int], ...]
    games: tuple[int, int]
    points: tuple[str, str]
    in_tiebreak: bool
    finished: str | None


DEFAULT_RULES = ScoreRules(("Me", "Opp"), 3, True, "at6", 7)


def rules_from_dict(d: Mapping) -> ScoreRules:
    """The JSON column's shape -> rules, validated. Raises ValueError rather
    than returning a default: a session whose rules do not parse must not
    silently score as best-of-three."""
    players = tuple(str(p).strip() for p in d.get("players", ()))
    if len(players) != 2 or not all(players):
        raise ValueError("players must be two non-empty names")
    sets = d.get("sets")
    if sets not in (1, 3, 5):
        raise ValueError("sets must be 1, 3 or 5")
    ad = d.get("ad")
    if not isinstance(ad, bool):
        raise ValueError("ad must be true or false")
    tiebreak = d.get("tiebreak")
    if tiebreak not in ("at6", "none", "only"):
        raise ValueError("tiebreak must be at6, none or only")
    tiebreak_to = d.get("tiebreakTo")
    if tiebreak_to not in (7, 10):
        raise ValueError("tiebreakTo must be 7 or 10")
    return ScoreRules(players, sets, ad, tiebreak, tiebreak_to)


def rules_to_dict(r: ScoreRules) -> dict:
    return {
        "players": list(r.players),
        "sets": r.sets,
        "ad": r.ad,
        "tiebreak": r.tiebreak,
        "tiebreakTo": r.tiebreak_to,
    }


def _point_labels(pts: list[int], in_tiebreak: bool, ad: bool) -> tuple[str, str]:
    if in_tiebreak:
        return (str(pts[0]), str(pts[1]))
    a, b = pts
    if ad and a >= 3 and b >= 3:
        if a == b:
            return ("40", "40")
        return ("Ad", "40") if a > b else ("40", "Ad")
    return (_POINT_LABELS[min(a, 3)], _POINT_LABELS[min(b, 3)])


def score(winners: Sequence[str], rules: ScoreRules) -> ScoreState:
    """Replay `winners` ('a' / 'b' per point) into a scoreboard state.

    Points after the match is decided are ignored rather than rejected: a
    reviewer who keeps scoring warm-down rallies has made no error the
    scoreboard needs to shout about, and the queue shows them as unscored.
    """
    sets_needed = rules.sets // 2 + 1
    sets: list[tuple[int, int]] = []
    games = [0, 0]
    pts = [0, 0]
    in_tb = rules.tiebreak == "only"
    finished: str | None = None

    for w in winners:
        if finished is not None:
            break
        if w not in ("a", "b"):
            raise ValueError(f"winner must be 'a' or 'b', not {w!r}")
        i = 0 if w == "a" else 1
        j = 1 - i
        pts[i] += 1

        if in_tb:
            if pts[i] >= rules.tiebreak_to and pts[i] - pts[j] >= 2:
                if rules.tiebreak == "only":
                    # The whole session is one tiebreak: the count stays on
                    # the board as the final score, nothing rolls into games.
                    finished = w
                else:
                    games[i] += 1
                    sets.append((games[0], games[1]))
                    games = [0, 0]
                    pts = [0, 0]
                    in_tb = False
                    if sum(1 for s in sets if s[i] > s[j]) >= sets_needed:
                        finished = w
            continue

        # A normal game. With advantage scoring you need four points and two
        # clear; without it the seventh point of a game (4 for the winner)
        # decides it at deuce.
        won_game = pts[i] >= 4 and (not rules.ad or pts[i] - pts[j] >= 2)
        if not won_game:
            continue
        games[i] += 1
        pts = [0, 0]
        if rules.tiebreak == "at6" and games == [6, 6]:
            in_tb = True
        elif games[i] >= 6 and games[i] - games[j] >= 2:
            sets.append((games[0], games[1]))
            games = [0, 0]
            if sum(1 for s in sets if s[i] > s[j]) >= sets_needed:
                finished = w

    return ScoreState(
        sets=tuple(sets),
        games=(games[0], games[1]),
        points=_point_labels(pts, in_tb, rules.ad),
        in_tiebreak=in_tb,
        finished=finished,
    )


def score_before(
    rallies: Sequence[Mapping], rally_id: str, rules: ScoreRules
) -> tuple[ScoreState, int]:
    """The state entering `rally_id`, and how many earlier points carry no
    winner. Replays every non-rejected point with idx below the target's,
    in idx order regardless of the order `rallies` arrived in. An id no row
    holds (an orphaned reel item) replays everything -- the board on such a
    clip is the best information there is, and never a crash."""
    target = next((r for r in rallies if r["id"] == rally_id), None)
    limit = target["idx"] if target is not None else None
    earlier = sorted(
        (r for r in rallies
         if (limit is None or r["idx"] < limit) and not r["rejected"] and r["point"]),
        key=lambda r: r["idx"],
    )
    winners = [r["winner"] for r in earlier if r["winner"]]
    unscored = sum(1 for r in earlier if not r["winner"])
    return score(winners, rules), unscored


def scoreboard_rows(state: ScoreState, rules: ScoreRules) -> list[list[str]]:
    """Two rows for a broadcast-style board: name, one column per completed
    set, current games, points. A tiebreak-only session has no games column
    -- the tiebreak count is the whole score. A finished match replaces the
    games and points columns with a single W for the winner, blank for the
    other; a finished tiebreak-only session keeps its final count instead."""
    rows = []
    for i, name in enumerate(rules.players):
        row = [name, *(str(s[i]) for s in state.sets)]
        if state.finished is not None and rules.tiebreak != "only":
            # Games and points are both 0-0 after the deciding set: showing
            # them would read as a match still in play. Sets, then W.
            row.append("W" if state.finished == ("a", "b")[i] else "")
        else:
            if rules.tiebreak != "only":
                row.append(str(state.games[i]))
            row.append(state.points[i])
        rows.append(row)
    return rows
```

- [ ] **Step 5: Run to verify pass**

Run: `~/miniconda3/envs/splitstep/bin/pytest tests/test_score.py -q && ~/miniconda3/envs/splitstep/bin/ruff check splitstep/score.py tests/test_score.py`
Expected: all pass, ruff clean. If a fixture case fails, re-count the winners string before touching the engine — the strings were hand-built.

- [ ] **Step 6: Commit**

```bash
git add splitstep/score.py tests/test_score.py tests/fixtures/score_cases.json
git commit -m "feat(score): pure tennis scoring engine pinned to a shared case file

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: TypeScript scoring engine against the same cases

**Files:**
- Create: `web/src/lib/score.ts`
- Create: `web/tests/score.test.ts`
- Modify: `web/src/lib/types.ts` (add `winner` to `Rally`, `scoring` to `Session`)

**Interfaces:**
- Consumes: `tests/fixtures/score_cases.json` from Task 1.
- Produces:
  ```ts
  export type Player = 'a' | 'b'
  export interface ScoreRules { players: [string, string]; sets: 1 | 3 | 5; ad: boolean;
                                tiebreak: 'at6' | 'none' | 'only'; tiebreakTo: 7 | 10 }
  export interface ScoreState { sets: [number, number][]; games: [number, number];
                                points: [string, string]; inTiebreak: boolean; finished: Player | null }
  export const DEFAULT_RULES: ScoreRules
  export function score(winners: Player[], rules: ScoreRules): ScoreState
  export function scoreBefore(rallies: Rally[], rallyId: string, rules: ScoreRules): { state: ScoreState; unscored: number }
  export function scoreboardRows(state: ScoreState, rules: ScoreRules): string[][]
  export function playerName(rules: ScoreRules, p: Player): string
  ```
- `Rally.winner: '' | 'a' | 'b'`; `Session.scoring: ScoreRules | null` (the server parses the column, Task 4).

- [ ] **Step 1: Add the types**

In `web/src/lib/types.ts`, after `note: string` in `Rally`:

```ts
  /** '' when nobody has said who won this point -- one representation of
   *  absence, like `note`. Positional: 'a' is the first name in the
   *  session's `scoring.players`, so renaming a player touches no rally. */
  winner: '' | 'a' | 'b'
```

In `Session`, after `status: string`:

```ts
  /** Match-score tracking rules, or null when the session does not track.
   *  Parsed server-side from the sessions.scoring column (see
   *  splitstep/db/sessions.py::scoring_rules). */
  scoring: ScoreRules | null
```

Add `import type { ScoreRules } from './score'` at the top of `types.ts`. (`score.ts` imports `Rally` from `types.ts` as a type-only import; TypeScript allows the cycle for types.)

Every test fixture that builds a `Rally` literal now needs `winner: ''`. Run `npm run check` after this step to find them: `web/tests/queue.test.ts` (`rally()` helper), and any other helper `npm run check` names. Add `winner: ''` to each; add `scoring: null` to any `Session` literal it names.

- [ ] **Step 2: Write the failing test**

```ts
// web/tests/score.test.ts
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { DEFAULT_RULES, score, scoreBefore, scoreboardRows } from '../src/lib/score'
import type { Player, ScoreRules } from '../src/lib/score'
import type { Rally } from '../src/lib/types'

// The same file tests/test_score.py reads -- see the module comment in
// score.ts for why one case file feeds two engines. Read with join(), not
// `new URL(..., import.meta.url)`; tokens.test.ts explains the vite quirk.
const CASES = JSON.parse(
  readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), '../../tests/fixtures/score_cases.json'),
    'utf8',
  ),
) as {
  defaultRules: ScoreRules
  cases: { name: string; rules?: Partial<ScoreRules>; winners: string; expect: unknown }[]
}

describe('score', () => {
  for (const c of CASES.cases) {
    it(c.name, () => {
      const rules = { ...CASES.defaultRules, ...(c.rules ?? {}) }
      expect(score(c.winners.split('') as Player[], rules)).toEqual(c.expect)
    })
  }
})

function rally(idx: number, over: Partial<Rally> = {}): Rally {
  return {
    id: `r${idx}`, session_id: 's', source_id: 'src', idx,
    start_ms: idx * 10000, end_ms: idx * 10000 + 8000,
    det_start_ms: null, det_end_ms: null, confidence: 0.5,
    starred: 0, rejected: 0, point: 1, reviewed_at: null, seen_at: null, note: '',
    winner: '', ...over,
  }
}

describe('scoreBefore', () => {
  it('replays only earlier scored points, in idx order', () => {
    const rallies = [
      rally(3, { winner: 'b' }),
      rally(1, { winner: 'a' }),
      rally(2, { winner: 'a' }),
      rally(4, { winner: 'a' }),
      rally(5, { winner: 'b' }),
    ]
    const { state, unscored } = scoreBefore(rallies, 'r4', DEFAULT_RULES)
    expect(state.points).toEqual(['40', '15'])
    expect(unscored).toBe(0)
  })

  it('skips unscored and rejected points but counts the unscored', () => {
    const rallies = [
      rally(1, { winner: 'a' }),
      rally(2),
      rally(3, { winner: 'b', rejected: 1 }),
      rally(4, { point: 0 }),
      rally(5),
    ]
    const { state, unscored } = scoreBefore(rallies, 'r5', DEFAULT_RULES)
    expect(state.points).toEqual(['15', '0'])
    expect(unscored).toBe(1)
  })

  it('replays everything for an id no rally holds', () => {
    const { state } = scoreBefore([rally(1, { winner: 'a' }), rally(2, { winner: 'a' })], 'nope', DEFAULT_RULES)
    expect(state.points).toEqual(['30', '0'])
  })
})

describe('scoreboardRows', () => {
  it('is name, sets, games, points', () => {
    const w = ('aaaabbbb'.repeat(4) + 'aaaaaaaa' + 'aaaab').split('') as Player[]
    expect(scoreboardRows(score(w, DEFAULT_RULES), DEFAULT_RULES)).toEqual([
      ['Me', '6', '0', '40'],
      ['Opp', '4', '0', '15'],
    ])
  })

  it('drops the games column for a tiebreak-only session', () => {
    const rules: ScoreRules = { ...DEFAULT_RULES, tiebreak: 'only' }
    expect(scoreboardRows(score(['a', 'a', 'b'], rules), rules)).toEqual([['Me', '2'], ['Opp', '1']])
  })

  it('marks the winner when finished', () => {
    const w = 'a'.repeat(48).split('') as Player[]
    expect(scoreboardRows(score(w, DEFAULT_RULES), DEFAULT_RULES)).toEqual([
      ['Me', '6', '6', 'W'],
      ['Opp', '0', '0', ''],
    ])
  })
})
```

- [ ] **Step 3: Run to verify failure**

Run: `cd web && npx vitest run tests/score.test.ts`
Expected: fails to resolve `../src/lib/score`.

- [ ] **Step 4: Implement `web/src/lib/score.ts`**

```ts
import type { Rally } from './types'

/**
 * Tennis scoring as a pure function of who won each point.
 *
 * Only the winner is stored per rally; the scoreboard is replayed from
 * that, so a corrected winner, an undo, a re-segment or a split recomputes
 * for free. This is the second copy of splitstep/score.py -- the queue
 * needs the score at keypress time, the reel job needs it in Python --
 * and both are pinned to tests/fixtures/score_cases.json. Change the rules
 * in both places together, and add a case.
 */
export type Player = 'a' | 'b'

export interface ScoreRules {
  players: [string, string]
  sets: 1 | 3 | 5
  ad: boolean
  tiebreak: 'at6' | 'none' | 'only'
  tiebreakTo: 7 | 10
}

export interface ScoreState {
  sets: [number, number][]
  games: [number, number]
  points: [string, string]
  inTiebreak: boolean
  finished: Player | null
}

export const DEFAULT_RULES: ScoreRules = {
  players: ['Me', 'Opp'],
  sets: 3,
  ad: true,
  tiebreak: 'at6',
  tiebreakTo: 7,
}

const POINT_LABELS = ['0', '15', '30', '40']

function pointLabels(pts: [number, number], inTiebreak: boolean, ad: boolean): [string, string] {
  if (inTiebreak) return [String(pts[0]), String(pts[1])]
  const [a, b] = pts
  if (ad && a >= 3 && b >= 3) {
    if (a === b) return ['40', '40']
    return a > b ? ['Ad', '40'] : ['40', 'Ad']
  }
  return [POINT_LABELS[Math.min(a, 3)], POINT_LABELS[Math.min(b, 3)]]
}

export function score(winners: Player[], rules: ScoreRules): ScoreState {
  const setsNeeded = Math.floor(rules.sets / 2) + 1
  const sets: [number, number][] = []
  let games: [number, number] = [0, 0]
  let pts: [number, number] = [0, 0]
  let inTb = rules.tiebreak === 'only'
  let finished: Player | null = null
  const setsWonBy = (i: 0 | 1) => sets.filter((s) => s[i] > s[1 - i]).length

  for (const w of winners) {
    // Points after match point are ignored, not rejected: the queue shows
    // them as unscored, which is honest and needs no error state.
    if (finished) break
    const i: 0 | 1 = w === 'a' ? 0 : 1
    const j: 0 | 1 = i === 0 ? 1 : 0
    pts[i] += 1

    if (inTb) {
      if (pts[i] >= rules.tiebreakTo && pts[i] - pts[j] >= 2) {
        if (rules.tiebreak === 'only') {
          // One tiebreak is the whole session: the count is the final score.
          finished = w
        } else {
          games[i] += 1
          sets.push([games[0], games[1]])
          games = [0, 0]
          pts = [0, 0]
          inTb = false
          if (setsWonBy(i) >= setsNeeded) finished = w
        }
      }
      continue
    }

    // Four points and two clear with advantage scoring; without it the
    // seventh point of a game decides it at deuce.
    const wonGame = pts[i] >= 4 && (!rules.ad || pts[i] - pts[j] >= 2)
    if (!wonGame) continue
    games[i] += 1
    pts = [0, 0]
    if (rules.tiebreak === 'at6' && games[0] === 6 && games[1] === 6) {
      inTb = true
    } else if (games[i] >= 6 && games[i] - games[j] >= 2) {
      sets.push([games[0], games[1]])
      games = [0, 0]
      if (setsWonBy(i) >= setsNeeded) finished = w
    }
  }

  return { sets, games, points: pointLabels(pts, inTb, rules.ad), inTiebreak: inTb, finished }
}

/**
 * The state entering `rallyId`, and how many earlier points carry no
 * winner. Replays every non-rejected point with a lower idx, in idx order
 * whatever order `rallies` arrived in -- the caller hands in the whole
 * session, not the source-scoped list the queue shows. An id no rally
 * holds replays everything.
 */
export function scoreBefore(
  rallies: Rally[],
  rallyId: string,
  rules: ScoreRules,
): { state: ScoreState; unscored: number } {
  const target = rallies.find((r) => r.id === rallyId)
  const earlier = rallies
    .filter((r) => (target ? r.idx < target.idx : true) && !r.rejected && r.point)
    .sort((x, y) => x.idx - y.idx)
  const winners = earlier.map((r) => r.winner).filter((w): w is Player => w !== '')
  const unscored = earlier.filter((r) => r.winner === '').length
  return { state: score(winners, rules), unscored }
}

export function playerName(rules: ScoreRules, p: Player): string {
  return rules.players[p === 'a' ? 0 : 1]
}

/** Two rows for a broadcast-style board: name, one column per completed
 *  set, current games, points. Tiebreak-only sessions have no games
 *  column; a finished match shows W in place of games and points. */
export function scoreboardRows(state: ScoreState, rules: ScoreRules): string[][] {
  return rules.players.map((name, i) => {
    const row = [name, ...state.sets.map((s) => String(s[i]))]
    if (state.finished !== null && rules.tiebreak !== 'only') {
      // Games and points are 0-0 after the deciding set; sets, then W.
      row.push(state.finished === (i === 0 ? 'a' : 'b') ? 'W' : '')
    } else {
      if (rules.tiebreak !== 'only') row.push(String(state.games[i]))
      row.push(state.points[i])
    }
    return row
  })
}
```

- [ ] **Step 5: Run to verify pass**

Run: `cd web && npx vitest run tests/score.test.ts && npm run check`
Expected: every case passes; `npm run check` clean (fix any `Rally` literals missing `winner`).

- [ ] **Step 6: Commit**

```bash
git add web/src/lib/score.ts web/tests/score.test.ts web/src/lib/types.ts web/tests
git commit -m "feat(score): TypeScript scoring engine, pinned to the same case file

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Migration 013, `set_winner`, and the re-segment / split carry

**Files:**
- Create: `splitstep/db/migrations/013_score_tracking.sql`
- Modify: `splitstep/db/rallies.py` (`replace_rallies` read-back + insert; `split_rally` insert; new `set_winner`)
- Modify: `splitstep/db/sessions.py` (new `scoring_rules`, `set_scoring`)
- Test: `tests/test_rallies_winner.py`

**Interfaces:**
- Produces:
  ```python
  # splitstep/db/rallies.py
  def set_winner(conn, rally_id: str, winner: str) -> None   # '' | 'a' | 'b'; non-empty also sets point=1
  # splitstep/db/sessions.py
  def scoring_rules(row: sqlite3.Row | Mapping) -> dict | None   # parsed column or None
  def set_scoring(conn, session_id: str, rules: dict | None) -> None
  ```

- [ ] **Step 1: Write the migration**

```sql
-- Match-score tracking. Two columns, and only two: the winner of each
-- point, and the session's rules. Everything a scoreboard shows is replayed
-- from winners in idx order by splitstep/score.py (and its TypeScript
-- twin), so nothing about the score itself is ever stored -- a corrected
-- winner or a re-segment recomputes for free instead of going stale.
--
-- winner: '' / 'a' / 'b'. Empty string, not NULL, following `note`: absence
-- has one representation. Positional rather than a name so renaming a
-- player touches no rally row.
ALTER TABLE rallies ADD COLUMN winner TEXT NOT NULL DEFAULT '';

-- scoring: '' when the session does not track a score, otherwise the rules
-- as JSON ({"players": [..], "sets": 3, "ad": true, "tiebreak": "at6",
-- "tiebreakTo": 7}). Per session, not per library: a match is a session.
ALTER TABLE sessions ADD COLUMN scoring TEXT NOT NULL DEFAULT '';
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_rallies_winner.py
import pytest

from splitstep.db.rallies import (
    list_rallies,
    replace_rallies,
    set_point,
    set_winner,
    split_rally,
)
from splitstep.db.sessions import (
    add_source,
    find_or_create_session_for_date,
    get_session,
    scoring_rules,
    set_scoring,
)
from splitstep.detect.segment import Interval

RULES = {"players": ["Me", "Opp"], "sets": 3, "ad": True, "tiebreak": "at6", "tiebreakTo": 7}


@pytest.fixture
def seeded(conn):
    session_id = find_or_create_session_for_date(conn, "2026-09-17")
    source_id, _idx = add_source(
        conn, session_id, recorded_at="2026-09-17T10:00:00Z", duration_ms=600_000,
        width=3840, height=2160, fps=30.0, original_name="IMG_1.MOV",
    )
    replace_rallies(conn, session_id, source_id,
                    [Interval(1000, 5000, 0.8), Interval(9000, 14000, 0.7)])
    return {"session_id": session_id, "source_id": source_id}


def _rallies(conn, session_id):
    return list_rallies(conn, session_id)


def test_winner_defaults_to_empty(conn, seeded):
    assert _rallies(conn, seeded["session_id"])[0]["winner"] == ""


def test_set_winner_also_marks_the_point_and_stamps_review(conn, seeded):
    rid = _rallies(conn, seeded["session_id"])[0]["id"]
    set_winner(conn, rid, "a")
    row = _rallies(conn, seeded["session_id"])[0]
    assert row["winner"] == "a"
    assert row["point"] == 1
    assert row["reviewed_at"] is not None
    assert row["seen_at"] is not None


def test_clearing_the_winner_leaves_the_point(conn, seeded):
    rid = _rallies(conn, seeded["session_id"])[0]["id"]
    set_winner(conn, rid, "a")
    set_winner(conn, rid, "")
    row = _rallies(conn, seeded["session_id"])[0]
    assert row["winner"] == ""
    assert row["point"] == 1


def test_unmarking_the_point_clears_the_winner(conn, seeded):
    # The two columns must not disagree about whether a point was scored.
    rid = _rallies(conn, seeded["session_id"])[0]["id"]
    set_winner(conn, rid, "b")
    set_point(conn, rid, False)
    row = _rallies(conn, seeded["session_id"])[0]
    assert row["point"] == 0
    assert row["winner"] == ""


def test_set_winner_rejects_anything_but_a_b_or_empty(conn, seeded):
    rid = _rallies(conn, seeded["session_id"])[0]["id"]
    with pytest.raises(ValueError):
        set_winner(conn, rid, "me")


def test_replace_rallies_carries_the_winner_with_the_point(conn, seeded):
    rid = _rallies(conn, seeded["session_id"])[0]["id"]
    set_winner(conn, rid, "a")
    # Shifted by 300ms: well over the 50% overlap the flags carry across on.
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(1300, 5300, 0.8), Interval(9000, 14000, 0.7)])
    rows = _rallies(conn, seeded["session_id"])
    assert rows[0]["point"] == 1
    assert rows[0]["winner"] == "a"
    assert rows[1]["winner"] == ""


def test_replace_rallies_drops_the_winner_when_no_new_rally_overlaps(conn, seeded):
    rid = _rallies(conn, seeded["session_id"])[0]["id"]
    set_winner(conn, rid, "a")
    replace_rallies(conn, seeded["session_id"], seeded["source_id"],
                    [Interval(20000, 25000, 0.8)])
    rows = _rallies(conn, seeded["session_id"])
    assert rows[0]["point"] == 0
    assert rows[0]["winner"] == ""


def test_split_inherits_the_winner_on_both_halves(conn, seeded):
    rid = _rallies(conn, seeded["session_id"])[0]["id"]
    set_winner(conn, rid, "b")
    split_rally(conn, rid, 3000)
    rows = _rallies(conn, seeded["session_id"])
    assert [r["winner"] for r in rows[:2]] == ["b", "b"]


def test_session_scoring_round_trips_and_clears(conn, seeded):
    sid = seeded["session_id"]
    assert scoring_rules(get_session(conn, sid)) is None
    set_scoring(conn, sid, RULES)
    assert scoring_rules(get_session(conn, sid)) == RULES
    set_scoring(conn, sid, None)
    assert scoring_rules(get_session(conn, sid)) is None
    assert get_session(conn, sid)["scoring"] == ""


def test_set_scoring_validates(conn, seeded):
    with pytest.raises(ValueError):
        set_scoring(conn, seeded["session_id"], {**RULES, "sets": 2})
```

- [ ] **Step 3: Run to verify failure**

Run: `~/miniconda3/envs/splitstep/bin/pytest tests/test_rallies_winner.py -q`
Expected: ImportError on `set_winner` / `scoring_rules`.

- [ ] **Step 4: Implement**

`splitstep/db/rallies.py`. Replace the read-back SELECT in `replace_rallies` and the insert:

```python
        old = conn.execute(
            "SELECT start_ms, end_ms, starred, rejected, point, winner, clip_path, note"
            " FROM rallies"
            " WHERE source_id = ? AND (starred = 1 OR rejected = 1 OR point = 1"
            " OR clip_path IS NOT NULL OR note != '')",
            (source_id,),
        ).fetchall()
```

After `point = _overlaps_any(iv, old, "point")` add:

```python
            # The winner rides with the point, by the same overlap rule, and
            # only with it: a new rally that loses `point` loses `winner`
            # too, so the two columns can never disagree about whether a
            # point was scored. Best-overlap like the note, not first-match
            # like the flags -- two old points can both clear 50% of one
            # merged span and the answer must not depend on row order.
            winner = _carried_winner(iv, old) if point else ""
```

Change the INSERT to include `winner`:

```python
            conn.execute(
                "INSERT INTO rallies (id,session_id,source_id,idx,start_ms,end_ms,"
                "det_start_ms,det_end_ms,confidence,starred,rejected,point,winner,"
                "clip_path,note)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, session_id, source_id, -placeholder_idx, iv.start_ms,
                 iv.end_ms, iv.start_ms, iv.end_ms, iv.confidence,
                 int(starred), int(rejected), int(point), winner, clip_path, note),
            )
```

Add `_carried_winner` next to `_carried_note`, sharing its ranking:

```python
def _carried_winner(iv: Interval, rows: list[sqlite3.Row]) -> str:
    """The winner for `iv` from the best-overlapping old *point*, else ''.
    Same two-stage ranking as _carried_note; restricted to rows that were
    points because a winner on a non-point row cannot exist (set_point
    clears it) and should not be invented here."""
    best, best_ms = "", -1
    for r in rows:
        if not r["point"] or not r["winner"]:
            continue
        if overlap_fraction(iv.start_ms, iv.end_ms, r["start_ms"], r["end_ms"]) < STAR_OVERLAP_MIN:
            continue
        ms = min(iv.end_ms, r["end_ms"]) - max(iv.start_ms, r["start_ms"])
        if ms > best_ms:
            best, best_ms = r["winner"], ms
    return best
```

(Read `_carried_note` first and mirror its exact overlap-ms computation if it differs from the line above.)

In `split_rally`'s INSERT add `winner` beside `point` in both the column list and the values tuple (`row["winner"]`).

Update `set_point` to clear the winner when unmarking:

```python
    conn.execute(
        "UPDATE rallies SET point = ?, winner = CASE WHEN ? THEN winner ELSE '' END,"
        " reviewed_at = COALESCE(reviewed_at, ?), seen_at = COALESCE(seen_at, ?) WHERE id = ?",
        (int(point), int(point), now, now, rally_id),
    )
```

and add to its docstring: "Unmarking clears `winner`: a winner on a non-point is a contradiction the scoreboard would have to guess about."

Add `set_winner` after `set_point`:

```python
def set_winner(conn: sqlite3.Connection, rally_id: str, winner: str) -> None:
    """Record who won this point ('a' / 'b'), or '' to say nobody has said.

    A non-empty winner also marks the rally a point: pressing A on a rally
    is a ruling that a point was played and who took it, and making the
    reviewer press P first would be two keys for one judgement. Clearing
    the winner leaves `point` alone -- "a point was played" still stands,
    only "who won" is withdrawn. Stamps reviewed_at/seen_at like set_point:
    this is a ruling on the clip.
    """
    if winner not in ("", "a", "b"):
        raise ValueError(f"winner must be '', 'a' or 'b', not {winner!r}")
    now = _now()
    conn.execute(
        "UPDATE rallies SET winner = ?, point = CASE WHEN ? != '' THEN 1 ELSE point END,"
        " reviewed_at = COALESCE(reviewed_at, ?), seen_at = COALESCE(seen_at, ?) WHERE id = ?",
        (winner, winner, now, now, rally_id),
    )
    conn.commit()
```

`splitstep/db/sessions.py` — add at the top `import json` and `from splitstep.score import rules_from_dict, rules_to_dict`, then:

```python
def scoring_rules(row) -> dict | None:
    """The session's match-score rules, or None when it does not track one.
    Parsed here, once, so every reader (the API, the reel job) sees a dict
    or None and never the raw column."""
    raw = row["scoring"]
    if not raw:
        return None
    return rules_to_dict(rules_from_dict(json.loads(raw)))


def set_scoring(conn: sqlite3.Connection, session_id: str, rules: dict | None) -> None:
    """Turn tracking on with `rules`, or off with None. Off leaves every
    rally's winner in place: turning it back on restores the score."""
    value = "" if rules is None else json.dumps(rules_to_dict(rules_from_dict(rules)))
    conn.execute("UPDATE sessions SET scoring = ? WHERE id = ?", (value, session_id))
    conn.commit()
```

- [ ] **Step 5: Run the new tests and the whole suite**

Run: `~/miniconda3/envs/splitstep/bin/pytest tests/test_rallies_winner.py tests/test_rallies_point.py tests/test_split.py tests/test_db.py -q`
Expected: pass. Then `~/miniconda3/envs/splitstep/bin/pytest -q` — expect all green (test_db walks every migration).
Then `~/miniconda3/envs/splitstep/bin/ruff check splitstep tests`.

- [ ] **Step 6: Commit**

```bash
git add splitstep/db/migrations/013_score_tracking.sql splitstep/db/rallies.py splitstep/db/sessions.py tests/test_rallies_winner.py
git commit -m "feat(db): rallies.winner and sessions.scoring, carried across re-segment and split

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: API — `/scoring`, `/winner`, and parsed session rules

**Files:**
- Modify: `splitstep/api/routes.py` (bodies; `api_get_session`; `api_list_sessions`; two new routes)
- Test: `tests/test_api_score.py`

**Interfaces:**
- Consumes: `set_winner`, `set_scoring`, `scoring_rules` (Task 3).
- Produces:
  - `POST /api/sessions/{id}/scoring` body `{"rules": {...} | null}` → `{"scoring": {...} | null}`
  - `POST /api/rallies/{id}/winner` body `{"winner": "a"|"b"|""}` → `{"ok": true, "session_status": ...}`; 409 when the session does not track.
  - `GET /api/sessions/{id}` → `session.scoring` is a dict or null; every rally has `winner`.
  - `GET /api/sessions` → each row has `scoring` parsed the same way.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_api_score.py
import pytest
from fastapi.testclient import TestClient

from splitstep.api.app import create_app
from splitstep.db.rallies import list_rallies, replace_rallies
from splitstep.db.schema import connect, migrate
from splitstep.db.sessions import add_source, find_or_create_session_for_date, set_session_status
from splitstep.detect.segment import Interval

RULES = {"players": ["Me", "Opp"], "sets": 3, "ad": True, "tiebreak": "at6", "tiebreakTo": 7}


@pytest.fixture
def conn(library):
    c = connect(library.db_path)
    migrate(c)
    yield c
    c.close()


@pytest.fixture
def client(library):
    with TestClient(create_app(library)) as c:
        yield c


@pytest.fixture
def seeded(conn):
    session_id = find_or_create_session_for_date(conn, "2026-09-17")
    source_id, _ = add_source(
        conn, session_id, recorded_at="2026-09-17T10:00:00Z", duration_ms=60_000,
        width=1920, height=1080, fps=30.0, original_name="A.MOV",
    )
    replace_rallies(conn, session_id, source_id,
                    [Interval(1000, 5000, 0.8), Interval(9000, 14000, 0.7)])
    set_session_status(conn, session_id, "ready")
    return {"session_id": session_id, "source_id": source_id}


def test_session_detail_reports_null_scoring_and_empty_winners(client, seeded):
    body = client.get(f"/api/sessions/{seeded['session_id']}").json()
    assert body["session"]["scoring"] is None
    assert [r["winner"] for r in body["rallies"]] == ["", ""]


def test_scoring_round_trips_and_shows_in_both_session_routes(client, seeded):
    sid = seeded["session_id"]
    r = client.post(f"/api/sessions/{sid}/scoring", json={"rules": RULES})
    assert r.status_code == 200
    assert r.json()["scoring"] == RULES
    assert client.get(f"/api/sessions/{sid}").json()["session"]["scoring"] == RULES
    listed = [s for s in client.get("/api/sessions").json() if s["id"] == sid]
    assert listed[0]["scoring"] == RULES

    r = client.post(f"/api/sessions/{sid}/scoring", json={"rules": None})
    assert r.json()["scoring"] is None


def test_bad_rules_are_a_422(client, seeded):
    r = client.post(f"/api/sessions/{seeded['session_id']}/scoring",
                    json={"rules": {**RULES, "tiebreak": "maybe"}})
    assert r.status_code == 422


def test_scoring_404s_on_an_unknown_session(client, seeded):
    assert client.post("/api/sessions/nope/scoring", json={"rules": RULES}).status_code == 404


def test_winner_refuses_while_the_session_does_not_track(client, conn, seeded):
    rid = list_rallies(conn, seeded["session_id"])[0]["id"]
    r = client.post(f"/api/rallies/{rid}/winner", json={"winner": "a"})
    assert r.status_code == 409


def test_winner_sets_the_point_and_refreshes_status(client, conn, seeded):
    sid = seeded["session_id"]
    client.post(f"/api/sessions/{sid}/scoring", json={"rules": RULES})
    rows = list_rallies(conn, sid)
    r = client.post(f"/api/rallies/{rows[0]['id']}/winner", json={"winner": "a"})
    assert r.status_code == 200
    assert "session_status" in r.json()
    row = list_rallies(conn, sid)[0]
    assert row["winner"] == "a"
    assert row["point"] == 1


def test_winner_rejects_a_bad_value(client, conn, seeded):
    sid = seeded["session_id"]
    client.post(f"/api/sessions/{sid}/scoring", json={"rules": RULES})
    rid = list_rallies(conn, sid)[0]["id"]
    assert client.post(f"/api/rallies/{rid}/winner", json={"winner": "me"}).status_code == 422


def test_unmarking_the_point_over_the_api_clears_the_winner(client, conn, seeded):
    sid = seeded["session_id"]
    client.post(f"/api/sessions/{sid}/scoring", json={"rules": RULES})
    rid = list_rallies(conn, sid)[0]["id"]
    client.post(f"/api/rallies/{rid}/winner", json={"winner": "b"})
    client.post(f"/api/rallies/{rid}/point", json={"point": False})
    assert list_rallies(conn, sid)[0]["winner"] == ""
```

- [ ] **Step 2: Run to verify failure**

Run: `~/miniconda3/envs/splitstep/bin/pytest tests/test_api_score.py -q`
Expected: 404s / KeyErrors on `scoring`.

- [ ] **Step 3: Implement**

In `splitstep/api/routes.py`:

Imports — add `set_winner` to the `splitstep.db.rallies` import list and `scoring_rules, set_scoring` to the `splitstep.db.sessions` list. Add `from typing import Literal`.

Bodies, next to `PointBody`:

```python
class WinnerBody(BaseModel):
    winner: Literal["", "a", "b"]


class ScoringBody(BaseModel):
    """The rules object, or null to stop tracking. Validation is
    splitstep.score.rules_from_dict's, surfaced as a 422 -- one validator
    for the CLI, the column and the route."""

    rules: dict | None

    @field_validator("rules")
    @classmethod
    def check_rules(cls, v: dict | None) -> dict | None:
        if v is None:
            return None
        from splitstep.score import rules_from_dict, rules_to_dict
        try:
            return rules_to_dict(rules_from_dict(v))
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
```

(Move the import to module top if `splitstep.score` has no import cycle with routes — it does not; put `from splitstep.score import rules_from_dict, rules_to_dict` with the other imports and drop the inline one.)

A helper to serialise a session row:

```python
def _session_json(row) -> dict:
    d = dict(row)
    d["scoring"] = scoring_rules(row)
    return d
```

In `api_list_sessions`, wherever the row is turned into the response dict (`dict(s)` or a literal built from `s`), use `_session_json(s)` as the base. In `api_get_session`, `"session": _session_json(session)`.

Routes, after `api_point`:

```python
@router.post("/api/rallies/{rally_id}/winner")
def api_winner(rally_id: str, body: WinnerBody, request: Request):
    """Who won this point. Refused while the session does not track a
    score: a stale tab must not write winners nobody can see. A non-empty
    winner also marks the point (see set_winner)."""
    conn = _conn(request)
    session_id = _session_id_for_rally(conn, rally_id)
    if scoring_rules(get_session(conn, session_id)) is None:
        raise HTTPException(status_code=409, detail="This session is not tracking a score.")
    set_winner(conn, rally_id, body.winner)
    return {"ok": True, "session_status": refresh_session_review_status(conn, session_id)}
```

After `api_get_session`:

```python
@router.post("/api/sessions/{session_id}/scoring")
def api_set_scoring(session_id: str, body: ScoringBody, request: Request):
    conn = _conn(request)
    if get_session(conn, session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    set_scoring(conn, session_id, body.rules)
    return {"scoring": scoring_rules(get_session(conn, session_id))}
```

Check `_session_id_for_rally` raises 404 for an unknown rally (read it); if it returns None instead, guard before `get_session`.

- [ ] **Step 4: Run to verify pass**

Run: `~/miniconda3/envs/splitstep/bin/pytest tests/test_api_score.py tests/test_api.py tests/test_api_review.py -q && ~/miniconda3/envs/splitstep/bin/ruff check splitstep tests`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add splitstep/api/routes.py tests/test_api_score.py
git commit -m "feat(api): session scoring rules and per-rally winner routes

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Scoreboard in the numbered overlay and the reel job

**Files:**
- Modify: `splitstep/media/numbered.py` (`render_overlay_png` gains `scoreboard`)
- Modify: `splitstep/jobs/handlers.py` (numbered branch replays each item's session)
- Test: `tests/test_numbered.py`, `tests/test_handler_reel.py`

**Interfaces:**
- Consumes: `score_before`, `scoreboard_rows`, `rules_from_dict` (Task 1); `scoring_rules` (Task 3); `list_rallies`.
- Produces: `render_overlay_png(dst, *, counter, note, font, scoreboard: list[list[str]] | None = None)`.

- [ ] **Step 1: Write the failing overlay tests** (append to `tests/test_numbered.py`)

```python
def _ink_in_region(png: Path, box: tuple[int, int, int, int]) -> int:
    with Image.open(png) as img:
        return sum(img.crop(box).getchannel("A").histogram()[1:])


BOTTOM_LEFT = (0, 1080, 1920, 2160)


def test_scoreboard_draws_bottom_left_and_nothing_there_without_one(tmp_path):
    plain = tmp_path / "plain.png"
    board = tmp_path / "board.png"
    render_overlay_png(plain, counter="3/20", note="", font=overlay_font())
    render_overlay_png(
        board, counter="3/20", note="", font=overlay_font(),
        scoreboard=[["Me", "6", "3", "30"], ["Opp", "4", "2", "15"]],
    )
    assert _ink_in_region(plain, BOTTOM_LEFT) == 0
    assert _ink_in_region(board, BOTTOM_LEFT) > 0
    # The counter is untouched by the board: same ink top-left either way.
    top_left = (0, 0, 1920, 1080)
    assert _ink_in_region(plain, top_left) == _ink_in_region(board, top_left)


def test_scoreboard_with_uneven_row_lengths_still_renders(tmp_path):
    # A finished match has a "W" and a "" in the last column; the grid must
    # size columns from the longest cell and not choke on an empty one.
    dst = tmp_path / "w.png"
    render_overlay_png(
        dst, counter="1/1", note="", font=overlay_font(),
        scoreboard=[["Me", "6", "6", "W"], ["Opponent", "0", "0", ""]],
    )
    assert _ink_in_region(dst, BOTTOM_LEFT) > 0
```

- [ ] **Step 2: Run to verify failure**

Run: `~/miniconda3/envs/splitstep/bin/pytest tests/test_numbered.py -q`
Expected: `TypeError: ... unexpected keyword argument 'scoreboard'`.

- [ ] **Step 3: Implement the board in `numbered.py`**

Add constants after `_NOTE_SIZE`:

```python
_BOARD_SIZE = 84
_BOARD_COL_GAP = 48
_BOARD_ROW_GAP = 16
```

Add the drawing helper after `_draw_line`:

```python
def _draw_scoreboard(
    draw: ImageDraw.ImageDraw, rows: list[list[str]], font: ImageFont.FreeTypeFont
) -> None:
    """A broadcast-style board, bottom-left: one pill, two rows, columns
    sized to their widest cell so the numbers line up under each other.
    Bottom-left because the counter and note own the top-left, and a reel
    that mixes tracked and untracked sessions must keep the counter in one
    place while the board comes and goes."""
    ncols = max(len(r) for r in rows)
    cells = [r + [""] * (ncols - len(r)) for r in rows]

    def width(text: str) -> int:
        if not text:
            return 0
        left, _, right, _ = draw.textbbox((0, 0), text, font=font)
        return right - left

    col_w = [max(width(row[c]) for row in cells) for c in range(ncols)]
    # Row height from the font's own ascent/descent so a name with a
    # descender does not collide with the row beneath it.
    ascent, descent = font.getmetrics()
    row_h = ascent + descent
    board_w = sum(col_w) + _BOARD_COL_GAP * (ncols - 1)
    board_h = row_h * len(cells) + _BOARD_ROW_GAP * (len(cells) - 1)
    x0 = _MARGIN
    y0 = _FRAME_HEIGHT - _MARGIN - board_h
    draw.rounded_rectangle(
        (x0 - _BOX_PAD, y0 - _BOX_PAD, x0 + board_w + _BOX_PAD, y0 + board_h + _BOX_PAD),
        radius=_BOX_PAD,
        fill=_BOX_FILL,
    )
    for r, row in enumerate(cells):
        y = y0 + r * (row_h + _BOARD_ROW_GAP)
        x = x0
        for c, text in enumerate(row):
            # Names left-aligned, numbers right-aligned within their column,
            # which is how every scoreboard on television reads.
            if c == 0:
                draw.text((x, y), text, font=font, fill=_TEXT_FILL)
            else:
                draw.text((x + col_w[c] - width(text), y), text, font=font, fill=_TEXT_FILL)
            x += col_w[c] + _BOARD_COL_GAP
```

Change the signature and body of `render_overlay_png`:

```python
def render_overlay_png(
    dst: Path,
    *,
    counter: str,
    note: str,
    font: str,
    scoreboard: list[list[str]] | None = None,
) -> None:
```

and before `canvas.save(dst)`:

```python
    if scoreboard:
        # The score *entering* this clip, like a live broadcast (see
        # splitstep/score.py::score_before). Rows come pre-formatted from
        # scoreboard_rows so this module knows nothing about tennis.
        _draw_scoreboard(draw, scoreboard, ImageFont.truetype(font, _BOARD_SIZE))
```

Update the module docstring's first line to "Burned-in counter, note and scoreboard for a numbered reel render."

- [ ] **Step 4: Run to verify pass**

Run: `~/miniconda3/envs/splitstep/bin/pytest tests/test_numbered.py -q`
Expected: pass.

- [ ] **Step 5: Write the failing handler test** (append to `tests/test_handler_reel.py`)

```python
def test_handle_reel_numbered_burns_the_score_entering_each_clip(
    library, conn, reel_of_two, monkeypatch
):
    from splitstep.db.rallies import set_winner
    from splitstep.db.sessions import set_scoring
    from splitstep.jobs import handlers as handlers_mod

    fx = reel_of_two
    set_scoring(conn, fx["session_id"], {
        "players": ["Me", "Opp"], "sets": 3, "ad": True, "tiebreak": "at6", "tiebreakTo": 7,
    })
    rows = list_rallies(conn, fx["session_id"])
    set_winner(conn, rows[0]["id"], "a")
    set_winner(conn, rows[1]["id"], "b")
    _cut_all(library, fx)

    seen: list[list[list[str]] | None] = []

    def spy(dst, *, counter, note, font, scoreboard=None):
        seen.append(scoreboard)
        return real(dst, counter=counter, note=note, font=font, scoreboard=scoreboard)

    real = handlers_mod.render_overlay_png
    monkeypatch.setattr(handlers_mod, "render_overlay_png", spy)

    handle_reel(library, {"reel_id": fx["reel"]["id"], "numbered": True})

    # Clip 1 is the first point: nothing has been scored yet. Clip 2 enters
    # at 15-0 to Me -- the score BEFORE the point it contains.
    assert seen == [
        [["Me", "0", "0"], ["Opp", "0", "0"]],
        [["Me", "0", "15"], ["Opp", "0", "0"]],
    ]


def test_handle_reel_numbered_has_no_board_for_an_untracked_session(
    library, conn, reel_of_two, monkeypatch
):
    from splitstep.jobs import handlers as handlers_mod

    fx = reel_of_two
    _cut_all(library, fx)
    seen = []
    real = handlers_mod.render_overlay_png

    def spy(dst, *, counter, note, font, scoreboard=None):
        seen.append(scoreboard)
        return real(dst, counter=counter, note=note, font=font, scoreboard=scoreboard)

    monkeypatch.setattr(handlers_mod, "render_overlay_png", spy)
    handle_reel(library, {"reel_id": fx["reel"]["id"], "numbered": True})
    assert seen == [None, None]
```

Add `list_rallies` to the test module's imports from `splitstep.db.rallies` if it is not already there.

- [ ] **Step 6: Run to verify failure**

Run: `~/miniconda3/envs/splitstep/bin/pytest tests/test_handler_reel.py -k score -q`
Expected: first test fails on `seen == [None, None]`.

- [ ] **Step 7: Implement the replay in `handlers.py`**

Imports: add `from splitstep.db.rallies import list_rallies, replace_rallies, set_clip_path` (extend the existing line), `from splitstep.db.sessions import ... get_session, scoring_rules` (extend the existing block), and `from splitstep.score import rules_from_dict, score_before, scoreboard_rows`.

Inside the `if payload.get("numbered"):` branch, before the `for` loop:

```python
        # The score entering each clip, replayed per session. Cached per
        # session because a reel is session-agnostic (items from several
        # matches can sit in one reel) and re-reading a session's rallies
        # per item would be N queries for one answer. An orphan item (no
        # rally) or an untracked session gets no board -- the counter and
        # note still burn as before.
        session_cache: dict[str, tuple[list, object] | None] = {}

        def board_for(item) -> list[list[str]] | None:
            if item.rally is None:
                return None
            if item.session_id not in session_cache:
                rules = scoring_rules(get_session(conn, item.session_id))
                session_cache[item.session_id] = (
                    None if rules is None
                    else ([dict(r) for r in list_rallies(conn, item.session_id)],
                          rules_from_dict(rules))
                )
            cached = session_cache[item.session_id]
            if cached is None:
                return None
            rallies, parsed = cached
            state, _unscored = score_before(rallies, item.rally["id"], parsed)
            return scoreboard_rows(state, parsed)
```

and change the `render_overlay_png` call to:

```python
                render_overlay_png(
                    png_i, counter=f"{i} / {total}", note=item.note, font=font,
                    scoreboard=board_for(item),
                )
```

`conn` must be the handler's open connection — read the top of `handle_reel` to confirm its name.

- [ ] **Step 8: Run to verify pass**

Run: `~/miniconda3/envs/splitstep/bin/pytest tests/test_handler_reel.py tests/test_numbered.py -q && ~/miniconda3/envs/splitstep/bin/ruff check splitstep tests`
Expected: pass.

- [ ] **Step 9: Commit**

```bash
git add splitstep/media/numbered.py splitstep/jobs/handlers.py tests/test_numbered.py tests/test_handler_reel.py
git commit -m "feat(reels): burn the score entering each clip onto numbered renders

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: `QueueController` winners, the `'winner'` action, persist and flash

**Files:**
- Modify: `web/src/lib/queue.ts`
- Modify: `web/src/lib/persist.ts`
- Modify: `web/src/lib/flash.ts`
- Modify: `web/src/lib/api.ts`
- Test: `web/tests/queue.test.ts`, `web/tests/persist.test.ts` (exists? if not, create), `web/tests/flash.test.ts` (same)

**Interfaces:**
- Produces:
  ```ts
  // queue.ts
  PersistableAction.kind adds 'winner'; PersistableAction gains `winner: Winner; previousWinner: Winner`
  UndoAction gains `winner: Winner`
  type Winner = '' | 'a' | 'b'
  QueueController.winner(p: 'a' | 'b'): PersistableAction | null   // sets winner + point, does not advance
  QueueController.winnerOf(rallyId): Winner
  QueueController.currentWinner: Winner
  liveSnapshot(rallies) writes `winner` too, and leaves rallies it never held untouched
  // persist.ts
  PersistApi.winner: (id, winner: Winner) => Promise<unknown>
  // api.ts
  api.winner(id, winner), api.setScoring(sessionId, rules | null)
  ```

- [ ] **Step 1: Write the failing tests** (append to `web/tests/queue.test.ts` inside the `describe`)

```ts
  it('records a winner, which also makes the rally a point', () => {
    const a = q.winner('a')!
    expect(a.kind).toBe('winner')
    expect(a.winner).toBe('a')
    expect(a.point).toBe(true)
    expect(a.previousWinner).toBe('')
    expect(a.previousPoint).toBe(false)
    expect(q.currentWinner).toBe('a')
    expect(q.currentIsPoint).toBe(true)
    expect(q.pointCount).toBe(1)
    // Like star/point, it does not advance.
    expect(q.index).toBe(0)
  })

  it('replaces a wrong winner in place', () => {
    q.winner('a')
    const b = q.winner('b')!
    expect(b.previousWinner).toBe('a')
    expect(q.currentWinner).toBe('b')
    expect(q.pointCount).toBe(1)
  })

  it('unmarking the point clears the winner', () => {
    q.winner('a')
    q.point()
    expect(q.currentIsPoint).toBe(false)
    expect(q.currentWinner).toBe('')
  })

  it('undo restores both the point and the winner', () => {
    q.winner('a')
    const u = q.undo()!
    expect(u.kind).toBe('undo')
    expect(u.point).toBe(false)
    expect(u.winner).toBe('')
    expect(q.currentWinner).toBe('')
    expect(q.currentIsPoint).toBe(false)
  })

  it('revert restores the previous winner', () => {
    const a = q.winner('a')!
    q.revert(a)
    expect(q.currentWinner).toBe('')
    expect(q.currentIsPoint).toBe(false)
  })

  it('seeds winners from the server snapshot', () => {
    const s = new QueueController([rally(1, { point: 1, winner: 'b' })])
    expect(s.currentWinner).toBe('b')
  })

  it('liveSnapshot carries winner, and leaves rallies it never held alone', () => {
    q.winner('a')
    const other = rally(9, { starred: 1, point: 1, winner: 'b' })
    const snap = q.liveSnapshot([rally(1), other])
    expect(snap[0].winner).toBe('a')
    expect(snap[0].point).toBe(1)
    // A rally from another source tab: not this controller's to zero out.
    expect(snap[1]).toEqual(other)
  })
```

- [ ] **Step 2: Run to verify failure**

Run: `cd web && npx vitest run tests/queue.test.ts`
Expected: type errors / `q.winner is not a function`.

- [ ] **Step 3: Implement in `queue.ts`**

Types:

```ts
export type Winner = '' | 'a' | 'b'

export interface PersistableAction {
  kind: 'star' | 'reject' | 'skip' | 'point' | 'winner'
  rallyId: string
  starred: boolean
  rejected: boolean
  point: boolean
  winner: Winner
  previousStarred: boolean
  previousRejected: boolean
  previousPoint: boolean
  previousWinner: Winner
}

export interface UndoAction {
  kind: 'undo'
  rallyId: string
  starred: boolean
  rejected: boolean
  point: boolean
  winner: Winner
}

interface HistoryEntry {
  index: number
  starred: boolean
  rejected: boolean
  point: boolean
  winner: Winner
}
```

Update the doc comments: the first sentence of `PersistableAction`'s comment becomes "An action that changed rally flags (star/reject/point/winner/skip)…"; `UndoAction`'s comment stays.

Fields and constructor:

```ts
  // Every rally this controller was built from, winner included ('' when
  // none). A Map rather than a Set because a winner is one of three
  // values, and seeding every id (not just the scored ones) is what lets
  // liveSnapshot tell "this controller cleared it" from "never held it".
  #winners = new Map<string, Winner>()
  ...
    for (const r of this.#rallies) this.#winners.set(r.id, r.winner)
```

Every existing action (`star`, `reject`, `skip`, `point`) gains `winner` and `previousWinner` in its returned object — for `star`, `reject`, `skip` both are `this.#winners.get(r.id) ?? ''`. `#record()` pushes `winner: this.#winners.get(r.id) ?? ''`.

`point()` — unmarking clears the winner, so the two never disagree (mirrors `set_point` in Task 3):

```ts
  point(): PersistableAction | null {
    const r = this.current
    if (!r) return null
    this.#record()
    const previousPoint = this.#points.has(r.id)
    const previousWinner = this.#winners.get(r.id) ?? ''
    const nowPoint = !previousPoint
    if (nowPoint) this.#points.add(r.id)
    else {
      this.#points.delete(r.id)
      // A winner on a non-point is a contradiction; the server's set_point
      // clears it too, so the two stay in step without a second round trip.
      this.#winners.set(r.id, '')
    }
    return {
      kind: 'point',
      rallyId: r.id,
      starred: this.#starred.has(r.id),
      rejected: this.#rejected.has(r.id),
      point: nowPoint,
      winner: nowPoint ? previousWinner : '',
      previousStarred: this.#starred.has(r.id),
      previousRejected: this.#rejected.has(r.id),
      previousPoint,
      previousWinner,
    }
  }

  // Who won the current point. Also marks it a point: pressing A/B is one
  // judgement ("a point, and she took it"), and asking for P first would
  // be two keys for it. Replaces a previous winner in place, which is how a
  // wrong answer gets corrected on the way back through the pass. Does not
  // advance, like every other verdict.
  winner(p: 'a' | 'b'): PersistableAction | null {
    const r = this.current
    if (!r) return null
    this.#record()
    const previousPoint = this.#points.has(r.id)
    const previousWinner = this.#winners.get(r.id) ?? ''
    this.#points.add(r.id)
    this.#winners.set(r.id, p)
    return {
      kind: 'winner',
      rallyId: r.id,
      starred: this.#starred.has(r.id),
      rejected: this.#rejected.has(r.id),
      point: true,
      winner: p,
      previousStarred: this.#starred.has(r.id),
      previousRejected: this.#rejected.has(r.id),
      previousPoint,
      previousWinner,
    }
  }

  winnerOf(rallyId: string): Winner {
    return this.#winners.get(rallyId) ?? ''
  }

  get currentWinner(): Winner {
    const r = this.current
    return r ? this.winnerOf(r.id) : ''
  }
```

`undo()` restores `this.#winners.set(r.id, entry.winner)` and returns `winner: entry.winner`. `revert()` does `this.#winners.set(action.rallyId, action.previousWinner)`.

`liveSnapshot`:

```ts
  liveSnapshot(rallies: Rally[]): Rally[] {
    return rallies.map((r) => {
      // Not this controller's rally (another source tab's, when the caller
      // hands in the whole session for a score replay): its server flags
      // are the freshest anyone has, so pass it through untouched rather
      // than zeroing flags this controller never held.
      if (!this.#winners.has(r.id)) return { ...r }
      return {
        ...r,
        starred: this.#starred.has(r.id) ? 1 : 0,
        rejected: this.#rejected.has(r.id) ? 1 : 0,
        point: this.#points.has(r.id) ? 1 : 0,
        winner: this.#winners.get(r.id) ?? '',
      }
    })
  }
```

Extend the comment above `liveSnapshot` with one sentence: "Rallies the controller was not built from pass through unchanged — see the inline comment."

- [ ] **Step 4: `persist.ts`, `flash.ts`, `api.ts`**

`persist.ts` — `PersistApi` gains `winner: (id: string, winner: Winner) => Promise<unknown>`; import `Winner` as a type. In the switch:

```ts
      case 'winner':
        // One POST: the server's set_winner marks the point itself, so a
        // second /point call would only race it.
        await api.winner(action.rallyId, action.winner)
        break
```

and in the `'undo'` case, after the three existing calls:

```ts
        // Winner last, always: set_point(false) clears the winner and
        // set_winner('a') re-marks the point, so point-then-winner is the
        // order that leaves the two columns agreeing. Sent even when the
        // restored winner is '' -- a point that keeps its flag but loses its
        // winner is only expressible this way. A 409 here can only mean
        // tracking was switched off in another tab; it surfaces through
        // the same toast as any other failed undo.
        await api.winner(action.rallyId, action.winner)
```

`flash.ts`:

```ts
    case 'winner':
      return {
        label: `Point · ${action.winner === 'a' ? 'A' : 'B'}`,
        glyph: '●',
        tone: 'point',
      }
```

(The panel beside the video names the player; the flash over the footage keys on the letter the reviewer just pressed. `QueueMode` overrides the label with the name — see Task 7 — so this default is what tests see.)

`api.ts`, beside `point`:

```ts
  winner: (id: string, winner: '' | 'a' | 'b') => post(`/api/rallies/${id}/winner`, { winner }),
  setScoring: (sessionId: string, rules: ScoreRules | null) =>
    req<{ scoring: ScoreRules | null }>(`/api/sessions/${sessionId}/scoring`, {
      method: 'POST',
      body: JSON.stringify({ rules }),
    }),
```

with `import type { ScoreRules } from './score'`.

- [ ] **Step 5: Tests for persist and flash**

If `web/tests/persist.test.ts` exists, add a case asserting `'winner'` calls `api.winner` once and nothing else, and that `'undo'` calls star, reject, point, winner in that order. If it does not exist, create it with exactly those two cases using a recording stub:

```ts
import { describe, expect, it } from 'vitest'
import { persistAction } from '../src/lib/persist'
import type { PersistableAction, UndoAction } from '../src/lib/queue'

function stub() {
  const calls: string[] = []
  const api = {
    star: async (id: string, v: boolean) => { calls.push(`star:${v}`) },
    reject: async (id: string, v: boolean) => { calls.push(`reject:${v}`) },
    point: async (id: string, v: boolean) => { calls.push(`point:${v}`) },
    winner: async (id: string, v: string) => { calls.push(`winner:${v}`) },
    seen: async () => { calls.push('seen') },
  }
  return { api, calls }
}

describe('persistAction', () => {
  it('sends a winner as one call', async () => {
    const { api, calls } = stub()
    const a: PersistableAction = {
      kind: 'winner', rallyId: 'r', starred: false, rejected: false, point: true, winner: 'a',
      previousStarred: false, previousRejected: false, previousPoint: false, previousWinner: '',
    }
    expect(await persistAction(a, api)).toEqual({ ok: true })
    expect(calls).toEqual(['winner:a'])
  })

  it('undo re-syncs all four, point before winner', async () => {
    const { api, calls } = stub()
    const u: UndoAction = { kind: 'undo', rallyId: 'r', starred: false, rejected: false, point: true, winner: 'b' }
    await persistAction(u, api)
    expect(calls).toEqual(['star:false', 'reject:false', 'point:true', 'winner:b'])
  })
})
```

For flash: add to `web/tests/flash.test.ts` (create if absent) a case that `flashFor(winnerAction)` has tone `'point'` and a label starting with `Point`.

- [ ] **Step 6: Run to verify pass**

Run: `cd web && npx vitest run && npm run check`
Expected: all green. `npm run check` will flag every place that builds a `PersistableAction`/`UndoAction` literal without the new fields — fix each (tests included).

- [ ] **Step 7: Commit**

```bash
git add web/src/lib/queue.ts web/src/lib/persist.ts web/src/lib/flash.ts web/src/lib/api.ts web/tests
git commit -m "feat(queue): winner action beside star/point/reject, with undo, revert and persist

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Score panel, rules card, and QueueMode wiring

**Files:**
- Create: `web/src/components/ScorePanel.svelte`
- Create: `web/src/components/ScoreSetup.svelte`
- Modify: `web/src/components/QueueMode.svelte`
- Modify: `web/src/routes/Session.svelte` (pass `sessionRallies`, keep `detail.session.scoring` in step)
- Modify: `web/src/lib/shortcuts.ts`
- Test: `web/tests/score-panel.test.ts`, `web/tests/score-setup.test.ts`, `web/tests/shortcuts.test.ts`

**Interfaces:**
- Consumes: `scoreBefore`, `scoreboardRows`, `playerName`, `DEFAULT_RULES`, `ScoreRules` (Task 2); `QueueController.winner`, `liveSnapshot` (Task 6); `api.setScoring`.
- Produces:
  - `ScorePanel` props: `{ rules: ScoreRules; state: ScoreState; unscored: number; prompting: boolean; onwin: (p: Player) => void }`
  - `ScoreSetup` props: `{ initial: ScoreRules; onstart: (rules: ScoreRules) => void; oncancel: () => void }`
  - `QueueMode` new props: `sessionRallies: Rally[]`, `onscoring?: (rules: ScoreRules | null) => void`
  - `shortcuts.ts`: the queue table gains `{ keys: ['A', 'B'], label: 'Who won the point (score tracking)' }` and `{ keys: ['H'], label: 'Show rejected rallies' }` (H is wired in Task 8; the reference lists it now so the duplicate-key test covers both).

- [ ] **Step 1: Shortcuts test + table**

In `web/tests/shortcuts.test.ts` there is a duplicate-key check over `shortcutKeys(mode)` for every mode; it needs no change but must stay green. Add to `QUEUE[0].items` in `shortcuts.ts` after the `P` entry:

```ts
      { keys: ['A', 'B'], label: 'Who won the point, when tracking a score' },
```

and to the `Open` group before `?`:

```ts
      { keys: ['H'], label: 'Show rejected rallies, to bring one back' },
```

`PRIMARY.queue` indices reference `QUEUE[0].items[0..3]` and `QUEUE[2].items[1]`, `QUEUE[3].items[2]` — inserting after `P` shifts `X` and `U` to indices 3 and 4, and adding `H` before `?` shifts `?` to index 3. Update `PRIMARY.queue` to `[QUEUE[0].items[0], QUEUE[0].items[1], QUEUE[0].items[3], QUEUE[0].items[4], QUEUE[2].items[1], QUEUE[3].items[3]]`. Run `npx vitest run tests/shortcuts.test.ts tests/keyhints-overlay.test.ts` and fix whatever asserts on the strip's contents.

- [ ] **Step 2: Write the failing component tests**

```ts
// web/tests/score-panel.test.ts
import { mount, unmount } from 'svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ScorePanel from '../src/components/ScorePanel.svelte'
import { DEFAULT_RULES, score } from '../src/lib/score'
import type { Player } from '../src/lib/score'

let host: HTMLElement | null = null
let app: Record<string, unknown> | null = null

function render(props: Record<string, unknown>) {
  host = document.createElement('div')
  document.body.appendChild(host)
  app = mount(ScorePanel, { target: host, props })
  return host
}

afterEach(() => {
  if (app) unmount(app)
  host?.remove()
  app = host = null
})

describe('ScorePanel', () => {
  it('renders one row per player with the board columns', () => {
    const state = score(('aaaabbbb'.repeat(4) + 'aaaaaaaa' + 'aaaab').split('') as Player[], DEFAULT_RULES)
    const el = render({ rules: DEFAULT_RULES, state, unscored: 0, prompting: false, onwin: () => {} })
    const rows = el.querySelectorAll('[data-testid="score-row"]')
    expect(rows.length).toBe(2)
    expect(rows[0].textContent?.replace(/\s+/g, ' ').trim()).toBe('Me 6 0 40')
    expect(rows[1].textContent?.replace(/\s+/g, ' ').trim()).toBe('Opp 4 0 15')
  })

  it('names the winner buttons after the players and fires onwin', () => {
    const onwin = vi.fn()
    const el = render({ rules: { ...DEFAULT_RULES, players: ['Ann', 'Bob'] }, state: score([], DEFAULT_RULES), unscored: 0, prompting: false, onwin })
    const buttons = el.querySelectorAll('button')
    expect(buttons[0].textContent).toContain('Ann')
    expect(buttons[1].textContent).toContain('Bob')
    ;(buttons[1] as HTMLButtonElement).click()
    expect(onwin).toHaveBeenCalledWith('b')
  })

  it('shows the unscored count only when there is one', () => {
    const none = render({ rules: DEFAULT_RULES, state: score([], DEFAULT_RULES), unscored: 0, prompting: false, onwin: () => {} })
    expect(none.textContent).not.toContain('unscored')
    unmount(app!); app = null; host?.remove()
    const some = render({ rules: DEFAULT_RULES, state: score([], DEFAULT_RULES), unscored: 2, prompting: false, onwin: () => {} })
    expect(some.textContent).toContain('2 unscored')
  })

  it('asks who won while prompting', () => {
    const el = render({ rules: DEFAULT_RULES, state: score([], DEFAULT_RULES), unscored: 0, prompting: true, onwin: () => {} })
    expect(el.querySelector('[role="status"]')?.textContent).toContain('Who won')
  })

  it('announces the winner of a finished match', () => {
    const el = render({ rules: DEFAULT_RULES, state: score('a'.repeat(48).split('') as Player[], DEFAULT_RULES), unscored: 0, prompting: false, onwin: () => {} })
    expect(el.textContent).toContain('Me wins')
  })
})
```

```ts
// web/tests/score-setup.test.ts
import { mount, unmount } from 'svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ScoreSetup from '../src/components/ScoreSetup.svelte'
import { DEFAULT_RULES } from '../src/lib/score'

let host: HTMLElement | null = null
let app: Record<string, unknown> | null = null

function render(props: Record<string, unknown>) {
  host = document.createElement('div')
  document.body.appendChild(host)
  app = mount(ScoreSetup, { target: host, props })
  return host
}

afterEach(() => {
  if (app) unmount(app)
  host?.remove()
  app = host = null
})

describe('ScoreSetup', () => {
  it('starts with the initial rules', async () => {
    const onstart = vi.fn()
    const el = render({ initial: DEFAULT_RULES, onstart, oncancel: () => {} })
    ;(el.querySelector('form') as HTMLFormElement).requestSubmit()
    await Promise.resolve()
    expect(onstart).toHaveBeenCalledWith(DEFAULT_RULES)
  })

  it('trims names and refuses an empty one', async () => {
    const onstart = vi.fn()
    const el = render({ initial: DEFAULT_RULES, onstart, oncancel: () => {} })
    const inputs = el.querySelectorAll('input[type="text"]') as NodeListOf<HTMLInputElement>
    inputs[0].value = '  '
    inputs[0].dispatchEvent(new Event('input', { bubbles: true }))
    ;(el.querySelector('form') as HTMLFormElement).requestSubmit()
    await Promise.resolve()
    expect(onstart).not.toHaveBeenCalled()
    expect(el.textContent).toContain('needs a name')
  })

  it('cancel fires oncancel', () => {
    const oncancel = vi.fn()
    const el = render({ initial: DEFAULT_RULES, onstart: () => {}, oncancel })
    ;(el.querySelector('button[type="button"]') as HTMLButtonElement).click()
    expect(oncancel).toHaveBeenCalled()
  })
})
```

- [ ] **Step 3: Run to verify failure**

Run: `cd web && npx vitest run tests/score-panel.test.ts tests/score-setup.test.ts`
Expected: cannot resolve the components.

- [ ] **Step 4: Write `ScorePanel.svelte`**

```svelte
<script lang="ts">
  import { playerName, scoreboardRows } from '../lib/score'
  import type { Player, ScoreRules, ScoreState } from '../lib/score'

  interface Props {
    rules: ScoreRules
    /** The score ENTERING the rally on screen (lib/score.ts::scoreBefore),
     *  which is also what the numbered render burns -- one number, two
     *  places, so the panel and the reel can never disagree. */
    state: ScoreState
    /** Points before this rally that carry no winner. Shown so the board
     *  reads as provisional instead of silently wrong. */
    unscored: number
    /** P was just pressed with tracking on: ask who won. */
    prompting: boolean
    onwin: (p: Player) => void
  }
  let { rules, state, unscored, prompting, onwin }: Props = $props()

  const rows = $derived(scoreboardRows(state, rules))
  const winnerName = $derived(state.finished ? playerName(rules, state.finished) : null)
  const KEY =
    'inline-block min-w-6 rounded border border-line bg-surface-2 px-1.5 text-center' +
    ' font-data text-caption text-dim'
</script>

<!-- A surface card, never bare court: the secondary text here is dim/faint
     and the contrast rule in app.css only holds it on a surface. -->
<aside class="rounded-lg border border-line bg-surface p-3" aria-label="match score">
  <table class="w-full font-data text-data tabular-nums">
    <tbody>
      {#each rows as row, i (i)}
        <tr data-testid="score-row" class={row[row.length - 1] === 'W' ? 'text-fg' : 'text-dim'}>
          <td class="pr-3 text-left text-fg">{row[0]}</td>
          {#each row.slice(1) as cell, c (c)}
            <td class="px-1 text-right">{cell}</td>
          {/each}
        </tr>
      {/each}
    </tbody>
  </table>

  {#if winnerName}
    <p class="mt-2 font-data text-data text-fg">{winnerName} wins</p>
  {/if}

  {#if prompting}
    <p class="mt-2 font-data text-data text-fg" role="status" aria-live="polite">
      Who won? <span class={KEY}>A</span> {rules.players[0]} · <span class={KEY}>B</span>
      {rules.players[1]} · <span class={KEY}>Esc</span> skip
    </p>
  {/if}

  <div class="mt-2 flex gap-2">
    <button
      type="button"
      class="flex-1 rounded border border-line px-2 py-1 font-data text-data text-fg
             hover:bg-surface-2 motion-safe:transition-colors"
      onclick={() => onwin('a')}
    >
      <span class={KEY}>A</span> {rules.players[0]} won
    </button>
    <button
      type="button"
      class="flex-1 rounded border border-line px-2 py-1 font-data text-data text-fg
             hover:bg-surface-2 motion-safe:transition-colors"
      onclick={() => onwin('b')}
    >
      <span class={KEY}>B</span> {rules.players[1]} won
    </button>
  </div>

  {#if unscored > 0}
    <p class="mt-2 font-data text-caption text-faint">{unscored} unscored point{unscored === 1 ? '' : 's'} before this</p>
  {/if}
</aside>
```

- [ ] **Step 5: Write `ScoreSetup.svelte`**

```svelte
<script lang="ts">
  import type { ScoreRules } from '../lib/score'

  interface Props {
    initial: ScoreRules
    onstart: (rules: ScoreRules) => void
    oncancel: () => void
  }
  let { initial, onstart, oncancel }: Props = $props()

  let nameA = $state(initial.players[0])
  let nameB = $state(initial.players[1])
  let sets = $state<ScoreRules['sets']>(initial.sets)
  let ad = $state(initial.ad)
  let tiebreak = $state<ScoreRules['tiebreak']>(initial.tiebreak)
  let tiebreakTo = $state<ScoreRules['tiebreakTo']>(initial.tiebreakTo)
  let error = $state<string | null>(null)

  function submit(e: SubmitEvent) {
    e.preventDefault()
    const a = nameA.trim()
    const b = nameB.trim()
    if (!a || !b) {
      error = 'Each player needs a name'
      return
    }
    error = null
    onstart({ players: [a, b], sets, ad, tiebreak, tiebreakTo })
  }

  const FIELD = 'rounded border border-line bg-surface px-2 py-1 font-data text-data text-fg'
</script>

<form class="rounded-lg border border-line bg-surface p-3" onsubmit={submit} aria-label="score tracking rules">
  <div class="grid grid-cols-2 gap-2">
    <label class="text-caption text-dim">Player A
      <input type="text" class="{FIELD} mt-1 w-full" bind:value={nameA} maxlength="24" />
    </label>
    <label class="text-caption text-dim">Player B
      <input type="text" class="{FIELD} mt-1 w-full" bind:value={nameB} maxlength="24" />
    </label>
    <label class="text-caption text-dim">Format
      <select class="{FIELD} mt-1 w-full" bind:value={tiebreak}>
        <option value="at6">Sets, tiebreak at 6–6</option>
        <option value="none">Sets, no tiebreak</option>
        <option value="only">One tiebreak only</option>
      </select>
    </label>
    {#if tiebreak === 'only'}
      <label class="text-caption text-dim">Tiebreak to
        <select class="{FIELD} mt-1 w-full" bind:value={tiebreakTo}>
          <option value={7}>7</option>
          <option value={10}>10</option>
        </select>
      </label>
    {:else}
      <label class="text-caption text-dim">Best of
        <select class="{FIELD} mt-1 w-full" bind:value={sets}>
          <option value={1}>1 set</option>
          <option value={3}>3 sets</option>
          <option value={5}>5 sets</option>
        </select>
      </label>
      <label class="col-span-2 flex items-center gap-2 text-caption text-dim">
        <input type="checkbox" class="accent-fg" bind:checked={ad} />
        Deuce / advantage (untick for no-ad)
      </label>
    {/if}
  </div>
  {#if error}
    <p class="mt-2 text-caption text-danger" role="alert">{error}</p>
  {/if}
  <div class="mt-3 flex justify-end gap-2">
    <button type="button" class="rounded border border-line px-3 py-1 font-data text-data text-dim hover:text-fg" onclick={oncancel}>Cancel</button>
    <button type="submit" class="rounded bg-fg px-3 py-1 font-data text-data font-medium text-bg hover:bg-fg/90">Start</button>
  </div>
</form>
```

`bind:value` on a `<select>` with numeric `<option value={7}>` keeps the number type in Svelte 5; `sets`/`tiebreakTo` stay numbers.

- [ ] **Step 6: Run the two component tests**

Run: `cd web && npx vitest run tests/score-panel.test.ts tests/score-setup.test.ts`
Expected: pass. (If `requestSubmit` is missing in jsdom 25, dispatch `new Event('submit', { cancelable: true })` on the form instead, in both tests.)

- [ ] **Step 7: Wire `QueueMode.svelte`**

Props — add to `Props` and the destructure:

```ts
    /** Every rally in the session, unscoped -- `detail.rallies` above is
     *  scoped to the selected video tab, and a score is a property of the
     *  match, so the replay has to see the other tabs' points too. */
    sessionRallies: Rally[]
    /** Tracking was switched on/off or its rules changed. Session keeps
     *  `detail.session.scoring` in step so a remount seeds correctly. */
    onscoring?: (rules: ScoreRules | null) => void
```

Imports: `import ScorePanel from './ScorePanel.svelte'`, `import ScoreSetup from './ScoreSetup.svelte'`, `import { DEFAULT_RULES, playerName, scoreBefore } from '../lib/score'`, `import type { Player, ScoreRules } from '../lib/score'`.

State, after `noteInput`:

```ts
  // Tracking rules, seeded once from the session and owned here until a
  // remount -- the same one-shot pattern as `queue`. The panel derives the
  // score from the live snapshot every time `version` bumps, so no score
  // is ever stored on this side either.
  let rules = $state<ScoreRules | null>(untrack(() => detail.session.scoring))
  let settingUp = $state(false)
  // P was pressed with tracking on: the panel asks who won until A/B/Esc.
  let awaitingWinner = $state(false)

  const board = $derived.by(() => {
    version
    if (!rules || !current) return null
    return scoreBefore(queue.liveSnapshot(sessionRallies), current.id, rules)
  })

  async function startTracking(next: ScoreRules): Promise<void> {
    try {
      const r = await api.setScoring(detail.session.id, next)
      rules = r.scoring
      settingUp = false
      onscoring?.(rules)
    } catch (e) {
      toaster.push(`Couldn't save the score rules -- ${String(e)}`)
    }
  }

  async function stopTracking(): Promise<void> {
    // Winners stay on the rallies (set_scoring leaves them), so this is
    // cheap to reverse; the confirm is about losing the panel mid-match by
    // a stray click, not about data.
    if (!confirm('Stop tracking the score for this session? Winners already recorded are kept.')) return
    try {
      await api.setScoring(detail.session.id, null)
      rules = null
      awaitingWinner = false
      onscoring?.(null)
    } catch (e) {
      toaster.push(`Couldn't turn off score tracking -- ${String(e)}`)
    }
  }

  function recordWinner(p: Player): void {
    if (!rules) return
    awaitingWinner = false
    apply(queue.winner(p))
  }
```

In `showFlash`, name the player instead of the letter when rules are known: after `const next = flashFor(action)` add

```ts
    if (next && action.kind === 'winner' && rules && action.winner) {
      next.label = `Point · ${playerName(rules, action.winner)}`
    }
```

Key handler — change the `P` case and add three:

```ts
      case 'p':
      case 'P': {
        const action = queue.point()
        apply(action)
        // With tracking on, a fresh point wants a winner; un-marking does not.
        awaitingWinner = !!rules && !!action && action.point
        break
      }
      case 'a':
      case 'A':
        if (rules) recordWinner('a')
        break
      case 'b':
      case 'B':
        if (rules) recordWinner('b')
        break
      case 'Escape':
        // Only the prompt: KeyHints owns Escape for its overlay (capture
        // phase), and queue mode has no other Escape meaning.
        awaitingWinner = false
        break
```

Also clear `awaitingWinner = false` in the `ArrowRight`/`ArrowLeft`/`u`/`U` cases (moving on or undoing abandons the question).

Template — a toolbar above the deck, inside the `{:else}` branch before `<div class="relative">`:

```svelte
  <div class="mb-2 flex items-center justify-between gap-4 font-data text-data text-dim">
    <label class="flex items-center gap-2">
      <input
        type="checkbox"
        class="accent-fg"
        checked={rules !== null}
        onchange={(e) => {
          if (e.currentTarget.checked) settingUp = true
          else void stopTracking()
        }}
      />
      Track score
    </label>
    <!-- Show-rejected toggle lands here in the next task. -->
  </div>
  {#if settingUp && !rules}
    <div class="mb-2">
      <ScoreSetup initial={DEFAULT_RULES} onstart={startTracking} oncancel={() => (settingUp = false)} />
    </div>
  {/if}
```

Wrap the deck and the panel side by side. Change `<div class="relative">` (the deck wrapper) into:

```svelte
  <div class="flex flex-col gap-3 xl:flex-row">
    <div class="relative min-w-0 flex-1">
      <VideoDeck ... unchanged ... />
      ... counter pill and flash unchanged ...
    </div>
    {#if rules && board}
      <div class="xl:w-72 xl:shrink-0">
        <ScorePanel {rules} state={board.state} unscored={board.unscored} prompting={awaitingWinner} onwin={recordWinner} />
      </div>
    {/if}
  </div>
```

`xl:` (Tailwind's default 1280px), not the repo's `ultra:` variant — `--breakpoint-ultra` is 1800px and the reviewer's laptop is 1440 wide, so `ultra:` would stack the panel under the video on the one screen that matters. Below `xl` the panel stacks beneath the deck, which is the spec's "narrow layouts" case.

The checkbox stays unchecked while `settingUp` (rules still null) — `checked={rules !== null}` handles that, and cancelling the card leaves it unchecked.

- [ ] **Step 8: `Session.svelte`**

Pass the new props where `QueueMode` is mounted:

```svelte
      <QueueMode
        detail={scopeToSource(detail, selectedSourceId)}
        sessionRallies={detail.rallies}
        onscoring={(rules) => {
          if (detail) detail.session.scoring = rules
        }}
        ...existing props...
      />
```

`detail` is `$state`, so the assignment is reactive and survives the next `{#key rallyRevision}` remount. Check `detail`'s declaration — if it is `$state.raw`, replace with `detail = { ...detail, session: { ...detail.session, scoring: rules } }`.

- [ ] **Step 9: Check and run everything**

Run: `cd web && npm run check && npx vitest run`
Expected: clean. Any `SessionDetail`/`Session` literal in tests without `scoring` gets `scoring: null`.

- [ ] **Step 10: Browser verification**

Start the dev pair (see CLAUDE.md: `splitstep serve` + `npm run dev`, or `preview_start` with a launch config if `.claude/launch.json` has one; otherwise `npm run build` then `serve` alone). Open a session in queue mode:
1. Tick **Track score** → card appears → Start → panel shows `Me 0 0 · Opp 0 0`.
2. Press `P` → panel prompts "Who won?"; press `A` → flash "Point · Me", panel unchanged (score *entering* this rally); `→` → panel reads 15-0.
3. `←`, `B` → corrected to Opp; `→` → 0-15.
4. `U` → undone; panel returns.
5. Untick → confirm → panel gone; re-tick → Start → score restored.
Screenshot the panel beside the video at 1440×900.

- [ ] **Step 11: Commit**

```bash
git add web/src/components/ScorePanel.svelte web/src/components/ScoreSetup.svelte web/src/components/QueueMode.svelte web/src/routes/Session.svelte web/src/lib/shortcuts.ts web/tests
git commit -m "feat(queue): optional match-score panel -- P asks who won, A/B answer

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Show rejected rallies and un-reject

**Files:**
- Modify: `web/src/lib/queue.ts` (constructor option)
- Modify: `web/src/components/QueueMode.svelte` (toggle, `H`)
- Modify: `web/src/routes/Session.svelte` (`showRejected` state, remount)
- Modify: `web/src/components/OverviewBand.svelte`
- Test: `web/tests/queue.test.ts`, `web/tests/overview-band.test.ts`

**Interfaces:**
- Produces: `new QueueController(rallies, { includeRejected?: boolean })`; `QueueMode` props `showRejected: boolean`, `ontoggle_rejected: (currentRallyId: string | null) => void`.

- [ ] **Step 1: Failing controller tests** (append to `queue.test.ts`)

```ts
  describe('includeRejected', () => {
    it('keeps rejected rallies in the queue, in order, and counts them', () => {
      const s = new QueueController([rally(1), rally(2, { rejected: 1 }), rally(3)], { includeRejected: true })
      expect(s.total).toBe(3)
      expect(s.rejectedCount).toBe(1)
      expect(s.isRejected('r2')).toBe(true)
    })

    it('X on a shown rejected rally un-rejects it', () => {
      const s = new QueueController([rally(1, { rejected: 1, seen_at: 'x' })], { includeRejected: true })
      expect(s.currentIsRejected).toBe(true)
      const a = s.reject()!
      expect(a.rejected).toBe(false)
      expect(s.rejectedCount).toBe(0)
    })

    it('resumes on the first unseen rally even when it is rejected', () => {
      const s = new QueueController([rally(1, { seen_at: 'x' }), rally(2, { rejected: 1 })], { includeRejected: true })
      expect(s.current?.id).toBe('r2')
    })

    it('default still hides rejected rallies', () => {
      const s = new QueueController([rally(1), rally(2, { rejected: 1 })])
      expect(s.total).toBe(1)
    })
  })
```

- [ ] **Step 2: Run to verify failure**

Run: `cd web && npx vitest run tests/queue.test.ts`
Expected: the option is ignored; `total` is 2 not 3.

- [ ] **Step 3: Implement the option**

```ts
export interface QueueOptions {
  /** Keep rejected rallies in the pass so one can be brought back. Off by
   *  default: the ordinary pass is about what is left to judge. */
  includeRejected?: boolean
}

  constructor(rallies: Rally[], options: QueueOptions = {}) {
    this.#rallies = options.includeRejected ? [...rallies] : rallies.filter((r) => !r.rejected)
    for (const r of this.#rallies) if (r.starred) this.#starred.add(r.id)
    for (const r of this.#rallies) if (r.point) this.#points.add(r.id)
    // Seeded only when they are shown: rejectedCount has always meant "rejected
    // in this pass" for the default queue, and the finish screen's tally
    // reads from it.
    if (options.includeRejected) for (const r of this.#rallies) if (r.rejected) this.#rejected.add(r.id)
    ...
```

`remainingMs` already filters `#rejected`, so time-left stays honest with rejected rallies shown.

- [ ] **Step 4: Wire the toggle**

`QueueMode.svelte` props:

```ts
    showRejected: boolean
    /** Flip show-rejected. Session remounts the queue (a fresh controller
     *  is the only way the filter changes) and comes back on this rally. */
    ontoggle_rejected: (currentRallyId: string | null) => void
```

Constructor: `new QueueController(untrack(() => detail.rallies), { includeRejected: untrack(() => showRejected) })`.

Key: `case 'h': case 'H': ontoggle_rejected(current ? current.id : null); break`.

Toolbar (the placeholder comment from Task 7):

```svelte
    <label class="flex items-center gap-2">
      <input type="checkbox" class="accent-fg" checked={showRejected}
             onchange={() => ontoggle_rejected(current ? current.id : null)} />
      Show rejected <span class="text-faint">H</span>
    </label>
```

Status row: the existing `{#if currentRejected}<span class="text-faint">rejected</span>{/if}` already badges it — extend the copy to `rejected · X brings it back` when `showRejected`.

`Session.svelte`: add `let showRejected = $state(false)` outside the `{#key}`; pass `{showRejected}` and

```ts
  function toggleRejected(rallyId: string | null) {
    showRejected = !showRejected
    focusedRallyId = rallyId
    rallyRevision += 1
  }
```

`focusedRallyId` is what `startAtRallyId={focusedRallyId}` already reads on the queue mount, and `mode` stays `'queue'` so the `{:else if focusedRallyId}` timeline branch is never reached. This is the same "come back on this rally" trick `closeTimeline` uses.

- [ ] **Step 5: OverviewBand — failing test**

```ts
// web/tests/overview-band.test.ts
import { mount, unmount } from 'svelte'
import { afterEach, describe, expect, it } from 'vitest'
import OverviewBand from '../src/components/OverviewBand.svelte'
import type { Rally, Source } from '../src/lib/types'

const source: Source = {
  id: 'src', session_id: 's', idx: 1, recorded_at: '', offset_ms: 0, duration_ms: 100000,
  width: 1920, height: 1080, fps: 30, has_original: 1, court_preset_id: null, status: 'ready', rotation_deg: 0,
}
function rally(idx: number, over: Partial<Rally> = {}): Rally {
  return {
    id: `r${idx}`, session_id: 's', source_id: 'src', idx, start_ms: idx * 10000, end_ms: idx * 10000 + 5000,
    det_start_ms: null, det_end_ms: null, confidence: 0.5, starred: 0, rejected: 0, point: 0,
    reviewed_at: null, seen_at: null, note: '', winner: '', ...over,
  }
}

let host: HTMLElement | null = null
let app: Record<string, unknown> | null = null
afterEach(() => { if (app) unmount(app); host?.remove(); app = host = null })

describe('OverviewBand', () => {
  it('draws a rejected rally hatched and faint, and says so in its title', () => {
    host = document.createElement('div'); document.body.appendChild(host)
    app = mount(OverviewBand, { target: host, props: {
      rallies: [rally(1), rally(2, { rejected: 1 })], sources: [source], currentId: 'r1',
      windowStartMs: 0, windowEndMs: 20000, onpick: () => {},
    } })
    const buttons = host.querySelectorAll('button')
    expect(buttons[1].className).toContain('hatched')
    expect(buttons[1].getAttribute('title')).toBe('rally 2 (rejected)')
    expect(buttons[0].className).not.toContain('hatched')
  })
})
```

- [ ] **Step 6: Implement in `OverviewBand.svelte`**

Button class expression becomes:

```svelte
      class="absolute top-1 bottom-1 rounded-sm {r.rejected
        ? 'hatched bg-faint'
        : r.starred
          ? 'bg-star'
          : 'bg-dim'} {r.id === currentId ? 'ring-2 ring-fg' : r.rejected ? 'opacity-40' : 'opacity-60'}"
      title={r.rejected ? `rally ${r.idx} (rejected)` : `rally ${r.idx}`}
      aria-label={r.rejected ? `rally ${r.idx} (rejected)` : `rally ${r.idx}`}
```

Add a scoped style block (allowed — see CLAUDE.md on `vite.config.ts`'s test-mode guard):

```svelte
<style>
  /* A gap in the band used to read as "nothing here"; hatching says
     "something was here and a human said no". Token colour via var() so
     the pattern follows the theme. */
  .hatched {
    background-image: repeating-linear-gradient(
      135deg,
      transparent 0 3px,
      var(--color-bg) 3px 5px
    );
  }
</style>
```

- [ ] **Step 7: Run and verify in the browser**

Run: `cd web && npx vitest run && npm run check`
Then in the browser: reject a rally, reload, press `H` → it reappears with the badge; `X` → "Kept" flash; `H` → gone from the pass; open timeline → the band hatches rejected rallies.

- [ ] **Step 8: Commit**

```bash
git add web/src/lib/queue.ts web/src/components/QueueMode.svelte web/src/routes/Session.svelte web/src/components/OverviewBand.svelte web/tests/queue.test.ts web/tests/overview-band.test.ts
git commit -m "feat(queue): H shows rejected rallies so X can bring one back

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Timeline mode fits one screen

**Files:**
- Modify: `web/src/components/TimelineMode.svelte`

- [ ] **Step 1: Wrap the deck**

Replace the bare `<VideoDeck ...>` under `{#if rally && source}` with:

```svelte
  <!-- Capped so the deck, the status line, both bands and the threshold row
       share one screen: trimming is a deck-and-band gesture and scrolling
       between them is the failure this removes. 28rem is what sits below
       the deck plus the page header; a 16:9 frame at (100vh - 28rem) tall
       is this wide. On a tall display the cap is never reached. -->
  <div class="mx-auto w-full max-w-[calc((100vh-28rem)*16/9)]">
    <VideoDeck
      ...unchanged props...
    />
  </div>
```

- [ ] **Step 2: Verify in the browser at two sizes**

Use `resize_window` to 1440×900 and 1920×1080; open timeline on a rally; confirm with a screenshot that the threshold row is visible without scrolling at both sizes, and that at 1920×1080 the deck is centred with margins. If 28rem leaves a gap or overflows, measure the actual height below the deck with `javascript_tool` (`document.querySelector('section:last-of-type').getBoundingClientRect().bottom`) and adjust the rem once.

- [ ] **Step 3: Commit**

```bash
git add web/src/components/TimelineMode.svelte
git commit -m "fix(timeline): cap the deck so it and both bands fit one screen

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: CLI parity for scoring (so the terminal cannot drift from HTTP)

**Files:**
- Modify: `splitstep/cli.py`
- Test: `tests/test_cli.py`

CLAUDE.md's rule for setup is "HTTP and terminal call one function". Scoring gets the same: `splitstep score set <session_id> --players Me Opp --sets 3 --no-ad --tiebreak at6|none|only --tiebreak-to 7` and `splitstep score off <session_id>`, both calling `set_scoring`; `splitstep score show <session_id>` prints `scoreboard_rows` of the full replay.

- [ ] **Step 1: Failing tests** (append to `tests/test_cli.py`; `main`, `conn`, `library`, `add_source`, `find_or_create_session_for_date` are already imported there)

```python
def _scored_session(conn):
    from splitstep.db.rallies import replace_rallies, set_winner
    from splitstep.detect.segment import Interval

    session_id = find_or_create_session_for_date(conn, "2026-09-17")
    source_id, _ = add_source(
        conn, session_id, recorded_at="2026-09-17T10:00:00Z", duration_ms=600_000,
        width=1920, height=1080, fps=30.0, original_name="IMG_1.MOV",
    )
    replace_rallies(conn, session_id, source_id,
                    [Interval(1000, 5000, 0.8), Interval(9000, 14000, 0.7)])
    rows = list_rallies(conn, session_id)
    set_winner(conn, rows[0]["id"], "a")
    set_winner(conn, rows[1]["id"], "a")
    return session_id


def test_score_set_then_show_prints_the_board(library, conn, capsys):
    session_id = _scored_session(conn)
    conn.close()
    rc = main(["--library", str(library.root), "score", "set", session_id,
               "--players", "Ann", "Bob", "--sets", "3", "--tiebreak", "at6"])
    assert rc == 0
    rc = main(["--library", str(library.root), "score", "show", session_id])
    assert rc == 0
    out = capsys.readouterr().out
    # Two scored points to Ann: 30-0 in the first game.
    assert "Ann" in out and "30" in out
    assert "Bob" in out


def test_score_off_then_show_says_not_tracking(library, conn, capsys):
    session_id = _scored_session(conn)
    conn.close()
    main(["--library", str(library.root), "score", "set", session_id])
    rc = main(["--library", str(library.root), "score", "off", session_id])
    assert rc == 0
    rc = main(["--library", str(library.root), "score", "show", session_id])
    assert rc == 0
    assert "not tracking" in capsys.readouterr().out


def test_score_set_rejects_bad_rules(library, conn, capsys):
    session_id = _scored_session(conn)
    conn.close()
    rc = main(["--library", str(library.root), "score", "set", session_id, "--sets", "4"])
    assert rc == 2  # argparse choices reject it before any DB write


def test_score_on_an_unknown_session_fails(library, conn, capsys):
    conn.close()
    rc = main(["--library", str(library.root), "score", "show", "nope"])
    assert rc == 1
    assert "not found" in capsys.readouterr().err.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `~/miniconda3/envs/splitstep/bin/pytest tests/test_cli.py -k score_ -q`
Expected: argparse error `invalid choice: 'score'` (exit 2) on the first test.

- [ ] **Step 3: Implement**

In `splitstep/cli.py`, imports: add `get_session, scoring_rules, set_scoring` to the `splitstep.db.sessions` import and `from splitstep.score import DEFAULT_RULES, rules_to_dict, score_before, scoreboard_rows`. Commands, after `cmd_labels_score`:

```python
def _session_or_fail(conn, session_id: str):
    row = get_session(conn, session_id)
    if row is None:
        print(f"session not found: {session_id}", file=sys.stderr)
    return row


def cmd_score_set(args) -> int:
    """Same validator as POST /api/sessions/{id}/scoring (set_scoring ->
    rules_from_dict), so HTTP and terminal cannot drift."""
    library = _library(args)
    conn = connect(library.db_path)
    migrate(conn)
    if _session_or_fail(conn, args.session_id) is None:
        return 1
    rules = {
        "players": list(args.players),
        "sets": args.sets,
        "ad": not args.no_ad,
        "tiebreak": args.tiebreak,
        "tiebreakTo": args.tiebreak_to,
    }
    set_scoring(conn, args.session_id, rules)
    print(f"tracking {args.players[0]} vs {args.players[1]}: best of {args.sets},"
          f" {'no-ad' if args.no_ad else 'ad'}, tiebreak {args.tiebreak}")
    return 0


def cmd_score_off(args) -> int:
    library = _library(args)
    conn = connect(library.db_path)
    migrate(conn)
    if _session_or_fail(conn, args.session_id) is None:
        return 1
    set_scoring(conn, args.session_id, None)
    print("score tracking off; winners kept")
    return 0


def cmd_score_show(args) -> int:
    library = _library(args)
    conn = connect(library.db_path)
    migrate(conn)
    session = _session_or_fail(conn, args.session_id)
    if session is None:
        return 1
    rules = scoring_rules(session)
    if rules is None:
        print("not tracking a score for this session")
        return 0
    from splitstep.score import rules_from_dict
    parsed = rules_from_dict(rules)
    # An id no rally holds replays every scored point: the current score.
    state, unscored = score_before(
        [dict(r) for r in list_rallies(conn, args.session_id)], "", parsed
    )
    for row in scoreboard_rows(state, parsed):
        print("  ".join(f"{cell:>4}" if i else f"{cell:<12}" for i, cell in enumerate(row)))
    if unscored:
        print(f"({unscored} point{'s' if unscored != 1 else ''} with no winner)")
    return 0
```

(`list_rallies` is already imported in `cli.py` if `cmd_clips_export` uses it — check; import it from `splitstep.db.rallies` otherwise. Move `rules_from_dict` to the top-level import with the others.)

Parser, after the `labels` block in `main`:

```python
    p = sub.add_parser("score", help="match-score tracking for a session")
    score_sub = p.add_subparsers(dest="score_cmd", required=True)

    ss = score_sub.add_parser("set", help="turn tracking on (or change the rules)")
    ss.add_argument("session_id")
    ss.add_argument("--players", nargs=2, metavar=("A", "B"),
                    default=list(DEFAULT_RULES.players))
    ss.add_argument("--sets", type=int, choices=(1, 3, 5), default=DEFAULT_RULES.sets)
    ss.add_argument("--no-ad", action="store_true", help="sudden death at deuce")
    ss.add_argument("--tiebreak", choices=("at6", "none", "only"), default=DEFAULT_RULES.tiebreak)
    ss.add_argument("--tiebreak-to", type=int, choices=(7, 10), default=DEFAULT_RULES.tiebreak_to)
    ss.set_defaults(func=cmd_score_set)

    so = score_sub.add_parser("off", help="stop tracking; winners are kept")
    so.add_argument("session_id")
    so.set_defaults(func=cmd_score_off)

    sw = score_sub.add_parser("show", help="print the current board")
    sw.add_argument("session_id")
    sw.set_defaults(func=cmd_score_show)
```

`rules_to_dict` is unused if the dict is built by hand above — drop it from the import.

- [ ] **Step 3b: Run to verify pass**

Run: `~/miniconda3/envs/splitstep/bin/pytest tests/test_cli.py -q && ~/miniconda3/envs/splitstep/bin/ruff check splitstep tests`
Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add splitstep/cli.py tests/test_cli.py
git commit -m "feat(cli): splitstep score set/show/off, sharing the API's validator

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Full verification and the live library

- [ ] **Step 1: Whole suites**

Run:
```bash
~/miniconda3/envs/splitstep/bin/pytest -q
~/miniconda3/envs/splitstep/bin/ruff check splitstep tests
cd web && npm run check && npx vitest run && npm run build
```
Expected: all green; note the new pytest count for CLAUDE.md.

- [ ] **Step 2: Apply migration 013 to the live library**

Follow the memory note "Applying a migration to the live library": back up with sqlite `.backup` (not `cp`), restart `splitstep serve`, then verify:

```bash
sqlite3 /Volumes/SanDisk_2TB/SplitStep/library.db "PRAGMA user_version; SELECT COUNT(*) FROM rallies WHERE winner != '';"
```
Expected: `13`, `0`.

- [ ] **Step 3: End-to-end on real footage**

In the running app: enable tracking on a real session, score three points, build a numbered reel of points, render it, and confirm the board in the bottom-left of the rendered `.mp4` (`ffmpeg -ss 1 -i reels/<slug>.mp4 -frames:v 1 /tmp/frame.png` then view). This is the "verify the artifact" rule.

---

### Task 12: Docs — CLAUDE.md, spec correction, SMOKE.md

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/superpowers/specs/2026-09-17-score-tracking-design.md`
- Modify: `docs/SMOKE.md`

- [ ] **Step 1: CLAUDE.md**

Under **Conventions that matter**, add one bullet:

> - **Score is replayed, never stored.** `rallies.winner` (`''`/`'a'`/`'b'`) and `sessions.scoring` (rules JSON, `''` = off) are the only score columns. `splitstep/score.py` and `web/src/lib/score.ts` are the same function twice, pinned to `tests/fixtures/score_cases.json` by both test suites — change the rules in both and add a case. Everything shows the score *entering* a rally (`score_before`), replayed over the whole session in `idx` order; `QueueMode` takes `sessionRallies` for this because `detail` is scoped to a video tab. `set_point(False)` clears `winner` and `set_winner('a')` sets `point`, so the two columns cannot disagree; `replace_rallies` carries `winner` only where it carries `point`. Winner keys are `A`/`B`, not `1`/`2` — the digits are playback speed.

Update the pytest count in the Commands block, and in the Frontend section's list of `lib/` modules add `score.ts`.

- [ ] **Step 2: Spec correction**

In the spec's Queue UI section, replace `1` / `2` with `A` / `B` and add a dated note: "2026-09-17 correction (planning): `1`/`2` are playback-speed keys in queue mode; winner keys are `A`/`B`, positional on `rules.players`."

- [ ] **Step 3: SMOKE.md**

Add rows for: score tracking on/off, A/B/P/Esc, undo of a winner, numbered render with board, show-rejected + un-reject, timeline fit at 1440×900 — each marked with what Task 11 actually exercised.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/superpowers/specs/2026-09-17-score-tracking-design.md docs/SMOKE.md
git commit -m "docs: score tracking, show-rejected and timeline fit

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```
