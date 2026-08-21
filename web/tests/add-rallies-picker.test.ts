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
    note: '',
    ...overrides,
  }
}

const sessions: Session[] = [{
  id: 's1', title: '2026-08-18', played_on: '2026-08-18', status: 'reviewed',
  rally_count: 4, starred_count: 1, point_count: 2,
}]

const detail: SessionDetail = {
  session: { id: 's1', title: '2026-08-18', played_on: '2026-08-18', status: 'reviewed' },
  sources: [],
  rallies: [
    rally('r1', 1, { point: 1 }),
    rally('r2', 2, { point: 1, starred: 1 }),
    rally('r3', 3),
    rally('r4', 4, { point: 1, rejected: 1 }),
  ],
}

const mockApi = {
  listSessions: vi.fn().mockResolvedValue(sessions),
  getSession: vi.fn().mockResolvedValue(detail),
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
})
