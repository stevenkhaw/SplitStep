import { describe, expect, it } from 'vitest'
import {
  NO_CURVE_NOTE,
  choicesFollowUp,
  cutAtPhrase,
  detectionActionLabel,
  detectionActionNote,
  planDetection,
  redetectConfirmMessage,
  regionAssignedNow,
  regionInEffect,
  regionStateLabel,
  sameRegion,
  scorerSwitch,
} from '../src/lib/resegment'
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

// ---------------------------------------------------------------------------
// The copy the one-button panel renders. Pure functions rather than markup
// for the reason CLAUDE.md gives: a `.svelte` file is unreachable from
// vitest in any way that proves a sentence, and these sentences are the
// whole feature -- a button whose label does not state its cost is the bug
// this change exists to remove.
// ---------------------------------------------------------------------------

describe('detectionActionLabel', () => {
  it('states the cost of the expensive run in the label itself', () => {
    // The reviewer re-segmented to 0.15, then ran a detect, and the
    // fifteen-minute run reset them to 0.25. A button that does not say
    // "~15 min" invites that click without warning.
    const label = detectionActionLabel('redetect')
    expect(label).toContain('Run detection')
    expect(label).toContain('15 min')
  })

  it('states that the cheap one is instant, so the two never read alike', () => {
    const label = detectionActionLabel('resegment')
    expect(label).toContain('Re-segment')
    expect(label).toContain('instant')
    expect(label).not.toContain('15 min')
  })

  it('names the disabled state rather than leaving a live-looking button', () => {
    expect(detectionActionLabel('none')).toBe('Nothing to apply')
  })
})

describe('detectionActionNote', () => {
  it('explains a region change in terms of when the quad is applied', () => {
    const note = detectionActionNote(planDetection(detected({ court_preset_points: MOVED_CORNERS }), {
      region: MOVED_CORNERS,
      threshold: 0.15,
    }))
    expect(note).toContain('play region')
    expect(note).toContain('features are built')
  })

  it('explains a threshold-only change as a replay of cached features', () => {
    const note = detectionActionNote(planDetection(detected(), { region: SAME_CORNERS, threshold: 0.3 }))
    expect(note).toContain('cached features')
    expect(note).toContain('no GPU')
  })

  it('says a full run is the only option when there are no features to replay', () => {
    // An `ingested` source: proxy built, never detected. Session hands this
    // panel every non-needs_setup source, so this is a real state, not a
    // hypothetical.
    const note = detectionActionNote(
      planDetection(detected({ features_at: null }), { region: SAME_CORNERS, threshold: 0.3 }),
    )
    expect(note).toContain('no cached features')
  })

  it('says plainly that nothing has changed, rather than going silent', () => {
    const note = detectionActionNote(planDetection(detected(), { region: SAME_CORNERS, threshold: 0.15 }))
    expect(note).toContain('match')
  })

  it('leads with the region when both moved, matching which action is planned', () => {
    // planDetection resolves a region change to `redetect`, which carries
    // the threshold along with it -- so the sentence has to describe the
    // region, or the button and its explanation disagree.
    const note = detectionActionNote(
      planDetection(detected({ court_preset_points: MOVED_CORNERS }), {
        region: MOVED_CORNERS,
        threshold: 0.3,
      }),
    )
    expect(note).toContain('play region')
  })
})

describe('regionInEffect', () => {
  it('is the quad the cached features were actually built under', () => {
    // The user's literal question -- "how do i know what play region it is
    // using?" -- and it had no answer anywhere in the app.
    expect(regionInEffect(detected())).toEqual({ kind: 'quad', points: CORNERS })
  })

  it('is "nothing detected yet" before the first run, not "whole frame"', () => {
    // No features means no run happened; claiming the whole frame was used
    // would describe a detection that never took place.
    expect(regionInEffect(detected({ features_at: null })).kind).toBe('unrun')
  })

  it('is unknown, not "whole frame", when features exist but recorded no region', () => {
    // Three states collapse here and the client must not tell them apart:
    // a pre-016 server, a detect that ran with no preset, a preset row now
    // deleted. Two of the three are "whole frame" and one is not, so the
    // honest answer is that we do not know.
    expect(regionInEffect(detected({ features_preset_points: null })).kind).toBe('unknown')
    expect(regionInEffect(detected({ features_preset_points: undefined })).kind).toBe('unknown')
  })

  it('is unknown with no source at all', () => {
    expect(regionInEffect(undefined).kind).toBe('unknown')
  })
})

