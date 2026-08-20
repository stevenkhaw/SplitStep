import type { Rally } from './types'

/**
 * Rallies on `sourceId` whose live boundaries have diverged from what the
 * detector produced. Re-segmenting discards these -- the server carries
 * stars and rejections across by >50% overlap (see `replace_rallies` /
 * `STAR_OVERLAP_MIN` in bootleg/db/rallies.py), but a hand-dragged boundary
 * has no such carry-over, so the caller must name this count before paying
 * the cost (spec 6).
 */
export function editedBoundaryCount(rallies: Rally[], sourceId: string): number {
  return rallies.filter(
    (r) =>
      r.source_id === sourceId && (r.start_ms !== r.det_start_ms || r.end_ms !== r.det_end_ms),
  ).length
}

/** The confirmation copy naming that cost, singular/plural correct. */
export function resegmentConfirmMessage(editedCount: number): string {
  return (
    `Re-segmenting discards ${editedCount} hand-edited ` +
    `boundar${editedCount === 1 ? 'y' : 'ies'} on this source. ` +
    `Stars and rejections are kept. Continue?`
  )
}
