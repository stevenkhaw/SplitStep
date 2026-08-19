import { describe, expect, it } from 'vitest'
import { createRouter, navigate, parseHash } from '../src/lib/router.svelte'

describe('parseHash', () => {
  it('maps empty hash to the library', () => {
    expect(parseHash('')).toEqual({ name: 'library' })
    expect(parseHash('#')).toEqual({ name: 'library' })
    expect(parseHash('#/')).toEqual({ name: 'library' })
  })

  it('maps a session hash to the session route', () => {
    expect(parseHash('#/s/2026-08-19')).toEqual({ name: 'session', id: '2026-08-19' })
  })

  it('keeps ids containing dashes and suffixes intact', () => {
    expect(parseHash('#/s/2026-08-19-b')).toEqual({ name: 'session', id: '2026-08-19-b' })
  })

  it('falls back to the library for anything unrecognized', () => {
    expect(parseHash('#/nonsense/deep')).toEqual({ name: 'library' })
  })
})

describe('createRouter', () => {
  it('constructs and exposes the current route from window.location.hash', () => {
    window.location.hash = '#/s/2026-08-19'
    const router = createRouter()
    expect(router.current).toEqual({ name: 'session', id: '2026-08-19' })
  })

  // $state silently does nothing in a plain .ts file -- it throws only when the
  // rune is actually evaluated -- so a test that merely imports the module cannot
  // catch that. This test forces evaluation and drives a real hashchange, so it
  // fails if router.svelte.ts ever regresses to router.ts or the $state wiring
  // otherwise breaks.
  it('reacts to hashchange events', () => {
    window.location.hash = ''
    const router = createRouter()
    expect(router.current).toEqual({ name: 'library' })

    window.location.hash = '#/s/session-42'
    window.dispatchEvent(new HashChangeEvent('hashchange'))

    expect(router.current).toEqual({ name: 'session', id: 'session-42' })
  })
})

describe('navigate', () => {
  it('sets window.location.hash', () => {
    navigate('/s/foo')
    expect(window.location.hash).toBe('#/s/foo')
  })
})
