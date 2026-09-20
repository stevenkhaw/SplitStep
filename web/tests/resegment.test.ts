import { describe, expect, it } from 'vitest'
import {
  STALE_REGION_WARNING,
  editedBoundaryCount,
  recordedThresholdNote,
  seedThreshold,
  regionNewerThanFeatures,
  staleRegionSources,
  resegmentConfirmMessage,
  resegmentLossPhrase,
  splitCount,
} from '../src/lib/resegment'
import type { Rally, Source } from '../src/lib/types'

function rally(overrides: Partial<Rally> = {}): Rally {
  return {
    id: 'r1',
    session_id: 's1',
    source_id: 'src1',
    idx: 1,
    start_ms: 1000,
    end_ms: 2000,
    det_start_ms: 1000,
    det_end_ms: 2000,
    confidence: 0.9,
    starred: 0,
    rejected: 0,
    point: 0,
    reviewed_at: null,
    seen_at: null,
    note: '',
    winner: '',
    ...overrides,
  }
}

describe('editedBoundaryCount', () => {
  it('is 0 when every rally on the source matches its detected bounds', () => {
    const rallies = [rally({ id: 'r1' }), rally({ id: 'r2', idx: 2 })]
    expect(editedBoundaryCount(rallies, 'src1')).toBe(0)
  })

  it('counts a rally whose start_ms was hand-dragged away from det_start_ms', () => {
    const rallies = [rally({ id: 'r1', start_ms: 900 })]
    expect(editedBoundaryCount(rallies, 'src1')).toBe(1)
  })

  it('counts a rally whose end_ms was hand-dragged away from det_end_ms', () => {
    const rallies = [rally({ id: 'r1', end_ms: 2500 })]
    expect(editedBoundaryCount(rallies, 'src1')).toBe(1)
  })

  it('counts a rally with both bounds edited only once', () => {
    const rallies = [rally({ id: 'r1', start_ms: 900, end_ms: 2500 })]
    expect(editedBoundaryCount(rallies, 'src1')).toBe(1)
  })

  it('ignores edits on rallies belonging to a different source', () => {
    const rallies = [rally({ id: 'r1', source_id: 'src2', start_ms: 900 })]
    expect(editedBoundaryCount(rallies, 'src1')).toBe(0)
  })

  it('counts multiple edited rallies on the same source', () => {
    const rallies = [
      rally({ id: 'r1', start_ms: 900 }),
      rally({ id: 'r2', idx: 2, end_ms: 3500 }),
      rally({ id: 'r3', idx: 3 }), // untouched
    ]
    expect(editedBoundaryCount(rallies, 'src1')).toBe(2)
  })

  it('stars/rejects alone (no bounds change) do not count as edits', () => {
    const rallies = [rally({ id: 'r1', starred: 1, rejected: 0 })]
    expect(editedBoundaryCount(rallies, 'src1')).toBe(0)
  })
})

describe('resegmentLossPhrase', () => {
  it('says nothing was hand-edited for (0, 0)', () => {
    expect(resegmentLossPhrase(0, 0)).toBe('nothing hand-edited')
  })

  it('names a single hand-edited boundary, singular, for (1, 0)', () => {
    expect(resegmentLossPhrase(1, 0)).toBe('1 hand-edited boundary')
  })

  it('names a single split, singular, for (0, 1)', () => {
    expect(resegmentLossPhrase(0, 1)).toBe('1 split')
  })

  it('names both losses, each pluralized independently, for (2, 3)', () => {
    expect(resegmentLossPhrase(2, 3)).toBe('2 hand-edited boundaries and 3 splits')
  })
})

describe('resegmentConfirmMessage', () => {
  it('uses singular "boundary" for a count of 1', () => {
    expect(resegmentConfirmMessage(1, 0)).toContain('1 hand-edited boundary ')
    expect(resegmentConfirmMessage(1, 0)).not.toContain('boundaries')
  })

  it('uses plural "boundaries" for any other positive count', () => {
    expect(resegmentConfirmMessage(3, 0)).toContain('3 hand-edited boundaries')
  })

  it('says nothing was hand-edited when both counts are zero, rather than "0 boundaries"', () => {
    // Both counts zero is still a real prompt -- the reviewer is replacing
    // every rally on the source -- but there is nothing hand-made to name,
    // so the message says so plainly instead of a hollow "0 hand-edited
    // boundaries".
    expect(resegmentConfirmMessage(0, 0)).toContain('nothing hand-edited')
    expect(resegmentConfirmMessage(0, 0)).not.toContain('boundary')
    expect(resegmentConfirmMessage(0, 0)).not.toContain('boundaries')
  })

  it('names stars/rejections as preserved', () => {
    expect(resegmentConfirmMessage(2, 0)).toMatch(/stars and rejections are kept/i)
  })
})

