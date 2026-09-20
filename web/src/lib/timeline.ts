import { clamp } from './time'
import { winnerLabel } from './score'
import type { ScoreRules } from './score'
import type { Rally, Source } from './types'

export interface TimelineMark {
  sourceId: string
  offsetMs: number
  gapMs: number
}

export interface Timeline {
  totalMs: number
  marks: TimelineMark[]
}

/**
 * Build the virtual session timeline.
 *
 * Sources are laid out CONTIGUOUSLY, not to real elapsed time: rendering a
 * 22-minute water break to scale would spend half the overview on dead space.
 * The real gap is carried on the mark so the UI can label it.
 */
export function sessionTimeline(sources: Source[]): Timeline {
  const ordered = [...sources].sort((a, b) => a.idx - b.idx)
  const marks: TimelineMark[] = []
  let totalMs = 0

  ordered.forEach((s, i) => {
    let gapMs = 0
    if (i > 0) {
      const prev = ordered[i - 1]
      const prevEnd = Date.parse(prev.recorded_at) + prev.duration_ms
      const thisStart = Date.parse(s.recorded_at)
      gapMs = Number.isFinite(prevEnd) && Number.isFinite(thisStart)
        ? Math.max(0, thisStart - prevEnd)
        : 0
    }
    marks.push({ sourceId: s.id, offsetMs: s.offset_ms, gapMs })
    totalMs += s.duration_ms
  })

  return { totalMs, marks }
}

export function toSessionMs(sources: Source[], sourceId: string, localMs: number): number {
  const s = sources.find((x) => x.id === sourceId)
  return s ? s.offset_ms + localMs : localMs
}

export function msToFraction(ms: number, totalMs: number): number {
  if (totalMs <= 0) return 0
  return clamp(ms / totalMs, 0, 1)
}

export function fractionToMs(fraction: number, totalMs: number): number {
  return Math.round(clamp(fraction, 0, 1) * totalMs)
}

/**
 * Where a pointer at `xPx` along a bar of `widthPx` lands in a source.
 *
 * The bar spans the WHOLE source, not one rally's span -- this is what the
 * add-a-rally scrub reaches the rest of the file with, where `VideoDeck`
 * only ever plays between an existing in and out point.
 *
 * The zero-width guard is not defensive padding: a bar measured before
 * layout (a `$effect` racing the first paint, a hidden panel) reports
 * `width: 0`, and `fractionToMs` cannot absorb the resulting Infinity/NaN
 * -- `clamp`'s comparisons are both false for NaN, so it passes straight
 * through to `Math.round` and a NaN reaches the commit as a rally span.
 * Out-of-range x needs no guard here; `fractionToMs` already clamps, which
 * is what makes a click past either end of the bar land on the end.
 */
export function scrubMsAt(xPx: number, widthPx: number, durationMs: number): number {
  if (widthPx <= 0) return 0
  return fractionToMs(xPx / widthPx, durationMs)
}

/**
 * The inverse of `scrubMsAt`: where `ms` is drawn along the same bar.
 *
 * In pixels, to round-trip against the pointer coordinate the caller
 * measured. A caller positioning with a percentage should use
 * `msToFraction` directly rather than dividing this back out.
 */
export function scrubXFor(ms: number, widthPx: number, durationMs: number): number {
  return msToFraction(ms, durationMs) * widthPx
}

/** A window of `spanMs` around `centerMs`, shifted (never shrunk) to stay in bounds. */
export function zoomWindow(
  centerMs: number,
  spanMs: number,
  totalMs: number,
): { startMs: number; endMs: number } {
  const span = Math.min(spanMs, totalMs)
  let startMs = centerMs - span / 2
  if (startMs < 0) startMs = 0
  if (startMs + span > totalMs) startMs = totalMs - span
  return { startMs: Math.round(startMs), endMs: Math.round(startMs + span) }
}

