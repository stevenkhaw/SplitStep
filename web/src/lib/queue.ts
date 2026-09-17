import { UndoStack } from './undo'
import type { Rally } from './types'

/** Who won a rally's point: '' means unscored. Mirrors `Rally.winner`. */
export type Winner = '' | 'a' | 'b'

/**
 * An action that changed rally flags (star/reject/point/winner/skip) and may
 * need reverting if its POST to the server fails. Carries the pre-action flag
 * state (previousStarred/previousRejected/previousPoint/previousWinner) so
 * revert() can restore exactly this rally's flags without depending on
 * undo-stack position.
 */
export interface PersistableAction {
  kind: 'star' | 'reject' | 'skip' | 'point' | 'winner'
  rallyId: string
  starred: boolean
  rejected: boolean
  point: boolean
  winner: Winner
  previousStarred: boolean
  previousRejected: boolean
  previousPoint: boolean
  previousWinner: Winner
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
  point: boolean
  winner: Winner
}

/**
 * Discriminated on `kind`: 'star' | 'reject' | 'skip' | 'point' narrow to
 * PersistableAction (which has previousStarred/previousRejected/
 * previousPoint and can be passed to revert()); 'undo' narrows to UndoAction
 * (which cannot — passing an UndoAction to revert() is a compile error, not
 * a silent no-op/bad write, because UndoAction has no previous* fields at
 * all).
 */
export type QueueAction = PersistableAction | UndoAction

export interface QueueOptions {
  /** Keep rejected rallies in the pass so one can be brought back. Off by
   *  default: the ordinary pass is about what is left to judge. */
  includeRejected?: boolean
}

interface HistoryEntry {
  index: number
  starred: boolean
  rejected: boolean
  point: boolean
  winner: Winner
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
  #points = new Set<string>()
  // Every rally this controller was built from, winner included ('' when
  // none). A Map rather than a Set because a winner is one of three
  // values, and seeding every id (not just the scored ones) is what lets
  // liveSnapshot tell "this controller cleared it" from "never held it".
  #winners = new Map<string, Winner>()
  #history = new UndoStack<HistoryEntry>()

