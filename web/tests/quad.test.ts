import { describe, expect, it } from 'vitest'
import {
  DEFAULT_QUAD_POINTS,
  assignedPresetLabel,
  clonePoints,
  defaultPresetName,
  movePoint,
  pointFromClient,
  polygonClipPath,
  quadPolygonPoints,
} from '../src/lib/quad'
import type { Preset } from '../src/lib/types'

describe('pointFromClient', () => {
  const rect = { left: 100, top: 50, width: 200, height: 100 }

  it('maps a point inside the rect to normalized [0,1] coordinates', () => {
    expect(pointFromClient(200, 100, rect)).toEqual([0.5, 0.5])
  })

  it('maps the top-left corner to [0,0]', () => {
    expect(pointFromClient(100, 50, rect)).toEqual([0, 0])
  })

  it('maps the bottom-right corner to [1,1]', () => {
    expect(pointFromClient(300, 150, rect)).toEqual([1, 1])
  })

  it('clamps a point left of the rect to x=0', () => {
    expect(pointFromClient(0, 100, rect)).toEqual([0, 0.5])
  })

  it('clamps a point above the rect to y=0', () => {
    expect(pointFromClient(200, 0, rect)).toEqual([0.5, 0])
  })

  it('clamps a point right of and below the rect to [1,1]', () => {
    expect(pointFromClient(9999, 9999, rect)).toEqual([1, 1])
  })

  it('does not divide by zero when the rect has zero width or height', () => {
    const zero = { left: 10, top: 10, width: 0, height: 0 }
    expect(pointFromClient(10, 10, zero)).toEqual([0, 0])
  })
})

describe('movePoint', () => {
  it('replaces only the point at the given index', () => {
    const points: [number, number][] = [
      [0, 0],
      [1, 0],
      [1, 1],
      [0, 1],
    ]
    const next = movePoint(points, 2, [0.4, 0.6])
    expect(next).toEqual([
      [0, 0],
      [1, 0],
      [0.4, 0.6],
      [0, 1],
    ])
  })

  it('does not mutate the input array', () => {
    const points: [number, number][] = [
      [0, 0],
      [1, 0],
      [1, 1],
      [0, 1],
    ]
    const original = points.map((p) => [...p])
    movePoint(points, 0, [0.9, 0.9])
    expect(points).toEqual(original)
  })
})

describe('clonePoints', () => {
  it('produces a deep copy -- mutating the clone does not touch the original', () => {
    const original = DEFAULT_QUAD_POINTS
    const clone = clonePoints(original)
    clone[0][0] = 0.99
    expect(original[0][0]).not.toBe(0.99)
  })
})

describe('polygonClipPath', () => {
  it('formats points as a percentage-based CSS polygon', () => {
    const points: [number, number][] = [
      [0, 0],
      [1, 0],
      [1, 1],
      [0, 1],
    ]
    expect(polygonClipPath(points)).toBe(
      'polygon(0.0000% 0.0000%, 100.0000% 0.0000%, 100.0000% 100.0000%, 0.0000% 100.0000%)',
    )
  })
})

describe('defaultPresetName', () => {
  it('combines session id and source index', () => {
    expect(defaultPresetName('2026-08-18', 1)).toBe('2026-08-18 source 1')
  })
})

describe('assignedPresetLabel', () => {
  const presets: Preset[] = [
    { id: 'p1', name: 'Memorial Park court 3', points: DEFAULT_QUAD_POINTS, created_at: '' },
  ]

  it('says nothing is assigned when presetId is null', () => {
    expect(assignedPresetLabel(null, presets)).toBe('none -- detection runs on the full frame')
  })

  it('shows the preset name when the assigned preset is in the list', () => {
    expect(assignedPresetLabel('p1', presets)).toBe('Memorial Park court 3')
  })

  it('falls back to the raw id when the preset is not (yet) in the list', () => {
    expect(assignedPresetLabel('unknown-id', presets)).toBe('unknown-id')
  })
})

describe('quadPolygonPoints', () => {
  it('scales normalized corners into an SVG viewBox', () => {
    expect(quadPolygonPoints([[0, 0], [1, 0], [1, 1], [0, 1]], 40, 24)).toBe('0,0 40,0 40,24 0,24')
  })

  it('rounds to two decimals, because an SVG points attribute is markup, not maths', () => {
    // A full double per corner would put ~17 characters of noise in the DOM
    // for a 40px-wide thumbnail. Two decimals is a hundredth of a pixel.
    expect(quadPolygonPoints([[1 / 3, 2 / 3]], 40, 24)).toBe('13.33,16')
  })

  it('is empty for no corners, so the caller renders nothing rather than a broken polygon', () => {
    expect(quadPolygonPoints([], 40, 24)).toBe('')
  })
})
