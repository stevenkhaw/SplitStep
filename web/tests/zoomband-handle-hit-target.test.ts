import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ZoomBand from '../src/components/ZoomBand.svelte'
import type { Rally } from '../src/lib/types'

// The rally box's rendered width is not decoration: `nearestHandle` hit-tests
// a pointer against `frac(start_ms)`/`frac(end_ms)`, while the user aims at
// the box's drawn edges. If the two disagree the right handle becomes
// unclickable -- the click falls outside the 12px grab radius of either true
// handle position and is treated as a scrub instead.

// jsdom has no PointerEvent constructor. ZoomBand only reads `clientX` and
// `pointerId`, both of which a MouseEvent stand-in carries fine.
class FakePointerEvent extends MouseEvent {
  pointerId: number
  constructor(type: string, init: MouseEventInit & { pointerId: number }) {
    super(type, init)
    this.pointerId = init.pointerId
  }
}

// jsdom computes no layout, so `band.getBoundingClientRect()` -- which all of
// ZoomBand's pointer math is measured against -- would read zero width.
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

const WINDOW_START_MS = 85_000
const WINDOW_END_MS = 125_000 // 40s, matching TimelineMode's ZOOM_SPAN_MS

function rally(overrides: Partial<Rally> = {}): Rally {
  return {
    id: 'r1',
    session_id: 's1',
    source_id: 'src1',
    idx: 1,
    // 10s inside a 40s window -> 25% of the band. Comfortably under the 16s
    // it would take to reach the 0.4 floor, and close to the 7.6s median of
    // real footage.
    start_ms: 100_000,
    end_ms: 110_000,
    det_start_ms: 100_000,
    det_end_ms: 110_000,
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

describe('ZoomBand draws handles where the hit test looks for them', () => {
  let target: HTMLDivElement
  let instance: unknown
  let rectSpy: ReturnType<typeof vi.spyOn>
  let oncommit: ReturnType<typeof vi.fn>
  let onscrub: ReturnType<typeof vi.fn>

  beforeEach(() => {
    target = document.createElement('div')
    document.body.appendChild(target)
    rectSpy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(FAKE_RECT)
    oncommit = vi.fn()
    onscrub = vi.fn()
  })

  afterEach(() => {
    if (instance) unmount(instance as never)
    target.remove()
    rectSpy.mockRestore()
    instance = undefined
  })

  function render(r: Rally, neighbours: Rally[] = []) {
    instance = mount(ZoomBand, {
      target,
      props: {
        rally: r,
        neighbours,
        windowStartMs: WINDOW_START_MS,
        windowEndMs: WINDOW_END_MS,
        onchange: vi.fn(),
        oncommit,
        onscrub,
      },
    })
    flushSync()
  }

  function band(): HTMLDivElement {
    const el = target.querySelector('[aria-label="rally boundaries"]')
    if (!el) throw new Error('ZoomBand root not found')
    const b = el as HTMLDivElement
    b.setPointerCapture = vi.fn()
    b.releasePointerCapture = vi.fn()
    return b
  }

  function stylePercent(el: Element | null, prop: 'left' | 'width'): number {
    if (!el) throw new Error(`element for ${prop} not found`)
    const match = new RegExp(`${prop}:\\s*([\\d.]+)%`).exec(el.getAttribute('style') ?? '')
    if (!match) throw new Error(`no ${prop}% in style: ${el.getAttribute('style')}`)
    return Number(match[1])
  }

  const box = () => target.querySelector('.border-blue-400')

  function pointerAt(type: string, clientX: number, pointerId = 1): FakePointerEvent {
    return new FakePointerEvent(type, { clientX, pointerId, bubbles: true })
  }

  it('draws a rally narrower than the floor at its true width, not the floor', () => {
    render(rally())
    // 100000..110000 in an 85000..125000 window: left 37.5%, width 25%.
    expect(stylePercent(box(), 'left')).toBeCloseTo(37.5, 5)
    expect(stylePercent(box(), 'width')).toBeCloseTo(25, 5)
  })

  it('still draws a sliver for a rally too short to see', () => {
    // 100ms of a 40s window is 0.25% -- the floor exists so this stays
    // visible at all rather than collapsing to a hairline.
    render(rally({ start_ms: 100_000, end_ms: 100_100 }))
    expect(stylePercent(box(), 'width')).toBeCloseTo(0.4, 5)
  })

  it('leaves a rally wider than the floor untouched', () => {
    // 25.6s of a 40s window is 64%, well clear of the floor. Kept inside
    // 85000..125000: `frac` clamps to the window, so a rally running past its
    // edge would measure short and test nothing about the floor.
    render(rally({ start_ms: 95_000, end_ms: 120_600 }))
    expect(stylePercent(box(), 'width')).toBeCloseTo(64, 5)
  })

  it('drags the end handle grabbed at the box edge the user can actually see', () => {
    render(rally())
    const el = band()

    // The drawn right edge, in px on the 1000px band. This is where the user
    // aims. Before the units fix it rendered at 77.5% (37.5 + a 40% floor)
    // while `nearestHandle` was still looking at 62.5%, so this pointerdown
    // matched no handle and fell through to a scrub.
    const rightEdgePx = (stylePercent(box(), 'left') + stylePercent(box(), 'width')) * 10
    el.dispatchEvent(pointerAt('pointerdown', rightEdgePx))
    flushSync()
    expect(onscrub).not.toHaveBeenCalled()

    // Pull it 100px left: 10% of a 40s window is 4s off the end.
    el.dispatchEvent(pointerAt('pointermove', rightEdgePx - 100))
    flushSync()
    el.dispatchEvent(pointerAt('pointerup', rightEdgePx - 100))
    flushSync()

    expect(oncommit).toHaveBeenCalledTimes(1)
    const [startMs, endMs] = oncommit.mock.calls[0]
    expect(startMs).toBe(100_000)
    expect(endMs).toBeCloseTo(106_000, -1)
  })

  it('grabs the end handle from a comfortable distance, not pixel-perfect', () => {
    render(rally())
    const el = band()

    // 20px short of the handle: outside the old 12px radius, inside the
    // current one. A mouse cannot reliably land on an 8px-wide target, and
    // missing it silently scrubs instead of doing nothing, which is worse
    // than a miss -- it moves the playhead and looks like the drag "didn't
    // take".
    const rightEdgePx = (stylePercent(box(), 'left') + stylePercent(box(), 'width')) * 10
    el.dispatchEvent(pointerAt('pointerdown', rightEdgePx - 20))
    flushSync()
    expect(onscrub).not.toHaveBeenCalled()

    el.dispatchEvent(pointerAt('pointermove', rightEdgePx - 120))
    flushSync()
    el.dispatchEvent(pointerAt('pointerup', rightEdgePx - 120))
    flushSync()
    expect(oncommit).toHaveBeenCalledTimes(1)
    // The grab is forgiving about where you press, but the drag still tracks
    // the pointer absolutely -- it must not carry the 20px offset along.
    const [, endMs] = oncommit.mock.calls[0]
    expect(endMs).toBeCloseTo(105_200, -1)
  })

  it('still leaves a scrub zone between the handles of a short rally', () => {
    // 2.4s is the shortest rally in the real 2026-08-18 session: 66px wide on
    // a 1104px band, so two 24px zones still leave a gap in the middle. If
    // the zones ever do meet, nearestHandle splits at the midpoint rather
    // than favouring one side, so the degradation is graceful -- but on real
    // footage it should not come to that.
    render(rally({ start_ms: 100_000, end_ms: 102_400 }))
    const el = band()
    const leftPx = stylePercent(box(), 'left') * 10
    const widthPx = stylePercent(box(), 'width') * 10

    el.dispatchEvent(pointerAt('pointerdown', leftPx + widthPx / 2))
    flushSync()
    expect(onscrub).toHaveBeenCalledTimes(1)
    expect(oncommit).not.toHaveBeenCalled()
  })

  it('draws neighbours at their true width too', () => {
    render(rally(), [rally({ id: 'r2', start_ms: 112_000, end_ms: 118_000 })])
    const neighbour = target.querySelector('.bg-blue-500\\/30')
    // 6s of a 40s window is 15%, not the 20% floor.
    expect(stylePercent(neighbour, 'width')).toBeCloseTo(15, 5)
  })
})
