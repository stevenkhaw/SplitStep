import { describe, expect, it } from 'vitest'
import { cutAtPhrase, planDetection, sameRegion } from '../src/lib/resegment'
import type { Source } from '../src/lib/types'

/**
 * The four corners a reviewer actually drew, and a second preset row
 * holding exactly the same four. Two rows, one region -- the wizard writes
 * a fresh preset row on every save, so this pair is the ordinary case, not
 * a contrived one.
 */
const CORNERS: [number, number][] = [
  [0.35, 0.35],
  [0.65, 0.35],
  [0.98, 1.0],
  [0.02, 1.0],
]
const SAME_CORNERS: [number, number][] = CORNERS.map(([x, y]) => [x, y] as [number, number])
const MOVED_CORNERS: [number, number][] = [
  [0.35, 0.35],
  [0.65, 0.35],
  [0.90, 1.0], // one corner dragged in
  [0.02, 1.0],
]

function src(overrides: Partial<Source> = {}): Source {
  return {
    id: 'src1',
    session_id: 's1',
    idx: 1,
    recorded_at: '2026-09-16T10:00:00Z',
    offset_ms: 0,
    duration_ms: 600000,
    width: 1920,
    height: 1080,
    fps: 30,
    has_original: 1,
    court_preset_id: null,
    status: 'ready',
    rotation_deg: 0,
    features_at: '2026-09-16T03:29:00+00:00',
    preset_assigned_at: null,
    segment_threshold: null,
    ...overrides,
  }
}

/** A detected source: features on disk, a region recorded, a threshold recorded. */
function detected(overrides: Partial<Source> = {}): Source {
  return src({
    court_preset_id: 'p2',
    features_preset_id: 'p1',
    court_preset_points: SAME_CORNERS,
    features_preset_points: CORNERS,
    segment_threshold: 0.15,
    ...overrides,
  })
}

describe('sameRegion', () => {
  it('is true for identical corners held by two different preset rows', () => {
    // The whole bug. The wizard stamps a NEW preset row on every save, so
    // comparing ids reports a change every time the reviewer re-confirms
    // the region they already had -- three times on one source, three
    // fifteen-minute detects that changed nothing.
    expect(sameRegion(CORNERS, SAME_CORNERS)).toBe(true)
  })

  it('is false when a corner actually moved', () => {
    expect(sameRegion(CORNERS, MOVED_CORNERS)).toBe(false)
  })

  it('is false when the two quads hold different numbers of corners', () => {
    expect(sameRegion(CORNERS, CORNERS.slice(0, 3))).toBe(false)
  })
})

describe('planDetection: region changes', () => {
  it('asks for a re-detect when the assigned region differs from the features one', () => {
    // The quad is applied when features are BUILT, so cached features are
    // already shaped by the old one -- no threshold can see the new region.
    const plan = planDetection(detected(), { region: MOVED_CORNERS, threshold: 0.15 })
    expect(plan.action).toBe('redetect')
    expect(plan.regionChanged).toBe(true)
  })

  it('asks for nothing when the region is the same four corners in a new row', () => {
    const plan = planDetection(detected(), { region: SAME_CORNERS, threshold: 0.15 })
    expect(plan.action).toBe('none')
    expect(plan.regionChanged).toBe(false)
  })

  it('claims no region change when the features carry no recorded region', () => {
    // A source detected before migration 016 has no features_preset_points.
    // Unknown is not "different" -- warning here would nag every row in an
    // older library, which trains the reviewer to ignore the warning that
    // matters.
    const plan = planDetection(detected({ features_preset_points: null }), {
      region: MOVED_CORNERS,
      threshold: 0.15,
    })
    expect(plan.regionChanged).toBe(false)
    expect(plan.action).toBe('none')
  })

  it('claims no region change when no region is assigned right now', () => {
    const plan = planDetection(detected(), { region: null, threshold: 0.15 })
    expect(plan.regionChanged).toBe(false)
    expect(plan.action).toBe('none')
  })

  it('claims no region change when the payload omits the quads entirely', () => {
    // An older server, or a response cached before migration 016: the keys
    // are absent, which is `undefined` rather than null.
    const { court_preset_points: _a, features_preset_points: _b, ...rest } = detected()
    const plan = planDetection(rest as Source, { region: CORNERS, threshold: 0.15 })
    expect(plan.regionChanged).toBe(false)
    expect(plan.action).toBe('none')
  })
})

