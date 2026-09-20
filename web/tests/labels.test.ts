import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { beforeEach, describe, expect, it } from 'vitest'
import {
  inheritedDriftPhrase,
  isDetected,
  LabelController,
  LabelWriter,
  overlapFraction,
  persistLabel,
} from '../src/lib/labels'
import type { BoundaryFlag, DetectedRally, LabelAction, Verdict } from '../src/lib/labels'
import type { LabelRecord, Rally } from '../src/lib/types'

function rally(idx: number, over: Partial<Rally> = {}): Rally {
  return {
    id: `r${idx}`,
    session_id: 's',
    source_id: 'src',
    idx,
    start_ms: idx * 10000,
    end_ms: idx * 10000 + 8000,
    det_start_ms: idx * 10000,
    det_end_ms: idx * 10000 + 8000,
    confidence: 0.7,
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

function record(over: Partial<LabelRecord> = {}): LabelRecord {
  return {
    source_id: 'src',
    span_start_ms: 10000,
    span_end_ms: 18000,
    verdict: 'clean',
    boundary_flags: [],
    true_start_ms: null,
    true_end_ms: null,
    ...over,
  }
}

describe('the audit route stays blind', () => {
  // The invariant is "no overlap-resolved verdict reaches the blind pass",
  // and it is worth more than the rest of this file: the blind sample is the
  // only measurement in the project that can see recall over play the
  // detector never proposed (`span_recall` is over spans it did propose, so
  // it is blind to exactly what this pass exists to find). A verdict
  // inherited from a *neighbouring* span rendered beside a window would tell
  // the reviewer what someone already concluded about footage next door, and
  // the number that comes out the far end is then partly a measurement of
  // the detector's own opinion. The 2026-08-20 pass hand-labelled a clip
  // wrong and only the blindness exposed it.
  //
  // Asserted on the files rather than on behaviour because the separation IS
  // structural: neither file builds a controller or resolves an overlap, so
  // there is no flag anyone has to remember -- only two modules that must
  // not start. Reading the source is the only way to pin that, since jsdom
  // has no <video> and the route is verified by hand.
  //
  // Every name the fallback is reachable through, not just the controller:
  // `LabelController` resolves inherited verdicts in its constructor, but
  // `overlapFraction` and `inheritedDriftPhrase` are exported too, and
  // someone reaching for a helper directly would walk straight through a
  // guard that only watched the class.
  const FORBIDDEN = ['LabelController', 'overlapFraction', 'inheritedDriftPhrase']

  /**
   * Comments stripped first. `audit.ts` says out loud that its restore path
   * mirrors `LabelController.restore`, and that sentence is the kind of
   * cross-reference this codebase wants -- a guard that banned naming the
   * thing would be answered by deleting the explanation, which is the
   * opposite of the point. What must not appear is a *use*.
   */
  function code(file: string): string {
    return readFileSync(join(dirname(fileURLToPath(import.meta.url)), file), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '')
  }

  for (const file of ['../src/routes/Audit.svelte', '../src/lib/audit.ts']) {
    it(`${file} resolves no verdict by overlap`, () => {
      const src = code(file)
      for (const name of FORBIDDEN) {
        expect(src).not.toMatch(new RegExp(`\\b${name}\\b`))
      }
    })
  }
})

// The same file tests/test_overlap.py reads. overlapFraction is a hand-port
// of splitstep/db/rallies.py::overlap_fraction, and nothing but this file
// pinned them: the server carries stars across a re-segment with that
// function and `labels score` matches candidates to labelled spans with it,
// so a client drawing the line one millisecond elsewhere shows a verdict the
// scorer does not count, or hides one it does -- with no error anywhere.
// Read with join(), not `new URL(..., import.meta.url)`; tokens.test.ts
// explains the vite quirk.
const OVERLAP_CASES = JSON.parse(
  readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), '../../tests/fixtures/overlap_cases.json'),
    'utf8',
  ),
) as {
  gate: number
  cases: { name: string; a: [number, number]; b: [number, number]; fraction: number; atLeastHalf: boolean }[]
}

describe('overlapFraction', () => {
  it('is gated at the threshold the fixture asserts against', () => {
    // SPAN_OVERLAP_MIN is not exported, so this reads it the way the guard
    // below reads the client floor -- and the Python side asserts the same
    // number against STAR_OVERLAP_MIN, which is what makes the fixture's
    // `atLeastHalf` column mean anything in either language.
    const src = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), '../src/lib/labels.ts'),
      'utf8',
    )
    expect(src).toContain(`const SPAN_OVERLAP_MIN = ${OVERLAP_CASES.gate}\n`)
  })

  for (const c of OVERLAP_CASES.cases) {
    it(c.name, () => {
      // Exact equality, not toBeCloseTo: every fraction in the file
      // round-trips through its decimal literal, so both languages parse
      // the identical double and an honest port produces it bit for bit.
      // A near-miss here is drift, not float noise.
      expect(overlapFraction(c.a[0], c.a[1], c.b[0], c.b[1])).toBe(c.fraction)
      expect(overlapFraction(c.a[0], c.a[1], c.b[0], c.b[1]) >= OVERLAP_CASES.gate).toBe(
        c.atLeastHalf,
      )
      // Both callers pass the pair in whichever order they hold it, so the
      // symmetry is relied on rather than incidental.
      expect(overlapFraction(c.b[0], c.b[1], c.a[0], c.a[1])).toBe(c.fraction)
    })
  }

  it('returns 0.0 for a zero-length span rather than dividing by zero', () => {
    // Covered by the fixture too, but stated here for the JS-only reason:
    // the `max(1, ...)` guard on the denominator is what makes this safe in
    // Python, while in JS the same division would yield Infinity or NaN
    // instead of raising, which is worse -- Infinity clears the >= 0.5 gate
    // and would attribute a verdict to a span of no duration at all.
    expect(overlapFraction(1000, 1000, 1000, 1000)).toBe(0.0)
    expect(overlapFraction(1000, 2000, 1500, 1500)).toBe(0.0)
    expect(overlapFraction(1500, 1500, 1000, 2000)).toBe(0.0)
  })
})

