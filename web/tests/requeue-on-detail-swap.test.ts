import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Rally, SessionDetail } from '../src/lib/types'

// Session.svelte pulls in QueueMode/TimelineMode/VideoDeck, all of which
// call through `api`. Mocking the module (rather than passing a fake object
// as a prop -- nothing here accepts `api` as a prop) is what lets this test
// mount the real component tree without hitting a real network or a real
// <video> element's unimplemented-in-jsdom playback.
const mockApi = {
  listSessions: vi.fn(),
  getSession: vi.fn(),
  star: vi.fn().mockResolvedValue({ ok: true }),
  reject: vi.fn().mockResolvedValue({ ok: true }),
  reviewed: vi.fn().mockResolvedValue({ ok: true }),
  setBounds: vi.fn().mockResolvedValue({ ok: true }),
  resegment: vi.fn(),
  scores: vi.fn().mockResolvedValue({ step_ms: 200, threshold: 0.45, scores: [] }),
  // QuadEditor (Task 14) fetches presets unconditionally on mount, just
  // like ResegmentPanel fetches scores -- needs a resolved Promise, not
  // just a spy, or mounting Session.svelte throws (`.then` of undefined).
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

// jsdom has no real media pipeline: HTMLMediaElement.prototype.play/pause
// are literally unimplemented there (they log "Not implemented" and return
// `undefined`), whereas every real browser's `.play()` always returns a
// Promise. VideoDeck relies on that spec guarantee (`el.play().then(...)`),
// correctly -- this stub is a jsdom compatibility shim for the test
// environment, not a workaround for a bug in the component.
HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined)
HTMLMediaElement.prototype.pause = vi.fn()
HTMLMediaElement.prototype.load = vi.fn()

// Dynamic import AFTER vi.mock so the mocked module is what Session.svelte
// (and everything it imports) resolves `../lib/api` to.
const { default: SessionHarness } = await import('./support/SessionHarness.svelte')

function rally(id: string, idx: number, sourceId = 'src1'): Rally {
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

function detailWith(rallies: Rally[]): SessionDetail {
  return {
    session: {
      id: 's1',
      title: 'test session',
      played_on: '2026-08-19',
      status: 'ready',
    },
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

describe('QueueMode reflects a replaced rally set (P2 Task 11/12 fix)', () => {
  let target: HTMLDivElement
  // biome-ignore-next-line: instance typing intentionally loose -- see setId() cast below
  let instance: { setId: (id: string) => void } | undefined

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

  it('shows the new rally count after `detail` is replaced on a mounted Session', async () => {
    const original = detailWith([rally('r1', 1), rally('r2', 2)])
    const resegmented = detailWith([rally('r9', 1), rally('r10', 2), rally('r11', 3)])
    mockApi.getSession.mockResolvedValueOnce(original).mockResolvedValueOnce(resegmented)

    instance = mount(SessionHarness, { target }) as unknown as { setId: (id: string) => void }
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/rally 1 \/ 2/))

    // Session.svelte's own effect re-fetches whenever its `id` prop changes
    // and does `detail = d` with whatever comes back -- the exact
    // `detail`-swap-on-a-mounted-instance shape Task 13's re-segment panel
    // will trigger (`onresegmented={() => api.getSession(id).then((d) =>
    // (detail = d))}`). This harness flips `id` rather than calling that
    // exact callback because nothing in the current app exposes a hook to
    // do so yet (ResegmentPanel is Task 13's deliverable) -- but the code
    // path inside Session.svelte that must remount QueueMode is identical
    // either way: a plain `detail = <new SessionDetail>` assignment.
    instance.setId('s2')
    flushSync()

    await vi.waitFor(() => expect(target.textContent).toMatch(/rally 1 \/ 3/))
    expect(target.textContent).not.toMatch(/rally 1 \/ 2/)
  })

  it('does NOT remount (queue position survives) when detail.rallies is untouched', async () => {
    const original = detailWith([rally('r1', 1), rally('r2', 2), rally('r3', 3)])
    mockApi.getSession.mockResolvedValueOnce(original)

    instance = mount(SessionHarness, { target }) as unknown as { setId: (id: string) => void }
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/rally 1 \/ 3/))

    // Advance the queue (skip past rally 1) without touching `detail` at
    // all -- re-rendering Session.svelte for an unrelated reason (its own
    // `{#key detail.rallies}` re-evaluating) must not reset a position the
    // rally set itself never changed.
    // <svelte:window onkeydown> attaches directly to `window`, so dispatch
    // there rather than relying on bubbling from a DOM element.
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight' }))
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/rally 2 \/ 3/))
  })
})
