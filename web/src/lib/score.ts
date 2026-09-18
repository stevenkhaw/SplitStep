import type { Rally } from './types'

/**
 * Tennis scoring as a pure function of who won each point.
 *
 * Only the winner is stored per rally; the scoreboard is replayed from
 * that, so a corrected winner, an undo, a re-segment or a split recomputes
 * for free. This is the second copy of splitstep/score.py -- the queue
 * needs the score at keypress time, the reel job needs it in Python --
 * and both are pinned to tests/fixtures/score_cases.json. Change the rules
 * in both places together, and add a case.
 */
export type Player = 'a' | 'b'

export interface ScoreRules {
  players: [string, string]
  sets: 1 | 3 | 5
  ad: boolean
  tiebreak: 'at6' | 'none' | 'only'
  tiebreakTo: 7 | 10
  /** Who served the session's first point, or null when nobody said.
   *  Never defaulted: every session recorded before this key existed has
   *  no honest value, and 'a' would be wrong half the time. Absent renders
   *  no server at all, the way an unassigned preset renders no region. */
  firstServer: Player | null
}

export interface ScoreState {
  sets: [number, number][]
  games: [number, number]
  points: [string, string]
  inTiebreak: boolean
  finished: Player | null
  /** Who serves the point this state is entering, derived from
   *  rules.firstServer by the same replay and stored nowhere. Null when no
   *  first server was named, and null once the match is decided -- there is
   *  no next point, which is why scoreboardRows drops games and points
   *  there too. */
  server: Player | null
}

export const DEFAULT_RULES: ScoreRules = {
  players: ['Me', 'Opp'],
  sets: 3,
  ad: true,
  tiebreak: 'at6',
  tiebreakTo: 7,
  firstServer: null,
}

/**
 * Who serves the next point, from how many service units are done.
 *
 * Serve alternates every game, so the parity of completed games decides
 * it. A whole tiebreak counts as one unit, which is not a shortcut: the
 * player who would have served the next game serves the tiebreak's first
 * point, and treating the tiebreak as that game makes "whoever served
 * first in the tiebreak receives first in the next set" (ITF rule 5) fall
 * out of plain alternation instead of needing a special case.
 *
 * Inside a tiebreak the serve changes after the first point and every two
 * after that -- points 1 / 2,3 / 4,5 -- so the point about to be played,
 * 1-based, has had floor(p / 2) changes before it.
 */
function serverOf(
  rules: ScoreRules,
  units: number,
  inTb: boolean,
  pts: [number, number],
): Player | null {
  if (rules.firstServer === null) return null
  const flips = units + (inTb ? Math.floor((pts[0] + pts[1] + 1) / 2) : 0)
  return (['a', 'b'] as const)[(rules.firstServer === 'a' ? 0 : 1) ^ (flips % 2)]
}

const POINT_LABELS = ['0', '15', '30', '40']

function pointLabels(pts: [number, number], inTiebreak: boolean, ad: boolean): [string, string] {
  if (inTiebreak) return [String(pts[0]), String(pts[1])]
  const [a, b] = pts
  if (ad && a >= 3 && b >= 3) {
    if (a === b) return ['40', '40']
    return a > b ? ['Ad', '40'] : ['40', 'Ad']
  }
  return [POINT_LABELS[Math.min(a, 3)], POINT_LABELS[Math.min(b, 3)]]
}

export function score(winners: Player[], rules: ScoreRules): ScoreState {
  const setsNeeded = Math.floor(rules.sets / 2) + 1
  const sets: [number, number][] = []
  let games: [number, number] = [0, 0]
  let pts: [number, number] = [0, 0]
  let inTb = rules.tiebreak === 'only'
  let finished: Player | null = null
  let units = 0 // completed service units: games, plus a whole tiebreak as one
  const setsWonBy = (i: 0 | 1) => sets.filter((s) => s[i] > s[1 - i]).length

  for (const w of winners) {
    // Points after match point are ignored, not rejected: the queue shows
    // them as unscored, which is honest and needs no error state.
    if (finished) break
    const i: 0 | 1 = w === 'a' ? 0 : 1
    const j: 0 | 1 = i === 0 ? 1 : 0
    pts[i] += 1

    if (inTb) {
      if (pts[i] >= rules.tiebreakTo && pts[i] - pts[j] >= 2) {
        if (rules.tiebreak === 'only') {
          // One tiebreak is the whole session: the count is the final score.
          finished = w
        } else {
          games[i] += 1
          units += 1
          sets.push([games[0], games[1]])
          games = [0, 0]
          pts = [0, 0]
          inTb = false
          if (setsWonBy(i) >= setsNeeded) finished = w
        }
      }
      continue
    }

    // Four points and two clear with advantage scoring; without it the
    // seventh point of a game decides it at deuce.
    const wonGame = pts[i] >= 4 && (!rules.ad || pts[i] - pts[j] >= 2)
    if (!wonGame) continue
    games[i] += 1
    units += 1
    pts = [0, 0]
    if (rules.tiebreak === 'at6' && games[0] === 6 && games[1] === 6) {
      inTb = true
    } else if (games[i] >= 6 && games[i] - games[j] >= 2) {
      sets.push([games[0], games[1]])
      games = [0, 0]
      if (setsWonBy(i) >= setsNeeded) finished = w
    }
  }

  return {
    sets,
    games,
    points: pointLabels(pts, inTb, rules.ad),
    inTiebreak: inTb,
    finished,
    server: finished !== null ? null : serverOf(rules, units, inTb, pts),
  }
}

