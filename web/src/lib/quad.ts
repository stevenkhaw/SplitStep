import type { Preset } from './types'

/**
 * A trapezoid roughly matching a baseline camera: narrow at the far
 * baseline, wide at the near one, extended to the frame bottom (y = 1.0) so
 * a near player standing between the camera and the baseline stays inside.
 * `Quad.contains` (splitstep/detect/geometry.py) is inclusive on every edge
 * specifically so a foot point landing exactly on y = 1.0 -- which happens
 * constantly at ~1ft camera height, where the near player's box is clipped
 * by the frame edge -- still counts as inside the play region.
 */
export const DEFAULT_QUAD_POINTS: [number, number][] = [
  [0.35, 0.35],
  [0.65, 0.35],
  [0.98, 1.0],
  [0.02, 1.0],
]

export function clonePoints(points: [number, number][]): [number, number][] {
  return points.map(([x, y]) => [x, y])
}

export interface Rect {
  left: number
  top: number
  width: number
  height: number
}

/**
 * Maps a pointer's client coordinates into a point normalized to [0, 1]
 * within `rect`, clamped to stay inside it.
 *
 * `rect` must be the bounding rect of the element the frame image actually
 * *renders into* -- not necessarily its outer container. QuadEditor avoids
 * this ever being a distinct, letterboxed sub-rect of a larger fixed-aspect
 * box (the `object-contain`-inside-`aspect-video` hazard): its wrapping
 * element has no forced aspect ratio, so the image lays out at its natural
 * proportions and the wrapper's own rect always equals the image's
 * rendered rect exactly, whatever the source video's aspect ratio is. That
 * makes this function correct for the wrapper's rect with no separate
 * "content rect" computation needed.
 */
export function pointFromClient(clientX: number, clientY: number, rect: Rect): [number, number] {
  const w = rect.width || 1
  const h = rect.height || 1
  const x = (clientX - rect.left) / w
  const y = (clientY - rect.top) / h
  return [Math.min(1, Math.max(0, x)), Math.min(1, Math.max(0, y))]
}

/** Immutably replaces the point at `index`; every other point is untouched. */
export function movePoint(
  points: [number, number][],
  index: number,
  next: [number, number],
): [number, number][] {
  return points.map((old, i) => (i === index ? next : old))
}

/** CSS `clip-path: polygon(...)` value for the shaded play-region overlay. */
export function polygonClipPath(points: [number, number][]): string {
  return `polygon(${points.map(([x, y]) => `${(x * 100).toFixed(4)}% ${(y * 100).toFixed(4)}%`).join(', ')})`
}

/**
 * A quad's corners as an SVG `points` attribute, scaled into a `width` x
 * `height` viewBox.
 *
 * For the thumbnail the detection panel shows beside each region -- the
 * shape itself, at about the size of a word, so "the features were built
 * under a different quad" is something a reviewer sees rather than infers
 * from two timestamps. A frame behind it would cost one server-side ffmpeg
 * extraction per panel (QuadCanvas' cost, and the reason QuadEditor stays
 * collapsed), and the shape alone answers the question being asked.
 *
 * Two decimals: this string is markup, and a full double per corner would
 * put seventeen characters of noise in the DOM to place a 40px polygon to
 * within a hundredth of a pixel. `Number()` drops the trailing zeros
 * `toFixed` adds, so a whole number stays whole.
 */
export function quadPolygonPoints(
  points: [number, number][],
  width: number,
  height: number,
): string {
  return points
    .map(([x, y]) => `${Number((x * width).toFixed(2))},${Number((y * height).toFixed(2))}`)
    .join(' ')
}

export function defaultPresetName(sessionId: string, sourceIdx: number): string {
  return `${sessionId} source ${sourceIdx}`
}

/**
 * Human-readable description of what (if anything) is currently assigned
 * to a source, for the "detection still uses the full frame until you
 * re-run it" messaging. `presetId` is `null` when no preset is assigned;
 * the assigned preset may also be legitimately absent from `presets` for a
 * moment (e.g. right after mount, before the list has loaded).
 */
export function assignedPresetLabel(presetId: string | null, presets: Preset[]): string {
  if (!presetId) return 'none -- detection runs on the full frame'
  const preset = presets.find((p) => p.id === presetId)
  return preset ? preset.name : presetId
}
