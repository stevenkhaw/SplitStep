import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// VideoDeck owns two <video> elements and swaps them on advance so a seek
// never stalls the queue. The outgoing element is left playing: it is
// normally silenced only as a side effect of the preload effect reassigning
// its src for the rally after next, and that effect returns early when there
// is no next rally. Advancing onto the LAST rally therefore orphans an
// element mid-playback, still emitting audio -- and `pause()` only ever
// reaches `live()`, so neither the space bar nor anything else can stop it.

HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined)
HTMLMediaElement.prototype.pause = vi.fn()
HTMLMediaElement.prototype.load = vi.fn()

const { default: VideoDeckHarness } = await import('./support/VideoDeckHarness.svelte')

// Both rallies live in the same source file, which is the ordinary case: the
// preload seeks the idle element into the same proxy the live one is playing.
const SRC = 'about:blank'

describe('VideoDeck silences the element it swaps away from', () => {
  let target: HTMLDivElement
  let instance: { advance: (to: Record<string, unknown>) => void } | undefined

  beforeEach(() => {
    vi.clearAllMocks()
    target = document.createElement('div')
    document.body.appendChild(target)
  })

  afterEach(() => {
    if (instance) unmount(instance as never)
    target.remove()
    instance = undefined
  })

  function videos(): HTMLVideoElement[] {
    return Array.from(target.querySelectorAll('video'))
  }

  function mountDeck() {
    instance = mount(VideoDeckHarness, {
      target,
      props: {
        src: SRC,
        startMs: 1000,
        endMs: 5000,
        nextSrc: SRC,
        nextStartMs: 9000,
        onended: vi.fn(),
      },
    }) as unknown as { advance: (to: Record<string, unknown>) => void }
    flushSync()
  }

  it('pauses the outgoing element when advancing onto the last rally', () => {
    mountDeck()
    const [a, b] = videos()
    // Element A is live on rally 1; B has been preloaded at rally 2's start.
    const aPause = vi.spyOn(a, 'pause')
    const bPause = vi.spyOn(b, 'pause')

    // Advance to rally 2 -- the preloaded one -- with nothing after it. This
    // is the last clip of a review pass.
    instance?.advance({ src: SRC, startMs: 9000, endMs: 14000 })
    flushSync()

    // B is now live and should be playing; A has been swapped away from and
    // must not be left running.
    expect(aPause).toHaveBeenCalled()
    expect(bPause).not.toHaveBeenCalled()
  })

  it('pauses the outgoing element when a next rally does exist', () => {
    // The case that already worked, pinned so the fix does not regress it or
    // silence the wrong element.
    mountDeck()
    const [a, b] = videos()
    const aPause = vi.spyOn(a, 'pause')
    const bPause = vi.spyOn(b, 'pause')

    instance?.advance({
      src: SRC,
      startMs: 9000,
      endMs: 14000,
      nextSrc: SRC,
      nextStartMs: 20000,
    })
    flushSync()

    expect(aPause).toHaveBeenCalled()
    expect(bPause).not.toHaveBeenCalled()
  })
})