describe('LabelController', () => {
  let c: LabelController

  beforeEach(() => {
    c = new LabelController([rally(1), rally(2), rally(3)], [])
  })

  it('starts on the first rally', () => {
    expect(c.current?.id).toBe('r1')
    expect(c.index).toBe(0)
    expect(c.total).toBe(3)
  })

  it('includes rejected rallies, unlike the review queue', () => {
    // A rejected rally is exactly the not_play the corpus most needs. The
    // queue filters them out because it is a review flow; this is not.
    const withRejected = new LabelController([rally(1, { rejected: 1 }), rally(2)], [])
    expect(withRejected.total).toBe(2)
    expect(withRejected.current?.id).toBe('r1')
  })

  it('leaves hand-made rallies out of the corpus queue entirely', () => {
    // A rally with no detector span has nothing to anchor a corpus row to.
    // Filtering at construction rather than skipping during next()/back() is
    // what keeps index and total truthful -- the "12 / 121" counter must not
    // promise judgements that can never be made.
    const detected = rally(1)
    const handMade = rally(2, { det_start_ms: null, det_end_ms: null })
    const c = new LabelController([detected, handMade], [])
    expect(c.total).toBe(1)
    expect(c.current?.id).toBe('r1')
  })

  it('lands on index 0 when asked to start at a hand-made rally', () => {
    // jumpTo already no-ops on an id it cannot find, so filtering at
    // construction needs no change there -- this pins that it stays true.
    const c = new LabelController(
      [rally(1), rally(2, { det_start_ms: null, det_end_ms: null })], [])
    c.jumpTo('r2')
    expect(c.current?.id).toBe('r1')
  })

  it('setVerdict returns an action carrying the previous state', () => {
    const action = c.setVerdict('clean')
    expect(action).toEqual({
      rallyId: 'r1',
      verdict: 'clean',
      flags: [],
      previousVerdict: null,
      previousFlags: [],
    })
    expect(c.currentVerdict).toBe('clean')
  })

  it('setVerdict does not advance', () => {
    // Flags are added to the same span after the verdict, so the cursor has
    // to stay put -- and 95218d3 removed auto-advance from the queue for the
    // same reason.
    c.setVerdict('clean')
    expect(c.index).toBe(0)
  })

  it('toggleFlag adds then removes, and keeps canonical order', () => {
    c.setVerdict('clean')
    c.toggleFlag('end_late')
    c.toggleFlag('start_early')
    expect(c.currentFlags).toEqual(['start_early', 'end_late'])
    c.toggleFlag('end_late')
    expect(c.currentFlags).toEqual(['start_early'])
  })

  it('flags are disabled until a verdict is set', () => {
    expect(c.flagsEnabled).toBe(false)
    expect(c.toggleFlag('end_late')).toBeNull()
    expect(c.currentFlags).toEqual([])
  })

  it('flags are disabled under not_play and unsure', () => {
    // No boundary to be wrong about on a span holding no rally.
    c.setVerdict('not_play')
    expect(c.flagsEnabled).toBe(false)
    expect(c.toggleFlag('end_late')).toBeNull()

    c.setVerdict('unsure')
    expect(c.flagsEnabled).toBe(false)
  })

  it('changing a verdict to not_play clears flags already set', () => {
    c.setVerdict('clean')
    c.toggleFlag('end_late')
    const action = c.setVerdict('not_play')
    expect(c.currentFlags).toEqual([])
    expect(action?.flags).toEqual([])
  })

  it('flags are enabled under partly', () => {
    c.setVerdict('partly')
    expect(c.flagsEnabled).toBe(true)
  })

  it('seeds from existing labels matched on the exact detector span', () => {
    const seeded = new LabelController(
      [rally(1), rally(2)],
      [record({ span_start_ms: 10000, span_end_ms: 18000, verdict: 'not_play' })],
    )
    expect(seeded.currentVerdict).toBe('not_play')
    // An exact match is the reviewer's judgement of exactly this span, so it
    // carries no inherited badge -- only the overlap fallback does.
    expect(seeded.currentInherited).toBe(false)
    seeded.next()
    expect(seeded.currentVerdict).toBeNull()
    expect(seeded.currentInherited).toBe(false)
  })

  it('seeds from a label whose span moved under a re-segment, and marks it inherited', () => {
    // Measured on the live library: 2026-09-16 source 01 had 44 judgements
    // and a re-segment left 6 of them resolving, because the lookup was an
    // exact match on a detector span the detector had just rewritten. The
    // rows were never lost -- rally_labels has no foreign key precisely so
    // replace_rallies cannot wipe it -- only the display of them was.
    //
    // 200 ms of drift is one rally the reviewer already watched, so the
    // verdict shows. It shows as INHERITED because it is still a judgement
    // of a span slightly different from this one, and nothing is written
    // until the reviewer confirms it against the current span.
    const seeded = new LabelController(
      [rally(1)],
      [record({ span_start_ms: 10200, span_end_ms: 18000, verdict: 'not_play' })],
    )
    expect(seeded.currentVerdict).toBe('not_play')
    expect(seeded.currentInherited).toBe(true)
  })

  it('does not seed from a label overlapping less than half the span', () => {
    // Below the >= 0.5 gate these are two different rallies by the only
    // definition the codebase has (STAR_OVERLAP_MIN, the same rule
    // replace_rallies uses to carry a star and labels score uses to match a
    // candidate). Attributing a verdict across it would invent a judgement
    // of a clip nobody watched -- the objection exact matching was defending
    // against, which the fallback narrows rather than abandons.
    const seeded = new LabelController(
      [rally(1)], // 10000 - 18000
      [record({ span_start_ms: 14100, span_end_ms: 22100, verdict: 'clean' })], // 3900/8000
    )
    expect(seeded.currentVerdict).toBeNull()
    expect(seeded.currentInherited).toBe(false)
  })

  it('takes the best-overlapping label when two clear the gate, whatever the input order', () => {
    // Two old judgements can both survive into one new span after a merge.
    // Ranking has to be a total order or the answer depends on the order the
    // API happened to return the rows in, which would make the same corpus
    // render two different verdicts on two reloads.
    const better = record({ span_start_ms: 10500, span_end_ms: 18000, verdict: 'clean' })
    const worse = record({ span_start_ms: 13000, span_end_ms: 18000, verdict: 'not_play' })

    const forward = new LabelController([rally(1)], [better, worse])
    expect(forward.currentVerdict).toBe('clean')
    expect(forward.currentInherited).toBe(true)

    const reversed = new LabelController([rally(1)], [worse, better])
    expect(reversed.currentVerdict).toBe('clean')
    expect(reversed.currentInherited).toBe(true)
  })

  it('breaks an overlap tie on the nearer start, whatever the input order', () => {
    // Both of these cover 7800 of the rally's 8000 ms, so the fraction alone
    // cannot separate them; the nearer start edge does.
    const rally1 = rally(1, { det_start_ms: 10000, det_end_ms: 18000 })
    const near = record({ span_start_ms: 10100, span_end_ms: 17900, verdict: 'clean' })
    const far = record({ span_start_ms: 10200, span_end_ms: 18000, verdict: 'not_play' })

    expect(new LabelController([rally1], [near, far]).currentVerdict).toBe('clean')
    expect(new LabelController([rally1], [far, near]).currentVerdict).toBe('clean')
  })

  it('breaks a start-edge tie on the tighter span, whatever the input order', () => {
    // The last symmetry the three earlier clauses leave standing. The
    // corpus is append-only ACROSS re-segments -- rows are never rewritten
    // and never deleted -- so one source accumulates spans from several
    // detector runs, and two of those sharing a start edge is ordinary, not
    // contrived: `segment()` places every edge on a fixed sample grid.
    //
    // Both records below score a flat 1.0 (overlapFraction divides by the
    // shorter span, and the rally IS the shorter span), both have drift 0,
    // and both start at 12000 -- so score, drift and start edge are all
    // exhausted and only the end edge is left to decide. `latest_labels`
    // orders by span_start_ms alone, so without a fourth clause the answer
    // is whichever row sqlite happened to emit first: two contradictory
    // verdicts, one of them rendered at random.
    const rally1 = rally(1, { det_start_ms: 12000, det_end_ms: 18000 })
    const tighter = record({ span_start_ms: 12000, span_end_ms: 20000, verdict: 'clean' })
    const looser = record({ span_start_ms: 12000, span_end_ms: 24000, verdict: 'not_play' })

    expect(new LabelController([rally1], [tighter, looser]).currentVerdict).toBe('clean')
    expect(new LabelController([rally1], [looser, tighter]).currentVerdict).toBe('clean')
  })

  it('does not resurrect a hand-made rally through the overlap fallback', () => {
    // A rally with no detector span is filtered out at construction and the
    // fallback must not be a second door back in: rally_labels anchors on
    // (source_id, det_start_ms, det_end_ms), so there is still nothing for a
    // judgement on one of these to attach to, however well some neighbouring
    // labelled span overlaps its player-set bounds.
    const handMade = rally(1, { det_start_ms: null, det_end_ms: null })
    const seeded = new LabelController(
      [handMade],
      [record({ span_start_ms: 10200, span_end_ms: 18000, verdict: 'clean' })],
    )
    expect(seeded.total).toBe(0)
    expect(seeded.currentVerdict).toBeNull()
    expect(seeded.currentInherited).toBe(false)
  })

  it('stops calling a verdict inherited once the reviewer confirms it', () => {
    // The confirming write goes out against this rally's CURRENT det span
    // (LabelWriter sends the span's whole state and the span is the rally's
    // own), so the judgement is no longer second-hand and the next
    // re-segment starts from an exact match again.
    const seeded = new LabelController(
      [rally(1)],
      [record({ span_start_ms: 10200, span_end_ms: 18000, verdict: 'not_play' })],
    )
    expect(seeded.currentInherited).toBe(true)
    seeded.setVerdict('not_play')
    expect(seeded.currentInherited).toBe(false)
  })

  it('brings the inherited badge back when the confirming keystroke is undone', () => {
    // Undo restores the state the reviewer was looking at, and before the
    // keystroke that state was an unconfirmed inherited verdict. Leaving the
    // badge off would show a second-hand judgement as first-hand.
    const seeded = new LabelController(
      [rally(1)],
      [record({ span_start_ms: 10200, span_end_ms: 18000, verdict: 'not_play' })],
    )
    seeded.setVerdict('clean')
    expect(seeded.currentInherited).toBe(false)
    seeded.undo()
    expect(seeded.currentVerdict).toBe('not_play')
    expect(seeded.currentInherited).toBe(true)
  })

  it('does not seed a label from one source onto another source\'s rally at the same span', () => {
    // M2 regression. Sources are independent clips whose timelines each
    // start at 0, and segment() places every edge on a fixed sample grid,
    // so two sources can produce rallies with an identical
    // (det_start_ms, det_end_ms) pair -- guaranteed at the start edge for
    // any rally within the start pad, since the clamp puts both at 0.
    // LabelMode fetches labels per source and flattens them into one list
    // before handing it to the controller, so a span-only key would let
    // source A's verdict render on source B's rally of the same span --
    // exactly what exact-span matching (see the constructor doc comment)
    // exists to prevent, reintroduced through a different door.
    const rallyA = rally(1, { id: 'rA', source_id: 'srcA', det_start_ms: 0, det_end_ms: 8000 })
    const rallyB = rally(1, { id: 'rB', source_id: 'srcB', det_start_ms: 0, det_end_ms: 8000 })
    const labelA = record({ source_id: 'srcA', span_start_ms: 0, span_end_ms: 8000, verdict: 'clean' })

    const seeded = new LabelController([rallyA, rallyB], [labelA])
    expect(seeded.currentVerdict).toBe('clean') // rA: source A's own label
    seeded.next()
    expect(seeded.currentVerdict).toBeNull() // rB: must not inherit it
  })

  it('ignores a verdict-less boundary row when seeding', () => {
    // It carries a corrected span, not a judgement -- rendering it as a
    // verdict would invent one.
    const seeded = new LabelController(
      [rally(1)],
      [record({ verdict: null, boundary_flags: ['end_late'], true_end_ms: 17000 })],
    )
    expect(seeded.currentVerdict).toBeNull()
    expect(seeded.currentFlags).toEqual([])
  })

  it('labelledCount counts rallies with a verdict, seeded or set', () => {
    const seeded = new LabelController(
      [rally(1), rally(2), rally(3)],
      [record({ span_start_ms: 10000, span_end_ms: 18000 })],
    )
    expect(seeded.labelledCount).toBe(1)
    seeded.next()
    seeded.setVerdict('not_play')
    expect(seeded.labelledCount).toBe(2)
  })

  it('next and back move the cursor and clamp at both ends', () => {
    c.back()
    expect(c.index).toBe(0)
    c.next()
    c.next()
    c.next()
    c.next()
    expect(c.index).toBe(2)
  })

  it('undo restores the cursor and clears a first-time verdict', () => {
    c.setVerdict('clean')
    c.next()
    c.setVerdict('not_play')

    // Returns a retraction action (verdict null), not null: "unlabelled" is
    // a state the corpus can hold, so the caller has something to persist.
    // Returning null here is what made undo local-only -- see the
    // 'undoing a label durably' block below.
    const action = c.undo()
    expect(action?.rallyId).toBe('r2')
    expect(action?.verdict).toBeNull()
    expect(c.index).toBe(1)
    expect(c.currentVerdict).toBeNull()
  })

  it('undo of a changed verdict returns the restored verdict to persist', () => {
    c.setVerdict('clean')
    c.setVerdict('not_play')

    const action = c.undo()
    expect(action?.rallyId).toBe('r1')
    expect(action?.verdict).toBe('clean')
    expect(c.currentVerdict).toBe('clean')
  })

  it('undo returns null with nothing to undo', () => {
    expect(c.undo()).toBeNull()
  })

  it('restore puts back exactly the failed rallys state, wherever the cursor is', () => {
    // Mirrors QueueController.revert: a failed POST must not move the user's
    // position or consume their undo.
    const action = c.setVerdict('clean')!
    c.next()
    c.restore(action.rallyId, { verdict: action.previousVerdict, flags: action.previousFlags })
    expect(c.index).toBe(1)
    c.back()
    expect(c.currentVerdict).toBeNull()
  })

  it('brings the inherited badge back when the confirming write fails', () => {
    // The mirror of 'brings the inherited badge back when the confirming
    // keystroke is undone', reached by the other door. #confirm drops the
    // mark optimistically the instant the key is pressed; if the POST then
    // fails, the corpus holds nothing at all for this rally's own span and
    // the verdict on screen is once again second-hand. Leaving the badge
    // off would present an inherited judgement as first-hand -- and it
    // would then evaporate at the next re-segment, which is precisely the
    // loss this feature exists to make visible.
    const seeded = new LabelController(
      [rally(1)],
      [record({ span_start_ms: 10200, span_end_ms: 18000, verdict: 'not_play' })],
    )
    const action = seeded.setVerdict('clean')!
    expect(seeded.currentInherited).toBe(false)

    seeded.restore(action.rallyId, {
      verdict: action.previousVerdict,
      flags: action.previousFlags,
    })

    expect(seeded.currentVerdict).toBe('not_play')
    expect(seeded.currentInherited).toBe(true)
    expect(seeded.currentInheritedFrom).toEqual({ startMs: 10200, endMs: 18000 })
  })

  it('does not re-badge a verdict the server actually accepted', () => {
    // restore falls back to the last state the SERVER holds, which after a
    // landed write is a judgement made against this rally's own span. Only
    // a restore that lands back on the seeded state is second-hand again;
    // anything else is first-hand and must not wear the badge.
    const seeded = new LabelController(
      [rally(1)],
      [record({ span_start_ms: 10200, span_end_ms: 18000, verdict: 'not_play' })],
    )
    seeded.setVerdict('clean')
    seeded.restore('r1', { verdict: 'clean', flags: [] })
    expect(seeded.currentInherited).toBe(false)
  })

  it('does not re-badge when only the flags differ from the seeded state', () => {
    // A flag toggle is a write of its own and it confirms too, so a
    // restore to {seeded verdict, different flags} is a state the reviewer
    // reached by asserting something against this span -- the verdict
    // matching the seed is a coincidence, not an inheritance.
    const seeded = new LabelController(
      [rally(1)],
      [record({ span_start_ms: 10200, span_end_ms: 18000, verdict: 'clean' })],
    )
    seeded.toggleFlag('start_early')
    seeded.restore('r1', { verdict: 'clean', flags: ['start_early'] })
    expect(seeded.currentInherited).toBe(false)
  })

  it('mutating the array returned by currentFlags does not change controller state', () => {
    c.setVerdict('clean')
    c.toggleFlag('start_early')
    const f = c.currentFlags
    f.push('end_late')
    expect(c.currentFlags).toEqual(['start_early'])
  })

  it('mutating action.previousFlags does not change controller state', () => {
    c.setVerdict('clean')
    c.toggleFlag('start_early')
    const action = c.setVerdict('partly')!
    action.previousFlags.push('end_late')
    expect(c.currentFlags).toEqual(['start_early'])
  })

  it('restore puts back the true pre-action flags even if a caller mutated a returned array in between', () => {
    c.setVerdict('clean')
    c.toggleFlag('start_early')
    const action = c.setVerdict('partly')!
    // Simulate some other part of the app holding currentFlags (e.g. for
    // rendering) and mutating it before the failed POST is known about.
    // Before the fix this is the same array object as action.previousFlags,
    // so the mutation silently corrupts the snapshot the failure path -- and
    // now LabelWriter's own record of what the server holds -- depends on.
    c.currentFlags.push('end_late')
    c.restore(action.rallyId, { verdict: action.previousVerdict, flags: action.previousFlags })
    expect(c.currentFlags).toEqual(['start_early'])
  })
})

