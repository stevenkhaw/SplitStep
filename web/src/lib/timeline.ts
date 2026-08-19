import { clamp } from './time'
import type { Source } from './types'

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
