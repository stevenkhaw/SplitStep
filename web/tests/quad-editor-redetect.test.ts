import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Source } from '../src/lib/types'

// Same mocking approach as quad-editor-scrub.test.ts: QuadEditor imports
// `../lib/api` directly rather than taking it as a prop.
const mockApi = {
  listSessions: vi.fn(),
  getSession: vi.fn(),
  star: vi.fn(),
  reject: vi.fn(),
  seen: vi.fn(),
  setBounds: vi.fn(),
  resegment: vi.fn(),
  scores: vi.fn(),
  listPresets: vi.fn().mockResolvedValue([]),
  createPreset: vi.fn(),
  setPreset: vi.fn(),
  detectSource: vi.fn(),
  jobs: vi.fn(),
  proxyUrl: () => 'about:blank',
  frameUrl: () => 'about:blank',
  getSource: vi.fn(),
  setup: vi.fn(),
  previewUrl: () => 'about:blank',
}

vi.mock('../src/lib/api', () => ({ api: mockApi }))

const { default: QuadEditor } = await import('../src/components/QuadEditor.svelte')

function source(overrides: Partial<Source> = {}): Source {
  return {
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
    features_at: null,
    preset_assigned_at: null,
    ...overrides,
  }
}

describe('QuadEditor re-detect call to action', () => {
  let target: HTMLDivElement
  let instance: unknown

  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.listPresets.mockResolvedValue([])
    mockApi.createPreset.mockResolvedValue({ id: 'p1' })
    mockApi.setPreset.mockResolvedValue({ ok: true })
    mockApi.detectSource.mockResolvedValue({ job_id: 'j1', already_running: false })
    target = document.createElement('div')
    document.body.appendChild(target)
  })

  afterEach(() => {
    if (instance) unmount(instance as never)
    target.remove()
    instance = undefined
    vi.unstubAllGlobals()
  })

  function open(sources: Source[] = [source()]) {
    instance = mount(QuadEditor, {
      target,
      props: { sessionId: 's1', sources, onassigned: vi.fn() },
    })
    flushSync()
    // Collapsed on mount -- see quad-editor-scrub.test.ts for why the toggle
    // is dispatched by hand rather than clicking <summary>.
    const details = target.querySelector('details')
    if (!details) throw new Error('panel details not found')
    details.open = true
    details.dispatchEvent(new Event('toggle'))
    flushSync()
  }

  async function settle() {
    // `save` awaits createPreset -> setPreset -> refreshPresets, and each
    // awaited mock resolution costs more than one microtask to unwind.
    // Draining generously is cheaper than counting them.
    for (let i = 0; i < 20; i++) await Promise.resolve()
    flushSync()
  }

  function button(text: string): HTMLButtonElement | null {
    const all = [...target.querySelectorAll('button')] as HTMLButtonElement[]
    return all.find((b) => b.textContent?.trim() === text) ?? null
  }

  async function assign() {
    const save = button('Save & assign')
    if (!save) throw new Error('save button not found')
    save.click()
    await settle()
  }

  it('does not offer a re-detect before a region has been assigned', () => {
    open()
    expect(button('Run detection with this region')).toBeNull()
  })

  it('offers the re-detect as a real button after an assignment', async () => {
    open()
    await assign()
    expect(button('Run detection with this region')).not.toBeNull()
  })

  it('gives the re-detect the filled primary treatment, not a text link', async () => {
    // The whole bug: this was a caption-sized text link and the reviewer
    // never saw it, re-segmented instead, and concluded re-segment was
    // broken. `bg-fg` + an explicit `text-bg` is the app's filled primary
    // (see FirstRun.svelte) -- never an accent colour, which this app does
    // not have.
    open()
    await assign()
    const b = button('Run detection with this region')
    expect(b?.className).toContain('bg-fg')
    expect(b?.className).toContain('text-bg')
    expect(b?.className).not.toContain('hover:underline')
  })

  it('puts the status sentence and the button in one card', async () => {
    open()
    await assign()
    const b = button('Run detection with this region')
    const card = b?.closest('div.border-line')
    expect(card).not.toBeNull()
    expect(card?.textContent).toContain('Saved and assigned')
    expect(card?.textContent).toContain(
      "Re-segment can't see a new region — detection rebuilds the features.",
    )
  })

  it('queues detection on click, once confirmed, and drops the card', async () => {
    vi.stubGlobal('confirm', vi.fn().mockReturnValue(true))
    open()
    await assign()
    button('Run detection with this region')?.click()
    await settle()

    expect(mockApi.detectSource).toHaveBeenCalledWith('src1')
    expect(button('Run detection with this region')).toBeNull()
  })

  it('does not queue detection when the confirm is declined', async () => {
    vi.stubGlobal('confirm', vi.fn().mockReturnValue(false))
    open()
    await assign()
    button('Run detection with this region')?.click()
    await settle()

    expect(mockApi.detectSource).not.toHaveBeenCalled()
    // Still offered -- declining a confirm is not a decision to hide it.
    expect(button('Run detection with this region')).not.toBeNull()
  })

  it('withdraws the offer when the editor switches to another source', async () => {
    // The offer names "this region" on the source it was assigned to; a
    // different source's features have nothing to do with it.
    open([source(), source({ id: 'src2', idx: 2 })])
    await assign()
    const select = target.querySelector('select') as HTMLSelectElement
    select.value = 'src2'
    select.dispatchEvent(new Event('change', { bubbles: true }))
    flushSync()

    expect(button('Run detection with this region')).toBeNull()
  })
})
