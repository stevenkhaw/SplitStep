import { describe, expect, it } from 'vitest'
import { describeExportCounts, describeExportResult, exportSetLabel } from '../src/lib/export'
import type { ExportResult } from '../src/lib/types'

const result = (o: Partial<ExportResult> = {}): ExportResult => ({
  queued: 0, already_cut: 0, in_flight: 0, unavailable: 0, total: 0, ...o,
})

describe('describeExportCounts', () => {
  it('always names queued, even at zero', () => {
    expect(describeExportCounts(result())).toBe('0 queued')
  })

  it('keeps in flight distinct from already cut', () => {
    // The bug this contract exists to prevent: a second press mid-encode
    // reporting everything as done.
    expect(describeExportCounts(result({ in_flight: 3 }))).toBe('0 queued, 3 in flight')
    expect(describeExportCounts(result({ already_cut: 3 }))).toBe('0 queued, 3 already cut')
  })

  it('reports all four when all four are nonzero', () => {
    expect(describeExportCounts(result({
      queued: 1, already_cut: 2, in_flight: 3, unavailable: 4,
    }))).toBe('1 queued, 2 already cut, 3 in flight, 4 unavailable')
  })
})

describe('describeExportResult', () => {
  it('prefixes the set label', () => {
    expect(describeExportResult('points', result({ queued: 2 })))
      .toBe('point clips: 2 queued')
    expect(exportSetLabel('starred')).toBe('starred clips')
  })
})