  constructor(rallies: Rally[], options: QueueOptions = {}) {
    this.#rallies = options.includeRejected ? [...rallies] : rallies.filter((r) => !r.rejected)
    for (const r of this.#rallies) if (r.starred) this.#starred.add(r.id)
    for (const r of this.#rallies) if (r.point) this.#points.add(r.id)
    for (const r of this.#rallies) this.#winners.set(r.id, r.winner)
    // Seeded only when they are shown: rejectedCount has always meant
    // "rejected in this pass" for the default queue (rejected rallies are
    // filtered out above, so the Set stays empty), and the finish screen's
    // tally reads from it.
    if (options.includeRejected) for (const r of this.#rallies) if (r.rejected) this.#rejected.add(r.id)

    // seen_at, not reviewed_at: reviewed_at means "a human ruled on this
    // rally" (star/point/reject) and drives session status alone; seen_at
    // means "a human has looked at this rally at all", including a plain
    // right-arrow skip that renders no verdict (see persist.ts's skip
    // case). Resuming on reviewed_at used to mean a rally that was arrowed
    // past but never judged kept reviewed_at NULL forever, so every reopen
    // landed back on it no matter how far the reviewer had actually looked.
    //
    // A shown-but-rejected rally counts as a resume target too, even once
    // seen: it was rejected on an earlier pass (necessarily already seen),
    // and H exists precisely so it can be reconsidered -- landing past the
    // end of a single-rally rejected pass would read as "nothing to show"
    // instead of putting the one rally H was pressed for in front of you.
    // In practice Session.svelte always follows this with jumpTo(current
    // rally id) on a toggle, so this only matters for a controller built
    // directly (e.g. a first load with a rejected rally already in it).
    const firstUnseen = this.#rallies.findIndex(
      (r) => r.seen_at === null || (options.includeRejected && !!r.rejected),
    )
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

  get pointCount(): number {
    return this.#points.size
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

  isPoint(rallyId: string): boolean {
    return this.#points.has(rallyId)
  }

  get currentIsStarred(): boolean {
    const r = this.current
    return r ? this.#starred.has(r.id) : false
  }

  get currentIsRejected(): boolean {
    const r = this.current
    return r ? this.#rejected.has(r.id) : false
  }

  get currentIsPoint(): boolean {
    const r = this.current
    return r ? this.#points.has(r.id) : false
  }

  winnerOf(rallyId: string): Winner {
    return this.#winners.get(rallyId) ?? ''
  }

  get currentWinner(): Winner {
    const r = this.current
    return r ? this.winnerOf(r.id) : ''
  }

  #record(): void {
    const r = this.current
    if (!r) return
    this.#history.push({
      index: this.#index,
      starred: this.#starred.has(r.id),
      rejected: this.#rejected.has(r.id),
      point: this.#points.has(r.id),
      winner: this.#winners.get(r.id) ?? '',
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
    const previousPoint = this.#points.has(r.id)
    const nowStarred = !previousStarred
    if (nowStarred) this.#starred.add(r.id)
    else this.#starred.delete(r.id)
    // Deliberately does NOT advance. Reviewing a detector that produces a
    // large share of false positives means watching a clip more than once and
    // changing your mind about it; auto-advance made both awkward. `skip()`
    // (bound to the right arrow) is the only thing that moves the cursor.
    const previousWinner = this.#winners.get(r.id) ?? ''
    return {
      kind: 'star',
      rallyId: r.id,
      starred: nowStarred,
      rejected: false,
      point: previousPoint,
      winner: previousWinner,
      previousStarred,
      previousRejected,
      previousPoint,
      previousWinner,
    }
  }

  reject(): PersistableAction | null {
    const r = this.current
    if (!r) return null
    this.#record()
    const previousStarred = this.#starred.has(r.id)
    const previousRejected = this.#rejected.has(r.id)
    const previousPoint = this.#points.has(r.id)
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
    const previousWinner = this.#winners.get(r.id) ?? ''
    return {
      kind: 'reject',
      rallyId: r.id,
      starred: this.#starred.has(r.id),
      rejected: nowRejected,
      point: this.#points.has(r.id),
      winner: previousWinner,
      previousStarred,
      previousRejected,
      previousPoint,
      previousWinner,
    }
  }

  skip(): PersistableAction | null {
    const r = this.current
    if (!r) return null
    this.#record()
    const previousStarred = this.#starred.has(r.id)
    const previousRejected = this.#rejected.has(r.id)
    const previousPoint = this.#points.has(r.id)
    this.#index += 1
    // Carries ALL THREE flags through untouched. `rejected: false` was safe only
    // while reject() advanced on its own, which made "reject then skip the
    // same rally" unreachable. Now the right arrow is the only way forward,
    // so it lands on rallies the user has just flagged -- and hard-coding
    // false here would silently undo the reject they just made.
    const previousWinner = this.#winners.get(r.id) ?? ''
    return {
      kind: 'skip',
      rallyId: r.id,
      starred: previousStarred,
      rejected: previousRejected,
      point: previousPoint,
      winner: previousWinner,
      previousStarred,
      previousRejected,
      previousPoint,
      previousWinner,
    }
  }

  point(): PersistableAction | null {
    const r = this.current
    if (!r) return null
    this.#record()
    const previousPoint = this.#points.has(r.id)
    const previousWinner = this.#winners.get(r.id) ?? ''
    const nowPoint = !previousPoint
    if (nowPoint) this.#points.add(r.id)
    else {
      this.#points.delete(r.id)
      // A winner on a non-point is a contradiction; the server's set_point
      // clears it too, so the two stay in step without a second round trip.
      this.#winners.set(r.id, '')
    }
    // Deliberately does NOT advance, and deliberately does not touch
    // starred/rejected: "was a point played out" is orthogonal to "is this a
    // highlight" and to "is this a rally at all".
    return {
      kind: 'point',
      rallyId: r.id,
      starred: this.#starred.has(r.id),
      rejected: this.#rejected.has(r.id),
      point: nowPoint,
      winner: nowPoint ? previousWinner : '',
      previousStarred: this.#starred.has(r.id),
      previousRejected: this.#rejected.has(r.id),
      previousPoint,
      previousWinner,
    }
  }

  // Who won the current point. Also marks it a point: pressing A/B is one
  // judgement ("a point, and she took it"), and asking for P first would
  // be two keys for it. Replaces a previous winner in place, which is how a
  // wrong answer gets corrected on the way back through the pass. Does not
  // advance, like every other verdict.
  winner(p: 'a' | 'b'): PersistableAction | null {
    const r = this.current
    if (!r) return null
    this.#record()
    const previousPoint = this.#points.has(r.id)
    const previousWinner = this.#winners.get(r.id) ?? ''
    this.#points.add(r.id)
    this.#winners.set(r.id, p)
    return {
      kind: 'winner',
      rallyId: r.id,
      starred: this.#starred.has(r.id),
      rejected: this.#rejected.has(r.id),
      point: true,
      winner: p,
      previousStarred: this.#starred.has(r.id),
      previousRejected: this.#rejected.has(r.id),
      previousPoint,
      previousWinner,
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
    if (entry.point) this.#points.add(r.id)
    else this.#points.delete(r.id)
    this.#winners.set(r.id, entry.winner)
    return {
      kind: 'undo',
      rallyId: r.id,
      starred: entry.starred,
      rejected: entry.rejected,
      point: entry.point,
      winner: entry.winner,
    }
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
    if (action.previousPoint) this.#points.add(action.rallyId)
    else this.#points.delete(action.rallyId)
    this.#winners.set(action.rallyId, action.previousWinner)
  }

  // `rallies`, with `starred`/`rejected`/`point` overwritten from this
  // session's live Sets rather than whatever server-snapshot values were
  // baked into the Rally objects at construction. For a consumer that needs
  // this session's current flags on rallies this controller does not
  // otherwise expose a per-rally accessor for -- namely TimelineMode's
  // OverviewBand, handed a copy via QueueMode's onopen_timeline, so a rally
  // starred (or rejected) earlier in this queue session renders correctly
  // there even though `detail.rallies` itself is never refetched just from a
  // star/reject/point/skip action (see Session.svelte's comment on
  // QueueMode never writing back into `detail`).
  //
  // Unlike `current`/`isStarred`/`isRejected` (which only ever reason about
  // rallies still in `#rallies`, i.e. not rejected), this takes the caller's
  // own `rallies` array and covers all of it, including ones this
  // controller has itself rejected out of the active queue -- OverviewBand
  // still needs to place and color those.
  //
  // Returns fresh objects; per the class-level invariant, the Rally objects
  // this controller was constructed with are never mutated. Rallies the
  // controller was not built from pass through unchanged -- see the inline
  // comment.
  liveSnapshot(rallies: Rally[]): Rally[] {
    return rallies.map((r) => {
      // Not this controller's rally (another source tab's, when the caller
      // hands in the whole session for a score replay): its server flags
      // are the freshest anyone has, so pass it through untouched rather
      // than zeroing flags this controller never held.
      if (!this.#winners.has(r.id)) return { ...r }
      return {
        ...r,
        starred: this.#starred.has(r.id) ? 1 : 0,
        rejected: this.#rejected.has(r.id) ? 1 : 0,
        point: this.#points.has(r.id) ? 1 : 0,
        winner: this.#winners.get(r.id) ?? '',
      }
    })
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
