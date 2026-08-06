// Typed client for the ludodex API.

export interface TagRef {
  tag: string
  origins: string[]   // e.g. ['ludodex'], ['playnite'], or multiple
}

export interface GameRow {
  norm_key: string
  entry_key?: string       // per-platform entry id (base_key@platform) — the addressable id
  platform?: string | null // this entry's platform (pc / genesis / ps4 / …)
  title: string
  n_sources: number
  n_kinds: number
  sources_summary: string
  platforms: string
  emulation: boolean       // has an emulation/ROM source (selectable for device wishlist)
  matched: boolean         // cross-referenced to a metadata provider (IGDB/ScreenScraper)
  identified: boolean      // a known title: matched OR from a real store/manual source
  has_cover: boolean
  cover_v?: string | null  // chosen cover's content hash — cache-buster so a re-pinned cover shows live
  ludodex_score: number | null
  tags: TagRef[]
  attrs?: Record<string, string>   // attribute kind -> value(s), for the optional attribute columns
  wanted?: boolean         // a wishlist-only entry (you want it, don't own it)
  framing_cover?: Frame    // saved position+zoom for this game's cover, if any
}

// Per-image framing: edge insets (% of viewport; negative bleeds/crops, positive
// letterboxes) + zoom (0.1–5.0). Applied at render time.
export interface Frame {
  top: number; right: number; bottom: number; left: number; zoom: number
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
  hidden_unidentified?: number   // unidentified matches hidden by the toggle (search)
  limit: number
  offset: number
  items: GameRow[]
}

export interface SourceRow {
  source: string; platform: string; source_id: string
  title_raw: string; detail: string; os?: string[] | null; state?: 'have' | 'want'
  year?: number | null
}
export interface SplitSuggestion {
  multiple: boolean
  reason?: string
  games: { title: string; year: number | null; rows: number[] }[]
  sources: SourceRow[]
}
export interface GameDetail {
  norm_key: string          // base title key (used for title-level mutations)
  entry_key?: string        // this platform entry's id (base_key@platform)
  platform?: string | null  // this entry's platform
  also_owned_on?: { entry_key: string; platform: string; title: string; via?: string }[]
  title: string
  sources: { source: string; platform: string; source_id: string; title_raw: string; detail: string; os: string[] | null; state?: 'have' | 'want'; collection?: string | null; via_collection?: string }[]
  rom_files?: { path: string; filename: string; system: string }[]   // on-disk ROM path(s)
  attributes: Record<string, string[]>
  tags: TagRef[]
  scores: Scores
  metadata_links: { provider: string; provider_id: string; slug: string; url: string }[]
  provider_links?: { provider: string; url: string }[]   // favicon shortcuts (metadata + steam store)
  media_kinds: string[]
  ai_meta?: AiFinding | null
  attribute_provenance?: Record<string, { value: string; origins: string[]; ai: boolean }[]>
  attribute_alternates?: Record<string, { provider: string; value: string }[]>  // per-provider retained values
  identity_confidence?: Record<string, { score: number; reason: string }>  // per-provider match certainty
  disabled_identity?: string[]   // metadata providers the user turned off for this game
  attribute_overrides?: Record<string, { value: string; origin: string }>
  editable_kinds?: string[]
  ownership?: OwnershipFact[]
  framing?: Record<string, Frame>   // kind -> saved position+zoom
  hero_pref?: string | null         // hero override: 'marquee' | a media kind | null (auto)
  collection?: Collection | null    // set when THIS entry is itself a compilation
}

// A compilation the user owns, and the standalone games it contains (DESIGN §13).
export interface Collection {
  coll_key: string
  name: string
  origin: string
  members: { member_key: string; member_title: string; member_platform: string; member_year: number | null; origin: string }[]
}

export interface OwnershipFact {
  form: 'physical' | 'rom' | 'digital'
  platform: string
  state: 'have' | 'want'
  note: string
}

// A known gaming system (from the IGDB platform catalog) — the searchable list
// in the ownership overlay's "add any system" section.
export interface SystemEntry { id: string; name: string; abbr?: string }
// One platform this game released on, per IGDB.
export interface GameRelease { id: string; name: string; abbr?: string
  year?: number | null; human?: string | null }

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
export interface AiCollectionMember { title: string; platform?: string; year?: number | null }
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
  // compilation membership proposal (DESIGN §13) — accepting it records the collection
  // AND materializes member entries, so it must render as a reviewable change
  collection?: { is_collection: boolean; name?: string | null
    members?: AiCollectionMember[] } | null
}
export interface FindingContext {
  title: string | null
  systems: string[]
  year: number | null
  sources: string[]
  files: string[]
  paths: string[]
  folders: string[]
  tags: string[]
  siblings: string[]
  current_match: string | null
  current_match_year?: number | null
  // The values a proposed change would REPLACE, keyed by attribute kind. Lets the review
  // page state "release year: 2007 → 2024" instead of only naming the new value.
  current_attrs?: Record<string, string | string[] | null>
  // how this game is currently identified (igdb_resolution.matched_by), its homebrew.py
  // release type (null = commercial), and whether that type must never be a commercial title.
  provenance?: string | null
  release_type?: string | null
  release_block?: boolean
  match_confidence?: number | null   // 0-100 identity certainty (task #13)
  match_reason?: string | null
}
export interface AiFinding {
  id: number
  run_id: number
  norm_key: string
  title: string
  kind: 'match' | 'identify' | 'supplement' | 'collection'
  status: 'proposed' | 'accepted' | 'rejected' | 'applied'
  confidence: number
  model: string
  created: number
  payload: AiFindingPayload
  context?: FindingContext | null
  selection?: { attributes: string[] | null; match: boolean } | null
  // proposed changes that would OVERWRITE the user's manual edits (pin / attr overrides)
  manual_conflicts?: { identity: boolean; attrs: string[] }
}
export interface AiApplySelection {
  finding_id: number
  attributes: string[] | null
  match: boolean
  // undefined = as proposed (cards view / older clients); false = membership unticked
  collection?: boolean
}
export type AiFindingCounts = Record<string, Record<string, number>>
// Per-platform cover diff for a finding: what accepting it does to the served cover.
export interface MediaDiffPlatform {
  entry_key: string
  platform: string | null
  has_before: boolean      // an entry serves a cover today
  own_art: boolean         // that cover is this console's OWN art (never displaced)
  change: 'add' | 'replace' | 'none'
}
export interface MediaAdd { kind: string; url: string; new: boolean }
export interface MediaDiff {
  norm_key: string
  title: string
  after_cover: string | null   // the matched provider cover the entry(ies) adopt
  platforms: MediaDiffPlatform[]
  added_art: MediaAdd[]         // full IGDB art set that fetches on apply (cover/bg/shots)
}
export interface AiScanTargets { unmatched: number; matched: number; missing: number; all: number; web_capable: boolean; provider?: string; model?: string; escalation_model?: string | null; attributes: string[]; media_kinds: string[] }
export type ScopeValue = boolean | string[]   // true=all, false=none, [kinds]=subset
export interface ScanOpts { web?: boolean; match_provider?: boolean; metadata?: ScopeValue; media?: ScopeValue; scores?: boolean }
export interface AiScanRun {
  id: number
  target: string
  total: number
  done: number
  findings: number
  skipped?: number
  errored?: number
  complete?: number      // already matched & complete — nothing to change
  unmatched?: number     // no match and the AI couldn't identify it
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
  identified?: number
  unidentified?: number
  wanted?: number
  cross_source: number
  unmatched: number
  low_confidence?: number
  no_media: number
  by_source: Record<string, number>
  media: { games_with_art: number; by_kind: Record<string, number> }
  pending_meta?: number
}

