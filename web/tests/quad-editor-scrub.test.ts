import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Source } from '../src/lib/types'

// Same mocking approach as resegment-panel.test.ts: QuadEditor imports
// `../lib/api` directly rather than taking it as a prop.
const mockApi = {
  listSessions: vi.fn(),
  getSession: vi.fn(),
  star: vi.fn(),
  reject: vi.fn(),
  reviewed: vi.fn(),
  setBounds: vi.fn(),
  resegment: vi.fn(),
  scores: vi.fn(),
  listPresets: vi.fn().mockResolvedValue([]),
  createPreset: vi.fn(),
  setPreset: vi.fn(),
  jobs: vi.fn(),
  proxyUrl: () => 'about:blank',
  frameUrl: (sessionId: string, idx: number, atMs = 0) =>
    `/media/${sessionId}/${idx}/frame.jpg?at_ms=${atMs}`,
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
    duration_ms: 1173905,
    width: 3840,
    height: 2160,
    fps: 30,
    has_original: 1,
    court_preset_id: null,
    status: 'ready',
    ...overrides,
  }
}

describe('QuadEditor frame scrubbing', () => {
  let target: HTMLDivElement
  let instance: unknown

  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.listPresets.mockResolvedValue([])
    target = document.createElement('div')
    document.body.appendChild(target)
  })

  afterEach(() => {
    if (instance) unmount(instance as never)
    target.remove()
    instance = undefined
  })

  function open(s: Source = source()) {
    instance = mount(QuadEditor, {
      target,
      props: { sessionId: 's1', sources: [s], onassigned: vi.fn() },
    })
    flushSync()
  }

  function frameAtMs(): number {
    const img = target.querySelector('img')
    if (!img) throw new Error('frame image not found')
    return Number(new URL(img.getAttribute('src') ?? '', 'http://x').searchParams.get('at_ms'))
  }

  function click(label: string) {
    const el = target.querySelector(`button[aria-label="${label}"]`)
    if (!el) throw new Error(`${label} button not found`)
    ;(el as HTMLButtonElement).click()
    flushSync()
  }

  function slider(): HTMLInputElement {
    const el = target.querySelector('input[type="range"]')
    if (!el) throw new Error('scrub slider not found')
    return el as HTMLInputElement
  }

  it('opens past t=0, where phone footage is usually still black', () => {
    open()
    expect(frameAtMs()).toBe(30000)
  })

  it('opens on a real frame for a clip shorter than the default offset', () => {
    open(source({ duration_ms: 2000, fps: 30 }))
    // lastSafeFrameMs(2000, 30): the server's own end-of-stream clamp.
    expect(frameAtMs()).toBe(1966)
  })

  it('steps exactly one frame forward and back', () => {
    open()
    click('next frame')
    expect(frameAtMs()).toBe(30033)
    click('previous frame')
    expect(frameAtMs()).toBe(30000)
  })

  it('steps by a second', () => {
    open()
    click('forward one second')
    expect(frameAtMs()).toBe(31000)
    click('back one second')
    expect(frameAtMs()).toBe(30000)
  })

  it('never scrubs before the start of the clip', () => {
    open(source({ duration_ms: 2000, fps: 30 }))
    click('back one second')
    click('back one second')
    click('back one second')
    expect(frameAtMs()).toBe(0)
  })

  it('does not fetch a new frame until the slider is released', () => {
    open()
    const el = slider()
    el.value = '90000'
    el.dispatchEvent(new Event('input', { bubbles: true }))
    flushSync()
    // Dragging moves the readout only -- one ffmpeg extraction per distinct
    // timestamp is too expensive to fire on every input event.
    expect(frameAtMs()).toBe(30000)

    el.dispatchEvent(new Event('change', { bubbles: true }))
    flushSync()
    expect(frameAtMs()).toBe(90000)
  })

  it('clamps a slider release past the end of the clip to the last decodable frame', () => {
    const s = source({ duration_ms: 2000, fps: 30 })
    open(s)
    const el = slider()
    el.value = '999999'
    el.dispatchEvent(new Event('change', { bubbles: true }))
    flushSync()
    expect(frameAtMs()).toBe(1966)
  })
})
