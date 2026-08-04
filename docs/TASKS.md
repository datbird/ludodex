# ludodex — task queue

The working backlog. Numbers are stable task IDs referenced in commit messages
(`feat(match-confidence): … (#13)`). Per-task design docs live in
`docs/superpowers/specs/`; execution plans in `docs/superpowers/plans/`.

Last reviewed: 2026-07-23.

---

## Shipped 2026-07-25/26 — verification across all tiers

Spec: `docs/superpowers/specs/2026-07-24-match-verification-all-tiers-design.md`.
22 commits, `8d0fae4`..`1a38efe`.

**The governing invariant:** a record must be verified against the thing it describes. Both
defects were one failure — *confidence without inspection*: identity trusted a provider match
nothing had checked, media served an image nothing had looked at.

**The structural insight:** every tier's AI step selected for a **gap** (Light=`unmatched`,
Heavy=`missing`, Algo=no AI). A confidently-WRONG match has no gap — provider link present,
attributes full, inherited from the wrong record — so **no tier ever examined it**. Nothing
was mis-wired; the chain simply never received that class of game. `aimeta.review_targets()`
now feeds Algo's refusals into Light/Heavy.

| Area | What shipped |
|---|---|
| Identity | `_igdb_bundle_ids()` refuses a bundle's identity (IGDB `game_type`) before `_id_groups` can merge; `identity_review` table records why. **80 live.** Refused entries keep metadata. |
| Media shape | `KIND_ORIENT`/`shape_ok`/`derived_dims` ranked ahead of provider priority. Orientation derivable from URL; resolution measured-only (Steam serves `library_600x900.jpg` at 300x450). |
| Filler covers | `looks_padded()` detects Steam's blur-padded auto-`portrait.png` by a contiguous run of dead edge-energy bands. **146/150, 0 false positives.** Tri-state `media.filler`. |
| Collections | Candidates from the provider signal not the title; members materialized as real entries (`sources.via_collection`); one product = one collection; `catalog_patch.materialize_members()` keeps §13's no-rebuild property. |
| Review UI | Both sides of every change stated (`current_attrs` — the server had never sent old values); reserved attribute section; one content column; theme-tuned wash. |

**Bugs found by running it (4.5h Light import):**
- **All vision was dead** — `thinking_budget=0` 400s on `gemini-flash-latest`; `_vision_gemini`
  never got the hardening `_call_gemini` had. Killed art pick, add-by-image, de-dup,
  categorize and the manual "Pick best cover" button.
- `select()` ran before `materialize()`, which is what populates `filler`/dims → re-select added.
- A store sync fetched **only Steam art** → IGDB art now runs at every tier.
- Platform display strings stored raw (`'PC'` vs `'pc'`) → `norm_system`.
- De-dup suppressed the `also_owned_on` credit §13.3 promises.

**Not yet executed in a real run:** re-select-after-materialize, IGDB-art-at-every-tier, the
Light cover vision pass. A `curation`-scope reset + Light ingest is the validation
(`library` scope keeps `collections.sqlite`, so the collection engine never re-fires).

## Shipped 2026-07-26 — production-hardening review (4-agent audit of the range above)

A four-reviewer audit (media/vision · collections · identity/spend · server/UI) of
`bfd72f2..51f4a0c` found the deterministic core sound but flagged 2 spend-rule breaches,
5 more Criticals and ~15 Importants. All fixed:

**Vision/media**
- **AI art picks are now DURABLE** (`media.ai_pick`, ranked pin → shape/filler evidence →
  ai_pick → provider priority): `select()` re-ranks every sync and used to erase each paid
  pick, which the next sync re-bought — indefinitely. `_ai_art_pass` now also gates on the
  `art_adjudicated` marker (scope-aware: a Light 'cover' mark still lets Heavy judge the
  other kinds), so a resync re-pays nothing.
- `_ai_adjudicate_game` judges/clears only the **neutral system bucket, per game_key** —
  it used to wipe `chosen` across every console bucket of the norm_key without replacing
  them. Candidates are ranked (provider/resolution), not oldest-six.
- **Every materialize path measures**: `media_choose.stamp_measured` is the single
  write-back (batch, serve-time, vision thumbs). A sha1-only backfill permanently excluded
  the row from measurement — in `ondemand` mode that killed the filler detector outright.
  `filler` stays NULL when unmeasurable (was falsely stamped 0).
