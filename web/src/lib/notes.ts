import type { Rally } from './types'

/**
 * The longest note that still renders as two lines inside the caption pill at
 * 4K without shrinking the type. Mirrors NOTE_MAX_CHARS in
 * splitstep/db/rallies.py -- the server enforces it too, because the UI is not
 * the only writer a library ever has.
 */
export const NOTE_MAX_CHARS = 120

/**
 * What actually gets sent and stored: trimmed, then capped.
 *
 * Trim happens BEFORE the cap so 120 characters of text followed by spaces is
 * not treated as over the limit -- the same order the server's validator uses,
 * so the two can never disagree about whether a note fits.
 */
export function normalizeNote(raw: string): string {
  return raw.trim().slice(0, NOTE_MAX_CHARS)
}

/**
 * Whether committing `buffer` would change what the server holds.
 *
 * Compares normalized forms, so opening the field and closing it -- or adding
 * and removing a trailing space -- costs no POST at all.
 */
export function isDirty(buffer: string, saved: string): boolean {
  return normalizeNote(buffer) !== normalizeNote(saved)
}

/**
 * Rally id -> note, for the notes a session actually has.
 *
 * Empty notes are omitted rather than stored as '', so `has(id)` is the whole
 * question the ✎ indicator asks and no caller has to distinguish "absent" from
 * "present but empty".
 */
export function seedNotes(rallies: Rally[]): Map<string, string> {
  const m = new Map<string, string>()
  for (const r of rallies) {
    if (r.note) m.set(r.id, r.note)
  }
  return m
}

/** The subset of `api` that a NoteWriter needs. */
export interface NoteApi {
  setNote: (id: string, note: string) => Promise<unknown>
}

export type NoteCommitOutcome =
  | { status: 'ok' }
  | { status: 'unchanged' }
  | { status: 'superseded' }
  | { status: 'failed'; error: unknown }

/**
 * Owns the id -> note map for a queue session, and serialises writes to it
 * per rally.
 *
 * Unlike LabelWriter (`labels.ts`), which hands a WriteOutcome back to
 * LabelController and lets that controller decide what to do, NoteWriter
 * owns the map itself. There is no NoteController: notes are deliberately
 * kept out of QueueController and out of persist.ts (they model three
 * booleans with an undo stack; a note is free text with no toggle semantics,
 * and threading it through QueueAction would put text into an undo history
 * that exists to walk back verdicts). With no other object to hold the map
 * or apply a restore, NoteWriter has to do both itself -- QueueMode only
 * reads has()/get() and calls commit().
 *
 * The serialisation is the same problem LabelWriter solves and for the same
 * reason: a fast reviewer can commit a second note before the first POST
 * returns (edit, Enter, reopen, edit again), and the request that lands last
 * must be the one the reviewer made last, not whichever happened to win the
 * race.
 */
export class NoteWriter {
  #api: NoteApi
  #notes: Map<string, string>
  // Per rally: the tail of its write chain, how many of its writes are
  // queued-but-unsettled, and the last state the server is known to hold.
  #tail = new Map<string, Promise<unknown>>()
  #queued = new Map<string, number>()
  #confirmed = new Map<string, string>()

  constructor(api: NoteApi, notes: Map<string, string> = new Map()) {
    this.#api = api
    // Copied so a caller's seedNotes() map is not aliased into controller
    // state -- mirrors QueueController/LabelController's own copy-on-input
    // discipline elsewhere in this codebase.
    this.#notes = new Map(notes)
  }

  has(rallyId: string): boolean {
    return this.#notes.has(rallyId)
  }

  get(rallyId: string): string {
    return this.#notes.get(rallyId) ?? ''
  }

  #apply(rallyId: string, note: string): void {
    // Empty notes are omitted, not stored as '', matching seedNotes -- so
    // has() stays the indicator's single source of truth instead of every
    // caller having to treat '' and "absent" as the same case twice.
    if (note) this.#notes.set(rallyId, note)
    else this.#notes.delete(rallyId)
  }

  /**
   * Normalizes `buffer` and, if it differs from the rally's current note,
   * writes it: the map is updated immediately (this runs on a LAN box, and
   * making the reviewer wait on a round trip per note would break the
   * rhythm the whole queue exists to protect), then the POST fires, with the
   * pre-write value restored if it fails and nothing newer is queued behind
   * it.
   *
   * Not declared `async`: the isDirty check and the optimistic #apply both
   * need to happen synchronously, before this returns, so a caller that
   * reads the map right after calling commit() (without awaiting) already
   * sees the new value -- that immediacy is the whole point of "optimistic".
   *
   * A no-op buffer (open the field, close it unchanged) returns 'unchanged'
   * without writing to the map or touching the network at all.
   */
  commit(rallyId: string, buffer: string): Promise<NoteCommitOutcome> {
    const next = normalizeNote(buffer)
    const saved = this.get(rallyId)
    if (!isDirty(next, saved)) return Promise.resolve({ status: 'unchanged' })

    this.#apply(rallyId, next)

    const depth = (this.#queued.get(rallyId) ?? 0) + 1
    this.#queued.set(rallyId, depth)
    // Nothing was in flight for this rally, so what the server holds right
    // now is exactly `saved`. Captured here rather than read off `saved`
    // later, because a whole burst can fail together and this write's own
    // predecessor may never have reached the server either -- see
    // LabelWriter.submit for the same reasoning.
    if (depth === 1) this.#confirmed.set(rallyId, saved)

    const run = async (): Promise<NoteCommitOutcome> => {
      try {
        await this.#api.setNote(rallyId, next)
        const remaining = (this.#queued.get(rallyId) ?? 1) - 1
        this.#queued.set(rallyId, remaining)
        this.#confirmed.set(rallyId, next)
        return { status: 'ok' }
      } catch (error) {
        const remaining = (this.#queued.get(rallyId) ?? 1) - 1
        this.#queued.set(rallyId, remaining)
        // A newer commit for this rally is already queued behind this one.
        // It carries the whole state and will decide what's true, so
        // restoring here would drag the field back to a value the reviewer
        // has already typed over.
        if (remaining > 0) return { status: 'superseded' }
        const restore = this.#confirmed.get(rallyId) ?? saved
        this.#apply(rallyId, restore)
        return { status: 'failed', error }
      }
    }

    // `then(run, run)` rather than `then(run)`: nothing upstream of `run`
    // can reject (the try/catch inside it handles the only promise that
    // can), but chaining it this way mirrors LabelWriter's tail and keeps a
    // future change to that shape from silently stranding a rally's queue.
    const chained = (this.#tail.get(rallyId) ?? Promise.resolve()).then(run, run)
    this.#tail.set(rallyId, chained)
    return chained
  }
}
