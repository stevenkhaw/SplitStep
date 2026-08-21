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
 * `existing` with every span of `incoming` it does not already hold appended.
 *
 * Additive, never a replacement: existing entries and the order a human
 * dragged them into are untouched. Overwriting membership would silently
 * discard a manual reorder -- the same class of mistake replace_rallies
 * makes with boundary edits, which already cost this project a 9.6-second
 * rally. Mirrors what `add_items` does server-side; used here so the picker
 * can show the result of an add before committing to it.
 */
export function mergeSpans(existing: SpanRef[], incoming: SpanRef[]): SpanRef[] {
  const seen = new Set(existing.map(spanKey))
  const out = [...existing]
  for (const span of incoming) {
    const key = spanKey(span)
    if (seen.has(key)) continue
    seen.add(key)
    out.push(spanRef(span))
  }
  return out
}

export function missingClipCount(items: ReelItem[]): number {
  return items.filter((i) => !i.clip_ready).length
}

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
