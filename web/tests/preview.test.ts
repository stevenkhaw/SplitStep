import { describe, expect, it } from 'vitest'
import { previewTimestamps } from '../src/lib/preview'
import { lastSafeFrameMs } from '../src/lib/time'
import { api } from '../src/lib/api'

describe('previewTimestamps', () => {
  it('spaces nine frames across the clip at 10% intervals', () => {
    const ts = previewTimestamps(1_000_000, 30)
    expect(ts).toHaveLength(9)
    expect(ts[0]).toBe(100_000)
    expect(ts[8]).toBe(900_000)
  })

  it('never returns a timestamp past the last decodable frame', () => {
    const ts = previewTimestamps(2000, 30)
    expect(Math.max(...ts)).toBeLessThanOrEqual(1966)
  })

  it('de-duplicates on a clip too short to spread across', () => {
    expect(new Set(previewTimestamps(10, 30)).size).toBe(previewTimestamps(10, 30).length)
  })

  it('honours an explicit count', () => {
    expect(previewTimestamps(1_000_000, 30, 4)).toHaveLength(4)
  })

  it('yields the only decodable frame for a clip shorter than one frame period', () => {
    expect(previewTimestamps(10, 30)).toEqual([0])
  })

  it('returns strictly increasing timestamps within the safe frame boundary for realistic clips', () => {
    const durationMs = 1_173_905 // 19.6 minutes
    const fps = 29.97
    const ts = previewTimestamps(durationMs, fps)
    const max = lastSafeFrameMs(durationMs, fps)

    // No timestamp should be 0
    expect(ts).not.toContain(0)

    // Values should be strictly increasing
    for (let i = 1; i < ts.length; i++) {
      expect(ts[i]).toBeGreaterThan(ts[i - 1])
    }

    // All values should be within the safe frame boundary
    for (const timestamp of ts) {
      expect(timestamp).toBeLessThanOrEqual(max)
    }
  })
})

describe('previewUrl', () => {
  it('rounds fractional timestamps to integers in the query string', () => {
    const url = api.previewUrl('session-123', 0, 123.456, 90)
    expect(url).toContain('at_ms=123')
    expect(url).not.toContain('at_ms=123.456')
  })
})
