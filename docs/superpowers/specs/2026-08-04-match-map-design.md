# Match Map — a self-maintaining title/identity index

Status: design, not built. 2026-08-04.

## Why

Identity is foundational (datbird, 2026-08-04): *"if that alone is done, all other
surrounding process can be performed at the users whims or as time/budget requires with
very little error."* Everything downstream — art, attributes, scores, a later on-demand
pull — is cheap and low-risk once a game is matched, and impossible while it is not.

Today the same identity is re-derived from scratch on every pass, at wildly uneven cost:

| step | cost per game |
|---|---|
| ScreenScraper name search (hit) | ~10s |
| ScreenScraper name search (miss) | ~2min — no `pc` system id, so the slow cross-system path |
| AI alias rescue | a model call, then one provider search per alias |

And it is **thrown away**. The 2026-08-04 rescue pass discovered that `007 First Light`
is ScreenScraper's `James Bond 007: First Light` (id 68679) and that `HELLCARD` is
`Book of Demons: HELLCARD` — knowledge no string metric can derive, paid for once and
then held only as a per-norm_key row that a reset erases.

The Match Map makes that knowledge durable, shared, and the FIRST thing consulted.

## What it is NOT

Not a new matching path. The lesson of the 2026-08-03 pipeline audit is that every
hand-copied subset of a rule drifts, so this adds **no call sites**. It slots inside the
one function that already decides a provider identity.

## 1. Where it plugs in

`provider_ids.resolve()` is already the single place a provider identity is decided, and
every onramp reaches it through `_match_providers` (guarded by
`test_match_is_not_ingest.py`). Its current shape:

```
cached decision?  ->  return it
                  ->  search(title, systems)
                  ->  record
```

becomes:

```
cached decision?  ->  return it
MATCH MAP hit?    ->  record + return          <- new, free, no network
                  ->  search(title, systems)
                  ->  search each MAP alias    <- replaces the ad-hoc AI alias retry
                  ->  AI rescue (last resort)
                  ->  record + LEARN BACK      <- new
```

Two consequences fall out for free:

* all six enrichment onramps and the import get it, because they already funnel here;
* `test_pipeline_unified.py`'s existing guard keeps it that way.

`_match_providers`'s bespoke `_search_with_aliases` closure is **deleted** — its job moves
into `resolve()`, where the map can serve aliases from any origin rather than only the AI.

## 2. Storage

`matchmap.sqlite` in `LUDODEX_DATA` — its own file, because it must be independently
resettable (§5) and independently syncable (§7).

```sql
CREATE TABLE alias(
  alias_norm     TEXT NOT NULL,      -- titlenorm.norm() of any observed name/filename
  canonical_norm TEXT NOT NULL,      -- titlenorm.norm() of the game's canonical title
  canonical      TEXT,               -- display form, for the UI
  origin         TEXT NOT NULL,      -- seed | learned | ai | user
  created        INTEGER,
  PRIMARY KEY(alias_norm, canonical_norm));

CREATE TABLE provider_id(
  canonical_norm TEXT NOT NULL,
  provider       TEXT NOT NULL,      -- igdb | screenscraper | steamgriddb | steam
  provider_id    TEXT NOT NULL,
  origin         TEXT NOT NULL,
  confirmed_at   INTEGER,            -- when a PROVIDER last returned it
  PRIMARY KEY(canonical_norm, provider));
```

`titlenorm.norm` is the existing normaliser (folds case, punctuation, articles, roman
numerals, `&`/`+`, editions) — reused, not reimplemented, so the map keys the same way
every other lookup in the project does.

### Precedence

`user` > `seed` > `learned` > `ai`. A hand-taught mapping is the user's ground truth and
outranks everything, exactly as `matched_by='manual'` already does in `igdb_resolution`.

## 3. Learn-back

The step that makes it compound. On a resolution that a PROVIDER confirmed:

* the queried title → the matched name goes in as `alias`;
* the returned id goes in as `provider_id` with `confirmed_at`.

**A `learned` row is never written from an unconfirmed AI answer.** The model proposes
names; only a provider returning an id creates a durable row. This is the same discipline
as "a failure is not a miss" (2026-08-03) — a non-answer must not be recorded as one.

AI-proposed aliases are stored with `origin='ai'` so a later pass can retry them without
re-paying, but they never masquerade as confirmed knowledge.

## 4. Provider ids are a CACHE, aliases are TRUTH

An alias is a durable fact about the world: `Rockman X4` IS `Mega Man X4`, and that will
be true forever. A provider id is that provider's current record and can be merged,
retired or renumbered.

So `provider_id` rows carry `confirmed_at` and are treated as a cache: a re-verify pass
may refresh them, and a 404 on use demotes the row rather than the alias. `alias` rows have
no expiry.

## 5. Reset — customisations only

Settings → **Database**, beside the existing snapshot/backup controls
(`ludodex-snapshot-backups`).

* **Reset custom matches** — `DELETE FROM alias WHERE origin='user'` and the same for
  `provider_id`. Seed, learned and AI rows survive.

Tooltip, written to be read by a person under stress:

> Clears the title matches you've added by hand. Your games, art and metadata aren't
> touched — only the custom name-to-game mappings you taught it. Built-in and learned
> matches stay.