describe('persisting a label', () => {
  it('sends the verdict and flags for the action rally', async () => {
    const calls: Array<[string, string, string[]]> = []
    const fakeApi = {
      label: async (id: string, verdict: string, flags: string[]) => {
        calls.push([id, verdict, flags])
        return {}
      },
      retractLabel: async () => ({}),
    }
    const c = new LabelController([rally(1)], [])
    const action = c.setVerdict('partly')!
    await persistLabel(action, fakeApi)
    expect(calls).toEqual([['r1', 'partly', []]])
  })

  it('reports a failure so the caller can revert', async () => {
    const fakeApi = {
      label: async () => {
        throw new Error('offline')
      },
      retractLabel: async () => {
        throw new Error('offline')
      },
    }
    const c = new LabelController([rally(1)], [])
    const action = c.setVerdict('clean')!
    expect(await persistLabel(action, fakeApi)).toEqual({ ok: false })
  })
})

// A stand-in for the two label write routes plus the read route, following
// the same resolution rule splitstep/db/labels.py does: rows are appended and
// never edited, the newest row per (source, span) is the current one, and a
// row carrying neither a verdict nor a correction (i.e. a retraction of a
// verdict-only label) leaves the span with no current label at all.
function fakeServer(rallies: Rally[]) {
  interface Row {
    source_id: string
    span_start_ms: number
    span_end_ms: number
    verdict: Verdict | null
    flags: BoundaryFlag[]
  }
  const rows: Row[] = []
  // Every rally this fake server is constructed with, in these tests, is
  // detector-proposed -- fakeServer stands in for the two label write
  // routes, and a hand-made rally has no detector span for a label row to
  // key on in the first place (see LabelController's constructor filter).
  // isDetected narrows that at the type level instead of asserting it away,
  // so a future test that hands fakeServer a hand-made rally fails loudly
  // here rather than silently writing a row keyed on a null span.
  const spanOf = (rallyId: string): DetectedRally => {
    const r = rallies.find((x) => x.id === rallyId)
    if (!r) throw new Error(`no rally ${rallyId}`)
    if (!isDetected(r)) throw new Error(`rally ${rallyId} has no detector span`)
    return r
  }
  const key = (r: { source_id: string; span_start_ms: number; span_end_ms: number }) =>
    `${r.source_id}:${r.span_start_ms}:${r.span_end_ms}`
  const latestFor = (k: string) => [...rows].reverse().find((row) => key(row) === k)

  return {
    rows,
    api: {
      label: async (id: string, verdict: string, flags: string[]) => {
        const r = spanOf(id)
        rows.push({
          source_id: r.source_id,
          span_start_ms: r.det_start_ms,
          span_end_ms: r.det_end_ms,
          verdict: verdict as Verdict,
          flags: flags as BoundaryFlag[],
        })
        return {}
      },
      retractLabel: async (id: string) => {
        const r = spanOf(id)
        const row = {
          source_id: r.source_id,
          span_start_ms: r.det_start_ms,
          span_end_ms: r.det_end_ms,
        }
        // Nothing to retract is a no-op server-side, not an error.
        if (latestFor(key(row))?.verdict == null) return { id: null }
        rows.push({ ...row, verdict: null, flags: [] })
        return { id: 'x' }
      },
    },
    /** What GET /api/sources/{id}/labels would return right now. */
    records(): LabelRecord[] {
      const latest = new Map<string, Row>()
      for (const row of rows) latest.set(key(row), row)
      return [...latest.values()]
        .filter((row) => row.verdict !== null)
        .map((row) => ({
          source_id: row.source_id,
          span_start_ms: row.span_start_ms,
          span_end_ms: row.span_end_ms,
          verdict: row.verdict,
          boundary_flags: row.flags,
          true_start_ms: null,
          true_end_ms: null,
        }))
    },
  }
}

