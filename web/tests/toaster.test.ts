import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createToaster, toastToneClasses } from '../src/lib/toaster.svelte'

describe('createToaster', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  // $state silently does nothing in a plain .ts file -- it only throws once the
  // rune is actually evaluated, so a test that merely imports the module cannot
  // catch a regression to a plain (non-reactive) toaster.ts. Forcing evaluation
  // here is what pins the .svelte.ts requirement.
  it('starts empty', () => {
    const toaster = createToaster()
    expect(toaster.toasts).toEqual([])
  })

  it('push adds a toast with the given message', () => {
    const toaster = createToaster()
    toaster.push('hello')
    expect(toaster.toasts).toHaveLength(1)
    expect(toaster.toasts[0].message).toBe('hello')
  })

  it('assigns each pushed toast a distinct, stable id', () => {
    const toaster = createToaster()
    toaster.push('first')
    toaster.push('second')
    const [a, b] = toaster.toasts
    expect(a.id).not.toBe(b.id)
  })

  it('dismiss removes a toast by id', () => {
    const toaster = createToaster()
    const id = toaster.push('bye soon')
    toaster.dismiss(id)
    expect(toaster.toasts).toEqual([])
  })

  it('dismisses itself automatically after durationMs', () => {
    const toaster = createToaster(4000)
    toaster.push('auto')
    expect(toaster.toasts).toHaveLength(1)

    vi.advanceTimersByTime(3999)
    expect(toaster.toasts).toHaveLength(1)

    vi.advanceTimersByTime(1)
    expect(toaster.toasts).toHaveLength(0)
  })

  it('does not disturb other toasts when one is dismissed', () => {
    const toaster = createToaster()
    const first = toaster.push('a')
    toaster.push('b')
    toaster.dismiss(first)
    expect(toaster.toasts).toHaveLength(1)
    expect(toaster.toasts[0].message).toBe('b')
  })

  // Every existing caller of push() is a failure notice (star/reject persist
  // failing, a boundary edit getting refused). Defaulting the tone to
  // 'error' is what lets all of those call sites stay exactly as they are
  // -- one-argument calls that keep reading red -- while the export success
  // path opts into 'info' explicitly. Flipping this default would silently
  // restyle every one of those genuine failures.
  it('push without a tone defaults to error', () => {
    const toaster = createToaster()
    toaster.push('a persist failed')
    expect(toaster.toasts[0].tone).toBe('error')
  })

  it('an explicit info tone survives to the rendered toast', () => {
    const toaster = createToaster()
    toaster.push('24 queued', 'info')
    expect(toaster.toasts[0].tone).toBe('info')
  })
})

describe('toastToneClasses', () => {
  it('keeps error alarming red', () => {
    expect(toastToneClasses('error')).toMatch(/red/)
  })

  it('renders info in a distinct, non-red style', () => {
    const classes = toastToneClasses('info')
    expect(classes).not.toMatch(/red/)
  })

  it('is consistent for the same tone regardless of surface', () => {
    // Both call shapes a consumer might use must still key off tone, not
    // silently fall back to error -- that is exactly the bug where an
    // info toast reads as red in whichever mode forgot to branch on it.
    expect(toastToneClasses('info')).not.toBe(toastToneClasses('error'))
    expect(toastToneClasses('info', 'muted')).not.toBe(toastToneClasses('error', 'muted'))
  })
})
