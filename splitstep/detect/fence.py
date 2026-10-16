"""Fence mode: every box + audio hits + court corners -> play probability -> clips.

The 2026-10-16 measurement (spec, "What was measured") found the hand-tuned
pair/subject scores barely better than chance on a fence-mounted camera,
while a logistic regression over rolling-window features of the same raw
inputs reached 0.86 held-out AUC. This module is that model in production
form, kept as three pure pieces so a re-fit or a threshold sweep never needs
a GPU:

- `fence_features`: boxes.jsonl frames + per-frame audio hits + the court ->
  one finite row per frame.
- `FenceModel`: standardise, dot, sigmoid. Its JSON file (fitted by the
  training step, not here) names the features it was fitted on, and `load`
  refuses one that disagrees with what `fence_features` produces.
- `fence_segment`: smoothed probabilities -> intervals, with a first-serve
  fault folded into the point that follows it.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from splitstep.detect.boxes import BALL, PERSON, Detection
from splitstep.detect.court import Court
from splitstep.detect.segment import Interval

# Rolling windows in frames (200 ms each: 1, 2, 4 and 8 s), the spike's set.
WINDOWS = (5, 10, 20, 40)
PRESENCE_WINDOW = 10

# Person boxes below this confidence never become a player. boxes.jsonl keeps
# everything down to 0.1 so this choice can move without re-detecting; 0.25
# is the spike's near-player cut and ultralytics' old default.
PLAYER_CONF = 0.25

# A box whose bottom edge sits on the frame's bottom edge has no foot in
# frame: its y2 is where the picture stops, not where the person stands, so
# its court position (and every speed derived from it) is fiction. On the
# 2026-10-12 fixture these are almost all someone right in front of the
# camera between points; dropping them lifts the 8 s near-speed AUC from
# 0.65 to 0.69 (most of the gap between the court-zone tracker and the
# spike's hand-drawn image band) and the combined model's held-out AUC from
# 0.84 to 0.875.
CLIPPED_FOOT_Y = 0.98

# Body-lengths per second. A real player tops out well below this at 5 fps;
# anything faster is a track swapping identity, and one swap must not
# outweigh a minute of honest motion in a window mean.
SPEED_CAP = 3.0

# Box aspect (h / w) is clipped so a degenerate sliver box cannot put a huge
# value into a rolling std.
ASPECT_CAP = 10.0

# Below this a box height is not a body length to divide by.
_MIN_H = 1e-3


def _names() -> tuple[str, ...]:
    names: list[str] = []
    for w in WINDOWS:
        names += [
            f"near_speed_{w}", f"far_speed_{w}", f"near_lateral_{w}", f"far_lateral_{w}",
            f"near_top_sd_{w}", f"near_aspect_sd_{w}",
            f"hits_centred_{w}", f"hits_trailing_{w}", f"hits_leading_{w}",
            f"balls_{w}",
        ]
    names += ["near_present", "far_present"]
    return tuple(n for n in names if n not in _DROPPED)


# Fitted to |weight| < 0.05 on 2026-10-12 (L2 logistic, refit after each
# drop): removing them left held-out AUC at 0.875 (4-minute blocks), so they
# were only noise for the next session's fit to latch onto.
_DROPPED = frozenset({
    "far_lateral_5", "far_lateral_10", "near_aspect_sd_5", "near_aspect_sd_20",
    "hits_centred_5", "hits_centred_20", "balls_5",
})


# What `fence_features` produces, in column order. A model file must name
# exactly these, or its weights would land on the wrong columns.
FEATURE_NAMES: tuple[str, ...] = _names()


def _track(frames: Sequence[tuple[int, list[Detection]]], court: Court, zone: str):
    """One player per frame: the largest box in `zone` to start, then the
    candidate nearest the last one seen.

    Nearest-neighbour rather than largest-every-frame because the largest box
    in a half flips between the player and whoever walks behind them; the
    last position persists across empty frames so a player who drops out for
    a second is picked back up rather than replaced by the largest stranger.
    """
    out: list[Detection | None] = []
    prev: Detection | None = None
    for _, dets in frames:
        cands = [
            d for d in dets
            if d.cls == PERSON and d.conf >= PLAYER_CONF and d.h > _MIN_H
            and d.foot < CLIPPED_FOOT_Y and court.zone(d.cx, d.foot) == zone
        ]
        if not cands:
            out.append(None)
            continue
        if prev is None:
            pick = max(cands, key=lambda d: d.h)
        else:
            p = prev
            pick = min(cands, key=lambda d: abs(d.cx - p.cx) + abs(d.foot - p.foot))
        out.append(pick)
        prev = pick
    return out


def _speeds(track, t_s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Total and lateral speed in body-lengths/s, 0 where either frame is empty.

    Body-lengths of image motion rather than court metres from `court_xy`:
    on the 2026-10-12 fixture metres separated play slightly *worse* (4 s
    near-speed AUC 0.66 vs 0.69; combined held-out AUC 0.86 vs 0.875). The
    far end of the homography magnifies a few pixels of box jitter into
    metres, while dividing by box height already cancels most of the
    perspective.
    """
    n = len(track)
    total = np.zeros(n)
    lateral = np.zeros(n)
    for i in range(1, n):
        a, b = track[i - 1], track[i]
        dt = t_s[i] - t_s[i - 1]
        if a is None or b is None or not dt > 0:
            continue
        dx = (b.cx - a.cx) / b.h
        dy = (b.foot - a.foot) / b.h
        total[i] = min(math.hypot(dx, dy) / dt, SPEED_CAP)
        lateral[i] = min(abs(dx) / dt, SPEED_CAP)
    return total, lateral


