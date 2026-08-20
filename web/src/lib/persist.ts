import type { PersistableAction, QueueAction } from './queue'

/** The subset of `api` that persisting a QueueAction needs. */
export interface PersistApi {
  star: (id: string, starred: boolean) => Promise<unknown>
  reject: (id: string, rejected: boolean) => Promise<unknown>
  reviewed: (id: string) => Promise<unknown>
}

export type PersistOutcome = { ok: true } | { ok: false; revert: PersistableAction | null }

/**
 * Sends a single QueueAction to the server.
 *
 * On failure, tells the caller what (if anything) to hand to
 * `QueueController.revert()`: a PersistableAction (star/reject/skip) reverts
 * cleanly to its own pre-action flags, so `revert` names it directly. An
 * UndoAction carries no previous* state -- undoing IS the act of restoring a
 * prior state, and undo() is user-facing and pops the history stack LIFO, so
 * silently "reverting the revert" here would walk back whatever the user's
 * *next* undo happens to land on rather than the action that actually failed
 * to save. `revert: null` tells the caller to surface the failure instead of
 * mutating QueueController state -- see QueueController.revert()'s own
 * comment for the other half of this reasoning.
 */
export async function persistAction(action: QueueAction, api: PersistApi): Promise<PersistOutcome> {
  try {
    switch (action.kind) {
      case 'star':
        await api.star(action.rallyId, action.starred)
        break
      case 'reject':
        await api.reject(action.rallyId, action.rejected)
        break
      case 'skip':
        await api.reviewed(action.rallyId)
        break
      case 'undo':
        // Undo can restore either flag (or both back to their prior
        // values), so both are re-synced to the server. Sequential, not
        // Promise.all: if the first fails there is no reason to race the
        // second against it, and the ordering stays predictable to reason
        // about from a server log.
        await api.star(action.rallyId, action.starred)
        await api.reject(action.rallyId, action.rejected)
        break
    }
    return { ok: true }
  } catch {
    return { ok: false, revert: action.kind === 'undo' ? null : action }
  }
}

/**
 * A short, human-readable notice for a failed persist. `rallyIdx` is the
 * rally's 1-based, session-wide position (Rally.idx from the server) --
 * stable regardless of where the queue's cursor has moved on to since.
 */
export function describePersistFailure(action: QueueAction, rallyIdx?: number): string {
  const label = rallyIdx !== undefined ? `rally ${rallyIdx}` : 'a rally'
  if (action.kind === 'undo') return `Couldn't save undo on ${label} -- please retry`
  return `Couldn't save ${action.kind} on ${label} -- reverted`
}
