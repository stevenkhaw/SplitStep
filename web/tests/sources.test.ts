import { describe, expect, it } from 'vitest'
import { resolveSelectedTab, scopeToSource, scopedStatus, sourceTabs } from '../src/lib/sources'
import type { Rally, Source } from '../src/lib/types'

function source(over: Partial<Source> = {}): Source {
  return {
    id: 'src1',
    session_id: 's1',
    idx: 1,
    recorded_at: '2026-08-19T10:00:00Z',
    offset_ms: 0,
    duration_ms: 600000,
    width: 1920,
    height: 1080,
    fps: 30,
    has_original: 1,
    court_preset_id: null,
    status: 'ready',
    rotation_deg: 0,
    ...over,
  }
}

function rally(over: Partial<Rally> = {}): Rally {
  return {
    id: 'r1',
    session_id: 's1',
    source_id: 'src1',
    idx: 1,
    start_ms: 1000,
    end_ms: 9000,
    det_start_ms: 1000,
    det_end_ms: 9000,
    confidence: 0.8,
    starred: 0,
    rejected: 0,
    point: 0,
    reviewed_at: null,
    seen_at: null,
    note: '',
    winner: '',
    ...over,
  }
}

describe('sourceTabs', () => {
  it('orders tabs by idx, not by array order', () => {
    const sources = [source({ id: 'src2', idx: 2 }), source({ id: 'src1', idx: 1 })]
    const tabs = sourceTabs(sources, [])
    expect(tabs.map((t) => t.id)).toEqual(['src1', 'src2'])
  })

  it('counts each tab’s rallies by source_id', () => {
    const sources = [source({ id: 'src1', idx: 1 }), source({ id: 'src2', idx: 2 })]
    const rallies = [
      rally({ id: 'r1', source_id: 'src1' }),
      rally({ id: 'r2', source_id: 'src1' }),
      rally({ id: 'r3', source_id: 'src2' }),
    ]
    const tabs = sourceTabs(sources, rallies)
    expect(tabs).toEqual([
      { id: 'src1', idx: 1, rallyCount: 2 },
      { id: 'src2', idx: 2, rallyCount: 1 },
    ])
  })

  it('excludes a needs_setup source -- it has no proxy and no rallies to review yet', () => {
    const sources = [
      source({ id: 'src1', idx: 1, status: 'needs_setup' }),
      source({ id: 'src2', idx: 2, status: 'ready' }),
    ]
    const tabs = sourceTabs(sources, [])
    expect(tabs.map((t) => t.id)).toEqual(['src2'])
  })

  it('is empty when every source needs setup', () => {
    const sources = [source({ id: 'src1', idx: 1, status: 'needs_setup' })]
    expect(sourceTabs(sources, [])).toEqual([])
  })

  it('gives a source with no rallies yet a zero count rather than dropping it', () => {
    const sources = [source({ id: 'src1', idx: 1, status: 'ready' })]
    expect(sourceTabs(sources, [])).toEqual([{ id: 'src1', idx: 1, rallyCount: 0 }])
  })
})

describe('resolveSelectedTab', () => {
  const tabs = [
    { id: 'src1', idx: 1, rallyCount: 2 },
    { id: 'src2', idx: 2, rallyCount: 3 },
  ]

  it('has no selection for zero tabs', () => {
    expect(resolveSelectedTab([], null)).toBeNull()
  })

  it('has no selection for exactly one tab -- the strip does not render', () => {
    expect(resolveSelectedTab([{ id: 'src1', idx: 1, rallyCount: 2 }], null)).toBeNull()
    // Even if something upstream had a selection set, one tab means no tab
    // UI, so the selection collapses back to null.
    expect(resolveSelectedTab([{ id: 'src1', idx: 1, rallyCount: 2 }], 'src1')).toBeNull()
  })

  it('defaults to the first tab when nothing was selected before', () => {
    expect(resolveSelectedTab(tabs, null)).toBe('src1')
  })

  it('keeps the current selection when it still names a tab', () => {
    expect(resolveSelectedTab(tabs, 'src2')).toBe('src2')
  })

  it('falls back to the first tab when the current selection no longer exists', () => {
    // E.g. a re-segment or a source finishing setup changed the tab set out
    // from under whatever was picked.
    expect(resolveSelectedTab(tabs, 'src-gone')).toBe('src1')
  })
})

describe('scopeToSource', () => {
  it('passes the detail through unchanged when sourceId is null', () => {
    const detail = { rallies: [rally({ id: 'r1' })] }
    expect(scopeToSource(detail, null)).toBe(detail)
  })

  it('filters rallies to the given source', () => {
    const detail = {
      rallies: [
        rally({ id: 'r1', source_id: 'src1' }),
        rally({ id: 'r2', source_id: 'src2' }),
        rally({ id: 'r3', source_id: 'src1' }),
      ],
    }
    const scoped = scopeToSource(detail, 'src1')
    expect(scoped.rallies.map((r) => r.id)).toEqual(['r1', 'r3'])
  })

  it('does not mutate the original detail or its rallies array', () => {
    const rallies = [rally({ id: 'r1', source_id: 'src1' }), rally({ id: 'r2', source_id: 'src2' })]
    const detail = { rallies }
    scopeToSource(detail, 'src1')
    expect(detail.rallies).toBe(rallies)
    expect(detail.rallies).toHaveLength(2)
  })

  it('resolves an unknown sourceId to an empty rally list, not a fallback', () => {
    const detail = { rallies: [rally({ id: 'r1', source_id: 'src1' })] }
    const scoped = scopeToSource(detail, 'no-such-source')
    expect(scoped.rallies).toEqual([])
  })

  it('preserves other fields of the detail when scoping', () => {
    const detail = { session: { id: 's1' }, rallies: [rally({ id: 'r1', source_id: 'src1' })] }
    const scoped = scopeToSource(detail, 'src1')
    expect(scoped.session).toEqual({ id: 's1' })
  })
})

describe('scopedStatus', () => {
  const detail = {
    sources: [
      { id: 'src1', status: 'ready' },
      { id: 'src2', status: 'failed' },
    ],
    session: { status: 'detecting' },
  }

  it('reports the selected source\'s own status', () => {
    expect(scopedStatus(detail, 'src2')).toBe('failed')
  })

  it('falls back to the session status with no tab selected', () => {
    expect(scopedStatus(detail, null)).toBe('detecting')
  })

  it('falls back to the session status when the id matches nothing', () => {
    // A stale selection after the source list changed: the session-level
    // truth beats a wrong-but-plausible per-source guess.
    expect(scopedStatus(detail, 'gone')).toBe('detecting')
  })
})
