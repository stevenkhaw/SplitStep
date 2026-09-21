import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { DEFAULT_RULES, score, scoreBefore, scoreboardRows } from '../src/lib/score'
import type { AnchorState, Player, ScoreRules } from '../src/lib/score'
import type { Rally, ScoreAnchor } from '../src/lib/types'

// The same file tests/test_score.py reads -- see the module comment in
// score.ts for why one case file feeds two engines. Read with join(), not
// `new URL(..., import.meta.url)`; tokens.test.ts explains the vite quirk.
const CASES = JSON.parse(
  readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), '../../tests/fixtures/score_cases.json'),
    'utf8',
  ),
) as {
  defaultRules: ScoreRules
  cases: {
    name: string
    rules?: Partial<ScoreRules>
    /** The state the replay starts from instead of love-all -- a reviewer's
     *  anchor (migration 017). Raw point counts, never labels. */
    anchor?: AnchorState
    winners: string
    expect: unknown
  }[]
}

describe('score', () => {
  for (const c of CASES.cases) {
    it(c.name, () => {
      const rules = { ...CASES.defaultRules, ...(c.rules ?? {}) }
      expect(score(c.winners.split('') as Player[], rules, c.anchor ?? null)).toEqual(c.expect)
    })
  }
})

function rally(idx: number, over: Partial<Rally> = {}): Rally {
  return {
    id: `r${idx}`, session_id: 's', source_id: 'src', idx,
    start_ms: idx * 10000, end_ms: idx * 10000 + 8000,
    det_start_ms: null, det_end_ms: null, confidence: 0.5,
    starred: 0, rejected: 0, point: 1, reviewed_at: null, seen_at: null, note: '',
    winner: '', ...over,
  }
}

function anchor(atMs: number, over: Partial<AnchorState> = {}, sourceId = 'src'): ScoreAnchor {
  return { source_id: sourceId, at_ms: atMs, sets: [], games: [0, 0], points: [0, 0], ...over }
}

describe('scoreBefore with anchors', () => {
  const five = () => [1, 2, 3, 4, 5].map((i) => rally(i, { winner: 'a' }))

  it('resets the replay at the anchor and later rallies build on it', () => {
    const rallies = five()
    // Without the anchor r3 is entered at 40-0.
    const a = anchor(rallies[2].start_ms, { sets: [[6, 4]], games: [2, 3], points: [0, 1] })
    const at3 = scoreBefore(rallies, 'r3', DEFAULT_RULES, [a]).state
    expect(at3.sets).toEqual([[6, 4]])
    expect(at3.games).toEqual([2, 3])
    expect(at3.points).toEqual(['0', '15'])
    // r3 and r4 to a on top of it: 30-15 entering r5.
    expect(scoreBefore(rallies, 'r5', DEFAULT_RULES, [a]).state.points).toEqual(['30', '15'])
  })

  it('leaves rallies before the anchor untouched', () => {
    const rallies = five()
    const a = anchor(rallies[3].start_ms, { games: [5, 5] })
    const s = scoreBefore(rallies, 'r3', DEFAULT_RULES, [a]).state
    expect(s.games).toEqual([0, 0])
    expect(s.points).toEqual(['30', '0'])
  })

  it('takes the newest anchor at or before the rally', () => {
    const rallies = five()
    const early = anchor(rallies[0].start_ms, { games: [1, 0] })
    const late = anchor(rallies[2].start_ms, { games: [4, 4] })
    const s = scoreBefore(rallies, 'r4', DEFAULT_RULES, [late, early]).state
    expect(s.games).toEqual([4, 4])
    expect(s.points).toEqual(['15', '0'])
    // Two anchors inside one rally's span: the later time wins.
    const twin = anchor(rallies[2].start_ms + 500, { games: [2, 2] })
    expect(scoreBefore(rallies, 'r4', DEFAULT_RULES, [late, twin, early]).state.games).toEqual([2, 2])
  })

  it('applies an anchor inside a rally to that rally', () => {
    const rallies = five()
    const mid = (rallies[1].start_ms + rallies[1].end_ms) / 2
    expect(scoreBefore(rallies, 'r2', DEFAULT_RULES, [anchor(mid, { games: [3, 0] })]).state.games)
      .toEqual([3, 0])
  })

  it('applies an anchor in a gap to the next rally', () => {
    const rallies = five()
    const gap = anchor(rallies[0].end_ms + 1000, { games: [3, 0] })
    expect(scoreBefore(rallies, 'r2', DEFAULT_RULES, [gap]).state.games).toEqual([3, 0])
    expect(scoreBefore(rallies, 'r1', DEFAULT_RULES, [gap]).state.games).toEqual([0, 0])
  })

  it('keeps anchors on different sources apart', () => {
    const rallies = [
      rally(1, { winner: 'a', source_id: 'one', start_ms: 1000, end_ms: 5000 }),
      rally(2, { winner: 'a', source_id: 'one', start_ms: 9000, end_ms: 14000 }),
      rally(3, { winner: 'a', source_id: 'two', start_ms: 1000, end_ms: 5000 }),
      rally(4, { winner: 'a', source_id: 'two', start_ms: 9000, end_ms: 14000 }),
    ]
    const a = anchor(1000, { games: [5, 5] }, 'two')
    expect(scoreBefore(rallies, 'r2', DEFAULT_RULES, [a]).state.games).toEqual([0, 0])
    expect(scoreBefore(rallies, 'r4', DEFAULT_RULES, [a]).state.games).toEqual([5, 5])
  })

  it("carries an anchor after a source's last rally into the next source", () => {
    const rallies = [
      rally(1, { winner: 'a', source_id: 'one', start_ms: 1000, end_ms: 5000 }),
      rally(2, { winner: 'a', source_id: 'two', start_ms: 1000, end_ms: 5000 }),
    ]
    const s = scoreBefore(rallies, 'r2', DEFAULT_RULES, [anchor(50000, { games: [2, 2] }, 'one')]).state
    expect(s.games).toEqual([2, 2])
    expect(s.points).toEqual(['0', '0'])
  })

  it('ignores an anchor on a source with no rallies', () => {
    const s = scoreBefore([rally(1, { winner: 'a' })], '', DEFAULT_RULES, [anchor(0, { games: [3, 3] }, 'x')]).state
    expect(s.games).toEqual([0, 0])
    expect(s.points).toEqual(['15', '0'])
  })

  it('survives a re-segment that renames every rally and nudges the boundaries', () => {
    // What replace_rallies does: every id is new, every boundary moves a
    // little. The anchor is a time, so it lands on the same footage.
    const before = five()
    const a = anchor(before[2].start_ms, { games: [4, 4] })
    const after = before.map((r, i) =>
      rally(i + 1, { id: `new${i}`, winner: 'a', start_ms: r.start_ms - 300, end_ms: r.end_ms + 200 }))
    expect(scoreBefore(after, 'new3', DEFAULT_RULES, [a]).state.games).toEqual([4, 4])
    expect(scoreBefore(after, 'new3', DEFAULT_RULES, [a]).state.points).toEqual(['15', '0'])
  })

  it('counts unscored points only after the anchor', () => {
    const rallies = [rally(1), rally(2, { winner: 'a' }), rally(3)]
    expect(scoreBefore(rallies, '', DEFAULT_RULES, [anchor(rallies[1].start_ms)]).unscored).toBe(1)
  })
})