describe('undoing a label durably', () => {
  it('undo of a first-time label returns a retraction to persist', () => {
    const c = new LabelController([rally(1)], [])
    c.setVerdict('clean')

    const action = c.undo()
    // Not null: returning nothing here is what made undo local-only, so the
    // reviewer saw an unlabelled clip while the corpus still said 'clean'.
    expect(action).not.toBeNull()
    expect(action?.rallyId).toBe('r1')
    expect(action?.verdict).toBeNull()
    expect(action?.previousVerdict).toBe('clean')
  })

  it('a first-time label undone reads back as unlabelled after a reload', async () => {
    const rallies = [rally(1), rally(2)]
    const server = fakeServer(rallies)
    const c = new LabelController(rallies, [])

    await persistLabel(c.setVerdict('clean')!, server.api)
    await persistLabel(c.undo()!, server.api)

    // Exactly what LabelMode does on mount: build a fresh controller from
    // whatever the API now reports.
    const reloaded = new LabelController(rallies, server.records())
    expect(reloaded.currentVerdict).toBeNull()
    expect(reloaded.labelledCount).toBe(0)
  })

  it('a retraction supersedes the judgement without erasing it', async () => {
    const rallies = [rally(1)]
    const server = fakeServer(rallies)
    const c = new LabelController(rallies, [])

    await persistLabel(c.setVerdict('clean')!, server.api)
    await persistLabel(c.undo()!, server.api)

    // Append-only: two rows, the judgement still on record behind the
    // retraction that withdrew it.
    expect(server.rows.map((r) => r.verdict)).toEqual(['clean', null])
  })

  it('undo of a changed verdict persists the verdict it restores', async () => {
    const rallies = [rally(1)]
    const server = fakeServer(rallies)
    const c = new LabelController(rallies, [])

    await persistLabel(c.setVerdict('clean')!, server.api)
    await persistLabel(c.setVerdict('not_play')!, server.api)
    await persistLabel(c.undo()!, server.api)

    expect(new LabelController(rallies, server.records()).currentVerdict).toBe('clean')
  })

  it('retracting a span nobody has judged writes nothing', async () => {
    const rallies = [rally(1)]
    const server = fakeServer(rallies)
    const c = new LabelController(rallies, [])
    c.setVerdict('clean')
    // The label POST never happened (offline, say), so the server holds
    // nothing for this span -- the retraction must not invent a row.
    await persistLabel(c.undo()!, server.api)
    expect(server.rows).toEqual([])
  })
})

