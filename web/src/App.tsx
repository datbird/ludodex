import { useEffect, useState, useRef, useCallback, useMemo, Fragment, type CSSProperties, type ChangeEvent, type DragEvent, type FormEvent, type MouseEvent as ReactMouseEvent } from 'react'
import { api } from './api'
import type {
  GameRow, GameDetail, Stats, Facets, GamesQuery, AiConfig, AiArea,
  AiUsageModel, AiUsageDay, AiUsageSummary, AiPrice, Currency, Caps,
  DedupeSuggestion, ArtPick, Service, ServiceConnect, Achievements as AchData,
  MediaLibrary, MediaAsset, MediaKind, BannedMedia,
  OpsStatus, OpsDatabase, SyncService, SyncJob, RomLocation, RomJob, TagRef, Scores,
  Spotlight as SpotlightData, IdentifyCandidate, RecognizedGame,
  Device, LibraryManager,
  FileVariable, FileProfile, FilePlan, FileDetect, SourceModel,
  Runbook, RunHistoryRow, Troubleshoot, Job, AiCap,
  AiFinding, AiFindingCounts, AiScanTargets, AiScanRun, AiApplySelection,
  AiFindingPayload, ProviderMatch, ScopeValue,
  AuthUser, AuthStatus, AuthUserRow, CfAccessState, CfMapping, DbSyncState, DbSyncTest,
  Prefs, MediaMode, FileopsApplyMode, MediaLangMode, MediaLangResult, FsStat, OwnershipFact, Frame,
  SpotlightTheme, SourceRow, SplitSuggestion,
  GameRelease, SystemEntry,
} from './api'
import { providerColor, providerLabel } from './providers'
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

// A small colored dot for a provider origin — used on attribute values + source
// rows so provider attribution is visible and consistent everywhere.
function ProvDot({ origin }: { origin: string }) {
  return <span className="prov-dot" title={providerLabel(origin)}
    style={{ background: providerColor(origin) }} />
}
// A colored provider label pill (where the provider name is spelled out).
function ProvTag({ origin }: { origin: string }) {
  const c = providerColor(origin)
  return <span className="prov-tag"
    style={{ color: c, borderColor: c + '66', background: c + '18' }}>
    {providerLabel(origin)}</span>
}
// Tint a value badge from its origin(s): faint fill + border of the first
// provider's color (multi-origin values also carry a dot per provider).
function attrBadgeStyle(origins: string[]): CSSProperties {
  const c = providerColor(origins[0] || 'manual')
  return { borderColor: c + '99', background: c + '20' }
}

// Lock background page scroll while a modal/overlay is mounted, so scrolling
// inside the modal doesn't scroll the page behind it. Ref-counted so several
// stacked overlays don't unlock the body until the last one closes.
let _scrollLocks = 0
function useScrollLock(active = true) {
  useEffect(() => {
    if (!active) return
    _scrollLocks += 1
    if (_scrollLocks === 1) {
      // Reserve the (now-hidden) scrollbar's width so desktop content doesn't
      // shift when the modal opens. On mobile this is 0 (no persistent bar).
      const sbw = window.innerWidth - document.documentElement.clientWidth
      document.body.style.overflow = 'hidden'
      document.documentElement.style.overflow = 'hidden'
      if (sbw > 0) document.body.style.paddingRight = sbw + 'px'
    }
    return () => {
      _scrollLocks -= 1
      if (_scrollLocks <= 0) {
        _scrollLocks = 0
        document.body.style.overflow = ''
        document.documentElement.style.overflow = ''
        document.body.style.paddingRight = ''
      }
    }
  }, [active])
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
    // one section per categorical attribute (genres, themes, developers, …)
    ...Object.entries(facets?.attributes || {}).map(([kind, vals]) => ({
      title: kind.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase()),
      rows: vals.map((v) => ({ id: `attr:${kind}:${v}`, name: v })).sort(byName),
    })),
  ]
}

// Human label for a filter token, falling back to a prettified id when the row
// isn't in the current sections yet (e.g. facets still loading).
function prettifyFilterId(id: string): string {
  if (id.startsWith('attr:')) {                 // attr:<kind>:<value> -> just the value
    const rest = id.slice(5)
    const i = rest.indexOf(':')
    return i >= 0 ? rest.slice(i + 1) : rest
  }
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
  g: { norm_key: string; entry_key?: string; title: string; has_cover: boolean; identified?: boolean; framing_cover?: Frame; cover_v?: string | null }
  compact?: boolean
}) {
  const [failed, setFailed] = useState(false)
  if (!g.has_cover || failed)
    return <NoArt title={g.title} unmatched={g.identified === false} compact={compact} />
  // Framing needs a positioned, sized container (the poster grid's .cover). The
  // compact thumbnails (table cell, spotlight) aren't positioned, so a framed
  // .frame-box would escape to the viewport — and framing a ~40px thumb is
  // pointless anyway — so only apply framing in the full (non-compact) cover.
  const fs = compact ? undefined : frameStyle(g.framing_cover)
  // key/src carry cover_v so a re-pinned cover swaps in without a hard refresh.
  const img = <img key={g.cover_v || 'c'} loading="lazy"
    src={api.mediaUrl(g.entry_key ?? g.norm_key, 'cover', true, g.cover_v)} alt=""
    onError={() => setFailed(true)} />
  return fs ? <div className="frame-box" style={fs}>{img}</div> : img
}

