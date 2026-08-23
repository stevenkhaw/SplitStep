import { describe, expect, it } from 'vitest'
import { activeJobsLabel, formatElapsed, jobPhase, jobRows } from '../src/lib/jobs'
import type { Job } from '../src/lib/types'

const T0 = Date.parse('2026-08-23T12:00:00Z')
const at = (secondsAfterT0: number) => new Date(T0 + secondsAfterT0 * 1000).toISOString()

function job(over: Partial<Job> = {}): Job {
  return {
    id: 'j',
    type: 'clip',
    status: 'running',
    progress: 0,
    error: null,
    created_at: at(0),
    finished_at: null,
    ...over,
  }
}

describe('jobPhase', () => {
  // The badge used to say "1 job running", which names the queue's
  // implementation rather than anything the reviewer recognises. These are
  // the five handlers in splitstep/jobs/handlers.py, named for the phase the
  // pipeline doc already calls them.
  it('names every handler in terms of what it is doing', () => {
    expect(jobPhase('ingest')).toBe('Reading footage')
    expect(jobPhase('build_proxy')).toBe('Building proxy')
    expect(jobPhase('detect')).toBe('Finding rallies')
    expect(jobPhase('clip')).toBe('Cutting clips')
    expect(jobPhase('reel')).toBe('Rendering reel')
  })

  // A handler added server-side without a label here should surface as
  // itself rather than as a blank or a lie.
  it('falls back to the raw type for a handler it does not know', () => {
    expect(jobPhase('transcode_audio')).toBe('transcode_audio')
  })
})

describe('formatElapsed', () => {
  it('reads as seconds under a minute', () => {
    expect(formatElapsed(0)).toBe('0s')
    expect(formatElapsed(45_000)).toBe('45s')
    expect(formatElapsed(59_900)).toBe('59s')
  })

  // Zero-padded so the badge does not change width every ten seconds, which
  // at a 3s poll is a visible twitch next to the session title.
  it('pads the seconds once there are minutes', () => {
    expect(formatElapsed(60_000)).toBe('1m00s')
    expect(formatElapsed(260_000)).toBe('4m20s')
  })

  it('drops to hours and minutes past an hour', () => {
    expect(formatElapsed(3_600_000)).toBe('1h00m')
    expect(formatElapsed(3_720_000)).toBe('1h02m')
  })

  it('never runs backwards on a clock skew', () => {
    expect(formatElapsed(-5_000)).toBe('0s')
  })
})

describe('activeJobsLabel', () => {
  it('is null when nothing is queued or running', () => {
    expect(activeJobsLabel([], T0)).toBeNull()
    expect(activeJobsLabel([job({ status: 'done', progress: 1 })], T0)).toBeNull()
    expect(activeJobsLabel([job({ status: 'failed' })], T0)).toBeNull()
  })

  it('names the running job and how long the work has been going', () => {
    const jobs = [job({ type: 'build_proxy', created_at: at(0) })]
    expect(activeJobsLabel(jobs, T0 + 260_000)).toBe('Building proxy · 4m20s')
  })

  // The worker is single-threaded (jobq.claim takes one job at a time), so
  // exactly one job is ever `running`. It is the one the badge names, even
  // when a queued sibling was created first.
  it('names the running job, not the oldest one', () => {
    const jobs = [
      job({ type: 'clip', status: 'queued', created_at: at(0) }),
      job({ type: 'detect', status: 'running', created_at: at(30) }),
    ]
    expect(activeJobsLabel(jobs, T0 + 60_000)).toMatch(/^Finding rallies/)
  })

  // Elapsed is measured from the *batch*, not from the clip currently
  // encoding, for the same reason progress already averages over queued jobs
  // at zero: during a 25-clip export the question is how long this export has
  // been going, and there is no started_at column to answer the other one.
  it('measures elapsed from the earliest active job, not the running one', () => {
    const jobs = [
      job({ status: 'queued', created_at: at(0) }),
      job({ status: 'running', created_at: at(600) }),
    ]
    expect(activeJobsLabel(jobs, T0 + 660_000)).toMatch(/11m00s/)
  })

  it('ignores finished jobs when measuring elapsed', () => {
    const jobs = [
      job({ status: 'done', created_at: at(-3600) }),
      job({ status: 'running', type: 'reel', created_at: at(0) }),
    ]
    expect(activeJobsLabel(jobs, T0 + 45_000)).toBe('Rendering reel · 45s')
  })

  // A detect job never reports progress, so averaging it in would pin the
  // badge at 0% for fifteen minutes -- which reads as stuck, and is strictly
  // worse than no number at all.
  it('shows no percentage while every active job is at zero', () => {
    const jobs = [job({ type: 'detect', progress: 0 })]
    expect(activeJobsLabel(jobs, T0 + 1000)).toBe('Finding rallies · 1s')
  })

  it('averages progress across every active job once one reports', () => {
    const jobs = [
      job({ progress: 0.5, created_at: at(0) }),
      job({ status: 'queued', progress: 0, created_at: at(0) }),
    ]
    expect(activeJobsLabel(jobs, T0 + 1000)).toBe('Cutting clips · 1s · 25% · +1 queued')
  })

  // ffmpeg reaching its last frame is not the job being over: the temp file
  // still has to be replaced onto the real name and the row written. A badge
  // reading 100% looks wedged.
  it('never reads 100% while a job is still active', () => {
    expect(activeJobsLabel([job({ progress: 1 })], T0)).toBe('Cutting clips · 0s · 99%')
  })

  it('says how many are waiting behind the running one', () => {
    const jobs = [
      job({ status: 'running' }),
      job({ status: 'queued' }),
      job({ status: 'queued' }),
    ]
    expect(activeJobsLabel(jobs, T0)).toBe('Cutting clips · 0s · +2 queued')
  })
})

describe('jobRows', () => {
  it('is empty when there is nothing active and nothing failed', () => {
    expect(jobRows([job({ status: 'done' })], T0)).toEqual([])
  })

  // Running first, then the queue in the order the worker will take it, then
  // failures -- which stay listed because a failed job is the one thing here
  // the reviewer may have to act on.
  it('lists running, then queued oldest-first, then failed', () => {
    const jobs = [
      job({ id: 'q2', status: 'queued', type: 'clip', created_at: at(20) }),
      job({ id: 'f1', status: 'failed', type: 'reel', error: 'boom' }),
      job({ id: 'q1', status: 'queued', type: 'clip', created_at: at(10) }),
      job({ id: 'r1', status: 'running', type: 'detect', created_at: at(5) }),
    ]
    expect(jobRows(jobs, T0 + 60_000).map((r) => r.id)).toEqual(['r1', 'q1', 'q2', 'f1'])
  })

  it('carries the phase name and elapsed for the running job', () => {
    const rows = jobRows([job({ type: 'build_proxy', created_at: at(0) })], T0 + 90_000)
    expect(rows[0]).toMatchObject({ phase: 'Building proxy', status: 'running', elapsed: '1m30s' })
  })

  // A queued job has not started, so an elapsed time for it would be a
  // measurement of nothing.
  it('gives a queued job no elapsed time', () => {
    const rows = jobRows([job({ status: 'queued' })], T0 + 90_000)
    expect(rows[0].elapsed).toBeNull()
  })

  it('carries a failed job’s error so the row can explain itself', () => {
    const rows = jobRows([job({ status: 'failed', error: 'ffmpeg exit 234' })], T0)
    expect(rows[0]).toMatchObject({ status: 'failed', error: 'ffmpeg exit 234' })
  })
})
