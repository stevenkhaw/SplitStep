import { describe, expect, it } from 'vitest'
import { UndoStack } from '../src/lib/undo'

interface Entry {
  rallyId: string
  field: 'starred' | 'rejected'
  previous: boolean
}

describe('UndoStack', () => {
  it('is empty to start', () => {
    expect(new UndoStack<Entry>().size).toBe(0)
    expect(new UndoStack<Entry>().pop()).toBeUndefined()
  })

  it('returns entries most recent first', () => {
    const s = new UndoStack<Entry>()
    s.push({ rallyId: 'a', field: 'starred', previous: false })
    s.push({ rallyId: 'b', field: 'rejected', previous: false })
    expect(s.pop()?.rallyId).toBe('b')
    expect(s.pop()?.rallyId).toBe('a')
    expect(s.pop()).toBeUndefined()
  })

  it('tracks size', () => {
    const s = new UndoStack<Entry>()
    s.push({ rallyId: 'a', field: 'starred', previous: false })
    expect(s.size).toBe(1)
    s.pop()
    expect(s.size).toBe(0)
  })

  it('clears', () => {
    const s = new UndoStack<Entry>()
    s.push({ rallyId: 'a', field: 'starred', previous: false })
    s.clear()
    expect(s.size).toBe(0)
  })

  it('bounds its depth so a long session cannot grow it without limit', () => {
    const s = new UndoStack<Entry>(3)
    for (const id of ['a', 'b', 'c', 'd']) {
      s.push({ rallyId: id, field: 'starred', previous: false })
    }
    expect(s.size).toBe(3)
    expect(s.pop()?.rallyId).toBe('d')
    expect(s.pop()?.rallyId).toBe('c')
    expect(s.pop()?.rallyId).toBe('b')
    expect(s.pop()).toBeUndefined()
  })
})
