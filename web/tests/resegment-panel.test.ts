import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Rally, Source } from '../src/lib/types'

// Same mocking approach as requeue-on-detail-swap.test.ts: ResegmentPanel
// calls through `../lib/api` directly, so the module is mocked rather than
// passed in as a prop.
const mockApi = {
  listSessions: vi.fn(),
  getSession: vi.fn(),
  star: vi.fn(),
  reject: vi.fn(),
  seen: vi.fn(),
  setBounds: vi.fn(),
  resegment: vi.fn(),
  scores: vi.fn().mockResolvedValue({ step_ms: 200, threshold: 0.45, scores: [0.1, 0.9] }),
  listPresets: vi.fn(),
  createPreset: vi.fn(),
  setPreset: vi.fn(),
  detectSource: vi.fn(),
  jobs: vi.fn(),
  proxyUrl: () => 'about:blank',
  frameUrl: () => 'about:blank',
  getSource: vi.fn(),
  setup: vi.fn(),
  previewUrl: () => 'about:blank',
}

vi.mock('../src/lib/api', () => ({ api: mockApi }))

const { default: ResegmentPanel } = await import('../src/components/ResegmentPanel.svelte')

function source(id: string, idx: number, overrides: Partial<Source> = {}): Source {
  return {
    id,
    session_id: 's1',
    idx,
    recorded_at: '2026-08-19T10:00:00Z',
    offset_ms: 0,
    duration_ms: 600000,
    width: 1920,
    height: 1080,
    fps: 30,
    has_original: 1,
    court_preset_id: null,
    status: 'ready',
    rotation_deg: 0,
    features_at: null,
    preset_assigned_at: null,
    segment_threshold: null,
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

describe('ResegmentPanel', () => {
  let target: HTMLDivElement
  let instance: unknown

  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.scores.mockResolvedValue({ step_ms: 200, threshold: 0.45, scores: [0.1, 0.9] })
    target = document.createElement('div')
    document.body.appendChild(target)
  })

  afterEach(() => {
    if (instance) unmount(instance as never)
    target.remove()
    instance = undefined
    vi.useRealTimers()
  })

  // The panel ships collapsed, and its /scores effect is gated on that --
  // parsing features.jsonl on every session load for a panel nobody opened
  // is the cost the collapse removes. Every test here is about what happens
  // once it *is* open, so each one expands it first. Setting `open` and
  // dispatching the toggle by hand rather than clicking <summary>: jsdom
  // fires the real toggle asynchronously, which would race both flushSync
  // and the fake timers the debounce test installs.
  function expand() {
    const details = target.querySelector('details')
    if (!details) throw new Error('panel details not found')
    details.open = true
    details.dispatchEvent(new Event('toggle'))
    flushSync()
  }

  function slider(): HTMLInputElement {
    const el = target.querySelector('input[type="range"]')
    if (!el) throw new Error('threshold slider not found')
    return el as HTMLInputElement
  }

  function dragTo(value: string) {
    const el = slider()
    el.value = value
    el.dispatchEvent(new Event('input', { bubbles: true }))
  }

  it('adopts the threshold the API reports instead of a hardcoded default', async () => {
    // subject-mode profile: threshold lives on a different scale (0.25) than
    // the old hardcoded pair-mode default (0.45) -- the slider must start
    // where the resolved value actually is, not at a constant.
    mockApi.scores.mockResolvedValue({ step_ms: 200, threshold: 0.25, scores: [0.1, 0.9] })
    instance = mount(ResegmentPanel, {
      target,
      props: { sources: [source('src1', 1)], rallies: [rally()], onresegmented: vi.fn() },
    })
    flushSync()
    expand()
    await vi.waitFor(() => expect(mockApi.scores).toHaveBeenCalled())
    flushSync()

    // The first call must omit the threshold entirely -- that's how the UI
    // asks the API to resolve the per-source profile default.
    expect(mockApi.scores).toHaveBeenCalledWith('src1', undefined)
    expect(slider().value).toBe('0.25')
  })

  it('resets the threshold to null and re-queries without one when the selected source changes', async () => {
    // src1 resolves to a pair-mode default (0.45), src2 to a subject-mode
    // default (0.25) -- exactly the mixed-profile session the review
    // flagged: switching sources must re-ask the API for the *new*
    // source's own default, not silently reapply the old one.
    mockApi.scores.mockImplementation((id: string) =>
      Promise.resolve(
        id === 'src1'
          ? { step_ms: 200, threshold: 0.45, scores: [0.1, 0.9] }
          : { step_ms: 200, threshold: 0.25, scores: [0.2, 0.8] },
      ),
    )
    instance = mount(ResegmentPanel, {
      target,
      props: {
        sources: [source('src1', 1), source('src2', 2)],
        rallies: [rally()],
        onresegmented: vi.fn(),
      },
    })
    flushSync()
    expand()
    await vi.waitFor(() => expect(mockApi.scores).toHaveBeenCalledWith('src1', undefined))
    flushSync()
    expect(slider().value).toBe('0.45')

    mockApi.scores.mockClear()
    const select = target.querySelector('select') as HTMLSelectElement
    select.value = 'src2'
    select.dispatchEvent(new Event('change', { bubbles: true }))
    flushSync()

    // The reset to null is synchronous -- the slider is disabled the
    // instant the source changes, before the (mocked, async) response for
    // src2 has had any chance to resolve.
    expect(slider().disabled).toBe(true)
    expect(mockApi.scores).toHaveBeenCalledWith('src2', undefined)

    await vi.waitFor(() => expect(slider().value).toBe('0.25'))
    expect(slider().disabled).toBe(false)
  })

  it('ignores a stale /scores response that resolves after switching to a different source', async () => {
    // /scores' cost scales with features.jsonl's length, so responses do not
    // land in request order: switch from a long source to a short one and
    // the long one's (src1's) response routinely arrives *after* the short
    // one's (src2's) -- this is the expected case, not a rare interleaving.
    // Without the scoredSourceId guard, src1's late response would win the
    // last write to `threshold` and hand the UI a pair-mode 0.45 for a
    // subject-mode source, silently sendable to POST /resegment.
    let resolveSrc1!: (v: { step_ms: number; threshold: number; scores: number[] }) => void
    let resolveSrc2!: (v: { step_ms: number; threshold: number; scores: number[] }) => void
    mockApi.scores.mockImplementation(
      (id: string) =>
        new Promise((resolve) => {
          if (id === 'src1') resolveSrc1 = resolve
          else resolveSrc2 = resolve
        }),
    )

    instance = mount(ResegmentPanel, {
      target,
      props: {
        sources: [source('src1', 1), source('src2', 2)],
        rallies: [rally()],
        onresegmented: vi.fn(),
      },
    })
    flushSync()
    expand()
    await vi.waitFor(() => expect(mockApi.scores).toHaveBeenCalledWith('src1', undefined))

    // Switch before src1's (slow) response has arrived.
    const select = target.querySelector('select') as HTMLSelectElement
    select.value = 'src2'
    select.dispatchEvent(new Event('change', { bubbles: true }))
    flushSync()
    await vi.waitFor(() => expect(mockApi.scores).toHaveBeenCalledWith('src2', undefined))

    // src2's (short-source) response lands first, as it routinely would.
    resolveSrc2({ step_ms: 200, threshold: 0.25, scores: [0.2, 0.8] })
    await vi.waitFor(() => expect(slider().value).toBe('0.25'))

    // src1's stale response lands late, after the switch it no longer
    // belongs to. It must not overwrite the src2 value now on screen.
    resolveSrc1({ step_ms: 200, threshold: 0.45, scores: [0.1, 0.9] })
    await Promise.resolve()
    await Promise.resolve()
    flushSync()

    expect(slider().value).toBe('0.25')
  })

  it('debounces the threshold slider: a burst of input collapses into one scores call', async () => {
    vi.useFakeTimers()
    instance = mount(ResegmentPanel, {
      target,
      props: { sources: [source('src1', 1)], rallies: [rally()], onresegmented: vi.fn() },
    })
    flushSync()
    expand()
    // The initial mount effect fetches scores once for the default source --
    // unrelated to the slider, so it's excluded from the burst count below.
    await vi.waitFor(() => expect(mockApi.scores).toHaveBeenCalledTimes(1))
    mockApi.scores.mockClear()

    dragTo('0.50')
    flushSync()
    vi.advanceTimersByTime(50)
    dragTo('0.60')
    flushSync()
    vi.advanceTimersByTime(50)
    dragTo('0.70')
    flushSync()
    // Still inside the 150ms debounce window -- no call yet.
    expect(mockApi.scores).not.toHaveBeenCalled()

    vi.advanceTimersByTime(150)
    await vi.waitFor(() => expect(mockApi.scores).toHaveBeenCalledTimes(1))
    expect(mockApi.scores).toHaveBeenCalledWith('src1', 0.7)
  })

  it('does not confirm and calls onresegmented when no boundaries are hand-edited', async () => {
    const onresegmented = vi.fn()
    mockApi.resegment.mockResolvedValue({ count: 4 })
    const confirmSpy = vi.spyOn(window, 'confirm')

    instance = mount(ResegmentPanel, {
      target,
      props: {
        sources: [source('src1', 1)],
        rallies: [rally({ id: 'r1' }), rally({ id: 'r2', idx: 2 })], // untouched bounds
        onresegmented,
      },
    })
    flushSync()
    expand()
    // The button is disabled until the initial /scores response resolves
    // the threshold (see the null-window handling) -- a real user can't
    // click it any sooner, and neither can this test.
    await vi.waitFor(() => expect(mockApi.scores).toHaveBeenCalled())
    flushSync()

    const button = target.querySelector('button') as HTMLButtonElement
    button.click()
    flushSync()
    // Wait for the callback that only fires after `await api.resegment(...)`
    // resolves -- not just for the call itself, which happens synchronously
    // inside run() before that await yields.
    await vi.waitFor(() => expect(onresegmented).toHaveBeenCalledTimes(1))

    expect(confirmSpy).not.toHaveBeenCalled()
    expect(mockApi.resegment).toHaveBeenCalledWith('src1', 0.45)
    await vi.waitFor(() => expect(target.textContent).toMatch(/4 rallies at threshold/))
  })

  it('confirms, naming the count, before discarding hand-edited boundaries -- proceeds on OK', async () => {
    const onresegmented = vi.fn()
    mockApi.resegment.mockResolvedValue({ count: 2 })
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)

    instance = mount(ResegmentPanel, {
      target,
      props: {
        sources: [source('src1', 1)],
        // r1's start_ms was hand-dragged away from det_start_ms.
        rallies: [rally({ id: 'r1', start_ms: 900 }), rally({ id: 'r2', idx: 2 })],
        onresegmented,
      },
    })
    flushSync()
    expand()
    await vi.waitFor(() => expect(mockApi.scores).toHaveBeenCalled())
    flushSync()

    const button = target.querySelector('button') as HTMLButtonElement
    button.click()
    flushSync()
    await vi.waitFor(() => expect(onresegmented).toHaveBeenCalledTimes(1))

    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(confirmSpy.mock.calls[0][0]).toContain('1 hand-edited boundary')
    expect(mockApi.resegment).toHaveBeenCalledWith('src1', 0.45)
  })

  it('cancelling the confirmation aborts -- no resegment call, no onresegmented', async () => {
    const onresegmented = vi.fn()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)

    instance = mount(ResegmentPanel, {
      target,
      props: {
        sources: [source('src1', 1)],
        rallies: [rally({ id: 'r1', start_ms: 900 })],
        onresegmented,
      },
    })
    flushSync()
    expand()
    await vi.waitFor(() => expect(mockApi.scores).toHaveBeenCalled())
    flushSync()

    const button = target.querySelector('button') as HTMLButtonElement
    button.click()
    flushSync()

    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(mockApi.resegment).not.toHaveBeenCalled()
    expect(onresegmented).not.toHaveBeenCalled()
  })
})


