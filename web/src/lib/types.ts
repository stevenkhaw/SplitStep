export interface Session {
  id: string
  title: string
  played_on: string
  status: string
  rally_count: number
  starred_count: number
  point_count: number
}

export interface Source {
  id: string
  session_id: string
  idx: number
  recorded_at: string
  offset_ms: number
  duration_ms: number
  width: number
  height: number
  fps: number
  has_original: number
  court_preset_id: string | null
  status: string
  rotation_deg: number
}

export interface Rally {
  id: string
  session_id: string
  source_id: string
  idx: number
  start_ms: number
  end_ms: number
  det_start_ms: number
  det_end_ms: number
  confidence: number
  starred: number
  rejected: number
  point: number
  reviewed_at: string | null
  seen_at: string | null
  note: string
}

export interface SessionDetail {
  session: Omit<Session, 'rally_count' | 'starred_count' | 'point_count'>
  sources: Source[]
  rallies: Rally[]
}

export interface ScoreSeries {
  step_ms: number
  threshold: number
  scores: number[]
}

export interface Preset {
  id: string
  name: string
  points: [number, number][]
  created_at: string
}

export interface Job {
  id: string
  type: string
  status: string
  progress: number
  error: string | null
}

// The four outcomes plan_export sorts a set's rallies into (splitstep/export.py
// ExportPlan): a clip on disk, a job already working the same span, and a
// rally whose source vanished are distinct reasons nothing new was queued,
// not one "already done" bucket -- see lib/export.ts's describeExportResult.
export interface ExportResult {
  queued: number
  already_cut: number
  in_flight: number
  unavailable: number
  total: number
}

export interface LabelRecord {
  source_id: string
  span_start_ms: number
  span_end_ms: number
  verdict: 'clean' | 'not_play' | 'partly' | 'unsure' | null
  boundary_flags: ('start_early' | 'start_late' | 'end_early' | 'end_late')[]
  true_start_ms: number | null
  true_end_ms: number | null
}

export interface Reel {
  id: string
  name: string
  slug: string
  rendered_path: string | null
  rendered_at: string | null
  dirty: number
  created_at: string
  item_count: number
}

/**
 * One row of the builder, as the server resolves it.
 *
 * `session_id` and `source_idx` are here because a reel is session-agnostic:
 * the item alone cannot say which proxy the preview should seek, and the
 * clip path needs the same pair. `rally` is null for an ORPHAN -- an item
 * whose span no rally holds any more, which a threshold sweep produces
 * routinely. It is badged, never dropped: the clip on disk is what the reel
 * is made of.
 */
export interface ReelItem {
  source_id: string
  session_id: string
  source_idx: number
  start_ms: number
  end_ms: number
  duration_ms: number
  position: number
  clip_ready: boolean
  rally: Rally | null
}

/** How a reel addresses an item: a span of a source, never a rally id --
 * replace_rallies deletes every rally on a sweep, so a held rally id
 * expires and a span does not. */
export interface SpanRef {
  source_id: string
  start_ms: number
  end_ms: number
}

export interface ReelDetail {
  // rendered_bytes lives ONLY here, not on Reel itself: the list route
  // deliberately never stat()s every reel's render on every page load (see
  // api_get_reel's comment in splitstep/api/routes.py), so a listed Reel truly
  // does not carry this field -- widening it onto Reel would let TypeScript
  // promise a number the list response never sends.
  reel: Reel & { rendered_bytes: number | null }
  items: ReelItem[]
}

export interface ReelDeleteResult {
  deleted: boolean
  removed_file: boolean
}

export interface ReelMergeResult {
  added: number
  existing: number
  total: number
  /** Present only on the session-set route, which creates or finds the reel. */
  slug?: string
  name?: string
}

export interface RenderResult {
  job_id: string
  already_running: boolean
}