/**
 * The state entering `rallyId`, and how many earlier points carry no
 * winner. Replays every non-rejected point with a lower idx, in idx order
 * whatever order `rallies` arrived in -- the caller hands in the whole
 * session, not the source-scoped list the queue shows. An id no rally
 * holds replays everything.
 */
export function scoreBefore(
  rallies: Rally[],
  rallyId: string,
  rules: ScoreRules,
): { state: ScoreState; unscored: number } {
  const target = rallies.find((r) => r.id === rallyId)
  const earlier = rallies
    .filter((r) => (target ? r.idx < target.idx : true) && !r.rejected && r.point)
    .sort((x, y) => x.idx - y.idx)
  const winners = earlier.map((r) => r.winner).filter((w): w is Player => w !== '')
  const unscored = earlier.filter((r) => r.winner === '').length
  return { state: score(winners, rules), unscored }
}

export function playerName(rules: ScoreRules, p: Player): string {
  return rules.players[p === 'a' ? 0 : 1]
}

/** Two rows for a broadcast-style board: name, one column per completed
 *  set, current games, points. Tiebreak-only sessions have no games
 *  column; a finished match shows W in place of games and points. */
export function scoreboardRows(state: ScoreState, rules: ScoreRules): string[][] {
  return rules.players.map((name, i) => {
    const row = [name, ...state.sets.map((s) => String(s[i]))]
    if (state.finished !== null && rules.tiebreak !== 'only') {
      // Games and points are 0-0 after the deciding set; sets, then W.
      row.push(state.finished === (i === 0 ? 'a' : 'b') ? 'W' : '')
    } else {
      if (rules.tiebreak !== 'only') row.push(String(state.games[i]))
      row.push(state.points[i])
    }
    return row
  })
}

/**
 * One letter per player, for a control too small to hold a name.
 *
 * Initials, because "S" beside Sam's rally is read without translation;
 * positional A/B when they collide, because two players called Alex and
 * Ana rendering the same letter would be worse than no initials at all.
 * The fallback is all-or-nothing on purpose -- giving one player their
 * initial and the other a positional letter would read as one scheme with
 * a typo in it.
 */
export function playerGlyphs(rules: ScoreRules): [string, string] {
  const [a, b] = rules.players.map((n) => n.trim().charAt(0).toUpperCase())
  return a === b ? ['A', 'B'] : [a, b]
}

export interface WinnerChip {
  /** What the chip draws: the winner's letter, or an en dash for none. */
  glyph: string
  /** The same state in words, for the title attribute. */
  title: string
  /** Whether a winner is recorded -- the caller fills the chip on true. */
  set: boolean
}

/**
 * What QueueMode's fourth chip should say for a rally, or null when the
 * session tracks no score. Null rather than an empty chip: an always-there
 * winner box on an untracked session advertises a control that does
 * nothing.
 *
 * Takes the winner rather than the Rally because the queue controller's
 * live map is the one that reflects the row -- set_point(false) clears the
 * winner there and on the server, and the Rally the controller was built
 * from is deliberately never mutated.
 */
export function winnerChip(winner: Player | '', rules: ScoreRules | null): WinnerChip | null {
  if (!rules) return null
  const label = winnerLabel(winner, rules)
  if (label === null) return { glyph: '–', title: 'no winner recorded', set: false }
  return { glyph: playerGlyphs(rules)[winner === 'a' ? 0 : 1], title: label, set: true }
}

/** "won by Sam", or null when there is nothing to say -- no rules, or no
 *  winner recorded. Shared by the queue's chip and the overview band's
 *  label so the two cannot word the same fact differently. */
export function winnerLabel(winner: Player | '', rules: ScoreRules | null): string | null {
  if (!rules || winner === '') return null
  return `won by ${rules.players[winner === 'a' ? 0 : 1]}`
}
