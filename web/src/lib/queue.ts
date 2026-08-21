import { UndoStack } from './undo'
import type { Rally } from './types'

/**
 * An action that changed rally flags (star/reject/skip) and may need
 * reverting if its POST to the server fails. Carries the pre-action flag
 * state (previousStarred/previousRejected) so revert() can restore exactly
 * this rally's flags without depending on undo-stack position.
 */
export interface PersistableAction {
  kind: 'star' | 'reject' | 'skip'
  rallyId: string
  starred: boolean
  rejected: boolean
  previousStarred: boolean
  previousRejected: boolean
}

/**
 * The result of a user-invoked undo(). There is nothing to persist and
 * nothing to revert for it, so it deliberately carries no previous* state
 * and cannot be passed to revert() — see the QueueAction union below.
 */
export interface UndoAction {
  kind: 'undo'
  rallyId: string
  starred: boolean
  rejected: boolean
}

/**
 * Discriminated on `kind`: 'star' | 'reject' | 'skip' narrow to
 * PersistableAction (which has previousStarred/previousRejected and can be
 * passed to revert()); 'undo' narrows to UndoAction (which cannot — passing
 * an UndoAction to revert() is a compile error, not a silent no-op/bad
 * write, because UndoAction has no previous* fields at all).
 */
export type QueueAction = PersistableAction | UndoAction

interface HistoryEntry {
  index: number
  starred: boolean
  rejected: boolean
}

/**
 * The queue-mode state machine.
 *
 * Deliberately pure: no DOM, no fetch, no timers. The component calls into
 * this and pushes the returned QueueAction to the server. Keeping it free of
 * media-element concerns is what makes the whole review flow testable, since
 * jsdom has no <video> implementation.
 */
export class QueueController {
  #rallies: Rally[]
  #index = 0
  #starred = new Set<string>()
  #rejected = new Set<string>()
  #history = new UndoStack<HistoryEntry>()

