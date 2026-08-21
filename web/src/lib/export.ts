import type { ExportResult } from './types'

/** The button/toast label for a set -- `which` itself ('points') reads
 * wrong mid-sentence ("export points clips"), so every message that names
 * the set goes through this instead of inlining the ternary twice. */
export function exportSetLabel(which: 'points' | 'starred'): string {
  return which === 'points' ? 'point clips' : 'starred clips'
}

/**
 * A short, human-readable notice for the reviewed panel's toaster.
 *
 * `queued` is always shown, even at zero, and the other three only appear
 * when nonzero -- so a second press mid-encode reads as "0 queued, 3 in
 * flight" rather than the false "0 queued, 3 already cut" that the plan's
 * four separate counts exist specifically to prevent (see ExportPlan in
 * bootleg/export.py). Collapsing already_cut/in_flight/unavailable back
 * into one bucket here would silently reintroduce the bug the endpoint's
 * four-count contract was built to fix.
 */
export function describeExportResult(which: 'points' | 'starred', result: ExportResult): string {
  const label = exportSetLabel(which)
  const parts = [`${result.queued} queued`]
  if (result.already_cut > 0) parts.push(`${result.already_cut} already cut`)
  if (result.in_flight > 0) parts.push(`${result.in_flight} in flight`)
  if (result.unavailable > 0) parts.push(`${result.unavailable} unavailable`)
  return `${label}: ${parts.join(', ')}`
}
