import { describe, expect, it, vi } from 'vitest'
import { describePersistFailure, persistAction } from '../src/lib/persist'
import type { PersistApi } from '../src/lib/persist'
import type { PersistableAction, UndoAction } from '../src/lib/queue'

function api(overrides: Partial<PersistApi> = {}): PersistApi {
  return {
    star: vi.fn().mockResolvedValue(undefined),
    reject: vi.fn().mockResolvedValue(undefined),
    point: vi.fn().mockResolvedValue(undefined),
    seen: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  }
}

const starAction: PersistableAction = {
  kind: 'star',
  rallyId: 'r1',
  starred: true,
  rejected: false,
  point: false,
  previousStarred: false,
  previousRejected: false,
  previousPoint: false,
}

const rejectAction: PersistableAction = {
  kind: 'reject',
  rallyId: 'r2',
  starred: false,
  rejected: true,
  point: false,
  previousStarred: false,
  previousRejected: false,
  previousPoint: false,
}

const skipAction: PersistableAction = {
  kind: 'skip',
  rallyId: 'r3',
  starred: false,
  rejected: false,
  point: false,
  previousStarred: false,
  previousRejected: false,
  previousPoint: false,
}

const pointAction: PersistableAction = {
  kind: 'point',
  rallyId: 'r1',
  starred: false,
  rejected: false,
  point: true,
  previousStarred: false,
  previousRejected: false,
  previousPoint: false,
}

const undoAction: UndoAction = {
  kind: 'undo',
  rallyId: 'r4',
  starred: true,
  rejected: false,
  point: false,
}

describe('persistAction', () => {
  it('calls api.star for a star action', async () => {
    const a = api()
    const outcome = await persistAction(starAction, a)
    expect(a.star).toHaveBeenCalledWith('r1', true)
    expect(outcome).toEqual({ ok: true })
  })

  it('calls api.reject for a reject action', async () => {
    const a = api()
    await persistAction(rejectAction, a)
    expect(a.reject).toHaveBeenCalledWith('r2', true)
  })

  it('persists a point action', async () => {
    const a = api()
    const outcome = await persistAction(pointAction, a)
    expect(a.point).toHaveBeenCalledWith('r1', true)
    expect(outcome).toEqual({ ok: true })
  })

  it('calls api.star, api.reject and api.point, in order, for an undo action', async () => {
    const calls: string[] = []
    const a = api({
      star: vi.fn().mockImplementation(async () => {
        calls.push('star')
      }),
      reject: vi.fn().mockImplementation(async () => {
        calls.push('reject')
      }),
      point: vi.fn().mockImplementation(async () => {
        calls.push('point')
      }),
    })
    await persistAction(undoAction, a)
    expect(a.star).toHaveBeenCalledWith('r4', true)
    expect(a.reject).toHaveBeenCalledWith('r4', false)
    expect(a.point).toHaveBeenCalledWith('r4', false)
    expect(calls).toEqual(['star', 'reject', 'point'])
  })

  it('reports the failed action as the thing to revert when a star persist fails', async () => {
    const a = api({ star: vi.fn().mockRejectedValue(new Error('network down')) })
    const outcome = await persistAction(starAction, a)
    expect(outcome).toEqual({ ok: false, revert: starAction })
  })

  it('reports the failed action as the thing to revert when a reject persist fails', async () => {
    const a = api({ reject: vi.fn().mockRejectedValue(new Error('network down')) })
    const outcome = await persistAction(rejectAction, a)
    expect(outcome).toEqual({ ok: false, revert: rejectAction })
  })

  it('calls api.seen, not api.star/reject, for a skip action', async () => {
    // A skip stamps seen_at (via api.seen), deliberately NOT reviewed_at --
    // see persist.ts's skip case. Master's version of this test asserted a
    // skip "cannot fail because it reaches the network at all"; it reaches
    // the network again now, and the failure path it was guarding against
    // has its own test directly below.
    const a = api()
    const outcome = await persistAction(skipAction, a)
    expect(a.seen).toHaveBeenCalledWith('r3')
    expect(a.star).not.toHaveBeenCalled()
    expect(a.reject).not.toHaveBeenCalled()
    expect(outcome).toEqual({ ok: true })
  })

  it('reports the failed action as the thing to revert when a skip persist fails', async () => {
    const a = api({ seen: vi.fn().mockRejectedValue(new Error('network down')) })
    const outcome = await persistAction(skipAction, a)
    expect(outcome).toEqual({ ok: false, revert: skipAction })
  })

  it('reports revert: null when an undo persist fails -- UndoAction cannot be reverted', async () => {
    const a = api({ star: vi.fn().mockRejectedValue(new Error('network down')) })
    const outcome = await persistAction(undoAction, a)
    expect(outcome).toEqual({ ok: false, revert: null })
  })

  it('reports revert: null when the second half (reject) of an undo persist fails', async () => {
    const a = api({ reject: vi.fn().mockRejectedValue(new Error('network down')) })
    const outcome = await persistAction(undoAction, a)
    expect(outcome).toEqual({ ok: false, revert: null })
  })
})

describe('describePersistFailure', () => {
  it('names the rally number and the reverted action', () => {
    expect(describePersistFailure(starAction, 7)).toBe("Couldn't save star on rally 7 -- reverted")
    expect(describePersistFailure(rejectAction, 2)).toBe("Couldn't save reject on rally 2 -- reverted")
    expect(describePersistFailure(skipAction, 3)).toBe("Couldn't save skip on rally 3 -- reverted")
  })

  it('falls back to a generic label when the rally number is unknown', () => {
    expect(describePersistFailure(starAction)).toBe("Couldn't save star on a rally -- reverted")
  })

  it('describes an undo failure without claiming anything was reverted', () => {
    expect(describePersistFailure(undoAction, 4)).toBe("Couldn't save undo on rally 4 -- please retry")
  })
})

describe('persistAction — skip stamps seen_at, never reviewed_at', () => {
  const skipAction: PersistableAction = {
    kind: 'skip',
    rallyId: 'r3',
    starred: false,
    rejected: false,
    point: false,
    previousStarred: false,
    previousRejected: false,
    previousPoint: false,
  }

  it('calls only api.seen and reports success', async () => {
    // `→` is pressed on every clip, so persisting it as "reviewed" would
    // mark a whole session reviewed just for walking through it -- still
    // true, and still why only S/X/P stamp reviewed_at. Skip stamps the
    // separate seen_at column, which is what the queue resumes from
    // (QueueController's firstUnseen). It does move session status, since
    // status reads seen_at now: skimming a pass end to end finishes it.
    const a = api()
    const out = await persistAction(skipAction, a)
    expect(out).toEqual({ ok: true })
    expect(a.seen).toHaveBeenCalledWith('r3')
    // The three that DO stamp reviewed_at, and the whole point of the split:
    // a plain right-arrow must reach none of them.
    expect(a.star).not.toHaveBeenCalled()
    expect(a.reject).not.toHaveBeenCalled()
    expect(a.point).not.toHaveBeenCalled()
  })
})