- "Download now" re-selects after materialize (the named invariant path that skipped it).
- Sync IGDB art uses the new **`--sync-art` incremental mode** — the old `--provider igdb`
  call DELETEd every igdb row (losing sha1/dims/filler verdicts) + refetched + ran the
  unscoped prune_dead HTTP sweep, every sync. Phase gate fixed to `media_enabled`.
- `igdbart` + `artpick` registered as sync phases (they silently no-op'd before — a paid
  pass with no visible receipt).

**Collections**
- **The provider-confirmed path can now actually complete**: `steam_meta.store_name` —
  captured for exactly this and read by *nothing* — plus the provider-confirmed flag are
  rendered into the `detect_collections` prompt ("the store lists this purchase as …"), so
  the model judges the PRODUCT, not the member-shaped entry title.
- **Negative verdicts persist** (`collection_rejected`, cleared by any later recording):
  un-recorded nominees used to re-bill on every scan, forever.
- Apply replays **'accepted' findings only** (durable store already holds 'applied') and
  never overwrites an `origin='manual'` collection — re-apply used to reset hand-curated
  member lists to the AI's stale version.
- `materialize_members` now runs from **every** recording path (wand auto-detect, manual
  endpoints, apply) and **reconciles both directions**: delete/shrink removes the phantom
  entries it created (restoring a satisfied want); §13.3 want-satisfaction attaches the
  via-ownership to wishlist-only members. Contract-tested: `test_materialize_members.py`.
- Patched==rebuilt parity restored: game_key via `_load_resolutions`/`_game_key`
  (bundle-refused), platform via the shared `member_platform_label` resolver
  (platmap-equivalence to EXISTING library labels — 'Game Boy' lands on `gameboy`,
  never a new `game boy` facet).
