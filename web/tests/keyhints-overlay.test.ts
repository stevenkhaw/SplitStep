import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { default: KeyHints } = await import('../src/components/KeyHints.svelte')

function press(key: string, target: EventTarget = window) {
  const e = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })
  target.dispatchEvent(e)
  flushSync()
  return e
}

describe('KeyHints overlay', () => {
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

  const dialog = () => target.querySelector('[role="dialog"]')

  function open(mode: 'queue' | 'timeline' | 'label' = 'queue') {
    instance = mount(KeyHints, { target, props: { mode } })
    flushSync()
  }

  it('renders the inline strip without the overlay', () => {
    open()
    expect(target.querySelectorAll('kbd').length).toBeGreaterThan(0)
    expect(dialog()).toBeNull()
  })

  it('opens on ? and closes on Escape', () => {
    open()
    press('?')
    expect(dialog()).not.toBeNull()
    press('Escape')
    expect(dialog()).toBeNull()
  })

  // The reason this listener is capture-phase. TimelineMode and LabelMode
  // both bind Escape on `window` to leave the mode; an Escape aimed at this
  // overlay must not also kick the reviewer out of the timeline. A
  // bubble-phase implementation passes every other test in this file and
  // fails only this one.
  it('does not let the closing Escape reach a mode handler on window', () => {
    const modeHandler = vi.fn()
    window.addEventListener('keydown', modeHandler)
    try {
      open('timeline')
      press('?')
      expect(dialog()).not.toBeNull()
      modeHandler.mockClear()

      press('Escape')
      expect(dialog()).toBeNull()
      expect(modeHandler).not.toHaveBeenCalled()
    } finally {
      window.removeEventListener('keydown', modeHandler)
    }
  })

  it('does not let the opening ? reach a mode handler either', () => {
    const modeHandler = vi.fn()
    window.addEventListener('keydown', modeHandler)
    try {
      open()
      press('?')
      expect(modeHandler).not.toHaveBeenCalled()
    } finally {
      window.removeEventListener('keydown', modeHandler)
    }
  })

  // Escape belongs to the mode whenever the overlay is not showing -- the
  // guard must be conditional, not a blanket swallow.
  it('leaves Escape alone while the overlay is closed', () => {
    const modeHandler = vi.fn()
    window.addEventListener('keydown', modeHandler)
    try {
      open('timeline')
      press('Escape')
      expect(modeHandler).toHaveBeenCalled()
    } finally {
      window.removeEventListener('keydown', modeHandler)
    }
  })

  it('closes on a backdrop click but not on a click inside the panel', () => {
    open()
    press('?')
    const panel = dialog() as HTMLElement
    const backdrop = panel.parentElement as HTMLElement

    // The panel carries no click handler of its own -- the backdrop checks
    // target === currentTarget instead -- so this is what pins that a click
    // on the content does not fall through and dismiss the overlay.
    panel.querySelector('h2')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    flushSync()
    expect(dialog()).not.toBeNull()

    backdrop.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    flushSync()
    expect(dialog()).toBeNull()
  })

  it('stands down when the ? is typed into a field', () => {
    open()
    const input = document.createElement('input')
    document.body.appendChild(input)
    try {
      press('?', input)
      expect(dialog()).toBeNull()
    } finally {
      input.remove()
    }
  })

  it('lists the mode it was given, not another one', () => {
    open('label')
    press('?')
    const text = dialog()?.textContent ?? ''
    expect(text).toMatch(/Starts early/)
    expect(text).not.toMatch(/Star this rally/)
  })

  it('stops listening once unmounted', () => {
    open()
    unmount(instance as never)
    instance = undefined
    press('?')
    expect(target.querySelector('[role="dialog"]')).toBeNull()
  })
})
