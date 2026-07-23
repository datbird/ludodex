# Tiered Store Ingest (Algo / Light AI / Heavy AI) — Design

Date: 2026-07-23
Status: approved (design) — ready to build in a fresh instance

## Summary

Store imports/syncs get three tiers, chosen per source (the picker already exists on the
Sync menu; today labelled Titles-only / Fill-the-blanks / Fill-everything — this spec is the
full behaviour behind those). **Steam** is the reference implementation because it alone
provides rich media, attributes and scores alongside ownership; the **same tier template
applies to every store** (GOG/Epic/EA/PSN/Xbox), just with thinner source data.

The hard rule governing all of it: **AI never runs against more than the user targeted.**
No new end-user confirmations — the tier choice + the sync click are the consent. See
[[ludodex-ai-spend-guardrail]]. Every AI loop must be scoped to exactly the games in the
run and bounded.

---

## The three tiers

### Tier 1 — 100% Algorithmic (zero AI)

For each imported game, deterministically (no model calls):

- **Pull all the store's own assets**, honouring the per-user screenshot/asset limit
  (`config screenshot_limit`, 0 = unlimited). For Steam that's the FULL set: cover, hero,
  logo, background (constructed CDN — `fetch_steam`/`STEAM_ART`) **and** screenshots +
  trailers (appdetails — `fetch_steam_media`). Today these are two separate paths and the
  constructed art only runs behind "Also sync media"; **Algo must run both as one Steam-media
  pass** (fold the constructed art into the appdetails pass so "Steam media" = the complete
  set, no separate toggle).
- **Pull all attributes the store exposes** (Steam appdetails: genres, developers,
  publishers, release date, description, categories, type).
- **Pull user scores** from every available source and compute the Ludodex score
  (`scores_fetch.py` → `scores.game_scores`). This runs on ALL tiers, Algo included.
- **Deterministic provider matches** (by store id, no AI): Steam appid → igdb, appid → SGDB.
  These need no model, so they belong in Algo; their attrs/art fill gaps under the precedence
  rules below. (AI-*assisted* matching — when the deterministic match misses — is Light.)

### Tier 2 — Light AI + Algo

Everything in Algo, plus:

