import { UndoStack } from './undo'
import type { LabelRecord, Rally } from './types'

export type Verdict = 'clean' | 'not_play' | 'partly' | 'unsure'
export type BoundaryFlag = 'start_early' | 'start_late' | 'end_early' | 'end_late'

// Mirrors FLAG_ORDER in splitstep/db/labels.py. The server re-orders on write
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

/**
 * One write to make: the state `rallyId`'s span should be left in, plus the
 * state it had before, so a failed write can be undone locally.
 *
 * `verdict: null` is a retraction -- the reviewer took a judgement back (`U`)
 * and the span should read as unlabelled everywhere, not just on their
 * screen. It is a distinct route server-side, not a label with a missing
 * field; see persistLabel.
 */
export interface LabelAction {
  rallyId: string
  verdict: Verdict | null
  flags: BoundaryFlag[]
  previousVerdict: Verdict | null
  previousFlags: BoundaryFlag[]
}

/** A rally's label state, as the controller holds it and the server stores it. */
export interface LabelState {
  verdict: Verdict | null
  flags: BoundaryFlag[]
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
 * A rally the detector proposed. `det_start_ms`/`det_end_ms` are non-null --
 * unlike a hand-made rally (see `web/src/lib/split.ts`), which has nothing
 * for `rally_labels` to anchor to, since that table keys on the detector's
 * own span.
 */
export type DetectedRally = Rally & { det_start_ms: number; det_end_ms: number }

/**
 * Narrows a `Rally` to a `DetectedRally`. The single source of truth for
 * "this rally has a detector span" -- `LabelController` uses it to filter at
 * construction, and the label-mode fake-server test harness uses the same
 * guard rather than re-deriving the check, so the two cannot drift.
 *
 * Tests only `det_start_ms`, but the narrowed type asserts both
 * `det_start_ms` and `det_end_ms` are `number`. That is sound, not an
 * oversight: `splitstep/db/migrations/009_rally_split.sql` carries
 * `CHECK ((det_start_ms IS NULL) = (det_end_ms IS NULL))`, so the database
 * itself guarantees the pair is always both-null or both-set, and one field
 * proving non-null licenses the other.
 */
export function isDetected(r: Rally): r is DetectedRally {
  return r.det_start_ms !== null
}

/**
 * The label-mode state machine.
 *
 * Deliberately pure, for the same reason QueueController is: no DOM, no
 * fetch, no timers. jsdom has no <video>, so anything that lived in the
 * component would be untestable.
 */
export class LabelController {
  #rallies: DetectedRally[]
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
    // Hand-made rallies (det_start_ms null -- a half someone split off, see
    // web/src/lib/split.ts) are dropped here, not skipped during
    // next()/back(). rally_labels anchors on the detector's own span, so
    // there is nothing for a judgement on one of these to attach to.
    // Filtering at construction is what keeps `index` and `total` truthful:
    // the counter must not promise judgements that can never be made. It is
    // also why `jumpTo` needs no change -- it already no-ops on an id it
    // cannot find, so a startAtRallyId naming a hand-made half lands on
    // index 0.
    //
    // Deliberately narrower than QueueController's filtering: a REJECTED
    // rally stays, because it is precisely the `not_play` the corpus is
    // short of (see below).
    this.#rallies = rallies.filter(isDetected)
    const bySpan = new Map<string, LabelRecord>()
    for (const rec of existing) {
      bySpan.set(spanKey(rec.source_id, rec.span_start_ms, rec.span_end_ms), rec)
    }

    for (const r of this.#rallies) {
      const rec = bySpan.get(spanKey(r.source_id, r.det_start_ms, r.det_end_ms))
      // A verdict-less row is a boundary correction from a drag, not a
      // judgement -- rendering it as one would invent a verdict the reviewer
      // never gave.
      if (!rec || rec.verdict === null) continue
      this.#verdicts.set(r.id, rec.verdict)
      this.#flags.set(r.id, [...rec.boundary_flags])
    }
  }

