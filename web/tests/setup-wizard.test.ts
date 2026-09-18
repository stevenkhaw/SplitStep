import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const source = {
  id: 'src1', session_id: 's1', idx: 1, recorded_at: '2026-08-19T10:00:00Z',
  offset_ms: 0, duration_ms: 1173905, width: 3840, height: 2160, fps: 30,
  has_original: 1, court_preset_id: null, status: 'needs_setup', rotation_deg: 90,
  features_at: null, preset_assigned_at: null,
}

const existingPreset = {
  id: 'existing-preset',
  name: 'Court 1',
  points: [
    [0.1, 0.1],
    [0.9, 0.1],
    [0.9, 0.9],
    [0.1, 0.9],
  ] as [number, number][],
  created_at: '2026-08-19T00:00:00Z',
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

// jsdom has no PointerEvent constructor (same workaround as
// timeline-drag-gain.test.ts); QuadCanvas's handle drag only ever reads
// clientX/clientY/pointerId off the events it receives, all of which a
// MouseEvent-based stand-in carries fine.
class FakePointerEvent extends MouseEvent {
  pointerId: number
  constructor(type: string, init: MouseEventInit & { pointerId: number }) {
    super(type, init)
    this.pointerId = init.pointerId
  }
}

// A fixed fake layout for the QuadCanvas wrapper -- jsdom never computes
// real layout, so `wrap.getBoundingClientRect()` (what QuadCanvas's drag
// math measures pointer positions against) would otherwise always read
// zero width/height and every dragged point would clamp to the same
// corner.
const FAKE_RECT: DOMRect = {
  left: 0, top: 0, right: 1000, bottom: 800, width: 1000, height: 800,
  x: 0, y: 0, toJSON: () => ({}),
}

describe('Setup wizard', () => {
  let target: HTMLDivElement
  let instance: unknown
  let rectSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.getSource.mockResolvedValue(source)
    mockApi.listPresets.mockResolvedValue([])
    mockApi.createPreset.mockResolvedValue({ id: 'p1' })
    mockApi.setup.mockResolvedValue({ job_id: 'j1' })
    target = document.createElement('div')
    document.body.appendChild(target)
    rectSpy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(FAKE_RECT)
  })

  afterEach(() => {
    if (instance) unmount(instance as never)
    target.remove()
    rectSpy.mockRestore()
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

  // Drags the first quad corner handle to a new position via real
  // pointerdown/pointermove/pointerup events, the way a user actually
  // changes the play region -- as opposed to clicking a preset button,
  // which only replaces `points` wholesale.
  function dragFirstCorner() {
    const handle = target.querySelector('button[aria-label="corner 1"]') as HTMLButtonElement
    if (!handle) throw new Error('corner 1 handle not found')
    handle.setPointerCapture = vi.fn()
    handle.releasePointerCapture = vi.fn()
    handle.dispatchEvent(
      new FakePointerEvent('pointerdown', { clientX: 100, clientY: 100, pointerId: 1, bubbles: true }),
    )
    flushSync()
    handle.dispatchEvent(
      new FakePointerEvent('pointermove', { clientX: 200, clientY: 150, pointerId: 1, bubbles: true }),
    )
    flushSync()
    handle.dispatchEvent(
      new FakePointerEvent('pointerup', { clientX: 200, clientY: 150, pointerId: 1, bubbles: true }),
    )
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

  // Finding: a preset created by this attempt's createPreset() call lived
  // only in start()'s local `presetId`, not in `selectedPresetId`, so a
  // setup() failure left selectedPresetId at null. Every retry then hit the
  // `!presetId` branch again and inserted another orphaned "<session>
  // source <idx>" preset row -- createPreset called once per retry instead
  // of once total.
  it('reuses the preset created by a failed attempt on retry, instead of creating another one', async () => {
    await open()
    click('use default play region')
    mockApi.setup.mockRejectedValueOnce(new Error('network error'))
    click('start detection')
    await vi.waitFor(() => expect(mockApi.setup).toHaveBeenCalledTimes(1))

    click('start detection') // retry after the failure
    await vi.waitFor(() => expect(mockApi.setup).toHaveBeenCalledTimes(2))

    expect(mockApi.createPreset).toHaveBeenCalledTimes(1)
    expect(mockApi.setup).toHaveBeenNthCalledWith(1, 'src1', 90, 'p1')
    expect(mockApi.setup).toHaveBeenNthCalledWith(2, 'src1', 90, 'p1')
  })

  // Finding: Confirm always called createPreset, even when the user just
  // clicked an existing preset button (which only copies its coordinates).
  // No dedupe and no unique name constraint on court_presets meant every
  // confirm -- and every retry after an error -- inserted another
  // indistinguishable "<session> source <idx>" row.

  it('confirming right after clicking an existing preset reuses its id and does not create a new one', async () => {
    mockApi.listPresets.mockResolvedValue([existingPreset])
    await open()
    await vi.waitFor(() =>
      expect(target.querySelector(`button[aria-label="use preset ${existingPreset.name}"]`)).not.toBeNull(),
    )
    click(`use preset ${existingPreset.name}`)
    click('start detection')
    await vi.waitFor(() => expect(mockApi.setup).toHaveBeenCalledTimes(1))
    expect(mockApi.createPreset).not.toHaveBeenCalled()
    expect(mockApi.setup).toHaveBeenCalledWith('src1', 90, existingPreset.id)
  })

  it('confirming after dragging a corner creates a new preset', async () => {
    mockApi.listPresets.mockResolvedValue([existingPreset])
    await open()
    click('use default play region')
    dragFirstCorner()
    click('start detection')
    await vi.waitFor(() => expect(mockApi.setup).toHaveBeenCalledTimes(1))
    expect(mockApi.createPreset).toHaveBeenCalledTimes(1)
    expect(mockApi.setup).toHaveBeenCalledWith('src1', 90, 'p1')
  })

  it('clicking a preset then dragging a corner creates a new preset rather than silently overwriting the reused one', async () => {
    mockApi.listPresets.mockResolvedValue([existingPreset])
    await open()
    await vi.waitFor(() =>
      expect(target.querySelector(`button[aria-label="use preset ${existingPreset.name}"]`)).not.toBeNull(),
    )
    click(`use preset ${existingPreset.name}`)
    dragFirstCorner()
    click('start detection')
    await vi.waitFor(() => expect(mockApi.setup).toHaveBeenCalledTimes(1))
    expect(mockApi.createPreset).toHaveBeenCalledTimes(1)
    expect(mockApi.setup).toHaveBeenCalledWith('src1', 90, 'p1')
    expect(mockApi.setup).not.toHaveBeenCalledWith('src1', 90, existingPreset.id)
  })
})
