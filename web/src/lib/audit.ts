import type { LabelAction, LabelApi, LabelState, Verdict } from './labels'
import type { SampleWindow } from './types'

/**
 * A labelling pass over windows the detector did not choose.
 *
 * Label mode walks rallies; this walks a blind sample (see
 * `splitstep/label_sample.py`), half of which is footage the detector
 * ignored. That half is the only thing in the app that can measure recall --
 * `span_recall` is over spans the detector proposed, so it cannot see play
 * it never proposed at all.
 *
 * Nothing here knows which windows were flagged, because nothing is told:
 * the API omits it deliberately, and the 2026-08-20 pass's own hand label
 * was wrong in a way only that blindness exposed.
 */

/** A span's identity, and the key `LabelWriter` serialises writes on. */
export function spanKey(startMs: number, endMs: number): string {
  return `${startMs}:${endMs}`
}

export function parseSpanKey(key: string): { startMs: number; endMs: number } {
  const [start, end] = key.split(':')
  return { startMs: Number(start), endMs: Number(end) }
}

/** The label rows this pass seeds from -- a subset of `LabelRecord`. */
export interface SeedLabel {
  span_start_ms: number
  span_end_ms: number
  verdict: Verdict | null
}

/** The subset of `api` the span-addressed routes need. */
export interface SpanApi {
  spanLabel: (
    sourceId: string,
    startMs: number,
    endMs: number,
    verdict: string,
    boundaryFlags: string[],
  ) => Promise<unknown>
  spanRetract: (sourceId: string, startMs: number, endMs: number) => Promise<unknown>
}

/**
 * Adapts the span routes to the `LabelApi` shape `LabelWriter` already
 * speaks, so this mode reuses that queue rather than growing a second one.
 *
 * `LabelWriter` keys on `action.rallyId`, which it treats as an opaque
 * string: it only needs two writes for the same thing to serialise behind
 * each other. A span key satisfies that exactly, and the reuse means the
 * supersede-and-restore rules that took a bug to get right (an out-of-order
 * burst leaving the corpus holding a state the reviewer had moved on from)
 * hold here without being restated.
 */
export function spanLabelApi(sourceId: string, api: SpanApi): LabelApi {
  return {
    label: (key, verdict, boundaryFlags) => {
      const { startMs, endMs } = parseSpanKey(key)
      return api.spanLabel(sourceId, startMs, endMs, verdict, boundaryFlags)
    },
    retractLabel: (key) => {
      const { startMs, endMs } = parseSpanKey(key)
      return api.spanRetract(sourceId, startMs, endMs)
    },
  }
}

interface HistoryEntry {
  index: number
  verdict: Verdict | null
}

export class AuditController {
  #windows: SampleWindow[]
  #verdicts = new Map<string, Verdict>()
  #history: HistoryEntry[] = []
  #index = 0

  constructor(windows: SampleWindow[], seed: SeedLabel[] = []) {
    this.#windows = windows
    for (const row of seed) {
      // Keyed on the exact span, and only spans in this sample are adopted.
      // A label from another seed's sample, or from the review queue, is a
      // real judgement about real footage -- just not about a window of
      // this pass, and counting it would inflate the progress readout.
      const key = spanKey(row.span_start_ms, row.span_end_ms)
      const inSample = windows.some(
        (w) => spanKey(w.start_ms, w.end_ms) === key,
      )
      if (inSample && row.verdict !== null) this.#verdicts.set(key, row.verdict)
    }
  }

  get total(): number {
    return this.#windows.length
  }

  get index(): number {
    return this.#index
  }

  get current(): SampleWindow | null {
    return this.#windows[this.#index] ?? null
  }

  get verdict(): Verdict | null {
    return this.verdictAt(this.#index)
  }

  verdictAt(i: number): Verdict | null {
    const w = this.#windows[i]
    if (!w) return null
    return this.#verdicts.get(spanKey(w.start_ms, w.end_ms)) ?? null
  }

  get judged(): number {
    return this.#windows.filter((_, i) => this.verdictAt(i) !== null).length
  }

  get done(): boolean {
    return this.total > 0 && this.judged === this.total
  }

  jumpTo(i: number): void {
    if (i >= 0 && i < this.total) this.#index = i
  }

  next(): void {
    this.jumpTo(this.#index + 1)
  }

  prev(): void {
    this.jumpTo(this.#index - 1)
  }

  /**
   * Judge the current window and move on.
   *
   * Returns the action to persist, or null when there is no window to judge.
   * Advancing is part of the same keystroke: a pass is a walk, and stopping
   * on each judged window would double every window's cost.
   */
  setVerdict(verdict: Verdict): LabelAction | null {
    const w = this.current
    if (!w) return null
    const key = spanKey(w.start_ms, w.end_ms)
    const previousVerdict = this.verdict
    this.#verdicts.set(key, verdict)
    this.#history.push({ index: this.#index, verdict: previousVerdict })
    this.next()
    return {
      rallyId: key,
      verdict,
      // Boundary flags belong to label mode, where a span is a rally whose
      // edges the detector chose and can have got wrong. A sampled window's
      // edges were chosen by a seeded tiling -- there is no edge here for a
      // human to call early or late.
      flags: [],
      previousVerdict,
      previousFlags: [],
    }
  }

  /**
   * Take back the last judgement, returning to the window it was made on.
   *
   * Restores whatever that window held before -- which is usually nothing,
   * but is the earlier verdict when the window had been judged twice. A
   * `null` verdict in the returned action is a retraction, a distinct route
   * server-side, not a label with a missing field.
   */
  undo(): LabelAction | null {
    const entry = this.#history.pop()
    if (!entry) return null
    const w = this.#windows[entry.index]
    if (!w) return null
    const key = spanKey(w.start_ms, w.end_ms)
    const previousVerdict = this.verdictAt(entry.index)
    if (entry.verdict === null) this.#verdicts.delete(key)
    else this.#verdicts.set(key, entry.verdict)
    this.#index = entry.index
    return {
      rallyId: key,
      verdict: entry.verdict,
      flags: [],
      previousVerdict,
      previousFlags: [],
    }
  }

  /**
   * Put a span back to the state the server last accepted, after a write
   * failed with nothing newer queued behind it. Mirrors
   * `LabelController.restore`: it touches neither the index nor the history,
   * because a failed network call must not move the reviewer or consume
   * their undo.
   */
  restore(key: string, state: LabelState): void {
    if (state.verdict === null) this.#verdicts.delete(key)
    else this.#verdicts.set(key, state.verdict)
  }
}
