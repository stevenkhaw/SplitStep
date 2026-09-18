import { describe, expect, it } from 'vitest'
import { DEFAULT_RULES, playerGlyphs, winnerChip, winnerLabel } from '../src/lib/score'
import type { ScoreRules } from '../src/lib/score'

const rules = (over: Partial<ScoreRules> = {}): ScoreRules => ({ ...DEFAULT_RULES, ...over })

describe('playerGlyphs', () => {
  it('is each name first initial, uppercased', () => {
    expect(playerGlyphs(rules({ players: ['sam', 'Opp'] }))).toEqual(['S', 'O'])
  })

  it('falls back to positional letters when the initials collide', () => {
    // Two players named Alex and Ana must not both render "A".
    expect(playerGlyphs(rules({ players: ['Alex', 'Ana'] }))).toEqual(['A', 'B'])
  })

  it('compares initials case-insensitively before falling back', () => {
    expect(playerGlyphs(rules({ players: ['alex', 'Ana'] }))).toEqual(['A', 'B'])
  })
})

describe('winnerChip', () => {
  it('is null without rules -- an untracked session has no winner to show', () => {
    expect(winnerChip('a', null)).toBeNull()
    expect(winnerChip('', null)).toBeNull()
  })

  it('reads as unrecorded when no winner is stored', () => {
    expect(winnerChip('', rules({ players: ['Sam', 'Opp'] }))).toEqual({
      glyph: '–',
      title: 'no winner recorded',
      set: false,
    })
  })

  it('carries the winner initial and names them in words', () => {
    expect(winnerChip('a', rules({ players: ['Sam', 'Opp'] }))).toEqual({
      glyph: 'S',
      title: 'won by Sam',
      set: true,
    })
    expect(winnerChip('b', rules({ players: ['Sam', 'Opp'] }))).toEqual({
      glyph: 'O',
      title: 'won by Opp',
      set: true,
    })
  })

  it('uses the positional letter when the initials collide', () => {
    expect(winnerChip('b', rules({ players: ['Alex', 'Ana'] }))).toEqual({
      glyph: 'B',
      title: 'won by Ana',
      set: true,
    })
  })
})

describe('winnerLabel', () => {
  it('is null when the session tracks no score', () => {
    expect(winnerLabel('a', null)).toBeNull()
  })

  it('is null when no winner is recorded -- an unscored rally says nothing', () => {
    expect(winnerLabel('', rules())).toBeNull()
  })

  it('names the winner', () => {
    expect(winnerLabel('b', rules({ players: ['Sam', 'Opp'] }))).toBe('won by Opp')
  })
})
