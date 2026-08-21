import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import VideoDeck from '../src/components/VideoDeck.svelte'

// QueueMode and the timeline both loop a clip with `onended={() => deck.replay()}`,
// so the out-point has to be detectable more than once for the same rally.
// `rallyFinished` guards against rAF and `timeupdate` both firing onended for
// a single pass; if nothing clears it on a deliberate restart, the second pass
// finds it still set, returns before pausing, and playback runs on into
// whatever footage follows the rally.

// jsdom's HTMLMediaElement is inert: play/pause are unimplemented and return
// undefined, while every real browser returns a Promise from play(), which
// VideoDeck relies on.
HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined)
HTMLMediaElement.prototype.pause = vi.fn()
HTMLMediaElement.prototype.load = vi.fn()

const START_MS = 1000
const END_MS = 5000

describe('VideoDeck detects the out-point on every pass, not just the first', () => {
  let target: HTMLDivElement
  let instance: { replay: () => void } | undefined
  let onended: ReturnType<typeof vi.fn>

  beforeEach(() => {
    vi.clearAllMocks()
    target = document.createElement('div')
    document.body.appendChild(target)
    onended = vi.fn()
  })

  afterEach(() => {
    if (instance) unmount(instance as never)
    target.remove()
    instance = undefined
  })

  function live(): HTMLVideoElement {
    const el = target.querySelector('video')
    if (!el) throw new Error('no video element')
    return el
  }

  // Drive the `timeupdate` backstop rather than rAF: it is the path that
  // survives a throttled tab, it is deterministic under vitest, and both
  // paths funnel through the same finishRally guard.
  function playThroughTheOutPoint(): void {
    const el = live()
    el.currentTime = END_MS / 1000
    el.dispatchEvent(new Event('timeupdate'))
    flushSync()
  }

  it('fires onended again after a replay of the same rally', () => {
    instance = mount(VideoDeck, {
      target,
      props: { src: 'about:blank', startMs: START_MS, endMs: END_MS, onended },
    }) as unknown as { replay: () => void }
    flushSync()

    playThroughTheOutPoint()
    expect(onended).toHaveBeenCalledTimes(1)

    // Exactly what QueueMode's onended handler does.
    instance.replay()
    flushSync()

    playThroughTheOutPoint()
    expect(onended).toHaveBeenCalledTimes(2)
  })

  it('pauses at the out-point on the second pass, not only the first', () => {
    instance = mount(VideoDeck, {
      target,
      props: { src: 'about:blank', startMs: START_MS, endMs: END_MS, onended },
    }) as unknown as { replay: () => void }
    flushSync()

    playThroughTheOutPoint()
    const pausesAfterFirst = (HTMLMediaElement.prototype.pause as ReturnType<typeof vi.fn>).mock
      .calls.length
    expect(pausesAfterFirst).toBeGreaterThan(0)

    instance.replay()
    flushSync()
    playThroughTheOutPoint()

    // The runaway symptom: without a pause here the element keeps playing
    // past the rally and into the next one's footage.
    expect(
      (HTMLMediaElement.prototype.pause as ReturnType<typeof vi.fn>).mock.calls.length,
    ).toBeGreaterThan(pausesAfterFirst)
  })

  it('still fires onended only once per pass', () => {
    // The guard's original job: rAF and `timeupdate` race to spot the
    // out-point, and whichever loses must not fire a second onended for the
    // same pass. Clearing the flag on restart must not reopen that.
    instance = mount(VideoDeck, {
      target,
      props: { src: 'about:blank', startMs: START_MS, endMs: END_MS, onended },
    }) as unknown as { replay: () => void }
    flushSync()

    playThroughTheOutPoint()
    playThroughTheOutPoint()
    playThroughTheOutPoint()
    expect(onended).toHaveBeenCalledTimes(1)
  })
})