export interface Facets {
  sources: string[]
  platforms: string[]
  attributes?: Record<string, string[]>   // kind -> values (every categorical attribute)
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
  data?: boolean
  assigned: string | null
  assigned_model: string | null
  escalates?: boolean            // area has an escalated (web/hard-case) pass
  escalation_model?: string | null   // bigger model for that pass (null = reuse normal)
  effective: string | null
  effective_model: string | null
  prompt: string | null          // user override (null = using default)
  default_prompt: string
  prompt_vars: string[]          // <<token>> placeholders the prompt supports
}

export interface Caps { total: number; usd: number; input: number; output: number }
export interface CapUsed { total: number; input: number; output: number; usd: number; unpriced: boolean }
export interface AiUsageModel {
  provider: string; model: string; calls: number
  input: number; output: number; total: number; month: number
  month_usd: number; lifetime_usd: number | null; unpriced: boolean
  price: { in: number; out: number; cached: number | null } | null
  last_day: string | null; active_days: number; caps: Caps | null
}
export interface AiUsageProvider {
  provider: string; month: number; total: number
  month_usd: number; unpriced: boolean; caps: Caps | null
}
export interface AiUsageDay { day: string; calls: number; input: number; output: number }
export interface Currency { code: string; fx: number }
export interface AiUsageSummary {
  models: AiUsageModel[]; providers: AiUsageProvider[]; currency: Currency
}
export interface AiCap { scope: 'global' | 'provider' | 'model'; key: string; caps: Caps; used: CapUsed }
export interface AiPrice {
  provider: string; model: string
  in_usd: number | null; out_usd: number | null; cached_usd: number | null
  source: string; updated: string
}

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
  areas?: Record<string, { provider?: string; model?: string; escalation_model?: string; prompt?: string }>
}

export interface ArtPick {
  kind: string
  candidates: { id: number; provider: string }[]
  recommended_id: number | null
  reason: string
}

export interface ProviderScope {
  provider: string
  enabled: boolean
  off_sources: string[]      // exclusions only — everything else is ON
  off_platforms: string[]
  cost: string               // measured per-game wall clock, shown in the UI
}
export interface ProviderScopeState {
  providers: ProviderScope[]; sources: string[]; platforms: string[]
}

export interface MatchedProvider {
  provider: string
  matched: boolean
  id: string | null
  url: string | null
  holds: Record<string, number>     // kind -> how many assets we already hold from it
}