describe('planDetection: threshold changes', () => {
  it('asks for a re-segment when only the threshold moved', () => {
    const plan = planDetection(detected(), { region: SAME_CORNERS, threshold: 0.3 })
    expect(plan.action).toBe('resegment')
    expect(plan.thresholdChanged).toBe(true)
    expect(plan.regionChanged).toBe(false)
  })

  it('asks for nothing when the threshold is exactly the recorded one', () => {
    expect(planDetection(detected(), { region: SAME_CORNERS, threshold: 0.15 }).action).toBe('none')
  })

  it('treats a recorded 0 as a recorded threshold, not as absent', () => {
    const at_zero = detected({ segment_threshold: 0 })
    expect(planDetection(at_zero, { region: SAME_CORNERS, threshold: 0 }).action).toBe('none')
    expect(planDetection(at_zero, { region: SAME_CORNERS, threshold: 0.1 }).action).toBe('resegment')
  })

  it('offers a re-segment when nothing recorded what the rallies were cut at', () => {
    // Unknown behaves the opposite way here than it does for the region,
    // deliberately: suppressing the action would leave every pre-migration
    // source permanently unable to re-cut, and a re-segment is 200ms.
    const plan = planDetection(detected({ segment_threshold: null }), {
      region: SAME_CORNERS,
      threshold: 0.25,
    })
    expect(plan.action).toBe('resegment')
  })

  it('asks for nothing while the slider has no value yet', () => {
    const plan = planDetection(detected(), { region: SAME_CORNERS, threshold: null })
    expect(plan.thresholdChanged).toBe(false)
    expect(plan.action).toBe('none')
  })

  it('is one re-detect when both the region and the threshold moved', () => {
    // POST /detect takes a threshold now (da51585), so the expensive run
    // carries the cheap change with it -- there is no second button.
    const plan = planDetection(detected(), { region: MOVED_CORNERS, threshold: 0.3 })
    expect(plan.action).toBe('redetect')
    expect(plan.regionChanged).toBe(true)
    expect(plan.thresholdChanged).toBe(true)
  })
})

describe('planDetection: no cached features', () => {
  const fresh = src({ features_at: null, court_preset_points: CORNERS })

  it('says no score curve is available before the first detect', () => {
    expect(planDetection(fresh, { region: CORNERS, threshold: null }).curveAvailable).toBe(false)
  })

  it('says a curve is available once features exist', () => {
    expect(planDetection(detected(), { region: SAME_CORNERS, threshold: 0.15 }).curveAvailable).toBe(
      true,
    )
  })

  it('needs a full detect for a threshold, because there are no features to replay', () => {
    // segment() replays features.jsonl; without the file there is nothing
    // to re-segment and /scores answers 409.
    expect(planDetection(fresh, { region: CORNERS, threshold: 0.25 }).action).toBe('redetect')
  })

  it('has no curve and no source at all', () => {
    const plan = planDetection(undefined, { region: CORNERS, threshold: 0.25 })
    expect(plan.action).toBe('none')
    expect(plan.curveAvailable).toBe(false)
  })
})

describe('cutAtPhrase', () => {
  it('names the threshold the current rallies were cut at', () => {
    expect(cutAtPhrase(detected())).toBe('cut at 0.15')
  })

  it('pads to the two decimals the slider readout uses', () => {
    expect(cutAtPhrase(detected({ segment_threshold: 0.2 }))).toBe('cut at 0.20')
  })

  it('says unknown, and never a number, when nothing recorded one', () => {
    const phrase = cutAtPhrase(detected({ segment_threshold: null }))
    expect(phrase).toContain('unknown')
    expect(phrase).not.toMatch(/\d/)
  })

  it('says unknown for a payload with no segment_threshold key at all', () => {
    const { segment_threshold: _omitted, ...rest } = detected()
    expect(cutAtPhrase(rest as Source)).toContain('unknown')
  })

  it('says unknown when there is no source', () => {
    expect(cutAtPhrase(undefined)).toContain('unknown')
  })

  it('is carried on the plan, so the panel cannot say one thing and do another', () => {
    expect(planDetection(detected(), { region: SAME_CORNERS, threshold: 0.15 }).cutAt).toBe(
      'cut at 0.15',
    )
  })
})

describe('float comparison', () => {
  it('compares exactly, so a value that never round-tripped is a real change', () => {
    // 0.1 + 0.2 is not 0.3 in any IEEE-754 language, and nothing in this
    // app produces it: the slider emits Number("0.30") and the server
    // round-trips the same double through JSON. See the comment on
    // sameRegion for why exact is the honest comparison here.
    expect(planDetection(detected({ segment_threshold: 0.1 + 0.2 }), {
      region: SAME_CORNERS,
      threshold: 0.3,
    }).thresholdChanged).toBe(true)
  })

  it('sees a slider step as a change and a re-parse of the same step as none', () => {
    const recorded = detected({ segment_threshold: Number('0.15') })
    expect(
      planDetection(recorded, { region: SAME_CORNERS, threshold: Number('0.15') }).thresholdChanged,
    ).toBe(false)
    expect(
      planDetection(recorded, { region: SAME_CORNERS, threshold: Number('0.16') }).thresholdChanged,
    ).toBe(true)
  })

  it('sees a JSON round-trip of the same quad as the same quad', () => {
    // Both sides of the region comparison reach the client through one
    // path -- presets.quad JSON -> Python float -> JSON -> JS double -- and
    // that round-trip is exact in both languages.
    const round_tripped = JSON.parse(JSON.stringify(CORNERS)) as [number, number][]
    expect(sameRegion(CORNERS, round_tripped)).toBe(true)
  })
})
