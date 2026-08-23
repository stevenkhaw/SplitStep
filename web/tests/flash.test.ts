import { describe, expect, it } from 'vitest'
import { flashFor } from '../src/lib/flash'
import type { PersistableAction, QueueAction } from '../src/lib/queue'

function act(over: Partial<PersistableAction> = {}): QueueAction {
  return {
    kind: 'star',
    rallyId: 'r1',
    starred: false,
    rejected: false,
    point: false,
    previousStarred: false,
    previousRejected: false,
    previousPoint: false,
    ...over,
  }
}

describe('flashFor', () => {
  // Star, point and reject are all toggles, so the confirmation has to say
  // which way it went. "Starred" on a keypress that un-starred would be a
  // lie, and a worse one than showing nothing.
  it('names the direction a star went', () => {
    expect(flashFor(act({ kind: 'star', starred: true }))).toEqual({
      label: 'Starred',
      glyph: '★',
      tone: 'star',
    })
    expect(flashFor(act({ kind: 'star', starred: false, previousStarred: true }))).toEqual({
      label: 'Unstarred',
      glyph: '★',
      tone: 'star',
    })
  })

  it('names the direction a point went', () => {
    expect(flashFor(act({ kind: 'point', point: true }))?.label).toBe('Point')
    expect(flashFor(act({ kind: 'point', point: false, previousPoint: true }))?.label).toBe(
      'Not a point',
    )
  })

  // The one the whole flash exists for: X advances to the next rally
  // immediately, so without a confirmation a rejection is visually identical
  // to pressing the right arrow.
  it('confirms a rejection, and its undo', () => {
    expect(flashFor(act({ kind: 'reject', rejected: true }))).toEqual({
      label: 'Rejected',
      glyph: '✕',
      tone: 'reject',
    })
    expect(flashFor(act({ kind: 'reject', rejected: false, previousRejected: true }))?.label).toBe(
      'Kept',
    )
  })

  // Reject takes the receding tone, not danger -- same rule as the palette:
  // it is the most common action here and colouring it as an error states
  // the wrong thing about routine work.
  it('does not dress a rejection as a failure', () => {
    expect(flashFor(act({ kind: 'reject', rejected: true }))?.tone).toBe('reject')
  })

  // Walking the queue is movement, not a judgement. Flashing on it would
  // fire on every keypress of a pass and make the confirmations that do
  // matter invisible.
  it('says nothing for a plain skip', () => {
    expect(flashFor(act({ kind: 'skip' }))).toBeNull()
  })

  it('confirms an undo without claiming to know what it undid', () => {
    expect(
      flashFor({ kind: 'undo', rallyId: 'r1', starred: false, rejected: false, point: false }),
    ).toEqual({ label: 'Undone', glyph: '⟲', tone: 'neutral' })
  })
})
