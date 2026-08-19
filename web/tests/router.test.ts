import { describe, expect, it } from 'vitest'
import { parseHash } from '../src/lib/router.svelte'

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
