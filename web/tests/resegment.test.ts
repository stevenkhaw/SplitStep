import { describe, expect, it } from 'vitest'
import { editedBoundaryCount, resegmentConfirmMessage } from '../src/lib/resegment'
import type { Rally } from '../src/lib/types'

function rally(overrides: Partial<Rally> = {}): Rally {
  return {
    id: 'r1',
    session_id: 's1',
    source_id: 'src1',
    idx: 1,
    start_ms: 1000,
    end_ms: 2000,
    det_start_ms: 1000,
    det_end_ms: 2000,
    confidence: 0.9,
    starred: 0,
    rejected: 0,
    reviewed_at: null,
    ...overrides,
  }
}

describe('editedBoundaryCount', () => {
  it('is 0 when every rally on the source matches its detected bounds', () => {
    const rallies = [rally({ id: 'r1' }), rally({ id: 'r2', idx: 2 })]
    expect(editedBoundaryCount(rallies, 'src1')).toBe(0)
  })

  it('counts a rally whose start_ms was hand-dragged away from det_start_ms', () => {
    const rallies = [rally({ id: 'r1', start_ms: 900 })]
    expect(editedBoundaryCount(rallies, 'src1')).toBe(1)
  })

  it('counts a rally whose end_ms was hand-dragged away from det_end_ms', () => {
    const rallies = [rally({ id: 'r1', end_ms: 2500 })]
    expect(editedBoundaryCount(rallies, 'src1')).toBe(1)
  })

  it('counts a rally with both bounds edited only once', () => {
    const rallies = [rally({ id: 'r1', start_ms: 900, end_ms: 2500 })]
    expect(editedBoundaryCount(rallies, 'src1')).toBe(1)
  })

  it('ignores edits on rallies belonging to a different source', () => {
    const rallies = [rally({ id: 'r1', source_id: 'src2', start_ms: 900 })]
    expect(editedBoundaryCount(rallies, 'src1')).toBe(0)
  })

  it('counts multiple edited rallies on the same source', () => {
    const rallies = [
      rally({ id: 'r1', start_ms: 900 }),
      rally({ id: 'r2', idx: 2, end_ms: 3500 }),
      rally({ id: 'r3', idx: 3 }), // untouched
    ]
    expect(editedBoundaryCount(rallies, 'src1')).toBe(2)
  })

  it('stars/rejects alone (no bounds change) do not count as edits', () => {
    const rallies = [rally({ id: 'r1', starred: 1, rejected: 0 })]
    expect(editedBoundaryCount(rallies, 'src1')).toBe(0)
  })
})

describe('resegmentConfirmMessage', () => {
  it('uses singular "boundary" for a count of 1', () => {
    expect(resegmentConfirmMessage(1)).toContain('1 hand-edited boundary ')
    expect(resegmentConfirmMessage(1)).not.toContain('boundaries')
  })

  it('uses plural "boundaries" for any other count', () => {
    expect(resegmentConfirmMessage(0)).toContain('0 hand-edited boundaries')
    expect(resegmentConfirmMessage(3)).toContain('3 hand-edited boundaries')
  })

  it('names stars/rejections as preserved', () => {
    expect(resegmentConfirmMessage(2)).toMatch(/stars and rejections are kept/i)
  })
})