- Cross-run one-product-one-collection holds in BOTH arrival orders (a recorded sibling
  now blocks the canonical app's re-nomination); the canonical appid is preferred when a
  norm_key holds several.
- Materialized member rows carry their Collection label in game_detail again.

**Identity / spend**
- **Spec signal 3 implemented** (many-to-one: 2+ distinct same-store owned apps with
  different titles on one IGDB id ⇒ refuse + `identity_review 'many_to_one'`) — the
  deterministic backstop when `game_type` is missing/0. Emulation excluded on purpose
  (two regional-variant ROM dumps must keep merging).
- Bundle refusal now holds on EVERY identity route: entry-resolution overrides,
  `media_fetch.game_key`/`_backfill_game_key` (media stamped `igdb:<bundle>` could never
  match the refused entry's `title:` key at serve time — plus a repair pass for
  previously-stamped rows), and the rename-on-match pass no longer donates a bundle's
  title to a ROM entry.
- **`review_targets` has an exit**: once Light/Heavy examines a refused entry it's marked
  in the durable `review_decided` (keyed on the refusal detail, so a NEW refusal
  re-qualifies) — it used to re-bill an analyze call per refused entry per sync, forever.

**Review UI**
- Collection findings **render** (kind chip, card body, and a tickable "Collection
  membership" change row stating both sides) and the selection is **honored**
  (`selection.collection`) — accepting one attribute used to silently record membership
  and materialize entries the reviewer never saw.
- Unknown ≠ absent: when the server omits finding context (large lists) the UI says
  "current value unknown" / "current link unknown" instead of asserting "not set" /
  "not linked" — statements that were often false.
- Empty attribute section consults `payload.missing` ("N attributes still unknown (…)")
  instead of claiming everything is set.
- `--text-dim` defined per theme (light-mode "not set" was ~2:1 contrast);
  `chg-warn`/`chg-sources` join the shared content-column gutter.

**Known-remaining (deliberate):**
- Spec signal 1's full shape (canonical sub-app ⇒ auto-attach the parent product entry)
  is still nomination-dedupe only; signals 2+3 cover the merge-refusal half.
- Member titles at AI fullness can still near-duplicate a shorter owned title (only
  observed on Ys; the base_key skip mitigates exact matches). No fuzzy matching on
  purpose — a token-prefix rule would wrongly skip "Portal 2" against owned "Portal".
- `igdb_meta` payloads cached before `game_type` was requested have no bundle flag until
  their TTL refetch — a plain rebuild alone does NOT cure a pre-existing bundle merge.
- The collection's own entry keeps its member-shaped title ("Ys I"); the bundle's real
  name lives on the collection record. Renaming the entry to the store title is polish,
  not correctness.

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

| # | Task | What's left |
|---|---|---|
| 3 | Auto-fix confidence tuning | The gate is now a setting (Settings → Library → "Automatic fixes"), defaulting to 75. Choosing a *different* value needs a real library to observe false positives against — and it may well never need changing. |
| 23 | **Non-game filter has never fired** | `hide_non_games` defaults ON and its SQL is correct, but it reads `sco.steam_type` — which held **0 rows for a 2208-game library**, so the rule tested membership in an empty table and hid nothing, ever. Populated by `scores_fetch.py` alone, which has never produced a row here. Two layers: (a) the table is empty; (b) even full it cannot catch fpsVR or Wallpaper Engine, because Steam SELLS those as `type=game` — the code's own docstring names both as the case needing a manual override. **Fixed 2026-08-01 (genre signal):** a Steam GENRE in `NON_GAME_GENRES` (utilities, software, audio/video production, design & illustration, game development…) now hides an entry too — free, already on the entry, and the only signal that catches that class. A manual `content_type` override still outranks both, in both directions. 20 live entries carry such a genre (3dmark, displayfusion, deovr video player, evga precision x1, fpsvr…). **Still open:** populating `steam_type` at all, and whether Spotlight should be stricter than the library (a showcase of top games has no business surfacing a benchmark tool even if the library still lists it). |
| ~~20~~ | ~~**Members must join the RUN, not trail it**~~ **DONE 2026-08-02** | Members now get a deterministic ingest (`created_out` → `_ingest_new_members`, shipped `7558ea5`), but it is bolted on AFTER the pipeline as a separate phase, which is the root of #24: it re-implements the ordering and gets it wrong (pull → select, no materialize). **The correct shape is members injected into the run's WORKING KEY SET early**, before the media/art phases — then they ride fetch → materialize → measure → select → art-adjudicate in the pipeline's own order, with zero duplicated logic. Same "one path, not a second one" rule as #21's fetch primitive. This also makes tier inheritance automatic instead of a threaded parameter. **DECISION (datbird, 2026-08-02): members inherit the tier of the run that created them.** Choosing Light/Heavy for a run IS explicit intent for that tier, and a member of a collection being ingested is inside the target, not a cascade beyond it — so the spend rule is satisfied. A member created by a bare apply/manual record (no tier chosen) keeps the deterministic path only. **SHIPPED:** `_materialize_collection_members(created_out=, ingest=)` reports what it created and skips the standalone pass when a run will take the keys; `_aimeta_apply` resolves member identity then merges them into `touched`, which feeds `_enqueue_media_reconcile` → `_scoped_media_reconcile` — the same fetch, select and `ai_art_auto_pick` adjudication every other touched game gets. Tier inheritance falls out with no parameter threaded. Contract-tested: `test_members_join_run.py`. |
| ~~24~~ | ~~**Member ingest selects before measuring**~~ **FIXED 2026-08-02** | `_ingest_new_members` does pull → `select()`, skipping materialize — and materialize is what populates width/height/filler. So selection runs blind, dimensions get stamped later at serve time, and nothing re-selects. Live result: a **460x215 landscape ScreenScraper grid chosen as `cover`** for Halo MCC members, served over IGDB's portrait because own-console art beats neutral. Violates the materialize→select invariant the 2026-07-26 audit established for exactly this reason. Repaired in place 2026-08-02 by re-running `select()` (11,387 buckets, 1.9s, measured-wrong-shape chosen 36 → 20) but WILL RECUR on the next collection recorded. Fixed by adding select -> MEASURE -> re-select to `_ingest_new_members`, scoped to the members the run created (`media_choose.materialize()` has no per-game filter and would sweep the catalog, so `_asset_local_path` — the non-destructive serve-time helper — does the measuring per row). #20's architecture would still remove the duplication entirely. |
| ~~25~~ | ~~**igdb_resolution rows with igdb_id 0/NULL**~~ **RESOLVED 2026-08-02** | Not corrupt data — a deliberate NEGATIVE CACHE. A row of `igdb_id=0, matched_by='none'` records that a pass searched and found nothing, so the next run doesn't pay for the same miss. Live count is 40, not 33. **The bug was the READ:** `_member_identity` asked `SELECT 1 FROM igdb_resolution WHERE norm_key=?` and treated ANY row as "already identified — never re-decide", which made every miss PERMANENT. Those 40 are not all non-games: `crash bandicoot 3 warped`, `ys i ancient ys vanished` and three Space Quest chapters were locked out of ever being identified. Now: a real id (>0) is a decision to respect, `matched_by='manual'` is a decision to respect (including a deliberate "this matches nothing"), and a falsy automatic row is the ABSENCE of a decision — so a later, better-informed pass may try again. Every other reader already filtered correctly (`igdb_id>0` or `if not iid`). Contract-tested: `test_negative_cache.py` (7).
| ~~26~~ | ~~**Web-sourced media must rank below curated providers**~~ **ALREADY TRUE** | datbird's ranking policy (2026-08-02): tier 1 = deterministic image fitness (ratio, resolution, filler), tier 2 = the curated providers ludodex supports (IGDB/SS/SGDB/Steam), tier 3 = web-search-obtained media. Tiers 1 and 2 are ALREADY the design — `select()` ranks shape and filler above provider, `shape_ok` never penalises unknown dimensions, and `PRIORITY` already puts screenscraper above igdb for covers. **Tier 3 is the gap**: Wikimedia/`page_images`/`_complete_text_web` art sits in the same provider list with no demotion. **Verified 2026-08-02: no change needed.** Web art is tagged `provider='web'` and no `PRIORITY` list contains it, so `rank.get(provider, 99)` already places it below every curated provider. All three tiers of the policy are in place. |
| ~~21~~ | ~~**Every provider is a provider**~~ **DONE 2026-08-03** | All three parts shipped. **(a)** SS eligibility follows `games.platform`. **(b)+(c)** `provider_ids.py` is the identity cache and `_match_providers()` records SS + SGDB identity for every game from EVERY onramp including the import (`provmatch` phase in `_sync_worker`) — a match is not an ingest. Backfilled: SS 1610/2255, SGDB 2252/2255, all 2255 decided. Three matcher defects found and fixed along the way (failure-recorded-as-miss on all three providers; PC games searched on raw title only because `systeme_id('pc')` is None; trademark + edition-suffix normalisation). Guards: `test_match_is_not_ingest.py`, `test_provider_query_rules.py`, `check_invariants.py` I7.
| ~~22~~ | ~~**Deterministic "Fetch from <provider>"**~~ **DONE 2026-08-03** | `GET /api/media/matched-providers/{nk}` + `POST /api/media/fetch/{nk}`, and the `MediaFetchMenu` control beside the wand in BOTH placements (All Media + each category). Another caller of the shared pipeline narrowed by `provider=`/`kinds=`, not a new fetch path. Free by definition — no AI area consulted. Unmatched providers are shown DISABLED rather than hidden.
| ~~18~~ | ~~**Live library repair — duplicate Ys collection**~~ **DONE 2026-08-01** | The review queue was accepted 2026-08-01 shortly BEFORE the apply-path guard (`97c8faa`) deployed, so the duplicate it prevents got in: `ys i` and `ys 2` are both recorded as "Ys I & II Chronicles+" with identical members, and the two phantom member entries carry a via-row from each — double-credited. Also still live: `ms-dos` ×11, `microsoft windows` ×1, `pc-8801` ×2, because the platform re-key rides `materialize_members`, which fires on record/apply/delete and hasn't been triggered since the deploy. **The whole repair is ONE call** — `DELETE /api/collections/ys 2` runs `clear_collection` then `_materialize_collection_members()`, which drops the duplicate's via-rows, collapses both Ys members onto the apps actually owned (removing both phantoms and the `pc-8801` facet), and re-keys the rest to `pc`. Expected: facets 16 → 13, Ys 8 entries → 6. Verified on a catalog copy and idempotent on re-run. Delete `ys 2`, NOT `ys i` — `ys i` holds the canonical appid 223810. **RESOLVED** — repaired 2026-08-01 by calling the endpoint's own two functions in-container (`clear_collection` + `materialize_members`): collections 30→29, facets 16→12, junk labels none, Ys 8→6 entries, re-run idempotent. Backup at `<appdata>/ludodex-repair-backup-20260801/`. |
| 19 | **No UI to delete a collection** | `DELETE /api/collections/{coll_key}` exists and `api.ts` exports `deleteCollection`, but **nothing in `web/src` ever calls it** — there is no collection-management surface at all. A recorded collection can be created by the wand and never removed by a user. Real missing functionality, independent of #18, and the smallest route to unblocking it. |
| 17 | **Agent/API auth** | The API surface is complete (192 endpoints — scan, apply, rebuild, collections are all there), but every `/api/*` route is behind the session middleware, so an agent can't drive the app it maintains without a credential. That gate is correct — the instance is internet-exposed — so the fix is a way IN, not a way around: a dedicated non-admin account, or a scoped API token honoured alongside the session cookie. Until then, agent-side verification has to go through `docker exec`, which bypasses the very surface it should be testing. NB an agent with host root can mint its own session in `auth.sqlite`; that it *shouldn't* is a matter of conduct, not enforcement, which is itself an argument for issuing a real credential. |

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

## Identity congruence — the last of the "two derivations" bugs (2026-08-02)

datbird: *"why didnt we pull in any other categories of media here? Very popular game so
more media definitly exists"* — a title showing **Screenshots 0 / Videos 0 / Manuals 0**
while holding 15-40 screenshots.

Neutral art only serves when `media.game_key = games.game_key` (DESIGN §11.9), and THREE
places derived that key:

1. the catalog, stamping the entry;
2. `media_fetch.game_key()` at fetch time, from `igdb_resolution`;
3. `_backfill_game_key`'s repair, also from `igdb_resolution`, applying its own policy —
   refusing anything IGDB calls a bundle (`game_type` 3/13).

The catalog was free to give an entry exactly such a bundle identity, so (1) and (3)
disagreed permanently. Live: **41 entries carrying `igdb:<bundle>` with all 990 of their
neutral media rows still on `title:<base_key>`** — Halo MCC, Crash N. Sane Trilogy, the
Contra/Castlevania Anniversary Collections, the D&D and Forgotten Realms series, DOOM 3
BFG, DMC HD. Each still rendered a cover, which is why it never looked like an identity
fault: own-console ScreenScraper art matches on `norm_key+system` and never consults
`game_key` at all.

**Fix:** the repair reads the CATALOG and follows it. (2) stays as a fetch-time first
guess — the entry may not exist yet mid-ingest — but it is now guaranteed to be
reconciled before any selection. The bundle refusal needs no special case: an entry that
refused one reads back `title:<nk>`. Entries sharing a base_key that DISAGREE are skipped
rather than guessed at.

**Found by the ordering guard, not by inspection:** `_ingest_new_members` fetched via
`_pull_media_sources` and selected with NO repair, so art it fetched was invisible on
landing. The "download media into the repo" job took `media_choose.con_index()`, which
(unlike `media_fetch`'s) never carried the repair either.

### `check_invariants.py` — assert the finished data, not the units

Every wrong-art report in this project traced to derived truth computed twice and
drifting, and the symptom only ever showed up in the END STATE. So that is where it is
now checked. **Read-only, safe against a live instance:**

    docker exec -i ludodex python3 /app/check_invariants.py

- **I1** neutral media identity matches its entry
- **I2** no falsy identity (`igdb:0`) is used as a key
- **I3** no chosen asset has a known-wrong shape
- **I4** every viable candidate set elects a winner
- **I5** exactly one chosen asset per (game, system, identity, kind)
- **I6** media an entry *holds* is media an entry can *show* (eligible art only —
  another console's art is siloed away on purpose, §11.4, and counting it would report
  the design working)

Run it after any ingest, wand run or repair. Live 2026-08-02 post-fix: **all six hold**
(I1 41 → 0, I6 70 → 0).

`test_ingest_order.py` pins the fresh-ingest guarantee itself: art stays visible through
identity arriving late, moving again, and being revoked — and the build fails if any
fetch path reaches `select()` without reconciling identity first.

## Incident 2026-08-02 — the test suite erased the live media index

Running `test_*.py` inside the production container wiped all 66,280 rows of
`media-index.sqlite`. `test_shape_select.py` set its data dir with
`os.environ.setdefault("LUDODEX_DATA", tempfile.mkdtemp())`; setdefault KEEPS an
inherited value, the container already exports `LUDODEX_DATA=/data`, so the temp dir was
never used and the fixture helper's `DELETE FROM media` ran against production.

Restored from the `2026-08-02_172430` snapshot (the media BLOBS under `/data/media` were
never touched — only the index), then re-materialized and re-selected to recover the
work done after that snapshot. The wiped file is kept at
`/data/media-index.sqlite.WIPED-20260802` pending confidence in the restore.

Collateral, both repaired: `test_autosync` reset `backingstore_auto_minutes` to a
hardcoded `0` rather than the prior value, silently disabling scheduled sync (it was 5).

Guards added so it cannot recur:

- `test_support.isolate()` ASSIGNS a temp dir; `assert_isolated()` exits loudly if the
  resolved dir is live (`/data`, `<appdata>/ludodex`) or unset.
- `test_dbsync_live.py`'s hand-rolled version of that check now calls the shared one.
- The three tests that mutate the running instance BY DESIGN (`test_autosync`,
  `test_dbsync_roundtrip`, `test_dbsync_live`) require `LUDODEX_LIVE_TESTS=1`.
- `run_tests.sh` gives every test its own scratch dir instead of trusting it.
- `test_isolation_guard.py` pins all of the above, including a sweep that fails if any
  test file reintroduces `setdefault` on `LUDODEX_DATA`.

**Run the suite with `docker exec -i ludodex bash /app/run_tests.sh`** — never by looping
over `test_*.py` by hand.

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

## Pipeline unification — one chain, every onramp (2026-08-03)

datbird: *"these should be unified functions where we utilize modules and functions
consistently through the entire product… no matter what onramp/offramp we're taking to
enrich, correct metadata/media the pipelines remain consistent, which means any fixes,
improvements and changes made easily apply to all onramps/offramps."*

An audit of the entry points found exactly what that predicted. **Only one of them ran
the full chain:**

| onramp | what it actually ran |
|---|---|
| `_sync_worker` (import) | match only |
| `_wand_fill_media` (the wand!) | fetch → stamp → select |
| `_scoped_media_reconcile` | match → fetch → stamp → select → measure → prune → ai |
| `_ingest_new_members` | fetch → measure → stamp → select |
| `_reconcile_media_now` | fetch(IGDB only) → stamp → select |
| `_fetch_media_for` | fetch → stamp → select |
| `media_fetch_provider` | fetch → stamp → select |

The wand's own media step **never measured and never pruned** — it chose art it had never
looked at and could leave a blank placeholder as the pick. Member ingest never pruned.
`_reconcile_media_now` fetched IGDB only, so the "immediate" result an apply showed could
be replaced later by the background pass — which reads as the app changing its mind.

### The one chain

- **`_enrich_media(keys, …)`** = match → fetch → `_media_finish`
- **`_media_finish(keys, …)`** = stamp → select → measure → prune → **re-select**

Each step depends on the one before it, and the order is the product of every bug this
session: stamp before select (neutral art only serves when identity agrees, §11.9);
measure before the final pick (selecting first is selecting blind); prune after measure
(a blank can only be detected once bytes are in hand); **re-select after prune** (the step
whose absence produced every "wrong cover displayed" report).

Scoped throughout — `select(only=keys)` and a per-row measure — so running it for one
game costs one game.

### Deliberate exemptions

- `_sync_worker` fetches through `media_fetch.py` **subprocesses** (streamed progress over
  a whole library) and then runs `_media_finish`. The batching differs; the chain does not.
- `_media_worker` is bulk repo hydration — no fetch, no enrichment.
- `media_asset` repairs a single dead reference at serve time.

### Guard

`test_pipeline_unified.py` (26) is source-level on purpose: the failure mode is *a code
path that never learned about a function*, which no unit test of that function can catch —
it works perfectly, nobody called it. It fails the build if an onramp re-implements a
step, if the tail's order changes, or if a new caller of stamp/prune appears outside the
declared exemptions.

### Metadata: one identity-consequence chain (same day)

The media audit's twin. An identity is not one fact, it is **four that must move
together**:

1. `games.game_key` → `igdb:<id>` — neutral art only serves when `media.game_key` agrees
   (DESIGN §11.9), so leaving it behind makes the art a run just fetched invisible;
2. the IGDB `metadata_links` row — what the Matched-providers menu reads;
3. the canonical title, for ROM/archive-only entries;
4. the provider-record **attributes** — genres, themes, developer, publisher, release.

`_pin_live` did all four. `_member_identity` did the first two and stopped, so **a game
identified as a collection member got no genres, no developer and no publisher**, while
the same game pinned by hand got all of them — two different meanings of "identified"
depending on which door you came through.

`_pin_live` is now **`_apply_identity`**, the single chain, called by `_member_identity`,
`aimeta_pin` and `resolve_per_entry_identity`. `test_pipeline_unified.py` (39) fails the
build if an onramp hand-writes `game_key` or a `metadata_links` row instead of calling it.
