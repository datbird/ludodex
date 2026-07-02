// Typed client for the ludodex API.

export interface TagRef {
  tag: string
  origins: string[]   // e.g. ['ludodex'], ['playnite'], or multiple
}

export interface GameRow {
  norm_key: string
  title: string
  n_sources: number
  n_kinds: number
  sources_summary: string
  platforms: string
  matched: boolean
  has_cover: boolean
  ludodex_score: number | null
  tags: TagRef[]
}

export interface ScoreSource {
  source: string
  name: string
  kind: 'critic' | 'user'
  score: number | null
  votes: number | null
  raw: string | null
}
export interface Scores {
  critic: number | null
  players: number | null
  ludodex: number | null
  critic_weight: number
  sources: ScoreSource[]
}

export interface GamesPage {
  total: number
  limit: number
  offset: number
  items: GameRow[]
}

export interface GameDetail {
  norm_key: string
  title: string
  sources: { source: string; platform: string; source_id: string; title_raw: string; detail: string; os: string[] | null }[]
  attributes: Record<string, string[]>
  tags: TagRef[]
  scores: Scores
  metadata_links: { provider: string; provider_id: string; slug: string; url: string }[]
  media_kinds: string[]
  ai_meta?: AiFinding | null
  attribute_provenance?: Record<string, { value: string; origins: string[]; ai: boolean }[]>
  attribute_overrides?: Record<string, { value: string; origin: string }>
}

export interface ProviderMatch {
  provider?: 'igdb' | 'screenscraper'
  igdb_id?: number
  ss_id?: string | number
  name: string
  year: number | null
  cover?: string | null
  platforms?: string[]
}
export interface SourceCite { title: string; url: string }

export interface AiMatch {
  status: 'ok' | 'wrong' | 'unmatched' | 'unsure'
  confidence: number
  issue: string | null
  suggested_title: string | null
  suggested_year: number | null
}
export interface AiFindingPayload {
  match: AiMatch
  attributes: Record<string, string | string[]>
  notes: string
  current_match: { title: string | null; year: number | null; slug: string } | null
  missing: string[]
  provider_match?: ProviderMatch | null
  provider_matches?: ProviderMatch[]
  sources?: SourceCite[]
  web?: boolean
}
export interface AiFinding {
  id: number
  run_id: number
  norm_key: string
  title: string
  kind: 'match' | 'identify' | 'supplement'
  status: 'proposed' | 'accepted' | 'rejected'
  confidence: number
  model: string
  created: number
  payload: AiFindingPayload
  selection?: { attributes: string[] | null; match: boolean } | null
}
export interface AiApplySelection {
  finding_id: number
  attributes: string[] | null
  match: boolean
}
export type AiFindingCounts = Record<string, Record<string, number>>
export interface AiScanTargets { unmatched: number; matched: number; missing: number; all: number; web_capable: boolean; attributes: string[]; media_kinds: string[] }
export type ScopeValue = boolean | string[]   // true=all, false=none, [kinds]=subset
export interface ScanOpts { web?: boolean; match_provider?: boolean; metadata?: ScopeValue; media?: ScopeValue }
export interface AiScanRun {
  id: number
  target: string
  total: number
  done: number
  findings: number
  status: string
  created: number
  finished: number | null
}
export interface GameTags {
  norm_key: string
  tags: TagRef[]
}

export interface Stats {
  games: number
  cross_source: number
  unmatched: number
  no_media: number
  by_source: Record<string, number>
  media: { games_with_art: number; by_kind: Record<string, number> }
}

export interface Facets {
  sources: string[]
  platforms: string[]
}

export interface AiProvider {
  id: string
  configured: boolean
  masked: string | null
  model: string
  models: string[]
}

export interface AiArea {
  id: string
  name: string
  status: string
  description: string
  vision?: boolean
  assigned: string | null
  assigned_model: string | null
  effective: string | null
  effective_model: string | null
  prompt: string | null          // user override (null = using default)
  default_prompt: string
  prompt_vars: string[]          // <<token>> placeholders the prompt supports
}

