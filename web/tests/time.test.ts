import { describe, expect, it } from 'vitest'
import {
  clamp,
  formatDuration,
  formatTs,
  frameStep,
  lastSafeFrameMs,
} from '../src/lib/time'

describe('formatTs', () => {
  it('formats under a minute', () => {
    expect(formatTs(0)).toBe('0:00.0')
    expect(formatTs(9700)).toBe('0:09.7')
  })

  it('rolls over to minutes without producing :60', () => {
    expect(formatTs(59950)).toBe('1:00.0')
    expect(formatTs(60000)).toBe('1:00.0')
  })

  it('formats past an hour', () => {
    expect(formatTs(2413200)).toBe('40:13.2')
    expect(formatTs(3757100)).toBe('1:02:37.1')
  })

  it('does not produce a negative timestamp', () => {
    expect(formatTs(-500)).toBe('0:00.0')
  })
})

describe('formatDuration', () => {
  it('reads as seconds for short rallies', () => {
    expect(formatDuration(13200)).toBe('13.2s')
    expect(formatDuration(900)).toBe('0.9s')
  })
})

describe('frameStep', () => {
  it('advances exactly one frame at 30fps', () => {
    expect(frameStep(1000, 30, 1)).toBeCloseTo(1033.33, 1)
  })

  it('steps backwards', () => {
    expect(frameStep(1000, 30, -1)).toBeCloseTo(966.67, 1)
  })

  it('never goes below zero', () => {
    expect(frameStep(10, 30, -1)).toBe(0)
  })

  it('tolerates a zero fps rather than dividing by it', () => {
    expect(frameStep(1000, 0, 1)).toBe(1000)
  })
})

describe('clamp', () => {
  it('bounds both directions', () => {
    expect(clamp(5, 0, 10)).toBe(5)
    expect(clamp(-1, 0, 10)).toBe(0)
    expect(clamp(11, 0, 10)).toBe(10)
  })
})

describe('lastSafeFrameMs', () => {
  it('backs off by one frame period plus a millisecond', () => {
    // The 30fps case measured against ffmpeg in api_frame's comment:
    // 1967ms fails on a 2000ms clip, 1966ms succeeds.
    expect(lastSafeFrameMs(2000, 30)).toBe(1966)
  })

  it('matches the server clamp at a synthetic 10fps', () => {
    expect(lastSafeFrameMs(2000, 10)).toBe(1899)
  })

  it('falls back to half the duration when fps is unknown', () => {
    expect(lastSafeFrameMs(2000, 0)).toBe(1000)
  })

  it('never returns a negative timestamp for a clip shorter than the margin', () => {
    expect(lastSafeFrameMs(10, 30)).toBe(0)
  })
})
