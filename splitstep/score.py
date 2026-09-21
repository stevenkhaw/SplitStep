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
    # Who served the session's first point, or None when nobody said. Not
    # defaulted: every session recorded before this key existed has no
    # honest value, and 'a' would be wrong half the time. The same choice
    # preset_assigned_at and sampled_recall make -- absent stays absent, and
    # the UI renders no server rather than a guess.
    first_server: str | None = None


@dataclass(frozen=True)
class ScoreState:
    sets: tuple[tuple[int, int], ...]
    games: tuple[int, int]
    points: tuple[str, str]
    in_tiebreak: bool
    finished: str | None
    # Who serves the point this state is entering, derived from
    # rules.first_server by the same replay -- stored nowhere, like the rest
    # of the score. None when no first server was named, and None once the
    # match is decided, for the reason scoreboard_rows drops games and
    # points there: there is no next point to serve.
    server: str | None


@dataclass(frozen=True)
class AnchorState:
    """A reviewer's "entering here, the score is X" (migration 017).

    An anchor is an INPUT to the replay, exactly like a winner is -- not a
    stored output. score() starts from it instead of love-all, and every
    point after it builds on it. Raw point counts, never labels: "40-40" is
    (3, 3) and "Ad-40" is (4, 3), the same numbers the replay keeps
    internally, so a labelled string never has to be parsed back.

    Deliberately three fields. in_tiebreak, finished, the server and the
    service-unit count are all derivable from these plus the rules, and a
    stored copy of any of them could disagree with the numbers it
    describes -- the same reason det_start_ms IS NULL marks a split rally
    rather than a boolean beside it.
    """

    sets: tuple[tuple[int, int], ...]
    games: tuple[int, int]
    points: tuple[int, int]


DEFAULT_RULES = ScoreRules(("Me", "Opp"), 3, True, "at6", 7, None)


def _pair(v, what: str) -> tuple[int, int]:
    if (not isinstance(v, (list, tuple)) or len(v) != 2
            or not all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in v)):
        raise ValueError(f"{what} must be two non-negative integers")
    return (v[0], v[1])


def anchor_from_dict(d: Mapping) -> AnchorState:
    """The JSON column's shape -> anchor, validated. Shape only: whether
    6-4 3-2 is *reachable* under the session's rules is not this
    function's question -- a reviewer anchoring is correcting the record,
    and the record is what they say it is."""
    sets = d.get("sets")
    if not isinstance(sets, (list, tuple)):
        raise ValueError("sets must be a list of pairs")  # noqa: TRY004 -- see rules_from_dict
    return AnchorState(
        sets=tuple(_pair(s, "each set") for s in sets),
        games=_pair(d.get("games"), "games"),
        points=_pair(d.get("points"), "points"),
    )


def anchor_to_dict(a: AnchorState) -> dict:
    return {"sets": [list(s) for s in a.sets], "games": list(a.games), "points": list(a.points)}


@dataclass(frozen=True)
class Anchor:
    """An AnchorState placed in a session: at `at_ms` on `source_id`.

    A time on a source, never a rally id, for the reason reel_items is
    keyed on a span (migration 007): replace_rallies deletes every rally
    for a source on each sweep, so anything keyed to a rally evaporates on
    the first re-segment. A time survives it -- the boundaries around it
    may move, but the footage at that instant does not.
    """

    source_id: str
    at_ms: int
    state: AnchorState


def _place_anchors(ordered: Sequence[Mapping], anchors: Sequence[Anchor]) -> list[tuple]:
    """Where each anchor sits in the session's rally order, as a sortable
    key: (idx, slot, at_ms). Slot 0 is "before this rally", slot 1 "after
    it".

    An anchor belongs to the first rally of its source that ends after it
    -- the rally containing it, or the next one along if it fell in a gap.
    Containing rather than "starting at or after": the UI stamps an anchor
    at a rally's start_ms, and a re-segment that nudges that boundary a few
    hundred milliseconds either way would otherwise hand the anchor to the
    neighbouring rally, silently. Past the source's last rally it sits
    after that rally, so the next source's points build on it. A source
    with no rallies gives it no place in the order at all, and it is
    dropped rather than guessed at.
    """
    placed = []
    for a in anchors:
        first = next((r for r in ordered
                      if r["source_id"] == a.source_id and r["end_ms"] > a.at_ms), None)
        if first is not None:
            placed.append((first["idx"], 0, a.at_ms, a))
            continue
        last = next((r for r in reversed(ordered) if r["source_id"] == a.source_id), None)
        if last is not None:
            placed.append((last["idx"], 1, a.at_ms, a))
    return placed


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
    first_server = d.get("firstServer")
    if first_server not in (None, "a", "b"):
        raise ValueError("firstServer must be a, b or absent")
    return ScoreRules(players, sets, ad, tiebreak, tiebreak_to, first_server)