  get current(): DetectedRally | undefined {
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

  /**
   * Opens on `rallyId` instead of index 0. Mirrors QueueController.jumpTo,
   * including its "silently does nothing for an unknown id" fallback -- which
   * matters more here than there: this controller's list is unfiltered while
   * the queue's excludes rejected rallies, so the two genuinely differ and an
   * id from one can be absent from the other (e.g. a re-segment gave every
   * rally a new uuid since the id was captured).
   */
  jumpTo(rallyId: string): void {
    const i = this.#rallies.findIndex((r) => r.id === rallyId)
    if (i !== -1) this.#index = i
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

    // Returns an action even when the restored state has no verdict. That
    // case used to return null and persist nothing, on the reasoning that
    // the append-only corpus has no "unlabel" -- but the reviewer was then
    // left looking at a clip the corpus still called 'clean', and a reload
    // brought the verdict back. `retract_label` (splitstep/db/labels.py) is
    // the append-only way to say it: one more row, superseding the
    // judgement without erasing it.
    return {
      rallyId: r.id,
      verdict: entry.verdict,
      flags: [...entry.flags],
      previousVerdict,
      previousFlags,
    }
  }

  /**
   * Puts one rally into an explicit state after its POST failed.
   *
   * Rally-correlated rather than position-correlated, exactly like
   * QueueController.revert: it targets `rallyId` wherever that sits, never
   * touches `#index`, and never pops `#history`. A failed network call must
   * not move the reviewer's position or consume their undo.
   *
   * Takes a state rather than the failed action, which is the one difference
   * from QueueController.revert: LabelWriter can have several writes queued
   * for one rally, and when a run of them fails the state to fall back to is
   * the last one the server actually accepted -- not the failed action's own
   * predecessor, which may never have landed either.
   */
  restore(rallyId: string, state: LabelState): void {
    if (state.verdict === null) this.#verdicts.delete(rallyId)
    else this.#verdicts.set(rallyId, state.verdict)
    this.#flags.set(rallyId, [...state.flags])
  }
}

/** The subset of `api` that persisting a LabelAction needs. */
export interface LabelApi {
  label: (id: string, verdict: string, boundaryFlags: string[]) => Promise<unknown>
  retractLabel: (id: string) => Promise<unknown>
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
    // A retraction is its own route, not a label with a null verdict: a
    // verdict-less label row is what a boundary drag writes, and the two
    // carry opposite meanings server-side (one withdraws a judgement, the
    // other asserts a measurement while making no judgement at all).
    if (action.verdict === null) await api.retractLabel(action.rallyId)
    else await api.label(action.rallyId, action.verdict, action.flags)
    return { ok: true }
  } catch {
    return { ok: false }
  }
}

/**
 * The outcome of one queued write.
 *
 * `superseded` is the interesting one: the write failed, but the reviewer
 * has already made a newer action for the same rally that is still queued.
 * Reverting there would drag the screen back to a state the reviewer left
 * two keystrokes ago, and the newer write carries the whole state anyway --
 * so the failure is absorbed and the newer write decides what is true.
 */
export type WriteOutcome =
  | { status: 'ok' }
  | { status: 'superseded' }
  | { status: 'failed'; restore: LabelState }

/**
 * Serialises label writes per rally.
 *
 * LabelMode fires a POST per keystroke without awaiting the previous one, so
 * a fast `clean` -> `Q` -> `P` burst put three requests on the wire at once.
 * Every request carries the span's whole state (verdict + flags) and the
 * server resolves the current label as the latest row written, so whichever
 * request happened to land last won -- and that is not necessarily the last
 * one the reviewer made. The screen and the corpus then disagreed, silently
 * and permanently.
 *
 * One in-flight write per rally fixes the ordering at the source instead of
 * detecting the reordering afterwards. Per rally, not global: a rally is a
 * detector span (one source's spans are disjoint, and the server anchors
 * every label to (source_id, span)), so two rallies' writes cannot collide,
 * and making them queue behind each other would stall a whole labelling pass
 * behind one slow request on a LAN box.
 *
 * Every action still gets its own POST -- the corpus is append-only and its
 * history is the point (clip #1's hand label was wrong, and the correction
 * was itself the finding), so nothing here coalesces a burst into one write.
 *
 * What this does NOT order is two browser tabs labelling the same rally. A
 * queue is per client. Nothing short of a server-side revision would order
 * that, and the cost of getting it wrong there is one reviewer overwriting
 * their own other window -- rare, visible on the next reload, and still
 * fully recoverable from history. Reordering inside ONE tab was neither
 * rare nor visible.
 */
export class LabelWriter {
  #api: LabelApi
  // Per rally: the tail of its write chain, how many of its writes are
  // queued-but-unsettled, and the last state the server is known to hold.
  #tail = new Map<string, Promise<unknown>>()
  #queued = new Map<string, number>()
  #confirmed = new Map<string, LabelState>()

  constructor(api: LabelApi) {
    this.#api = api
  }

  submit(action: LabelAction): Promise<WriteOutcome> {
    const key = action.rallyId
    const depth = (this.#queued.get(key) ?? 0) + 1
    this.#queued.set(key, depth)
    // Nothing was in flight for this rally, so what the server holds right
    // now is exactly what this action supersedes. Captured here rather than
    // read off the failed action later, because a whole burst can fail and
    // then the action's own predecessor never reached the server either.
    if (depth === 1) {
      this.#confirmed.set(key, {
        verdict: action.previousVerdict,
        flags: [...action.previousFlags],
      })
    }

    const run = async (): Promise<WriteOutcome> => {
      const outcome = await persistLabel(action, this.#api)
      const remaining = (this.#queued.get(key) ?? 1) - 1
      this.#queued.set(key, remaining)

      if (outcome.ok) {
        this.#confirmed.set(key, { verdict: action.verdict, flags: [...action.flags] })
        return { status: 'ok' }
      }
      if (remaining > 0) return { status: 'superseded' }
      const confirmed = this.#confirmed.get(key) ?? {
        verdict: action.previousVerdict,
        flags: [...action.previousFlags],
      }
      return { status: 'failed', restore: { verdict: confirmed.verdict, flags: [...confirmed.flags] } }
    }

    // `then(run, run)` rather than `then(run)`: persistLabel swallows its own
    // rejections today, but a chain that breaks on one rejected link would
    // strand every later write for that rally with no way to notice.
    const next = (this.#tail.get(key) ?? Promise.resolve()).then(run, run)
    this.#tail.set(key, next)
    return next
  }
}
