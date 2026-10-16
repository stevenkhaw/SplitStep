import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Source } from '../src/lib/types'

// Same mocking approach as quad-editor-redetect.test.ts.
const mockApi = {
  listPresets: vi.fn().mockResolvedValue([]),
  createPreset: vi.fn(),
  setPreset: vi.fn(),
  setChoices: vi.fn(),
  setup: vi.fn(),
  detectSource: vi.fn(),
  resegment: vi.fn(),
  frameUrl: () => 'about:blank',
}

vi.mock('../src/lib/api', () => ({ api: mockApi }))

const { default: QuadEditor } = await import('../src/components/QuadEditor.svelte')

function source(overrides: Partial<Source> = {}): Source {
  return {
    id: 'src1',
    session_id: 's1',
    idx: 1,
    recorded_at: '2026-10-12T10:00:00Z',
    offset_ms: 0,
    duration_ms: 600000,
    width: 1920,
    height: 1080,
    fps: 30,
    has_original: 1,
    court_preset_id: 'loose',
    status: 'ready',
    rotation_deg: 0,
    features_at: '2026-10-12T11:00:00Z',
    preset_assigned_at: null,
    segment_threshold: 0.45,
    mount: '',
    play_format: '',
    court_corners: null,
    ...overrides,
  }
}

// A fixed layout for the canvas wrapper -- jsdom computes none, and the drag
// math measures the pointer against this rect (see setup-wizard.test.ts).
const FAKE_RECT: DOMRect = {
  left: 0, top: 0, right: 1000, bottom: 800, width: 1000, height: 800,
  x: 0, y: 0, toJSON: () => ({}),
}

class FakePointerEvent extends MouseEvent {
  pointerId: number
  constructor(type: string, init: MouseEventInit & { pointerId: number }) {
    super(type, init)
    this.pointerId = init.pointerId
  }
}

