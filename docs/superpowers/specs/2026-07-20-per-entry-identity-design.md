# Per-Entry Game Identity — Design (task #8)

Date: 2026-07-20
Status: approved (design)

## Problem

The wand matches a whole normalized title to **one** IGDB id and stamps it on every
platform entry in that title group. Its safety net (contamination detection) only catches
**backports** (older hardware than the game) and only re-checks **already-committed**
bindings — so:

- **Forward-platform different games sail through.** The PS3 "Tomb Raider" (2013 reboot) and
  Xbox "Tomb Raider: Definitive Edition" get stamped with the 1996 original's id (`igdb:1164`)
  and the wand overwrites their 2013 art. The NDS "Alice in Wonderland" (2010 Tim Burton movie
  game) gets stamped with the 1985 Windham Classics id.
- **Freshly-proposed matches aren't adjudicated** in the same wand run (contamination only
  reads committed `igdb:%` bindings), so a bad match applies before anything checks it.
- **Backports aren't reliably caught either** — an Atari-2600 "Star Fox" is grouped with the
  1993 SNES Star Fox ("also owned on Snes"); the check exists but didn't fire on it.
- **Loose title matching** (the ScreenScraper token-coverage path, `qc>=0.8`, no candidate-name
  floor) lets a MAME `journey` ROM match "The Sims 4: **Journey** to Batuu" (task #9).

Root cause: identity is **title-level**, but a normalized title can map to ONE game across
platforms (Doom) OR to SEVERAL different games (Tomb Raider 1996 vs 2013 vs the GameBoy game).

## Goal / End-state

Every `(entry, platform)` ends on its **own correct game identity**:
- Same game across platforms stays **unified** (Doom is `igdb:<Doom>` everywhere).
- A different same-title game is **re-identified to its own game when confident** (PS3 → the
  2013 reboot id with 2013 art; NDS Alice → the 2010 movie game), else **detached**
  (separated, unidentified) so nothing wrong is applied.
- **Never over-separate**: an entry is only split off when we're *confident it's a different
  game*; when uncertain, it stays with the group (protects legit ports — Alice's Apple2 +
  GameBoy stay the one 1985 game).

## Design

### 1. Per-entry resolution (always; deterministic-first)

For every identified title, resolve each `(entry, platform)` individually. Cost stays bounded
because it's **deterministic-first** — AI is spent only on genuinely ambiguous entries.

- One AI call proposes the game **name** for the title (unchanged from today).
- Build the **candidate set** = all exact-normalized-title IGDB games for that name (primary
  name or `alternative_names`; this is today's `igdb_enrich._title_matches`, but we keep ALL
  matches rather than collapsing to one). For a title like "Tomb Raider" this yields the 1996
  game AND the 2013 game AND the GB game, each with its `platforms` list and release year.
- Canonicalize each candidate's platforms via `platmap.canon`; note each candidate's year.
- **Per entry** on platform `pe`, compute `fits` = candidates where `platmap.canon(pe)` is in
  the candidate's platform set AND `not console_eras.impossible(pe, candidate.year)`:
  - `len(fits) == 1` → that candidate is the entry's id (the common, unambiguous outcome; for
    Doom every entry fits the single candidate → all `igdb:<Doom>`).
  - `len(fits) > 1` → **ambiguous → AI adjudicates** which release this entry is.
  - `len(fits) == 0` → see Gate A below.
- The **primary** = the candidate the most entries fit (ties → earliest year / most platforms).
  It defines "the group's game" for Gate A comparisons.

### 2. Two confidence gates

- **Gate A — "is it even a different game?"** An entry is only separated/re-identified when we
  are **confident it's different**:
  - `fits == 0` **and `console_eras.impossible(pe, primary.year)`** (the platform predates the
    game — 2600 for a 1993 game) → confidently different → Gate B.
  - `fits == 0` **but era-compatible** (the platform could plausibly host a port; IGDB just may
    not list it) → **not confident different → keep the primary** (assume legit port). This is
    the over-separation guard.
  - `fits > 1` with candidates from clearly different games → **AI adjudicates**; only acts on a
    confident verdict.
- **Gate B — "then what is it?"** Once confident an entry is different:
  - A specific correct candidate/id is known (deterministic fit or AI-named) → set that id.
  - Different but no confident correct id → **detach** (unidentified, `matched_by='detached'`).

