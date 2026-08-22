import type {
  ExportResult,
  Job,
  LabelRecord,
  Preset,
  Reel,
  ReelDetail,
  ReelMergeResult,
  RenderResult,
  ScoreSeries,
  Session,
  SessionDetail,
  Source,
  SpanRef,
} from './types'

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: init?.body ? { 'content-type': 'application/json' } : undefined,
  })
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`${init?.method ?? 'GET'} ${path} -> ${res.status} ${detail}`)
  }
  return res.json() as Promise<T>
}

const post = (path: string, body?: unknown) =>
  req<{ ok?: boolean; session_status?: string; count?: number; id?: string }>(path, {
    method: 'POST',
    body: body === undefined ? undefined : JSON.stringify(body),
  })

export const api = {
  listSessions: () => req<Session[]>('/api/sessions'),
  getSession: (id: string) => req<SessionDetail>(`/api/sessions/${id}`),
  // Not routed through post() -- its hardcoded return type covers only the
  // ok/session_status/count/id shape other routes use, not the four counts
  // plan_export reports. `setup` below hits the same mismatch the same way.
  exportClips: (sessionId: string, which: 'points' | 'starred') =>
    req<ExportResult>(`/api/sessions/${sessionId}/export`, {
      method: 'POST',
      body: JSON.stringify({ which }),
    }),

  listReels: () => req<Reel[]>('/api/reels'),
  createReel: (name: string) =>
    req<Reel>('/api/reels', { method: 'POST', body: JSON.stringify({ name }) }),
  getReel: (slug: string) => req<ReelDetail>(`/api/reels/${slug}`),
  addReelItems: (slug: string, items: SpanRef[]) =>
    req<ReelMergeResult>(`/api/reels/${slug}/items`, {
      method: 'POST',
      body: JSON.stringify({ items }),
    }),
  removeReelItem: (slug: string, span: SpanRef) =>
    req<{ removed: boolean; total: number }>(`/api/reels/${slug}/items/remove`, {
      method: 'POST',
      body: JSON.stringify(span),
    }),
  setReelOrder: (slug: string, order: SpanRef[]) =>
    req<{ ok: boolean }>(`/api/reels/${slug}/order`, {
      method: 'POST',
      body: JSON.stringify({ order }),
    }),
  // Not routed through post(): like exportClips, its return shape is the
  // four counts plan_reel_export reports, not post()'s ok/count/id union.
  exportReelClips: (slug: string) =>
    req<ExportResult>(`/api/reels/${slug}/export`, { method: 'POST' }),
  renderReel: (slug: string) =>
    req<RenderResult>(`/api/reels/${slug}/render`, { method: 'POST' }),
  createSessionReel: (sessionId: string, which: 'points' | 'starred') =>
    req<ReelMergeResult>(`/api/sessions/${sessionId}/reels`, {
      method: 'POST',
      body: JSON.stringify({ which }),
    }),

  star: (id: string, starred: boolean) => post(`/api/rallies/${id}/star`, { starred }),
  reject: (id: string, rejected: boolean) => post(`/api/rallies/${id}/reject`, { rejected }),
  point: (id: string, point: boolean) => post(`/api/rallies/${id}/point`, { point }),
  setNote: (id: string, note: string) => post(`/api/rallies/${id}/note`, { note }),
  reviewed: (id: string) => post(`/api/rallies/${id}/reviewed`),
  // Called on every plain right-arrow (see persist.ts's skip case) --
  // "a human looked at this", independent of reviewed_at's "a human ruled
  // on this". See bootleg/db/rallies.py::set_seen.
  seen: (id: string) => post(`/api/rallies/${id}/seen`),
  setBounds: (id: string, start_ms: number, end_ms: number) =>
    post(`/api/rallies/${id}/bounds`, { start_ms, end_ms }),
  label: (id: string, verdict: string, boundary_flags: string[]) =>
    post(`/api/rallies/${id}/label`, { verdict, boundary_flags }),
  // Withdraws the current verdict for the rally's detector span, appending a
  // retraction row rather than deleting anything. Its own route because a
  // verdict-less label row is what a boundary drag writes, and the two mean
  // opposite things -- see api_label_retract in bootleg/api/routes.py.
  retractLabel: (id: string) => post(`/api/rallies/${id}/label/retract`),
  sourceLabels: (sourceId: string) => req<LabelRecord[]>(`/api/sources/${sourceId}/labels`),

  resegment: (sourceId: string, threshold: number) =>
    post(`/api/sources/${sourceId}/resegment`, { threshold }),
  scores: (sourceId: string, threshold?: number) =>
    req<ScoreSeries>(`/api/sources/${sourceId}/scores`
      + (threshold === undefined ? '' : `?threshold=${threshold}`)),

  listPresets: () => req<Preset[]>('/api/court_presets'),
  createPreset: (name: string, points: [number, number][]) =>
    post('/api/court_presets', { name, points }),
  setPreset: (sourceId: string, preset_id: string) =>
    post(`/api/sources/${sourceId}/preset`, { preset_id }),

  jobs: () => req<Job[]>('/api/jobs'),

  proxyUrl: (sessionId: string, idx: number) => `/media/${sessionId}/${idx}/proxy.mp4`,
  frameUrl: (sessionId: string, idx: number, atMs = 0) =>
    `/media/${sessionId}/${idx}/frame.jpg?at_ms=${atMs}`,
  // The rendered 4K file, not the 1080p proxy `proxyUrl` points at -- the
  // server resolves `slug` to a path itself, so this is a lookup key, not a
  // filesystem path (see api_reel_media).
  reelUrl: (slug: string) => `/media/reels/${slug}.mp4`,

  getSource: (id: string) => req<Source>(`/api/sources/${id}`),
  setup: (id: string, rotation_deg: number, preset_id: string) =>
    req<{ job_id: string }>(`/api/sources/${id}/setup`, {
      method: 'POST',
      body: JSON.stringify({ rotation_deg, preset_id }),
    }),
  previewUrl: (sessionId: string, idx: number, atMs: number, rot: number) =>
    `/media/${sessionId}/${idx}/preview.jpg?at_ms=${Math.round(atMs)}&rot=${rot}`, // Round to int: server parses as int and caches one JPEG per distinct value
}