/**
 * A label API whose calls are released by hand, so a test can decide the
 * order requests complete in. `started` records the order calls were made,
 * `settled` the order they finished.
 */
function controllableApi() {
  const started: string[] = []
  const settled: string[] = []
  const pending: Array<{ tag: string; ok: () => void; fail: () => void }> = []
  let inFlight = 0
  let maxInFlight = 0

  const call = (tag: string) =>
    new Promise<unknown>((resolve, reject) => {
      started.push(tag)
      inFlight += 1
      maxInFlight = Math.max(maxInFlight, inFlight)
      pending.push({
        tag,
        ok: () => {
          inFlight -= 1
          settled.push(tag)
          resolve({})
        },
        fail: () => {
          inFlight -= 1
          settled.push(tag)
          reject(new Error('offline'))
        },
      })
    })

  return {
    started,
    settled,
    get maxInFlight() {
      return maxInFlight
    },
    get pendingTags() {
      return pending.map((p) => p.tag)
    },
    /** Release the nth outstanding call, oldest first. */
    release(index: number, outcome: 'ok' | 'fail' = 'ok') {
      const entry = pending.splice(index, 1)[0]
      if (!entry) throw new Error(`no pending call at ${index}`)
      if (outcome === 'ok') entry.ok()
      else entry.fail()
    },
    api: {
      label: (id: string, verdict: string, flags: string[]) =>
        call(`${id}:${verdict}:${flags.join('+')}`),
      retractLabel: (id: string) => call(`${id}:retract`),
    },
  }
}