  constructor(rallies: Rally[]) {
    this.#rallies = rallies.filter((r) => !r.rejected)
    for (const r of this.#rallies) if (r.starred) this.#starred.add(r.id)

    const firstUnseen = this.#rallies.findIndex((r) => r.reviewed_at === null)
    this.#index = firstUnseen === -1 ? this.#rallies.length : firstUnseen
  }

  get current(): Rally | undefined {
    return this.#rallies[this.#index]
  }

  get nextRally(): Rally | undefined {
    return this.#rallies[this.#index + 1]
  }

  get index(): number {
    return this.#index
  }

  get total(): number {
    return this.#rallies.length
  }

  get starredCount(): number {
    return this.#starred.size
  }

  get rejectedCount(): number {
    return this.#rejected.size
  }

  get finished(): boolean {
    return this.#index >= this.#rallies.length
  }

  // Live star/reject state, read from the #starred/#rejected Sets — the
  // source of truth for review flags during a session. The Rally objects
  // handed in at construction are server snapshots and are deliberately
  // never mutated; keeping the Sets authoritative (instead of writing back
  // into `.starred`/`.rejected` on the Rally) is what lets `current` still
  // return the original object while these stay accurate after star/back.
  // Do not "fix" this by mutating the Rally objects to keep them in sync.
  isStarred(rallyId: string): boolean {
    return this.#starred.has(rallyId)
  }

  isRejected(rallyId: string): boolean {
    return this.#rejected.has(rallyId)
  }

  get currentIsStarred(): boolean {
    const r = this.current
    return r ? this.#starred.has(r.id) : false
  }

  get currentIsRejected(): boolean {
    const r = this.current
    return r ? this.#rejected.has(r.id) : false
  }

  #record(): void {
    const r = this.current
    if (!r) return
    this.#history.push({
      index: this.#index,
      starred: this.#starred.has(r.id),
      rejected: this.#rejected.has(r.id),
    })
    // UndoStack bounds its own depth, so a 300-rally session cannot grow it
    // without limit.
  }

  star(): PersistableAction | null {
    const r = this.current
    if (!r) return null
    this.#record()
    const previousStarred = this.#starred.has(r.id)
    const previousRejected = this.#rejected.has(r.id)
    const nowStarred = !previousStarred
    if (nowStarred) this.#starred.add(r.id)
    else this.#starred.delete(r.id)
    // Deliberately does NOT advance. Reviewing a detector that produces a
    // large share of false positives means watching a clip more than once and
    // changing your mind about it; auto-advance made both awkward. `skip()`
    // (bound to the right arrow) is the only thing that moves the cursor.
    return {
      kind: 'star',
      rallyId: r.id,
      starred: nowStarred,
      rejected: false,
      previousStarred,
      previousRejected,
    }
  }

  reject(): PersistableAction | null {
    const r = this.current
    if (!r) return null
    this.#record()
    const previousStarred = this.#starred.has(r.id)
    const previousRejected = this.#rejected.has(r.id)
    // Toggles, mirroring star(). Rejected rallies are filtered out of the
    // queue when it is constructed, so before this a mis-press could only be
    // taken back via undo -- and not at all once the page reloaded. Toggling
    // makes a second press the obvious remedy for the rest of the pass.
    const nowRejected = !previousRejected
    if (nowRejected) {
      this.#rejected.add(r.id)
      this.#starred.delete(r.id)
    } else {
      this.#rejected.delete(r.id)
    }
    // Does not advance, for the same reason star() does not.
    return {
      kind: 'reject',
      rallyId: r.id,
      starred: this.#starred.has(r.id),
      rejected: nowRejected,
      previousStarred,
      previousRejected,
    }
  }

  skip(): PersistableAction | null {
    const r = this.current
    if (!r) return null
    this.#record()
    const previousStarred = this.#starred.has(r.id)
    const previousRejected = this.#rejected.has(r.id)
    this.#index += 1
    // Carries BOTH flags through untouched. `rejected: false` was safe only
    // while reject() advanced on its own, which made "reject then skip the
    // same rally" unreachable. Now the right arrow is the only way forward,
    // so it lands on rallies the user has just flagged -- and hard-coding
    // false here would silently undo the reject they just made.
    return {
      kind: 'skip',
      rallyId: r.id,
      starred: previousStarred,
      rejected: previousRejected,
      previousStarred,
      previousRejected,
    }
  }

  back(): void {
    if (this.#index > 0) this.#index -= 1
  }

  undo(): UndoAction | null {
    const entry = this.#history.pop()
    if (!entry) return null
    this.#index = entry.index
    const r = this.#rallies[entry.index]
    if (!r) return null
    if (entry.starred) this.#starred.add(r.id)
    else this.#starred.delete(r.id)
    if (entry.rejected) this.#rejected.add(r.id)
    else this.#rejected.delete(r.id)
    return { kind: 'undo', rallyId: r.id, starred: entry.starred, rejected: entry.rejected }
  }

  // Reverts a single previously-issued PersistableAction (e.g. because its
  // persist to the server failed) by restoring that rally's flags from the
  // action's previous* state. Unlike undo(), this is action-correlated
  // rather than position-correlated: it targets action.rallyId specifically,
  // wherever (if anywhere) it sits in the undo history, so acting on a
  // stale/failed action never reverts a different, more recent action
  // instead. It never touches #index and never pops #history — a failed
  // network call must not move the user's position or consume their undo.
  //
  // Takes PersistableAction, not the full QueueAction union: an UndoAction
  // has no previous* state to revert to, so passing one is a compile error
  // rather than a silent no-op or an accidental unstar from reading
  // `undefined` as falsy.
  revert(action: PersistableAction): void {
    if (action.previousStarred) this.#starred.add(action.rallyId)
    else this.#starred.delete(action.rallyId)
    if (action.previousRejected) this.#rejected.add(action.rallyId)
    else this.#rejected.delete(action.rallyId)
  }

  // `rallies`, with `starred`/`rejected` overwritten from this session's
  // live Sets rather than whatever server-snapshot values were baked into
  // the Rally objects at construction. For a consumer that needs this
  // session's current flags on rallies this controller does not otherwise
  // expose a per-rally accessor for -- namely TimelineMode's OverviewBand,
  // handed a copy via QueueMode's onopen_timeline, so a rally starred (or
  // rejected) earlier in this queue session renders correctly there even
  // though `detail.rallies` itself is never refetched just from a star/
  // reject/skip action (see Session.svelte's comment on QueueMode never
  // writing back into `detail`).
  //
  // Unlike `current`/`isStarred`/`isRejected` (which only ever reason about
  // rallies still in `#rallies`, i.e. not rejected), this takes the caller's
  // own `rallies` array and covers all of it, including ones this
  // controller has itself rejected out of the active queue -- OverviewBand
  // still needs to place and color those.
  //
  // Returns fresh objects; per the class-level invariant, the Rally objects
  // this controller was constructed with are never mutated.
  liveSnapshot(rallies: Rally[]): Rally[] {
    return rallies.map((r) => ({
      ...r,
      starred: this.#starred.has(r.id) ? 1 : 0,
      rejected: this.#rejected.has(r.id) ? 1 : 0,
    }))
  }

  jumpTo(rallyId: string): void {
    const i = this.#rallies.findIndex((r) => r.id === rallyId)
    if (i !== -1) this.#index = i
  }

  remainingMs(speed: number): number {
    const total = this.#rallies
      .slice(this.#index)
      .filter((r) => !this.#rejected.has(r.id))
      .reduce((acc, r) => acc + (r.end_ms - r.start_ms), 0)
    return Math.round(total / Math.max(speed, 0.1))
  }
}
