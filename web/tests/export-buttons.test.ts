import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Rally, SessionDetail } from '../src/lib/types'

const mockApi = {
  listSessions: vi.fn(),
  getSession: vi.fn(),
  star: vi.fn().mockResolvedValue({ ok: true }),
  reject: vi.fn().mockResolvedValue({ ok: true }),
  reviewed: vi.fn().mockResolvedValue({ ok: true }),
  setBounds: vi.fn().mockResolvedValue({ ok: true }),
  resegment: vi.fn(),
  label: vi.fn().mockResolvedValue({ ok: true }),
  sourceLabels: vi.fn().mockResolvedValue([]),
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
  exportClips: vi.fn().mockResolvedValue({ queued: 2, already_cut: 0, in_flight: 0, unavailable: 0, total: 2 }),
}

vi.mock('../src/lib/api', () => ({ api: mockApi }))

// jsdom has no real media pipeline; every real browser's play() returns a
// Promise and VideoDeck relies on that guarantee.
HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined)
HTMLMediaElement.prototype.pause = vi.fn()
HTMLMediaElement.prototype.load = vi.fn()

const { default: SessionHarness } = await import('./support/SessionHarness.svelte')

function rally(id: string, idx: number, overrides: Partial<Rally> = {}): Rally {
  return {
    id,
    session_id: 's1',
    source_id: 'src1',
    idx,
    start_ms: idx * 10000,
    end_ms: idx * 10000 + 8000,
    det_start_ms: idx * 10000,
    det_end_ms: idx * 10000 + 8000,
    confidence: 0.9,
    starred: 0,
    rejected: 0,
    point: 0,
    reviewed_at: null,
    note: '',
    ...overrides,
  }
}

function detailWith(rallies: Rally[]): SessionDetail {
  return {
    session: { id: 's1', title: 'test session', played_on: '2026-08-19', status: 'ready' },
    sources: [
      {
        id: 'src1',
        session_id: 's1',
        idx: 1,
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
      },
    ],
    rallies,
  }
}

describe('the reviewed panel exports clips', () => {
  let target: HTMLDivElement
  let instance: unknown

  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.exportClips.mockResolvedValue({
      queued: 2, already_cut: 0, in_flight: 0, unavailable: 0, total: 2,
    })
    target = document.createElement('div')
    document.body.appendChild(target)
  })

  afterEach(() => {
    if (instance) unmount(instance as never)
    target.remove()
    instance = undefined
  })

  function reviewed() {
    // Every rally already judged, so QueueController.current is undefined and
    // the finished branch renders -- the panel that has been a dead end since
    // the review UI was built.
    return detailWith([
      rally('r1', 1, { point: 1, reviewed_at: '2026-08-19T11:00:00Z' }),
      rally('r2', 2, { point: 1, reviewed_at: '2026-08-19T11:01:00Z' }),
      rally('r3', 3, { starred: 1, reviewed_at: '2026-08-19T11:02:00Z' }),
    ])
  }

  function button(label: RegExp): HTMLButtonElement {
    const el = Array.from(target.querySelectorAll('button')).find((b) =>
      label.test(b.textContent ?? ''),
    )
    if (!el) throw new Error(`no button matching ${label}`)
    return el as HTMLButtonElement
  }

  it('shows both sets with their counts', async () => {
    mockApi.getSession.mockResolvedValue(reviewed())
    instance = mount(SessionHarness, { target })
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/Session reviewed/))

    expect(button(/point clips/i).textContent).toMatch(/2/)
    expect(button(/starred clips/i).textContent).toMatch(/1/)
  })

  it('exports the set the button names', async () => {
    mockApi.getSession.mockResolvedValue(reviewed())
    instance = mount(SessionHarness, { target })
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/Session reviewed/))

    button(/point clips/i).click()
    await vi.waitFor(() => expect(mockApi.exportClips).toHaveBeenCalled())
    expect(mockApi.exportClips).toHaveBeenCalledWith('s1', 'points')
  })

  it('reports already-cut so a second press does not look broken', async () => {
    mockApi.getSession.mockResolvedValue(reviewed())
    mockApi.exportClips.mockResolvedValue({
      queued: 0, already_cut: 2, in_flight: 0, unavailable: 0, total: 2,
    })
    instance = mount(SessionHarness, { target })
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/Session reviewed/))

    button(/point clips/i).click()
    // Without this the second press is indistinguishable from a dead button.
    await vi.waitFor(() => expect(target.textContent).toMatch(/already cut/i))
  })

  it('reports in-flight separately so a second press mid-encode is honest', async () => {
    // Encoding is still running from the first press -- these spans are not
    // "already cut", and must not be reported as if they were.
    mockApi.getSession.mockResolvedValue(reviewed())
    mockApi.exportClips.mockResolvedValue({
      queued: 0, already_cut: 0, in_flight: 2, unavailable: 0, total: 2,
    })
    instance = mount(SessionHarness, { target })
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/Session reviewed/))

    button(/point clips/i).click()
    await vi.waitFor(() => expect(target.textContent).toMatch(/in flight/i))
  })

  it('disables a set with nothing in it', async () => {
    mockApi.getSession.mockResolvedValue(
      detailWith([rally('r1', 1, { point: 1, reviewed_at: '2026-08-19T11:00:00Z' })]),
    )
    instance = mount(SessionHarness, { target })
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/Session reviewed/))
    expect(button(/starred clips/i).disabled).toBe(true)
  })
})
