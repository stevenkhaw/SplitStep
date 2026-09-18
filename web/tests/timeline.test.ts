import { describe, expect, it } from 'vitest'
import {
  MIN_RALLY_MS,
  clampMinGap,
  fractionToMs,
  msToFraction,
  nearestHandle,
  scoreCurvePoints,
  scoreToY,
  sessionTimeline,
  setInPoint,
  setOutPoint,
  toSessionMs,
  zoomWindow,
} from '../src/lib/timeline'
import type { Source } from '../src/lib/types'

function source(idx: number, offsetMs: number, durationMs: number, recordedAt: string): Source {
  return {
    id: `src${idx}`,
    session_id: 's',
    idx,
    recorded_at: recordedAt,
    offset_ms: offsetMs,
    duration_ms: durationMs,
    width: 1920,
    height: 1080,
    fps: 30,
    has_original: 1,
    court_preset_id: null,
    status: 'ready',
    rotation_deg: 0,
    features_at: null,
    preset_assigned_at: null,
  }
}

describe('sessionTimeline', () => {
  it('sums durations for the virtual timeline', () => {
    const t = sessionTimeline([
      source(1, 0, 600000, '2026-08-19T10:00:00Z'),
      source(2, 600000, 300000, '2026-08-19T10:32:00Z'),
    ])
    expect(t.totalMs).toBe(900000)
  })

  it('marks each source boundary with the real elapsed gap', () => {
    const t = sessionTimeline([
      source(1, 0, 600000, '2026-08-19T10:00:00Z'),
      source(2, 600000, 300000, '2026-08-19T10:32:00Z'),
    ])
    // source 1 ran 10:00 -> 10:10; source 2 started 10:32, so a 22 minute gap
    expect(t.marks[1].gapMs).toBe(22 * 60 * 1000)
    expect(t.marks[1].offsetMs).toBe(600000)
  })

  it('reports no gap before the first source', () => {
    const t = sessionTimeline([source(1, 0, 600000, '2026-08-19T10:00:00Z')])
    expect(t.marks[0].gapMs).toBe(0)
  })

  it('lays sources out contiguously, not to real elapsed time', () => {
    // a 22 minute break must not consume timeline width
    const t = sessionTimeline([
      source(1, 0, 600000, '2026-08-19T10:00:00Z'),
      source(2, 600000, 300000, '2026-08-19T10:32:00Z'),
    ])
    expect(t.totalMs).toBe(900000) // not 900000 + 22 minutes
  })

  it('handles an empty source list', () => {
    expect(sessionTimeline([]).totalMs).toBe(0)
  })

  it('clamps a negative gap from out-of-order timestamps to zero', () => {
    const t = sessionTimeline([
      source(1, 0, 600000, '2026-08-19T10:00:00Z'),
      source(2, 600000, 300000, '2026-08-19T09:00:00Z'),
    ])
    expect(t.marks[1].gapMs).toBe(0)
  })
})

describe('toSessionMs', () => {
  const sources = [
    source(1, 0, 600000, '2026-08-19T10:00:00Z'),
    source(2, 600000, 300000, '2026-08-19T10:32:00Z'),
  ]

  it('passes through for the first source', () => {
    expect(toSessionMs(sources, 'src1', 5000)).toBe(5000)
  })

  it('offsets later sources', () => {
    expect(toSessionMs(sources, 'src2', 5000)).toBe(605000)
  })

  it('returns the local value for an unknown source', () => {
    expect(toSessionMs(sources, 'nope', 5000)).toBe(5000)
  })
})

describe('msToFraction / fractionToMs', () => {
  it('round-trips', () => {
    expect(msToFraction(450000, 900000)).toBe(0.5)
    expect(fractionToMs(0.5, 900000)).toBe(450000)
  })

  it('clamps out-of-range input', () => {
    expect(msToFraction(-100, 900000)).toBe(0)
    expect(msToFraction(9000000, 900000)).toBe(1)
    expect(fractionToMs(-0.5, 900000)).toBe(0)
    expect(fractionToMs(1.5, 900000)).toBe(900000)
  })

  it('returns zero rather than dividing by a zero total', () => {
    expect(msToFraction(1000, 0)).toBe(0)
  })
})

describe('zoomWindow', () => {
  it('centres a window of the requested span', () => {
    expect(zoomWindow(100000, 40000, 900000)).toEqual({ startMs: 80000, endMs: 120000 })
  })

  it('shifts rather than shrinks at the start', () => {
    expect(zoomWindow(5000, 40000, 900000)).toEqual({ startMs: 0, endMs: 40000 })
  })

  it('shifts rather than shrinks at the end', () => {
    expect(zoomWindow(895000, 40000, 900000)).toEqual({ startMs: 860000, endMs: 900000 })
  })

  it('caps the span at the session length', () => {
    expect(zoomWindow(5000, 40000, 20000)).toEqual({ startMs: 0, endMs: 20000 })
  })
})

describe('nearestHandle', () => {
  // a 1000px band, handles need to be within 10px to grab
  it('grabs the start handle', () => {
    expect(nearestHandle(0.302, 0.3, 0.7, 10, 1000)).toBe('start')
  })

  it('grabs the end handle', () => {
    expect(nearestHandle(0.698, 0.3, 0.7, 10, 1000)).toBe('end')
  })

  it('returns null in the middle', () => {
    expect(nearestHandle(0.5, 0.3, 0.7, 10, 1000)).toBeNull()
  })

  it('returns null outside the segment', () => {
    expect(nearestHandle(0.05, 0.3, 0.7, 10, 1000)).toBeNull()
  })

  it('picks the closer handle when the segment is very short', () => {
    expect(nearestHandle(0.5005, 0.5, 0.502, 10, 1000)).toBe('start')
    expect(nearestHandle(0.5018, 0.5, 0.502, 10, 1000)).toBe('end')
  })
})

