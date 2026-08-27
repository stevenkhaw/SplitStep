import { formatDuration, formatTs } from './time'
import type { Clip, ExportResult } from './types'

export interface ClipGroup {
  source_idx: number
  clips: Clip[]
}

/**
 * Clips bucketed by source, in the order each source_idx first appears.
 *
 * `GET /api/sessions/{id}/clips` already sorts by `(source_idx, start_ms)`
 * (see `api_list_clips`), so in practice every run of one source's clips is
 * already contiguous -- but this groups by first-seen order via a `Map`
 * rather than assuming that and folding consecutive runs, so a change to
 * the server's sort (or a caller handing this an unsorted list) still
 * produces one group per source instead of splitting a source into two
 * groups the panel would render twice.
 */
export function groupClipsBySource(clips: Clip[]): ClipGroup[] {
  const order: number[] = []
  const bySource = new Map<number, Clip[]>()
  for (const clip of clips) {
    let bucket = bySource.get(clip.source_idx)
    if (!bucket) {
      bucket = []
      bySource.set(clip.source_idx, bucket)
      order.push(clip.source_idx)
    }
    bucket.push(clip)
  }
  return order.map((source_idx) => ({ source_idx, clips: bySource.get(source_idx)! }))
}

/**
 * The `/media/clips/...` URL for one clip.
 *
 * Built from the clip's own `source_idx`/`start_ms`/`end_ms`, not from
 * `clip.relpath` echoed verbatim: the server's media route always resolves
 * under `clips_dir/{source_idx:02d}/{name}` (`api_clip_media` in
 * splitstep/api/routes.py) regardless of how the file currently sits on
 * disk, and a not-yet-reconciled legacy layout can leave `relpath` flat
 * (`"01-1000-9000.mp4"`, see `parse_clip_name`'s legacy branch) even though
 * the route only ever looks in the nested shape. `clip_relpath`'s own
 * naming (`{start}-{end}.mp4`) is what the route expects, and start_ms/
 * end_ms are exactly what parsed it out of whatever shape the file was in,
 * so reconstructing from them is what actually hits regardless.
 */
export function clipMediaUrl(sessionId: string, clip: Clip): string {
  return `/media/clips/${sessionId}/${clip.source_idx}/${clip.start_ms}-${clip.end_ms}.mp4`
}

/** Duration + span, e.g. `8.0s · 0:10.0–0:18.0` -- the same two time
 *  helpers (lib/time.ts) every other duration/timestamp in the app goes
 *  through, so a clip's row reads in the same units as the queue and the
 *  timeline rather than inventing a third format. */
export function clipLabel(clip: Clip): string {
  return `${formatDuration(clip.end_ms - clip.start_ms)} · ${formatTs(clip.start_ms)}–${formatTs(clip.end_ms)}`
}

/**
 * A byte count as decimal MB/GB, e.g. `12.4 MB` / `1.3 GB` -- ported from
 * the CLI's `_fmt_bytes` (splitstep/cli.py), which is what `clips orphans`/
 * `clips prune` print for the exact same files this panel lists. Not
 * lib/reels.ts's `formatBytes`: that one is deliberately binary (1024) to
 * agree with Finder/du for a delete confirmation naming a *rendered reel*.
 * This number sits next to a clip pulled from the same filesystem walk the
 * CLI already reports on, and it must read as the same figure the CLI
 * would print for that file, not a different (if more textbook-correct)
 * one -- disagreeing between the two surfaces for the same byte count would
 * read as a bug even when both are technically right.
 */
export function formatClipSize(bytes: number): string {
  return bytes >= 1_000_000_000 ? `${(bytes / 1e9).toFixed(1)} GB` : `${(bytes / 1e6).toFixed(1)} MB`
}

/** The clips folder's library-relative path, e.g. `sessions/s1/clips` --
 *  the same shape `Library.clips_dir` builds server-side (splitstep/config.py)
 *  and what `POST /api/clips/reveal` expects for the panel's "reveal
 *  folder" button. */
export function sessionClipsRelpath(sessionId: string): string {
  return `sessions/${sessionId}/clips`
}

/** One clip file's library-relative path, for its own "Reveal" button.
 *  `clip.relpath` is only clips-dir-relative (see the `Clip` type), so this
 *  composes it onto `sessionClipsRelpath` rather than sending `relpath`
 *  bare -- the reveal route resolves against the library root, not the
 *  clips folder. */
export function clipRevealRelpath(sessionId: string, clip: Clip): string {
  return `${sessionClipsRelpath(sessionId)}/${clip.relpath}`
}

/** A stable identity for "which clip's video is expanded" -- span-derived
 *  like the clip's own filename (clip_relpath), so it survives a refetch
 *  that hands back a fresh array of fresh objects. */
export function clipKey(clip: Clip): string {
  return `${clip.source_idx}:${clip.start_ms}:${clip.end_ms}`
}

/**
 * The clip count ClipsPanel should poll toward after an export kicks off.
 *
 * `ExportResult` reports what the export *decided* (queued/already_cut/
 * in_flight/unavailable), not how many files exist on disk, so the panel's
 * own most recently known count is the baseline `queued` new clips should
 * land on top of -- `already_cut` ones are already counted in that
 * baseline, and `in_flight`/`unavailable` ones are not this call's doing
 * (a still-running earlier export, or a source that vanished), so adding
 * them again here would set a target this export alone can never reach.
 */
export function clipPollTarget(currentCount: number, result: ExportResult): number {
  return currentCount + result.queued
}

/**
 * Whether ClipsPanel should keep polling for an export in progress.
 *
 * Same shape as lib/reels.ts's `shouldPollReel` -- read through a
 * `$derived` so the effect only reacts to a genuine true<->false flip, not
 * to every fresh `clips` array a poll (or an unrelated refetch) produces.
 * `target === null` means no export has kicked off since the panel mounted
 * (or the session changed under it -- see ClipsPanel's session-swap reset),
 * so there is nothing to poll for regardless of the count.
 */
export function shouldPollForExport(count: number, target: number | null): boolean {
  return target !== null && count < target
}
