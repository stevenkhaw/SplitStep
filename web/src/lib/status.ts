import type { AppMode, Reel, Session } from './types'

/**
 * How a list card should present a status.
 *
 * The library and reels rows used to print the raw column value -- literally
 * `needs_setup`, underscore and all -- in the same grey as the counts beside
 * it, so the one status that is a call to action looked exactly like the
 * four that are not. This maps to a human label and one of three tones, and
 * lives in lib/ rather than in the markup so the two pages cannot drift and
 * the mapping is testable.
 *
 * `active` means "this is moving, or wants you" and takes the accent;
 * `quiet` is a settled state and stays dim; `danger` is reserved for actual
 * failure, per the palette's rule.
 */
export type StatusTone = 'quiet' | 'active' | 'danger'

export interface StatusBadge {
  label: string
  tone: StatusTone
}

// The pipeline's own vocabulary (see CLAUDE.md's status ladder). Anything
// mid-flight reads as active: the reviewer's question at a glance is "is
// this ready to work on", and every one of these answers "not yet".
const SESSION_LABELS: Record<string, StatusBadge> = {
  needs_setup: { label: 'Needs setup', tone: 'active' },
  ingesting: { label: 'Reading', tone: 'active' },
  building: { label: 'Building proxy', tone: 'active' },
  ingested: { label: 'Queued', tone: 'active' },
  detecting: { label: 'Finding rallies', tone: 'active' },
  ready: { label: 'Ready', tone: 'quiet' },
  reviewed: { label: 'Reviewed', tone: 'quiet' },
  failed: { label: 'Failed', tone: 'danger' },
}

/** A status added server-side without a case here renders as itself, which
 *  is wrong-looking but true, rather than as a confident wrong guess. */
export function sessionStatus(s: Session): StatusBadge {
  return SESSION_LABELS[s.status] ?? { label: s.status, tone: 'quiet' }
}

/**
 * Supersedes lib/reels.ts's `reelStateLabel`, which this replaced: same three
 * render states, plus the empty case the list page also has to draw, plus the
 * tone. Three states and not two because `rendered_path` survives a
 * membership change (see mark_dirty) -- the file is still on disk and still
 * watchable, it is merely out of date, and collapsing "never rendered" and
 * "stale" would hide that there is something to watch right now.
 */
/**
 * The empty-queue sentence, by why the queue is empty.
 *
 * The hardcoded copy this replaces promised "wait for detection to finish"
 * unconditionally -- a lie for a failed source, where nothing is coming --
 * and pointed at the re-segment panel, which friend mode cannot see. The
 * status decides whether progress is actually pending; the app mode decides
 * whether the re-segment pointer names something on screen.
 */
export function emptyQueueCopy(sourceStatus: string, appMode: AppMode): string {
  if (sourceStatus === 'failed') {
    return 'Detection failed for this video — open the jobs badge above to retry it.'
  }
  if (sourceStatus === 'needs_setup') {
    // Nothing is running here: the pipeline is waiting on the human, and
    // "still running" would promise progress that never comes.
    return 'This video still needs its court set up before detection can start.'
  }
  if (sourceStatus !== 'ready' && sourceStatus !== 'reviewed') {
    return 'No rallies yet — detection is still running.'
  }
  return appMode === 'dev'
    ? 'The current threshold produced zero rallies. Re-segment at a lower threshold below.'
    : 'No rallies were found in this video.'
}

export function reelStatus(r: Reel): StatusBadge {
  if (r.item_count === 0) return { label: 'Empty', tone: 'quiet' }
  // `dirty` is set whenever the item list changes, so a rendered reel whose
  // clips have since moved is offering a file that no longer matches it --
  // the reel equivalent of needs_setup. Dirty with nothing rendered yet is
  // not stale, though: there is no old file to replace, it has simply never
  // been built, and calling that "needs re-render" would name a step the
  // user has not taken a first time.
  if (!r.rendered_path) return { label: 'Not rendered', tone: 'quiet' }
  if (r.dirty) return { label: 'Needs re-render', tone: 'active' }
  return { label: 'Rendered', tone: 'quiet' }
}
