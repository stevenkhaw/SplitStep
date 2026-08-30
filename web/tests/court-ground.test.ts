import { mount, unmount } from 'svelte'
import { afterEach, describe, expect, it } from 'vitest'
import CourtGround from '../src/components/CourtGround.svelte'

let host: HTMLElement | null = null
let app: Record<string, unknown> | null = null

function render(props: { tier: 'browse' | 'review' }) {
  host = document.createElement('div')
  document.body.appendChild(host)
  app = mount(CourtGround, { target: host, props })
  return host
}

afterEach(() => {
  if (app) unmount(app)
  host?.remove()
  app = null
  host = null
})

describe('CourtGround', () => {
  it('draws a court', () => {
    const el = render({ tier: 'browse' })
    // 11 markings + net + 2 posts + surface + run-off.
    expect(el.querySelectorAll('path').length).toBe(16)
  })

  it('hides itself from assistive tech', () => {
    const el = render({ tier: 'browse' })
    expect(el.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true')
  })

  // The review tier is the whole point of having tiers: atmosphere behind
  // footage you are judging competes with the footage.
  it('recedes on the review tier', () => {
    const browse = render({ tier: 'browse' })
    const browseOpacity = Number(getComputedStyle(browse.firstElementChild!).opacity)
    if (app) unmount(app)
    host?.remove()
    const review = render({ tier: 'review' })
    const reviewOpacity = Number(getComputedStyle(review.firstElementChild!).opacity)
    expect(reviewOpacity).toBeLessThan(browseOpacity)
  })
})
