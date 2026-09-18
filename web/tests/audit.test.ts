import { describe, expect, it, vi } from 'vitest'
import {
  AuditController,
  spanKey,
  spanLabelApi,
  parseSpanKey,
} from '../src/lib/audit'
import type { SampleWindow } from '../src/lib/types'

function windows(): SampleWindow[] {
  return [
    { start_ms: 25_000, end_ms: 33_000 },
    { start_ms: 90_000, end_ms: 98_000 },
    { start_ms: 200_000, end_ms: 208_000 },
  ]
}

describe('spanKey', () => {
  it('round-trips a span', () => {
    expect(parseSpanKey(spanKey(25_000, 33_000))).toEqual({
      startMs: 25_000,
      endMs: 33_000,
    })
  })

  it('is distinct for spans sharing a start', () => {
    expect(spanKey(0, 8000)).not.toBe(spanKey(0, 9000))
  })
})

describe('AuditController', () => {
  it('opens on the first window', () => {
    const c = new AuditController(windows())
    expect(c.index).toBe(0)
    expect(c.total).toBe(3)
    expect(c.current).toEqual({ start_ms: 25_000, end_ms: 33_000 })
  })

  it('reports no verdict for a window nobody has judged', () => {
    expect(new AuditController(windows()).verdict).toBeNull()
  })

  it('records a verdict and advances', () => {
    // A labelling pass is a walk: stopping on each judged window would make
    // every window cost two keystrokes instead of one.
    const c = new AuditController(windows())
    c.setVerdict('clean')
    expect(c.index).toBe(1)
    expect(c.verdictAt(0)).toBe('clean')
  })

  it('stays on the last window rather than advancing past the end', () => {
    const c = new AuditController(windows())
    c.jumpTo(2)
    c.setVerdict('not_play')
    expect(c.index).toBe(2)
    expect(c.verdictAt(2)).toBe('not_play')
  })

  it('returns the action a writer needs, carrying the previous state', () => {
    const c = new AuditController(windows())
    c.setVerdict('clean')
    c.jumpTo(0)
    const action = c.setVerdict('partly')
    expect(action).toMatchObject({
      rallyId: spanKey(25_000, 33_000),
      verdict: 'partly',
      previousVerdict: 'clean',
    })
  })

  it('counts how many windows have been judged', () => {
    const c = new AuditController(windows())
    expect(c.judged).toBe(0)
    c.setVerdict('clean')
    c.setVerdict('not_play')
    expect(c.judged).toBe(2)
  })

  it('seeds from labels already in the corpus, so a reload resumes', () => {
    // The sample itself is recomputed from a seed rather than stored; the
    // judgements are what persist, and they come back keyed by span.
    const c = new AuditController(windows(), [
      { span_start_ms: 90_000, span_end_ms: 98_000, verdict: 'not_play' },
    ])
    expect(c.verdictAt(1)).toBe('not_play')
    expect(c.judged).toBe(1)
  })

  it('ignores a seeded label for a span that is not in this sample', () => {
    // A different seed, or a source labelled through the review queue: those
    // rows are real, but they are not windows of this pass.
    const c = new AuditController(windows(), [
      { span_start_ms: 1000, span_end_ms: 5000, verdict: 'clean' },
    ])
    expect(c.judged).toBe(0)
  })

  it('undoes the last verdict and returns to the window it was on', () => {
    const c = new AuditController(windows())
    c.setVerdict('clean')
    c.setVerdict('not_play')
    const action = c.undo()
    expect(c.index).toBe(1)
    expect(c.verdictAt(1)).toBeNull()
    expect(action).toMatchObject({ verdict: null, previousVerdict: 'not_play' })
  })

  it('undoes back to a previous verdict rather than to nothing, when there was one', () => {
    const c = new AuditController(windows())
    c.setVerdict('clean')
    c.jumpTo(0)
    c.setVerdict('partly')
    c.undo()
    expect(c.verdictAt(0)).toBe('clean')
  })

  it('does nothing when there is nothing to undo', () => {
    const c = new AuditController(windows())
    expect(c.undo()).toBeNull()
    expect(c.index).toBe(0)
  })

  it('restores the state the server last accepted after a failed write', () => {
    const c = new AuditController(windows())
    c.setVerdict('clean')
    c.restore(spanKey(25_000, 33_000), { verdict: null, flags: [] })
    expect(c.verdictAt(0)).toBeNull()
  })

  it('is done only when every window has been judged', () => {
    const c = new AuditController(windows())
    c.setVerdict('clean')
    c.setVerdict('clean')
    expect(c.done).toBe(false)
    c.setVerdict('clean')
    expect(c.done).toBe(true)
  })
})

describe('spanLabelApi', () => {
  it('sends a verdict to the span-addressed route', async () => {
    const api = { spanLabel: vi.fn().mockResolvedValue({}), spanRetract: vi.fn() }
    await spanLabelApi('src1', api).label(spanKey(25_000, 33_000), 'clean', [])
    expect(api.spanLabel).toHaveBeenCalledWith('src1', 25_000, 33_000, 'clean', [])
  })

  it('sends an undo to the retract route', async () => {
    const api = { spanLabel: vi.fn(), spanRetract: vi.fn().mockResolvedValue({}) }
    await spanLabelApi('src1', api).retractLabel(spanKey(90_000, 98_000))
    expect(api.spanRetract).toHaveBeenCalledWith('src1', 90_000, 98_000)
  })
})