- **Identify in igdb / ScreenScraper / SteamGridDB, using AI only as needed** (i.e. when the
  deterministic match failed or is ambiguous). NB **ScreenScraper barely covers Steam PC
  games** (it's a retro/console DB) — attempt it, but most Steam→SS matches legitimately miss;
  that is not a failure.
- **Per-provider match confidence** (0–100) for each of igdb/SS/SGDB, from title + release
  year + platform/system agreement. The source-provided title is 100% (it's the store's own
  data); confidence is about matching it *against* another provider. Gate on the single
  settings threshold (`match_confidence_threshold`, current default fine for now).
- **Identity badges** for igdb/SS/SGDB shown when that provider's confidence ≥ threshold.
- **Game-vs-utility cross-reference** — is this actually a game, or a utility that slipped
  classification (Wallpaper Engine, fpsVR…)? Mostly deterministic already via Steam's `type`
  field + `hide_non_games` + the `content_type` override; AI adjudicates only the gray cases.
- **Fill missing BASE attributes/media** from the three identified providers (definition
  below). AI "best asset picker" (see Precedence) chooses among candidates per base category.

### Tier 3 — Heavy AI + Algo

Everything in Light, plus:

- **If AI identity confidence ≥ threshold, download ALL media.** Cross-reference media across
  ALL identity sources, **AI-dedup** (drop near-duplicate images), and **AI-categorise** each
  asset into the right kind (cover/hero/logo/background/screenshot/…) when the source kind is
  ambiguous. Covers/screenshots materialise locally; **videos stay streamed** (trailers are
  tens of MB — do not download every one).
- **Full attribute + media consensus** — the Light cross-reference, but for EVERY attribute
  and media category, not just base. AI adjudicates the best value/asset per field across all
  providers.
- **Web-search gap-fill** — after consensus, for anything still missing (attrs, media, and
  **scores** when the normal sources found none or too few), run a web search to fill it. The
  web-grounded discovery path already exists (`server/ai.find_media_pages` /
  `find_media_urls`); web-searched *scores* are net-new.

---

## Base / critical set (the fill target for every game)

**Attributes:** canonical/normalized title · developer/publisher · release year · **genres**.
(Description is pulled but is NOT base.)

**Media:** cover · hero/background · ≥1 screenshot · ≥1 video · ≥1 manual.

"Base" = the set the pipeline always *attempts*. Missing-because-genuinely-unavailable is
fine (a modern store game with no manual). Manuals are listed deliberately even though most
modern titles won't have one — a re-release (OG Doom) may pick one up from SS, and that's
desired.

---

## Precedence & the AI asset picker

- **Steam games:** Steam's own art is authoritative. **Other stores:** igdb → SS.
- **Algo:** deterministic pick — store-first (Steam-first for Steam), then best ratio /
  resolution. No AI.
- **Light/Heavy:** an **AI "best asset per category"** judge picks among ALL candidates for
  each media category the tier imports, scoring on ratio, orientation (upright vs sideways),
  resolution, and **"official-first, else coolest"** (official store/igdb art wins; only reach
  for subjectively "cool" fan art when nothing official is decent). Extends the existing
  smart-art AI area (`ai` area `art`) from covers to every category.

---

## Provenance & editable identities (the largest new piece)

- **Every attribute value and media asset retains its source, and unused alternates are kept,
  not discarded.**
  - Media already does this: all candidates indexed in `media-index.sqlite`, one `chosen=1`
    per (game, kind). ✓
  - **Attributes do NOT yet** — build_library merges to a single value per attribute. This
    needs a per-provider attribute store (retain each provider's value; a chosen/override
    picks the winner) — a real data-model addition.
- **Identity-match badges are interactive** (igdb/SS/SGDB — the *metadata-provider* badges,
  NOT the store-ownership badges like Steam/GOG which are facts, not editable):
  - **Disable** a provider → its attrs/media drop out of use; the game falls back to the next
    source's retained values.
  - **Edit / re-associate** → paste a corrected provider URL/ID → re-pull that identity's
    attrs/media, replacing what that provider had contributed.
  - Cascades to every attr/media the game currently uses from that provider.
  - Builds on the existing manual-match UI (`ResolveModal`) and the override stores.

---

## Confidence model (de-igdb-centre it)

One threshold (`match_confidence_threshold`) classifies high vs low. Extend `matchconf.py`
so igdb, SS **and** SGDB each carry a 0–100 confidence from title + year + platform agreement.
Used for: Light identity badges; Heavy media-download gate.

---

## Scoring (all tiers)

`scores_fetch.py` pulls from every source (Steam appreviews, igdb, GOG, SS) and computes the
Ludodex score — on Algo, Light and Heavy. **Always scope with `--keys <scanned/imported
games>`** (added this session) so it never re-scores the whole catalogue. Heavy adds a
web-search fallback for games with no/too-few scores.

---

## What already exists to build on (do NOT reinvent)

- **Steam appdetails media** (`media_fetch.fetch_steam_media`, `--steam-media`, `--keys`,
  incremental via `steam_media_seen`) — screenshots + trailers. Trailers built from the movie
  id (`STEAM_MOVIE`) because appdetails now lists only DASH/HLS.
- **Steam constructed art** (`fetch_steam`/`STEAM_ART`: cover/hero/logo/background) — exists,
  gated behind "Also sync media"; needs folding into the Steam-media pass so Algo pulls it.
- **igdb enrich** (deterministic appid + name match, attrs, art) — `igdb_enrich.py`.
- **ScreenScraper** — `ss_scrape.py` (retro-oriented).
- **SteamGridDB** — art by appid/name (`media_fetch.fetch_steamgriddb*`).
- **Scores + Ludodex score** — `scores_fetch.py` (now `--keys`-scoped), `scoring.py`.
- **Match confidence** — `matchconf.py` (igdb-centric today; extend to SS/SGDB).
- **Wand / metadata scan** (identity, associations, supplements, media reconcile) —
  `server/app.py` `_aimeta_scan` / `_aimeta_apply`, `aimeta.py`. Scoped to the passed
  norm_keys. AI areas in `server/ai.py` (`art`, `identify`, `dedupe`, `split`, `metadata`,
  `ingest`, …).
- **Web-grounded discovery** — `server/ai.find_media_pages` / `find_media_urls` (Gemini
  grounded search).
- **Game-vs-utility** — Steam `type` in `scores.steam_type`, `hide_non_games`, `content_type`
  override.
- **Attribute overrides** — `overrides.py` (per-attr manual/provider re-point, retains origin).
- **Screenshot/asset limit** — `config screenshot_limit` (0 = unlimited), Steam settings UI.
- **Tier plumbing** — store tier persisted as `config import_mode_<sid>`
  (`import_mode_for`), device tier as `library_managers.import_mode`; sync worker "AI
  supplement" phase; `ingest_ai.py` (lite/heavy ROM path). Device Heavy supplement is scoped
  to `sources=["emulation"]`.

## Net-new work (the build)

1. **Fold Steam constructed art into the Steam-media pass** so Algo pulls the complete Steam
   set (art + screenshots + trailers) in one go, no "Also sync media" dependency.
2. **Extract store attributes at Algo** from appdetails (genres/devs/pubs/description/
   release/type) — today attrs come via igdb.
3. **Per-provider confidence** in `matchconf.py` for SS + SGDB.
4. **Per-provider attribute retention** (data model) + provenance on every value.
5. **Editable/disable identity badges** (UI + cascade) for igdb/SS/SGDB.
6. **AI best-asset picker** extended to all media categories (ratio/orientation/res/
   official-vs-cool).
7. **AI media de-dup** across providers (Heavy).
8. **AI category placement** for ambiguous media (Heavy).
9. **Full per-attribute/-media consensus** (Heavy).
10. **Web-searched scores** fallback (Heavy).
11. Wire all of the above behind the three tiers, per store, **scoped to the run's games**,
    honouring the guardrail (no runaway, no new confirmations).

## Open items to decide during build

- Exact Ludodex-score "too few sources" threshold that triggers Heavy's web-score fallback.
- Whether SGDB "identity" (art-only provider) warrants a badge, or just contributes art under
  igdb/SS identity.
- De-dup mechanism: perceptual hash first, AI only for gray pairs (cost control).

See [[ludodex-ai-spend-guardrail]], [[ludodex-wand-contract]], [[ludodex-detail-media-ux]],
[[ludodex-match-confidence]], [[ludodex-per-platform-entries]].
