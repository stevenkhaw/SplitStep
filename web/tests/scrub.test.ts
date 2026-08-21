import { describe, expect, it } from 'vitest'
import { fractionToScrubMs, scrubMsToFraction } from '../src/lib/scrub'

describe('fractionToScrubMs', () => {
  const start = 10_000
  const end = 17_600 // 7.6s span, the median real-footage rally length

  it('maps fraction 0 to the start', () => {
    expect(fractionToScrubMs(0, start, end)).toBe(start)
  })

  it('maps fraction 1 to the end', () => {
    expect(fractionToScrubMs(1, start, end)).toBe(end)
  })

  it('maps the midpoint fraction to the midpoint timestamp', () => {
    expect(fractionToScrubMs(0.5, start, end)).toBe(start + (end - start) / 2)
  })

  it('clamps a fraction dragged left of the track to the start', () => {
    // Routine once the pointer holds capture -- the drag continues to fire
    // pointermove for x well outside the bar's rect.
    expect(fractionToScrubMs(-0.4, start, end)).toBe(start)
  })

  it('clamps a fraction dragged past the right edge to the end', () => {
    // This is the case that matters most: label mode loops inside the rally
    // specifically so the reviewer never sees the next rally's footage, and
    // an unclamped overshoot here would seek straight past det_end_ms into it.
    expect(fractionToScrubMs(1.7, start, end)).toBe(end)
  })

  it('degrades to a single point for a zero-length span instead of throwing', () => {
    expect(fractionToScrubMs(0.5, 5_000, 5_000)).toBe(5_000)
  })
})

describe('scrubMsToFraction', () => {
  const start = 10_000
  const end = 17_600

  it('is the inverse of fractionToScrubMs at the start', () => {
    expect(scrubMsToFraction(start, start, end)).toBe(0)
  })

  it('is the inverse of fractionToScrubMs at the end', () => {
    expect(scrubMsToFraction(end, start, end)).toBe(1)
  })

  it('round-trips a fraction through fractionToScrubMs and back', () => {
    const ms = fractionToScrubMs(0.3, start, end)
    expect(scrubMsToFraction(ms, start, end)).toBeCloseTo(0.3, 5)
  })

  it('clamps a timestamp before the start to fraction 0', () => {
    expect(scrubMsToFraction(0, start, end)).toBe(0)
  })

  it('clamps a timestamp past the end to fraction 1', () => {
    expect(scrubMsToFraction(999_999, start, end)).toBe(1)
  })

  it('returns 0 for a zero-length span instead of dividing by zero', () => {
    expect(scrubMsToFraction(5_000, 5_000, 5_000)).toBe(0)
  })
})
