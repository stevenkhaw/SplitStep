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
 * `DetectionPanel`'s persistent warning (the decision-time paragraph the
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
 * True when two play regions are the same region.
 *
 * **By value, never by id, and that is the entire point of this function.**
 * The setup wizard writes a *fresh* `court_presets` row on every save, so
 * `court_preset_id !== features_preset_id` is true after re-confirming the
 * region you already had. The user hit exactly that: the same four corners
 * assigned three times on one source, each save announcing "the play region
 * changed" and each one offering a fifteen-minute re-detect that produced
 * byte-identical features. An id answers "is this the same row?"; the
 * question detection actually asks is "were these features built under
 * these corners?", and only the corners can answer it.
 *
 * Exact `===` on the floats, deliberately, with no epsilon:
 *
 *   - Both sides reach the client through one path -- `presets.quad` JSON ->
 *     `Quad.from_json` -> Python float -> JSON -> JS double (`_preset_points`
 *     in splitstep/api/routes.py serves both). Python's `json` and
 *     JavaScript's `JSON.parse` both round-trip a double exactly, so two
 *     rows storing the same corners deserialize bit-identically. There is no
 *     drift for a tolerance to absorb.
 *   - The corners that *are* different are enormously different. A quad
 *     corner is a normalized pointer position (`pointFromClient`, lib/quad.ts),
 *     so the smallest change a reviewer can make is one pixel -- about 5e-4
 *     of a 1920-wide frame, twelve orders of magnitude above double noise.
 *     An epsilon would therefore only ever discriminate values that cannot
 *     occur, while adding a band in which a genuine drag is silently
 *     ignored.
 *
 * So the tolerance would buy nothing and could only lose a real edit. If a
 * future writer ever *computes* a quad (a snap-to-court, an imported
 * calibration) rather than reading one back, that reasoning expires and this
 * is the comment to revisit.
 *
 * Unknown on either side is not equality and not inequality -- callers must
 * test for it before asking (see `planDetection`).
 */
export function sameRegion(a: [number, number][], b: [number, number][]): boolean {
  if (a.length !== b.length) return false
  return a.every((p, i) => p[0] === b[i][0] && p[1] === b[i][1])
}

/** What the reviewer currently has on screen: a region and a threshold. */
export interface DetectionDraft {
  /** The region assigned to the source now (normally `court_preset_points`),
   *  or null when none is assigned or the server did not say. */
  region: [number, number][] | null | undefined
  /** The threshold the slider sits at, or null before one has been resolved. */
  threshold: number | null
}

/**
 * The one action a detection panel should offer.
 *
 * `redetect` is the expensive run (YOLO + audio, ~15 min) and the only thing
 * that can act on a play region, because the quad filters boxes when
 * `features.jsonl` is BUILT. `resegment` replays those cached features
 * through the pure `segment()` (~200 ms). `none` disables the button.
 */
export type DetectionActionKind = 'redetect' | 'resegment' | 'none'

export interface DetectionPlan {
  action: DetectionActionKind
  /** The assigned region differs, by value, from the one the features were
   *  built under. False whenever either side is unknown. */
  regionChanged: boolean
  /** The draft threshold differs from the one the current rallies were cut
   *  at (or nothing recorded one, so it cannot be shown to match). */
  thresholdChanged: boolean
  /** Whether a score curve exists to render at all -- see `hasFeatures`. */
  curveAvailable: boolean
  /** `cutAtPhrase(source)`, carried here so the sentence the panel prints
   *  and the action its button takes are resolved from one call and cannot
   *  describe different sources. */
  cutAt: string
}

