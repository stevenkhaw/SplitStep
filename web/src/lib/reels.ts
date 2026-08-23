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
 * `splitstep`'s job worker is single-threaded and each cut is a full ffmpeg
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

/**
 * A byte count as a human-readable size, e.g. `725 MB`.
 *
 * Binary units (1024, not 1000): that is what `du`/Finder/Explorer report,
 * and this number exists to be compared against what the user already sees
 * on disk -- disagreeing with that by using decimal units would make the
 * delete confirmation look wrong even when it is technically correct.
 *
 * One decimal place below 10 of a unit, none above: `1.4 GB` carries a
 * decision-relevant digit (is this the big reel or the small one?), while
 * `725.4 MB` does not -- the confirmation is read once, under a second,
 * before a destructive click.
 */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${Math.round(bytes)} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  const rounded = value < 10 ? Math.round(value * 10) / 10 : Math.round(value)
  return `${rounded} ${units[unit]}`
}

/**
 * What the inline delete confirmation names -- the reel by name, and its
 * render's size when there is one to reclaim.
 *
 * `renderedBytes === null` covers both "never rendered" and "rendered_path
 * pointed outside reels/ and rendered_file refused it" (see splitstep/reels.py):
 * both mean the same thing to a reviewer deciding whether to press the
 * button -- there is no file this delete will reclaim -- so the wording
 * collapses them rather than trying to explain a distinction that only
 * matters server-side.
 */
export function deleteConfirmationText(name: string, renderedBytes: number | null): string {
  const target = renderedBytes === null ? '' : ` and its ${formatBytes(renderedBytes)} render`
  return `Delete "${name}"${target}? This cannot be undone.`
}

/**
 * A rename input, trimmed -- or null if there is nothing worth saving.
 *
 * Mirrors the server's own validator (`_ReelNameBody.check_name` in
 * splitstep/api/routes.py) so the Save button is disabled for exactly the
 * input the POST would otherwise reject with 422, rather than letting a
 * click round-trip to the server just to learn that.
 */
export function normalizedReelName(input: string): string | null {
  const trimmed = input.trim()
  return trimmed === '' ? null : trimmed
}