def _centred(x: np.ndarray, w: int) -> np.ndarray:
    # np.convolve's "same" mode returns max(len(x), w) elements, so a source
    # shorter than the window (under 8 s at the widest) would come back the
    # wrong length. Slicing "full" at the offset "same" uses gives exactly
    # len(x) elements and the same values wherever the window fits, so the
    # 2026-10-12 measurements are unchanged.
    lo = (w - 1) // 2
    return np.convolve(x, np.ones(w) / w, "full")[lo:lo + len(x)]


def _trailing(x: np.ndarray, w: int) -> np.ndarray:
    # Mean over the frame and the w-1 before it; the first frames average
    # over what exists rather than over zeros that were never measured.
    c = np.concatenate(([0.0], np.cumsum(x)))
    i = np.arange(len(x))
    lo = np.maximum(0, i - w + 1)
    return (c[i + 1] - c[lo]) / (i + 1 - lo)


def _leading(x: np.ndarray, w: int) -> np.ndarray:
    # The strongest single signal measured (0.83 AUC for 4-8 s): a point
    # starts at the toss, seconds before the first impact the audio hears.
    return _trailing(x[::-1], w)[::-1]


def _rolling_sd(x: np.ndarray, w: int) -> np.ndarray:
    m = _centred(x, w)
    return np.sqrt(np.maximum(_centred(x * x, w) - m * m, 0.0))


def fence_features(
    frames: Sequence[tuple[int, list[Detection]]],
    hits: Sequence[int],
    court: Court,
) -> tuple[np.ndarray, tuple[str, ...]]:
    """One row per frame of `frames`, columns `FEATURE_NAMES`, finite everywhere.

    `hits` is features.jsonl's per-frame audio impact count, aligned 1:1 with
    `frames`. All-zero hits (a silent or audio-less source) is a valid input.
    """
    n = len(frames)
    if len(hits) != n:
        raise ValueError(
            f"Got {n} box frames but {len(hits)} audio-hit frames; "
            "boxes.jsonl and features.jsonl must come from the same detect run."
        )
    if n == 0:
        return np.zeros((0, len(FEATURE_NAMES))), FEATURE_NAMES

    t_s = np.array([t for t, _ in frames], dtype=float) / 1000.0
    near = _track(frames, court, "near")
    far = _track(frames, court, "far")
    near_v, near_lat = _speeds(near, t_s)
    far_v, far_lat = _speeds(far, t_s)

    # Missing frames read as 0 in the box-shape series, as in the spike: the
    # jump to and from 0 is part of what the std measures there, and the
    # fitted weights were learned with it.
    top = np.array([d.top if d else 0.0 for d in near])
    aspect = np.array([min(d.h / max(d.w, _MIN_H), ASPECT_CAP) if d else 0.0 for d in near])
    balls = np.array([sum(1 for d in dets if d.cls == BALL) for _, dets in frames], dtype=float)
    h = np.nan_to_num(np.asarray(hits, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)

    cols: dict[str, np.ndarray] = {}
    for w in WINDOWS:
        cols[f"near_speed_{w}"] = _centred(near_v, w)
        cols[f"far_speed_{w}"] = _centred(far_v, w)
        cols[f"near_lateral_{w}"] = _centred(near_lat, w)
        cols[f"far_lateral_{w}"] = _centred(far_lat, w)
        cols[f"near_top_sd_{w}"] = _rolling_sd(top, w)
        cols[f"near_aspect_sd_{w}"] = _rolling_sd(aspect, w)
        cols[f"hits_centred_{w}"] = _centred(h, w)
        cols[f"hits_trailing_{w}"] = _trailing(h, w)
        cols[f"hits_leading_{w}"] = _leading(h, w)
        cols[f"balls_{w}"] = _centred(balls, w)
    cols["near_present"] = _centred(np.array([d is not None for d in near], float),
                                    PRESENCE_WINDOW)
    cols["far_present"] = _centred(np.array([d is not None for d in far], float),
                                   PRESENCE_WINDOW)

    x = np.column_stack([cols[k] for k in FEATURE_NAMES])
    # Belt and braces: every input above is already bounded, but a NaN that
    # slipped through would turn one frame's probability into NaN and the
    # segmenter's comparisons silently false.
    return np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0), FEATURE_NAMES


