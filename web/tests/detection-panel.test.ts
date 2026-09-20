import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Rally, Source } from '../src/lib/types'

// Same mocking approach as resegment-panel.test.ts before it: the panel
// calls through `../lib/api` directly rather than taking it as a prop.
const mockApi = {
  resegment: vi.fn(),
  scores: vi.fn(),
  detectSource: vi.fn(),
  proxyUrl: () => 'about:blank',
  frameUrl: () => 'about:blank',
}

vi.mock('../src/lib/api', () => ({ api: mockApi }))

const { default: DetectionPanel } = await import('../src/components/DetectionPanel.svelte')

/** The four corners a reviewer drew, and the same four in a second row. */
const CORNERS: [number, number][] = [
  [0.35, 0.35],
  [0.65, 0.35],
  [0.98, 1.0],
  [0.02, 1.0],
]
const SAME_CORNERS: [number, number][] = CORNERS.map(([x, y]) => [x, y] as [number, number])
const MOVED_CORNERS: [number, number][] = [
  [0.35, 0.35],
  [0.65, 0.35],
  [0.9, 1.0],
  [0.02, 1.0],
]

function source(overrides: Partial<Source> = {}): Source {
  return {
    id: 'src1',
    session_id: 's1',
    idx: 1,
    recorded_at: '2026-09-16T10:00:00Z',
    offset_ms: 0,
    duration_ms: 600000,
    width: 1920,
    height: 1080,
    fps: 30,
    has_original: 1,
    court_preset_id: 'p2',
    status: 'ready',
    rotation_deg: 0,
    features_at: '2026-09-16T03:29:00+00:00',
    preset_assigned_at: '2026-09-16T17:49:00+00:00',
    segment_threshold: 0.15,
    features_preset_id: 'p1',
    court_preset_points: SAME_CORNERS,
    features_preset_points: CORNERS,
    ...overrides,
  }
}

function rally(overrides: Partial<Rally> = {}): Rally {
  return {
    id: 'r1',
    session_id: 's1',
    source_id: 'src1',
    idx: 1,
    start_ms: 1000,
    end_ms: 2000,
    det_start_ms: 1000,
    det_end_ms: 2000,
    confidence: 0.9,
    starred: 0,
    rejected: 0,
    point: 0,
    reviewed_at: null,
    seen_at: null,
    note: '',
    winner: '',
    ...overrides,
  }
}