describe('splits versus boundary edits', () => {
  const detected = rally({ id: 'a', start_ms: 1000, end_ms: 5000,
                           det_start_ms: 1000, det_end_ms: 5000 })
  const dragged = rally({ id: 'b', start_ms: 1200, end_ms: 5000,
                          det_start_ms: 1000, det_end_ms: 5000 })
  const handMade = rally({ id: 'c', start_ms: 5000, end_ms: 9000,
                           det_start_ms: null, det_end_ms: null })

  it('counts hand-made rallies as splits, not as edited boundaries', () => {
    // `r.start_ms !== r.det_start_ms` is always true against a null det
    // span, so without the exclusion a split half would be counted here and
    // then described with the wrong noun -- "hand-edited boundaries" for a
    // rally whose boundaries were never edited.
    const all = [detected, dragged, handMade]
    expect(editedBoundaryCount(all, 'src1')).toBe(1)
    expect(splitCount(all, 'src1')).toBe(1)
  })

  it('names both losses, with correct singular and plural', () => {
    expect(resegmentConfirmMessage(1, 0)).toContain('1 hand-edited boundary')
    expect(resegmentConfirmMessage(0, 1)).toContain('1 split')
    expect(resegmentConfirmMessage(2, 3)).toContain('2 hand-edited boundaries')
    expect(resegmentConfirmMessage(2, 3)).toContain('3 splits')
  })

  it('says nothing about splits when there are none', () => {
    expect(resegmentConfirmMessage(2, 0)).not.toContain('split')
  })
})


function src(overrides: Partial<Source> = {}): Source {
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
    features_at: null,
    preset_assigned_at: null,
    segment_threshold: null,
    ...overrides,
  }
}

describe('regionNewerThanFeatures', () => {
  it('is true when the region was assigned after the features were written', () => {
    // The case the warning exists for: re-segment replays features built
    // under the OLD quad, so it cannot see this region at all.
    expect(
      regionNewerThanFeatures(
        src({
          features_at: '2026-09-01T10:00:00+00:00',
          preset_assigned_at: '2026-09-02T10:00:00+00:00',
        }),
      ),
    ).toBe(true)
  })

  it('is false when the features were rebuilt after the region was assigned', () => {
    expect(
      regionNewerThanFeatures(
        src({
          features_at: '2026-09-02T10:00:00+00:00',
          preset_assigned_at: '2026-09-01T10:00:00+00:00',
        }),
      ),
    ).toBe(false)
  })

  it('is false when the two timestamps are equal', () => {
    // Strictly newer, not "not older": equal means the detect that wrote
    // these features already had this region.
    const t = '2026-09-01T10:00:00+00:00'
    expect(regionNewerThanFeatures(src({ features_at: t, preset_assigned_at: t }))).toBe(false)
  })

  it('is false when no region has ever been assigned', () => {
    expect(
      regionNewerThanFeatures(
        src({ features_at: '2026-09-01T10:00:00+00:00', preset_assigned_at: null }),
      ),
    ).toBe(false)
  })

  it('is false when the source has never been detected', () => {
    expect(
      regionNewerThanFeatures(
        src({ features_at: null, preset_assigned_at: '2026-09-01T10:00:00+00:00' }),
      ),
    ).toBe(false)
  })

  it('is false when both timestamps are unknown, so an old library never nags', () => {
    // Every row in a library predating migration 014 has a null
    // preset_assigned_at, including rows whose region is genuinely older
    // than their features. Unknown is not a reason to warn.
    expect(regionNewerThanFeatures(src())).toBe(false)
  })
})

