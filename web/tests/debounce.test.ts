import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { debounce } from '../src/lib/debounce'

describe('debounce', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('does not call fn until waitMs of silence has passed', () => {
    const fn = vi.fn()
    const d = debounce(fn, 150)
    d('a')
    vi.advanceTimersByTime(149)
    expect(fn).not.toHaveBeenCalled()
  })

  it('calls fn once after waitMs', () => {
    const fn = vi.fn()
    const d = debounce(fn, 150)
    d('a')
    vi.advanceTimersByTime(150)
    expect(fn).toHaveBeenCalledTimes(1)
    expect(fn).toHaveBeenCalledWith('a')
  })

  it('collapses a burst of calls into one, using the last arguments', () => {
    const fn = vi.fn()
    const d = debounce(fn, 150)
    d(1)
    vi.advanceTimersByTime(50)
    d(2)
    vi.advanceTimersByTime(50)
    d(3)
    vi.advanceTimersByTime(150)
    expect(fn).toHaveBeenCalledTimes(1)
    expect(fn).toHaveBeenCalledWith(3)
  })

  it('cancel() drops a pending call', () => {
    const fn = vi.fn()
    const d = debounce(fn, 150)
    d('a')
    d.cancel()
    vi.advanceTimersByTime(500)
    expect(fn).not.toHaveBeenCalled()
  })

  it('cancel() on an already-fired call is a harmless no-op', () => {
    const fn = vi.fn()
    const d = debounce(fn, 150)
    d('a')
    vi.advanceTimersByTime(150)
    expect(() => d.cancel()).not.toThrow()
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it('a later call after firing starts a fresh debounce window', () => {
    const fn = vi.fn()
    const d = debounce(fn, 150)
    d('a')
    vi.advanceTimersByTime(150)
    expect(fn).toHaveBeenCalledTimes(1)

    d('b')
    vi.advanceTimersByTime(149)
    expect(fn).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(1)
    expect(fn).toHaveBeenCalledTimes(2)
    expect(fn).toHaveBeenLastCalledWith('b')
  })
})
