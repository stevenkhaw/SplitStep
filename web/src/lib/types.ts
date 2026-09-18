import type { ScoreRules } from './score'

export interface Session {
  id: string
  title: string
  played_on: string
  status: string
  /** Match-score tracking rules, or null when the session does not track.
   *  Parsed server-side from the sessions.scoring column (see
   *  splitstep/db/sessions.py::scoring_rules). */
  scoring: ScoreRules | null
  rally_count: number
  starred_count: number
  point_count: number
  /** Lowest source idx, for the card's still via `api.frameUrl`. Null when
   *  the session has no sources yet. */
  thumb_idx: number | null
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
  // The mtime of this source's features.jsonl, and when its play region was
  // assigned. Null on either side means genuinely unknown -- never detected,
  // never assigned, or a row predating migration 014 -- which is why
  // `regionNewerThanFeatures` treats a null as "no warning" rather than as
  // an old timestamp. See splitstep/db/migrations/014_preset_assigned_at.sql.
  features_at: string | null
  preset_assigned_at: string | null
}

export interface Rally {
  id: string
  session_id: string
  source_id: string
  idx: number
  start_ms: number
  end_ms: number
  // NULL on a rally a human made by splitting one in two: the detector
  // never proposed it. See splitstep/db/migrations/009_rally_split.sql.
  // Every consumer that asks "is this a detector proposal?" tests this.
  det_start_ms: number | null
  det_end_ms: number | null
  confidence: number
  starred: number
  rejected: number
  point: number
  reviewed_at: string | null
  seen_at: string | null
  note: string
  /** '' when nobody has said who won this point -- one representation of
   *  absence, like `note`. Positional: 'a' is the first name in the
   *  session's `scoring.players`, so renaming a player touches no rally. */
  winner: '' | 'a' | 'b'
}

export interface SessionDetail {
  // The list-only fields are omitted because /api/sessions/{id} genuinely
  // does not return them: the counts are computed per row by the list
  // endpoint, and thumb_idx exists for the library card. Widening Session
  // instead of extending this list is what makes a field look available on
  // the detail page when it is always undefined there.
  session: Omit<Session, 'rally_count' | 'starred_count' | 'point_count' | 'thumb_idx'>
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

/** The server-side app mode (splitstep/appconfig.py). 'friend' hides the
 *  tuning tools -- label mode and the re-segment panel -- behind the
 *  Advanced toggle; 'dev' is the full surface and the default for a
 *  checkout whose config never wrote the key. */
export interface AppConfig {
  mode: 'friend' | 'dev'
  /** RallyMetrics's clips folder for heart-rate reels, or null when unset. */
  hr_clips_root: string | null
  /** True only when that folder is a directory right now (drive mounted). */
  hr_clips_available: boolean
}

export type AppMode = AppConfig['mode']

export interface Job {
  id: string
  type: string
  status: string
  progress: number
  error: string | null
  /** The traceback behind the one-sentence `error` (Phase 1's split). Only
   *  ever shown behind a disclosure -- the sentence is the message. */
  error_detail: string | null
  /** The two timestamps /api/jobs has always sent and the client used to
   *  drop on the floor. `created_at` is when the work was asked for, which
   *  is the only start the schema records -- claim() flips status without
   *  stamping anything -- and is what lib/jobs.ts measures elapsed from. */
  created_at: string
  finished_at: string | null
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

/**
 * One cut clip on disk, as `GET /api/sessions/{id}/clips` reports it --
 * read straight off the filesystem, not a database row (see
 * `api_list_clips` in splitstep/api/routes.py). `relpath` is relative to
 * the session's `clips/` directory (e.g. `"01/1000-9000.mp4"`), NOT to the
 * library root -- `sessionClipsRelpath`/`clipRevealRelpath` in lib/clips.ts
 * are what build the library-relative path `POST /api/clips/reveal` wants.
 */
export interface Clip {
  source_idx: number
  start_ms: number
  end_ms: number
  relpath: string
  size_bytes: number
}

/** `POST /api/clips/reveal`'s response. `reason` is present only when
 *  `ok` is false -- today that is exclusively "not on macOS" (see
 *  `api_reveal_clip`), but the shape leaves room for another reason without
 *  a breaking change. */
export interface RevealResult {
  ok: boolean
  reason?: string
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
  /** The first clip's own frame, for the card's cover. Null while the reel
   *  is empty -- which every reel is between creation and its first add. */
  thumb: { session_id: string; idx: number; at_ms: number } | null
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
  /** Free text, capped at ITEM_NOTE_MAX_CHARS (lib/reels.ts), burned into
   *  the corner of the clip when the reel is rendered numbered. Empty string
   *  when unset -- the server always sends the field, never omits it. */
  note: string
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

/**
 * One window of a blind labelling pass. Two fields, and that is the whole
 * contract: the server deliberately does not say which windows the detector
 * flagged (see `splitstep/label_sample.py::Window`).
 */
export interface SampleWindow {
  start_ms: number
  end_ms: number
}

export interface LabelSample {
  seed: number
  window_ms: number
  windows: SampleWindow[]
}