/**
 * Minimum gap enforced between a rally's start and end. Shared by every
 * bounds-editing path -- ZoomBand's own per-frame drag clamp and
 * TimelineMode's `[`/`]` keyboard commit (see `clampMinGap`) -- so neither
 * can persist a rally narrower than this, and the keyboard path in
 * particular can never persist an inverted one (start_ms > end_ms).
 */
export const MIN_RALLY_MS = 100

/**
 * Enforces `endMs >= startMs + minMs`, preserving `startMs` exactly and
 * pulling `endMs` forward when the gap is too small -- including when it's
 * inverted (endMs < startMs).
 *
 * `startMs` is always the anchor: TimelineMode's `[` passes the live
 * playhead position as `startMs` (so that exact in-point survives even in
 * the degenerate case) and the rally's untouched `end_ms` as `endMs`; `]`
 * passes the untouched `start_ms` as `startMs` (correctly anchoring it) and
 * the live playhead as `endMs`. Either way this never moves the value the
 * caller is trying to set, only the one it isn't.
 *
 * A drag-committed pair from ZoomBand already satisfies the invariant (its
 * own per-frame clamp enforces the same minimum), so applying this again is
 * a no-op for that path -- it exists here as the single shared backstop so
 * every caller of TimelineMode's `commitBounds` gets the same guarantee,
 * not just the ones that already remembered to clamp themselves.
 */
export function clampMinGap(
  startMs: number,
  endMs: number,
  minMs: number = MIN_RALLY_MS,
): { startMs: number; endMs: number } {
  if (endMs - startMs < minMs) return { startMs, endMs: startMs + minMs }
  return { startMs, endMs }
}

/**
 * Confine a draft span to a source that actually holds it.
 *
 * An existing rally is edited against bounds the detector already put
 * inside the file; a hand-drawn span has no such history, so its ends can
 * sit anywhere the pointer or the playhead did. The far end is the one
 * that bites: `lastSafeFrameMs` keeps playback short of the reported
 * duration, but a drag released past the bar, or a `]` on a playhead the
 * browser rounded up, both reach past it.
 *
 * `startMs` stays the anchor, as everywhere else in this file -- it is
 * clamped first so the `clampMinGap` backstop can only ever push the end
 * to exactly `durationMs`, never past it. The result is rounded because
 * this is what goes to the server as a span, and `fractionToMs` -- the
 * other end of the same scrub -- already deals in whole milliseconds.
 *
 * Crossing in and out is NOT this function's job: `setInPoint` and
 * `setOutPoint` already refuse that outright rather than collapsing a
 * span, and a draft goes through them for the same reason a rally does.
 * `clampMinGap` survives here only as the backstop for a pair that never
 * passed through either.
 */
export function clampSpanToSource(
  startMs: number,
  endMs: number,
  durationMs: number,
  minMs: number = MIN_RALLY_MS,
): { startMs: number; endMs: number } {
  // A source too short to hold one rally cannot be trimmed into one; hand
  // back the whole file rather than a span reaching past its own end.
  if (durationMs <= minMs) return { startMs: 0, endMs: Math.max(0, Math.round(durationMs)) }
  const start = clamp(startMs, 0, durationMs - minMs)
  const gapped = clampMinGap(start, clamp(endMs, 0, durationMs), minMs)
  return { startMs: Math.round(gapped.startMs), endMs: Math.round(gapped.endMs) }
}

/**
 * The result of a keyboard bounds edit: either a new pair of bounds, or a
 * refusal carrying a reason the UI can show the user.
 */
export type BoundsEdit =
  | { ok: true; startMs: number; endMs: number }
  | { ok: false; reason: string }