/** What LabelMode.apply does, minus the Svelte parts. */
async function applyLike(c: LabelController, w: LabelWriter, action: LabelAction | null) {
  if (!action) return
  const outcome = await w.submit(action)
  if (outcome.status === 'failed') c.restore(action.rallyId, outcome.restore)
  return outcome
}

const tick = () => new Promise((r) => setTimeout(r, 0))

describe('LabelWriter', () => {
  it('holds a rallys next write until the one before it has finished', async () => {
    // The clean -> start_early -> end_late burst from a fast reviewer. Each
    // POST carries the whole state, so out-of-order arrival leaves the
    // server holding an older state than the one on screen.
    const net = controllableApi()
    const c = new LabelController([rally(1)], [])
    const w = new LabelWriter(net.api)

    applyLike(c, w, c.setVerdict('clean'))
    applyLike(c, w, c.toggleFlag('start_early'))
    applyLike(c, w, c.toggleFlag('end_late'))
    await tick()

    expect(net.started).toEqual(['r1:clean:'])
    expect(net.maxInFlight).toBe(1)

    net.release(0)
    await tick()
    expect(net.started).toEqual(['r1:clean:', 'r1:clean:start_early'])

    net.release(0)
    await tick()
    net.release(0)
    await tick()

    // Reached the server in the order the reviewer made them, and the last
    // action is the last thing written.
    expect(net.settled).toEqual([
      'r1:clean:',
      'r1:clean:start_early',
      'r1:clean:start_early+end_late',
    ])
    expect(net.maxInFlight).toBe(1)
  })

  it('completing an earlier write after a later one is not something a rally can do', async () => {
    // The reverse-order case, run deliberately: release the newest
    // outstanding call first. There is only ever one, so "newest" is the
    // oldest -- serialising is what makes the reordering unreachable rather
    // than merely unlikely.
    const net = controllableApi()
    const c = new LabelController([rally(1)], [])
    const w = new LabelWriter(net.api)

    applyLike(c, w, c.setVerdict('clean'))
    applyLike(c, w, c.setVerdict('not_play'))
    await tick()

    expect(net.pendingTags).toEqual(['r1:clean:'])
    net.release(net.pendingTags.length - 1)
    await tick()
    expect(net.pendingTags).toEqual(['r1:not_play:'])
    net.release(net.pendingTags.length - 1)
    await tick()

    expect(net.settled).toEqual(['r1:clean:', 'r1:not_play:'])
  })

  it('does not make one rally wait behind another', async () => {
    // Serialisation is per rally (== per detector span: one source's spans
    // are disjoint, and the server anchors every label to (source, span)).
    // Making it global would stall the whole pass behind one slow POST.
    const net = controllableApi()
    const c = new LabelController([rally(1), rally(2)], [])
    const w = new LabelWriter(net.api)

    applyLike(c, w, c.setVerdict('clean'))
    c.next()
    applyLike(c, w, c.setVerdict('not_play'))
    await tick()

    expect(net.started).toEqual(['r1:clean:', 'r2:not_play:'])
  })

  it('an older writes failure does not revert a newer local state', async () => {
    const net = controllableApi()
    const c = new LabelController([rally(1)], [])
    const w = new LabelWriter(net.api)

    const first = applyLike(c, w, c.setVerdict('clean'))
    const second = applyLike(c, w, c.setVerdict('not_play'))
    await tick()

    net.release(0, 'fail') // the 'clean' write fails, late, with 'not_play' queued
    await tick()
    expect(await first).toEqual({ status: 'superseded' })

    net.release(0) // 'not_play' lands
    await tick()
    expect(await second).toEqual({ status: 'ok' })

    // The reviewer's last action stands. Reverting to the failed write's
    // pre-state would have put 'clean' back under them.
    expect(c.currentVerdict).toBe('not_play')
    expect(net.settled).toEqual(['r1:clean:', 'r1:not_play:'])
  })

  it('the last write failing restores what the server actually holds', async () => {
    const net = controllableApi()
    const c = new LabelController([rally(1)], [])
    const w = new LabelWriter(net.api)

    const first = applyLike(c, w, c.setVerdict('clean'))
    await tick()
    net.release(0)
    await tick()
    expect(await first).toEqual({ status: 'ok' })

    const second = applyLike(c, w, c.setVerdict('not_play'))
    await tick()
    net.release(0, 'fail')
    await tick()

    expect(await second).toEqual({ status: 'failed', restore: { verdict: 'clean', flags: [] } })
    expect(c.currentVerdict).toBe('clean')
  })

  it('a burst that fails outright restores the state from before the burst', async () => {
    // Not to the second-to-last action's state: none of these writes landed,
    // so the last known-persisted state is the seeded one, and restoring
    // anything else would leave the screen asserting something no reader
    // would agree with.
    const rallies = [rally(1)]
    const net = controllableApi()
    const c = new LabelController(rallies, [record({ span_start_ms: 10000, span_end_ms: 18000 })])
    const w = new LabelWriter(net.api)

    const outcomes = [
      applyLike(c, w, c.setVerdict('partly')),
      applyLike(c, w, c.toggleFlag('end_late')),
      applyLike(c, w, c.setVerdict('not_play')),
    ]
    for (let i = 0; i < 3; i += 1) {
      await tick()
      net.release(0, 'fail')
    }
    await tick()

    expect(await outcomes[0]).toEqual({ status: 'superseded' })
    expect(await outcomes[1]).toEqual({ status: 'superseded' })
    expect(await outcomes[2]).toEqual({
      status: 'failed',
      restore: { verdict: 'clean', flags: [] },
    })
    expect(c.currentVerdict).toBe('clean')
    expect(c.currentFlags).toEqual([])
  })

  it('carries an undo through the same queue as the labels it undoes', async () => {
    // A retraction racing the label it retracts is the same bug in its
    // worst form: the label landing last would leave the corpus holding a
    // verdict the reviewer explicitly took back.
    const net = controllableApi()
    const c = new LabelController([rally(1)], [])
    const w = new LabelWriter(net.api)

    applyLike(c, w, c.setVerdict('clean'))
    applyLike(c, w, c.undo())
    await tick()
    net.release(0)
    await tick()
    net.release(0)
    await tick()

    expect(net.settled).toEqual(['r1:clean:', 'r1:retract'])
  })

  it('a failed confirmation of an inherited verdict leaves it reading as inherited', async () => {
    // The two halves of this feature, wired together the way LabelMode
    // wires them (applyLike IS LabelMode.apply minus the Svelte parts).
    // Every other case in this block seeds an empty corpus, so nothing
    // here has ever exercised a failure against a seed that arrived
    // through the overlap fallback -- the one shape where restore has a
    // second thing to put back besides the verdict.
    const net = controllableApi()
    const c = new LabelController(
      [rally(1)],
      [record({ span_start_ms: 10200, span_end_ms: 18000, verdict: 'not_play' })],
    )
    const w = new LabelWriter(net.api)

    const pending = applyLike(c, w, c.setVerdict('clean'))
    await tick()
    expect(c.currentInherited).toBe(false)

    net.release(0, 'fail')
    await pending

    // Nothing reached the corpus, so the span is exactly as unjudged as it
    // was before the keystroke and the verdict on screen is second-hand
    // again -- badge and all.
    expect(c.currentVerdict).toBe('not_play')
    expect(c.currentInherited).toBe(true)
    expect(c.currentInheritedFrom).toEqual({ startMs: 10200, endMs: 18000 })
  })
})

