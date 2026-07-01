import { useEffect, useState, useCallback, Fragment, type CSSProperties } from 'react'
import { api } from './api'
import type {
  GameRow, GameDetail, Stats, Facets, GamesQuery, AiConfig,
  DedupeSuggestion, ArtPick,
} from './api'
import './App.css'

const PAGE_OPTIONS = [25, 50, 100, 500, 1000]

type FilterState = Record<string, 'include' | 'exclude'>
type FilterRowDef = { id: string; name: string }
type FilterSection = { title: string; rows: FilterRowDef[] }

// Sort keys (ids match server SORT_SQL). A key can occupy one of 3 priority slots.
const SORT_SECTIONS: FilterSection[] = [
  { title: 'General', rows: [
    { id: 'title', name: 'Title' },
    { id: 'platform', name: 'System' },
    { id: 'source', name: 'Source' },
    { id: 'n_sources', name: '# Sources' },
    { id: 'n_kinds', name: '# Media kinds' },
  ] },
  { title: 'Status', rows: [
    { id: 'matched', name: 'Matched (identified)' },
    { id: 'has_cover', name: 'Has cover' },
    { id: 'cross_source', name: 'Cross-source' },
  ] },
]
type SortState = Record<string, 1 | 2 | 3>

// Toggleable table columns (Title always shows). Order here = column order.
const TABLE_COLS: { id: string; label: string }[] = [
  { id: 'art', label: 'Poster' },
  { id: 'platforms', label: 'Platforms' },
  { id: 'matched', label: 'Identified' },
  { id: 'n_sources', label: 'Sources' },
  { id: 'sources', label: 'Available from' },
  { id: 'n_kinds', label: 'Media' },
  { id: 'has_cover', label: 'Cover' },
]
const TABLE_COL_IDS = TABLE_COLS.map((c) => c.id)

// Per-profile UI preferences (view/perPage/theme). Namespaced by profile id so
// each profile keeps its own; PROFILE is a placeholder until real login exists.
// Reads fall back to the legacy flat keys so existing choices carry over.
const PROFILE = 'guest'
const prefKey = (name: string) => `ludodex:${PROFILE}:${name}`
function readPref(name: string, fallback: string): string {
  try {
    return localStorage.getItem(prefKey(name))
      ?? localStorage.getItem(`ludodex-${name}`)   // migrate old global keys
      ?? fallback
  } catch { return fallback }
}
function writePref(name: string, value: string) {
  try { localStorage.setItem(prefKey(name), value) } catch { /* storage disabled */ }
}

const SRC_LABEL: Record<string, string> = {
  steam: 'Steam', gog: 'GOG', epic: 'Epic', itch: 'itch.io', ea: 'EA',
  emulation: 'Emulation', archive: 'Local archive',
}
const srcLabel = (s: string) => SRC_LABEL[s] || s.charAt(0).toUpperCase() + s.slice(1)

// Build the include/exclude filter sections from live facets. Status flags are
// bare tokens; sources/systems use source:/system: tokens (see server FLAG_SQL).
function buildFilterSections(facets: Facets | null): FilterSection[] {
  const srcs = (facets?.sources || []).filter((s) => s !== 'playnite' && s !== 'launchbox')
  return [
    { title: 'Status', rows: [
      { id: 'matched', name: 'Matched (identified)' },
      { id: 'has_cover', name: 'Has cover' },
      { id: 'cross_source', name: 'Cross-source' },
    ] },
    { title: 'Sources', rows: [
      ...srcs.map((s) => ({ id: 'source:' + s, name: srcLabel(s) })),
      { id: 'playnite', name: 'Playnite' },
      { id: 'launchbox', name: 'LaunchBox' },
    ] },
    { title: 'Systems', rows: (facets?.platforms || []).map((p) => ({ id: 'system:' + p, name: p })) },
  ]
}

// Deterministic hue (0–359) from a title, so a game without art always gets the
// same generated-cover color.
function hueOf(s: string): number {
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0
  return h % 360
}

function NoArt({ title, compact, unmatched }: {
  title: string; compact?: boolean; unmatched?: boolean
}) {
  const style = { '--h': hueOf(title) } as CSSProperties
  return (
    <div className={'noart' + (compact ? ' sm' : '')} style={style}>
      {compact
        ? <span className="noart-initials">{title.slice(0, 2).toUpperCase()}</span>
        : <span className="noart-title">{title}</span>}
      {unmatched && (
        <svg className="noart-badge" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="2" aria-hidden="true">
          <title>Unidentified — this file didn’t match any known game</title>
          <circle cx="12" cy="12" r="9" />
          <line x1="5.64" y1="5.64" x2="18.36" y2="18.36" />
        </svg>
      )}
    </div>
  )
}