def rules_to_dict(r: ScoreRules) -> dict:
    return {
        "players": list(r.players),
        "sets": r.sets,
        "ad": r.ad,
        "tiebreak": r.tiebreak,
        "tiebreakTo": r.tiebreak_to,
        "firstServer": r.first_server,
    }


def _server(rules: ScoreRules, units: int, in_tb: bool, pts: list[int]) -> str | None:
    """Who serves the next point, from how many service units are done.

    Serve alternates every game, so the parity of completed games decides
    it. A whole tiebreak counts as one unit, which is not a shortcut: the
    player who would have served the next game serves the tiebreak's first
    point, and treating the tiebreak as that game makes "whoever served
    first in the tiebreak receives first in the next set" (ITF rule 5) fall
    out of plain alternation instead of needing a special case.

    Inside a tiebreak the serve changes after the first point and every two
    after that -- points 1 / 2,3 / 4,5 -- so the point about to be played,
    1-based, has had floor(p / 2) changes before it.
    """
    if rules.first_server is None:
        return None
    flips = units
    if in_tb:
        flips += (pts[0] + pts[1] + 1) // 2
    return ("a", "b")[(0 if rules.first_server == "a" else 1) ^ (flips % 2)]


def _point_labels(pts: list[int], in_tiebreak: bool, ad: bool) -> tuple[str, str]:
    if in_tiebreak:
        return (str(pts[0]), str(pts[1]))
    a, b = pts
    if ad and a >= 3 and b >= 3:
        if a == b:
            return ("40", "40")
        return ("Ad", "40") if a > b else ("40", "Ad")
    return (_POINT_LABELS[min(a, 3)], _POINT_LABELS[min(b, 3)])


def score(
    winners: Sequence[str], rules: ScoreRules, anchor: AnchorState | None = None
) -> ScoreState:
    """Replay `winners` ('a' / 'b' per point) into a scoreboard state,
    from love-all or from `anchor` (see AnchorState).

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
    units = 0   # completed service units: games, plus a whole tiebreak as one

    if anchor is not None:
        sets = list(anchor.sets)
        games = list(anchor.games)
        pts = list(anchor.points)
        # Everything else about the anchored state is read off those three
        # numbers, so the anchor cannot contradict itself. 6-6 under at6 is
        # a tiebreak by definition; every game in a completed set was a
        # service unit (a 7-6 set is twelve games plus the tiebreak, which
        # counted as one -- thirteen either way); and a player holding the
        # sets needed has already won, so nothing after the anchor scores.
        in_tb = rules.tiebreak == "only" or (rules.tiebreak == "at6" and games == [6, 6])
        units = sum(a + b for a, b in sets) + games[0] + games[1]
        for i, p in enumerate(("a", "b")):
            if rules.tiebreak == "only":
                if pts[i] >= rules.tiebreak_to and pts[i] - pts[1 - i] >= 2:
                    finished = p
            elif sum(1 for s in sets if s[i] > s[1 - i]) >= sets_needed:
                finished = p

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
                    units += 1
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
        units += 1
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
        server=None if finished is not None else _server(rules, units, in_tb, pts),
    )


def score_before(
    rallies: Sequence[Mapping], rally_id: str, rules: ScoreRules,
    anchors: Sequence[Anchor] = (),
) -> tuple[ScoreState, int]:
    """The state entering `rally_id`, and how many earlier points carry no
    winner. Replays every non-rejected point with idx below the target's,
    in idx order regardless of the order `rallies` arrived in. An id no row
    holds (an orphaned reel item) replays everything -- the board on such a
    clip is the best information there is, and never a crash.

    With anchors, the replay starts from the newest one at or before the
    target (see _place_anchors) instead of from love-all, and only the
    points after it are replayed. Points the anchor supersedes are not
    counted as unscored either: the reviewer has stated the score, so a
    missing winner before it is no longer a gap in it.
    """
    target = next((r for r in rallies if r["id"] == rally_id), None)
    limit = target["idx"] if target is not None else None
    ordered = sorted(rallies, key=lambda r: r["idx"])

    # (limit, 0) is "before the target itself", which an anchor stamped on
    # the target's own start_ms is; anything placed after it is not.
    placed = [p for p in _place_anchors(ordered, anchors)
              if limit is None or (p[0], p[1]) <= (limit, 0)]
    chosen = max(placed, key=lambda p: p[:3]) if placed else None
    start: AnchorState | None = None
    from_idx = float("-inf")
    if chosen is not None:
        start = chosen[3].state
        from_idx = chosen[0] + chosen[1]   # slot 1 = after that rally

    earlier = [
        r for r in ordered
        if r["idx"] >= from_idx and (limit is None or r["idx"] < limit)
        and not r["rejected"] and r["point"]
    ]
    winners = [r["winner"] for r in earlier if r["winner"]]
    unscored = sum(1 for r in earlier if not r["winner"])
    return score(winners, rules, start), unscored


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
