import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import {
  FORMATS,
  MOUNTS,
  detectionOwed,
  needsCorners,
  scorerFor,
  untunedNote,
} from '../src/lib/mount'
import type { Mount, PlayFormat, Scorer, ScorerChoice } from '../src/lib/types'

// The same file tests/test_scorer.py reads; see tokens.test.ts for why join().
const CASES = JSON.parse(
  readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), '../../tests/fixtures/scorer_cases.json'),
    'utf8',
  ),
) as { mount: Mount; play_format: PlayFormat; scorer: ScorerChoice }[]

const owed = (
  scorer_now: ScorerChoice,
  detected_scorer: Scorer | null,
  has_boxes = false,
) => detectionOwed({ scorer_now, detected_scorer, has_boxes })

describe('options', () => {
  it('lists the choices with Not set last', () => {
    expect(MOUNTS.map((m) => m.label)).toEqual(['Fence', 'Ground', 'Not set'])
    expect(FORMATS.map((f) => f.label)).toEqual(['Singles', 'Doubles', 'Drills', 'Not set'])
    expect(MOUNTS[2].value).toBe('')
    expect(FORMATS[3].value).toBe('')
  })
})

describe('scorerFor', () => {
  it('covers every combination in the shared fixture', () => {
    expect(CASES).toHaveLength(12)
    for (const c of CASES) expect(scorerFor(c.mount, c.play_format)).toBe(c.scorer)
  })
})

describe('untunedNote', () => {
  it('names the combination for fence with anything but singles', () => {
    expect(untunedNote('fence', 'doubles')).toBe(
      'Not tuned for fence + doubles yet — using the general detector.',
    )
    expect(untunedNote('fence', 'drills')).toContain('fence + drills')
    expect(untunedNote('fence', '')).toContain('fence + not set')
  })
  it('is silent when fence is tuned or not chosen', () => {
    expect(untunedNote('fence', 'singles')).toBeNull()
    expect(untunedNote('ground', 'doubles')).toBeNull()
    expect(untunedNote('', '')).toBeNull()
  })
})

describe('needsCorners', () => {
  it('is true for fence only', () => {
    expect(needsCorners('fence', 'singles')).toBe(true)
    expect(needsCorners('fence', '')).toBe(true)
    expect(needsCorners('ground', 'singles')).toBe(false)
    expect(needsCorners('', '')).toBe(false)
  })
})

describe('detectionOwed', () => {
  it('fence wanted with cached boxes is a re-cut', () => {
    expect(owed('fence', 'subject', true)).toBe('resegment')
    expect(owed('fence', null, true)).toBe('resegment')
  })
  it('fence wanted without boxes is a re-detect', () => {
    expect(owed('fence', 'pair', false)).toBe('redetect')
    expect(owed('fence', null, false)).toBe('redetect')
  })
  it('fence already detected is nothing', () => {
    expect(owed('fence', 'fence', true)).toBe('none')
  })
  it('auto matches what auto produced, and unknown', () => {
    expect(owed('auto', 'pair')).toBe('none')
    expect(owed('auto', 'subject')).toBe('none')
    expect(owed('auto', null)).toBe('none')
  })
  it('ground over pair features is a re-cut; unknown is not a change', () => {
    expect(owed('subject', 'pair')).toBe('resegment')
    expect(owed('subject', 'subject')).toBe('none')
    expect(owed('subject', null)).toBe('none')
  })
  it('ground over a fence run is a re-cut, features are always cached', () => {
    expect(owed('subject', 'fence')).toBe('resegment')
  })
  it('a redrawn fence court (server nulls detected_scorer) is a re-cut', () => {
    expect(owed('fence', null, true)).toBe('resegment')
  })
  it('a source from an older server owes nothing', () => {
    expect(detectionOwed({})).toBe('none')
  })
})
