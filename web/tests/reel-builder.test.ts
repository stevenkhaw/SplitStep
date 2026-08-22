import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReelDetail, ReelItem } from '../src/lib/types'

function item(start: number, overrides: Partial<ReelItem> = {}): ReelItem {
  return {
    source_id: 'src1',
    session_id: 's1',
    source_idx: 1,
    start_ms: start,
    end_ms: start + 4000,
    duration_ms: 4000,
    position: 0,
    clip_ready: true,
    rally: null,
    ...overrides,
  }
}

function detail(items: ReelItem[], dirty = 1): ReelDetail {
  return {
    reel: {
      id: 'r1', name: '2026-08-18 points', slug: '2026-08-18-points',
      rendered_path: null, rendered_at: null, dirty,
      created_at: '2026-08-21T10:00:00Z', item_count: items.length,
    },
    items,
  }
}

const mockApi = {
  getReel: vi.fn(),
  addReelItems: vi.fn().mockResolvedValue({ added: 1, existing: 0, total: 1 }),
  removeReelItem: vi.fn().mockResolvedValue({ removed: true, total: 0 }),
  setReelOrder: vi.fn().mockResolvedValue({ ok: true }),
  exportReelClips: vi.fn().mockResolvedValue({
    queued: 2, already_cut: 0, in_flight: 0, unavailable: 0, total: 2,
  }),
  renderReel: vi.fn().mockResolvedValue({ job_id: 'j1', already_running: false }),
  listSessions: vi.fn().mockResolvedValue([]),
  getSession: vi.fn().mockResolvedValue({ session: {}, sources: [], rallies: [] }),
  jobs: vi.fn().mockResolvedValue([]),
  frameUrl: () => 'about:blank',
  proxyUrl: () => 'about:blank',
}
vi.mock('../src/lib/api', () => ({ api: mockApi }))

HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined)
HTMLMediaElement.prototype.pause = vi.fn()
HTMLMediaElement.prototype.load = vi.fn()

const { default: Reel } = await import('../src/routes/Reel.svelte')

let host: HTMLElement
let component: ReturnType<typeof mount> | null = null

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  vi.clearAllMocks()
  mockApi.jobs.mockResolvedValue([])
  mockApi.listSessions.mockResolvedValue([])
})

afterEach(() => {
  if (component) unmount(component)
  component = null
  host.remove()
})

async function open(d: ReelDetail) {
  mockApi.getReel.mockResolvedValue(d)
  component = mount(Reel, { target: host, props: { slug: d.reel.slug } })
  flushSync()
  await Promise.resolve()
  await Promise.resolve()
  flushSync()
}

const render = () => host.querySelector('[data-render]') as HTMLButtonElement
const cut = () => host.querySelector('[data-cut]') as HTMLButtonElement
const previewToggle = () => host.querySelector('[data-preview]') as HTMLButtonElement
const previewHeading = () =>
  [...host.querySelectorAll('h2')].find((h) => h.textContent === 'Preview') ?? null

async function settle(): Promise<void> {
  await Promise.resolve()
  await Promise.resolve()
  flushSync()
}

// Waits for the Nth (0-indexed) call to getReel to have actually resolved
// and been applied. Awaiting `mock.results[n].value` -- the exact Promise
// the effect itself is chained off -- rather than a fixed number of
// `Promise.resolve()` ticks guarantees the component's own `.then` (attached
// first, when the effect ran) has already fired before this continues:
// callbacks on one promise run in attachment order. A tick-counting
// `settle()` is one layer too shallow for cutMissing()/render(), which now
// wrap their API call in an extra `async () => { await ... }` inside
// `mutate` -- one more promise hop than a fixed tick count assumed.
async function afterRefetch(callIndex: number): Promise<void> {
  await vi.waitFor(() => expect(mockApi.getReel.mock.results.length).toBeGreaterThan(callIndex))
  await mockApi.getReel.mock.results[callIndex]!.value
  flushSync()
}