describe('clampMinGap', () => {
  it('passes a normally-ordered, wide-enough pair through unchanged', () => {
    expect(clampMinGap(1000, 5000)).toEqual({ startMs: 1000, endMs: 5000 })
  })

  it('pulls endMs forward when the gap is smaller than the minimum', () => {
    expect(clampMinGap(1000, 1050)).toEqual({ startMs: 1000, endMs: 1100 })
  })

  it('never persists an inverted rally (Finding 7: "[" past end_ms)', () => {
    // e.g. the playhead parked past the rally's current end_ms, then "["
    // committed with startMs > endMs.
    const result = clampMinGap(8000, 5000)
    expect(result.startMs).toBe(8000) // the live playhead position, honored exactly
    expect(result.endMs).toBeGreaterThan(result.startMs)
    expect(result.endMs).toBe(8100)
  })

  it('anchors startMs exactly, even in the degenerate case -- callers rely on this to pick which side is the anchor', () => {
    expect(clampMinGap(500, 500)).toEqual({ startMs: 500, endMs: 600 })
  })

  it('honors a custom minimum', () => {
    expect(clampMinGap(1000, 1010, 50)).toEqual({ startMs: 1000, endMs: 1050 })
  })

  it('exports the default minimum used across the drag and keyboard paths', () => {
    expect(MIN_RALLY_MS).toBe(100)
  })
})

describe('scoreToY', () => {
  it('maps a score of 1 near the top of the viewBox', () => {
    expect(scoreToY(1, 38)).toBe(2)
  })

  it('maps a score of 0 to the bottom of the viewBox', () => {
    expect(scoreToY(0, 38)).toBe(38)
  })

  it('clamps out-of-range scores', () => {
    expect(scoreToY(-1, 38)).toBe(38)
    expect(scoreToY(2, 38)).toBe(2)
  })
})

describe('scoreCurvePoints', () => {
  it('returns an empty string for an empty series', () => {
    expect(scoreCurvePoints([], 200, 0, 40000, 1000, 38)).toBe('')
  })

  it('returns an empty string for a non-positive step', () => {
    expect(scoreCurvePoints([0.1, 0.2, 0.3], 0, 0, 40000, 1000, 38)).toBe('')
  })

  it('returns an empty string when fewer than two samples fall in the window', () => {
    // step 200ms, window 0..100ms -> only index 0 is in range
    expect(scoreCurvePoints([0.1, 0.2, 0.3], 200, 0, 100, 1000, 38)).toBe('')
  })

  it('spaces samples evenly across the requested width, mapped by score', () => {
    // step 1000ms, window 0..2500ms -> ceil(2500/1000)=3 -> indices 0,1,2 (3 samples)
    const points = scoreCurvePoints([0, 0.5, 1], 1000, 0, 2500, 1000, 38)
    expect(points).toBe('0,38 500,20 1000,2')
  })

  it('clips the slice to the available series length', () => {
    // window extends past the end of the series -- only what exists is drawn
    const points = scoreCurvePoints([0, 1], 1000, 0, 5000, 1000, 38)
    expect(points).toBe('0,38 1000,2')
  })
})

describe('setInPoint / setOutPoint refuse a collapse (rally 17 destruction, 2026-08-21)', () => {
  // Rally 17 of session 2026-08-18 was a 9.6 s rally at 312300-321900. It was
  // found in the database as 328867-328967 -- exactly MIN_RALLY_MS, parked at
  // a playhead seven seconds past its own end. Pressing `[` there anchored the
  // in-point and dragged the out-point to start+100ms, silently destroying it.
  const START = 312300
  const END = 321900

  it('refuses an in-point at or past the rally end', () => {
    const r = setInPoint(START, END, END + 7000)
    expect(r.ok).toBe(false)
  })

  it('explains what to do instead, rather than just failing', () => {
    const r = setInPoint(START, END, END + 7000)
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.reason).toMatch(/out-point/i)
  })

  it('refuses an in-point that would leave less than the minimum gap', () => {
    expect(setInPoint(START, END, END - 50).ok).toBe(false)
  })

  it('accepts an in-point that leaves exactly the minimum gap', () => {
    const r = setInPoint(START, END, END - MIN_RALLY_MS)
    expect(r.ok).toBe(true)
    if (r.ok) expect([r.startMs, r.endMs]).toEqual([END - MIN_RALLY_MS, END])
  })

  it('accepts an ordinary trim and leaves the far bound untouched', () => {
    const r = setInPoint(START, END, START + 2000)
    expect(r.ok).toBe(true)
    if (r.ok) expect([r.startMs, r.endMs]).toEqual([START + 2000, END])
  })

  it('refuses an out-point at or before the rally start', () => {
    expect(setOutPoint(START, END, START - 5000).ok).toBe(false)
    expect(setOutPoint(START, END, START + 50).ok).toBe(false)
  })

  it('accepts an ordinary out-point trim', () => {
    const r = setOutPoint(START, END, END - 2000)
    expect(r.ok).toBe(true)
    if (r.ok) expect([r.startMs, r.endMs]).toEqual([START, END - 2000])
  })

  it('leaves re-spanning a rally possible by setting the far bound first', () => {
    // The old collapse behaviour was load-bearing for one workflow: moving a
    // rally wholesale to a later span. Refusing must not block that -- it just
    // has to be done out-point first.
    const out = setOutPoint(START, END, 340000)
    expect(out.ok).toBe(true)
    if (!out.ok) return
    const inn = setInPoint(out.startMs, out.endMs, 330000)
    expect(inn.ok).toBe(true)
    if (inn.ok) expect([inn.startMs, inn.endMs]).toEqual([330000, 340000])
  })
})
