/** Format a timestamp for reading against a real session. */
export function formatTs(ms: number): string {
  const tenths = Math.max(0, Math.round(ms / 100))
  const totalSeconds = Math.floor(tenths / 10)
  const d = tenths % 10
  const s = totalSeconds % 60
  const m = Math.floor(totalSeconds / 60) % 60
  const h = Math.floor(totalSeconds / 3600)
  const mm = h > 0 ? String(m).padStart(2, '0') : String(m)
  const ss = String(s).padStart(2, '0')
  return h > 0 ? `${h}:${mm}:${ss}.${d}` : `${mm}:${ss}.${d}`
}

/** Rally lengths read better as plain seconds. */
export function formatDuration(ms: number): string {
  return `${(Math.max(0, ms) / 1000).toFixed(1)}s`
}

/** Step one frame. There is no native frame API; the proxy's 1s GOP makes this accurate. */
export function frameStep(ms: number, fps: number, dir: 1 | -1): number {
  if (!fps || fps <= 0) return ms
  return Math.max(0, ms + dir * (1000 / fps))
}

export function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v
}

/**
 * The last timestamp that reliably decodes to a real frame.
 *
 * Mirrors the server's own clamp in `api_frame` (splitstep/api/routes.py):
 * past end-of-stream ffmpeg fails with exit 234, and `duration_ms - 1` is
 * not far enough back -- the true last frame lands up to one frame period
 * before the reported duration. Clamping client-side too keeps a scrubber
 * dragged to its maximum from requesting a timestamp the server would
 * silently rewrite, which would otherwise make the slider position and the
 * displayed frame disagree.
 */
export function lastSafeFrameMs(durationMs: number, fps: number): number {
  const margin = fps > 0 ? Math.floor(1000 / fps) + 1 : Math.max(1, Math.floor(durationMs / 2))
  return Math.max(0, durationMs - margin)
}

/**
 * Where the quad editor opens a source.
 *
 * Not 0: phone footage habitually starts on a black or pocketed frame (the
 * moment the record button is hit), and a black frame is useless for
 * dragging a play region over. A little way in, the camera is normally
 * placed and pointed at the court. Clamped by `lastSafeFrameMs`, so a clip
 * shorter than this still opens on a real frame.
 */
export const DEFAULT_SCRUB_MS = 30_000
