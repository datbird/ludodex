# How ludodex works

Four stages, each independently re-runnable. Nothing here is a one-shot import: every
stage is incremental, so a re-run costs only what changed.

```
 storefronts ──┐
 ROM archives ─┼──▶ ingest ──▶ identity ──▶ enrich ──▶ media ──▶ library
 frontends ────┘
```

## 1. Ingest — what do I have?

Each ownership source produces a flat list of titles. They are *sources*, not
authorities: a source says "this exists here", nothing more.

| source | how |
|---|---|
| Steam | Web API `GetOwnedGames`. The key bypasses profile privacy **only for its owner's SteamID**, so no public profile, login or 2FA is needed |
| Epic | `legendary list --json` |
| GOG | Galaxy OAuth — one `--code`, then a cached refresh token |
| itch.io | server-side API (`/profile/owned-keys`) with a personal key |
| EA, PSN, Xbox | see [AUTH.md](AUTH.md) — each has its own quirks |
| ROM archives | recursive index parsing No-Intro / GoodTools tags for system, region and version |

### Local archives are a two-stage crawl

Any folder or drive — SD card, USB, NAS mount — can be registered as an archive. The
crawl is deliberately split so a rescan is cheap:

1. **`crawl.py`** — append-only inventory. Records raw file facts (path, name, ext, size,
   mtime) and adds **only new files**; an existing file just touches `last_seen`, a
   changed one is re-flagged.
2. **`process.py`** — reads unprocessed files and extracts system, cleaned title, dedupe
   key, region, languages, version, revision, disc number and dump flags, plus whether
   the file is a **variant of a game already in the catalog** and, when it is, the game
   key it is a variant OF (`base_norm_key`, left NULL otherwise). Both are per
   (game, platform), not per title: a Genesis file is not a variant of a SNES game.
   Marks each file processed,
   so the next run starts where this one stopped.

A removable drive that isn't plugged in is skipped, not forgotten — its already-indexed
games stay in the catalog.

## 2. Identity — what *is* it?

This is the hard part and the reason ludodex exists.

A filename is not an identity. `Rayman (USA) (Rev A).bin` has to become a specific game,
on a specific platform, in a specific year — and getting that wrong is worse than not
answering, because a wrong match propagates into art, metadata and eventually into what
gets copied onto a device.

**One acceptance gate, shared by every provider.** Each provider previously got this
wrong in its own way, so the rule now lives in exactly one place and measures both
directions against the title you own:

- **coverage** — every distinguishing word of the owned title must appear in the
  candidate. A candidate missing "The Reckoning" is a different product however well it
  matches the rest. This is what stops a parent game, a sequel or a collection standing
  in for the real thing.
- **era** — a year that *disagrees* is disqualifying, not merely unrewarded. Identical
  titles separated only by year are remakes, and a remake wearing its original's box art
  is a bug you notice for months.
- **hardware** — a name that matches on the wrong machine is a different product.
  Lives in the shared gate as `matchgate.hardware_ok` / `hardware_stated`. Unknown on
  either side refuses nothing (NULL is not a mismatch); a caller that must not accept
  without evidence pairs the two. TheGamesDB is the one path that cannot use it yet:
  nothing locally names its platform ids, so it refuses ambiguity instead. Mirroring
  `thegamesdb.platforms()` (one request, changes about never) would close that.

A miss returns *no answer*, never a plausible neighbour. That distinction is enforced
rather than assumed: a lookup that misses and gets read as consent is the single most
recurring defect shape in this codebase, and several tests exist purely to keep it dead.

**Per-platform entries.** The unit of identity is `(game, platform)`, not title. *Sonic
2* on Genesis and *Sonic 2* on Game Gear are different games that share a name.

## 3. Enrich — what do we know about it?

A **metadata provider** is consulted to fill gaps. It is never a source and adds no
ownership.

The merge is **fill-gaps only**: if a game already has values for a kind — from a store,
from Playnite, from your own edit — the provider leaves that kind alone. Owned-source
data always wins.

Providers: **IGDB** (genres, themes, modes, developers, publishers, series, release
dates, ratings, age ratings), **ScreenScraper** (emulation metadata and media),
**RetroAchievements**, **OpenCritic**-style scores. All cached, all TTL'd, all
re-runnable.

## 4. Media — what should it look like?

Art is indexed **by reference** first, then materialized on demand. The index is cheap
and complete; the repo on disk stays small.

Selection is a ranking, not a preference list. For each `(game, platform, kind)` it
weighs pinning, shape, resolution band, language, provider rank, AI judgement, and
demotes — never excludes — filler, template art and padded images. A game whose only
asset is bad still shows that asset; it just loses to anything better that arrives later.

Providers: ES-DE / RetroDECK sets, your Steam grid folder, Steam CDN, IGDB images,
ScreenScraper, SteamGridDB.

## 5. The offline match index (optional)

Everything above needs identity, and identity used to mean a network round trip per
question. The optional **match index** collapses that to one local table: any handle in
— a store id, a normalized title, a ROM hash — every other handle out, in about a
quarter of a millisecond.

It is built from local mirrors of IGDB's and ScreenScraper's catalogs, is entirely
rebuildable, and is not required: without it ludodex simply searches providers directly
and learns what it finds as it goes.

Three layers answer, in order: **your corrections**, then **what ludodex learned while
scraping**, then **the shipped index**. Your data is never overwritten by a supplement,
and which of the lower two answers first is a setting.

See [DATA-LICENSE.md](../DATA-LICENSE.md) for what the published index contains and the
terms it carries.

## Re-running

`scripts/update.sh` runs the whole chain: refresh stores → crawl and process archives →
rebuild → enrich → index and choose media → optional remote sync. Everything is
incremental, so a no-op refresh is fast.
