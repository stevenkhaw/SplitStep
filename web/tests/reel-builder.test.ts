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
  })

  it('persists a reorder and refetches', async () => {
    await open(detail([item(1000), item(9000)]))
    const handles = [...host.querySelectorAll('[data-drag-handle]')] as HTMLElement[]
    handles[0].dispatchEvent(new KeyboardEvent('keydown', {
      bubbles: true, key: 'ArrowDown', altKey: true,
    }))
    flushSync()
    expect(mockApi.setReelOrder).toHaveBeenCalledWith('2026-08-18-points', [
      { source_id: 'src1', start_ms: 9000, end_ms: 13000 },
      { source_id: 'src1', start_ms: 1000, end_ms: 5000 },
    ])
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
})
