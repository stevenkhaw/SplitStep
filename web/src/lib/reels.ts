import type { Reel, ReelItem, SpanRef } from './types'

/**
 * A reel item's identity: the span of a source, never a rally id.
 *
 * `replace_rallies` deletes every rally for a source on each threshold
 * sweep, so a held rally id expires; a span does not, and it is what the
 * clip on disk is named for. Same key `reel_items`' primary key uses.
 */
export function spanKey(span: SpanRef): string {
  return `${span.source_id}:${span.start_ms}:${span.end_ms}`
}

export function spanRef(span: SpanRef): SpanRef {
  // A narrowing copy: ReelItem and Rally both carry the three fields plus a
  // lot else, and POSTing the whole object would send the server fields it
  // ignores today and might not ignore later.
  return { source_id: span.source_id, start_ms: span.start_ms, end_ms: span.end_ms }
}

/**
 * A stable identity for a reel's current membership *and order*, for keying
 * the preview's `{#key}` block.
 *
 * `detail.items` is a fresh array of fresh objects on every fetch -- it
 * comes back over the wire as JSON, which never shares identity with what
 * produced it -- so keying on the array reference itself remounts the
 * preview on every successful refetch (after `cutMissing()`, after
 * `render()`) and even on a failed mutation's recovery refetch, none of
 * which change which spans are in the reel or what order they play in.
 * Joining each item's `spanKey` in list order gives a key that is stable
 * across exactly those no-op refetches, and changes only when a span is
 * actually added, removed, or reordered -- the cases that need a fresh
 * preview controller rather than one mutated mid-playback.
 */
export function reelMembershipKey(items: ReelItem[]): string {
  return items.map(spanKey).join('|')
}

export function missingClipCount(items: ReelItem[]): number {
  return items.filter((i) => !i.clip_ready).length
}

/**
 * Whether the builder page should still be polling this reel.
 *
 * A thin wrapper over `missingClipCount`, but named for the decision it
 * answers rather than the count it happens to use, so the "is there
 * anything left to wait for" check has one place and one test instead of
 * being reconstructed at the call site. `Reel.svelte` reads this through a
 * `$derived` so a poll's own refetch -- a fresh `items` array every time,
 * same as any other fetch -- only retriggers the polling effect on a
 * genuine true-to-false flip, never on the refetch itself.
 */
export function shouldPollReel(items: ReelItem[]): boolean {
  return missingClipCount(items) > 0
}

/**
 * How often the builder page polls a reel with clips still being cut.
 *
 * `bootleg`'s job worker is single-threaded and each cut is a full ffmpeg
 * encode -- minutes, not seconds -- so there is nothing to gain from a
 * tight loop, only load on a server that shares its worker pool with media
 * serving. 15s means "Render — N clips not cut yet" is never stale by more
 * than a moment relative to a job that takes minutes, without polling
 * anywhere near as often as `JobsBadge` (3s), which is cheap by comparison:
 * one small `jobs` query rather than a full reel-with-items fetch.
 */
export const REEL_POLL_INTERVAL_MS = 15000

/**
 * Why Render is disabled, or null if it is not.
 *
 * The count is in the string on purpose: §5.1 requires render to refuse
 * while any clip is missing and to NAME how many, so the reason is visible
 * on the button rather than discovered by pressing it. Render never
 * auto-enqueues the cuts -- that would turn one button into half an hour of
 * encoding -- so the user needs to know what to press instead.
 */
export function renderBlockedReason(items: ReelItem[]): string | null {
  if (items.length === 0) return 'No clips in this reel yet'
  const missing = missingClipCount(items)
  if (missing === 0) return null
  return `${missing} ${missing === 1 ? 'clip' : 'clips'} not cut yet`
}

/**
 * A reel's render state for the list page.
 *
 * Three states, not two: `rendered_path` survives a membership change (see
 * mark_dirty), because the file is still on disk and still watchable, it is
 * merely out of date. Collapsing "never rendered" and "stale" would hide
 * that there is something to watch right now.
 */
export function reelStateLabel(reel: Reel): string {
  if (!reel.rendered_path) return 'not rendered'
  return reel.dirty ? 'needs re-render' : 'rendered'
}

/**
 * Whether the builder should offer a Watch control for this reel.
 *
 * True even while `dirty`: mark_rendered deliberately leaves `rendered_path`
 * set across a membership change (see its docstring) because the file on
 * disk is unchanged and still watchable, only possibly out of sync with the
 * reel's current items. Hiding Watch on dirty would throw away a playable
 * file for no reason; the caller's job is to LABEL a dirty watch as the
 * last render rather than the current membership, not to hide it.
 */
export function canWatchRendered(reel: Reel): boolean {
  return reel.rendered_path !== null
}
