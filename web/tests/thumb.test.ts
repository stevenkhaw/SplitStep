import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mockApi = {
  frameUrl: (sessionId: string, idx: number, atMs = 0) =>
    `/media/${sessionId}/${idx}/frame.jpg?at_ms=${atMs}`,
}
vi.mock('../src/lib/api', () => ({ api: mockApi }))

const { default: ThumbHarness } = await import('./support/ThumbHarness.svelte')

describe('Thumb', () => {
  let target: HTMLDivElement
  let instance: { setIdx: (i: number | null) => void } | undefined

  beforeEach(() => {
    target = document.createElement('div')
    document.body.appendChild(target)
  })

  afterEach(() => {
    if (instance) unmount(instance as never)
    target.remove()
    instance = undefined
  })

  const img = () => target.querySelector('img')

  function open(idx: number | null = 1) {
    instance = mount(ThumbHarness, { target, props: { idx } }) as unknown as {
      setIdx: (i: number | null) => void
    }
    flushSync()
  }

  it('asks the frame endpoint for the requested moment', () => {
    open()
    expect(img()?.getAttribute('src')).toBe('/media/s1/1/frame.jpg?at_ms=30000')
  })

  // Two different ways to have no picture, one placeholder: no source at all
  // (nothing to ask for) and a source whose proxy has not been built yet
  // (frame.jpg 404s until handle_build_proxy finishes). The card has to keep
  // its height either way or the row jumps as a session moves through the
  // pipeline.
  it('draws the placeholder when there is no source to ask about', () => {
    open(null)
    expect(img()).toBeNull()
  })

  it('falls back to the placeholder when the frame does not load', () => {
    open()
    img()?.dispatchEvent(new Event('error'))
    flushSync()
    expect(img()).toBeNull()
  })

  // The reason the reset effect exists. A session polled while its proxy is
  // still building 404s once; without clearing `broken` on a new src it would
  // stay on the placeholder for the rest of the page's life, even after the
  // proxy lands and the card re-renders against a different source.
  it('tries again when the source it points at changes', () => {
    open()
    img()?.dispatchEvent(new Event('error'))
    flushSync()
    expect(img()).toBeNull()

    instance!.setIdx(2)
    flushSync()
    expect(img()?.getAttribute('src')).toBe('/media/s1/2/frame.jpg?at_ms=30000')
  })
})
