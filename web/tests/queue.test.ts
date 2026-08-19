import { beforeEach, describe, expect, it } from 'vitest'
import { QueueController } from '../src/lib/queue'
import type { Rally } from '../src/lib/types'

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

describe('QueueController', () => {
  let q: QueueController

  beforeEach(() => {
    q = new QueueController([rally(1), rally(2), rally(3)])
  })

  it('starts on the first rally', () => {
    expect(q.current?.id).toBe('r1')
    expect(q.index).toBe(0)
    expect(q.total).toBe(3)
  })

  it('resumes at the first unreviewed rally', () => {
    const resumed = new QueueController([
      rally(1, { reviewed_at: '2026-08-19T10:00:00Z' }),
      rally(2, { reviewed_at: '2026-08-19T10:00:01Z' }),
      rally(3),
    ])
    expect(resumed.current?.id).toBe('r3')
  })

  it('advances on star and reports the action', () => {
    const action = q.star()
    expect(action).toEqual({ kind: 'star', rallyId: 'r1', starred: true, rejected: false })
    expect(q.current?.id).toBe('r2')
    expect(q.starredCount).toBe(1)
  })

  it('advances on reject', () => {
    q.reject()
    expect(q.current?.id).toBe('r2')
    expect(q.rejectedCount).toBe(1)
  })

  it('advances on skip without changing counts', () => {
    q.skip()
    expect(q.current?.id).toBe('r2')
    expect(q.starredCount).toBe(0)
    expect(q.rejectedCount).toBe(0)
  })

  it('goes back one', () => {
    q.skip()
    q.back()
    expect(q.current?.id).toBe('r1')
  })

  it('cannot go back past the start', () => {
    q.back()
    expect(q.index).toBe(0)
  })

  it('finishes after the last rally', () => {
    q.skip()
    q.skip()
    expect(q.finished).toBe(false)
    q.skip()
    expect(q.finished).toBe(true)
    expect(q.current).toBeUndefined()
  })

  it('exposes the next rally so it can be preloaded', () => {
    expect(q.nextRally?.id).toBe('r2')
    q.skip()
    expect(q.nextRally?.id).toBe('r3')
    q.skip()
    expect(q.nextRally).toBeUndefined()
  })

  it('undoes a star, restoring both position and count', () => {
    q.star()
    const undone = q.undo()
    expect(undone).toEqual({ kind: 'undo', rallyId: 'r1', starred: false, rejected: false })
    expect(q.current?.id).toBe('r1')
    expect(q.starredCount).toBe(0)
  })

  it('undoes a reject', () => {
    q.reject()
    q.undo()
    expect(q.current?.id).toBe('r1')
    expect(q.rejectedCount).toBe(0)
  })

  it('undoes a skip', () => {
    q.skip()
    q.undo()
    expect(q.current?.id).toBe('r1')
  })

  it('returns null when there is nothing to undo', () => {
    expect(q.undo()).toBeNull()
  })

  it('undoes repeatedly back to the start', () => {
    q.star()
    q.reject()
    q.skip()
    q.undo()
    q.undo()
    q.undo()
    expect(q.index).toBe(0)
    expect(q.starredCount).toBe(0)
    expect(q.rejectedCount).toBe(0)
  })

  it('toggles a star off when re-starred on the same rally', () => {
    q.star()
    q.back()
    const action = q.star()
    expect(action?.starred).toBe(false)
    expect(q.starredCount).toBe(0)
  })

  it('jumps to a rally by id', () => {
    q.jumpTo('r3')
    expect(q.current?.id).toBe('r3')
  })

  it('ignores a jump to an unknown id', () => {
    q.jumpTo('nope')
    expect(q.current?.id).toBe('r1')
  })

  it('estimates remaining time from the rallies left, scaled by speed', () => {
    // three 8s rallies, none seen: 24s at 1x, 12s at 2x
    expect(q.remainingMs(1)).toBe(24000)
    expect(q.remainingMs(2)).toBe(12000)
    q.skip()
    expect(q.remainingMs(1)).toBe(16000)
  })

  it('handles an empty rally list without throwing', () => {
    const empty = new QueueController([])
    expect(empty.finished).toBe(true)
    expect(empty.current).toBeUndefined()
    expect(empty.remainingMs(1)).toBe(0)
    expect(empty.star()).toBeNull()
  })

  it('skips rejected rallies when building the queue', () => {
    const withRejected = new QueueController([
      rally(1),
      rally(2, { rejected: 1 }),
      rally(3),
    ])
    expect(withRejected.total).toBe(2)
    withRejected.skip()
    expect(withRejected.current?.id).toBe('r3')
  })
})