/**
 * Set a rally's in-point to the playhead, refusing rather than collapsing.
 *
 * `clampMinGap` cannot express "no". Given an in-point past the rally's end it
 * anchors the in-point and drags the out-point to `start + MIN_RALLY_MS`,
 * which prevents an inverted rally but silently destroys a good one. That is
 * not hypothetical: rally 17 of session 2026-08-18 was a 9.6 s rally found in
 * the database as a 100 ms sliver seven seconds past its own end, because `[`
 * was pressed with the playhead parked out there. There was no confirmation,
 * no undo in this mode, and no save feedback, so it happened invisibly.
 *
 * Refusing does not block re-spanning a rally wholesale -- the one workflow
 * the collapse behaviour supported. Move the out-point first, then the
 * in-point follows; only the order changes.
 */
export function setInPoint(
  startMs: number,
  endMs: number,
  playheadMs: number,
  minMs: number = MIN_RALLY_MS,
): BoundsEdit {
  if (playheadMs > endMs - minMs) {
    return {
      ok: false,
      reason: "That's past this rally's end — set the out-point first, then the in-point.",
    }
  }
  return { ok: true, startMs: playheadMs, endMs }
}

/** Mirror of `setInPoint` for the out-point. See its comment for the why. */
export function setOutPoint(
  startMs: number,
  endMs: number,
  playheadMs: number,
  minMs: number = MIN_RALLY_MS,
): BoundsEdit {
  if (playheadMs < startMs + minMs) {
    return {
      ok: false,
      reason: "That's before this rally's start — set the in-point first, then the out-point.",
    }
  }
  return { ok: true, startMs, endMs: playheadMs }
}

/** Which drag handle, if any, a pointer at `xFraction` is grabbing. */
export function nearestHandle(
  xFraction: number,
  startFraction: number,
  endFraction: number,
  grabPx: number,
  widthPx: number,
): 'start' | 'end' | null {
  const grab = grabPx / widthPx
  const dStart = Math.abs(xFraction - startFraction)
  const dEnd = Math.abs(xFraction - endFraction)
  if (dStart > grab && dEnd > grab) return null
  return dStart <= dEnd ? 'start' : 'end'
}

/**
 * Map a detector score (0..1) to an SVG y-coordinate, higher score drawn
 * higher up. `height` is the viewBox height; the curve is inset 2px from the
 * top so a score of exactly 1 doesn't clip against the viewBox edge.
 */
export function scoreToY(score: number, height: number): number {
  return height - clamp(score, 0, 1) * (height - 2)
}

/**
 * SVG `points` for the score curve visible under the zoomed band.
 *
 * `scores` is the full per-source series at a fixed `stepMs` cadence; only
 * the slice covering [windowStartMs, windowEndMs] is drawn, stretched to
 * fill [0, width] regardless of how many samples fall in that slice -- the
 * window is a fixed span (see zoomWindow) but the sample count within it can
 * vary by a step at either edge, so this is index-based, not ms-based,
 * spacing.
 */
export function scoreCurvePoints(
  scores: number[],
  stepMs: number,
  windowStartMs: number,
  windowEndMs: number,
  width: number,
  height: number,
): string {
  if (scores.length === 0 || stepMs <= 0) return ''
  const from = Math.max(0, Math.floor(windowStartMs / stepMs))
  const to = Math.min(scores.length, Math.ceil(windowEndMs / stepMs))
  const slice = scores.slice(from, to)
  if (slice.length < 2) return ''
  return slice
    .map((s, i) => `${(i / (slice.length - 1)) * width},${scoreToY(s, height)}`)
    .join(' ')
}

/**
 * What an overview-band bar calls itself, in its title and its accessible
 * name. The band's visual channels are full -- fill carries starred, a
 * hatch carries rejected -- and its bars are routinely under a pixel wide
 * at session scale, so a winner cannot be a glyph or a second hue there
 * (and a hue for A vs B is ruled out regardless; see app.css). The label
 * is the one honest channel left.
 */
export function rallyBandLabel(rally: Rally, rules: ScoreRules | null): string {
  const won = winnerLabel(rally.winner, rules)
  return `rally ${rally.idx}${rally.rejected ? ' (rejected)' : ''}${won ? ` \u00b7 ${won}` : ''}`
}
