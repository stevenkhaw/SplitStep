import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mockApi = {
  listSessions: vi.fn().mockResolvedValue([
    {
      id: 's1',
      title: 'Test Session',
      played_on: '2026-08-19',
      status: 'needs_setup',
      rally_count: 0,
      starred_count: 0,
    },
  ]),
  getSession: vi.fn().mockResolvedValue({
    session: {
      id: 's1',
      title: 'Test Session',
      played_on: '2026-08-19',
      status: 'needs_setup',
    },
    sources: [
      {
        id: 'src1',
        session_id: 's1',
        idx: 0,
        recorded_at: '2026-08-19T00:00:00Z',
        offset_ms: 0,
        duration_ms: 60000,
        width: 1920,
        height: 1080,
        fps: 30,
        has_original: 1,
        court_preset_id: null,
        status: 'needs_setup',
        rotation_deg: 0,
      },
    ],
    rallies: [],
  }),
}
vi.mock('../src/lib/api', () => ({ api: mockApi }))

const { default: Library } = await import('../src/routes/Library.svelte')

describe('Library', () => {
  let target: HTMLDivElement
  let instance: unknown

  beforeEach(() => {
    target = document.createElement('div')
    document.body.appendChild(target)
  })

  afterEach(() => {
    if (instance) unmount(instance as never)
    target.remove()
    instance = undefined
  })

  it('links a needs_setup session to its wizard instead of its review page', async () => {
    instance = mount(Library, { target, props: {} })
    flushSync()
    await vi.waitFor(() => expect(mockApi.listSessions).toHaveBeenCalled())
    flushSync()
    // Wait for the DOM to update
    await new Promise(r => setTimeout(r, 50))
    flushSync()
    const link = target.querySelector('a[href^="#/setup/"], button[aria-label="set up"]')
    expect(link).not.toBeNull()
  })
})
