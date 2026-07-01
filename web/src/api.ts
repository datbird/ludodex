// Typed client for the ludodex API.

export interface GameRow {
  norm_key: string
  title: string
  n_sources: number
  n_kinds: number
  sources_summary: string
  platforms: string
  matched: boolean
  has_cover: boolean
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
  sources: { source: string; platform: string; source_id: string; title_raw: string; detail: string }[]
  attributes: Record<string, string[]>
  metadata_links: { provider: string; provider_id: string; slug: string; url: string }[]
  media_kinds: string[]
}

export interface Stats {
  games: number
  cross_source: number
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
  assigned: string | null
  assigned_model: string | null
  effective: string | null
  effective_model: string | null
}

export interface AiConfig {
  active: string | null
  default: { provider: string | null; model: string | null }
  providers: AiProvider[]
  areas: AiArea[]
}

export interface AiConfigUpdate {
  provider?: string
  keys?: Record<string, string>
  models?: Record<string, string>
  areas?: Record<string, { provider?: string; model?: string }>
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

async function get<T>(path: string): Promise<T> {
  const r = await fetch(path)
  if (!r.ok) throw new Error(`${r.status} ${path}`)
  return r.json()
}

export const api = {
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
  // AI provider config (phase 3 — BYOAI; keys are write-only, never returned)
  aiConfig: () => get<AiConfig>('/api/ai/config'),
  aiModels: (provider: string, refresh = false) =>
    get<{ provider: string; models: string[] }>(
      `/api/ai/models/${encodeURIComponent(provider)}` + (refresh ? '?refresh=true' : '')),
  setAiConfig: async (body: AiConfigUpdate) => {
    const r = await fetch('/api/ai/config', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!r.ok) throw new Error(`${r.status} /api/ai/config`)
    return r.json() as Promise<AiConfig>
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
}
