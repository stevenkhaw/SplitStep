import { clamp, lastSafeFrameMs } from './time'

/**
 * Timestamps for the setup grid: evenly spread across the clip rather than
 * randomly sampled. Even spacing is reproducible across reloads and visibly
 * covers the whole session; random sampling only appears to.
 *
 * Never includes t=0 -- the frame a phone records as the shutter is hit is
 * routinely black, which is the reason this grid exists.
 *
 * This property holds for any clip with at least one frame beyond the
 * end-of-stream safety margin (lastSafeFrameMs). For a clip shorter than one
 * frame period, t=0 is the only decodable frame the file has; returning it
 * is correct, because returning an empty array would leave the wizard with
 * nothing to show and no way to place a play region.
 */
export function previewTimestamps(durationMs: number, fps: number, n = 9): number[] {
  const max = lastSafeFrameMs(durationMs, fps)
  const out: number[] = []
  for (let i = 1; i <= n; i++) {
    out.push(Math.round(clamp((durationMs * i) / (n + 1), 0, max)))
  }
  return [...new Set(out)]
}
