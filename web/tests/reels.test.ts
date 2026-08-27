import { describe, expect, it } from 'vitest'
import {
  ITEM_NOTE_MAX_CHARS,
  canWatchRendered,
  deleteConfirmationText,
  formatBytes,
  missingClipCount,
  normalizedItemNote,
  normalizedReelName,
  reelMembershipKey,
  renderBlockedReason,
  shouldPollReel,
  spanKey,
} from '../src/lib/reels'
import type { Reel, ReelItem, SpanRef } from '../src/lib/types'

const span = (source_id: string, start_ms: number, end_ms: number): SpanRef => ({
  source_id, start_ms, end_ms,
})

function item(overrides: Partial<ReelItem> = {}): ReelItem {
  return {
    source_id: 'src1',
    session_id: 's1',
    source_idx: 1,
    start_ms: 1000,
    end_ms: 5000,
    duration_ms: 4000,
    position: 0,
    clip_ready: true,
    rally: null,
    note: '',
    ...overrides,
  }
}

function reel(overrides: Partial<Reel> = {}): Reel {
  return {
    id: 'r1',
    name: 'r',
    slug: 'r',
    rendered_path: null,
    rendered_at: null,
    thumb: null,
    dirty: 1,
    created_at: '2026-08-21T10:00:00Z',
    item_count: 0,
    ...overrides,
  }
}

describe('spanKey', () => {
  it('distinguishes sources with the same span', () => {
    expect(spanKey(span('a', 1, 2))).not.toBe(spanKey(span('b', 1, 2)))
  })

  it('is stable for the same span', () => {
    expect(spanKey(span('a', 1, 2))).toBe(spanKey(span('a', 1, 2)))
  })
})

describe('reelMembershipKey', () => {
  it('is stable across two different array instances with the same spans in order', () => {
    // The whole point: `detail.items` is a fresh array on every fetch (JSON
    // never shares identity), so a `{#key}` on the array reference would
    // remount on every refetch. The key must not.
    const a = [item({ source_id: 's1', start_ms: 1000 }), item({ source_id: 's1', start_ms: 9000 })]
    const b = [item({ source_id: 's1', start_ms: 1000 }), item({ source_id: 's1', start_ms: 9000 })]
    expect(a).not.toBe(b)
    expect(reelMembershipKey(a)).toBe(reelMembershipKey(b))
  })

  it('changes when an item is removed', () => {
    const before = [item({ source_id: 's1', start_ms: 1000 }), item({ source_id: 's1', start_ms: 9000 })]
    const after = [item({ source_id: 's1', start_ms: 1000 })]
    expect(reelMembershipKey(before)).not.toBe(reelMembershipKey(after))
  })

  it('changes when the same items are reordered', () => {
    // Order is part of the identity too -- a reorder needs a fresh preview
    // controller just as much as an add or remove does.
    const before = [item({ source_id: 's1', start_ms: 1000 }), item({ source_id: 's1', start_ms: 9000 })]
    const after = [item({ source_id: 's1', start_ms: 9000 }), item({ source_id: 's1', start_ms: 1000 })]
    expect(reelMembershipKey(before)).not.toBe(reelMembershipKey(after))
  })

  it('changes when an item is added', () => {
    const before = [item({ source_id: 's1', start_ms: 1000 })]
    const after = [item({ source_id: 's1', start_ms: 1000 }), item({ source_id: 's1', start_ms: 9000 })]
    expect(reelMembershipKey(before)).not.toBe(reelMembershipKey(after))
  })

  it('is empty but stable for an empty reel', () => {
    expect(reelMembershipKey([])).toBe(reelMembershipKey([]))
  })
})

describe('missingClipCount', () => {
  it('counts items with no clip on disk', () => {
    expect(missingClipCount([item(), item({ clip_ready: false })])).toBe(1)
  })
})

describe('shouldPollReel', () => {
  it('is true while any clip is still missing', () => {
    expect(shouldPollReel([item({ clip_ready: false }), item()])).toBe(true)
  })

  it('is false once every clip is ready', () => {
    expect(shouldPollReel([item(), item()])).toBe(false)
  })

  it('is false for an empty reel -- nothing to wait for', () => {
    expect(shouldPollReel([])).toBe(false)
  })
})

