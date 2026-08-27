import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReelItem } from '../src/lib/types'

vi.mock('../src/lib/api', () => ({
  api: { frameUrl: () => 'about:blank' },
}))

const { default: ReelItemList } = await import('../src/components/ReelItemList.svelte')

// jsdom has no PointerEvent constructor at all (unlike every real browser).
// The component only reads clientY and pointerId off the event, both of
// which a MouseEvent stand-in carries fine -- same fix already applied in
// timeline-drag-gain.test.ts, labelmode-scrub-bar.test.ts and
// zoomband-handle-hit-target.test.ts.
class FakePointerEvent extends MouseEvent {
  pointerId: number
  constructor(type: string, init: MouseEventInit & { pointerId: number }) {
    super(type, init)
    this.pointerId = init.pointerId
  }
}

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
    note: '',
    ...overrides,
  }
}

let host: HTMLElement
let component: ReturnType<typeof mount> | null = null

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
})

afterEach(() => {
  if (component) unmount(component)
  component = null
  host.remove()
})

function rows(): HTMLElement[] {
  return [...host.querySelectorAll('[data-reel-row]')] as HTMLElement[]
}

/** jsdom gives every element a zero rect, so the component's only DOM read
 * has to be stubbed for the drag to mean anything. 40px rows from y=0. */
function stubRects(): void {
  rows().forEach((row, i) => {
    row.getBoundingClientRect = () =>
      ({ top: i * 40, bottom: i * 40 + 40, height: 40, left: 0, right: 100,
         width: 100, x: 0, y: i * 40, toJSON: () => ({}) }) as DOMRect
  })
}

function drag(fromRow: number, toClientY: number): void {
  const handle = rows()[fromRow].querySelector('[data-drag-handle]') as HTMLElement
  handle.setPointerCapture = vi.fn()
  handle.releasePointerCapture = vi.fn()
  handle.dispatchEvent(new FakePointerEvent('pointerdown', {
    bubbles: true, pointerId: 1, clientY: fromRow * 40 + 20,
  }))
  flushSync()
  window.dispatchEvent(new FakePointerEvent('pointermove', {
    bubbles: true, pointerId: 1, clientY: toClientY,
  }))
  flushSync()
  window.dispatchEvent(new FakePointerEvent('pointerup', { bubbles: true, pointerId: 1 }))
  flushSync()
}

