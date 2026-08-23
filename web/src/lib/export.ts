import type { ExportResult } from './types'

/** The button/toast label for a set -- `which` itself ('points') reads
 * wrong mid-sentence ("export points clips"), so every message that names
 * the set goes through this instead of inlining the ternary twice. */
export function exportSetLabel(which: 'points' | 'starred'): string {
  return which === 'points' ? 'point clips' : 'starred clips'
}

/**
 * The four-count phrase both cut buttons report.
 *
 * `queued` is always shown, even at zero, and the other three only appear
 * when nonzero -- so a second press mid-encode reads as "0 queued, 3 in
 * flight" rather than the false "0 queued, 3 already cut" that the four
 * separate counts exist to prevent (see ExportPlan in splitstep/export.py).
 * One implementation, called from the reviewed panel and from the reel
 * builder: two copies of this would be two chances to collapse the buckets
 * back into one, which is the mistake that once made a second press
 * mid-encode report everything as done.
 */
export function describeExportCounts(result: ExportResult): string {
  const parts = [`${result.queued} queued`]
  if (result.already_cut > 0) parts.push(`${result.already_cut} already cut`)
  if (result.in_flight > 0) parts.push(`${result.in_flight} in flight`)
  if (result.unavailable > 0) parts.push(`${result.unavailable} unavailable`)
  return parts.join(', ')
}

/** A short, human-readable notice for the reviewed panel's toaster. */
export function describeExportResult(which: 'points' | 'starred', result: ExportResult): string {
  return `${exportSetLabel(which)}: ${describeExportCounts(result)}`
}