describe('Reel builder', () => {
  it('renders the reel name and its items', async () => {
    await open(detail([item(1000), item(9000)]))
    expect(host.textContent).toContain('2026-08-18 points')
    expect(host.querySelectorAll('[data-reel-row]')).toHaveLength(2)
  })

  it('disables Render while a clip is missing and names the count', async () => {
    await open(detail([item(1000, { clip_ready: false }), item(9000)]))
    expect(render().disabled).toBe(true)
    // The reason is on the button, so the user knows to press Cut instead.
    // Render must never auto-enqueue the cuts.
    expect(render().textContent).toContain('1 clip not cut yet')
    expect(mockApi.renderReel).not.toHaveBeenCalled()
  })

  it('disables Render on an empty reel', async () => {
    await open(detail([]))
    expect(render().disabled).toBe(true)
  })

  it('enables Render once every clip is ready', async () => {
    await open(detail([item(1000), item(9000)]))
    expect(render().disabled).toBe(false)
    render().click()
    flushSync()
    expect(mockApi.renderReel).toHaveBeenCalledWith('2026-08-18-points')
  })

  it('cutting reports the four counts separately', async () => {
    await open(detail([item(1000, { clip_ready: false })]))
    mockApi.exportReelClips.mockResolvedValue({
      queued: 0, already_cut: 0, in_flight: 3, unavailable: 0, total: 3,
    })
    cut().click()
    flushSync()
    await Promise.resolve()
    await Promise.resolve()
    flushSync()
    expect(host.textContent).toContain('3 in flight')
    expect(host.textContent).not.toContain('already cut')
  })

  it('cutting is the only thing that enqueues an encode', async () => {
    await open(detail([item(1000), item(9000)]))

    render().click()
    flushSync()
    expect(mockApi.exportReelClips).not.toHaveBeenCalled()
    await settle()

    // The other direction: cutting must not also enqueue a render. Cleared
    // rather than checked from a fresh mount, so this exercises the same
    // shared `busy` guard the render click above just went through.
    mockApi.renderReel.mockClear()
    cut().click()
    flushSync()
    await settle()
    expect(mockApi.renderReel).not.toHaveBeenCalled()
  })

  it('persists a reorder and refetches', async () => {
    await open(detail([item(1000), item(9000)]))
    expect(mockApi.getReel).toHaveBeenCalledTimes(1)
    const handles = [...host.querySelectorAll('[data-drag-handle]')] as HTMLElement[]
    handles[0].dispatchEvent(new KeyboardEvent('keydown', {
      bubbles: true, key: 'ArrowDown', altKey: true,
    }))
    flushSync()
    expect(mockApi.setReelOrder).toHaveBeenCalledWith('2026-08-18-points', [
      { source_id: 'src1', start_ms: 9000, end_ms: 13000 },
      { source_id: 'src1', start_ms: 1000, end_ms: 5000 },
    ])
    // The "refetches" half of the name: a successful reorder bumps
    // `revision`, and the effect that watches it re-reads the reel.
    await settle()
    expect(mockApi.getReel).toHaveBeenCalledTimes(2)
  })

  it('removes an item through the API', async () => {
    await open(detail([item(1000)]))
    ;(host.querySelector('[data-remove]') as HTMLElement).click()
    flushSync()
    expect(mockApi.removeReelItem).toHaveBeenCalledWith('2026-08-18-points', {
      source_id: 'src1', start_ms: 1000, end_ms: 5000,
    })
  })

  it('surfaces a failed reorder instead of leaving a phantom order', async () => {
    // 409 means the client's membership view is stale. Refetching is the
    // fix, and saying so beats a list that silently disagrees with the
    // server about what order it is in.
    await open(detail([item(1000), item(9000)]))
    mockApi.setReelOrder.mockRejectedValue(new Error('409 stale'))
    const handles = [...host.querySelectorAll('[data-drag-handle]')] as HTMLElement[]
    handles[0].dispatchEvent(new KeyboardEvent('keydown', {
      bubbles: true, key: 'ArrowDown', altKey: true,
    }))
    flushSync()
    await Promise.resolve()
    await Promise.resolve()
    flushSync()
    expect(host.textContent).toContain("Couldn't reorder")
  })

  it('does not remount the preview on a refetch that leaves membership unchanged', async () => {
    await open(detail([item(1000), item(9000)]))
    previewToggle().click()
    flushSync()
    const before = previewHeading()
    expect(before).toBeTruthy()

    // A fresh array of fresh objects, same spans in the same order -- what
    // a real refetch returns, since JSON never shares identity with what
    // produced it. cutMissing() is just a convenient way to trigger the
    // refetch; the fixture's items are already all clip_ready.
    mockApi.getReel.mockResolvedValue(detail([item(1000), item(9000)]))
    cut().click()
    flushSync()
    await afterRefetch(1)

    expect(previewHeading()).toBe(before)
  })

  it('remounts the preview when an item is removed', async () => {
    await open(detail([item(1000), item(9000)]))
    previewToggle().click()
    flushSync()
    const before = previewHeading()
    expect(before).toBeTruthy()

    mockApi.getReel.mockResolvedValue(detail([item(1000)]))
    cut().click()
    flushSync()
    await afterRefetch(1)

    expect(previewHeading()).not.toBe(before)
  })

  it('remounts the preview when the items are reordered', async () => {
    await open(detail([item(1000), item(9000)]))
    previewToggle().click()
    flushSync()
    const before = previewHeading()
    expect(before).toBeTruthy()

    mockApi.getReel.mockResolvedValue(detail([item(9000), item(1000)]))
    cut().click()
    flushSync()
    await afterRefetch(1)

    expect(previewHeading()).not.toBe(before)
  })
})