describe('ResegmentPanel stale-region warning', () => {
  let target: HTMLDivElement
  let instance: unknown

  const STALE = {
    features_at: '2026-09-01T10:00:00+00:00',
    preset_assigned_at: '2026-09-02T10:00:00+00:00',
  }
  const FRESH = {
    features_at: '2026-09-02T10:00:00+00:00',
    preset_assigned_at: '2026-09-01T10:00:00+00:00',
  }
  const WARNING = "Play region changed after the last detect — re-segment still uses the old one."

  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.scores.mockResolvedValue({ step_ms: 200, threshold: 0.45, scores: [0.1, 0.9] })
    mockApi.detectSource.mockResolvedValue({ job_id: 'j1', already_running: false })
    target = document.createElement('div')
    document.body.appendChild(target)
  })

  afterEach(() => {
    if (instance) unmount(instance as never)
    target.remove()
    instance = undefined
    vi.restoreAllMocks()
  })

  async function open(sources: Source[]) {
    instance = mount(ResegmentPanel, {
      target,
      props: { sources, rallies: [rally()], onresegmented: vi.fn() },
    })
    flushSync()
    const details = target.querySelector('details')
    if (!details) throw new Error('panel details not found')
    details.open = true
    details.dispatchEvent(new Event('toggle'))
    flushSync()
    await vi.waitFor(() => expect(mockApi.scores).toHaveBeenCalled())
    flushSync()
  }

  function detectButton(): HTMLButtonElement | null {
    const all = [...target.querySelectorAll('button')] as HTMLButtonElement[]
    return all.find((b) => b.textContent?.trim() === 'Run detection') ?? null
  }

  it('warns, and offers a detect, when the region is newer than the features', async () => {
    await open([source('src1', 1, STALE)])
    expect(target.textContent).toContain(WARNING)
    expect(detectButton()).not.toBeNull()
  })

  it('gives that detect the filled primary treatment', async () => {
    await open([source('src1', 1, STALE)])
    expect(detectButton()?.className).toContain('bg-fg')
    expect(detectButton()?.className).toContain('text-bg')
  })

  it('stays quiet when the features were rebuilt after the region was assigned', async () => {
    await open([source('src1', 1, FRESH)])
    expect(target.textContent).not.toContain('Play region changed')
    expect(detectButton()).toBeNull()
  })

  it('stays quiet on a library with no timestamps at all', async () => {
    // Every row predating migration 014 looks like this. Unknown is not a
    // reason to nag.
    await open([source('src1', 1)])
    expect(target.textContent).not.toContain('Play region changed')
  })

  it('warns about a stale source even while a different one is selected', async () => {
    // This used to be scoped to the selected source, which was defensible
    // only while the warning lived beside the selector. It does not any
    // more: the selector is inside the collapse, so scoping to it would
    // mean a stale source 2 says nothing at all until someone opens the
    // panel and picks it -- the same silence this whole fix is about.
    await open([source('src1', 1, FRESH), source('src2', 2, STALE)])
    expect(target.textContent).toContain(WARNING)
    expect(target.textContent).toContain('source 2')
  })

  it('queues detection on the selected source, once confirmed', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    await open([source('src1', 1, STALE)])
    detectButton()?.click()
    flushSync()

    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(mockApi.detectSource).toHaveBeenCalledWith('src1')
  })

  it('does not queue detection when the confirmation is declined', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    await open([source('src1', 1, STALE)])
    detectButton()?.click()
    flushSync()

    expect(mockApi.detectSource).not.toHaveBeenCalled()
  })
})