Confidence threshold for AI verdicts reuses the current contamination bar (`>= 0.75`),
tunable (see task #3).

### 3. Apply per-entry

- Store per-entry ids via `entry_res.set_entry(nk, platform, igdb_id, 'ai_entry')` and detaches
  via `entry_res.set_detach(...)` (the durable per-`(norm_key,platform)` store build_library
  already honors ahead of the title-level resolution).
- `build_library._game_key` already prefers per-entry ids/detaches, so a touched entry's
  `game_key` becomes its own `igdb:<id>` (or `title:<nk>` when detached).
- **Media reconciles per-entry** so the PS3 entry keeps/fetches the 2013 game's art instead of
  the 1996 cover — via the existing scoped `_reconcile_media_now` / `media_fetch.fetch_igdb`
  per-entry-override path.

### 4. Respect manual decisions

Never override a user's manual pin/detach (`matched_by` `manual`/`detached` set by the user).
The resolution skips entries with a manual override, exactly as `_aimeta_apply` skips detached
entries today.

### 5. Runs inside the wand scan, on the fresh matches

Per-entry resolution runs **within `_aimeta_scan`**, operating on the matches *proposed in this
run* (before/at apply), not only on already-committed `igdb:%` bindings. This fixes the "the
safety net never sees the new match" gap — one wand run leaves every entry correct.

### 6. Subsumes contamination; absorbs #9

- The old `platmap.contamination_suspect` backport check becomes a special case of Gate A
  (`fits==0` + era-impossible → detach). The 2600 Star Fox falls out naturally (no 2600 Star
  Fox candidate → era-impossible → detach). The dedicated `_auto_fix_contamination` pass is
  folded into per-entry resolution (or kept as a thin wrapper).
- **Task #9 is largely absorbed:** a MAME `journey` entry resolving against "The Sims 4:
  Journey to Batuu" (Windows-only, 2020) has `fits==0` for MAME/arcade and is era-impossible →
  detach/reject. Additionally, tighten the loose ScreenScraper token-coverage gate
  (`_ss_match`, `qc>=0.8` with no candidate-name floor) so a short ROM name can't match a much
  longer title — add a candidate-name coverage floor and/or a length-ratio guard. (This
  matching-quality tightening is included here since it directly serves per-entry correctness.)

## Data model / storage

No schema change. Reuses:
- `entry_resolution` (via `entry_res.py`): per-`(norm_key, platform)` id override / detach.
- `games.game_key` / `base_key` (`build_library`): `_game_key` precedence already honors
  per-entry overrides and era-separation (`\x1f`).
- Media serve-gate keys on `game_key` (already per-entry).

## Integration points (files)

- `igdb_enrich.py` — `_pick_era_aware` / `_title_matches`: return the full exact-title candidate
  set (with platforms + year) instead of one pick; add a per-entry `fits` selector.
- `platmap.py` — reuse `canon`, `igdb_canons`, `GEN`; the per-entry fit test.
- `console_eras.py` — reuse `impossible(platform, year)` for the confident-different gate.
- `server/app.py` — `_aimeta_scan` (drive per-entry resolution over the scan's entries),
  `_provider_match` / `_ss_match` (candidate set + tighten SS gate), the apply path
  (`_apply_surgical_meta` / `_reconcile_media_now`) to write per-entry ids + media; fold
  `_auto_fix_contamination` into the new flow.
- `server/ai.py` — a per-entry adjudication contract (extends/reuses `detect_contamination`):
  given an entry (title, platform, filename, year) and the candidate games (name, year,
  platforms), return `{same_as_group: bool, correct_igdb_id: int|null, detach: bool,
  confidence: float, reason}`.
- `entry_res.py` — reuse `set_entry` / `set_detach`.

## AI adjudication contract

Batched (≤20 suspects/call, like `detect_contamination`). Per ambiguous entry, inputs: the
entry's title/platform/filename/parsed-year and the candidate games (name/year/platforms).
Output per entry: `{same_as_group, correct_igdb_id|null, detach, confidence, reason}`. Only
verdicts with `confidence >= threshold` act; below → leave with the group (Gate A default).

## Non-goals

- Re-scoring / re-ranking games (unrelated).
- The Spotlight collapse (task #7, done) — it consumes correct `game_key`s this produces.
- Perfecting compilation detection (task #12).
- A full rewrite of the matching stack — we extend the existing era-aware matcher, not replace
  it.

## Testing strategy

- **Unit (standalone, no server import):** a fixture of candidate sets + entries exercising
  the `fits` selector and the two gates:
  - Tomb Raider: PS1/Saturn/PC → 1996; PS3/Xbox → 2013; GB → GB game (or detach).
  - Alice: Apple2 + GameBoy → the one 1985 game (NOT separated — over-separation guard); NDS →
    2010 movie game.
  - Star Fox: Atari-2600 → detach (era-impossible, no 2600 candidate); SNES → SNES Star Fox.
  - Doom: every platform → the single Doom candidate (unambiguous, no AI).
  - Journey: MAME entry vs a Windows-2020 candidate → detach/reject.
- **SS gate:** short-query-inside-long-title (`journey` vs "…Journey to Batuu") no longer passes.
- **Live copy:** run the resolver over a copy of the live catalog's Tomb Raider / Alice / Star
  Fox groups; assert the expected per-entry ids/detaches; diff against a full rebuild.

## Risks

- **Cost at scale:** a wand "all" run does per-entry deterministic resolution for every entry;
  AI fires only on ambiguous ones, but a large ambiguous set (many multi-release franchises)
  still chunks AI calls. Mitigate: deterministic-first keeps AI to the true ambiguities; the
  user controls scan scope; batch ≤20.
- **IGDB platform-list completeness:** IGDB may omit platforms a game really shipped on →
  `fits==0` false positives. The era-compatible → keep-primary guard (Gate A) prevents
  over-separation in that case; only era-*impossible* entries are separated deterministically.
- **Over-separation** is the primary user concern — the two-gate rule defaults to "keep
  together unless confident different," and the AI threshold gates the rest.
- **Interaction with existing era-separation (`\x1f` base_key):** per-entry ids must compose
  with the current `_game_key`/base_key logic; verify a full rebuild reproduces the wand's
  per-entry result (byte-diff like `verify_catalog_patch.py`).
