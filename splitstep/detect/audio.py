import statistics
import subprocess
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfilt

ENVELOPE_MS = 10
ADAPTIVE_WINDOW_S = 2.0


@dataclass(frozen=True)
class Hit:
    t_ms: int
    strength: float


def extract_pcm(path: Path, sr: int = 22050) -> np.ndarray:
    """Decode a file's audio to mono float32 in [-1, 1]."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn",
         "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"audio extraction failed: {proc.stderr.decode().strip()}")
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def detect_hits(
    samples: np.ndarray,
    sr: int,
    *,
    highpass_hz: float = 800.0,
    k: float = 4.0,
    min_gap_ms: int = 120,
    peaky_min: float = 3.0,
    prom_frac: float = 0.30,
) -> list[Hit]:
    if samples.size < sr // 10:
        return []

    sos = butter(4, highpass_hz, btype="highpass", fs=sr, output="sos")
    filtered = sosfilt(sos, samples)

    hop = max(1, int(sr * ENVELOPE_MS / 1000))
    n = filtered.size // hop
    if n < 3:
        return []
    env = np.sqrt(np.mean(filtered[: n * hop].reshape(n, hop) ** 2, axis=1))

    med = float(np.median(env))
    p999 = float(np.percentile(env, 99.9))

    # Gate 1 -- does this signal contain transients at all?
    # Flat noise has a light tail (p99.9/median ~ 1.2); real impacts ~ 11.6.
    if med <= 0 or p999 / med < peaky_min:
        return []

    # Gate 2 -- self-scaling prominence floor. A strike must be loud
    # relative to the whole recording, not merely to its local neighbours.
    prominence_floor = prom_frac * (p999 - med)

    win = max(3, int(ADAPTIVE_WINDOW_S * 1000 / ENVELOPE_MS))
    pad = win // 2
    padded = np.pad(env, pad, mode="reflect")
    baseline = np.empty(n)
    spread = np.empty(n)
    for i in range(n):
        chunk = padded[i : i + win]
        local_med = np.median(chunk)
        baseline[i] = local_med
        spread[i] = np.median(np.abs(chunk - local_med))

    threshold = baseline + k * np.maximum(spread, 1e-6)

    hits: list[Hit] = []
    min_gap_frames = max(1, int(min_gap_ms / ENVELOPE_MS))
    last = -min_gap_frames
    for i in range(1, n - 1):
        prominence = env[i] - baseline[i]
        if env[i] < threshold[i] or prominence < prominence_floor:
            continue
        if env[i] < env[i - 1] or env[i] < env[i + 1]:
            continue
        if i - last < min_gap_frames:
            continue
        hits.append(Hit(t_ms=i * ENVELOPE_MS, strength=float(prominence)))
        last = i
    return hits


def hits_to_grid(
    hits: list[Hit],
    duration_ms: int,
    step_ms: int = 200,
) -> list[tuple[int, float]]:
    """Per grid step: (impacts in the trailing 1 s, regularity of the last 4 gaps)."""
    steps = max(0, duration_ms // step_ms)
    times = [h.t_ms for h in hits]
    out: list[tuple[int, float]] = []

    for s in range(steps):
        t = s * step_ms
        window = [x for x in times if t - 1000 <= x <= t]
        count = len(window)

        recent = [x for x in times if x <= t][-5:]
        gaps = [b - a for a, b in pairwise(recent)]
        if len(gaps) >= 2:
            mean = statistics.fmean(gaps)
            reg = 0.0 if mean <= 0 else max(0.0, 1.0 - (statistics.pstdev(gaps) / mean))
        else:
            reg = 0.0

        out.append((count, round(reg, 4)))
    return out
