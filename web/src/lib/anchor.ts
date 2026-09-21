import type { AnchorState, ScoreRules, ScoreState } from './score'

/**
 * The anchor form's logic (AnchorForm.svelte is the shell).
 *
 * A draft is what the reviewer edits: sets and games as numbers, points as
 * the LABELS the board shows -- "40", "Ad", or a tiebreak count -- because
 * the form seeds from the replayed state and the reviewer corrects what
 * they see, not the count behind it. `anchorFromDraft` turns it into the
 * raw counts both engines replay from (AnchorState), and is the one place
 * a label is read back into a number.
 */
export interface AnchorDraft {
  sets: [number, number][]
  games: [number, number]
  points: [string, string]
}

/** What the points selects offer outside a tiebreak. `Ad` is only valid
 *  against `40`; anchorFromDraft says so rather than the select hiding it. */
export const POINT_LABELS = ['0', '15', '30', '40', 'Ad'] as const

export type DraftResult = { ok: true; state: AnchorState } | { ok: false; error: string }

/** Every field defaults to the replayed value, so fixing one number is
 *  one edit while a complete re-anchor stays possible. */
export function draftFromState(state: ScoreState): AnchorDraft {
  return {
    sets: state.sets.map((s) => [s[0], s[1]]),
    games: [state.games[0], state.games[1]],
    points: [state.points[0], state.points[1]],
  }
}

/** Whether the point entering these games is a tiebreak point -- the same
 *  rule score() derives inTiebreak from an anchor with, so the form asks
 *  for a count exactly when the engine will read one. */
export function isTiebreakAt(games: [number, number], rules: ScoreRules): boolean {
  if (rules.tiebreak === 'only') return true
  return rules.tiebreak === 'at6' && games[0] === 6 && games[1] === 6
}

/** The draft with `n` completed sets: new ones open at 0-0, extra ones
 *  drop from the end. Untouched when the count already matches. */
export function withSetCount(draft: AnchorDraft, n: number): AnchorDraft {
  if (draft.sets.length === n) return draft
  const sets = draft.sets.slice(0, n)
  while (sets.length < n) sets.push([0, 0])
  return { ...draft, sets }
}

function count(v: unknown): number | null {
  const n = typeof v === 'string' ? Number(v.trim()) : v
  return typeof n === 'number' && Number.isInteger(n) && n >= 0 && (typeof v !== 'string' || v.trim() !== '')
    ? n
    : null
}

function gamePoints(points: [string, string], rules: ScoreRules): [number, number] | string {
  const idx = points.map((p) => POINT_LABELS.indexOf(p as (typeof POINT_LABELS)[number]))
  if (idx.some((i) => i < 0)) return 'Points must be 0, 15, 30, 40 or Ad'
  const [a, b] = idx as [number, number]
  const adA = a === 4
  const adB = b === 4
  if (adA || adB) {
    if (!rules.ad) return 'No advantage in a no-ad game'
    if (adA && adB) return 'Only one player can hold the advantage'
    if ((adA && b !== 3) || (adB && a !== 3)) return 'Advantage is only against 40'
  }
  // 0/15/30/40 are the counts 0-3; Ad is one more than the 40 it faces.
  return [a, b]
}

/**
 * The draft as an AnchorState, or why it cannot be one.
 *
 * Shape only, plus the two things the engine would misread rather than
 * refuse: a label it cannot count, and more completed sets than the
 * format has left -- a best-of-three entered with three sets played is a
 * match that ended before this rally, which is no place to anchor.
 */
export function anchorFromDraft(draft: AnchorDraft, rules: ScoreRules): DraftResult {
  const sets: [number, number][] = []
  for (const s of draft.sets) {
    const a = count(s[0])
    const b = count(s[1])
    if (a === null || b === null) return { ok: false, error: 'Set scores must be whole numbers' }
    sets.push([a, b])
  }
  if (sets.length >= rules.sets) {
    return { ok: false, error: `A best of ${rules.sets} has at most ${rules.sets - 1} sets before a rally` }
  }
  const ga = count(draft.games[0])
  const gb = count(draft.games[1])
  if (ga === null || gb === null) return { ok: false, error: 'Games must be whole numbers' }
  const games: [number, number] = [ga, gb]

  let points: [number, number]
  if (isTiebreakAt(games, rules)) {
    const pa = count(draft.points[0])
    const pb = count(draft.points[1])
    if (pa === null || pb === null) return { ok: false, error: 'Tiebreak points must be whole numbers' }
    points = [pa, pb]
  } else {
    const p = gamePoints(draft.points, rules)
    if (typeof p === 'string') return { ok: false, error: p }
    points = p
  }
  return { ok: true, state: { sets, games, points } }
}
