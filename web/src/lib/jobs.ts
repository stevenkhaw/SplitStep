import type { Job } from './types'

// What the queue considers unfinished. Mirrors splitstep/db/jobs.py, where
// claim() and has_pending_job() both use exactly these two.
const ACTIVE = new Set(['queued', 'running'])

/**
 * The five handlers in splitstep/jobs/handlers.py, named for what the person
 * watching would call them rather than for the row in the jobs table.
 *
 * The badge previously read "1 job running", which says only that the queue
 * is a queue. These are the phase names the pipeline is already described in
 * (CLAUDE.md's watcher -> ingest -> build_proxy -> detect diagram), so the
 * badge and the docs agree.
 */
export const JOB_PHASES: Record<string, string> = {
  ingest: 'Reading footage',
  build_proxy: 'Building proxy',
  detect: 'Finding rallies',
  clip: 'Cutting clips',
  reel: 'Rendering reel',
}

/** A handler added server-side without a label here surfaces as itself,
 *  which is wrong-looking but true, rather than blank or mislabelled. */
export function jobPhase(type: string): string {
  return JOB_PHASES[type] ?? type
}

/**
 * Elapsed time, at the coarsest unit that still moves while you watch it.
 *
 * Seconds are zero-padded once there are minutes so the badge keeps its
 * width: it repaints every 3s with the poll, and a string that grows and
 * shrinks a character sits next to the session title and twitches.
 */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  if (total < 60) return `${total}s`
  const m = Math.floor(total / 60)
  if (m < 60) return `${m}m${String(total % 60).padStart(2, '0')}s`
  return `${Math.floor(m / 60)}h${String(m % 60).padStart(2, '0')}m`
}

function startedMs(j: Job): number {
  const t = Date.parse(j.created_at)
  return Number.isNaN(t) ? Number.POSITIVE_INFINITY : t
}

/**
 * The badge's line, or null when the worker is idle.
 *
 * Shape: `Building proxy · 4m20s · 43% · +2 queued`, with the last two parts
 * appearing only when they have something to say.
 *
 * **Elapsed is measured from the batch, not from the job currently running.**
 * There is no started_at column -- `claim()` only flips status -- so the one
 * honest reading of created_at is "when this work was asked for". That is
 * also the more useful number: during a 25-clip export the question is how
 * long the export has been going, not how far into clip 19 ffmpeg is. It is
 * the same call the percentage below already makes.
 *
 * `nowMs` is a parameter rather than a Date.now() call so the whole function
 * stays pure and its tests do not need a clock.
 */
export function activeJobsLabel(jobs: Job[], nowMs: number): string | null {
  const active = jobs.filter((j) => ACTIVE.has(j.status))
  if (active.length === 0) return null

  // The worker is single-threaded, so at most one job is ever `running`. If
  // the poll catches the gap between one finishing and the next being
  // claimed, the oldest queued job is what the reviewer is waiting on.
  const primary =
    active.find((j) => j.status === 'running') ??
    [...active].sort((a, b) => startedMs(a) - startedMs(b))[0]

  const since = Math.min(...active.map(startedMs))
  const parts = [jobPhase(primary.type), formatElapsed(nowMs - since)]

  // A detect job never reports progress, so averaging it in would pin the
  // badge at 0% for fifteen minutes -- which reads as stuck, and is strictly
  // worse than showing no number. Queued jobs count as zero on purpose: the
  // fraction is over the batch.
  const total = active.reduce((sum, j) => sum + (j.progress || 0), 0)
  if (total > 0) {
    // Floor, and capped below 100: ffmpeg's last frame is not the job's last
    // step -- the temp file still has to be replaced onto the real name and
    // the row written -- and a badge reading 100% looks wedged.
    parts.push(`${Math.min(99, Math.floor((total / active.length) * 100))}%`)
  }

  const waiting = active.length - 1
  if (waiting > 0) parts.push(`+${waiting} queued`)

  return parts.join(' · ')
}

export interface JobRow {
  id: string
  phase: string
  status: string
  /** null for anything not running: a queued job has not started, so an
   *  elapsed time for it would be a measurement of nothing. */
  elapsed: string | null
  progress: number
  error: string | null
}

/**
 * Rows for the panel behind the badge.
 *
 * Running first, then the queue in the order the worker will actually take
 * it (oldest created_at first, matching idx_jobs_queued), then failures.
 * Failed jobs stay listed rather than being counted separately because they
 * are the one thing here the reviewer may have to act on, and the error text
 * is what says which.
 */
export function jobRows(jobs: Job[], nowMs: number): JobRow[] {
  const byAge = (a: Job, b: Job) => startedMs(a) - startedMs(b)
  const running = jobs.filter((j) => j.status === 'running').sort(byAge)
  const queued = jobs.filter((j) => j.status === 'queued').sort(byAge)
  const failed = jobs.filter((j) => j.status === 'failed').sort(byAge)

  const since = Math.min(...[...running, ...queued].map(startedMs))

  return [...running, ...queued, ...failed].map((j) => ({
    id: j.id,
    phase: jobPhase(j.type),
    status: j.status,
    elapsed: j.status === 'running' ? formatElapsed(nowMs - since) : null,
    progress: j.progress || 0,
    error: j.error,
  }))
}
