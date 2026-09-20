import { describe, expect, it } from 'vitest'
import { applyAdd, applyMerge, applySplit, canMerge, canSplit, findMergePrev } from '../src/lib/split'
import type { Rally } from '../src/lib/types'

function rally(overrides: Partial<Rally> = {}): Rally {
  return {
    id: 'r1',
    session_id: 's1',
    source_id: 'src1',
    idx: 1,
    start_ms: 1000,
    end_ms: 9000,
    det_start_ms: 1000,
    det_end_ms: 9000,
    confidence: 0.8,
    starred: 0,
    rejected: 0,
    point: 0,
    reviewed_at: null,
    seen_at: null,
    note: '',
    winner: '',
    ...overrides,
  }
}

describe('canSplit', () => {
  it('allows a cut with room for both halves', () => {
    expect(canSplit(rally(), 5000)).toBe(true)
  })

  it('refuses a cut that would leave a half under MIN_RALLY_MS', () => {
    // The client floor, which the server deliberately does not enforce --
    // it rejects only the incoherent (a zero-length half), leaving "merely
    // tiny" to be discouraged here, exactly as /bounds and clampMinGap
    // already divide the same question.
    expect(canSplit(rally(), 1050)).toBe(false)
    expect(canSplit(rally(), 8950)).toBe(false)
  })

  it('refuses a cut at or outside the boundaries', () => {
    expect(canSplit(rally(), 1000)).toBe(false)
    expect(canSplit(rally(), 9000)).toBe(false)
    expect(canSplit(rally(), 20000)).toBe(false)
  })
})

describe('canMerge', () => {
  const made = rally({ id: 'r2', idx: 2, start_ms: 5000, det_start_ms: null, det_end_ms: null })
  const prev = rally({ id: 'r1', idx: 1, start_ms: 1000, end_ms: 5000 })

  it('allows a hand-made half abutting its predecessor', () => {
    expect(canMerge(made, prev)).toBe(true)
  })

  it('refuses a rally carrying a detector span', () => {
    expect(canMerge(rally({ id: 'r2', start_ms: 5000 }), prev)).toBe(false)
  })

  it('refuses when there is no predecessor', () => {
    expect(canMerge(made, undefined)).toBe(false)
  })

  it('refuses a predecessor in another source', () => {
    expect(canMerge(made, rally({ source_id: 'src2', end_ms: 5000 }))).toBe(false)
  })

  it('refuses a predecessor that no longer abuts', () => {
    expect(canMerge(made, rally({ end_ms: 4000 }))).toBe(false)
  })
})

