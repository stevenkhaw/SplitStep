import { describe, expect, it, vi } from 'vitest'
import { describePersistFailure, persistAction } from '../src/lib/persist'
import type { PersistApi } from '../src/lib/persist'
import type { PersistableAction, UndoAction } from '../src/lib/queue'

function api(overrides: Partial<PersistApi> = {}): PersistApi {
  return {
    star: vi.fn().mockResolvedValue(undefined),
    reject: vi.fn().mockResolvedValue(undefined),
    reviewed: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  }
}

const starAction: PersistableAction = {
  kind: 'star',
  rallyId: 'r1',
  starred: true,
  rejected: false,
  previousStarred: false,
  previousRejected: false,
}

const rejectAction: PersistableAction = {
  kind: 'reject',
  rallyId: 'r2',
  starred: false,
  rejected: true,
  previousStarred: false,
  previousRejected: false,
}

const skipAction: PersistableAction = {
  kind: 'skip',
  rallyId: 'r3',
  starred: false,
  rejected: false,
  previousStarred: false,
  previousRejected: false,
}

const undoAction: UndoAction = { kind: 'undo', rallyId: 'r4', starred: true, rejected: false }

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

  it('calls api.reviewed for a skip action', async () => {
    const a = api()
    await persistAction(skipAction, a)
    expect(a.reviewed).toHaveBeenCalledWith('r3')
  })

  it('calls both api.star and api.reject, in order, for an undo action', async () => {
    const calls: string[] = []
    const a = api({
      star: vi.fn().mockImplementation(async () => {
        calls.push('star')
      }),
      reject: vi.fn().mockImplementation(async () => {
        calls.push('reject')
      }),
    })
    await persistAction(undoAction, a)
    expect(a.star).toHaveBeenCalledWith('r4', true)
    expect(a.reject).toHaveBeenCalledWith('r4', false)
    expect(calls).toEqual(['star', 'reject'])
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

  it('reports the failed action as the thing to revert when a skip persist fails', async () => {
    const a = api({ reviewed: vi.fn().mockRejectedValue(new Error('network down')) })
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
