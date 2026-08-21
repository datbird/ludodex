# Vestigial paths and re-derived rules — a stack audit

Status: findings, 2026-08-21. datbird:

> "This wouldnt be the first time that we've fixed an issue multiple times in the
> pipeline… can you try and identify in the whole stack/pipeline for these types of dead
> code paths, vistigial code pathes… especially ones that are actually causing
> performance issues in ingest."

Method: AST scans over `ludodex/` + `server/` (zero-reference functions, DB connections
opened inside loops, identical SQL across files), plus a live timing run in the container.
Everything below is measured or quoted from the code, not inferred.

## Headline

The codebase is in better shape than the question implies — 20 dead functions out of
~1,500, and no era/misattribution guard is unreachable. But there is **one structural
cause producing a cluster of re-derived rules**, and it is named in four different files
by their own comments.

## 1. `build_library.py` cannot be imported, so four modules copy it

`build_library.py` has **150 module-level statements, 106 of them calls** — including
`merges.alias_map()`, `splits.overrides()` and `ingesthints.overrides()` at import time,
then the entire build as top-level code. `import build_library` *runs a catalog rebuild*.

So every module that needs one of its rules copies the rule instead. Each says so:

| site | comment | copies |
|---|---|---|
| `media_fetch.py:115` | "Mirror build_library's bundle refusal (game_type 3=bundle/13=pack)" | `_igdb_bundle_ids()` |
| `catalog_patch.py:59` | "Mirror build_library._game_key for a non-era-separated entry" | `_game_key()` |
| `media_index.py:44` | "Mirrors build_library._emu_ep" | `_emu_ep()` |
| `ingest_ai.py:50` | "Mirrors build_library._rom_indexes so the…" | `_rom_indexes()` |

This is the project's own stated recurring bug — *derived truth computed in more than one
place and drifting silently* — institutionalised by a module boundary. The 2026-08-02
"identity congruence" incident was exactly this shape: three places derived `game_key`
independently and 41 entries held 990 invisible media rows.

**The counter-example proving the fix works** is already in the tree.
`check_invariants.py:226`:

> "call the REAL rule, do not restate it — this checker having its own copy…"

**Fix:** move the four helpers into an importable module (`build_library` keeps its
top-level script body; the *rules* move to a `librules.py` or into the modules that
already own the concept), and have `build_library` import them like everyone else. Four
copies become one, and the next change to the bundle rule reaches every reader.

## 2. Two selectors for one identity decision (found separately, 2026-08-21)

`_provider_match()` in `server/app.py` forked: emulation consoles →
`igdb_enrich._pick_era_aware()`, store/PC → a second, weaker inline matcher. The store
branch is what bound the owned 2013 Steam **Star Trek** to igdb:11485, the 1971 mainframe
record.

`_pick_era_aware` already carried the rule, and names this exact case in its docstring:

> `require_unique` … refuse when 2+ same-name games remain, because a generic store title
> is a coin-flip — IGDB ranks the 1973 mainframe "Star Trek" above the 2013 game you
> actually own.

The catalog sync calls it with `require_unique=bool(appid)` (`igdb_enrich.py:895`) and
correctly refused — which is why `igdb_resolution` holds `matched_by='none'`. Only the
wand/AI path had the weak copy. `_provider_match`'s own docstring claims that fork was
closed — *"instead of via a second, weaker matcher"* — but only for the consoles branch.

Commit `7249399` fixed the behaviour and deleted the bad ranking, but added
`matchgate.pick_by_year()` as a **third** selector rather than removing the fork.

**Fix:** `_provider_match` calls one selector for both branches, `require_unique=True`
when there is no console context; `_pick_era_aware` gains the year disambiguation and
calls `matchgate.pick_by_year()` for it, replacing its own inline
`ok.sort(key=lambda h: (_year_of(h) is None, _year_of(h) or 9999))`. Net: `sorted(...)[0]`
disappears from both sites, one selector remains.

## 3. Measured ingest cost: a DB connection per game

14 sites open a SQLite connection inside a loop. The one on the ingest path is
`_match_providers()` (the `provmatch` phase of `_sync_worker`), which opens **three per
game** — `ro()` at `app.py:7142`, `ro()` at `7177`, `sqlite3.connect()` at `7282` — inside
`for nk in keys`.

Timed in the container against the live library:

| | 2,255 games |
|---|---|
| connection per game | 3.32 s |
| one connection reused | 0.43 s |
| overhead | **2.89 s (8×)** |