export interface AiUsageModel {
  provider: string; model: string; calls: number
  input: number; output: number; total: number; month: number
  last_day: string | null; active_days: number; model_cap: number
}
export interface AiUsageProvider { provider: string; month: number; total: number; cap: number }
export interface AiUsageDay { day: string; calls: number; input: number; output: number }
export interface AiCap { scope: 'provider' | 'model'; key: string; cap: number; month: number }

export interface AiConfig {
  active: string | null
  default: { provider: string | null; model: string | null }
  vision_default: {
    provider: string | null; model: string | null
    assigned: string | null; assigned_model: string | null
  }
  providers: AiProvider[]
  areas: AiArea[]
}

export interface AiConfigUpdate {
  provider?: string
  vision?: { provider?: string; model?: string }
  keys?: Record<string, string>
  models?: Record<string, string>
  areas?: Record<string, { provider?: string; model?: string; prompt?: string }>
}

export interface ArtPick {
  kind: string
  candidates: { id: number; provider: string }[]
  recommended_id: number | null
  reason: string
}

export interface DedupeSuggestion {
  a: string; b: string; a_nk: string; b_nk: string
  a_src: string; b_src: string; ratio: number
  same: boolean; confidence: number | null; reason: string
}

export interface ServiceField {
  key: string
  label: string
  secret: boolean
  configured: boolean
  value: string
}
export interface LimitField {
  key: string
  label: string
  unit: string
  default: string
  value: string
}
export interface Prefs {
  hide_non_games: boolean
  spotlight_seconds: number
}
export interface IdentifyCandidate {
  igdb_id: number | null
  name: string
  year: number | null
  platforms: string[]
  cover: string | null
}
export interface RecognizedGame {
  title: string
  platform: string
  source: string
  confidence: number
}
export interface LibraryManager {
  id: number; device_id: number; kind: string; kind_label: string
  name: string; rom_path: string; media_path: string; enabled: number
  media_kinds?: string[]
}
export interface Device {
  id: number; name: string; transport: string; host: string; port: number
  username: string; auth: string; key_path: string; share: string
  enabled: number; has_password: boolean; managers: LibraryManager[]
}
export interface EmuLocation {
  name: string
  path: string
  role: 'roms' | 'media' | 'both'
  kinds: string[]        // media kinds to index (empty = all); only for media/both
  enabled: boolean
  status: string         // 'mounted' | 'present' | 'MISSING' | 'unset'
}
export interface ServiceConnect {
  url: string
  action_label: string
  field_label: string
  post: string
  connected: boolean
  note?: string
}
export interface Service {
  id: string
  name: string
  role: 'source' | 'provider' | 'both'
  hint: string
  fields: ServiceField[]
  limits: LimitField[]
  connect?: ServiceConnect
  enabled?: boolean
}

export interface SyncService {
  id: string
  name: string
  enabled: boolean
  ready: boolean
  needs_auth: boolean
  connect: ServiceConnect | null
  count: number | null
}
export interface SyncJobService {
  state: 'pending' | 'running' | 'ok' | 'failed' | 'skipped'
  count: number | null
  error: string | null
}
export interface SyncJob {
  running: boolean
  finished: boolean
  step: string
  error: string | null
  added: number | null
  services: Record<string, SyncJobService>
}

export interface Achievement {
  id: number
  title: string
  description: string
  points: number
  earned: boolean
  earned_date: string | null
  badge: string | null
}
export interface Achievements {
  matched: boolean
  ra_id?: number | null
  num_ach: number
  num_earned: number
  pulled_at?: string | null
  achievements: Achievement[]
}

export interface MediaAsset {
  id: number
  kind: string
  provider: string
  ref_type: string
  ext: string | null
  width: number | null
  height: number | null
  is_image: boolean
  pinned: boolean
  rank: number | null
  url: string
  thumb: string | null
  user?: boolean
}
export interface MediaLibrary {
  norm_key: string
  scalar_kinds: string[]
  multi_cap: number
  assets: MediaAsset[]
}
export interface MediaKind {
  kind: string
  scalar: boolean
  cap: number
  description: string
}