// The reported bug, and the reason this describe exists separately from the
// one above: every test up there expands the panel first, so all of them
// passed while the warning was unreachable in practice. The panel ships
// collapsed (the /scores fetch is expensive and gated on it), and the stale
// region warning used to render *inside* it -- so the one sentence telling a
// reviewer why their freshly assigned play region is being ignored was only
// visible to someone who deliberately opened the threshold-tuning panel.
// Source 2026-09-16/01 got a region fourteen hours after its features were
// built; the reviewer re-segmented, saw nothing change, and was never told.
//
// Note what these tests assert on, and why it is not text or visibility:
// jsdom has no layout and no UA stylesheet for <details>, so a collapsed
// panel's children are all still in the DOM and still in `textContent`.
// Nothing about "is it on screen" is observable here. What IS observable is
// the structural fact that decides it in a real browser -- content inside a
// closed <details> is hidden unless it is inside the <summary> -- so these
// assert on containment, which is the same claim without the layout.
describe('ResegmentPanel stale-region warning, panel collapsed', () => {
  let target: HTMLDivElement
  let instance: unknown

  const STALE = {
    features_at: '2026-09-16T03:29:00+00:00',
    preset_assigned_at: '2026-09-16T17:49:00+00:00',
  }
  const FRESH = {
    features_at: '2026-09-16T17:49:00+00:00',
    preset_assigned_at: '2026-09-16T03:29:00+00:00',
  }
  const WARNING = 'Play region changed after the last detect — re-segment still uses the old one.'

  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.scores.mockResolvedValue({ step_ms: 200, threshold: 0.45, scores: [0.1, 0.9] })
    mockApi.detectSource.mockResolvedValue({ job_id: 'j1', already_running: false })
    target = document.createElement('div')
    document.body.appendChild(target)
  })

  afterEach(() => {
    if (instance) unmount(instance as never)
    target.remove()
    instance = undefined
    vi.restoreAllMocks()
  })

  // Deliberately never expands: mount and look, exactly as a reviewer
  // arriving on the session route does.
  function mountCollapsed(sources: Source[]) {
    instance = mount(ResegmentPanel, {
      target,
      props: { sources, rallies: [rally()], onresegmented: vi.fn() },
    })
    flushSync()
  }

  function details(): HTMLDetailsElement {
    const el = target.querySelector('details')
    if (!el) throw new Error('panel details not found')
    return el as HTMLDetailsElement
  }

  /**
   * Whether this node is on screen with the panel collapsed. A closed
   * <details> hides everything it contains except its own <summary>, so
   * that is the whole rule -- outside the details, or inside the summary.
   */
  function shownWhileCollapsed(node: Node | null | undefined): boolean {
    if (!node) return false
    const d = details()
    const summary = d.querySelector('summary')
    return !d.contains(node) || !!summary?.contains(node)
  }

  function warningEl(): Element | null {
    return (
      [...target.querySelectorAll('p, div, section')].filter((el) =>
        el.textContent?.includes(WARNING),
      ).pop() ?? null
    )
  }

  function detectButton(): HTMLButtonElement | null {
    const all = [...target.querySelectorAll('button')] as HTMLButtonElement[]
    return all.find((b) => b.textContent?.trim() === 'Run detection') ?? null
  }

  it('shows the warning without the panel being expanded', () => {
    mountCollapsed([source('src1', 1, STALE)])
    expect(details().open).toBe(false)
    expect(warningEl()).not.toBeNull()
    expect(shownWhileCollapsed(warningEl())).toBe(true)
  })

  it('shows the detect button too, not just the sentence', () => {
    // CLAUDE.md records what happens when this affordance is a text link
    // rather than a button: the reviewer misses it and re-segments instead,
    // which is the exact failure being fixed here. Hiding the button behind
    // the collapse is the same failure by a different route.
    mountCollapsed([source('src1', 1, STALE)])
    expect(shownWhileCollapsed(detectButton())).toBe(true)
    expect(detectButton()?.className).toContain('bg-fg')
    expect(detectButton()?.className).toContain('text-bg')
  })

  it('costs no /scores fetch to render -- staleness comes from the source prop alone', () => {
    // The collapse exists to avoid parsing the whole of features.jsonl on
    // every session load. Surfacing the warning must not hand that cost back.
    mountCollapsed([source('src1', 1, STALE)])
    expect(warningEl()).not.toBeNull()
    expect(mockApi.scores).not.toHaveBeenCalled()
  })

  it('renders nothing at all when the features are newer than the region', () => {
    mountCollapsed([source('src1', 1, FRESH)])
    expect(target.textContent).not.toContain('Play region changed')
    expect(detectButton()).toBeNull()
  })

  it('stays quiet on a library with no timestamps, collapsed or not', () => {
    // Every row predating migration 014 looks like this. Unknown is not a
    // reason to nag, and moving the warning out of the collapse would make
    // a false positive permanently visible rather than merely findable.
    mountCollapsed([source('src1', 1)])
    expect(target.textContent).not.toContain('Play region changed')
  })

  it('warns about every stale source, not only the selected one', () => {
    // The select that picks a source is itself inside the collapse, so a
    // warning outside it cannot be scoped by that selection without going
    // silent again on exactly the sources nobody has picked yet.
    mountCollapsed([source('src1', 1, FRESH), source('src2', 2, STALE)])
    expect(shownWhileCollapsed(warningEl())).toBe(true)
    expect(target.textContent).toContain('source 2')
  })

  it('names each stale source and gives each its own detect', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mountCollapsed([source('src1', 1, STALE), source('src2', 2, STALE)])
    const buttons = [...target.querySelectorAll('button')].filter(
      (b) => b.textContent?.trim() === 'Run detection',
    ) as HTMLButtonElement[]
    expect(buttons.length).toBe(2)
    expect(target.textContent).toContain('source 1')
    expect(target.textContent).toContain('source 2')

    // The second button must queue the second source, not whatever the
    // (hidden) selector happens to hold.
    buttons[1].click()
    flushSync()
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(mockApi.detectSource).toHaveBeenCalledWith('src2')
  })

  it('queues detection from the collapsed panel, once confirmed', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mountCollapsed([source('src1', 1, STALE)])
    detectButton()?.click()
    flushSync()

    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(mockApi.detectSource).toHaveBeenCalledWith('src1')
  })

  it('says it once, not once per place it could have gone', () => {
    // Above the collapse *and* inside it would be the same sentence twice,
    // and two identical detect buttons, on an expanded panel.
    mountCollapsed([source('src1', 1, STALE)])
    const d = details()
    d.open = true
    d.dispatchEvent(new Event('toggle'))
    flushSync()

    expect((target.textContent ?? '').split(WARNING).length - 1).toBe(1)
    const buttons = [...target.querySelectorAll('button')].filter(
      (b) => b.textContent?.trim() === 'Run detection',
    )
    expect(buttons.length).toBe(1)
  })
})


