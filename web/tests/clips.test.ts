import { describe, expect, it } from 'vitest'
import {
  clipKey,
  clipLabel,
  clipMediaUrl,
  clipPollTarget,
  clipRevealRelpath,
  formatClipSize,
  groupClipsBySource,
  sessionClipsRelpath,
  shouldPollForExport,
} from '../src/lib/clips'
import type { Clip, ExportResult } from '../src/lib/types'

function clip(overrides: Partial<Clip> = {}): Clip {
  return {
    source_idx: 1,
    start_ms: 1000,
    end_ms: 9000,
    relpath: '01/1000-9000.mp4',
    size_bytes: 12_000_000,
    ...overrides,
  }
}

function exportResult(overrides: Partial<ExportResult> = {}): ExportResult {
  return { queued: 0, already_cut: 0, in_flight: 0, unavailable: 0, total: 0, ...overrides }
}

describe('groupClipsBySource', () => {
  it('groups clips under their source, preserving clip order within a group', () => {
    const clips = [
      clip({ source_idx: 1, start_ms: 0, end_ms: 1000 }),
      clip({ source_idx: 1, start_ms: 2000, end_ms: 3000 }),
      clip({ source_idx: 2, start_ms: 0, end_ms: 1000 }),
    ]
    const groups = groupClipsBySource(clips)
    expect(groups).toHaveLength(2)
    expect(groups[0].source_idx).toBe(1)
    expect(groups[0].clips.map((c) => c.start_ms)).toEqual([0, 2000])
    expect(groups[1].source_idx).toBe(2)
    expect(groups[1].clips.map((c) => c.start_ms)).toEqual([0])
  })

  it('preserves the order sources first appear in', () => {
    const clips = [clip({ source_idx: 2 }), clip({ source_idx: 1 })]
    const groups = groupClipsBySource(clips)
    expect(groups.map((g) => g.source_idx)).toEqual([2, 1])
  })

  it('folds a source that reappears non-consecutively into one group', () => {
    const clips = [
      clip({ source_idx: 1, start_ms: 0 }),
      clip({ source_idx: 2, start_ms: 0 }),
      clip({ source_idx: 1, start_ms: 5000 }),
    ]
    const groups = groupClipsBySource(clips)
    expect(groups).toHaveLength(2)
    expect(groups[0].source_idx).toBe(1)
    expect(groups[0].clips.map((c) => c.start_ms)).toEqual([0, 5000])
  })

  it('returns an empty list for no clips', () => {
    expect(groupClipsBySource([])).toEqual([])
  })
})

describe('clipMediaUrl', () => {
  it('builds the media URL from the clip’s own fields', () => {
    expect(clipMediaUrl('s1', clip({ source_idx: 3, start_ms: 1200, end_ms: 8400 })))
      .toBe('/media/clips/s1/3/1200-8400.mp4')
  })

  it('ignores relpath, even a legacy flat one, and reconstructs the nested name', () => {
    const legacy = clip({ source_idx: 1, start_ms: 1000, end_ms: 9000, relpath: '01-1000-9000.mp4' })
    expect(clipMediaUrl('s1', legacy)).toBe('/media/clips/s1/1/1000-9000.mp4')
  })
})

describe('clipLabel', () => {
  it('reads as duration then span', () => {
    expect(clipLabel(clip({ start_ms: 10000, end_ms: 18000 }))).toBe('8.0s · 0:10.0–0:18.0')
  })
})

describe('formatClipSize', () => {
  it('reads in MB below a gigabyte', () => {
    expect(formatClipSize(12_400_000)).toBe('12.4 MB')
  })

  it('reads sub-megabyte sizes as a fractional MB, matching the CLI', () => {
    expect(formatClipSize(500_000)).toBe('0.5 MB')
  })

  it('switches to GB at the CLI’s exact threshold', () => {
    expect(formatClipSize(999_999_999)).toBe('1000.0 MB')
    expect(formatClipSize(1_000_000_000)).toBe('1.0 GB')
  })

  it('reads large clips in GB', () => {
    expect(formatClipSize(1_340_000_000)).toBe('1.3 GB')
  })
})

describe('sessionClipsRelpath', () => {
  it('matches Library.clips_dir’s shape', () => {
    expect(sessionClipsRelpath('s1')).toBe('sessions/s1/clips')
  })
})

describe('clipRevealRelpath', () => {
  it('composes the session folder with the clip’s own relpath', () => {
    expect(clipRevealRelpath('s1', clip({ relpath: '01/1000-9000.mp4' })))
      .toBe('sessions/s1/clips/01/1000-9000.mp4')
  })
})

describe('clipKey', () => {
  it('is stable for the same span', () => {
    expect(clipKey(clip({ source_idx: 1, start_ms: 1000, end_ms: 9000 })))
      .toBe(clipKey(clip({ source_idx: 1, start_ms: 1000, end_ms: 9000 })))
  })

  it('distinguishes clips on different sources with the same span', () => {
    expect(clipKey(clip({ source_idx: 1 }))).not.toBe(clipKey(clip({ source_idx: 2 })))
  })
})

describe('clipPollTarget', () => {
  it('adds only queued, not already_cut/in_flight/unavailable, to the baseline', () => {
    const result = exportResult({ queued: 3, already_cut: 2, in_flight: 1, unavailable: 1, total: 7 })
    expect(clipPollTarget(5, result)).toBe(8)
  })

  it('leaves the target at the baseline when nothing new was queued', () => {
    expect(clipPollTarget(5, exportResult({ already_cut: 5, total: 5 }))).toBe(5)
  })
})

describe('shouldPollForExport', () => {
  it('is false with no target set', () => {
    expect(shouldPollForExport(0, null)).toBe(false)
  })

  it('is true while short of the target', () => {
    expect(shouldPollForExport(3, 5)).toBe(true)
  })

  it('is false once the target is reached', () => {
    expect(shouldPollForExport(5, 5)).toBe(false)
  })

  it('is false once the count exceeds the target (a later export queued more)', () => {
    expect(shouldPollForExport(6, 5)).toBe(false)
  })
})
