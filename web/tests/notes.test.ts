import { describe, expect, it } from 'vitest'
import { NOTE_MAX_CHARS, isDirty, normalizeNote, seedNotes } from '../src/lib/notes'
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
