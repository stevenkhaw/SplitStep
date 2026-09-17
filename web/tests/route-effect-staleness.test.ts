import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Rally, SessionDetail } from '../src/lib/types'

// Same mocking shape as requeue-on-detail-swap.test.ts: Session.svelte pulls
// in QueueMode/TimelineMode/VideoDeck/QuadEditor/ResegmentPanel, all of which
// call through `api` on mount, so the module is mocked rather than injected.
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

// jsdom leaves HTMLMediaElement.play/pause unimplemented; VideoDeck relies on
// the spec guarantee that play() returns a Promise. Environment shim, not a
// workaround for a component bug.
HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined)
HTMLMediaElement.prototype.pause = vi.fn()
HTMLMediaElement.prototype.load = vi.fn()

const { default: SessionHarness } = await import('./support/SessionHarness.svelte')
const { default: SetupHarness } = await import('./support/SetupHarness.svelte')

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
    seen_at: null,
    note: '',
    winner: '',
  }
}

function detailWith(rallies: Rally[]): SessionDetail {
  return {
    session: {
      id: 's1',
      title: 'test session',
      played_on: '2026-08-19',
      status: 'ready',
      scoring: null,
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

// A promise whose settlement this test controls, so a *previous* id's fetch
// can be made to resolve after a later one has already landed. Nothing else
// reproduces the out-of-order case: with plain mockResolvedValueOnce the
// microtask queue always drains in call order.
function deferred<T>(): { promise: Promise<T>; resolve: (v: T) => void } {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

// Yields to the macrotask queue, which drains every pending microtask on the
// way. The negative assertions below ("no stale error appeared") need this:
// awaiting the settled promise itself only advances one tick, and Setup's
// handler sits two `.then`s further down its chain -- so a single await would
// let the assertion run before the state it is checking could have been
// written, and the test would pass without proving anything.
function settle(): Promise<void> {
  return new Promise((res) => setTimeout(res, 0))
}

describe('Session.svelte load effect does not carry state across an id change', () => {
  let target: HTMLDivElement
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

  it('clears a previous error once a later id loads successfully', async () => {
    // The template puts `{#if error}` ahead of the detail branch, so an error
    // left over from a *different* session does not merely look wrong -- it
    // hides a session that loaded perfectly well, with only a reload to
    // recover.
    mockApi.getSession
      .mockRejectedValueOnce(new Error('GET /api/sessions/s1 -> 404'))
      .mockResolvedValueOnce(detailWith([rally('r1', 1), rally('r2', 2)]))

    instance = mount(SessionHarness, { target }) as unknown as { setId: (id: string) => void }
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/404/))

    instance.setId('s2')
    flushSync()

    await vi.waitFor(() => expect(target.textContent).toMatch(/rally 1 \/ 2/))
    expect(target.textContent).not.toMatch(/404/)
  })

  it('ignores a superseded id’s response that resolves late', async () => {
    // Rally ids are globally unique, so a star fired against a stale detail
    // lands on a real rally -- just not the one the header names. There is no
    // visible signal, which is what makes this worse than the error case.
    const slow = deferred<SessionDetail>()
    mockApi.getSession
      .mockReturnValueOnce(slow.promise)
      .mockResolvedValueOnce(detailWith([rally('r9', 1), rally('r10', 2), rally('r11', 3)]))

    instance = mount(SessionHarness, { target }) as unknown as { setId: (id: string) => void }
    flushSync()

    instance.setId('s2')
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/rally 1 \/ 3/))

    // s1 finally answers, with a rally set that is no longer on screen.
    slow.resolve(detailWith([rally('r1', 1), rally('r2', 2)]))
    await slow.promise
    await settle()
    flushSync()

    expect(target.textContent).toMatch(/rally 1 \/ 3/)
    expect(target.textContent).not.toMatch(/rally 1 \/ 2/)
  })

  it('does not show the previous id’s rallies while the new id is in flight', async () => {
    // The window between navigating and the response landing is the same
    // defect in a third disguise: the header renders the new id immediately
    // (it is a prop) while `detail` still holds the old session, so the page
    // reads as the new session and behaves as the old one. Keystrokes work
    // the whole time.
    const slow = deferred<SessionDetail>()
    mockApi.getSession
      .mockResolvedValueOnce(detailWith([rally('r1', 1), rally('r2', 2)]))
      .mockReturnValueOnce(slow.promise)

    instance = mount(SessionHarness, { target }) as unknown as { setId: (id: string) => void }
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/rally 1 \/ 2/))

    instance.setId('s2')
    flushSync()

    expect(target.textContent).not.toMatch(/rally 1 \/ 2/)

    slow.resolve(detailWith([rally('r9', 1), rally('r10', 2), rally('r11', 3)]))
    await slow.promise
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/rally 1 \/ 3/))
  })

  it('ignores a superseded id’s rejection that arrives late', async () => {
    // The mirror of the case above: a *failure* belonging to a session the
    // user has already left must not raise a banner over the one they are on.
    let rejectSlow!: (e: Error) => void
    const rejecting = new Promise<SessionDetail>((_res, rej) => {
      rejectSlow = rej
    })

    mockApi.getSession
      .mockReturnValueOnce(rejecting)
      .mockResolvedValueOnce(detailWith([rally('r9', 1), rally('r10', 2), rally('r11', 3)]))

    instance = mount(SessionHarness, { target }) as unknown as { setId: (id: string) => void }
    flushSync()

    instance.setId('s2')
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/rally 1 \/ 3/))

    rejectSlow(new Error('GET /api/sessions/s1 -> 500'))
    await rejecting.catch(() => undefined)
    await settle()
    flushSync()

    expect(target.textContent).not.toMatch(/500/)
    expect(target.textContent).toMatch(/rally 1 \/ 3/)
  })
})

