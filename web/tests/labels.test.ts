import { beforeEach, describe, expect, it } from 'vitest'
import { LabelController, persistLabel } from '../src/lib/labels'
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
    source_id: 'src',
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

  it('does not seed a label from one source onto another source\'s rally at the same span', () => {
    // M2 regression. Sources are independent clips whose timelines each
    // start at 0, and segment() places every edge on a fixed sample grid,
    // so two sources can produce rallies with an identical
    // (det_start_ms, det_end_ms) pair -- guaranteed at the start edge for
    // any rally within the start pad, since the clamp puts both at 0.
    // LabelMode fetches labels per source and flattens them into one list
    // before handing it to the controller, so a span-only key would let
    // source A's verdict render on source B's rally of the same span --
    // exactly what exact-span matching (see the constructor doc comment)
    // exists to prevent, reintroduced through a different door.
    const rallyA = rally(1, { id: 'rA', source_id: 'srcA', det_start_ms: 0, det_end_ms: 8000 })
    const rallyB = rally(1, { id: 'rB', source_id: 'srcB', det_start_ms: 0, det_end_ms: 8000 })
    const labelA = record({ source_id: 'srcA', span_start_ms: 0, span_end_ms: 8000, verdict: 'clean' })

    const seeded = new LabelController([rallyA, rallyB], [labelA])
    expect(seeded.currentVerdict).toBe('clean') // rA: source A's own label
    seeded.next()
    expect(seeded.currentVerdict).toBeNull() // rB: must not inherit it
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

  it('mutating the array returned by currentFlags does not change controller state', () => {
    c.setVerdict('clean')
    c.toggleFlag('start_early')
    const f = c.currentFlags
    f.push('end_late')
    expect(c.currentFlags).toEqual(['start_early'])
  })

  it('mutating action.previousFlags does not change controller state', () => {
    c.setVerdict('clean')
    c.toggleFlag('start_early')
    const action = c.setVerdict('partly')!
    action.previousFlags.push('end_late')
    expect(c.currentFlags).toEqual(['start_early'])
  })

  it('revert restores the true pre-action flags even if a caller mutated a returned array in between', () => {
    c.setVerdict('clean')
    c.toggleFlag('start_early')
    const action = c.setVerdict('partly')!
    // Simulate some other part of the app holding currentFlags (e.g. for
    // rendering) and mutating it before the failed POST is known about.
    // Before the fix this is the same array object as action.previousFlags,
    // so the mutation silently corrupts the snapshot revert() depends on.
    c.currentFlags.push('end_late')
    c.revert(action)
    expect(c.currentFlags).toEqual(['start_early'])
  })
})

describe('persisting a label', () => {
  it('sends the verdict and flags for the action rally', async () => {
    const calls: Array<[string, string, string[]]> = []
    const fakeApi = {
      label: async (id: string, verdict: string, flags: string[]) => {
        calls.push([id, verdict, flags])
        return {}
      },
    }
    const c = new LabelController([rally(1)], [])
    const action = c.setVerdict('partly')!
    await persistLabel(action, fakeApi)
    expect(calls).toEqual([['r1', 'partly', []]])
  })

  it('reports a failure so the caller can revert', async () => {
    const fakeApi = {
      label: async () => {
        throw new Error('offline')
      },
    }
    const c = new LabelController([rally(1)], [])
    const action = c.setVerdict('clean')!
    expect(await persistLabel(action, fakeApi)).toEqual({ ok: false })
  })
})