describe('scoreBefore', () => {
  it('replays only earlier scored points, in idx order', () => {
    const rallies = [
      rally(3, { winner: 'b' }),
      rally(1, { winner: 'a' }),
      rally(2, { winner: 'a' }),
      rally(4, { winner: 'a' }),
      rally(5, { winner: 'b' }),
    ]
    const { state, unscored } = scoreBefore(rallies, 'r4', DEFAULT_RULES)
    // Earlier points ahead of r4 (idx 1,2,3), in idx order: a, a, b -> 30-15.
    expect(state.points).toEqual(['30', '15'])
    expect(unscored).toBe(0)
  })

  it('skips unscored and rejected points but counts the unscored', () => {
    const rallies = [
      rally(1, { winner: 'a' }),
      rally(2),
      rally(3, { winner: 'b', rejected: 1 }),
      rally(4, { point: 0 }),
      rally(5),
    ]
    const { state, unscored } = scoreBefore(rallies, 'r5', DEFAULT_RULES)
    expect(state.points).toEqual(['15', '0'])
    expect(unscored).toBe(1)
  })

  it('replays everything for an id no rally holds', () => {
    const { state } = scoreBefore([rally(1, { winner: 'a' }), rally(2, { winner: 'a' })], 'nope', DEFAULT_RULES)
    expect(state.points).toEqual(['30', '0'])
  })
})

describe('scoreboardRows', () => {
  it('is name, sets, games, points', () => {
    const w = ('aaaabbbb'.repeat(4) + 'aaaaaaaa' + 'aaaab').split('') as Player[]
    // 6-4 set, then a love game to A, then 0-15.
    expect(scoreboardRows(score(w, DEFAULT_RULES), DEFAULT_RULES)).toEqual([
      ['Me', '6', '1', '0'],
      ['Opp', '4', '0', '15'],
    ])
  })

  it('drops the games column for a tiebreak-only session', () => {
    const rules: ScoreRules = { ...DEFAULT_RULES, tiebreak: 'only' }
    expect(scoreboardRows(score(['a', 'a', 'b'], rules), rules)).toEqual([['Me', '2'], ['Opp', '1']])
  })

  it('marks the winner when finished', () => {
    const w = 'a'.repeat(48).split('') as Player[]
    expect(scoreboardRows(score(w, DEFAULT_RULES), DEFAULT_RULES)).toEqual([
      ['Me', '6', '6', 'W'],
      ['Opp', '0', '0', ''],
    ])
  })
})