describe('ReelItemList', () => {
  it('renders one row per item with its duration', () => {
    component = mount(ReelItemList, {
      target: host,
      props: { items: [item(1000), item(9000)], oncommit: vi.fn(), onremove: vi.fn(), onnote: vi.fn() },
    })
    flushSync()
    expect(rows()).toHaveLength(2)
    expect(host.textContent).toContain('4.0s')
  })

  it('badges an item whose clip is not cut', () => {
    component = mount(ReelItemList, {
      target: host,
      props: {
        items: [item(1000, { clip_ready: false })],
        oncommit: vi.fn(), onremove: vi.fn(), onnote: vi.fn(),
      },
    })
    flushSync()
    expect(host.textContent).toContain('missing')
  })

  it('badges an orphan without hiding it', () => {
    // §5: an item whose rally vanished renders as orphaned but stays
    // playable and renderable, never silently dropped.
    component = mount(ReelItemList, {
      target: host,
      props: { items: [item(1000, { rally: null })], oncommit: vi.fn(), onremove: vi.fn(), onnote: vi.fn() },
    })
    flushSync()
    expect(rows()).toHaveLength(1)
    expect(host.textContent).toContain('orphan')
  })

  it('commits a reorder once, on release', () => {
    const oncommit = vi.fn()
    component = mount(ReelItemList, {
      target: host,
      props: { items: [item(1000), item(9000), item(20000)], oncommit, onremove: vi.fn(), onnote: vi.fn() },
    })
    flushSync()
    stubRects()

    drag(0, 101) // past the midpoint of row 2 (y=100)

    expect(oncommit).toHaveBeenCalledTimes(1)
    expect(oncommit.mock.calls[0][0].map((i: ReelItem) => i.start_ms))
      .toEqual([9000, 20000, 1000])
  })

  it('does not commit on pointercancel, even after a real move', () => {
    // An aborted gesture (app switch, an edge-swipe back, a touch
    // scroll-vs-drag conflict) is not a decision -- only a real release
    // means the user meant the reorder. This is the regression test for the
    // finding: endDrag used to be wired to both pointerup and
    // pointercancel, so a cancelled drag committed a reorder nobody
    // confirmed.
    const oncommit = vi.fn()
    const items = [
      item(1000, { source_idx: 1 }),
      item(9000, { source_idx: 2 }),
      item(20000, { source_idx: 3 }),
    ]
    component = mount(ReelItemList, {
      target: host,
      props: { items, oncommit, onremove: vi.fn(), onnote: vi.fn() },
    })
    flushSync()
    stubRects()

    const handle = rows()[0].querySelector('[data-drag-handle]') as HTMLElement
    handle.setPointerCapture = vi.fn()
    handle.releasePointerCapture = vi.fn()
    handle.dispatchEvent(new FakePointerEvent('pointerdown', {
      bubbles: true, pointerId: 1, clientY: 20,
    }))
    flushSync()
    window.dispatchEvent(new FakePointerEvent('pointermove', {
      bubbles: true, pointerId: 1, clientY: 101, // past row 2's midpoint
    }))
    flushSync()
    window.dispatchEvent(new FakePointerEvent('pointercancel', { bubbles: true, pointerId: 1 }))
    flushSync()

    expect(oncommit).not.toHaveBeenCalled()
    // The row must not be left visually mid-drag: the list renders back in
    // its original order, not the in-progress drag order, and the component
    // is not left believing a drag is still in progress.
    const sourceOrder = rows().map((r) => r.textContent?.match(/source (\d+)/)?.[1])
    expect(sourceOrder).toEqual(['01', '02', '03'])
  })

  it('does not commit when the row lands where it started', () => {
    // A click on the handle is not a reorder. Firing anyway would mark the
    // reel dirty and demand a re-render for nothing.
    const oncommit = vi.fn()
    component = mount(ReelItemList, {
      target: host,
      props: { items: [item(1000), item(9000)], oncommit, onremove: vi.fn(), onnote: vi.fn() },
    })
    flushSync()
    stubRects()

    drag(0, 20)

    expect(oncommit).not.toHaveBeenCalled()
  })

  it('removes an item', () => {
    const onremove = vi.fn()
    component = mount(ReelItemList, {
      target: host,
      props: { items: [item(1000), item(9000)], oncommit: vi.fn(), onremove, onnote: vi.fn() },
    })
    flushSync()
    ;(rows()[1].querySelector('[data-remove]') as HTMLElement).click()
    flushSync()
    expect(onremove.mock.calls[0][0].start_ms).toBe(9000)
  })

  it('reorders from the keyboard', () => {
    // The drag handle is a button, and a pointer-only reorder is unreachable
    // without a mouse. Alt+Arrow moves the focused row, which is also the
    // only reorder path svelte-check's a11y rules will accept on a div.
    const oncommit = vi.fn()
    component = mount(ReelItemList, {
      target: host,
      props: { items: [item(1000), item(9000)], oncommit, onremove: vi.fn(), onnote: vi.fn() },
    })
    flushSync()
    const handle = rows()[0].querySelector('[data-drag-handle]') as HTMLElement
    handle.dispatchEvent(new KeyboardEvent('keydown', {
      bubbles: true, key: 'ArrowDown', altKey: true,
    }))
    flushSync()
    expect(oncommit.mock.calls[0][0].map((i: ReelItem) => i.start_ms)).toEqual([9000, 1000])
  })
})

const note = () => host.querySelector('[data-note]') as HTMLButtonElement | null
const noteInput = () => host.querySelector('[data-note-input]') as HTMLInputElement | null
const noteSave = () => host.querySelector('[data-note-save]') as HTMLButtonElement | null

