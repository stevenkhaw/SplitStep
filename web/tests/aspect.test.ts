import { describe, expect, it } from 'vitest'
import {
  DEFAULT_ASPECT,
  DEFAULT_DECK_HEIGHT,
  TIMELINE_DECK_HEIGHT,
  deckMaxWidth,
  isPortrait,
  videoAspect,
} from '../src/lib/aspect'

describe('videoAspect', () => {
  it('reads a landscape source', () => {
    expect(videoAspect(1920, 1080)).toBeCloseTo(16 / 9)
  })

  it('reads a portrait source', () => {
    // The shape of six of the eleven sources on the drive: a 4K iPhone
    // clip the wizard rotated, whose proxy comes out 608x1080.
    expect(videoAspect(608, 1080)).toBeCloseTo(608 / 1080)
    expect(videoAspect(608, 1080)).toBeLessThan(1)
  })

  it('falls back to 16:9 rather than dividing by zero', () => {
    // A source row before its proxy is built carries zeroes, and a <video>
    // reports 0x0 before metadata. Both mean "not known yet", and the value
    // goes straight into a CSS aspect-ratio, so neither may produce NaN or
    // Infinity.
    for (const [w, h] of [[0, 0], [1920, 0], [0, 1080]] as const) {
      expect(videoAspect(w, h)).toBe(DEFAULT_ASPECT)
    }
    expect(videoAspect(null, null)).toBe(DEFAULT_ASPECT)
    expect(videoAspect(undefined, undefined)).toBe(DEFAULT_ASPECT)
  })

  it('refuses a negative dimension the same way', () => {
    expect(videoAspect(-1920, 1080)).toBe(DEFAULT_ASPECT)
  })
})

describe('isPortrait', () => {
  it('splits on the square', () => {
    expect(isPortrait(videoAspect(608, 1080))).toBe(true)
    expect(isPortrait(videoAspect(1920, 1080))).toBe(false)
    // A square frame is not a phone held on its end -- same tie-break the
    // render side takes (splitstep/media/transcode.py::clip_frame).
    expect(isPortrait(videoAspect(1080, 1080))).toBe(false)
  })
})

describe('deckMaxWidth', () => {
  it('turns the available height into the width that aspect implies', () => {
    expect(deckMaxWidth(16 / 9, TIMELINE_DECK_HEIGHT))
      .toBe(`calc(calc(100vh - 28rem) * ${16 / 9})`)
  })

  it('budgets a height by default, so a portrait deck cannot run off screen', () => {
    // Without a cap a 9:16 box at 1440px wide wants to be 2560px tall, which
    // puts every control under it below the fold -- and a landscape deck
    // never needed one, which is why there wasn't one to inherit.
    expect(deckMaxWidth(9 / 16)).toBe(`calc(${DEFAULT_DECK_HEIGHT} * ${9 / 16})`)
  })

  it('gives a portrait deck a narrower cap than a landscape one', () => {
    const portrait = videoAspect(608, 1080)
    expect(deckMaxWidth(portrait)).toContain(`* ${portrait}`)
    expect(portrait).toBeLessThan(16 / 9)
  })
})
