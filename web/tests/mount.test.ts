import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import {
  CORNER_LABELS,
  DEFAULT_CORNER_POINTS,
  FORMATS,
  MOUNTS,
  detectionOwed,
  needsCorners,
  parseCorners,
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
  it('auto over a fence run is a re-cut: auto never produces fence', () => {
    // Fence + singles switched to doubles, drills, Not set, or off Fence.
    expect(owed('auto', 'fence', true)).toBe('resegment')
    expect(owed('auto', 'fence', false)).toBe('resegment')
  })
  it('ground over pair features is a re-cut', () => {
    expect(owed('subject', 'pair')).toBe('resegment')
    expect(owed('subject', 'subject')).toBe('none')
  })
  it('ground over unknown is a re-cut: no pre-018 source has mount ground', () => {
    expect(owed('subject', null)).toBe('resegment')
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

describe('court corners', () => {
  it('labels the handles in the server\'s storage order', () => {
    expect(CORNER_LABELS).toEqual(['near-left', 'near-right', 'far-right', 'far-left'])
  })

  it('starts the handles on a shape the server would accept', () => {
    // Same turning test as validate_corners: image y points down, so every
    // cross product z is negative for near-left -> ... -> far-left.
    const p = DEFAULT_CORNER_POINTS
    for (let i = 0; i < 4; i++) {
      const [ax, ay] = p[(i + 3) % 4]
      const [bx, by] = p[i]
      const [cx, cy] = p[(i + 1) % 4]
      expect((bx - ax) * (cy - by) - (by - ay) * (cx - bx)).toBeLessThan(0)
    }
    for (const [x, y] of p) {
      expect(x).toBeGreaterThanOrEqual(0)
      expect(x).toBeLessThanOrEqual(1)
      expect(y).toBeGreaterThanOrEqual(0)
      expect(y).toBeLessThanOrEqual(1)
    }
  })

  it('parses the stored JSON string', () => {
    expect(parseCorners('[[0.1,0.9],[0.9,0.9],[0.7,0.4],[0.3,0.4]]')).toEqual([
      [0.1, 0.9],
      [0.9, 0.9],
      [0.7, 0.4],
      [0.3, 0.4],
    ])
  })

  it('answers null, never a throw, for nothing stored or anything malformed', () => {
    expect(parseCorners(null)).toBeNull()
    expect(parseCorners(undefined)).toBeNull()
    expect(parseCorners('')).toBeNull()
    expect(parseCorners('not json')).toBeNull()
    expect(parseCorners('[[0,0],[1,0],[1,1]]')).toBeNull()
    expect(parseCorners('[[0,0],[1,0],[1,1],[0,"1"]]')).toBeNull()
    expect(parseCorners('{"a":1}')).toBeNull()
  })
})
