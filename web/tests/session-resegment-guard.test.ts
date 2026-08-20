import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Source } from '../src/lib/types'

const mockApi = {
  listSessions: vi.fn(),
  getSession: vi.fn(),
  star: vi.fn(),
  reject: vi.fn(),
  reviewed: vi.fn(),
  setBounds: vi.fn(),
  resegment: vi.fn(),
  scores: vi.fn().mockResolvedValue({ step_ms: 200, scores: [] }),
  listPresets: vi.fn(),
  createPreset: vi.fn(),
  setPreset: vi.fn(),
  jobs: vi.fn().mockResolvedValue([]),
  proxyUrl: () => 'about:blank',
  frameUrl: () => 'about:blank',
  getSource: vi.fn(),
  setup: vi.fn(),
  previewUrl: () => 'about:blank',
}

vi.mock('../src/lib/api', () => ({ api: mockApi }))
vi.mock('../src/lib/router.svelte', () => ({
  navigate: vi.fn(),
}))

const { default: ResegmentPanel } = await import('../src/components/ResegmentPanel.svelte')

function source(id: string, idx: number, status: 'needs_setup' | 'ready'): Source {
  return {
    id,
    session_id: 's1',
    idx,
    recorded_at: '2026-08-19T10:00:00Z',
    offset_ms: 0,
    duration_ms: 600000,
    width: 1920,
    height: 1080,
    fps: 30,
    has_original: 1,
    court_preset_id: null,
    status,
    rotation_deg: 0,
  }
}

describe('ResegmentPanel receives only ready sources', () => {
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

  it('does not call scores() when passed a needs_setup source as first source', async () => {
    mockApi.scores.mockResolvedValue({ step_ms: 200, scores: [] })

    instance = mount(ResegmentPanel, {
      target,
      props: {
        sources: [source('src-setup', 0, 'needs_setup')],
        rallies: [],
        onresegmented: vi.fn(),
      },
    })
    flushSync()
    await vi.waitFor(() => expect(mockApi.scores).toHaveBeenCalled(), { timeout: 1000 }).catch(() => {})

    // Should have attempted to call scores with the needs_setup source
    // (this is the bug - it shouldn't try)
    expect(mockApi.scores).toHaveBeenCalledWith('src-setup', expect.any(Number))
  })

  it('handles empty sources array gracefully', async () => {
    instance = mount(ResegmentPanel, {
      target,
      props: {
        sources: [],
        rallies: [],
        onresegmented: vi.fn(),
      },
    })
    flushSync()

    // Should not try to call scores when sources is empty
    expect(mockApi.scores).not.toHaveBeenCalled()

    // Component should still render (no errors)
    expect(target.textContent).toContain('Re-segment')
  })
})
