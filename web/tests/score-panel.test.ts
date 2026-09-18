import { mount, unmount } from 'svelte'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ScorePanel from '../src/components/ScorePanel.svelte'
import { DEFAULT_RULES, score } from '../src/lib/score'
import type { Player, ScoreRules, ScoreState } from '../src/lib/score'

let host: HTMLElement | null = null
let app: Record<string, unknown> | null = null

interface Props {
  rules: ScoreRules
  state: ScoreState
  unscored: number
  prompting: boolean
  onwin: (p: Player) => void
}

function render(props: Props) {
  host = document.createElement('div')
  document.body.appendChild(host)
  app = mount(ScorePanel, { target: host, props })
  return host
}

afterEach(() => {
  if (app) unmount(app)
  host?.remove()
  app = host = null
})

describe('ScorePanel', () => {
  it('renders one row per player with the board columns', () => {
    // 'aaaabbbb' x4 splits games 4-4, then 'aaaaaaaa' takes the set 6-4,
    // then 'aaaab' is a love game to A followed by a single point to B --
    // 6-4, 1 game, 0-15 (see score.ts's scoreboardRows).
    const state = score(('aaaabbbb'.repeat(4) + 'aaaaaaaa' + 'aaaab').split('') as Player[], DEFAULT_RULES)
    const el = render({ rules: DEFAULT_RULES, state, unscored: 0, prompting: false, onwin: () => {} })
    const rows = el.querySelectorAll('[data-testid="score-row"]')
    expect(rows.length).toBe(2)
    expect(rows[0].textContent?.replace(/\s+/g, ' ').trim()).toBe('Me 6 1 0')
    expect(rows[1].textContent?.replace(/\s+/g, ' ').trim()).toBe('Opp 4 0 15')
  })

  it('names the winner buttons after the players and fires onwin', () => {
    const onwin = vi.fn()
    const el = render({ rules: { ...DEFAULT_RULES, players: ['Ann', 'Bob'] }, state: score([], DEFAULT_RULES), unscored: 0, prompting: false, onwin })
    const buttons = el.querySelectorAll('button')
    expect(buttons[0].textContent).toContain('Ann')
    expect(buttons[1].textContent).toContain('Bob')
    ;(buttons[1] as HTMLButtonElement).click()
    expect(onwin).toHaveBeenCalledWith('b')
  })

  it('shows the unscored count only when there is one', () => {
    const none = render({ rules: DEFAULT_RULES, state: score([], DEFAULT_RULES), unscored: 0, prompting: false, onwin: () => {} })
    expect(none.textContent).not.toContain('unscored')
    unmount(app!); app = null; host?.remove()
    const some = render({ rules: DEFAULT_RULES, state: score([], DEFAULT_RULES), unscored: 2, prompting: false, onwin: () => {} })
    expect(some.textContent).toContain('2 unscored')
  })

  it('asks who won while prompting', () => {
    const el = render({ rules: DEFAULT_RULES, state: score([], DEFAULT_RULES), unscored: 0, prompting: true, onwin: () => {} })
    expect(el.querySelector('[role="status"]')?.textContent).toContain('Who won')
  })

  it('announces the winner of a finished match', () => {
    const el = render({ rules: DEFAULT_RULES, state: score('a'.repeat(48).split('') as Player[], DEFAULT_RULES), unscored: 0, prompting: false, onwin: () => {} })
    expect(el.textContent).toContain('Me wins')
  })

  it('shows no server at all when the session names no first server', () => {
    // An absent firstServer is unknown, not 'a'. No column, no marker --
    // the table is exactly what it was before servers existed.
    const el = render({ rules: DEFAULT_RULES, state: score([], DEFAULT_RULES), unscored: 0, prompting: false, onwin: () => {} })
    expect(el.querySelectorAll('[data-testid="serve-marker"]').length).toBe(0)
    expect(el.querySelector('[data-testid="score-row"]')?.textContent?.replace(/\s+/g, ' ').trim())
      .toBe('Me 0 0')
  })

  it('marks the serving player, and only them', () => {
    const rules: ScoreRules = { ...DEFAULT_RULES, players: ['Ann', 'Bob'], firstServer: 'a' }
    // One love game to Ann: serve passes to Bob for the second game.
    const el = render({ rules, state: score('aaaa'.split('') as Player[], rules), unscored: 0, prompting: false, onwin: () => {} })
    const marks = el.querySelectorAll('[data-testid="serve-marker"]')
    expect(marks.length).toBe(2)
    expect(marks[0].getAttribute('aria-label')).toBeNull()
    expect(marks[1].getAttribute('aria-label')).toBe('serving')
    expect(marks[0].textContent?.trim()).toBe('')
    expect(marks[1].textContent?.trim()).not.toBe('')
  })

  it('drops the server once the match is decided', () => {
    const rules: ScoreRules = { ...DEFAULT_RULES, firstServer: 'a' }
    const el = render({ rules, state: score('a'.repeat(48).split('') as Player[], rules), unscored: 0, prompting: false, onwin: () => {} })
    expect(el.querySelectorAll('[data-testid="serve-marker"]').length).toBe(0)
  })
})