Three sites, so **≈8.7 s wasted per sync today**, and **≈130 s** once the ROM library
brings the entry count to ~33,760. Not the dominant cost of an 8.9-hour ingest, but it is
free to remove: hoist the connection above the loop, as `game_context(norm_key, lib=None)`
already does with its optional `lib=` parameter.

Other connection-in-loop sites, unmeasured, ranked by likely exposure:

| site | loop | note |
|---|---|---|
| `app.py:4928` `resolve_per_entry_identity` | nest=2 | nested loop |
| `app.py:6062` `_ai_adjudicate_game` | nest=2 | nested loop |
| `aimeta.py:90` `_rom_file_context` | nest=1 | per game during an AI scan |
| `app.py:7952` `_entry_rom_paths` | nest=1 | |
| `ingest_ai.py:99` `targets` | nest=1 | |
| `merges.py:126` `rekey_user_data` | nest=1 | |
| `backups.py:189/190`, `app.py:9199/9224/9225` | nest=1 | ops paths, not per-game |

## 4. Dead functions — 20, and two are deliberate

Zero references anywhere in `ludodex/`, `server/`, `tests/` or `scripts/`. FastAPI routes
and dunders excluded.

```
aimeta.py:611           runs_want_scores        mobygames.py:291      recent
config.py:1065          commercial_ok           mobygames.py:477      extract_covers
devicesync.py:168       gamelist_upsert         playnite.py:79        source_for_guid
entry_res.py:50         clear_entry             publish_profiles.py:277  playlist_ext
fileops.py:1234         manifest_status         ra.py:84              get_console_ids
mediaflags.py:82        no_redist_set           splits.py:84          remove_key
merges.py:104           list_merges             splits.py:94          list_peels
ss_mirror.py:591        _next_utc_midnight      thegamesdb.py:372     by_hash
ai.py:639               _month_tokens_model     thegamesdb.py:383     by_unique_id
```

**Keep two.** `server/ai.py:2225 same_image` and `2245 categorize_media` are the two
vision passes built as callable AI capabilities and *deliberately* not auto-fired over
whole imports — the spend guardrail. They are unreferenced on purpose; the note belongs in
their docstrings so the next audit does not delete them.

`entry_res.clear_entry` and `splits.remove_key` are write-inverses of functions that ARE
used — a delete with no caller usually means a UI affordance that was never built (the
same shape as task #19, where `DELETE /api/collections/{key}` existed and nothing in
`web/src` ever called it). Worth checking before removing.

## 5. Duplicated SQL worth a second look

Identical statements in 2+ files. Most are harmless repeated lookups; these encode a
**rule**, so a change to the rule has to find every copy:

| statement | sites |
|---|---|
| `SELECT norm_key, igdb_id FROM igdb_resolution WHERE igdb_id>0` | `build_library.py:497`, `catalog_patch.py:30`, `media_fetch.py:113`, `media_fetch.py:684` |
| `SELECT norm_key, platform, igdb_id FROM entry_resolution WHERE igdb_id>0` | `build_library.py:650`, `catalog_patch.py:36`, `entry_res.py:59` |
| `SELECT g.norm_key, s.source_id FROM games g JOIN sources s … WHERE s.source='steam'` | `igdb_enrich.py:736`, `media_fetch.py:360`, `media_index.py:143`, `steam_tags.py:63` |
| `DELETE FROM metadata_links WHERE game_id=? AND provider='igdb'` | `build_library.py:1404`, `app.py:5320`, `app.py:5324`, `app.py:6588` |

The `igdb_id>0` predicate is the #25 negative-cache rule. `provider_ids.py:359` states its
full form — `if pid > 0 or matched_by == "manual"` — and the four `igdb_resolution`
readers carry only the first half. That is correct for them today (a manual "matches
nothing" pin has no id to return), but the rule is written out four times and the fuller
version lives elsewhere, which is how the halves drift apart.

## Suggested order

1. **Close the `_provider_match` fork** — smallest, and it removes a mechanism the last
   commit added. Test already exists.
2. **Hoist the connections in `_match_providers`** — measured, free, ~130 s per sync at
   full library size.
3. **Make `build_library`'s four rules importable** — the structural fix, and the one that
   stops this class recurring.
4. **Delete the 18 dead functions**, document the 2 deliberate ones.

Related: `2026-08-21-undated-identity-design.md`, `2026-08-06-uniform-providers.md`,
`2026-08-06-pipeline-test-inventory.md`.
