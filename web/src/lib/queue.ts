import { UndoStack } from './undo'
import type { Rally } from './types'

export interface QueueAction {
  kind: 'star' | 'reject' | 'skip' | 'undo'
  rallyId: string
  starred: boolean
  rejected: boolean
  // Pre-action flag state for this rally, captured by star()/reject()/skip()
  // so a failed persist can be correlated back to exactly the rally it
  // touched (see revert()) instead of relying on undo-stack position.
  previousStarred?: boolean
  previousRejected?: boolean
}

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

  star(): QueueAction | null {
    const r = this.current
    if (!r) return null
    this.#record()
    const previousStarred = this.#starred.has(r.id)
    const previousRejected = this.#rejected.has(r.id)
    const nowStarred = !previousStarred
    if (nowStarred) this.#starred.add(r.id)
    else this.#starred.delete(r.id)
    this.#index += 1
    return {
      kind: 'star',
      rallyId: r.id,
      starred: nowStarred,
      rejected: false,
      previousStarred,
      previousRejected,
    }
  }

  reject(): QueueAction | null {
    const r = this.current
    if (!r) return null
    this.#record()
    const previousStarred = this.#starred.has(r.id)
    const previousRejected = this.#rejected.has(r.id)
    this.#rejected.add(r.id)
    this.#starred.delete(r.id)
    this.#index += 1
    return {
      kind: 'reject',
      rallyId: r.id,
      starred: false,
      rejected: true,
      previousStarred,
      previousRejected,
    }
  }

  skip(): QueueAction | null {
    const r = this.current
    if (!r) return null
    this.#record()
    const previousStarred = this.#starred.has(r.id)
    const previousRejected = this.#rejected.has(r.id)
    this.#index += 1
    return {
      kind: 'skip',
      rallyId: r.id,
      starred: previousStarred,
      rejected: false,
      previousStarred,
      previousRejected,
    }
  }

  back(): void {
    if (this.#index > 0) this.#index -= 1
  }

  undo(): QueueAction | null {
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

  // Reverts a single previously-issued action (e.g. because its persist to
  // the server failed) by restoring that rally's flags from the action's
  // previous* state. Unlike undo(), this is action-correlated rather than
  // position-correlated: it targets action.rallyId specifically, wherever
  // (if anywhere) it sits in the undo history, so acting on a stale/failed
  // action never reverts a different, more recent action instead. It never
  // touches #index and never pops #history — a failed network call must not
  // move the user's position or consume their undo.
  revert(action: QueueAction): void {
    if (action.previousStarred) this.#starred.add(action.rallyId)
    else this.#starred.delete(action.rallyId)
    if (action.previousRejected) this.#rejected.add(action.rallyId)
    else this.#rejected.delete(action.rallyId)
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
