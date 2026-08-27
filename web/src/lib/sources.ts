import type { Rally, Source } from './types'

/**
 * Per-video tabs for a multi-source session.
 *
 * A `needs_setup` source has no rallies -- detection hasn't run, there is
 * nothing to review yet -- and no proxy to seek either, so it is excluded
 * rather than shown as an empty tab a reviewer could select into a blank
 * page. Ordered by `idx` (recording order), not array order, because
 * `SessionDetail.sources` carries no ordering guarantee of its own.
 */
export interface SourceTab {
  id: string
  idx: number
  rallyCount: number
}

export function sourceTabs(sources: Source[], rallies: Rally[]): SourceTab[] {
  return sources
    .filter((s) => s.status !== 'needs_setup')
    .sort((a, b) => a.idx - b.idx)
    .map((s) => ({
      id: s.id,
      idx: s.idx,
      rallyCount: rallies.filter((r) => r.source_id === s.id).length,
    }))
}

/**
 * Which tab should be selected once a fresh `SessionDetail` lands (initial
 * load, closeTimeline's refetch, a re-segment, setup finishing on another
 * source -- any of them can add, remove or renumber tabs out from under
 * whatever was selected before). Pure so Session.svelte's two fetch sites
 * can share one rule without drifting: `null` for zero or one tab (there is
 * nothing to choose between, and the tab strip itself won't render), the
 * current selection if it still names a tab in the new set, the first tab
 * otherwise.
 */
export function resolveSelectedTab(tabs: SourceTab[], current: string | null): string | null {
  if (tabs.length <= 1) return null
  if (tabs.some((t) => t.id === current)) return current
  return tabs[0].id
}

/**
 * Narrows a session detail's rallies to one source, for the mode block
 * (queue/label/timeline) once a tab is selected. `sourceId === null` means
 * "no tab selected" -- the single-video case, and the state before a
 * multi-video session's default tab is applied -- and returns `detail`
 * itself rather than a copy, so a single-source session takes this
 * function's identity branch and stays pixel-identical to before tabs
 * existed.
 *
 * Deliberately narrows only `rallies`, not `sources`: callers (QueueMode,
 * LabelMode, TimelineMode) resolve a rally's source by id and, in
 * TimelineMode's case, build session-wide ms offsets from every source's
 * `idx`/`duration_ms` -- both need the *full* source list even while the
 * rally list is scoped to one of them. Session.svelte hands QuadEditor and
 * ResegmentPanel the unscoped `detail`/`readySources` directly, never
 * through this function, for the same reason at a coarser grain: those
 * panels have their own source pickers and must stay able to reach every
 * video, not just the selected tab.
 */
export function scopeToSource<T extends { rallies: Rally[] }>(detail: T, sourceId: string | null): T {
  if (sourceId === null) return detail
  return { ...detail, rallies: detail.rallies.filter((r) => r.source_id === sourceId) }
}

/**
 * The status the scoped view should report: the selected source's own, or
 * the session's when no tab is selected — and the session's again when the
 * id matches nothing (a stale selection after the source list changed),
 * because a wrong-but-plausible per-source status is worse than the
 * session-level truth. Lives here beside scopeToSource so the empty-queue
 * copy's input is testable; inline in Session.svelte it was not.
 */
export function scopedStatus(
  detail: { sources: { id: string; status: string }[]; session: { status: string } },
  sourceId: string | null,
): string {
  return detail.sources.find((s) => s.id === sourceId)?.status ?? detail.session.status
}
