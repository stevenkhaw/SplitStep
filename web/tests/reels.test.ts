import { describe, expect, it } from 'vitest'
import {
  mergeSpans,
  missingClipCount,
  reelStateLabel,
  renderBlockedReason,
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

describe('mergeSpans', () => {
  it('appends only what is new, in the order given', () => {
    expect(mergeSpans([span('a', 1, 2)], [span('a', 3, 4), span('a', 5, 6)])).toEqual([
      span('a', 1, 2), span('a', 3, 4), span('a', 5, 6),
    ])
  })

  it('never removes or reorders what is already there', () => {
    // The whole point: a second click must not discard a manual reorder.
    const existing = [span('a', 5, 6), span('a', 1, 2)]
    expect(mergeSpans(existing, [span('a', 1, 2)])).toEqual(existing)
  })

  it('drops a duplicate within the incoming list too', () => {
    expect(mergeSpans([], [span('a', 1, 2), span('a', 1, 2)])).toEqual([span('a', 1, 2)])
  })

  it('does not mutate its inputs', () => {
    const existing = [span('a', 1, 2)]
    mergeSpans(existing, [span('a', 3, 4)])
    expect(existing).toEqual([span('a', 1, 2)])
  })
})

describe('missingClipCount', () => {
  it('counts items with no clip on disk', () => {
    expect(missingClipCount([item(), item({ clip_ready: false })])).toBe(1)
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
