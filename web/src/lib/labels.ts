import { UndoStack } from './undo'
import type { LabelRecord, Rally } from './types'

export type Verdict = 'clean' | 'not_play' | 'partly' | 'unsure'
export type BoundaryFlag = 'start_early' | 'start_late' | 'end_early' | 'end_late'

// Mirrors FLAG_ORDER in bootleg/db/labels.py. The server re-orders on write
// anyway, but sending and rendering the same order keeps the UI's flag row
// from reshuffling as you toggle.
export const FLAG_ORDER: readonly BoundaryFlag[] = [
  'start_early',
  'start_late',
  'end_early',
  'end_late',
]

// Verdicts that admit a boundary error. A span holding no rally has no
// boundary to be wrong about, and 'unsure' means the clip could not be
// judged at all.
const FLAGGABLE: readonly Verdict[] = ['clean', 'partly']

export interface LabelAction {
  rallyId: string
  verdict: Verdict
  flags: BoundaryFlag[]
  previousVerdict: Verdict | null
  previousFlags: BoundaryFlag[]
}

interface HistoryEntry {
  index: number
  verdict: Verdict | null
  flags: BoundaryFlag[]
}

// Keyed on (source_id, span), not span alone. Sources are independent clips
// whose timelines each start at 0, and segment() places every edge on a
// fixed sample grid, so two sources can produce identical
// (det_start_ms, det_end_ms) pairs -- guaranteed at the start edge for any
// rally within the start pad, since the clamp puts both at 0. LabelMode
// fetches labels per source and flattens them into one list before handing
// it to this controller, so a span-only key let source A's verdict render
// on source B's rally of the same span (M2) -- precisely the collision
// exact-span matching exists to prevent, reintroduced through a different
// door.
function spanKey(sourceId: string, startMs: number, endMs: number): string {
  return `${sourceId}:${startMs}:${endMs}`
}

/**
 * The label-mode state machine.
 *
 * Deliberately pure, for the same reason QueueController is: no DOM, no
 * fetch, no timers. jsdom has no <video>, so anything that lived in the
 * component would be untestable.
 */
export class LabelController {
  #rallies: Rally[]
  #verdicts = new Map<string, Verdict>()
  #flags = new Map<string, BoundaryFlag[]>()
  #index = 0
  #history = new UndoStack<HistoryEntry>()

  /**
   * `rallies` is NOT filtered by `rejected`, unlike QueueController. A
   * rejected rally is precisely the `not_play` the corpus is short of;
   * hiding it here would bias the set toward what the detector already gets
   * right.
   *
   * `existing` seeds from labels already stored. Matching is on the exact
   * detector span: a re-segment that moved an edge produced genuinely
   * different detector output, so the old judgement is not a judgement of
   * this span, and matching by overlap would attribute a verdict to a clip
   * nobody watched.
   */
  constructor(rallies: Rally[], existing: LabelRecord[]) {
    this.#rallies = rallies
    const bySpan = new Map<string, LabelRecord>()
    for (const rec of existing) {
      bySpan.set(spanKey(rec.source_id, rec.span_start_ms, rec.span_end_ms), rec)
    }

    for (const r of rallies) {
      const rec = bySpan.get(spanKey(r.source_id, r.det_start_ms, r.det_end_ms))
      // A verdict-less row is a boundary correction from a drag, not a
      // judgement -- rendering it as one would invent a verdict the reviewer
      // never gave.
      if (!rec || rec.verdict === null) continue
      this.#verdicts.set(r.id, rec.verdict)
      this.#flags.set(r.id, [...rec.boundary_flags])
    }
  }

  get current(): Rally | undefined {
    return this.#rallies[this.#index]
  }

  get index(): number {
    return this.#index
  }

  get total(): number {
    return this.#rallies.length
  }

  get labelledCount(): number {
    return this.#verdicts.size
  }