// Default frame + the inline style that positions/zooms an image inside its
// viewport. Returns undefined for an unframed (identity) frame.
const DEFAULT_FRAME: Frame = { top: 0, right: 0, bottom: 0, left: 0, zoom: 1 }
function frameStyle(f?: Frame | null): CSSProperties | undefined {
  if (!f || (!f.top && !f.right && !f.bottom && !f.left && f.zoom === 1)) return undefined
  return {
    top: f.top + '%', right: f.right + '%', bottom: f.bottom + '%', left: f.left + '%',
    transform: `scale(${f.zoom})`,
  }
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

// Auth gate: on load, decide between the create-admin screen (fresh install),
// the login screen, or the app. The static SPA is always served; the API is
// gated server-side, so this just picks what to render.
export default function App() {
  const [authState, setAuthState] = useState<AuthStatus | null | undefined>(undefined)
  const refreshAuth = useCallback(
    () => api.authStatus().then(setAuthState).catch(() => setAuthState(null)), [])
  useEffect(() => { refreshAuth() }, [refreshAuth])

  if (authState === undefined) return <div className="boot-screen">Loading…</div>
  if (!authState || !authState.authenticated) {
    return <AuthScreen needsSetup={!!authState?.needs_setup} onAuthed={refreshAuth} />
  }
  return <LudodexApp user={authState.user}
    onLogout={async () => { try { await api.authLogout() } finally { refreshAuth() } }} />
}

function AuthScreen({ needsSetup, onAuthed }: { needsSetup: boolean; onAuthed: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setErr('')
    if (needsSetup && password !== confirm) { setErr('Passwords do not match'); return }
    setBusy(true)
    try {
      if (needsSetup) await api.authSetup(username.trim(), password)
      else await api.authLogin(username.trim(), password)
      onAuthed()
    } catch (e) { setErr((e as Error).message); setBusy(false) }
  }
  return (
    <div className="auth-screen">
      <form className="auth-card" onSubmit={submit}>
        <img className="auth-logo" src="/logo-mark.png" alt="" />
        <h1 className="auth-title">ludo<span>dex</span></h1>
        {needsSetup ? (
          <p className="auth-sub">Welcome — create the <b>admin</b> account to get started.</p>
        ) : (
          <p className="auth-sub">Sign in to your library.</p>
        )}
        <label className="auth-field">
          <span>Username</span>
          <input autoFocus autoComplete="username" value={username}
            onChange={(e) => setUsername(e.target.value)} />
        </label>
        <label className="auth-field">
          <span>Password</span>
          <input type="password" autoComplete={needsSetup ? 'new-password' : 'current-password'}
            value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {needsSetup && (
          <label className="auth-field">
            <span>Confirm password</span>
            <input type="password" autoComplete="new-password" value={confirm}
              onChange={(e) => setConfirm(e.target.value)} />
          </label>
        )}
        {err && <div className="auth-err">{err}</div>}
        <button className="go primary auth-submit" type="submit"
          disabled={busy || !username.trim() || !password}>
          {busy ? 'Please wait…' : needsSetup ? 'Create admin & continue' : 'Sign in'}
        </button>
        {needsSetup && <div className="auth-hint">Password must be at least 8 characters. This account is stored only on this server.</div>}
      </form>
    </div>
  )
}

function LudodexApp({ user, onLogout }: { user: AuthUser | null; onLogout: () => void }) {
  const [stats, setStats] = useState<Stats | null>(null)
  const [facets, setFacets] = useState<Facets | null>(null)
  const [q, setQ] = useState('')
  const [status, setStatus] = useState<'owned' | 'wanted' | 'all'>('owned')
  // Bare unidentified ROMs (just a filename, no match) are hidden by default.
  const [showUnidentified, setShowUnidentified] = useState(false)
  // Mobile: collapse everything past the search into one "Options" disclosure.
  const [optsOpen, setOptsOpen] = useState(false)
  const [filters, setFilters] = useState<FilterState>({})
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [filterQ, setFilterQ] = useState('')
  const [openSecs, setOpenSecs] = useState<Set<string>>(new Set())   // filter sections expanded (collapsed by default)
  const toggleSec = (t: string) =>
    setOpenSecs((s) => { const n = new Set(s); n.has(t) ? n.delete(t) : n.add(t); return n })
  const [sort, setSort] = useState<SortState>({})
  const [sortOpen, setSortOpen] = useState(false)
  const [searchMode, setSearchMode] = useState<'basic' | 'ai' | 'query'>('basic')
  const aiMode = searchMode === 'ai'
  const [aiNote, setAiNote] = useState('')

  const [items, setItems] = useState<GameRow[]>([])
  const [total, setTotal] = useState(0)
  const [hidden, setHidden] = useState(0)   // unidentified matches hidden by the toggle
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  // multi-select → device wishlist ("I want these games on that device")
  const [selectMode, setSelectMode] = useState(false)
  // multi-select: entry_key -> the row (keeps norm_key/emulation for actions across pages)
  const [picked, setPicked] = useState<Map<string, GameRow>>(new Map())
  const [wishDevs, setWishDevs] = useState<Device[]>([])
  const [addMenuOpen, setAddMenuOpen] = useState(false)
  const [wantMsg, setWantMsg] = useState('')
  // set when "Add to device" is chosen on a mixed selection: confirm skipping the
  // marketplace games before adding the ROM-eligible ones to that device's wishlist.
  const [wantConfirm, setWantConfirm] = useState<{ deviceId: number; deviceName: string } | null>(null)
  const [showSettings, setShowSettings] = useState(false)
  const [settingsTarget, setSettingsTarget] = useState<string | null>(null)
  const openSettings = useCallback((section?: string) => {
    setSettingsTarget(section ?? null); setShowSettings(true)
  }, [])
  const [showProfile, setShowProfile] = useState(false)
  const [showAddGame, setShowAddGame] = useState(false)
  const [showWand, setShowWand] = useState(false)
  const [prefsTick, setPrefsTick] = useState(0)   // bump to push prefs changes live
  // Dashboard is always the landing page (not persisted), per product decision.
  const [tab, setTab] = useState<'library' | 'dashboard' | 'files'>('dashboard')
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

  const refreshStats = useCallback(() => { api.stats().then(setStats).catch(() => {}) }, [])
  useEffect(() => {
    refreshStats()
    api.facets().then(setFacets).catch(() => {})
  }, [refreshStats])

  const load = useCallback(async (reset: boolean) => {
    setLoading(true)
    try {
      const off = reset ? 0 : offset
      const qy: GamesQuery = {
        q: searchMode === 'basic' ? (q || undefined) : undefined,
        query: searchMode === 'query' ? (q || undefined) : undefined,
        include: Object.keys(filters).filter((k) => filters[k] === 'include'),
        exclude: Object.keys(filters).filter((k) => filters[k] === 'exclude'),
        sort: ([1, 2, 3] as const)
          .map((r) => Object.keys(sort).find((k) => sort[k] === r))
          .filter((k): k is string => !!k),
        status,
        // the show-unidentified toggle is the single control for ROM visibility —
        // honored during search too (toggle it on to find unidentified titles).
        identified: showUnidentified ? 'all' : 'only',
        limit: perPage,
        offset: off,
      }
      const page = await api.games(qy)
      setTotal(page.total)
      setHidden(page.hidden_unidentified ?? 0)
      setOffset(off + page.items.length)
      setItems((prev) => (reset ? page.items : [...prev, ...page.items]))
    } finally {
      setLoading(false)
    }
  }, [q, status, showUnidentified, filters, sort, perPage, offset, searchMode])

  const filterKey = JSON.stringify(filters)
  const sortReloadKey = JSON.stringify(sort)
  // reload on filter/sort change (debounced); AI mode doesn't auto-run
  useEffect(() => {
    if (aiMode) return
    setAiNote('')
    const t = setTimeout(() => { setOffset(0); load(true) }, 250)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, status, showUnidentified, filterKey, sortReloadKey, perPage, searchMode])

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
  // load the device list the first time select mode is entered (for the add menu)
  useEffect(() => {
    if (selectMode && !wishDevs.length) api.devices().then((d) => setWishDevs(d.devices)).catch(() => {})
  }, [selectMode, wishDevs.length])
  const togglePick = (g: GameRow) =>
    setPicked((p) => {
      const n = new Map(p); const k = g.entry_key ?? g.norm_key
      n.has(k) ? n.delete(k) : n.set(k, g); return n
    })
  const onCard = (g: GameRow) => {
    if (!selectMode) { setSelected(g.entry_key ?? g.norm_key); return }
    togglePick(g)                             // any game is selectable
  }
  // Store-marketplace games (Steam/Epic/GOG/itch/EA…) live in their launchers and
  // can't sync to a device — only ROMs (has an emulation source) are eligible.
  const addPickedTo = (deviceId: number, deviceName: string) => {
    const hasStore = [...picked.values()].some((r) => !r.emulation)
    setAddMenuOpen(false)
    if (hasStore) { setWantConfirm({ deviceId, deviceName }); return }  // mixed → confirm
    doAddWants(deviceId, deviceName)
  }
  const doAddWants = async (deviceId: number, deviceName: string) => {
    setWantConfirm(null)
    const nks = [...new Set([...picked.values()].filter((r) => r.emulation).map((r) => r.norm_key))]
    if (!nks.length) {
      setWantMsg('None of the selected games are ROMs.')
      setTimeout(() => setWantMsg(''), 4500); return
    }
    try {
      const r = await api.addWants(deviceId, nks)
      setWantMsg(`Added ${r.added} to ${deviceName}${r.skipped ? ` · ${r.skipped} skipped` : ''} ✓`)
      setPicked(new Map())
    } catch (e) { setWantMsg((e as Error).message) }
    setTimeout(() => setWantMsg(''), 4500)
  }
  // Bulk Magic wand: AI-enrich every selected game (base keys, deduped) in one scan —
  // findings land in the Jobs monitor to review/accept, same as the single-game wand.
  const wandPicked = async () => {
    const nks = [...new Set([...picked.values()].map((r) => r.norm_key))]
    if (!nks.length) return
    try {
      await api.aimetaScan({ norm_keys: nks, label: `${nks.length} selected`,
                             media: true, metadata: true, web: true })
      setWantMsg(`✨ Sent ${nks.length} game(s) to the Jobs monitor — review & accept there`)
      setPicked(new Map()); setSelectMode(false); setAddMenuOpen(false)
    } catch (e) { setWantMsg((e as Error).message) }
    setTimeout(() => setWantMsg(''), 5000)
  }

  // selection split for the device wishlist: ROMs are eligible, marketplace games aren't
  const pickedRoms = [...picked.values()].filter((r) => r.emulation)
  const pickedStore = [...picked.values()].filter((r) => !r.emulation)
  const activeFilters = Object.keys(filters).length
  const filterSections = buildFilterSections(facets)
  // every categorical attribute is available as an optional table column (id
  // 'attr:<kind>'); 'description' is excluded (too long for a cell).
  const attrCols = useMemo(() => Object.keys(facets?.attributes || {})
    .filter((k) => k !== 'description').sort()
    .map((k) => ({ id: 'attr:' + k, kind: k, label: k.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase()) })),
    [facets])
  const visibleAttrCols = attrCols.filter((c) => cols.includes(c.id))
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
      setHidden(0)
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
              {(stats.identified ?? stats.games).toLocaleString()} identified games · {stats.media.games_with_art.toLocaleString()} with art ·{' '}
              {stats.cross_source} cross-source
            </div>
          )}
        </div>
        <div className="header-actions">
          <JobMonitor />
          <SyncMenu />
          <button className="icon-btn" title="Settings" onClick={() => openSettings()}>
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
                <div className="pm-name">{user?.username || 'Guest'}</div>
                <div className="pm-sub">{user
                  ? (user.role === 'admin' ? 'Administrator' : 'Signed in')
                  : 'Not signed in'}</div>
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
                <button className="pm-item" onClick={onLogout}>Sign out</button>
              </div>
            )}
          </div>
        </div>
      </header>

      <ParticleTabs className="main-tabs" fill active={tab}
        onSelect={(id) => setTab(id as 'library' | 'dashboard' | 'files')}
        tabs={[{ id: 'dashboard', label: 'Dashboard' }, { id: 'library', label: 'Library' },
               { id: 'files', label: 'Files' }]} />

      {tab === 'dashboard' && <Dashboard stats={stats} onBrowse={() => setTab('library')}
        onFilter={(f) => { setFilters(f); setTab('library') }} onOpen={setSelected}
        prefsTick={prefsTick} onOpenSettings={openSettings} />}

      {tab === 'library' && (<>
      {!!stats?.pending_meta && (
        <PendingApplyBar count={stats.pending_meta}
          onApplied={() => { refreshStats(); load(true) }} />
      )}
      <div className={'controls' + (optsOpen ? ' opts-open' : '')}>
        <div className="search-mode has-tip"
          data-tip="Basic = title contains, across your whole library. ✨ AI = natural-language (“co-op platformers I own”). ⌘ Query = advanced field:value search.">
          {(['basic', 'ai', 'query'] as const).map((m) => (
            <button key={m} type="button" className={'sm-seg' + (searchMode === m ? ' on' : '')}
              onClick={() => { setSearchMode(m); setAiNote('') }}>
              {m === 'basic' ? 'Basic' : m === 'ai' ? '✨ AI' : '⌘ Query'}
            </button>
          ))}
        </div>
        <input
          className="search"
          placeholder={aiMode ? 'Ask: "co-op platformers I own"…'
            : searchMode === 'query' ? 'mario platform:snes genre:racing year:>1990 -tag:multiplayer'
            : 'Search titles…'}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (aiMode && e.key === 'Enter') runAi() }}
        />
        {aiMode && <button className="go" onClick={runAi}>Ask</button>}
        {searchMode === 'query' && (
          <span className="query-hint has-tip" data-tip="platform: system: source: genre: theme: dev: publisher: series: tag: os: device: · year:>1990 · score:>=75 · prefix - to exclude · quote &quot;multi word&quot;">?</span>
        )}
        <button type="button" className={'opts-toggle' + (optsOpen ? ' on' : '')}
          aria-expanded={optsOpen} onClick={() => setOptsOpen((v) => !v)}>
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor"
            strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <line x1="4" y1="6" x2="20" y2="6" /><line x1="4" y1="12" x2="20" y2="12" />
            <line x1="4" y1="18" x2="20" y2="18" />
          </svg>
          Options
        </button>
        <div className="own-seg has-tip lib-collapse" role="group" aria-label="Ownership"
          data-tip="Owned = games you have. Wanted = your imported store wishlists (Steam/GOG) for titles you don't own yet. All = both.">
          {(['owned', 'wanted', 'all'] as const).map((s) => (
            <button key={s} type="button" className={'own-seg-btn' + (status === s ? ' on' : '')}
              disabled={aiMode} onClick={() => setStatus(s)}>
              {s === 'owned' ? 'Owned' : s === 'wanted' ? 'Wanted' : 'All'}
              {s === 'wanted' && !!stats?.wanted && <span className="own-seg-n">{stats.wanted}</span>}
            </button>
          ))}
        </div>
        <div className={'filter-wrap lib-collapse' + (filtersOpen ? '' : ' has-tip')} ref={filtersRef}
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
                    const open = fq ? true : openSecs.has(sec.title)  // search auto-expands matches
                    return (
                      <Fragment key={sec.title}>
                        <button className="fg-section fg-section-toggle" onClick={() => toggleSec(sec.title)}>
                          <span className={'sync-chev' + (open ? ' open' : '')}>▸</span>
                          <span>{sec.title}</span>
                          <span className="fg-count">{sec.rows.length}</span>
                        </button>
                        {open && rows.map((r) => (
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
        <div className={'filter-wrap lib-collapse' + (sortOpen ? '' : ' has-tip')} ref={sortRef}
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
      <div className={'results-bar' + (optsOpen ? ' opts-open' : '')}>
        <div className="count">{total.toLocaleString()} results
          {!showUnidentified && hidden > 0 && (
            <button className="hidden-hint" onClick={() => setShowUnidentified(true)}
              title="Show the unidentified ROMs that also match your search">
              · 👁 {hidden.toLocaleString()} unidentified match{hidden === 1 ? '' : 'es'} hidden — show
            </button>
          )}</div>
        <div className="results-tools lib-collapse">
          {!!stats?.unidentified && (
            <button className={'filter-btn' + (showUnidentified ? ' active' : '')}
              title="Bare ROMs with no provider match yet — hidden until identified (manually or by the Magic wand)"
              onClick={() => setShowUnidentified((v) => !v)}>
              {showUnidentified ? '🙈 Hide' : '👁'} {stats.unidentified.toLocaleString()} unidentified
            </button>
          )}
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
                  {attrCols.length > 0 && <div className="col-note col-sec">Attributes</div>}
                  {attrCols.map((c) => (
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
          <button className={'filter-btn' + (selectMode ? ' active' : '')}
            title="Select multiple games — AI-enrich them with the Magic wand, or add ROMs to a device's wishlist"
            onClick={() => { setSelectMode((v) => !v); setPicked(new Map()); setAddMenuOpen(false) }}>
            {selectMode ? '✕ Cancel select' : '☑ Select'}
          </button>
          <button className="filter-btn wand-btn"
            title="Let AI enrich and supplement metadata and media for your library"
            onClick={() => setShowWand(true)}>
            <span className="wand-spark">✨</span> Magic wand
          </button>
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
        <div className={'grid' + (selectMode ? ' selecting' : '')}>
          {items.map((g) => (
            <button key={g.entry_key ?? g.norm_key} onClick={() => onCard(g)}
              className={'card'
                + (selectMode && picked.has(g.entry_key ?? g.norm_key) ? ' picked' : '')}>
              {selectMode && (
                <span className="card-check">{picked.has(g.entry_key ?? g.norm_key) ? '✓' : ''}</span>
              )}
              <div className="cover">
                <Cover g={g} />
                {g.wanted && <span className="want-badge">WANTED</span>}
              </div>
              <div className="title">{g.title}</div>
              <div className="srcs">{g.wanted ? g.sources_summary.replace(/wishlist:/, 'Wishlist: ') : g.sources_summary}</div>
            </button>
          ))}
        </div>
      ) : (
        <div className="table-scroll">
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
              {visibleAttrCols.map((c) => <th key={c.id} className="gt-attr">{c.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {items.map((g) => (
              <tr key={g.entry_key ?? g.norm_key} onClick={() => onCard(g)}
                className={selectMode && picked.has(g.entry_key ?? g.norm_key) ? 'picked' : ''}>
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
                  <td className="gt-num" title={g.identified && !g.matched
                    ? 'Identified by its store/manual source (not cross-referenced to a metadata provider)'
                    : g.matched ? 'Matched to a metadata provider (IGDB/ScreenScraper)'
                    : 'Not identified — a bare file with no provider match'}>
                    {g.identified ? '✓' : <span className="dim">—</span>}</td>}
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
                {visibleAttrCols.map((c) => (
                  <td key={c.id} className="gt-attr" title={g.attrs?.[c.kind] || ''}>
                    {g.attrs?.[c.kind] || <span className="dim">—</span>}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}

      {items.length < total && (
        <button className="more" disabled={loading} onClick={() => load(false)}>
          {loading ? 'Loading…' : `Load more (${items.length}/${total})`}
        </button>
      )}

      {selectMode && (
        <div className="select-bar">
          <span className="sel-count">{picked.size} selected</span>
          <span className="sel-hint dim">✨ Magic wand: any game · Add to device: ROM-only wishlist (no transfer)</span>
          <button className="go" disabled={!picked.size} onClick={wandPicked}
            title="AI-enrich every selected game — findings land in the Jobs monitor to review & accept">
            ✨ Magic wand</button>
          <div className="sel-add">
            <button className="go" disabled={!pickedRoms.length}
              title={!picked.size ? 'Select some games first'
                : !pickedRoms.length ? 'None of the selected games are ROMs — store games (Steam, Epic, GOG…) live in their launchers and can’t sync to a device'
                : pickedStore.length ? `${pickedRoms.length} ROM(s) will sync; ${pickedStore.length} store game(s) will be skipped`
                : `Add ${pickedRoms.length} ROM(s) to a device’s wishlist`}
              onClick={() => setAddMenuOpen((v) => !v)}>
              Add to device ▾</button>
            {addMenuOpen && pickedRoms.length > 0 && (
              <div className="sel-dev-menu">
                {wishDevs.length === 0
                  ? <div className="dim sel-dev-none">No devices — add one in Connections.</div>
                  : wishDevs.map((d) => (
                    <button key={d.id} onClick={() => addPickedTo(d.id, d.name)}>
                      {d.name} <span className="dim">{d.transport}</span></button>
                  ))}
              </div>
            )}
          </div>
          {picked.size > 0 && <button className="ops-btn" onClick={() => setPicked(new Map())}>Clear</button>}
          {wantMsg && <span className="connect-msg ok sel-msg">{wantMsg}</span>}
        </div>
      )}

      {wantConfirm && (
        <div className="overlay" onClick={() => setWantConfirm(null)}>
          <div className="panel confirm-panel" onClick={(e) => e.stopPropagation()}>
            <button className="close" onClick={() => setWantConfirm(null)}>×</button>
            <h3>Some games can’t sync to {wantConfirm.deviceName}</h3>
            <p className="confirm-lede">Only ROMs and local files can be added to a device.
              These {pickedStore.length} marketplace game{pickedStore.length === 1 ? '' : 's'} will
              be <b>skipped</b> — they live in their own launcher (Steam, Epic, GOG…):</p>
            <ul className="skip-list">
              {pickedStore.map((r) => (
                <li key={r.entry_key ?? r.norm_key}>
                  <span className="skip-title">{r.title}</span>
                  <span className="dim">{r.sources_summary}</span></li>
              ))}
            </ul>
            <div className="confirm-actions">
              <button className="go" disabled={!pickedRoms.length}
                onClick={() => doAddWants(wantConfirm.deviceId, wantConfirm.deviceName)}>
                Add {pickedRoms.length} ROM{pickedRoms.length === 1 ? '' : 's'} to {wantConfirm.deviceName}</button>
              <button className="ops-btn" onClick={() => setWantConfirm(null)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
      </>)}

      {tab === 'files' && <FilesTab />}

      {selected && <Detail nk={selected} onClose={() => { setSelected(null); refreshStats() }}
        onMediaChanged={() => load(true)} onNavigate={setSelected} />}
      {showSettings && <Settings onClose={() => setShowSettings(false)}
        onPrefsChanged={() => { load(true); setPrefsTick((t) => t + 1) }} user={user}
        initialSection={settingsTarget} />}
      {showAddGame && <AddGame facets={facets} onClose={() => setShowAddGame(false)}
        onAdded={() => load(true)} />}
      {showWand && <MagicWandOverlay
        filterQuery={{
          q: q || undefined,
          include: Object.keys(filters).filter((k) => filters[k] === 'include'),
          exclude: Object.keys(filters).filter((k) => filters[k] === 'exclude'),
        }}
        filterCount={total}
        onClose={() => setShowWand(false)} />}
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
  { id: 'metadata', name: 'AI Metadata', icon: '🔎' },
  { id: 'account', name: 'Account & Users', icon: '👤' },
]
const SUBSECTIONS: Record<string, { id: string; name: string }[]> = {
  ai: [{ id: 'usage', name: 'AI Usage' }, { id: 'keys', name: 'API Keys' },
       { id: 'budgets', name: 'Budgets & limits' }, { id: 'report', name: 'Usage report' }],
  connections: [{ id: 'devices', name: 'Devices' },
                { id: 'credentials', name: 'Stores & providers' },
                { id: 'dbsync', name: 'Database sync' },
                { id: 'limits', name: 'Rate limits' }],
  library: [{ id: 'preferences', name: 'Preferences' }, { id: 'banned', name: 'Banned media' }],
  dashboard: [{ id: 'spotlight', name: 'Spotlight' }],
  metadata: [{ id: 'scan', name: 'Scan' },
             { id: 'review', name: 'Review' }],
  account: [{ id: 'users', name: 'Users' },
            { id: 'access', name: 'Cloudflare Access' }],
}

// Extra search terms per subtab so the Settings search matches the actual
// controls inside each panel, not just the tab's name. Keyed by subtab id.
const SETTINGS_KEYWORDS: Record<string, string> = {
  usage: 'ai model provider default vision data badge function area prompt gemini anthropic openai limits',
  keys: 'api key token credentials gemini openai anthropic openrouter',
  budgets: 'budget cost price dollar currency usd token limit cap spend rate openrouter monthly input output',
  report: 'usage report cost tokens spend',
  devices: 'device library manager rom media path ssh host master edit folder connection',
  credentials: 'stores providers steam gog epic itch screenscraper igdb ea login accounts credentials',
  dbsync: 'database sync backup replicate',
  limits: 'rate limit api throttle quota cooldown per minute per day',
  preferences: 'media language ban file operations browse commander manifests apply mode preferences distribution',
  banned: 'banned media unban hidden',
  spotlight: 'spotlight rotation themes dashboard',
  scan: 'metadata scan audit supplement ai',
  review: 'metadata review findings accept apply',
  users: 'users accounts password role admin login',
  access: 'cloudflare access sso jwt auth',
}

function Settings({ onClose, onPrefsChanged, user, initialSection }: {
  onClose: () => void; onPrefsChanged: () => void; user: AuthUser | null
  initialSection?: string | null
}) {
  const [section, setSection] = useState(initialSection || 'ai')
  const [sub, setSub] = useState(
    initialSection ? ((SUBSECTIONS[initialSection] ?? [])[0]?.id ?? '') : 'usage')
  const [q, setQ] = useState('')   // settings search
  const [cfg, setCfg] = useState<AiConfig | null>(null)

  const reload = () => api.aiConfig().then(setCfg).catch(() => {})
  useEffect(() => { reload() }, [])
  useScrollLock()

  // Escape closes the settings window (matches every other modal's expectation).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // "Account & Users" is admin-only. Sorted alphabetically by name so the list —
  // and any future section — stays in order without manual bookkeeping.
  const sections = SECTIONS
    .filter((s) => s.id !== 'account' || user?.role === 'admin')
    .sort((a, b) => a.name.localeCompare(b.name))
  const subs = SUBSECTIONS[section] ?? []

  // Search across every section+subtab (by name + the keyword hints above).
  // Non-null while a query is active; each hit jumps straight to its sub-panel.
  const term = q.trim().toLowerCase()
  const results = term
    ? sections.flatMap((s) => (SUBSECTIONS[s.id] ?? [])
        .filter((t) => (s.name + ' ' + t.name + ' ' + (SETTINGS_KEYWORDS[t.id] || ''))
          .toLowerCase().includes(term))
        .map((t) => ({ section: s.id, icon: s.icon, sectionName: s.name, sub: t.id, subName: t.name })))
    : null

  return (
    <div className="overlay" onClick={onClose}>
      <div className="settings-window" onClick={(e) => e.stopPropagation()}>
        <button className="close" onClick={onClose} aria-label="Close settings">×</button>
        <nav className="settings-nav">
          <div className="settings-title">Settings</div>
          <input className="settings-search" type="search" placeholder="🔍 Search settings…"
            value={q} onChange={(e) => setQ(e.target.value)} autoFocus />
          {results
            ? (results.length
                ? results.map((r) => (
                    <button key={r.section + '/' + r.sub} className="nav-item nav-result"
                      onClick={() => { setSection(r.section); setSub(r.sub); setQ('') }}>
                      <span className="nav-icon">{r.icon}</span>
                      <span className="nav-result-txt">{r.subName}
                        <span className="nav-result-sec">{r.sectionName}</span></span>
                    </button>))
                : <div className="nav-none dim">No settings match “{q}”.</div>)
            : sections.map((s) => (
                <button key={s.id}
                  className={'nav-item' + (section === s.id ? ' sel' : '')}
                  onClick={() => { setSection(s.id); setSub((SUBSECTIONS[s.id] ?? [])[0]?.id ?? '') }}>
                  <span className="nav-icon">{s.icon}</span>{s.name}
                </button>
              ))}
        </nav>
        <div className="settings-main">
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
                : sub === 'dbsync' ? <DatabaseSync />
                : sub === 'limits' ? <RateLimits /> : null)
              : section === 'library'
              ? (sub === 'banned' ? <BannedMediaPanel /> : <LibraryPrefs onChanged={onPrefsChanged} />)
              : section === 'dashboard'
              ? <DashboardPrefs onChanged={onPrefsChanged} />
              : section === 'metadata'
              ? (sub === 'scan' ? <MetadataScan /> : <MetadataReview />)
              : section === 'account'
              ? (sub === 'access' ? <CfAccessPanel /> : <UsersPanel currentUser={user} />)
              : !cfg ? <div className="loading">Loading…</div>
              : sub === 'usage' ? <AiUsage cfg={cfg} onChange={reload} />
              : sub === 'keys' ? <ApiKeys cfg={cfg} onChange={reload} />
              : sub === 'budgets' ? <AiBudgets />
              : sub === 'report' ? <AiUsageReport />
              : null}
          </div>
        </div>
      </div>
    </div>
  )
}

function UsersPanel({ currentUser }: { currentUser: AuthUser | null }) {
  const [data, setData] = useState<{ users: AuthUserRow[]; me: number; roles: string[] } | null>(null)
  const [err, setErr] = useState('')
  const [nu, setNu] = useState({ username: '', password: '', role: 'user' })
  const [busy, setBusy] = useState(false)
  const [pwFor, setPwFor] = useState<number | null>(null)
  const [pwVal, setPwVal] = useState('')

  const load = () => api.listUsers().then((d) => { setData(d); setErr('') })
    .catch((e) => setErr((e as Error).message))
  useEffect(() => { load() }, [])

  const add = async (e: FormEvent) => {
    e.preventDefault(); setErr(''); setBusy(true)
    try { await api.addUser(nu.username.trim(), nu.password, nu.role); setNu({ username: '', password: '', role: 'user' }); load() }
    catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const del = async (u: AuthUserRow) => {
    if (!confirm(`Delete user “${u.username}”? They’ll be signed out and removed.`)) return
    setErr('')
    try { await api.deleteUser(u.id); load() } catch (e) { setErr((e as Error).message) }
  }
  const changeRole = async (u: AuthUserRow, role: string) => {
    setErr('')
    try { await api.setUserRole(u.id, role); load() } catch (e) { setErr((e as Error).message); load() }
  }
  const resetPw = async (u: AuthUserRow) => {
    setErr('')
    try { await api.resetPassword(u.id, pwVal); setPwFor(null); setPwVal('') }
    catch (e) { setErr((e as Error).message) }
  }

  if (!data) return err ? <div className="connect-msg err">{err}</div> : <div className="loading">Loading…</div>
  const roles = data.roles.length ? data.roles : ['admin', 'user']
  return (
    <>
      <h2>Users</h2>
      <p className="dim">Local accounts for signing in to ludodex, stored on this server
        (passwords are hashed, never plain text). Admins can add or remove users and change
        roles. The last admin can’t be removed or demoted.
        {currentUser && <> You’re signed in as <b>{currentUser.username}</b>.</>}</p>
      {err && <div className="connect-msg err">{err}</div>}
      <div className="users-list">
        {data.users.map((u) => (
          <div key={u.id} className="user-row">
            <div className="user-main">
              <span className="user-name">{u.username}
                {u.id === data.me && <span className="user-you">you</span>}</span>
              <span className="user-since">added {new Date(u.created * 1000).toLocaleDateString()}</span>
            </div>
            <select className="user-role" value={u.role} onChange={(e) => changeRole(u, e.target.value)}>
              {roles.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
            <button className="ops-btn" onClick={() => { setPwFor(pwFor === u.id ? null : u.id); setPwVal('') }}>
              {pwFor === u.id ? 'Cancel' : 'Reset password'}</button>
            <button className="emu-rm" title={u.id === data.me ? "You can't delete yourself" : 'Delete user'}
              onClick={() => del(u)} disabled={u.id === data.me}>×</button>
            {pwFor === u.id && (
              <div className="user-pw-row">
                <input type="password" autoComplete="new-password" placeholder="new password (min 8)"
                  value={pwVal} onChange={(e) => setPwVal(e.target.value)} />
                <button className="go primary" disabled={pwVal.length < 8} onClick={() => resetPw(u)}>Set password</button>
              </div>
            )}
          </div>
        ))}
      </div>
      <form className="user-add" onSubmit={add}>
        <div className="user-add-title">＋ Add a user</div>
        <div className="user-add-grid">
          <input placeholder="username" autoComplete="off" value={nu.username}
            onChange={(e) => setNu({ ...nu, username: e.target.value })} />
          <input type="password" autoComplete="new-password" placeholder="password (min 8)" value={nu.password}
            onChange={(e) => setNu({ ...nu, password: e.target.value })} />
          <select value={nu.role} onChange={(e) => setNu({ ...nu, role: e.target.value })}>
            {roles.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
          <button className="go primary" type="submit"
            disabled={busy || !nu.username.trim() || nu.password.length < 8}>Add user</button>
        </div>
      </form>
    </>
  )
}

function DatabaseSync() {
  const [st, setSt] = useState<DbSyncState | null>(null)
  const [pb, setPb] = useState({ url: '', email: '', password: '' })
  const [fb, setFb] = useState({ project_id: '', database: '', prefix: '', sa_json: '' })
  const [msg, setMsg] = useState<{ pb?: string; fb?: string; run?: string }>({})
  const [checks, setChecks] = useState<{ pb?: DbSyncTest; fb?: DbSyncTest }>({})
  const [busy, setBusy] = useState('')

  const hydrate = (d: DbSyncState) => {
    setSt(d)
    setPb({ url: d.pocketbase.url, email: d.pocketbase.email, password: '' })
    setFb({ project_id: d.firebase.project_id, database: d.firebase.database, prefix: d.firebase.prefix, sa_json: '' })
  }
  useEffect(() => { api.dbSync().then(hydrate).catch(() => {}) }, [])
  // poll while a sync job is running so the result updates live
  const running = st?.job?.running
  useEffect(() => {
    if (!running) return
    const t = setInterval(() => api.dbSync().then(setSt).catch(() => {}), 2000)
    return () => clearInterval(t)
  }, [running])

  if (!st) return <div className="loading">Loading…</div>
  const job = st.job

  const savePb = async () => {
    setBusy('pb-save'); setMsg((m) => ({ ...m, pb: '' }))
    try {
      setSt(await api.dbSyncSet({ pocketbase: { url: pb.url, email: pb.email, ...(pb.password ? { password: pb.password } : {}) } }))
      setPb((p) => ({ ...p, password: '' })); setMsg((m) => ({ ...m, pb: 'Saved ✓' }))
    } catch (e) { setMsg((m) => ({ ...m, pb: (e as Error).message })) } finally { setBusy('') }
  }
  const saveFb = async () => {
    setBusy('fb-save'); setMsg((m) => ({ ...m, fb: '' }))
    try {
      setSt(await api.dbSyncSet({ firebase: { project_id: fb.project_id, database: fb.database, prefix: fb.prefix, ...(fb.sa_json ? { sa_json: fb.sa_json } : {}) } }))
      setFb((f) => ({ ...f, sa_json: '' })); setMsg((m) => ({ ...m, fb: 'Saved ✓' }))
    } catch (e) { setMsg((m) => ({ ...m, fb: (e as Error).message })) } finally { setBusy('') }
  }
  const test = async (target: 'pocketbase' | 'firebase') => {
    const k = target === 'pocketbase' ? 'pb' : 'fb'
    setBusy(k + '-test'); setChecks((c) => ({ ...c, [k]: undefined }))
    try { const r = await api.dbSyncTest(target); setChecks((c) => ({ ...c, [k]: r })) }
    catch (e) { setChecks((c) => ({ ...c, [k]: { ok: false, checks: [], summary: (e as Error).message } })) }
    finally { setBusy('') }
  }
  const checklist = (r?: DbSyncTest) => r && (
    <div className={'dbsync-check' + (r.ok ? ' ok' : ' bad')}>
      {r.checks.map((ch, i) => (
        <div key={i} className="dbsync-check-row">
          <span className={'dbsync-tick' + (ch.ok ? ' ok' : ' bad')}>{ch.ok ? '✓' : '✗'}</span>
          <span className="dbsync-check-label">{ch.label}</span>
          <span className="dbsync-check-detail">{ch.detail}</span>
        </div>
      ))}
      <div className="dbsync-check-summary">{r.summary}</div>
    </div>
  )
  const toggle = async (patch: Record<string, unknown>) => { try { setSt(await api.dbSyncSet(patch)) } catch { /* ignore */ } }
  const runNow = async () => {
    setBusy('run'); setMsg((m) => ({ ...m, run: '' }))
    try { setSt(await api.dbSyncRun()) } catch (e) { setMsg((m) => ({ ...m, run: (e as Error).message })) } finally { setBusy('') }
  }

  return (
    <>
      <h2>Database sync</h2>
      <p className="dim">Mirror your library out to a database that other apps and devices can read.</p>
      <div className="dbsync-explain">
        <div><b>What this does.</b> After each catalog rebuild, ludodex publishes the finished
          catalog — your <code>games</code> and <code>sources</code> — into the target(s) you enable
          below. That copy is for <em>other</em> clients to read; it isn’t where ludodex itself
          stores data.</div>
        <div><b>Why it’s one-way.</b> ludodex’s own database is a local SQLite file it reads
          in-process — that’s what makes it fast (microsecond queries, no network). The sync only
          <em> pushes out</em>, so PocketBase/Firestore become read replicas while your local library
          stays the single source of truth. Nothing is pulled back, and enabling this never slows the
          app down.</div>
        <div><b>Why not just run on PocketBase?</b> PocketBase is itself SQLite behind a web API —
          making it the primary store would put a network hop on every query for no real gain. Keeping
          SQLite local and treating PocketBase as a mirror is strictly faster.</div>
        <div><b>How the push works.</b> Each record gets a stable id (a hash of its natural key) and an
          idempotent upsert, tracked by a content-hash cache — so only new/changed/removed records are
          sent, and a re-sync with nothing changed does almost no work. Safe to run repeatedly; it
          creates the collections on first run.</div>
      </div>

      <div className="dbsync-card">
        <div className="dbsync-head">
          <span className="dbsync-name">PocketBase</span>
          <label className="switch">
            <input type="checkbox" checked={st.pb_enabled} onChange={(e) => toggle({ pb_enabled: e.target.checked })} />
            <span className="track"><span className="knob" /></span>
            <span className="switch-text">{st.pb_enabled ? 'On' : 'Off'}</span>
          </label>
        </div>
        <div className="dm-form">
          <label className="dm-field"><span>Server URL</span>
            <input placeholder="https://pb.example.com" value={pb.url} onChange={(e) => setPb({ ...pb, url: e.target.value })} /></label>
          <label className="dm-field"><span>Admin email</span>
            <input value={pb.email} onChange={(e) => setPb({ ...pb, email: e.target.value })} /></label>
          <label className="dm-field"><span>Admin password {st.pocketbase.password_set && <em>(saved — type to replace)</em>}</span>
            <input type="password" autoComplete="new-password" placeholder={st.pocketbase.password_set ? '••••••••' : ''}
              value={pb.password} onChange={(e) => setPb({ ...pb, password: e.target.value })} /></label>
        </div>
        <div className="dbsync-actions">
          <button className="go primary" disabled={busy !== ''} onClick={savePb}>{busy === 'pb-save' ? 'Saving…' : 'Save'}</button>
          <button className="ops-btn" disabled={busy !== ''} onClick={() => test('pocketbase')}>{busy === 'pb-test' ? 'Testing…' : 'Test connection'}</button>
          {msg.pb && <span className="dbsync-msg">{msg.pb}</span>}
        </div>
        {checklist(checks.pb)}
      </div>

      <div className="dbsync-card">
        <div className="dbsync-head">
          <span className="dbsync-name">Firebase Firestore</span>
          <label className="switch">
            <input type="checkbox" checked={st.fb_enabled} onChange={(e) => toggle({ fb_enabled: e.target.checked })} />
            <span className="track"><span className="knob" /></span>
            <span className="switch-text">{st.fb_enabled ? 'On' : 'Off'}</span>
          </label>
        </div>
        <div className="dm-form">
          <label className="dm-field"><span>Project ID</span>
            <input value={fb.project_id} onChange={(e) => setFb({ ...fb, project_id: e.target.value })} /></label>
          <label className="dm-field"><span>Database <em>(usually “(default)”)</em></span>
            <input value={fb.database} onChange={(e) => setFb({ ...fb, database: e.target.value })} /></label>
          <label className="dm-field"><span>Collection prefix <em>(optional)</em></span>
            <input placeholder="ludodex_" value={fb.prefix} onChange={(e) => setFb({ ...fb, prefix: e.target.value })} /></label>
          <label className="dm-field"><span>Service-account key (JSON) {st.firebase.sa_set && <em>(saved — paste to replace)</em>}</span>
            <textarea className="dbsync-sa" rows={4} placeholder={'{ "type": "service_account", … }'}
              value={fb.sa_json} onChange={(e) => setFb({ ...fb, sa_json: e.target.value })} /></label>
        </div>
        <div className="dbsync-actions">
          <button className="go primary" disabled={busy !== ''} onClick={saveFb}>{busy === 'fb-save' ? 'Saving…' : 'Save'}</button>
          <button className="ops-btn" disabled={busy !== ''} onClick={() => test('firebase')}>{busy === 'fb-test' ? 'Testing…' : 'Test connection'}</button>
          {msg.fb && <span className="dbsync-msg">{msg.fb}</span>}
        </div>
        {checklist(checks.fb)}
      </div>

      <div className="dbsync-run">
        <button className="go" onClick={runNow}
          disabled={busy !== '' || (!st.pb_enabled && !st.fb_enabled) || !!job?.running}>
          {job?.running ? 'Syncing…' : 'Sync now'}</button>
        {job && <span className={'dbsync-msg' + (job.ok === false ? ' err' : '')}>
          {job.running ? (job.step || 'Syncing…')
            : job.ok ? `Synced to ${job.target} ✓`
            : job.finished ? `Failed: ${job.error || 'see server log'}` : ''}</span>}
        {msg.run && <span className="dbsync-msg err">{msg.run}</span>}
        {(!st.pb_enabled && !st.fb_enabled) && <span className="dim">Enable a target above to sync.</span>}
      </div>
    </>
  )
}

function CfAccessPanel() {
  const [st, setSt] = useState<CfAccessState | null>(null)
  const [err, setErr] = useState('')
  const [team, setTeam] = useState('')
  const [aud, setAud] = useState('')
  const [saved, setSaved] = useState(false)
  const [nm, setNm] = useState<{ email: string; user_id: number }>({ email: '', user_id: 0 })

  const load = () => api.cfAccess()
    .then((d) => { setSt(d); setTeam(d.team_domain); setAud(d.aud); setErr('') })
    .catch((e) => setErr((e as Error).message))
  useEffect(() => { load() }, [])

  const patch = async (p: Partial<{ enabled: boolean; team_domain: string; aud: string }>) => {
    try { setSt(await api.cfAccessSet(p)); setErr('') } catch (e) { setErr((e as Error).message) }
  }
  const saveCfg = async () => {
    await patch({ team_domain: team.trim().replace(/\/+$/, ''), aud: aud.trim() })
    setSaved(true); setTimeout(() => setSaved(false), 1500)
  }
  const addMap = async (e: FormEvent) => {
    e.preventDefault(); setErr('')
    try {
      const r = await api.cfMapEmail(nm.email.trim(), nm.user_id)
      setSt((s) => s ? { ...s, mappings: r.mappings } : s); setNm({ email: '', user_id: 0 })
    } catch (e) { setErr((e as Error).message) }
  }
  const rm = async (email: string) => {
    try { const r = await api.cfUnmapEmail(email); setSt((s) => s ? { ...s, mappings: r.mappings } : s) }
    catch (e) { setErr((e as Error).message) }
  }

  if (!st) return err ? <div className="connect-msg err">{err}</div> : <div className="loading">Loading…</div>
  return (
    <>
      <h2>Cloudflare Access (SSO)</h2>
      <p className="dim">Put ludodex behind a Cloudflare Access application and users are signed in
        automatically from their Cloudflare identity — no separate ludodex password. ludodex verifies
        the signed Access token (your team’s certificates + this app’s <b>AUD</b> tag), reads the
        authenticated email, and logs the request in as the ludodex user you map that email to below.
        Full setup steps are in <b>CLOUDFLARE.md</b>.</p>
      {err && <div className="connect-msg err">{err}</div>}

      <label className="switch cf-enable">
        <input type="checkbox" checked={st.enabled} onChange={(e) => patch({ enabled: e.target.checked })} />
        <span className="track"><span className="knob" /></span>
        <span className="switch-text">{st.enabled ? 'Enabled' : 'Disabled'}</span>
      </label>

      <div className="cf-cfg">
        <label className="dm-field">
          <span>Team domain</span>
          <input placeholder="yourteam.cloudflareaccess.com" value={team}
            onChange={(e) => setTeam(e.target.value)} />
        </label>
        <label className="dm-field">
          <span>Application Audience (AUD) tag</span>
          <input placeholder="AUD from your Access application" value={aud}
            onChange={(e) => setAud(e.target.value)} />
        </label>
        <div className="cf-cfg-actions">
          <button className="go primary" onClick={saveCfg}>Save</button>
          {saved && <span className="saved">Saved ✓</span>}
        </div>
      </div>

      <h3>Email → user mappings</h3>
      <p className="dim">When Cloudflare presents one of these emails, ludodex signs in as the mapped
        user. You can point several emails at the same user; unmapped emails are simply not signed in.</p>
      <div className="users-list">
        {st.mappings.length === 0 && <div className="sync-note dim">No emails mapped yet.</div>}
        {st.mappings.map((m: CfMapping) => (
          <div key={m.email} className="user-row cf-map-row">
            <span className="cf-email">{m.email}</span>
            <span className="cf-arrow">→</span>
            <span className="cf-target">{m.username}<span className="user-you">{m.role}</span></span>
            <button className="emu-rm" title="Remove mapping" onClick={() => rm(m.email)}>×</button>
          </div>
        ))}
      </div>
      <form className="user-add" onSubmit={addMap}>
        <div className="user-add-title">＋ Map an email to a user</div>
        <div className="user-add-grid">
          <input type="email" placeholder="email@example.com" value={nm.email}
            onChange={(e) => setNm({ ...nm, email: e.target.value })} />
          <select value={nm.user_id} onChange={(e) => setNm({ ...nm, user_id: Number(e.target.value) })}>
            <option value={0}>— pick a user —</option>
            {st.users.map((u) => <option key={u.id} value={u.id}>{u.username}</option>)}
          </select>
          <button className="go primary" type="submit"
            disabled={!nm.email.trim() || !nm.user_id}>Map email</button>
        </div>
      </form>
    </>
  )
}

const MEDIA_LANGUAGES = ['English', 'Japanese', 'French', 'German', 'Spanish',
  'Italian', 'Portuguese', 'Dutch', 'Korean', 'Chinese', 'Russian', 'Polish']
const MEDIA_MODES: { id: MediaMode; name: string; hint: string }[] = [
  { id: 'ondemand', name: 'On demand', hint: 'Keep references and fetch each image the first time it’s shown, then cache it. Lightest on storage; needs the source reachable.' },
  { id: 'chosen', name: 'Download what’s shown', hint: 'On each sync, pull the chosen image per game (cover, logo, …) into ludodex’s own repo — instant, self-contained, works offline. Extra candidates stay on-demand.' },
  { id: 'all', name: 'Download everything', hint: 'Also pull every alternate candidate — a full local archive. Uses the most storage.' },
]

// Settings › Library › Banned media: assets you banned (deleted + never
// re-downloaded). Unban to let the provider supply them again on the next fetch.
function BannedMediaPanel() {
  const [items, setItems] = useState<BannedMedia[] | null>(null)
  const [busy, setBusy] = useState('')
  const load = useCallback(() => api.bannedMedia().then((d) => setItems(d.banned)).catch(() => setItems([])), [])
  useEffect(() => { load() }, [load])
  const unban = async (b: BannedMedia) => {
    setBusy(b.norm_key + b.ref)
    try { await api.unbanMedia(b); await load() } catch { /* */ } finally { setBusy('') }
  }
  if (!items) return <div className="loading">Loading…</div>
  return (
    <div className="banned-media">
      <h3>Banned media</h3>
      <p className="dim">Assets you banned are deleted and never re-downloaded from their
        provider. Unban one to let it come back on the next media fetch.</p>
      {items.length === 0
        ? <div className="sync-note dim">Nothing banned.</div>
        : (
          <div className="bm-list">
            {items.map((b) => (
              <div key={b.norm_key + b.kind + b.provider + b.ref} className="bm-row">
                <span className="bm-title">{b.title}</span>
                <span className="bm-kind">{b.kind.replace(/_/g, ' ')}</span>
                <span className="bm-prov"><ProvTag origin={b.provider} /></span>
                <span className="bm-ref dim" title={b.ref}>{b.ref.replace(/^https?:\/\//, '').slice(0, 48)}</span>
                <button className="ops-btn" disabled={busy === b.norm_key + b.ref}
                  onClick={() => unban(b)}>Unban</button>
              </div>
            ))}
          </div>
        )}
    </div>
  )
}

function LibraryPrefs({ onChanged }: { onChanged: () => void }) {
  const [prefs, setPrefs] = useState<Prefs | null>(null)
  const [busy, setBusy] = useState(false)
  const load = () => api.prefs().then(setPrefs).catch(() => {})
  useEffect(() => { load() }, [])
  const running = prefs?.media_job?.running
  useEffect(() => {
    if (!running) return
    const t = setInterval(() => api.prefs().then(setPrefs).catch(() => {}), 2000)
    return () => clearInterval(t)
  }, [running])

  if (!prefs) return <div className="loading">Loading…</div>
  const job = prefs.media_job

  const setHide = async (v: boolean) => {
    setPrefs({ ...prefs, hide_non_games: v })
    try { await api.setPrefs({ hide_non_games: v }); onChanged() } catch { load() }
  }
  const setMode = async (m: MediaMode) => {
    setPrefs({ ...prefs, media_mode: m })
    try { await api.setPrefs({ media_mode: m }) } catch { load() }
  }
  const setLangAt = async (i: number, v: string) => {
    const slots = [0, 1, 2].map((j) => (prefs.media_languages || [])[j] || '')
    slots[i] = v
    const next = slots.filter(Boolean).filter((x, j, a) => a.indexOf(x) === j)
    setPrefs({ ...prefs, media_languages: next })
    try { await api.setPrefs({ media_languages: next }) } catch { load() }
  }
  const setLangMode = async (m: MediaLangMode) => {
    setPrefs({ ...prefs, media_lang_mode: m })
    try { await api.setPrefs({ media_lang_mode: m }) } catch { load() }
  }
  const [langResult, setLangResult] = useState<MediaLangResult | null>(null)
  const applyLangFilter = async () => {
    setBusy(true)
    try { setLangResult(await api.mediaLanguageFilter()) } finally { setBusy(false) }
  }
  const downloadNow = async () => {
    setBusy(true)
    try { const r = await api.mediaMaterialize(); setPrefs((p) => p ? { ...p, media_job: r.media_job } : p) }
    finally { setBusy(false) }
  }
  const setApplyMode = async (m: FileopsApplyMode) => {
    setPrefs({ ...prefs, fileops_apply_mode: m })
    try { await api.setPrefs({ fileops_apply_mode: m }) } catch { load() }
  }
  const setManifests = async (v: boolean) => {
    setPrefs({ ...prefs, manifests_enabled: v })
    try { await api.setPrefs({ manifests_enabled: v }) } catch { load() }
  }
  const setXbox = async (v: 'xbox' | 'pc') => {
    setPrefs({ ...prefs, xbox_platform: v })
    try { await api.setPrefs({ xbox_platform: v }) } catch { load() }
  }

  return (
    <div className="lib-prefs">
      <div className="pref-row">
        <label className="switch">
          <input type="checkbox" checked={prefs.hide_non_games}
            onChange={(e) => setHide(e.target.checked)} />
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

      <div className="pref-section">
        <div className="pref-name">Media storage</div>
        <span className="pref-hint">When you add a media source, how much art should ludodex pull
          into its own repository? (The repo lives on the media volume you configured.)</span>
        <div className="media-modes">
          {MEDIA_MODES.map((m) => (
            <label key={m.id} className={'media-mode' + (prefs.media_mode === m.id ? ' on' : '')}>
              <input type="radio" name="media_mode" checked={prefs.media_mode === m.id}
                onChange={() => setMode(m.id)} />
              <div className="media-mode-body">
                <div className="media-mode-name">{m.name}
                  {m.id === 'chosen' && <span className="media-mode-tag">recommended</span>}</div>
                <div className="media-mode-hint">{m.hint}</div>
              </div>
            </label>
          ))}
        </div>
        <div className="media-actions">
          <button className="go" disabled={busy || !!job?.running} onClick={downloadNow}>
            {job?.running ? 'Downloading…' : 'Download now'}</button>
          {job && <span className={'dbsync-msg' + (job.ok === false ? ' err' : '')}>
            {job.running ? (job.step || 'Downloading…')
              : job.ok ? `Downloaded ${job.downloaded} asset${job.downloaded === 1 ? '' : 's'}${job.dead ? `, dropped ${job.dead} dead ref${job.dead === 1 ? '' : 's'}` : ''} ✓`
              : job.finished ? `Failed: ${job.error || 'see server log'}` : ''}</span>}
          <span className="pref-hint media-actions-hint">Applies the setting above to your existing library right now.</span>
        </div>
      </div>

      <div className="pref-section">
        <div className="pref-name">Xbox games — platform</div>
        <span className="pref-hint">Xbox is the one store that spans PC and console. Choose which
          platform inbound Xbox games are bucketed as. This only sets the default for a sync — you
          can always mark a game owned on Xbox <em>and</em> PC by hand. Takes effect on the next sync.</span>
        <div className="media-modes">
          {(['xbox', 'pc'] as const).map((v) => (
            <label key={v} className={'media-mode' + (prefs.xbox_platform === v ? ' on' : '')}>
              <input type="radio" name="xbox_platform" checked={prefs.xbox_platform === v}
                onChange={() => setXbox(v)} />
              <div className="media-mode-body">
                <div className="media-mode-name">{v === 'xbox' ? 'Xbox' : 'PC'}
                  {v === 'xbox' && <span className="media-mode-tag">default</span>}</div>
                <div className="media-mode-hint">{v === 'xbox'
                  ? 'Its own Xbox platform entry (keeps Xbox separate from your PC library).'
                  : 'Fold into your PC library (Game Pass / Xbox-app PC titles alongside Steam/GOG).'}</div>
              </div>
            </label>
          ))}
        </div>
      </div>

      <div className="pref-section">
        <div className="pref-name">Media language</div>
        <span className="pref-hint">Your preferred languages for artwork &amp; media (box art, logos,
          manuals), most preferred first. The ✨ smart art picker prefers your 1st language where
          quality is comparable.</span>
        <div className="lang-slots">
          {[0, 1, 2].map((i) => (
            <select key={i} className="pref-select"
              value={(prefs.media_languages || [])[i] || ''}
              onChange={(e) => setLangAt(i, e.target.value)}>
              <option value="">{i === 0 ? 'Any (no preference)' : '— none —'}</option>
              {MEDIA_LANGUAGES.map((l) => <option key={l} value={l}>{l}</option>)}
            </select>
          ))}
        </div>
        <span className="pref-hint" style={{ marginTop: 10 }}>When a media asset is in a language that
          is <em>none</em> of the above:</span>
        <select className="pref-select" value={prefs.media_lang_mode || 'off'}
          onChange={(e) => setLangMode(e.target.value as MediaLangMode)}>
          <option value="off">Keep it (off)</option>
          <option value="hide">Hide it — keep the file, never auto-choose</option>
          <option value="ban">Ban it — delete &amp; never re-download</option>
        </select>
        <span className="pref-hint">Only art we can confidently tie to a single language is affected
          (mostly ScreenScraper box art by region); language-neutral, multi-region and store art is
          always kept. Runs automatically on every sync.</span>
        {prefs.media_lang_mode && prefs.media_lang_mode !== 'off' && (
          <div className="media-actions">
            <button className="go" disabled={busy} onClick={applyLangFilter}>
              {busy ? 'Applying…' : 'Apply to library now'}</button>
            {langResult && (
              <span className="pref-hint media-actions-hint">Scanned {langResult.scanned} ·
                kept {langResult.kept}{langResult.hidden ? ` · hidden ${langResult.hidden}` : ''}
                {langResult.banned ? ` · banned ${langResult.banned}` : ''}</span>
            )}
          </div>
        )}
      </div>

      <div className="pref-section">
        <div className="pref-name">File operations (Browse)</div>
        <span className="pref-hint">When you drag files between panes in the Files → Browse
          commander, should the move/copy be staged for review first, or run the instant you drop?</span>
        <div className="media-modes">
          {([['preview', 'Preview, then apply', 'A drop stages the operation — review source → destination, size and overwrite warnings, then Apply.'],
             ['immediate', 'Immediate', 'A drop runs right away. (Deletes still ask to confirm.)']] as const).map(([id, name, hint]) => (
            <label key={id} className={'media-mode' + ((prefs.fileops_apply_mode || 'preview') === id ? ' on' : '')}>
              <input type="radio" name="fileops_apply_mode" checked={(prefs.fileops_apply_mode || 'preview') === id}
                onChange={() => setApplyMode(id)} />
              <div className="media-mode-body">
                <div className="media-mode-name">{name}
                  {id === 'preview' && <span className="media-mode-tag">default</span>}</div>
                <div className="media-mode-hint">{hint}</div>
              </div>
            </label>
          ))}
        </div>

        <div className="pref-row" style={{ marginTop: 14 }}>
          <label className="switch">
            <input type="checkbox" checked={prefs.manifests_enabled !== false}
              onChange={(e) => setManifests(e.target.checked)} />
            <span className="track"><span className="knob" /></span>
          </label>
          <div className="pref-text">
            <span className="pref-name">Write <code>.ludodex</code> folder manifests</span>
            <span className="pref-hint">
              When ludodex restructures, extracts or indexes a folder, it drops a small hidden
              <code>.ludodex.json</code> at the folder's root recording what layout it follows, how many
              games / files / systems it holds, and where its media lives. Any device that later sees the
              folder reads this instead of re-scanning hundreds of thousands of files — so previews are
              instant and operations already know what's there. It's a validated hint (re-checked cheaply,
              ignored if the folder changed underneath it), is never moved or counted as a game, and holds
              no private info. Turn off if you'd rather ludodex never write into your folders.
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}

const SPOTLIGHT_PRESETS = [5, 8, 12, 20, 30, 45, 60]

function DashboardPrefs({ onChanged }: { onChanged: () => void }) {
  const [secs, setSecs] = useState<number | null>(null)
  const [themes, setThemes] = useState<SpotlightTheme[] | null>(null)
  const [themesOpen, setThemesOpen] = useState(false)
  useEffect(() => { api.prefs().then((p) => setSecs(p.spotlight_seconds)).catch(() => {}) }, [])
  useEffect(() => {
    if (themesOpen && themes === null)
      api.spotlightThemes().then((r) => setThemes(r.themes)).catch(() => setThemes([]))
  }, [themesOpen, themes])

  const commit = async (v: number) => {
    const clamped = Math.max(3, Math.min(300, Math.round(v)))
    setSecs(clamped)
    try { await api.setPrefs({ spotlight_seconds: clamped }); onChanged() }
    catch { /* keep optimistic value */ }
  }

  const toggleTheme = async (id: string, enabled: boolean) => {
    const next = (themes || []).map((t) => t.id === id ? { ...t, enabled } : t)
    setThemes(next)
    const disabled = next.filter((t) => !t.enabled).map((t) => t.id)
    try { await api.setPrefs({ spotlight_disabled: disabled }); onChanged() }
    catch { api.spotlightThemes().then((r) => setThemes(r.themes)).catch(() => {}) }
  }
  const nOff = (themes || []).filter((t) => !t.enabled).length

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
          <input type="range" min={3} max={300} step={1} value={secs}
            onChange={(e) => setSecs(Number(e.target.value))}
            onMouseUp={(e) => commit(Number((e.target as HTMLInputElement).value))}
            onKeyUp={(e) => commit(Number((e.target as HTMLInputElement).value))} />
          <input className="pref-num" type="number" min={3} max={300} value={secs}
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

      <div className="pref-block">
        <button type="button" className="pref-collapse"
          onClick={() => setThemesOpen((o) => !o)}>
          <span className={'caret' + (themesOpen ? ' open' : '')}>▸</span>
          <span className="pref-name">Spotlight categories</span>
          {nOff > 0 && <span className="pref-badge">{nOff} hidden</span>}
        </button>
        {themesOpen && (
          <>
            <div className="pref-hint">
              Turn off any themes you never want on the dashboard (e.g. “Best of
              Neo Geo”). Disabling every theme leaves “Top rated” as a fallback.
            </div>
            {themes === null ? <div className="loading">Loading…</div> : (
              <div className="spot-themes">
                {themes.map((t) => (
                  <label key={t.id}
                    className={'spot-theme' + (t.enabled ? '' : ' off')}>
                    <input type="checkbox" checked={t.enabled}
                      onChange={(e) => toggleTheme(t.id, e.target.checked)} />
                    <span className="spot-theme-title">{t.title}</span>
                    <span className="spot-theme-sub">{t.subtitle}</span>
                  </label>
                ))}
              </div>
            )}
          </>
        )}
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
  useScrollLock()
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
      <div className="add-or-folder">
        <div className="add-or-divider"><span className="add-or-word">OR</span></div>
        <div className="add-or-copy">point to a folder of images on the server</div>
      </div>
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

// Budget currency helpers. Budgets are stored in USD; a chosen currency is a
// display/entry layer converted at a stored FX rate (units of currency per USD).
const CURRENCIES = ['USD', 'EUR', 'GBP', 'CAD', 'AUD', 'JPY', 'INR', 'BRL', 'MXN']
const CUR_SYMBOL: Record<string, string> = { USD: '$', EUR: '€', GBP: '£', CAD: 'C$',
  AUD: 'A$', JPY: '¥', INR: '₹', BRL: 'R$', MXN: 'MX$' }
const curSym = (c: Currency) => CUR_SYMBOL[c.code] || (c.code + ' ')
const money = (usd: number, c: Currency) =>
  curSym(c) + ((usd || 0) * (c.fx || 1)).toLocaleString(undefined,
    { minimumFractionDigits: 2, maximumFractionDigits: 2 })

// A $-budget input shown in the display currency; saves the USD equivalent.
function MoneyInput({ usd, cur, onSave }: { usd: number; cur: Currency; onSave: (usd: number) => void }) {
  const disp = usd ? (usd * (cur.fx || 1)).toFixed(2) : ''
  const [v, setV] = useState(disp)
  useEffect(() => { setV(disp) }, [disp])
  const commit = () => {
    const n = parseFloat((v || '').replace(/[^0-9.]/g, '') || '0')
    const asUsd = n > 0 ? n / (cur.fx || 1) : 0
    if (Math.abs(asUsd - usd) > 1e-9) onSave(asUsd)
  }
  return <input className="cap-input money" inputMode="decimal" placeholder="∞"
    value={v} onChange={(e) => setV(e.target.value)} onBlur={commit}
    onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }} />
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
  const [data, setData] = useState<AiUsageSummary | null>(null)
  const [sel, setSel] = useState<AiUsageModel | null>(null)
  const [series, setSeries] = useState<AiUsageDay[] | null>(null)

  useEffect(() => {
    api.aiUsage().then(setData)
      .catch(() => setData({ models: [], providers: [], currency: { code: 'USD', fx: 1 } }))
  }, [])

  const openSeries = async (m: AiUsageModel) => {
    setSel(m); setSeries(null)
    try { setSeries((await api.aiUsageSeries(m.provider, m.model)).days) } catch { setSeries([]) }
  }

  if (!data) return <div className="loading">Loading…</div>
  const cur = data.currency
  return (
    <>
      <h2>Usage report</h2>
      <p className="dim">Tokens and estimated spend per provider and model — this month and
        lifetime. Spend uses your actual token counts × the model prices; set budgets and
        caps in <b>Budgets &amp; limits</b>. Click a model for its 30-day history.</p>

      {data.models.length === 0 ? (
        <div className="sync-note dim">No AI usage recorded yet — it appears here after
          you run AI features (search, art pick, add-by-image, dedupe).</div>
      ) : (
        <>
          <div className="usage-providers">
            {data.providers.map((p) => (
              <div key={p.provider} className="usage-prov">
                <span className="up-name">{providerName(p.provider)}</span>
                <span className="up-month">{fmtTok(p.month)}<span className="dim"> /mo</span></span>
                <span className="up-cost" title="estimated spend this month">
                  {p.unpriced ? '≥' : ''}{money(p.month_usd, cur)}<span className="dim"> /mo</span></span>
              </div>
            ))}
          </div>

          <div className="usage-list">
            {data.models.map((m) => {
              const on = sel && sel.model === m.model && sel.provider === m.provider
              return (
                <div key={m.provider + '/' + m.model}
                  className={'usage-row' + (on ? ' sel' : '')}
                  onClick={() => openSeries(m)}>
                  <div className="ur-main">
                    <span className="ur-model">{m.model}</span>
                    <span className="ur-prov">{providerName(m.provider)}
                      {m.unpriced && <span className="tag soon" title="No price set — add it in Budgets & limits">no price</span>}</span>
                  </div>
                  <div className="ur-nums">
                    <span title="tokens this month">{fmtTok(m.month)}<span className="dim">/mo</span></span>
                    <span title="spend this month">{m.unpriced ? '—' : money(m.month_usd, cur)}</span>
                    <span className="dim" title="lifetime tokens">{fmtTok(m.total)}</span>
                    <span className="dim" title="calls">{m.calls}×</span>
                  </div>
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

// Budgets & limits — dollar budgets (primary) with token caps (reliable fallback,
// even if prices break), plus the editable price table + display currency.
function AiBudgets() {
  const [caps, setCaps] = useState<AiCap[] | null>(null)
  const [cur, setCur] = useState<Currency>({ code: 'USD', fx: 1 })
  const [prices, setPrices] = useState<AiPrice[]>([])
  const [openProv, setOpenProv] = useState<Set<string>>(new Set())  // providers expanded (collapsed by default)
  const toggleProv = (p: string) =>
    setOpenProv((s) => { const n = new Set(s); n.has(p) ? n.delete(p) : n.add(p); return n })
  const [orEnabled, setOrEnabled] = useState(false)
  const [sched, setSched] = useState<{ daily: boolean; time: string }>({ daily: true, time: '04:00' })
  const [lastUpdate, setLastUpdate] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [resolving, setResolving] = useState(false)
  const [askResolve, setAskResolve] = useState(false)   // Auto-resolve prompt open
  const [resolveNote, setResolveNote] = useState('')    // user's "what's wrong" note
  const [msg, setMsg] = useState('')
  const [fxInput, setFxInput] = useState('')
  // add-a-limit form
  const [scope, setScope] = useState<'provider' | 'model'>('provider')
  const [prov, setProv] = useState('gemini')
  const [modelKey, setModelKey] = useState('')
  const [addUsd, setAddUsd] = useState('')
  // add-a-price form
  const [pProv, setPProv] = useState('gemini')
  const [pModel, setPModel] = useState('')
  const [pIn, setPIn] = useState('')
  const [pOut, setPOut] = useState('')

  const load = () => {
    api.aiLimits().then((d) => setCaps(d.caps)).catch(() => setCaps([]))
    api.aiPrices().then((d) => {
      setPrices(d.prices); setCur(d.currency); setOrEnabled(d.openrouter)
      setSched(d.schedule); setLastUpdate(d.last_update)
    }).catch(() => {})
  }
  const toggleOpenRouter = async (on: boolean) => {
    setOrEnabled(on)
    try { await api.setPricesOpenRouter(on) } catch { setOrEnabled(!on) }
  }
  const saveSchedule = async (daily: boolean, timeStr?: string) => {
    const prev = sched
    setSched({ daily, time: timeStr ?? sched.time })
    try { setSched((await api.setPriceSchedule(daily, timeStr)).schedule) } catch { setSched(prev) }
  }
  useEffect(() => { load() }, [])

  const saveCaps = async (sc: 'global' | 'provider' | 'model', key: string, next: Partial<Caps>) => {
    try { setCaps((await api.setAiLimit(sc, key, next)).caps) }
    catch (e) { setMsg(e instanceof Error ? e.message : 'failed') }
  }
  const addLimit = async () => {
    const key = scope === 'provider' ? prov : modelKey.trim()
    if (!key) return
    const n = parseFloat(addUsd.replace(/[^0-9.]/g, '') || '0')
    await saveCaps(scope, key, { usd: n > 0 ? n / (cur.fx || 1) : 0, total: n > 0 ? 0 : 1 })
    setModelKey(''); setAddUsd('')
  }
  const savePrice = async (p: AiPrice, inUsd: number, outUsd: number, cached: number | null) => {
    try { setPrices((await api.setAiPrice(p.provider, p.model, inUsd, outUsd, cached)).prices) }
    catch (e) { setMsg(e instanceof Error ? e.message : 'failed') }
  }
  const addPrice = async () => {
    if (!pModel.trim()) return
    try {
      setPrices((await api.setAiPrice(pProv, pModel.trim(),
        parseFloat(pIn || '0'), parseFloat(pOut || '0'))).prices)
      setPModel(''); setPIn(''); setPOut('')
    } catch (e) { setMsg(e instanceof Error ? e.message : 'failed') }
  }
  const refresh = async () => {
    setRefreshing(true); setMsg('')
    try { const r = await api.refreshAiPrices(); setPrices(r.prices); setMsg(`Fetched ${r.updated} price(s) — provider-direct rates, no markup.`) }
    catch (e) { setMsg('Refresh failed: ' + (e instanceof Error ? e.message : '')) }
    finally { setRefreshing(false) }
  }
  const doResolve = async (useAi: boolean) => {
    setResolving(true); setMsg(''); setAskResolve(false)
    try {
      const r = await api.resolveAiPrices(useAi, resolveNote)
      setPrices(r.prices)
      const parts: string[] = []
      if (r.fetched) parts.push(`${r.fetched} from feed`)
      if (r.ai_resolved) parts.push(`${r.ai_resolved} by AI`)
      let m = parts.length ? `Resolved ${parts.join(' + ')} price(s)` : 'No prices changed'
      if (useAi && r.targeted === 0) m += ' — everything is already priced; name a specific model or provider in the note to re-price it'
      if (r.still_missing) m += ` — ${r.still_missing} still unpriced`
      if (r.ai_error) m += ` · AI error: ${r.ai_error}`
      setMsg(m)
      setResolveNote('')
    } catch (e) { setMsg('Resolve failed: ' + (e instanceof Error ? e.message : '')) }
    finally { setResolving(false) }
  }
  const changeCurrency = async (code: string) => {
    try { setCur((await api.setCurrency(code, code === 'USD' ? 1 : (cur.code === code ? cur.fx : undefined))).currency) }
    catch { /* */ }
  }
  const saveFx = async () => {
    const n = parseFloat(fxInput.replace(/[^0-9.]/g, '') || '0')
    if (n > 0) { try { setCur((await api.setCurrency(cur.code, n)).currency); setFxInput('') } catch { /* */ } }
  }

  if (!caps) return <div className="loading">Loading…</div>
  return (
    <>
      <h2>Budgets &amp; limits</h2>
      <p className="dim">Set a <b>monthly $ budget</b> globally (all AI combined) and/or per
        provider or model — spend is your real token counts (from each API response) × the
        prices below. Token caps are a reliable fallback that keep working even if a price is
        missing. <b>Any</b> cap being hit — global, provider, or model — stops further calls
        that month.</p>

      <div className="cur-row">
        <span className="cur-label">Currency</span>
        <select value={cur.code} onChange={(e) => changeCurrency(e.target.value)}>
          {CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        {cur.code !== 'USD' && (
          <label className="cur-fx">1&nbsp;USD =
            <input inputMode="decimal" placeholder={String(cur.fx)} value={fxInput}
              onChange={(e) => setFxInput(e.target.value)} onBlur={saveFx}
              onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }} />
            {cur.code}</label>
        )}
        {cur.code !== 'USD' && <span className="dim cur-note">≈ approx — you’re billed in USD.</span>}
      </div>

      <div className="budgets">
        {(() => {
          const g = caps.find((c) => c.scope === 'global')
          const gc = g?.caps ?? { total: 0, usd: 0, input: 0, output: 0 }
          const gu = g?.used ?? { total: 0, input: 0, output: 0, usd: 0, unpriced: false }
          return (
            <div className="budget-row global-budget">
              <div className="br-head">
                <span className="cap-scope global">global</span>
                <span className="cap-key">All AI — every provider &amp; model combined</span>
              </div>
              <div className="br-fields">
                <label className="br-field"><span>Budget /mo ({curSym(cur).trim()})</span>
                  <MoneyInput usd={gc.usd} cur={cur} onSave={(usd) => saveCaps('global', 'all', { ...gc, usd })} />
                  <span className="br-used dim">{gu.unpriced ? '≥' : ''}{money(gu.usd, cur)} used</span></label>
                <label className="br-field"><span>Total tokens /mo</span>
                  <CapInput value={gc.total} onSave={(total) => saveCaps('global', 'all', { ...gc, total })} />
                  <span className="br-used dim">{fmtTok(gu.total)} used</span></label>
                <label className="br-field"><span>Input tokens /mo</span>
                  <CapInput value={gc.input} onSave={(input) => saveCaps('global', 'all', { ...gc, input })} />
                  <span className="br-used dim">{fmtTok(gu.input)} used</span></label>
                <label className="br-field"><span>Output tokens /mo</span>
                  <CapInput value={gc.output} onSave={(output) => saveCaps('global', 'all', { ...gc, output })} />
                  <span className="br-used dim">{fmtTok(gu.output)} used</span></label>
              </div>
              {gc.usd > 0 && gu.unpriced &&
                <div className="br-warn dim">⚠ Some usage has no price set — the $ budget can’t be enforced, but token caps still apply.</div>}
            </div>
          )
        })()}
        <div className="budgets-sub dim">Per-provider &amp; per-model caps (optional — apply on top of the global budget)</div>
        {caps.filter((c) => c.scope !== 'global').length === 0 && <div className="sync-note dim">No per-provider/model caps yet — add one below.</div>}
        {caps.filter((c) => c.scope !== 'global').map((c) => (
          <div key={c.scope + '/' + c.key} className="budget-row">
            <div className="br-head">
              <span className={'cap-scope ' + c.scope}>{c.scope}</span>
              <span className="cap-key">{c.scope === 'provider' ? providerName(c.key) : c.key}</span>
              <button className="emu-rm" title="Remove all caps for this"
                onClick={() => saveCaps(c.scope, c.key, { total: 0, usd: 0, input: 0, output: 0 })}>×</button>
            </div>
            <div className="br-fields">
              <label className="br-field"><span>Budget /mo ({curSym(cur).trim()})</span>
                <MoneyInput usd={c.caps.usd} cur={cur} onSave={(usd) => saveCaps(c.scope, c.key, { ...c.caps, usd })} />
                <span className="br-used dim">{c.used.unpriced ? '≥' : ''}{money(c.used.usd, cur)} used</span></label>
              <label className="br-field"><span>Total tokens /mo</span>
                <CapInput value={c.caps.total} onSave={(total) => saveCaps(c.scope, c.key, { ...c.caps, total })} />
                <span className="br-used dim">{fmtTok(c.used.total)} used</span></label>
              <label className="br-field"><span>Input tokens /mo</span>
                <CapInput value={c.caps.input} onSave={(input) => saveCaps(c.scope, c.key, { ...c.caps, input })} />
                <span className="br-used dim">{fmtTok(c.used.input)} used</span></label>
              <label className="br-field"><span>Output tokens /mo</span>
                <CapInput value={c.caps.output} onSave={(output) => saveCaps(c.scope, c.key, { ...c.caps, output })} />
                <span className="br-used dim">{fmtTok(c.used.output)} used</span></label>
            </div>
            {c.caps.usd > 0 && c.used.unpriced &&
              <div className="br-warn dim">⚠ Some usage here has no price set — the $ budget can’t be enforced, but the token caps still apply.</div>}
          </div>
        ))}
        <div className="budget-add">
          <select value={scope} onChange={(e) => setScope(e.target.value as 'provider' | 'model')}>
            <option value="provider">Provider</option>
            <option value="model">Model</option>
          </select>
          {scope === 'provider'
            ? <select value={prov} onChange={(e) => setProv(e.target.value)}>
                {Object.keys(PROVIDER_LABELS).map((p) => <option key={p} value={p}>{providerName(p)}</option>)}
              </select>
            : <input placeholder="model id (e.g. gemini-3.5-flash)" value={modelKey}
                onChange={(e) => setModelKey(e.target.value)} />}
          <input className="cap-num" inputMode="decimal" placeholder={`budget ${curSym(cur).trim()}/mo`}
            value={addUsd} onChange={(e) => setAddUsd(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') addLimit() }} />
          <button className="ops-btn" onClick={addLimit}>＋ add</button>
        </div>
      </div>

      <div className="price-panel">
        <div className="pp-head">Model prices <span className="dim">(USD per 1M tokens, per provider)</span>
          {orEnabled && (
            <button className="ops-btn" disabled={refreshing || resolving} onClick={refresh}
              title="Looks up each model's current per-token price from the public OpenRouter model catalog — a pass-through of each provider's OWN published rates (no markup), so the numbers equal calling the provider directly. Never changes your manual edits or how your calls are routed.">
              {refreshing ? '↻ Fetching…' : '↻ Fetch current prices'}</button>
          )}
          <button className="ops-btn" disabled={refreshing || resolving}
            onClick={() => setAskResolve((v) => !v)}
            title="Auto-resolve prices — pulls the price feed, and can ask your AI to price models the feed can't (renamed, deprecated or brand-new).">
            {resolving ? '✨ Resolving…' : '✨ Auto-resolve'}</button>
        </div>
        {askResolve && (
          <div className="pp-resolve">
            <div className="ppr-q">Auto-resolve model prices. Optionally tell the AI what's wrong so it
              focuses on it — this is added to the model-price prompt.</div>
            <textarea className="ppr-note" rows={2} value={resolveNote}
              onChange={(e) => setResolveNote(e.target.value)}
              placeholder="Optional — e.g. “gemini-3.1-pro shows no price” or “claude-opus-4-8 looks deprecated, use its current rate”" />
            <div className="ppr-actions">
              <button className="go" disabled={resolving} onClick={() => doResolve(true)}
                title="Pull the feed, then have your AI price whatever's still missing (uses your configured AI + the note above).">✨ Resolve with AI</button>
              <button className="ops-btn" disabled={resolving || !orEnabled} onClick={() => doResolve(false)}
                title={orEnabled ? 'Pull the OpenRouter feed only — no AI.' : 'Enable the OpenRouter feed below to use feed-only.'}>Feed only</button>
              <button className="ops-btn ppr-cancel" disabled={resolving} onClick={() => setAskResolve(false)}>Cancel</button>
            </div>
            <div className="dim ppr-hint">AI-set prices are best-effort (a model may not know exact rates) and
              are marked <em>ai</em> — review them, and edit any to lock it as <em>manual</em>.</div>
          </div>
        )}
        <div className="pp-note dim">Your calls go <b>direct to each provider</b> — these are just
          the rates used to turn tokens into dollars. ludodex ships <b>native per-provider prices</b>
          (from each provider’s own pricing page) marked <em>default</em>. Edit any rate to override
          it (marked <em>manual</em>, never overwritten). A model with no price shows spend as “—”
          and can’t enforce a $ budget — use a token cap there.</div>
        <label className="switch pp-or">
          <input type="checkbox" checked={orEnabled} onChange={(e) => toggleOpenRouter(e.target.checked)} />
          <span className="track"><span className="knob" /></span>
          <span className="switch-text pp-or-txt">Also allow fetching prices from the OpenRouter catalog
            <span className="dim"> — off by default. It lists each provider’s own rates with no markup, but you don’t need it; native defaults + your edits already cover pricing.</span></span>
        </label>
        {orEnabled && (
          <div className="pp-sched">
            <label className="switch pp-sched-on">
              <input type="checkbox" checked={sched.daily} onChange={(e) => saveSchedule(e.target.checked)} />
              <span className="track"><span className="knob" /></span>
              <span className="switch-text">Auto-update prices daily</span>
            </label>
            {sched.daily && (
              <label className="pp-sched-time">at
                <input type="time" value={sched.time} onChange={(e) => saveSchedule(true, e.target.value)} />
                <span className="dim">server time{lastUpdate ? ` · last ran ${lastUpdate}` : ''}</span>
              </label>
            )}
            <div className="pp-sched-note dim">Refreshes only when you have budgets/limits set — no point otherwise.</div>
          </div>
        )}
        {msg && <div className="pp-msg dim">{msg}</div>}
        <div className="price-list">
          <div className="price-row phead"><span>Model</span><span>Input</span><span>Output</span><span>Cached</span><span>Src</span><span /></div>
          {Object.entries(prices.reduce((g, p) => { (g[p.provider] ||= []).push(p); return g }, {} as Record<string, AiPrice[]>))
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([provider, rows]) => {
              const open = openProv.has(provider)
              return (
                <Fragment key={provider}>
                  <button className={'pr-provider' + (open ? ' open' : '')}
                    onClick={() => toggleProv(provider)}>
                    <span className={'pr-chev' + (open ? ' open' : '')}>▸</span>
                    <span className="pr-provider-name">{providerName(provider)}</span>
                    <span className="pr-count">{rows.length}</span>
                  </button>
                  {open && rows.map((p) => <PriceRow key={p.provider + '/' + p.model} p={p} onSave={savePrice} />)}
                </Fragment>
              )
            })}
        </div>
        <div className="price-add">
          <select value={pProv} onChange={(e) => setPProv(e.target.value)}>
            {Object.keys(PROVIDER_LABELS).map((p) => <option key={p} value={p}>{providerName(p)}</option>)}
          </select>
          <input placeholder="model id" value={pModel} onChange={(e) => setPModel(e.target.value)} />
          <input className="price-num" inputMode="decimal" placeholder="in $/1M" value={pIn} onChange={(e) => setPIn(e.target.value)} />
          <input className="price-num" inputMode="decimal" placeholder="out $/1M" value={pOut} onChange={(e) => setPOut(e.target.value)} />
          <button className="ops-btn" onClick={addPrice}>＋ add price</button>
        </div>
      </div>
    </>
  )
}

function PriceRow({ p, onSave }: { p: AiPrice; onSave: (p: AiPrice, i: number, o: number, c: number | null) => void }) {
  const [i, setI] = useState(p.in_usd != null ? String(p.in_usd) : '')
  const [o, setO] = useState(p.out_usd != null ? String(p.out_usd) : '')
  const [c, setC] = useState(p.cached_usd != null ? String(p.cached_usd) : '')
  useEffect(() => {
    setI(p.in_usd != null ? String(p.in_usd) : '')
    setO(p.out_usd != null ? String(p.out_usd) : '')
    setC(p.cached_usd != null ? String(p.cached_usd) : '')
  }, [p.in_usd, p.out_usd, p.cached_usd])
  const commit = () => {
    const iv = parseFloat(i || '0'), ov = parseFloat(o || '0')
    const cv = c.trim() === '' ? null : parseFloat(c)
    if (iv !== (p.in_usd ?? 0) || ov !== (p.out_usd ?? 0) || cv !== (p.cached_usd ?? null)) onSave(p, iv, ov, cv)
  }
  const num = (val: string, set: (s: string) => void) =>
    <input className="price-num" inputMode="decimal" value={val} onBlur={commit}
      onChange={(e) => set(e.target.value)}
      onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }} />
  return (
    <div className="price-row">
      <span className="pr-model" title={p.provider}>{p.model}</span>
      {num(i, setI)}{num(o, setO)}{num(c, setC)}
      <span className={'pr-src ' + p.source} title={p.source === 'manual' ? 'your manual override' : p.source === 'openrouter' ? 'auto-fetched provider-direct rate' : p.source === 'ai' ? 'resolved by AI — best-effort, review it' : 'shipped default'}>{p.source === 'manual' ? 'manual' : p.source === 'openrouter' ? 'auto' : p.source}</span>
      <span />
    </div>
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

// Modality badges shown on AI functions — vision (analyzes images) / data (works
// over the catalog & text). Reused in the legend, the two default rows, and each
// function row so the badge a user sees is literally the same component everywhere.
const VisionBadge = () => <span className="tag vision">vision</span>
const DataBadge = () => <span className="tag data">data</span>

function AiUsage({ cfg, onChange }: { cfg: AiConfig; onChange: () => void }) {
  const [dedupeOpen, setDedupeOpen] = useState(false)
  const [promptOpen, setPromptOpen] = useState<string | null>(null)
  const [areaOpen, setAreaOpen] = useState<Set<string>>(new Set())
  const toggleArea = (id: string) =>
    setAreaOpen((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n })
  const [areaQuery, setAreaQuery] = useState('')
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
      <p className="dim ai-badge-legend">
        The badges tell you what a function does: <VisionBadge /> means it analyzes
        images (so it uses the <em>Image analysis</em> default), <DataBadge /> means it
        works over your catalog &amp; text. A function that does both shows both.
      </p>

      <div className="default-row">
        <span className="dr-label">Global default <span className="dr-paren">(<DataBadge />)</span></span>
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
        <span className="dr-label">Image analysis <span className="dr-paren">(<VisionBadge />)</span></span>
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

      {(() => {
        const q = areaQuery.trim().toLowerCase()
        const shown = q
          ? cfg.areas.filter((a) => (a.name + ' ' + a.description + ' ' + a.id)
              .toLowerCase().includes(q))
          : cfg.areas
        return (
      <>
      <div className="area-search">
        <input type="search" placeholder="🔍 Search AI functions…" value={areaQuery}
          onChange={(e) => setAreaQuery(e.target.value)} />
        {q && <span className="area-search-count">{shown.length} of {cfg.areas.length}</span>}
      </div>
      <div className="usage-areas">
        {shown.length === 0 && (
          <div className="area-none">No AI functions match “{areaQuery}”.</div>
        )}
        {shown.map((a) => {
          const dfltProv = a.vision ? cfg.vision_default.provider : cfg.default.provider
          const effProv = a.assigned ?? dfltProv
          const rowOpen = areaOpen.has(a.id)
          const pOpen = promptOpen === a.id
          const provLabel = a.assigned ? providerName(a.assigned) : `Default · ${providerName(dfltProv)}`
          const modelLabel = a.assigned_model || a.effective_model || 'model'
          return (
            <div key={a.id} className={'area-row' + (rowOpen ? ' open' : '')}>
              <div className="area-head" onClick={() => toggleArea(a.id)}>
                <span className={'sync-chev' + (rowOpen ? ' open' : '')}>▸</span>
                <span className="area-name">{a.name}
                  {a.vision && <VisionBadge />}
                  {a.data && <DataBadge />}
                  {a.prompt && <span className="tag soon">custom prompt</span>}
                  {a.status !== 'live' && <span className="tag soon">{a.status}</span>}</span>
                <span className="area-summary dim">{provLabel} · {modelLabel}</span>
              </div>
              {rowOpen && (
                <div className="area-body">
                  <div className="area-desc">{a.description}</div>
                  <div className="area-selects">
                    <label className="area-sel"><span>Provider</span>
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
                    </label>
                    <label className="area-sel"><span>Model</span>
                      <ModelInput models={a.vision ? visionFor(effProv) : modelsFor(effProv)}
                        value={a.assigned_model ?? ''}
                        placeholder={a.effective_model ?? 'model'}
                        onSave={(m) => setArea(a.id, a.assigned ?? '', m)} />
                    </label>
                  </div>
                  <div className="area-btns">
                    <button className="link-btn" onClick={() => setPromptOpen(pOpen ? null : a.id)}>
                      {pOpen ? '▾ Hide prompt' : '✎ Edit prompt'}</button>
                    {a.id === 'dedupe' && (
                      <button className="run-btn" onClick={() => setDedupeOpen(true)}>▶ Run dedupe assist</button>
                    )}
                  </div>
                  {pOpen && <AreaPromptEditor area={a} onSave={(p) => savePrompt(a.id, p)} />}
                </div>
              )}
            </div>
          )
        })}
      </div>
      </>
      )
      })()}

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
      <div className="settings-actions sticky-actions">
        <button className="go" disabled={saving} onClick={save}>{saving ? 'Saving…' : 'Save keys'}</button>
        {saved && <span className="saved">Saved ✓</span>}
      </div>
    </>
  )
}

type LmKinds = Record<string, [string, boolean, boolean]>

// Path field with directory autocomplete. Lists the child folders of whatever
// directory the text is currently in (on the device — local container FS or SSH)
// and filters them by the trailing partial name; click one to descend.
function PathInput({ deviceId, value, onChange, placeholder }: {
  deviceId: number; value: string; onChange: (v: string) => void; placeholder?: string
}) {
  const [dirs, setDirs] = useState<string[]>([])
  const [openList, setOpenList] = useState(false)
  const [err, setErr] = useState('')
  const seq = useRef(0)

  const slash = value.lastIndexOf('/')
  const dir = slash >= 0 ? value.slice(0, slash + 1) : ''       // directory to list
  const frag = slash >= 0 ? value.slice(slash + 1) : value      // partial name to match

  const listDir = useCallback(async (d: string) => {
    const id = ++seq.current
    try {
      const r = await api.browseDevice(deviceId, d || '/')
      if (id !== seq.current) return
      if (r.ok) { setDirs(r.dirs); setErr('') } else { setDirs([]); setErr(r.error || 'cannot list') }
    } catch (e) { if (id === seq.current) { setDirs([]); setErr((e as Error).message) } }
  }, [deviceId])

  useEffect(() => {
    if (!openList) return
    const t = setTimeout(() => listDir(dir || '/'), 180)
    return () => clearTimeout(t)
  }, [dir, openList, listDir])

  const shown = dirs.filter((d) => d.toLowerCase().startsWith(frag.toLowerCase()))
  const choose = (name: string) => {
    const next = (dir || '/') + name + '/'
    onChange(next); listDir(next)
  }

  return (
    <div className="pathinput">
      <input placeholder={placeholder} value={value} autoComplete="off" spellCheck={false}
        onChange={(e) => { onChange(e.target.value); setOpenList(true) }}
        onFocus={() => { setOpenList(true); listDir(dir || '/') }}
        onBlur={() => setTimeout(() => setOpenList(false), 150)} />
      {openList && (shown.length > 0 || err) && (
        <div className="pathinput-list">
          {err
            ? <div className="pathinput-err">{err}</div>
            : shown.slice(0, 50).map((d) => (
              <button type="button" key={d} className="pathinput-item"
                onMouseDown={(e) => { e.preventDefault(); choose(d) }}>{d}/</button>
            ))}
        </div>
      )}
    </div>
  )
}

// Add OR edit a library manager (a rom/media folder on a device). `existing`
// prefills the form and switches it to update-in-place (backend UPDATEs by id).
function ManagerModal({ deviceId, deviceName, kinds, existing, onClose, onSaved }: {
  deviceId: number; deviceName: string; kinds: [string, [string, boolean, boolean]][]
  existing?: LibraryManager; onClose: () => void; onSaved: (d: { devices: Device[] }) => void
}) {
  useScrollLock()
  const [kind, setKind] = useState(existing?.kind || kinds[0]?.[0] || 'roms')
  const [name, setName] = useState(existing?.name || '')
  const [rom, setRom] = useState(existing?.rom_path || '')
  const [media, setMedia] = useState(existing?.media_path || '')
  const [mkinds, setMkinds] = useState<MediaKind[]>([])
  const [pick, setPick] = useState<Set<string>>(new Set(existing?.media_kinds || []))   // empty = all types
  const [busy, setBusy] = useState(false)
  useEffect(() => { api.mediaKinds().then((d) => setMkinds(d.kinds)).catch(() => {}) }, [])
  const caps = kinds.find(([k]) => k === kind)?.[1]
  const doesRoms = caps ? caps[1] : true
  const doesMedia = caps ? caps[2] : false
  const kindLabel = caps ? caps[0] : kind
  const togglePick = (k: string) =>
    setPick((p) => { const n = new Set(p); n.has(k) ? n.delete(k) : n.add(k); return n })
  const save = async () => {
    setBusy(true)
    try {
      onSaved(await api.setManager({
        ...(existing ? { id: existing.id } : {}),
        device_id: deviceId, kind, name,
        rom_path: doesRoms ? rom : '', media_path: doesMedia ? media : '',
        media_kinds: doesMedia ? Array.from(pick) : [],
      }))
      onClose()
    } finally { setBusy(false) }
  }
  return (
    <div className="overlay overlay-2" onClick={onClose}>
      <div className="panel dm-panel" onClick={(e) => e.stopPropagation()}>
        <button className="close" onClick={onClose}>×</button>
        <h2>{existing ? `Edit ${existing.name || kindLabel}` : `Add to ${deviceName}`}</h2>
        <p className="dim">A <b>library manager</b> is a folder on this device that holds ROMs
          or downloaded media (RetroDECK/ES-DE, RetroBat, Playnite, LaunchBox, or a raw folder).</p>
        <div className="dm-form">
          <label className="dm-field">
            <span>What is it?</span>
            <select value={kind} onChange={(e) => setKind(e.target.value)}>
              {kinds.map(([k, v]) => <option key={k} value={k}>{v[0]}</option>)}
            </select>
          </label>
          <label className="dm-field">
            <span>Label <em>(optional)</em></span>
            <input placeholder={kindLabel} value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          {doesRoms && (
            <label className="dm-field">
              <span>ROM path {doesMedia && <em>on device</em>}</span>
              <PathInput deviceId={deviceId} value={rom} onChange={setRom} placeholder="/path/to/roms" />
            </label>
          )}
          {doesMedia && (
            <label className="dm-field">
              <span>Media path {doesRoms && <em>(optional)</em>}</span>
              <PathInput deviceId={deviceId} value={media} onChange={setMedia} placeholder="/path/to/downloaded_media" />
            </label>
          )}
        </div>
        {kind === 'media' && (
          <div className="emu-kinds">
            <div className="emu-kinds-head">
              Media types in this folder
              <span className="emu-kinds-note">
                {pick.size === 0 ? 'all types (nothing selected = everything)' : `${pick.size} selected`}</span>
              <button className="emu-kinds-clear" onClick={() => setPick(new Set(mkinds.map((k) => k.kind)))}>Select all</button>
              {pick.size > 0 && <button className="emu-kinds-clear" onClick={() => setPick(new Set())}>Reset to all</button>}
            </div>
            <div className="emu-kinds-grid">
              {mkinds.map((k) => (
                <label key={k.kind} className={'emu-kchip' + (pick.has(k.kind) ? ' on' : '')} title={k.description}>
                  <input type="checkbox" checked={pick.has(k.kind)} onChange={() => togglePick(k.kind)} />
                  {k.kind.replace(/_/g, ' ')}
                </label>
              ))}
            </div>
          </div>
        )}
        <div className="dm-actions">
          <button className="ops-btn" onClick={onClose}>Cancel</button>
          <button className="go primary" disabled={busy} onClick={save}>
            {busy ? 'Saving…' : existing ? 'Save changes' : 'Add'}</button>
        </div>
      </div>
    </div>
  )
}

function AddManager({ deviceId, deviceName, kinds, onAdded }: {
  deviceId: number; deviceName: string; kinds: [string, [string, boolean, boolean]][]
  onAdded: (d: { devices: Device[] }) => void
}) {
  const [open, setOpen] = useState(false)
  return open
    ? <ManagerModal deviceId={deviceId} deviceName={deviceName} kinds={kinds}
        onClose={() => setOpen(false)} onSaved={onAdded} />
    : <button className="dm-add-btn" onClick={() => setOpen(true)}
        title="Add a library manager to this device">＋ Add library manager</button>
}

type DevForm = {
  id?: number; name: string; transport: string; host: string; port: number
  username: string; auth: string; key_path: string; password: string; share: string
}

// Shared add/edit form for a device. On update (initial.id set) a blank password
// keeps the stored one, and unspecified fields are merged server-side.
function DeviceForm({ initial, submitLabel, hasPassword, onSaved, onCancel }: {
  initial: DevForm; submitLabel: string; hasPassword?: boolean
  onSaved: (d: { devices: Device[] }) => void; onCancel?: () => void
}) {
  const [f, setF] = useState<DevForm>(initial)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const up = (k: keyof DevForm, v: string | number) => setF((p) => ({ ...p, [k]: v }))
  const save = async () => {
    if (!f.name.trim()) return
    setBusy(true); setErr('')
    try { onSaved(await api.setDevice(f)); if (!initial.id) setF(initial) }
    catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  return (
    <>
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
          {f.auth === 'password' && <input type="password" autoComplete="off"
            placeholder={hasPassword ? 'password (blank = keep current)' : 'password'}
            value={f.password} onChange={(e) => up('password', e.target.value)} />}
          <input className="dev-port" type="number" placeholder="port" value={f.port} onChange={(e) => up('port', Number(e.target.value) || 22)} />
        </>}
        <button className="go primary" disabled={busy || !f.name.trim()} onClick={save}>{busy ? 'Saving…' : submitLabel}</button>
        {onCancel && <button className="ops-btn dev-cancel" onClick={onCancel}>Cancel</button>}
      </div>
      {err && <div className="connect-msg err">{err}</div>}
    </>
  )
}

function AddDevice({ onAdded }: { onAdded: (d: { devices: Device[] }) => void }) {
  const blank: DevForm = { name: '', transport: 'ssh', host: '', port: 22, username: '', auth: 'alias', key_path: '', password: '', share: '' }
  return (
    <div className="dev-add">
      <div className="dev-add-title">＋ Add a device</div>
      <DeviceForm initial={blank} submitLabel="Add device" onSaved={onAdded} />
    </div>
  )
}

function DevicesPanel() {
  const [data, setData] = useState<{ devices: Device[]; lm_kinds: LmKinds } | null>(null)
  const [test, setTest] = useState<Record<number, { ok: boolean; detail: string }>>({})
  const [sync, setSync] = useState<Record<number, { device?: string; results?: { manager: string; ok: boolean; roms?: number; media?: string; error?: string }[]; error?: string }>>({})
  const [busy, setBusy] = useState<number | null>(null)
  const [editing, setEditing] = useState<number | null>(null)
  const [editMgr, setEditMgr] = useState<number | null>(null)   // library-manager id being edited
  const [wantCounts, setWantCounts] = useState<Record<string, number>>({})
  const [wants, setWants] = useState<Record<number, GameRow[]>>({})
  const [wantsOpen, setWantsOpen] = useState<number | null>(null)
  useEffect(() => { api.wantsSummary().then((w) => setWantCounts(w.counts)).catch(() => {}) }, [])
  const toggleWants = async (id: number) => {
    if (wantsOpen === id) { setWantsOpen(null); return }
    setWantsOpen(id)
    if (!wants[id]) {
      try { const r = await api.deviceWants(id); setWants((w) => ({ ...w, [id]: r.wants })) } catch { /* */ }
    }
  }
  const removeWant = async (id: number, nk: string) => {
    await api.removeWant(id, nk).catch(() => {})
    setWants((w) => ({ ...w, [id]: (w[id] || []).filter((g) => g.norm_key !== nk) }))
    setWantCounts((c) => ({ ...c, [id]: Math.max(0, (c[id] || 1) - 1) }))
  }

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
              <button className="ops-btn" onClick={() => setEditing(editing === d.id ? null : d.id)}>{editing === d.id ? 'Close' : 'Edit'}</button>
              <button className="emu-rm" title="Remove device" onClick={async () => apply(await api.removeDevice(d.id))}>×</button>
            </div>
          </div>
          {editing === d.id && (
            <div className="dev-edit">
              <DeviceForm submitLabel="Save changes" hasPassword={d.has_password}
                initial={{ id: d.id, name: d.name, transport: d.transport, host: d.host,
                  port: d.port || 22, username: d.username, auth: d.auth,
                  key_path: d.key_path, password: '', share: d.share }}
                onSaved={(x) => { apply(x); setEditing(null) }}
                onCancel={() => setEditing(null)} />
            </div>
          )}
          {test[d.id] && <div className={'connect-msg ' + (test[d.id].ok ? 'ok' : 'err')}>{test[d.id].ok ? '✓ ' : '✗ '}{test[d.id].detail}</div>}
          {sync[d.id] && (sync[d.id].error
            ? <div className="connect-msg err">{sync[d.id].error}</div>
            : sync[d.id].results && <div className="dev-sync">{sync[d.id].results!.map((r, i) =>
                <div key={i} className={r.ok ? 'ok' : 'err'}>{r.ok ? '✓' : '✗'} {r.manager}: {r.ok ? `${r.roms ?? 0} roms${r.media ? ' + media' : ''}` : r.error}</div>)}</div>)}
          <div className="dev-mgrs">
            {d.managers.map((m) => (
              <div key={m.id} className="dev-mgr">
                <span className="dm-kind">{m.kind_label}</span>
                <span className="dm-name">{m.name || m.kind}</span>
                <code className="dm-path">{[m.rom_path && 'ROMs: ' + m.rom_path, m.media_path && 'Media: ' + m.media_path].filter(Boolean).join('   ·   ') || '(no paths set)'}</code>
                {m.media_path && <span className="dm-mkinds">{m.media_kinds && m.media_kinds.length ? m.media_kinds.map((k) => k.replace(/_/g, ' ')).join(', ') : 'all media types'}</span>}
                <button className="dm-edit" title="Edit paths / settings" onClick={() => setEditMgr(m.id)}>✎</button>
                <button className="emu-rm" title="Remove" onClick={async () => apply(await api.removeManager(m.id))}>×</button>
                {editMgr === m.id && (
                  <ManagerModal existing={m} deviceId={d.id} deviceName={d.name} kinds={kinds}
                    onClose={() => setEditMgr(null)} onSaved={(x) => { apply(x); setEditMgr(null) }} />
                )}
              </div>
            ))}
            <AddManager deviceId={d.id} deviceName={d.name} kinds={kinds} onAdded={apply} />
          </div>
          {(wantCounts[d.id] > 0 || wants[d.id]) && (
            <div className="dev-wants">
              <button className="dev-wants-head" onClick={() => toggleWants(d.id)}>
                <span className={'sync-chev' + (wantsOpen === d.id ? ' open' : '')}>▸</span>
                Wishlist
                <span className="dim">{wantCounts[d.id] ?? (wants[d.id]?.length || 0)} game{(wantCounts[d.id] ?? 0) === 1 ? '' : 's'} wanted here</span>
              </button>
              {wantsOpen === d.id && (
                <div className="dev-wants-list">
                  {(wants[d.id] || []).length === 0
                    ? <div className="sync-note dim">Nothing yet — pick games in the library (Select → Add to device).</div>
                    : (wants[d.id] || []).map((g) => (
                      <div key={g.norm_key} className="dev-want">
                        <span className="dw-title">{g.title}</span>
                        <span className="dw-plat dim">{g.platforms}</span>
                        <button className="emu-rm" title="Remove from wishlist"
                          onClick={() => removeWant(d.id, g.norm_key)}>×</button>
                      </div>
                    ))}
                </div>
              )}
            </div>
          )}
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
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const toggle = (id: string) =>
    setExpanded((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n })
  const svcStatus = (s: Service): { ok: boolean; text: string } => {
    if (s.connect) return s.connect.connected ? { ok: true, text: '✓ connected' } : { ok: false, text: 'not connected' }
    const fs = s.fields || []
    if (!fs.length) return { ok: true, text: '' }
    const set = fs.filter((f) => f.configured).length
    if (set === fs.length) return { ok: true, text: '✓ set' }
    if (set > 0) return { ok: false, text: `${set}/${fs.length} set` }
    return { ok: false, text: 'not set' }
  }

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
            {svcs.map((s) => {
              const open = expanded.has(s.id)
              const st = svcStatus(s)
              return (
              <div key={s.id} className={'svc-card' + (s.enabled === false ? ' off' : '') + (open ? ' open' : '')}>
                <div className="key-head svc-head" onClick={() => toggle(s.id)}>
                  <span className={'sync-chev' + (open ? ' open' : '')}>▸</span>
                  <span className="prov-name">{s.name}</span>
                  {s.role !== 'provider' && (
                    <label className="switch svc-enable" title="Include this source when syncing"
                      onClick={(e) => e.stopPropagation()}>
                      <input type="checkbox" checked={s.enabled !== false}
                        onChange={async (e) => { await api.setSourceEnabled(s.id, e.target.checked); reload() }} />
                      <span className="track"><span className="knob" /></span>
                    </label>
                  )}
                  <span className={'svc-stat' + (st.ok ? ' ok' : '')}>{st.text}</span>
                </div>
                {open && (
                  <div className="svc-body">
                    {s.hint && <span className="prov-hint">{s.hint}</span>}
                    {s.doc && <a className="prov-doc" href={s.doc.url} target="_blank" rel="noreferrer noopener">{s.doc.label}</a>}
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
                )}
              </div>
            )})}
          </div>
        )
      })}
      <div className="settings-actions sticky-actions">
        <button className="go" disabled={saving} onClick={save}>
          {saving ? 'Saving…' : 'Save credentials'}</button>
        {saved && <span className="saved">Saved ✓</span>}
      </div>
    </>
  )
}

// In-UI connect flow. Two shapes: device-code (Xbox — a short code you enter at
// microsoft.com/link, we poll for completion) and paste-token (EA/Epic/GOG/PSN —
// open an auth URL, paste what it shows). Dispatch on connect.mode.
function ConnectFlow({ connect, onDone }: { connect: ServiceConnect; onDone: () => void }) {
  if (connect.mode === 'device') return <DeviceConnectFlow connect={connect} onDone={onDone} />
  return <PasteConnectFlow connect={connect} onDone={onDone} />
}

// Device-code flow: click once, enter the shown code at microsoft.com/link, and
// ludodex polls until Microsoft confirms — no address-bar code to race.
function DeviceConnectFlow({ connect, onDone }: { connect: ServiceConnect; onDone: () => void }) {
  const [phase, setPhase] = useState<'idle' | 'starting' | 'waiting' | 'done' | 'error'>('idle')
  const [code, setCode] = useState('')
  const [uri, setUri] = useState('https://www.microsoft.com/link')
  const [copied, setCopied] = useState(false)
  const [msg, setMsg] = useState('')
  const pollRef = useRef<number | null>(null)
  const stop = () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null } }
  useEffect(() => stop, [])   // clear the timer if the panel unmounts mid-wait

  const start = async () => {
    setPhase('starting'); setMsg(''); setCopied(false)
    try {
      const r = await api.deviceStart(connect.start!)
      if (!r.ok) { setPhase('error'); setMsg(r.error || 'Couldn’t start — try again.'); return }
      setCode(r.user_code); setUri(r.verification_uri); setPhase('waiting')
      window.open(r.verification_uri, '_blank', 'noreferrer')
      const ivMs = Math.max(3, r.interval || 5) * 1000
      stop()
      pollRef.current = window.setInterval(async () => {
        try {
          const p = await api.devicePoll(connect.poll!)
          if (p.status === 'connected') {
            stop(); setPhase('done')
            setMsg(`Connected${p.account ? ' as ' + p.account : ''} ✓`); onDone()
          } else if (p.status === 'expired' || p.status === 'declined') {
            stop(); setPhase('error')
            setMsg(p.status === 'declined'
              ? 'Sign-in was declined. Click to try again.'
              : 'The code expired. Click to get a fresh one.')
          }
          // 'pending' → keep waiting
        } catch { /* transient network blip — keep polling */ }
      }, ivMs)
    } catch (e) { setPhase('error'); setMsg((e as Error).message) }
  }

  const copy = () => { navigator.clipboard?.writeText(code); setCopied(true) }

  return (
    <div className="connect-flow">
      <div className="connect-status">
        {connect.connected
          ? <span className="conn-ok">● Connected</span>
          : <span className="conn-off">○ Not connected</span>}
      </div>
      {phase === 'waiting' ? (
        <div className="device-wait">
          <div className="device-step">1. Enter this code:</div>
          <div className="device-code-row">
            <code className="device-code">{code}</code>
            <button className="clear-btn" onClick={copy}>{copied ? 'Copied ✓' : 'Copy'}</button>
          </div>
          <div className="device-step">2. at <a className="connect-link" href={uri}
            target="_blank" rel="noreferrer">{uri.replace('https://www.', '')} ↗</a>, sign in &amp; approve.</div>
          <div className="device-spin">Waiting for you to approve…</div>
        </div>
      ) : (
        <div className="connect-row">
          <button className="go" disabled={phase === 'starting'} onClick={start}>
            {phase === 'starting' ? 'Starting…'
              : phase === 'error' ? 'Try again'
              : connect.connected ? 'Reconnect Xbox' : connect.action_label}</button>
        </div>
      )}
      {connect.note && phase !== 'waiting' && <div className="connect-note">{connect.note}</div>}
      {msg && <div className={'connect-msg' + (phase === 'done' ? ' ok' : phase === 'error' ? ' err' : '')}>{msg}</div>}
    </div>
  )
}

// Paste-token flow (EA/Epic/GOG/PSN): a link to open the auth URL, a paste box,
// and a Connect button — no CLI.
function PasteConnectFlow({ connect, onDone }: { connect: ServiceConnect; onDone: () => void }) {
  const [val, setVal] = useState('')
  const [busy, setBusy] = useState(false)
  const [starting, setStarting] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  // Dynamic sign-in URL (Nintendo): mint it server-side on click, then open it.
  const openDynamic = async () => {
    setStarting(true); setMsg(null)
    try {
      const r = await api.authorizeStart(connect.start!)
      if (r.ok && r.url) window.open(r.url, '_blank', 'noopener')
      else setMsg({ ok: false, text: r.error || "Couldn't start the sign-in." })
    } catch (e) { setMsg({ ok: false, text: (e as Error).message }) }
    finally { setStarting(false) }
  }

  const go = async () => {
    if (!val.trim()) return
    setBusy(true); setMsg(null)
    try {
      const r = await api.connectService(connect.post!, val.trim())
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
        {connect.start
          ? <button className="connect-link" disabled={starting} onClick={openDynamic}>
              {starting ? 'Opening…' : connect.action_label + ' ↗'}
            </button>
          : <a className="connect-link" href={connect.url} target="_blank" rel="noreferrer">
              {connect.action_label} ↗
            </a>}
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
  useScrollLock()
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
  const [listOpen, setListOpen] = useState(false)         // Sources — collapsed by default
  const [expanded, setExpanded] = useState<Set<string>>(new Set())  // per-service
  const [media, setMedia] = useState<Set<string>>(new Set())        // "sync media" checks
  const [romLocs, setRomLocs] = useState<RomLocation[]>([])
  const [romJob, setRomJob] = useState<RomJob | null>(null)
  const [romListOpen, setRomListOpen] = useState(false)             // ROM repos — collapsed by default
  const [romExpanded, setRomExpanded] = useState<Set<number>>(new Set())

  const load = useCallback(async () => {
    try { const s = await api.syncStatus(); setSvcs(s.services); setJob(s.job) }
    catch { /* offline */ }
    try { const r = await api.romsStatus(); setRomLocs(r.locations); setRomJob(r.job) }
    catch { /* offline */ }
  }, [])
  useEffect(() => { if (open) load() }, [open, load])
  const running = !!job?.running
  const romRunning = !!romJob?.running
  const anyRunning = running || romRunning
  // Stay open until an outside click; but don't let a click-away abort a run.
  const wrapRef = useClickOutside<HTMLDivElement>(open, () => { if (!anyRunning) setOpen(false) })
  useEffect(() => {
    if (!anyRunning) return
    const t = setInterval(load, 1500)
    return () => clearInterval(t)
  }, [anyRunning, load])

  const enabled = svcs.filter((s) => s.enabled)
  const readyCount = enabled.filter((s) => s.ready).length
  const anyReady = readyCount > 0
  const toggleExpand = (id: string) =>
    setExpanded((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n })
  const toggleMedia = (id: string) =>
    setMedia((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n })

  const runAll = async (fullMode: boolean) => {
    setMsg('')
    const readyIds = enabled.filter((s) => s.ready).map((s) => s.id)
    const mediaIds = readyIds.filter((id) => media.has(id))
    try { setJob(await api.syncRun(['all'], mediaIds, fullMode)) } catch (e) { setMsg((e as Error).message) }
    load()
  }
  const runOne = async (id: string, fullMode: boolean) => {
    setMsg('')
    try { setJob(await api.syncRun([id], media.has(id) ? [id] : [], fullMode)) } catch (e) { setMsg((e as Error).message) }
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
      runOne(id, false)   // auto-sync after a fresh connect = new-games (fast)
    }
    tick()
  }

  const rowState = (id: string) => job?.services?.[id]?.state

  const romEnabled = romLocs.filter((l) => l.enabled)
  const toggleRomExpand = (id: number) =>
    setRomExpanded((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n })
  const runRomAll = async () => {
    setMsg('')
    try { setRomJob(await api.romsRun('all')) } catch (e) { setMsg((e as Error).message) }
    load()
  }
  const runArtScan = async (id: number) => {
    setMsg('')
    try {
      await api.scanLocalArt(id)
      setMsg('Indexing local art — track it in the Jobs monitor (top-right).')
    } catch (e) { setMsg((e as Error).message) }
    setTimeout(() => setMsg(''), 5000)
  }
  const runRomOne = async (id: number) => {
    setMsg('')
    try { setRomJob(await api.romsRun([id])) } catch (e) { setMsg((e as Error).message) }
    load()
  }
  const romRowState = (id: number) => romJob?.devices?.[String(id)]

  return (
    <div className="sync-wrap filter-wrap" ref={wrapRef}>
      <button className={'icon-btn' + (anyRunning ? ' spin' : '')} title="Sync library"
        onClick={() => setOpen((v) => !v)}>
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor"
          strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          {/* single circular arrow (Feather rotate-cw): the circle is centered at
              (12,12), so spinning looks like just the arrow turning — not the whole
              square sweeping corner-to-corner like refresh-cw did. */}
          <path d="M23 4v6h-6" />
          <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
        </svg>
      </button>
      {open && (
        <div className="filter-menu sync-menu">
          <div className="filter-head">
            <span>Sync library</span>
            {running && job?.step && <span className="sync-step">{job.step}</span>}
            {romRunning && romJob?.step && <span className="sync-step">{romJob.step}</span>}
          </div>
          <div className="sync-choice-q">Sync all configured:</div>
          <div className="sync-choice">
            <button className="sync-choice-opt" disabled={anyRunning || !anyReady}
              onClick={() => runAll(false)}
              title="Fast — pull new games and fill in anything still missing a match, attributes or art (retries earlier misses). Won't re-check what's already complete.">
              <span className="sync-choice-name">{running ? 'Syncing…' : 'New games'}</span>
              <span className="sync-choice-sub">fast · fills gaps</span>
            </button>
            <button className="sync-choice-opt" disabled={anyRunning || !anyReady}
              onClick={() => runAll(true)}
              title="Re-check EVERY game — re-match and refresh existing ratings, descriptions, tags, attributes & art. Slower.">
              <span className="sync-choice-name">Full refresh</span>
              <span className="sync-choice-sub">slower · re-checks all</span>
            </button>
          </div>
          {!anyReady && !running && (
            <div className="sync-note dim">Nothing ready yet — connect a store below.</div>
          )}

          <button className="sync-section" onClick={() => setListOpen((v) => !v)}>
            <span className={'sync-chev' + (listOpen ? ' open' : '')}>▸</span>
            <span className="sync-section-name">Sources</span>
            <span className="sync-section-meta">{readyCount}/{enabled.length} ready</span>
          </button>

          {listOpen && (
            <div className="sync-list">
              {enabled.map((s) => {
                const js = rowState(s.id)
                const isOpen = expanded.has(s.id)
                return (
                  <div key={s.id} className={'sync-row' + (isOpen ? ' open' : '')}>
                    <div className="sync-row-head" onClick={() => toggleExpand(s.id)}>
                      <span className={'sync-chev' + (isOpen ? ' open' : '')}>▸</span>
                      <span className="sync-name">{s.name}
                        {media.has(s.id) && s.can_media && <span className="sync-media-tag">+media</span>}</span>
                      <span className="sync-meta">
                        {js === 'running' ? <span className="sync-run">syncing…</span>
                          : js === 'ok' ? <span className="conn-ok">✓ {(s.count ?? 0).toLocaleString()}</span>
                          : js === 'failed' ? <span className="conn-off">✗ failed</span>
                          : s.count != null ? `${s.count.toLocaleString()} owned`
                          : s.ready ? 'ready' : s.needs_auth ? 'sign in' : 'not set'}
                      </span>
                      {s.ready && js !== 'running' && (
                        <span className="sync-two" onClick={(e) => e.stopPropagation()}>
                          <button className="ops-btn" title="New — pull new games and fill anything still missing a match, attributes or art (retries earlier misses)"
                            disabled={anyRunning}
                            onClick={(e) => { e.stopPropagation(); runOne(s.id, false) }}>New</button>
                          <button className="ops-btn" title="Full — re-check every game: re-match and refresh existing ratings, descriptions, tags & art"
                            disabled={anyRunning}
                            onClick={(e) => { e.stopPropagation(); runOne(s.id, true) }}>Full</button>
                        </span>
                      )}
                    </div>
                    {isOpen && (
                      <div className="sync-row-body">
                        {s.can_media && (
                          <label className="sync-media-opt">
                            <input type="checkbox" checked={media.has(s.id)}
                              onChange={() => toggleMedia(s.id)} />
                            <span>Also sync <b>media</b> (cover art & screenshots), not just titles &amp; metadata</span>
                          </label>
                        )}
                        {js === 'failed' && (
                          job?.services?.[s.id]?.reauth && s.connect ? (
                            <div className="sync-auth">
                              <div className="sync-auth-label">Your {s.name} sign-in expired — reconnect to sync</div>
                              <ConnectFlow connect={s.connect} onDone={connectThenSync(s.id)} />
                              {job?.services?.[s.id]?.error && (
                                <details className="sync-err-details">
                                  <summary>Details</summary>
                                  <div className="sync-err">{job.services[s.id].error}</div>
                                </details>
                              )}
                            </div>
                          ) : job?.services?.[s.id]?.error ? (
                            <div className="sync-err">{job.services[s.id].error}</div>
                          ) : null
                        )}
                        {s.needs_auth && s.connect && js !== 'running' && (
                          <div className="sync-auth">
                            <div className="sync-auth-label">Sign in to sync</div>
                            <ConnectFlow connect={s.connect} onDone={connectThenSync(s.id)} />
                          </div>
                        )}
                        {!s.ready && !s.needs_auth && (
                          <div className="sync-note dim">Add credentials in Settings → Stores &amp; providers.</div>
                        )}
                        {s.ready && !s.needs_auth && !s.can_media && (
                          <div className="sync-note dim">Syncs which games you own on {s.name}.</div>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}

          {job?.phases && job.phases.some((p) => p.state !== 'pending') && (
            <div className="sync-list sync-phases">
              {job.phases.map((p) => (
                <div key={p.id} className="sync-row phase-row">
                  <div className="sync-row-head static">
                    <span className="sync-chev-pad" />
                    <span className="sync-name">{p.label}</span>
                    <span className="sync-meta">
                      {p.state === 'running' ? <span className="sync-run">working…</span>
                        : p.state === 'ok' ? <span className="conn-ok">✓{p.detail ? ' ' + p.detail : ''}</span>
                        : p.state === 'failed' ? <span className="conn-off">✗ failed</span>
                        : p.state === 'skipped' ? <span className="dim">—</span>
                        : <span className="dim">pending</span>}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}

          {romLocs.length > 0 && (
            <>
              <button className="sync-section" onClick={() => setRomListOpen((v) => !v)}>
                <span className={'sync-chev' + (romListOpen ? ' open' : '')}>▸</span>
                <span className="sync-section-name">ROM repos</span>
                <span className="sync-section-meta">{romEnabled.length} location{romEnabled.length === 1 ? '' : 's'}</span>
              </button>
              {romListOpen && (
                <div className="sync-list">
                  <button className="go sync-all sync-rom-all" disabled={anyRunning || !romEnabled.length}
                    onClick={runRomAll}>
                    {romRunning ? 'Scanning…' : 'Sync all ROM locations'}
                  </button>
                  <div className="rom-children">
                  {romEnabled.map((l) => {
                    const rs = romRowState(l.id)
                    const isOpen = romExpanded.has(l.id)
                    return (
                      <div key={l.id} className={'sync-row' + (isOpen ? ' open' : '')}>
                        <div className="sync-row-head" onClick={() => toggleRomExpand(l.id)}>
                          <span className={'sync-chev' + (isOpen ? ' open' : '')}>▸</span>
                          <span className="sync-name">{l.name}
                            <span className="sync-xport">{l.transport}{l.host ? ' · ' + l.host : ''}</span></span>
                          <span className="sync-meta">
                            {rs?.state === 'running' ? <span className="sync-run">scanning…</span>
                              : rs?.state === 'ok' ? <span className="conn-ok">✓ {(rs.roms ?? 0).toLocaleString()}</span>
                              : rs?.state === 'failed' ? <span className="conn-off">✗ failed</span>
                              : l.games != null ? <>{l.games.toLocaleString()} games<span className="sync-files"> · {(l.count ?? 0).toLocaleString()} files</span></>
                              : l.count != null ? `${l.count.toLocaleString()} files`
                              : 'not scanned'}
                          </span>
                          {rs?.state !== 'running' && (
                            <button className="ops-btn" disabled={anyRunning}
                              onClick={(e) => { e.stopPropagation(); runRomOne(l.id) }}>Sync</button>
                          )}
                          {l.transport === 'local' && (
                            <button className="ops-btn" title="Index art already sitting inside this ROM tree (no move) so your local covers show up"
                              onClick={(e) => { e.stopPropagation(); runArtScan(l.id) }}>Index art</button>
                          )}
                        </div>
                        {isOpen && (
                          <div className="sync-row-body">
                            {l.managers.map((m) => (
                              <div key={m.id} className="sync-mgr">
                                <span className="sync-mgr-kind">{m.kind_label}</span>
                                <code className="sync-mgr-path">{m.rom_path}</code>
                                {m.count != null && <span className="dim">{m.count.toLocaleString()}</span>}
                              </div>
                            ))}
                            {rs?.state === 'failed' && rs.error && <div className="sync-err">{rs.error}</div>}
                            <div className="sync-note dim">Rescans this location for added/removed ROMs, then rebuilds the catalog.</div>
                          </div>
                        )}
                      </div>
                    )
                  })}
                  </div>
                </div>
              )}
            </>
          )}

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
  const [dbOpen, setDbOpen] = useState(false)   // Databases — collapsed by default
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
            <button className="ops-section-toggle" onClick={() => setDbOpen((v) => !v)}>
              <span className={'sync-chev' + (dbOpen ? ' open' : '')}>▸</span>
              <span>Databases</span>
              <span className="ops-db-count">{dbs.length}</span>
            </button>
            <button className="ops-link" disabled={!!busy} onClick={checkAll}>
              {busy === 'check' ? 'checking…' : 'Check all'}
            </button>
          </div>
          {dbOpen && dbs.map((d) => (
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
  ['os', 'OS'], ['device', 'Device'],
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
function AttributeProvenance({ d, onChanged }: { d: GameDetail; onChanged: () => void }) {
  const prov = d.attribute_provenance || {}
  const overrides = d.attribute_overrides || {}
  const [editing, setEditing] = useState<string | null>(null)
  const [manual, setManual] = useState('')
  // Hidden by default when a game opens (per request); the header toggle reveals it.
  const [show, setShow] = useState(false)
  useEffect(() => { setShow(false); setEditing(null) }, [d.norm_key])

  // Every editable attribute kind, blanks included, in the server's vocabulary order;
  // any extra kind the game happens to have (that isn't in the vocab) is appended.
  const vocab = d.editable_kinds && d.editable_kinds.length
    ? d.editable_kinds : Object.keys(prov).sort()
  const extra = Object.keys(prov).filter((k) => !vocab.includes(k)).sort()
  const kinds = [...vocab, ...extra]
  const filled = kinds.filter((k) => prov[k]?.length || overrides[k]).length

  const setOv = async (kind: string, value: string, origin: string) => {
    try { await api.setAttributeOverride(d.norm_key, kind, value, origin) } catch { /* ignore */ }
    setEditing(null); setManual(''); onChanged()
  }
  const clearOv = async (kind: string) => {
    try { await api.clearAttributeOverride(d.norm_key, kind) } catch { /* ignore */ }
    onChanged()
  }

  return (
    <section className="attr-prov">
      <button className="attr-prov-toggle" onClick={() => setShow((s) => !s)}>
        <span className={'caret' + (show ? ' open' : '')}>▸</span>
        <h3>View / edit all attributes
          <span className="sec-help">{filled} of {kinds.length} set · ✨ = AI-derived · click a row to change or fill it</span>
        </h3>
      </button>
      {show && (
      <div className="ap-list">
        {kinds.map((kind) => {
          const vals = prov[kind] || []
          const ov = overrides[kind]
          const open = editing === kind
          const blank = !vals.length && !ov
          return (
            <div key={kind} className={'ap-row' + (open ? ' open' : '') + (blank ? ' blank' : '')}>
              <button className="ap-kind" onClick={() => { setEditing(open ? null : kind); setManual('') }}>
                <span className="ap-kname">{kind.replace(/_/g, ' ')}</span>
                <span className="ap-vals">
                  {ov ? (
                    <span className="ap-chip ap-chosen prov-badge" style={attrBadgeStyle([ov.origin])}>
                      {ov.value}<ProvTag origin={ov.origin} /></span>
                  ) : blank ? (
                    <span className="ap-empty dim">— add</span>
                  ) : vals.slice(0, 6).map((v, i) => (
                    <span key={i} className="ap-chip prov-badge" style={attrBadgeStyle(v.origins)}>
                      {v.ai && <span className="attr-sparkle" title="AI-derived">✨</span>}
                      {v.value}
                      {v.origins.map((o) => <ProvDot key={o} origin={o} />)}</span>
                  ))}
                  {!ov && vals.length > 6 && <span className="dim">+{vals.length - 6}</span>}
                </span>
                <span className="ap-caret">{open ? '▾' : '▸'}</span>
              </button>
              {open && (
                <div className="attr-repoint">
                  <div className="ar-h">{blank ? 'Add a value for' : 'Canonical value for'} “{kind.replace(/_/g, ' ')}”</div>
                  {ov && (
                    <div className="ar-cur">Currently pinned: <b>{ov.value}</b>
                      <ProvTag origin={ov.origin} />
                      <button className="link-btn" onClick={() => clearOv(kind)}>revert to sources</button>
                    </div>
                  )}
                  {vals.length > 0 && (
                    <div className="ar-opts">
                      {vals.map((v, i) => (
                        <button key={i} className="ar-opt" onClick={() => setOv(kind, v.value, v.origins[0] || 'provider')}>
                          {v.ai && <span className="attr-sparkle">✨</span>}{v.value}
                          {v.origins.map((o) => <ProvTag key={o} origin={o} />)}
                        </button>
                      ))}
                    </div>
                  )}
                  <div className="ar-manual">
                    <input placeholder={vals.length ? 'or type a manual value…' : 'type a value…'} value={manual}
                      onChange={(e) => setManual(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Enter' && manual.trim()) setOv(kind, manual.trim(), 'manual') }} />
                    <button className="ops-btn go" disabled={!manual.trim()}
                      onClick={() => setOv(kind, manual.trim(), 'manual')}>Set</button>
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
      )}
    </section>
  )
}

function About({ attrs, scores, prov }: {
  attrs: Record<string, string[]>; scores?: Scores
  prov?: Record<string, { value: string; origins: string[]; ai: boolean }[]>
}) {
  const first = (k: string) => attrs[k]?.[0]
  const originsOf = (k: string, v: string) =>
    prov?.[k]?.find((x) => x.value === v)?.origins ?? []
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
            {attrs[k].map((v) => {
              const origins = originsOf(k, v)
              return (
                <span key={v} className="tag prov-badge"
                  style={origins.length ? attrBadgeStyle(origins) : undefined}>
                  {v}{origins.map((o) => <ProvDot key={o} origin={o} />)}
                </span>
              )
            })}
          </span>
        </div>
      ) : null)}
    </section>
  )
}

// Accepted-but-unapplied metadata changes surface here, above the library search,
// so applying isn't buried in Settings. Apply runs the batch job (link matches,
// fetch records, rebuild); the bar clears once the pending count drops.
function PendingApplyBar({ count, onApplied }: { count: number; onApplied: () => void }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const apply = async () => {
    setBusy(true); setErr('')
    try {
      await api.aimetaApply(undefined, true)      // apply all accepted, all media
    } catch (e) { setBusy(false); setErr(e instanceof Error ? e.message : 'failed'); return }
    let tries = 0                                  // apply is a background job — poll
    const poll = async () => {
      tries += 1
      const [s, jobs] = await Promise.all([
        api.stats().catch(() => null),
        api.jobs().then((r) => r.jobs).catch(() => [] as Job[]),
      ])
      const pendingCleared = !!s && (s.pending_meta ?? 0) < count
      const jobDone = !jobs.some((j) =>
        (j.kind === 'aimeta' || j.id.includes('aimeta')) &&
        (j.status === 'running' || j.status === 'paused'))
      if (pendingCleared || (jobDone && tries >= 2) || tries > 80) {
        setBusy(false); onApplied(); return
      }
      setTimeout(poll, 3000)
    }
    setTimeout(poll, 3000)
  }
  return (
    <div className="pending-apply">
      <div className="pa-info">
        <span className="pa-count">{count}</span>
        <div className="pa-text">
          <b>{count} metadata change{count === 1 ? '' : 's'} accepted — not applied yet.</b>
          <span className="pa-sub">
            Accepting only queues a change; your library won't update until you apply.
            {err && <span className="pa-err"> · {err}</span>}
          </span>
        </div>
      </div>
      <button className="pa-btn" disabled={busy} onClick={apply}>
        {busy ? 'Applying…' : 'Apply now'}
      </button>
    </div>
  )
}

// Manual per-format ownership: mark a physical disc you own, or a per-platform
// ROM/digital *want* that coexists with what you already have. Store/ROM syncs
// can't know these, so they live in the durable ownership store.
const OWN_FORMS: { id: string; label: string }[] = [
  { id: 'physical', label: '💿 Physical' },
  { id: 'rom', label: '🎮 ROM' },
  { id: 'digital', label: '☁ Digital' },
]
const formLabel = (f: string) => OWN_FORMS.find((x) => x.id === f)?.label ?? f
const stateIcon = (s: string) => (s === 'want' ? '🕗' : '✓')
// Pretty-print a stored ownership platform slug: prefer the live IGDB system name,
// then systemLabel's mapping, else the raw slug.
const prettyPlatform = (slug: string, form: string, names?: Map<string, string>) =>
  (slug && names?.get(slug)) || systemLabel(form === 'rom' ? 'emulation' : form, slug) || slug

function OwnershipEditor({ nk, title, facts, onChanged }: {
  nk: string; title: string; facts: OwnershipFact[]; onChanged: () => void
}) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)

  const remove = async (f: OwnershipFact) => {
    setBusy(true)
    try { await api.clearOwnership(nk, f.form, f.platform, f.state); onChanged() }
    catch { /* surfaced elsewhere */ } finally { setBusy(false) }
  }

  return (
    <div className="own-editor">
      <div className="own-editor-head">
        <span className="own-editor-title">Track ownership manually</span>
        <button className="own-add-toggle" onClick={() => setOpen(true)}>
          + Add physical / want
        </button>
      </div>
      {facts.length > 0 && (
        <div className="own-facts">
          {facts.map((f, i) => (
            <span key={i} className={'own-chip ' + f.state}>
              <span className="own-chip-state">{stateIcon(f.state)}</span>
              {formLabel(f.form)}{f.platform ? ' · ' + prettyPlatform(f.platform, f.form) : ''}
              <button className="own-chip-x" title="Remove" disabled={busy}
                onClick={() => remove(f)}>×</button>
            </span>
          ))}
        </div>
      )}
      {open && <OwnershipOverlay nk={nk} title={title} facts={facts}
        onChanged={onChanged} onClose={() => setOpen(false)} />}
    </div>
  )
}

// Full-screen overlay for adding ownership: pick what you're recording (form +
// have/want) once, then click any system to toggle it. Two lists — the game's
// real IGDB cross-platform releases up top, and the full searchable catalog of
// known systems below for anything IGDB doesn't list.
function OwnershipOverlay({ nk, title, facts, onChanged, onClose }: {
  nk: string; title: string; facts: OwnershipFact[]
  onChanged: () => void; onClose: () => void
}) {
  useScrollLock()
  const [form, setForm] = useState<'physical' | 'rom' | 'digital'>('physical')
  const [state, setState] = useState<'have' | 'want'>('have')
  const [q, setQ] = useState('')
  const [releases, setReleases] = useState<GameRelease[] | null>(null)
  const [relInfo, setRelInfo] = useState<{ resolved: boolean; source?: string | null }>({ resolved: false })
  const [systems, setSystems] = useState<SystemEntry[]>([])
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')

  useEffect(() => {
    let live = true
    api.gameReleases(nk)
      .then((r) => { if (live) { setReleases(r.releases || []); setRelInfo({ resolved: r.resolved, source: r.source }) } })
      .catch(() => { if (live) setReleases([]) })
    api.knownSystems().then((r) => { if (live) setSystems(r.systems || []) }).catch(() => {})
    return () => { live = false }
  }, [nk])

  // name lookup for prettifying chips (releases first, then the full catalog)
  const names = useMemo(() => {
    const m = new Map<string, string>()
    for (const s of systems) m.set(s.id, s.name)
    for (const r of (releases || [])) m.set(r.id, r.name)
    return m
  }, [systems, releases])

  const factOf = (pid: string) => facts.find((f) => f.platform === pid && f.form === form && f.state === state)
  const otherFacts = (pid: string) => facts.filter((f) => f.platform === pid && !(f.form === form && f.state === state))

  const toggle = async (sys: { id: string; name: string }) => {
    setBusy(sys.id); setErr('')
    try {
      if (factOf(sys.id)) await api.clearOwnership(nk, form, sys.id, state)
      else await api.setOwnership(nk, form, sys.id, state, '', title)
      onChanged()
    } catch (e) { setErr(e instanceof Error ? e.message : 'failed') }
    finally { setBusy('') }
  }

  const ql = q.trim().toLowerCase()
  const match = (name: string, id: string, abbr?: string) =>
    !ql || name.toLowerCase().includes(ql) || id.includes(ql) || (abbr || '').toLowerCase().includes(ql)
  const rel = (releases || []).filter((r) => match(r.name, r.id, r.abbr))
  const relIds = new Set((releases || []).map((r) => r.id))
  const sys = systems.filter((s) => !relIds.has(s.id) && match(s.name, s.id, s.abbr))
    .slice(0, ql ? 300 : 80)

  const Row = (s: { id: string; name: string; abbr?: string; year?: number | null }) => {
    const on = !!factOf(s.id)
    const others = otherFacts(s.id)
    return (
      <button key={s.id} type="button" disabled={busy === s.id}
        className={'own-sys' + (on ? ' on' : '') + (busy === s.id ? ' busy' : '')}
        onClick={() => toggle(s)}>
        <span className="own-sys-check">{on ? '✓' : '+'}</span>
        <span className="own-sys-name">{s.name}</span>
        {s.year ? <span className="own-sys-year dim">{s.year}</span> : null}
        {others.length > 0 && (
          <span className="own-sys-badges" title="Already marked in other forms">
            {others.map((o, i) => (
              <span key={i} className={'own-sys-badge ' + o.state}>
                {stateIcon(o.state)}{formLabel(o.form).replace(/^\S+\s/, '')}
              </span>
            ))}
          </span>
        )}
      </button>
    )
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="panel own-panel" onClick={(e) => e.stopPropagation()}>
        <button className="close" onClick={onClose}>×</button>
        <h2 className="own-panel-title">Track ownership<span className="dim"> · {title}</span></h2>
        <p className="own-panel-desc dim">
          Pick what you're recording, then click a system to mark it. Toggle again to remove.
        </p>

        <div className="own-controls">
          <div className="own-seg own-form-seg">
            {OWN_FORMS.map((f) => (
              <button key={f.id} className={form === f.id ? 'on' : ''}
                onClick={() => setForm(f.id as typeof form)}>{f.label}</button>
            ))}
          </div>
          <div className="own-seg own-state-seg">
            <button className={state === 'have' ? 'on' : ''} onClick={() => setState('have')}>✓ Have</button>
            <button className={state === 'want' ? 'on want' : 'want'} onClick={() => setState('want')}>🕗 Want</button>
          </div>
        </div>

        <input className="own-search" placeholder="Search systems…" value={q} autoFocus
          onChange={(e) => setQ(e.target.value)} />
        {err && <div className="own-err">⚠ {err}</div>}

        <div className="own-lists">
          <section className="own-sect">
            <h3 className="own-sect-h">Released on
              {releases && relInfo.resolved && <span className="dim"> · {(releases || []).length} platform{releases.length === 1 ? '' : 's'} per IGDB</span>}
            </h3>
            {releases === null
              ? <div className="dim own-note">Looking up releases…</div>
              : !relInfo.resolved
                ? <div className="dim own-note">No IGDB match for this game — search the full system list below.</div>
                : rel.length === 0
                  ? <div className="dim own-note">{ql ? 'No matching release.' : 'No platform data.'}</div>
                  : <div className="own-sys-grid">{rel.map(Row)}</div>}
          </section>

          <section className="own-sect">
            <h3 className="own-sect-h">All systems
              <span className="dim"> · {systems.length ? 'search to narrow' : 'loading…'}</span>
            </h3>
            {sys.length === 0
              ? <div className="dim own-note">{ql ? 'No system matches.' : ''}</div>
              : <div className="own-sys-grid">{sys.map(Row)}</div>}
            {!ql && systems.length > sys.length + relIds.size &&
              <div className="dim own-note">…and {systems.length - sys.length - relIds.size} more — type to search.</div>}
          </section>
        </div>

        {facts.length > 0 && (
          <div className="own-current">
            <span className="own-current-h dim">Marked:</span>
            {facts.map((f, i) => (
              <span key={i} className={'own-chip ' + f.state}>
                <span className="own-chip-state">{stateIcon(f.state)}</span>
                {formLabel(f.form)}{f.platform ? ' · ' + prettyPlatform(f.platform, f.form, names) : ''}
                <button className="own-chip-x" title="Remove"
                  onClick={() => api.clearOwnership(nk, f.form, f.platform, f.state).then(onChanged).catch(() => {})}>×</button>
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// Gear + sliders to position/zoom one image inside its viewport. Live-previews
// through onChange (the parent applies it to the image) and debounce-saves.
function FrameEditor({ nk, kind, value, onChange, label, disabled }: {
  nk: string; kind: string; value?: Frame
  onChange: (f: Frame | undefined) => void; label?: string; disabled?: boolean
}) {
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  const gearRef = useRef<HTMLButtonElement>(null)
  const f = value ?? DEFAULT_FRAME
  const saveT = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const set = (patch: Partial<Frame>) => {
    const nf = { ...f, ...patch }
    onChange(nf)
    clearTimeout(saveT.current)
    saveT.current = setTimeout(() => { api.setFraming(nk, kind, nf).catch(() => {}) }, 350)
  }
  const reset = () => {
    clearTimeout(saveT.current); onChange(undefined)
    api.clearFraming(nk, kind).catch(() => {})
  }
  const toggle = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (pos) { setPos(null); return }
    const r = gearRef.current?.getBoundingClientRect()
    if (r) setPos({ top: Math.min(r.bottom + 6, window.innerHeight - 250),
                    left: Math.min(r.left, window.innerWidth - 250) })
  }
  if (disabled) {
    return <button className="frame-gear disabled" disabled
      title="Position/zoom framing has moved — open the media (click the count) and use ⚙ on the #1 image. This gear is reserved for future per-category settings.">⚙</button>
  }
  return (
    <>
      <button ref={gearRef} className="frame-gear" title={`Frame the ${label ?? kind}`}
        onClick={toggle}>⚙</button>
      {pos && (
        <>
          <div className="frame-backdrop" onClick={(e) => { e.stopPropagation(); setPos(null) }} />
          <div className="frame-panel" style={{ top: pos.top, left: pos.left }}
            onClick={(e) => e.stopPropagation()}>
            <div className="frame-panel-head">Frame the {label ?? kind}</div>
            <div className="frame-row">
              <label>Zoom</label>
              <input type="range" min={0.25} max={5} step={0.05} value={f.zoom}
                onChange={(e) => set({ zoom: Number(e.target.value) })} />
              <span className="frame-val">{Math.round(f.zoom * 100)}%</span>
            </div>
            {(['top', 'right', 'bottom', 'left'] as const).map((side) => (
              <div className="frame-row" key={side}>
                <label>{side}</label>
                <input type="range" min={-50} max={50} step={1} value={f[side]}
                  onChange={(e) => set({ [side]: Number(e.target.value) } as Partial<Frame>)} />
                <span className="frame-val">{f[side] > 0 ? '+' : ''}{f[side]}%</span>
              </div>
            ))}
            <div className="frame-acts">
              <button className="frame-reset" onClick={reset}>Reset</button>
              <button className="frame-done" onClick={() => setPos(null)}>Done</button>
            </div>
          </div>
        </>
      )}
    </>
  )
}

// Fly a ✨ from a source element toward the Jobs monitor, insinuating the work was
// handed off there. Pure DOM (no React state) so it survives the overlay closing.
function flyToJobs(from: DOMRect) {
  const target = document.querySelector('.jobmon')?.getBoundingClientRect()
  const el = document.createElement('span')
  el.className = 'wand-fly'
  el.textContent = '✨'
  const sx = from.left + from.width / 2, sy = from.top + from.height / 2
  el.style.left = sx + 'px'
  el.style.top = sy + 'px'
  document.body.appendChild(el)
  const tx = (target ? target.left + target.width / 2 : window.innerWidth - 40) - sx
  const ty = (target ? target.top + target.height / 2 : 20) - sy
  requestAnimationFrame(() => {
    el.style.transform = `translate(${tx}px, ${ty}px) scale(0.25) rotate(200deg)`
    el.style.opacity = '0'
  })
  window.setTimeout(() => el.remove(), 950)
}

// Lightweight transient toast, body-mounted so it outlives whatever fired it.
function showToast(msg: string) {
  const el = document.createElement('div')
  el.className = 'app-toast'
  el.textContent = msg
  document.body.appendChild(el)
  requestAnimationFrame(() => el.classList.add('show'))
  window.setTimeout(() => {
    el.classList.remove('show')
    window.setTimeout(() => el.remove(), 350)
  }, 4200)
}

function FixDupModal({ nk, title, onClose, onMerged }: {
  nk: string; title: string; onClose: () => void; onMerged: (canonical: string) => void
}) {
  const [canonical, setCanonical] = useState<'this' | 'other' | null>(null)
  const [q, setQ] = useState('')
  const [results, setResults] = useState<GameRow[]>([])
  const [suggested, setSuggested] = useState<GameRow[]>([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [confirm, setConfirm] = useState<{ other: string; msg: string } | null>(null)

  // Likely duplicates for THIS game, from the fuzzy similarity scan — so you
  // usually don't have to search.
  useEffect(() => {
    api.suspectedDupes(80).then((r) => {
      const rows = r.dupes
        .filter((d) => d.a_nk === nk || d.b_nk === nk)
        .map((d) => (d.a_nk === nk
          ? { norm_key: d.b_nk, title: d.b } as GameRow
          : { norm_key: d.a_nk, title: d.a } as GameRow))
      setSuggested(rows)
    }).catch(() => {})
  }, [nk])

  useEffect(() => {
    if (!q.trim()) { setResults([]); return }
    const t = setTimeout(() => {
      api.games({ q, status: 'all' } as GamesQuery)
        .then((p) => setResults(p.items.filter((g) => g.norm_key !== nk).slice(0, 25)))
        .catch(() => {})
    }, 250)
    return () => clearTimeout(t)
  }, [q, nk])

  const doMerge = async (other: string, force = false) => {
    if (!canonical || busy) return
    setBusy(true); setErr(''); setConfirm(null)
    try { const r = await api.mergeGame(nk, other, canonical, force); onMerged(r.canonical) }
    catch (e) {
      if (e instanceof Error && e.name === 'ConfirmRequired') {
        setConfirm({ other, msg: e.message }); setBusy(false)
      } else { setErr(e instanceof Error ? e.message : 'merge failed'); setBusy(false) }
    }
  }

  const row = (g: GameRow) => (
    <div key={g.norm_key} className="fixdup-row">
      <span className="fixdup-name">{g.title}</span>
      <button className="ops-btn" disabled={busy} onClick={() => doMerge(g.norm_key)}>Merge</button>
    </div>
  )

  return (
    <div className="overlay fixdup-overlay" onClick={onClose}>
      <div className="panel fixdup-panel" onClick={(e) => e.stopPropagation()}>
        <button className="close" onClick={onClose}>×</button>
        <h2>Fix duplication</h2>
        {!canonical ? (
          <div className="fixdup-ask">
            <p>Is <b>{title}</b> the entry you want to keep — the source of truth?</p>
            <div className="fixdup-choice">
              <button className="go" onClick={() => setCanonical('this')}>Yes — keep this one</button>
              <button className="ops-btn" onClick={() => setCanonical('other')}>No — another entry is correct</button>
            </div>
          </div>
        ) : (
          <div className="fixdup-pick">
            <p className="dim">{canonical === 'this'
              ? <>Pick the duplicate to fold <b>into</b> “{title}”. Its ownership, media &amp; tags move here; “{title}” keeps its title &amp; match.</>
              : <>Pick the correct entry — “{title}” folds <b>into</b> it (that one keeps its title &amp; match).</>}</p>
            {suggested.length > 0 && (
              <div className="fixdup-suggest">
                <div className="fixdup-sub">Likely duplicates</div>
                {suggested.map(row)}
              </div>
            )}
            <input className="fixdup-search" autoFocus placeholder="…or search all games" value={q}
              onChange={(e) => setQ(e.target.value)} />
            {results.map(row)}
            <button className="fixdup-back" onClick={() => { setCanonical(null); setQ('') }}>← back</button>
          </div>
        )}
        {confirm && (
          <div className="fixdup-confirm">
            <p>⚠️ {confirm.msg}</p>
            <div className="fixdup-choice">
              <button className="go" disabled={busy}
                onClick={() => doMerge(confirm.other, true)}>Merge anyway</button>
              <button className="ops-btn" disabled={busy}
                onClick={() => setConfirm(null)}>Cancel</button>
            </div>
          </div>
        )}
        {busy && <div className="dim fixdup-busy">Merging + rebuilding catalog…</div>}
        {err && <div className="fixdup-err">{err}</div>}
      </div>
    </div>
  )
}

// "Peel apart": one entry actually holds two different same-named games (a remake /
// re-release). Pick the source rows that belong to the OTHER game, name it (with a
// year so it's distinct), and split them into their own entry. ✨ asks the AI to
// work out the grouping for you.
function PeelModal({ nk, title, onClose, onPeeled }: {
  nk: string; title: string; onClose: () => void; onPeeled: () => void
}) {
  useScrollLock()
  const [srcs, setSrcs] = useState<SourceRow[] | null>(null)
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const [newTitle, setNewTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [ai, setAi] = useState<SplitSuggestion | null>(null)
  const [aiBusy, setAiBusy] = useState(false)

  useEffect(() => { api.gameSources(nk).then((r) => setSrcs(r.sources)).catch(() => setSrcs([])) }, [nk])

  const key = (s: SourceRow) => s.source + '' + s.source_id
  const toggle = (i: number) => setPicked((p) => {
    const n = new Set(p); n.has(i) ? n.delete(i) : n.add(i); return n
  })

  const askAi = async () => {
    setAiBusy(true); setErr('')
    try {
      const r = await api.splitSuggest(nk)
      setAi(r)
      if (!r.multiple) setErr('AI thinks this is really one game — nothing to peel.')
    } catch (e) { setErr(e instanceof Error ? e.message : 'AI split failed') }
    finally { setAiBusy(false) }
  }

  // Apply an AI-proposed game (index >=1, i.e. NOT the canonical first one): select
  // its rows and prefill the title.
  const applySuggestion = (gi: number) => {
    if (!ai) return
    const g = ai.games[gi]
    setPicked(new Set((g.rows || []).map((n) => n - 1).filter((i) => i >= 0 && i < (srcs?.length || 0))))
    setNewTitle(g.title || '')
  }

  const doPeel = async () => {
    if (!srcs || !picked.size || !newTitle.trim() || busy) return
    setBusy(true); setErr('')
    const rows = [...picked].map((i) => ({ source: srcs[i].source, source_id: srcs[i].source_id }))
    try { await api.splitGame(nk, rows, newTitle.trim()); onPeeled() }
    catch (e) { setErr(e instanceof Error ? e.message : 'peel failed'); setBusy(false) }
  }

  return (
    <div className="overlay fixdup-overlay" onClick={onClose}>
      <div className="panel fixdup-panel peel-panel" onClick={(e) => e.stopPropagation()}>
        <button className="close" onClick={onClose}>×</button>
        <h2>Peel apart</h2>
        <p className="dim">“{title}” may be <b>two different games with the same name</b> (a
          remake / re-release). Tick the source(s) that belong to the <b>other</b> game,
          give it a distinct name (include a year), and it splits into its own entry.</p>

        <div className="peel-ai">
          <button className="ops-btn" onClick={askAi} disabled={aiBusy || !srcs}>
            {aiBusy ? '✨ Thinking…' : '✨ Ask AI to work it out'}</button>
          {ai && ai.reason && <span className="dim peel-ai-reason">{ai.reason}</span>}
        </div>
        {ai && ai.multiple && ai.games.length > 1 && (
          <div className="peel-suggest">
            {ai.games.map((g, gi) => (
              <div key={gi} className="peel-sug-row">
                <span className="peel-sug-name">{g.title}
                  <span className="dim"> · {(g.rows || []).length} source(s)</span></span>
                {gi === 0
                  ? <span className="peel-sug-keep">keeps this entry</span>
                  : <button className="ops-btn" onClick={() => applySuggestion(gi)}>Select these</button>}
              </div>
            ))}
          </div>
        )}

        {srcs === null ? <div className="loading">Loading sources…</div> : (
          <div className="peel-sources">
            {srcs.map((s, i) => (
              <label key={key(s)} className={'peel-src' + (picked.has(i) ? ' on' : '')}>
                <input type="checkbox" checked={picked.has(i)} onChange={() => toggle(i)} />
                <span className="peel-src-name">{s.title_raw || '(untitled)'}</span>
                <span className="peel-src-meta">{s.source}{s.platform && s.platform !== s.source ? ' · ' + s.platform : ''}
                  {s.year ? ' · ' + s.year : ''}</span>
              </label>
            ))}
          </div>
        )}

        <div className="peel-name">
          <label>Name the peeled-off game</label>
          <input autoFocus placeholder="e.g. Uno (2006)" value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)} />
        </div>
        <div className="fixdup-choice">
          <button className="go" disabled={busy || !picked.size || !newTitle.trim()}
            onClick={doPeel}>Peel {picked.size || ''} off into a new entry</button>
          <button className="ops-btn" disabled={busy} onClick={onClose}>Cancel</button>
        </div>
        {busy && <div className="dim fixdup-busy">Peeling + rebuilding catalog…</div>}
        {err && <div className="fixdup-err">{err}</div>}
      </div>
    </div>
  )
}

function Detail({ nk, onClose, onMediaChanged, onNavigate }: {
  nk: string; onClose: () => void; onMediaChanged?: () => void
  onNavigate?: (key: string) => void   // jump to a sibling platform entry ("also owned on")
}) {
  // `nk` is this platform entry's id (base_key@platform). The DETAIL is fetched by it
  // (per-platform view), but media candidates + title-level mutations key off the
  // base title key, so derive it for those.
  const base = nk.includes('@') ? nk.slice(0, nk.lastIndexOf('@')) : nk
  const [mediaDirty, setMediaDirty] = useState(false)
  // close, but first refresh the grid/spotlight if the media (e.g. chosen cover)
  // changed here — so a re-pinned cover shows without a hard refresh.
  const close = () => { if (mediaDirty) onMediaChanged?.(); onClose() }
  useScrollLock()
  const [d, setD] = useState<GameDetail | null>(null)
  const [media, setMedia] = useState<MediaLibrary | null>(null)
  const [kinds, setKinds] = useState<MediaKind[]>([])
  const [wandSent, setWandSent] = useState(false)
  const [wandErr, setWandErr] = useState('')
  const [frames, setFrames] = useState<Record<string, Frame>>({})
  const [fixDup, setFixDup] = useState(false)
  const [peel, setPeel] = useState(false)
  const [toolsOpen, setToolsOpen] = useState(false)
  const toolsRef = useClickOutside<HTMLDivElement>(toolsOpen, () => setToolsOpen(false))
  useEffect(() => { setToolsOpen(false) }, [nk])

  const reloadDetail = useCallback(() => { api.detail(nk).then(setD).catch(() => {}) }, [nk])
  useEffect(() => { reloadDetail() }, [reloadDetail])
  useEffect(() => { setFrames(d?.framing ?? {}) }, [d?.framing])
  useEffect(() => { setMedia(null); api.mediaLibrary(nk).then(setMedia).catch(() => {}) }, [nk])
  useEffect(() => {
    api.mediaKinds().then((r) => {
      setKinds(r.kinds)
      r.kinds.forEach((k, i) => { KIND_ORDER[k.kind] = i })
    }).catch(() => {})
  }, [])

  // Single-game magic wand: fire-and-forget. Kick off a background scan job, fling
  // a ✨ toward the Jobs monitor, and tell the user to review/accept it there — so
  // they can keep browsing and queue up as many as they like. No inline waiting.
  const runWand = (e: ReactMouseEvent) => {
    if (!d) return
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
    setWandErr('')
    // scan by the BARE norm_key (base), not the entry_key — the aimeta pipeline
    // (game_context) looks games up by norm_key, so passing "…@gba" silently no-ops.
    api.aimetaScan({ norm_keys: [base], label: d.title, media: true, metadata: true, web: true })
      .then(() => {
        flyToJobs(rect)
        showToast('✨ Magic sent to the job monitor — check there for status & to accept')
        setWandSent(true)
        window.setTimeout(() => setWandSent(false), 5000)
      })
      .catch((err) => setWandErr((err as Error).message))
  }

  const assets = media?.assets ?? []
  const pickKind = (kind: string) => {
    const of = assets.filter((a) => a.kind === kind && a.is_image)
    return of.find((a) => a.pinned) ?? of[0] ?? null
  }
  // a game is "identified" once it's a known title (a provider match or a real
  // store/manual source); a bare ROM (emulation/archive only, no match) is not.
  const identified = (d?.metadata_links?.length ?? 0) > 0 ||
    (d?.sources ?? []).some((s) => !NON_ID_SRC.has(s.source))
  const bgKind = pickKind('hero') ? 'hero' : pickKind('background') ? 'background'
    : pickKind('header') ? 'header' : null
  const bg = bgKind ? pickKind(bgKind) : null
  const logo = pickKind('logo')
  // No wide hero art → float ALL of the game's images by, right-to-left in a loop,
  // so a cover/screenshot-only game still gets a lively header instead of a swatch.
  const marquee = bg ? [] : assets.filter((a) => a.is_image && a.kind !== 'logo')

  return (
    <div className="overlay" onClick={close}>
      <div className="panel game-panel" onClick={(e) => e.stopPropagation()}>
        <button className="close" onClick={close}>×</button>
        {d && (
          <div className="hero-tools" ref={toolsRef}>
            <button className="hero-tools-btn" title="Game tools"
              aria-label="Game tools" onClick={() => setToolsOpen((o) => !o)}>🧰</button>
            {toolsOpen && (
              <div className="hero-tools-menu">
                <button onClick={(e) => { runWand(e); setToolsOpen(false) }}>
                  <span className="htm-ic">✨</span> Magic wand
                  <span className="htm-sub">AI-enrich (review in Jobs)</span></button>
                <button onClick={() => { setToolsOpen(false); setFixDup(true) }}>
                  <span className="htm-ic">⧉</span> Fix duplication
                  <span className="htm-sub">Merge a duplicate entry in</span></button>
                <button onClick={() => { setToolsOpen(false); setPeel(true) }}>
                  <span className="htm-ic">✂</span> Peel apart
                  <span className="htm-sub">Split two same-named games</span></button>
              </div>
            )}
          </div>
        )}
        {fixDup && d && (
          <FixDupModal nk={base} title={d.title} onClose={() => setFixDup(false)}
            onMerged={(canon) => { setFixDup(false); if (canon === nk) reloadDetail(); else onClose() }} />
        )}
        {peel && d && (
          <PeelModal nk={base} title={d.title} onClose={() => setPeel(false)}
            onPeeled={() => { setPeel(false); reloadDetail() }} />
        )}
        {!d ? <div className="loading">Loading…</div> : (
          <>
            <div className={'hero' + (bg ? '' : marquee.length ? ' hero-marquee-mode' : ' hero-plain')}
                 style={(bg || marquee.length) ? undefined : { ['--h' as string]: hueOf(d.title) } as CSSProperties}>
              {bg && (
                <div className="hero-bg-frame" style={frameStyle(bgKind ? frames[bgKind] : undefined)}>
                  <img className="hero-bg" src={bg.url} alt="" />
                </div>
              )}
              {!bg && marquee.length > 0 && (
                <div className="hero-marquee" aria-hidden="true">
                  <div className="hero-marquee-track"
                       style={{ animationDuration: Math.max(18, marquee.length * 7) + 's' }}>
                    {[...marquee, ...marquee].map((a, i) => (
                      <img key={i} className="hero-marquee-img" src={a.url} alt="" loading="lazy" />
                    ))}
                  </div>
                </div>
              )}
              <div className="hero-shade" />
              <div className="hero-fg">
                {logo
                  ? <img className="hero-logo" src={logo.url} alt={d.title} />
                  : <h2 className="hero-title">{d.title}</h2>}
                <div className="hero-sub">{d.title}</div>
                {(d.platform || (d.also_owned_on && d.also_owned_on.length > 0)) && (
                  <div className="also-on">
                    {d.platform && <span className="also-on-cur">{d.platform}</span>}
                    {d.also_owned_on && d.also_owned_on.length > 0 && (
                      <>
                        <span className="also-on-label">also owned on</span>
                        {d.also_owned_on.map((s) => (
                          <button key={s.entry_key} className={'also-on-chip' + (s.via ? ' via' : '')} type="button"
                            onClick={() => onNavigate?.(s.entry_key)}
                            title={s.via ? `Owned via “${s.via}” (${s.platform})` : `View ${s.title} on ${s.platform}`}>
                            {s.platform}{s.via ? ' 📦' : ''}</button>
                        ))}
                      </>
                    )}
                  </div>
                )}
              </div>
              {(wandSent || wandErr) && (
                <span className={'hero-wand-note' + (wandErr ? ' err' : '')}>
                  {wandErr || '✨ Sent to the job monitor — review & accept there'}</span>
              )}
            </div>

            <ArtStrip nk={nk} assets={assets} loading={!media}
              kinds={kinds} onChange={(m) => { setMedia(m); setMediaDirty(true) }}
              frames={frames} onFrame={(k, fr) => setFrames((p) => {
                const n = { ...p }; if (fr) n[k] = fr; else delete n[k]; return n
              })} />

              <div className="panel-body">
                <Achievements nk={d.norm_key} />

                {d.ai_meta && d.ai_meta.status !== 'rejected' &&
                  <AiMetaCallout finding={d.ai_meta} onChanged={reloadDetail} />}

                <About attrs={d.attributes} scores={d.scores} prov={d.attribute_provenance} />

                <AttributeProvenance d={d} onChanged={reloadDetail} />

                <TagSection nk={d.norm_key} initial={d.tags} />

                <section>
                  <h3>In your library
                    <span className="sec-help">how you have (or want) this game — one row per format, store entry, or console</span>
                  </h3>
                  <table className="sources-table">
                    <thead>
                      <tr>
                        <th>Status</th>
                        <th>Source</th>
                        <th>System</th>
                        <th>OS</th>
                        <th>Listed as</th>
                        <th>Collection</th>
                        <th>Detail</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.sources.map((s, i) => (
                        <tr key={i} className={s.state === 'want' ? 'src-want' : ''}>
                          <td>{s.state === 'want'
                            ? <span className="own-pill want">🕗 Want</span>
                            : !identified && NON_ID_SRC.has(s.source)
                            ? <span className="own-pill unmatched" title="Not identified yet — identify it (Magic wand or manually) to add it to your library">◌ Unmatched</span>
                            : <span className="own-pill have">✓ Have</span>}</td>
                          <td className="badge" style={{ color: providerColor(s.source) }}>
                            {s.source === 'physical' ? '💿 physical' : s.source}</td>
                          <td>{systemLabel(s.source, s.platform)
                            ?? <span className="dim">—</span>}</td>
                          <td>{s.os && s.os.length
                            ? <span className="os-cell">{s.os.map((o) =>
                                <span key={o} className="os-badge" title={OS_NAME[o] ?? o}>
                                  {OS_ABBR[o] ?? o}</span>)}</span>
                            : <span className="dim">—</span>}</td>
                          <td>{s.title_raw}</td>
                          <td>{s.collection
                            ? <span className="own-pill coll" title={`Owned as part of “${s.collection}”`}>📦 Yes</span>
                            : <span className="dim">—</span>}</td>
                          <td className="dim">{s.detail || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <OwnershipEditor nk={d.norm_key} title={d.title} facts={d.ownership ?? []} onChanged={reloadDetail} />
                </section>

                {d.collection && d.collection.members.length > 0 && (
                  <section className="coll-section">
                    <h3>📦 This is a collection
                      <span className="sec-help">a compilation you own — it credits ownership to each game inside, so they show “owned … via {d.collection.name}”</span>
                    </h3>
                    <ul className="coll-members">
                      {d.collection.members.map((m) => (
                        <li key={m.member_key}>
                          <span className="coll-m-title">{m.member_title}</span>
                          {m.member_platform && <span className="dim">{m.member_platform}</span>}
                          {m.member_year != null && <span className="dim">({m.member_year})</span>}
                        </li>
                      ))}
                    </ul>
                  </section>
                )}

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
          </>
        )}
      </div>
    </div>
  )
}

// Horizontally-scrollable strip of every art asset the game actually has, with
// image kinds that render into a fixed viewport, so framing (position+zoom) applies
const FRAMABLE_KINDS = new Set(['cover', 'background', 'hero', 'header', 'fanart'])
const VIDEO_EXTS = new Set(['mp4', 'webm', 'mov', 'm4v', 'ogv'])
const isVideo = (a: MediaAsset) => !a.is_image && VIDEO_EXTS.has((a.ext || '').toLowerCase())
const isPdf = (a: MediaAsset) => (a.ext || '').toLowerCase() === 'pdf'
// sources that don't, by themselves, identify a game (just a file/ownership fact)
const NON_ID_SRC = new Set(['emulation', 'archive', 'physical', 'rom', 'digital'])
// The strip under the hero: compact icon buttons for the browse-worthy media
// collections (screenshots / videos / manuals). Every other kind is still
// collected + managed in the All Media tab — this just surfaces these three;
// clicking one opens the per-kind overview (MediaKindOverlay).
const STRIP_KINDS: { kind: string; icon: string; label: string }[] = [
  { kind: 'screenshot', icon: '📷', label: 'Screenshots' },
  { kind: 'video', icon: '🎬', label: 'Videos' },
  { kind: 'manual', icon: '📖', label: 'Manuals' },
]
function ArtStrip({ nk, assets, loading, kinds, onChange, frames, onFrame }: {
  nk: string; assets: MediaAsset[]; loading: boolean; kinds: MediaKind[]
  onChange: (m: MediaLibrary) => void
  frames?: Record<string, Frame>; onFrame?: (kind: string, f: Frame | undefined) => void
}) {
  const [openKind, setOpenKind] = useState<MediaKind | null>(null)
  const [allOpen, setAllOpen] = useState(false)
  const items = STRIP_KINDS
    .map((s) => ({ ...s, n: assets.filter((a) => a.kind === s.kind).length,
                   mk: kinds.find((x) => x.kind === s.kind) }))
    .filter((s) => s.mk)
  if (loading) return <div className="art-strip loading-sm">Loading media…</div>
  return (
    <div className="media-strip">
      {items.map((s) => (
        <button key={s.kind} className={'ms-btn' + (s.n ? '' : ' empty')} disabled={!s.n}
          title={s.n ? `View ${s.n} ${s.label.toLowerCase()}` : `No ${s.label.toLowerCase()} yet`}
          onClick={() => s.mk && setOpenKind(s.mk)}>
          <span className="ms-icon">{s.icon}</span>
          <span className="ms-text">{s.label}</span>
          <span className="ms-count">{s.n || 0}</span>
        </button>
      ))}
      <button className="ms-btn ms-all" title="Browse & manage every media type — add, reorder, frame, ban"
        onClick={() => setAllOpen(true)}>
        <span className="ms-icon">🗂</span>
        <span className="ms-text">All Media</span>
      </button>
      {openKind && (
        <MediaKindOverlay nk={nk} kind={openKind}
          assets={assets.filter((a) => a.kind === openKind.kind)}
          onChange={onChange} onClose={() => setOpenKind(null)}
          frames={frames} onFrame={onFrame} />
      )}
      {allOpen && (
        <div className="overlay" onClick={() => setAllOpen(false)}>
          <div className="panel mko-panel allmedia-panel" onClick={(e) => e.stopPropagation()}>
            <button className="close" onClick={() => setAllOpen(false)}>×</button>
            <AllMedia nk={nk} kinds={kinds} assets={assets} onChange={onChange}
              frames={frames} onFrame={onFrame} />
            <ArtPicker nk={nk} />
          </div>
        </div>
      )}
    </div>
  )
}

// The full media classification vocabulary — every kind, present or not, with a
// tooltip explaining what it is and why it exists.
function AllMedia({ nk, kinds, assets, onChange, frames, onFrame }: {
  nk: string; kinds: MediaKind[]; assets: MediaAsset[]
  onChange: (m: MediaLibrary) => void
  frames?: Record<string, Frame>; onFrame?: (kind: string, f: Frame | undefined) => void
}) {
  const byKind: Record<string, MediaAsset[]> = {}
  assets.forEach((a) => { (byKind[a.kind] ??= []).push(a) })
  return (
    <section className="all-media">
      <h3>All Media <span className="am-note">every classification — click a count to open its media; frame the #1 image's position &amp; zoom with ⚙ there</span></h3>
      <div className="am-grid">
        {kinds.map((k) => (
          <MediaKindCard key={k.kind} nk={nk} kind={k}
            assets={byKind[k.kind] ?? []} onChange={onChange}
            frames={frames} onFrame={onFrame} />
        ))}
      </div>
    </section>
  )
}

// Overlay gallery of every asset of ONE media kind: drag to set priority order,
// click any item to view it enlarged (close returns here), videos play inline.
function MediaKindOverlay({ nk, kind, assets, onClose, onChange, frames, onFrame }: {
  nk: string; kind: MediaKind; assets: MediaAsset[]; onClose: () => void
  onChange: (m: MediaLibrary) => void
  frames?: Record<string, Frame>; onFrame?: (kind: string, f: Frame | undefined) => void
}) {
  useScrollLock()
  // Order = the priority actually used: pinned rank first; otherwise the chosen/used
  // asset floats to the top, then the rest by id (stable). So position #1 is always
  // what the game displays.
  const sortKey = (a: MediaAsset) => a.rank != null ? a.rank : (a.chosen ? -1 : 1e9)
  const byRank = (list: MediaAsset[]) =>
    [...list].sort((a, b) => sortKey(a) - sortKey(b) || a.id - b.id)
  const framable = FRAMABLE_KINDS.has(kind.kind) && !!onFrame
  const [order, setOrder] = useState<MediaAsset[]>(() => byRank(assets))
  const [viewing, setViewing] = useState<MediaAsset | null>(null)
  const [drag, setDrag] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { setOrder(byRank(assets)) }, [assets])

  const persist = async (next: MediaAsset[]) => {
    setBusy(true)
    try { onChange(await api.setPins(nk, kind.kind, next.map((a) => a.id))) }
    catch { /* */ } finally { setBusy(false) }
  }
  const drop = (to: number) => {
    if (drag === null || drag === to) { setDrag(null); return }
    const next = [...order]
    next.splice(to, 0, next.splice(drag, 1)[0])
    setOrder(next); setDrag(null); persist(next)
  }
  const act = async (fn: () => Promise<MediaLibrary>) => {
    setBusy(true)
    try { onChange(await fn()) } catch { /* */ } finally { setBusy(false) }
  }
  const redist = (a: MediaAsset, val: boolean) => act(() => api.setMediaRedist(nk, a.id, val))
  const remove = (a: MediaAsset) => {
    if (a.user) { act(() => api.deleteUserMedia(nk, a.id)); return }
    if (window.confirm(`Ban this ${a.kind.replace(/_/g, ' ')}?\n\nIt'll be deleted and `
      + `never re-downloaded from ${a.provider}. You can unban it later in `
      + `Settings › Banned media.`)) act(() => api.banMedia(nk, a.id))
  }
  const thumb = (a: MediaAsset) => isVideo(a)
    ? <video src={a.url} muted preload="metadata" playsInline />
    : (a.thumb || a.is_image)
    ? <img src={a.thumb || a.url} alt={a.kind} loading="lazy" />
    : <span className="mko-file">{(a.ext || 'file').toUpperCase()}</span>

  return (
    <div className="overlay" onClick={onClose}>
      <div className="panel mko-panel" onClick={(e) => e.stopPropagation()}>
        <button className="close" onClick={onClose}>×</button>
        <h2 className="mko-title">{kind.kind.replace(/_/g, ' ')}
          <span className="dim"> · {order.length}</span>
          {busy && <span className="dim mko-saving"> · saving…</span>}</h2>
        <p className="mko-desc dim">Drag to set priority — <b>#1 is the one used</b>
          · click to enlarge{framable ? ' · ⚙ on #1 frames its position & zoom' : ''}
          {kind.description ? ' · ' + kind.description : ''}</p>
        {order.length === 0
          ? <div className="sync-note dim">No media of this type yet.</div>
          : (
            <div className="mko-grid">
              {order.map((a, i) => (
                <figure key={a.id} className={'mko-item' + (drag === i ? ' dragging' : '')}
                  draggable onDragStart={() => setDrag(i)}
                  onDragOver={(e) => e.preventDefault()} onDrop={() => drop(i)}
                  onDragEnd={() => setDrag(null)}>
                  <div className="mko-media" onClick={() => setViewing(a)} title="Click to enlarge">
                    {thumb(a)}
                    <span className={'mko-rank' + (i === 0 ? ' used' : '')}
                      title={i === 0 ? 'Used for this game' : 'Priority order'}>{i + 1}</span>
                    {framable && i === 0 && (
                      <div className="mko-frame-gear" onClick={(e) => e.stopPropagation()}>
                        <FrameEditor nk={nk} kind={kind.kind} value={frames?.[kind.kind]}
                          label={kind.kind} onChange={(fr) => onFrame!(kind.kind, fr)} />
                      </div>
                    )}
                  </div>
                  <figcaption className="mko-cap">
                    <span>{a.provider}{a.user ? ' · yours' : ''}{i === 0 ? ' · used' : ''}</span>
                    {a.width ? <span className="dim">{a.width}×{a.height}</span> : null}
                  </figcaption>
                  <div className="mko-ctl">
                    <label className="mko-redist"
                      title="Uncheck to keep this locally but NOT copy it to other machines when games are sent to them">
                      <input type="checkbox" checked={a.redistributable !== false}
                        onChange={(e) => redist(a, e.target.checked)} /> shareable
                    </label>
                    {a.user
                      ? <button className="mko-remove" title="Delete this upload"
                          onClick={() => remove(a)}>🗑</button>
                      : <button className="mko-remove" title="Ban — delete & never re-download from the provider"
                          onClick={() => remove(a)}>🚫</button>}
                  </div>
                </figure>
              ))}
            </div>
          )}
      </div>
      {viewing && (
        <div className="overlay mko-view" onClick={() => setViewing(null)}>
          <button className="close" onClick={() => setViewing(null)}>×</button>
          <div className="mko-view-inner" onClick={(e) => e.stopPropagation()}>
            {viewing.is_image
              ? <img src={viewing.url} alt={viewing.kind} />
              : isVideo(viewing)
              ? <video src={viewing.url} controls autoPlay playsInline />
              : isPdf(viewing)
              ? <iframe className="mko-pdf" src={viewing.url} title={viewing.kind} />
              : <a className="mko-file big" href={viewing.url} target="_blank" rel="noreferrer">
                  Open {(viewing.ext || 'file').toUpperCase()}</a>}
          </div>
        </div>
      )}
    </div>
  )
}

// One media-kind card with an upload affordance: paste a direct URL (server
// downloads it) or upload a file from the device. User uploads show as removable
// thumbnails and take precedence as the game's art for that kind. Clicking the
// name/count opens a gallery overlay of every asset of this kind.
function MediaKindCard({ nk, kind, assets, onChange, frames, onFrame }: {
  nk: string; kind: MediaKind; assets: MediaAsset[]
  onChange: (m: MediaLibrary) => void
  frames?: Record<string, Frame>; onFrame?: (kind: string, f: Frame | undefined) => void
}) {
  const [open, setOpen] = useState(false)
  const [gallery, setGallery] = useState(false)
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
        <span className={'am-name' + (n ? ' am-clickable' : '')}
          title={n ? `View all ${kind.kind.replace(/_/g, ' ')} media` : kind.description}
          onClick={() => { if (n) setGallery(true) }}>{kind.kind.replace(/_/g, ' ')}</span>
        <span className="am-actions">
          <button className={'am-badge' + (n ? ' am-clickable' : '')} disabled={!n}
            title={n ? 'View all' : undefined}
            onClick={() => { if (n) setGallery(true) }}>{n ? `×${n}` : '—'}</button>
          {onFrame && (
            /* Position/zoom framing moved into the media overlay (⚙ on the #1 image).
               The gear stays here, grayed, reserved for future per-category settings. */
            <FrameEditor nk={nk} kind={kind.kind} value={frames?.[kind.kind]} label={kind.kind}
              onChange={(fr) => onFrame(kind.kind, fr)} disabled />
          )}
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
              {isVideo(a)
                ? <video src={a.url} muted controls preload="metadata" playsInline />
                : (a.thumb || a.is_image)
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
      {gallery && <MediaKindOverlay nk={nk} kind={kind} assets={assets}
        onChange={onChange} onClose={() => setGallery(false)}
        frames={frames} onFrame={onFrame} />}
    </div>
  )
}

// Rotating "Spotlight": a themed top-N (overall / per-platform / per-store /
// per-decade / underrated / hidden gems…) that auto-shuffles, or shuffle by hand.
// A thin countdown bar depletes right→left over the (configurable) dwell time and
// drives the rotation: when it finishes it loads the next theme. Hovering pauses
// the bar — and therefore the rotation — so you can read/click without it moving.
function SpotlightSection({ onOpen, prefsTick, onOpenSettings }: {
  onOpen: (nk: string) => void; prefsTick: number; onOpenSettings: (section?: string) => void
}) {
  const [sp, setSp] = useState<SpotlightData | null>(null)
  const [loading, setLoading] = useState(false)
  const [seconds, setSeconds] = useState(12)
  const [cycle, setCycle] = useState(0)   // remounts the timer bar → restarts it
  const [paused, setPaused] = useState(false)
  const kindRef = useRef<string | undefined>(undefined)   // last theme, to avoid repeats

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const next = await api.spotlight('random', kindRef.current)
      kindRef.current = next.kind
      setSp(next)
    } catch { /* offline */ }
    finally { setLoading(false); setCycle((c) => c + 1) }
  }, [])
  useEffect(() => { load() }, [load])
  // Dwell time comes from prefs; re-read when the setting changes (prefsTick).
  useEffect(() => {
    api.prefs().then((p) => setSeconds(p.spotlight_seconds)).catch(() => {})
  }, [prefsTick])
  // Rotate on a real timer (not the CSS animationend, which can silently miss a
  // fire when the tab is backgrounded/paused and leave the spotlight stuck).
  useEffect(() => {
    if (paused || loading || !sp) return
    const t = window.setTimeout(load, Math.max(3, seconds) * 1000)
    return () => window.clearTimeout(t)
  }, [paused, loading, sp, seconds, load])
  // The rotate timeout resets to a full `seconds` whenever it (re)arms — e.g. on
  // un-hover. Restart the CSS bar in lockstep so it never finishes early and sits
  // empty while the (fresh, longer) timeout is still counting down.
  useEffect(() => {
    if (!paused && !loading && sp) setCycle((c) => c + 1)
  }, [paused, seconds])   // eslint-disable-line react-hooks/exhaustive-deps

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
        <div className="sl-actions">
          <button className="sl-shuffle" title="Shuffle spotlight"
            onClick={load} disabled={loading}>
            {/* SVG (circle centered at viewBox 12,12) so it spins around its true
                center — a text ⟳ glyph is off-center in its box and wobbles. */}
            <svg className={'sl-ico' + (loading ? ' spin' : '')} viewBox="0 0 24 24"
              width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2.2"
              strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M23 4v6h-6" />
              <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
            </svg></button>
          <button className="sl-shuffle" title="Spotlight settings"
            onClick={() => onOpenSettings('dashboard')} aria-label="Spotlight settings">⚙</button>
        </div>
      </div>
      <div className="sl-timer-track">
        <div key={cycle} className="sl-timer"
          style={{ animationDuration: seconds + 's', animationPlayState: paused ? 'paused' : 'running' }} />
      </div>
      <div className={'sl-row' + (loading ? ' fading' : '')}>
        {sp.items.map((g, i) => (
          <button key={g.entry_key ?? g.norm_key} className="sl-card"
            onClick={() => onOpen(g.entry_key ?? g.norm_key)} title={g.title}>
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

function Dashboard({ stats, onBrowse, onFilter, onOpen, prefsTick, onOpenSettings }: {
  stats: Stats | null; onBrowse: () => void; onFilter: (f: FilterState) => void
  onOpen: (nk: string) => void; prefsTick: number; onOpenSettings: (section?: string) => void
}) {
  // Spotlight fetches its own (fast) data, so render it immediately — don't hide it
  // behind the slower stats() call that gates the rest of the dashboard.
  return (
    <div className="dashboard">
      <SpotlightSection onOpen={onOpen} prefsTick={prefsTick} onOpenSettings={onOpenSettings} />
      {!stats
        ? <div className="loading">Loading…</div>
        : <DashStats stats={stats} onBrowse={onBrowse} onFilter={onFilter} />}
    </div>
  )
}

function DashStats({ stats, onBrowse, onFilter }: {
  stats: Stats; onBrowse: () => void; onFilter: (f: FilterState) => void
}) {
  const artPct = stats.games ? Math.round((stats.media.games_with_art / stats.games) * 100) : 0
  const pct = (n: number) => stats.games ? Math.round((n / stats.games) * 100) : 0
  const sources = Object.entries(stats.by_source).sort((a, b) => b[1] - a[1])
  const kinds = Object.entries(stats.media.by_kind).sort((a, b) => b[1] - a[1])
  return (
    <>
      <div className="dash-cards">
        <div className="dash-card">
          <div className="dc-num">{(stats.identified ?? stats.games).toLocaleString()}</div>
          <div className="dc-label">Games{!!stats.unidentified &&
            <span className="dc-sub"> · {stats.unidentified.toLocaleString()} unidentified</span>}</div>
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
    </>
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

function ProgressBar({ done, total, failed, running }:
  { done: number; total: number; failed: number; running?: boolean }) {
  const pct = total ? Math.round((done / total) * 100) : 0
  const fpct = total ? Math.round((failed / total) * 100) : 0
  // a running job with no measurable progress yet shows an indeterminate sweep;
  // otherwise the real % (nudged to a visible sliver so it never looks empty).
  const indeterminate = !!running && pct === 0
  return (
    <div className={'run-progress' + (running ? ' running' : '') + (indeterminate ? ' indet' : '')}
      title={`${done}/${total} done${failed ? `, ${failed} failed` : ''}`}>
      <span className="rp-done" style={indeterminate ? undefined : { width: Math.max(pct, running ? 4 : 0) + '%' }} />
      {failed > 0 && <span className="rp-fail" style={{ width: fpct + '%' }} />}
    </div>
  )
}

function useDevices() {
  const [devices, setDevices] = useState<Device[]>([])
  useEffect(() => { api.devices().then((d) => setDevices(d.devices)).catch(() => {}) }, [])
  return devices
}

// ---- Files tab: a dedicated top-level home for file-structure work ----------
const joinPath = (base: string, name: string) =>
  (base === '/' ? '' : base.replace(/\/$/, '')) + '/' + name

// ---- Commander: a dual-pane file manager (Total-Commander style) ------------
// Each pane is rooted at a device; you navigate, multi-select and drag files
// between panes. A drop stages a Move/Copy (preview) that you Apply — or runs
// immediately (per the fileops_apply_mode setting). Same-device ops go through
// the reversible runbook engine (undoable in History); cross-device ops run as
// rsync background jobs (the Jobs monitor).
type PaneState = {
  deviceId: number; path: string
  dirs: { name: string; nfiles: number }[]
  files: { name: string; size: number }[]
  loading: boolean; error?: string
  sel: Set<string>; anchor: string | null
}
type Dragload = { deviceId: number; dir: string; names: string[]; sizes: Record<string, number> }
type Staged = {
  srcDevice: number; srcDir: string; items: string[]
  dstDevice: number; dstDir: string; mode: 'move' | 'copy'
  total: number; overwrites: string[]
}
type Side = 'left' | 'right'
const newPane = (deviceId: number): PaneState =>
  ({ deviceId, path: '/', dirs: [], files: [], loading: false, sel: new Set(), anchor: null })
const orderedNames = (p: PaneState) => [...p.dirs.map((d) => d.name), ...p.files.map((f) => f.name)]
const parentPath = (p: string) => {
  const q = p.replace(/\/+$/, ''); const i = q.lastIndexOf('/')
  return i <= 0 ? '/' : q.slice(0, i)
}
const commonAncestor = (paths: string[]): string => {
  const split = paths.map((p) => p.replace(/\/+$/, '').split('/').filter(Boolean))
  const first = split[0] || []; const out: string[] = []
  for (let i = 0; i < first.length; i++) {
    if (split.every((s) => s[i] === first[i])) out.push(first[i]); else break
  }
  return '/' + out.join('/')
}
const relTo = (root: string, abs: string) =>
  abs.replace(/\/+$/, '').slice(root === '/' ? 1 : root.length + 1)
const shortPath = (p: string) => { const s = p.split('/').filter(Boolean); return s.length > 2 ? '…/' + s.slice(-2).join('/') : p }

function CommanderPane({ pane, devices, active, showChecks, onActivate, onDevice, onNavigate,
  onClickRow, onToggleCheck, onContextRow, onContextPane, onDragStartRow, onDropInto,
  onRefresh, onNewFolder, onDelete }: {
  pane: PaneState; devices: Device[]; active: boolean; showChecks: boolean
  onActivate: () => void
  onDevice: (id: number) => void
  onNavigate: (path: string) => void
  onClickRow: (name: string, e: ReactMouseEvent) => void
  onToggleCheck: (name: string) => void
  onContextRow: (name: string, e: ReactMouseEvent) => void
  onContextPane: (e: ReactMouseEvent) => void
  onDragStartRow: (name: string, e: DragEvent) => void
  onDropInto: (dstDir: string, dstNames: string[] | null) => void
  onRefresh: () => void; onNewFolder: () => void; onDelete: () => void
}) {
  const [over, setOver] = useState<string | null>(null)
  const segs = pane.path.split('/').filter(Boolean)
  const devName = pane.deviceId === 0 ? 'This server'
    : (devices.find((d) => d.id === pane.deviceId)?.name || 'Device ' + pane.deviceId)
  const allow = (e: DragEvent) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move' }
  const check = (name: string) => showChecks && (
    <input type="checkbox" className="cmd-check" checked={pane.sel.has(name)}
      onClick={(e) => e.stopPropagation()} onChange={() => onToggleCheck(name)} />
  )

  return (
    <div className={'cmd-pane' + (active ? ' active' : '')} onMouseDown={onActivate}>
      <div className="cmd-head">
        <select className="cmd-dev" value={pane.deviceId}
          onChange={(e) => onDevice(Number(e.target.value))}>
          <option value={0}>This server (local)</option>
          {devices.map((d) => <option key={d.id} value={d.id}>{d.name}{d.host ? ` (${d.host})` : ''}</option>)}
        </select>
        <div className="cmd-tools">
          <button title="Up" disabled={pane.path === '/'} onClick={() => onNavigate(parentPath(pane.path))}>↑</button>
          <button title="Refresh" onClick={onRefresh}>⟳</button>
          <button title="New folder" onClick={onNewFolder}>＋</button>
          <button title="Delete selected" disabled={!pane.sel.size} onClick={onDelete}>🗑</button>
        </div>
      </div>
      <div className="cmd-crumb">
        <button className="crumb" onClick={() => onNavigate('/')}>🖥 {devName}</button>
        {segs.map((s, i) => (
          <Fragment key={i}>
            <span className="crumb-sep">/</span>
            <button className="crumb" onClick={() => onNavigate('/' + segs.slice(0, i + 1).join('/'))}>{s}</button>
          </Fragment>
        ))}
      </div>
      <div className={'cmd-list' + (over === '' ? ' drop' : '')}
        onDragOver={allow} onDragLeave={() => setOver(null)}
        onContextMenu={onContextPane}
        onDrop={(e) => { e.preventDefault(); setOver(null); onDropInto(pane.path, orderedNames(pane)) }}>
        {pane.loading && <div className="dim cmd-note">Loading…</div>}
        {pane.error && <div className="fo-warn cmd-note">⚠ {pane.error}</div>}
        {!pane.loading && !pane.error && !pane.dirs.length && !pane.files.length &&
          <div className="dim cmd-note">Empty folder.</div>}
        {pane.dirs.map((d) => (
          <div key={'d/' + d.name}
            className={'cmd-row dir' + (pane.sel.has(d.name) ? ' sel' : '') + (over === d.name ? ' drop' : '')}
            draggable onDragStart={(e) => onDragStartRow(d.name, e)}
            onClick={(e) => onClickRow(d.name, e)}
            onContextMenu={(e) => onContextRow(d.name, e)}
            onDoubleClick={() => onNavigate(joinPath(pane.path, d.name))}
            onDragOver={(e) => { allow(e); setOver(d.name) }}
            onDragLeave={() => setOver((o) => (o === d.name ? null : o))}
            onDrop={(e) => { e.preventDefault(); e.stopPropagation(); setOver(null); onDropInto(joinPath(pane.path, d.name), null) }}>
            {check(d.name)}
            <span className="cmd-ic">📁</span><span className="fb-name">{d.name}</span>
            <span className="dim cmd-meta">{d.nfiles.toLocaleString()}</span>
          </div>
        ))}
        {pane.files.map((f) => (
          <div key={'f/' + f.name}
            className={'cmd-row file' + (pane.sel.has(f.name) ? ' sel' : '')}
            draggable onDragStart={(e) => onDragStartRow(f.name, e)}
            onClick={(e) => onClickRow(f.name, e)}
            onContextMenu={(e) => onContextRow(f.name, e)}>
            {check(f.name)}
            <span className="cmd-ic">📄</span><span className="fb-name">{f.name}</span>
            <span className="dim cmd-meta">{fmtBytes(f.size)}</span>
          </div>
        ))}
      </div>
      <div className="cmd-foot dim">
        {pane.dirs.length + pane.files.length} item{pane.dirs.length + pane.files.length === 1 ? '' : 's'}
        {pane.sel.size > 0 && <> · {pane.sel.size} selected</>}
      </div>
    </div>
  )
}

// Inspect: basic stats for one file/folder on a device (right-click → Inspect).
function InspectModal({ deviceId, path, name, onClose }: {
  deviceId: number; path: string; name: string; onClose: () => void
}) {
  useScrollLock()
  const [st, setSt] = useState<FsStat | null>(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    let live = true
    api.fsStat(deviceId, path).then((s) => { if (live) setSt(s) })
      .catch((e) => { if (live) setErr((e as Error).message) })
    return () => { live = false }
  }, [deviceId, path])
  const isDir = (st?.type || '').includes('directory')
  const rows: [string, string][] = []
  if (st?.ok) {
    rows.push(['Type', isDir ? 'Folder' : (st.type || 'File')])
    if (isDir) {
      rows.push(['Contains', `${(st.dirs ?? 0).toLocaleString()} folder${st.dirs === 1 ? '' : 's'}, ${(st.files ?? 0).toLocaleString()} file${st.files === 1 ? '' : 's'}`])
      rows.push(['Total size', st.total != null ? fmtBytes(st.total) : '—'])
    } else {
      rows.push(['Size', st.size != null ? `${fmtBytes(st.size)} (${st.size.toLocaleString()} bytes)` : '—'])
    }
    if (st.mtime) rows.push(['Modified', `${relTime(st.mtime)} · ${new Date(st.mtime * 1000).toLocaleString()}`])
    if (st.perm) rows.push(['Permissions', `${st.perm}${st.owner ? `  ·  ${st.owner}:${st.group || ''}` : ''}`])
  }
  return (
    <div className="overlay overlay-2" onClick={onClose}>
      <div className="cmd-inspect" onClick={(e) => e.stopPropagation()}>
        <button className="close" onClick={onClose}>×</button>
        <h2>{isDir ? '📁' : '📄'} {name}</h2>
        <code className="dm-path">{path}</code>
        {!st && !err && <div className="loading">Reading…</div>}
        {err && <div className="connect-msg err">{err}</div>}
        {st && !st.ok && <div className="connect-msg err">{st.error || 'Could not stat this path'}</div>}
        {st?.ok && (
          <table className="cmd-inspect-tbl"><tbody>
            {rows.map(([k, v]) => (
              <tr key={k}><th>{k}</th><td>{v}</td></tr>
            ))}
          </tbody></table>
        )}
      </div>
    </div>
  )
}

function Commander() {
  const devices = useDevices()
  const [left, setLeft] = useState<PaneState>(() => newPane(0))
  const [right, setRight] = useState<PaneState>(() => newPane(0))
  const [active, setActive] = useState<Side>('left')
  const [applyMode, setApplyMode] = useState<FileopsApplyMode>('preview')
  const [staged, setStaged] = useState<Staged | null>(null)
  const [openRun, setOpenRun] = useState<number | null>(null)
  const [toast, setToast] = useState('')
  const [busy, setBusy] = useState(false)
  const [showChecks, setShowChecks] = useState(false)
  const [menu, setMenu] = useState<{ side: Side; name: string | null; x: number; y: number } | null>(null)
  const [inspect, setInspect] = useState<{ deviceId: number; path: string; name: string } | null>(null)
  const dragRef = useRef<Dragload | null>(null)
  const setPane = (side: Side, up: (p: PaneState) => PaneState) =>
    (side === 'left' ? setLeft : setRight)(up)
  const paneOf = (side: Side) => (side === 'left' ? left : right)

  useEffect(() => { api.prefs().then((p) => setApplyMode(p.fileops_apply_mode)).catch(() => {}) }, [])

  const loadPane = useCallback(async (side: Side, deviceId: number, path: string) => {
    const p = path.replace(/\/+$/, '') || '/'
    setPane(side, (s) => ({ ...s, deviceId, path: p, loading: true, error: '', sel: new Set(), anchor: null }))
    try {
      const r = await api.browseEntries(deviceId, p)
      setPane(side, (s) => (s.deviceId === deviceId && s.path === p
        ? { ...s, dirs: r.dirs, files: r.files, loading: false, error: r.ok ? '' : r.error } : s))
    } catch (e) {
      setPane(side, (s) => (s.deviceId === deviceId && s.path === p
        ? { ...s, dirs: [], files: [], loading: false, error: (e as Error).message } : s))
    }
  }, [])

  // initial + on-mount load of both panes at their device root
  useEffect(() => { loadPane('left', 0, '/'); loadPane('right', 0, '/') }, [loadPane])

  const onDevice = (side: Side, id: number) => loadPane(side, id, '/')
  const onNavigate = (side: Side, path: string) => loadPane(side, paneOf(side).deviceId, path)
  const refreshBoth = () => { const l = left, r = right; loadPane('left', l.deviceId, l.path); loadPane('right', r.deviceId, r.path) }

  const onClickRow = (side: Side, name: string, e: ReactMouseEvent) => {
    setActive(side)
    setPane(side, (p) => {
      const names = orderedNames(p)
      if (e.shiftKey && p.anchor) {
        const a = names.indexOf(p.anchor), b = names.indexOf(name)
        if (a >= 0 && b >= 0) return { ...p, sel: new Set(names.slice(Math.min(a, b), Math.max(a, b) + 1)) }
      }
      if (e.metaKey || e.ctrlKey) {
        const sel = new Set(p.sel); sel.has(name) ? sel.delete(name) : sel.add(name)
        return { ...p, sel, anchor: name }
      }
      return { ...p, sel: new Set([name]), anchor: name }
    })
  }

  const onDragStartRow = (side: Side, name: string, e: DragEvent) => {
    setActive(side)
    const p = paneOf(side)
    const names = p.sel.has(name) && p.sel.size ? [...p.sel] : [name]
    const sizes: Record<string, number> = {}
    for (const f of p.files) if (names.includes(f.name)) sizes[f.name] = f.size
    dragRef.current = { deviceId: p.deviceId, dir: p.path, names, sizes }
    e.dataTransfer.effectAllowed = 'copyMove'
    e.dataTransfer.setData('text/plain', names.join('\n'))
  }

  const stage = (o: Staged) => { if (applyMode === 'immediate') dispatch(o); else setStaged(o) }

  const onDropInto = (dstSide: Side, dstDir: string, dstNames: string[] | null) => {
    const d = dragRef.current; dragRef.current = null
    if (!d || !d.names.length) return
    const dstDevice = paneOf(dstSide).deviceId
    if (d.deviceId === dstDevice && d.dir === dstDir) return          // dropped where it already lives
    const total = Object.values(d.sizes).reduce((a, b) => a + b, 0)
    const overwrites = dstNames ? d.names.filter((n) => dstNames.includes(n)) : []
    stage({ srcDevice: d.deviceId, srcDir: d.dir, items: d.names,
      dstDevice, dstDir, mode: 'move', total, overwrites })
  }

  const onToggleCheck = (side: Side, name: string) => {
    setActive(side)
    setPane(side, (p) => {
      const sel = new Set(p.sel); sel.has(name) ? sel.delete(name) : sel.add(name)
      return { ...p, sel, anchor: name }
    })
  }
  const onContextRow = (side: Side, name: string, e: ReactMouseEvent) => {
    e.preventDefault(); e.stopPropagation()
    setActive(side)
    setPane(side, (p) => p.sel.has(name) ? p : { ...p, sel: new Set([name]), anchor: name })
    setMenu({ side, name, x: e.clientX, y: e.clientY })
  }
  const onContextPane = (side: Side, e: ReactMouseEvent) => {
    e.preventDefault()
    setActive(side)
    setMenu({ side, name: null, x: e.clientX, y: e.clientY })
  }
  const openInspect = (side: Side, name: string) => {
    const p = paneOf(side)
    setInspect({ deviceId: p.deviceId, path: joinPath(p.path, name), name })
  }

  const sendTo = (mode: 'move' | 'copy', fromSide: Side = active) => {
    const src = paneOf(fromSide), dst = paneOf(fromSide === 'left' ? 'right' : 'left')
    if (!src.sel.size) { setToast('Select files in the ' + fromSide + ' pane first.'); return }
    if (src.deviceId === dst.deviceId && src.path === dst.path) { setToast('Both panes show the same folder.'); return }
    const items = [...src.sel]
    const total = items.reduce((a, n) => a + (src.files.find((f) => f.name === n)?.size || 0), 0)
    const dstNames = orderedNames(dst)
    stage({ srcDevice: src.deviceId, srcDir: src.path, items,
      dstDevice: dst.deviceId, dstDir: dst.path, mode, total,
      overwrites: items.filter((n) => dstNames.includes(n)) })
  }

  const dispatch = async (o: Staged) => {
    setBusy(true); setToast('')
    try {
      if (o.srcDevice === o.dstDevice) {                              // same device → reversible runbook
        const root = commonAncestor([o.srcDir, o.dstDir])
        const ops = o.items.map((n) => ({ op: o.mode,
          src: relTo(root, joinPath(o.srcDir, n)), dst: relTo(root, joinPath(o.dstDir, n)) }))
        const { run_id } = await api.createRunbookOps({ device_id: o.srcDevice, root, ops,
          label: `${o.mode === 'move' ? 'Move' : 'Copy'} ${o.items.length} item${o.items.length === 1 ? '' : 's'}` })
        await api.executeRunbook(run_id)
        setOpenRun(run_id)
      } else {                                                        // cross device → background rsync
        await api.fsTransfer({ src_device: o.srcDevice, dst_device: o.dstDevice,
          src_dir: o.srcDir, dst_dir: o.dstDir, items: o.items, mode: o.mode })
        setToast('Transfer started — track it in the Jobs monitor (top-right).')
      }
      setStaged(null)
      refreshBoth()
    } catch (e) { setToast('Failed: ' + (e as Error).message) }
    finally { setBusy(false) }
  }

  const newFolder = async (side: Side) => {
    const p = paneOf(side)
    const name = window.prompt('New folder name in ' + p.path + ':')?.trim()
    if (!name) return
    try { await api.fsMkdir(p.deviceId, joinPath(p.path, name)); loadPane(side, p.deviceId, p.path) }
    catch (e) { setToast('Create folder failed: ' + (e as Error).message) }
  }
  const del = async (side: Side) => {
    const p = paneOf(side)
    if (!p.sel.size) return
    if (!window.confirm(`Delete ${p.sel.size} item${p.sel.size === 1 ? '' : 's'} — this can't be undone. Continue?`)) return
    try { await api.fsDelete(p.deviceId, [...p.sel].map((n) => joinPath(p.path, n))); loadPane(side, p.deviceId, p.path) }
    catch (e) { setToast('Delete failed: ' + (e as Error).message) }
  }
  const setMode = async (m: FileopsApplyMode) => {
    setApplyMode(m)
    try { await api.setPrefs({ fileops_apply_mode: m }) } catch { /* keep optimistic */ }
  }
  const devName = (id: number) => id === 0 ? 'This server' : (devices.find((d) => d.id === id)?.name || 'Device ' + id)

  const renderPane = (side: Side) => (
    <CommanderPane pane={paneOf(side)} devices={devices} active={active === side} showChecks={showChecks}
      onActivate={() => setActive(side)}
      onDevice={(id) => onDevice(side, id)}
      onNavigate={(path) => onNavigate(side, path)}
      onClickRow={(name, e) => onClickRow(side, name, e)}
      onToggleCheck={(name) => onToggleCheck(side, name)}
      onContextRow={(name, e) => onContextRow(side, name, e)}
      onContextPane={(e) => onContextPane(side, e)}
      onDragStartRow={(name, e) => onDragStartRow(side, name, e)}
      onDropInto={(dstDir, dstNames) => onDropInto(side, dstDir, dstNames)}
      onRefresh={() => loadPane(side, paneOf(side).deviceId, paneOf(side).path)}
      onNewFolder={() => newFolder(side)}
      onDelete={() => del(side)} />
  )

  // Context menu: item actions when a row was right-clicked, else pane actions.
  const menuPane = menu ? paneOf(menu.side) : null
  const menuIsDir = !!(menu?.name && menuPane?.dirs.some((d) => d.name === menu.name))
  const menuMulti = (menuPane?.sel.size || 0) > 1
  const closeMenu = () => setMenu(null)

  return (
    <div className="cmd">
      <div className="cmd-frame">
        <div className="cmd-bar">
          <div className="cmd-bar-ops">
            <button onClick={() => sendTo('copy')} title="Copy active selection to the other pane">Copy →</button>
            <button onClick={() => sendTo('move')} title="Move active selection to the other pane">Move →</button>
          </div>
          <div className="cmd-bar-mode">
            <button className={'cmd-check-toggle' + (showChecks ? ' on' : '')} onClick={() => setShowChecks((v) => !v)}
              title="Show a selection checkbox next to every item">☑ Checkboxes</button>
            <span className="cmd-bar-div" />
            <span className="dim">On drop:</span>
            <button className={applyMode === 'preview' ? 'on' : ''} onClick={() => setMode('preview')}
              title="Stage the operation and let you review it before applying">Preview</button>
            <button className={applyMode === 'immediate' ? 'on' : ''} onClick={() => setMode('immediate')}
              title="Run the operation the instant you drop">Immediate</button>
          </div>
        </div>
        <div className="cmd-panes">
          {renderPane('left')}
          {renderPane('right')}
        </div>
      </div>
      {staged && (
        <div className="cmd-stage">
          <div className="cmd-stage-mode">
            <button className={staged.mode === 'move' ? 'on' : ''} onClick={() => setStaged({ ...staged, mode: 'move' })}>Move</button>
            <button className={staged.mode === 'copy' ? 'on' : ''} onClick={() => setStaged({ ...staged, mode: 'copy' })}>Copy</button>
          </div>
          <div className="cmd-stage-text">
            <b>{staged.items.length}</b> item{staged.items.length === 1 ? '' : 's'} ·{' '}
            {shortPath(staged.srcDir)} → <b>{devName(staged.dstDevice)}</b>:{shortPath(staged.dstDir)}
            {staged.total > 0 && <> · {fmtBytes(staged.total)}</>}
            {staged.srcDevice !== staged.dstDevice
              ? <span className="cmd-stage-x"> · cross-device (background{staged.mode === 'move' ? ', not undoable' : ''})</span>
              : <span className="dim"> · reversible</span>}
            {staged.overwrites.length > 0 && <span className="cmd-stage-warn"> · ⚠ {staged.overwrites.length} would overwrite</span>}
          </div>
          <div className="cmd-stage-act">
            <button className="go" disabled={busy} onClick={() => dispatch(staged)}>{busy ? 'Applying…' : 'Apply'}</button>
            <button className="ghost" onClick={() => setStaged(null)}>Cancel</button>
          </div>
        </div>
      )}
      {toast && <div className="cmd-toast" onClick={() => setToast('')}>{toast}</div>}
      {menu && (
        <>
          <div className="cmd-menu-backdrop" onClick={closeMenu}
            onContextMenu={(e) => { e.preventDefault(); closeMenu() }} />
          <div className="cmd-menu" style={{ left: Math.min(menu.x, window.innerWidth - 220), top: Math.min(menu.y, window.innerHeight - 240) }}>
            {menu.name ? (
              <>
                <div className="cmd-menu-head">{menuMulti ? `${menuPane?.sel.size} items` : menu.name}</div>
                {!menuMulti && menuIsDir &&
                  <button onClick={() => { onNavigate(menu.side, joinPath(menuPane!.path, menu.name!)); closeMenu() }}>Open</button>}
                {!menuMulti &&
                  <button onClick={() => { openInspect(menu.side, menu.name!); closeMenu() }}>Inspect…</button>}
                <button onClick={() => { sendTo('copy', menu.side); closeMenu() }}>Copy → other pane</button>
                <button onClick={() => { sendTo('move', menu.side); closeMenu() }}>Move → other pane</button>
                <div className="cmd-menu-sep" />
                <button className="danger" onClick={() => { del(menu.side); closeMenu() }}>Delete…</button>
              </>
            ) : (
              <>
                <button onClick={() => { newFolder(menu.side); closeMenu() }}>New folder…</button>
                <button onClick={() => { loadPane(menu.side, paneOf(menu.side).deviceId, paneOf(menu.side).path); closeMenu() }}>Refresh</button>
              </>
            )}
          </div>
        </>
      )}
      {inspect && <InspectModal deviceId={inspect.deviceId} path={inspect.path} name={inspect.name}
        onClose={() => setInspect(null)} />}
      {openRun !== null && <RunbookModal runId={openRun} onClose={() => { setOpenRun(null); refreshBoth() }} />}
    </div>
  )
}

function FilesTab() {
  const [sub, setSub] = useState<'browse' | 'operations' | 'profiles' | 'history'>('browse')
  return (
    <div className="files-tab">
      <ParticleTabs className="sub-tabs" active={sub}
        onSelect={(id) => setSub(id as 'browse' | 'operations' | 'profiles' | 'history')}
        tabs={[{ id: 'browse', label: 'Browse' }, { id: 'operations', label: 'Operations' },
               { id: 'profiles', label: 'Profiles' }, { id: 'history', label: 'History' }]} />
      <div className="files-body">
        {sub === 'browse' ? <Commander />
          : sub === 'operations' ? <FileOpsOperations />
          : sub === 'profiles' ? <FileProfiles />
          : <FileHistory />}
      </div>
    </div>
  )
}

// Build a nested tree from a flat list of POSIX paths (for the Before/After panels).
type TNode = { name: string; children: Record<string, TNode>; isFile: boolean }
function pathsToTree(paths: string[]): TNode {
  const root: TNode = { name: '', children: {}, isFile: false }
  for (const p of paths) {
    let cur = root
    const parts = p.split('/').filter(Boolean)
    parts.forEach((seg, i) => {
      if (!cur.children[seg]) cur.children[seg] = { name: seg, children: {}, isFile: false }
      cur = cur.children[seg]
      if (i === parts.length - 1) cur.isFile = true
    })
  }
  return root
}
function TreeRows({ node, depth = 0 }: { node: TNode; depth?: number }) {
  const entries = Object.values(node.children).sort((a, b) =>
    a.isFile === b.isFile ? a.name.localeCompare(b.name) : a.isFile ? 1 : -1)
  return (
    <>
      {entries.map((c) => (
        <Fragment key={c.name}>
          <div className={c.isFile ? 'ft-file' : 'ft-dir'} style={{ paddingLeft: depth * 14 + 6 }}>
            {c.isFile ? '📄' : '📁'} <span className="fb-name">{c.name}</span>
          </div>
          {!c.isFile && <TreeRows node={c} depth={depth + 1} />}
        </Fragment>
      ))}
    </>
  )
}

// Above this file count, don't auto-plan (planning reads every file) — make it an
// explicit "Preview plan" click. Detection itself is always cheap (bounded).
const AUTO_PLAN_LIMIT = 5000

// Seconds elapsed while `active`, for "working…" readouts. Resets when it stops.
function useElapsed(active: boolean) {
  const [s, setS] = useState(0)
  useEffect(() => {
    if (!active) { setS(0); return }
    const t0 = Date.now()
    const iv = setInterval(() => setS(Math.round((Date.now() - t0) / 1000)), 500)
    return () => clearInterval(iv)
  }, [active])
  return s
}

// Plain-language explanation of each operation type, shown under the picker.
const OP_INFO: Record<'restructure' | 'extract', {
  blurb: string; points: string[]; safe: string
}> = {
  restructure: {
    blurb: 'Reorganize an existing ROM folder so its structure matches a layout profile — ' +
      'renaming and re-nesting files and folders to whatever your emulator or frontend expects. ' +
      'The games themselves are never opened or altered.',
    points: [
      'Flatten, or group into per-system folders, to match the chosen profile',
      'Generate .m3u playlists so multi-disc games load as one entry',
      'Normalize names (regions, tags, brackets) and prune the empty folders left behind',
    ],
    safe: 'Nothing is downloaded or deleted — every file is moved in place, and the whole change ' +
      'applies as a single reversible runbook you can undo from History.',
  },
  extract: {
    blurb: 'Pull the box art, screenshots and logos that are tangled up inside your ROM folders ' +
      '(loose images, per-system media/ subfolders, and art referenced by gamelist.xml) out into ' +
      'a clean, standardized media folder.',
    points: [
      'Sort artwork by system and by kind — covers, screenshots, marquees',
      'Gather loose and gamelist-referenced images into one predictable place',
      'Leave every ROM exactly where it is — only the artwork moves',
    ],
    safe: 'Only artwork is relocated, into the destination folder below; the change applies as a ' +
      'single reversible runbook you can undo from History.',
  },
}

// File operations as a Before → After split: pick an operation type, then see the
// current layout transform into the target, and Apply it as a reversible runbook.
function FileOpsOperations() {
  const devices = useDevices()
  const [op, setOp] = useState<'restructure' | 'extract'>('restructure')
  const [deviceId, setDeviceId] = useState(0)
  const [root, setRoot] = useState('')
  const [scope, setScope] = useState('multi_system')
  const [system, setSystem] = useState('')
  const [profiles, setProfiles] = useState<FileProfile[]>([])
  const [profileId, setProfileId] = useState('builtin:flat')
  const [dest, setDest] = useState('downloaded_media')
  const [mediaLayout, setMediaLayout] = useState('esde')
  const [extractOp, setExtractOp] = useState<'move' | 'copy'>('move')
  const [layouts, setLayouts] = useState<{ id: string; name: string; desc: string }[]>([])
  const [plan, setPlan] = useState<FilePlan | null>(null)
  const [current, setCurrent] = useState<FileDetect | null>(null)   // the actual current folder (Before)
  const [detecting, setDetecting] = useState(false)
  const [srcModel, setSrcModel] = useState<SourceModel | null>(null)
  const [srcDeclare, setSrcDeclare] = useState('')                  // '' = auto-detect; else a profile id
  const [planRequested, setPlanRequested] = useState(false)         // opted into planning a big folder
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [openRun, setOpenRun] = useState<number | null>(null)
  const seq = useRef(0)
  const dseq = useRef(0)
  const planAbort = useRef<AbortController | null>(null)

  useEffect(() => { api.fileProfiles().then((p) => setProfiles(p.profiles)).catch(() => {}) }, [])
  useEffect(() => { api.mediaLayouts().then((r) => setLayouts(r.layouts)).catch(() => {}) }, [])
  const base = () => ({ device_id: deviceId, root: root.trim(), scope, system: system.trim() || undefined })

  // How big is the folder, and can we auto-plan it? Detection reports a `capped`
  // flag + a (floor) file count; past AUTO_PLAN_LIMIT planning is opt-in.
  const big = !!current && (current.capped || current.counts.files > AUTO_PLAN_LIMIT)
  const mf = current?.manifest || null
  const mfProfile = mf?.fresh ? mf.profile : null
  // Declaring the source already matches the TARGET profile — or a fresh manifest
  // that already records that conformance — ⇒ nothing to plan.
  const conforms = op === 'restructure'
    && ((!!srcDeclare && srcDeclare === profileId) || (!!mfProfile && mfProfile === profileId))
  const shouldPlan = !!current && !conforms && (!big || planRequested)

  // Before = the ACTUAL current folder. Refetch (fast, bounded) whenever the
  // folder/device/scope changes, independent of the plan.
  useEffect(() => {
    if (!root.trim()) { setCurrent(null); setSrcModel(null); return }
    const id = ++dseq.current
    setSrcModel(null); setPlanRequested(false)
    const t = setTimeout(async () => {
      setDetecting(true)
      try { const d = await api.fileDetect(base()); if (id === dseq.current) setCurrent(d) }
      catch { if (id === dseq.current) setCurrent(null) }
      finally { if (id === dseq.current) setDetecting(false) }
    }, 400)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deviceId, root, scope, system])

  // Preview the plan — but only when it's cheap or you've asked for it. Big folders
  // wait for a "Preview plan" click (planning reads every file); declaring the source
  // already matches the target skips it entirely.
  useEffect(() => {
    if (!shouldPlan) { setPlan(null); if (planAbort.current) planAbort.current.abort(); return }
    const id = ++seq.current
    setErr('')
    const t = setTimeout(async () => {
      planAbort.current?.abort()
      const ac = new AbortController(); planAbort.current = ac
      setBusy('plan')
      try {
        const pl = op === 'extract'
          ? await api.planExtract({ ...base(), dest: dest.trim() || 'downloaded_media', layout: mediaLayout, op: extractOp }, ac.signal)
          : await api.filePlan({ ...base(), profile: profileId }, ac.signal)
        if (id === seq.current) setPlan(pl)
      } catch (e) {
        if ((e as Error).name === 'AbortError') return
        if (id === seq.current) { setErr((e as Error).message); setPlan(null) }
      } finally { if (id === seq.current) setBusy('') }
    }, 300)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shouldPlan, op, deviceId, root, scope, system, profileId, dest, mediaLayout, extractOp])

  const cancelPlan = () => { planAbort.current?.abort(); seq.current++; setBusy(''); setPlanRequested(false) }
  const reindex = async () => {
    setBusy('index'); setErr(''); setMsg('')
    try {
      await api.manifestWrite({ ...base(), operation: 'index' })
      setMsg('Indexing this folder in the background — it will show a fresh manifest when done (track it in the Jobs monitor).')
    } catch (e) { setErr((e as Error).message) } finally { setBusy('') }
  }
  const modelIt = async () => {
    setBusy('model'); setErr('')
    try { setSrcModel((await api.modelSource(base())).model) }
    catch (e) { setErr((e as Error).message) } finally { setBusy('') }
  }
  const apply = async () => {
    setBusy('apply'); setErr('')
    try {
      const b = op === 'extract'
        ? { ...base(), operation: 'extract', dest: dest.trim() || 'downloaded_media', layout: mediaLayout, op: extractOp }
        : { ...base(), profile: profileId }
      setOpenRun((await api.createRunbook(b)).run_id)
    } catch (e) { setErr((e as Error).message) } finally { setBusy('') }
  }

  const sel = profiles.find((p) => p.id === profileId)
  const builtins = profiles.filter((p) => p.builtin)
  const customs = profiles.filter((p) => !p.builtin)
  const s = plan?.summary
  const beforeTree = pathsToTree(current?.sample || [])
  const afterTree = pathsToTree((plan?.sample || []).map((m) => m.dst))
  const planning = busy === 'plan'
  const detElapsed = useElapsed(detecting)
  const planElapsed = useElapsed(planning)
  const fileCount = current ? current.counts.files.toLocaleString() + (current.capped ? '+' : '') : ''

  return (
    <>
      <h2>File operations</h2>
      <p className="dim">Preview a change as <b>Before → After</b>, then apply it as a
        reversible <b>runbook</b>. Only the files the operation targets are moved.</p>

      <div className="fo-workbench">
      <div className="fo-wb-top">
      <div className="fo-optype">
        <button className={'fo-optype-btn' + (op === 'restructure' ? ' on' : '')} onClick={() => setOp('restructure')}>Restructure ROMs</button>
        <button className={'fo-optype-btn' + (op === 'extract' ? ' on' : '')} onClick={() => setOp('extract')}>Extract media</button>
      </div>

      <div className="fo-optype-info">
        <div className="fo-oi-head">
          <span className="fo-oi-icon">{op === 'restructure' ? '🗂' : '🖼'}</span>
          <span className="fo-oi-title">{op === 'restructure' ? 'Restructure ROMs' : 'Extract media'}</span>
        </div>
        <p className="fo-oi-blurb">{OP_INFO[op].blurb}</p>
        <ul className="fo-oi-points">
          {OP_INFO[op].points.map((p, i) => <li key={i}>{p}</li>)}
        </ul>
        <p className="fo-oi-safe">🛡 {OP_INFO[op].safe}</p>
      </div>

      <div className="fo-form">
        <label className="fo-field"><span>Device</span>
          <select value={deviceId} onChange={(e) => setDeviceId(Number(e.target.value))}>
            <option value={0}>This server (local)</option>
            {devices.map((d) => <option key={d.id} value={d.id}>{d.name}{d.host ? ` (${d.host})` : ''}</option>)}
          </select>
        </label>
        <label className="fo-field fo-grow"><span>Path</span>
          <PathInput deviceId={deviceId} value={root} onChange={setRoot} placeholder="/path/to/roms" />
        </label>
        <label className="fo-field"><span>Layout</span>
          <select value={scope} onChange={(e) => setScope(e.target.value)}>
            <option value="multi_system">Holds many systems</option>
            <option value="single_system">Is one system</option>
          </select>
        </label>
        {scope === 'single_system' && (
          <label className="fo-field"><span>System</span>
            <input value={system} placeholder="snes" onChange={(e) => setSystem(e.target.value)} /></label>
        )}
      </div>
      {err && <div className="connect-msg err">{err}</div>}
      {msg && <div className="connect-msg fo-msg">{msg}</div>}
      </div>

      <div className="fo-split">
        <div className="fo-split-arrow" aria-hidden="true">→</div>
        <section className="fo-side before">
          <div className="fo-side-head"><span className="fo-side-tag">Before</span> what is it now?</div>
          <div className="fo-side-body">
            <div className="fo-side-controls">
              <button className="ops-btn" disabled={!root.trim() || busy !== ''} onClick={modelIt}>
                {busy === 'model' ? <><span className="fo-spinner fo-spinner-sm" /> Modeling…</> : '✨ Model this folder'}</button>
              {current && <span className="dim fo-cur-note">{fileCount} files · {current.systems.length} systems · {current.current === 'folder' ? 'folder-per-game' : 'flat'}{current.capped ? ' · large' : ''}</span>}
            </div>
            {mf ? (
              <div className={'fo-manifest' + (mf.fresh ? ' fresh' : ' stale')}>
                <div className="fo-mf-head">
                  <span className="fo-mf-tag">🏷 {mf.fresh ? 'Managed folder' : 'Manifest out of date'}</span>
                  {mf.fresh && mf.written_at && <span className="dim">indexed {relTime(Date.parse(mf.written_at) / 1000)}</span>}
                </div>
                {mf.profile_name && <div className="fo-mf-line">Follows <b>{mf.profile_name}</b>{mf.conforms ? ' ✓' : ''}</div>}
                {(mf.media || []).map((m, i) => (
                  <div key={i} className="dim fo-mf-line">media → <code>{m.where}</code>{m.device ? ` on ${m.device}` : ''} ({m.layout})</div>
                ))}
                {!mf.fresh && <div className="dim fo-mf-line">The folder changed since it was indexed — ludodex fell back to reading it directly.
                  <button className="fo-mf-btn" disabled={busy !== ''} onClick={reindex}>Re-index</button></div>}
              </div>
            ) : current && (
              <button className="fo-mf-index" disabled={busy !== ''} onClick={reindex}>
                {busy === 'index' ? 'Indexing…' : '🏷 Index this folder (write a manifest)'}</button>
            )}
            {op === 'restructure' && (
              <label className="fo-declare"><span>Source already follows</span>
                <select value={srcDeclare} onChange={(e) => setSrcDeclare(e.target.value)}>
                  <option value="">Auto-detect (read the folder)</option>
                  {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </label>
            )}
            {srcModel && (
              <div className="fo-srcmodel">
                {srcModel.summary && <div>{srcModel.summary}</div>}
                {srcModel.media?.present && <div className="dim">media: {srcModel.media.where}{srcModel.media.naming ? ` (${srcModel.media.naming})` : ''}</div>}
              </div>
            )}
            <div className="fo-tree">
              {detecting ? <div className="fo-tree-empty fo-loading"><span className="fo-spinner" /> Reading…{detElapsed > 1 ? ` ${detElapsed}s` : ''}</div>
                : current && current.sample.length ? <TreeRows node={beforeTree} />
                : <div className="fo-tree-empty">Set a path to preview.</div>}
            </div>
          </div>
        </section>

        <section className="fo-side after">
          <div className="fo-side-head"><span className="fo-side-tag">After</span> {op === 'extract' ? 'media extracted' : 'target layout'}</div>
          <div className="fo-side-body">
            <div className="fo-side-controls">
              {op === 'restructure' ? (
                <select value={profileId} onChange={(e) => setProfileId(e.target.value)}>
                  <optgroup label="Built-in">{builtins.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</optgroup>
                  {customs.length > 0 && <optgroup label="Custom">{customs.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</optgroup>}
                </select>
              ) : (
                <>
                  <label className="fo-field fo-grow"><span>Where does it go? (folder under the path)</span>
                    <input value={dest} onChange={(e) => setDest(e.target.value)} placeholder="downloaded_media" /></label>
                  <label className="fo-field fo-grow"><span>Media structure</span>
                    <select value={mediaLayout} onChange={(e) => setMediaLayout(e.target.value)}>
                      {layouts.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
                    </select></label>
                  <div className="fo-copymove" role="group" aria-label="Copy or move">
                    <button type="button" className={extractOp === 'move' ? 'on' : ''} onClick={() => setExtractOp('move')}
                      title="Relocate the art out of the ROM tree (originals removed)">Move</button>
                    <button type="button" className={extractOp === 'copy' ? 'on' : ''} onClick={() => setExtractOp('copy')}
                      title="Duplicate the art into the media folder (originals kept)">Copy</button>
                  </div>
                </>
              )}
            </div>
            {op === 'restructure' && sel && <div className="fo-profile-hint"><code>{sel.target}</code><span className="dim"> — {sel.description}</span></div>}
            {op === 'extract' && (() => {
              const ld = layouts.find((l) => l.id === mediaLayout)
              return <div className="dim fo-hint">→ {(ld?.desc || 'downloaded_media/&lt;system&gt;/…').replace('<dest>', dest || 'downloaded_media')} · {extractOp === 'copy' ? 'originals kept (copy)' : 'originals moved out'} · ROM files aren't touched.</div>
            })()}
            <div className="fo-tree">
              {conforms ? (
                <div className="fo-tree-empty fo-conforms">✓ You've declared this folder already follows <b>{sel?.name || srcDeclare}</b> — nothing to plan.</div>
              ) : planning ? (
                <div className="fo-tree-empty fo-loading">
                  <div><span className="fo-spinner" /> Planning…{planElapsed > 1 ? ` ${planElapsed}s` : ''}</div>
                  {big && <><div className="dim fo-plan-note">Reading all {fileCount} files — this can take a while.</div>
                    <button className="ops-btn fo-cancel" onClick={cancelPlan}>Cancel</button></>}
                </div>
              ) : big && !planRequested ? (
                <div className="fo-tree-empty fo-optin">
                  <div className="fo-optin-scale">This folder is large — <b>{fileCount}</b> files.</div>
                  <p className="dim">Planning reads every file to compute the exact moves, so it isn't run automatically. Preview it when you're ready — or tell it the source layout on the left to skip this.</p>
                  <button className="go" onClick={() => setPlanRequested(true)}>Preview plan ▸</button>
                </div>
              ) : plan && plan.sample.length ? <TreeRows node={afterTree} />
                : plan ? <div className="fo-tree-empty">Nothing to change — already in this layout.</div>
                : <div className="fo-tree-empty">Set a path to see the result.</div>}
            </div>
          </div>
        </section>
      </div>
      </div>

      {s && (
        <div className="fo-footer">
          <div className="fo-chips">
            <span className="fo-stat">{(s.moves || 0).toLocaleString()} {op === 'extract' ? 'files extracted' : 'moves'}</span>
            {s.renames > 0 && <span className="fo-stat">{s.renames} renames</span>}
            {s.m3u > 0 && <span className="fo-stat">{s.m3u} .m3u</span>}
            {s.prune > 0 && <span className="fo-stat">{s.prune} empty dirs</span>}
            {s.skipped > 0 && <span className="fo-stat dim">{s.skipped.toLocaleString()} untouched</span>}
            <span className="dim fo-sample-note">(showing a representative sample)</span>
          </div>
          {(plan?.warnings || []).map((w, i) => <div key={i} className="fo-warn">⚠ {w}</div>)}
          <button className="go" disabled={busy !== '' || !s.moves} onClick={apply}>
            {busy === 'apply' ? 'Building…' : 'Apply → runbook'}</button>
        </div>
      )}

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
  useScrollLock()
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

// ---- AI metadata audit & supplement ----
const AIM_KIND_LABEL: Record<string, string> = {
  match: 'Match flag', identify: 'Identify', supplement: 'Supplement',
}

function fmtAttrVal(v: string | string[]): string {
  return Array.isArray(v) ? v.join(', ') : String(v)
}

// Normalize a finding payload's provider link(s): prefer the multi-provider list,
// fall back to the legacy single provider_match (IGDB).
function providerMatches(p: AiFindingPayload): ProviderMatch[] {
  if (p.provider_matches && p.provider_matches.length) return p.provider_matches
  return p.provider_match ? [p.provider_match] : []
}

const PROVIDER_LABEL: Record<string, string> = {
  igdb: 'IGDB', screenscraper: 'ScreenScraper',
}

function pmLabel(m: ProviderMatch): string {
  return PROVIDER_LABEL[m.provider || (m.igdb_id ? 'igdb' : '')] || m.provider || 'provider'
}

function pmId(m: ProviderMatch): string | number | undefined {
  return m.igdb_id ?? m.ss_id
}

// The green "✓ Matched to <provider>" row(s) shown on finding cards / callouts.
function ProviderMatchRows({ p }: { p: AiFindingPayload }) {
  const ms = providerMatches(p)
  if (!ms.length) return null
  return (
    <>
      {ms.map((m, i) => (
        <div key={i} className={'aim-provmatch' + (m.provider === 'screenscraper' ? ' ss' : '')}>
          {m.cover && <img className="aim-pm-cover" src={m.cover} alt="" />}
          <div className="aim-pm-txt">
            <span className="aim-pm-tag">✓ Matched to {pmLabel(m)}</span>
            <b>{m.name}</b>{m.year ? ` (${m.year})` : ''}
            {m.provider === 'screenscraper' && <span className="aim-pm-art">art</span>}
          </div>
        </div>
      ))}
    </>
  )
}

function AimAttrList({ attrs }: { attrs: Record<string, string | string[]> }) {
  const keys = Object.keys(attrs || {})
  if (!keys.length) return null
  return (
    <div className="aim-attrs">
      {keys.map((k) => (
        <div key={k} className="aim-attr">
          <span className="aim-attr-k">{k.replace(/_/g, ' ')}</span>
          <span className="aim-attr-v">{fmtAttrVal(attrs[k])}</span>
        </div>
      ))}
    </div>
  )
}

function AimFindingBody({ f }: { f: AiFinding }) {
  const p = f.payload
  const m = p.match || ({} as AiFinding['payload']['match'])
  return (
    <div className="aim-body">
      {f.kind === 'identify' && (
        <div className="aim-callout info">
          No provider match → AI: <b>{m.suggested_title || '—'}</b>
          {m.suggested_year ? ` (${m.suggested_year})` : ''}
        </div>
      )}
      {f.kind === 'match' && (
        <div className="aim-callout warn">
          ⚠ Match may be wrong — currently <b>{p.current_match?.title || '—'}</b>
          {p.current_match?.year ? ` (${p.current_match.year})` : ''}; AI thinks this is{' '}
          <b>{m.suggested_title || '—'}</b>{m.suggested_year ? ` (${m.suggested_year})` : ''}.
          {m.issue && <div className="aim-issue">{m.issue}</div>}
        </div>
      )}
      <ProviderMatchRows p={p} />
      {f.kind === 'supplement' && Object.keys(p.attributes || {}).length > 0 &&
        <div className="aim-fills-label">Fills gaps:</div>}
      <AimAttrList attrs={p.attributes} />
      {p.web && (p.sources || []).length > 0 && (
        <details className="aim-sources">
          <summary>🔎 {p.sources!.length} web source{p.sources!.length === 1 ? '' : 's'}</summary>
          <ul>
            {p.sources!.map((s, i) => (
              <li key={i}><a href={s.url} target="_blank" rel="noreferrer">{s.title || s.url}</a></li>
            ))}
          </ul>
        </details>
      )}
      {p.notes && <div className="aim-notes">{p.notes}</div>}
    </div>
  )
}

function AimActions({ f, onAct }: { f: AiFinding; onAct: (a: 'accept' | 'reject' | 'reset') => void }) {
  if (f.status === 'proposed') return (
    <div className="aim-actions">
      <button className="ops-btn go" onClick={() => onAct('accept')}>✓ Accept</button>
      <button className="ops-btn" onClick={() => onAct('reject')}>✕ Reject</button>
    </div>
  )
  return (
    <div className="aim-actions">
      <span className={'run-badge s-' + (f.status === 'accepted' ? 'done' : 'failed')}>{f.status}</span>
      <button className="link-btn" onClick={() => onAct('reset')}>reset</button>
    </div>
  )
}

// true = all, false = none, [kinds] = a chosen subset
function scopeValue(master: boolean, picked: Set<string>): ScopeValue {
  if (master) return true
  return picked.size ? Array.from(picked) : false
}

// the wand's media scope is remembered so Apply pulls the same kinds it scanned for
const WAND_MEDIA_KEY = 'ludodex_wand_media'
function wandMedia(): ScopeValue {
  try { const v = localStorage.getItem(WAND_MEDIA_KEY); if (v) return JSON.parse(v) } catch { /* */ }
  return true
}

function ScopeCategory({ name, unit, items, master, setMaster, picked, setPicked }: {
  name: string; unit: string; items: string[]
  master: boolean; setMaster: (b: boolean) => void
  picked: Set<string>; setPicked: (s: Set<string>) => void
}) {
  const [open, setOpen] = useState(false)
  const n = master ? items.length : picked.size
  const label = master ? `all ${items.length} ${unit}` : n === 0 ? `no ${unit}` : `${n} of ${items.length} ${unit}`
  const toggle = (k: string) => {
    const s = new Set(picked); s.has(k) ? s.delete(k) : s.add(k); setPicked(s)
  }
  return (
    <div className={'wand-scope' + (!master && picked.size === 0 ? ' off' : '')}>
      <div className="wand-scope-h">
        <label className="wand-check wand-scope-master">
          <input type="checkbox" checked={master} onChange={(e) => setMaster(e.target.checked)} />
          <span>{name} <span className="dim">— {label}</span></span>
        </label>
        <button type="button" className="wand-scope-exp" title={open ? 'Collapse' : 'Expand'}
          onClick={() => setOpen((v) => !v)}>{open ? '▾' : '▸'}</button>
      </div>
      {open && (
        <div className="wand-scope-kinds">
          {items.map((k) => (
            <label key={k} className={'wand-kchip' + ((master || picked.has(k)) ? ' on' : '') + (master ? ' locked' : '')}
              title={master ? 'All selected — uncheck the master to pick individually' : ''}>
              <input type="checkbox" checked={master || picked.has(k)} disabled={master} onChange={() => toggle(k)} />
              {k.replace(/_/g, ' ')}
            </label>
          ))}
        </div>
      )}
    </div>
  )
}

function MagicWandOverlay({ filterQuery, filterCount, onClose }: {
  filterQuery: GamesQuery; filterCount: number; onClose: () => void
}) {
  useScrollLock()
  const [targets, setTargets] = useState<AiScanTargets | null>(null)
  const [scope, setScope] = useState<'all' | 'filtered'>('filtered')
  const [web, setWeb] = useState(false)
  const [matchProvider, setMatchProvider] = useState(true)
  const [limit, setLimit] = useState(100)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  // metadata + media scope: master "all" flag + a picked subset when master is off
  const [mdMaster, setMdMaster] = useState(true)
  const [mdPicked, setMdPicked] = useState<Set<string>>(new Set())
  const [mediaMaster, setMediaMaster] = useState(true)
  const [mediaPicked, setMediaPicked] = useState<Set<string>>(new Set())
  useEffect(() => { api.aimetaTargets().then(setTargets).catch(() => {}) }, [])
  const hasFilter = !!(filterQuery.q || (filterQuery.include || []).length || (filterQuery.exclude || []).length)

  const metadataVal = scopeValue(mdMaster, mdPicked)
  const mediaVal = scopeValue(mediaMaster, mediaPicked)
  const nothingToDo = metadataVal === false && mediaVal === false

  const wave = async () => {
    setBusy(true); setErr(''); setMsg('')
    try {
      const opts = {
        web: web && !!targets?.web_capable, match_provider: matchProvider,
        metadata: metadataVal, media: mediaVal,
      }
      try { localStorage.setItem(WAND_MEDIA_KEY, JSON.stringify(mediaVal)) } catch { /* */ }
      let r
      if (scope === 'all') {
        r = await api.aimetaScan({ target: 'all', limit, ...opts })
      } else {
        // resolve the current filter to an explicit set of norm_keys
        const page = await api.games({ ...filterQuery, limit: 2000, offset: 0 })
        const keys = page.items.map((g) => g.norm_key)
        if (!keys.length) { setErr('No games in the current filter.'); setBusy(false); return }
        r = await api.aimetaScan({ norm_keys: keys, label: 'filtered', ...opts })
      }
      setMsg(`✨ Scan started on ${r.count.toLocaleString()} game(s)${r.web ? ' (web search on)' : ''} — watch the job monitor by the sync button, then review in Settings → AI Metadata → Review.`)
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="panel wand-panel" onClick={(e) => e.stopPropagation()}>
        <button className="close" onClick={onClose}>×</button>
        <h2 className="wand-title"><span className="wand-spark">✨</span> Magic wand</h2>
        <p className="dim">Let AI make your library as complete as possible — identify unmatched
          games, match them to a real provider, and fill in the gaps.</p>

        {msg ? (
          <>
            <div className="sync-note wand-ok">{msg}</div>
            <div className="settings-actions"><button className="go" onClick={onClose}>Done</button></div>
          </>
        ) : (
          <>
            <div className="wand-sec">
              <div className="wand-sec-h">Scope</div>
              <label className="wand-radio">
                <input type="radio" checked={scope === 'filtered'} onChange={() => setScope('filtered')} />
                <span>{hasFilter ? 'Only what’s currently filtered' : 'Current view'}
                  <span className="wand-n">{filterCount.toLocaleString()} games</span></span>
              </label>
              <label className="wand-radio">
                <input type="radio" checked={scope === 'all'} onChange={() => setScope('all')} />
                <span>All games<span className="wand-n">{targets ? targets.all.toLocaleString() : '…'}</span></span>
              </label>
              {scope === 'all' && (
                <label className="wand-limit">Max games this run
                  <input type="number" min={1} value={limit}
                    onChange={(e) => setLimit(Math.max(1, parseInt(e.target.value, 10) || 1))} />
                </label>
              )}
            </div>

            <div className="wand-sec">
              <div className="wand-sec-h">Options</div>
              <label className={'wand-check' + (targets && !targets.web_capable ? ' off' : '')}
                title={targets && !targets.web_capable ? 'The metadata AI provider has no web search — pick Gemini/Anthropic/OpenAI in AI settings' : 'Slower, but verifies against live web sources'}>
                <input type="checkbox" checked={web} disabled={!targets?.web_capable}
                  onChange={(e) => setWeb(e.target.checked)} />
                <span>Search the web for verification <span className="dim">(slower)</span></span>
              </label>
              <label className="wand-check">
                <input type="checkbox" checked={matchProvider} onChange={(e) => setMatchProvider(e.target.checked)} />
                <span>Match to a provider (IGDB) <span className="dim">— turn AI identities into real links</span></span>
              </label>
              <div className="wand-info">Matching a provider also pulls that provider’s trusted
                attributes and media when you Apply the results.</div>
            </div>

            <div className="wand-sec">
              <div className="wand-sec-h">Fill</div>
              <ScopeCategory name="Metadata" unit="attributes" items={targets?.attributes || []}
                master={mdMaster} setMaster={setMdMaster} picked={mdPicked} setPicked={setMdPicked} />
              <ScopeCategory name="Media" unit="types" items={targets?.media_kinds || []}
                master={mediaMaster} setMaster={setMediaMaster} picked={mediaPicked} setPicked={setMediaPicked} />
              {nothingToDo && <div className="wand-info dim">Turn on Metadata or Media — nothing is selected to fill.</div>}
            </div>

            {err && <div className="fo-warn">⚠ {err}</div>}
            <div className="settings-actions wand-actions">
              <button className="go wand-go" disabled={busy || nothingToDo} onClick={wave}>
                {busy ? 'Starting…' : '✨ Wave the wand'}</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function MetadataScan() {
  const [targets, setTargets] = useState<AiScanTargets | null>(null)
  const [scans, setScans] = useState<AiScanRun[]>([])
  const [limit, setLimit] = useState(100)
  const [web, setWeb] = useState(false)
  const [matchProvider, setMatchProvider] = useState(true)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const loadScans = useCallback(() => api.aimetaScans().then((d) => setScans(d.scans)).catch(() => {}), [])
  useEffect(() => { api.aimetaTargets().then(setTargets).catch(() => {}); loadScans() }, [loadScans])
  const anyRunning = scans.some((s) => s.status === 'running')
  useEffect(() => {
    if (!anyRunning) return
    const t = setInterval(loadScans, 3000)
    return () => clearInterval(t)
  }, [anyRunning, loadScans])

  const start = async (target: string) => {
    setBusy(target); setErr(''); setMsg('')
    try {
      const r = await api.aimetaScan({ target, limit, web: web && !!targets?.web_capable, match_provider: matchProvider })
      setMsg(`Scanning ${r.count.toLocaleString()} game(s) — track progress in the job monitor by the sync button, then review results in the Review tab.`)
      loadScans()
    } catch (e) { setErr((e as Error).message) } finally { setBusy('') }
  }

  const CARDS: { id: 'unmatched' | 'matched' | 'missing' | 'all'; name: string; desc: string }[] = [
    { id: 'unmatched', name: 'Unmatched', desc: 'Games no provider matched — AI proposes an identity + fills attributes.' },
    { id: 'matched', name: 'Verify matches', desc: 'Audit existing matches and flag likely-wrong ones (remake vs original).' },
    { id: 'missing', name: 'Missing attributes', desc: 'Fill holes providers left — year, genres, developer, and more.' },
    { id: 'all', name: 'Everything', desc: 'Scan the whole catalog (largest — mind your usage caps).' },
  ]

  return (
    <>
      <h2>Metadata scan</h2>
      <p className="dim">Have AI audit provider matches, identify unmatched games, and fill
        attribute gaps. Results land in the <b>Review</b> tab for you to accept or reject —
        nothing is applied automatically. Uses the <b>Metadata search &amp; supplement</b> AI area
        (set its provider/model in AI settings).</p>

      <div className="aim-cards">
        {CARDS.map((c) => (
          <div key={c.id} className="aim-card">
            <div className="aim-card-name">{c.name}
              <span className="aim-card-count">{targets ? (targets[c.id]).toLocaleString() : '…'}</span>
            </div>
            <div className="aim-card-desc">{c.desc}</div>
            <button className="ops-btn go" disabled={!!busy || !targets || !targets[c.id]}
              onClick={() => start(c.id)}>
              {busy === c.id ? 'Starting…' : 'Scan'}
            </button>
          </div>
        ))}
      </div>

      <div className="aim-limit">
        <label>Max games this scan
          <input type="number" min={1} value={limit}
            onChange={(e) => setLimit(Math.max(1, parseInt(e.target.value, 10) || 1))} />
        </label>
        <label className="wand-check" title={targets && !targets.web_capable ? 'Provider has no web search' : 'Slower; verifies against live web sources'}>
          <input type="checkbox" checked={web} disabled={!targets?.web_capable}
            onChange={(e) => setWeb(e.target.checked)} /> Web search
        </label>
        <label className="wand-check">
          <input type="checkbox" checked={matchProvider} onChange={(e) => setMatchProvider(e.target.checked)} /> Match provider (IGDB)
        </label>
      </div>

      {msg && <div className="sync-note">{msg}</div>}
      {err && <div className="fo-warn">⚠ {err}</div>}

      <h3 className="aim-h3">Recent scans</h3>
      {scans.length === 0 && <div className="sync-note dim">No scans yet.</div>}
      {scans.map((s) => (
        <div key={s.id} className="fo-hrow aim-scan">
          <StatusBadge status={s.status} />
          <span className="fo-hprofile">{s.target}</span>
          <ProgressBar done={s.done} total={s.total} failed={0} />
          <span className="dim">{s.done}/{s.total} · {s.findings} findings</span>
          <span className="dim fo-hwhen">{relTime(s.finished || s.created)}</span>
        </div>
      ))}
    </>
  )
}

// One selectable change within a finding: the provider link, or one attribute fill.
type Change =
  | { id: string; type: 'match'; label: string; value: string; cover?: string | null }
  | { id: string; type: 'attr'; attrKind: string; label: string; value: string }

function findingChanges(f: AiFinding): Change[] {
  const out: Change[] = []
  const pms = providerMatches(f.payload)
  if (pms.length) {
    out.push({
      id: `${f.id}:match`, type: 'match',
      label: '🔗 Link to ' + pms.map((m) => `${pmLabel(m)} #${pmId(m)}`).join(' + '),
      cover: pms.find((m) => m.cover)?.cover ?? null,
      value: pms.map((m) => `${m.name}${m.year ? ` (${m.year})` : ''}`).join(' · '),
    })
  }
  for (const k of Object.keys(f.payload.attributes || {})) {
    out.push({
      id: `${f.id}:attr:${k}`, type: 'attr', attrKind: k,
      label: k.replace(/_/g, ' '), value: fmtAttrVal(f.payload.attributes[k]),
    })
  }
  return out
}

function MetadataChangeset({ runId, onApplied }: { runId?: number; onApplied?: () => void }) {
  const [findings, setFindings] = useState<AiFinding[] | null>(null)
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api.aimetaFindings('proposed', undefined, runId).then((d) => {
      setFindings(d.findings)
      const all = new Set<string>()
      d.findings.forEach((f) => findingChanges(f).forEach((c) => all.add(c.id)))
      setSel(all)
    }).catch(() => setFindings([]))
  }, [runId])
  useEffect(() => { load() }, [load])

  const groups = (findings || []).map((f) => ({ f, changes: findingChanges(f) }))
    .filter((g) => g.changes.length > 0)
  const allIds = groups.flatMap((g) => g.changes.map((c) => c.id))
  const selectedCount = allIds.filter((id) => sel.has(id)).length
  const gamesTouched = groups.filter((g) => g.changes.some((c) => sel.has(c.id))).length

  const toggle = (id: string) =>
    setSel((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n })
  const toggleGroup = (ids: string[], on: boolean) =>
    setSel((prev) => {
      const n = new Set(prev)
      ids.forEach((id) => (on ? n.add(id) : n.delete(id)))
      return n
    })
  const setAll = (on: boolean) => setSel(on ? new Set(allIds) : new Set())

  const apply = async () => {
    const selections: AiApplySelection[] = []
    for (const g of groups) {
      const attrs = g.changes.filter((c) => c.type === 'attr' && sel.has(c.id))
        .map((c) => (c as Extract<Change, { type: 'attr' }>).attrKind)
      const match = g.changes.some((c) => c.type === 'match' && sel.has(c.id))
      if (attrs.length === 0 && !match) continue
      selections.push({ finding_id: g.f.id, attributes: attrs, match })
    }
    if (!selections.length) return
    setBusy(true); setNote('')
    try {
      const r = await api.aimetaApply(selections, wandMedia())
      const msg = `✨ Applying ${selectedCount} change(s) across ${gamesTouched} game(s)` +
        (r.coalesced ? ' — added to the running rebuild.' : ' — rebuilding. Track it in the job monitor.')
      if (onApplied) { showToast(msg); onApplied() } else { setNote(msg); load() }
    } catch (e) { setNote((e as Error).message) } finally { setBusy(false) }
  }

  if (!findings) return <div className="loading">Loading…</div>
  if (!groups.length) {
    return <div className="sync-note dim">{runId
      ? 'Nothing left to review here — these changes were already applied (or dismissed).'
      : 'No proposed changes — run the ✨ Magic wand or a scan first.'}</div>
  }

  return (
    <div className="chg-wrap">
      <p className="dim">Here's everything the AI wants to change. Tick the changes to keep,
        then apply — like a runbook, nothing happens until you apply.</p>

      {groups.map(({ f, changes }) => {
        const ids = changes.map((c) => c.id)
        const on = ids.filter((id) => sel.has(id))
        const allOn = on.length === ids.length
        const p = f.payload
        return (
          <div key={f.id} className="chg-group">
            <div className="chg-ghead">
              <label className="chg-check">
                <input type="checkbox" checked={allOn}
                  ref={(el) => { if (el) el.indeterminate = on.length > 0 && !allOn }}
                  onChange={(e) => toggleGroup(ids, e.target.checked)} />
              </label>
              <span className="chg-gtitle">{f.title}</span>
              <span className="chg-conf">{Math.round((f.confidence || 0) * 100)}%</span>
            </div>
            {p.match?.status === 'wrong' && (
              <div className="chg-warn">⚠ current match may be wrong:{' '}
                <b>{p.current_match?.title || '—'}</b> → <b>{p.match.suggested_title || '—'}</b></div>
            )}
            {changes.map((c) => (
              <label key={c.id} className={'chg-row' + (c.type === 'match' ? ' chg-link' : '')}>
                <input type="checkbox" checked={sel.has(c.id)} onChange={() => toggle(c.id)} />
                {c.type === 'match' && c.cover && <img className="chg-cover" src={c.cover} alt="" />}
                <span className="chg-label">{c.label}</span>
                <span className="chg-arrow">→</span>
                <span className="chg-value">{c.value}</span>
              </label>
            ))}
            {p.web && (p.sources || []).length > 0 && (
              <details className="chg-sources">
                <summary>🔎 {p.sources!.length} web source{p.sources!.length === 1 ? '' : 's'}</summary>
                <ul>{p.sources!.map((s, i) => (
                  <li key={i}><a href={s.url} target="_blank" rel="noreferrer">{s.title || s.url}</a></li>
                ))}</ul>
              </details>
            )}
          </div>
        )
      })}

      {note && <div className="sync-note">{note}</div>}
      <div className="chg-bar">
        <span className="chg-summary"><b>{selectedCount}</b> change{selectedCount === 1 ? '' : 's'}
          {' '}across <b>{gamesTouched}</b> game{gamesTouched === 1 ? '' : 's'} selected</span>
        <button className="ops-btn" onClick={() => setAll(true)}>Select all</button>
        <button className="ops-btn" onClick={() => setAll(false)}>Deselect all</button>
        <button className="ops-btn go" disabled={busy || selectedCount === 0} onClick={apply}>
          {busy ? 'Applying…' : onApplied ? '✨ Accept & apply' : '✨ Apply selected'}</button>
      </div>
    </div>
  )
}

function MetadataReview() {
  const [view, setView] = useState<'cards' | 'changeset'>('changeset')
  const [data, setData] = useState<{ findings: AiFinding[]; counts: AiFindingCounts } | null>(null)
  const [kind, setKind] = useState('')          // '' = all
  const [status, setStatus] = useState('proposed')
  const reload = useCallback(
    () => api.aimetaFindings(status || undefined, kind || undefined)
      .then(setData).catch(() => setData({ findings: [], counts: {} })),
    [status, kind])
  useEffect(() => { reload() }, [reload])

  const [note, setNote] = useState('')
  const act = async (id: number, action: 'accept' | 'reject' | 'reset') => {
    try { await api.aimetaFindingAction(id, action) } catch { /* ignore */ }
    reload()
  }
  const acceptAll = async () => {
    setNote('')
    try { const r = await api.aimetaAcceptAll(); setNote(`Accepted ${r.accepted} proposal(s).`) }
    catch (e) { setNote((e as Error).message) }
    reload()
  }
  const apply = async () => {
    setNote('')
    try { await api.aimetaApply(undefined, wandMedia()); setNote('✨ Applying accepted findings — linking provider matches and rebuilding the catalog. Track it in the job monitor.') }
    catch (e) { setNote((e as Error).message) }
  }
  const countFor = (k: string) => {
    const c = data?.counts || {}
    if (!k) return Object.values(c).reduce((n, s) => n + (s[status] || 0), 0)
    return (c[k]?.[status]) || 0
  }
  const KINDS = [{ id: '', name: 'All' }, { id: 'match', name: 'Match flags' },
    { id: 'identify', name: 'Identify' }, { id: 'supplement', name: 'Supplement' }]

  return (
    <>
      <div className="aim-review-head">
        <h2>Metadata review</h2>
        <div className="aim-viewtoggle">
          <button className={'tab' + (view === 'changeset' ? ' sel' : '')}
            onClick={() => setView('changeset')}>Changeset</button>
          <button className={'tab' + (view === 'cards' ? ' sel' : '')}
            onClick={() => setView('cards')}>Cards</button>
        </div>
      </div>
      {view === 'changeset' ? <MetadataChangeset /> : <>
      <p className="dim">AI proposals from your scans. Accept to keep (accepted supplements
        show on the game and bake into the catalog on the next rebuild); reject to dismiss.</p>

      <div className="aim-toolbar">
        <button className="ops-btn" onClick={acceptAll}>✓ Accept all proposed</button>
        <button className="ops-btn go" onClick={apply}>✨ Apply to catalog</button>
        <span className="dim aim-toolbar-hint">Apply links accepted provider matches and rebuilds
          the catalog (runs in the job monitor).</span>
      </div>
      {note && <div className="sync-note">{note}</div>}

      <div className="aim-filters">
        <div className="aim-tabs">
          {KINDS.map((k) => (
            <button key={k.id} className={'tab' + (kind === k.id ? ' sel' : '')}
              onClick={() => setKind(k.id)}>{k.name}
              <span className="aim-tab-n">{countFor(k.id)}</span>
            </button>
          ))}
        </div>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="proposed">Proposed</option>
          <option value="accepted">Accepted</option>
          <option value="rejected">Rejected</option>
        </select>
      </div>

      {!data ? <div className="loading">Loading…</div>
        : data.findings.length === 0
          ? <div className="sync-note dim">No findings — run a scan from the Scan tab.</div>
          : data.findings.map((f) => (
            <div key={f.id} className={'aim-finding k-' + f.kind}>
              <div className="aim-fhead">
                <span className="aim-ftitle">{f.title}</span>
                <span className={'aim-kind k-' + f.kind}>{AIM_KIND_LABEL[f.kind]}</span>
                <span className="aim-conf">{Math.round((f.confidence || 0) * 100)}%</span>
              </div>
              <AimFindingBody f={f} />
              <AimActions f={f} onAct={(a) => act(f.id, a)} />
            </div>
          ))}
      </>}
    </>
  )
}

// Compact AI-finding callout for the game detail view.
function AiMetaCallout({ finding, onChanged }: { finding: AiFinding; onChanged: () => void }) {
  const [busy, setBusy] = useState(false)
  const act = async (a: 'accept' | 'reject') => {
    setBusy(true)
    try { await api.aimetaFindingAction(finding.id, a); onChanged() }
    finally { setBusy(false) }
  }
  const p = finding.payload
  const m = p.match || ({} as AiFinding['payload']['match'])
  const cls = finding.kind === 'match' ? 'warn' : finding.kind === 'identify' ? 'info' : 'soft'
  return (
    <div className={'aim-detail ' + cls}>
      <div className="aim-detail-text">
        {finding.kind === 'match' && <>⚠ <b>AI: this match may be wrong</b> — suggests{' '}
          {m.suggested_title}{m.suggested_year ? ` (${m.suggested_year})` : ''}
          {m.issue && <span className="aim-issue"> — {m.issue}</span>}</>}
        {finding.kind === 'identify' && <><b>AI identified this as</b>{' '}
          {m.suggested_title}{m.suggested_year ? ` (${m.suggested_year})` : ''}</>}
        {finding.kind === 'supplement' && <><b>AI can fill:</b>{' '}
          {Object.keys(p.attributes || {}).map((k) => k.replace(/_/g, ' ')).join(', ')}</>}
        {providerMatches(p).length > 0 && <div className="aim-issue">✓ Real provider match:{' '}
          {providerMatches(p).map((m) => `${pmLabel(m)} — ${m.name}${m.year ? ` (${m.year})` : ''}`).join('; ')}</div>}
      </div>
      {finding.status === 'proposed' ? (
        <div className="aim-actions">
          <button className="ops-btn go" disabled={busy} onClick={() => act('accept')}>Accept</button>
          <button className="ops-btn" disabled={busy} onClick={() => act('reject')}>Reject</button>
          <span className="aim-hint">Accepting just queues it — you still Apply from the banner above Search.</span>
        </div>
      ) : finding.status === 'accepted' ? (
        <div className="aim-pending">
          <span className="aim-pending-badge">✓ Accepted — not applied yet</span>
          <span className="aim-pending-note">This won't change your library until you <b>Apply pending changes</b> (banner above the library search).</span>
        </div>
      ) : (
        <span className={'run-badge s-' + (finding.status === 'applied' ? 'done' : 'failed')}>
          {finding.status === 'applied' ? '✓ applied' : finding.status}</span>
      )}
    </div>
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

// The diff/accept screen for one finished wand scan job: shows exactly what will
// change (reusing the changeset), scoped to that run; accepting applies it.
function AiReviewModal({ runId, title, onClose }: { runId: number; title: string; onClose: () => void }) {
  useScrollLock()
  return (
    <div className="overlay" onClick={onClose}>
      <div className="panel review-panel" onClick={(e) => e.stopPropagation()}>
        <button className="close" onClick={onClose}>×</button>
        <h2>✨ Review changes{title ? <> — <span className="rv-title">{title}</span></> : null}</h2>
        <p className="dim">Exactly what the AI wants to change for this game. Tick what to keep,
          then Accept &amp; apply — it goes straight into your catalog.</p>
        <MetadataChangeset runId={runId} onApplied={onClose} />
      </div>
    </div>
  )
}

// Strip the "Metadata scan — " prefix so the review header reads as the game name.
const scanTitle = (label: string) => label.replace(/^Metadata scan\s*[—-]\s*/, '')

// A finished wand scan that produced suggestions is ready to review & accept.
const reviewable = (j: Job) => j.kind === 'aimeta' && j.status === 'done' && (j.findings ?? 0) > 0

function JobMonitor() {
  const [jobs, setJobs] = useState<Job[]>([])
  const [open, setOpen] = useState(false)
  const [review, setReview] = useState<{ runId: number; title: string } | null>(null)
  const load = useCallback(() => api.jobs().then((j) => setJobs(j.jobs)).catch(() => {}), [])
  useEffect(() => { load(); const t = setInterval(load, 2500); return () => clearInterval(t) }, [load])

  // Surface any reviewable scan even when other jobs are active, so accepting is
  // never buried — the whole point is to queue wands and accept them from here.
  const active = jobs.filter((j) => j.status === 'running' || j.status === 'paused')
  const ready = jobs.filter(reviewable)
  const base = active.length ? active : jobs
  const shown = [...ready, ...base.filter((j) => !reviewable(j))].slice(0, 3)
  const pause = async (id: string) => { await api.pauseJob(id).catch(() => {}); load() }
  const del = async (id: string) => { await api.deleteJob(id).catch(() => {}); load() }
  const openReview = (j: Job) => setReview({ runId: j.run_id!, title: scanTitle(j.label) })

  return (
    <div className={'jobmon' + (active.length ? ' busy' : ' idle')}>
      <div className="jobmon-rows">
        {shown.length === 0 ? (
          <button className="jobmon-idle" title="Open job monitor" onClick={() => setOpen(true)}>
            <span className="jm-idle-dot" />
            {jobs.length ? `${jobs.length} recent job${jobs.length === 1 ? '' : 's'}` : 'No active jobs'}
          </button>
        ) : shown.map((j) => (
          <div key={j.id} className={'jobmon-row' + (reviewable(j) ? ' jm-ready' : '')}>
            <span className="jm-label" title={j.detail ? `${j.label} — ${j.detail}` : j.label}>
              {j.label}{j.detail ? <span className="dim"> — {j.detail}</span> : null}</span>
            {reviewable(j) ? (
              <button className="jm-accept" title="Review & accept these changes" onClick={() => openReview(j)}>
                ✨ Review &amp; accept
              </button>
            ) : (
              <ProgressBar done={j.progress.done} total={j.progress.total} failed={j.progress.failed} running={j.status === 'running'} />
            )}
            {!reviewable(j) && <span className={'jm-status s-' + j.status}>{j.status}</span>}
            {j.cancelable && <button className="jm-btn" title="Pause" onClick={() => pause(j.id)}>⏸</button>}
            {j.deletable && <button className="jm-btn" title="Remove" onClick={() => del(j.id)}>×</button>}
          </div>
        ))}
        {active.length > 3 && <span className="jm-more">+{active.length - 3} more</span>}
      </div>
      <button className="jm-expand icon-btn" title="All jobs" onClick={() => setOpen(true)}>⤢</button>
      {open && <JobOverlay onClose={() => setOpen(false)} />}
      {review && <AiReviewModal runId={review.runId} title={review.title}
        onClose={() => { setReview(null); load() }} />}
    </div>
  )
}

function JobOverlay({ onClose }: { onClose: () => void }) {
  const [jobs, setJobs] = useState<Job[]>([])
  const [openRun, setOpenRun] = useState<number | null>(null)
  const [review, setReview] = useState<{ runId: number; title: string } | null>(null)
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
              <ProgressBar done={j.progress.done} total={j.progress.total} failed={j.progress.failed} running={j.status === 'running'} />
              <span className="dim job-when">{relTime(j.when)}</span>
              <span className="job-acts" onClick={(e) => e.stopPropagation()}>
                {reviewable(j) && (
                  <button className="jm-accept" title="Review & accept these changes"
                    onClick={() => setReview({ runId: j.run_id!, title: scanTitle(j.label) })}>
                    ✨ Review &amp; accept
                  </button>
                )}
                {j.cancelable && <button className="jm-btn" title="Pause" onClick={() => act(api.pauseJob(j.id))}>⏸</button>}
                {j.restartable && <button className="jm-btn" title="Restart / resume" onClick={() => act(api.restartJob(j.id))}>▶</button>}
                {j.deletable && <button className="jm-btn" title="Delete" onClick={() => act(api.deleteJob(j.id))}>×</button>}
              </span>
              {j.error && <div className="connect-msg err job-err">{j.error}</div>}
            </div>
          ))}
        </div>
        {openRun != null && <RunbookModal runId={openRun} onClose={() => { setOpenRun(null); load() }} />}
        {review && <AiReviewModal runId={review.runId} title={review.title}
          onClose={() => { setReview(null); load() }} />}
      </div>
    </div>
  )
}
