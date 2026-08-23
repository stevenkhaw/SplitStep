import { describe, expect, it } from 'vitest'
import {
  MODES,
  isHelpKey,
  primaryShortcuts,
  shortcutGroups,
  shortcutKeys,
} from '../src/lib/shortcuts'

describe('isHelpKey', () => {
  const ev = (over: Partial<KeyboardEvent> = {}) =>
    ({ key: '?', metaKey: false, ctrlKey: false, altKey: false, target: null, ...over }) as unknown as KeyboardEvent

  it('opens on ?', () => {
    expect(isHelpKey(ev())).toBe(true)
  })

  // A shifted / is still `?` on a US layout, but not every layout puts it
  // there -- `/` is accepted so the key is reachable either way.
  it('also opens on the unshifted slash', () => {
    expect(isHelpKey(ev({ key: '/' }))).toBe(true)
  })

  it('ignores any other key', () => {
    expect(isHelpKey(ev({ key: 's' }))).toBe(false)
  })

  // Same guard every mode handler already applies: a `?` typed into the
  // preset-name field or a note is text, not a shortcut.
  it('stands down for an editable target', () => {
    const input = document.createElement('input')
    expect(isHelpKey(ev({ target: input }))).toBe(false)
  })

  it('stands down for a browser shortcut', () => {
    expect(isHelpKey(ev({ metaKey: true }))).toBe(false)
    expect(isHelpKey(ev({ ctrlKey: true }))).toBe(false)
  })
})

describe('shortcutGroups', () => {
  it('covers all three keyboard modes', () => {
    expect(MODES).toEqual(['queue', 'timeline', 'label'])
    for (const m of MODES) expect(shortcutGroups(m).length).toBeGreaterThan(0)
  })

  it('gives every entry at least one key and a label', () => {
    for (const m of MODES) {
      for (const g of shortcutGroups(m)) {
        expect(g.title).not.toBe('')
        for (const s of g.items) {
          expect(s.keys.length).toBeGreaterThan(0)
          expect(s.label).not.toBe('')
        }
      }
    }
  })

  // A key doing two things in one mode is a real conflict, not a display
  // problem -- whichever handler runs second silently loses.
  it('binds each key once per mode', () => {
    for (const m of MODES) {
      const keys = shortcutKeys(m)
      expect(new Set(keys).size).toBe(keys.length)
    }
  })
})

describe('primaryShortcuts', () => {
  // This is the whole point of the module: the strip under the video and the
  // overlay are generated from one list, so the inline legend cannot drift
  // out of date the way a hand-written sentence did.
  it('is drawn from the same list the overlay renders', () => {
    for (const m of MODES) {
      const all = shortcutGroups(m).flatMap((g) => g.items)
      for (const s of primaryShortcuts(m)) {
        expect(all).toContainEqual(s)
      }
    }
  })

  it('stays short enough to sit on one line', () => {
    for (const m of MODES) {
      expect(primaryShortcuts(m).length).toBeLessThanOrEqual(6)
    }
  })
})

describe('the queue reference matches the handler it documents', () => {
  // Spot-checks against QueueMode's own switch. If a binding moves, this is
  // what says the documentation moved with it.
  it('lists the review verdicts', () => {
    const items = shortcutGroups('queue').flatMap((g) => g.items)
    expect(items.find((s) => s.keys.includes('S'))?.label).toMatch(/star/i)
    expect(items.find((s) => s.keys.includes('X'))?.label).toMatch(/reject/i)
    expect(items.find((s) => s.keys.includes('P'))?.label).toMatch(/point/i)
  })

  it('lists the speed keys as one entry, not four', () => {
    const speed = shortcutGroups('queue')
      .flatMap((g) => g.items)
      .find((s) => s.label.match(/speed/i))
    expect(speed?.keys).toEqual(['`', '1', '2', '3'])
  })
})

describe('the label reference matches its verdict and flag maps', () => {
  it('lists all four verdicts and all four boundary flags', () => {
    const items = shortcutGroups('label').flatMap((g) => g.items)
    for (const k of ['1', '2', '3', '4', 'Q', 'W', 'O', 'P']) {
      expect(items.some((s) => s.keys.includes(k))).toBe(true)
    }
  })
})

describe('timeline split bindings', () => {
  it('binds C and U in timeline mode', () => {
    const keys = shortcutKeys('timeline')
    expect(keys).toContain('C')
    expect(keys).toContain('U')
  })

  it('keeps the timeline strip at six and puts split in it', () => {
    // Past six the strip wraps and stops being glanceable, which is the
    // failure it replaces. The frame-step pair leaves for the `?` overlay --
    // they are a mirror pair, discoverable from one another.
    const strip = primaryShortcuts('timeline')
    expect(strip).toHaveLength(6)
    expect(strip.flatMap((s) => s.keys)).toEqual(['[', ']', 'C', 'U', 'Esc', '?'])
  })
})
