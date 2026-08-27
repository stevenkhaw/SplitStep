import { describe, expect, it } from 'vitest'
import { emptyQueueCopy, reelStatus, sessionStatus } from '../src/lib/status'
import type { Reel, Session } from '../src/lib/types'

function session(over: Partial<Session> = {}): Session {
  return {
    id: '2026-08-18',
    title: '2026-08-18',
    played_on: '2026-08-18',
    status: 'ready',
    rally_count: 32,
    starred_count: 6,
    point_count: 25,
    thumb_idx: 1,
    ...over,
  }
}

function reel(over: Partial<Reel> = {}): Reel {
  return {
    id: 'r1',
    name: 'Best of',
    slug: 'best-of',
    rendered_path: null,
    rendered_at: null,
    dirty: 0,
    created_at: '2026-08-22T00:00:00Z',
    item_count: 6,
    thumb: null,
    ...over,
  }
}

describe('sessionStatus', () => {
  // The card previously printed the raw column value -- `needs_setup` with
  // the underscore -- in the same grey as everything else beside it, so the
  // one status that is a call to action looked identical to the four that
  // are not.
  it('calls out the status that wants the user to do something', () => {
    expect(sessionStatus(session({ status: 'needs_setup' }))).toEqual({
      label: 'Needs setup',
      tone: 'active',
    })
  })

  it('treats the pipeline statuses as in-progress, not as states to act on', () => {
    for (const s of ['ingesting', 'building', 'ingested', 'detecting']) {
      expect(sessionStatus(session({ status: s })).tone).toBe('active')
    }
  })

  it('keeps the settled statuses quiet', () => {
    expect(sessionStatus(session({ status: 'ready' }))).toEqual({ label: 'Ready', tone: 'quiet' })
    expect(sessionStatus(session({ status: 'reviewed' }))).toEqual({
      label: 'Reviewed',
      tone: 'quiet',
    })
  })

  it('is the one place danger is earned', () => {
    expect(sessionStatus(session({ status: 'failed' })).tone).toBe('danger')
  })

  // A status added server-side without a case here must still render as
  // something, and as itself rather than as a wrong guess.
  it('passes an unknown status through untranslated', () => {
    expect(sessionStatus(session({ status: 'archiving' }))).toEqual({
      label: 'archiving',
      tone: 'quiet',
    })
  })
})

describe('reelStatus', () => {
  it('is empty before anything is added', () => {
    expect(reelStatus(reel({ item_count: 0 }))).toEqual({ label: 'Empty', tone: 'quiet' })
  })

  it('says a reel has never been rendered', () => {
    expect(reelStatus(reel())).toEqual({ label: 'Not rendered', tone: 'quiet' })
  })

  it('says a reel is rendered once it has a file', () => {
    expect(reelStatus(reel({ rendered_path: 'reels/best-of.mp4' }))).toEqual({
      label: 'Rendered',
      tone: 'quiet',
    })
  })

  // `dirty` is set whenever the item list changes, so a rendered reel whose
  // clips moved is showing a file that no longer matches it. That is the
  // reel equivalent of needs_setup: something to act on.
  it('flags a rendered reel whose contents have since changed', () => {
    expect(reelStatus(reel({ rendered_path: 'reels/best-of.mp4', dirty: 1 }))).toEqual({
      label: 'Needs re-render',
      tone: 'active',
    })
  })

  // Dirty with no render yet is not a re-render prompt -- there is nothing
  // stale to replace, it has simply never been built.
  it('does not call an unrendered reel stale', () => {
    expect(reelStatus(reel({ dirty: 1 })).label).toBe('Not rendered')
  })
})

describe('emptyQueueCopy', () => {
  // A failed source must not promise progress ("wait for detection to
  // finish" when nothing is coming), and friend mode must not point at a
  // re-segment panel it cannot see.
  it('names the failure and where to retry it', () => {
    expect(emptyQueueCopy('failed', 'dev')).toMatch(/failed/i)
    expect(emptyQueueCopy('failed', 'dev')).toMatch(/retry/i)
    expect(emptyQueueCopy('failed', 'friend')).not.toMatch(/re-segment/i)
  })

  it('says detection is still running for anything short of ready', () => {
    for (const s of ['ingesting', 'building', 'detecting', 'needs_setup']) {
      expect(emptyQueueCopy(s, 'dev')).toMatch(/still running|on its way/i)
    }
  })

  it('points dev at the re-segment panel and friend at nothing it cannot see', () => {
    expect(emptyQueueCopy('ready', 'dev')).toMatch(/re-segment/i)
    expect(emptyQueueCopy('ready', 'friend')).not.toMatch(/re-segment|threshold/i)
  })
})
