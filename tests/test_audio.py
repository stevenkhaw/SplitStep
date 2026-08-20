import numpy as np

from bootleg.detect.audio import Hit, detect_hits, hits_to_grid

SR = 22050


def click_track(intervals_s, *, duration_s=10.0, noise=0.02, amp=0.9, seed=0):
    rng = np.random.default_rng(seed)
    x = rng.normal(0, noise, int(SR * duration_s)).astype(np.float32)
    t = 1.0
    for gap in intervals_s:
        i = int(t * SR)
        if i < len(x) - 200:
            # a short broadband transient, like ball on strings
            burst = rng.normal(0, 1, 200).astype(np.float32) * amp
            burst *= np.exp(-np.linspace(0, 6, 200))
            x[i:i + 200] += burst
        t += gap
    return x


def test_detects_evenly_spaced_hits():
    x = click_track([1.0] * 6)
    hits = detect_hits(x, SR)
    assert 5 <= len(hits) <= 8, f"expected ~6 hits, got {len(hits)}"


def test_ignores_pure_noise():
    rng = np.random.default_rng(1)
    x = rng.normal(0, 0.02, SR * 5).astype(np.float32)
    assert len(detect_hits(x, SR)) <= 1


def test_low_frequency_buffets_are_rejected():
    """Wind buffets are low-frequency transients. The 800 Hz highpass must
    remove them. Unlike a steady sine, these have sharp envelopes, so this
    test fails if the highpass is removed -- it actually guards the filter."""
    rng = np.random.default_rng(3)
    x = rng.normal(0, 0.02, SR * 8).astype(np.float32)
    for t0 in (1.0, 2.5, 4.0, 5.5, 7.0):
        i = int(t0 * SR)
        ln = int(0.25 * SR)
        tt = np.arange(ln) / SR
        x[i:i + ln] += (1.2 * np.sin(2 * np.pi * 60 * tt) * np.exp(-tt * 12)).astype(np.float32)
    assert detect_hits(x, SR) == []


def test_min_gap_suppresses_double_triggers():
    x = click_track([0.02, 0.02, 0.02], duration_s=5.0)
    hits = detect_hits(x, SR, min_gap_ms=200)
    assert len(hits) <= 2


def test_grid_counts_hits_in_trailing_second():
    hits = [Hit(1000, 1.0), Hit(1500, 1.0), Hit(2600, 1.0)]
    grid = hits_to_grid(hits, duration_ms=4000, step_ms=200)
    assert len(grid) == 20
    at_2000 = grid[10]           # t = 2000 ms; window covers 1000..2000
    assert at_2000[0] == 2
    at_3800 = grid[19]           # t = 3800 ms; window covers 2800..3800
    assert at_3800[0] == 0


def test_grid_regularity_is_high_for_metronomic_hits():
    hits = [Hit(1000 + i * 1000, 1.0) for i in range(5)]
    grid = hits_to_grid(hits, duration_ms=6000, step_ms=200)
    assert grid[-1][1] > 0.8


def test_grid_regularity_is_low_for_erratic_hits():
    hits = [Hit(1000, 1.0), Hit(1100, 1.0), Hit(3900, 1.0), Hit(4000, 1.0)]
    grid = hits_to_grid(hits, duration_ms=6000, step_ms=200)
    assert grid[-1][1] < 0.5


def test_grid_is_all_zero_without_hits():
    grid = hits_to_grid([], duration_ms=2000, step_ms=200)
    assert all(count == 0 and reg == 0.0 for count, reg in grid)
