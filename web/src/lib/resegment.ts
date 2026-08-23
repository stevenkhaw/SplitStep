import type { Rally } from './types'

/**
 * Rallies on `sourceId` whose live boundaries have diverged from what the
 * detector produced. Re-segmenting discards these -- the server carries
 * stars and rejections across by >50% overlap (see `replace_rallies` /
 * `STAR_OVERLAP_MIN` in splitstep/db/rallies.py), but a hand-dragged boundary
 * has no such carry-over, so the caller must name this count before paying
 * the cost (spec 6).
 *
 * Hand-made rallies are excluded and counted by `splitCount` instead. Their
 * det span is null, so `start_ms !== det_start_ms` is trivially true and they
 * would otherwise be counted here and then described with the wrong noun.
 */
export function editedBoundaryCount(rallies: Rally[], sourceId: string): number {
  return rallies.filter(
    (r) =>
      r.source_id === sourceId &&
      r.det_start_ms !== null &&
      (r.start_ms !== r.det_start_ms || r.end_ms !== r.det_end_ms),
  ).length
}

/**
 * Rallies on `sourceId` a human made by splitting one in two. Lost to a
 * re-segment for the same reason a boundary edit is, and for a reason the
 * word "boundary" does not cover: replace_rallies rebuilds from detector
 * intervals, and a hand-made rally has no interval to be rebuilt from.
 */
export function splitCount(rallies: Rally[], sourceId: string): number {
  return rallies.filter((r) => r.source_id === sourceId && r.det_start_ms === null).length
}

/** The confirmation copy naming that cost, singular/plural correct. */
export function resegmentConfirmMessage(editedCount: number, splits: number): string {
  const losses: string[] = []
  if (editedCount > 0) {
    losses.push(`${editedCount} hand-edited boundar${editedCount === 1 ? 'y' : 'ies'}`)
  }
  if (splits > 0) losses.push(`${splits} split${splits === 1 ? '' : 's'}`)
  // Both counts zero is still a real prompt: the reviewer is replacing every
  // rally on the source and should be told so, even when nothing hand-made
  // is at stake.
  const what = losses.length ? losses.join(' and ') : 'nothing hand-edited'
  return `Re-segmenting discards ${what} on this source. Stars and rejections are kept. Continue?`
}
