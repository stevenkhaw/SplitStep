import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../src/lib/api'

/**
 * `createRally` is one line, and the line is the whole risk: the route
 * answers `{ok, rally_id}` while the split route beside it answers
 * `{ok, new_rally_id}`. Reading the wrong key costs nothing at compile time
 * and hands `applyAdd` an `undefined` id, so the new row would carry an id
 * the server does not have and the next merge would name a rally that is
 * not there. Nothing else in the app can catch that, so it is pinned here.
 */
function stubFetch(body: unknown, ok = true) {
  const f = vi.fn(async () => ({
    ok,
    status: ok ? 200 : 400,
    json: async () => body,
    text: async () => JSON.stringify(body),
  }))
  vi.stubGlobal('fetch', f)
  return f
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('api.createRally', () => {
  it('posts the span to the source and returns the id the server made', async () => {
    const f = stubFetch({ ok: true, rally_id: 'r-new' })
    const id = await api.createRally('src1', 9000, 12000)
    expect(id).toBe('r-new')
    const [path, init] = f.mock.calls[0] as unknown as [string, RequestInit]
    expect(path).toBe('/api/sources/src1/rallies')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual({ start_ms: 9000, end_ms: 12000 })
  })

  it('surfaces the server sentence when the span is refused', async () => {
    stubFetch({ detail: 'A rally must be at least 100 ms long.' }, false)
    await expect(api.createRally('src1', 9000, 9000)).rejects.toThrow(/100 ms/)
  })
})
