import { describe, expect, it } from 'vitest'
import { activeJobsLabel } from '../src/lib/jobs'
import type { Job } from '../src/lib/types'

function job(over: Partial<Job> = {}): Job {
  return { id: 'j', type: 'clip', status: 'running', progress: 0, error: null, ...over }
}

describe('activeJobsLabel', () => {
  it('is null when nothing is queued or running', () => {
    expect(activeJobsLabel([])).toBeNull()
    expect(activeJobsLabel([job({ status: 'done', progress: 1 })])).toBeNull()
    expect(activeJobsLabel([job({ status: 'failed' })])).toBeNull()
  })

  it('counts queued jobs alongside running ones', () => {
    const label = activeJobsLabel([job({ status: 'running' }), job({ status: 'queued' })])
    expect(label).toBe('2 jobs running')
  })

  it('says job, not jobs, for one', () => {
    expect(activeJobsLabel([job()])).toBe('1 job running')
  })

  // A detect job never reports progress, so averaging it in would pin the
  // badge at 0% for fifteen minutes -- which reads as stuck, and is strictly
  // worse than the count alone. No number until something has one.
  it('shows no percentage while every active job is at zero', () => {
    expect(activeJobsLabel([job({ progress: 0 }), job({ status: 'queued' })])).toBe(
      '2 jobs running',
    )
  })

  // The question during a half-hour export is "how far through the batch",
  // not "how far through this one clip", so queued jobs count as zero and
  // the fraction is over all of them.
  it('averages progress across every active job once one reports', () => {
    const label = activeJobsLabel([
      job({ progress: 0.5 }),
      job({ status: 'queued', progress: 0 }),
    ])
    expect(label).toBe('2 jobs running · 25%')
  })

  it('ignores finished jobs when averaging', () => {
    const label = activeJobsLabel([
      job({ status: 'done', progress: 1 }),
      job({ progress: 0.4 }),
    ])
    expect(label).toBe('1 job running · 40%')
  })

  // ffmpeg reaching its last frame is not the job being over: the temp file
  // still has to be replaced onto the real name and the row written. A badge
  // reading "100% running" looks wedged, so the last percent is held back
  // until the job actually leaves the active set.
  it('never reads 100% while a job is still active', () => {
    expect(activeJobsLabel([job({ progress: 1 })])).toBe('1 job running · 99%')
  })
})