export interface OpsService {
  id: string; name: string; state: string; pid: number
  uptime_seconds: number; host: string; port: number
}
export interface OpsDatabase {
  id: string; name: string; role: string; path: string
  exists: boolean; size: number
  status?: string; detail?: string; reclaimable?: number
}
export interface OpsStatus { services: OpsService[]; databases: OpsDatabase[] }

export interface SpotlightItem {
  norm_key: string
  title: string
  score: number | null
  sources: string
  matched: boolean
  has_cover: boolean
}
export interface Spotlight {
  kind: string
  title: string
  subtitle: string
  items: SpotlightItem[]
}

export interface GamesQuery {
  q?: string
  source?: string
  platform?: string
  has_kind?: string
  include?: string[]
  exclude?: string[]
  sort?: string[]
  limit?: number
  offset?: number
}

// ---- File-operations engine ----
export interface FileVariable { token: string; label: string; description: string; example: string }
export interface FileProfile {
  id?: string; name: string; description: string; target: string
  m3u: boolean; prune_empty: boolean; rename: boolean; all_files: boolean
  archive_policy: string; builtin?: boolean; source?: string
}
export interface FileDetect {
  current: 'flat' | 'folder'; systems: string[]
  counts: { files: number; top_exts: [string, number][] }; sample: string[]
}
export interface FilePlanSummary {
  files: number; units: number; moves: number; renames: number
  skipped: number; m3u: number; prune: number
}
export interface FilePlanMove { op: string; src: string; dst: string }
export interface FilePlan { summary: FilePlanSummary; warnings: string[]; sample: FilePlanMove[] }
export interface FileCommandResult {
  explanation: string; profile: FileProfile; scope: string; system?: string
  summary: FilePlanSummary; warnings: string[]; sample: FilePlanMove[]
}
export interface FileInferResult { profile: FileProfile; detected: FileDetect }
export interface RunStep {
  seq: number; op: string; src: string | null; dst: string | null
  status: string; error: string
}
export interface RunGroupMove { seq: number; from: string; to: string; status: string }
export interface RunGroup { dir: string; moves: RunGroupMove[] }
export interface RunInfo {
  id: number; device_id: number; root: string; profile: string; scope: string
  system: string; n_ops: number; status: string; created: number
  started: number | null; finished: number | null; note: string
}
export interface Runbook {
  run: RunInfo; counts: Record<string, number>; steps: RunStep[]; groups: RunGroup[]
  running?: boolean; job_error?: string | null
}
export interface CreateRunbookResult { run_id: number; runbook: Runbook; warnings: string[] }
export interface RunHistoryRow extends RunInfo { done: number; failed: number; pending: number }
export interface TroubleshootFinding {
  seq: number; op: string; path: string; error: string; cause: string; fix: string
}
export interface Troubleshoot {
  status: string; failed: number; remaining: number; resumable: boolean
  findings: TroubleshootFinding[]
}
export interface JobProgress { done: number; total: number; failed: number }
export interface Job {
  id: string; kind: 'sync' | 'fileops'; run_id?: number; label: string
  status: string; detail: string; error: string | null; progress: JobProgress
  when: number | null; cancelable: boolean; restartable: boolean; deletable: boolean
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(path)
  if (!r.ok) throw new Error(`${r.status} ${path}`)
  return r.json()
}