describe('applySplit', () => {
  it('replaces one rally with two abutting halves', () => {
    const out = applySplit([rally()], 'r1', 5000, 'new', ['src1'])
    expect(out).toHaveLength(2)
    expect([out[0].start_ms, out[0].end_ms]).toEqual([1000, 5000])
    expect([out[1].start_ms, out[1].end_ms]).toEqual([5000, 9000])
    expect(out[1].id).toBe('new')
  })

  it('gives the new half no detector span and leaves the first half its own', () => {
    const out = applySplit([rally()], 'r1', 5000, 'new', ['src1'])
    expect([out[0].det_start_ms, out[0].det_end_ms]).toEqual([1000, 9000])
    expect(out[1].det_start_ms).toBeNull()
    expect(out[1].det_end_ms).toBeNull()
  })

  it('inherits review flags and nulls nothing the server keeps', () => {
    const out = applySplit(
      [rally({ starred: 1, point: 1, note: 'late backhand', seen_at: 'T0' })],
      'r1', 5000, 'new', ['src1'],
    )
    expect(out[1].starred).toBe(1)
    expect(out[1].point).toBe(1)
    expect(out[1].note).toBe('late backhand')
    expect(out[1].seen_at).toBe('T0')
  })

  it('renumbers idx the way the server does — source order, then start_ms', () => {
    // The parity that matters. splitstep/db/rallies.py::_renumber orders by
    // sources.idx then start_ms; a client that renumbered differently would
    // print an idx the server disagrees with, on a field the reviewer reads
    // out loud.
    const rallies = [
      rally({ id: 'a1', source_id: 'src1', idx: 1, start_ms: 1000, end_ms: 9000 }),
      rally({ id: 'a2', source_id: 'src1', idx: 2, start_ms: 20000, end_ms: 25000 }),
      rally({ id: 'b1', source_id: 'src2', idx: 3, start_ms: 1000, end_ms: 4000 }),
    ]
    const sourceOrder = ['src1', 'src2']
    const out = applySplit(rallies, 'a1', 5000, 'new', sourceOrder)
    expect(out.map((r) => [r.id, r.idx])).toEqual([
      ['a1', 1], ['new', 2], ['a2', 3], ['b1', 4],
    ])
  })

  it('returns the list unchanged for an unknown id', () => {
    const input = [rally()]
    expect(applySplit(input, 'nope', 5000, 'new', ['src1'])).toEqual(input)
  })

  it('keeps server source order when the target is its source only rally', () => {
    // The case the parity test above cannot see: with src1 contributing just
    // the split target, a first-appearance ordering would move src1 behind
    // src2, because applySplit removes the target before renumbering. That is
    // why sourceOrder is required rather than inferred.
    const rallies = [
      rally({ id: 'a1', source_id: 'src1', idx: 1, start_ms: 1000, end_ms: 9000 }),
      rally({ id: 'b1', source_id: 'src2', idx: 2, start_ms: 1000, end_ms: 4000 }),
      rally({ id: 'b2', source_id: 'src2', idx: 3, start_ms: 8000, end_ms: 9000 }),
    ]
    const out = applySplit(rallies, 'a1', 5000, 'new', ['src1', 'src2'])
    expect(out.map((r) => [r.id, r.idx])).toEqual([
      ['a1', 1], ['new', 2], ['b1', 3], ['b2', 4],
    ])
  })
})

describe('findMergePrev', () => {
  // A manual drag can leave two rallies in one source sharing an end_ms --
  // /bounds only checks end_ms > start_ms for the row being dragged, not
  // that it stays clear of its neighbours. When that end_ms also equals the
  // target's start_ms, both rows "abut" by a raw scan and only sorted
  // adjacency -- the server's own tiebreak -- picks the one actually next
  // to the target. Proven both ways round so a regression that quietly goes
  // back to array order shows up regardless of which row happens first in
  // `rallies`.
  const early = rally({ id: 'early', source_id: 'src1', start_ms: 1000, end_ms: 5000 })
  const late = rally({ id: 'late', source_id: 'src1', start_ms: 3000, end_ms: 5000 })
  const target = rally({
    id: 'target',
    source_id: 'src1',
    start_ms: 5000,
    end_ms: 9000,
    det_start_ms: null,
    det_end_ms: null,
  })

  it('picks the later-starting of two tied predecessors -- late first in the array', () => {
    expect(findMergePrev([late, early, target], target, ['src1'])?.id).toBe('late')
  })

  it('picks the later-starting of two tied predecessors -- late last in the array', () => {
    expect(findMergePrev([target, early, late], target, ['src1'])?.id).toBe('late')
  })
})

