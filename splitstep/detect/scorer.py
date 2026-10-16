"""Which scorer cuts a source, and the one call every segmenting caller makes.

The detect handler, POST /resegment, GET /scores, `splitstep segment` and
`splitstep labels score` all go through `segment_source`, for the reason
`params_for_frames` gave when it was the single entry point: HTTP and
terminal must not drift on which model a source gets. The choice is now
made from the reviewer's own answers (mount, play format) first and from
the footage only when those say nothing -- see the spec's "Which scorer
runs" table (docs/superpowers/specs/2026-10-16-fence-mode-design.md).

A source whose mount is '' takes the `auto` branch, which is
`params_for_frames` + `segment` exactly as before: every library that
predates fence mode must cut byte-for-byte the same rallies.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from splitstep import resources
from splitstep.detect.boxes import read_boxes
from splitstep.detect.court import Court
from splitstep.detect.features import read_features
from splitstep.detect.fence import FenceModel, fence_curve, fence_probs, fence_segment
from splitstep.detect.segment import (
    SUBJECT_CLOSE_GAP_S,
    SUBJECT_THRESHOLD,
    Interval,
    SegmentParams,
    params_for_frames,
    sample_interval_ms,
    score_series,
    segment,
)
from splitstep.detect.viewpoint import analyze_view

log = logging.getLogger(__name__)

Choice = Literal["fence", "subject", "auto"]


class FenceUnavailable(Exception):
    """A source set to Fence + Singles that fence mode cannot cut yet.

    The message is a sentence for the reviewer, naming what to do: routes
    return it as a 409 and the detect job fails with it.
    """


@dataclass(frozen=True)
class Segmentation:
    intervals: list[Interval]
    # Always the threshold actually cut at, never the caller's argument: a
    # default run passes None and only the scorer resolves it (migration 015).
    threshold: float
    # The concrete scorer used -- "fence", "subject" or "pair" -- never
    # "auto", because detected_scorer must say what cut the rallies on screen.
    scorer: str
    step_ms: int
    # The curve the threshold is compared against, on that scorer's scale.
    scores: list[float]


def resolve_scorer(mount: str, play_format: str) -> Choice:
    """The spec's table. Fence with any format but singles stays on auto
    because the fence model was fitted on singles only; Ground forces subject
    because a loose quad admitting neighbours used to push ground sources
    into pair."""
    if mount == "fence" and play_format == "singles":
        return "fence"
    if mount == "ground":
        return "subject"
    return "auto"


def segment_source(
    source: Mapping, src_dir: Path, *, threshold: float | None = None
) -> Segmentation:
    choice = resolve_scorer(source["mount"], source["play_format"])
    if choice == "fence":
        return _fence(source, src_dir, threshold)

    frames = read_features(src_dir / "features.jsonl")
    if choice == "subject":
        # What params_for_frames builds when analyze_view says subject, with
        # the profile taken from the reviewer's answer instead of the guess.
        # analyze_view still supplies subject_min_h: that gate is measured
        # from this source's own near-player height either way.
        params = SegmentParams(
            profile="subject",
            subject_min_h=analyze_view(frames).subject_min_h,
            threshold=SUBJECT_THRESHOLD if threshold is None else threshold,
            close_gap_s=SUBJECT_CLOSE_GAP_S,
        )
        log.info("segmenting with profile=subject (forced by mount=ground) subject_min_h=%.4f",
                 params.subject_min_h)
    else:
        params = params_for_frames(frames, threshold=threshold)
    return Segmentation(
        intervals=segment(frames, params),
        threshold=params.threshold,
        scorer=params.profile,
        step_ms=sample_interval_ms(frames),
        scores=score_series(frames, params),
    )


def _fence(source: Mapping, src_dir: Path, threshold: float | None) -> Segmentation:
    boxes = src_dir / "boxes.jsonl"
    # Checked before the corners: drawing corners on a source with no boxes
    # still leaves it uncuttable, so the detect is the step to name first.
    if not boxes.exists():
        raise FenceUnavailable(
            "This source was detected before fence mode existed, so it has no "
            "player boxes yet; re-run detection to use fence mode."
        )
    if not source["court_corners"]:
        raise FenceUnavailable(
            "Fence mode needs this source's court; draw the court corners, "
            "then re-segment."
        )
    model = FenceModel.load(resources.fence_model())
    frames = read_boxes(boxes)
    hits = [f.hits for f in read_features(src_dir / "features.jsonl")]
    court = Court.from_json(source["court_corners"])
    probs = fence_probs(frames, hits, court, model)
    t_ms = [t for t, _ in frames]
    step, curve = fence_curve(t_ms, probs, model)
    return Segmentation(
        intervals=fence_segment(t_ms, probs, model, threshold=threshold),
        threshold=model.threshold if threshold is None else threshold,
        scorer="fence",
        step_ms=round(step),
        scores=[float(s) for s in curve],
    )