function sourceWith(id: string, idx: number) {
  return {
    id,
    session_id: 's1',
    idx,
    recorded_at: '2026-08-19T10:00:00Z',
    offset_ms: 0,
    duration_ms: 1173905,
    width: 3840,
    height: 2160,
    fps: 30,
    has_original: 1,
    court_preset_id: null,
    status: 'needs_setup',
    rotation_deg: 90,
  }
}

describe('Setup.svelte load effect does not carry state across an id change', () => {
  let target: HTMLDivElement
  let instance: { setId: (id: string) => void } | undefined

  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.listPresets.mockResolvedValue([])
    target = document.createElement('div')
    document.body.appendChild(target)
  })

  afterEach(() => {
    if (instance) unmount(instance as never)
    target.remove()
    instance = undefined
  })

  it('clears a previous error once a later id loads successfully', async () => {
    mockApi.getSource
      .mockRejectedValueOnce(new Error('GET /api/sources/src1 -> 404'))
      .mockResolvedValueOnce(sourceWith('src2', 2))

    instance = mount(SetupHarness, { target }) as unknown as { setId: (id: string) => void }
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/404/))

    instance.setId('src2')
    flushSync()

    await vi.waitFor(() => expect(target.textContent).toMatch(/Set up source 2/))
    expect(target.textContent).not.toMatch(/404/)
  })

  it('does not show the previous id’s source while the new id is in flight', async () => {
    // Worse here than on the session page. `start()` submits
    // `api.setup(source.id, ...)` -- the *stale* source's id, not the prop --
    // so confirming a quad dragged over the previous source's frames queues a
    // proxy rebuild and a fifteen-minute detect against the wrong source, then
    // navigates away as though it had worked.
    const slow = deferred<ReturnType<typeof sourceWith>>()
    mockApi.getSource
      .mockResolvedValueOnce(sourceWith('src1', 1))
      .mockReturnValueOnce(slow.promise)

    instance = mount(SetupHarness, { target }) as unknown as { setId: (id: string) => void }
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/Set up source 1/))

    instance.setId('src2')
    flushSync()

    expect(target.textContent).not.toMatch(/Set up source 1/)

    slow.resolve(sourceWith('src2', 2))
    await slow.promise
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/Set up source 2/))
  })

  it('drops a play region committed against the previous id', async () => {
    // `points` is the user's own work, not fetched state, so nothing in the
    // load effect touched it. Carrying it across meant arriving at the next
    // source's wizard with the previous source's quad already committed and
    // `start detection` live -- one click from queueing a rebuild and a
    // detect over a play region drawn on different footage.
    mockApi.listPresets.mockResolvedValue([
      {
        id: 'existing-preset',
        name: 'Court 1',
        points: [
          [0.1, 0.1],
          [0.9, 0.1],
          [0.9, 0.9],
          [0.1, 0.9],
        ] as [number, number][],
        created_at: '2026-08-19T00:00:00Z',
      },
    ])
    mockApi.getSource
      .mockResolvedValueOnce(sourceWith('src1', 1))
      .mockResolvedValueOnce(sourceWith('src2', 2))

    instance = mount(SetupHarness, { target }) as unknown as { setId: (id: string) => void }
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/Set up source 1/))
    await settle()
    flushSync()

    const startBtn = () =>
      target.querySelector('button[aria-label="start detection"]') as HTMLButtonElement
    expect(startBtn().disabled).toBe(true)

    ;(
      target.querySelector('button[aria-label="use preset Court 1"]') as HTMLButtonElement
    ).click()
    flushSync()
    expect(startBtn().disabled).toBe(false)

    instance.setId('src2')
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/Set up source 2/))
    await settle()
    flushSync()

    expect(startBtn().disabled).toBe(true)
  })

  it('ignores a superseded id’s rejection that arrives late', async () => {
    let rejectSlow!: (e: Error) => void
    const rejecting = new Promise<ReturnType<typeof sourceWith>>((_res, rej) => {
      rejectSlow = rej
    })

    mockApi.getSource.mockReturnValueOnce(rejecting).mockResolvedValueOnce(sourceWith('src2', 2))

    instance = mount(SetupHarness, { target }) as unknown as { setId: (id: string) => void }
    flushSync()

    instance.setId('src2')
    flushSync()
    await vi.waitFor(() => expect(target.textContent).toMatch(/Set up source 2/))

    rejectSlow(new Error('GET /api/sources/src1 -> 500'))
    await rejecting.catch(() => undefined)
    await settle()
    flushSync()

    expect(target.textContent).not.toMatch(/500/)
    expect(target.textContent).toMatch(/Set up source 2/)
  })
})
