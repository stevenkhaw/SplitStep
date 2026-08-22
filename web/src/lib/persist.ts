import type { PersistableAction, QueueAction } from './queue'

/** The subset of `api` that persisting a QueueAction needs. */
export interface PersistApi {
  star: (id: string, starred: boolean) => Promise<unknown>
  reject: (id: string, rejected: boolean) => Promise<unknown>
  point: (id: string, point: boolean) => Promise<unknown>
  seen: (id: string) => Promise<unknown>
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
      case 'point':
        await api.point(action.rallyId, action.point)
        break
      case 'skip':
        // Stamps seen_at, deliberately NOT reviewed_at. The right arrow is
        // pressed on every clip just to move through the pass, so calling
        // each one *judged* would be a lie -- that reasoning (review-UX
        // 2.4) still holds exactly, and is why seen_at and reviewed_at are
        // two columns now instead of one doing both jobs. Only star, point
        // and reject stamp reviewed_at, via their own COALESCE.
        //
        // What this DOES do is finish a session: since migration 008
        // refresh_session_review_status reads seen_at, so skimming a pass
        // end to end marks it reviewed without judging anything. That is
        // deliberate -- a pass you looked all the way through is a pass you
        // finished. It is also what the queue resumes from
        // (QueueController's firstUnseen), which is the whole reason the
        // column exists: reviewed_at could never answer "did a human look
        // at this" for a rally arrowed past but never ruled on.
        await api.seen(action.rallyId)
        break
      case 'undo':
        // Undo can restore any of the three flags (or all of them back to
        // their prior values), so all three are re-synced to the server.
        // Sequential, not Promise.all: if an earlier one fails there is no
        // reason to race the rest against it, and the ordering stays
        // predictable to reason about from a server log.
        await api.star(action.rallyId, action.starred)
        await api.reject(action.rallyId, action.rejected)
        await api.point(action.rallyId, action.point)
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