describe('regionAssignedNow', () => {
  it('is the quad currently assigned to the source', () => {
    expect(regionAssignedNow(detected({ court_preset_points: MOVED_CORNERS })))
      .toEqual({ kind: 'quad', points: MOVED_CORNERS })
  })

  it('is "whole frame" when no preset is assigned, which is what detection does', () => {
    expect(
      regionAssignedNow(detected({ court_preset_id: null, court_preset_points: null })).kind,
    ).toBe('whole-frame')
  })

  it('is unknown when a preset is assigned but the server did not serve its corners', () => {
    // A server predating migration 016 omits the points key entirely while
    // still reporting the id. Saying "whole frame" there would be a lie
    // about a region that is genuinely in force.
    expect(
      regionAssignedNow(detected({ court_preset_id: 'p2', court_preset_points: undefined })).kind,
    ).toBe('unknown')
  })

  it('is unknown with no source at all', () => {
    expect(regionAssignedNow(undefined).kind).toBe('unknown')
  })
})

describe('regionStateLabel', () => {
  it('gives every state words, so none of them renders as a blank', () => {
    expect(regionStateLabel({ kind: 'quad', points: CORNERS })).toBe('four corners')
    expect(regionStateLabel({ kind: 'whole-frame' })).toBe('whole frame')
    expect(regionStateLabel({ kind: 'unknown' })).toBe('not recorded')
    expect(regionStateLabel({ kind: 'unrun' })).toBe('nothing detected yet')
  })
})

describe('NO_CURVE_NOTE', () => {
  it('says why there is no curve instead of drawing an empty one', () => {
    // A flat line at zero reads as "the detector found no play", which is a
    // false claim; nothing has looked yet.
    expect(NO_CURVE_NOTE).toContain('No score curve yet')
    expect(NO_CURVE_NOTE).toContain('camera profile')
  })
})

