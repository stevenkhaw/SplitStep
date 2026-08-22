import { describe, expect, it } from 'vitest'
import { ReelPreviewController } from '../src/lib/reelPreview'
import type { ReelItem } from '../src/lib/types'

function item(start: number, overrides: Partial<ReelItem> = {}): ReelItem {
  return {
    source_id: 'src1',
    session_id: 's1',
    source_idx: 1,
    start_ms: start,
    end_ms: start + 4000,
    duration_ms: 4000,
    position: 0,
    clip_ready: true,
    rally: null,
    ...overrides,
  }
}

describe('ReelPreviewController', () => {
  it('starts on the first item', () => {
    const c = new ReelPreviewController([item(1000), item(9000)])
    expect(c.index).toBe(0)
    expect(c.current?.start_ms).toBe(1000)
    expect(c.total).toBe(2)
    expect(c.finished).toBe(false)
  })

  it('exposes the next item so VideoDeck can preload it', () => {
    // The whole reason preview seeks the proxy instead of chaining clip
    // files: VideoDeck already preloads the next span into its second
    // element, across different sources.
    const c = new ReelPreviewController([item(1000), item(9000)])
    expect(c.next?.start_ms).toBe(9000)
  })

  it('advances in reel order, not chronological order', () => {
    // A hand-reordered reel plays as ordered. Sorting here would silently
    // undo the drag.
    const c = new ReelPreviewController([item(9000), item(1000)])
    expect(c.current?.start_ms).toBe(9000)
    c.advance()
    expect(c.current?.start_ms).toBe(1000)
  })

  it('finishes past the last item rather than looping', () => {
    const c = new ReelPreviewController([item(1000)])
    expect(c.next).toBeUndefined()
    c.advance()
    expect(c.finished).toBe(true)
    expect(c.current).toBeUndefined()
  })

  it('does not run past the end on a repeated advance', () => {
    const c = new ReelPreviewController([item(1000)])
    c.advance()
    c.advance()
    expect(c.index).toBe(1)
  })

  it('restarts from the top', () => {
    const c = new ReelPreviewController([item(1000), item(9000)])
    c.advance()
    c.advance()
    c.restart()
    expect(c.index).toBe(0)
    expect(c.finished).toBe(false)
  })

  it('jumps to an item', () => {
    const c = new ReelPreviewController([item(1000), item(9000), item(20000)])
    c.jumpTo(2)
    expect(c.current?.start_ms).toBe(20000)
  })

  it('ignores an out-of-range jump', () => {
    // The list can shrink under the preview (a removal in the builder), and
    // silently seeking to nothing is worse than staying put.
    const c = new ReelPreviewController([item(1000)])
    c.jumpTo(5)
    expect(c.index).toBe(0)
  })

  it('is immediately finished for an empty reel', () => {
    const c = new ReelPreviewController([])
    expect(c.finished).toBe(true)
    expect(c.total).toBe(0)
  })

  it('previews items whose clip is not cut yet', () => {
    // Preview plays the PROXY, so it is unaffected by whether the 4K clip
    // exists -- that is the point of §6.5. Filtering to ready items would
    // hide exactly the spans a reviewer is about to cut.
    const c = new ReelPreviewController([item(1000, { clip_ready: false })])
    expect(c.current?.start_ms).toBe(1000)
  })
})
