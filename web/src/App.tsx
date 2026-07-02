import { useEffect, useState, useRef, useCallback, Fragment, type CSSProperties, type ChangeEvent, type DragEvent } from 'react'
import { api } from './api'
import type {
  GameRow, GameDetail, Stats, Facets, GamesQuery, AiConfig, AiArea,
  AiUsageModel, AiUsageProvider, AiUsageDay,
  DedupeSuggestion, ArtPick, Service, ServiceConnect, Achievements as AchData,
  MediaLibrary, MediaAsset, MediaKind,
  OpsStatus, OpsDatabase, SyncService, SyncJob, TagRef, Scores,
  Spotlight as SpotlightData, EmuLocation, IdentifyCandidate, RecognizedGame,
  Device,
  FileVariable, FileProfile, FileDetect, FilePlan, FileCommandResult,
  Runbook, RunHistoryRow, Troubleshoot, Job,
} from './api'
import './App.css'

const PAGE_OPTIONS = [25, 50, 100, 500, 1000]

type FilterState = Record<string, 'include' | 'exclude'>
type FilterRowDef = { id: string; name: string }
type FilterSection = { title: string; rows: FilterRowDef[] }

// Sort keys (ids match server SORT_SQL). A key can occupy one of 3 priority slots.
const SORT_SECTIONS: FilterSection[] = [
  { title: 'General', rows: [
    { id: 'ludodex_score', name: 'Ludodex score' },
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
  { id: 'score', label: 'Score' },
  { id: 'platforms', label: 'Platforms' },
  { id: 'matched', label: 'Identified' },
  { id: 'n_sources', label: 'Sources' },
  { id: 'sources', label: 'Available from' },
  { id: 'tags', label: 'Tags' },
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
  psn: 'PlayStation', xbox: 'Xbox', emulation: 'Emulation', archive: 'Local archive',
}
const srcLabel = (s: string) => SRC_LABEL[s] || s.charAt(0).toUpperCase() + s.slice(1)

// Keep an open dropdown/overlay open until the user clicks outside it (or
// re-clicks its own toggle button, which lives inside the ref'd wrapper) —
// replaces the twitchy close-on-mouse-leave. Attach the returned ref to the
// element that wraps both the toggle button and the menu.
function useClickOutside<T extends HTMLElement>(active: boolean, onClose: () => void) {
  const ref = useRef<T>(null)
  const cb = useRef(onClose)
  cb.current = onClose
  useEffect(() => {
    if (!active) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) cb.current()
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [active])
  return ref
}

// Build the include/exclude filter sections from live facets. Status flags are
// bare tokens; sources/systems use source:/system: tokens (see server FLAG_SQL).
// Each section's rows are sorted alphabetically (case-insensitive) by display name.
function buildFilterSections(facets: Facets | null): FilterSection[] {
  const srcs = (facets?.sources || []).filter((s) => s !== 'playnite' && s !== 'launchbox')
  const byName = (a: { name: string }, b: { name: string }) =>
    a.name.localeCompare(b.name, undefined, { sensitivity: 'base' })
  return [
    { title: 'Status', rows: [
      { id: 'matched', name: 'Matched (identified)' },
      { id: 'has_cover', name: 'Has cover' },
      { id: 'cross_source', name: 'Cross-source' },
    ].sort(byName) },
    { title: 'Sources', rows: [
      ...srcs.map((s) => ({ id: 'source:' + s, name: srcLabel(s) })),
      { id: 'playnite', name: 'Playnite' },
      { id: 'launchbox', name: 'LaunchBox' },
    ].sort(byName) },
    { title: 'Systems', rows: (facets?.platforms || [])
      .map((p) => ({ id: 'system:' + p, name: p })).sort(byName) },
  ]
}

// Human label for a filter token, falling back to a prettified id when the row
// isn't in the current sections yet (e.g. facets still loading).
function prettifyFilterId(id: string): string {
  const bare = id.replace(/^(source|system):/, '')
  return bare.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

// Deterministic hue (0–359) from a title, so a game without art always gets the
// same generated-cover color.
function hueOf(s: string): number {
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0
  return h % 360
}

// Particle-burst tab bar: a sliding underline plus an 8-spark burst on each
// selection. Adapted (React port) from CodeFronts' "Particle Burst" CSS tabs,
// MIT licensed — https://codefronts.com/navigation/css-tabs/particle-burst/
// See web/THIRD_PARTY.md for the attribution/licence notice.
const BURST_COLORS = ['#ff6b6b', '#ffd166', '#06d6a0', '#118ab2', '#ef476f']

function ParticleTabs({ tabs, active, onSelect, className, fill }: {
  tabs: { id: string; label: string }[]
  active: string
  onSelect: (id: string) => void
  className?: string
  fill?: boolean
}) {
  const navRef = useRef<HTMLElement>(null)
  const lineRef = useRef<HTMLSpanElement>(null)
  const btnRefs = useRef<Record<string, HTMLButtonElement | null>>({})

  // Slide the underline to sit under the active tab (also on resize / tab changes).
  const reposition = useCallback(() => {
    const btn = btnRefs.current[active]
    const line = lineRef.current
    if (!btn || !line) return
    line.style.left = btn.offsetLeft + 'px'
    line.style.width = btn.offsetWidth + 'px'
  }, [active])
  useEffect(() => { reposition() }, [reposition, tabs.length])
  useEffect(() => {
    window.addEventListener('resize', reposition)
    return () => window.removeEventListener('resize', reposition)
  }, [reposition])

  // Spawn 8 colour sparks from the tab centre that fly outward and fade.
  const burst = (btn: HTMLButtonElement) => {
    const nav = navRef.current
    if (!nav || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const rect = btn.getBoundingClientRect()
    const navRect = nav.getBoundingClientRect()
    const cx = rect.left - navRect.left + rect.width / 2
    const cy = rect.top - navRect.top + rect.height / 2
    for (let i = 0; i < 8; i++) {
      const s = document.createElement('span')
      s.className = 'pt-spark'
      s.style.left = cx + 'px'
      s.style.top = cy + 'px'
      s.style.background = BURST_COLORS[i % BURST_COLORS.length]
      const angle = (i / 8) * Math.PI * 2
      const dist = 32 + Math.random() * 18
      s.style.setProperty('--dx', Math.cos(angle) * dist + 'px')
      s.style.setProperty('--dy', Math.sin(angle) * dist + 'px')
      nav.appendChild(s)
      setTimeout(() => { s.remove() }, 700)
    }
  }

  const pick = (id: string, el: HTMLButtonElement) => {
    if (id !== active) onSelect(id)
    burst(el)
  }

  return (
    <nav className={'pt-nav' + (fill ? ' fill' : '') + (className ? ' ' + className : '')}
      ref={navRef}>
      <span className="pt-line" aria-hidden="true" ref={lineRef} />
      {tabs.map((t) => (
        <button key={t.id} data-t
          ref={(el) => { btnRefs.current[t.id] = el }}
          className={'pt-tab' + (t.id === active ? ' active' : '')}
          onClick={(e) => pick(t.id, e.currentTarget)}>{t.label}</button>
      ))}
    </nav>
  )
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
function Cover({ g, compact }: {
  g: { norm_key: string; title: string; has_cover: boolean; matched: boolean }
  compact?: boolean
}) {
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
  const [showAddGame, setShowAddGame] = useState(false)
  const [prefsTick, setPrefsTick] = useState(0)   // bump to push prefs changes live
  // Dashboard is always the landing page (not persisted), per product decision.
  const [tab, setTab] = useState<'library' | 'dashboard'>('dashboard')
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
  // Currently-applied filters, resolved to display names for the "Applied" chips.
  const filterNames = new Map<string, string>()
  filterSections.forEach((s) => s.rows.forEach((r) => filterNames.set(r.id, r.name)))
  const appliedFilters = Object.entries(filters).map(([id, state]) => ({
    id, state, name: filterNames.get(id) || prettifyFilterId(id),
  }))
  // Dropdowns stay open until an outside click or a re-click of their toggle.
  const filtersRef = useClickOutside<HTMLDivElement>(filtersOpen && !aiMode, () => setFiltersOpen(false))
  const sortRef = useClickOutside<HTMLDivElement>(sortOpen && !aiMode, () => setSortOpen(false))
  const colsRef = useClickOutside<HTMLDivElement>(colsOpen, () => setColsOpen(false))
  const profileRef = useClickOutside<HTMLDivElement>(showProfile, () => setShowProfile(false))

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
          <JobMonitor />
          <SyncMenu />
          <button className="icon-btn" title="Settings" onClick={() => setShowSettings(true)}>
            <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor"
              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
          </button>
          <ServerOps />
          <div className="profile-wrap" ref={profileRef}>
            <button className="profile" title="Profile" onClick={() => setShowProfile((v) => !v)}>
              <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                <circle cx="12" cy="8" r="4" fill="currentColor" />
                <path d="M4 20c0-4 4-6 8-6s8 2 8 6" fill="currentColor" />
              </svg>
            </button>
            {showProfile && (
              <div className="profile-menu">
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

      <ParticleTabs className="main-tabs" fill active={tab}
        onSelect={(id) => setTab(id as 'library' | 'dashboard')}
        tabs={[{ id: 'dashboard', label: 'Dashboard' }, { id: 'library', label: 'Library' }]} />

      {tab === 'dashboard' && <Dashboard stats={stats} onBrowse={() => setTab('library')}
        onFilter={(f) => { setFilters(f); setTab('library') }} onOpen={setSelected}
        prefsTick={prefsTick} />}

      {tab === 'library' && (<>
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
        <div className={'filter-wrap' + (filtersOpen ? '' : ' has-tip')} ref={filtersRef}
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
            <div className="filter-menu">
              <div className="filter-head">
                <span>Filters</span>
                {activeFilters > 0 &&
                  <button className="filter-clear" onClick={() => setFilters({})}>
                    Clear ({activeFilters})</button>}
              </div>
              <input className="filter-search" placeholder="Search attributes…"
                value={filterQ} onChange={(e) => setFilterQ(e.target.value)} autoFocus />
              {appliedFilters.length > 0 && (
                <div className="filter-applied">
                  <div className="fa-title">Applied</div>
                  <div className="fa-chips">
                    {appliedFilters.map((f) => (
                      <button key={f.id} type="button" className={'fa-chip ' + f.state}
                        onClick={() => setFlag(f.id, f.state)}
                        title={`Remove — ${f.state === 'include' ? 'including' : 'excluding'} ${f.name}`}>
                        <span className="fa-tag">{f.state === 'include' ? 'Incl' : 'Excl'}</span>
                        <span className="fa-name">{f.name}</span>
                        <span className="fa-x" aria-hidden="true">×</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
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
        <div className={'filter-wrap' + (sortOpen ? '' : ' has-tip')} ref={sortRef}
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
            <div className="filter-menu">
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
            <div className="filter-wrap" ref={colsRef}>
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
                <div className="filter-menu cols-menu">
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
          <button className="filter-btn add-game" title="Add a game"
            onClick={() => setShowAddGame(true)}>
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor"
              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
            </svg>
            Add game
          </button>
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
              {showCol('score') && <th className="gt-num">Score</th>}
              <th>Title</th>
              {showCol('platforms') && <th>Platforms</th>}
              {showCol('matched') && <th className="gt-num">Identified</th>}
              {showCol('n_sources') && <th className="gt-num">Sources</th>}
              {showCol('sources') && <th>Available from</th>}
              {showCol('tags') && <th>Tags</th>}
              {showCol('n_kinds') && <th className="gt-num">Media</th>}
              {showCol('has_cover') && <th className="gt-num">Cover</th>}
            </tr>
          </thead>
          <tbody>
            {items.map((g) => (
              <tr key={g.norm_key} onClick={() => setSelected(g.norm_key)}>
                {showCol('art') && <td className="gt-art"><Cover g={g} compact /></td>}
                {showCol('score') && <td className="gt-num">
                  {g.ludodex_score != null
                    ? <ScoreBadge v={g.ludodex_score} /> : <span className="dim">—</span>}
                </td>}
                <td className="gt-title">{g.title}</td>
                {showCol('platforms') && <td className="gt-plat">{g.platforms
                  ? g.platforms.split(',').map((p) => <span key={p} className="pill">{p}</span>)
                  : <span className="dim">—</span>}</td>}
                {showCol('matched') &&
                  <td className="gt-num">{g.matched ? '✓' : <span className="dim">—</span>}</td>}
                {showCol('n_sources') && <td className="gt-num">{g.n_sources}</td>}
                {showCol('sources') && <td className="gt-srcs">{g.sources_summary}</td>}
                {showCol('tags') && <td className="gt-tags">
                  {g.tags?.length ? (<>
                    {g.tags.slice(0, 6).map((t) => <TagBadge key={t.tag} t={t} />)}
                    {g.tags.length > 6 && <span className="tag-more">+{g.tags.length - 6}</span>}
                  </>) : <span className="dim">—</span>}
                </td>}
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
      </>)}

      {selected && <Detail nk={selected} onClose={() => setSelected(null)} />}
      {showSettings && <Settings onClose={() => setShowSettings(false)}
        onPrefsChanged={() => { load(true); setPrefsTick((t) => t + 1) }} />}
      {showAddGame && <AddGame facets={facets} onClose={() => setShowAddGame(false)}
        onAdded={() => load(true)} />}
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
const SECTIONS = [
  { id: 'ai', name: 'AI settings', icon: '✨' },
  { id: 'connections', name: 'Connections', icon: '🔌' },
  { id: 'library', name: 'Library', icon: '📚' },
  { id: 'dashboard', name: 'Dashboard', icon: '🎛️' },
  { id: 'emulation', name: 'Emulation', icon: '🕹️' },
  { id: 'files', name: 'File ops', icon: '🗂️' },
]
const SUBSECTIONS: Record<string, { id: string; name: string }[]> = {
  ai: [{ id: 'usage', name: 'AI Usage' }, { id: 'keys', name: 'API Keys' },
       { id: 'report', name: 'Usage report' }],
  connections: [{ id: 'devices', name: 'Devices' },
                { id: 'credentials', name: 'Stores & providers' },
                { id: 'limits', name: 'Rate limits' }],
  library: [{ id: 'preferences', name: 'Preferences' }],
  dashboard: [{ id: 'spotlight', name: 'Spotlight' }],
  emulation: [{ id: 'storage', name: 'Storage locations' }],
  files: [{ id: 'operations', name: 'Operations' },
          { id: 'profiles', name: 'Profiles' },
          { id: 'history', name: 'History' }],
}

function Settings({ onClose, onPrefsChanged }: { onClose: () => void; onPrefsChanged: () => void }) {
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
            {section === 'connections'
              ? (sub === 'devices' ? <DevicesPanel />
                : sub === 'credentials' ? <Credentials />
                : sub === 'limits' ? <RateLimits /> : null)
              : section === 'library'
              ? <LibraryPrefs onChanged={onPrefsChanged} />
              : section === 'dashboard'
              ? <DashboardPrefs onChanged={onPrefsChanged} />
              : section === 'emulation'
              ? <EmulationStorage />
              : section === 'files'
              ? (sub === 'operations' ? <FileOpsOperations />
                : sub === 'profiles' ? <FileProfiles />
                : sub === 'history' ? <FileHistory /> : null)
              : !cfg ? <div className="loading">Loading…</div>
              : sub === 'usage' ? <AiUsage cfg={cfg} onChange={reload} />
              : sub === 'keys' ? <ApiKeys cfg={cfg} onChange={reload} />
              : sub === 'report' ? <AiUsageReport />
              : null}
          </div>
        </div>
      </div>
    </div>
  )
}

function LibraryPrefs({ onChanged }: { onChanged: () => void }) {
  const [hideNonGames, setHideNonGames] = useState<boolean | null>(null)
  useEffect(() => { api.prefs().then((p) => setHideNonGames(p.hide_non_games)).catch(() => {}) }, [])

  const toggle = async (v: boolean) => {
    setHideNonGames(v)
    try { await api.setPrefs({ hide_non_games: v }); onChanged() }
    catch { setHideNonGames(!v) }
  }

  if (hideNonGames === null) return <div className="loading">Loading…</div>
  return (
    <div className="lib-prefs">
      <div className="pref-row">
        <label className="switch">
          <input type="checkbox" checked={hideNonGames}
            onChange={(e) => toggle(e.target.checked)} />
          <span className="track"><span className="knob" /></span>
        </label>
        <div className="pref-text">
          <span className="pref-name">Hide non-games</span>
          <span className="pref-hint">
            Exclude Steam apps flagged as applications, tools, soundtracks, videos or
            hardware (from the <code>steam_type</code> signal) everywhere in the library.
          </span>
        </div>
      </div>
    </div>
  )
}

const SPOTLIGHT_PRESETS = [5, 8, 12, 20, 30, 45, 60]

function DashboardPrefs({ onChanged }: { onChanged: () => void }) {
  const [secs, setSecs] = useState<number | null>(null)
  useEffect(() => { api.prefs().then((p) => setSecs(p.spotlight_seconds)).catch(() => {}) }, [])

  const commit = async (v: number) => {
    const clamped = Math.max(3, Math.min(120, Math.round(v)))
    setSecs(clamped)
    try { await api.setPrefs({ spotlight_seconds: clamped }); onChanged() }
    catch { /* keep optimistic value */ }
  }

  if (secs === null) return <div className="loading">Loading…</div>
  return (
    <div className="lib-prefs">
      <div className="pref-block">
        <div className="pref-name">Spotlight rotation</div>
        <div className="pref-hint">
          How long each Spotlight stays on the dashboard before rotating to the next
          theme. A thin bar counts this down; hovering the Spotlight pauses it.
        </div>
        <div className="pref-control">
          <input type="range" min={3} max={60} step={1} value={secs}
            onChange={(e) => setSecs(Number(e.target.value))}
            onMouseUp={(e) => commit(Number((e.target as HTMLInputElement).value))}
            onKeyUp={(e) => commit(Number((e.target as HTMLInputElement).value))} />
          <input className="pref-num" type="number" min={3} max={120} value={secs}
            onChange={(e) => commit(Number(e.target.value))} />
          <span className="pref-unit">seconds</span>
        </div>
        <div className="pref-presets">
          {SPOTLIGHT_PRESETS.map((p) => (
            <button key={p} type="button"
              className={'preset' + (p === secs ? ' on' : '')}
              onClick={() => commit(p)}>{p}s</button>
          ))}
        </div>
      </div>
    </div>
  )
}

const ROLE_LABEL: Record<string, string> = { both: 'ROMs + Media', roms: 'ROMs', media: 'Media' }

function EmulationStorage() {
  const [locs, setLocs] = useState<EmuLocation[] | null>(null)
  const [kinds, setKinds] = useState<MediaKind[]>([])
  const [name, setName] = useState('')
  const [path, setPath] = useState('')
  const [role, setRole] = useState<'roms' | 'media' | 'both'>('both')
  const [pick, setPick] = useState<Set<string>>(new Set())   // empty = all kinds
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const load = () => api.emuLocations().then((d) => setLocs(d.locations)).catch(() => setLocs([]))
  useEffect(() => { load(); api.mediaKinds().then((d) => setKinds(d.kinds)).catch(() => {}) }, [])

  const add = async () => {
    if (!name.trim() || !path.trim()) return
    setBusy(true); setErr('')
    try {
      const d = await api.setEmuLocation({
        name: name.trim(), path: path.trim(), role,
        kinds: role === 'roms' ? [] : Array.from(pick),
      })
      setLocs(d.locations); setName(''); setPath(''); setRole('both'); setPick(new Set())
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const remove = async (n: string) => { setLocs((await api.removeEmuLocation(n)).locations) }
  const toggle = async (n: string, on: boolean) => { setLocs((await api.setEmuLocationEnabled(n, on)).locations) }
  const togglePick = (k: string) =>
    setPick((prev) => { const n = new Set(prev); n.has(k) ? n.delete(k) : n.add(k); return n })

  const STATUS = {
    mounted: { label: 'Mounted', cls: 'ok' }, present: { label: 'Present', cls: 'ok' },
    MISSING: { label: 'Missing', cls: 'err' }, unset: { label: 'Unset', cls: 'dim' },
  } as Record<string, { label: string; cls: string }>
  const showMedia = role === 'media' || role === 'both'

  if (!locs) return <div className="loading">Loading…</div>
  return (
    <div className="emu-storage">
      <div className="pref-hint" style={{ marginBottom: 14 }}>
        Folders ludodex scans for emulation. A location can hold <b>ROMs + Media</b>
        {' '}(default — everything in one place), <b>ROMs</b> only, or <b>Media</b>
        {' '}only. Add local paths, or the mount point of an SMB/network share (mount
        it at the OS level first). Media is read from the ES-DE
        {' '}<code>downloaded_media</code> layout; for a Media location you can pick
        which media types to index.
      </div>

      <div className="emu-list">
        {locs.length === 0 && <div className="sync-note dim">No locations yet.</div>}
        {locs.map((a) => (
          <div key={a.name} className={'emu-row' + (a.enabled ? '' : ' off')}>
            <label className="switch">
              <input type="checkbox" checked={a.enabled}
                onChange={(e) => toggle(a.name, e.target.checked)} />
              <span className="track"><span className="knob" /></span>
            </label>
            <div className="emu-info">
              <div className="emu-name">{a.name}
                <span className={'emu-kind role-' + a.role}>{ROLE_LABEL[a.role]}</span>
                {(a.role !== 'roms') && (
                  <span className="emu-media-count">
                    {a.kinds.length ? `${a.kinds.length} media type${a.kinds.length === 1 ? '' : 's'}` : 'all media'}</span>
                )}
                <span className={'emu-status ' + (STATUS[a.status]?.cls || 'dim')}>
                  {STATUS[a.status]?.label || a.status}</span>
              </div>
              <code className="emu-path">{a.path}</code>
            </div>
            <button className="emu-rm" title="Remove location"
              onClick={() => remove(a.name)}>×</button>
          </div>
        ))}
      </div>

      <div className="emu-add">
        <div className="emu-add-title">＋ Add a location</div>
        <div className="emu-add-grid">
          <input placeholder="Name (e.g. Deck SD, NAS roms)" value={name}
            onChange={(e) => setName(e.target.value)} />
          <input placeholder="Path (e.g. /run/media/deck/SD or /mnt/roms)" value={path}
            onChange={(e) => setPath(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') add() }} />
          <select value={role} onChange={(e) => setRole(e.target.value as 'roms' | 'media' | 'both')}>
            <option value="both">ROMs + Media (default)</option>
            <option value="roms">ROMs only</option>
            <option value="media">Media only</option>
          </select>
          <button className="go" disabled={busy || !name.trim() || !path.trim()}
            onClick={add}>{busy ? 'Adding…' : 'Add'}</button>
        </div>

        {showMedia && (
          <div className="emu-kinds">
            <div className="emu-kinds-head">
              Media types to index
              <span className="emu-kinds-note">
                {pick.size === 0 ? 'all types (nothing selected = everything)'
                  : `${pick.size} selected`}</span>
              {pick.size > 0 && <button className="emu-kinds-clear" onClick={() => setPick(new Set())}>Reset to all</button>}
            </div>
            <div className="emu-kinds-grid">
              {kinds.map((k) => (
                <label key={k.kind} className={'emu-kchip' + (pick.has(k.kind) ? ' on' : '')}
                  title={k.description}>
                  <input type="checkbox" checked={pick.has(k.kind)}
                    onChange={() => togglePick(k.kind)} />
                  {k.kind.replace(/_/g, ' ')}
                </label>
              ))}
            </div>
          </div>
        )}
        {err && <div className="connect-msg err">{err}</div>}
      </div>
    </div>
  )
}

const ADD_SOURCES = ['manual', 'steam', 'gog', 'epic', 'itch', 'ea', 'psn', 'xbox', 'emulation']

// Add a game to the library: a manual name/source/system form that identifies the
// game across providers (IGDB), or an AI pass that recognizes games in image(s).
function AddGame({ facets, onClose, onAdded }: {
  facets: Facets | null; onClose: () => void; onAdded: () => void
}) {
  const [tab, setTab] = useState<'manual' | 'image'>('manual')
  const systems = facets?.platforms || []
  const sources = Array.from(new Set([...ADD_SOURCES, ...(facets?.sources || [])]))
  const wrapRef = useClickOutside<HTMLDivElement>(true, onClose)

  return (
    <div className="overlay" onClick={onClose}>
      <div className="add-window" ref={wrapRef} onClick={(e) => e.stopPropagation()}>
        <button className="close" onClick={onClose}>×</button>
        <div className="add-title">Add a game</div>
        <ParticleTabs className="panel-tabs2" active={tab}
          onSelect={(id) => setTab(id as 'manual' | 'image')}
          tabs={[{ id: 'manual', label: 'By name' }, { id: 'image', label: 'From image (AI)' }]} />
        <div className="add-body">
          {tab === 'manual'
            ? <AddManual sources={sources} systems={systems} onAdded={onAdded} />
            : <AddFromImage sources={sources} systems={systems} onAdded={onAdded} />}
        </div>
      </div>
    </div>
  )
}

function AddManual({ sources, systems, onAdded }: {
  sources: string[]; systems: string[]; onAdded: () => void
}) {
  const [name, setName] = useState('')
  const [source, setSource] = useState('manual')
  const [platform, setPlatform] = useState('')
  const [cands, setCands] = useState<IdentifyCandidate[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const search = async () => {
    if (!name.trim()) return
    setSearching(true); setMsg(null)
    try { setCands((await api.identify(name.trim())).candidates) }
    catch (e) { setMsg({ ok: false, text: (e as Error).message }) }
    finally { setSearching(false) }
  }
  const add = async () => {
    if (!name.trim()) return
    setBusy(true); setMsg(null)
    try {
      const r = await api.addGame({ title: name.trim(), source, platform: platform.trim() || source })
      onAdded()
      setMsg({ ok: true, text: r.new_game ? `Added “${name.trim()}”.` : `Linked “${name.trim()}” (already in catalog).` })
      setName(''); setCands(null); setPlatform('')
    } catch (e) { setMsg({ ok: false, text: (e as Error).message }) }
    finally { setBusy(false) }
  }

  return (
    <div className="add-manual">
      <div className="add-hint">Enter the game, pick where you own it and what system,
        then optionally <b>Search providers</b> to confirm the exact title before adding.</div>
      <div className="add-field">
        <label>Game name</label>
        <div className="add-name-row">
          <input value={name} onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Chrono Trigger"
            onKeyDown={(e) => { if (e.key === 'Enter') search() }} />
          <button className="go" disabled={searching || !name.trim()} onClick={search}>
            {searching ? 'Searching…' : 'Search providers'}</button>
        </div>
      </div>
      {cands && (
        <div className="add-cands">
          {cands.length === 0 && <div className="sync-note dim">No matches — you can still add it as typed.</div>}
          {cands.map((c) => (
            <button key={c.igdb_id ?? c.name} type="button"
              className={'add-cand' + (name.trim() === c.name ? ' sel' : '')}
              onClick={() => setName(c.name)}>
              {c.cover ? <img src={c.cover} alt="" loading="lazy" /> : <span className="add-cand-noart" />}
              <span className="add-cand-info">
                <span className="add-cand-name">{c.name}</span>
                <span className="add-cand-meta">{[c.year, c.platforms.slice(0, 4).join(', ')].filter(Boolean).join(' · ')}</span>
              </span>
            </button>
          ))}
        </div>
      )}
      <div className="add-row2">
        <div className="add-field">
          <label>Source</label>
          <select value={source} onChange={(e) => setSource(e.target.value)}>
            {sources.map((s) => <option key={s} value={s}>{srcLabel(s)}</option>)}
          </select>
        </div>
        <div className="add-field">
          <label>System</label>
          <input list="add-systems" value={platform}
            onChange={(e) => setPlatform(e.target.value)}
            placeholder="e.g. snes, ps5, switch" />
          <datalist id="add-systems">{systems.map((s) => <option key={s} value={s} />)}</datalist>
        </div>
      </div>
      <div className="add-actions">
        <button className="go primary" disabled={busy || !name.trim()} onClick={add}>
          {busy ? 'Adding…' : 'Add to library'}</button>
        {msg && <span className={'connect-msg ' + (msg.ok ? 'ok' : 'err')}>{msg.text}</span>}
      </div>
    </div>
  )
}

function AddFromImage({ sources, systems, onAdded }: {
  sources: string[]; systems: string[]; onAdded: () => void
}) {
  const [urls, setUrls] = useState<string[]>([])
  const [folder, setFolder] = useState('')
  const [note, setNote] = useState('')
  const [recognizing, setRecognizing] = useState(false)
  const [rows, setRows] = useState<(RecognizedGame & { checked: boolean; src: string; plat: string })[] | null>(null)
  const [err, setErr] = useState('')
  const [added, setAdded] = useState(0)
  const [busy, setBusy] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const onFiles = (files: FileList | null) => {
    if (!files) return
    const arr = Array.from(files).slice(0, 8)
    Promise.all(arr.map((f) => new Promise<string>((res) => {
      const r = new FileReader(); r.onload = () => res(r.result as string); r.readAsDataURL(f)
    }))).then((u) => { setUrls(u); setRows(null); setAdded(0); setErr('') })
  }
  const matchSource = (s: string) => {
    const l = s.toLowerCase()
    return sources.find((x) => x === l || srcLabel(x).toLowerCase() === l) || 'manual'
  }
  const matchSystem = (p: string) => {
    const l = p.toLowerCase()
    return systems.find((x) => x.toLowerCase() === l) || l
  }
  const toRows = (games: RecognizedGame[]) => games.map((g) => ({ ...g, checked: true,
    src: matchSource(g.source), plat: matchSystem(g.platform) }))
  const recognize = async () => {
    if (!urls.length) return
    setRecognizing(true); setErr(''); setNote('')
    try { setRows(toRows((await api.identifyImage(urls)).games)) }
    catch (e) { setErr((e as Error).message) }
    finally { setRecognizing(false) }
  }
  const scanFolder = async () => {
    if (!folder.trim()) return
    setRecognizing(true); setErr(''); setNote(''); setRows(null); setAdded(0)
    try {
      const r = await api.identifyFolder(folder.trim())
      setRows(toRows(r.games))
      setNote(`Scanned ${r.scanned}${r.total_found > r.scanned ? ` of ${r.total_found}` : ''} image${r.scanned === 1 ? '' : 's'}${r.total_found > r.scanned ? ' (raise the cap to scan more)' : ''}.`)
    } catch (e) { setErr((e as Error).message) }
    finally { setRecognizing(false) }
  }
  const addSelected = async () => {
    if (!rows) return
    setBusy(true); setErr(''); let n = 0
    for (const g of rows.filter((r) => r.checked)) {
      try { await api.addGame({ title: g.title, source: g.src, platform: g.plat || g.src }); n++ }
      catch { /* skip failures, keep going */ }
    }
    setAdded(n); onAdded(); setBusy(false)
  }
  const upd = (i: number, patch: Partial<{ checked: boolean; src: string; plat: string }>) =>
    setRows((prev) => prev!.map((r, j) => j === i ? { ...r, ...patch } : r))

  return (
    <div className="add-image">
      <div className="add-hint">Drop in one or more images — box art, a screenshot, a
        store page, or a whole shelf of cases. The AI finds every game it can and you
        pick which to add.</div>
      <div className="add-drop" onClick={() => fileRef.current?.click()}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => { e.preventDefault(); onFiles(e.dataTransfer.files) }}>
        {urls.length ? <div className="add-thumbs">{urls.map((u, i) =>
          <img key={i} src={u} alt="" />)}</div>
          : <span>Click or drop images here (up to 8)</span>}
        <input ref={fileRef} type="file" accept="image/*" multiple hidden
          onChange={(e) => onFiles(e.target.files)} />
      </div>
      {urls.length > 0 && !rows && (
        <button className="go primary" disabled={recognizing} onClick={recognize}>
          {recognizing ? 'Looking…' : `Recognize games in ${urls.length} image${urls.length === 1 ? '' : 's'}`}</button>
      )}
      <div className="add-or-folder">or point to a folder of images on the server</div>
      <div className="add-name-row">
        <input value={folder} onChange={(e) => setFolder(e.target.value)}
          placeholder="/mnt/roms/box-art  (server path — mount network shares first)"
          onKeyDown={(e) => { if (e.key === 'Enter') scanFolder() }} />
        <button className="go" disabled={recognizing || !folder.trim()} onClick={scanFolder}>
          {recognizing ? 'Scanning…' : 'Scan folder'}</button>
      </div>
      {note && <div className="add-hint" style={{ margin: '8px 0 0' }}>{note}</div>}
      {err && <div className="connect-msg err">{err}</div>}
      {rows && (
        <div className="add-reco">
          {rows.length === 0 && <div className="sync-note dim">No games recognized in those images.</div>}
          {rows.map((g, i) => (
            <div key={i} className={'add-reco-row' + (g.checked ? '' : ' off')}>
              <input type="checkbox" checked={g.checked} onChange={(e) => upd(i, { checked: e.target.checked })} />
              <span className="add-reco-name">{g.title}
                {g.confidence > 0 && <span className="add-reco-conf">{Math.round(g.confidence * 100)}%</span>}</span>
              <select value={g.src} onChange={(e) => upd(i, { src: e.target.value })}>
                {sources.map((s) => <option key={s} value={s}>{srcLabel(s)}</option>)}
              </select>
              <input className="add-reco-plat" list="add-systems2" value={g.plat}
                placeholder="system" onChange={(e) => upd(i, { plat: e.target.value })} />
            </div>
          ))}
          <datalist id="add-systems2">{systems.map((s) => <option key={s} value={s} />)}</datalist>
          {rows.length > 0 && (
            <div className="add-actions">
              <button className="go primary" disabled={busy || !rows.some((r) => r.checked)}
                onClick={addSelected}>
                {busy ? 'Adding…' : `Add ${rows.filter((r) => r.checked).length} selected`}</button>
              {added > 0 && <span className="connect-msg ok">Added {added} game{added === 1 ? '' : 's'}.</span>}
            </div>
          )}
        </div>
      )}
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

function fmtTok(n: number): string {
  if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + 'M'
  if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + 'k'
  return String(n)
}

// Monthly-cap input (blank = unlimited); commits on blur / Enter.
function CapInput({ value, onSave }: { value: number; onSave: (v: number) => void }) {
  const [v, setV] = useState(value ? String(value) : '')
  useEffect(() => { setV(value ? String(value) : '') }, [value])
  const commit = () => {
    const n = parseInt((v || '').replace(/[^0-9]/g, '') || '0', 10)
    if (n !== value) onSave(n)
  }
  return <input className="cap-input" type="text" inputMode="numeric" placeholder="∞"
    value={v} onChange={(e) => setV(e.target.value)} onBlur={commit}
    onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }} />
}

// 30-day stacked (input+output) token bar chart.
function UsageChart({ days }: { days: AiUsageDay[] }) {
  const max = Math.max(1, ...days.map((d) => d.input + d.output))
  const total = days.reduce((s, d) => s + d.input + d.output, 0)
  return (
    <div className="usage-chart">
      <div className="uc-bars">
        {days.map((d, i) => (
          <div key={d.day} className={'uc-bar' + (i === days.length - 1 ? ' today' : '')}
            title={`${d.day}: ${(d.input + d.output).toLocaleString()} tokens (${d.input.toLocaleString()} in / ${d.output.toLocaleString()} out · ${d.calls} calls)`}>
            <div className="uc-in" style={{ height: `${(d.input / max) * 100}%` }} />
            <div className="uc-out" style={{ height: `${(d.output / max) * 100}%` }} />
          </div>
        ))}
      </div>
      <div className="uc-legend">
        <span><i className="uc-sw in" /> input</span>
        <span><i className="uc-sw out" /> output</span>
        <span className="dim">30-day total: {total.toLocaleString()} tokens · today highlighted</span>
      </div>
    </div>
  )
}

function AiUsageReport() {
  const [data, setData] = useState<{ models: AiUsageModel[]; providers: AiUsageProvider[] } | null>(null)
  const [sel, setSel] = useState<AiUsageModel | null>(null)
  const [series, setSeries] = useState<AiUsageDay[] | null>(null)

  const load = () => api.aiUsage().then(setData).catch(() => setData({ models: [], providers: [] }))
  useEffect(() => { load() }, [])

  const openSeries = async (m: AiUsageModel) => {
    setSel(m); setSeries(null)
    try { setSeries((await api.aiUsageSeries(m.provider, m.model)).days) } catch { setSeries([]) }
  }
  const setCap = async (scope: 'provider' | 'model', key: string, v: number) => {
    setData(await api.setAiLimit(scope, key, v))
  }

  if (!data) return <div className="loading">Loading…</div>
  return (
    <>
      <h2>Usage report</h2>
      <p className="dim">Token usage per provider and model. Set a monthly cap (tokens)
        to <b>stop calls</b> once a provider or model reaches it — leave blank for
        unlimited. Click a model for its 30-day history.</p>

      {data.models.length === 0 ? (
        <div className="sync-note dim">No AI usage recorded yet — it appears here after
          you run AI features (search, art pick, add-by-image, dedupe).</div>
      ) : (
        <>
          <div className="usage-providers">
            {data.providers.map((p) => (
              <div key={p.provider} className={'usage-prov' + (p.cap > 0 && p.month >= p.cap ? ' over' : '')}>
                <span className="up-name">{providerName(p.provider)}</span>
                <span className="up-month">{fmtTok(p.month)}<span className="dim"> /mo</span></span>
                <label className="up-cap">cap
                  <CapInput value={p.cap} onSave={(v) => setCap('provider', p.provider, v)} /></label>
              </div>
            ))}
          </div>

          <div className="usage-list">
            {data.models.map((m) => {
              const over = m.model_cap > 0 && m.month >= m.model_cap
              const on = sel && sel.model === m.model && sel.provider === m.provider
              return (
                <div key={m.provider + '/' + m.model}
                  className={'usage-row' + (on ? ' sel' : '') + (over ? ' over' : '')}
                  onClick={() => openSeries(m)}>
                  <div className="ur-main">
                    <span className="ur-model">{m.model}</span>
                    <span className="ur-prov">{providerName(m.provider)}</span>
                  </div>
                  <div className="ur-nums">
                    <span title="this month">{fmtTok(m.month)}<span className="dim">/mo</span></span>
                    <span className="dim" title="lifetime total">{fmtTok(m.total)}</span>
                    <span className="dim" title="calls">{m.calls}×</span>
                  </div>
                  <label className="ur-cap" onClick={(e) => e.stopPropagation()}>cap
                    <CapInput value={m.model_cap} onSave={(v) => setCap('model', m.model, v)} /></label>
                </div>
              )
            })}
          </div>

          {sel && (
            <div className="usage-detail">
              <div className="ud-title">{sel.model} <span className="dim">· {providerName(sel.provider)} · last 30 days</span></div>
              {!series ? <div className="loading">Loading…</div> : <UsageChart days={series} />}
            </div>
          )}
        </>
      )}
    </>
  )
}

// Per-area system-prompt editor: shows the effective prompt (custom override or
// default), lets you edit + save it, or reset back to the built-in default.
function AreaPromptEditor({ area, onSave }: { area: AiArea; onSave: (prompt: string) => void }) {
  const base = area.prompt ?? area.default_prompt
  const [text, setText] = useState(base)
  const overriding = area.prompt != null
  const dirty = text !== base
  return (
    <div className="prompt-editor">
      <div className="prompt-meta">
        <span>System prompt {overriding
          ? <span className="tag soon">customized</span>
          : <span className="dim">(default)</span>}</span>
        {area.prompt_vars.length > 0 && (
          <span className="prompt-vars">Placeholders (kept verbatim, filled at run time):
            {' '}{area.prompt_vars.map((v) => <code key={v}>{`<<${v}>>`}</code>)}</span>
        )}
      </div>
      <textarea className="prompt-text" value={text} rows={9} spellCheck={false}
        onChange={(e) => setText(e.target.value)} />
      <div className="prompt-actions">
        <button className="go primary" disabled={!dirty} onClick={() => onSave(text)}>Save prompt</button>
        <button className="ghost" disabled={!overriding && text === area.default_prompt}
          onClick={() => { setText(area.default_prompt); onSave('') }}>Reset to default</button>
        {dirty && <span className="dim">unsaved changes</span>}
      </div>
    </div>
  )
}

function AiUsage({ cfg, onChange }: { cfg: AiConfig; onChange: () => void }) {
  const [dedupeOpen, setDedupeOpen] = useState(false)
  const [promptOpen, setPromptOpen] = useState<string | null>(null)
  const [liveModels, setLiveModels] = useState<Record<string, string[]>>({})
  const [visionModels, setVisionModels] = useState<Record<string, string[]>>({})
  const [refreshing, setRefreshing] = useState(false)
  const prov = (id: string | null) => cfg.providers.find((p) => p.id === id)
  // Live, full model catalog per provider (falls back to the curated hints in cfg).
  const modelsFor = (id: string | null) =>
    (id && liveModels[id]) || prov(id)?.models || []
  // Image-capable subset (for the image-analysis default + vision areas).
  const visionFor = (id: string | null) =>
    (id && visionModels[id]) || modelsFor(id)

  // Fetch each configured provider's catalog (full + vision-only). refresh=true
  // busts the server cache so new/removed provider models show up without restart.
  const loadModels = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true)
    try {
      const cfgd = cfg.providers.filter((p) => p.configured)
      const [full, vis] = await Promise.all([
        Promise.all(cfgd.map((p) => api.aiModels(p.id, refresh, false)
          .then((r) => [p.id, r.models] as const).catch(() => null))),
        Promise.all(cfgd.map((p) => api.aiModels(p.id, refresh, true)
          .then((r) => [p.id, r.models] as const).catch(() => null))),
      ])
      const m: Record<string, string[]> = {}, v: Record<string, string[]> = {}
      for (const row of full) if (row) m[row[0]] = row[1]
      for (const row of vis) if (row) v[row[0]] = row[1]
      setLiveModels(m); setVisionModels(v)
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
  async function setVisionProvider(p: string) { await api.setAiConfig({ vision: { provider: p, model: '' } }); onChange() }
  async function saveVisionModel(m: string) { await api.setAiConfig({ vision: { model: m } }); onChange() }
  async function setArea(id: string, provider: string, model: string) {
    await api.setAiConfig({ areas: { [id]: { provider, model } } }); onChange()
  }
  async function savePrompt(id: string, prompt: string) {
    await api.setAiConfig({ areas: { [id]: { prompt } } }); onChange()
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
          title="Re-fetch each provider's model list from its API (image-analysis rows show only vision-capable models)">
          {refreshing ? '↻ Refreshing…' : '↻ Refresh models'}
        </button>
      </div>

      <div className="default-row">
        <span className="dr-label">Image analysis <span className="dr-sub">(vision)</span></span>
        <select value={cfg.vision_default.assigned ?? ''}
          onChange={(e) => setVisionProvider(e.target.value)}>
          <option value="">Same as global default</option>
          {cfg.providers.map((p) => (
            <option key={p.id} value={p.id} disabled={!p.configured}>
              {providerName(p.id)}{p.configured ? '' : ' (no key)'}
            </option>
          ))}
        </select>
        <ModelInput models={visionFor(cfg.vision_default.provider)}
          value={cfg.vision_default.assigned_model ?? ''}
          placeholder={cfg.vision_default.model ?? 'vision model'}
          onSave={saveVisionModel} />
        <button className="refresh-btn" onClick={() => loadModels(true)} disabled={refreshing}
          title="Re-fetch model lists from each provider's API — this row shows only vision-capable models">
          {refreshing ? '↻ Refreshing…' : '↻ Refresh models'}
        </button>
      </div>

      <table className="usage-table">
        <thead><tr><th>Interface area</th><th>Provider</th><th>Model</th></tr></thead>
        <tbody>
          {cfg.areas.map((a) => {
            const dfltProv = a.vision ? cfg.vision_default.provider : cfg.default.provider
            const effProv = a.assigned ?? dfltProv
            const open = promptOpen === a.id
            return (
              <Fragment key={a.id}>
              <tr>
                <td>
                  <div className="area-name">{a.name}
                    {a.vision && <span className="tag vision">vision</span>}
                    {a.prompt && <span className="tag soon">custom prompt</span>}
                    {a.status !== 'live' && <span className="tag soon">{a.status}</span>}</div>
                  <div className="area-desc">{a.description}</div>
                  <div className="area-btns">
                    <button className="link-btn" onClick={() => setPromptOpen(open ? null : a.id)}>
                      {open ? '▾ Hide prompt' : '✎ Edit prompt'}</button>
                    {a.id === 'dedupe' && (
                      <button className="run-btn" onClick={() => setDedupeOpen(true)}>▶ Run dedupe assist</button>
                    )}
                  </div>
                </td>
                <td>
                  <select value={a.assigned ?? ''} onChange={(e) => setArea(a.id, e.target.value, '')}>
                    <option value="">
                      {a.vision ? `Image default (${providerName(dfltProv)})` : `Default (${providerName(dfltProv)})`}
                    </option>
                    {cfg.providers.map((p) => (
                      <option key={p.id} value={p.id} disabled={!p.configured}>
                        {providerName(p.id)}{p.configured ? '' : ' (no key)'}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <ModelInput models={a.vision ? visionFor(effProv) : modelsFor(effProv)}
                    value={a.assigned_model ?? ''}
                    placeholder={a.effective_model ?? 'model'}
                    onSave={(m) => setArea(a.id, a.assigned ?? '', m)} />
                </td>
              </tr>
              {open && (
                <tr className="prompt-row">
                  <td colSpan={3}><AreaPromptEditor area={a}
                    onSave={(p) => savePrompt(a.id, p)} /></td>
                </tr>
              )}
              </Fragment>
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

type LmKinds = Record<string, [string, boolean, boolean]>

function AddManager({ deviceId, kinds, onAdded }: {
  deviceId: number; kinds: [string, [string, boolean, boolean]][]
  onAdded: (d: { devices: Device[] }) => void
}) {
  const [kind, setKind] = useState(kinds[0]?.[0] || 'roms')
  const [name, setName] = useState('')
  const [rom, setRom] = useState('')
  const [media, setMedia] = useState('')
  const [busy, setBusy] = useState(false)
  const add = async () => {
    setBusy(true)
    try {
      onAdded(await api.setManager({ device_id: deviceId, kind, name, rom_path: rom, media_path: media }))
      setName(''); setRom(''); setMedia('')
    } finally { setBusy(false) }
  }
  return (
    <div className="dm-add">
      <select value={kind} onChange={(e) => setKind(e.target.value)}>
        {kinds.map(([k, v]) => <option key={k} value={k}>{v[0]}</option>)}
      </select>
      <input placeholder="label (optional)" value={name} onChange={(e) => setName(e.target.value)} />
      <input placeholder="ROM path on device" value={rom} onChange={(e) => setRom(e.target.value)} />
      <input placeholder="media path (optional)" value={media} onChange={(e) => setMedia(e.target.value)} />
      <button className="ops-btn" disabled={busy} onClick={add}>＋ add</button>
    </div>
  )
}

function AddDevice({ onAdded }: { onAdded: (d: { devices: Device[] }) => void }) {
  const blank = { name: '', transport: 'ssh', host: '', port: 22, username: '', auth: 'alias', key_path: '', password: '', share: '' }
  const [f, setF] = useState(blank)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const up = (k: string, v: string | number) => setF((p) => ({ ...p, [k]: v }))
  const add = async () => {
    if (!f.name.trim()) return
    setBusy(true); setErr('')
    try { onAdded(await api.setDevice(f)); setF(blank) }
    catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  return (
    <div className="dev-add">
      <div className="dev-add-title">＋ Add a device</div>
      <div className="dev-add-grid">
        <input placeholder="Name (e.g. Steam Deck)" value={f.name} onChange={(e) => up('name', e.target.value)} />
        <select value={f.transport} onChange={(e) => up('transport', e.target.value)}>
          <option value="ssh">SSH</option><option value="smb">SMB</option><option value="local">Local</option>
        </select>
        {f.transport !== 'local' && <>
          <input placeholder="host / IP / ssh alias" value={f.host} onChange={(e) => up('host', e.target.value)} />
          <select value={f.auth} onChange={(e) => up('auth', e.target.value)}>
            <option value="alias">~/.ssh config alias</option>
            <option value="key">key file</option>
            <option value="password">password</option>
          </select>
          {f.auth !== 'alias' && <input placeholder="username" value={f.username} onChange={(e) => up('username', e.target.value)} />}
          {f.auth === 'key' && <input placeholder="key path (~/.ssh/id_ed25519)" value={f.key_path} onChange={(e) => up('key_path', e.target.value)} />}
          {f.auth === 'password' && <input type="password" placeholder="password" value={f.password} onChange={(e) => up('password', e.target.value)} />}
          <input className="dev-port" type="number" placeholder="port" value={f.port} onChange={(e) => up('port', Number(e.target.value) || 22)} />
        </>}
        <button className="go primary" disabled={busy || !f.name.trim()} onClick={add}>Add device</button>
      </div>
      {err && <div className="connect-msg err">{err}</div>}
    </div>
  )
}

function DevicesPanel() {
  const [data, setData] = useState<{ devices: Device[]; lm_kinds: LmKinds } | null>(null)
  const [test, setTest] = useState<Record<number, { ok: boolean; detail: string }>>({})
  const [sync, setSync] = useState<Record<number, { device?: string; results?: { manager: string; ok: boolean; roms?: number; media?: string; archive?: number; error?: string }[]; error?: string }>>({})
  const [busy, setBusy] = useState<number | null>(null)

  const load = () => api.devices().then(setData).catch(() => setData({ devices: [], lm_kinds: {} }))
  useEffect(() => { load() }, [])
  // mutations return only {devices}; keep the lm_kinds we already have
  const apply = (d: { devices: Device[] }) =>
    setData((prev) => prev ? { ...prev, devices: d.devices } : { devices: d.devices, lm_kinds: {} })
  const testDev = async (id: number) => {
    setBusy(id)
    try { const r = await api.testDevice(id); setTest((t) => ({ ...t, [id]: r })) }
    catch (e) { setTest((t) => ({ ...t, [id]: { ok: false, detail: (e as Error).message } })) }
    finally { setBusy(null) }
  }
  const syncDev = async (id: number) => {
    setBusy(id); setSync((s) => ({ ...s, [id]: {} }))
    try { const r = await api.syncDevice(id); setSync((s) => ({ ...s, [id]: r })); load() }
    catch (e) { setSync((s) => ({ ...s, [id]: { error: (e as Error).message } })) }
    finally { setBusy(null) }
  }

  if (!data) return <div className="loading">Loading…</div>
  const kinds = Object.entries(data.lm_kinds) as [string, [string, boolean, boolean]][]
  return (
    <>
      <h2>Devices</h2>
      <p className="dim">Machines that host your game libraries (Steam Deck, a PC, a NAS).
        ludodex reaches each over <b>SSH</b> and pulls its ROMs + media. Add a device,
        then add the <b>library managers</b> on it (RetroDECK/ES-DE, RetroBat, Playnite,
        LaunchBox, or a raw ROM folder) with their paths. Creds are stored locally,
        never in 1Password. (SMB needs cifs-utils on the server.)</p>

      {data.devices.length === 0 && <div className="sync-note dim">No devices yet — add one below.</div>}
      {data.devices.map((d) => (
        <div key={d.id} className="dev-card">
          <div className="dev-head">
            <span className="dev-name">{d.name}</span>
            <span className="dev-conn">{d.transport}
              {d.host ? ` · ${d.username ? d.username + '@' : ''}${d.host}${d.port && d.port !== 22 ? ':' + d.port : ''}` : ''}
              {' · '}{d.auth}{d.has_password ? ' 🔑' : ''}</span>
            <div className="dev-actions">
              <button className="ops-btn" disabled={busy === d.id} onClick={() => testDev(d.id)}>Test</button>
              <button className="ops-btn" disabled={busy === d.id} onClick={() => syncDev(d.id)}>{busy === d.id ? 'Syncing…' : 'Sync'}</button>
              <button className="emu-rm" title="Remove device" onClick={async () => apply(await api.removeDevice(d.id))}>×</button>
            </div>
          </div>
          {test[d.id] && <div className={'connect-msg ' + (test[d.id].ok ? 'ok' : 'err')}>{test[d.id].ok ? '✓ ' : '✗ '}{test[d.id].detail}</div>}
          {sync[d.id] && (sync[d.id].error
            ? <div className="connect-msg err">{sync[d.id].error}</div>
            : sync[d.id].results && <div className="dev-sync">{sync[d.id].results!.map((r, i) =>
                <div key={i} className={r.ok ? 'ok' : 'err'}>{r.ok ? '✓' : '✗'} {r.manager}: {r.ok ? (r.archive != null ? `${r.archive} archived files` : `${r.roms ?? 0} roms${r.media ? ' + media' : ''}`) : r.error}</div>)}</div>)}
          <div className="dev-mgrs">
            {d.managers.map((m) => (
              <div key={m.id} className="dev-mgr">
                <span className="dm-kind">{m.kind_label}</span>
                <span className="dm-name">{m.name || m.kind}</span>
                <code className="dm-path">{[m.rom_path && 'ROMs: ' + m.rom_path, m.media_path && 'Media: ' + m.media_path].filter(Boolean).join('   ·   ') || '(no paths set)'}</code>
                <button className="emu-rm" title="Remove" onClick={async () => apply(await api.removeManager(m.id))}>×</button>
              </div>
            ))}
            <AddManager deviceId={d.id} kinds={kinds} onAdded={apply} />
          </div>
        </div>
      ))}
      <AddDevice onAdded={apply} />
    </>
  )
}

function Credentials() {
  const [data, setData] = useState<Service[] | null>(null)
  const [vals, setVals] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const reload = () => api.servicesConfig().then((d) => setData(d.services)).catch(() => {})
  useEffect(() => { reload() }, [])

  async function save() {
    setSaving(true); setSaved(false)
    try {
      const payload: Record<string, string> = {}
      Object.entries(vals).forEach(([k, v]) => { if (v !== '') payload[k] = v })
      await api.setServices(payload); setVals({}); setSaved(true); reload()
    } finally { setSaving(false) }
  }
  const clearField = async (key: string) => { await api.setServices({ [key]: '' }); reload() }

  if (!data) return <div className="loading">Loading…</div>

  const groups: { title: string; roles: string[] }[] = [
    { title: 'Sources & Providers', roles: ['both'] },
    { title: 'Sources (establish ownership)', roles: ['source'] },
    { title: 'Providers (enrich metadata / art)', roles: ['provider'] },
  ]
  return (
    <>
      <h2>Service credentials</h2>
      <p className="dim">
        Credentials are stored on the server; secrets are only ever shown obscured
        (first 3 + last 4). Type a value to replace, or Clear to remove.
        <strong> Sources</strong> establish what you own; <strong>Providers</strong> enrich
        games with metadata/art — a few services do both.
      </p>
      {groups.map((g) => {
        const svcs = data.filter((s) => g.roles.includes(s.role))
        if (!svcs.length) return null
        return (
          <div key={g.title} className="svc-group">
            <div className="svc-group-title">{g.title}</div>
            {svcs.map((s) => (
              <div key={s.id} className={'svc-card' + (s.enabled === false ? ' off' : '')}>
                <div className="key-head">
                  <span className="prov-name">{s.name}</span>
                  {s.role !== 'provider' && (
                    <label className="switch svc-enable" title="Include this source when syncing">
                      <input type="checkbox" checked={s.enabled !== false}
                        onChange={async (e) => { await api.setSourceEnabled(s.id, e.target.checked); reload() }} />
                      <span className="track"><span className="knob" /></span>
                      <span className="switch-text">{s.enabled === false ? 'Off' : 'On'}</span>
                    </label>
                  )}
                  <span className="prov-hint">{s.hint}</span>
                </div>
                {s.fields.map((f) => (
                  <div key={f.key} className="svc-field">
                    <span className="svc-label">{f.label}</span>
                    <code className={'masked' + (f.configured ? '' : ' empty')}>
                      {f.configured ? f.value : 'not set'}
                    </code>
                    <input type={f.secret ? 'password' : 'text'} autoComplete="off"
                      placeholder={f.configured ? 'type to replace' : 'enter value'}
                      value={vals[f.key] ?? ''}
                      onChange={(e) => setVals({ ...vals, [f.key]: e.target.value })} />
                    {f.configured &&
                      <button className="clear-btn" onClick={() => clearField(f.key)}>Clear</button>}
                  </div>
                ))}
                {s.connect && <ConnectFlow connect={s.connect} onDone={reload} />}
              </div>
            ))}
          </div>
        )
      })}
      <div className="settings-actions">
        <button className="go" disabled={saving} onClick={save}>
          {saving ? 'Saving…' : 'Save credentials'}</button>
        {saved && <span className="saved">Saved ✓</span>}
      </div>
    </>
  )
}

// In-UI connect flow for services that authenticate by pasting a browser token
// (EA): a link to open the auth URL, a paste box, and a Connect button — no CLI.
function ConnectFlow({ connect, onDone }: { connect: ServiceConnect; onDone: () => void }) {
  const [val, setVal] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const go = async () => {
    if (!val.trim()) return
    setBusy(true); setMsg(null)
    try {
      const r = await api.connectService(connect.post, val.trim())
      if (r.ok) { setMsg({ ok: true, text: `Connected${r.account ? ' as ' + r.account : ''} ✓` }); setVal(''); onDone() }
      else setMsg({ ok: false, text: r.error || 'That token didn’t work.' })
    } catch (e) { setMsg({ ok: false, text: (e as Error).message }) }
    finally { setBusy(false) }
  }

  return (
    <div className="connect-flow">
      <div className="connect-status">
        {connect.connected
          ? <span className="conn-ok">● Connected</span>
          : <span className="conn-off">○ Not connected</span>}
      </div>
      <div className="connect-row">
        <a className="connect-link" href={connect.url} target="_blank" rel="noreferrer">
          {connect.action_label} ↗
        </a>
        <input className="connect-input" placeholder={connect.field_label}
          value={val} onChange={(e) => setVal(e.target.value)} />
        <button className="go" disabled={busy || !val.trim()} onClick={go}>
          {busy ? 'Connecting…' : 'Connect'}</button>
      </div>
      {connect.note && <div className="connect-note">{connect.note}</div>}
      {msg && <div className={'connect-msg' + (msg.ok ? ' ok' : ' err')}>{msg.text}</div>}
    </div>
  )
}

function RateLimits() {
  const [data, setData] = useState<Service[] | null>(null)
  const [vals, setVals] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const reload = () => api.servicesConfig().then((d) => setData(d.services)).catch(() => {})
  useEffect(() => { reload() }, [])

  async function save() {
    setSaving(true); setSaved(false)
    try {
      await api.setServices(vals); setVals({}); setSaved(true); reload()
    } finally { setSaving(false) }
  }

  if (!data) return <div className="loading">Loading…</div>
  return (
    <>
      <h2>Rate limits</h2>
      <p className="dim">
        How conservatively ludodex calls each service's API. <strong>Cooldown</strong> is the
        minimum wait between requests; <strong>per minute / per day</strong> cap request volume
        (blank = the default shown, or unlimited). Raise these if a provider starts throttling
        or returning quota errors.
      </p>
      {data.map((s) => (
        <div key={s.id} className="svc-card">
          <div className="key-head">
            <span className="prov-name">{s.name}</span>
            <span className="prov-hint">{s.hint}</span>
          </div>
          {s.limits.map((f) => (
            <div key={f.key} className="svc-field">
              <span className="svc-label">{f.label}</span>
              <input type="number" min="0" inputMode="numeric"
                placeholder={f.default ? `default ${f.default}` : 'unlimited'}
                value={vals[f.key] ?? f.value}
                onChange={(e) => setVals({ ...vals, [f.key]: e.target.value })} />
              <span className="svc-unit">{f.unit}</span>
            </div>
          ))}
        </div>
      ))}
      <div className="settings-actions">
        <button className="go" disabled={saving} onClick={save}>
          {saving ? 'Saving…' : 'Save limits'}</button>
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

function fmtBytes(n: number) {
  if (!n) return '0 B'
  const u = ['B', 'KB', 'MB', 'GB']
  const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)))
  return (n / Math.pow(1024, i)).toFixed(i ? 1 : 0) + ' ' + u[i]
}
function fmtUptime(s: number) {
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`
}

// Header dropdown: restart the server + database health checks / maintenance.
// Library sync: pull owned games per store, then rebuild the catalog. Stores that
// need a browser sign-in (Epic/EA) surface their connect flow inline and sync the
// moment auth completes.
function SyncMenu() {
  const [open, setOpen] = useState(false)
  const [svcs, setSvcs] = useState<SyncService[]>([])
  const [job, setJob] = useState<SyncJob | null>(null)
  const [msg, setMsg] = useState('')

  const load = useCallback(async () => {
    try { const s = await api.syncStatus(); setSvcs(s.services); setJob(s.job) }
    catch { /* offline */ }
  }, [])
  useEffect(() => { if (open) load() }, [open, load])
  const running = !!job?.running
  // Stay open until an outside click; but don't let a click-away abort a run.
  const wrapRef = useClickOutside<HTMLDivElement>(open, () => { if (!running) setOpen(false) })
  useEffect(() => {
    if (!running) return
    const t = setInterval(load, 1500)
    return () => clearInterval(t)
  }, [running, load])

  const enabled = svcs.filter((s) => s.enabled)
  const anyReady = enabled.some((s) => s.ready)

  const runAll = async () => {
    setMsg('')
    try { setJob(await api.syncRun(['all'])) } catch (e) { setMsg((e as Error).message) }
    load()
  }
  const runOne = async (id: string) => {
    setMsg('')
    try { setJob(await api.syncRun([id])) } catch (e) { setMsg((e as Error).message) }
    load()
  }
  // After a browser connect (Epic/EA) succeeds, sync that store — but only once any
  // in-flight job finishes, since a single sync runs at a time.
  const connectThenSync = (id: string) => async () => {
    const tick = async () => {
      try {
        const s = await api.syncStatus()
        if (s.job?.running) { setTimeout(tick, 1500); return }
      } catch { /* */ }
      runOne(id)
    }
    tick()
  }

  const rowState = (id: string) => job?.services?.[id]?.state

  return (
    <div className="sync-wrap filter-wrap" ref={wrapRef}>
      <button className={'icon-btn' + (running ? ' spin' : '')} title="Sync library"
        onClick={() => setOpen((v) => !v)}>
        <svg viewBox="0 0 24 24" width="21" height="21" fill="none" stroke="currentColor"
          strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M21 2v6h-6" /><path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
          <path d="M3 22v-6h6" /><path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
        </svg>
      </button>
      {open && (
        <div className="filter-menu sync-menu">
          <div className="filter-head">
            <span>Sync library</span>
            {running && job?.step && <span className="sync-step">{job.step}</span>}
          </div>
          <button className="go sync-all" disabled={running || !anyReady} onClick={runAll}>
            {running ? 'Syncing…' : 'Sync all configured'}
          </button>
          {!anyReady && !running && (
            <div className="sync-note dim">Nothing ready yet — connect a store below.</div>
          )}

          <div className="sync-list">
            {enabled.map((s) => {
              const js = rowState(s.id)
              return (
                <div key={s.id} className="sync-row">
                  <div className="sync-row-head">
                    <span className="sync-name">{s.name}</span>
                    <span className="sync-meta">
                      {js === 'running' ? <span className="sync-run">syncing…</span>
                        : js === 'ok' ? <span className="conn-ok">✓ {(s.count ?? 0).toLocaleString()}</span>
                        : js === 'failed' ? <span className="conn-off">✗ failed</span>
                        : s.count != null ? `${s.count.toLocaleString()} owned`
                        : s.ready ? 'ready' : ''}
                    </span>
                    {s.ready && js !== 'running' && (
                      <button className="ops-btn" disabled={running}
                        onClick={() => runOne(s.id)}>Sync</button>
                    )}
                  </div>
                  {js === 'failed' && job?.services?.[s.id]?.error && (
                    <div className="sync-err">{job.services[s.id].error}</div>
                  )}
                  {s.needs_auth && s.connect && js !== 'running' && (
                    <div className="sync-auth">
                      <div className="sync-auth-label">Sign in to sync</div>
                      <ConnectFlow connect={s.connect} onDone={connectThenSync(s.id)} />
                    </div>
                  )}
                  {!s.ready && !s.needs_auth && (
                    <div className="sync-note dim">Add credentials in Settings</div>
                  )}
                </div>
              )
            })}
          </div>

          {!running && job?.added != null && (
            <div className="sync-done">Added {job.added} new game{job.added === 1 ? '' : 's'}.</div>
          )}
          {!running && job?.error && <div className="sync-err">{job.error}</div>}
          {msg && <div className="sync-err">{msg}</div>}
        </div>
      )}
    </div>
  )
}

function ServerOps() {
  const [open, setOpen] = useState(false)
  const [status, setStatus] = useState<OpsStatus | null>(null)
  const [dbs, setDbs] = useState<OpsDatabase[]>([])
  const [busy, setBusy] = useState('')          // db id or 'restart' or 'check'
  const [msg, setMsg] = useState('')
  const [restarting, setRestarting] = useState(false)
  // Stay open until an outside click; don't close mid-operation.
  const wrapRef = useClickOutside<HTMLDivElement>(open, () => { if (!busy) setOpen(false) })

  const load = useCallback(() => {
    api.opsStatus().then((s) => { setStatus(s); setDbs(s.databases) }).catch(() => {})
  }, [])
  useEffect(() => { if (open) load() }, [open, load])

  const checkAll = async () => {
    setBusy('check'); setMsg('')
    try { setDbs((await api.dbCheck('all')).results) }
    catch { setMsg('Health check failed') } finally { setBusy('') }
  }
  const fix = async (db: string, action: 'optimize' | 'recover') => {
    if (action === 'recover' &&
      !confirm(`Rebuild "${db}" from a SQL dump? The current file is backed up as .bak first.`)) return
    setBusy(db); setMsg('')
    try {
      const r = await api.dbFix(db, action)
      setMsg(action === 'optimize'
        ? `${db}: reclaimed ${fmtBytes(r.reclaimed ?? 0)}`
        : `${db}: rebuilt (backup ${r.backup})`)
      setDbs((await api.dbCheck('all')).results)
    } catch (e) { setMsg(`${db}: ${(e as Error).message}`) } finally { setBusy('') }
  }
  const restart = async () => {
    if (!confirm('Restart the Ludodex server? The app will be briefly unavailable.')) return
    setBusy('restart'); setRestarting(true); setMsg('Restarting…')
    try { await api.opsRestart() } catch { /* connection drops as it re-execs */ }
    // poll until it answers again
    let n = 0
    const poll = setInterval(async () => {
      n++
      try {
        const s = await api.opsStatus()
        if (s.services[0]?.uptime_seconds < 30 || n > 20) {
          clearInterval(poll); setStatus(s); setDbs(s.databases)
          setRestarting(false); setBusy(''); setMsg('Server restarted ✓')
        }
      } catch { /* still down */ }
    }, 1000)
  }

  const statusDot = (s?: string) =>
    s === 'ok' ? 'ok' : s === 'error' ? 'err' : 'muted'

  return (
    <div className="ops-wrap filter-wrap" ref={wrapRef}>
      <button className="icon-btn" title="Server operations" onClick={() => setOpen((v) => !v)}>
        <svg viewBox="0 0 24 24" width="21" height="21" fill="none" stroke="currentColor"
          strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <rect x="2" y="3" width="20" height="7" rx="2" />
          <rect x="2" y="14" width="20" height="7" rx="2" />
          <line x1="6" y1="6.5" x2="6.01" y2="6.5" /><line x1="6" y1="17.5" x2="6.01" y2="17.5" />
        </svg>
      </button>
      {open && (
        <div className="filter-menu ops-menu">
          <div className="filter-head"><span>Server operations</span></div>

          <div className="ops-section">Services</div>
          {status?.services.map((s) => (
            <div key={s.id} className="ops-svc">
              <div>
                <div className="ops-svc-name">
                  <span className={'ops-dot ' + (restarting ? 'muted' : 'ok')} />
                  {s.name}
                </div>
                <div className="ops-svc-meta">
                  {restarting ? 'restarting…'
                    : `pid ${s.pid} · up ${fmtUptime(s.uptime_seconds)} · :${s.port}`}
                </div>
              </div>
              <button className="ops-btn danger" disabled={!!busy} onClick={restart}>
                {busy === 'restart' ? '…' : 'Restart'}
              </button>
            </div>
          ))}

          <div className="ops-section ops-section-row">
            <span>Databases</span>
            <button className="ops-link" disabled={!!busy} onClick={checkAll}>
              {busy === 'check' ? 'checking…' : 'Check all'}
            </button>
          </div>
          {dbs.map((d) => (
            <div key={d.id} className="ops-db">
              <span className={'ops-dot ' + statusDot(d.status)} />
              <div className="ops-db-main">
                <div className="ops-db-name">{d.name}
                  <span className="ops-db-role">{d.role}</span>
                </div>
                <div className="ops-db-meta">
                  {d.exists ? fmtBytes(d.size) : 'missing'}
                  {d.status && d.status !== 'ok' ? ` · ${d.detail}` : ''}
                </div>
              </div>
              {d.exists && d.size > 0 && (
                <div className="ops-db-actions">
                  <button className="ops-btn" disabled={!!busy}
                    onClick={() => fix(d.id, 'optimize')}
                    title="PRAGMA optimize + REINDEX + VACUUM">
                    {busy === d.id ? '…' : 'Optimize'}</button>
                  {d.status === 'error' && (
                    <button className="ops-btn danger" disabled={!!busy}
                      onClick={() => fix(d.id, 'recover')}
                      title="Rebuild from SQL dump (backs up original)">Repair</button>
                  )}
                </div>
              )}
            </div>
          ))}
          {msg && <div className="ops-msg">{msg}</div>}
        </div>
      )}
    </div>
  )
}

const KIND_ORDER: Record<string, number> = {}
function kindRank(k: string) {
  return k in KIND_ORDER ? KIND_ORDER[k] : 999
}

const OS_NAME: Record<string, string> = { windows: 'Windows', mac: 'macOS', linux: 'Linux' }
const OS_ABBR: Record<string, string> = { windows: 'Win', mac: 'Mac', linux: 'Linux' }

// Storefront sources whose "system" is just PC.
const PC_SOURCES = new Set(['steam', 'gog', 'epic', 'itch'])
function systemLabel(source: string, platform: string): string | null {
  if (platform && platform !== source) return platform  // emulation → console
  if (PC_SOURCES.has(source)) return 'PC'
  return null
}

// Attribute kinds rendered as labeled tag groups, in this order.
const TAG_GROUPS: [string, string][] = [
  ['genres', 'Genres'], ['themes', 'Themes'], ['game_modes', 'Modes'],
  ['player_perspectives', 'Perspective'], ['series', 'Series'],
]

// Tags are styled by ORIGIN so their provenance is visible at a glance (a
// ludodex/user tag looks distinct from a Playnite tag, etc). A tag can carry more
// than one origin; the highest-priority one drives the badge look.
const TAG_ORIGIN_PRIORITY = ['ludodex', 'playnite', 'steam', 'launchbox', 'import']
const TAG_ORIGIN_LABEL: Record<string, string> = {
  ludodex: 'Your tag', playnite: 'Playnite', steam: 'Steam',
  launchbox: 'LaunchBox', import: 'Imported',
}
function tagOrigin(origins: string[]): string {
  for (const o of TAG_ORIGIN_PRIORITY) if (origins.includes(o)) return o
  return origins[0] || 'import'
}
function TagBadge({ t, onRemove }: { t: TagRef; onRemove?: () => void }) {
  const o = tagOrigin(t.origins)
  const label = t.origins.map((x) => TAG_ORIGIN_LABEL[x] ?? x).join(' + ')
  return (
    <span className={'tag tag-' + o} title={label + ' tag'}>
      {t.tag}
      {onRemove && (
        <button className="tag-x" title="Remove tag"
          onClick={(e) => { e.stopPropagation(); onRemove() }}>×</button>
      )}
    </span>
  )
}

// The unified "Tags" section: every game's tags (imported + your own) in one place,
// each badge styled by origin. Only your own (ludodex) tags are editable.
function TagSection({ nk, initial }: { nk: string; initial: TagRef[] }) {
  const [tags, setTags] = useState<TagRef[]>(initial)
  const [val, setVal] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { setTags(initial) }, [initial])

  const add = async () => {
    const t = val.trim()
    if (!t || busy) return
    setBusy(true)
    try { setTags((await api.addTag(nk, t)).tags); setVal('') }
    catch { /* ignore */ } finally { setBusy(false) }
  }
  const remove = async (t: string) => {
    setBusy(true)
    try { setTags((await api.removeTag(nk, t)).tags) }
    catch { /* ignore */ } finally { setBusy(false) }
  }

  return (
    <section className="tag-section">
      <h3>Tags<span className="sec-help">imported tags keep their source; add your own anytime</span></h3>
      <div className="tag-wrap">
        {tags.map((t) => (
          <TagBadge key={t.tag + '|' + t.origins.join(',')} t={t}
            onRemove={t.origins.includes('ludodex') ? () => remove(t.tag) : undefined} />
        ))}
        <span className="tag-add">
          <input value={val} placeholder="add a tag…" disabled={busy}
            onChange={(e) => setVal(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') add() }} />
          {val.trim() && (
            <button className="tag-add-btn" disabled={busy} onClick={add}>Add</button>
          )}
        </span>
      </div>
    </section>
  )
}

// A 0–100 score chip, colour-graded (green/amber/red) by value.
function scoreClass(v: number) { return v >= 80 ? 'hi' : v >= 60 ? 'mid' : 'lo' }
function ScoreBadge({ v }: { v: number }) {
  return <span className={'score-badge ' + scoreClass(v)}>{v}</span>
}

// The "About" block: description, scores, key facts and tag groups — built from
// the raw game_attributes so nothing is shown as a cryptic key/value dump.
function About({ attrs, scores }: { attrs: Record<string, string[]>; scores?: Scores }) {
  const first = (k: string) => attrs[k]?.[0]
  const desc = first('description')
  const released = first('release_date') || first('release_year')
  const dev = attrs['developers']?.join(', ')
  const pub = attrs['publishers']?.join(', ')
  const ludodex = scores?.ludodex ?? null
  const critic = scores?.critic ?? null
  const players = scores?.players ?? null

  const facts: [string, string][] = []
  if (released) facts.push(['Released', released])
  if (dev) facts.push(['Developer', dev])
  if (pub) facts.push(['Publisher', pub])

  const hasTags = TAG_GROUPS.some(([k]) => attrs[k]?.length)
  if (!desc && !facts.length && ludodex == null && !hasTags) return null

  return (
    <section className="about">
      <h3>About</h3>
      {desc && <p className="about-desc">{desc}</p>}
      {ludodex != null && (
        <div className="score-row">
          <div className="score score-main">
            <ScoreBadge v={ludodex} />
            <span>Ludodex score
              <span className="score-wt"> · {Math.round((scores!.critic_weight) * 100)}% critic</span>
            </span>
          </div>
          {critic != null && <div className="score"><b>{critic}</b><span>Critic</span></div>}
          {players != null && <div className="score"><b>{players}</b><span>Players</span></div>}
        </div>
      )}
      {scores?.sources?.length ? (
        <div className="score-sources">
          <div className="ss-head">Rating sources</div>
          {scores.sources.map((s) => (
            <div key={s.source + s.kind} className="score-src">
              <span className={'ss-dot ss-' + s.kind} title={s.kind} />
              <span className="ss-name">{s.name}</span>
              <span className="ss-kind">{s.kind}</span>
              <span className="ss-val">{s.raw ?? s.score}</span>
              {s.votes != null && <span className="ss-votes">{s.votes.toLocaleString()} reviews</span>}
            </div>
          ))}
        </div>
      ) : null}
      {facts.length > 0 && (
        <dl className="facts">
          {facts.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
        </dl>
      )}
      {TAG_GROUPS.map(([k, label]) => attrs[k]?.length ? (
        <div key={k} className="tag-group">
          <span className="tg-label">{label}</span>
          <span className="tg-tags">
            {attrs[k].map((v) => <span key={v} className="tag">{v}</span>)}
          </span>
        </div>
      ) : null)}
    </section>
  )
}

function Detail({ nk, onClose }: { nk: string; onClose: () => void }) {
  const [d, setD] = useState<GameDetail | null>(null)
  const [media, setMedia] = useState<MediaLibrary | null>(null)
  const [kinds, setKinds] = useState<MediaKind[]>([])
  const [tab, setTab] = useState<'attributes' | 'media'>('attributes')

  useEffect(() => { api.detail(nk).then(setD).catch(() => {}) }, [nk])
  useEffect(() => { setMedia(null); api.mediaLibrary(nk).then(setMedia).catch(() => {}) }, [nk])
  useEffect(() => {
    api.mediaKinds().then((r) => {
      setKinds(r.kinds)
      r.kinds.forEach((k, i) => { KIND_ORDER[k.kind] = i })
    }).catch(() => {})
  }, [])

  const assets = media?.assets ?? []
  const pickKind = (kind: string) => {
    const of = assets.filter((a) => a.kind === kind && a.is_image)
    return of.find((a) => a.pinned) ?? of[0] ?? null
  }
  const bg = pickKind('hero') ?? pickKind('background') ?? pickKind('header')
  const logo = pickKind('logo')

  return (
    <div className="overlay" onClick={onClose}>
      <div className="panel game-panel" onClick={(e) => e.stopPropagation()}>
        <button className="close" onClick={onClose}>×</button>
        {!d ? <div className="loading">Loading…</div> : (
          <>
            <div className={'hero' + (bg ? '' : ' hero-plain')}
                 style={bg ? undefined : { ['--h' as string]: hueOf(d.title) } as CSSProperties}>
              {bg && <img className="hero-bg" src={bg.url} alt="" />}
              <div className="hero-shade" />
              <div className="hero-fg">
                {logo
                  ? <img className="hero-logo" src={logo.url} alt={d.title} />
                  : <h2 className="hero-title">{d.title}</h2>}
                <div className="hero-sub">{d.title}</div>
              </div>
            </div>

            <ArtStrip nk={nk} assets={assets} loading={!media} kinds={kinds}
                      onChange={setMedia} />

            <ParticleTabs className="panel-tabs2" active={tab}
              onSelect={(id) => setTab(id as 'attributes' | 'media')}
              tabs={[{ id: 'attributes', label: 'Attributes' }, { id: 'media', label: 'All Media' }]} />

            {tab === 'attributes' ? (
              <div className="panel-body">
                <Achievements nk={d.norm_key} />

                <About attrs={d.attributes} scores={d.scores} />

                <TagSection nk={d.norm_key} initial={d.tags} />

                <section>
                  <h3>In your library
                    <span className="sec-help">where you own this game — one row per file or store entry</span>
                  </h3>
                  <table className="sources-table">
                    <thead>
                      <tr>
                        <th>Source</th>
                        <th>System</th>
                        <th>OS</th>
                        <th>Listed as</th>
                        <th>Detail</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.sources.map((s, i) => (
                        <tr key={i}>
                          <td className="badge">{s.source}</td>
                          <td>{systemLabel(s.source, s.platform)
                            ?? <span className="dim">—</span>}</td>
                          <td>{s.os && s.os.length
                            ? <span className="os-cell">{s.os.map((o) =>
                                <span key={o} className="os-badge" title={OS_NAME[o] ?? o}>
                                  {OS_ABBR[o] ?? o}</span>)}</span>
                            : <span className="dim">—</span>}</td>
                          <td>{s.title_raw}</td>
                          <td className="dim">{s.detail || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </section>

                {d.metadata_links.length > 0 && (
                  <section className="idvia">
                    <h3>Identified via
                      <span className="sec-help">metadata providers this game was matched against</span>
                    </h3>
                    <div className="idvia-chips">
                      {d.metadata_links.map((l, i) => (
                        l.url
                          ? <a key={i} className="idchip" href={l.url}
                               target="_blank" rel="noreferrer">
                              {l.provider}{l.provider_id ? ` #${l.provider_id}` : ''}
                            </a>
                          : <span key={i} className="idchip">
                              {l.provider}{l.provider_id ? ` #${l.provider_id}` : ''}
                            </span>
                      ))}
                    </div>
                  </section>
                )}
              </div>
            ) : (
              <div className="panel-body">
                <AllMedia nk={d.norm_key} kinds={kinds} assets={assets} onChange={setMedia} />
                <ArtPicker nk={d.norm_key} />
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

// Horizontally-scrollable strip of every art asset the game actually has, with
// pin controls. Pinned assets are what gets exported; scalar kinds keep one,
// others up to the cap, in the order you arrange them.
// Full-screen media viewer: enlarged image with prev/next (buttons, arrow keys,
// mouse wheel). `items` are the navigable images; `index` is the current one.
function Lightbox({ items, index, onClose, onIndex }: {
  items: MediaAsset[]; index: number; onClose: () => void; onIndex: (i: number) => void
}) {
  const n = items.length
  const go = useCallback((dir: number) => {
    if (n) onIndex((index + dir + n) % n)
  }, [index, n, onIndex])
  const lastWheel = useRef(0)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      else if (e.key === 'ArrowRight' || e.key === 'ArrowDown') go(1)
      else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') go(-1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [go, onClose])

  const a = items[index]
  if (!a) return null
  return (
    <div className="lightbox" onClick={onClose}
      onWheel={(e) => {
        if (e.timeStamp - lastWheel.current < 110) return   // throttle wheel steps
        lastWheel.current = e.timeStamp
        go(e.deltaY > 0 ? 1 : -1)
      }}>
      <button className="lb-close" title="Close (Esc)" onClick={onClose}>×</button>
      {n > 1 && <button className="lb-nav lb-prev" title="Previous (←)"
        onClick={(e) => { e.stopPropagation(); go(-1) }}>‹</button>}
      <figure className="lb-figure" onClick={(e) => e.stopPropagation()}>
        <img className="lb-img" src={a.url} alt={a.kind} />
        <figcaption className="lb-cap">
          <span className="lb-kind">{a.kind.replace(/_/g, ' ')}</span>
          <span className="lb-prov">{a.provider}{a.width ? ` · ${a.width}×${a.height}` : ''}</span>
          <span className="lb-count">{index + 1} / {n}</span>
        </figcaption>
      </figure>
      {n > 1 && <button className="lb-nav lb-next" title="Next (→)"
        onClick={(e) => { e.stopPropagation(); go(1) }}>›</button>}
    </div>
  )
}

function ArtStrip({ nk, assets, loading, kinds, onChange }: {
  nk: string; assets: MediaAsset[]; loading: boolean; kinds: MediaKind[]
  onChange: (m: MediaLibrary) => void
}) {
  const [viewIdx, setViewIdx] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const desc = (k: string) => kinds.find((x) => x.kind === k)?.description ?? k
  const scalar = (k: string) => kinds.find((x) => x.kind === k)?.scalar ?? true

  const pinnedIds = (kind: string) =>
    assets.filter((a) => a.kind === kind && a.pinned)
      .sort((a, b) => (a.rank ?? 0) - (b.rank ?? 0)).map((a) => a.id)

  const commit = async (kind: string, ids: number[]) => {
    setBusy(true)
    try { onChange(await api.setPins(nk, kind, ids)) }
    catch { /* ignore */ } finally { setBusy(false) }
  }
  const toggle = (a: MediaAsset) => {
    const cur = pinnedIds(a.kind)
    commit(a.kind, a.pinned ? cur.filter((i) => i !== a.id) : [...cur, a.id])
  }
  const move = (a: MediaAsset, dir: -1 | 1) => {
    const cur = pinnedIds(a.kind)
    const i = cur.indexOf(a.id), j = i + dir
    if (i < 0 || j < 0 || j >= cur.length) return
    ;[cur[i], cur[j]] = [cur[j], cur[i]]
    commit(a.kind, cur)
  }

  const ordered = [...assets].sort((a, b) => {
    const kr = kindRank(a.kind) - kindRank(b.kind)
    if (kr) return kr
    if (a.pinned !== b.pinned) return a.pinned ? -1 : 1
    return (a.rank ?? 99) - (b.rank ?? 99)
  })
  const viewable = ordered.filter((a) => a.is_image)     // navigable in the lightbox

  if (loading) return <div className="art-strip loading-sm">Loading artwork…</div>
  if (!ordered.length)
    return <div className="art-strip art-empty">No artwork indexed for this game yet.</div>

  return (
    <div className={'art-strip' + (busy ? ' busy' : '')}>
      {ordered.map((a) => (
        <div key={a.id} className={'art-tile' + (a.pinned ? ' pinned' : '')}
             title={desc(a.kind)}>
          <div className="art-thumb">
            {a.is_image
              ? <img loading="lazy" className="art-open" src={a.thumb ?? a.url} alt={a.kind}
                  onClick={() => setViewIdx(viewable.indexOf(a))} title="Click to enlarge" />
              : <a className="art-ext" href={a.url} target="_blank" rel="noreferrer"
                  title="Open file">{(a.ext ?? '?').toUpperCase()}</a>}
            <button className="art-pin" onClick={() => toggle(a)}
              title={a.pinned ? 'Unpin (exclude from export)' : 'Pin (include in export)'}>
              {a.pinned
                ? (scalar(a.kind) ? '★' : a.rank)
                : '☆'}
            </button>
            {a.pinned && !scalar(a.kind) && (
              <div className="art-order">
                <button onClick={() => move(a, -1)} title="Move earlier">‹</button>
                <button onClick={() => move(a, 1)} title="Move later">›</button>
              </div>
            )}
          </div>
          <div className="art-kind">{a.kind.replace(/_/g, ' ')}</div>
          <div className="art-prov">{a.provider}</div>
        </div>
      ))}
      {viewIdx !== null && viewable[viewIdx] && (
        <Lightbox items={viewable} index={viewIdx}
          onClose={() => setViewIdx(null)} onIndex={setViewIdx} />
      )}
    </div>
  )
}

// The full media classification vocabulary — every kind, present or not, with a
// tooltip explaining what it is and why it exists.
function AllMedia({ nk, kinds, assets, onChange }: {
  nk: string; kinds: MediaKind[]; assets: MediaAsset[]
  onChange: (m: MediaLibrary) => void
}) {
  const byKind: Record<string, MediaAsset[]> = {}
  assets.forEach((a) => { (byKind[a.kind] ??= []).push(a) })
  return (
    <section className="all-media">
      <h3>All Media <span className="am-note">every classification — add your own from a URL or your device</span></h3>
      <div className="am-grid">
        {kinds.map((k) => (
          <MediaKindCard key={k.kind} nk={nk} kind={k}
            assets={byKind[k.kind] ?? []} onChange={onChange} />
        ))}
      </div>
    </section>
  )
}

// One media-kind card with an upload affordance: paste a direct URL (server
// downloads it) or upload a file from the device. User uploads show as removable
// thumbnails and take precedence as the game's art for that kind.
function MediaKindCard({ nk, kind, assets, onChange }: {
  nk: string; kind: MediaKind; assets: MediaAsset[]
  onChange: (m: MediaLibrary) => void
}) {
  const [open, setOpen] = useState(false)
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)
  const userAssets = assets.filter((a) => a.user)
  const n = assets.length

  const run = async (fn: () => Promise<MediaLibrary>, close = true) => {
    setBusy(true); setErr('')
    try { onChange(await fn()); setUrl(''); if (close) setOpen(false) }
    catch (e) { setErr((e as Error).message) }
    finally { setBusy(false) }
  }
  const onFile = (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (f) run(() => api.uploadMedia(nk, kind.kind, f))
    e.target.value = ''
  }

  return (
    <div className={'am-item' + (n ? ' has' : '')}>
      <div className="am-head">
        <span className="am-name" title={kind.description}>{kind.kind.replace(/_/g, ' ')}</span>
        <span className="am-actions">
          <span className="am-badge">{n ? `×${n}` : '—'}</span>
          <button className={'am-up' + (open ? ' on' : '')} title="Add media"
            onClick={() => setOpen((v) => !v)}>+</button>
        </span>
      </div>
      <div className="am-desc">{kind.description}</div>
      <div className="am-meta">{kind.scalar ? 'single' : `up to ${kind.cap}`}</div>

      {userAssets.length > 0 && (
        <div className="am-thumbs">
          {userAssets.map((a) => (
            <span key={a.id} className="am-thumb">
              {a.is_image
                ? <img src={a.thumb || a.url} alt="" />
                : <span className="am-file">{(a.ext || 'file').toUpperCase()}</span>}
              <button className="am-del" title="Remove upload" disabled={busy}
                onClick={() => run(() => api.deleteUserMedia(nk, a.id), false)}>×</button>
            </span>
          ))}
        </div>
      )}

      {open && (
        <div className="am-uploader">
          <div className="am-up-row">
            <input className="am-url" placeholder="paste a direct media URL…" value={url}
              disabled={busy} onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && url.trim()) run(() => api.addMediaFromUrl(nk, kind.kind, url.trim())) }} />
            <button className="am-btn" disabled={busy || !url.trim()}
              onClick={() => run(() => api.addMediaFromUrl(nk, kind.kind, url.trim()))}>Download</button>
          </div>
          <div className="am-or">or</div>
          <button className="am-btn file" disabled={busy}
            onClick={() => fileRef.current?.click()}>Upload from device</button>
          <input ref={fileRef} type="file" hidden onChange={onFile}
            accept="image/*,video/mp4,video/webm,application/pdf" />
          {busy && <div className="am-busy">Working…</div>}
          {err && <div className="am-err">{err}</div>}
        </div>
      )}
    </div>
  )
}

// Rotating "Spotlight": a themed top-N (overall / per-platform / per-store /
// per-decade / underrated / hidden gems…) that auto-shuffles, or shuffle by hand.
// A thin countdown bar depletes right→left over the (configurable) dwell time and
// drives the rotation: when it finishes it loads the next theme. Hovering pauses
// the bar — and therefore the rotation — so you can read/click without it moving.
function SpotlightSection({ onOpen, prefsTick }: {
  onOpen: (nk: string) => void; prefsTick: number
}) {
  const [sp, setSp] = useState<SpotlightData | null>(null)
  const [loading, setLoading] = useState(false)
  const [seconds, setSeconds] = useState(12)
  const [cycle, setCycle] = useState(0)   // remounts the timer bar → restarts it
  const [paused, setPaused] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try { setSp(await api.spotlight('random')) } catch { /* offline */ }
    finally { setLoading(false); setCycle((c) => c + 1) }
  }, [])
  useEffect(() => { load() }, [load])
  // Dwell time comes from prefs; re-read when the setting changes (prefsTick).
  useEffect(() => {
    api.prefs().then((p) => setSeconds(p.spotlight_seconds)).catch(() => {})
  }, [prefsTick])

  if (!sp || !sp.items.length) return null
  return (
    <section className="spotlight"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}>
      <div className="sl-head">
        <div className="sl-title">Spotlight
          <span className="sl-theme">{sp.title}</span>
          <span className="sl-sub">{sp.subtitle}</span>
        </div>
        <button className={'sl-shuffle' + (loading ? ' spin' : '')} title="Shuffle spotlight"
          onClick={load} disabled={loading}>⟳</button>
      </div>
      <div className="sl-timer-track">
        <div key={cycle} className="sl-timer"
          style={{ animationDuration: seconds + 's', animationPlayState: paused ? 'paused' : 'running' }}
          onAnimationEnd={() => { if (!paused && !loading) load() }} />
      </div>
      <div className={'sl-row' + (loading ? ' fading' : '')}>
        {sp.items.map((g, i) => (
          <button key={g.norm_key} className="sl-card" onClick={() => onOpen(g.norm_key)}
            title={g.title}>
            <span className="sl-rank">{i + 1}</span>
            <div className="sl-art"><Cover g={g} compact /></div>
            {g.score != null && <span className={'sl-score ' + scoreClass(g.score)}>{g.score}</span>}
            <div className="sl-name">{g.title}</div>
          </button>
        ))}
      </div>
    </section>
  )
}

function Dashboard({ stats, onBrowse, onFilter, onOpen, prefsTick }: {
  stats: Stats | null; onBrowse: () => void; onFilter: (f: FilterState) => void
  onOpen: (nk: string) => void; prefsTick: number
}) {
  if (!stats) return <div className="loading">Loading…</div>
  const artPct = stats.games ? Math.round((stats.media.games_with_art / stats.games) * 100) : 0
  const pct = (n: number) => stats.games ? Math.round((n / stats.games) * 100) : 0
  const sources = Object.entries(stats.by_source).sort((a, b) => b[1] - a[1])
  const kinds = Object.entries(stats.media.by_kind).sort((a, b) => b[1] - a[1])
  return (
    <div className="dashboard">
      <SpotlightSection onOpen={onOpen} prefsTick={prefsTick} />
      <div className="dash-cards">
        <div className="dash-card">
          <div className="dc-num">{stats.games.toLocaleString()}</div>
          <div className="dc-label">Games</div>
        </div>
        <div className="dash-card">
          <div className="dc-num">{stats.media.games_with_art.toLocaleString()}</div>
          <div className="dc-label">With artwork · {artPct}%</div>
        </div>
        <div className="dash-card">
          <div className="dc-num">{stats.cross_source.toLocaleString()}</div>
          <div className="dc-label">Cross-source</div>
        </div>
        <div className="dash-card">
          <div className="dc-num">{sources.length}</div>
          <div className="dc-label">Sources</div>
        </div>
      </div>

      <div className="dash-section-label">Needs attention</div>
      <div className="dash-cards attn-cards">
        <button className={'dash-card attn' + (stats.unmatched ? ' warn' : ' good')}
          onClick={() => onFilter({ matched: 'exclude' })}
          title="Files that didn't match any known game — click to view in Library">
          <div className="dc-num">{stats.unmatched.toLocaleString()}</div>
          <div className="dc-label">Unmatched · {pct(stats.unmatched)}% <span className="dc-go">view →</span></div>
        </button>
        <button className={'dash-card attn' + (stats.no_media ? ' warn' : ' good')}
          onClick={() => onFilter({ has_media: 'exclude' })}
          title="Games with no media/artwork at all — click to view in Library">
          <div className="dc-num">{stats.no_media.toLocaleString()}</div>
          <div className="dc-label">No media · {pct(stats.no_media)}% <span className="dc-go">view →</span></div>
        </button>
      </div>

      <div className="dash-cols">
        <section className="dash-panel">
          <h3>By source</h3>
          {sources.map(([name, n]) => (
            <div key={name} className="dash-bar-row">
              <span className="dbr-name">{name}</span>
              <span className="dbr-track">
                <span style={{ width: (stats.games ? (n / stats.games) * 100 : 0) + '%' }} />
              </span>
              <span className="dbr-val">{n.toLocaleString()}</span>
            </div>
          ))}
        </section>

        <section className="dash-panel">
          <h3>Media by kind</h3>
          {kinds.length === 0 && <div className="dim">No media indexed yet.</div>}
          {kinds.map(([name, n]) => (
            <div key={name} className="dash-bar-row">
              <span className="dbr-name">{name}</span>
              <span className="dbr-track">
                <span style={{ width: (kinds[0][1] ? (n / kinds[0][1]) * 100 : 0) + '%' }} />
              </span>
              <span className="dbr-val">{n.toLocaleString()}</span>
            </div>
          ))}
        </section>
      </div>

      <button className="more" onClick={onBrowse}>Browse the library →</button>
    </div>
  )
}

function Achievements({ nk }: { nk: string }) {
  const [a, setA] = useState<AchData | null>(null)
  const [loaded, setLoaded] = useState(false)
  useEffect(() => {
    setLoaded(false)
    api.achievements(nk).then(setA).catch(() => setA(null)).finally(() => setLoaded(true))
  }, [nk])

  if (!loaded) return null
  if (!a || !a.matched || a.num_ach === 0) return null

  const pct = a.num_ach ? Math.round((a.num_earned / a.num_ach) * 100) : 0
  return (
    <section className="achievements">
      <h3>
        Achievements
        <span className="ach-count">{a.num_earned}/{a.num_ach} · {pct}%</span>
      </h3>
      <div className="ach-bar"><span style={{ width: pct + '%' }} /></div>
      <div className="ach-grid">
        {a.achievements.map((ac) => (
          <div key={ac.id} className={'ach' + (ac.earned ? ' earned' : '')}
               title={ac.description}>
            {ac.badge && <img loading="lazy" src={ac.badge} alt="" />}
            <div className="ach-meta">
              <div className="ach-title">{ac.title}</div>
              <div className="ach-desc">{ac.description}</div>
            </div>
            <div className="ach-pts">{ac.points}</div>
          </div>
        ))}
      </div>
    </section>
  )
}

// ============================ File-operations engine UI ============================

function relTime(epoch: number | null): string {
  if (!epoch) return ''
  const s = Math.floor(Date.now() / 1000 - epoch)
  if (s < 60) return 'just now'
  if (s < 3600) return Math.floor(s / 60) + 'm ago'
  if (s < 86400) return Math.floor(s / 3600) + 'h ago'
  return new Date(epoch * 1000).toLocaleDateString()
}

function StatusBadge({ status }: { status: string }) {
  return <span className={'run-badge s-' + status}>{status || '—'}</span>
}

function ProgressBar({ done, total, failed }: { done: number; total: number; failed: number }) {
  const pct = total ? Math.round((done / total) * 100) : 0
  const fpct = total ? Math.round((failed / total) * 100) : 0
  return (
    <div className="run-progress"
      title={`${done}/${total} done${failed ? `, ${failed} failed` : ''}`}>
      <span className="rp-done" style={{ width: pct + '%' }} />
      {failed > 0 && <span className="rp-fail" style={{ width: fpct + '%' }} />}
    </div>
  )
}

function useDevices() {
  const [devices, setDevices] = useState<Device[]>([])
  useEffect(() => { api.devices().then((d) => setDevices(d.devices)).catch(() => {}) }, [])
  return devices
}

function PlanPreview({ plan }: { plan: FilePlan }) {
  const s = plan.summary
  return (
    <div className="fo-panel">
      <div className="fo-chips">
        <span className="fo-stat">{s.moves.toLocaleString()} moves</span>
        {s.renames > 0 && <span className="fo-stat">{s.renames} renames</span>}
        {s.m3u > 0 && <span className="fo-stat">{s.m3u} .m3u</span>}
        {s.prune > 0 && <span className="fo-stat">{s.prune} prune</span>}
        {s.skipped > 0 && <span className="fo-stat dim">{s.skipped.toLocaleString()} skipped</span>}
      </div>
      {plan.warnings.map((w, i) => <div key={i} className="fo-warn">⚠ {w}</div>)}
      {plan.sample.length > 0 && (
        <div className="fo-sample">
          {plan.sample.slice(0, 12).map((m, i) => (
            <div key={i} className="fo-move">
              <span className="fo-from">{m.src}</span>
              <span className="fo-arrow">→</span>
              <span className="fo-to">{m.dst}</span>
            </div>
          ))}
          {s.moves > 12 && <div className="dim">…and {(s.moves - 12).toLocaleString()} more</div>}
        </div>
      )}
      {s.moves === 0 && <div className="sync-note dim">Nothing to change — already in this layout.</div>}
    </div>
  )
}

function FileOpsOperations() {
  const devices = useDevices()
  const [deviceId, setDeviceId] = useState(0)
  const [root, setRoot] = useState('')
  const [scope, setScope] = useState('multi_system')
  const [system, setSystem] = useState('')
  const [profiles, setProfiles] = useState<FileProfile[]>([])
  const [profileId, setProfileId] = useState('builtin:flat')
  const [detected, setDetected] = useState<FileDetect | null>(null)
  const [plan, setPlan] = useState<FilePlan | null>(null)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [openRun, setOpenRun] = useState<number | null>(null)
  const [cmd, setCmd] = useState('')
  const [cmdRes, setCmdRes] = useState<FileCommandResult | null>(null)
  const [infer, setInfer] = useState<FileProfile | null>(null)

  const reloadProfiles = () => api.fileProfiles().then((p) => setProfiles(p.profiles)).catch(() => {})
  useEffect(() => { reloadProfiles() }, [])

  const body = () => ({ device_id: deviceId, root: root.trim(), scope, system: system.trim() || undefined })
  const canRun = root.trim().length > 0

  const scan = async () => {
    setErr(''); setBusy('scan'); setDetected(null); setPlan(null)
    try { setDetected(await api.fileDetect(body())) }
    catch (e) { setErr((e as Error).message) } finally { setBusy('') }
  }
  const preview = async () => {
    setErr(''); setBusy('plan'); setPlan(null)
    try { setPlan(await api.filePlan({ ...body(), profile: profileId })) }
    catch (e) { setErr((e as Error).message) } finally { setBusy('') }
  }
  const makeRunbook = async (profile: string | FileProfile) => {
    setErr(''); setBusy('runbook')
    try { const r = await api.createRunbook({ ...body(), profile }); setOpenRun(r.run_id) }
    catch (e) { setErr((e as Error).message) } finally { setBusy('') }
  }
  const askAi = async () => {
    if (!cmd.trim()) return
    setErr(''); setBusy('cmd'); setCmdRes(null)
    try { setCmdRes(await api.fileCommand({ ...body(), text: cmd.trim() })) }
    catch (e) { setErr((e as Error).message) } finally { setBusy('') }
  }
  const designProfile = async () => {
    setErr(''); setBusy('infer'); setInfer(null)
    try { setInfer((await api.fileInfer(body())).profile) }
    catch (e) { setErr((e as Error).message) } finally { setBusy('') }
  }
  const saveInferred = async () => {
    if (!infer) return
    try { await api.saveFileProfile(infer); setInfer(null); reloadProfiles() }
    catch (e) { setErr((e as Error).message) }
  }

  const sel = profiles.find((p) => p.id === profileId)
  const builtins = profiles.filter((p) => p.builtin)
  const customs = profiles.filter((p) => !p.builtin)

  return (
    <>
      <h2>File operations</h2>
      <p className="dim">Reorganize, rename and repair ROM sets on any device — safely.
        Pick a location and a layout profile, preview the plan, then run it as a
        reversible <b>runbook</b>. Only recognized game files are touched.</p>

      <div className="fo-form">
        <label className="fo-field">
          <span>Device</span>
          <select value={deviceId} onChange={(e) => setDeviceId(Number(e.target.value))}>
            <option value={0}>This server (local)</option>
            {devices.map((d) => <option key={d.id} value={d.id}>{d.name}{d.host ? ` (${d.host})` : ''}</option>)}
          </select>
        </label>
        <label className="fo-field fo-grow">
          <span>Path</span>
          <input value={root} placeholder="/path/to/roms" onChange={(e) => setRoot(e.target.value)} />
        </label>
        <label className="fo-field">
          <span>Layout of this path</span>
          <select value={scope} onChange={(e) => setScope(e.target.value)}>
            <option value="multi_system">Holds many system folders</option>
            <option value="single_system">Is a single system</option>
          </select>
        </label>
        {scope === 'single_system' && (
          <label className="fo-field">
            <span>System name</span>
            <input value={system} placeholder="snes" onChange={(e) => setSystem(e.target.value)} />
          </label>
        )}
      </div>

      <div className="fo-form">
        <label className="fo-field fo-grow">
          <span>Target profile</span>
          <select value={profileId} onChange={(e) => setProfileId(e.target.value)}>
            <optgroup label="Built-in">
              {builtins.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </optgroup>
            {customs.length > 0 && (
              <optgroup label="Custom">
                {customs.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </optgroup>
            )}
          </select>
        </label>
      </div>
      {sel && <div className="fo-profile-hint"><code>{sel.target}</code><span className="dim"> — {sel.description}</span></div>}

      <div className="fo-actions">
        <button className="ops-btn" disabled={!canRun || busy !== ''} onClick={scan}>{busy === 'scan' ? 'Scanning…' : 'Scan'}</button>
        <button className="ops-btn" disabled={!canRun || busy !== ''} onClick={preview}>{busy === 'plan' ? 'Planning…' : 'Preview plan'}</button>
        <button className="go" disabled={!canRun || busy !== ''} onClick={() => makeRunbook(profileId)}>{busy === 'runbook' ? 'Building…' : 'Create runbook →'}</button>
      </div>
      {err && <div className="connect-msg err">{err}</div>}

      {detected && (
        <div className="fo-panel">
          <div className="fo-panel-head">Current layout: <b>{detected.current === 'folder' ? 'folder-per-game' : 'flat'}</b> · {detected.counts.files.toLocaleString()} files · {detected.systems.length} systems</div>
          <div className="fo-systems">{detected.systems.slice(0, 40).map((s) => <span key={s} className="fo-chip">{s}</span>)}</div>
          <div className="fo-exts dim">Top types: {detected.counts.top_exts.slice(0, 10).map(([e, n]) => `${e} (${n})`).join(', ')}</div>
        </div>
      )}

      {plan && <PlanPreview plan={plan} />}

      <div className="fo-ai">
        <h3>✨ AI assist</h3>
        <div className="fo-ai-row">
          <input value={cmd} placeholder="Describe what to do… e.g. put each game in its own folder and build m3u playlists"
            onChange={(e) => setCmd(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') askAi() }} />
          <button className="ops-btn" disabled={!canRun || !cmd.trim() || busy !== ''} onClick={askAi}>{busy === 'cmd' ? 'Thinking…' : 'Ask AI'}</button>
          <button className="ops-btn" disabled={!canRun || busy !== ''} onClick={designProfile}>{busy === 'infer' ? 'Analyzing…' : 'Design a profile'}</button>
        </div>
        {cmdRes && (
          <div className="fo-panel">
            <div className="fo-ai-expl">{cmdRes.explanation}</div>
            <div className="fo-profile-hint"><code>{cmdRes.profile.target}</code></div>
            <PlanPreview plan={{ summary: cmdRes.summary, warnings: cmdRes.warnings, sample: cmdRes.sample }} />
            <button className="go" disabled={busy !== ''} onClick={() => makeRunbook(cmdRes.profile)}>Create runbook from this →</button>
          </div>
        )}
        {infer && (
          <div className="fo-panel">
            <div className="fo-panel-head">Proposed profile: <b>{infer.name}</b></div>
            <div className="dim">{infer.description}</div>
            <div className="fo-profile-hint"><code>{infer.target}</code></div>
            <div className="fo-actions">
              <button className="ops-btn" onClick={saveInferred}>Save profile</button>
              <button className="go" disabled={busy !== ''} onClick={() => makeRunbook(infer)}>Use once →</button>
            </div>
          </div>
        )}
      </div>

      {openRun != null && <RunbookModal runId={openRun} onClose={() => setOpenRun(null)} />}
    </>
  )
}

function FileProfiles() {
  const [profiles, setProfiles] = useState<FileProfile[]>([])
  const [vars, setVars] = useState<FileVariable[]>([])
  const [editing, setEditing] = useState<FileProfile | null>(null)
  const reload = () => api.fileProfiles().then((p) => setProfiles(p.profiles)).catch(() => {})
  useEffect(() => { reload(); api.fileVariables().then((v) => setVars(v.variables)).catch(() => {}) }, [])

  const blank: FileProfile = { name: '', description: '', target: '{system}/{filename}', m3u: false, prune_empty: true, rename: false, all_files: false, archive_policy: 'keep' }
  const clone = (p: FileProfile) => setEditing({ ...p, id: undefined, name: p.name + ' (copy)', builtin: false })
  const del = async (pid: string) => { if (confirm('Delete this profile?')) { await api.deleteFileProfile(pid); reload() } }

  const builtins = profiles.filter((p) => p.builtin)
  const customs = profiles.filter((p) => !p.builtin)
  return (
    <>
      <h2>Layout profiles</h2>
      <p className="dim">A profile describes a target on-disk layout as a path template
        over the variable bubbles. Built-ins are read-only — clone one to tweak it, or
        build your own.</p>
      <button className="go" onClick={() => setEditing({ ...blank })}>+ New profile</button>

      <h3>Built-in</h3>
      {builtins.map((p) => <ProfileRow key={p.id} p={p} onClone={() => clone(p)} />)}
      {customs.length > 0 && (
        <>
          <h3>Custom</h3>
          {customs.map((p) => <ProfileRow key={p.id} p={p} onEdit={() => setEditing(p)} onClone={() => clone(p)} onDelete={() => del(p.id!)} />)}
        </>
      )}

      {editing && <ProfileEditor profile={editing} vars={vars} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reload() }} />}
    </>
  )
}

function ProfileRow({ p, onEdit, onClone, onDelete }: { p: FileProfile; onEdit?: () => void; onClone: () => void; onDelete?: () => void }) {
  return (
    <div className="fo-prow">
      <div className="fo-prow-main">
        <span className="fo-pname">{p.name}</span>
        <code className="dm-path">{p.target}</code>
        <span className="dim fo-pdesc">{p.description}</span>
      </div>
      <div className="fo-prow-tags">
        {p.m3u && <span className="fo-tag">m3u</span>}
        {p.rename && <span className="fo-tag">rename</span>}
        {p.all_files && <span className="fo-tag warn">all files</span>}
      </div>
      <div className="dev-actions">
        {onEdit && <button className="ops-btn" onClick={onEdit}>Edit</button>}
        <button className="ops-btn" onClick={onClone}>Clone</button>
        {onDelete && <button className="emu-rm" title="Delete" onClick={onDelete}>×</button>}
      </div>
    </div>
  )
}

function ProfileEditor({ profile, vars, onClose, onSaved }: { profile: FileProfile; vars: FileVariable[]; onClose: () => void; onSaved: () => void }) {
  const [p, setP] = useState<FileProfile>(profile)
  const [err, setErr] = useState('')
  const [saving, setSaving] = useState(false)
  const taRef = useRef<HTMLInputElement>(null)
  const wrapRef = useClickOutside<HTMLDivElement>(true, onClose)

  const insert = (token: string) => {
    const ins = `{${token}}`
    const el = taRef.current
    if (el && el.selectionStart != null) {
      const s = el.selectionStart, e = el.selectionEnd ?? s
      const next = p.target.slice(0, s) + ins + p.target.slice(e)
      setP({ ...p, target: next })
      requestAnimationFrame(() => { el.focus(); el.setSelectionRange(s + ins.length, s + ins.length) })
    } else setP({ ...p, target: p.target + ins })
  }
  const onDrop = (e: DragEvent<HTMLInputElement>) => {
    e.preventDefault()
    const t = e.dataTransfer.getData('text/token')
    if (t) insert(t)
  }
  const example = () => {
    let s = p.target
    vars.forEach((v) => { s = s.split(`{${v.token}}`).join(v.example) })
    return s
  }
  const save = async () => {
    setSaving(true); setErr('')
    try { await api.saveFileProfile(p); onSaved() }
    catch (e) { setErr((e as Error).message) } finally { setSaving(false) }
  }
  return (
    <div className="overlay overlay-2" onClick={onClose}>
      <div className="fo-editor" ref={wrapRef} onClick={(e) => e.stopPropagation()}>
        <button className="close" onClick={onClose}>×</button>
        <h2>{p.id ? 'Edit profile' : 'New profile'}</h2>
        <label className="fo-field"><span>Name</span>
          <input value={p.name} onChange={(e) => setP({ ...p, name: e.target.value })} /></label>
        <label className="fo-field"><span>Description</span>
          <input value={p.description} onChange={(e) => setP({ ...p, description: e.target.value })} /></label>

        <div className="fo-bubbles-label">Variable bubbles — click or drag into the template:</div>
        <div className="fo-bubbles">
          {vars.map((v) => (
            <span key={v.token} className="fo-bubble" draggable title={`${v.description} (e.g. ${v.example})`}
              onDragStart={(e: DragEvent<HTMLSpanElement>) => e.dataTransfer.setData('text/token', v.token)}
              onClick={() => insert(v.token)}>{v.label}</span>
          ))}
        </div>
        <label className="fo-field"><span>Target template</span>
          <input ref={taRef} className="fo-template" value={p.target}
            onDrop={onDrop} onDragOver={(e) => e.preventDefault()}
            onChange={(e) => setP({ ...p, target: e.target.value })} /></label>
        <div className="fo-preview">Example → <code>{example()}</code></div>

        <div className="fo-toggles">
          <label><input type="checkbox" checked={p.m3u} onChange={(e) => setP({ ...p, m3u: e.target.checked })} /> Build .m3u playlists for multi-disc games</label>
          <label><input type="checkbox" checked={p.rename} onChange={(e) => setP({ ...p, rename: e.target.checked })} /> Rename single-file games to match the template</label>
          <label><input type="checkbox" checked={p.prune_empty} onChange={(e) => setP({ ...p, prune_empty: e.target.checked })} /> Remove folders left empty</label>
          <label className="fo-danger"><input type="checkbox" checked={p.all_files} onChange={(e) => setP({ ...p, all_files: e.target.checked })} /> ⚠ Include non-game files (media/saves) — advanced</label>
        </div>
        {err && <div className="connect-msg err">{err}</div>}
        <div className="fo-actions">
          <button className="go" disabled={saving || !p.name.trim() || !p.target.trim()} onClick={save}>{saving ? 'Saving…' : 'Save profile'}</button>
          <button className="ops-btn" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  )
}

function FileHistory() {
  const [runs, setRuns] = useState<RunHistoryRow[]>([])
  const [openRun, setOpenRun] = useState<number | null>(null)
  const reload = () => api.fileHistory().then((r) => setRuns(r.runs)).catch(() => {})
  useEffect(() => { reload() }, [])
  return (
    <>
      <h2>Runbook history</h2>
      <p className="dim">Every file-operation run is kept as a reversible runbook. Click one to view the playbook or revert it.</p>
      {runs.length === 0 && <div className="sync-note dim">No runs yet.</div>}
      {runs.map((r) => (
        <div key={r.id} className="fo-hrow" onClick={() => setOpenRun(r.id)}>
          <StatusBadge status={r.status} />
          <span className="fo-hprofile">{r.profile}</span>
          <code className="dm-path">{r.root}</code>
          <span className="dim">{r.done}/{r.n_ops}{r.failed ? ` · ${r.failed} failed` : ''}</span>
          <span className="dim fo-hwhen">{relTime(r.finished || r.started || r.created)}</span>
        </div>
      ))}
      {openRun != null && <RunbookModal runId={openRun} onClose={() => { setOpenRun(null); reload() }} />}
    </>
  )
}

function RunbookModal({ runId, onClose }: { runId: number; onClose: () => void }) {
  const [rb, setRb] = useState<Runbook | null>(null)
  const [ts, setTs] = useState<Troubleshoot | null>(null)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const wrapRef = useClickOutside<HTMLDivElement>(true, onClose)

  const load = useCallback(() => api.getRunbook(runId).then(setRb).catch(() => {}), [runId])
  useEffect(() => { load() }, [load])
  const status = rb?.run.status
  const live = !!rb?.running || status === 'running'
  useEffect(() => {
    if (!live) return
    const t = setInterval(load, 1500)
    return () => clearInterval(t)
  }, [live, load])

  const execute = async () => { setBusy('exec'); setErr(''); try { await api.executeRunbook(runId); setTs(null); setTimeout(load, 400) } catch (e) { setErr((e as Error).message) } finally { setBusy('') } }
  const pause = async () => { setBusy('pause'); try { await api.pauseJob('run:' + runId); setTimeout(load, 400) } catch (e) { setErr((e as Error).message) } finally { setBusy('') } }
  const undo = async () => {
    if (!confirm('Revert this runbook? Every successful step is undone — files moved back and generated playlists removed.')) return
    setBusy('undo'); setErr(''); try { await api.undoRunbook(runId); setTimeout(load, 600) } catch (e) { setErr((e as Error).message) } finally { setBusy('') }
  }
  const troubleshoot = async () => { setBusy('ts'); try { setTs(await api.troubleshootRunbook(runId)) } catch (e) { setErr((e as Error).message) } finally { setBusy('') } }

  if (!rb) return (
    <div className="overlay overlay-2" onClick={onClose}>
      <div className="fo-runbook" ref={wrapRef} onClick={(e) => e.stopPropagation()}><div className="loading">Loading…</div></div>
    </div>
  )
  const c = rb.counts
  const total = rb.run.n_ops
  const done = c.ok || 0, failed = c.failed || 0, pending = c.pending || 0
  const hasPending = pending > 0 || status === 'planned' || status === 'paused'
  return (
    <div className="overlay overlay-2" onClick={onClose}>
      <div className="fo-runbook" ref={wrapRef} onClick={(e) => e.stopPropagation()}>
        <button className="close" onClick={onClose}>×</button>
        <h2>Runbook #{rb.run.id} <StatusBadge status={status || ''} /></h2>
        <div className="fo-rb-meta">
          <code className="dm-path">{rb.run.root}</code>
          <span className="dim"> · {rb.run.profile} · {rb.run.scope === 'single_system' ? 'single system' : 'multi-system'}</span>
        </div>
        <ProgressBar done={done} total={total} failed={failed} />
        <div className="fo-rb-counts">
          <span>{done}/{total} done</span>
          {failed > 0 && <span className="err">{failed} failed</span>}
          {pending > 0 && <span className="dim">{pending} pending</span>}
        </div>
        {rb.job_error && <div className="connect-msg err">{rb.job_error}</div>}
        {err && <div className="connect-msg err">{err}</div>}

        <div className="fo-actions fo-rb-actions">
          {hasPending && !live && <button className="go" disabled={busy !== ''} onClick={execute}>{(status === 'paused' || done > 0) ? 'Resume ▶' : 'Execute ▶'}</button>}
          {live && <button className="ops-btn" disabled={busy !== ''} onClick={pause}>Pause ⏸</button>}
          {(status === 'done' || status === 'partial') && <button className="ops-btn fo-revert" disabled={busy !== ''} onClick={undo}>↩ Revert</button>}
          {(status === 'partial' || failed > 0) && <button className="ops-btn" disabled={busy !== ''} onClick={troubleshoot}>{busy === 'ts' ? '…' : 'Troubleshoot'}</button>}
        </div>

        {ts && (
          <div className="fo-panel">
            <div className="fo-panel-head">Troubleshoot — {ts.failed} failed · {ts.remaining} pending</div>
            {ts.findings.map((f) => (
              <div key={f.seq} className="fo-finding">
                <div className="fo-find-path"><code>{f.path}</code></div>
                <div className="fo-find-err err">{f.error}</div>
                <div className="fo-find-cause">{f.cause}</div>
                <div className="fo-find-fix dim">→ {f.fix}</div>
              </div>
            ))}
            {ts.resumable && <button className="go" disabled={busy !== ''} onClick={execute}>Resume ▶</button>}
          </div>
        )}

        <h3>Playbook</h3>
        <div className="fo-groups">
          {rb.groups.length === 0 && <div className="sync-note dim">No moves in this runbook.</div>}
          {rb.groups.map((g) => (
            <div key={g.dir} className="fo-group">
              <div className="fo-group-dir">📁 {g.dir}</div>
              {g.moves.map((m) => (
                <div key={m.seq} className="fo-gmove">
                  <span className={'fo-dot s-' + m.status} />
                  <span className="fo-from">{m.from}</span>
                  <span className="fo-arrow">→</span>
                  <span className="fo-to">{m.to}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
        <RunbookExtras rb={rb} />
      </div>
    </div>
  )
}

function RunbookExtras({ rb }: { rb: Runbook }) {
  const writes = rb.steps.filter((s) => s.op === 'write').length
  const rmdirs = rb.steps.filter((s) => s.op === 'rmdir').length
  if (!writes && !rmdirs) return null
  return (
    <div className="dim fo-extra">
      {writes > 0 && `${writes} playlist file(s) generated. `}
      {rmdirs > 0 && `${rmdirs} empty folder(s) removed.`}
    </div>
  )
}

function JobMonitor() {
  const [jobs, setJobs] = useState<Job[]>([])
  const [open, setOpen] = useState(false)
  const load = useCallback(() => api.jobs().then((j) => setJobs(j.jobs)).catch(() => {}), [])
  useEffect(() => { load(); const t = setInterval(load, 2500); return () => clearInterval(t) }, [load])

  const active = jobs.filter((j) => j.status === 'running' || j.status === 'paused')
  const shown = (active.length ? active : jobs).slice(0, 2)
  const pause = async (id: string) => { await api.pauseJob(id).catch(() => {}); load() }
  const del = async (id: string) => { await api.deleteJob(id).catch(() => {}); load() }

  if (jobs.length === 0) return null
  return (
    <div className="jobmon">
      <div className="jobmon-rows">
        {shown.map((j) => (
          <div key={j.id} className="jobmon-row">
            <span className="jm-label" title={j.label}>{j.label}</span>
            <ProgressBar done={j.progress.done} total={j.progress.total} failed={j.progress.failed} />
            <span className={'jm-status s-' + j.status}>{j.status}</span>
            {j.cancelable && <button className="jm-btn" title="Pause" onClick={() => pause(j.id)}>⏸</button>}
            {j.deletable && <button className="jm-btn" title="Remove" onClick={() => del(j.id)}>×</button>}
          </div>
        ))}
      </div>
      <button className="jm-expand icon-btn" title="All jobs" onClick={() => setOpen(true)}>⤢</button>
      {open && <JobOverlay onClose={() => setOpen(false)} />}
    </div>
  )
}

function JobOverlay({ onClose }: { onClose: () => void }) {
  const [jobs, setJobs] = useState<Job[]>([])
  const [openRun, setOpenRun] = useState<number | null>(null)
  const wrapRef = useClickOutside<HTMLDivElement>(true, onClose)
  const load = useCallback(() => api.jobs().then((j) => setJobs(j.jobs)).catch(() => {}), [])
  useEffect(() => { load(); const t = setInterval(load, 2000); return () => clearInterval(t) }, [load])

  const act = async (fn: Promise<unknown>) => { try { await fn } catch { /* */ } load() }
  return (
    <div className="overlay" onClick={onClose}>
      <div className="job-overlay" ref={wrapRef} onClick={(e) => e.stopPropagation()}>
        <button className="close" onClick={onClose}>×</button>
        <h2>Jobs</h2>
        {jobs.length === 0 && <div className="sync-note dim">No jobs.</div>}
        <div className="job-table">
          {jobs.map((j) => (
            <div key={j.id} className={'job-trow' + (j.kind === 'fileops' ? ' clickable' : '')}
              onClick={() => { if (j.kind === 'fileops' && j.run_id) setOpenRun(j.run_id) }}>
              <span className="job-label">{j.label}{j.detail ? <span className="dim"> — {j.detail}</span> : null}</span>
              <span className={'jm-status s-' + j.status}>{j.status}</span>
              <ProgressBar done={j.progress.done} total={j.progress.total} failed={j.progress.failed} />
              <span className="dim job-when">{relTime(j.when)}</span>
              <span className="job-acts" onClick={(e) => e.stopPropagation()}>
                {j.cancelable && <button className="jm-btn" title="Pause" onClick={() => act(api.pauseJob(j.id))}>⏸</button>}
                {j.restartable && <button className="jm-btn" title="Restart / resume" onClick={() => act(api.restartJob(j.id))}>▶</button>}
                {j.deletable && <button className="jm-btn" title="Delete" onClick={() => act(api.deleteJob(j.id))}>×</button>}
              </span>
              {j.error && <div className="connect-msg err job-err">{j.error}</div>}
            </div>
          ))}
        </div>
        {openRun != null && <RunbookModal runId={openRun} onClose={() => { setOpenRun(null); load() }} />}
      </div>
    </div>
  )
}
