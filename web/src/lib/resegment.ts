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

/**
 * The sentence naming the one state in which the re-segment slider lies.
 *
 * Constant, and here rather than inline in the component, for the reason
 * CLAUDE.md gives it weight at all: this is one of exactly two places the
 * app says that a play-region change needs a full re-detect and not a
 * re-segment (the other is the quad editor's card after an assignment).
 * A string a test can pin is a string that cannot quietly lose the half
 * that carries the instruction.
 */
export const STALE_REGION_WARNING =
  'Play region changed after the last detect — re-segment still uses the old one.'

/**
 * Every source in `sources` whose play region is newer than its cached
 * features, in the order given.
 *
 * All of them, deliberately, and not just whichever source the panel has
 * selected. The warning this feeds is rendered OUTSIDE the panel's
 * `<details>`, because inside it -- collapsed by default -- it was invisible
 * to the reviewer it exists for (the reported bug: a region assigned
 * fourteen hours after the features were built, a re-segment that changed
 * nothing, and no explanation anywhere on screen). But the source selector
 * is itself inside that collapse, so scoping this to the selection would
 * restore the same silence for every source nobody has picked yet: a stale
 * source 2 would say nothing until someone opened the tuning panel and
 * chose it, which is exactly the act this warning exists to pre-empt.
 *
 * Cheap on purpose -- it reads two timestamps off the prop and nothing
 * else. Rendering the warning must not cost the /scores fetch the collapse
 * was put there to avoid.
 */
export function staleRegionSources(sources: Source[]): Source[] {
  return sources.filter(regionNewerThanFeatures)
}

/**
 * The value the threshold slider opens on for `source`: the threshold that
 * source's current rallies were actually cut at, or null when nothing
 * recorded one.
 *
 * Null is not a fallback value, it is the absence of one -- the panel answers
 * it by asking `/scores` to resolve the per-source profile default, exactly
 * as it did before this column existed. What it must never do is *render* a
 * null as a number, which is the whole bug: the slider seeded itself from
 * that profile default (0.25 subject, 0.45 pair) no matter what produced the
 * rallies underneath it, so source 2026-09-16/01 -- re-segmented at 0.15,
 * holding rallies with confidence down to 0.176 -- reopened reading 0.25.
 * A reset would have been cosmetic; a number stating the wrong threshold
 * over the rallies it describes is a false claim about the data.
 *
 * `?? null` and not `|| null`: a recorded 0 is a recorded threshold, and it
 * is the one value where truthiness and recordedness disagree.
 */
export function seedThreshold(source: Source | undefined): number | null {
  return source?.segment_threshold ?? null
}

/**
 * What the panel says, in words, about the threshold behind the rallies the
 * reviewer is looking at -- split into the sentence and the number so the
 * component can put the number in `font-data` (every threshold, timecode and
 * confidence in the app is mono, so none of them jitters beside a moving
 * readout).
 *
 * `value` is null for an unrecorded threshold, and `lead` then says so rather
 * than naming the default the slider fell back to. Stating "cut at 0.25" over
 * rallies nobody knows the threshold for would re-introduce the bug in prose
 * having just removed it from the slider.
 *
 * Two decimals, matching the slider's own readout: one number rendered twice
 * on the same row must not be spelled two ways.
 *
 * Reads through `seedThreshold` rather than the field, so this sentence and
 * the slider's starting position can never disagree about whether a
 * threshold is known -- including for a payload that carries no
 * `segment_threshold` key at all (an older server, a cached response), which
 * is `undefined` rather than null and would otherwise reach `.toFixed` and
 * take the whole session route down with it.
 */
export function recordedThresholdNote(
  source: Source | undefined,
): { lead: string; value: string | null } | null {
  if (!source) return null
  const threshold = seedThreshold(source)
  if (threshold === null) {
    return {
      lead:
        'These rallies were cut before SplitStep recorded the threshold — ' +
        'the slider shows this source’s profile default.',
      value: null,
    }
  }
  return { lead: 'These rallies were cut at', value: threshold.toFixed(2) }
}
