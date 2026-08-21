import { beforeEach, describe, expect, it } from 'vitest'
import { LabelController, LabelWriter, persistLabel } from '../src/lib/labels'
import type { BoundaryFlag, LabelAction, Verdict } from '../src/lib/labels'
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
    reviewed_at: null,
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
    seeded.next()
    expect(seeded.currentVerdict).toBeNull()
  })

  it('does not seed from a label whose span merely overlaps', () => {
    // A re-segment that moved this edge produced different detector output,
    // so the old judgement is not a judgement of this span. Matching by
    // overlap here would silently attribute a verdict to a clip nobody
    // watched.
    const seeded = new LabelController(
      [rally(1)],
      [record({ span_start_ms: 10200, span_end_ms: 18000, verdict: 'not_play' })],
    )
    expect(seeded.currentVerdict).toBeNull()
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
// the same resolution rule bootleg/db/labels.py does: rows are appended and
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
  const spanOf = (rallyId: string) => {
    const r = rallies.find((x) => x.id === rallyId)
    if (!r) throw new Error(`no rally ${rallyId}`)
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
})
