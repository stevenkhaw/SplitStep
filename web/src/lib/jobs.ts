import type { Job } from './types'

// What the queue considers unfinished. Mirrors splitstep/db/jobs.py, where
// claim() and has_pending_job() both use exactly these two.
const ACTIVE = new Set(['queued', 'running'])

/**
 * The badge's line for whatever the worker is doing, or null when it is idle.
 *
 * The count alone was the only feedback anywhere during a clip export, and at
 * 4-8x realtime a 24-point session is about half an hour of it saying the
 * same thing. `handle_clip` now writes `jobs.progress`, so there is a real
 * fraction to show -- but only the clip handler writes one, and a detect job
 * averaged in at zero would pin the badge at 0% for fifteen minutes, which
 * reads as stuck. Hence: no percentage at all until something reports one.
 *
 * The fraction is over every active job, queued ones included at zero,
 * because the question during an export is how far through the batch it is
 * rather than how far through the clip currently encoding.
 */
export function activeJobsLabel(jobs: Job[]): string | null {
  const active = jobs.filter((j) => ACTIVE.has(j.status))
  if (active.length === 0) return null

  const count = `${active.length} job${active.length === 1 ? '' : 's'} running`
  const total = active.reduce((sum, j) => sum + (j.progress || 0), 0)
  if (total <= 0) return count

  // Floor, not round: a bar must never claim a percent the encode has not
  // reached. Capped below 100 because ffmpeg's last frame is not the job's
  // last step -- the temp file still has to be replaced onto the real name
  // and the row written -- and a badge reading "100% running" looks wedged.
  const pct = Math.min(99, Math.floor((total / active.length) * 100))
  return `${count} · ${pct}%`
}
