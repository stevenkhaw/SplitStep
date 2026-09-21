import { flushSync, mount, unmount } from 'svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import AnchorForm from '../src/components/AnchorForm.svelte'
import { DEFAULT_RULES, score } from '../src/lib/score'
import type { AnchorState, Player, ScoreRules, ScoreState } from '../src/lib/score'

let host: HTMLElement | null = null
let app: Record<string, unknown> | null = null

interface Props {
  rules: ScoreRules
  replayed: ScoreState
  onsave: (state: AnchorState) => void
  oncancel: () => void
}

function render(props: Props) {
  host = document.createElement('div')
  document.body.appendChild(host)
  app = mount(AnchorForm, { target: host, props })
  // A <select>'s bound value lands on the option list after mount; the
  // inputs above it are populated synchronously and would pass without this.
  flushSync()
  return host
}

afterEach(() => {
  if (app) unmount(app)
  host?.remove()
  app = host = null
})

const w = (s: string) => s.split('') as Player[]

function setField(el: HTMLElement, name: string, value: string) {
  const field = el.querySelector(`[name="${name}"]`) as HTMLInputElement | HTMLSelectElement
  field.value = value
  field.dispatchEvent(new Event(field.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }))
  flushSync()
}

describe('AnchorForm', () => {
  it('seeds every field from the replayed state and saves it back as counts', async () => {
    // 6-4, a love game, then 30-15: sets, games and points all non-trivial.
    const state = score(w('aaaabbbb'.repeat(4) + 'aaaaaaaa' + 'aaaa' + 'aab'), DEFAULT_RULES)
    const onsave = vi.fn()
    const el = render({ rules: DEFAULT_RULES, replayed: state, onsave, oncancel: () => {} })
    expect((el.querySelector('[name="set0a"]') as HTMLInputElement).value).toBe('6')
    expect((el.querySelector('[name="set0b"]') as HTMLInputElement).value).toBe('4')
    expect((el.querySelector('[name="gamesA"]') as HTMLInputElement).value).toBe('1')
    expect((el.querySelector('[name="pointsA"]') as HTMLSelectElement).value).toBe('30')
    expect((el.querySelector('[name="pointsB"]') as HTMLSelectElement).value).toBe('15')
    ;(el.querySelector('form') as HTMLFormElement).requestSubmit()
    await Promise.resolve()
    expect(onsave).toHaveBeenCalledWith({ sets: [[6, 4]], games: [1, 0], points: [2, 1] })
  })

  it('offers point labels outside a tiebreak and a count inside one', () => {
    const el = render({ rules: DEFAULT_RULES, replayed: score([], DEFAULT_RULES), onsave: () => {}, oncancel: () => {} })
    expect(el.querySelector('select[name="pointsA"]')).not.toBeNull()
    // Editing games to 6-6 flips the points fields to counts: the engine
    // will read a tiebreak there, so the form must ask for one.
    setField(el, 'gamesA', '6')
    setField(el, 'gamesB', '6')
    expect(el.querySelector('select[name="pointsA"]')).toBeNull()
    expect(el.querySelector('input[name="pointsA"]')).not.toBeNull()
  })

  it('adds and removes completed sets', () => {
    const el = render({ rules: DEFAULT_RULES, replayed: score([], DEFAULT_RULES), onsave: () => {}, oncancel: () => {} })
    expect(el.querySelectorAll('[name^="set"]').length).toBe(0)
    ;(el.querySelector('[data-testid="add-set"]') as HTMLButtonElement).click()
    flushSync()
    expect(el.querySelectorAll('[name^="set"]').length).toBe(2)
    ;(el.querySelector('[data-testid="remove-set"]') as HTMLButtonElement).click()
    flushSync()
    expect(el.querySelectorAll('[name^="set"]').length).toBe(0)
  })

  it('cannot add more sets than the format has left to play', () => {
    const el = render({ rules: DEFAULT_RULES, replayed: score([], DEFAULT_RULES), onsave: () => {}, oncancel: () => {} })
    const add = el.querySelector('[data-testid="add-set"]') as HTMLButtonElement
    add.click(); flushSync()
    add.click(); flushSync()
    expect(el.querySelectorAll('[name^="set"]').length).toBe(4)
    expect(add.disabled).toBe(true)
  })

  it('names an impossible score instead of saving it', async () => {
    const onsave = vi.fn()
    const el = render({ rules: DEFAULT_RULES, replayed: score([], DEFAULT_RULES), onsave, oncancel: () => {} })
    setField(el, 'pointsA', 'Ad')
    setField(el, 'pointsB', 'Ad')
    ;(el.querySelector('form') as HTMLFormElement).requestSubmit()
    await Promise.resolve()
    flushSync()
    expect(onsave).not.toHaveBeenCalled()
    expect(el.querySelector('[role="alert"]')?.textContent).toContain('advantage')
  })

  it('cancels', () => {
    const oncancel = vi.fn()
    const el = render({ rules: DEFAULT_RULES, replayed: score([], DEFAULT_RULES), onsave: () => {}, oncancel })
    ;(el.querySelector('[data-testid="anchor-cancel"]') as HTMLButtonElement).click()
    expect(oncancel).toHaveBeenCalled()
  })
})