  get currentVerdict(): Verdict | null {
    const r = this.current
    return r ? (this.#verdicts.get(r.id) ?? null) : null
  }

  get currentFlags(): BoundaryFlag[] {
    const r = this.current
    // Copied, not the live array: a caller holding this reference must not
    // be able to reach into controller state (e.g. `currentFlags.push(...)`)
    // without going through toggleFlag.
    return r ? [...(this.#flags.get(r.id) ?? [])] : []
  }

  get flagsEnabled(): boolean {
    const v = this.currentVerdict
    return v !== null && FLAGGABLE.includes(v)
  }

  #record(): void {
    const r = this.current
    if (!r) return
    this.#history.push({
      index: this.#index,
      verdict: this.#verdicts.get(r.id) ?? null,
      flags: [...(this.#flags.get(r.id) ?? [])],
    })
  }

  setVerdict(verdict: Verdict): LabelAction | null {
    const r = this.current
    if (!r) return null
    this.#record()
    const previousVerdict = this.#verdicts.get(r.id) ?? null
    const previousFlags = [...(this.#flags.get(r.id) ?? [])]

    this.#verdicts.set(r.id, verdict)
    // Dropping to a verdict that admits no boundary error clears whatever was
    // already flagged, so a not_play row can never carry an end_late that
    // contradicts it.
    // Copied rather than reused: `flags` becomes the array stored in
    // `#flags`, and `previousFlags` is handed back to the caller as the
    // action's pre-action snapshot. Aliasing the two would let a caller
    // mutating action.previousFlags corrupt controller state, and revert()
    // depends on previousFlags staying an immutable snapshot of what
    // preceded this action.
    const flags = FLAGGABLE.includes(verdict) ? [...previousFlags] : []
    this.#flags.set(r.id, flags)

    // Deliberately does not advance -- boundary flags are added to this same
    // span next, and `→` is the only thing that moves the cursor.
    return {
      rallyId: r.id,
      verdict,
      flags: [...flags],
      previousVerdict,
      previousFlags: [...previousFlags],
    }
  }

  toggleFlag(flag: BoundaryFlag): LabelAction | null {
    const r = this.current
    const verdict = this.currentVerdict
    if (!r || verdict === null || !FLAGGABLE.includes(verdict)) return null
    this.#record()
    const previousFlags = [...(this.#flags.get(r.id) ?? [])]
    const next = previousFlags.includes(flag)
      ? previousFlags.filter((f) => f !== flag)
      : [...previousFlags, flag]
    // Canonical order, so the rendered row does not reshuffle as you toggle.
    const ordered = FLAG_ORDER.filter((f) => next.includes(f))
    this.#flags.set(r.id, ordered)

    return {
      rallyId: r.id,
      verdict,
      flags: [...ordered],
      previousVerdict: verdict,
      previousFlags,
    }
  }

  next(): void {
    if (this.#index < this.#rallies.length - 1) this.#index += 1
  }

  back(): void {
    if (this.#index > 0) this.#index -= 1
  }

  undo(): LabelAction | null {
    const entry = this.#history.pop()
    if (!entry) return null
    this.#index = entry.index
    const r = this.#rallies[entry.index]
    if (!r) return null

    const previousVerdict = this.#verdicts.get(r.id) ?? null
    const previousFlags = [...(this.#flags.get(r.id) ?? [])]

    if (entry.verdict === null) this.#verdicts.delete(r.id)
    else this.#verdicts.set(r.id, entry.verdict)
    this.#flags.set(r.id, [...entry.flags])

    // Returns null when the restored state has no verdict: there is nothing
    // to POST, since the API has no "unlabel" and the corpus is append-only.
    // The caller simply has nothing to persist.
    if (entry.verdict === null) return null
    return {
      rallyId: r.id,
      verdict: entry.verdict,
      flags: [...entry.flags],
      previousVerdict,
      previousFlags,
    }
  }

  /**
   * Restores one rally's state after its POST failed.
   *
   * Action-correlated rather than position-correlated, exactly like
   * QueueController.revert: it targets `action.rallyId` wherever that sits,
   * never touches `#index`, and never pops `#history`. A failed network call
   * must not move the reviewer's position or consume their undo.
   */
  revert(action: LabelAction): void {
    if (action.previousVerdict === null) this.#verdicts.delete(action.rallyId)
    else this.#verdicts.set(action.rallyId, action.previousVerdict)
    this.#flags.set(action.rallyId, [...action.previousFlags])
  }
}

/** The subset of `api` that persisting a LabelAction needs. */
export interface LabelApi {
  label: (id: string, verdict: string, boundaryFlags: string[]) => Promise<unknown>
}

export type LabelOutcome = { ok: true } | { ok: false }

/**
 * Sends one LabelAction to the server.
 *
 * Every call appends a row -- the corpus is append-only by design, so
 * toggling a flag three times leaves three rows and `latest_labels` resolves
 * which is current. That is deliberate: a corrected judgement must never
 * erase the one it corrected.
 */
export async function persistLabel(
  action: LabelAction,
  api: LabelApi,
): Promise<LabelOutcome> {
  try {
    await api.label(action.rallyId, action.verdict, action.flags)
    return { ok: true }
  } catch {
    return { ok: false }
  }
}
