import { describe, expect, it } from 'vitest'
import {
  chooserCopy,
  chooserState,
  defaultLibraryPath,
  describeTarget,
  sortEntries,
  type LibraryEntry,
} from '../src/lib/launcher'

const GB = 1024 ** 3

const entry = (path: string, over: Partial<LibraryEntry> = {}): LibraryEntry => ({
  path,
  reachable: true,
  hasLibrary: true,
  ...over,
})

describe('chooserState', () => {
  it('is first-run when nothing has ever been configured', () => {
    expect(chooserState([], null)).toBe('first-run')
  })

  it('is not-connected when the configured library is unreachable', () => {
    expect(chooserState([entry('/v/ssd', { reachable: false })], '/v/ssd')).toBe(
      'not-connected',
    )
  })

  it('is not-connected when the configured library is not in the list at all', () => {
    expect(chooserState([], '/v/ssd')).toBe('not-connected')
  })

  it('is switching when the configured library is reachable', () => {
    // Reaching the chooser with a working library means the user asked for
    // it from Settings -- autoboot would have skipped it otherwise.
    expect(chooserState([entry('/v/ssd')], '/v/ssd')).toBe('switching')
  })

  it('is first-run when known libraries exist but none is configured', () => {
    expect(chooserState([entry('/v/ssd')], null)).toBe('first-run')
  })
})

describe('chooserCopy', () => {
  it('explains why the setting exists on first run', () => {
    const copy = chooserCopy('first-run', null)
    expect(copy.heading).toMatch(/where/i)
    expect(copy.body).toMatch(/external drive/i)
  })

  it('names the missing path when not connected', () => {
    const copy = chooserCopy('not-connected', '/Volumes/SanDisk_2TB/SplitStep')
    expect(copy.body).toContain('/Volumes/SanDisk_2TB/SplitStep')
  })

  it('promises nothing is moved when switching', () => {
    // Re-point, never move. Saying so is the whole reason switching is safe.
    expect(chooserCopy('switching', '/v/ssd').body).toMatch(/does not move/i)
  })

  it('does not claim a path is missing when switching', () => {
    expect(chooserCopy('switching', '/v/ssd').body).not.toMatch(/not available/i)
  })
})

describe('defaultLibraryPath', () => {
  it('suggests Movies, the macOS home for video', () => {
    expect(defaultLibraryPath('/Users/steven')).toBe('/Users/steven/Movies/SplitStep')
  })

  it('tolerates a trailing slash on home', () => {
    expect(defaultLibraryPath('/Users/steven/')).toBe('/Users/steven/Movies/SplitStep')
  })
})

describe('describeTarget', () => {
  it('says a folder will be opened when it already holds a library', () => {
    expect(describeTarget(500 * GB, true)).toMatch(/open/i)
  })

  it('says a folder will be created when it does not', () => {
    expect(describeTarget(500 * GB, false)).toMatch(/create/i)
  })

  it('reports free space so an external drive is an informed choice', () => {
    expect(describeTarget(500 * GB, false)).toContain('500 GB')
  })

  it('omits free space rather than guessing when it is unknown', () => {
    const text = describeTarget(null, false)
    expect(text).not.toMatch(/free/i)
    expect(text).toMatch(/create/i)
  })
})

describe('sortEntries', () => {
  it('keeps recency order but sinks unreachable entries', () => {
    const sorted = sortEntries([
      entry('/a', { reachable: false }),
      entry('/b'),
      entry('/c'),
    ])
    expect(sorted.map((e) => e.path)).toEqual(['/b', '/c', '/a'])
  })

  it('does not mutate its input', () => {
    const input = [entry('/a', { reachable: false }), entry('/b')]
    sortEntries(input)
    expect(input[0].path).toBe('/a')
  })
})
