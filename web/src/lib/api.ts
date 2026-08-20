import type { Job, Preset, ScoreSeries, Session, SessionDetail, Source } from './types'

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

  star: (id: string, starred: boolean) => post(`/api/rallies/${id}/star`, { starred }),
  reject: (id: string, rejected: boolean) => post(`/api/rallies/${id}/reject`, { rejected }),
  reviewed: (id: string) => post(`/api/rallies/${id}/reviewed`),
  setBounds: (id: string, start_ms: number, end_ms: number) =>
    post(`/api/rallies/${id}/bounds`, { start_ms, end_ms }),

  resegment: (sourceId: string, threshold: number) =>
    post(`/api/sources/${sourceId}/resegment`, { threshold }),
  scores: (sourceId: string, threshold: number) =>
    req<ScoreSeries>(`/api/sources/${sourceId}/scores?threshold=${threshold}`),

  listPresets: () => req<Preset[]>('/api/court_presets'),
  createPreset: (name: string, points: [number, number][]) =>
    post('/api/court_presets', { name, points }),
  setPreset: (sourceId: string, preset_id: string) =>
    post(`/api/sources/${sourceId}/preset`, { preset_id }),

  jobs: () => req<Job[]>('/api/jobs'),

  proxyUrl: (sessionId: string, idx: number) => `/media/${sessionId}/${idx}/proxy.mp4`,
  frameUrl: (sessionId: string, idx: number, atMs = 0) =>
    `/media/${sessionId}/${idx}/frame.jpg?at_ms=${atMs}`,

  getSource: (id: string) => req<Source>(`/api/sources/${id}`),
  setup: (id: string, rotation_deg: number, preset_id: string) =>
    req<{ job_id: string }>(`/api/sources/${id}/setup`, {
      method: 'POST',
      body: JSON.stringify({ rotation_deg, preset_id }),
    }),
  previewUrl: (sessionId: string, idx: number, atMs: number, rot: number) =>
    `/media/${sessionId}/${idx}/preview.jpg?at_ms=${Math.round(atMs)}&rot=${rot}`, // Round to int: server parses as int and caches one JPEG per distinct value
}
