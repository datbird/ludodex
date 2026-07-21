# Spotlight Same-Game Collapse — Design (task #7)

Date: 2026-07-20
Status: approved (design)

## Problem

Spotlight rails (e.g. "Best of the 1990s") rank game *entries*, not games. A game
owned on N platforms appears N times — the 1990s rail showed **Doom five times**
(Jaguar, PlayStation, Sega 32X, SNES, PC), naming/platform variants duplicate
(Castlevania: SOTN ×2), and **compilations** (DOOM + DOOM II) compete as their own row
alongside their member games. One great game floods the row.

## Goal / Scope

Collapse each **spotlight rail** to one representative poster per game.

- **Spotlight rails only.** The main Library grid keeps one tile per `(game, platform)`
  — that is the intentional ownership model (you *do* own it on 5 platforms).
- **No identity data is changed.** This is a query + presentation fix, so it cannot
  corrupt the catalog. It collapses by whatever identity already exists.

## Design

### 1. Collapse key

`_spotlight_rows` (server/app.py:952-1019) currently does `GROUP BY g.base_key`. Change
the grouping to the **resolved identity**:

- group key = `g.game_key` when it `LIKE 'igdb:%'`, else `g.base_key`.

Effect:
- All entries sharing an IGDB id collapse to one row (the 5 Doom ports all resolve to
  `igdb:<Doom>`).
- Doom vs Doom II stay separate (different IGDB ids) — the "same-game, not franchise"
  rule.
- Unidentified `title:` entries stay separate (grouped by `base_key`), because two
  unknowns cannot be safely merged.

### 2. Representative + ranking

- **Rank** each collapsed group by its **max** member universal score, so the game lands
  in the correct top-N slot.
- **Representative row** (poster + title + entry link) = the member with a **cover
  first**, then **highest score** — never surface a placeholder tile when a real cover
  exists somewhere in the group (this also fixes the blank "Doom" tiles).
- Implement via a window function (`ROW_NUMBER() OVER (PARTITION BY <groupkey> ORDER BY
  has_cover DESC, score DESC)` pick rank 1) plus `MAX(score)` for the group's ranking,
  or an equivalent two-step selection in Python if the bundled SQLite lacks window
  functions.

### 3. Compilations

- Exclude rows that are **recorded compilations** (present in `collections.sqlite` via
  `compilations.is_collection` / `all_collections`) from generational/decade spotlights
  **by default**.
- Add an **"Include collections"** toggle in the spotlight gear menu; when on,
  compilations are included.
- Mechanism: fetch the compilation key-set (coll_keys = norm_key/base_key) in Python and
  exclude in the query (attach `collections.sqlite`, or pass an exclusion list).
- Caveat: only catches compilations ludodex has recorded (AI-detected or manual);
  broader compilation detection is a separate concern.

### 4. UX

- The collapsed tile stays a single clean poster. Clicking it opens the representative
  game's detail, which already lists "also owned on \<platforms\>".
- Add a small **"N platforms" badge** on a collapsed tile, shown only when N > 1. Trivial
  to remove if unwanted.

## Non-goals (deferred to task #8)

- Correcting **wrong** identities (the reboot/backport problem — Tomb Raider PS3, Alice
  NDS, Star Fox 2600). Collapse groups by whatever `game_key` exists; wrong identities
  remain wrong until #8 fixes identity.
- Any broader "versions tracker" concept beyond the spotlight display.
- Library-grid collapse.

## Files

- `server/app.py`: `_spotlight_rows` (grouping key, representative selection, ranking,
  compilation exclusion); a spotlight pref/flag for the "Include collections" toggle;
  the spotlight config endpoint.
- Frontend (`web/src/App.tsx`): `SpotlightSection` — the gear menu "Include collections"
  toggle, and the optional "N platforms" badge on the spotlight card.

## Testing

- A decade rail with 5 Doom ports (all `igdb:<Doom>`) collapses to **one** Doom tile that
  shows a real cover; Doom II remains a separate tile.
- A recorded compilation is excluded by default and reappears when the toggle is on.
- Unidentified same-title entries on different platforms are **not** wrongly merged.
- The representative prefers a member with a cover (no placeholder when a cover exists in
  the group).
- Ranking uses the group's best score (the game keeps its correct rank/position).

## Risks

- SQLite window-function support (3.25+) — verify the bundled version; fall back to a
  two-step Python selection if absent.
- The score join is per `norm_key`; a collapsed group spanning multiple `norm_key`s must
  aggregate scores (MAX) rather than pick one arbitrarily.
- Compilation exclusion depends on the collection being recorded; unrecorded compilations
  won't be excluded (acceptable for this cut; noted above).
