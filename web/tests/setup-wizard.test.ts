import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const source = {
  id: 'src1', session_id: 's1', idx: 1, recorded_at: '2026-08-19T10:00:00Z',
  offset_ms: 0, duration_ms: 1173905, width: 3840, height: 2160, fps: 30,
  has_original: 1, court_preset_id: null, status: 'needs_setup', rotation_deg: 90,
}

const mockApi = {
  getSource: vi.fn().mockResolvedValue(source),
  listPresets: vi.fn().mockResolvedValue([]),
  createPreset: vi.fn().mockResolvedValue({ id: 'p1' }),
  setup: vi.fn().mockResolvedValue({ job_id: 'j1' }),
  previewUrl: (s: string, i: number, at: number, rot: number) =>
    `/media/${s}/${i}/preview.jpg?at_ms=${at}&rot=${rot}`,
}
vi.mock('../src/lib/api', () => ({ api: mockApi }))

const { default: Setup } = await import('../src/routes/Setup.svelte')

describe('Setup wizard', () => {
  let target: HTMLDivElement
  let instance: unknown

  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.getSource.mockResolvedValue(source)
    mockApi.listPresets.mockResolvedValue([])
    mockApi.createPreset.mockResolvedValue({ id: 'p1' })
    mockApi.setup.mockResolvedValue({ job_id: 'j1' })
    target = document.createElement('div')
    document.body.appendChild(target)
  })

  afterEach(() => {
    if (instance) unmount(instance as never)
    target.remove()
    instance = undefined
  })

  async function open() {
    instance = mount(Setup, { target, props: { id: 'src1' } })
    flushSync()
    await vi.waitFor(() => expect(mockApi.getSource).toHaveBeenCalled())
    flushSync()
  }

  function grid(): HTMLImageElement[] {
    return [...target.querySelectorAll('img[data-grid]')] as HTMLImageElement[]
  }

  function click(label: string) {
    const el = target.querySelector(`button[aria-label="${label}"]`) as HTMLButtonElement
    if (!el) throw new Error(`${label} not found`)
    el.click()
    flushSync()
  }

  it('shows nine spread preview frames, none of them t=0', async () => {
    await open()
    expect(grid()).toHaveLength(9)
    expect(grid().every((img) => !img.src.includes('at_ms=0&'))).toBe(true)
  })

  it('opens at the rotation the source was ingested with', async () => {
    await open()
    expect(grid()[0].src).toContain('rot=90')
  })

  it('rotating clockwise re-requests the grid at the next quarter turn', async () => {
    await open()
    click('rotate clockwise')
    expect(grid()[0].src).toContain('rot=180')
  })

  it('rotating counter-clockwise wraps below zero', async () => {
    await open()
    click('rotate counter-clockwise')  // 90 -> 0
    click('rotate counter-clockwise')  // 0 -> 270
    expect(grid()[0].src).toContain('rot=270')
  })

  it('cannot start detection before a play region is set', async () => {
    await open()
    const start = target.querySelector('button[aria-label="start detection"]')
    expect((start as HTMLButtonElement).disabled).toBe(true)
  })

  it('creates the preset and posts the setup exactly once', async () => {
    await open()
    click('rotate counter-clockwise')       // 90 -> 0
    click('use default play region')
    click('start detection')
    click('start detection')                // double-click must not double-post
    await vi.waitFor(() => expect(mockApi.setup).toHaveBeenCalledTimes(1))
    expect(mockApi.createPreset).toHaveBeenCalledTimes(1)
    expect(mockApi.setup).toHaveBeenCalledWith('src1', 0, 'p1')
  })
})