// The reported bug: the reviewer re-segmented source 2026-09-16/01 at 0.15,
// closed the app, reopened it, and the slider read 0.25. Nothing stored the
// number -- the panel seeded itself from /scores, which answers with the
// per-source PROFILE DEFAULT, so the readout was a claim about the detector
// dressed up as a claim about the rallies underneath it. That source now
// holds rallies with confidence down to 0.176; the label above them said
// 0.25.
//
// These assert on the slider's value and on which arguments /scores is
// called with, not on anything visual: jsdom has no layout, and the value is
// the thing that was wrong anyway.
describe('ResegmentPanel threshold seeding', () => {
  let target: HTMLDivElement
  let instance: unknown

  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.scores.mockResolvedValue({ step_ms: 200, threshold: 0.25, scores: [0.1, 0.9] })
    target = document.createElement('div')
    document.body.appendChild(target)
  })

  afterEach(() => {
    if (instance) unmount(instance as never)
    target.remove()
    instance = undefined
    vi.restoreAllMocks()
  })

  async function open(sources: Source[]) {
    instance = mount(ResegmentPanel, {
      target,
      props: { sources, rallies: [rally()], onresegmented: vi.fn() },
    })
    flushSync()
    const details = target.querySelector('details')
    if (!details) throw new Error('panel details not found')
    details.open = true
    details.dispatchEvent(new Event('toggle'))
    flushSync()
    await vi.waitFor(() => expect(mockApi.scores).toHaveBeenCalled())
    flushSync()
  }

  function slider(): HTMLInputElement {
    const el = target.querySelector('input[type="range"]')
    if (!el) throw new Error('threshold slider not found')
    return el as HTMLInputElement
  }

  it('opens on the threshold the source was cut at, not the profile default', async () => {
    await open([source('src1', 1, { segment_threshold: 0.15 })])
    expect(slider().value).toBe('0.15')
  })

  it('asks /scores for that same threshold, so the curve draws its line there', async () => {
    // Passing it explicitly also stops loadScores adopting the echoed
    // profile default -- it only does that when asked to resolve one
    // (th === null), which is exactly what must not happen here.
    await open([source('src1', 1, { segment_threshold: 0.15 })])
    expect(mockApi.scores).toHaveBeenCalledWith('src1', 0.15)
  })

  it('falls back to the profile default when nothing was recorded', async () => {
    // Every source segmented before migration 015 looks like this, and so
    // does one that has never been detected. Unknown is not 0.25; it is a
    // question for /scores, the way it always was.
    await open([source('src1', 1, { segment_threshold: null })])
    expect(mockApi.scores).toHaveBeenCalledWith('src1', undefined)
    expect(slider().value).toBe('0.25')
  })

  it('never renders an unrecorded threshold as a number before /scores answers', async () => {
    // The null window. The readout says so and the slider stays disabled --
    // a number here would be invented, which is the bug in miniature.
    let resolve!: (v: { step_ms: number; threshold: number; scores: number[] }) => void
    mockApi.scores.mockImplementation(() => new Promise((r) => (resolve = r)))
    instance = mount(ResegmentPanel, {
      target,
      props: {
        sources: [source('src1', 1, { segment_threshold: null })],
        rallies: [rally()],
        onresegmented: vi.fn(),
      },
    })
    flushSync()
    const details = target.querySelector('details') as HTMLDetailsElement
    details.open = true
    details.dispatchEvent(new Event('toggle'))
    flushSync()

    expect(slider().disabled).toBe(true)
    expect(target.textContent).toContain('…')

    resolve({ step_ms: 200, threshold: 0.25, scores: [0.1, 0.9] })
    await vi.waitFor(() => expect(slider().disabled).toBe(false))
  })

  it('seeds a recorded threshold with no null window at all', async () => {
    // A recorded value needs no round trip, so the slider is live on the
    // first frame -- there is nothing to wait for.
    mockApi.scores.mockImplementation(() => new Promise(() => {}))
    instance = mount(ResegmentPanel, {
      target,
      props: {
        sources: [source('src1', 1, { segment_threshold: 0.15 })],
        rallies: [rally()],
        onresegmented: vi.fn(),
      },
    })
    flushSync()
    const details = target.querySelector('details') as HTMLDetailsElement
    details.open = true
    details.dispatchEvent(new Event('toggle'))
    flushSync()

    expect(slider().disabled).toBe(false)
    expect(slider().value).toBe('0.15')
  })

  it('re-seeds from the newly selected source, per source', async () => {
    // The two profiles' thresholds are on different scales, and so are two
    // sources' recorded values. Carrying one across a switch would state the
    // wrong number about the new source's rallies.
    await open([
      source('src1', 1, { segment_threshold: 0.15 }),
      source('src2', 2, { segment_threshold: 0.4 }),
    ])
    expect(slider().value).toBe('0.15')

    const select = target.querySelector('select') as HTMLSelectElement
    select.value = 'src2'
    select.dispatchEvent(new Event('change', { bubbles: true }))
    flushSync()

    expect(slider().value).toBe('0.4')
    expect(mockApi.scores).toHaveBeenCalledWith('src2', 0.4)
  })

  it('tells the reviewer, in words, what the rallies on screen were cut at', async () => {
    await open([source('src1', 1, { segment_threshold: 0.15 })])
    expect(target.textContent).toContain('cut at')
    expect(target.textContent).toContain('0.15')
  })

  it('says the threshold is unknown rather than naming one, when it is', async () => {
    await open([source('src1', 1, { segment_threshold: null })])
    expect(target.textContent).toContain('before')
    expect(target.textContent).not.toContain('cut at')
  })

  it('renders the recorded number in font-data, like every other figure', async () => {
    // A threshold is a number that sits beside a moving readout; the mono
    // role carries tabular-nums so neither jitters (CLAUDE.md, Type).
    await open([source('src1', 1, { segment_threshold: 0.15 })])
    const mono = [...target.querySelectorAll('.font-data')]
    expect(mono.some((el) => el.textContent?.trim() === '0.15')).toBe(true)
  })
})
