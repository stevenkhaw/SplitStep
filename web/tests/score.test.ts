import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { DEFAULT_RULES, score, scoreBefore, scoreboardRows } from '../src/lib/score'
import type { Player, ScoreRules } from '../src/lib/score'
import type { Rally } from '../src/lib/types'

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
  cases: { name: string; rules?: Partial<ScoreRules>; winners: string; expect: unknown }[]
}

describe('score', () => {
  for (const c of CASES.cases) {
    it(c.name, () => {
      const rules = { ...CASES.defaultRules, ...(c.rules ?? {}) }
      expect(score(c.winners.split('') as Player[], rules)).toEqual(c.expect)
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