/**
 * Which single action a source needs to make the reviewer's draft real.
 *
 * One function, because the UI's failure was having two surfaces -- a quad
 * editor and a re-segment panel -- and no way for the reviewer to tell which
 * one their change needed. The rules, in the order they are decided:
 *
 * 1. **No cached features, no re-segment.** `segment()` replays
 *    `features.jsonl`; before the first detect the file does not exist and
 *    `/scores` answers 409. Anything the reviewer wants on such a source
 *    costs the full run -- which is fine, because `POST /detect` now takes a
 *    threshold (da51585) and carries the cheap change along with it.
 * 2. **A changed region beats a changed threshold**, for the same reason:
 *    the detect run re-segments at the end anyway, so one job serves both
 *    and there is never a second button to press afterwards.
 * 3. **Unknown never counts as changed, for the region.** Either quad being
 *    null (or absent) means the comparison cannot be made, and claiming a
 *    change would offer fifteen minutes of GPU on no evidence -- across an
 *    entire pre-016 library, which is how a warning gets trained away.
 * 4. **Unknown DOES count as changed, for the threshold, and the asymmetry
 *    is deliberate.** A re-segment costs 200 ms and is behind a confirm,
 *    while suppressing it would leave every source segmented before
 *    migration 015 permanently unable to re-cut from this panel: its
 *    recorded threshold is null, so no slider position could ever be proven
 *    to differ from it. The cheap action fails open, the expensive one fails
 *    closed.
 */
export function planDetection(
  source: Source | undefined,
  draft: DetectionDraft,
): DetectionPlan {
  const cutAt = cutAtPhrase(source)
  if (!source) {
    return { action: 'none', regionChanged: false, thresholdChanged: false,
             curveAvailable: false, cutAt }
  }

  const builtUnder = source.features_preset_points
  const regionChanged =
    !!draft.region && !!builtUnder && !sameRegion(draft.region, builtUnder)

  // `?? null` rather than a truthiness test: a recorded 0 is a recorded
  // threshold, and it is the one value where truthy and recorded disagree.
  const recorded = source.segment_threshold ?? null
  const thresholdChanged =
    draft.threshold !== null && (recorded === null || draft.threshold !== recorded)

  const curveAvailable = hasFeatures(source)

  let action: DetectionActionKind = 'none'
  if (regionChanged) action = 'redetect'
  else if (thresholdChanged) action = curveAvailable ? 'resegment' : 'redetect'
  return { action, regionChanged, thresholdChanged, curveAvailable, cutAt }
}

/**
 * Whether this source has cached features -- i.e. whether a score curve can
 * be drawn and a re-segment can run at all.
 *
 * `features_at` is the mtime of `features.jsonl` (`_features_at` in
 * splitstep/api/routes.py), so null means the file is not there. The panel
 * has to say this out loud rather than render an empty chart: on a first
 * run the threshold picker is genuinely blind, and a flat line at zero
 * reads as "the detector found no play" instead of "nothing has looked
 * yet".
 */
export function hasFeatures(source: Source | undefined): boolean {
  return !!source?.features_at
}

/**
 * The threshold the rallies on screen were cut at, as a phrase.
 *
 * "unknown" and never a number when nothing recorded one -- an unsegmented
 * source, or one cut before migration 015. Naming the profile default here
 * would re-state the original bug in prose: 0.25 is a fact about the
 * detector's `subject` profile, not about the list of rallies beside this
 * sentence, and source 2026-09-16/01 was cut at 0.15.
 *
 * Two decimals, matching the slider readout, so one number is not spelled
 * two ways on one row.
 */