describe('inheritedDriftPhrase', () => {
  // The phrase is the whole point of the badge: "this verdict is inherited"
  // alone does not tell a reviewer what moved, and a reviewer who cannot see
  // how far the judged span sits from the one on screen cannot tell a
  // rounding-width drift from half a rally.

  it('reads each edge as a signed offset from the span on screen', () => {
    expect(inheritedDriftPhrase(10000, 18000, 10200, 18000)).toBe(
      'judged span start +0.2s · end 0.0s',
    )
  })

  it('signs an earlier edge negative and a later one positive', () => {
    expect(inheritedDriftPhrase(10000, 18000, 9500, 18400)).toBe(
      'judged span start -0.5s · end +0.4s',
    )
  })

  it('leaves an unmoved edge unsigned', () => {
    // A zero offset has no direction, and "+0.0s" would read as a drift too
    // small to print rather than as no drift at all.
    expect(inheritedDriftPhrase(10000, 18000, 10000, 17250)).toBe(
      'judged span start 0.0s · end -0.8s',
    )
  })
})

describe('where an inherited verdict came from', () => {
  it('reports the labelled span the verdict was resolved from', () => {
    const seeded = new LabelController(
      [rally(1)], // 10000 - 18000
      [record({ span_start_ms: 10200, span_end_ms: 18000, verdict: 'not_play' })],
    )
    expect(seeded.currentInheritedFrom).toEqual({ startMs: 10200, endMs: 18000 })
  })

  it('reports nothing for an exact match or for an unjudged rally', () => {
    const exact = new LabelController([rally(1), rally(2)], [record({ verdict: 'clean' })])
    expect(exact.currentInheritedFrom).toBeNull()
    exact.next()
    expect(exact.currentInheritedFrom).toBeNull()
  })

  it('follows the inherited mark through a confirmation and its undo', () => {
    // Same state, one getter richer: once the reviewer asserts a verdict
    // against this rally's own span there is no longer a different span to
    // name, and undoing that keystroke puts the reviewer back in front of
    // the second-hand judgement it replaced.
    const seeded = new LabelController(
      [rally(1)],
      [record({ span_start_ms: 10200, span_end_ms: 18000, verdict: 'not_play' })],
    )
    seeded.setVerdict('clean')
    expect(seeded.currentInheritedFrom).toBeNull()
    seeded.undo()
    expect(seeded.currentInheritedFrom).toEqual({ startMs: 10200, endMs: 18000 })
  })
})
