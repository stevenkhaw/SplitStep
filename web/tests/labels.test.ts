import { beforeEach, describe, expect, it } from 'vitest'
import { LabelController } from '../src/lib/labels'
import type { LabelRecord, Rally } from '../src/lib/types'

function rally(idx: number, over: Partial<Rally> = {}): Rally {
  return {
    id: `r${idx}`,
    session_id: 's',
    source_id: 'src',
    idx,
    start_ms: idx * 10000,
    end_ms: idx * 10000 + 8000,
    det_start_ms: idx * 10000,
    det_end_ms: idx * 10000 + 8000,
    confidence: 0.7,
    starred: 0,
    rejected: 0,
    reviewed_at: null,
    ...over,
  }
}

function record(over: Partial<LabelRecord> = {}): LabelRecord {
  return {
    span_start_ms: 10000,
    span_end_ms: 18000,
    verdict: 'clean',
    boundary_flags: [],
    true_start_ms: null,
    true_end_ms: null,
    ...over,
  }
}

describe('LabelController', () => {
  let c: LabelController

  beforeEach(() => {
    c = new LabelController([rally(1), rally(2), rally(3)], [])
  })

  it('starts on the first rally', () => {
    expect(c.current?.id).toBe('r1')
    expect(c.index).toBe(0)
    expect(c.total).toBe(3)
  })

  it('includes rejected rallies, unlike the review queue', () => {
    // A rejected rally is exactly the not_play the corpus most needs. The
    // queue filters them out because it is a review flow; this is not.
    const withRejected = new LabelController([rally(1, { rejected: 1 }), rally(2)], [])
    expect(withRejected.total).toBe(2)
    expect(withRejected.current?.id).toBe('r1')
  })

  it('setVerdict returns an action carrying the previous state', () => {
    const action = c.setVerdict('clean')
    expect(action).toEqual({
      rallyId: 'r1',
      verdict: 'clean',
      flags: [],
      previousVerdict: null,
      previousFlags: [],
    })
    expect(c.currentVerdict).toBe('clean')
  })

  it('setVerdict does not advance', () => {
    // Flags are added to the same span after the verdict, so the cursor has
    // to stay put -- and 95218d3 removed auto-advance from the queue for the
    // same reason.
    c.setVerdict('clean')
    expect(c.index).toBe(0)
  })

  it('toggleFlag adds then removes, and keeps canonical order', () => {
    c.setVerdict('clean')
    c.toggleFlag('end_late')
    c.toggleFlag('start_early')
    expect(c.currentFlags).toEqual(['start_early', 'end_late'])
    c.toggleFlag('end_late')
    expect(c.currentFlags).toEqual(['start_early'])
  })

  it('flags are disabled until a verdict is set', () => {
    expect(c.flagsEnabled).toBe(false)
    expect(c.toggleFlag('end_late')).toBeNull()
    expect(c.currentFlags).toEqual([])
  })

  it('flags are disabled under not_play and unsure', () => {
    // No boundary to be wrong about on a span holding no rally.
    c.setVerdict('not_play')
    expect(c.flagsEnabled).toBe(false)
    expect(c.toggleFlag('end_late')).toBeNull()

    c.setVerdict('unsure')
    expect(c.flagsEnabled).toBe(false)
  })

  it('changing a verdict to not_play clears flags already set', () => {
    c.setVerdict('clean')
    c.toggleFlag('end_late')
    const action = c.setVerdict('not_play')
    expect(c.currentFlags).toEqual([])
    expect(action?.flags).toEqual([])
  })

  it('flags are enabled under partly', () => {
    c.setVerdict('partly')
    expect(c.flagsEnabled).toBe(true)
  })

  it('seeds from existing labels matched on the exact detector span', () => {
    const seeded = new LabelController(
      [rally(1), rally(2)],
      [record({ span_start_ms: 10000, span_end_ms: 18000, verdict: 'not_play' })],
    )
    expect(seeded.currentVerdict).toBe('not_play')
    seeded.next()
    expect(seeded.currentVerdict).toBeNull()
  })

  it('does not seed from a label whose span merely overlaps', () => {
    // A re-segment that moved this edge produced different detector output,
    // so the old judgement is not a judgement of this span. Matching by
    // overlap here would silently attribute a verdict to a clip nobody
    // watched.
    const seeded = new LabelController(
      [rally(1)],
      [record({ span_start_ms: 10200, span_end_ms: 18000, verdict: 'not_play' })],
    )
    expect(seeded.currentVerdict).toBeNull()
  })

  it('ignores a verdict-less boundary row when seeding', () => {
    // It carries a corrected span, not a judgement -- rendering it as a
    // verdict would invent one.
    const seeded = new LabelController(
      [rally(1)],
      [record({ verdict: null, boundary_flags: ['end_late'], true_end_ms: 17000 })],
    )
    expect(seeded.currentVerdict).toBeNull()
    expect(seeded.currentFlags).toEqual([])
  })

  it('labelledCount counts rallies with a verdict, seeded or set', () => {
    const seeded = new LabelController(
      [rally(1), rally(2), rally(3)],
      [record({ span_start_ms: 10000, span_end_ms: 18000 })],
    )
    expect(seeded.labelledCount).toBe(1)
    seeded.next()
    seeded.setVerdict('not_play')
    expect(seeded.labelledCount).toBe(2)
  })

  it('next and back move the cursor and clamp at both ends', () => {
    c.back()
    expect(c.index).toBe(0)
    c.next()
    c.next()
    c.next()
    c.next()
    expect(c.index).toBe(2)
  })

  it('undo restores the cursor and clears a first-time verdict', () => {
    c.setVerdict('clean')
    c.next()
    c.setVerdict('not_play')

    // Returns null: the restored state has no verdict, and the corpus is
    // append-only with no "unlabel" route, so there is nothing to persist.
    expect(c.undo()).toBeNull()
    expect(c.index).toBe(1)
    expect(c.currentVerdict).toBeNull()
  })

  it('undo of a changed verdict returns the restored verdict to persist', () => {
    c.setVerdict('clean')
    c.setVerdict('not_play')

    const action = c.undo()
    expect(action?.rallyId).toBe('r1')
    expect(action?.verdict).toBe('clean')
    expect(c.currentVerdict).toBe('clean')
  })

  it('undo returns null with nothing to undo', () => {
    expect(c.undo()).toBeNull()
  })

  it('revert restores exactly the failed action rallys state, wherever the cursor is', () => {
    // Mirrors QueueController.revert: a failed POST must not move the user's
    // position or consume their undo.
    const action = c.setVerdict('clean')!
    c.next()
    c.revert(action)
    expect(c.index).toBe(1)
    c.back()
    expect(c.currentVerdict).toBeNull()
  })
})
