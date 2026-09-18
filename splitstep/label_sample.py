"""Choosing which windows of a source a human should judge.

The labelling corpus anchors on a span, not a rally (see
`db/migrations/003_rally_labels.sql`), but every route into it goes through a
rally -- so in practice a reviewer can only judge footage the detector already
proposed. That measures precision and boundary error and can never measure
recall, which is exactly the caveat
`docs/superpowers/plans/2026-08-20-camera-viewpoint-validation.md` closes on.

This module picks the windows, half from what the detector flagged and half
from what it ignored, and deliberately declines to say which is which.
"""

import random
from collections.abc import Sequence
from dataclasses import dataclass

# 8 s: the window the 2026-08-21 labelling pass used, kept so its fifteen
# judgements and anything sampled now are the same kind of measurement. Long
# enough to tell a rally from a walk to the fence, short enough that a window
# is usually all one thing -- that pass still caught a serve wind-up falling
# at the very edge of a window, so it is a compromise, not a solved size.
DEFAULT_WINDOW_MS = 8000


@dataclass(frozen=True)
class Window:
    """One span to be judged.

    Two fields, and adding a third is a decision, not a convenience: anything
    here reaches the reviewer through the API, and a field naming which half
    of the sample a window came from would undo the blindness the sample
    exists to have. The 2026-08-20 pass hand-labelled a clip wrong and only
    the tool's blindness exposed it.
    """

    start_ms: int
    end_ms: int


def _tile_gaps(
    duration_ms: int, intervals: Sequence[tuple[int, int]], window_ms: int
) -> list[int]:
    """Start offsets of non-overlapping windows lying entirely in the gaps.

    Tiled rather than sampled continuously: an hour-long source has millions
    of legal offsets, and drawing from a tiling keeps the candidate pool
    small, keeps two draws from landing a millisecond apart on the same play,
    and makes the seeded choice reproducible without storing anything.
    """
    merged: list[list[int]] = []
    for s, e in sorted(intervals):
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])

    starts: list[int] = []
    cursor = 0
    for s, e in merged + [[duration_ms, duration_ms]]:
        gap_start, gap_end = cursor, min(s, duration_ms)
        at = gap_start
        while at + window_ms <= gap_end:
            starts.append(at)
            at += window_ms
        cursor = max(cursor, e)
    return starts


def _flagged_starts(
    duration_ms: int, intervals: Sequence[tuple[int, int]], window_ms: int
) -> list[int]:
    """One window per detector interval, centred on it and clamped inside the
    source. Centred rather than aligned to the interval's start so a window
    lands on the middle of what was proposed -- a rally's first 8 s can be the
    approach to a serve, which judges differently from the rally itself."""
    starts = []
    for s, e in intervals:
        centre = (s + e) // 2
        start = min(max(centre - window_ms // 2, 0), duration_ms - window_ms)
        starts.append(start)
    return starts


def sample_windows(
    *,
    duration_ms: int,
    intervals: Sequence[tuple[int, int]],
    n: int,
    seed: int,
    window_ms: int = DEFAULT_WINDOW_MS,
) -> list[Window]:
    """`n` non-overlapping windows of `window_ms`, drawn half from spans the
    detector proposed and half from spans it ignored, shuffled.

    Deterministic in `seed`, which is what lets a sitting survive a reload
    with no table behind it: the same seed recomputes the same sample.

    Returns fewer than `n` when the source has no room for more. Padding the
    list would put spans nobody chose into a corpus whose entire value is
    that a human chose and judged every row in it.
    """
    if window_ms <= 0:
        raise ValueError("window_ms must be positive")
    if duration_ms < window_ms or n <= 0:
        return []

    rng = random.Random(seed)
    flagged = _flagged_starts(duration_ms, intervals, window_ms)
    ignored = _tile_gaps(duration_ms, intervals, window_ms)
    rng.shuffle(flagged)
    rng.shuffle(ignored)

    chosen: list[int] = []

    def take(pool: list[int], limit: int) -> None:
        while pool and limit > 0:
            start = pool.pop()
            # Every window is the same length, so two of them overlap exactly
            # when their starts sit closer together than one window. A
            # flagged window centred on an interval can land on a tile from
            # the gap beside it; dropping one of the pair is cheaper than
            # letting the corpus hold two judgements of the same footage that
            # neither supersedes (`latest_labels` resolves per exact span).
            if any(abs(start - c) < window_ms for c in chosen):
                continue
            chosen.append(start)
            limit -= 1

    # Alternating rather than "half of one then half of the other": when one
    # pool runs dry the other simply keeps supplying, so a source whose
    # detector found nothing (or found everything) still yields a full sample
    # instead of half of one.
    want_flagged = n // 2
    take(flagged, want_flagged)
    take(ignored, n - len(chosen))
    take(flagged, n - len(chosen))

    rng.shuffle(chosen)
    return [Window(s, s + window_ms) for s in chosen]
