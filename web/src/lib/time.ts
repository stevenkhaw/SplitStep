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