describe('renderBlockedReason', () => {
  it('names the count so the button says why it is disabled', () => {
    expect(renderBlockedReason([item({ clip_ready: false }), item({ clip_ready: false })]))
      .toBe('2 clips not cut yet')
  })

  it('says "clip" for one', () => {
    expect(renderBlockedReason([item({ clip_ready: false })])).toBe('1 clip not cut yet')
  })

  it('blocks an empty reel', () => {
    expect(renderBlockedReason([])).toBe('No clips in this reel yet')
  })

  it('returns null when every clip is ready', () => {
    expect(renderBlockedReason([item(), item()])).toBeNull()
  })
})

describe('canWatchRendered', () => {
  it('is false before the first render -- there is no file yet', () => {
    expect(canWatchRendered(reel())).toBe(false)
  })

  it('is true once rendered', () => {
    expect(canWatchRendered(reel({
      rendered_path: 'reels/r.mp4', rendered_at: '2026-08-21T12:00:00Z', dirty: 0,
    }))).toBe(true)
  })

  it('stays true while dirty -- the last render is still on disk and still playable', () => {
    expect(canWatchRendered(reel({
      rendered_path: 'reels/r.mp4', rendered_at: '2026-08-21T12:00:00Z', dirty: 1,
    }))).toBe(true)
  })
})

describe('formatBytes', () => {
  it('reads plain bytes below 1 KB', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(999)).toBe('999 B')
  })

  it('switches to KB at 1024 bytes', () => {
    expect(formatBytes(1024)).toBe('1 KB')
    expect(formatBytes(1536)).toBe('1.5 KB')
  })

  it('switches to MB, matching the user\'s real reel size from the spec', () => {
    expect(formatBytes(725 * 1024 * 1024)).toBe('725 MB')
  })

  it('promotes when rounding crosses a unit boundary', () => {
    // 1023.6 MB rounds to 1024 -- an out-of-range "1024 MB" -- so it must
    // promote to the next unit instead.
    expect(formatBytes(1023.6 * 1024 ** 2)).toBe('1 GB')
    expect(formatBytes(1023.6 * 1024 ** 3)).toBe('1 TB')
  })

  it('switches to GB and keeps one decimal below 10', () => {
    expect(formatBytes(1024 * 1024 * 1024)).toBe('1 GB')
    expect(formatBytes(2.5 * 1024 * 1024 * 1024)).toBe('2.5 GB')
  })

  it('drops the decimal at 10 units and above', () => {
    expect(formatBytes(10 * 1024 * 1024)).toBe('10 MB')
    expect(formatBytes(123 * 1024 * 1024)).toBe('123 MB')
  })
})

describe('deleteConfirmationText', () => {
  it('names the reel alone when there is no render to reclaim', () => {
    expect(deleteConfirmationText('Best of July', null))
      .toBe('Delete "Best of July"? This cannot be undone.')
  })

  it('names the render size too when there is one', () => {
    expect(deleteConfirmationText('Best of July', 725 * 1024 * 1024))
      .toBe('Delete "Best of July" and its 725 MB render? This cannot be undone.')
  })
})

describe('normalizedReelName', () => {
  it('trims surrounding whitespace', () => {
    expect(normalizedReelName('  Best of July  ')).toBe('Best of July')
  })

  it('is null for a blank or whitespace-only input', () => {
    expect(normalizedReelName('')).toBeNull()
    expect(normalizedReelName('   ')).toBeNull()
  })

  it('is null for pure whitespace even with tabs and newlines', () => {
    expect(normalizedReelName('\t\n ')).toBeNull()
  })
})

describe('normalizedItemNote', () => {
  it('trims surrounding whitespace', () => {
    expect(normalizedItemNote('  deep lob  ')).toBe('deep lob')
  })

  it('passes a note exactly at the cap', () => {
    const exact = 'x'.repeat(ITEM_NOTE_MAX_CHARS)
    expect(normalizedItemNote(exact)).toBe(exact)
  })

  it('refuses a note one character over the cap', () => {
    const over = 'x'.repeat(ITEM_NOTE_MAX_CHARS + 1)
    expect(normalizedItemNote(over)).toBeNull()
  })

  it('keeps an empty string -- clearing the note is legal', () => {
    expect(normalizedItemNote('')).toBe('')
    expect(normalizedItemNote('   ')).toBe('')
  })
})
