import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Rally, SessionDetail } from '../src/lib/types'

// Finding 3 (CRITICAL): TimelineMode's zoom window recenters mid-drag,
// giving ZoomBand's boundary handles ~2x gain (window shifts by half of
// every move, so each pointermove amplifies the last).
//
// Same mocking approach as resegment-panel.test.ts / requeue-on-detail-
// swap.test.ts.
const mockApi = {
  listSessions: vi.fn(),
  getSession: vi.fn(),
  star: vi.fn(),
  reject: vi.fn(),
  seen: vi.fn(),
  setBounds: vi.fn().mockResolvedValue({ ok: true }),
  resegment: vi.fn(),
  scores: vi.fn().mockResolvedValue({ step_ms: 200, threshold: 0.45, scores: [] }),
  listPresets: vi.fn(),
  createPreset: vi.fn(),
  setPreset: vi.fn(),
  jobs: vi.fn(),
  proxyUrl: () => 'about:blank',
  frameUrl: () => 'about:blank',
  getSource: vi.fn(),
  setup: vi.fn(),
  previewUrl: () => 'about:blank',
}

vi.mock('../src/lib/api', () => ({ api: mockApi }))

HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined)
HTMLMediaElement.prototype.pause = vi.fn()
HTMLMediaElement.prototype.load = vi.fn()

// jsdom has no PointerEvent constructor at all (unlike every real browser).
// ZoomBand only ever reads `clientX` and `pointerId` off the events it
// receives, both of which a MouseEvent-based stand-in carries fine.
class FakePointerEvent extends MouseEvent {
  pointerId: number
  constructor(type: string, init: MouseEventInit & { pointerId: number }) {
    super(type, init)
    this.pointerId = init.pointerId
  }
}

const { default: TimelineMode } = await import('../src/components/TimelineMode.svelte')

// A fixed, fake layout for every element -- jsdom itself never computes
// real layout, so ZoomBand's `band.getBoundingClientRect()` (which its
// pointer math is measured against) would otherwise always read zero
// width. 1000px wide, matching this file's px/ms arithmetic below.
const FAKE_RECT: DOMRect = {
  left: 0,
  top: 0,
  right: 1000,
  bottom: 64,
  width: 1000,
  height: 64,
  x: 0,
  y: 0,
  toJSON: () => ({}),
}

function source(): SessionDetail['sources'][number] {
  return {
    id: 'src1',
    session_id: 's1',
    idx: 1,
    recorded_at: '2026-08-19T10:00:00Z',
    offset_ms: 0,
    duration_ms: 600000, // 10 minutes -- wide enough that a 40s zoom window centered near this rally never clamps against a source edge
    width: 1920,
    height: 1080,
    fps: 30,
    has_original: 1,
    court_preset_id: null,
    status: 'ready',
    rotation_deg: 0,
  }
}

function rally(overrides: Partial<Rally> = {}): Rally {
  return {
    id: 'r1',
    session_id: 's1',
    source_id: 'src1',
    idx: 1,
    start_ms: 100000,
    end_ms: 110000,
    det_start_ms: 100000,
    det_end_ms: 110000,
    confidence: 0.9,
    starred: 0,
    rejected: 0,
    point: 0,
    reviewed_at: null,
    seen_at: null,
    note: '',
    ...overrides,
  }
}

function detailWith(rallies: Rally[]): SessionDetail {
  return {
    session: { id: 's1', title: 'test session', played_on: '2026-08-19', status: 'ready' },
    sources: [source()],
    rallies,
  }
}

