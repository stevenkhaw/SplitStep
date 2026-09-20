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
  seen: vi.fn().mockResolvedValue({ ok: true }),
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
  getSessionClips: vi.fn().mockResolvedValue([]),
  revealClip: vi.fn().mockResolvedValue({ ok: true }),
}

vi.mock('../src/lib/api', () => ({ api: mockApi }))

HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined)
HTMLMediaElement.prototype.pause = vi.fn()
HTMLMediaElement.prototype.load = vi.fn()

const { default: Session } = await import('../src/routes/Session.svelte')

function source(
  id: string,
  idx: number,
  status: 'needs_setup' | 'ready',
  overrides: Partial<Source> = {},
): Source {
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
    features_at: null,
    preset_assigned_at: null,
    segment_threshold: null,
    ...overrides,
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
    point: 0,
    reviewed_at: null,
    seen_at: null,
    note: '',
    winner: '',
  }
}

// The guard lives at the caller, not inside DetectionPanel: Session.svelte
// filters `detail.sources` down to `readySources` before rendering a panel
// for each one (see Session.svelte's comment above the `{#each}`). A
// needs_setup source has no proxy and no features.jsonl on disk, so there
// is no region to show against it and api.scores() on one is a guaranteed
// failure. DetectionPanel itself applies no such filter -- it renders
// whichever single source it is handed -- so these tests pin the caller's
// filtering, not a defense inside the panel.
describe('Session filters sources before handing them to DetectionPanel', () => {
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

  // Every <details> on the page: QuadEditor's, and each Detection panel's
  // threshold section, whose /scores effect is gated on being open.
  // `open` + a hand-dispatched toggle rather than clicking <summary>, since
  // jsdom fires the real toggle asynchronously and it would race flushSync.
  function expandPanels() {
    for (const details of target.querySelectorAll('details')) {
      details.open = true
      details.dispatchEvent(new Event('toggle'))
    }
    flushSync()
  }

  function detectionPanels(): Element[] {
    return [...target.querySelectorAll('section[aria-label^="detection for source"]')]
  }

  it('renders a detection panel for the ready source and none for the needs_setup one', async () => {
    // needs_setup listed FIRST: a regression to passing the raw, unfiltered
    // `sources` array would render a panel for it, and its own heading
    // names the source, so the assertion below catches which one.
    mockApi.getSession.mockResolvedValue({
      session: { id: 's1', title: 'Mixed Session', played_on: '2026-08-19', status: 'needs_setup', scoring: null },
      sources: [
        source('src-setup', 1, 'needs_setup'),
        source('src-ready', 2, 'ready', { features_at: '2026-09-16T03:29:00+00:00' }),
      ],
      rallies: [rally('r1', 1, 'src-ready')],
    } as SessionDetail)

    instance = mount(Session, { target, props: { id: 's1' } })
    flushSync()

    await vi.waitFor(() => expect(detectionPanels().length).toBe(1))
    expect(detectionPanels()[0].getAttribute('aria-label')).toBe('detection for source 2')
  })

  it('calls scores() only with a ready source id, never a needs_setup source id', async () => {
    mockApi.getSession.mockResolvedValue({
      session: { id: 's1', title: 'Mixed Session', played_on: '2026-08-19', status: 'needs_setup', scoring: null },
      sources: [
        source('src-setup', 1, 'needs_setup'),
        source('src-ready', 2, 'ready', { features_at: '2026-09-16T03:29:00+00:00' }),
      ],
      rallies: [rally('r1', 1, 'src-ready')],
    } as SessionDetail)

    instance = mount(Session, { target, props: { id: 's1' } })
    flushSync()

    await vi.waitFor(() => expect(detectionPanels().length).toBe(1))
    expandPanels()
    await vi.waitFor(() => expect(mockApi.scores).toHaveBeenCalled())

    // No threshold argument: this source recorded none (migration 015
    // predates it), so the panel asks the API to resolve that source's own
    // profile default. The id is what this test pins, not the argument.
    expect(mockApi.scores).toHaveBeenCalledWith('src-ready', undefined)
    expect(mockApi.scores).not.toHaveBeenCalledWith('src-setup', undefined)
  })

  it('renders no detection panel and never calls scores() when every source needs setup', async () => {
    mockApi.getSession.mockResolvedValue({
      session: { id: 's1', title: 'Setup Session', played_on: '2026-08-19', status: 'needs_setup', scoring: null },
      sources: [source('src-setup', 1, 'needs_setup')],
      rallies: [],
    } as SessionDetail)

    instance = mount(Session, { target, props: { id: 's1' } })
    flushSync()

    await vi.waitFor(() => expect(target.textContent).toContain('Set up source 1'))
    // Expanding whatever did render keeps this about the filtering rather
    // than about the collapse: there is no detection panel here to open.
    expandPanels()

    expect(detectionPanels().length).toBe(0)
    expect(mockApi.scores).not.toHaveBeenCalled()
  })

  it('never fetches scores for a source whose features do not exist yet', async () => {
    // An `ingested` source -- proxy built, never detected -- is a ready
    // source, so it gets a panel. /scores answers 409 without features, so
    // opening that panel must not ask; the threshold section says why
    // there is no curve instead (NO_CURVE_NOTE).
    mockApi.getSession.mockResolvedValue({
      session: { id: 's1', title: 'Fresh Session', played_on: '2026-08-19', status: 'ready', scoring: null },
      sources: [source('src-fresh', 1, 'ready')],
      rallies: [],
    } as SessionDetail)

    instance = mount(Session, { target, props: { id: 's1' } })
    flushSync()

    await vi.waitFor(() => expect(detectionPanels().length).toBe(1))
    expandPanels()
    await Promise.resolve()
    flushSync()

    expect(mockApi.scores).not.toHaveBeenCalled()
    expect(target.textContent).toContain('No score curve yet')
  })
})
