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
})