describe('ZoomBand drag tracks the pointer 1:1 (Finding 3: window must not recenter mid-drag)', () => {
  let target: HTMLDivElement
  let instance: unknown
  let rectSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    vi.clearAllMocks()
    target = document.createElement('div')
    document.body.appendChild(target)
    rectSpy = vi
      .spyOn(HTMLElement.prototype, 'getBoundingClientRect')
      .mockReturnValue(FAKE_RECT)
  })

  afterEach(() => {
    if (instance) unmount(instance as never)
    target.remove()
    rectSpy.mockRestore()
    instance = undefined
  })

  function band(): HTMLDivElement {
    const el = target.querySelector('[aria-label="rally boundaries"]')
    if (!el) throw new Error('ZoomBand root not found')
    return el as HTMLDivElement
  }

  // ZoomBand's highlighted-rally box -- its rendered `left` percentage is
  // exactly what a real user watches the handle do while dragging.
  function boxLeftPercent(): number {
    const el = target.querySelector('.border-accent') as HTMLElement | null
    if (!el) throw new Error('rally box not found')
    const match = /left:\s*([\d.]+)%/.exec(el.getAttribute('style') ?? '')
    if (!match) throw new Error(`no left% in style: ${el.getAttribute('style')}`)
    return Number(match[1])
  }

  function pointerAt(type: string, clientX: number, pointerId = 1): FakePointerEvent {
    return new FakePointerEvent(type, { clientX, pointerId, bubbles: true })
  }

  it('moving the start handle N px moves the rendered handle N px, not 2N, across several intermediate moves', async () => {
    // rally centered at 105000ms; zoomWindow(105000, 40000, 600000) =
    // [85000, 125000] -- neither edge clamped. start_ms=100000 sits at
    // fraction 0.375 of that window -> px 375 on our 1000px fake band.
    instance = mount(TimelineMode, {
      target,
      props: { detail: detailWith([rally()]), rallyId: 'r1', onclose: vi.fn() },
    })
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/rally 1/))

    const el = band()
    el.setPointerCapture = vi.fn()
    el.releasePointerCapture = vi.fn()

    // Grab the start handle dead-on (px 375 == its exact fraction in the
    // window above).
    el.dispatchEvent(pointerAt('pointerdown', 375))
    flushSync()

    // Drag it left in four small steps of 25px each (100px total) rather
    // than one big jump -- the bug compounds *per move*, so several small
    // steps expose it far more clearly than a single large one.
    const steps = [350, 325, 300, 275]
    for (const px of steps) {
      el.dispatchEvent(pointerAt('pointermove', px))
      flushSync()
      // The window is frozen for the whole drag, so the rendered handle's
      // fraction of the (unchanging) 1000px-wide band is a fixed linear
      // function of the pointer's raw px position: left% == px / 10.
      // Before the fix, the window recentered after every move (shifting
      // by half of that move's delta), so this diverges further from
      // px/10 with every step instead of tracking it exactly.
      expect(boxLeftPercent()).toBeCloseTo(px / 10, 5)
    }

    el.dispatchEvent(pointerAt('pointerup', 275))
    flushSync()
    await vi.waitFor(() => expect(mockApi.setBounds).toHaveBeenCalledTimes(1))

    // Final committed start_ms: window start (85000) + (275/1000)*40000ms
    // span = 85000 + 11000 = 96000 -- exactly the linear mapping from the
    // *initial* window, independent of how many intermediate steps it took
    // to get there. end_ms (the untouched handle) is unchanged.
    expect(mockApi.setBounds).toHaveBeenCalledWith('r1', 96000, 110000)
  })

  it('reaching the same final pointer position in one big jump or many tiny steps commits the identical bounds (path independence == no compounding)', async () => {
    async function dragToFinalPx(steps: number[]) {
      vi.clearAllMocks()
      mockApi.setBounds.mockResolvedValue({ ok: true })
      const inst = mount(TimelineMode, {
        target,
        props: { detail: detailWith([rally()]), rallyId: 'r1', onclose: vi.fn() },
      })
      flushSync()
      await vi.waitFor(() => expect(target.textContent).toMatch(/rally 1/))

      const el = band()
      el.setPointerCapture = vi.fn()
      el.releasePointerCapture = vi.fn()
      el.dispatchEvent(pointerAt('pointerdown', 375))
      flushSync()
      for (const px of steps) {
        el.dispatchEvent(pointerAt('pointermove', px))
        flushSync()
      }
      el.dispatchEvent(pointerAt('pointerup', steps[steps.length - 1]))
      flushSync()
      await vi.waitFor(() => expect(mockApi.setBounds).toHaveBeenCalledTimes(1))
      const committed = mockApi.setBounds.mock.calls[0]
      unmount(inst as never)
      return committed
    }

    const oneBigStep = await dragToFinalPx([275])
    const tenTinySteps = await dragToFinalPx([365, 355, 345, 335, 325, 315, 305, 295, 285, 275])

    expect(oneBigStep).toEqual(tenTinySteps)
    expect(oneBigStep).toEqual(['r1', 96000, 110000])
  })
})
