import { describe, expect, it } from 'vitest'
import { previewTimestamps } from '../src/lib/preview'

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
})
