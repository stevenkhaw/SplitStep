import { describe, expect, it } from 'vitest'
import {
  canWatchRendered,
  missingClipCount,
  reelMembershipKey,
  reelStateLabel,
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

describe('reelStateLabel', () => {
  it('reads "not rendered" before the first render', () => {
    expect(reelStateLabel(reel())).toBe('not rendered')
  })

  it('distinguishes a stale render from no render at all', () => {
    // rendered_path survives a membership change on purpose (see
    // mark_dirty): the file is still on disk and still watchable, it is
    // merely out of date, and collapsing the two states would hide that.
    expect(reelStateLabel(reel({
      rendered_path: 'reels/r.mp4', rendered_at: '2026-08-21T12:00:00Z', dirty: 1,
    }))).toBe('needs re-render')
  })

  it('reads "rendered" when clean', () => {
    expect(reelStateLabel(reel({
      rendered_path: 'reels/r.mp4', rendered_at: '2026-08-21T12:00:00Z', dirty: 0,
    }))).toBe('rendered')
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
