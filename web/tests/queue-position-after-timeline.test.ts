import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Rally, SessionDetail } from '../src/lib/types'

// Leaving TimelineMode remounts QueueMode (Session bumps `rallyRevision`, and
// a fresh QueueController is the only way a trimmed rally's new bounds reach
// the queue). A remounted controller starts at the first rally whose
// `seen_at` is null -- and `seen_at` is stamped only by star/point/reject/
// skip, never by a bounds edit. So trimming rally 3 and pressing Escape used
// to land the user back on rally 1, having lost their place in a 60-rally
// pass.

const mockApi = {
  listSessions: vi.fn(),
  getSession: vi.fn(),
  star: vi.fn().mockResolvedValue({ ok: true }),
  reject: vi.fn().mockResolvedValue({ ok: true }),
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
    seen_at: null,
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

function press(key: string): void {
  window.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }))
  flushSync()
}

describe('leaving the timeline returns to the rally you were editing', () => {
  let target: HTMLDivElement
  let instance: unknown

  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.scores.mockResolvedValue({ step_ms: 200, threshold: 0.45, scores: [] })
    mockApi.listPresets.mockResolvedValue([])
    mockApi.jobs.mockResolvedValue([])
    mockApi.sourceLabels.mockResolvedValue([])
    target = document.createElement('div')
    document.body.appendChild(target)
  })

  afterEach(() => {
    if (instance) unmount(instance as never)
    target.remove()
    instance = undefined
  })

  it('lands back on the same rally, not the first unreviewed one', async () => {
    mockApi.getSession.mockResolvedValue(
      detailWith([rally('r1', 1), rally('r2', 2), rally('r3', 3)]),
    )

    instance = mount(SessionHarness, { target })
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/rally 1 \/ 3/))

    // Walk to rally 3. Nothing is starred or rejected, so every rally still
    // has reviewed_at === null -- which is exactly the state that makes a
    // remount snap back to rally 1.
    press('ArrowRight')
    press('ArrowRight')
    expect(target.textContent).toMatch(/rally 3 \/ 3/)

    press('t')
    // TimelineMode's own help line -- the queue never renders it.
    await vi.waitFor(() => expect(target.textContent).toMatch(/set in\/out/))

    press('Escape')
    await vi.waitFor(() => expect(target.textContent).toMatch(/rally \d+ \/ 3/))
    expect(target.textContent).toMatch(/rally 3 \/ 3/)
  })

  it('lands back on the same rally after label mode, not the first unreviewed one', async () => {
    // Same defect, different sibling: `mode === 'label'` is its own branch in
    // Session's `{#if}/{:else if}` chain, so switching into it already tears
    // QueueMode down regardless of `{#key rallyRevision}` -- the same
    // mechanism the timeline case above exploits. Label mode never bumps
    // rallyRevision (it has no reason to refetch), so this only passes if
    // queue position is restored via startAtRallyId, the same way
    // openTimeline/closeTimeline restore it.
    mockApi.getSession.mockResolvedValue(
      detailWith([rally('r1', 1), rally('r2', 2), rally('r3', 3)]),
    )

    instance = mount(SessionHarness, { target })
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/rally 1 \/ 3/))

    // Walk to rally 3. Nothing is starred or rejected, so every rally still
    // has reviewed_at === null -- exactly the state that makes a remount
    // snap back to rally 1.
    press('ArrowRight')
    press('ArrowRight')
    expect(target.textContent).toMatch(/rally 3 \/ 3/)

    press('l')
    // LabelMode's own help line -- the queue never renders it. Its
    // sourceLabels() fetch resolves on a microtask, so this also waits out
    // the mount before the second `l` fires.
    await vi.waitFor(() => expect(target.textContent).toMatch(/back to queue/))

    press('l')
    await vi.waitFor(() => expect(target.textContent).toMatch(/rally \d+ \/ 3/))
    expect(target.textContent).toMatch(/rally 3 \/ 3/)
  })

  it('opens label mode from the "Session reviewed" screen, with no current rally', async () => {
    // M3 regression: QueueMode's `l` handler used to require `current`,
    // copied from the `t`/timeline case where a specific rally is needed --
    // but LabelController iterates the full unfiltered rally list and needs
    // no current rally, so the copied guard closed off the only entry point
    // into label mode exactly when a reviewer finishing a pass would most
    // want to open it.
    mockApi.getSession.mockResolvedValue(
      detailWith([
        rally('r1', 1, { seen_at: '2026-08-19T11:00:00Z' }),
        rally('r2', 2, { seen_at: '2026-08-19T11:01:00Z' }),
        rally('r3', 3, { seen_at: '2026-08-19T11:02:00Z' }),
      ]),
    )

    instance = mount(SessionHarness, { target })
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/Session reviewed/))

    press('l')
    // LabelMode's own help line -- only rendered once it has actually
    // mounted, so this proves label mode opened rather than the key doing
    // nothing.
    await vi.waitFor(() => expect(target.textContent).toMatch(/back to queue/))
  })

  it('opens label mode on the rally you had open, not rally 1', async () => {
    // M4 regression: Session's openLabel captured the rally id into
    // focusedRallyId but never threaded it into <LabelMode>, so pressing
    // `l` on rally 3 of 3 opened on rally 1 -- the id was captured and used
    // only for the return trip.
    mockApi.getSession.mockResolvedValue(
      detailWith([rally('r1', 1), rally('r2', 2), rally('r3', 3)]),
    )

    instance = mount(SessionHarness, { target })
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/rally 1 \/ 3/))

    // Walk to rally 3.
    press('ArrowRight')
    press('ArrowRight')
    expect(target.textContent).toMatch(/rally 3 \/ 3/)

    press('l')
    // LabelMode's own position counter ("N / M · K labelled") is a
    // different format from the queue's "rally N / M" line, so this can
    // only match LabelMode's own display, never stale queue text.
    await vi.waitFor(() => expect(target.textContent).toMatch(/3 \/ 3 · 0 labelled/))
  })

  it('still opens on the first unseen rally when the timeline was never used', async () => {
    // The restore must not defeat the normal resume behaviour: with rallies
    // 1 and 2 already seen, a fresh session opens on rally 3.
    mockApi.getSession.mockResolvedValue(
      detailWith([
        rally('r1', 1, { seen_at: '2026-08-19T11:00:00Z' }),
        rally('r2', 2, { seen_at: '2026-08-19T11:01:00Z' }),
        rally('r3', 3),
      ]),
    )

    instance = mount(SessionHarness, { target })
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/rally 3 \/ 3/))
  })
})
