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

// The four outcomes plan_export sorts a set's rallies into (bootleg/export.py
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
