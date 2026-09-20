import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Rally, SessionDetail, Source } from '../src/lib/types'

// Same mocking approach as timeline-mode-threshold.test.ts: TimelineMode
// calls through `../lib/api` directly, so the module is mocked rather than
// passed in as a prop.
const mockApi = {
  setBounds: vi.fn().mockResolvedValue({ ok: true }),
  scores: vi.fn().mockResolvedValue({ step_ms: 200, threshold: 0.45, scores: [0.1, 0.9] }),
  createRally: vi.fn().mockResolvedValue('r-new'),
  splitRally: vi.fn(),
  mergeRally: vi.fn(),
  proxyUrl: () => 'about:blank',
  frameUrl: () => 'about:blank',
}

vi.mock('../src/lib/api', () => ({ api: mockApi }))

// jsdom compatibility shims. play/pause/load are unimplemented, same
// rationale as timeline-drag-gain.test.ts. ResizeObserver is needed on top
// of those because this file is the first to mount SourceScrub, whose
// `bind:clientWidth` Svelte 5 implements with one -- a no-op is enough,
// since jsdom reports 0 for every measured width anyway and nothing here
// asserts on the bar's geometry.
HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined)
HTMLMediaElement.prototype.pause = vi.fn()
HTMLMediaElement.prototype.load = vi.fn()
vi.stubGlobal(
  'ResizeObserver',
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
)

const { default: TimelineMode } = await import('../src/components/TimelineMode.svelte')

function source(id: string, idx: number, durationMs: number): Source {
  return {
    id,
    session_id: 's1',
    idx,
    recorded_at: '2026-08-19T10:00:00Z',
    offset_ms: 0,
    duration_ms: durationMs,
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
  }
}

function rally(id: string, idx: number, sourceId: string): Rally {
  return {
    id,
    session_id: 's1',
    source_id: sourceId,
    idx,
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
    winner: '',
  }
}

// Two sources of very different length in one session. The length gap is
// load-bearing for the first test: a span drawn against the short source
// sits well inside the long one, so the server would accept it and the
// wrong-source rally would exist rather than 400.
function twoSourceDetail(): SessionDetail {
  return {
    session: { id: 's1', title: 'test session', played_on: '2026-08-19', status: 'ready', scoring: null },
    sources: [source('src1', 1, 300000), source('src2', 2, 1800000)],
    rallies: [rally('r1', 1, 'src1'), rally('r2', 2, 'src2')],
  }
}

function press(key: string): void {
  window.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }))
  flushSync()
}

describe('an open add is bound to the source it was drawn against', () => {
  let target: HTMLDivElement
  let instance: unknown

  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.scores.mockResolvedValue({ step_ms: 200, threshold: 0.45, scores: [0.1, 0.9] })
    mockApi.createRally.mockResolvedValue('r-new')
    target = document.createElement('div')
    document.body.appendChild(target)
  })

  afterEach(() => {
    if (instance) unmount(instance as never)
    target.remove()
    instance = undefined
  })

  function open(rallyId = 'r1') {
    instance = mount(TimelineMode, {
      target,
      props: { detail: twoSourceDetail(), rallyId, onclose: vi.fn() },
    })
    flushSync()
  }

  function draftPanel(): Element | null {
    // The add panel is the one surface that says a rally is being made.
    return [...target.querySelectorAll('p')].find(
      (p) => p.textContent?.trim() === 'Adding a rally the detector missed',
    ) ?? null
  }

  it('refuses an overview-band pick while a draft is open', () => {
    // OverviewBand spans the WHOLE session, so its picks cross sources.
    // `rally` derives from the picked id and `source` from `rally`, so an
    // ungated pick re-points every source-shaped thing the add depends on
    // -- the scrub's duration, the deck, and the id the commit POSTs to --
    // underneath a span the reviewer drew against a different file.
    open('r1')
    press('n')
    expect(draftPanel()).not.toBeNull()

    const rally2 = target.querySelector('[aria-label="rally 2"]') as HTMLButtonElement
    rally2.click()
    flushSync()

    // Still on r1: the pick is refused the way `C` and `U` already refuse
    // during an add, because the draft is the only copy of a span the
    // reviewer picked by eye.
    expect(target.textContent).toContain('rally 1 ·')
    expect(draftPanel()).not.toBeNull()
  })

  it('commits against the source the span was drawn on, never the focused one', async () => {
    open('r1')
    press('n')
    const rally2 = target.querySelector('[aria-label="rally 2"]') as HTMLButtonElement
    rally2.click()
    flushSync()
    press('Enter')
    await vi.waitFor(() => expect(mockApi.createRally).toHaveBeenCalled())

    // The span was measured against src1's five minutes. src2 is half an
    // hour long, so the server accepts it happily and the library gains a
    // rally on the wrong source at meaningless timestamps.
    expect(mockApi.createRally.mock.calls[0][0]).toBe('src1')
  })
})

describe('committing an add is not re-entrant', () => {
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
  })

  it('holding Enter posts the span once, not once per key repeat', async () => {
    // `draft` is cleared only AFTER the await, so every repeat that lands
    // during the round trip sees an open draft and fires its own POST. D8
    // deliberately removed the server-side collision check -- overlap
    // between rallies is allowed by design -- so all of them succeed and
    // the source gains a pile of identical rallies.
    let release!: (id: string) => void
    mockApi.createRally.mockReturnValue(
      new Promise<string>((resolve) => {
        release = resolve
      }),
    )

    instance = mount(TimelineMode, {
      target,
      props: { detail: twoSourceDetail(), rallyId: 'r1', onclose: vi.fn() },
    })
    flushSync()

    press('n')
    press('Enter')
    press('Enter')
    press('Enter')

    expect(mockApi.createRally).toHaveBeenCalledTimes(1)

    release('r-new')
    await vi.waitFor(() => expect(target.textContent).toContain('Added a rally'))
  })

  it('a failed commit releases the guard so the reviewer can retry', async () => {
    mockApi.createRally.mockRejectedValueOnce(new Error('offline'))
    vi.spyOn(console, 'error').mockImplementation(() => {})

    instance = mount(TimelineMode, {
      target,
      props: { detail: twoSourceDetail(), rallyId: 'r1', onclose: vi.fn() },
    })
    flushSync()

    press('n')
    press('Enter')
    await vi.waitFor(() => expect(target.textContent).toContain('Could not add this rally'))

    // The draft is deliberately still open on the failure path, so a retry
    // has to be possible -- a guard that leaked would strand it there.
    mockApi.createRally.mockResolvedValue('r-new')
    press('Enter')
    await vi.waitFor(() => expect(mockApi.createRally).toHaveBeenCalledTimes(2))
  })
})