function typeInto(input: HTMLInputElement, value: string): void {
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

describe('ReelItemList notes', () => {
  it('shows the note when present', () => {
    component = mount(ReelItemList, {
      target: host,
      props: {
        items: [item(1000, { note: 'deep lob' })],
        oncommit: vi.fn(), onremove: vi.fn(), onnote: vi.fn(),
      },
    })
    flushSync()
    expect(note()!.textContent).toBe('deep lob')
  })

  it('offers an affordance to add one when absent', () => {
    component = mount(ReelItemList, {
      target: host,
      props: { items: [item(1000)], oncommit: vi.fn(), onremove: vi.fn(), onnote: vi.fn() },
    })
    flushSync()
    expect(note()!.textContent).toBe('+ note')
  })

  it('opens pre-filled with the current note', () => {
    component = mount(ReelItemList, {
      target: host,
      props: {
        items: [item(1000, { note: 'deep lob' })],
        oncommit: vi.fn(), onremove: vi.fn(), onnote: vi.fn(),
      },
    })
    flushSync()
    note()!.click()
    flushSync()
    expect(noteInput()!.value).toBe('deep lob')
  })

  it('commits an open edit when the next note field is clicked', () => {
    // The gesture that lost a full pass of notes (2026-08-26): type a note,
    // click the next row's note, type, click on -- each click used to
    // silently discard the text above it. Switching rows must commit first.
    const onnote = vi.fn()
    component = mount(ReelItemList, {
      target: host,
      props: { items: [item(1000), item(9000)], oncommit: vi.fn(), onremove: vi.fn(), onnote },
    })
    flushSync()
    const notes = host.querySelectorAll('[data-note]')
    ;(notes[0] as HTMLButtonElement).click()
    flushSync()
    typeInto(noteInput()!, 'match point')
    flushSync()
    ;(host.querySelectorAll('[data-note]')[0] as HTMLButtonElement).click() // second row's button (first is now an input)
    flushSync()
    expect(onnote).toHaveBeenCalledTimes(1)
    expect(onnote.mock.calls[0][0].start_ms).toBe(1000)
    expect(onnote.mock.calls[0][1]).toBe('match point')
    // And the second row's editor is open, pre-filled empty.
    expect(noteInput()!.value).toBe('')
  })

  it('does not re-commit an unchanged note on switch', () => {
    const onnote = vi.fn()
    component = mount(ReelItemList, {
      target: host,
      props: {
        items: [item(1000, { note: 'deep lob' }), item(9000)],
        oncommit: vi.fn(), onremove: vi.fn(), onnote,
      },
    })
    flushSync()
    ;(host.querySelectorAll('[data-note]')[0] as HTMLButtonElement).click()
    flushSync()
    ;(host.querySelectorAll('[data-note]')[0] as HTMLButtonElement).click()
    flushSync()
    expect(onnote).not.toHaveBeenCalled()
  })

  it('commits the trimmed note through onnote on Save', () => {
    const onnote = vi.fn()
    component = mount(ReelItemList, {
      target: host,
      props: { items: [item(1000)], oncommit: vi.fn(), onremove: vi.fn(), onnote },
    })
    flushSync()
    note()!.click()
    flushSync()
    typeInto(noteInput()!, '  deep lob  ')
    flushSync()
    noteSave()!.click()
    flushSync()
    expect(onnote).toHaveBeenCalledTimes(1)
    expect(onnote.mock.calls[0][1]).toBe('deep lob')
    expect(onnote.mock.calls[0][0].start_ms).toBe(1000)
  })

  it('commits on Enter', () => {
    const onnote = vi.fn()
    component = mount(ReelItemList, {
      target: host,
      props: { items: [item(1000)], oncommit: vi.fn(), onremove: vi.fn(), onnote },
    })
    flushSync()
    note()!.click()
    flushSync()
    const input = noteInput()!
    typeInto(input, 'deep lob')
    input.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'Enter' }))
    flushSync()
    expect(onnote).toHaveBeenCalledWith(expect.objectContaining({ start_ms: 1000 }), 'deep lob')
  })

  it('cancels on Escape without calling onnote', () => {
    const onnote = vi.fn()
    component = mount(ReelItemList, {
      target: host,
      props: {
        items: [item(1000, { note: 'deep lob' })],
        oncommit: vi.fn(), onremove: vi.fn(), onnote,
      },
    })
    flushSync()
    note()!.click()
    flushSync()
    const input = noteInput()!
    typeInto(input, 'discarded')
    input.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'Escape' }))
    flushSync()

    expect(onnote).not.toHaveBeenCalled()
    // Escape closed the field: the input is gone, the original note shows.
    expect(noteInput()).toBeNull()
    expect(note()!.textContent).toBe('deep lob')
  })

  it('does not commit on a blur -- there is no handler wired to fire one', () => {
    // The exact hazard saveName's comment (Reel.svelte) describes: removing
    // a focused element fires a trailing blur after Enter/Escape has already
    // closed the field. jsdom does not reproduce that trailing blur on its
    // own, so this fires one explicitly at the last live reference to the
    // (now-removed) input, proving no blur handler is listening at all
    // rather than merely that jsdom didn't trigger one.
    const onnote = vi.fn()
    component = mount(ReelItemList, {
      target: host,
      props: { items: [item(1000)], oncommit: vi.fn(), onremove: vi.fn(), onnote },
    })
    flushSync()
    note()!.click()
    flushSync()
    const input = noteInput()!
    typeInto(input, 'discarded')
    input.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'Escape' }))
    flushSync()

    input.dispatchEvent(new Event('blur'))
    flushSync()
    expect(onnote).not.toHaveBeenCalled()
  })

  it('disables Save for a note over the cap', () => {
    component = mount(ReelItemList, {
      target: host,
      props: { items: [item(1000)], oncommit: vi.fn(), onremove: vi.fn(), onnote: vi.fn() },
    })
    flushSync()
    note()!.click()
    flushSync()
    typeInto(noteInput()!, 'x'.repeat(201))
    flushSync()
    expect(noteSave()!.disabled).toBe(true)
  })
})
