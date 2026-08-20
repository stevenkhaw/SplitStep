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
  reviewed: vi.fn(),
  setBounds: vi.fn(),
  resegment: vi.fn(),
  scores: vi.fn().mockResolvedValue({ step_ms: 200, threshold: 0.45, scores: [0.1, 0.9] }),
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

const { default: ResegmentPanel } = await import('../src/components/ResegmentPanel.svelte')

function source(id: string, idx: number): Source {
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
    reviewed_at: null,
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