describe('applyMerge', () => {
  it('absorbs the half into its predecessor and renumbers', () => {
    const rallies = applySplit([rally()], 'r1', 5000, 'new', ['src1'])
    const out = applyMerge(rallies, 'new', ['src1'])
    expect(out).toHaveLength(1)
    expect([out[0].id, out[0].start_ms, out[0].end_ms]).toEqual(['r1', 1000, 9000])
    expect(out[0].idx).toBe(1)
  })

  it('leaves the list alone when the merge is not allowed', () => {
    const input = [rally()]
    expect(applyMerge(input, 'r1', ['src1'])).toEqual(input)
  })

  // Same tied setup as findMergePrev above: if applyMerge and the picker
  // it now shares ever drifted apart, this would merge into `early` while
  // the component landed the reviewer on `late` (or vice versa) -- silently
  // wrong on a row that looks perfectly normal afterwards.
  it('merges into the later-starting predecessor when two rows tie on end_ms, leaving the earlier one untouched', () => {
    const early = rally({ id: 'early', idx: 1, source_id: 'src1', start_ms: 1000, end_ms: 5000 })
    const late = rally({ id: 'late', idx: 2, source_id: 'src1', start_ms: 3000, end_ms: 5000 })
    const target = rally({
      id: 'target',
      idx: 3,
      source_id: 'src1',
      start_ms: 5000,
      end_ms: 9000,
      det_start_ms: null,
      det_end_ms: null,
    })
    const out = applyMerge([early, late, target], 'target', ['src1'])
    expect(out).toHaveLength(2)
    const merged = out.find((r) => r.id === 'late')
    expect(merged && [merged.start_ms, merged.end_ms]).toEqual([3000, 9000])
    const untouched = out.find((r) => r.id === 'early')
    expect(untouched && [untouched.start_ms, untouched.end_ms]).toEqual([1000, 5000])
  })
})

describe('applyAdd', () => {
  const sourceOrder = ['src1', 'src2']

  it('inserts the new rally in start_ms order and renumbers around it', () => {
    const before = [
      rally({ id: 'a', idx: 1, start_ms: 1000, end_ms: 5000 }),
      rally({ id: 'b', idx: 2, start_ms: 20000, end_ms: 26000 }),
    ]
    const after = applyAdd(
      before,
      { id: 'new', sessionId: 's1', sourceId: 'src1', startMs: 9000, endMs: 12000 },
      sourceOrder,
    )
    expect(after.map((r) => [r.id, r.idx])).toEqual([
      ['a', 1],
      ['new', 2],
      ['b', 3],
    ])
  })

  // The absence of a detector span IS the "a human made this" marker (see
  // the module comment), and every consequence -- merge accepting it,
  // /label refusing it, LabelController filtering it out -- reads this and
  // nothing else.
  it('carries no detector span', () => {
    const [added] = applyAdd(
      [],
      { id: 'new', sessionId: 's1', sourceId: 'src1', startMs: 9000, endMs: 12000 },
      sourceOrder,
    )
    expect(added.det_start_ms).toBeNull()
    expect(added.det_end_ms).toBeNull()
  })

  // Nobody said this span is a point, a favourite or a mistake. A default
  // that claimed any of those would be a judgement the reviewer never made.
  it('starts plain -- no star, no point, no rejection, no note, no winner', () => {
    const [added] = applyAdd(
      [],
      { id: 'new', sessionId: 's1', sourceId: 'src1', startMs: 9000, endMs: 12000 },
      sourceOrder,
    )
    expect(added.starred).toBe(0)
    expect(added.point).toBe(0)
    expect(added.rejected).toBe(0)
    expect(added.note).toBe('')
    expect(added.winner).toBe('')
    expect(added.reviewed_at).toBeNull()
    expect(added.seen_at).toBeNull()
    expect(added.confidence).toBe(0)
  })

  it("takes the server's id, not one invented here", () => {
    const [added] = applyAdd(
      [],
      { id: 'from-server', sessionId: 's1', sourceId: 'src1', startMs: 9000, endMs: 12000 },
      sourceOrder,
    )
    expect(added.id).toBe('from-server')
  })

  // Same rule _renumber uses: source idx first, then start_ms. A span added
  // to the first source renumbers the second source's rallies too.
  it('renumbers across the whole session, not just the source it lands in', () => {
    const before = [
      rally({ id: 'a', idx: 1, source_id: 'src1', start_ms: 1000, end_ms: 5000 }),
      rally({ id: 'b', idx: 2, source_id: 'src2', start_ms: 1000, end_ms: 5000 }),
    ]
    const after = applyAdd(
      before,
      { id: 'new', sessionId: 's1', sourceId: 'src1', startMs: 9000, endMs: 12000 },
      sourceOrder,
    )
    expect(after.map((r) => [r.id, r.idx])).toEqual([
      ['a', 1],
      ['new', 2],
      ['b', 3],
    ])
  })
})
