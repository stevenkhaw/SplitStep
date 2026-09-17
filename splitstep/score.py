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
        # ValueError, not TypeError: this validates the shape of a JSON blob
        # from the db or an API body, not a caller's argument type, and the
        # other three checks below raise ValueError for the same reason --
        # a uniform except ValueError at the boundary must catch all of them.
        raise ValueError("ad must be true or false")  # noqa: TRY004
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