export interface DupeCandidate {
  a: string; b: string; a_nk: string; b_nk: string; a_src: string; b_src: string; ratio: number
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
export type MediaMode = 'ondemand' | 'chosen' | 'all'
export type MediaJob = {
  running?: boolean; finished?: boolean; mode?: string; step?: string
  ok?: boolean | null; downloaded?: number; dead?: number; error?: string
}
export interface FsStat {
  ok: boolean; error?: string; path?: string
  type?: string; size?: number | null; mtime?: number | null
  perm?: string; owner?: string; group?: string
  dirs?: number | null; files?: number | null; total?: number | null
}
export type FileopsApplyMode = 'preview' | 'immediate'
export type MediaLangMode = 'off' | 'hide' | 'ban'
export interface MediaLangResult {
  mode: MediaLangMode; scanned: number; hidden: number; banned: number; kept: number
}
export interface SpotlightTheme {
  id: string
  title: string
  subtitle: string
  enabled: boolean
}
export interface BackupJob {
  id: number; name: string; enabled: number
  contents: string[]; all_contents: boolean
  dest_kind: 'local' | 'device'; dest_path: string; device_id: number | null
  every_minutes: number; retention: number; encrypted: boolean
  last_run: number; last_ok: number | null; last_error: string
  last_file: string; last_size: number
}
export interface BackupItem { file: string; id: string; name: string; role: string; size: number }
export interface BackupRun {
  running: boolean; id: number; name: string; log: string[]
  ok: boolean | null; error?: string; scheduled?: boolean
  result?: { file: string; size: number; databases: number; pruned: number; dest: string }
}
export interface BackupsState {
  jobs: BackupJob[]; available: BackupItem[]; job: BackupRun | null
  devices: { id: number; name: string; transport: string }[]
}

export interface Prefs {
  hide_non_games: boolean
  spotlight_seconds: number
  spotlight_disabled?: string[]
  spotlight_include_collections?: boolean
  media_mode: MediaMode
  screenshot_limit?: number    // max screenshots kept per game (0 = no limit)
  media_language: string       // '' = any; else the preferred media language (legacy single)
  media_languages: string[]    // ordered 1st,2nd,3rd preferred media languages
  media_regions?: string       // comma-ordered region codes; blank = follow language
  media_lang_mode: MediaLangMode
  fileops_apply_mode: FileopsApplyMode
  manifests_enabled: boolean
  xbox_platform: 'xbox' | 'pc'   // which platform inbound Xbox games are bucketed as
  match_confidence_threshold?: number   // identity certainty below this = "low confidence"
  match_ai_band_lo?: number             // gray zone the wand's AI re-scores
  match_ai_band_hi?: number
  auto_fix_confidence?: number   // 50-100; AI certainty the wand needs to auto-fix
  media_job: MediaJob | null
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
export type ImportMode = 'algo' | 'lite' | 'heavy'
export type ResetScope = 'library' | 'curation' | 'factory'
export interface ResetPlan {
  scope: ResetScope; databases: string[]; database_bytes: number
  tsvs: string[]; tsv_bytes: number
  rom_indexes: string[]; rom_index_bytes: number
  media_files: number; media_bytes: number; media_repo: string
  media_preserved: string[]
  token_dirs: string[]; kept: string[]; total_bytes: number
}
export interface LibraryManager {
  id: number; device_id: number; kind: string; kind_label: string
  name: string; rom_path: string; media_path: string; enabled: number
  media_kinds?: string[]
  import_mode?: ImportMode
}
export interface ImportEstimate {
  mode: ImportMode; has_cap: boolean; targets?: number; calls?: number
  in_tokens?: number; out_tokens?: number; cost_usd?: number | null
  provider?: string; model?: string; error?: string
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
  field_label?: string
  post?: string
  connected: boolean
  note?: string
  mode?: 'device' | 'paste'   // 'device' = code-at-microsoft.com/link (Xbox); default paste
  start?: string              // device-flow: POST to begin, returns user_code
  poll?: string               // device-flow: POST to poll for completion
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
  doc?: { url: string; label: string }
}

export interface SyncService {
  id: string
  name: string
  enabled: boolean
  ready: boolean
  needs_auth: boolean
  connect: ServiceConnect | null
  count: number | null
  can_media: boolean
  import_mode?: ImportMode
}
export interface SyncJobService {
  state: 'pending' | 'running' | 'ok' | 'failed' | 'skipped'
  count: number | null
  error: string | null
  reauth?: boolean
}
export interface SyncPhase {
  id: string
  label: string
  state: 'pending' | 'running' | 'ok' | 'failed' | 'skipped'
  detail: string
}
export interface SyncJob {
  running: boolean
  finished: boolean
  step: string
  error: string | null
  added: number | null
  services: Record<string, SyncJobService>
  phases?: SyncPhase[]
}

// ROM-repo sync (Connections devices with ROM library managers)
export interface RomManager {
  id: number; kind: string; kind_label: string; name: string
  rom_path: string; count: number | null; games?: number | null
}
export interface RomLocation {
  id: number; name: string; transport: string; host: string
  enabled: boolean; managers: RomManager[]; count: number | null
  games?: number | null
}
export interface RomJobDevice {
  state: 'pending' | 'running' | 'ok' | 'failed'
  roms: number | null; error: string | null
}
export interface RomJob {
  running: boolean; finished: boolean; step: string; error: string | null
  devices: Record<string, RomJobDevice>; prog?: { done: number; total: number }
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
  chosen?: boolean            // true = the asset actually used/displayed for this kind
  used?: boolean          // the asset the SERVE resolver actually returns
  redistributable?: boolean   // false = keep locally, don't copy to other machines
  url: string
  thumb: string | null
  user?: boolean
}
export interface BannedMedia {
  norm_key: string; kind: string; provider: string; ref: string
  updated: number; title: string
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
export interface BackingStore { name: string; local: number; remote: number; pulled: number; pulled_deleted: number; pushed: number; pushed_deleted: number }
export interface BackingResult { backend?: string; dry_run?: boolean; stores?: BackingStore[]; error?: string; at?: number }

export interface SpotlightItem {
  norm_key: string
  entry_key?: string        // per-platform entry id — the unique, addressable key
  platform?: string | null
  title: string
  score: number | null
  sources: string
  matched: boolean
  has_cover: boolean
  cover_v?: string | null
  n_platforms?: number      // how many platform entries this collapsed tile represents
}
export interface Spotlight {
  kind: string
  title: string
  subtitle: string
  items: SpotlightItem[]
}

export interface GamesQuery {
  q?: string
  query?: string   // advanced query-language search (field:value, -neg, year:>N)
  source?: string
  platform?: string
  has_kind?: string
  include?: string[]
  exclude?: string[]
  sort?: string[]
  status?: 'owned' | 'utilities' | 'wanted' | 'all'   // ownership filter (default owned)
  identified?: 'only' | 'all' | 'unidentified'  // hide bare ROMs (default only)
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
export interface ManifestMedia { kinds: string[]; where: string; device: string | null; layout: string; for: string }
export interface ManifestBrief {
  profile: string | null; profile_name?: string | null
  conforms?: boolean; written_at?: string; written_by?: string
  media?: ManifestMedia[]; role?: string; fresh: boolean; files?: number | null
}
export interface FileDetect {
  current: 'flat' | 'folder'; systems: string[]
  counts: { files: number; capped?: boolean; top_exts: [string, number][] }; sample: string[]
  capped?: boolean
  manifest?: ManifestBrief | null
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
export interface SourceModel {
  system_at?: string; groups?: string[]
  media?: { present: boolean; where?: string; naming?: string }; summary?: string
}
export interface SourceModelResult { model: SourceModel; detected: FileDetect }
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
  id: string; kind: 'sync' | 'romsync' | 'fileops' | 'aimeta' | 'aimeta-apply'; run_id?: number; label: string
  status: string; detail: string; error: string | null; progress: JobProgress
  when: number | null; cancelable: boolean; restartable: boolean; deletable: boolean
  findings?: number   // aimeta scan jobs: how many suggestions to review/accept
  target_key?: string // single-game scan → the game's key, so its name links to detail
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
    if (qy.query) p.set('query', qy.query)
    if (qy.source) p.set('source', qy.source)
    if (qy.platform) p.set('platform', qy.platform)
    if (qy.has_kind) p.set('has_kind', qy.has_kind)
    if (qy.include?.length) p.set('include', qy.include.join(','))
    if (qy.exclude?.length) p.set('exclude', qy.exclude.join(','))
    if (qy.sort?.length) p.set('sort', qy.sort.join(','))
    if (qy.status && qy.status !== 'owned') p.set('status', qy.status)
    if (qy.identified && qy.identified !== 'only') p.set('identified', qy.identified)
    p.set('limit', String(qy.limit ?? 60))
    p.set('offset', String(qy.offset ?? 0))
    return get<GamesPage>('/api/games?' + p.toString())
  },
  detail: (nk: string) => get<GameDetail>('/api/games/' + encodeURIComponent(nk)),
  suspectedDupes: (limit = 60) => get<{ dupes: DupeCandidate[] }>('/api/games/dupes?limit=' + limit),
  mergeGame: async (nk: string, other: string, canonical: 'this' | 'other',
                    force = false) => {
    const r = await fetch('/api/games/' + encodeURIComponent(nk) + '/merge', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ other, canonical, force }),
    })
    if (r.status === 409) {                     // different-year remake — needs confirm
      let msg = 'These look like different games — confirm to merge.'
      try { msg = (await r.json()).detail || msg } catch { /* keep default */ }
      const e = new Error(msg); e.name = 'ConfirmRequired'; throw e
    }
    if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 160)}`)
    return r.json() as Promise<{ merged: boolean; canonical: string; from: string }>
  },
  gameSources: (nk: string) =>
    get<{ norm_key: string; title: string; sources: SourceRow[] }>(
      '/api/games/' + encodeURIComponent(nk) + '/sources'),
  splitSuggest: async (nk: string) => {
    const r = await fetch('/api/games/' + encodeURIComponent(nk) + '/split-suggest',
      { method: 'POST' })
    if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 160)}`)
    return r.json() as Promise<SplitSuggestion>
  },
  splitGame: async (nk: string, rows: { source: string; source_id: string }[],
                    title: string) => {
    const r = await fetch('/api/games/' + encodeURIComponent(nk) + '/split', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ rows, title }),
    })
    if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 180)}`)
    return r.json() as Promise<{ split: boolean; to_key: string; title: string; peeled: number }>
  },
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
  setOwnership: async (nk: string, form: string, platform: string, state: string, note = '', title?: string) => {
    const r = await fetch('/api/games/' + encodeURIComponent(nk) + '/ownership', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ form, platform, state, note, title }),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ ownership: OwnershipFact[] }>
  },
  clearOwnership: async (nk: string, form: string, platform: string, state: string) => {
    const q = new URLSearchParams({ form, platform, state }).toString()
    const r = await fetch('/api/games/' + encodeURIComponent(nk) + '/ownership?' + q,
      { method: 'DELETE' })
    if (!r.ok) throw new Error(`${r.status} ownership`)
    return r.json() as Promise<{ ownership: OwnershipFact[] }>
  },
  gameReleases: async (nk: string) => {
    const r = await fetch('/api/games/' + encodeURIComponent(nk) + '/releases')
    if (!r.ok) throw new Error(`${r.status} releases`)
    return r.json() as Promise<{ resolved: boolean; igdb_id?: number; name?: string;
      releases: GameRelease[]; source?: string | null; error?: string }>
  },
  knownSystems: async () => {
    const r = await fetch('/api/systems')
    if (!r.ok) throw new Error(`${r.status} systems`)
    return r.json() as Promise<{ systems: SystemEntry[]; error?: string }>
  },
  setFraming: async (nk: string, kind: string, f: Frame) => {
    const r = await fetch('/api/games/' + encodeURIComponent(nk) + '/framing', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ kind, ...f }),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ kind: string; framing: Frame }>
  },
  clearFraming: async (nk: string, kind: string) => {
    const r = await fetch('/api/games/' + encodeURIComponent(nk) + '/framing?kind=' +
      encodeURIComponent(kind), { method: 'DELETE' })
    if (!r.ok) throw new Error(`${r.status} framing`)
    return r.json()
  },
  setHeroPref: async (nk: string, source: string) => {
    const r = await fetch('/api/games/' + encodeURIComponent(nk) + '/hero', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ source }),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ hero_pref: string | null }>
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
  banMedia: async (nk: string, id: number) => {
    const r = await fetch(`/api/games/${encodeURIComponent(nk)}/media/${id}/ban`, { method: 'POST' })
    if (!r.ok) throw new Error(`${r.status} ban`)
    return r.json() as Promise<MediaLibrary>
  },
  setMediaRedist: async (nk: string, id: number, redistributable: boolean) => {
    const r = await fetch(`/api/games/${encodeURIComponent(nk)}/media/${id}/redist`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ redistributable }),
    })
    if (!r.ok) throw new Error(`${r.status} redist`)
    return r.json() as Promise<MediaLibrary>
  },
  bannedMedia: () => get<{ banned: BannedMedia[] }>('/api/media/banned'),
  unbanMedia: async (b: { norm_key: string; kind: string; provider: string; ref: string }) => {
    const r = await fetch('/api/media/unban', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(b),
    })
    if (!r.ok) throw new Error(`${r.status} unban`)
    return r.json() as Promise<{ ok: boolean }>
  },
  mediaUrl: (nk: string, kind: string, thumb = false, v?: string | null) =>
    `/api/media/${encodeURIComponent(nk)}/${encodeURIComponent(kind)}` +
    (thumb ? '?size=thumb' : '') +
    (v ? (thumb ? '&' : '?') + 'v=' + encodeURIComponent(v) : ''),
  assetUrl: (id: number, thumb = false) =>
    `/api/media-asset/${id}` + (thumb ? '?size=thumb' : ''),
  artPick: async (nk: string, kind = 'cover') => {
    const r = await fetch(`/api/ai/art-pick/${encodeURIComponent(nk)}?kind=${kind}`,
      { method: 'POST' })
    if (!r.ok) throw new Error(`${r.status}`)
    return r.json() as Promise<ArtPick>
  },
  // Providers this game is MATCHED to — drives the "Fetch from…" menu. A provider
  // with no match comes back matched:false rather than missing, because absent and
  // unmatched are different things and hiding one makes it look like the other.
  providerScope: async () => {
    const r = await fetch('/api/providers/scope')
    if (!r.ok) throw new Error(`${r.status}`)
    return r.json() as Promise<ProviderScopeState>
  },
  setProviderScope: async (body: {
    provider: string; enabled?: boolean
    off_sources?: string[]; off_platforms?: string[]
  }) => {
    const r = await fetch('/api/providers/scope', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!r.ok) throw new Error(`${r.status}`)
    return r.json() as Promise<ProviderScopeState>
  },
  matchedProviders: async (nk: string) => {
    const r = await fetch(`/api/media/matched-providers/${encodeURIComponent(nk)}`)
    if (!r.ok) throw new Error(`${r.status}`)
    return r.json() as Promise<{ providers: MatchedProvider[] }>
  },
  // Deterministic pull from one matched provider. Free by definition — no AI area is
  // consulted — and additive, so candidates land immediately; only a change to the
  // CHOSEN asset is worth reporting back.
  mediaFetch: async (nk: string, provider: string, kinds?: string[]) => {
    const r = await fetch(`/api/media/fetch/${encodeURIComponent(nk)}`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ provider, kinds: kinds ?? null }),
    })
    if (!r.ok) throw new Error(`${r.status}`)
    return r.json() as Promise<{ added: number; chosen_changed: string[]; provider: string }>
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
  spotlight: (kind = 'random', exclude?: string) =>
    get<Spotlight>('/api/spotlight?kind=' + encodeURIComponent(kind)
      + (exclude ? '&exclude=' + encodeURIComponent(exclude) : '')),
  // Global preferences
  spotlightThemes: () =>
    get<{ themes: SpotlightTheme[] }>('/api/spotlight/themes'),
  prefs: () => get<Prefs>('/api/prefs'),
  setPrefs: async (p: Partial<Prefs>) => {
    const r = await fetch('/api/prefs', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(p),
    })
    if (!r.ok) throw new Error(`${r.status} prefs`)
    return r.json() as Promise<Prefs>
  },
  mediaLanguageFilter: (mode?: MediaLangMode) =>
    postJson<MediaLangResult>('/api/media/language-filter', mode ? { mode } : {}),
  mediaMaterialize: (mode?: MediaMode) =>
    postJson<{ media_job: MediaJob }>('/api/media/materialize', mode ? { mode } : {}),
  mediaMaterializeStatus: () => get<{ media_job: MediaJob }>('/api/media/materialize'),
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
  // Device wishlist: "I want these games on that device" (emulation only for now).
  wantsSummary: () => get<{ counts: Record<string, number> }>('/api/wants'),
  deviceWants: (id: number) => get<{ wants: GameRow[]; total: number }>('/api/devices/' + id + '/wants'),
  addWants: async (id: number, norm_keys: string[]) => {
    const r = await fetch('/api/devices/' + id + '/wants', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ norm_keys }),
    })
    if (!r.ok) throw new Error(`${r.status}`)
    return r.json() as Promise<{ added: number; skipped: number }>
  },
  removeWant: async (id: number, norm_key: string) => {
    const r = await fetch('/api/devices/' + id + '/wants/' + encodeURIComponent(norm_key), { method: 'DELETE' })
    if (!r.ok) throw new Error(`${r.status}`)
    return r.json() as Promise<{ ok: boolean }>
  },
  // Collections / compilations (DESIGN §13)
  setCollection: async (collKey: string, name: string, members: { title: string; platform?: string; year?: number | null }[]) => {
    const r = await fetch('/api/collections/' + encodeURIComponent(collKey), {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ name, members }),
    })
    if (!r.ok) throw new Error(`${r.status}`)
    return r.json() as Promise<{ coll_key: string; name: string; members: number }>
  },
  deleteCollection: async (collKey: string) => {
    const r = await fetch('/api/collections/' + encodeURIComponent(collKey), { method: 'DELETE' })
    if (!r.ok) throw new Error(`${r.status}`)
    return r.json() as Promise<{ ok: boolean }>
  },
  // Directory autocomplete for ROM/media paths. id 0 = local ludodex host/container.
  browseDevice: async (id: number, path: string) => {
    const r = await fetch('/api/devices/browse', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ device_id: id, path }),
    })
    if (!r.ok) throw new Error(`${r.status}`)
    return r.json() as Promise<{ ok: boolean; path: string; dirs: string[]; error?: string }>
  },
  // Read-only folder browser (Files › Browse): immediate dirs (with child counts)
  // + files (with sizes) of a path on a device. Lazy, one level per expand.
  browseEntries: async (id: number, path: string) => {
    const r = await fetch('/api/devices/browse-entries', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ device_id: id, path }),
    })
    if (!r.ok) throw new Error(`${r.status}`)
    return r.json() as Promise<{
      ok: boolean; path: string
      dirs: { name: string; nfiles: number }[]
      files: { name: string; size: number }[]
      error?: string
    }>
  },
  syncDevice: async (id: number) => {
    const r = await fetch('/api/devices/' + id + '/sync', { method: 'POST' })
    if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 160)}`)
    return r.json() as Promise<{ device: string; results: { manager: string; kind: string; ok: boolean; roms?: number; media?: string; error?: string }[] }>
  },
  backupArchives: (jobId: number) =>
    get<{ archives: string[]; encrypted: boolean; dest: string; dest_kind: string }>(
      `/api/backups/archives?job_id=${jobId}`),
  restoreBackup: (job_id: number, name: string, passphrase?: string) =>
    postJson<{ ok: boolean; count: number; restored: string[]; safety_backup: string }>(
      '/api/backups/restore', { job_id, name, passphrase }),
  restoreBackingStore: (dry_run?: boolean) =>
    postJson<{ backend: string; dry_run: boolean; restored: number
      stores: { name: string; remote: number; local_before: number; written: number }[] }>(
      '/api/backingstore/restore', { dry_run }),
  backups: () => get<BackupsState>('/api/backups/jobs'),
  backupStatus: () => get<{ job: BackupRun | null; jobs: BackupJob[] }>('/api/backups/status'),
  setBackupJob: (j: Partial<BackupJob> & { passphrase?: string | null }) =>
    postJson<{ ok: boolean; id: number }>('/api/backups/jobs', j),
  deleteBackupJob: (id: number) => mutate<{ ok: boolean }>(`/api/backups/jobs/${id}`, 'DELETE'),
  runBackupJob: (id: number) => postJson<{ ok: boolean }>(`/api/backups/jobs/${id}/run`, {}),
  importBackup: (path: string, passphrase?: string) =>
    postJson<{ ok: boolean; id: string; databases: number }>('/api/backups/import',
      { path, passphrase }),
  setManager: async (m: Partial<LibraryManager>) => {
    const r = await fetch('/api/devices/managers', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(m) })
    if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 140)}`)
    return r.json() as Promise<{ devices: Device[] }>
  },
  // What an import tier would cost on this source, and whether a budget cap is set
  importEstimate: (mode: ImportMode, mgr?: number) =>
    get<ImportEstimate>(`/api/devices/import-estimate?mode=${mode}` +
      (mgr ? `&mgr=${mgr}` : '')),
  ingestHints: (limit = 200) =>
    get<{ count: number; hints: Record<string, unknown>[] }>(`/api/ingest-hints?limit=${limit}`),
  clearIngestHints: async (system?: string) => {
    const r = await fetch('/api/ingest-hints' + (system ? `?system=${encodeURIComponent(system)}` : ''),
      { method: 'DELETE' })
    if (!r.ok) throw new Error(`${r.status} hints`); return r.json() as Promise<{ cleared: number }>
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
  aiUsage: () => get<AiUsageSummary>('/api/ai/usage'),
  aiUsageSeries: (provider: string, model: string) =>
    get<{ provider: string; model: string; days: AiUsageDay[] }>(
      `/api/ai/usage/series?provider=${encodeURIComponent(provider)}&model=${encodeURIComponent(model)}`),
  aiLimits: () => get<{ caps: AiCap[] }>('/api/ai/limits'),
  setAiLimit: async (scope: 'global' | 'provider' | 'model', key: string, caps: Partial<Caps>) => {
    const r = await fetch('/api/ai/limit', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ scope, key, caps }),
    })
    if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 140)}`)
    return r.json() as Promise<{ caps: AiCap[]; usage: AiUsageSummary }>
  },
  aiPrices: () => get<{ prices: AiPrice[]; currency: Currency; openrouter: boolean;
    schedule: { daily: boolean; time: string }; last_update: string | null }>('/api/ai/prices'),
  setPricesOpenRouter: async (openrouter: boolean) => {
    const r = await fetch('/api/ai/prices/source', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ openrouter }),
    })
    if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 140)}`)
    return r.json() as Promise<{ openrouter: boolean }>
  },
  setPriceSchedule: async (daily: boolean, time?: string) => {
    const r = await fetch('/api/ai/prices/schedule', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ daily, time }),
    })
    if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 140)}`)
    return r.json() as Promise<{ schedule: { daily: boolean; time: string } }>
  },
  setAiPrice: async (provider: string, model: string, in_usd: number, out_usd: number, cached_usd?: number | null) => {
    const r = await fetch('/api/ai/price', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ provider, model, in_usd, out_usd, cached_usd }),
    })
    if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 140)}`)
    return r.json() as Promise<{ prices: AiPrice[] }>
  },
  refreshAiPrices: async () => {
    const r = await fetch('/api/ai/prices/refresh', { method: 'POST' })
    if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 140)}`)
    return r.json() as Promise<{ updated: number; checked: number; prices: AiPrice[] }>
  },
  resolveAiPrices: async (use_ai: boolean, note?: string) => {
    const r = await fetch('/api/ai/prices/resolve', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ use_ai, note: note || '' }),
    })
    if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 140)}`)
    return r.json() as Promise<{ prices: AiPrice[]; fetched: number; ai_resolved: number;
      targeted: number; still_missing: number; fetch_error: string | null; ai_error: string | null }>
  },
  setCurrency: async (code: string, fx?: number) => {
    const r = await fetch('/api/ai/currency', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ code, fx }),
    })
    if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 140)}`)
    return r.json() as Promise<{ currency: Currency }>
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
  // Dynamic sign-in URL (Nintendo PKCE): the button asks the server to mint the
  // authorize URL (and stash the matching verifier) right before opening it.
  authorizeStart: async (startPath: string) => {
    const r = await fetch(startPath, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' })
    if (!r.ok) throw new Error(`${r.status}`)
    return r.json() as Promise<{ ok: boolean; url?: string; error?: string }>
  },
  // Device-code flow (Xbox): start returns the short user code + link; poll is
  // called on a timer until Microsoft reports the sign-in finished.
  deviceStart: async (startPath: string) => {
    const r = await fetch(startPath, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}',
    })
    if (!r.ok) throw new Error(`${r.status}`)
    return r.json() as Promise<{
      ok: boolean; user_code: string; verification_uri: string
      interval: number; expires_in: number; error?: string
    }>
  },
  devicePoll: async (pollPath: string) => {
    const r = await fetch(pollPath, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}',
    })
    if (!r.ok) throw new Error(`${r.status}`)
    return r.json() as Promise<{
      status: 'pending' | 'connected' | 'expired' | 'declined'; account: string | null
    }>
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
  syncStatus: () => get<{ services: SyncService[]; job: SyncJob | null; has_cap?: boolean }>('/api/sync/status'),
  bulkAttrKinds: () => get<{ kinds: string[] }>('/api/attributes/bulk'),
  bulkSetAttribute: (body: { norm_keys: string[]; kind: string; value?: string; clear?: boolean }) =>
    postJson<{ ok: boolean; kind: string; count: number; cleared: boolean }>('/api/attributes/bulk', body),
  resetPlan: (scope: ResetScope) => get<ResetPlan>(`/api/ops/reset/plan?scope=${scope}`),
  resetRun: (scope: ResetScope, confirm?: string) =>
    postJson<{ ok: boolean; removed: string[]; failed: string[]; safety_backup: string }>(
      '/api/ops/reset', { scope, ...(confirm ? { confirm } : {}) }),
  setImportMode: (id: string, mode: ImportMode) =>
    postJson<{ ok: boolean }>('/api/sync/import-mode', { id, mode }),
  syncRun: async (services: string[], media: string[] = [], full = false) => {
    const r = await fetch('/api/sync/run', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ services, media, full }),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<SyncJob>
  },
  // Index EmulationStation/RetroArch art living inside a device's ROM tree, in
  // place (no move) — so existing local covers show up. Local devices only.
  scanLocalArt: async (deviceId: number) => {
    const r = await fetch('/api/media/scan-local', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ device_id: deviceId }),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ started: boolean; roots: string[] }>
  },
  // ROM-repo sync: rescan Connections devices' ROM locations, then rebuild.
  romsStatus: () => get<{ locations: RomLocation[]; job: RomJob | null }>('/api/roms/status'),
  romsRun: async (devices: number[] | 'all') => {
    const r = await fetch('/api/roms/run', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ devices: devices === 'all' ? 'all' : devices }),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<RomJob>
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
  // whole-fleet maintenance
  opsOptimize: async () => {
    const r = await fetch('/api/ops/optimize', { method: 'POST' })
    if (!r.ok) throw new Error(`${r.status}`)
    return r.json() as Promise<{ ok: boolean; optimized: number; reclaimed: number; errors: string[] }>
  },
  opsBackup: async () => {
    const r = await fetch('/api/ops/backup', { method: 'POST' })
    if (!r.ok) throw new Error(`${r.status}`)
    return r.json() as Promise<{ ok: boolean; id: string; count: number; size: number }>
  },
  opsBackups: () => get<{ backups: { id: string; count: number; size: number }[] }>('/api/ops/backups'),
  // two-way backing-store sync (durable stores <-> PocketBase/etc.)
  backingStatus: () => get<{ running: boolean; last: BackingResult | null; backend: string; configured: boolean }>('/api/backingstore/status'),
  backingRun: async (dry = false) => {
    const r = await fetch('/api/backingstore/run', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ dry_run: dry }),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ started: boolean; backend: string; running?: boolean }>
  },
  backingConfig: () => get<{ backend: string; values: Record<string, string>; secret_set: Record<string, boolean>; fields: Record<string, string[]>; auto_minutes: number }>('/api/backingstore/config'),
  backingConfigSet: (patch: { backend?: string; auto_minutes?: number; values?: Record<string, string> }) =>
    postJson<{ backend: string; values: Record<string, string>; secret_set: Record<string, boolean>; fields: Record<string, string[]>; auto_minutes: number }>('/api/backingstore/config', patch),
  backingTest: (backend?: string) =>
    postJson<{ ok: boolean; backend: string; detail?: string; error?: string }>('/api/backingstore/test', backend ? { backend } : {}),
  opsRestore: async (id: string) => {
    const r = await fetch('/api/ops/restore', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ id }),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ ok: boolean; restored: number; safety_backup: string; restart_required: boolean }>
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
  fileDetect: async (body: { device_id: number; root: string; scope: string; system?: string }, signal?: AbortSignal) => {
    const r = await fetch('/api/fileops/detect', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body), signal,
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<FileDetect>
  },
  filePlan: async (body: { device_id: number; root: string; profile: string | FileProfile; scope: string; system?: string }, signal?: AbortSignal) => {
    const r = await fetch('/api/fileops/plan', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body), signal,
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<FilePlan>
  },
  mediaLayouts: () => get<{ layouts: { id: string; name: string; desc: string }[] }>('/api/fileops/media-layouts'),
  planExtract: async (body: { device_id: number; root: string; dest?: string; scope: string; system?: string; layout?: string; op?: 'move' | 'copy' }, signal?: AbortSignal) => {
    const r = await fetch('/api/fileops/plan-extract', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body), signal,
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<FilePlan>
  },
  modelSource: async (body: { device_id: number; root: string; scope: string; system?: string }) => {
    const r = await fetch('/api/fileops/model-source', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<SourceModelResult>
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
  createRunbook: async (body: { device_id: number; root: string; profile?: string | FileProfile; operation?: string; dest?: string; scope: string; system?: string; note?: string; layout?: string; op?: 'move' | 'copy' }) => {
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
  // ---- Commander: build a reversible runbook from raw same-device drops ----
  createRunbookOps: async (body: { device_id: number; root: string; ops: { op: string; src?: string; dst?: string }[]; label?: string; note?: string }) => {
    const r = await fetch('/api/fileops/runbook-ops', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ run_id: number; runbook: Runbook }>
  },
  // ---- Commander: cross-device transfer (backgrounded rsync job) ----
  fsTransfer: async (body: { src_device: number; dst_device: number; src_dir: string; dst_dir: string; items: string[]; mode: 'copy' | 'move' }) => {
    const r = await fetch('/api/fs/transfer', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ started: boolean; jid: string }>
  },
  fsMkdir: async (device_id: number, path: string) => {
    const r = await fetch('/api/fs/mkdir', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ device_id, path }),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ ok: boolean }>
  },
  fsDelete: async (device_id: number, paths: string[]) => {
    const r = await fetch('/api/fs/delete', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ device_id, paths }),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ ok: boolean; removed: number }>
  },
  fsStat: async (device_id: number, path: string) => {
    const r = await fetch('/api/fs/stat', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ device_id, path }),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<FsStat>
  },
  troubleshootRunbook: (id: number) => get<Troubleshoot>('/api/fileops/runbook/' + id + '/troubleshoot'),
  fileHistory: () => get<{ runs: RunHistoryRow[] }>('/api/fileops/history'),
  manifestWrite: async (body: { device_id: number; root: string; operation?: string; profile?: string; scope?: string; system?: string; dest?: string }) => {
    const r = await fetch('/api/fileops/manifest', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ started: boolean; jid: string }>
  },
  manifestDelete: async (device_id: number, root: string) => {
    const r = await fetch('/api/fileops/manifest/delete', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ device_id, root }),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ ok: boolean }>
  },
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
  clearJobs: async () => {
    const r = await fetch('/api/jobs/clear', { method: 'POST' })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ cleared: number }>
  },
  // ---- AI metadata audit & supplement ----
  aimetaTargets: () => get<AiScanTargets>('/api/aimeta/targets'),
  aimetaScans: () => get<{ scans: AiScanRun[] }>('/api/aimeta/scans'),
  aimetaFindings: (status?: string, kind?: string, runId?: number) => {
    const p = new URLSearchParams()
    if (status) p.set('status', status)
    if (kind) p.set('kind', kind)
    if (runId) p.set('run_id', String(runId))
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
  aimetaRefine: async (
    body: { norm_key: string; hint?: string; refs?: string[]; model?: string; web?: boolean; run_id?: number },
  ) => {
    const r = await fetch('/api/aimeta/refine', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ kind: string | null; finding: AiFinding | null; used_web: boolean; used_refs: string[]; model: string; context: FindingContext | null }>
  },
  // Hunt media for an already-identified game on demand (IGDB + SteamGridDB, + optional
  // AI open-web discovery when web:true). The trigger the wand lacks for matched games.
  aimetaRefreshMedia: async (body: { norm_key?: string; entry_key?: string; web?: boolean }) => {
    const r = await fetch('/api/aimeta/refresh-media', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ ok: boolean; norm_key: string; has_cover: boolean; chosen: Record<string, number>; web_added: number }>
  },
  // Full authoritative catalog re-derivation (background). Wand applies reconcile only
  // the touched games now, so this is the on-demand button for a global rebuild.
  rebuildCatalog: async () => {
    const r = await fetch('/api/catalog/rebuild', { method: 'POST' })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ started: boolean; running?: boolean }>
  },
  // On-demand AI art pick for one game — the wand's "pick nicest art" button. The ONLY
  // place the paid vision pick runs by default (routine apply/rebuild never calls it).
  aimetaPickArt: async (body: { norm_key?: string; entry_key?: string }) => {
    const r = await fetch('/api/aimeta/pick-art', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ ok: boolean; norm_key: string }>
  },
  // Manually pin an entry's identity to a specific IGDB game (the human override for
  // odd-ball cases). `igdb` = an IGDB game link, slug, or numeric id. `platform` present
  // → per-entry pin (just that platform); absent → whole title.
  aimetaPin: async (body: { norm_key: string; igdb?: string; platform?: string | null; detach?: boolean }) => {
    const r = await fetch('/api/aimeta/pin', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ ok: boolean; norm_key: string; platform: string | null; detached: boolean; igdb_id: number | null; title: string | null; url: string | null }>
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
  aimetaAccept: async (selections: AiApplySelection[]) => {
    const r = await fetch('/api/aimeta/accept', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ selections }),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ accepted: number; pending: number }>
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
    return r.json() as Promise<{ started: boolean; selected: number | null; coalesced?: boolean }>
  },
  aimetaMediaDiff: async (items: { norm_key: string; after_cover: string | null; igdb_id?: number | null; title?: string }[]) => {
    const r = await fetch('/api/aimeta/media-diff', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ items }),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ items: MediaDiff[]; sgdb: boolean }>
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
  setIdentityDisabled: async (nk: string, provider: string, disabled: boolean) => {
    const r = await fetch('/api/games/' + encodeURIComponent(nk) + '/identity/' + encodeURIComponent(provider), {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ disabled }),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<{ disabled_identity: string[] }>
  },
}