describe('planDetection: a changed mount, format or court', () => {
  // 2026-10-12 as it stands after Steven picks Fence + Singles and draws the
  // corners on the session page: same region, same threshold, but the rallies
  // were cut by the pair/subject auto profile.
  const fenceNow = (overrides: Partial<Source> = {}) =>
    detected({ scorer_now: 'fence', detected_scorer: 'pair', has_boxes: true, ...overrides })

  it('re-segments with fence mode when cached boxes suffice', () => {
    const plan = planDetection(fenceNow(), { region: SAME_CORNERS, threshold: 0.15 })
    expect(plan.action).toBe('resegment')
    expect(plan.scorerSwitch).toBe('fence')
    expect(detectionActionLabel(plan.action, plan.scorerSwitch)).toBe(
      'Re-segment with fence mode · instant',
    )
    expect(detectionActionNote(plan)).toContain('cached person boxes')
  })

  it('prices the full run when fence needs boxes the source never saved', () => {
    const plan = planDetection(fenceNow({ has_boxes: false }), {
      region: SAME_CORNERS,
      threshold: 0.15,
    })
    expect(plan.action).toBe('redetect')
    expect(detectionActionLabel(plan.action, plan.scorerSwitch)).toBe('Run detection · ~15 min')
    expect(detectionActionNote(plan)).toContain('did not save')
  })

  it('outranks an unchanged threshold -- the old scale cannot block the switch', () => {
    // threshold === recorded, so without the switch this plan is `none`.
    const plan = planDetection(fenceNow(), { region: SAME_CORNERS, threshold: 0.15 })
    expect(plan.thresholdChanged).toBe(false)
    expect(plan.action).toBe('resegment')
  })

  it('still lets a moved region win and say "region"', () => {
    const plan = planDetection(fenceNow(), { region: MOVED_CORNERS, threshold: 0.15 })
    expect(plan.action).toBe('redetect')
    expect(detectionActionNote(plan)).toContain('play region')
  })

  it('uses plain re-segment wording for a switch to Ground', () => {
    const plan = planDetection(
      detected({ scorer_now: 'subject', detected_scorer: 'pair', has_boxes: false }),
      { region: SAME_CORNERS, threshold: 0.15 },
    )
    expect(plan.action).toBe('resegment')
    expect(plan.scorerSwitch).toBe('subject')
    expect(detectionActionLabel(plan.action, plan.scorerSwitch)).toBe('Re-segment · instant')
    expect(detectionActionNote(plan)).toContain('subject profile')
  })

  it('re-cuts a fence-cut source switched off fence singles, with no fence-scale threshold', () => {
    // Fence + Singles cut at the fence model's 0.25, then the format moved to
    // Doubles: auto now, and 0.25 on the pair scale is a number nobody chose.
    // A non-null switch is what makes the panel seed (and drop) its slider to
    // null, so the re-cut sends no threshold and the new scorer resolves its own.
    const src = detected({
      scorer_now: 'auto', detected_scorer: 'fence', has_boxes: true, segment_threshold: 0.25,
    })
    expect(scorerSwitch(src)).toBe('auto')
    const plan = planDetection(src, { region: SAME_CORNERS, threshold: null })
    expect(plan.action).toBe('resegment')
    expect(plan.thresholdChanged).toBe(false)
    expect(plan.scorerSwitch).toBe('auto')
    expect(detectionActionLabel(plan.action, plan.scorerSwitch)).toBe('Re-segment · instant')
    expect(detectionActionNote(plan)).toContain('cached features')
  })

  it('re-cuts a switch to Ground over an unknown scorer', () => {
    const plan = planDetection(
      detected({ scorer_now: 'subject', detected_scorer: null }),
      { region: SAME_CORNERS, threshold: null },
    )
    expect(plan.action).toBe('resegment')
    expect(plan.scorerSwitch).toBe('subject')
  })

  it('owes nothing when the scorer already matches, or the server did not say', () => {
    expect(
      planDetection(fenceNow({ detected_scorer: 'fence' }), {
        region: SAME_CORNERS,
        threshold: 0.15,
      }).action,
    ).toBe('none')
    // A payload with no fence keys (an older server) is the plan it always was.
    expect(scorerSwitch(detected())).toBeNull()
    expect(planDetection(detected(), { region: SAME_CORNERS, threshold: 0.15 }).action).toBe(
      'none',
    )
  })

  it('names fence mode, not a region, in the re-detect confirm', () => {
    expect(redetectConfirmMessage('fence')).toContain('for fence mode')
    expect(redetectConfirmMessage()).toContain('with the new play region')
  })
})

describe('choicesFollowUp', () => {
  it('offers the fence re-cut after corners are saved over cached boxes', () => {
    const plan = choicesFollowUp(
      detected({ scorer_now: 'fence', detected_scorer: null, has_boxes: true }),
    )
    expect(plan.action).toBe('resegment')
    expect(plan.scorerSwitch).toBe('fence')
  })

  it('offers the priced full run when fence has no boxes to re-score', () => {
    const plan = choicesFollowUp(
      detected({ scorer_now: 'fence', detected_scorer: 'pair', has_boxes: false }),
    )
    expect(plan.action).toBe('redetect')
  })

  it('never plans a threshold re-cut -- this editor has no slider', () => {
    // segment_threshold null would make planDetection fail open on any draft
    // threshold; with none, an unchanged scorer owes nothing.
    expect(choicesFollowUp(detected({ segment_threshold: null })).action).toBe('none')
    expect(
      choicesFollowUp(detected({ scorer_now: 'fence', detected_scorer: 'fence' })).action,
    ).toBe('none')
  })

  it('is nothing for no source', () => {
    expect(choicesFollowUp(undefined).action).toBe('none')
  })
})
