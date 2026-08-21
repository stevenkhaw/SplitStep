import { clamp } from './time'

/**
 * Map a pointer's x-fraction across the scrub bar to a timestamp inside the
 * rally's span, clamped to [startMs, endMs].
 *
 * The clamp is the whole point of this function existing separately from
 * `timeline.ts`'s `fractionToMs` (which maps onto [0, totalMs], not an
 * arbitrary [startMs, endMs] pair). Label mode loops inside a rally
 * specifically so the reviewer never sees the next rally's footage by
 * accident -- a drag that overshoots the bar (fraction < 0 from dragging
 * left of the track, or > 1 from dragging past its right edge, both routine
 * once a pointer has capture) must not be able to do by hand what the
 * out-point guard in VideoDeck exists to prevent.
 */
export function fractionToScrubMs(fraction: number, startMs: number, endMs: number): number {
  const span = Math.max(0, endMs - startMs)
  return clamp(startMs + fraction * span, startMs, endMs)
}

/**
 * Inverse of `fractionToScrubMs` -- where the fill/playhead should sit for a
 * given timestamp. Used to paint the bar immediately on a scrub click/drag,
 * ahead of VideoDeck's next `onprogress` tick, and would apply equally to a
 * zero-length span (guarded here rather than left to divide-by-zero) even
 * though `MIN_RALLY_MS` keeps that out of reach in practice.
 */
export function scrubMsToFraction(ms: number, startMs: number, endMs: number): number {
  const span = endMs - startMs
  if (span <= 0) return 0
  return clamp((ms - startMs) / span, 0, 1)
}
