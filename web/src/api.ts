// Typed client for the ludodex API.

export interface TagRef {
  tag: string
  origins: string[]   // e.g. ['ludodex'], ['playnite'], or multiple
}

export interface GameRow {
  norm_key: string
  entry_key?: string       // per-platform entry id (base_key@platform) — the addressable id
  // The CARD this row stands for: ONE per game, folding its platforms, editions and
  // remasters (2026-08-25 design). Key the grid and navigation on this. Art still comes
  // from entry_key, because the card shows its representative ENTRY's art, and that art
  // is gated on that entry's own system.
  card_key?: string
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
export interface RelatedCard {
  card_key: string; title: string; entry_key: string; platforms: string
}

export interface GameDetail {
  norm_key: string          // base title key (used for title-level mutations)
  entry_key?: string        // this platform entry's id (base_key@platform)
  platform?: string | null  // this entry's platform
  card_key?: string         // the card this entry belongs to
  card_title?: string       // the card's name (the game, not one of its editions)
  // What this card shows INSTEAD of merging. `versions` is the same game in another
  // form (an edition, a remaster, a port); `remakes` is a new work BUILT AGAIN from this
  // one, which is why it keeps its own card; `series` is the rest of the franchise.
  // Owned only.
  versions?: RelatedCard[]
  remakes?: RelatedCard[]
  series?: RelatedCard[]
  series_name?: string | null
  // Every owned copy on this card, one per platform, with the edition each one is.
  copies?: { entry_key: string; norm_key: string; platform: string; title: string
             edition?: string; via?: string }[]
  /** @deprecated superseded by `copies`; kept for one release. */
  also_owned_on?: { entry_key: string; platform: string; title: string; via?: string }[]
  // DLC / expansions owned FOR this game. Real entries, so each links to its own detail
  // page with its own release date, description and art.
  addons?: { entry_key: string; norm_key: string; platform: string; title: string
             kind: 'dlc' | 'expansion' }[]
  // set when THIS entry is itself add-on content
  content_kind?: 'dlc' | 'expansion' | null
  extends?: { entry_key: string; norm_key: string; platform: string; title: string } | null
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
  // entry_key: the catalog entry this member opens, or null when the library has no
  // matching entry (a bundle can name a game that was never materialized)
  members: { member_key: string; member_title: string; member_platform: string; member_year: number | null; origin: string; entry_key?: string | null }[]
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
  // Per-provider identity state. `missed` means searched-and-empty (a recorded miss,
  // retried later); `unattempted` means never asked — different claims, shown apart.
  providers?: {
    matched: { provider: string; id: string }[]
    missed: string[]
    unattempted: string[]
    ineligible?: { provider: string; why: string }[]
  }
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
  // covers where every candidate is flagged as a letterboxed paste, so the
  // deterministic rules ranked nothing and a tiebreak chose
  cover_undecided?: number
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
/** The supplemental match index: a file, optional, replaced wholesale.
 *  `present` and populated-ness are different questions, so both are reported. */
/** Publish — see docs/superpowers/specs/2026-08-13-publish-design.md */
export interface PublishEntry {
  entry_key: string; state: string; source: string; added: number
  title?: string; platform?: string; norm_key?: string
}
export interface PublishRule {
  id: number; device_id: number; enabled: number; label: string | null
  expr: string; ord: number
}
export interface PublishEffective {
  entries: string[]; from_rules: number; explicit_includes: number
  explicit_excludes: number; excluded_from_rules: string[]; rules: PublishRule[]
}
export interface PublishPlanItem {
  entry_key: string; title: string | null; platform: string | null
  system: string | null; action: string; reason: string
  source: string[]; dest: string[]
  convert: { from: string; to: string; tool: string } | null
  bytes_in: number; disc: number | null; blockers: string[]
}
export interface PublishPlan {
  device_id: number; profile: string; observed: boolean; dry_run: boolean
  items: PublishPlanItem[]
  totals: Record<string, number>
  blockers: string[]
  free_bytes?: number; over_capacity?: boolean
}
/** ScreenScraper grants limits PER ACCOUNT; ludodex reads them rather than assuming. */
export interface SsTier {
  configured: boolean
  error?: string
  granted?: { threads: number; per_day: number; per_min: number }
  using?: { threads: number; reserve: number; min_block_seconds: number }
  used_today?: number | null
  level?: string
}

export interface AttrCapProvider {
  // `wired` is a fourth fact, and it outranks the other two: a provider no enrichment
  // step calls will not fill this however switched-on and credentialled it is.
  id: string; label: string; note: string; enabled: boolean; configured: boolean; wired: boolean
}
export interface AttrCapabilities {
  kinds: Record<string, { providers: AttrCapProvider[]; unsupplied: boolean
                          unfilled_today: boolean; tooltip: string }>
  unsupplied_note: string
}

export interface TgdbLimit {
  configured: boolean
  error?: string | null
  period?: string
  configured_limit?: number
  reserve?: number
  remaining_reported?: number | null
  extra_allowance?: number
  budget?: number
  // The key grants more than the configured limit — i.e. a tier was bought and is
  // sitting unused. Worth surfacing rather than quietly leaving on the table.
  underconfigured?: boolean
}

export interface PublishJob {
  running: boolean; device_id: number; done: number; total: number
  current: string | null; error: string
  report: {
    copied: number; converted: number; updated: number; removed: number
    skipped: number; failed: number
    errors: { entry_key: string; error: string }[]
    elapsed?: number
  } | null
}

export interface MatchIndexState {
  path: string
  default_path: string
  present: boolean
  has_index: boolean
  size: number
  prefer: 'dynamic' | 'supplement'
  release_url: string
  // How this copy of the file GOT HERE. null means it predates the record, which is an
  // honest answer and not the same as "built locally".
  origin?: { kind: 'downloaded' | 'built'; url?: string; at?: number } | null
  // Whether a token is stored, never the token itself.
  release_token_set?: boolean
  // sha256 and size of the file actually in use, so the UI can compare it to the release.
  installed?: { sha256: string; size: number } | null
  learned_keys: number
  // The user's own layer, broken down by namespace: "4,000 learned" does not say whether
  // the thing you are missing is in there.
  learned?: { identities: number; keys: number; overrides: number; by_ns: Record<string, number> }
  overrides: number
  identities?: number
  keys?: number
  built_at?: number | null
  license?: string | null
  attribution?: string | null
  sources?: { name: string; url: string; license: string; provides: string }[]
  job: { state: string; got: number; total: number; error: string; mode?: string } | null
}

export interface MatchIndexRelease {
  configured: boolean
  url?: string
  error?: string
  version?: string
  published_at?: string
  notes?: string
  asset?: { url?: string; size?: number; name?: string; sha256?: string }
  installed?: { sha256: string; size: number } | null
  // True ONLY on a digest match. Equal sizes are not equal files.
  installed_is_release?: boolean
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
  // host/port are null when the bind is not knowable: the server reports the socket
  // it is actually listening on, rather than repeating a default it cannot verify.
  uptime_seconds: number; host: string | null; port: number | null
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

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const r = await fetch(path, { signal })
  if (!r.ok) throw new Error(`${r.status} ${path}`)
  return r.json()
}

// Send a mutation, surfacing the server's {detail} message on failure.
async function mutate<T>(path: string, method: string, body?: unknown,
                         signal?: AbortSignal): Promise<T> {
  const r = await fetch(path, {
    method,
    headers: body !== undefined ? { 'content-type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal,
  })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) {
    // FastAPI's own 422 carries `detail` as a list of objects, not a string, and
    // new Error() of that reads "[object Object]".
    const detail = (data as { detail?: unknown }).detail
    throw new Error(typeof detail === 'string' && detail ? detail
      : detail ? `${r.status} ${JSON.stringify(detail).slice(0, 200)}` : `${r.status} ${path}`)
  }
  return data as T
}
const postJson = <T>(path: string, body: unknown, signal?: AbortSignal) =>
  mutate<T>(path, 'POST', body, signal)

// The media-kind vocabulary is fixed for the life of the server, and the game detail
// asks for it on every open. One request per page load; a failed one is not kept, so
// the next caller retries.
let mediaKindsCache: Promise<{ kinds: MediaKind[] }> | null = null

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
  // How long the next ingest will take. `fresh` asks the RESET question — nothing
  // cached, every game pays full price — a very different number from a resync.
  ingestEstimate: (fresh = false, tier = 'lite') =>
    get<IngestEstimate>(
      `/api/ingest/estimate?tier=${tier}&fresh=${fresh ? 'true' : 'false'}`),
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
  // `signal` matters here: a superseded library fetch that still lands overwrites the
  // newer query's rows (or appends the old page onto them). Callers abort the old one.
  games: (qy: GamesQuery, signal?: AbortSignal) => {
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
    return get<GamesPage>('/api/games?' + p.toString(), signal)
  },
  detail: (nk: string, signal?: AbortSignal) =>
    get<GameDetail>('/api/games/' + encodeURIComponent(nk), signal),
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
  splitSuggest: (nk: string) =>
    mutate<SplitSuggestion>('/api/games/' + encodeURIComponent(nk) + '/split-suggest', 'POST'),
  splitGame: (nk: string, rows: { source: string; source_id: string }[],
              title: string) =>
    postJson<{ split: boolean; to_key: string; title: string; peeled: number }>('/api/games/' + encodeURIComponent(nk) + '/split', { rows, title }),
  achievements: (nk: string, signal?: AbortSignal) =>
    get<Achievements>('/api/games/' + encodeURIComponent(nk) + '/achievements', signal),
  addTag: (nk: string, tag: string) =>
    postJson<GameTags>('/api/games/' + encodeURIComponent(nk) + '/tags', { tag }),
  removeTag: (nk: string, tag: string) =>
    mutate<GameTags>('/api/games/' + encodeURIComponent(nk) + '/tags/' + encodeURIComponent(tag), 'DELETE'),
  setOwnership: (nk: string, form: string, platform: string, state: string, note = '', title?: string) =>
    postJson<{ ownership: OwnershipFact[] }>('/api/games/' + encodeURIComponent(nk) + '/ownership', { form, platform, state, note, title }),
  clearOwnership: (nk: string, form: string, platform: string, state: string) => {
    const q = new URLSearchParams({ form, platform, state }).toString()
    return mutate<{ ownership: OwnershipFact[] }>('/api/games/' + encodeURIComponent(nk) + '/ownership?' + q, 'DELETE')
  },
  gameReleases: (nk: string) =>
    get<{ resolved: boolean; igdb_id?: number; name?: string;
      releases: GameRelease[]; source?: string | null; error?: string }>('/api/games/' + encodeURIComponent(nk) + '/releases'),
  knownSystems: () =>
    get<{ systems: SystemEntry[]; error?: string }>('/api/systems'),
  setFraming: (nk: string, kind: string, f: Frame) =>
    postJson<{ kind: string; framing: Frame }>('/api/games/' + encodeURIComponent(nk) + '/framing', { kind, ...f }),
  clearFraming: (nk: string, kind: string) =>
    mutate<unknown>('/api/games/' + encodeURIComponent(nk) + '/framing?kind=' + encodeURIComponent(kind), 'DELETE'),
  setHeroPref: (nk: string, source: string) =>
    postJson<{ hero_pref: string | null }>('/api/games/' + encodeURIComponent(nk) + '/hero', { source }),
  mediaLibrary: (nk: string) =>
    get<MediaLibrary>('/api/games/' + encodeURIComponent(nk) + '/media'),
  mediaKinds: () => {
    if (!mediaKindsCache) {
      mediaKindsCache = get<{ kinds: MediaKind[] }>('/api/media-kinds')
      mediaKindsCache.catch(() => { mediaKindsCache = null })
    }
    return mediaKindsCache
  },
  uploadMedia: async (nk: string, kind: string, file: File) => {
    const r = await fetch(`/api/games/${encodeURIComponent(nk)}/media/` +
      `${encodeURIComponent(kind)}/upload?filename=${encodeURIComponent(file.name)}`, {
      method: 'POST', headers: file.type ? { 'content-type': file.type } : undefined,
      body: file,
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${r.status}`)
    return r.json() as Promise<MediaLibrary>
  },
  addMediaFromUrl: (nk: string, kind: string, url: string) =>
    postJson<MediaLibrary>(`/api/games/${encodeURIComponent(nk)}/media/` +
      `${encodeURIComponent(kind)}/url`, { url }),
  deleteUserMedia: (nk: string, id: number) =>
    mutate<MediaLibrary>(`/api/games/${encodeURIComponent(nk)}/media/user/${id}`, 'DELETE'),
  setPins: (nk: string, kind: string, ids: number[]) =>
    postJson<MediaLibrary>('/api/games/' + encodeURIComponent(nk) + '/pins', { kind, ids }),
  banMedia: (nk: string, id: number) =>
    mutate<MediaLibrary>(`/api/games/${encodeURIComponent(nk)}/media/${id}/ban`, 'POST'),
  setMediaRedist: (nk: string, id: number, redistributable: boolean) =>
    postJson<MediaLibrary>(`/api/games/${encodeURIComponent(nk)}/media/${id}/redist`, { redistributable }),
  bannedMedia: () => get<{ banned: BannedMedia[] }>('/api/media/banned'),
  unbanMedia: (b: { norm_key: string; kind: string; provider: string; ref: string }) =>
    postJson<{ ok: boolean }>('/api/media/unban', b),
  mediaUrl: (nk: string, kind: string, thumb = false, v?: string | null) =>
    `/api/media/${encodeURIComponent(nk)}/${encodeURIComponent(kind)}` +
    (thumb ? '?size=thumb' : '') +
    (v ? (thumb ? '&' : '?') + 'v=' + encodeURIComponent(v) : ''),
  artPick: (nk: string, kind = 'cover') =>
    mutate<ArtPick>(`/api/ai/art-pick/${encodeURIComponent(nk)}?kind=${kind}`, 'POST'),
  // Providers this game is MATCHED to — drives the "Fetch from…" menu. A provider
  // with no match comes back matched:false rather than missing, because absent and
  // unmatched are different things and hiding one makes it look like the other.
  providerScope: () =>
    get<ProviderScopeState>('/api/providers/scope'),
  setProviderScope: (body: {
    provider: string; enabled?: boolean
    off_sources?: string[]; off_platforms?: string[]
  }) =>
    postJson<ProviderScopeState>('/api/providers/scope', body),
  matchedProviders: (nk: string) =>
    get<{ providers: MatchedProvider[] }>(`/api/media/matched-providers/${encodeURIComponent(nk)}`),
  // Deterministic pull from one matched provider. Free by definition — no AI area is
  // consulted — and additive, so candidates land immediately; only a change to the
  // CHOSEN asset is worth reporting back.
  mediaFetch: (nk: string, provider: string, kinds?: string[]) =>
    postJson<{ added: number; chosen_changed: string[]; provider: string }>(`/api/media/fetch/${encodeURIComponent(nk)}`, { provider, kinds: kinds ?? null }),
  artApply: (id: number, norm_key: string, kind: string) =>
    postJson<unknown>('/api/ai/art-apply', { id, norm_key, kind }),
  dedupe: (limit = 15) =>
    postJson<{ suggestions: DedupeSuggestion[] }>('/api/ai/dedupe', { limit }),
  // Dashboard spotlight (themed top-N; 'random' rotates through themes)
  spotlight: (kind = 'random', exclude?: string) =>
    get<Spotlight>('/api/spotlight?kind=' + encodeURIComponent(kind)
      + (exclude ? '&exclude=' + encodeURIComponent(exclude) : '')),
  // Global preferences
  spotlightThemes: () =>
    get<{ themes: SpotlightTheme[] }>('/api/spotlight/themes'),
  prefs: () => get<Prefs>('/api/prefs'),
  setPrefs: (p: Partial<Prefs>) =>
    postJson<Prefs>('/api/prefs', p),
  mediaLanguageFilter: (mode?: MediaLangMode) =>
    postJson<MediaLangResult>('/api/media/language-filter', mode ? { mode } : {}),
  mediaMaterialize: (mode?: MediaMode) =>
    postJson<{ media_job: MediaJob }>('/api/media/materialize', mode ? { mode } : {}),
  // Add a game manually: identify by name (IGDB) or recognize from images (AI)
  identify: (name: string) =>
    get<{ query: string; candidates: IdentifyCandidate[]; provider: string | null }>(
      '/api/identify?name=' + encodeURIComponent(name)),
  addGame: (g: { title: string; source: string; platform: string; detail?: string }) =>
    postJson<{ ok: boolean; norm_key: string; new_game: boolean }>('/api/games/add', g),
  identifyImage: (images: string[]) =>
    postJson<{ games: RecognizedGame[]; count: number }>('/api/games/identify-image', { images }),
  identifyFolder: (path: string, limit?: number) =>
    postJson<{ games: RecognizedGame[]; count: number; scanned: number; total_found: number; batch_errors: number }>('/api/games/identify-folder', { path, limit }),
  // Connections › Devices (machines hosting library managers, pulled over SSH)
  devices: () => get<{ devices: Device[]; lm_kinds: Record<string, [string, boolean, boolean]> }>('/api/devices'),
  setDevice: (d: Partial<Device> & { password?: string }) =>
    postJson<{ devices: Device[] }>('/api/devices', d),
  removeDevice: (id: number) =>
    mutate<{ devices: Device[] }>('/api/devices/' + id, 'DELETE'),
  testDevice: (id: number) =>
    mutate<{ ok: boolean; detail: string }>('/api/devices/' + id + '/test', 'POST'),
  // Device wishlist: "I want these games on that device" (emulation only for now).
  wantsSummary: () => get<{ counts: Record<string, number> }>('/api/wants'),
  deviceWants: (id: number) => get<{ wants: GameRow[]; total: number }>('/api/devices/' + id + '/wants'),
  addWants: (id: number, norm_keys: string[]) =>
    postJson<{ added: number; skipped: number }>('/api/devices/' + id + '/wants', { norm_keys }),
  removeWant: (id: number, norm_key: string) =>
    mutate<{ ok: boolean }>('/api/devices/' + id + '/wants/' + encodeURIComponent(norm_key), 'DELETE'),
  // Collections / compilations (DESIGN §13)
  deleteCollection: (collKey: string) =>
    mutate<{ ok: boolean }>('/api/collections/' + encodeURIComponent(collKey), 'DELETE'),
  // Directory autocomplete for ROM/media paths. id 0 = local ludodex host/container.
  browseDevice: (id: number, path: string) =>
    postJson<{ ok: boolean; path: string; dirs: string[]; error?: string }>('/api/devices/browse', { device_id: id, path }),
  // Read-only folder browser (Files › Browse): immediate dirs (with child counts)
  // + files (with sizes) of a path on a device. Lazy, one level per expand.
  browseEntries: (id: number, path: string) =>
    postJson<{
      ok: boolean; path: string
      dirs: { name: string; nfiles: number }[]
      files: { name: string; size: number }[]
      error?: string
    }>('/api/devices/browse-entries', { device_id: id, path }),
  syncDevice: (id: number) =>
    mutate<{ device: string; results: { manager: string; kind: string; ok: boolean; roms?: number; media?: string; error?: string }[] }>('/api/devices/' + id + '/sync', 'POST'),
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
  // --- Publish -------------------------------------------------------------
  ssTier: () => get<SsTier>('/api/services/screenscraper/tier'),
  attrCapabilities: () => get<AttrCapabilities>('/api/attributes/capabilities'),
  tgdbLimit: (refresh = false) =>
    get<TgdbLimit>(`/api/services/thegamesdb/limit${refresh ? '?refresh=true' : ''}`),
  publishEffective: (dev: number) =>
    get<PublishEffective>(`/api/devices/${dev}/publish/effective`),
  publishIntent: (dev: number, state = 'include') =>
    get<{ entries: PublishEntry[]; state: string }>(
      `/api/devices/${dev}/publish?state=${state}`),
  publishRuleSave: (dev: number, b: Partial<PublishRule>) =>
    postJson<{ id: number; rules: PublishRule[] }>(`/api/devices/${dev}/publish/rules`, b),
  publishRuleDelete: (dev: number, id: number) =>
    mutate<{ rules: PublishRule[] }>(`/api/devices/${dev}/publish/rules/${id}`, 'DELETE'),
  publishPlan: (dev: number, b: Record<string, unknown>) =>
    postJson<PublishPlan>(`/api/devices/${dev}/publish/plan`, b),
  publishApply: (dev: number, plan: PublishPlan, allow_blocked = false) =>
    postJson<{ ok: boolean }>(`/api/devices/${dev}/publish/apply`,
      { plan, allow_blocked }),
  publishJob: () => get<{ job: PublishJob | null }>('/api/publish/job'),

  matchIndex: () => get<MatchIndexState>('/api/matchindex'),
  setMatchIndex: (b: { prefer?: string; path?: string; release_url?: string; release_token?: string }) =>
    postJson<MatchIndexState>('/api/matchindex/settings', b),
  // Takes the url so the UI can TEST what was typed, before it is saved.
  matchIndexRelease: (url?: string) =>
    get<MatchIndexRelease>('/api/matchindex/release' + (url ? `?url=${encodeURIComponent(url)}` : '')),
  matchIndexDownload: (b: { url: string; size?: number }) =>
    postJson<{ ok: boolean }>('/api/matchindex/download', b),
  matchIndexRebuild: () => postJson<{ ok: boolean }>('/api/matchindex/rebuild', {}),
  matchIndexClearLearned: (what: 'learned' | 'overrides' | 'all') =>
    postJson<{ ok: boolean; cleared: Record<string, number> }>(
      '/api/matchindex/learned/clear', { what }),
  matchIndexImportLearned: (data: unknown, replace = false) =>
    postJson<{ ok: boolean; imported: { identities: number; keys: number; overrides: number } }>(
      '/api/matchindex/learned/import', { data, replace }),
  backups: () => get<BackupsState>('/api/backups/jobs'),
  backupStatus: () => get<{ job: BackupRun | null; jobs: BackupJob[] }>('/api/backups/status'),
  setBackupJob: (j: Partial<BackupJob> & { passphrase?: string | null }) =>
    postJson<{ ok: boolean; id: number }>('/api/backups/jobs', j),
  deleteBackupJob: (id: number) => mutate<{ ok: boolean }>(`/api/backups/jobs/${id}`, 'DELETE'),
  runBackupJob: (id: number) => postJson<{ ok: boolean }>(`/api/backups/jobs/${id}/run`, {}),
  setManager: (m: Partial<LibraryManager>) =>
    postJson<{ devices: Device[] }>('/api/devices/managers', m),
  // What an import tier would cost on this source, and whether a budget cap is set
  importEstimate: (mode: ImportMode, mgr?: number) =>
    get<ImportEstimate>(`/api/devices/import-estimate?mode=${mode}` +
      (mgr ? `&mgr=${mgr}` : '')),
  removeManager: (id: number) =>
    mutate<{ devices: Device[] }>('/api/devices/managers/' + id, 'DELETE'),
  // AI token usage + monthly limits
  aiUsage: () => get<AiUsageSummary>('/api/ai/usage'),
  aiUsageSeries: (provider: string, model: string) =>
    get<{ provider: string; model: string; days: AiUsageDay[] }>(
      `/api/ai/usage/series?provider=${encodeURIComponent(provider)}&model=${encodeURIComponent(model)}`),
  aiLimits: () => get<{ caps: AiCap[] }>('/api/ai/limits'),
  // Asked at the moment work starts. A dollar budget on a model with no price cannot
  // stop anything, and the only useful time to say so is before the first call.
  aiPricingCheck: (area = 'ingest') =>
    get<{ ok: boolean; provider?: string; model?: string; priced?: boolean;
      budget_usd?: number; reason?: string }>(
      `/api/ai/pricing-check?area=${encodeURIComponent(area)}`),
  aiPriceSuggest: (provider?: string, model?: string) =>
    postJson<{ provider: string; model: string; resolved: string | null;
      basis: 'exact' | 'feed' | 'alias' | 'ai' | 'family' | 'unknown'; price: number[] | null; like?: string }>('/api/ai/price/suggest', { provider, model }),
  setAiLimit: (scope: 'global' | 'provider' | 'model', key: string, caps: Partial<Caps>) =>
    postJson<{ caps: AiCap[]; usage: AiUsageSummary }>('/api/ai/limit', { scope, key, caps }),
  aiPrices: () => get<{ prices: AiPrice[]; currency: Currency; openrouter: boolean;
    schedule: { daily: boolean; time: string }; last_update: string | null }>('/api/ai/prices'),
  setPricesOpenRouter: (openrouter: boolean) =>
    postJson<{ openrouter: boolean }>('/api/ai/prices/source', { openrouter }),
  setPriceSchedule: (daily: boolean, time?: string) =>
    postJson<{ schedule: { daily: boolean; time: string } }>('/api/ai/prices/schedule', { daily, time }),
  setAiPrice: (provider: string, model: string, in_usd: number, out_usd: number, cached_usd?: number | null) =>
    postJson<{ prices: AiPrice[] }>('/api/ai/price', { provider, model, in_usd, out_usd, cached_usd }),
  refreshAiPrices: () =>
    mutate<{ updated: number; checked: number; prices: AiPrice[] }>('/api/ai/prices/refresh', 'POST'),
  resolveAiPrices: (use_ai: boolean, note?: string) =>
    postJson<{ prices: AiPrice[]; fetched: number; ai_resolved: number;
      targeted: number; still_missing: number; fetch_error: string | null; ai_error: string | null }>('/api/ai/prices/resolve', { use_ai, note: note || '' }),
  setCurrency: (code: string, fx?: number) =>
    postJson<{ currency: Currency }>('/api/ai/currency', { code, fx }),
  // AI provider config (phase 3 — BYOAI; keys are write-only, never returned)
  aiConfig: () => get<AiConfig>('/api/ai/config'),
  aiModels: (provider: string, refresh = false, vision = false) =>
    get<{ provider: string; models: string[] }>(
      `/api/ai/models/${encodeURIComponent(provider)}?refresh=${refresh}&vision=${vision}`),
  setAiConfig: (body: AiConfigUpdate) =>
    postJson<AiConfig>('/api/ai/config', body),
  // Service credentials (Sources + Providers; secrets returned masked)
  servicesConfig: () => get<{ services: Service[] }>('/api/services'),
  connectService: (postPath: string, value: string) =>
    postJson<{ ok: boolean; account: string | null; error?: string }>(postPath, { value }),
  // Dynamic sign-in URL (Nintendo PKCE): the button asks the server to mint the
  // authorize URL (and stash the matching verifier) right before opening it.
  authorizeStart: (startPath: string) =>
    postJson<{ ok: boolean; url?: string; error?: string }>(startPath, {}),
  // Device-code flow (Xbox): start returns the short user code + link; poll is
  // called on a timer until Microsoft reports the sign-in finished.
  deviceStart: (startPath: string) =>
    postJson<{
      ok: boolean; user_code: string; verification_uri: string
      interval: number; expires_in: number; error?: string
    }>(startPath, {}),
  devicePoll: (pollPath: string) =>
    postJson<{
      status: 'pending' | 'connected' | 'expired' | 'declined'; account: string | null
    }>(pollPath, {}),
  setSourceEnabled: (id: string, enabled: boolean) =>
    postJson<{ id: string; enabled: boolean }>(`/api/services/${encodeURIComponent(id)}/enabled`, { enabled }),
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
  syncRun: (services: string[], media: string[] = [], full = false) =>
    postJson<SyncJob>('/api/sync/run', { services, media, full }),
  // Index EmulationStation/RetroArch art living inside a device's ROM tree, in
  // place (no move) — so existing local covers show up. Local devices only.
  scanLocalArt: (deviceId: number) =>
    postJson<{ started: boolean; roots: string[] }>('/api/media/scan-local', { device_id: deviceId }),
  // ROM-repo sync: rescan Connections devices' ROM locations, then rebuild.
  romsStatus: () => get<{ locations: RomLocation[]; job: RomJob | null }>('/api/roms/status'),
  romsRun: (devices: number[] | 'all') =>
    postJson<RomJob>('/api/roms/run', { devices: devices === 'all' ? 'all' : devices }),
  setServices: (values: Record<string, string>) =>
    postJson<{ services: Service[] }>('/api/services', { values }),
  // Server operations (restart, DB health/repair)
  opsStatus: () => get<OpsStatus>('/api/ops/status'),
  opsRestart: () =>
    mutate<{ restarting: boolean }>('/api/ops/restart', 'POST'),
  dbCheck: (db = 'all') =>
    postJson<{ results: OpsDatabase[] }>('/api/ops/db-check', { db }),
  dbFix: (db: string, action: 'optimize' | 'recover') =>
    postJson<{ ok: boolean; reclaimed?: number; backup?: string }>('/api/ops/db-fix', { db, action }),
  // whole-fleet maintenance
  opsOptimize: () =>
    mutate<{ ok: boolean; optimized: number; reclaimed: number; errors: string[] }>('/api/ops/optimize', 'POST'),
  opsBackup: () =>
    mutate<{ ok: boolean; id: string; count: number; size: number }>('/api/ops/backup', 'POST'),
  opsBackups: () => get<{ backups: { id: string; count: number; size: number }[] }>('/api/ops/backups'),
  // two-way backing-store sync (durable stores <-> PocketBase/etc.)
  backingStatus: () => get<{ running: boolean; last: BackingResult | null; backend: string; configured: boolean }>('/api/backingstore/status'),
  backingRun: (dry = false) =>
    postJson<{ started: boolean; backend: string; running?: boolean }>('/api/backingstore/run', { dry_run: dry }),
  backingConfig: () => get<{ backend: string; values: Record<string, string>; secret_set: Record<string, boolean>; fields: Record<string, string[]>; auto_minutes: number }>('/api/backingstore/config'),
  backingConfigSet: (patch: { backend?: string; auto_minutes?: number; values?: Record<string, string> }) =>
    postJson<{ backend: string; values: Record<string, string>; secret_set: Record<string, boolean>; fields: Record<string, string[]>; auto_minutes: number }>('/api/backingstore/config', patch),
  backingTest: (backend?: string) =>
    postJson<{ ok: boolean; backend: string; detail?: string; error?: string }>('/api/backingstore/test', backend ? { backend } : {}),
  opsRestore: (id: string) =>
    postJson<{ ok: boolean; restored: number; safety_backup: string; restart_required: boolean }>('/api/ops/restore', { id }),
  // AI natural-language search (phase 3)
  aiSearch: (q: string) =>
    postJson<{ query: GamesQuery; explanation: string; result: GamesPage }>('/api/search', { q }),
  // ---- File-operations engine: profiles, plans, runbooks ----
  fileVariables: () => get<{ variables: FileVariable[] }>('/api/fileops/variables'),
  fileProfiles: () => get<{ profiles: FileProfile[] }>('/api/fileops/profiles'),
  saveFileProfile: (p: FileProfile) =>
    postJson<{ id: string; profiles: FileProfile[] }>('/api/fileops/profiles', p),
  deleteFileProfile: (pid: string) =>
    mutate<{ profiles: FileProfile[] }>('/api/fileops/profiles/' + encodeURIComponent(pid), 'DELETE'),
  fileDetect: (body: { device_id: number; root: string; scope: string; system?: string }, signal?: AbortSignal) =>
    postJson<FileDetect>('/api/fileops/detect', body, signal),
  filePlan: (body: { device_id: number; root: string; profile: string | FileProfile; scope: string; system?: string }, signal?: AbortSignal) =>
    postJson<FilePlan>('/api/fileops/plan', body, signal),
  mediaLayouts: () => get<{ layouts: { id: string; name: string; desc: string }[] }>('/api/fileops/media-layouts'),
  planExtract: (body: { device_id: number; root: string; dest?: string; scope: string; system?: string; layout?: string; op?: 'move' | 'copy' }, signal?: AbortSignal) =>
    postJson<FilePlan>('/api/fileops/plan-extract', body, signal),
  modelSource: (body: { device_id: number; root: string; scope: string; system?: string }) =>
    postJson<SourceModelResult>('/api/fileops/model-source', body),
  createRunbook: (body: { device_id: number; root: string; profile?: string | FileProfile; operation?: string; dest?: string; scope: string; system?: string; note?: string; layout?: string; op?: 'move' | 'copy' }) =>
    postJson<CreateRunbookResult>('/api/fileops/runbook', body),
  getRunbook: (id: number) => get<Runbook>('/api/fileops/runbook/' + id),
  executeRunbook: (id: number) =>
    mutate<{ started: boolean; run_id: number }>('/api/fileops/runbook/' + id + '/execute', 'POST'),
  undoRunbook: (id: number) =>
    mutate<{ started: boolean; run_id: number }>('/api/fileops/runbook/' + id + '/undo', 'POST'),
  // ---- Commander: build a reversible runbook from raw same-device drops ----
  createRunbookOps: (body: { device_id: number; root: string; ops: { op: string; src?: string; dst?: string }[]; label?: string; note?: string }) =>
    postJson<{ run_id: number; runbook: Runbook }>('/api/fileops/runbook-ops', body),
  // ---- Commander: cross-device transfer (backgrounded rsync job) ----
  fsTransfer: (body: { src_device: number; dst_device: number; src_dir: string; dst_dir: string; items: string[]; mode: 'copy' | 'move' }) =>
    postJson<{ started: boolean; jid: string }>('/api/fs/transfer', body),
  fsMkdir: (device_id: number, path: string) =>
    postJson<{ ok: boolean }>('/api/fs/mkdir', { device_id, path }),
  fsDelete: (device_id: number, paths: string[]) =>
    postJson<{ ok: boolean; removed: number }>('/api/fs/delete', { device_id, paths }),
  fsStat: (device_id: number, path: string) =>
    postJson<FsStat>('/api/fs/stat', { device_id, path }),
  troubleshootRunbook: (id: number) => get<Troubleshoot>('/api/fileops/runbook/' + id + '/troubleshoot'),
  fileHistory: () => get<{ runs: RunHistoryRow[] }>('/api/fileops/history'),
  manifestWrite: (body: { device_id: number; root: string; operation?: string; profile?: string; scope?: string; system?: string; dest?: string }) =>
    postJson<{ started: boolean; jid: string }>('/api/fileops/manifest', body),
  // ---- Unified job monitor (library sync + file-op runbooks) ----
  jobs: () => get<{ jobs: Job[] }>('/api/jobs'),
  pauseJob: (id: string) =>
    mutate<unknown>('/api/jobs/' + id + '/pause', 'POST'),
  restartJob: (id: string) =>
    mutate<unknown>('/api/jobs/' + id + '/restart', 'POST'),
  deleteJob: (id: string) =>
    mutate<unknown>('/api/jobs/' + id, 'DELETE'),
  clearJobs: () =>
    mutate<{ cleared: number }>('/api/jobs/clear', 'POST'),
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
  aimetaScan: (
    body: ({ target: string; limit?: number } | { norm_keys: string[]; label?: string }) & ScanOpts,
  ) =>
    postJson<{ run_id: number; target: string; count: number; web: boolean; match_provider: boolean }>('/api/aimeta/scan', body),
  aimetaRefine: (
    body: { norm_key: string; hint?: string; refs?: string[]; model?: string; web?: boolean; run_id?: number },
  ) =>
    postJson<{ kind: string | null; finding: AiFinding | null; used_web: boolean; used_refs: string[]; model: string; context: FindingContext | null }>('/api/aimeta/refine', body),
  // Full authoritative catalog re-derivation (background). Wand applies reconcile only
  // the touched games now, so this is the on-demand button for a global rebuild.
  rebuildCatalog: () =>
    mutate<{ started: boolean; running?: boolean }>('/api/catalog/rebuild', 'POST'),
  // Manually pin an entry's identity to a specific IGDB game (the human override for
  // odd-ball cases). `igdb` = an IGDB game link, slug, or numeric id. `platform` present
  // → per-entry pin (just that platform); absent → whole title.
  aimetaPin: (body: { norm_key: string; igdb?: string; platform?: string | null; detach?: boolean }) =>
    postJson<{ ok: boolean; norm_key: string; platform: string | null; detached: boolean; igdb_id: number | null; title: string | null; url: string | null }>('/api/aimeta/pin', body),
  aimetaFindingAction: (id: number, action: 'accept' | 'reject' | 'reset') =>
    mutate<{ findings: AiFinding[]; counts: AiFindingCounts }>('/api/aimeta/finding/' + id + '/' + action, 'POST'),
  aimetaAcceptAll: (minConfidence?: number) =>
    postJson<{ accepted: number; counts: AiFindingCounts }>('/api/aimeta/accept-all', { min_confidence: minConfidence || 0 }),
  aimetaAccept: (selections: AiApplySelection[]) =>
    postJson<{ accepted: number; pending: number }>('/api/aimeta/accept', { selections }),
  aimetaApply: (selections?: AiApplySelection[], media?: ScopeValue) => {
    const body: { selections?: AiApplySelection[]; media?: ScopeValue } = {}
    if (selections) body.selections = selections
    if (media !== undefined) body.media = media
    return postJson<{ started: boolean; selected: number | null; coalesced?: boolean }>('/api/aimeta/apply', body)
  },
  aimetaMediaDiff: (items: { norm_key: string; after_cover: string | null; igdb_id?: number | null; title?: string }[]) =>
    postJson<{ items: MediaDiff[]; sgdb: boolean }>('/api/aimeta/media-diff', { items }),
  setAttributeOverride: (nk: string, kind: string, value: string, origin: string) =>
    postJson<{ override: { value: string; origin: string } }>('/api/games/' + encodeURIComponent(nk) + '/attribute', { kind, value, origin }),
  clearAttributeOverride: (nk: string, kind: string) =>
    mutate<{ cleared: boolean }>('/api/games/' + encodeURIComponent(nk) + '/attribute/' + encodeURIComponent(kind), 'DELETE'),
  setIdentityDisabled: (nk: string, provider: string, disabled: boolean) =>
    postJson<{ disabled_identity: string[] }>('/api/games/' + encodeURIComponent(nk) + '/identity/' + encodeURIComponent(provider), { disabled }),
}

export type IngestEstimate = {
  tier: string; games: number; fresh: boolean; low: number; high: number
  summary: string
  phases: { phase: string; games: number; low: number; high: number; human: string }[]
  phase_labels: Record<string, string>
}