// Send a mutation, surfacing the server's {detail} message on failure.
async function mutate<T>(path: string, method: string, body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method,
    headers: body !== undefined ? { 'content-type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error((data as { detail?: string }).detail || `${r.status} ${path}`)
  return data as T
}
const postJson = <T>(path: string, body: unknown) => mutate<T>(path, 'POST', body)

export type AuthUser = { id?: number; username: string; role: string }
export type AuthStatus = { needs_setup: boolean; authenticated: boolean; user: AuthUser | null }
export type AuthUserRow = { id: number; username: string; role: string; created: number }
export type UsersList = { users: AuthUserRow[]; me: number; roles: string[] }
export type CfMapping = { email: string; user_id: number; username: string; role: string; created: number }
export type CfAccessState = {
  enabled: boolean; team_domain: string; aud: string
  mappings: CfMapping[]; users: AuthUserRow[]
}

export const api = {
  authStatus: () => get<AuthStatus>('/api/auth/status'),
  authSetup: (username: string, password: string) =>
    postJson<{ ok: boolean; user: AuthUser }>('/api/auth/setup', { username, password }),
  authLogin: (username: string, password: string) =>
    postJson<{ ok: boolean; user: AuthUser }>('/api/auth/login', { username, password }),
  authLogout: () => postJson<{ ok: boolean }>('/api/auth/logout', {}),
  listUsers: () => get<UsersList>('/api/auth/users'),
  addUser: (username: string, password: string, role: string) =>
    postJson<{ ok: boolean; user: AuthUserRow }>('/api/auth/users', { username, password, role }),
  deleteUser: (id: number) => mutate<{ ok: boolean }>('/api/auth/users/' + id, 'DELETE'),
  resetPassword: (id: number, password: string) =>
    postJson<{ ok: boolean }>('/api/auth/users/' + id + '/password', { password }),
  setUserRole: (id: number, role: string) =>
    postJson<{ ok: boolean }>('/api/auth/users/' + id + '/role', { role }),
  cfAccess: () => get<CfAccessState>('/api/auth/cf-access'),
  cfAccessSet: (patch: Partial<{ enabled: boolean; team_domain: string; aud: string }>) =>
    postJson<CfAccessState>('/api/auth/cf-access', patch),
  cfMapEmail: (email: string, user_id: number) =>
    postJson<{ ok: boolean; mappings: CfMapping[] }>('/api/auth/cf-access/map', { email, user_id }),
  cfUnmapEmail: (email: string) =>
    postJson<{ ok: boolean; mappings: CfMapping[] }>('/api/auth/cf-access/unmap', { email }),

  stats: () => get<Stats>('/api/stats'),
  facets: () => get<Facets>('/api/facets'),
  games: (qy: GamesQuery) => {
    const p = new URLSearchParams()
    if (qy.q) p.set('q', qy.q)
    if (qy.source) p.set('source', qy.source)
    if (qy.platform) p.set('platform', qy.platform)
    if (qy.has_kind) p.set('has_kind', qy.has_kind)
    if (qy.include?.length) p.set('include', qy.include.join(','))
    if (qy.exclude?.length) p.set('exclude', qy.exclude.join(','))
    if (qy.sort?.length) p.set('sort', qy.sort.join(','))
    p.set('limit', String(qy.limit ?? 60))
    p.set('offset', String(qy.offset ?? 0))
    return get<GamesPage>('/api/games?' + p.toString())
  },
  detail: (nk: string) => get<GameDetail>('/api/games/' + encodeURIComponent(nk)),
  achievements: (nk: string) =>
    get<Achievements>('/api/games/' + encodeURIComponent(nk) + '/achievements'),
  gameTags: (nk: string) =>
    get<GameTags>('/api/games/' + encodeURIComponent(nk) + '/tags'),
  addTag: async (nk: string, tag: string) => {
    const r = await fetch('/api/games/' + encodeURIComponent(nk) + '/tags', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ tag }),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<GameTags>
  },
  removeTag: async (nk: string, tag: string) => {
    const r = await fetch('/api/games/' + encodeURIComponent(nk) + '/tags/' +
      encodeURIComponent(tag), { method: 'DELETE' })
    if (!r.ok) throw new Error(`${r.status} tag`)
    return r.json() as Promise<GameTags>
  },
  mediaLibrary: (nk: string) =>
    get<MediaLibrary>('/api/games/' + encodeURIComponent(nk) + '/media'),
  mediaKinds: () => get<{ kinds: MediaKind[] }>('/api/media-kinds'),
  uploadMedia: async (nk: string, kind: string, file: File) => {
    const r = await fetch(`/api/games/${encodeURIComponent(nk)}/media/` +
      `${encodeURIComponent(kind)}/upload?filename=${encodeURIComponent(file.name)}`, {
      method: 'POST', headers: file.type ? { 'content-type': file.type } : undefined,
      body: file,
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<MediaLibrary>
  },
  addMediaFromUrl: async (nk: string, kind: string, url: string) => {
    const r = await fetch(`/api/games/${encodeURIComponent(nk)}/media/` +
      `${encodeURIComponent(kind)}/url`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ url }),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<MediaLibrary>
  },
  deleteUserMedia: async (nk: string, id: number) => {
    const r = await fetch(`/api/games/${encodeURIComponent(nk)}/media/user/${id}`,
      { method: 'DELETE' })
    if (!r.ok) throw new Error(`${r.status} delete`)
    return r.json() as Promise<MediaLibrary>
  },
  setPins: async (nk: string, kind: string, ids: number[]) => {
    const r = await fetch('/api/games/' + encodeURIComponent(nk) + '/pins', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ kind, ids }),
    })
    if (!r.ok) throw new Error(`${r.status} pins`)
    return r.json() as Promise<MediaLibrary>
  },
  mediaUrl: (nk: string, kind: string, thumb = false) =>
    `/api/media/${encodeURIComponent(nk)}/${encodeURIComponent(kind)}` +
    (thumb ? '?size=thumb' : ''),
  assetUrl: (id: number, thumb = false) =>
    `/api/media-asset/${id}` + (thumb ? '?size=thumb' : ''),
  artPick: async (nk: string, kind = 'cover') => {
    const r = await fetch(`/api/ai/art-pick/${encodeURIComponent(nk)}?kind=${kind}`,
      { method: 'POST' })
    if (!r.ok) throw new Error(`${r.status}`)
    return r.json() as Promise<ArtPick>
  },
  artApply: async (id: number, norm_key: string, kind: string) => {
    const r = await fetch('/api/ai/art-apply', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ id, norm_key, kind }),
    })
    if (!r.ok) throw new Error(`${r.status}`)
    return r.json()
  },
  dedupe: async (limit = 15) => {
    const r = await fetch('/api/ai/dedupe', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ limit }),
    })
    if (!r.ok) throw new Error(`${r.status} /api/ai/dedupe`)
    return r.json() as Promise<{ suggestions: DedupeSuggestion[] }>
  },
  // Dashboard spotlight (themed top-N; 'random' rotates through themes)
  spotlight: (kind = 'random') =>
    get<Spotlight>('/api/spotlight?kind=' + encodeURIComponent(kind)),
  // Global preferences
  prefs: () => get<Prefs>('/api/prefs'),
  setPrefs: async (p: Partial<Prefs>) => {
    const r = await fetch('/api/prefs', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(p),
    })
    if (!r.ok) throw new Error(`${r.status} prefs`)
    return r.json() as Promise<Prefs>
  },
  // Add a game manually: identify by name (IGDB) or recognize from images (AI)
  identify: (name: string) =>
    get<{ query: string; candidates: IdentifyCandidate[]; provider: string | null }>(
      '/api/identify?name=' + encodeURIComponent(name)),
  addGame: async (g: { title: string; source: string; platform: string; detail?: string }) => {
    const r = await fetch('/api/games/add', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(g),
    })
    if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 140)}`)
    return r.json() as Promise<{ ok: boolean; norm_key: string; new_game: boolean }>
  },
  identifyImage: async (images: string[]) => {
    const r = await fetch('/api/games/identify-image', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ images }),
    })
    if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 160)}`)
    return r.json() as Promise<{ games: RecognizedGame[]; count: number }>
  },
  identifyFolder: async (path: string, limit?: number) => {
    const r = await fetch('/api/games/identify-folder', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ path, limit }),
    })
    if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 160)}`)
    return r.json() as Promise<{ games: RecognizedGame[]; count: number; scanned: number; total_found: number; batch_errors: number }>
  },
  // Connections › Devices (machines hosting library managers, pulled over SSH)
  devices: () => get<{ devices: Device[]; lm_kinds: Record<string, [string, boolean, boolean]> }>('/api/devices'),
  setDevice: async (d: Partial<Device> & { password?: string }) => {
    const r = await fetch('/api/devices', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(d) })
    if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 140)}`)
    return r.json() as Promise<{ devices: Device[] }>
  },
  removeDevice: async (id: number) => {
    const r = await fetch('/api/devices/' + id, { method: 'DELETE' })
    if (!r.ok) throw new Error(`${r.status} device`); return r.json() as Promise<{ devices: Device[] }>
  },
  testDevice: async (id: number) => {
    const r = await fetch('/api/devices/' + id + '/test', { method: 'POST' })
    if (!r.ok) throw new Error(`${r.status} test`); return r.json() as Promise<{ ok: boolean; detail: string }>
  },
  syncDevice: async (id: number) => {
    const r = await fetch('/api/devices/' + id + '/sync', { method: 'POST' })
    if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 160)}`)
    return r.json() as Promise<{ device: string; results: { manager: string; kind: string; ok: boolean; roms?: number; media?: string; error?: string }[] }>
  },
  setManager: async (m: Partial<LibraryManager>) => {
    const r = await fetch('/api/devices/managers', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(m) })
    if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 140)}`)
    return r.json() as Promise<{ devices: Device[] }>
  },
  removeManager: async (id: number) => {
    const r = await fetch('/api/devices/managers/' + id, { method: 'DELETE' })
    if (!r.ok) throw new Error(`${r.status} manager`); return r.json() as Promise<{ devices: Device[] }>
  },
  // Emulation storage locations (ROMs / media / both)
  emuLocations: () => get<{ locations: EmuLocation[] }>('/api/archives'),
  setEmuLocation: async (a: { name: string; path: string; role?: string; kinds?: string[]; enabled?: boolean }) => {
    const r = await fetch('/api/archives', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(a),
    })
    if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 120)}`)
    return r.json() as Promise<{ locations: EmuLocation[] }>
  },
  removeEmuLocation: async (name: string) => {
    const r = await fetch('/api/archives/' + encodeURIComponent(name), { method: 'DELETE' })
    if (!r.ok) throw new Error(`${r.status} location`)
    return r.json() as Promise<{ locations: EmuLocation[] }>
  },
  setEmuLocationEnabled: async (name: string, enabled: boolean) => {
    const r = await fetch('/api/archives/' + encodeURIComponent(name) + '/enabled', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ enabled }),
    })
    if (!r.ok) throw new Error(`${r.status} location`)
    return r.json() as Promise<{ locations: EmuLocation[] }>
  },
  // AI token usage + monthly limits
  aiUsage: () => get<{ models: AiUsageModel[]; providers: AiUsageProvider[] }>('/api/ai/usage'),
  aiUsageSeries: (provider: string, model: string) =>
    get<{ provider: string; model: string; days: AiUsageDay[] }>(
      `/api/ai/usage/series?provider=${encodeURIComponent(provider)}&model=${encodeURIComponent(model)}`),
  aiLimits: () => get<{ caps: AiCap[] }>('/api/ai/limits'),
  setAiLimit: async (scope: 'provider' | 'model', key: string, monthly_tokens: number) => {
    const r = await fetch('/api/ai/limit', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ scope, key, monthly_tokens }),
    })
    if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 140)}`)
    return r.json() as Promise<{ caps: AiCap[]; usage: { models: AiUsageModel[]; providers: AiUsageProvider[] } }>
  },
  // AI provider config (phase 3 — BYOAI; keys are write-only, never returned)
  aiConfig: () => get<AiConfig>('/api/ai/config'),
  aiModels: (provider: string, refresh = false, vision = false) =>
    get<{ provider: string; models: string[] }>(
      `/api/ai/models/${encodeURIComponent(provider)}?refresh=${refresh}&vision=${vision}`),
  setAiConfig: async (body: AiConfigUpdate) => {
    const r = await fetch('/api/ai/config', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!r.ok) throw new Error(`${r.status} /api/ai/config`)
    return r.json() as Promise<AiConfig>
  },
  // Service credentials (Sources + Providers; secrets returned masked)
  servicesConfig: () => get<{ services: Service[] }>('/api/services'),
  connectService: async (postPath: string, value: string) => {
    const r = await fetch(postPath, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ value }),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ ok: boolean; account: string | null; error?: string }>
  },
  setSourceEnabled: async (id: string, enabled: boolean) => {
    const r = await fetch(`/api/services/${encodeURIComponent(id)}/enabled`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ enabled }),
    })
    if (!r.ok) throw new Error(`${r.status} enabled`)
    return r.json() as Promise<{ id: string; enabled: boolean }>
  },
  // Ownership sync (pull owned games per store, then rebuild the catalog)
  syncStatus: () => get<{ services: SyncService[]; job: SyncJob | null }>('/api/sync/status'),
  syncRun: async (services: string[]) => {
    const r = await fetch('/api/sync/run', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ services }),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<SyncJob>
  },
  setServices: async (values: Record<string, string>) => {
    const r = await fetch('/api/services', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ values }),
    })
    if (!r.ok) throw new Error(`${r.status} /api/services`)
    return r.json() as Promise<{ services: Service[] }>
  },
  // Server operations (restart, DB health/repair)
  opsStatus: () => get<OpsStatus>('/api/ops/status'),
  opsRestart: async () => {
    const r = await fetch('/api/ops/restart', { method: 'POST' })
    if (!r.ok) throw new Error(`${r.status} restart`)
    return r.json() as Promise<{ restarting: boolean }>
  },
  dbCheck: async (db = 'all') => {
    const r = await fetch('/api/ops/db-check', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ db }),
    })
    if (!r.ok) throw new Error(`${r.status} db-check`)
    return r.json() as Promise<{ results: OpsDatabase[] }>
  },
  dbFix: async (db: string, action: 'optimize' | 'recover') => {
    const r = await fetch('/api/ops/db-fix', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ db, action }),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json()
  },
  // AI natural-language search (phase 3)
  aiSearch: async (q: string) => {
    const r = await fetch('/api/search', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ q }),
    })
    if (!r.ok) throw new Error(`${r.status} /api/search`)
    return r.json() as Promise<{ query: GamesQuery; explanation: string; result: GamesPage }>
  },
  // ---- File-operations engine: profiles, plans, runbooks ----
  fileVariables: () => get<{ variables: FileVariable[] }>('/api/fileops/variables'),
  fileProfiles: () => get<{ profiles: FileProfile[] }>('/api/fileops/profiles'),
  saveFileProfile: async (p: FileProfile) => {
    const r = await fetch('/api/fileops/profiles', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(p),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ id: string; profiles: FileProfile[] }>
  },
  deleteFileProfile: async (pid: string) => {
    const r = await fetch('/api/fileops/profiles/' + encodeURIComponent(pid), { method: 'DELETE' })
    if (!r.ok) throw new Error(`${r.status} profile`)
    return r.json() as Promise<{ profiles: FileProfile[] }>
  },
  fileDetect: async (body: { device_id: number; root: string; scope: string; system?: string }) => {
    const r = await fetch('/api/fileops/detect', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<FileDetect>
  },
  filePlan: async (body: { device_id: number; root: string; profile: string | FileProfile; scope: string; system?: string }) => {
    const r = await fetch('/api/fileops/plan', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<FilePlan>
  },
  fileInfer: async (body: { device_id: number; root: string; scope: string; system?: string }) => {
    const r = await fetch('/api/fileops/infer', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<FileInferResult>
  },
  fileCommand: async (body: { device_id: number; root: string; text: string; scope: string; system?: string }) => {
    const r = await fetch('/api/fileops/command', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<FileCommandResult>
  },
  createRunbook: async (body: { device_id: number; root: string; profile: string | FileProfile; scope: string; system?: string; note?: string }) => {
    const r = await fetch('/api/fileops/runbook', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<CreateRunbookResult>
  },
  getRunbook: (id: number) => get<Runbook>('/api/fileops/runbook/' + id),
  executeRunbook: async (id: number) => {
    const r = await fetch('/api/fileops/runbook/' + id + '/execute', { method: 'POST' })
    if (!r.ok) throw new Error(`${r.status} execute`)
    return r.json() as Promise<{ started: boolean; run_id: number }>
  },
  undoRunbook: async (id: number) => {
    const r = await fetch('/api/fileops/runbook/' + id + '/undo', { method: 'POST' })
    if (!r.ok) throw new Error(`${r.status} undo`)
    return r.json() as Promise<{ started: boolean; run_id: number }>
  },
  troubleshootRunbook: (id: number) => get<Troubleshoot>('/api/fileops/runbook/' + id + '/troubleshoot'),
  fileHistory: () => get<{ runs: RunHistoryRow[] }>('/api/fileops/history'),
  // ---- Unified job monitor (library sync + file-op runbooks) ----
  jobs: () => get<{ jobs: Job[] }>('/api/jobs'),
  pauseJob: async (id: string) => {
    const r = await fetch('/api/jobs/' + id + '/pause', { method: 'POST' })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json()
  },
  restartJob: async (id: string) => {
    const r = await fetch('/api/jobs/' + id + '/restart', { method: 'POST' })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json()
  },
  deleteJob: async (id: string) => {
    const r = await fetch('/api/jobs/' + id, { method: 'DELETE' })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json()
  },
  // ---- AI metadata audit & supplement ----
  aimetaTargets: () => get<AiScanTargets>('/api/aimeta/targets'),
  aimetaScans: () => get<{ scans: AiScanRun[] }>('/api/aimeta/scans'),
  aimetaFindings: (status?: string, kind?: string) => {
    const p = new URLSearchParams()
    if (status) p.set('status', status)
    if (kind) p.set('kind', kind)
    const q = p.toString()
    return get<{ findings: AiFinding[]; counts: AiFindingCounts }>(
      '/api/aimeta/findings' + (q ? '?' + q : ''))
  },
  aimetaScan: async (
    body: ({ target: string; limit?: number } | { norm_keys: string[]; label?: string }) & ScanOpts,
  ) => {
    const r = await fetch('/api/aimeta/scan', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ run_id: number; target: string; count: number; web: boolean; match_provider: boolean }>
  },
  aimetaFindingAction: async (id: number, action: 'accept' | 'reject' | 'reset') => {
    const r = await fetch('/api/aimeta/finding/' + id + '/' + action, { method: 'POST' })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ findings: AiFinding[]; counts: AiFindingCounts }>
  },
  aimetaAcceptAll: async (minConfidence?: number) => {
    const r = await fetch('/api/aimeta/accept-all', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ min_confidence: minConfidence || 0 }),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ accepted: number; counts: AiFindingCounts }>
  },
  aimetaApply: async (selections?: AiApplySelection[], media?: ScopeValue) => {
    const body: { selections?: AiApplySelection[]; media?: ScopeValue } = {}
    if (selections) body.selections = selections
    if (media !== undefined) body.media = media
    const r = await fetch('/api/aimeta/apply', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ started: boolean; selected: number | null }>
  },
  setAttributeOverride: async (nk: string, kind: string, value: string, origin: string) => {
    const r = await fetch('/api/games/' + encodeURIComponent(nk) + '/attribute', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ kind, value, origin }),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ override: { value: string; origin: string } }>
  },
  clearAttributeOverride: async (nk: string, kind: string) => {
    const r = await fetch('/api/games/' + encodeURIComponent(nk) + '/attribute/' + encodeURIComponent(kind), { method: 'DELETE' })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ cleared: boolean }>
  },
}
