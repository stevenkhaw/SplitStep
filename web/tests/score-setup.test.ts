import { mount, unmount } from 'svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ScoreSetup from '../src/components/ScoreSetup.svelte'
import { DEFAULT_RULES } from '../src/lib/score'
import type { ScoreRules } from '../src/lib/score'

let host: HTMLElement | null = null
let app: Record<string, unknown> | null = null

interface Props {
  initial: ScoreRules
  onstart: (rules: ScoreRules) => void
  oncancel: () => void
}

function render(props: Props) {
  host = document.createElement('div')
  document.body.appendChild(host)
  app = mount(ScoreSetup, { target: host, props })
  return host
}

afterEach(() => {
  if (app) unmount(app)
  host?.remove()
  app = host = null
})

describe('ScoreSetup', () => {
  it('starts with the initial rules', async () => {
    const onstart = vi.fn()
    const el = render({ initial: DEFAULT_RULES, onstart, oncancel: () => {} })
    ;(el.querySelector('form') as HTMLFormElement).requestSubmit()
    await Promise.resolve()
    expect(onstart).toHaveBeenCalledWith(DEFAULT_RULES)
  })

  it('trims names and refuses an empty one', async () => {
    const onstart = vi.fn()
    const el = render({ initial: DEFAULT_RULES, onstart, oncancel: () => {} })
    const inputs = el.querySelectorAll('input[type="text"]') as NodeListOf<HTMLInputElement>
    inputs[0].value = '  '
    inputs[0].dispatchEvent(new Event('input', { bubbles: true }))
    ;(el.querySelector('form') as HTMLFormElement).requestSubmit()
    await Promise.resolve()
    expect(onstart).not.toHaveBeenCalled()
    expect(el.textContent).toContain('needs a name')
  })

  it('cancel fires oncancel', () => {
    const oncancel = vi.fn()
    const el = render({ initial: DEFAULT_RULES, onstart: () => {}, oncancel })
    ;(el.querySelector('button[type="button"]') as HTMLButtonElement).click()
    expect(oncancel).toHaveBeenCalled()
  })

  it('leaves the first server unrecorded rather than guessing one', async () => {
    // There is no honest default: half the sessions would be labelled with
    // the wrong server. Absent stays absent (see score.ts::firstServer).
    const onstart = vi.fn()
    const el = render({ initial: DEFAULT_RULES, onstart, oncancel: () => {} })
    const server = el.querySelector('select[name="firstServer"]') as HTMLSelectElement
    expect(server.value).toBe('')
    ;(el.querySelector('form') as HTMLFormElement).requestSubmit()
    await Promise.resolve()
    expect(onstart.mock.calls[0][0].firstServer).toBeNull()
  })

  it('offers each player by name as the first server', async () => {
    const onstart = vi.fn()
    const el = render({
      initial: { ...DEFAULT_RULES, players: ['Ann', 'Bob'] },
      onstart,
      oncancel: () => {},
    })
    const server = el.querySelector('select[name="firstServer"]') as HTMLSelectElement
    expect([...server.options].map((o) => o.textContent?.trim())).toEqual([
      'Not recorded',
      'Ann',
      'Bob',
    ])
    server.value = 'b'
    server.dispatchEvent(new Event('change', { bubbles: true }))
    ;(el.querySelector('form') as HTMLFormElement).requestSubmit()
    await Promise.resolve()
    expect(onstart.mock.calls[0][0].firstServer).toBe('b')
  })

  it('tracks a renamed player in the first-server options', async () => {
    const el = render({ initial: DEFAULT_RULES, onstart: () => {}, oncancel: () => {} })
    const nameA = el.querySelectorAll('input[type="text"]')[0] as HTMLInputElement
    nameA.value = 'Sam'
    nameA.dispatchEvent(new Event('input', { bubbles: true }))
    await Promise.resolve()
    const server = el.querySelector('select[name="firstServer"]') as HTMLSelectElement
    expect([...server.options].map((o) => o.textContent?.trim())[1]).toBe('Sam')
  })
})
