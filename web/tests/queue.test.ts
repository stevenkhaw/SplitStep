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
    // Fix 2 adds previousStarred/previousRejected (the pre-action flag
    // state) to QueueAction so a failed persist can be correlated back to
    // this specific action via revert() — the full shape now includes them.
    expect(action).toEqual({
      kind: 'star',
      rallyId: 'r1',
      starred: true,
      rejected: false,
      previousStarred: false,
      previousRejected: false,
    })
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

describe('QueueController live flags (Fix 1)', () => {
  let q: QueueController

  beforeEach(() => {
    q = new QueueController([rally(1), rally(2), rally(3)])
  })

  it('reports live starred state via isStarred/currentIsStarred after star+back', () => {
    q.star()
    q.back()
    expect(q.current?.id).toBe('r1')
    expect(q.isStarred(q.current!.id)).toBe(true)
    expect(q.currentIsStarred).toBe(true)
    expect(q.isRejected(q.current!.id)).toBe(false)
    expect(q.currentIsRejected).toBe(false)
  })

  it('reports live starred state as false after toggling off', () => {
    q.star()
    q.back()
    q.star()
    q.back()
    expect(q.isStarred('r1')).toBe(false)
    expect(q.currentIsStarred).toBe(false)
  })

  it('reports live rejected state via isRejected/currentIsRejected after reject+back', () => {
    q.reject()
    q.back()
    expect(q.isRejected(q.current!.id)).toBe(true)
    expect(q.currentIsRejected).toBe(true)
  })

  it('returns false for unknown rally ids', () => {
    expect(q.isStarred('nope')).toBe(false)
    expect(q.isRejected('nope')).toBe(false)
  })

  it('returns false for currentIsStarred/currentIsRejected when current is undefined', () => {
    q.skip()
    q.skip()
    q.skip()
    expect(q.current).toBeUndefined()
    expect(q.currentIsStarred).toBe(false)
    expect(q.currentIsRejected).toBe(false)
  })

  it('does not mutate the original Rally object on star', () => {
    const r = q.current!
    q.star()
    expect(r.starred).toBe(0)
  })
})

describe('QueueController revert (Fix 2)', () => {
  let q: QueueController

  beforeEach(() => {
    q = new QueueController([rally(1), rally(2), rally(3)])
  })

  it('reverts only the targeted rally, leaving index and other rallies untouched', () => {
    const a1 = q.star() // r1
    q.reject() // r2
    q.star() // r3
    expect(q.index).toBe(3)

    q.revert(a1!)

    expect(q.index).toBe(3) // index untouched
    expect(q.isStarred('r1')).toBe(false) // reverted to pre-action state
    expect(q.isRejected('r2')).toBe(true) // untouched
    expect(q.isStarred('r3')).toBe(true) // untouched
  })

  it('is idempotent when called again on an already-reverted action', () => {
    const a1 = q.star()
    q.revert(a1!)
    expect(q.isStarred('r1')).toBe(false)
    q.revert(a1!)
    expect(q.isStarred('r1')).toBe(false)
  })

  it('does not consume undo history', () => {
    const a1 = q.star() // r1
    q.reject() // r2

    q.revert(a1!)

    // undo() should still see both history entries, undoing the most
    // recent (reject on r2) first.
    const undone = q.undo()
    expect(undone?.rallyId).toBe('r2')
    expect(q.isRejected('r2')).toBe(false)
    expect(q.index).toBe(1)
  })

  it('populates previousStarred/previousRejected on star, reject and skip', () => {
    const a1 = q.star()
    expect(a1).toMatchObject({ previousStarred: false, previousRejected: false })

    q.back()
    const a2 = q.reject()
    expect(a2).toMatchObject({ previousStarred: true, previousRejected: false })

    const a3 = q.skip()
    expect(a3).toMatchObject({ previousStarred: false, previousRejected: false })
  })
})

describe('QueueController remainingMs excludes rejected rallies (Fix 3)', () => {
  it('does not count a rejected rally toward the remaining estimate after back()', () => {
    const q = new QueueController([rally(1), rally(2), rally(3)])
    // three 8s rallies: 24s total at 1x
    expect(q.remainingMs(1)).toBe(24000)

    q.reject() // rejects r1, advances to r2
    // r1 is now rejected but still occupies index 0; back() moves onto it
    q.back()
    expect(q.current?.id).toBe('r1')

    // Only r2 + r3 (16s) should count, even though the cursor sits back on
    // the rejected r1.
    expect(q.remainingMs(1)).toBe(16000)
  })
})
