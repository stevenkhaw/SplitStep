import { ApiError } from './errors'
import type {
  AppConfig,
  AppMode,
  Clip,
  ExportResult,
  Job,
  LabelRecord,
  Preset,
  Reel,
  ReelDeleteResult,
  ReelDetail,
  ReelMergeResult,
  RenderResult,
  RevealResult,
  ScoreSeries,
  Session,
  SessionDetail,
  Source,
  SpanRef,
} from './types'

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    // FormData is the one body that must NOT get this header: the browser
    // writes multipart/form-data with its own boundary, and a manual
    // content-type here would send a boundary-less header the server cannot
    // parse. Everything else with a body is JSON.
    headers:
      init?.body && !(init.body instanceof FormData)
        ? { 'content-type': 'application/json' }
        : undefined,
  })
  if (!res.ok) {
    const raw = await res.text().catch(() => '')
    // FastAPI wraps its messages as {"detail": "..."} and HTTPException's
    // detail can itself be a string or an object. Unwrapped here, once, so
    // lib/errors.ts and every banner see the server's actual sentence rather
    // than a JSON envelope -- and a non-JSON body (a proxy error page, a
    // truncated response) still passes through as itself.
    let detail = raw
    try {
      const parsed = JSON.parse(raw)
      if (parsed && typeof parsed.detail === 'string') detail = parsed.detail
    } catch {
      // not JSON; `raw` is already the best available text
    }
    const method = init?.method ?? 'GET'
    // The message keeps the old shape on purpose: it is what `String(e)`
    // produces, which several call sites still log.
    throw new ApiError(`${method} ${path} -> ${res.status} ${raw}`, {
      status: res.status,
      method,
      path,
      detail,
    })
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
  renameReel: (slug: string, name: string) =>
    req<Reel>(`/api/reels/${slug}/rename`, { method: 'POST', body: JSON.stringify({ name }) }),
  deleteReel: (slug: string) =>
    req<ReelDeleteResult>(`/api/reels/${slug}`, { method: 'DELETE' }),
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
  setReelItemNote: (slug: string, span: SpanRef, note: string) =>
    req<{ ok: boolean }>(`/api/reels/${slug}/items/note`, {
      method: 'POST',
      body: JSON.stringify({ ...span, note }),
    }),
  // Not routed through post(): like exportClips, its return shape is the
  // four counts plan_reel_export reports, not post()'s ok/count/id union.
  exportReelClips: (slug: string) =>
    req<ExportResult>(`/api/reels/${slug}/export`, { method: 'POST' }),
  // `numbered` has no default here -- RenderBody.numbered defaults to false
  // server-side, but leaving it implicit client-side would let a caller
  // forget the flag and silently get the fast render when the numbered
  // checkbox was actually checked. The one caller (Reel.svelte's render())
  // always passes its `numbered` $state explicitly.
  // `hr` swaps every clip for RallyMetrics's heart-rate-overlaid copy; the
  // server refuses (409) while any item lacks one, so the checkbox only
  // needs to be honest about the intent, not the availability.
  renderReel: (slug: string, numbered: boolean, hr: boolean) =>
    req<RenderResult>(`/api/reels/${slug}/render`, {
      method: 'POST',
      body: JSON.stringify({ numbered, hr }),
    }),
  createSessionReel: (sessionId: string, which: 'points' | 'starred') =>
    req<ReelMergeResult>(`/api/sessions/${sessionId}/reels`, {
      method: 'POST',
      body: JSON.stringify({ which }),
    }),

  // Unwrapped to a plain array here, not left as the server's
  // `{"clips": [...]}` envelope -- every other list route on this object
  // (listReels, listPresets, sourceLabels) already hands the caller a bare
  // array, and ClipsPanel/lib/clips.ts want the same shape to stay
  // consistent with them.
  getSessionClips: (sessionId: string) =>
    req<{ clips: Clip[] }>(`/api/sessions/${sessionId}/clips`).then((r) => r.clips),
  // `relpath` is library-relative (see RevealBody's docstring in
  // splitstep/api/routes.py) -- lib/clips.ts's sessionClipsRelpath/
  // clipRevealRelpath build it, this just posts it.
  revealClip: (relpath: string) =>
    req<RevealResult>('/api/clips/reveal', {
      method: 'POST',
      body: JSON.stringify({ relpath }),
    }),

  star: (id: string, starred: boolean) => post(`/api/rallies/${id}/star`, { starred }),
  reject: (id: string, rejected: boolean) => post(`/api/rallies/${id}/reject`, { rejected }),
  point: (id: string, point: boolean) => post(`/api/rallies/${id}/point`, { point }),
  setNote: (id: string, note: string) => post(`/api/rallies/${id}/note`, { note }),
  // Called on every plain right-arrow (see persist.ts's skip case) --
  // on this". See splitstep/db/rallies.py::set_seen.
  seen: (id: string) => post(`/api/rallies/${id}/seen`),
  setBounds: (id: string, start_ms: number, end_ms: number) =>
    post(`/api/rallies/${id}/bounds`, { start_ms, end_ms }),
  splitRally: (id: string, atMs: number) =>
    req<{ ok: boolean; new_rally_id: string }>(`/api/rallies/${id}/split`, {
      method: 'POST',
      body: JSON.stringify({ at_ms: atMs }),
    }),
  // `req` rather than `post`: post's return type has no new_rally_id, and
  // widening it would loosen every other rally write's shape for one caller.
  mergeRally: (id: string) => post(`/api/rallies/${id}/merge`),
  label: (id: string, verdict: string, boundary_flags: string[]) =>
    post(`/api/rallies/${id}/label`, { verdict, boundary_flags }),
  // Withdraws the current verdict for the rally's detector span, appending a
  // retraction row rather than deleting anything. Its own route because a
  // verdict-less label row is what a boundary drag writes, and the two mean
  // opposite things -- see api_label_retract in splitstep/api/routes.py.
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
  retryJob: (id: string) => req<{ ok: boolean }>(`/api/jobs/${id}/retry`, { method: 'POST' }),
  detectSource: (id: string) =>
    req<{ job_id: string | null; already_running: boolean }>(`/api/sources/${id}/detect`, {
      method: 'POST',
    }),

  importFile: (file: File) => {
    const body = new FormData()
    body.append('file', file)
    return req<{ name: string }>('/api/import', { method: 'POST', body })
  },

  libraryStats: () => req<{ bytes: number }>('/api/library/stats'),

  config: () => req<AppConfig>('/api/config'),
  setMode: (mode: AppMode) =>
    req<AppConfig>('/api/config/mode', { method: 'POST', body: JSON.stringify({ mode }) }),

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
