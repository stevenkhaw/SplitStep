import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Rally, ScoreSeries, SessionDetail, Source } from '../src/lib/types'

// Same mocking approach as timeline-drag-gain.test.ts / resegment-panel.test.ts:
// TimelineMode calls through `../lib/api` directly, so the module is mocked
// rather than passed in as a prop.
const mockApi = {
  listSessions: vi.fn(),
  getSession: vi.fn(),
  star: vi.fn(),
  reject: vi.fn(),
  setBounds: vi.fn().mockResolvedValue({ ok: true }),
  resegment: vi.fn(),
  scores: vi.fn(),
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

// jsdom compatibility shim, same rationale as timeline-drag-gain.test.ts:
// HTMLMediaElement.prototype.play/pause/load are unimplemented in jsdom.
HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined)
HTMLMediaElement.prototype.pause = vi.fn()
HTMLMediaElement.prototype.load = vi.fn()

const { default: TimelineMode } = await import('../src/components/TimelineMode.svelte')

function source(id: string, idx: number): Source {
  return {
    id,
    session_id: 's1',
    idx,
    recorded_at: '2026-08-19T10:00:00Z',
    offset_ms: 0,
    duration_ms: 600000, // 10 minutes -- wide enough the 40s zoom window never clamps
    width: 1920,
    height: 1080,
    fps: 30,
    has_original: 1,
    court_preset_id: null,
    status: 'ready',
    rotation_deg: 0,
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
    note: '',
  }
}

// Two sources on different camera-view profiles (src1 pair-mode -> 0.45,
// src2 subject-mode -> 0.25) sharing one session -- the mixed-profile
// scenario the review flagged.
function mixedProfileDetail(): SessionDetail {
  return {
    session: { id: 's1', title: 'test session', played_on: '2026-08-19', status: 'ready' },
    sources: [source('src1', 1), source('src2', 2)],
    rallies: [rally('r1', 1, 'src1'), rally('r2', 2, 'src2')],
  }
}

// Regression coverage for the review's Important 2: neither
// timeline-drag-gain.test.ts nor requeue-on-detail-swap.test.ts (which also
// mount TimelineMode, directly or via Session) assert on scores() call
// arguments, the preview-threshold slider's disabled state, or ScoreCurve
// being gated -- so reverting TimelineMode's half of Task 6 would pass the
// rest of the suite silently. This file pins that half directly, mirroring
// resegment-panel.test.ts's coverage of ResegmentPanel.
describe('TimelineMode reads the segmentation threshold from the API', () => {
  let target: HTMLDivElement
  let instance: unknown

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

  function slider(): HTMLInputElement {
    const el = target.querySelector('[aria-label="preview threshold"]')
    if (!el) throw new Error('preview threshold slider not found')
    return el as HTMLInputElement
  }

  it('omits the threshold on the first /scores call, disables the slider and hides ScoreCurve until it resolves, then adopts the value', async () => {
    let resolveScores: ((v: ScoreSeries) => void) | undefined
    mockApi.scores.mockReturnValue(
      new Promise<ScoreSeries>((resolve) => {
        resolveScores = resolve
      }),
    )

    instance = mount(TimelineMode, {
      target,
      props: { detail: mixedProfileDetail(), rallyId: 'r1', onclose: vi.fn() },
    })
    flushSync()

    expect(mockApi.scores).toHaveBeenCalledWith('src1', undefined)
    // The null window is real: still unresolved, so nothing should show a
    // fabricated number, and the control must not be draggable.
    expect(slider().disabled).toBe(true)
    expect(target.querySelector('svg[role="img"]')).toBeNull() // ScoreCurve not rendered yet

    resolveScores?.({ step_ms: 200, threshold: 0.45, scores: [0.1, 0.9] })
    await vi.waitFor(() => expect(slider().disabled).toBe(false))
    expect(slider().value).toBe('0.45')
    expect(target.querySelector('svg[role="img"]')).not.toBeNull()
  })

  it('resets to null and re-queries without a threshold when the focused rally belongs to a different source', async () => {
    // src1 -> pair-mode default, src2 -> subject-mode default -- picking a
    // rally on src2 must re-resolve src2's own default, not reapply src1's.
    mockApi.scores.mockImplementation((id: string) =>
      Promise.resolve(
        id === 'src1'
          ? { step_ms: 200, threshold: 0.45, scores: [0.1, 0.9] }
          : { step_ms: 200, threshold: 0.25, scores: [0.2, 0.8] },
      ),
    )

    instance = mount(TimelineMode, {
      target,
      props: { detail: mixedProfileDetail(), rallyId: 'r1', onclose: vi.fn() },
    })
    flushSync()
    await vi.waitFor(() => expect(slider().value).toBe('0.45'))

    mockApi.scores.mockClear()
    // OverviewBand renders one button per rally, aria-labelled by idx (see
    // OverviewBand.svelte) -- r2 (idx 2) belongs to src2.
    const rally2 = target.querySelector('[aria-label="rally 2"]') as HTMLButtonElement
    rally2.click()
    flushSync()

    // Synchronous: the reset happens before the mocked async response for
    // src2 has any chance to resolve.
    expect(slider().disabled).toBe(true)
    expect(mockApi.scores).toHaveBeenCalledWith('src2', undefined)

    await vi.waitFor(() => expect(slider().value).toBe('0.25'))
    expect(slider().disabled).toBe(false)
  })

  it('ignores a stale /scores response that resolves after clicking a rally on a different source', async () => {
    // Same race as ResegmentPanel's, reached the way TimelineMode reaches
    // it: OverviewBand lets a reviewer click any rally, including one on a
    // different (and here, shorter-featured) source, mid-flight. /scores'
    // cost scales with features.jsonl's length, so src1's response
    // routinely arrives after src2's even though it was requested first --
    // this is ordinary review behaviour, not a rare interleaving.
    let resolveSrc1!: (v: ScoreSeries) => void
    let resolveSrc2!: (v: ScoreSeries) => void
    mockApi.scores.mockImplementation(
      (id: string) =>
        new Promise<ScoreSeries>((resolve) => {
          if (id === 'src1') resolveSrc1 = resolve
          else resolveSrc2 = resolve
        }),
    )

    instance = mount(TimelineMode, {
      target,
      props: { detail: mixedProfileDetail(), rallyId: 'r1', onclose: vi.fn() },
    })
    flushSync()
    expect(mockApi.scores).toHaveBeenCalledWith('src1', undefined)

    // Click over to src2's rally before src1's (slow) response has arrived.
    const rally2 = target.querySelector('[aria-label="rally 2"]') as HTMLButtonElement
    rally2.click()
    flushSync()
    expect(mockApi.scores).toHaveBeenCalledWith('src2', undefined)

    // src2's response lands first, as it routinely would.
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
})
