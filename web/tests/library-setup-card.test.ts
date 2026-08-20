import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mockNavigate = vi.fn()
const mockApi = {
  listSessions: vi.fn(),
  getSession: vi.fn(),
}

vi.mock('../src/lib/router.svelte', () => ({
  navigate: mockNavigate,
}))
vi.mock('../src/lib/api', () => ({ api: mockApi }))

const { default: Library } = await import('../src/routes/Library.svelte')

describe('Library', () => {
  let target: HTMLDivElement
  let instance: unknown

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

  it('needs_setup session has aria-label="set up"', async () => {
    mockApi.listSessions.mockResolvedValue([
      {
        id: 's-needs-setup',
        title: 'Setup Session',
        played_on: '2026-08-19',
        status: 'needs_setup',
        rally_count: 0,
        starred_count: 0,
      },
    ])

    instance = mount(Library, { target, props: {} })
    flushSync()

    await vi.waitFor(() => {
      const btn = target.querySelector('button[aria-label="set up"]')
      expect(btn).not.toBeNull()
    })
  })

  it('ready session does NOT have aria-label="set up"', async () => {
    mockApi.listSessions.mockResolvedValue([
      {
        id: 's-ready',
        title: 'Ready Session',
        played_on: '2026-08-19',
        status: 'ready',
        rally_count: 5,
        starred_count: 2,
      },
    ])

    instance = mount(Library, { target, props: {} })
    flushSync()

    await vi.waitFor(() => {
      expect(target.querySelector('button')).not.toBeNull()
    })

    const setupButton = target.querySelector('button[aria-label="set up"]')
    expect(setupButton).toBeNull()
  })

  it('clicking needs_setup session navigates to setup wizard', async () => {
    mockApi.listSessions.mockResolvedValue([
      {
        id: 's-needs-setup',
        title: 'Setup Session',
        played_on: '2026-08-19',
        status: 'needs_setup',
        rally_count: 0,
        starred_count: 0,
      },
    ])
    mockApi.getSession.mockResolvedValue({
      session: {
        id: 's-needs-setup',
        title: 'Setup Session',
        played_on: '2026-08-19',
        status: 'needs_setup',
      },
      sources: [
        {
          id: 'src-setup',
          session_id: 's-needs-setup',
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
    })

    instance = mount(Library, { target, props: {} })
    flushSync()

    await vi.waitFor(() => {
      const btn = target.querySelector('button[aria-label="set up"]')
      expect(btn).not.toBeNull()
    })

    const button = target.querySelector('button[aria-label="set up"]') as HTMLButtonElement
    button.click()
    flushSync()

    await vi.waitFor(() => {
      expect(mockApi.getSession).toHaveBeenCalledWith('s-needs-setup')
    })

    // Navigate is called with the first source ID
    await vi.waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/setup/src-setup')
    })
  })

  it('clicking ready session navigates directly to session page', async () => {
    mockApi.listSessions.mockResolvedValue([
      {
        id: 's-ready',
        title: 'Ready Session',
        played_on: '2026-08-19',
        status: 'ready',
        rally_count: 5,
        starred_count: 2,
      },
    ])

    instance = mount(Library, { target, props: {} })
    flushSync()

    await vi.waitFor(() => {
      expect(target.querySelector('button')).not.toBeNull()
    })

    const buttons = target.querySelectorAll('button')
    const readyButton = [...buttons].find(b => b.textContent?.includes('Ready Session'))
    expect(readyButton).toBeDefined()

    readyButton!.click()
    flushSync()

    await vi.waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/s/s-ready')
    })
  })

  it('double-clicking session button does not fire multiple api calls', async () => {
    mockApi.listSessions.mockResolvedValue([
      {
        id: 's-needs-setup',
        title: 'Setup Session',
        played_on: '2026-08-19',
        status: 'needs_setup',
        rally_count: 0,
        starred_count: 0,
      },
    ])
    mockApi.getSession.mockResolvedValue({
      session: {
        id: 's-needs-setup',
        title: 'Setup Session',
        played_on: '2026-08-19',
        status: 'needs_setup',
      },
      sources: [
        {
          id: 'src-setup',
          session_id: 's-needs-setup',
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
    })

    instance = mount(Library, { target, props: {} })
    flushSync()

    await vi.waitFor(() => {
      const btn = target.querySelector('button[aria-label="set up"]')
      expect(btn).not.toBeNull()
    })

    const button = target.querySelector('button[aria-label="set up"]') as HTMLButtonElement

    // Double-click
    button.click()
    button.click()
    flushSync()

    // Should only call getSession once, not twice (this is the bug we're testing for)
    await vi.waitFor(() => {
      expect(mockApi.getSession).toHaveBeenCalledTimes(1)
    })
  })
})
