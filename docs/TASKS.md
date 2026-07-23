# ludodex — task queue

The working backlog. Numbers are stable task IDs referenced in commit messages
(`feat(match-confidence): … (#13)`). Per-task design docs live in
`docs/superpowers/specs/`; execution plans in `docs/superpowers/plans/`.

Last reviewed: 2026-07-23.

---

## Big build — next major feature

- **Tiered store ingest (Algo / Light AI / Heavy AI)** — **IMPLEMENTED 2026-07-23** (uncommitted
  at time of writing → see the commit that lands this). Design:
  `docs/superpowers/specs/2026-07-23-tiered-store-ingest-design.md`. All 11 net-new items built,
  compile-clean, deterministic units verified. Details in **Tiered ingest — shipped** below.
  Two vision passes (media de-dup, category placement) are built as callable AI capabilities but
  **deliberately not auto-fired over whole imports** (guardrail: vision-over-all-media is a large
  automatic spend). Rebuild-dependent — the new data model + attribute feeds populate on the next
  `build_library`.

## Top of queue

- ~~**Cover / hero loading spinner**~~ **DONE 2026-07-23.** `SpinImg` component (cache-race guarded,
  src-keyed) wired into the library grid covers and the detail hero. `.img-spin` reuses `sync-spin`.

## Open

Nothing is open pending code. One item is **data-gated**:

| # | Task | What's left |
|---|---|---|
| 3 | Auto-fix confidence tuning | The gate is now a setting (Settings → Library → "Automatic fixes"), defaulting to 75. Choosing a *different* value needs a real library to observe false positives against — and it may well never need changing. |

