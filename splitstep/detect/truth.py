"""Hand-listed truth for a source, and the scoring report measured against it.

Every fence-mode number is computed here, so the matching rule is the thing
to keep stable: a truth point is *caught* when detected clips cover at least
half of it. The metrics are named in full ("points caught", "clip time that
is play") and never as plain "recall"/"precision" — a fixed-window hand list
makes both words ambiguous, and a number that implies coverage it lacks is
what cost an earlier round.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from splitstep.detect.segment import Interval

_COVERS = re.compile(r"^#\s*covers\s+(\S+)-(\S+)\s*$")
_FAULT_PREFIX = "missed first serve"


@dataclass(frozen=True)
class TruthPoint:
    start_ms: int
    end_ms: int
    note: str
    server: str


@dataclass(frozen=True)
class Truth:
    covers: tuple[int, int]
    points: tuple[TruthPoint, ...]


@dataclass(frozen=True)
class TruthReport:
    caught: int
    total: int
    play_share: float
    median_start_err_s: float
    median_end_err_s: float
    missed: tuple[int, ...]


def _parse_time(text: str) -> int:
    # Every field is checked, not just split: these files are hand-typed, and
    # `int()` alone reads `1:75` as 2:15 and `0:-5` as a time, both of which
    # would land a point somewhere nobody watched.
    parts = text.strip().split(":")
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        raise ValueError(f"bad time {text!r} (want m:ss or h:mm:ss)")
    nums = [int(p) for p in parts]
    if len(nums) == 2:
        nums.insert(0, 0)
    h, m, s = nums
    if s >= 60 or (len(parts) == 3 and m >= 60):
        raise ValueError(f"bad time {text!r}: a field past 59")
    return ((h * 60 + m) * 60 + s) * 1000


def load_truth(path: Path) -> Truth:
    """Read a hand-listed truth file, refusing anything a typo could produce.

    Every row problem raises `ValueError("<path>:<line>: <what>")`, because
    the alternative was worse than a crash: an inverted row was counted as a
    missed point, an overlapping pair counted the shared time twice in "clip
    time that is play", a zero-length row divided by zero in `evaluate`, and
    a row outside `# covers` was scored against time the file does not claim.
    Each of those reads as a detector result, not a typo. Rows must be in
    order and may touch (a fault ending the second the next serve starts)
    but never overlap.
    """
    path = Path(path)
    with open(path, newline="") as f:
        lines = f.read().splitlines()
    first = lines[0] if lines else ""
    m = _COVERS.match(first.strip())
    if not m:
        # Time outside the covered range is neither play nor idle; without
        # the range there is no way to know what the file does not claim.
        raise ValueError(f"{path}: first line must be '# covers m:ss-m:ss'")
    try:
        covers = (_parse_time(m.group(1)), _parse_time(m.group(2)))
    except ValueError as e:
        raise ValueError(f"{path}:1: {e}") from None
    if covers[1] <= covers[0]:
        raise ValueError(f"{path}:1: covers ends at or before it starts")

    reader = csv.reader(lines[1:], delimiter="\t")
    header = next(reader, [])
    if "in" not in header or "out" not in header:
        raise ValueError(f"{path}:2: header must name 'in' and 'out' columns")

    points: list[TruthPoint] = []
    # Line numbers from the file, not the reader: csv's own `line_num` counts
    # from wherever it was handed the stream, which is the off-by-one a
    # hand-editor hunting for a typo does not need.
    for lineno, cells in enumerate(reader, start=3):
        r = dict(zip(header, cells, strict=False))
        if not (r.get("in") or "").strip():
            continue
        where = f"{path}:{lineno}"
        try:
            a, b = _parse_time(r["in"]), _parse_time(r.get("out") or "")
        except (KeyError, ValueError) as e:
            raise ValueError(f"{where}: {e}") from None
        if b <= a:
            raise ValueError(f"{where}: out {r['out']} is not after in {r['in']}")
        if a < covers[0] or b > covers[1]:
            raise ValueError(f"{where}: {r['in']}-{r['out']} is outside '# covers'")
        if points and a < points[-1].end_ms:
            raise ValueError(
                f"{where}: starts at {r['in']}, before the previous row ends "
                "(rows must be sorted by in and must not overlap)"
            )
        points.append(
            TruthPoint(a, b, (r.get("note") or "").strip(), (r.get("server") or "").strip())
        )
    return Truth(covers=covers, points=tuple(points))


def fold_faults(truth: Truth) -> Truth:
    """Merge each `missed first serve` row into the point that follows it.

    A first-serve fault is dead time the detector has no reason to split from
    the second serve, so the folded view is the fairer one to score against.
    """
    out: list[TruthPoint] = []
    pts = truth.points
    i = 0
    while i < len(pts):
        p = pts[i]
        if p.note.startswith(_FAULT_PREFIX) and i + 1 < len(pts):
            nxt = pts[i + 1]
            out.append(TruthPoint(p.start_ms, nxt.end_ms, nxt.note, nxt.server))
            i += 2
        else:
            out.append(p)
            i += 1
    return Truth(covers=truth.covers, points=tuple(out))


def _median(xs: list[float]) -> float:
    return sorted(xs)[len(xs) // 2] if xs else float("nan")


def evaluate(intervals: Sequence[Interval], truth: Truth) -> TruthReport:
    lo, hi = truth.covers
    iv = [
        (max(i.start_ms, lo), min(i.end_ms, hi))
        for i in intervals
        if min(i.end_ms, hi) > max(i.start_ms, lo)
    ]
    starts: list[float] = []
    ends: list[float] = []
    missed: list[int] = []
    caught = 0
    for p in truth.points:
        a, b = p.start_ms, p.end_ms
        ov = [(s, e) for s, e in iv if min(b, e) - max(a, s) > 0]
        cov = sum(min(b, e) - max(a, s) for s, e in ov) / (b - a)
        if cov >= 0.5:
            caught += 1
            starts.append((min(s for s, _ in ov) - a) / 1000)
            ends.append((max(e for _, e in ov) - b) / 1000)
        else:
            missed.append(a)
    total_time = sum(e - s for s, e in iv)
    in_play = sum(
        max(0, min(p.end_ms, e) - max(p.start_ms, s)) for s, e in iv for p in truth.points
    )
    return TruthReport(
        caught=caught,
        total=len(truth.points),
        play_share=in_play / total_time if total_time else 0.0,
        median_start_err_s=_median(starts),
        median_end_err_s=_median(ends),
        missed=tuple(missed),
    )


def format_report(name: str, label: str, report: TruthReport) -> str:
    missed = " ".join(f"{m // 60000}:{m // 1000 % 60:02d}" for m in report.missed)
    return (
        f"{name} {label}: points caught {report.caught}/{report.total}, "
        f"clip time that is play {report.play_share:.0%}, "
        f"median start error {report.median_start_err_s:+.1f}s, "
        f"median end error {report.median_end_err_s:+.1f}s, "
        f"missed {missed or 'none'}"
    )
