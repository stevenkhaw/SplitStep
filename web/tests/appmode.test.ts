import { describe, expect, it, vi } from 'vitest'

vi.mock('../src/lib/api', () => ({
  api: {
    config: vi.fn(async () => ({ mode: 'friend' })),
    setMode: vi.fn(async (mode: string) => ({ mode })),
  },
}))

import { api } from '../src/lib/api'
import { appmode } from '../src/lib/appmode.svelte'

describe('appmode', () => {
  it('defaults to dev before load, adopts the server value after', async () => {
    // 'dev' first: a dev checkout is the only place the UI runs before the
    // config was ever written, and friend configs resolve before anyone can
    // press a key -- see the store's own comment.
    expect(appmode.current).toBe('dev')
    await appmode.load()
    expect(appmode.current).toBe('friend')
  })

  it('set() is optimistic and posts to the server', async () => {
    await appmode.set('dev')
    expect(appmode.current).toBe('dev')
    expect(api.setMode).toHaveBeenCalledWith('dev')
  })

  it('a failed load leaves the current mode untouched', async () => {
    vi.mocked(api.config).mockRejectedValueOnce(new Error('offline'))
    await appmode.set('friend')
    await appmode.load()
    expect(appmode.current).toBe('friend')
  })
})