A second, separate control — **Rebuild learned matches** — clears `learned` + `ai` and
lets them re-accumulate. Deliberately distinct: one throws away YOUR work, the other
throws away the app's, and a single button doing both is how people lose things.

## 6. Three distinct population paths

These are separate jobs and were conflated in the first draft of this spec. Naming them
apart matters, because only one of them runs on an existing install.

### 6a. Schema creation — every open

`matchmap.ensure_tables(con)`, called from `matchmap.con()` on every open, exactly as
`provider_ids.ensure_tables()` already works. No migration step, no install hook that can
be skipped: a deployment that has never seen the file gets a valid empty one the first
time anything touches it, including a fresh container with an empty `/data`.

### 6b. Shipped seed — build time, in the repo

`tools/matchmap_seed.py` GENERATES `data/matchmap-seed.json` and that artifact is
committed. It carries the regional-name families that recur across every library
(Rockman/Mega Man, Probotector/Contra, Nemesis/Gradius, Akumajou Dracula/Castlevania)
plus any provider ids stable enough to ship.

Imported with `origin='seed'` on first open of an empty map, so a brand-new install
starts with intelligence instead of earning it from zero. Idempotent: re-importing an
unchanged seed is a no-op.

### 6c. Adoption pass — ONE TIME, over the library you already have

**This is the "first default iteration" of the map, and it is the reason the feature is
worth building on day one rather than day thirty.** A working install already knows
thousands of confirmed identities; without this they stay locked in per-provider tables
and the map starts empty on a machine that had the answers all along.

`matchmap.adopt()` harvests, with **zero network calls**:

| from | becomes |
|---|---|
| `igdb_resolution` (igdb_id > 0) | `provider_id` igdb, `origin='learned'`, `confirmed_at` = its `resolved_at` |
| `ss_resolution` / `sgdb_resolution` (id > 0) | `provider_id` for that provider, same treatment |
| `metadata_links` | `provider_id` for steam and anything else linked |
| `games.canonical_title` vs the provider's stored `name` | an `alias` row whenever they differ — this is where `007 First Light` → `James Bond 007: First Light` becomes durable |
| `title_aliases` (the AI rescue cache) | `alias` rows with `origin='ai'` |
| `igdb_resolution.matched_by='manual'` and pinned entries | `origin='user'` — a hand-pin was always the user teaching it |

Properties it must have:

* **Idempotent** — safe to run repeatedly; re-running writes nothing new.
* **Resumable** — batched and interruptible like every other long job here.
* **Offline** — it reads what is already on disk. It must never call a provider, so it
  cannot fail partway for a network reason and cannot cost anything.
* **Runs automatically once**, on first open of an empty map on an install that has a
  catalog, and is re-runnable from Settings → Database as **Rebuild learned matches**
  (§5), which is the same operation.

Expected yield on the current library at time of writing: ~2,255 games × up to four
providers, plus every alias the 2026-08-04 rescue pass discovered — i.e. the map arrives
already knowing essentially everything this install has ever resolved.

### 6d. Ordering

`ensure_tables` → `seed import` (empty map only) → `adopt` (catalog present, once). All
three are no-ops on an install that has already done them, so the sequence is safe to
attempt on every boot.

## 7. Fitting the rest of the infra

| system | change |
|---|---|
| `backups.py` | add `matchmap.sqlite` to the snapshot set — user mappings are user data |
| `dbsync.py` | sync `origin='user'` rows only; seed/learned/ai are regenerable and would bloat the backing store |
| `check_invariants.py` | **I9** — no `alias` row points at a `canonical_norm` with no `provider_id` and no catalog entry (an orphan mapping teaches nothing) |
| `_apply_identity()` | on a manual pin, also write `origin='user'` rows — pinning a game IS teaching the map |
| `provider_ids.py` | hosts the lookup; `PROVIDERS` already enumerates the providers |
| `config.py` | `provider_allowed()` scope still gates whether a provider is consulted at all |

## 8. What this does NOT do

No fuzzy or string-distance matching. The map is exact lookups on normalised strings; the
judgement about WHICH game a name refers to stays with the provider or the model. Any
distance threshold loose enough to catch `crash bandicoot 3 warped` → `crash bandicoot
warped` is loose enough to recreate the `gods` → *God of War Ragnarök* bind this codebase
already fought.

## 9. Success criteria

1. A second identical ingest performs **zero** provider name-searches for anything the
   first one resolved.
0. After the adoption pass (§6c) on the CURRENT library, the map already contains every
   confirmed identity the install holds — verified by comparing its `provider_id` count
   against `igdb_resolution` + `ss_resolution` + `sgdb_resolution` + `metadata_links`.
2. Clearing `learned` and re-running reproduces the same identities.
3. A user mapping survives a library reset (it is `user` origin, synced, and backed up).
4. `check_invariants.py` I9 holds.

## Open question for datbird

Should a `user` mapping apply **globally** (this name is always that game) or **per
entry**? Global is more useful and matches the "intelligence" framing; per-entry is safer
and matches how `entry_resolution` already handles same-title splits. Recommendation:
global, since `entry_resolution` already exists for the per-entry case and would otherwise
be duplicated.