// Cover image with graceful fallback: if the game has no cover, OR the cover
// fails to load (e.g. a Deck-local file that 404s on this host), render the
// generated name-placeholder instead.
function Cover({ g, compact }: { g: GameRow; compact?: boolean }) {
  const [failed, setFailed] = useState(false)
  if (!g.has_cover || failed)
    return <NoArt title={g.title} unmatched={!g.matched} compact={compact} />
  return (
    <img loading="lazy" src={api.mediaUrl(g.norm_key, 'cover', true)} alt=""
      onError={() => setFailed(true)} />
  )
}

function FilterRow({ name, state, onSet }: {
  name: string; state?: 'include' | 'exclude'; onSet: (v: 'include' | 'exclude') => void
}) {
  const cell = (v: 'include' | 'exclude') => (
    <button type="button" className={'fg-cell fg-c' + (state === v ? ' checked ' + v : '')}
      onClick={() => onSet(v)} aria-label={`${v} ${name}`}>
      {state === v && (
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"
          strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M20 6L9 17l-5-5" />
        </svg>
      )}
    </button>
  )
  return (
    <>
      <div className="fg-name">{name}</div>
      {cell('include')}
      {cell('exclude')}
    </>
  )
}

function SortRow({ name, rank, onSet }: {
  name: string; rank?: 1 | 2 | 3; onSet: (r: 1 | 2 | 3) => void
}) {
  const cell = (r: 1 | 2 | 3) => (
    <button type="button" className={'fg-cell fg-c' + (rank === r ? ' checked rank' : '')}
      onClick={() => onSet(r)} aria-label={`sort priority ${r} by ${name}`}>
      {rank === r && r}
    </button>
  )
  return (
    <>
      <div className="fg-name">{name}</div>
      {cell(1)}{cell(2)}{cell(3)}
    </>
  )
}

