import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Rally, SessionDetail, Source } from '../src/lib/types'

// LabelMode calls through `api` for its per-source label fetch and for the
// proxy URL; mocking the module (rather than a prop -- LabelMode takes no
// `api` prop) is what lets this test mount the real component without
// hitting a network or a real <video> element.
const mockApi = {
  sourceLabels: vi.fn().mockResolvedValue([]),
  proxyUrl: () => 'about:blank',
  setLabel: vi.fn().mockResolvedValue({ ok: true }),
}

vi.mock('../src/lib/api', () => ({ api: mockApi }))

// jsdom's HTMLMediaElement is inert: play/pause/load are unimplemented and
// VideoDeck's `el.play().then(...)` needs a real Promise back.
HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined)
HTMLMediaElement.prototype.pause = vi.fn()
HTMLMediaElement.prototype.load = vi.fn()

const { default: LabelMode } = await import('../src/components/LabelMode.svelte')

// jsdom has no PointerEvent constructor; LabelMode's scrub handlers only
// read `clientX`/`pointerId`, both of which a MouseEvent stand-in carries.
class FakePointerEvent extends MouseEvent {
  pointerId: number
  constructor(type: string, init: MouseEventInit & { pointerId: number }) {
    super(type, init)
    this.pointerId = init.pointerId
  }
}

// jsdom computes no layout, so the scrub track's getBoundingClientRect --
// what its pointer math is measured against -- would read zero width.
const FAKE_RECT: DOMRect = {
  left: 0,
  top: 0,
  right: 1000,
  bottom: 12,
  width: 1000,
  height: 12,
  x: 0,
  y: 0,
  toJSON: () => ({}),
}

const START_MS = 10_000
const END_MS = 17_600 // 7.6s span, the median real-footage rally length

function detail(): SessionDetail {
  const source: Source = {
    id: 'src1',
    session_id: 's1',
    idx: 1,
    recorded_at: '2026-08-19T10:00:00Z',
    offset_ms: 0,
    duration_ms: 600_000,
    width: 1920,
    height: 1080,
    fps: 30,
    has_original: 1,
    court_preset_id: null,
    status: 'ready',
    rotation_deg: 0,
  }
  const rally: Rally = {
    id: 'r1',
    session_id: 's1',
    source_id: 'src1',
    idx: 1,
    start_ms: START_MS,
    end_ms: END_MS,
    det_start_ms: START_MS,
    det_end_ms: END_MS,
    confidence: 0.9,
    starred: 0,
    rejected: 0,
    point: 0,
    reviewed_at: null,
    seen_at: null,
    note: '',
  }
  return {
    session: {
      id: 's1',
      title: 'session',
      played_on: '2026-08-19',
      status: 'ready',
    },
    sources: [source],
    rallies: [rally],
  }
}

describe('LabelMode scrub bar', () => {
  let target: HTMLDivElement
  let instance: unknown
  let rectSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.sourceLabels.mockResolvedValue([])
    target = document.createElement('div')
    document.body.appendChild(target)
    rectSpy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(FAKE_RECT)
  })

  afterEach(() => {
    if (instance) unmount(instance as never)
    target.remove()
    rectSpy.mockRestore()
    instance = undefined
  })

  async function render() {
    instance = mount(LabelMode, {
      target,
      props: { detail: detail(), onclose: vi.fn() },
    })
    flushSync()
    // The label fetch (`api.sourceLabels`) is async, same as ResegmentPanel's
    // scores fetch in other tests -- wait for the controller to actually
    // mount a rally rather than assuming one microtask is enough.
    await vi.waitFor(() => {
      if (!target.querySelector('[aria-label="scrub within rally"]')) {
        throw new Error('scrub bar not mounted yet')
      }
    })
  }

  function scrubTrack(): HTMLDivElement {
    const el = target.querySelector('[aria-label="scrub within rally"]')
    if (!el) throw new Error('scrub bar not found')
    const b = el as HTMLDivElement
    b.setPointerCapture = vi.fn()
    b.releasePointerCapture = vi.fn()
    return b
  }

  function video(): HTMLVideoElement {
    const el = target.querySelector('video')
    if (!el) throw new Error('no video element')
    return el
  }

  function fillTransform(): string {
    // The scrub bar's only child is the fill div.
    const el = scrubTrack().querySelector('div')
    return el?.style.transform ?? ''
  }

  function pointerAt(type: string, clientX: number, pointerId = 1): FakePointerEvent {
    return new FakePointerEvent(type, { clientX, pointerId, bubbles: true })
  }

  it('seeks the deck to the clicked position', async () => {
    await render()
    const el = scrubTrack()

    // 30% across a 1000px band -> 30% into the 7.6s span.
    el.dispatchEvent(pointerAt('pointerdown', 300))
    flushSync()

    expect(video().currentTime).toBeCloseTo((START_MS + 0.3 * (END_MS - START_MS)) / 1000, 2)
    expect(fillTransform()).toContain('scaleX(0.3')
  })

  it('tracks a drag continuously, clamped to the rally span past the right edge', async () => {
    await render()
    const el = scrubTrack()

    el.dispatchEvent(pointerAt('pointerdown', 500))
    flushSync()
    expect(video().currentTime).toBeCloseTo((START_MS + END_MS) / 2 / 1000, 2)

    // Drag past the track's right edge -- pointer capture means this event
    // keeps arriving even once clientX has left the element's own bounds.
    // The out-point must hold regardless: this is label mode, and
    // overshooting here would show the next rally's footage, exactly what
    // the looping playback in VideoDeck exists to prevent.
    el.dispatchEvent(pointerAt('pointermove', 1400))
    flushSync()
    expect(video().currentTime).toBeCloseTo(END_MS / 1000, 2)
    expect(fillTransform()).toContain('scaleX(1')
  })

  it('does not scrub on pointermove before a pointerdown grabs the bar', async () => {
    await render()
    const el = scrubTrack()
    const before = video().currentTime

    el.dispatchEvent(pointerAt('pointermove', 700))
    flushSync()

    expect(video().currentTime).toBe(before)
  })

  it('stops tracking the pointer after pointerup', async () => {
    await render()
    const el = scrubTrack()

    el.dispatchEvent(pointerAt('pointerdown', 100))
    flushSync()
    el.dispatchEvent(pointerAt('pointerup', 100))
    flushSync()

    const afterUp = video().currentTime
    el.dispatchEvent(pointerAt('pointermove', 900))
    flushSync()

    expect(video().currentTime).toBe(afterUp)
  })
})
