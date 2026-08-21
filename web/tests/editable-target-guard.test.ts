import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Rally, SessionDetail } from '../src/lib/types'

// Finding 2 (CRITICAL): typing in any co-mounted input (QuadEditor's
// preset-name field, ResegmentPanel's/TimelineMode's threshold sliders --
// see Session.svelte's comment on why those are deliberately mounted
// alongside whichever mode is active) must not fire QueueMode/TimelineMode's
// single-letter keybindings. Same mocking approach as
// requeue-on-detail-swap.test.ts: Session.svelte and everything it renders
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

function rally(id: string, idx: number): Rally {
  return {
    id,
    session_id: 's1',
    source_id: 'src1',
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

describe('typing into a co-mounted field does not fire queue keybindings (Finding 2)', () => {
  let target: HTMLDivElement
  let instance: unknown

  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.getSession.mockResolvedValue(detailWith([rally('r1', 1), rally('r2', 2)]))
    target = document.createElement('div')
    document.body.appendChild(target)
  })

  afterEach(() => {
    if (instance) unmount(instance as never)
    target.remove()
    instance = undefined
  })

  // Both setup panels below the queue ship collapsed, and QuadEditor renders
  // nothing at all until opened (its frame <img> costs a server-side ffmpeg
  // extraction). The keystroke this test is about only exists once the panel
  // is open, so expand every panel first. `open` + a hand-dispatched toggle
  // rather than clicking <summary>, because jsdom fires the real toggle
  // asynchronously and it would race flushSync.
  function expandPanels() {
    for (const details of target.querySelectorAll('details')) {
      details.open = true
      details.dispatchEvent(new Event('toggle'))
    }
    flushSync()
  }

  function presetNameInput(): HTMLInputElement {
    const el = target.querySelector('input[aria-label="preset name"]')
    if (!el) throw new Error('preset-name input not found')
    return el as HTMLInputElement
  }

  function thresholdSlider(): HTMLInputElement {
    const el = target.querySelector('input[aria-label="detector threshold"]')
    if (!el) throw new Error('threshold slider not found')
    return el as HTMLInputElement
  }

  function keydownOn(el: Element, key: string): boolean {
    // dispatchEvent's return value is false iff some handler called
    // preventDefault() -- this is how we can tell whether the keystroke
    // actually reached the field (e.g. the space bar) rather than being
    // hijacked by QueueMode's window-level handler.
    const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })
    return el.dispatchEvent(event)
  }

  it('lets "Court 1" (including the space) be typed into QuadEditor\'s preset-name field without starring/skipping/undoing/replaying', async () => {
    instance = mount(Session, { target, props: { id: 's1' } })
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/rally 1 \/ 2/))
    expandPanels()

    const input = presetNameInput()
    input.focus()

    // Type "Court 1" one keydown at a time, targeted at the input -- exactly
    // as a real keystroke would bubble from it up to `window`, where
    // QueueMode's <svelte:window onkeydown> listens.
    for (const key of ['C', 'o', 'u', 'r', 't', ' ', '1']) {
      const notPrevented = keydownOn(input, key)
      if (key === ' ') {
        // Before the fix, QueueMode's `case ' '` called preventDefault()
        // unconditionally, so the space bar never reached the field --
        // "Court 1" was literally unenterable. dispatchEvent returning
        // `false` means some handler called preventDefault.
        expect(notPrevented).toBe(true)
      }
      input.value += key
      input.dispatchEvent(new Event('input', { bubbles: true }))
      flushSync()
    }

    expect(input.value).toBe('Court 1')

    // None of the queue mutations 'C', 'o', 'u', 'r', 't', '1' could have
    // triggered (star/reject/skip/undo/replay) actually fired.
    expect(mockApi.star).not.toHaveBeenCalled()
    expect(mockApi.reject).not.toHaveBeenCalled()
    expect(mockApi.reviewed).not.toHaveBeenCalled()
    // 'r' didn't replay -- HTMLMediaElement.play is also called by normal
    // autoplay, so assert on the queue's own visible position instead:
    // undo ('u') would have thrown queue.index off (there was nothing to
    // undo, but a mis-fired star/reject/skip would show here).
    expect(target.textContent).toMatch(/rally 1 \/ 2/)
  })

  it('does not skip/star/reject when ArrowRight/S/X are pressed while focus is in the threshold slider', async () => {
    instance = mount(Session, { target, props: { id: 's1' } })
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/rally 1 \/ 2/))

    const slider = thresholdSlider()
    slider.focus()

    const arrowNotPrevented = keydownOn(slider, 'ArrowRight')
    keydownOn(slider, 's')
    keydownOn(slider, 'x')
    flushSync()

    // Before the fix, QueueMode's `case 'ArrowRight'` called
    // preventDefault() unconditionally and then skipped the current rally
    // -- the slider's own native nudge never happened, and the visible
    // rally silently advanced.
    expect(arrowNotPrevented).toBe(true)
    expect(mockApi.reviewed).not.toHaveBeenCalled()
    expect(mockApi.star).not.toHaveBeenCalled()
    expect(mockApi.reject).not.toHaveBeenCalled()
    expect(target.textContent).toMatch(/rally 1 \/ 2/)
  })

  it('still responds to the same keys once focus leaves the field (the guard is target-scoped, not global)', async () => {
    instance = mount(Session, { target, props: { id: 's1' } })
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/rally 1 \/ 2/))

    // Dispatched with no particular DOM target focused -- bubbles from
    // `target` itself, not from an editable field.
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight' }))
    flushSync()

    // The cursor moving IS the proof the key landed. This used to assert on
    // `mockApi.reviewed`, but a skip no longer persists anything (only star
    // and reject mark a rally reviewed), so that call is not a signal any
    // more -- it would pass whether or not the key was handled.
    await vi.waitFor(() => expect(target.textContent).toMatch(/rally 2 \/ 2/))
    expect(mockApi.reviewed).not.toHaveBeenCalled()
  })
})
