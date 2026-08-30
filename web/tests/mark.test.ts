import { mount, unmount } from 'svelte'
import { afterEach, describe, expect, it } from 'vitest'
import Mark from '../src/components/Mark.svelte'
import { MARK, seamPath } from '../src/lib/mark'

let host: HTMLElement | null = null
let app: Record<string, unknown> | null = null

function render(props: { size: number; state?: 'still' | 'loading' }) {
  host = document.createElement('div')
  document.body.appendChild(host)
  app = mount(Mark, { target: host, props })
  return host
}

afterEach(() => {
  if (app) unmount(app)
  host?.remove()
  app = null
  host = null
})

describe('the mark', () => {
  it('is a ball split into two halves', () => {
    const el = render({ size: 32 })
    expect(el.querySelectorAll('circle').length).toBe(2)
  })

  it('carries both seams in each half, or it is not a tennis ball', () => {
    // Four arcs: two seams, drawn once per half. Dropping them is what made
    // the first draft read as two plain half-discs.
    const el = render({ size: 32 })
    expect(el.querySelectorAll('path').length).toBe(4)
  })

  it('honours the size it is given', () => {
    const el = render({ size: 19 })
    const svg = el.querySelector('svg')!
    expect(svg.getAttribute('width')).toBe('19')
    expect(svg.getAttribute('height')).toBe('19')
  })

  it('says nothing to a screen reader when decorative', () => {
    const el = render({ size: 19 })
    expect(el.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true')
  })

  it('announces itself when it is the loading state', () => {
    const el = render({ size: 32, state: 'loading' })
    expect(el.querySelector('[role="status"]')).not.toBeNull()
    expect(el.textContent).toContain('Loading')
  })

  it('still shows loading under reduced motion, not just to a screen reader', () => {
    // The animation classes are motion-safe:-gated -- under
    // prefers-reduced-motion they never apply, so a sighted reduced-motion
    // viewer sees a static ball unless the label itself becomes visible.
    // jsdom does not evaluate @media (prefers-reduced-motion), so this
    // asserts the escape hatch exists in markup rather than that it renders
    // under a given media state.
    const el = render({ size: 32, state: 'loading' })
    const label = el.querySelector('.sr-only')
    expect(label?.textContent).toContain('Loading')
    expect(label?.className).toContain('motion-reduce:not-sr-only')
  })
})

describe('MARK constants', () => {
  // packaging/make_icon.py draws the same ball in Pillow and asserts against
  // these numbers (tests/test_icon.py). Two drawings of one shape drift, so
  // this object is the single source and the Python side is the follower.
  it('is complete', () => {
    for (const key of [
      'viewBox', 'radius', 'gap', 'step', 'splitInset',
      'seamWidth', 'seamRx', 'seamRy', 'seamTopY', 'seamBottomY',
      'seamLeftX', 'seamRightX',
    ] as const) {
      expect(typeof MARK[key], key).toBe('number')
    }
  })

  it('splits the ball without detaching it', () => {
    // A gap wider than a quarter of the radius stops reading as one ball --
    // which is exactly the note that killed the widest icon variant.
    expect(MARK.gap).toBeGreaterThan(0)
    expect(MARK.gap).toBeLessThan(MARK.radius / 4)
  })
})

describe('the seam', () => {
  // Moved out of Mark.svelte's <script> so packaging/make_icon.py's
  // seam_arc() has one TypeScript-side construction to be a translation
  // of, not two inline template strings the guard test has to reparse out
  // of the component's markup.
  it('draws each half from its own edge, sweeping opposite ways', () => {
    const left = seamPath('left')
    const right = seamPath('right')
    expect(left).toContain(`M${MARK.seamLeftX} `)
    expect(right).toContain(`M${MARK.seamRightX} `)
    // The sweep flag is the 6th number in the SVG arc command -- opposite
    // for each side is what makes the two arcs bulge toward each other
    // instead of both curving the same way.
    expect(left).toMatch(/A[\d.]+ [\d.]+ 0 0 1 /)
    expect(right).toMatch(/A[\d.]+ [\d.]+ 0 0 0 /)
  })
})