# Segmentation and fold defaults, measured on 2026-10-12 (spec, "What was
# measured", 2026-10-16): smooth 1 s, threshold 0.2, close 1 s gaps, drop
# < 2 s, pad 0.5 s / 0.7 s caught 34/38 points with 53% of clip time play;
# folding "a clip <= 6 s followed by another within 15 s" turned that into
# 34 clips for 23 points at 67% play. A model file carries its own copy, so
# these are only what a freshly fitted model starts from.
DEFAULT_THRESHOLD = 0.2
DEFAULT_SMOOTH_S = 1.0
DEFAULT_CLOSE_GAP_S = 1.0
DEFAULT_MIN_DURATION_S = 2.0
DEFAULT_PAD_START_S = 0.5
DEFAULT_PAD_END_S = 0.7
DEFAULT_FOLD_MAX_S = 6.0
DEFAULT_FOLD_WITHIN_S = 15.0


@dataclass(frozen=True)
class FenceModel:
    features: tuple[str, ...]
    mean: tuple[float, ...]
    std: tuple[float, ...]
    weights: tuple[float, ...]
    bias: float
    threshold: float = DEFAULT_THRESHOLD
    smooth_s: float = DEFAULT_SMOOTH_S
    close_gap_s: float = DEFAULT_CLOSE_GAP_S
    min_duration_s: float = DEFAULT_MIN_DURATION_S
    pad_start_s: float = DEFAULT_PAD_START_S
    pad_end_s: float = DEFAULT_PAD_END_S
    fold_max_s: float = DEFAULT_FOLD_MAX_S
    fold_within_s: float = DEFAULT_FOLD_WITHIN_S
    trained_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        k = len(self.features)
        if not (len(self.mean) == len(self.std) == len(self.weights) == k):
            raise ValueError(
                f"Fence model has {k} features but {len(self.mean)} means, "
                f"{len(self.std)} stds and {len(self.weights)} weights."
            )

    @classmethod
    def load(cls, path: Path) -> FenceModel:
        d = json.loads(Path(path).read_text())
        for key in ("features", "mean", "std", "weights", "trained_on"):
            if key in d:
                d[key] = tuple(d[key])
        model = cls(**d)
        if model.features != FEATURE_NAMES:
            missing = [f for f in FEATURE_NAMES if f not in model.features]
            extra = [f for f in model.features if f not in FEATURE_NAMES]
            detail = []
            if missing:
                detail.append(f"missing {', '.join(missing)}")
            if extra:
                detail.append(f"unknown {', '.join(extra)}")
            raise ValueError(
                f"{path} was fitted on different features than fence mode computes "
                f"({'; '.join(detail) or 'same names in a different order'}); "
                "re-fit the model."
            )
        return model

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=1) + "\n")

    def predict(self, x: np.ndarray) -> np.ndarray:
        """Play probability per row of `x` (columns in `self.features` order)."""
        std = np.asarray(self.std, dtype=float)
        # A feature constant over the training set has std 0; it carries no
        # information, so it is centred and left unscaled rather than divided
        # into infinity.
        std = np.where(std > 0, std, 1.0)
        z = (np.asarray(x, dtype=float) - np.asarray(self.mean)) / std
        logit = z @ np.asarray(self.weights, dtype=float) + self.bias
        # Clipped so exp cannot overflow (which would also be a warning).
        return 1.0 / (1.0 + np.exp(-np.clip(logit, -50.0, 50.0)))


