import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { LabelRecord, Rally, SessionDetail, Source } from '../src/lib/types'

// Same shape as labelmode-scrub-bar.test.ts: LabelMode takes no `api` prop,
// so the module is mocked to keep the mount off the network and off a real
// <video>.
const mockApi = {
  sourceLabels: vi.fn().mockResolvedValue([]),
  proxyUrl: () => 'about:blank',
  label: vi.fn().mockResolvedValue({ ok: true }),
  retractLabel: vi.fn().mockResolvedValue({ ok: true }),
}

vi.mock('../src/lib/api', () => ({ api: mockApi }))

HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined)
HTMLMediaElement.prototype.pause = vi.fn()
HTMLMediaElement.prototype.load = vi.fn()

const { default: LabelMode } = await import('../src/components/LabelMode.svelte')

const START_MS = 10_000
const END_MS = 17_600

function source(): Source {
  return {
    id: 'src1',
    session_id: 's1',
    idx: 1,
    recorded_at: '2026-09-16T10:00:00Z',
    offset_ms: 0,
    duration_ms: 600_000,
    width: 1920,
    height: 1080,
    fps: 30,
    has_original: 1,
    court_preset_id: null,
    status: 'ready',
    rotation_deg: 0,
    features_at: null,
    preset_assigned_at: null,
  }
}

function rally(): Rally {
  return {
    id: 'r1',
    session_id: 's1',
    source_id: 'src1',
    idx: 1,
    start_ms: START_MS,
    end_ms: END_MS,
    det_start_ms: START_MS,
    det_end_ms: END_MS,
    confidence: 0.9,
    starred: 0,
    rejected: 0,
    point: 0,
    reviewed_at: null,
    seen_at: null,
    note: '',
    winner: '',
  }
}

function detail(): SessionDetail {
  return {
    session: {
      id: 's1',
      title: 'session',
      played_on: '2026-09-16',
      status: 'ready',
      scoring: null,
    },
    sources: [source()],
    rallies: [rally()],
  }
}

function label(over: Partial<LabelRecord> = {}): LabelRecord {
  return {
    source_id: 'src1',
    span_start_ms: START_MS,
    span_end_ms: END_MS,
    verdict: 'clean',
    boundary_flags: [],
    true_start_ms: null,
    true_end_ms: null,
    ...over,
  }
}

describe('LabelMode marks an inherited verdict', () => {
  let target: HTMLDivElement
  let instance: unknown

  beforeEach(() => {
    vi.clearAllMocks()
    target = document.createElement('div')
    document.body.appendChild(target)
  })

  afterEach(() => {
    if (instance) unmount(instance as never)
    target.remove()
    instance = undefined
  })

  async function render(labels: LabelRecord[]) {
    mockApi.sourceLabels.mockResolvedValue(labels)
    instance = mount(LabelMode, {
      target,
      props: { detail: detail(), onclose: vi.fn() },
    })
    flushSync()
    // The per-source label fetch is async; wait for the controller to have
    // mounted a rally rather than assuming one microtask is enough.
    await vi.waitFor(() => {
      if (!target.querySelector('[aria-label="scrub within rally"]')) {
        throw new Error('label mode not mounted yet')
      }
    })
  }

  function badge(): HTMLElement | null {
    return target.querySelector('[data-testid="inherited-badge"]')
  }

  function text(): string {
    return (badge()?.textContent ?? '').replace(/\s+/g, ' ').trim()
  }

  it('says the span moved, and by how much, for a verdict resolved by overlap', async () => {
    // 2026-09-16 source 01: 44 judgements, 6 of them still resolving after a
    // re-segment. They resolve again now -- but a verdict rendered with no
    // mark would claim the reviewer judged THIS span, when what they judged
    // was one 200ms away. The badge is the difference between confirming a
    // judgement and unknowingly inheriting one.
    await render([label({ span_start_ms: START_MS + 200, verdict: 'not_play' })])

    expect(badge()).not.toBeNull()
    expect(text()).toMatch(/moved/i)
    expect(text()).toContain('judged span start +0.2s · end 0.0s')
  })

  it('shows no badge when the verdict was written against this very span', async () => {
    await render([label({ verdict: 'clean' })])
    expect(badge()).toBeNull()
  })

  it('shows no badge on an unjudged rally', async () => {
    await render([])
    expect(badge()).toBeNull()
  })

  it('drops the badge once the reviewer confirms the verdict', async () => {
    // The confirming write goes out against this rally's own det span, so
    // the judgement stops being second-hand the moment the key lands --
    // leaving the badge up would keep warning about a span that no longer
    // has anything to do with what the corpus now holds.
    await render([label({ span_start_ms: START_MS + 200, verdict: 'not_play' })])
    expect(badge()).not.toBeNull()

    window.dispatchEvent(new KeyboardEvent('keydown', { key: '1' }))
    flushSync()

    expect(badge()).toBeNull()
  })
})
