import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Rally, Session, SessionDetail } from '../src/lib/types'

function rally(id: string, idx: number, overrides: Partial<Rally> = {}): Rally {
  return {
    id,
    session_id: 's1',
    source_id: 'src1',
    idx,
    start_ms: idx * 10000,
    end_ms: idx * 10000 + 4000,
    det_start_ms: idx * 10000,
    det_end_ms: idx * 10000 + 4000,
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

const sessions: Session[] = [
  {
    id: 's1', title: '2026-08-18', played_on: '2026-08-18', status: 'reviewed',
    rally_count: 4, starred_count: 1, point_count: 2,
  },
  {
    id: 's2', title: '2026-08-19', played_on: '2026-08-19', status: 'reviewed',
    rally_count: 1, starred_count: 0, point_count: 1,
  },
]

const detailS1: SessionDetail = {
  session: { id: 's1', title: '2026-08-18', played_on: '2026-08-18', status: 'reviewed' },
  sources: [],
  rallies: [
    rally('r1', 1, { point: 1 }),
    rally('r2', 2, { point: 1, starred: 1 }),
    rally('r3', 3),
    rally('r4', 4, { point: 1, rejected: 1 }),
  ],
}

// A second session with its own rallies, so a test can actually switch
// sessions -- with only one session in the fixture, "changing session
// clears the checked set" was untestable.
//
// Its rally deliberately reuses id 'r1' from session one's fixture. Real
// rally ids are unique across the whole library, so this collision can't
// happen in production -- but that is exactly why it is the right fixture
// for THIS test: `checked` is a Set of rally ids, and if the picker ever
// stopped clearing it on a session change, a stale id from the previous
// session would silently re-render as checked here only when the new
// session happens to reuse it, which the real backend's uniqueness makes
// rare enough to never get noticed. A distinct id would make that failure
// mode invisible to the test even though the reset itself is what's being
// verified, giving a false pass. Same idea as spanKey's own session-agnostic
// keying: the picker's correctness has to hold without leaning on ids being
// unique across sessions.
const detailS2: SessionDetail = {
  session: { id: 's2', title: '2026-08-19', played_on: '2026-08-19', status: 'reviewed' },
  sources: [],
  rallies: [
    rally('r1', 1, { session_id: 's2', source_id: 'src2', point: 1 }),
  ],
}

const mockApi = {
  listSessions: vi.fn().mockResolvedValue(sessions),
  getSession: vi.fn((id: string) => Promise.resolve(id === 's2' ? detailS2 : detailS1)),
}
vi.mock('../src/lib/api', () => ({ api: mockApi }))

const { default: AddRalliesPicker } = await import('../src/components/AddRalliesPicker.svelte')

let host: HTMLElement
let component: ReturnType<typeof mount> | null = null

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
})

afterEach(() => {
  if (component) unmount(component)
  component = null
  host.remove()
})

function boxes(): HTMLInputElement[] {
  return [...host.querySelectorAll('input[type=checkbox]')] as HTMLInputElement[]
}

async function settle(): Promise<void> {
  await Promise.resolve()
  await Promise.resolve()
  flushSync()
}

async function open(props: Record<string, unknown> = {}) {
  component = mount(AddRalliesPicker, {
    target: host,
    props: { existing: [], defaultSessionId: 's1', onadd: vi.fn(), onclose: vi.fn(), ...props },
  })
  flushSync()
  await settle()
}

describe('AddRalliesPicker', () => {
  it('opens on the points filter and hides rejected rallies', async () => {
    await open()
    // r1 and r2 are points; r4 is a point but rejected, and a rejected rally
    // is a bad detection -- it is not a clip anyone wants in a reel.
    expect(boxes()).toHaveLength(2)
  })

  it('switches to starred', async () => {
    await open()
    ;(host.querySelector('[data-filter="starred"]') as HTMLElement).click()
    flushSync()
    expect(boxes()).toHaveLength(1)
  })

  it('switches to all, still without rejected', async () => {
    await open()
    ;(host.querySelector('[data-filter="all"]') as HTMLElement).click()
    flushSync()
    expect(boxes()).toHaveLength(3)
  })

  it('shows a span already in the reel as checked and disabled', async () => {
    // Not hidden: a reviewer scanning for what is missing needs to see that
    // the rally is accounted for, and an add that silently did nothing is
    // the confusing alternative.
    await open({ existing: [{ source_id: 'src1', start_ms: 10000, end_ms: 14000 }] })
    expect(boxes()[0].checked).toBe(true)
    expect(boxes()[0].disabled).toBe(true)
  })

  it('adds only the newly checked spans', async () => {
    const onadd = vi.fn()
    await open({ onadd })
    boxes()[1].click()
    flushSync()
    ;(host.querySelector('[data-add]') as HTMLElement).click()
    flushSync()
    expect(onadd).toHaveBeenCalledWith([
      { source_id: 'src1', start_ms: 20000, end_ms: 24000 },
    ])
  })

  it('disables Add while nothing is checked', async () => {
    await open()
    expect((host.querySelector('[data-add]') as HTMLButtonElement).disabled).toBe(true)
  })

  it('select-all checks every enabled row', async () => {
    const onadd = vi.fn()
    await open({ onadd })
    ;(host.querySelector('[data-select-all]') as HTMLElement).click()
    flushSync()
    ;(host.querySelector('[data-add]') as HTMLElement).click()
    flushSync()
    expect(onadd.mock.calls[0][0]).toHaveLength(2)
  })

  it('clears the checked set when switching sessions', async () => {
    const onadd = vi.fn()
    await open({ onadd })
    boxes()[0].click()
    flushSync()
    expect(boxes()[0].checked).toBe(true)

    const select = host.querySelector('select[aria-label="Session"]') as HTMLSelectElement
    select.value = 's2'
    select.dispatchEvent(new Event('change', { bubbles: true }))
    flushSync()
    // getSession('s2') is async, so the row list is briefly in its
    // "Loading…" state (no checkboxes at all) -- wait for the fetch to
    // actually land rather than assuming two `settle()` ticks cover it.
    await vi.waitFor(() => {
      flushSync()
      expect(boxes().length).toBeGreaterThan(0)
    })

    // detailS2's rally reuses id 'r1' -- see the fixture comment above for
    // why. If `checked` survived the session switch, this box would render
    // checked without ever having been clicked in session two.
    expect(boxes().every((b) => !b.checked)).toBe(true)

    // A subsequent, genuine check in session two sends only its own span --
    // not a phantom carried over from session one's selection.
    boxes()[0].click()
    flushSync()
    ;(host.querySelector('[data-add]') as HTMLElement).click()
    flushSync()
    expect(onadd).toHaveBeenCalledWith([
      { source_id: 'src2', start_ms: 10000, end_ms: 14000 },
    ])
  })
})