The one-way **"Publish catalog"** mirror was **retired** 2026-07-21: nothing ever read what
it published, and its name was persistently confused with the backing store that actually
protects your data (that confusion *was* task #16). Its PocketBase/Firestore primitives were
extracted to `remote_db.py`, which `dbsync.py` is built on.

#2 is **closed**: all four backing-store backends now pass a live two-way round-trip
(`test_dbsync_live.py`). The only path still unexercised is Google's OAuth token mint
(`sync.fb_token`) against a real Firebase project — the emulator ignores auth. That code is
shared with the long-standing one-way Firestore mirror.

## Recently completed

| # | Task | Commit |
|---|---|---|
| 2 | Firestore adapter protocol test + live round-trips vs Postgres / MySQL / Firestore emulator; fixed a real resource-name bug in Firestore writes | `21d7da3`, `HEAD` |
| 3 | `auto_fix_confidence` setting driving all three AI-gated auto-fixes | `93982dc` |
| 4 | Live cover preview before applying a smart art pick | `11b66c9` |
| 10 | Click-to-enlarge review thumbnails (`ImageLightbox`, ← / → / Esc) | `b6658bb` |
| 11 | Entrance animation on every overlay, not just game detail | `b6658bb` |
| 12 | Systematic compilation detection during a wand scan | `5a6313e` |
| 14 | Error boundaries + `hooksweep.mjs` build guard | `3f76969` |
| 15 | Files → Browse opens at a remembered/library path, not `/` | `b9b130f` |
| 16 | "Backup & restore" vs "Publish catalog" renaming | `b9b130f` |
| 5 | Amiga CD32 hardware-token strip / re-platform | `84d2ecf` |
| 6 | Wand provenance + release-type + mismatch warning in review | `9cfdcd1` |
| 8 | Per-entry identity resolution wired into the wand scan | `bc5cd8f` |
| 9 | Legacy fuzzy-match scrub (`igdb_enrich --scrub-fuzzy`) | `b34eb63` |
| 13 | Match confidence (score, facet, dashboard card, chips, settings) | `4ca9dda` … `9641575` |
| — | First-run fixes: catalog-seed 500, phantom Sources count, Settings white-screen | `6d757c4` `6019cbb` `17be9ce` |

Task IDs 1 and 7 are not recorded in any surviving note — treat those numbers as retired
rather than assuming there is missing work behind them.

## Rebuild-dependent

These shipped but only take effect on the next full catalog build (`build_library`, via the
Server-ops → Rebuild button or a scan). On a freshly-wiped install they stay at zero until
the first sync + build:

- Match-confidence rule-based baseline across all identified entries (#13)
- CD32 re-key (#5)
- Fuzzy-match scrub results (#9)

## Backing-store testing

- `remote_db.py` holds the shared PocketBase/Firestore plumbing (ex-`sync.py`).
- `test_dbsync_firestore.py` — offline, no creds, no container. Fake Firestore REST server.
- `test_dbsync_live.py <backend>` — real two-way round-trip (push, no-op re-sync, pull,
  delete both ways, convergence) against an isolated `LUDODEX_DATA`. Run it inside the
  container, where the psycopg/PyMySQL drivers live:
  `docker exec -e LUDODEX_DATA=/tmp/dbsync-live ludodex python3 /app/test_dbsync_live.py postgres`
- For Firestore without a Google project, run a Firestore emulator and set
  `FIRESTORE_EMULATOR_HOST=<host:port>` (also settable as config `firestore_emulator_host`).
  The adapter skips service-account token minting in that mode.

## Guards

- `web/scripts/hooksweep.mjs` runs inside `pnpm build`: a React hook after an early return
  (or in a conditional/loop/callback) fails the build. It exists because oxlint has no
  `react-hooks/rules-of-hooks` rule and this repo has no eslint, so nothing caught the
  white-screen bug of `17be9ce`. Run it alone with `pnpm lint:hooks`.
- Error boundaries wrap the app root and each settings panel, so a render error degrades to
  a recoverable inline message rather than a blank page.

## Parked / blocked

- **ScreenScraper** — code complete, blocked on a forum devid/devpassword. See `DESIGN.md` §11.
- **Nintendo source** — PKCE login verified and UI-wired; `fetch_owned()` is still a TODO.
- **Device layer v1** (push-only) — designed in `DESIGN.md`, not built.
- **Discover / want-vs-have capstone** — store sale-watching over the wanted list. Wishlist
  import (Steam, GOG) and the Wanted attribute exist; the universe browser and price watching
  do not.
- **Art importers for Epic / EA / GOG / itch** — provider art-type → kind maps are researched
  and in place, but no fetch module exists; those stores are ownership-only today.
- **Magic-animations research** — evaluate a reference site's scroll animations for possible UI
  use. Discussion first, nothing started.

## UI tweaks — all DONE 2026-07-23

- ~~**Match-confidence pill placement**~~ **DONE.** Removed the `◎ NN% match` pill from the
  detail **About** header; confidence still surfaces in "View / edit all attributes" (as the
  `match confidence` row) and now on the interactive metadata-provider badges (tiered ingest).

- ~~**Header stats line miscalculates**~~ **DONE.** `/api/stats`: `cross_source` was `n_kinds>1`
  (media-kind count, 0 library-wide) → now `n_sources>1` + wanted filter (**0 → 22**);
  `games_with_art` was `COUNT(DISTINCT norm_key) chosen=1` (swept in unidentified/wanted/non-cover)
  → now identified, non-wanted games with a chosen cover (**2067 → 1492**, a real subset).
  Reproduced + verified against the live DB.

- ~~**Library toolbar → single line**~~ **DONE.** Mode half-pill on the search field's right edge;
  Owned/Wanted/All moved into the Filters popover top; new **View** popover (Layout/Sort/Columns/
  Per-page); Filters+View left-justified, Select/Tools/Add game right (`.controls-right`).

## Tiered ingest — shipped (2026-07-23)

The 11 net-new items from the design spec, all compile-clean:

1. **Steam art fold** — `_put_steam_art`; `fetch_steam_media(art=True)` pulls art+screenshots+
   trailers in one pass (idempotent via ON CONFLICT).
2. **Store attrs at Algo** — `_extract_steam_attrs` caches Steam appdetails attrs to
   `steam-meta.sqlite`; `build_library` feeds them **Steam-first** (authoritative for Steam games).
3. **Per-provider confidence** — `matchconf.ss_match_confidence` → `match_confidence_ss`; exposed
   as `identity_confidence`. SGDB stays art-only (spec's open decision — it has no identity).
4. **Per-provider attribute retention** — new `provider_attrs` table retains every provider's
   value incl. merge losers; `attribute_alternates` in the game-detail response.
5. **Best-asset picker** — `art` AI prompt now scores ratio/orientation/resolution/
   official-first-else-coolest across all kinds (`pick_art` is already per-kind).
6-7. **Media de-dup / category placement** — `ai.same_image`, `ai.categorize_media` + AI areas
   `dedupe_media`/`categorize`. Callable capabilities; NOT auto-fired over whole imports (guardrail).
8-9. **Consensus / web scores** — `ai.consensus_attributes`, `ai.web_scores`; wired into a
   **Heavy-only, keys-scoped** post-scan job (`_heavy_ai_consensus`) writing `ai-consensus`
   overrides + web `ratings`, then a scoped `scores_fetch recompute`.
10. **Editable identity badges** — `identity-disable.sqlite` + `identity_disable.py` +
    `POST /api/games/{nk}/identity/{provider}` + read-time cascade in `game_detail` (drops a
    disabled provider's links/confidence/attrs, falls back to a retained alternate). Frontend:
    interactive metadata badges (confidence pill, disable ⊘, re-point ✎) split from the immutable
    store-ownership badges.
11. **Tier wiring** — store Heavy now enables open-web + score refresh + AI consensus, **scoped to
    the import's games** and gated by the existing `ai.check_limit` caps. Algo/Lite unchanged
    (Algo = deterministic only; Lite = provider-only, no paid consensus).

**Rebuild-dependent** (populate on the next `build_library`): `provider_attrs`, `match_confidence_ss`,
the Steam-attr feed. The Steam-attr cache fills on the next Steam-media pass. New sidecar DB
`identity-disable.sqlite` auto-creates.

## Open design decision

- **Selection policy** (`DESIGN.md` §9) — which games push to a device: allowlist / tag /
  platform / all-playable. Gates the device-layer push UX, not the server.

---

## Doc hygiene

`HANDOFF.md` predates the server build and still describes the AI-forward server as the one
open task. It needs a rewrite (or retirement in favour of `DESIGN.md` + this file) before it
misleads anyone picking the project up.
