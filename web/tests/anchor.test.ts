import { describe, expect, it } from 'vitest'
import {
  anchorFromDraft,
  draftFromState,
  isTiebreakAt,
  POINT_LABELS,
  withSetCount,
} from '../src/lib/anchor'
import { DEFAULT_RULES, score } from '../src/lib/score'
import type { Player, ScoreRules } from '../src/lib/score'

const w = (s: string) => s.split('') as Player[]

describe('draftFromState', () => {
  it('seeds every field from the replayed state so fixing one number is one edit', () => {
    // 6-4, then a love game, then 0-15.
    const state = score(w('aaaabbbb'.repeat(4) + 'aaaaaaaa' + 'aaaab'), DEFAULT_RULES)
    expect(draftFromState(state)).toEqual({
      sets: [[6, 4]],
      games: [1, 0],
      points: ['0', '15'],
    })
  })

  it('carries the advantage label through', () => {
    const state = score(w('aaabbba'), DEFAULT_RULES)
    expect(draftFromState(state).points).toEqual(['Ad', '40'])
  })

  it('seeds tiebreak points as counts', () => {
    const state = score(w('aaaabbbb'.repeat(6) + 'aab'), DEFAULT_RULES)
    expect(draftFromState(state)).toEqual({ sets: [], games: [6, 6], points: ['2', '1'] })
  })
})

describe('isTiebreakAt', () => {
  it('is 6-6 under at6, always under only, never under none', () => {
    expect(isTiebreakAt([6, 6], DEFAULT_RULES)).toBe(true)
    expect(isTiebreakAt([5, 6], DEFAULT_RULES)).toBe(false)
    expect(isTiebreakAt([0, 0], { ...DEFAULT_RULES, tiebreak: 'only' })).toBe(true)
    expect(isTiebreakAt([6, 6], { ...DEFAULT_RULES, tiebreak: 'none' })).toBe(false)
  })
})

describe('withSetCount', () => {
  it('adds empty sets and drops from the end', () => {
    const d = { sets: [[6, 4]] as [number, number][], games: [1, 0] as [number, number], points: ['0', '0'] as [string, string] }
    expect(withSetCount(d, 2).sets).toEqual([[6, 4], [0, 0]])
    expect(withSetCount(d, 0).sets).toEqual([])
    expect(withSetCount(d, 1)).toEqual(d)
  })
})

describe('anchorFromDraft', () => {
  it('turns labels into the raw counts the engines replay from', () => {
    expect(anchorFromDraft({ sets: [[6, 4]], games: [3, 2], points: ['30', '15'] }, DEFAULT_RULES))
      .toEqual({ ok: true, state: { sets: [[6, 4]], games: [3, 2], points: [2, 1] } })
    expect(anchorFromDraft({ sets: [], games: [0, 0], points: ['Ad', '40'] }, DEFAULT_RULES))
      .toEqual({ ok: true, state: { sets: [], games: [0, 0], points: [4, 3] } })
    expect(anchorFromDraft({ sets: [], games: [0, 0], points: ['40', 'Ad'] }, DEFAULT_RULES))
      .toEqual({ ok: true, state: { sets: [], games: [0, 0], points: [3, 4] } })
  })

  it('reads tiebreak points as counts', () => {
    expect(anchorFromDraft({ sets: [], games: [6, 6], points: ['5', '3'] }, DEFAULT_RULES))
      .toEqual({ ok: true, state: { sets: [], games: [6, 6], points: [5, 3] } })
    const only: ScoreRules = { ...DEFAULT_RULES, tiebreak: 'only' }
    expect(anchorFromDraft({ sets: [], games: [0, 0], points: ['9', '9'] }, only))
      .toEqual({ ok: true, state: { sets: [], games: [0, 0], points: [9, 9] } })
  })

  it('refuses Ad against anything but 40, and Ad without advantage scoring', () => {
    expect(anchorFromDraft({ sets: [], games: [0, 0], points: ['Ad', '30'] }, DEFAULT_RULES).ok).toBe(false)
    expect(anchorFromDraft({ sets: [], games: [0, 0], points: ['Ad', 'Ad'] }, DEFAULT_RULES).ok).toBe(false)
    expect(anchorFromDraft({ sets: [], games: [0, 0], points: ['Ad', '40'] }, { ...DEFAULT_RULES, ad: false }).ok)
      .toBe(false)
  })

  it('refuses a number that is not a whole non-negative count', () => {
    expect(anchorFromDraft({ sets: [[6, -1]], games: [0, 0], points: ['0', '0'] }, DEFAULT_RULES).ok).toBe(false)
    expect(anchorFromDraft({ sets: [], games: [1.5, 0], points: ['0', '0'] }, DEFAULT_RULES).ok).toBe(false)
    expect(anchorFromDraft({ sets: [], games: [6, 6], points: ['x', '0'] }, DEFAULT_RULES).ok).toBe(false)
    expect(anchorFromDraft({ sets: [], games: [0, 0], points: ['20', '0'] }, DEFAULT_RULES).ok).toBe(false)
  })

  it('refuses more completed sets than the format has left to play', () => {
    // Best of three: two completed sets is the most a rally can be entered
    // under; three means the match ended before this rally.
    const r = anchorFromDraft({ sets: [[6, 4], [4, 6], [6, 4]], games: [0, 0], points: ['0', '0'] }, DEFAULT_RULES)
    expect(r.ok).toBe(false)
    expect(anchorFromDraft({ sets: [[6, 4], [4, 6]], games: [0, 0], points: ['0', '0'] }, DEFAULT_RULES).ok).toBe(true)
  })

  it('names what is wrong', () => {
    const r = anchorFromDraft({ sets: [], games: [0, 0], points: ['Ad', 'Ad'] }, DEFAULT_RULES)
    expect(r.ok === false && r.error.length > 0).toBe(true)
  })

  it('exposes the point labels the form offers', () => {
    expect(POINT_LABELS).toEqual(['0', '15', '30', '40', 'Ad'])
  })
})
