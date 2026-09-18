import type { Rally, Source } from './types'

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

/**
 * The noun phrase naming what a re-segment on this source will cost,
 * singular/plural correct in each count independently -- "1 hand-edited
 * boundary", "2 splits", "1 hand-edited boundary and 3 splits", or (both
 * counts zero) "nothing hand-edited".
 *
 * Shared by `resegmentConfirmMessage` (the click-time confirm dialog) and
 * `ResegmentPanel`'s persistent warning (the decision-time paragraph the
 * reviewer reads before clicking at all) so the two cannot say different
 * things about the same cost -- that disagreement would be a worse bug than
 * either one going silent.
 */
export function resegmentLossPhrase(editedCount: number, splits: number): string {
  const losses: string[] = []
  if (editedCount > 0) {
    losses.push(`${editedCount} hand-edited boundar${editedCount === 1 ? 'y' : 'ies'}`)
  }
  if (splits > 0) losses.push(`${splits} split${splits === 1 ? '' : 's'}`)
  // Both counts zero is still a real prompt: the reviewer is replacing every
  // rally on the source and should be told so, even when nothing hand-made
  // is at stake.
  return losses.length ? losses.join(' and ') : 'nothing hand-edited'
}

/** The confirmation copy naming that cost, singular/plural correct. */
export function resegmentConfirmMessage(editedCount: number, splits: number): string {
  const what = resegmentLossPhrase(editedCount, splits)
  return `Re-segmenting discards ${what} on this source. Stars and rejections are kept. Continue?`
}

/**
 * True when this source's play region was assigned after its cached
 * features were written -- the one state in which the re-segment slider
 * lies. `segment()` replays `features.jsonl`, and the quad is applied when
 * those features are BUILT (it filters boxes before near/far are elected,
 * see CLAUDE.md "Play region"), so a region newer than the file is invisible
 * to every threshold in the panel. Only a full re-detect picks it up.
 *
 * Both timestamps are ISO-8601 UTC from the server (`_now()` and
 * features.jsonl's mtime, normalised to the same isoformat), so a plain
 * string comparison orders them.
 *
 * Unknown on either side means no warning. A library predating migration
 * 014 has a null `preset_assigned_at` on every row, including rows whose
 * region really is stale; nagging about all of them would train the
 * reviewer to ignore the one case this exists for.
 */
export function regionNewerThanFeatures(source: Source): boolean {
  const { features_at, preset_assigned_at } = source
  if (!features_at || !preset_assigned_at) return false
  return preset_assigned_at > features_at
}

/**
 * The confirmation a re-detect asks for, wherever it is offered -- the quad
 * editor's card after an assignment, and the re-segment panel's stale-region
 * warning. One string, because the two buttons queue the same job and cost
 * the same thing; two copies would drift, and a reviewer who learned the
 * cost in one place would be told something different in the other.
 */
export function redetectConfirmMessage(): string {
  return (
    'Re-detect this video with the new play region? Starred and rejected ' +
    'carry over; manual boundary edits and split rallies are lost. ' +
    'Detection takes a while — the jobs badge tracks it.'
  )
}