export function cutAtPhrase(source: Source | undefined): string {
  const threshold = seedThreshold(source)
  return threshold === null ? 'cut at an unknown threshold' : `cut at ${threshold.toFixed(2)}`
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

/**
 * The primary button's label, stating what the click costs.
 *
 * The cost belongs *in the label* and not in a caption beside it, because
 * the reported failure was a click made without knowing: the reviewer
 * re-segmented to 0.15, pressed a button called "Run detection", and
 * fifteen minutes later had rallies cut at 0.25. "Run detection" and
 * "Re-segment" are two words apart and three orders of magnitude apart,
 * and only one of those facts was on screen.
 *
 * `none` gets words too rather than an empty disabled button -- a button
 * reading "Re-segment" that cannot be pressed looks broken, while one
 * reading "Nothing to apply" has said why.
 */
export function detectionActionLabel(action: DetectionActionKind): string {
  switch (action) {
    case 'redetect':
      return 'Run detection · ~15 min'
    case 'resegment':
      return 'Re-segment · instant'
    case 'none':
      return 'Nothing to apply'
  }
}

/**
 * One sentence saying why the button does what it does.
 *
 * Derived from the same `DetectionPlan` the label is, so the two cannot
 * disagree -- the panel resolves one plan and renders both from it. The
 * branches are in `planDetection`'s own precedence order, which is what
 * keeps the sentence describing the action actually planned: a region
 * change outranks a threshold change (the detect re-segments at the end
 * anyway), so when both moved this says "region" and the button says
 * "detection".
 */
export function detectionActionNote(plan: DetectionPlan): string {
  if (plan.action === 'none') {
    return 'The play region and the threshold both match what produced the rallies below.'
  }
  if (plan.regionChanged) {
    return (
      'The play region is applied when features are built, not when they are ' +
      'scored — only a full detection picks it up.'
    )
  }
  if (!plan.curveAvailable) {
    return 'This video has no cached features to re-cut, so the full run builds them first.'
  }
  return 'Re-cuts the rallies from this video’s cached features — no GPU, no re-encode.'
}

/**
 * What a panel can honestly say about one play region.
 *
 * Four states and not a nullable quad, because the three non-quad answers
 * mean genuinely different things to a reviewer deciding whether to spend
 * fifteen minutes. "Whole frame" is a real configuration detection runs in;
 * "not recorded" is the app admitting it cannot tell; "nothing detected
 * yet" is a run that never happened. Collapsing them would put the app
 * back to making claims it cannot support, which is the class of bug this
 * whole change is about.
 */
export type RegionState =
  | { kind: 'quad'; points: [number, number][] }
  | { kind: 'whole-frame' }
  | { kind: 'unknown' }
  | { kind: 'unrun' }

/**
 * The region the cached features were built under -- the one detection is
 * actually using, which had no answer anywhere in the UI before this.
 *
 * `unrun` before any features exist: there is no region "in effect"
 * because no run is in effect, and saying "whole frame" would describe a
 * detection that never happened.
 *
 * `unknown` and never `whole-frame` when features exist with no recorded
 * region. Three states produce that (a server predating migration 016, a
 * detect that genuinely ran with no preset, a preset row since deleted) and
 * only some of them are the whole frame -- see the `Source` type's comment
 * on `features_preset_points`.
 */
export function regionInEffect(source: Source | undefined): RegionState {
  if (!source) return { kind: 'unknown' }
  if (!hasFeatures(source)) return { kind: 'unrun' }
  const points = source.features_preset_points
  return points ? { kind: 'quad', points } : { kind: 'unknown' }
}

/**
 * The region assigned to the source right now -- what the next detection
 * would use, rendered beside the one above so the difference is visible
 * rather than inferred from a timestamp.
 *
 * Here `whole-frame` IS assertable, and the asymmetry with `regionInEffect`
 * is the point: no assigned preset means detection runs whole-frame, which
 * is a fact about the source row, not a gap in what the server served. The
 * gap case is separate -- an id with no corners, which a pre-016 server
 * does serve -- and stays `unknown`.
 */
export function regionAssignedNow(source: Source | undefined): RegionState {
  if (!source) return { kind: 'unknown' }
  const points = source.court_preset_points
  if (points) return { kind: 'quad', points }
  return source.court_preset_id ? { kind: 'unknown' } : { kind: 'whole-frame' }
}

/** Words for a `RegionState`, so no state renders as an empty cell. */
export function regionStateLabel(state: RegionState): string {
  switch (state.kind) {
    case 'quad':
      return 'four corners'
    case 'whole-frame':
      return 'whole frame'
    case 'unknown':
      return 'not recorded'
    case 'unrun':
      return 'nothing detected yet'
  }
}

/**
 * What the threshold section says in place of a score curve, before the
 * first detect.
 *
 * Not an empty chart: a flat line at zero reads as "the detector found no
 * play here", which is a claim, and a false one -- nothing has looked. And
 * not a slider either, which is the less obvious half. The two camera
 * profiles put the threshold on different scales (0.45 pair, 0.25 subject)
 * and `analyze_view` picks between them by measuring the features that do
 * not exist yet, so there is no position this slider could open at that
 * would mean anything. Naming a number before the profile is known is the
 * original bug wearing a different hat.
 */
export const NO_CURVE_NOTE =
  'No score curve yet — this video has not been detected. The threshold scale ' +
  'depends on the camera profile detection works out from the features, so the ' +
  'first run picks it.'
