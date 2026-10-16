import type { Mount, PlayFormat, ScorerChoice, Source } from './types'

/**
 * Where the camera is and what is being played: the two answers that pick
 * a detector. Pure logic so the setup wizard (a thin shell) and the tests
 * share one table; the table itself mirrors `resolve_scorer` on the server
 * and is pinned to it by tests/fixtures/scorer_cases.json, which both test
 * suites read -- the `score_cases.json` pattern.
 */
export const MOUNTS: { value: Mount; label: string }[] = [
  { value: 'fence', label: 'Fence' },
  { value: 'ground', label: 'Ground' },
  { value: '', label: 'Not set' },
]

export const FORMATS: { value: PlayFormat; label: string }[] = [
  { value: 'singles', label: 'Singles' },
  { value: 'doubles', label: 'Doubles' },
  { value: 'drills', label: 'Drills' },
  { value: '', label: 'Not set' },
]

/** Only a fence mount + singles has a tuned detector. Ground is the old
 *  subject scorer whatever the format; anything unanswered stays `auto`,
 *  which is what every source predating the question ran. */
export function scorerFor(mount: Mount, format: PlayFormat): ScorerChoice {
  if (mount === 'fence') return format === 'singles' ? 'fence' : 'auto'
  if (mount === 'ground') return 'subject'
  return 'auto'
}

/** The fence model was fitted on singles alone. Choosing fence for other
 *  play is allowed, but it must say out loud that it is not the fence
 *  detector running -- a quiet fallback would read as fence quality. */
export function untunedNote(mount: Mount, format: PlayFormat): string | null {
  if (mount !== 'fence' || format === 'singles') return null
  const what = format === '' ? 'not set' : format
  return `Not tuned for fence + ${what} yet — using the general detector.`
}

/** Fence geometry is drawn as four court corners rather than a play region. */
export function needsCorners(mount: Mount, _format: PlayFormat): boolean {
  return mount === 'fence'
}

export type DetectionOwed = 'none' | 'resegment' | 'redetect'

/**
 * What a source must re-run for its rallies to match its current answers.
 *
 * `auto` is satisfied by `pair`, `subject` or unknown: auto is what produced
 * those, so there is nothing to redo. Unknown (`null`/absent) is never a
 * change -- the `sameRegion` rule: an unproven claim across an old library is
 * how a warning gets trained away.
 */
export function detectionOwed(
  source: Pick<Source, 'scorer_now' | 'detected_scorer' | 'has_boxes'>,
): DetectionOwed {
  const wanted = source.scorer_now
  const detected = source.detected_scorer ?? null
  if (wanted === undefined || wanted === 'auto') return 'none'
  if (wanted === detected) return 'none'
  if (wanted === 'fence') return source.has_boxes ? 'resegment' : 'redetect'
  // wanted subject. Pair features carry the same per-frame boxes subject
  // scores, so cached features suffice. Fence output is a different feature
  // shape: re-detect.
  if (detected === null) return 'none'
  return detected === 'pair' ? 'resegment' : 'redetect'
}
