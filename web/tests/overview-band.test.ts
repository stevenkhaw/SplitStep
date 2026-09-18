import { mount, unmount } from 'svelte'
import { afterEach, describe, expect, it } from 'vitest'
import OverviewBand from '../src/components/OverviewBand.svelte'
import type { Rally, Source } from '../src/lib/types'

const source: Source = {
  id: 'src', session_id: 's', idx: 1, recorded_at: '', offset_ms: 0, duration_ms: 100000,
  width: 1920, height: 1080, fps: 30, has_original: 1, court_preset_id: null, status: 'ready', rotation_deg: 0,
  features_at: null, preset_assigned_at: null,
}
function rally(idx: number, over: Partial<Rally> = {}): Rally {
  return {
    id: `r${idx}`, session_id: 's', source_id: 'src', idx, start_ms: idx * 10000, end_ms: idx * 10000 + 5000,
    det_start_ms: null, det_end_ms: null, confidence: 0.5, starred: 0, rejected: 0, point: 0,
    reviewed_at: null, seen_at: null, note: '', winner: '', ...over,
  }
}

let host: HTMLElement | null = null
let app: Record<string, unknown> | null = null
afterEach(() => { if (app) unmount(app); host?.remove(); app = host = null })

describe('OverviewBand', () => {
  it('draws a rejected rally hatched and faint, and says so in its title', () => {
    host = document.createElement('div'); document.body.appendChild(host)
    app = mount(OverviewBand, { target: host, props: {
      rallies: [rally(1), rally(2, { rejected: 1 })], sources: [source], currentId: 'r1',
      windowStartMs: 0, windowEndMs: 20000, onpick: () => {},
    } })
    const buttons = host.querySelectorAll('button')
    expect(buttons[1].className).toContain('hatched')
    expect(buttons[1].getAttribute('title')).toBe('rally 2 (rejected)')
    expect(buttons[0].className).not.toContain('hatched')
  })
})
