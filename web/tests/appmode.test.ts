import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/lib/api', () => ({
  api: {
    config: vi.fn(async () => ({ mode: 'friend' })),
    setMode: vi.fn(async (mode: string) => ({ mode })),
  },
}))

import { api } from '../src/lib/api'
import { appmode } from '../src/lib/appmode.svelte'

// The store is module-level $state with no reset API, so each test starts by
// driving it back to 'dev' through the mocked setter -- otherwise the cases
// form a hidden sequence and only pass in file order.
beforeEach(async () => {
  vi.clearAllMocks()
  await appmode.set('dev')
})

describe('appmode', () => {
  it('adopts the server value on load', async () => {
    expect(appmode.current).toBe('dev')
    await appmode.load()
    expect(appmode.current).toBe('friend')
  })

  it('set() is optimistic and posts to the server', async () => {
    await appmode.set('friend')
    expect(appmode.current).toBe('friend')
    expect(api.setMode).toHaveBeenCalledWith('friend')
  })

  it('set() reverts when the write fails', async () => {
    vi.mocked(api.setMode).mockRejectedValueOnce(new Error('server restarting'))
    await appmode.set('friend')
    // The checkbox snapping back is the honest signal the write didn't stick.
    expect(appmode.current).toBe('dev')
  })

  it('a failed load leaves the current mode untouched', async () => {
    vi.mocked(api.config).mockRejectedValueOnce(new Error('offline'))
    await appmode.set('friend')
    await appmode.load()
    expect(appmode.current).toBe('friend')
  })
})
