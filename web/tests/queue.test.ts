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
    point: 0,
    reviewed_at: null,
    note: '',
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
      point: false,
      previousStarred: false,
      previousRejected: false,
      previousPoint: false,
    })
    expect(q.current?.id).toBe('r1') // stays put: only skip() advances
    expect(q.starredCount).toBe(1)
  })

  it('records a reject and reports the action', () => {
    q.reject()
    expect(q.current?.id).toBe('r1') // stays put: only skip() advances
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
    expect(undone).toEqual({
      kind: 'undo',
      rallyId: 'r1',
      starred: false,
      rejected: false,
      point: false,
    })
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
    // skip() is what advances now, so the walk is explicit
    const a1 = q.star()
    q.skip() // -> r2
    q.reject()
    q.skip() // -> r3
    q.star()
    q.skip() // -> past the end
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
    q.skip() // -> r2
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

    q.skip() // leave r1, which now carries a reject
    const a3 = q.skip() // r2, untouched
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

describe('QueueController discriminated QueueAction (Fix pass 2)', () => {
  it('runtime: PersistableAction carries previous* fields, UndoAction does not', () => {
    const q = new QueueController([rally(1), rally(2), rally(3)])

    const starAction = q.star()!
    expect(starAction.kind).toBe('star')
    expect('previousStarred' in starAction).toBe(true)
    expect('previousRejected' in starAction).toBe(true)

    const rejectAction = q.reject()!
    expect(rejectAction.kind).toBe('reject')
    expect('previousStarred' in rejectAction).toBe(true)

    const skipAction = q.skip()!
    expect(skipAction.kind).toBe('skip')
    expect('previousRejected' in skipAction).toBe(true)

    const undoAction = q.undo()!
    expect(undoAction.kind).toBe('undo')
    // UndoAction has no previous* state at all, not even set to undefined —
    // this is what makes revert(undoAction) a compile error below rather
    // than a silent "reads undefined as falsy" unstar at runtime.
    expect('previousStarred' in undoAction).toBe(false)
    expect('previousRejected' in undoAction).toBe(false)
  })

  it('compile-time: revert() rejects an UndoAction (would otherwise silently unstar)', () => {
    const q = new QueueController([rally(1), rally(2), rally(3)])
    q.star() // r1
    const undoAction = q.undo()! // undoes the star; a plain UndoAction
    expect(undoAction.kind).toBe('undo')

    // @ts-expect-error revert() accepts PersistableAction only. PersistableAction
    // requires previousStarred/previousRejected; UndoAction has neither, so
    // passing the undo() result here must fail to type-check. Before the
    // PersistableAction/UndoAction split this compiled cleanly and silently
    // unstarred the rally (undefined read as falsy). If this stops being a
    // type error, svelte-check will fail with "Unused '@ts-expect-error'
    // directive", which is the guard this test relies on.
    q.revert(undoAction)
  })

  it('still accepts a genuine PersistableAction in revert()', () => {
    const q = new QueueController([rally(1), rally(2), rally(3)])
    const starAction = q.star()! // PersistableAction, no ts-expect-error needed
    q.revert(starAction)
    expect(q.isStarred('r1')).toBe(false)
  })
})

describe('QueueController.liveSnapshot (Finding: OverviewBand rendered stale colors)', () => {
  it('overwrites starred/rejected from this session live state, not the snapshot the Rally was constructed with', () => {
    const q = new QueueController([rally(1), rally(2), rally(3)])
    q.star() // r1, live-only -- the Rally objects are never mutated
    q.skip() // -> r2
    q.reject() // r2, live-only

    const snap = q.liveSnapshot([rally(1), rally(2), rally(3)])
    expect(snap.find((r) => r.id === 'r1')?.starred).toBe(1)
    expect(snap.find((r) => r.id === 'r2')?.rejected).toBe(1)
    expect(snap.find((r) => r.id === 'r3')?.starred).toBe(0)
    expect(snap.find((r) => r.id === 'r3')?.rejected).toBe(0)
  })

  it('includes rallies this controller has itself rejected out of the active queue -- OverviewBand still needs to place them', () => {
    const q = new QueueController([rally(1), rally(2)])
    q.reject() // r1 leaves #rallies/current entirely

    const snap = q.liveSnapshot([rally(1), rally(2)])
    expect(snap.map((r) => r.id)).toEqual(['r1', 'r2'])
    expect(snap.find((r) => r.id === 'r1')?.rejected).toBe(1)
  })

  it('does not mutate the Rally objects passed in -- the class-level "never mutate" invariant', () => {
    const q = new QueueController([rally(1)])
    const original = rally(1)
    q.star()
    q.liveSnapshot([original])
    expect(original.starred).toBe(0)
  })

  it('reflects an already-server-starred rally that this session never touched', () => {
    const q = new QueueController([rally(1, { starred: 1 })])
    const snap = q.liveSnapshot([rally(1, { starred: 1 })])
    expect(snap[0].starred).toBe(1)
  })
})

describe('QueueController — non-advancing review (2026-08-20 review UX spec)', () => {
  let q: QueueController

  beforeEach(() => {
    q = new QueueController([rally(1), rally(2), rally(3)])
  })

  it('leaves the cursor where it is when starring', () => {
    q.star()
    expect(q.index).toBe(0)
    expect(q.current?.id).toBe('r1')
    expect(q.isStarred('r1')).toBe(true)
  })

  it('leaves the cursor where it is when rejecting', () => {
    q.reject()
    expect(q.index).toBe(0)
    expect(q.isRejected('r1')).toBe(true)
  })

  it('toggles a reject off on a second press', () => {
    q.reject()
    const second = q.reject()
    expect(q.isRejected('r1')).toBe(false)
    expect(second?.rejected).toBe(false)
  })

  it('does not clear a reject when skipping past it', () => {
    // Before the cursor changes landed, reject() advanced immediately so this
    // sequence was unreachable. Now `→` lands on a rally the user just
    // rejected, and skip() hard-coded `rejected: false`.
    q.reject()
    const action = q.skip()
    expect(action?.rejected).toBe(true)
    expect(q.isRejected('r1')).toBe(true)
  })

  it('does not clear a star when skipping past it', () => {
    q.star()
    const action = q.skip()
    expect(action?.starred).toBe(true)
    expect(q.isStarred('r1')).toBe(true)
  })

  it('advances only on skip', () => {
    q.star()
    q.reject()
    expect(q.index).toBe(0)
    q.skip()
    expect(q.index).toBe(1)
  })

  it('undoes a non-advancing reject back to its prior flags', () => {
    q.reject()
    q.undo()
    expect(q.index).toBe(0)
    expect(q.isRejected('r1')).toBe(false)
  })
})

describe('QueueController.point', () => {
  let q: QueueController

  beforeEach(() => {
    q = new QueueController([rally(1), rally(2), rally(3)])
  })

  it('point() toggles and does not advance', () => {
    const action = q.point()
    expect(action?.kind).toBe('point')
    expect(action?.point).toBe(true)
    expect(q.index).toBe(0)
    expect(q.currentIsPoint).toBe(true)

    expect(q.point()?.point).toBe(false)
    expect(q.currentIsPoint).toBe(false)
  })

  it('point is independent of star', () => {
    // A highlight is a subset of points in practice but not by construction:
    // a warm-up rally can be worth watching without being a point.
    q.point()
    q.star()
    expect(q.currentIsPoint).toBe(true)
    expect(q.currentIsStarred).toBe(true)
  })

  it('seeds pointCount from the server snapshot', () => {
    const seeded = new QueueController([rally(1, { point: 1 }), rally(2)])
    expect(seeded.pointCount).toBe(1)
  })

  it('undo restores the previous point state', () => {
    q.point()
    q.undo()
    expect(q.currentIsPoint).toBe(false)
  })

  it('revert restores the failed action rallys point without moving the cursor', () => {
    const action = q.point()!
    q.skip()
    q.revert(action)
    expect(q.index).toBe(1)
    q.back()
    expect(q.currentIsPoint).toBe(false)
  })

  it('skip carries point through untouched', () => {
    // Same reasoning as starred/rejected: the right arrow is the only way
    // forward, so it lands on rallies the user has just flagged and must not
    // silently clear one.
    q.point()
    const action = q.skip()
    expect(action?.point).toBe(true)
  })
})
