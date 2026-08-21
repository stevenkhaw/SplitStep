import { describe, expect, it } from 'vitest'
import { NOTE_MAX_CHARS, NoteWriter, isDirty, normalizeNote, seedNotes } from '../src/lib/notes'
import type { Rally } from '../src/lib/types'

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
    note: '',
    ...overrides,
  }
}

describe('normalizeNote', () => {
  it('trims, because trailing space silently widens the rendered caption', () => {
    expect(normalizeNote('  late on the backhand  ')).toBe('late on the backhand')
  })

  it('truncates at the cap rather than rejecting', () => {
    // The field stops accepting input at the cap, so a longer value only
    // arrives by paste. Silently keeping the first 120 characters beats
    // throwing away what the reviewer just pasted.
    expect(normalizeNote('x'.repeat(200))).toHaveLength(NOTE_MAX_CHARS)
  })

  it('trims before measuring, so trailing spaces cannot push a note over', () => {
    expect(normalizeNote('x'.repeat(120) + '   ')).toHaveLength(NOTE_MAX_CHARS)
  })

  it('trims leading space before capping, not after', () => {
    // The order is only observable from the leading side. With three leading
    // spaces and 125 x's, trim-then-cap keeps 120 x's; cap-then-trim would
    // slice at 120 characters INCLUDING the spaces and return only 117.
    // The trailing-space test above cannot see this: slicing a prefix and
    // trimming a trailing run of whitespace commute.
    expect(normalizeNote('   ' + 'x'.repeat(125))).toBe('x'.repeat(120))
  })

  it('leaves a note at exactly the cap alone', () => {
    expect(normalizeNote('x'.repeat(120))).toHaveLength(NOTE_MAX_CHARS)
  })
})

describe('isDirty', () => {
  it('is false when only whitespace differs', () => {
    // Opening the field and closing it must not cost a POST.
    expect(isDirty('  same  ', 'same')).toBe(false)
  })

  it('is true for a real edit', () => {
    expect(isDirty('changed', 'same')).toBe(true)
  })

  it('is true when clearing an existing note', () => {
    expect(isDirty('', 'had one')).toBe(true)
  })

  it('is false for two empties', () => {
    expect(isDirty('   ', '')).toBe(false)
  })
})

describe('seedNotes', () => {
  it('maps rally id to note', () => {
    const m = seedNotes([rally({ id: 'a', note: 'one' }), rally({ id: 'b', note: 'two' })])
    expect(m.get('a')).toBe('one')
    expect(m.get('b')).toBe('two')
  })

  it('omits rallies with no note, so `has` answers the indicator question', () => {
    const m = seedNotes([rally({ id: 'a', note: '' }), rally({ id: 'b', note: 'two' })])
    expect(m.has('a')).toBe(false)
    expect(m.has('b')).toBe(true)
  })
})

/**
 * A setNote API whose calls are released by hand, so a test can control the
 * order requests settle in. Mirrors labels.test.ts's controllableApi, pared
 * down to the one call NoteWriter makes.
 */
function controllableApi() {
  const started: string[] = []
  const settled: string[] = []
  const pending: Array<{ ok: () => void; fail: () => void }> = []

  return {
    started,
    settled,
    get pendingCount() {
      return pending.length
    },
    /** Release the nth outstanding call, oldest first. */
    release(index: number, outcome: 'ok' | 'fail' = 'ok') {
      const entry = pending.splice(index, 1)[0]
      if (!entry) throw new Error(`no pending call at ${index}`)
      if (outcome === 'ok') entry.ok()
      else entry.fail()
    },
    api: {
      setNote: (id: string, note: string) =>
        new Promise<unknown>((resolve, reject) => {
          const tag = `${id}:${note}`
          started.push(tag)
          pending.push({
            ok: () => {
              settled.push(tag)
              resolve({})
            },
            fail: () => {
              settled.push(tag)
              reject(new Error('offline'))
            },
          })
        }),
    },
  }
}

const tick = () => new Promise((r) => setTimeout(r, 0))

describe('NoteWriter', () => {
  it('updates the map on a successful write', async () => {
    const net = controllableApi()
    const w = new NoteWriter(net.api)

    const commit = w.commit('r1', '  slow on the backhand  ')
    await tick()
    net.release(0)
    await commit

    expect(w.get('r1')).toBe('slow on the backhand')
    expect(net.started).toEqual(['r1:slow on the backhand'])
  })

  it('restores the previous value when the write fails', async () => {
    const net = controllableApi()
    // Seeded as though from seedNotes, so there is a real previous value to
    // fall back to -- not just the empty-map default.
    const w = new NoteWriter(net.api, new Map([['r1', 'old note']]))

    const commit = w.commit('r1', 'new note')
    // The optimistic set is synchronous, before the POST settles -- this is
    // what lets the ✎ indicator update immediately instead of waiting on a
    // round trip.
    expect(w.get('r1')).toBe('new note')

    await tick()
    net.release(0, 'fail')
    const outcome = await commit

    expect(outcome.status).toBe('failed')
    expect(w.get('r1')).toBe('old note')
    expect(w.has('r1')).toBe(true)
  })

  it('clearing a note removes the entry, so has() stays the single source of truth', async () => {
    const net = controllableApi()
    const w = new NoteWriter(net.api, new Map([['r1', 'old note']]))

    const commit = w.commit('r1', '   ')
    await tick()
    net.release(0)
    await commit

    expect(w.has('r1')).toBe(false)
    expect(w.get('r1')).toBe('')
    expect(net.started).toEqual(['r1:'])
  })

  it('costs no request when the buffer normalizes to the same value already held', async () => {
    const net = controllableApi()
    const w = new NoteWriter(net.api, new Map([['r1', 'unchanged']]))

    const outcome = await w.commit('r1', '  unchanged  ')

    expect(outcome).toEqual({ status: 'unchanged' })
    expect(net.started).toEqual([])
    expect(w.get('r1')).toBe('unchanged')
  })

  it("an older write's failure does not revert a newer local value", async () => {
    // The fast-reviewer case: a second note is committed before the first
    // POST returns. Reverting to the first write's pre-state on its late
    // failure would put stale text back under the reviewer.
    const net = controllableApi()
    const w = new NoteWriter(net.api)

    const first = w.commit('r1', 'first')
    const second = w.commit('r1', 'second')
    await tick()

    expect(net.started).toEqual(['r1:first']) // second is queued, not yet sent
    net.release(0, 'fail')
    expect(await first).toEqual({ status: 'superseded' })
    expect(w.get('r1')).toBe('second') // not reverted out from under the newer write

    await tick()
    net.release(0)
    expect(await second).toEqual({ status: 'ok' })
    expect(w.get('r1')).toBe('second')
  })

  it('does not make one rally wait behind another', async () => {
    const net = controllableApi()
    const w = new NoteWriter(net.api)

    w.commit('r1', 'a')
    w.commit('r2', 'b')
    await tick()

    expect(net.started).toEqual(['r1:a', 'r2:b'])
  })
})