describe('staleRegionSources', () => {
  const STALE = {
    features_at: '2026-09-16T03:29:00+00:00',
    preset_assigned_at: '2026-09-16T17:49:00+00:00',
  }
  const FRESH = {
    features_at: '2026-09-16T17:49:00+00:00',
    preset_assigned_at: '2026-09-16T03:29:00+00:00',
  }

  it('picks out the sources whose region the cached features predate', () => {
    const stale = src({ id: 'src2', idx: 2, ...STALE })
    expect(staleRegionSources([src({ id: 'src1', idx: 1, ...FRESH }), stale])).toEqual([stale])
  })

  it('is empty when nothing is stale, which is the silent case', () => {
    expect(staleRegionSources([src({ ...FRESH }), src({ id: 'src2', idx: 2 })])).toEqual([])
  })

  it('keeps the caller-given order so the banner reads in source order', () => {
    const a = src({ id: 'a', idx: 1, ...STALE })
    const b = src({ id: 'b', idx: 2, ...STALE })
    expect(staleRegionSources([a, b]).map((s) => s.id)).toEqual(['a', 'b'])
  })

  it('is empty for an empty list', () => {
    expect(staleRegionSources([])).toEqual([])
  })
})

describe('STALE_REGION_WARNING', () => {
  it('says both halves: the region changed, and re-segment still uses the old one', () => {
    // Pinned because this sentence is one of exactly two places the app
    // says a quad change needs a re-detect rather than a re-segment (see
    // CLAUDE.md, "Play region"). Losing either half makes it a complaint
    // with no instruction in it.
    expect(STALE_REGION_WARNING).toContain('Play region changed after the last detect')
    expect(STALE_REGION_WARNING).toContain('re-segment still uses the old one')
  })
})

describe('seedThreshold', () => {
  // The reported bug, reduced to one function. The slider used to start at
  // whatever /scores called the profile default, which is a statement about
  // the DETECTOR, not about the rallies on screen. Source 2026-09-16/01 was
  // cut at 0.15 and the slider read 0.25.
  it('is the threshold the source records, when it has one', () => {
    expect(seedThreshold(src({ segment_threshold: 0.15 }))).toBe(0.15)
  })

  it('is null when the source has never recorded one', () => {
    // Null is not a number, deliberately: every source segmented before
    // migration 015 has no honest value, and 0.25 would be a guess the
    // reviewer would read as a fact. Null sends the panel back to asking
    // /scores for the profile default, exactly as it did before.
    expect(seedThreshold(src({ segment_threshold: null }))).toBeNull()
  })

  it('is null when there is no source at all', () => {
    expect(seedThreshold(undefined)).toBeNull()
  })

  it('does not treat a recorded 0 as absent', () => {
    // A `||` fallback would turn this into null and silently re-seed from
    // the profile default -- the falsy-zero bug, on the one value where the
    // difference between "recorded" and "unknown" is the whole point.
    expect(seedThreshold(src({ segment_threshold: 0 }))).toBe(0)
  })
})

describe('recordedThresholdNote', () => {
  it('names the number the current rallies were cut at', () => {
    const note = recordedThresholdNote(src({ segment_threshold: 0.15 }))
    expect(note?.value).toBe('0.15')
    expect(note?.lead).toContain('cut at')
  })

  it('formats to the same two decimals the slider readout uses', () => {
    // Otherwise the sentence and the readout above it disagree about the
    // same number, which is the shape of the bug being fixed.
    expect(recordedThresholdNote(src({ segment_threshold: 0.2 }))?.value).toBe('0.20')
  })

  it('carries no number at all when nothing was recorded', () => {
    const note = recordedThresholdNote(src({ segment_threshold: null }))
    expect(note?.value).toBeNull()
    expect(note?.lead).toContain('before')
  })

  it('says nothing when there is no source', () => {
    expect(recordedThresholdNote(undefined)).toBeNull()
  })

  it('treats a payload with no segment_threshold key at all as unknown', () => {
    // An older server, or a response cached before migration 015 shipped:
    // the field is `undefined`, not null. Reaching .toFixed on it throws
    // inside the render and takes the whole session route down to
    // "Loading…" -- observed exactly that way across five unrelated test
    // files the first time this function read the field directly.
    const { segment_threshold: _omitted, ...rest } = src()
    const note = recordedThresholdNote(rest as Source)
    expect(note?.value).toBeNull()
    expect(seedThreshold(rest as Source)).toBeNull()
  })
})
