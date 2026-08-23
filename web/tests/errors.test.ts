import { describe, expect, it } from 'vitest'
import { ApiError, describeApiError } from '../src/lib/errors'

describe('describeApiError', () => {
  // The banner used to render the thrown string verbatim:
  //   GET /api/sessions/nope -> 404 {"detail":"Session not found"}
  // Method, path, status and a JSON envelope are implementation detail. They
  // are not deleted, though -- this app's user is also the person who runs
  // the server, so the technical line is demoted to `detail` rather than
  // thrown away.
  it('turns a 404 into a sentence and keeps the technical line separately', () => {
    const e = new ApiError('GET /api/sessions/nope -> 404 {"detail":"Session not found"}', {
      status: 404,
      method: 'GET',
      path: '/api/sessions/nope',
      detail: 'Session not found',
    })
    expect(describeApiError(e, 'session')).toEqual({
      message: 'That session is not in the library any more.',
      detail: 'GET /api/sessions/nope → 404 Session not found',
    })
  })

  it('names whichever subject the caller is showing', () => {
    const e = new ApiError('x', { status: 404, method: 'GET', path: '/api/reels/x', detail: '' })
    expect(describeApiError(e, 'reel').message).toBe('That reel is not in the library any more.')
  })

  // The most likely real failure for a local app on an external drive: the
  // server was stopped, or the drive was ejected under it. fetch() rejects
  // with a TypeError and no status at all, and "Failed to fetch" tells the
  // user nothing they can act on.
  it('recognises a server that is not answering', () => {
    const e = new TypeError('Failed to fetch')
    expect(describeApiError(e, 'session')).toEqual({
      message: 'Can’t reach the server. Check that splitstep serve is still running.',
      detail: 'Failed to fetch',
    })
  })

  it('points a 5xx at the terminal, where the traceback actually is', () => {
    const e = new ApiError('boom', {
      status: 500,
      method: 'POST',
      path: '/api/reels/x/render',
      detail: 'ffmpeg exit 234',
    })
    const d = describeApiError(e, 'reel')
    expect(d.message).toBe('The server hit an error. Check the terminal running splitstep serve.')
    expect(d.detail).toBe('POST /api/reels/x/render → 500 ffmpeg exit 234')
  })

  // A 400 is the server rejecting something specific -- a bad rotation, a
  // quad outside the frame. Its detail is written for a person and is more
  // useful than anything this function could say instead.
  it('lets a 4xx validation message speak for itself', () => {
    const e = new ApiError('x', {
      status: 400,
      method: 'POST',
      path: '/api/sources/s1/setup',
      detail: 'rotation must be one of 0, 90, 180, 270',
    })
    expect(describeApiError(e, 'source').message).toBe('rotation must be one of 0, 90, 180, 270')
  })

  it('falls back rather than inventing a reason it does not have', () => {
    expect(describeApiError(new Error('something odd'), 'session')).toEqual({
      message: 'Something went wrong.',
      detail: 'something odd',
    })
  })

  it('survives being handed something that is not an Error at all', () => {
    expect(describeApiError('a bare string', 'session')).toEqual({
      message: 'Something went wrong.',
      detail: 'a bare string',
    })
  })
})

describe('ApiError', () => {
  it('is an Error, so existing catch blocks and String() still work', () => {
    const e = new ApiError('GET /x -> 404 nope', {
      status: 404,
      method: 'GET',
      path: '/x',
      detail: 'nope',
    })
    expect(e).toBeInstanceOf(Error)
    expect(String(e)).toContain('GET /x -> 404 nope')
  })
})
