import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from statistics import median

from bootleg.db.rallies import STAR_OVERLAP_MIN, overlap_fraction
from bootleg.detect.segment import Interval

# The same floor replace_rallies uses to carry a star across a re-segment.
# Aliased rather than re-declared so the two can never drift apart.
MATCH_OVERLAP_MIN = STAR_OVERLAP_MIN

# Verdicts that assert play happened. 'unsure' is deliberately absent: two of
# six clips inspected during validation were recorded as "walking, racket
# down, no ball visible -- stills cannot settle it", and forcing those into
# either column would inject noise while looking like data.
PLAY_VERDICTS = ("clean", "partly")


@dataclass(frozen=True)
class LabelRow:
    """One human judgement, decoupled from sqlite so the scorer is pure."""

    span_start_ms: int
    span_end_ms: int
    verdict: str | None
    true_start_ms: int | None
    true_end_ms: int | None


def rows_to_labels(rows: Iterable[sqlite3.Row]) -> list[LabelRow]:
    return [
        LabelRow(
            span_start_ms=r["span_start_ms"],
            span_end_ms=r["span_end_ms"],
            verdict=r["verdict"],
            true_start_ms=r["true_start_ms"],
            true_end_ms=r["true_end_ms"],
        )
        for r in rows
    ]


@dataclass(frozen=True)
class LabelScore:
    matched_play: int
    matched_not_play: int
    unknown: int
    missed_clean: int
    labelled_clean: int
    boundary_n: int
    start_bias_ms: float | None
    end_bias_ms: float | None
    start_mae_ms: float | None
    end_mae_ms: float | None

    @property
    def precision(self) -> float | None:
        decided = self.matched_play + self.matched_not_play
        return None if decided == 0 else self.matched_play / decided

    @property
    def span_recall(self) -> float | None:
        """Recall over labelled spans ONLY.

        This can never see play the detector did not propose, because every
        label in the corpus attaches to a span it did. The validation set was
        built the other way on purpose -- six of its fifteen windows are spans
        the detector ignored, two of which contain play. Anything rendering
        this number must name it "span recall (labelled spans only)"; letting
        a metric imply coverage it does not have is the error that cost the
        last round.
        """
        if self.labelled_clean == 0:
            return None
        return (self.labelled_clean - self.missed_clean) / self.labelled_clean


def _best_match(iv: Interval, labels: list[LabelRow]) -> int | None:
    """Index of the labelled span this candidate best corresponds to, or None.

    Returns an index rather than the row itself so the caller can record which
    labels were hit. LabelRow is a frozen dataclass and compares by value, so
    two identical rows would be indistinguishable by identity or equality --
    position is the only stable handle.
    """
    best: int | None = None
    best_frac = 0.0
    for i, lab in enumerate(labels):
        frac = overlap_fraction(iv.start_ms, iv.end_ms, lab.span_start_ms, lab.span_end_ms)
        if frac >= MATCH_OVERLAP_MIN and frac > best_frac:
            best, best_frac = i, frac
    return best


def score_against_labels(intervals: list[Interval], labels: list[LabelRow]) -> LabelScore:
    """Score one candidate segmentation against the human corpus.

    Matching is by >50% overlap, not exact equality: a different threshold
    moves every edge, so an exact match would report zero on a segmentation
    that is obviously the same set of rallies. (The read path in the API is
    the opposite -- it matches exactly, because "have I judged this exact
    detector output before" has no approximate answer.)
    """
    matched_play = matched_not_play = unknown = 0
    start_errs: list[int] = []
    end_errs: list[int] = []
    hit: set[int] = set()

    for iv in intervals:
        i = _best_match(iv, labels)
        if i is None:
            unknown += 1
            continue
        hit.add(i)
        lab = labels[i]
        if lab.verdict in PLAY_VERDICTS:
            matched_play += 1
        elif lab.verdict == "not_play":
            matched_not_play += 1
        # 'unsure' and a verdict-less boundary row fall through: neither is a
        # precision hit nor a miss, but both may still carry a corrected span.
        # 'not_play' is excluded here even though it too may carry true_*: a
        # span the human said contains no play has no correct boundary to be
        # wrong about, so its dragged edges cannot feed a metric that claims
        # to measure boundary accuracy. This is not the same exclusion as
        # 'unsure' above -- an undecidable clip may still have a real,
        # correctly-measured edge, only the play/no-play call could not be
        # made -- so 'unsure' keeps contributing here unchanged. Letting
        # 'not_play' in was Finding 2: start_bias_ms/end_mae_ms would then be
        # describing something other than what their names say, the exact
        # failure this branch exists to eliminate (CLAUDE.md, "Metric
        # honesty is the point of the codebase").
        has_correction = lab.true_start_ms is not None and lab.true_end_ms is not None
        if lab.verdict != "not_play" and has_correction:
            # Signed, and positive means the candidate opens/closes AFTER the
            # human's edge.
            start_errs.append(iv.start_ms - lab.true_start_ms)
            end_errs.append(iv.end_ms - lab.true_end_ms)

    clean = [i for i, lab in enumerate(labels) if lab.verdict == "clean"]
    missed_clean = sum(1 for i in clean if i not in hit)

    return LabelScore(
        matched_play=matched_play,
        matched_not_play=matched_not_play,
        unknown=unknown,
        missed_clean=missed_clean,
        labelled_clean=len(clean),
        boundary_n=len(start_errs),
        start_bias_ms=median(start_errs) if start_errs else None,
        end_bias_ms=median(end_errs) if end_errs else None,
        # Reported alongside the bias, never instead of it: equal and opposite
        # errors cancel in a median, so a detector with 400 ms of scatter in
        # both directions reads as perfect on bias alone.
        start_mae_ms=median(abs(e) for e in start_errs) if start_errs else None,
        end_mae_ms=median(abs(e) for e in end_errs) if end_errs else None,
    )