export default function App() {
  const [stats, setStats] = useState<Stats | null>(null)
  const [facets, setFacets] = useState<Facets | null>(null)
  const [q, setQ] = useState('')
  const [filters, setFilters] = useState<FilterState>({})
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [filterQ, setFilterQ] = useState('')
  const [sort, setSort] = useState<SortState>({})
  const [sortOpen, setSortOpen] = useState(false)
  const [aiMode, setAiMode] = useState(false)
  const [aiNote, setAiNote] = useState('')

  const [items, setItems] = useState<GameRow[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const [showSettings, setShowSettings] = useState(false)
  const [showProfile, setShowProfile] = useState(false)
  const [theme, setTheme] = useState<'dark' | 'light'>(
    () => (readPref('theme', 'dark') as 'dark' | 'light'))
  const [view, setView] = useState<'poster' | 'table'>(
    () => (readPref('view', 'poster') as 'poster' | 'table'))
  const [perPage, setPerPage] = useState<number>(
    () => Number(readPref('perpage', '50')) || 50)
  const [cols, setCols] = useState<string[]>(() => {
    // distinguish never-set (null -> all columns) from explicitly-empty ('' -> none)
    let raw: string | null = null
    try { raw = localStorage.getItem(prefKey('table-cols')) } catch { /* disabled */ }
    return raw === null ? TABLE_COL_IDS : raw.split(',').filter((c) => TABLE_COL_IDS.includes(c))
  })
  const [colsOpen, setColsOpen] = useState(false)

  useEffect(() => { writePref('view', view) }, [view])
  useEffect(() => { writePref('perpage', String(perPage)) }, [perPage])
  useEffect(() => { writePref('table-cols', cols.join(',')) }, [cols])

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    writePref('theme', theme)
  }, [theme])

  useEffect(() => {
    api.stats().then(setStats).catch(() => {})
    api.facets().then(setFacets).catch(() => {})
  }, [])

  const load = useCallback(async (reset: boolean) => {
    setLoading(true)
    try {
      const off = reset ? 0 : offset
      const qy: GamesQuery = {
        q: q || undefined,
        include: Object.keys(filters).filter((k) => filters[k] === 'include'),
        exclude: Object.keys(filters).filter((k) => filters[k] === 'exclude'),
        sort: ([1, 2, 3] as const)
          .map((r) => Object.keys(sort).find((k) => sort[k] === r))
          .filter((k): k is string => !!k),
        limit: perPage,
        offset: off,
      }
      const page = await api.games(qy)
      setTotal(page.total)
      setOffset(off + page.items.length)
      setItems((prev) => (reset ? page.items : [...prev, ...page.items]))
    } finally {
      setLoading(false)
    }
  }, [q, filters, sort, perPage, offset])

  const filterKey = JSON.stringify(filters)
  const sortReloadKey = JSON.stringify(sort)
  // reload on filter/sort change (debounced); AI mode doesn't auto-run
  useEffect(() => {
    if (aiMode) return
    setAiNote('')
    const t = setTimeout(() => { setOffset(0); load(true) }, 250)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, filterKey, sortReloadKey, perPage, aiMode])

  // Set a flag to include/exclude; clicking the same cell toggles it off, and
  // include/exclude are mutually exclusive per row.
  function setFlag(id: string, val: 'include' | 'exclude') {
    setFilters((prev) => {
      const next = { ...prev }
      if (next[id] === val) delete next[id]
      else next[id] = val
      return next
    })
  }
  const activeFilters = Object.keys(filters).length
  const filterSections = buildFilterSections(facets)
  const fq = filterQ.trim().toLowerCase()

  // Assign a sort key to a priority slot (1/2/3). Each slot holds one key and
  // each key one slot; clicking the current slot clears it.
  function setSortRank(key: string, rank: 1 | 2 | 3) {
    setSort((prev) => {
      const next = { ...prev }
      if (next[key] === rank) { delete next[key]; return next }
      for (const k of Object.keys(next)) if (next[k] === rank) delete next[k]
      next[key] = rank
      return next
    })
  }
  // keys in priority order (1st, 2nd, 3rd)
  const activeSort = Object.keys(sort).length
  const showCol = (id: string) => id === 'title' || cols.includes(id)
  const toggleCol = (id: string) =>
    setCols((prev) => prev.includes(id) ? prev.filter((c) => c !== id) : [...prev, id])

  async function runAi() {
    if (!q.trim()) return
    setLoading(true); setAiNote('')
    try {
      const res = await api.aiSearch(q)
      setItems(res.result.items)
      setTotal(res.result.total)
      setOffset(res.result.items.length)
      setAiNote(res.explanation)
    } catch {
      setAiNote('AI search unavailable (no API key configured) — showing a text search instead.')
      setOffset(0); load(true)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="app">
      <header>
        <div className="brand">
          <img className="logo-mark" src="/logo-mark.png" alt="" />
          <h1>ludo<span>dex</span></h1>
          {stats && (
            <div className="stats">
              {stats.games.toLocaleString()} games · {stats.media.games_with_art.toLocaleString()} with art ·{' '}
              {stats.cross_source} cross-source
            </div>
          )}
        </div>
        <div className="header-actions">
          <button className="icon-btn" title="Settings" onClick={() => setShowSettings(true)}>
            <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor"
              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
          </button>
          <div className="profile-wrap">
            <button className="profile" title="Profile" onClick={() => setShowProfile((v) => !v)}>
              <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                <circle cx="12" cy="8" r="4" fill="currentColor" />
                <path d="M4 20c0-4 4-6 8-6s8 2 8 6" fill="currentColor" />
              </svg>
            </button>
            {showProfile && (
              <div className="profile-menu" onMouseLeave={() => setShowProfile(false)}>
                <div className="pm-name">Guest</div>
                <div className="pm-sub">Not signed in</div>
                <div className="pm-divider" />
                <button className="pm-item pm-theme"
                  onClick={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}>
                  <span className="pm-theme-label">
                    {theme === 'dark' ? (
                      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
                        strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                        <circle cx="12" cy="12" r="4" />
                        <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" />
                      </svg>
                    ) : (
                      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
                        strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                        <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
                      </svg>
                    )}
                    {theme === 'dark' ? 'Light mode' : 'Dark mode'}
                  </span>
                </button>
                <div className="pm-divider" />
                <button className="pm-item" disabled>Sign in (coming soon)</button>
              </div>
            )}
          </div>
        </div>
      </header>

      <div className="controls">
        <label className="switch ai-switch has-tip"
          data-tip="Natural-language search. Describe what you want — e.g. “co-op platformers I own” or “unplayed RPGs with a cover” — and AI turns it into a catalog filter. Hit Ask or press Enter.">
          <input type="checkbox" checked={aiMode} onChange={(e) => setAiMode(e.target.checked)} />
          <span className="track"><span className="knob" /></span>
          <span className="switch-text">✨ AI</span>
        </label>
        <input
          className="search"
          placeholder={aiMode ? 'Ask: "co-op platformers I own"…' : 'Search titles…'}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (aiMode && e.key === 'Enter') runAi() }}
        />
        {aiMode && <button className="go" onClick={runAi}>Ask</button>}
        <div className={'filter-wrap' + (filtersOpen ? '' : ' has-tip')}
          data-tip="Narrow the library. Each row has two boxes: check Include to keep only games that match, or Exclude to hide games that match — one box per row. Rules combine (e.g. Steam + Matched, but not Emulation). Type in the search box to find a row.">
          <button className={'filter-btn' + (activeFilters ? ' on' : '')}
            disabled={aiMode} onClick={() => setFiltersOpen((v) => !v)}>
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor"
              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M22 3H2l8 9.46V19l4 2v-8.54L22 3z" />
            </svg>
            Filters{activeFilters ? ` (${activeFilters})` : ''}
          </button>
          {filtersOpen && !aiMode && (
            <div className="filter-menu" onMouseLeave={() => setFiltersOpen(false)}>
              <div className="filter-head">
                <span>Filters</span>
                {activeFilters > 0 &&
                  <button className="filter-clear" onClick={() => setFilters({})}>
                    Clear ({activeFilters})</button>}
              </div>
              <input className="filter-search" placeholder="Search attributes…"
                value={filterQ} onChange={(e) => setFilterQ(e.target.value)} autoFocus />
              <div className="filter-scroll">
                <div className="filter-grid">
                  <div className="fg-h">Name</div>
                  <div className="fg-h fg-c">Incl</div>
                  <div className="fg-h fg-c">Excl</div>
                  {filterSections.map((sec) => {
                    const rows = fq
                      ? sec.rows.filter((r) => r.name.toLowerCase().includes(fq))
                      : sec.rows
                    if (!rows.length) return null
                    return (
                      <Fragment key={sec.title}>
                        <div className="fg-section">{sec.title}</div>
                        {rows.map((r) => (
                          <FilterRow key={r.id} name={r.name} state={filters[r.id]}
                            onSet={(v) => setFlag(r.id, v)} />
                        ))}
                      </Fragment>
                    )
                  })}
                </div>
              </div>
            </div>
          )}
        </div>
        <div className={'filter-wrap' + (sortOpen ? '' : ' has-tip')}
          data-tip="Sort by up to three things at once. Put a 1 next to your main sort, a 2 for the tiebreaker, and a 3 for the next — so “1st: System, 2nd: Matched” groups by system, then matched first within each. One pick per column; click a pick again to clear it.">
          <button className={'filter-btn' + (activeSort ? ' on' : '')}
            disabled={aiMode} onClick={() => setSortOpen((v) => !v)}>
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor"
              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M3 6h12M3 12h9M3 18h6M17 8l3-3 3 3M20 5v14" />
            </svg>
            Sort{activeSort ? ` (${activeSort})` : ''}
          </button>
          {sortOpen && !aiMode && (
            <div className="filter-menu" onMouseLeave={() => setSortOpen(false)}>
              <div className="filter-head">
                <span>Sort by</span>
                {activeSort > 0 &&
                  <button className="filter-clear" onClick={() => setSort({})}>Clear</button>}
              </div>
              <div className="filter-scroll">
                <div className="filter-grid sort-grid">
                  <div className="fg-h">Name</div>
                  <div className="fg-h fg-c">1st</div>
                  <div className="fg-h fg-c">2nd</div>
                  <div className="fg-h fg-c">3rd</div>
                  {SORT_SECTIONS.map((sec) => (
                    <Fragment key={sec.title}>
                      <div className="fg-section">{sec.title}</div>
                      {sec.rows.map((r) => (
                        <SortRow key={r.id} name={r.name} rank={sort[r.id]}
                          onSet={(rk) => setSortRank(r.id, rk)} />
                      ))}
                    </Fragment>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {aiNote && <div className="ai-note">{aiNote}</div>}
      <div className="results-bar">
        <div className="count">{total.toLocaleString()} results</div>
        <div className="results-tools">
          <label className="per-page">
            Per page
            <select value={perPage} onChange={(e) => setPerPage(Number(e.target.value))}>
              {PAGE_OPTIONS.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <div className="view-toggle">
          <button className={view === 'poster' ? 'active' : ''} title="Poster view"
            onClick={() => setView('poster')}>
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" />
              <rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" />
            </svg> Posters
          </button>
          <button className={view === 'table' ? 'active' : ''} title="Table view"
            onClick={() => setView('table')}>
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <line x1="3" y1="6" x2="21" y2="6" /><line x1="3" y1="12" x2="21" y2="12" /><line x1="3" y1="18" x2="21" y2="18" />
            </svg> Table
          </button>
          </div>
          {view === 'table' && (
            <div className="filter-wrap">
              <button className="filter-btn" onClick={() => setColsOpen((v) => !v)}
                title="Choose which columns to show">
                <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor"
                  strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <rect x="3" y="3" width="18" height="18" rx="1" />
                  <line x1="9" y1="3" x2="9" y2="21" /><line x1="15" y1="3" x2="15" y2="21" />
                </svg>
                Columns
              </button>
              {colsOpen && (
                <div className="filter-menu cols-menu" onMouseLeave={() => setColsOpen(false)}>
                  <div className="filter-head"><span>Table columns</span></div>
                  {TABLE_COLS.map((c) => (
                    <label key={c.id} className="col-item">
                      <input type="checkbox" checked={cols.includes(c.id)}
                        onChange={() => toggleCol(c.id)} />
                      {c.label}
                    </label>
                  ))}
                  <div className="col-note">Title always shown.</div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {view === 'poster' ? (
        <div className="grid">
          {items.map((g) => (
            <button key={g.norm_key} className="card" onClick={() => setSelected(g.norm_key)}>
              <div className="cover">
                <Cover g={g} />
              </div>
              <div className="title">{g.title}</div>
              <div className="srcs">{g.sources_summary}</div>
            </button>
          ))}
        </div>
      ) : (
        <table className="game-table">
          <thead>
            <tr>
              {showCol('art') && <th className="gt-art"></th>}
              <th>Title</th>
              {showCol('platforms') && <th>Platforms</th>}
              {showCol('matched') && <th className="gt-num">Identified</th>}
              {showCol('n_sources') && <th className="gt-num">Sources</th>}
              {showCol('sources') && <th>Available from</th>}
              {showCol('n_kinds') && <th className="gt-num">Media</th>}
              {showCol('has_cover') && <th className="gt-num">Cover</th>}
            </tr>
          </thead>
          <tbody>
            {items.map((g) => (
              <tr key={g.norm_key} onClick={() => setSelected(g.norm_key)}>
                {showCol('art') && <td className="gt-art"><Cover g={g} compact /></td>}
                <td className="gt-title">{g.title}</td>
                {showCol('platforms') && <td className="gt-plat">{g.platforms
                  ? g.platforms.split(',').map((p) => <span key={p} className="pill">{p}</span>)
                  : <span className="dim">—</span>}</td>}
                {showCol('matched') &&
                  <td className="gt-num">{g.matched ? '✓' : <span className="dim">—</span>}</td>}
                {showCol('n_sources') && <td className="gt-num">{g.n_sources}</td>}
                {showCol('sources') && <td className="gt-srcs">{g.sources_summary}</td>}
                {showCol('n_kinds') && <td className="gt-num">{g.n_kinds}</td>}
                {showCol('has_cover') && <td className="gt-num">{g.has_cover ? '✓' : '—'}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {items.length < total && (
        <button className="more" disabled={loading} onClick={() => load(false)}>
          {loading ? 'Loading…' : `Load more (${items.length}/${total})`}
        </button>
      )}

      {selected && <Detail nk={selected} onClose={() => setSelected(null)} />}
      {showSettings && <Settings onClose={() => setShowSettings(false)} />}
    </div>
  )
}

const PROVIDER_LABELS: Record<string, { name: string; hint: string }> = {
  anthropic: { name: 'Anthropic (Claude)', hint: 'console.anthropic.com — pay-as-you-go' },
  openai: { name: 'OpenAI', hint: 'platform.openai.com — pay-as-you-go' },
  gemini: { name: 'Google Gemini', hint: 'aistudio.google.com — free tier available' },
  openrouter: { name: 'OpenRouter', hint: 'openrouter.ai — one key, many models' },
}
const KEY_FIELD: Record<string, string> = {
  anthropic: 'anthropic_api_key', openai: 'openai_api_key',
  gemini: 'gemini_api_key', openrouter: 'openrouter_api_key',
}

// settings sections (left nav) → subsections (top tabs). Extensible.
const SECTIONS = [{ id: 'ai', name: 'AI settings', icon: '✨' }]
const SUBSECTIONS: Record<string, { id: string; name: string }[]> = {
  ai: [{ id: 'usage', name: 'AI Usage' }, { id: 'keys', name: 'API Keys' }],
}

function Settings({ onClose }: { onClose: () => void }) {
  const [section, setSection] = useState('ai')
  const [sub, setSub] = useState('usage')
  const [cfg, setCfg] = useState<AiConfig | null>(null)

  const reload = () => api.aiConfig().then(setCfg).catch(() => {})
  useEffect(() => { reload() }, [])

  const subs = SUBSECTIONS[section] ?? []

  return (
    <div className="overlay" onClick={onClose}>
      <div className="settings-window" onClick={(e) => e.stopPropagation()}>
        <nav className="settings-nav">
          <div className="settings-title">Settings</div>
          {SECTIONS.map((s) => (
            <button key={s.id}
              className={'nav-item' + (section === s.id ? ' sel' : '')}
              onClick={() => { setSection(s.id); setSub((SUBSECTIONS[s.id] ?? [])[0]?.id ?? '') }}>
              <span className="nav-icon">{s.icon}</span>{s.name}
            </button>
          ))}
        </nav>
        <div className="settings-main">
          <button className="close" onClick={onClose}>×</button>
          <div className="settings-tabs">
            {subs.map((t) => (
              <button key={t.id}
                className={'tab' + (sub === t.id ? ' sel' : '')}
                onClick={() => setSub(t.id)}>{t.name}</button>
            ))}
          </div>
          <div className="settings-content">
            {!cfg ? <div className="loading">Loading…</div>
              : sub === 'usage' ? <AiUsage cfg={cfg} onChange={reload} />
              : sub === 'keys' ? <ApiKeys cfg={cfg} onChange={reload} />
              : null}
          </div>
        </div>
      </div>
    </div>
  )
}

function providerName(id: string | null): string {
  if (!id) return '—'
  return PROVIDER_LABELS[id]?.name ?? id
}

// model picker: free-text with provider's models as datalist suggestions
function ModelInput({ models, value, placeholder, onSave }: {
  models: string[]; value: string; placeholder?: string; onSave: (m: string) => void
}) {
  // Always include the current value so a saved model that isn't in the live
  // catalog (e.g. a curated default) still shows as the selected option.
  const opts = value && !models.includes(value) ? [value, ...models] : models
  return (
    <select className="model-input" value={value} onChange={(e) => onSave(e.target.value)}>
      <option value="">{placeholder ? `Default (${placeholder})` : '— choose model —'}</option>
      {opts.map((m) => <option key={m} value={m}>{m}</option>)}
    </select>
  )
}

function AiUsage({ cfg, onChange }: { cfg: AiConfig; onChange: () => void }) {
  const [dedupeOpen, setDedupeOpen] = useState(false)
  const [liveModels, setLiveModels] = useState<Record<string, string[]>>({})
  const [refreshing, setRefreshing] = useState(false)
  const prov = (id: string | null) => cfg.providers.find((p) => p.id === id)
  // Live, full model catalog per provider (falls back to the curated hints in cfg).
  const modelsFor = (id: string | null) =>
    (id && liveModels[id]) || prov(id)?.models || []

  // Fetch each configured provider's catalog. refresh=true busts the server cache
  // so newly added / removed provider models show up without a server restart.
  const loadModels = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true)
    try {
      const rows = await Promise.all(
        cfg.providers.filter((p) => p.configured).map((p) =>
          api.aiModels(p.id, refresh).then((r) => [p.id, r.models] as const).catch(() => null)
        ))
      const m: Record<string, string[]> = {}
      for (const row of rows) if (row) m[row[0]] = row[1]
      setLiveModels(m)
    } finally {
      if (refresh) setRefreshing(false)
    }
  }, [cfg.providers])

  useEffect(() => { loadModels(false) }, [loadModels])

  async function setDefaultProvider(p: string) { await api.setAiConfig({ provider: p }); onChange() }
  async function saveDefaultModel(m: string) {
    if (cfg.default.provider)
      await api.setAiConfig({ models: { [cfg.default.provider + '_model']: m } })
    onChange()
  }
  async function setArea(id: string, provider: string, model: string) {
    await api.setAiConfig({ areas: { [id]: { provider, model } } }); onChange()
  }

  return (
    <>
      <h2>AI Usage</h2>
      <p className="dim">
        Pick the AI model for each part of the interface — choose a provider, then the
        actual model. “Default” inherits the global default below. Subscriptions can’t
        power the app — use API keys (Gemini has a free tier). See <code>AI.md</code>.
      </p>

      <div className="default-row">
        <span className="dr-label">Global default</span>
        <select value={cfg.default.provider ?? ''} onChange={(e) => setDefaultProvider(e.target.value)}>
          {cfg.providers.map((p) => (
            <option key={p.id} value={p.id} disabled={!p.configured}>
              {providerName(p.id)}{p.configured ? '' : ' (no key)'}
            </option>
          ))}
        </select>
        <ModelInput models={modelsFor(cfg.default.provider)}
          value={cfg.default.model ?? ''} onSave={saveDefaultModel} />
        <button className="refresh-btn" onClick={() => loadModels(true)} disabled={refreshing}
          title="Re-fetch each provider's model list from its API">
          {refreshing ? '↻ Refreshing…' : '↻ Refresh models'}
        </button>
      </div>

      <table className="usage-table">
        <thead><tr><th>Interface area</th><th>Provider</th><th>Model</th></tr></thead>
        <tbody>
          {cfg.areas.map((a) => {
            const effProv = a.assigned ?? cfg.default.provider
            return (
              <tr key={a.id}>
                <td>
                  <div className="area-name">{a.name}
                    {a.status !== 'live' && <span className="tag soon">{a.status}</span>}</div>
                  <div className="area-desc">{a.description}</div>
                  {a.id === 'dedupe' && (
                    <button className="run-btn" onClick={() => setDedupeOpen(true)}>▶ Run dedupe assist</button>
                  )}
                </td>
                <td>
                  <select value={a.assigned ?? ''} onChange={(e) => setArea(a.id, e.target.value, '')}>
                    <option value="">Default ({providerName(cfg.default.provider)})</option>
                    {cfg.providers.map((p) => (
                      <option key={p.id} value={p.id} disabled={!p.configured}>
                        {providerName(p.id)}{p.configured ? '' : ' (no key)'}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <ModelInput models={modelsFor(effProv)}
                    value={a.assigned_model ?? ''}
                    placeholder={a.effective_model ?? 'model'}
                    onSave={(m) => setArea(a.id, a.assigned ?? '', m)} />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>

      {dedupeOpen && <Dedupe onClose={() => setDedupeOpen(false)} />}
    </>
  )
}

function ApiKeys({ cfg, onChange }: { cfg: AiConfig; onChange: () => void }) {
  const [keys, setKeys] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  async function save() {
    setSaving(true); setSaved(false)
    try {
      const payload: Record<string, string> = {}
      Object.entries(keys).forEach(([k, v]) => { if (v !== '') payload[k] = v })
      await api.setAiConfig({ keys: payload }); setKeys({}); setSaved(true); onChange()
    } finally { setSaving(false) }
  }
  async function clearKey(field: string) {
    await api.setAiConfig({ keys: { [field]: '' } }); onChange()
  }

  return (
    <>
      <h2>API Keys</h2>
      <p className="dim">
        Tokens are stored on the server and only ever shown obscured (first 3 + last 4
        characters). Type a new value to replace, or Clear to remove. See <code>AI.md</code>
        for where to get each key.
      </p>
      {cfg.providers.map((p) => (
        <div key={p.id} className="key-row">
          <div className="key-head">
            <span className="prov-name">{providerName(p.id)}</span>
            <span className="prov-hint">{PROVIDER_LABELS[p.id]?.hint}</span>
          </div>
          <div className="key-controls">
            <code className={'masked' + (p.configured ? '' : ' empty')}>
              {p.configured ? p.masked : 'not set'}
            </code>
            <input type="password" autoComplete="off"
              placeholder={p.configured ? 'type to replace' : 'paste API key'}
              value={keys[KEY_FIELD[p.id]] ?? ''}
              onChange={(e) => setKeys({ ...keys, [KEY_FIELD[p.id]]: e.target.value })} />
            {p.configured && <button className="clear-btn" onClick={() => clearKey(KEY_FIELD[p.id])}>Clear</button>}
          </div>
        </div>
      ))}
      <div className="settings-actions">
        <button className="go" disabled={saving} onClick={save}>{saving ? 'Saving…' : 'Save keys'}</button>
        {saved && <span className="saved">Saved ✓</span>}
      </div>
    </>
  )
}

function Dedupe({ onClose }: { onClose: () => void }) {
  const [loading, setLoading] = useState(true)
  const [items, setItems] = useState<DedupeSuggestion[]>([])
  const [err, setErr] = useState('')
  useEffect(() => {
    api.dedupe(12).then((d) => setItems(d.suggestions))
      .catch((e) => setErr(String(e))).finally(() => setLoading(false))
  }, [])
  return (
    <div className="overlay dedupe-overlay" onClick={onClose}>
      <div className="panel dedupe-panel" onClick={(e) => e.stopPropagation()}>
        <button className="close" onClick={onClose}>×</button>
        <h2>Dedupe assist</h2>
        <p className="dim">Likely same-game pairs that title-matching missed. The AI’s
          verdict is review-only — nothing is merged automatically.</p>
        {loading ? <div className="loading">Scanning… (AI is reviewing candidate pairs)</div>
          : err ? <div className="loading">Couldn’t run dedupe: {err}</div>
          : items.length === 0 ? <div className="loading">No likely duplicates found.</div>
          : (
            <div className="dedupe-list">
              {items.map((s, i) => (
                <div key={i} className={'dup' + (s.same ? ' same' : ' diff')}>
                  <div className="dup-verdict">
                    {s.same ? '≈ same game' : '≠ different'}
                    {s.confidence != null && <span className="conf">{Math.round(s.confidence * 100)}%</span>}
                  </div>
                  <div className="dup-titles"><b>{s.a}</b> <span className="vs">vs</span> <b>{s.b}</b></div>
                  <div className="dup-reason">{s.reason}</div>
                </div>
              ))}
            </div>
          )}
      </div>
    </div>
  )
}

function ArtPicker({ nk }: { nk: string }) {
  const [res, setRes] = useState<ArtPick | null>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [applied, setApplied] = useState<number | null>(null)

  async function run() {
    setLoading(true); setErr(''); setRes(null); setApplied(null)
    try { setRes(await api.artPick(nk, 'cover')) }
    catch (e) { setErr('Art pick unavailable (' + e + ')') }
    finally { setLoading(false) }
  }
  async function apply(id: number) {
    try { await api.artApply(id, nk, 'cover'); setApplied(id) } catch { /* ignore */ }
  }

  return (
    <section className="artpick">
      <h3>Smart art pick
        <button className="run-btn" disabled={loading} onClick={run}>
          {loading ? 'Analyzing…' : '✨ Pick best cover'}
        </button>
      </h3>
      {err && <div className="dim">{err}</div>}
      {res && (res.candidates.length < 2
        ? <div className="dim">{res.reason}</div>
        : (
          <>
            <div className="cand-row">
              {res.candidates.map((c) => (
                <figure key={c.id}
                  className={(c.id === res.recommended_id ? 'rec ' : '') + (c.id === applied ? 'applied' : '')}>
                  <img loading="lazy" src={api.assetUrl(c.id, true)} alt={c.provider} />
                  <figcaption>{c.provider}
                    {c.id === res.recommended_id && <span className="rec-tag">AI pick</span>}</figcaption>
                  <button className="apply-btn" onClick={() => apply(c.id)}>
                    {c.id === applied ? 'Set ✓' : 'Use'}</button>
                </figure>
              ))}
            </div>
            <div className="dim artpick-reason">{res.reason}</div>
          </>
        ))}
    </section>
  )
}

function Detail({ nk, onClose }: { nk: string; onClose: () => void }) {
  const [d, setD] = useState<GameDetail | null>(null)
  useEffect(() => { api.detail(nk).then(setD).catch(() => {}) }, [nk])

  return (
    <div className="overlay" onClick={onClose}>
      <div className="panel" onClick={(e) => e.stopPropagation()}>
        <button className="close" onClick={onClose}>×</button>
        {!d ? <div className="loading">Loading…</div> : (
          <>
            <h2>{d.title}</h2>
            <div className="media-row">
              {d.media_kinds.map((k) => (
                <figure key={k}>
                  <img loading="lazy" src={api.mediaUrl(d.norm_key, k)} alt={k}
                       onError={(e) => { (e.currentTarget.parentElement as HTMLElement).style.display = 'none' }} />
                  <figcaption>{k}</figcaption>
                </figure>
              ))}
            </div>

            <ArtPicker nk={d.norm_key} />

            {Object.keys(d.attributes).length > 0 && (
              <section>
                <h3>Attributes</h3>
                <dl>
                  {Object.entries(d.attributes).map(([k, v]) => (
                    <div key={k}><dt>{k}</dt><dd>{v.join(', ')}</dd></div>
                  ))}
                </dl>
              </section>
            )}

            <section>
              <h3>Sources ({d.sources.length})</h3>
              <table>
                <tbody>
                  {d.sources.map((s, i) => (
                    <tr key={i}>
                      <td className="badge">{s.source}</td>
                      <td>{s.platform}</td>
                      <td>{s.title_raw}</td>
                      <td className="dim">{s.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>

            {d.metadata_links.length > 0 && (
              <section className="links">
                <h3>Links</h3>
                {d.metadata_links.map((l, i) => (
                  <a key={i} href={l.url} target="_blank" rel="noreferrer">{l.provider}</a>
                ))}
              </section>
            )}
          </>
        )}
      </div>
    </div>
  )
}
