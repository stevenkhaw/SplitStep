import { describe, expect, it } from 'vitest'
import { dropIndex, moveItem } from '../src/lib/reorder'

describe('moveItem', () => {
  it('moves an item down', () => {
    expect(moveItem(['a', 'b', 'c', 'd'], 0, 2)).toEqual(['b', 'c', 'a', 'd'])
  })

  it('moves an item up', () => {
    expect(moveItem(['a', 'b', 'c', 'd'], 3, 1)).toEqual(['a', 'd', 'b', 'c'])
  })

  it('moving to the same index is a no-op', () => {
    expect(moveItem(['a', 'b', 'c'], 1, 1)).toEqual(['a', 'b', 'c'])
  })

  it('never mutates the input', () => {
    const list = ['a', 'b', 'c']
    moveItem(list, 0, 2)
    expect(list).toEqual(['a', 'b', 'c'])
  })

  it('clamps an out-of-range destination instead of dropping the item', () => {
    // A pointer dragged past the end of the list is an ordinary gesture, not
    // an error -- losing the row over it would be the worst possible answer.
    expect(moveItem(['a', 'b', 'c'], 0, 99)).toEqual(['b', 'c', 'a'])
    expect(moveItem(['a', 'b', 'c'], 2, -5)).toEqual(['c', 'a', 'b'])
  })

  it('returns a copy for an out-of-range source', () => {
    expect(moveItem(['a', 'b'], 7, 0)).toEqual(['a', 'b'])
  })
})

describe('dropIndex', () => {
  // Four 40px rows starting at y=0, so their midpoints are 20, 60, 100, 140.
  const midpoints = [20, 60, 100, 140]

  it('keeps the row where it is while the pointer stays in its own slot', () => {
    expect(dropIndex(20, midpoints, 0)).toBe(0)
  })

  it('lands after a neighbour once the pointer crosses its midpoint', () => {
    // Dragging row 0 down: the remaining midpoints are 60, 100, 140.
    expect(dropIndex(59, midpoints, 0)).toBe(0)
    expect(dropIndex(61, midpoints, 0)).toBe(1)
    expect(dropIndex(101, midpoints, 0)).toBe(2)
  })

  it('lands before a neighbour when dragging upward', () => {
    // Dragging row 3 up: the remaining midpoints are 20, 60, 100.
    expect(dropIndex(19, midpoints, 3)).toBe(0)
    expect(dropIndex(21, midpoints, 3)).toBe(1)
  })

  it('clamps past either end', () => {
    expect(dropIndex(-500, midpoints, 2)).toBe(0)
    expect(dropIndex(9999, midpoints, 2)).toBe(3)
  })

  it('is a no-op for a single-row list', () => {
    expect(dropIndex(9999, [20], 0)).toBe(0)
  })

  // The contract the component depends on: dropIndex returns an index for
  // moveItem against the ORIGINAL list, so the two compose without the
  // caller doing arithmetic.
  it('composes with moveItem', () => {
    const list = ['a', 'b', 'c', 'd']
    expect(moveItem(list, 0, dropIndex(101, midpoints, 0))).toEqual(['b', 'c', 'a', 'd'])
  })
})