describe('DetectionPanel', () => {
  let target: HTMLDivElement
  let instance: unknown

  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.scores.mockResolvedValue({ step_ms: 200, threshold: 0.25, scores: [0.1, 0.9] })
    mockApi.detectSource.mockResolvedValue({ job_id: 'j1', already_running: false })
    mockApi.resegment.mockResolvedValue({ count: 4 })
    target = document.createElement('div')
    document.body.appendChild(target)
  })

  afterEach(() => {
    if (instance) unmount(instance as never)
    target.remove()
    instance = undefined
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  function render(s: Source, rallies: Rally[] = [rally()], onresegmented = vi.fn()) {
    instance = mount(DetectionPanel, { target, props: { source: s, rallies, onresegmented } })
    flushSync()
  }

  /** The one <details> in the panel: the threshold section. */
  function details(): HTMLDetailsElement {
    const el = target.querySelector('details')
    if (!el) throw new Error('threshold section not found')
    return el as HTMLDetailsElement
  }

  async function expand() {
    const d = details()
    d.open = true
    d.dispatchEvent(new Event('toggle'))
    flushSync()
    await Promise.resolve()
    flushSync()
  }

  /**
   * Whether a node is on screen with the threshold section collapsed.
   *
   * jsdom has no layout and no UA stylesheet for <details>, so a closed
   * one keeps every child in the DOM and in `textContent` -- asserting on
   * text cannot tell visible from hidden here. What IS observable is the
   * structural fact that decides it in a real browser: a closed <details>
   * hides everything it contains except its own <summary>.
   */
  function shownWhileCollapsed(node: Node | null | undefined): boolean {
    if (!node) return false
    const d = details()
    return !d.contains(node) || !!d.querySelector('summary')?.contains(node)
  }

  function primary(): HTMLButtonElement {
    const el = target.querySelector('button[data-role="detection-primary"]')
    if (!el) throw new Error('primary action button not found')
    return el as HTMLButtonElement
  }

  function slider(): HTMLInputElement | null {
    return target.querySelector('input[type="range"]')
  }

  function dragTo(value: string) {
    const el = slider()
    if (!el) throw new Error('threshold slider not found')
    el.value = value
    el.dispatchEvent(new Event('input', { bubbles: true }))
    flushSync()
  }

  // -- the three plan states, and what the one button says in each --------

  it('offers the expensive run, priced, when the assigned region differs from the built one', () => {
    render(source({ court_preset_points: MOVED_CORNERS }))
    expect(primary().textContent).toContain('Run detection')
    expect(primary().textContent).toContain('15 min')
    expect(primary().disabled).toBe(false)
  })

  it('offers the instant re-cut, priced, when only the threshold moved', async () => {
    render(source())
    await expand()
    dragTo('0.30')
    expect(primary().textContent).toContain('Re-segment')
    expect(primary().textContent).toContain('instant')
  })

  it('disables the button, in words, when nothing has changed', () => {
    // The same four corners held by a second preset row -- the wizard
    // writes a fresh row on every save, so this is the ordinary case. It
    // used to announce "play region changed" and offer fifteen minutes of
    // GPU; the user hit it three times on one source.
    render(source())
    expect(primary().disabled).toBe(true)
    expect(primary().textContent).toContain('Nothing to apply')
    expect(target.textContent).not.toContain('only a full detection picks it up')
  })

  // -- the region in effect, which had no answer anywhere in the app ------

  it('names both regions: the one in effect and the one assigned now', () => {
    render(source({ court_preset_points: MOVED_CORNERS }))
    const inEffect = target.querySelector('[data-role="region-in-effect"]')
    const assigned = target.querySelector('[data-role="region-assigned"]')
    expect(inEffect?.textContent).toContain('four corners')
    expect(assigned?.textContent).toContain('four corners')
    // Each draws its own quad, so a difference is seen and not inferred.
    expect(inEffect?.querySelector('polygon')).not.toBeNull()
    expect(assigned?.querySelector('polygon')).not.toBeNull()
  })

  it('says "whole frame" for an unassigned region and "not recorded" for an unserved one', () => {
    render(source({ court_preset_id: null, court_preset_points: null, features_preset_points: null }))
    expect(target.querySelector('[data-role="region-assigned"]')?.textContent)
      .toContain('whole frame')
    expect(target.querySelector('[data-role="region-in-effect"]')?.textContent)
      .toContain('not recorded')
  })

  it('keeps both regions out of the collapse — the stale one is why the panel exists', () => {
    // The bug shipped this morning: the stale-region information lived
    // inside a disclosure triangle that ships closed, so the one sentence
    // explaining why a freshly assigned region is being ignored was
    // visible only to someone already doing the thing it warns against.
    render(source({ court_preset_points: MOVED_CORNERS }))
    expect(details().open).toBe(false)
    expect(shownWhileCollapsed(target.querySelector('[data-role="region-in-effect"]'))).toBe(true)
    expect(shownWhileCollapsed(target.querySelector('[data-role="region-assigned"]'))).toBe(true)
    expect(shownWhileCollapsed(primary())).toBe(true)
    expect(shownWhileCollapsed(target.querySelector('[data-role="action-note"]'))).toBe(true)
  })

  it('costs no /scores fetch to render all of that', () => {
    // /scores parses the whole of features.jsonl. The collapse exists to
    // avoid paying it on every session load, and surfacing the region
    // information must not hand that cost back.
    render(source({ court_preset_points: MOVED_CORNERS }))
    expect(mockApi.scores).not.toHaveBeenCalled()
  })

  it('fetches the curve only once the threshold section is opened', async () => {
    render(source())
    expect(mockApi.scores).not.toHaveBeenCalled()
    await expand()
    expect(mockApi.scores).toHaveBeenCalledWith('src1', 0.15)
  })

  // -- what the rallies on screen were cut at -----------------------------

  it('says what the current rallies were cut at, in font-data', () => {
    render(source({ segment_threshold: 0.15 }))
    const el = target.querySelector('[data-role="cut-at"]')
    expect(el?.textContent).toContain('cut at 0.15')
    expect(el?.className).toContain('font-data')
    expect(shownWhileCollapsed(el)).toBe(true)
  })

  it('says unknown in words, never as a number, when nothing recorded one', () => {
    render(source({ segment_threshold: null }))
    const el = target.querySelector('[data-role="cut-at"]')
    expect(el?.textContent).toContain('unknown threshold')
    expect(el?.textContent).not.toMatch(/\d/)
  })

  // -- before the first detect --------------------------------------------

  it('says there is no curve yet instead of drawing an empty chart', async () => {
    // An `ingested` source: proxy built, never detected. Session hands
    // this panel every non-needs_setup source, so this is a real state.
    render(source({ features_at: null }))
    await expand()
    expect(target.textContent).toContain('No score curve yet')
    expect(target.querySelector('svg[role="img"]')).toBeNull()
    expect(slider()).toBeNull()
    // /scores answers 409 without features; asking would be a guaranteed
    // failure rendered as an error the reviewer can do nothing about.
    expect(mockApi.scores).not.toHaveBeenCalled()
  })

  // -- the one button actually doing the two things -----------------------

  it('sends the chosen threshold with the expensive run, so it is not reset to the default', async () => {
    // The reported bug in one assertion: re-segment to 0.15, run a detect,
    // and the fifteen-minute run silently re-cuts at the profile default
    // because POST /detect had no way to take a threshold.
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(source({ court_preset_points: MOVED_CORNERS }))
    await expand()
    dragTo('0.18')
    primary().click()
    flushSync()
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(mockApi.detectSource).toHaveBeenCalledWith('src1', 0.18)
  })

  it('sends the recorded threshold with a detect the reviewer never opened the slider for', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(source({ court_preset_points: MOVED_CORNERS, segment_threshold: 0.15 }))
    primary().click()
    flushSync()
    await vi.waitFor(() => expect(mockApi.detectSource).toHaveBeenCalledWith('src1', 0.15))
  })

  it('does not queue the expensive run when the confirmation is declined', () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(source({ court_preset_points: MOVED_CORNERS }))
    primary().click()
    flushSync()
    expect(mockApi.detectSource).not.toHaveBeenCalled()
  })

  it('re-segments at the slider value and reports the count back', async () => {
    const onresegmented = vi.fn()
    render(source(), [rally()], onresegmented)
    await expand()
    dragTo('0.30')
    primary().click()
    flushSync()
    await vi.waitFor(() => expect(onresegmented).toHaveBeenCalledTimes(1))
    expect(mockApi.resegment).toHaveBeenCalledWith('src1', 0.3)
    await vi.waitFor(() => expect(target.textContent).toMatch(/4 rallies at threshold/))
  })

  it('confirms, naming the cost, before a re-segment discards a hand-edited boundary', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(source(), [rally({ start_ms: 900 })])
    await expand()
    dragTo('0.30')
    primary().click()
    flushSync()
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(confirmSpy.mock.calls[0][0]).toContain('1 hand-edited boundary')
    expect(mockApi.resegment).not.toHaveBeenCalled()
  })

  it('does not confirm a re-segment when nothing was hand-edited', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm')
    render(source())
    await expand()
    dragTo('0.30')
    primary().click()
    flushSync()
    expect(confirmSpy).not.toHaveBeenCalled()
    await vi.waitFor(() => expect(mockApi.resegment).toHaveBeenCalledWith('src1', 0.3))
  })

  // -- tokens --------------------------------------------------------------

  it('gives the live action the filled primary treatment, and no accent', () => {
    render(source({ court_preset_points: MOVED_CORNERS }))
    expect(primary().className).toContain('bg-fg')
    expect(primary().className).toContain('text-bg')
    // A stale region is a call to action, not a failure.
    expect(primary().className).not.toContain('danger')
  })

  it('debounces the slider, so a drag is not one /scores call per pixel', async () => {
    vi.useFakeTimers()
    render(source())
    await expand()
    await vi.waitFor(() => expect(mockApi.scores).toHaveBeenCalledTimes(1))
    mockApi.scores.mockClear()

    dragTo('0.30')
    vi.advanceTimersByTime(50)
    dragTo('0.40')
    vi.advanceTimersByTime(50)
    dragTo('0.50')
    expect(mockApi.scores).not.toHaveBeenCalled()

    vi.advanceTimersByTime(150)
    await vi.waitFor(() => expect(mockApi.scores).toHaveBeenCalledTimes(1))
    expect(mockApi.scores).toHaveBeenCalledWith('src1', 0.5)
  })
})