def fence_probs(
    frames: Sequence[tuple[int, list[Detection]]],
    hits: Sequence[int],
    court: Court,
    model: FenceModel,
) -> np.ndarray:
    x, names = fence_features(frames, hits, court)
    if names != model.features:
        raise ValueError("The fence model was fitted on different features; re-fit it.")
    # No player tracked on either half of our court for the whole presence
    # window (2 s) is not play, whatever the audio says. The model alone does
    # not learn this: with every box blanked for a minute of 2026-10-12 it
    # still put probabilities up to 0.92 there from the neighbouring courts'
    # impacts and the 8 s windows, and cut clips out of a covered lens.
    present = (x[:, names.index("near_present")] > 0) | (x[:, names.index("far_present")] > 0)
    return np.where(present, model.predict(x), 0.0)


def _frame_ms(t_ms: np.ndarray) -> float:
    d = np.diff(t_ms)
    d = d[d > 0]
    return float(np.median(d)) if len(d) else 200.0


def _join(a: list[float], b: list[float]) -> list[float]:
    """One clip from a's start to b's end, confidence weighted by duration."""
    da, db = a[1] - a[0], b[1] - b[0]
    conf = (a[2] * da + b[2] * db) / max(da + db, 1.0)
    return [a[0], max(a[1], b[1]), conf]


def _fold(clips: list[list[float]], max_ms: float, within_ms: float) -> list[list[float]]:
    """Merge a short clip into the clip after it, keeping the short one's start.

    A first-serve fault is a toss, a serve and a ball in the net: a short
    burst of play the model rightly finds, followed by a second serve a few
    seconds later. As two clips the fault is a reel entry nobody wants and
    the point's own clip starts mid-way through the server's routine.
    Measured on 2026-10-12 (spec): 34 clips for 23 points at 67% play once
    folded. "Within" is the gap from the short clip's end to the next one's
    start, both as padded. A merged clip is longer than `max_ms`, so a fold
    never chains.
    """
    out: list[list[float]] = []
    for c in clips:
        if out:
            p = out[-1]
            if p[1] - p[0] <= max_ms and c[0] - p[1] <= within_ms:
                out[-1] = _join(p, c)
                continue
        out.append(list(c))
    return out


def fence_curve(
    t_ms: Sequence[int], probs: np.ndarray, model: FenceModel
) -> tuple[float, np.ndarray]:
    """(frame step in ms, smoothed probabilities): the curve the threshold cuts.

    Public so the score graph can draw exactly what `fence_segment` compares
    against the threshold -- the raw probabilities flicker across a line the
    segmenter never sees them cross.
    """
    t = np.asarray(t_ms, dtype=float)
    p = np.nan_to_num(np.asarray(probs, dtype=float), nan=0.0)
    if len(t) != len(p):
        raise ValueError(f"Got {len(t)} timestamps but {len(p)} probabilities.")
    if len(t) == 0:
        return 200.0, p
    step = _frame_ms(t)
    w = max(1, round(model.smooth_s * 1000 / step))
    return step, _centred(p, w)


def fence_segment(
    t_ms: Sequence[int],
    probs: np.ndarray,
    model: FenceModel,
    threshold: float | None = None,
) -> list[Interval]:
    """Smooth, threshold, close gaps, drop short runs, pad, then fold faults."""
    t = np.asarray(t_ms, dtype=float)
    step, smooth = fence_curve(t_ms, probs, model)
    if len(t) == 0:
        return []
    th = model.threshold if threshold is None else threshold
    on = smooth >= th

    # Runs of frames over threshold, as [start_ms, end_ms, conf]; a frame
    # covers [t, t + step).
    runs: list[list[float]] = []
    i, n = 0, len(on)
    while i < n:
        if not on[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and on[j + 1]:
            j += 1
        runs.append([t[i], t[j] + step, float(smooth[i:j + 1].mean())])
        i = j + 1

    closed: list[list[float]] = []
    for r in runs:
        if closed and r[0] - closed[-1][1] <= model.close_gap_s * 1000:
            closed[-1] = _join(closed[-1], r)
        else:
            closed.append(r)

    kept = [r for r in closed if r[1] - r[0] >= model.min_duration_s * 1000]

    lo, hi = t[0], t[-1] + step
    padded: list[list[float]] = []
    for s, e, c in kept:
        clip = [max(lo, s - model.pad_start_s * 1000), min(hi, e + model.pad_end_s * 1000), c]
        if padded and clip[0] <= padded[-1][1]:
            # Padding can make neighbours touch; one clip, not two overlapping.
            padded[-1] = _join(padded[-1], clip)
        else:
            padded.append(clip)

    folded = _fold(padded, model.fold_max_s * 1000, model.fold_within_s * 1000)
    return [Interval(round(float(s)), round(float(e)), round(float(c), 4)) for s, e, c in folded]
