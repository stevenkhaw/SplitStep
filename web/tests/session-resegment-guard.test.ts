import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Rally, SessionDetail, Source } from '../src/lib/types'

// Same mocking approach as requeue-on-detail-swap.test.ts /
// editable-target-guard.test.ts: Session.svelte and everything it renders
// calls through `../lib/api` directly.
const mockApi = {
  listSessions: vi.fn(),
  getSession: vi.fn(),
  star: vi.fn().mockResolvedValue({ ok: true }),
  reject: vi.fn().mockResolvedValue({ ok: true }),
  reviewed: vi.fn().mockResolvedValue({ ok: true }),
  setBounds: vi.fn().mockResolvedValue({ ok: true }),
  resegment: vi.fn(),
  scores: vi.fn().mockResolvedValue({ step_ms: 200, threshold: 0.45, scores: [] }),
  listPresets: vi.fn().mockResolvedValue([]),
  createPreset: vi.fn().mockResolvedValue({ id: 'preset1' }),
  setPreset: vi.fn().mockResolvedValue({ ok: true }),
  jobs: vi.fn().mockResolvedValue([]),
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

const { default: Session } = await import('../src/routes/Session.svelte')
const { default: ResegmentPanel } = await import('../src/components/ResegmentPanel.svelte')

function source(id: string, idx: number, status: 'needs_setup' | 'ready'): Source {
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
    status,
    rotation_deg: 0,
  }
}

function rally(id: string, idx: number, sourceId: string): Rally {
  return {
    id,
    session_id: 's1',
    source_id: sourceId,
    idx,
    start_ms: idx * 1000,
    end_ms: idx * 1000 + 500,
    det_start_ms: idx * 1000,
    det_end_ms: idx * 1000 + 500,
    confidence: 0.9,
    starred: 0,
    rejected: 0,
    reviewed_at: null,
  }
}

// The guard lives at the caller, not inside ResegmentPanel: Session.svelte
// filters `detail.sources` down to `readySources` before ever handing them
// to ResegmentPanel (see Session.svelte's comment above its
// <ResegmentPanel> -- a needs_setup source has no features.jsonl on disk,
// so api.scores() on one is a guaranteed failure). ResegmentPanel itself
// applies no such filter to its own `sources` prop; it just defaults
// `sourceId` to `sources[0]?.id` and fetches scores for whatever it's
// given. These tests pin the caller's filtering, not a defense inside the
// panel.
describe('Session filters sources before handing them to ResegmentPanel', () => {
  let target: HTMLDivElement
  let instance: unknown

  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.scores.mockResolvedValue({ step_ms: 200, threshold: 0.45, scores: [] })
    target = document.createElement('div')
    document.body.appendChild(target)
  })

  afterEach(() => {
    if (instance) unmount(instance as never)
    target.remove()
    instance = undefined
  })

  it('calls scores() only with a ready source id, never a needs_setup source id', async () => {
    // needs_setup listed FIRST: if Session ever regressed to passing the
    // raw (unfiltered) `sources` array through, ResegmentPanel's
    // `sources[0]?.id` default would pick this one, and the assertion below
    // would catch it.
    mockApi.getSession.mockResolvedValue({
      session: { id: 's1', title: 'Mixed Session', played_on: '2026-08-19', status: 'needs_setup' },
      sources: [source('src-setup', 1, 'needs_setup'), source('src-ready', 2, 'ready')],
      rallies: [rally('r1', 1, 'src-ready')],
    } as SessionDetail)

    instance = mount(Session, { target, props: { id: 's1' } })
    flushSync()

    await vi.waitFor(() => expect(mockApi.scores).toHaveBeenCalled())

    expect(mockApi.scores).toHaveBeenCalledWith('src-ready', expect.any(Number))
    expect(mockApi.scores).not.toHaveBeenCalledWith('src-setup', expect.any(Number))
  })

  it('renders no ResegmentPanel and never calls scores() when every source needs setup', async () => {
    mockApi.getSession.mockResolvedValue({
      session: { id: 's1', title: 'Setup Session', played_on: '2026-08-19', status: 'needs_setup' },
      sources: [source('src-setup', 1, 'needs_setup')],
      rallies: [],
    } as SessionDetail)

    instance = mount(Session, { target, props: { id: 's1' } })
    flushSync()

    await vi.waitFor(() => expect(target.textContent).toContain('Set up source 1'))

    expect(mockApi.scores).not.toHaveBeenCalled()
  })
})

describe('ResegmentPanel handles an empty sources prop gracefully', () => {
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

  it('does not call scores() and still renders when sources is empty', async () => {
    instance = mount(ResegmentPanel, {
      target,
      props: {
        sources: [],
        rallies: [],
        onresegmented: vi.fn(),
      },
    })
    flushSync()

    expect(mockApi.scores).not.toHaveBeenCalled()
    expect(target.textContent).toContain('Re-segment')
  })
})