describe('QuadEditor on a processed fence source', () => {
  let target: HTMLDivElement
  let instance: unknown
  let rectSpy: ReturnType<typeof vi.spyOn>
  let onassigned: ReturnType<typeof vi.fn>

  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.listPresets.mockResolvedValue([])
    mockApi.setChoices.mockResolvedValue({ ok: true })
    mockApi.resegment.mockResolvedValue({ count: 12 })
    mockApi.detectSource.mockResolvedValue({ job_id: 'j1', already_running: false })
    target = document.createElement('div')
    document.body.appendChild(target)
    rectSpy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(FAKE_RECT)
    onassigned = vi.fn()
  })

  afterEach(() => {
    if (instance) unmount(instance as never)
    target.remove()
    rectSpy.mockRestore()
    instance = undefined
  })

  let onresegmented: ReturnType<typeof vi.fn>

  function open(s: Source = source()) {
    onresegmented = vi.fn()
    instance = mount(QuadEditor, {
      target,
      props: { sessionId: 's1', sources: [s], onassigned, onresegmented, rallies: [] },
    })
    flushSync()
    const details = target.querySelector('details')
    if (!details) throw new Error('panel details not found')
    details.open = true
    details.dispatchEvent(new Event('toggle'))
    flushSync()
  }

  function click(label: string) {
    const el = target.querySelector(`button[aria-label="${label}"]`) as HTMLButtonElement
    if (!el) throw new Error(`${label} not found`)
    el.click()
    flushSync()
  }

  function button(text: string): HTMLButtonElement | null {
    const all = [...target.querySelectorAll('button')] as HTMLButtonElement[]
    return all.find((b) => b.textContent?.trim() === text) ?? null
  }

  function drag(label: string) {
    const handle = target.querySelector(`button[aria-label="${label}"]`) as HTMLButtonElement
    if (!handle) throw new Error(`${label} not found`)
    handle.setPointerCapture = vi.fn()
    for (const type of ['pointerdown', 'pointermove', 'pointerup']) {
      handle.dispatchEvent(
        new FakePointerEvent(type, { clientX: 100, clientY: 720, pointerId: 1, bubbles: true }),
      )
      flushSync()
    }
  }

  async function settle() {
    for (let i = 0; i < 20; i++) await Promise.resolve()
    flushSync()
  }

  it('sends Fence + Singles and the corners together through /choices, never /setup', async () => {
    // 2026-10-12: no corners stored, so the server refuses Fence + Singles
    // alone -- the three have to arrive in one request.
    open()
    click('mount Fence')
    click('format Singles')
    expect(target.textContent).toMatch(/doubles sidelines and both baselines/)
    expect(button('Save corners')?.disabled).toBe(true)

    drag('near-left corner')
    button('Save corners')!.click()
    await settle()

    expect(mockApi.setup).not.toHaveBeenCalled()
    expect(mockApi.createPreset).not.toHaveBeenCalled()
    expect(mockApi.setChoices).toHaveBeenCalledTimes(1)
    const [id, body] = mockApi.setChoices.mock.calls[0]
    expect(id).toBe('src1')
    expect(body.mount).toBe('fence')
    expect(body.play_format).toBe('singles')
    expect(body.court_corners[0]).toEqual([0.1, 0.9])
    expect(onassigned).toHaveBeenCalledTimes(1)
  })

  it('renders the server\'s refusal as its sentence', async () => {
    const { ApiError } = await import('../src/lib/errors')
    const detail = 'The near-left corner is at (0.9, 0.9) — place them in order.'
    mockApi.setChoices.mockRejectedValue(
      new ApiError('POST -> 400', { status: 400, method: 'POST', path: '/x', detail }),
    )
    open(source({ mount: 'fence', play_format: 'singles',
                  court_corners: '[[0.1,0.9],[0.9,0.9],[0.7,0.4],[0.3,0.4]]' }))
    button('Save corners')!.click()
    await settle()
    expect(target.textContent).toContain(detail)
  })

  it('saves a switch to Ground without touching stored corners', async () => {
    open(source({ mount: 'fence', play_format: 'singles',
                  court_corners: '[[0.1,0.9],[0.9,0.9],[0.7,0.4],[0.3,0.4]]' }))
    click('mount Ground')
    button('Save')!.click()
    await settle()
    expect(mockApi.setChoices).toHaveBeenCalledWith('src1', { mount: 'ground', play_format: 'singles' })
  })

  // -- the re-run, offered here because DetectionPanel is dev-only -----------

  const STORED = '[[0.1,0.9],[0.9,0.9],[0.7,0.4],[0.3,0.4]]'
  /** What the session hands back after the save: fence wanted, not yet cut. */
  const owed = (has_boxes: boolean) =>
    source({
      mount: 'fence', play_format: 'singles', court_corners: STORED,
      scorer_now: 'fence', detected_scorer: null, has_boxes,
    })

  it('offers the fence re-cut after Save corners and runs it behind a confirm', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    open(owed(true))
    expect(target.querySelector('[data-role="choices-follow-up"]')).toBeNull()
    button('Save corners')!.click()
    await settle()
    // Never a pointer to a panel friend mode does not render.
    expect(target.textContent).not.toMatch(/Detection panel/)
    const run = button('Re-segment with fence mode · instant')
    expect(run).not.toBeNull()
    run!.click()
    await settle()
    expect(confirmSpy.mock.calls[0][0]).toMatch(/Re-segmenting discards/)
    expect(mockApi.resegment).toHaveBeenCalledWith('src1')
    expect(onresegmented).toHaveBeenCalledTimes(1)
    expect(target.querySelector('[data-role="choices-follow-up"]')).toBeNull()
    confirmSpy.mockRestore()
  })

  it('prices the full run when there are no boxes, and stops on a declined confirm', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    open(owed(false))
    button('Save corners')!.click()
    await settle()
    button('Run detection · ~15 min')!.click()
    await settle()
    expect(confirmSpy.mock.calls[0][0]).toMatch(/for fence mode/)
    expect(confirmSpy.mock.calls[0][0]).toMatch(/manual boundary edits/)
    expect(mockApi.detectSource).not.toHaveBeenCalled()

    confirmSpy.mockReturnValue(true)
    button('Run detection · ~15 min')!.click()
    await settle()
    expect(mockApi.detectSource).toHaveBeenCalledWith('src1')
    confirmSpy.mockRestore()
  })

  it('offers nothing when the save owes nothing', async () => {
    open(source({ mount: 'fence', play_format: 'singles', court_corners: STORED,
                  scorer_now: 'fence', detected_scorer: 'fence', has_boxes: true }))
    button('Save corners')!.click()
    await settle()
    expect(target.querySelector('[data-role="choices-follow-up"]')).toBeNull()
  })
})
