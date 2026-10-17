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
  setChoices: vi.fn().mockResolvedValue({ ok: true }),
  detectSource: vi.fn(),
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


// The hand check that found this: on 2026-10-12, Save corners answered
// "Saved." and the server held Fence + Singles with no boxes, yet neither
// QuadEditor's follow-up nor DetectionPanel moved until a full reload.
describe('Session after a mount/format/corners save', () => {
  let target: HTMLDivElement
  let instance: unknown

  const STORED = '[[0.1,0.9],[0.9,0.9],[0.7,0.4],[0.3,0.4]]'
  const before = source('src1', 1, 'ready', {
    features_at: '2026-10-12T11:00:00+00:00',
    segment_threshold: 0.45,
    mount: '', play_format: '', court_corners: STORED,
    scorer_now: 'auto', detected_scorer: 'pair', has_boxes: false,
  })
  const after = { ...before, mount: 'fence', play_format: 'singles', court_corners: STORED,
                  scorer_now: 'fence', detected_scorer: null } as Source
  const payload = (s: Source) => ({
    session: { id: 's1', title: 'Fence', played_on: '2026-10-12', status: 'ready', scoring: null },
    sources: [s],
    anchors: [],
    rallies: [rally('r1', 1, 'src1')],
  }) as SessionDetail

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

  it('offers the priced re-run in both surfaces once the refetch lands', async () => {
    mockApi.getSession.mockResolvedValueOnce(payload(before)).mockResolvedValue(payload(after))
    instance = mount(Session, { target, props: { id: 's1' } })
    flushSync()
    await vi.waitFor(() => expect(target.querySelector('details')).not.toBeNull())
    const details = target.querySelector('details')!
    details.open = true
    details.dispatchEvent(new Event('toggle'))
    flushSync()
    const click = (sel: string) => {
      ;(target.querySelector(sel) as HTMLButtonElement).click()
      flushSync()
    }
    // Nothing owed before the save: auto already produced these rallies.
    expect(target.querySelector('[data-role="choices-follow-up"]')).toBeNull()
    click('button[aria-label="mount Fence"]')
    click('button[aria-label="format Singles"]')
    const save = [...target.querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === 'Save corners',
    ) as HTMLButtonElement
    // Corners already stored (drawn before), so Save is live without a drag.
    expect(save.disabled).toBe(false)
    save.click()
    await vi.waitFor(() => expect(mockApi.getSession).toHaveBeenCalledTimes(2))
    await vi.waitFor(() =>
      expect(target.querySelector('[data-role="choices-follow-up"]')).not.toBeNull(),
    )
    const primary = target.querySelector('button[data-role="detection-primary"]')
    expect(primary?.textContent).toMatch(/Run detection · ~15 min/)
  })
})
