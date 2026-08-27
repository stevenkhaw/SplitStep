import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Rally, SessionDetail } from '../src/lib/types'

const mockApi = {
  listSessions: vi.fn(),
  getSession: vi.fn(),
  star: vi.fn().mockResolvedValue({ ok: true }),
  reject: vi.fn().mockResolvedValue({ ok: true }),
  point: vi.fn().mockResolvedValue({ ok: true }),
  seen: vi.fn().mockResolvedValue({ ok: true }),
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
  getSessionClips: vi.fn().mockResolvedValue([]),
  revealClip: vi.fn().mockResolvedValue({ ok: true }),
  exportClips: vi.fn().mockResolvedValue({
    queued: 2, already_cut: 0, in_flight: 0, unavailable: 0, total: 2,
  }),
  createSessionReel: vi.fn(),
}
vi.mock('../src/lib/api', () => ({ api: mockApi }))

const navigate = vi.fn()
vi.mock('../src/lib/router.svelte', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  navigate,
}))

HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined)
HTMLMediaElement.prototype.pause = vi.fn()
HTMLMediaElement.prototype.load = vi.fn()

const { default: SessionHarness } = await import('./support/SessionHarness.svelte')

function rally(id: string, idx: number, overrides: Partial<Rally> = {}): Rally {
  return {
    id, session_id: 's1', source_id: 'src1', idx,
    start_ms: idx * 10000, end_ms: idx * 10000 + 8000,
    det_start_ms: idx * 10000, det_end_ms: idx * 10000 + 8000,
    confidence: 0.9, starred: 0, rejected: 0, point: 0, reviewed_at: null, seen_at: null,
    note: '',
    ...overrides,
  }
}

function reviewed(): SessionDetail {
  return {
    session: { id: 's1', title: 'test session', played_on: '2026-08-19', status: 'ready' },
    sources: [{
      id: 'src1', session_id: 's1', idx: 1, recorded_at: '2026-08-19T10:00:00Z',
      offset_ms: 0, duration_ms: 600000, width: 1920, height: 1080, fps: 30,
      has_original: 1, court_preset_id: null, status: 'ready', rotation_deg: 0,
    }],
    rallies: [
      rally('r1', 1, { point: 1, reviewed_at: '2026-08-19T11:00:00Z', seen_at: '2026-08-19T11:00:00Z' }),
      rally('r2', 2, { point: 1, reviewed_at: '2026-08-19T11:01:00Z', seen_at: '2026-08-19T11:01:00Z' }),
      rally('r3', 3, { starred: 1, reviewed_at: '2026-08-19T11:02:00Z', seen_at: '2026-08-19T11:02:00Z' }),
    ],
  }
}

let target: HTMLDivElement
let instance: unknown

beforeEach(() => {
  vi.clearAllMocks()
  mockApi.jobs.mockResolvedValue([])
  mockApi.createSessionReel.mockResolvedValue({
    slug: '2026-08-19-points', name: '2026-08-19 points',
    added: 2, existing: 0, total: 2,
  })
  target = document.createElement('div')
  document.body.appendChild(target)
})

afterEach(() => {
  if (instance) unmount(instance as never)
  target.remove()
  instance = undefined
})

function button(label: RegExp): HTMLButtonElement {
  const el = Array.from(target.querySelectorAll('button')).find((b) =>
    label.test(b.textContent ?? ''),
  )
  if (!el) throw new Error(`no button matching ${label}`)
  return el as HTMLButtonElement
}

async function openReviewed() {
  mockApi.getSession.mockResolvedValue(reviewed())
  instance = mount(SessionHarness, { target })
  flushSync()
  await vi.waitFor(() => expect(target.textContent).toMatch(/Session reviewed/))
}

describe('the reviewed panel compiles reels', () => {
  it('keeps both export buttons alongside both reel buttons', async () => {
    // §6.2: additions, not replacements. Four actions in two rows.
    await openReviewed()
    expect(button(/Export point clips/i)).toBeTruthy()
    expect(button(/Export starred clips/i)).toBeTruthy()
    expect(button(/Reel of all points/i).textContent).toMatch(/2/)
    expect(button(/Reel of starred/i).textContent).toMatch(/1/)
  })

  it('creates the reel for the set the button names and opens the builder', async () => {
    await openReviewed()
    button(/Reel of all points/i).click()
    await vi.waitFor(() => expect(mockApi.createSessionReel).toHaveBeenCalled())
    expect(mockApi.createSessionReel).toHaveBeenCalledWith('s1', 'points')
    await vi.waitFor(() => expect(navigate).toHaveBeenCalledWith('/reels/2026-08-19-points'))
  })

  it('creating a reel cuts nothing', async () => {
    // The line §6.2 draws: a button labelled "reel" must never start half an
    // hour of encoding. Cutting stays the export buttons' job.
    await openReviewed()
    button(/Reel of starred/i).click()
    await vi.waitFor(() => expect(mockApi.createSessionReel).toHaveBeenCalled())
    expect(mockApi.exportClips).not.toHaveBeenCalled()
  })

  it('disables a reel button whose set is empty', async () => {
    mockApi.getSession.mockResolvedValue({
      ...reviewed(),
      rallies: [rally('r1', 1, { point: 1, reviewed_at: '2026-08-19T11:00:00Z', seen_at: '2026-08-19T11:00:00Z' })],
    })
    instance = mount(SessionHarness, { target })
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/Session reviewed/))
    expect(button(/Reel of starred/i).disabled).toBe(true)
  })

  it('surfaces a failure instead of navigating', async () => {
    await openReviewed()
    mockApi.createSessionReel.mockRejectedValue(new Error('boom'))
    button(/Reel of all points/i).click()
    await vi.waitFor(() => expect(target.textContent).toMatch(/Couldn't build/))
    expect(navigate).not.toHaveBeenCalled()
  })
})
