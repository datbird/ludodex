# Pipeline test inventory

Status: inventory, 2026-08-06. The list of things this pipeline has been observed to get
wrong, and whether anything currently stops each one recurring.

Every entry is a defect that actually happened, not a hypothetical. `covered` means an
offline test asserts it today; `GAP` means nothing does. `MODEL` means the assertion is
about a model's judgement and cannot be made without spending tokens.

## A. Identity — is this the right game?

| # | must hold | status |
|---|---|---|
| A1 | Every configured provider is matched for every game, whether or not media is taken | covered — `test_match_is_not_ingest`, `test_provider_identity` |
| A2 | A provider can be turned off wholesale, per source, and per platform | covered — `test_provider_scope` |
| A3 | "We failed to look" is never recorded as "it isn't there" | covered — `test_search_failure_not_miss` |
| A4 | A recorded miss expires and is re-asked; it never becomes permanent | covered — `test_negative_cache` |
| A5 | A search variant may widen the SEARCH, never the ACCEPTANCE | covered — `test_ss_variant_acceptance` |
| A6 | Every distinguishing word must be present; edition/article/year noise is exempt | covered — `test_ss_variant_acceptance` |
| A7 | One provider id is one game — enforced at write, not merely checked | covered — `test_provider_id_unique` |
| A8 | `steam_appid` and `manual` are exempt from A7 (a DLC appid legitimately resolves to its parent) | covered — `test_provider_id_unique` |
| A9 | A refused match is recorded as an attempt, not as an absence | covered — `test_provider_id_unique` |
| A10 | Per-provider query construction rules (system ids, cross-system fallback, budgets) | covered — `test_provider_query_rules` |
| **A11** | **An AI-proposed alias names the SAME game, not a sibling** — "Ninja Gaiden Sigma 2" → "Ninja Gaiden 2" bound two games to SS 25266 | **GAP · MODEL** |
| **A12** | **An unconfirmed AI alias is never stored as confirmed knowledge** — only a provider returning an id creates durable truth | **GAP** |
| **A13** | **AI gray-zone match confidence scores the right band** (`matchconf` + `_score_confidence_ai`) | **GAP · MODEL** |

## B. Links — is the match visible and truthful?

| # | must hold | status |
|---|---|---|
| B1 | A catalog rebuild never throws away a provider match | covered — `test_links_survive_rebuild` |
| B2 | `sync` is authoritative: an identity that disappears takes its link with it | covered — `test_links_survive_rebuild` |
| B3 | IGDB links fill without overwriting build_library's judgement, and removal keys off the RESOLUTION not the game_key | covered — `test_links_survive_rebuild` |
| B4 | Every matched provider surfaces a link, not only those that stored a URL | covered — `test_provider_links` |

## C. Catalog and entries

| # | must hold | status |
|---|---|---|
| C1 | Collection members materialize as real entries | covered — `test_materialize_members` |
| C2 | Member titles collapse to the product's own label | covered — `test_member_title_collapse` |
| C3 | One product never creates a duplicate collection | covered — `test_collection_apply_guard` |
| C4 | Members created during a run JOIN that run rather than trailing it | covered — `test_members_join_run` |
| C5 | Identity arriving LATE still ends up visible | covered — `test_ingest_order` |
| **C6** | **Metadata derivation across `igdb_enrich → build_library → scores_fetch` unions** — never audited line by line | **GAP** |

## D. Media identity

| # | must hold | status |
|---|---|---|
| D1 | A media row's `game_key` follows the ENTRY's identity — one derivation, not two | covered — `test_game_key_stamp` |
| D2 | Neutral art serves only when `media.game_key = games.game_key` | covered — invariant I1 |
| D3 | A scoped re-select fixes one game without disturbing the rest | covered — `test_scoped_select` |
| D4 | A promotion after a dead asset obeys the SAME ordering as selection | covered — `test_repick_parity` |
| D5 | A dead reference drops out of contention wherever discovered | covered — `test_dead_ref_repick` |

## E. Media selection

| # | must hold | status |
|---|---|---|
| E1 | Shape rules reject wrong-orientation art per kind | covered — `test_shape_select` |
| E2 | The image wins, then the provider | covered — `test_res_band` |
| E3 | The blank guard covers every kind that occupies a slot | covered — `test_blank_media_guard` |
| E4 | ONE definition of "has a cover" | covered — `test_cover_rule` |
| E5 | ONE media pipeline; every onramp runs it | covered — `test_pipeline_unified` |
| E6 | The asset labelled USED is the asset actually served | covered — invariant I8 |

## F. Vision — the largest gap

Everything here is plumbing-tested and behaviour-untested. The plumbing tests prove the
verdict is parsed and applied; nothing proves the verdict is any good, and that is where
the two worst defects of 2026-08-05 lived.

| # | must hold | status |
|---|---|---|
| F1 | A reject verdict is parsed, and "none of these" is expressible | covered — `test_art_reject_wrong_game` |
| F2 | A reject only bans when it names a different game the gate confirms is different | covered — `test_art_reject_wrong_game` |
| F3 | A lone candidate is still judged (ranking needs two, verification does not) | covered — source guard |
| F4 | Vision judges per `(system, game_key)` silo, not the neutral bucket alone | covered — source guard |
| **F5** | **The model prefers the OWNED regional title** — Beyond Oasis over The Story of Thor, in any script | **GAP · MODEL** |
| **F6** | **The model rejects a sibling's art** — Police Quest II art offered for Police Quest I | **GAP · MODEL** |
| **F7** | **The model does NOT reject correct art** — the 624-cover false-positive run. The single most valuable live assertion | **GAP · MODEL** |
| **F8** | **The model prefers real art over Steam's blur-padded auto-portrait** | **GAP · MODEL** |
| **F9** | **A video contact sheet is judged as the right game, trailer vs gameplay distinguished** | **GAP · MODEL** (deterministic half covered by `test_media_video`, `test_video_evidence_prompt`) |
| **F10** | **`categorize` assigns the right kind to a loose image** | **GAP · MODEL** |
| **F11** | **`dedupe_media` calls near-duplicates the same and distinct art different** | **GAP · MODEL** |
| **F12** | **`adjudicate_attributes` / `consensus` picks the better provider per field** | **GAP · MODEL** |
| **F13** | **`identify` (add-by-image) reads box art into a correct title** | **GAP · MODEL** |
| **F14** | **`split` separates a merged entry correctly** | **GAP · MODEL** |
| **F15** | **`ingest` hints rewrite a filename into a real title** | **GAP · MODEL** |

## G. Metadata correctness

| # | must hold | status |
|---|---|---|
| G1 | Language never decides whether a RULE fires — genres match on Steam's id | covered — `test_genre_language` |
| G2 | The non-game filter can actually fire | covered — `test_non_game_hidden` |
| G3 | The fetch language comes from the one user setting, never a hardcoded constant | covered — `test_genre_language` |

## H. Spend — the #1 project rule, with no test at all

| # | must hold | status |
|---|---|---|
| **H1** | **No paid call fires without an explicit scope** — the guardrail itself | **GAP** |
| **H2** | **An already-judged game is not re-billed** (`art_adjudicated`, `ai_pick` durability) | **GAP** |
| **H3** | **Configured caps/limits actually stop a loop** (`ai.check_limit`) | **GAP** |
| **H4** | **Algo tier makes ZERO model calls** — by definition, and never verified | **GAP** |
| **H5** | **Lite judges covers only; Heavy judges every kind** — the tier contract | **GAP** |

## I. Infrastructure

| # | must hold | status |
|---|---|---|
| I1 | A test cannot run against a live data directory | covered — `test_isolation_guard` |
| I2 | dbsync round-trips, merges, and deletes correctly across backends | covered — 4 dbsync tests |
| I3 | The scheduler fires an autosync without manual action | covered — `test_autosync` |

## Summary

- 36 offline test files, ~450 assertions, covering **34 of 52** listed items.
- **18 gaps.** 13 of them require a model; 5 (A12, C6, H1–H4 minus the model ones) do not.
- The gaps are not spread evenly. They cluster in exactly two places: **what the model
  actually decides (F)** and **whether paid work is bounded (H)** — which are, in order,
  the thing that produces every visible defect and the thing the project's first rule is
  about.

## What a live suite has to look like

The constraint the user named — deterministic to RUN, model-backed where it matters — is
satisfiable, but not by asserting on model text. The suite must assert on **behaviour
that must hold regardless of wording**:

* a fixed golden corpus of candidate sets, committed as image bytes or stable URLs, one
  case per item F5–F15;
* each case asserts a property (`the chosen index is the US cover`, `no reject is
  returned`), never a string;
* one model call per case, cheapest configured model;
* run on demand, never per commit;
* the whole suite bounded and reported in tokens so the cost is visible before it runs.

Estimated: ~20 cases, one call each, ~$0.05–0.15 per full run at Flash pricing.

**Open questions for datbird, before this is built:**

1. **Flakiness policy.** A model is not deterministic. Does a case that fails 1 run in 20
   fail the suite, or does it need N-of-M consensus (which multiplies cost by N)?
2. **Corpus storage.** Golden images committed to the repo (stable, adds ~10–20 MB) or
   referenced by provider URL (small, but breaks when a provider re-hosts)?
3. **Scope of the first build.** All 13 model items, or start with F5–F8 (the art
   decisions that produced every defect you have actually seen)?
