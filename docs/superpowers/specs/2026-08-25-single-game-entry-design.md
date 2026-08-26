# Single Game Entry — Design

Date: 2026-08-25
Status: approved (design)

## Problem

The library shows one card per `(game, platform)`. A game you own on four systems is four
tiles. `Dark Souls` on PC and `Dark Souls` on Switch are the same game, they resolve to the
same IGDB id, and they still occupy two cards.

The per-platform unit arrived on 2026-07-15 (DESIGN §11) to fix platform-blind art: a
TurboGrafx game showing a Game Boy cover. That fix was correct and its machinery must
survive. The card count was the side effect, and the card count is the defect.

Platform splitting is also only half of what a user reads as duplication. Six rows carry a
Dark Souls title in the live catalog today:

| entry | platform | game_key |
|---|---|---|
| dark souls | pc | igdb:81085 |
| dark souls | switch | igdb:81085 |
| dark souls prepare to die | pc | title:dark souls prepare to die |
| dark souls 2 | pc | igdb:2368 |
| dark souls 2 scholar of the first sin | pc | igdb:8222 |
| dark souls 3 | pc | igdb:11133 |

Only the first two are a platform split. The rest are editions filed as separate titles.
Collapsing platforms alone removes one row out of six.

## Goal

One card per game. Platforms and editions are properties of that card, not separate cards.

A game is the same game across ports, editions and remasters. A remake is a different game.

Two safety properties are non-negotiable:

1. Genuinely different games that share a name stay apart. The 1986 Amiga *Portal* never
   rejoins Valve's 2007 *Portal*. The 1994 Game Boy *Uno* never rejoins the Steam one.
2. A card never displays another console's art.

## Measured impact

Against the live catalog on 2026-08-25, before any change:

| | count |
|---|---:|
| entries (tiles today) | 2,488 |
| distinct `game_key` (tiles after the platform collapse) | 2,426 |
| distinct card key after the edition fold | 2,388 |
| owned IGDB ids that the fold rule relabels | 252 |

Read that honestly. This library is mostly PC storefront titles, so the visible drop is
about 4%. The platform collapse merges 62 entries and the edition fold merges 38 more. The
fold relabels 252 entries' grouping identity, but most of them fold onto a root nothing else
in the library owns, so the tile count barely moves.

The change matters far more on an emulation-heavy catalog. The pre-reset catalog held 34,914
entries over 30,898 base games, and there the per-platform split was the dominant duplicate.

## The unit

The library groups by a new **`card_key`** on each entry.

- Default: `card_key = game_key`. That key exists on every row already, as `igdb:<id>` when
  identified and `title:<norm_key>` when not. No new identity work for the common case.
- The edition fold may rewrite `card_key` to the fold root's `igdb:<id>`.

`game_key` is not touched. It stays the media identity binding it has been since DESIGN
§11.9, and every media serve gate keeps reading it. `card_key` is a display and grouping key
layered on top.

Two properties fall out of using `game_key` as the default:

- Era-separated siblings share `title:<norm_key>` and become one card. The Apple II and Game
  Boy *Alice in Wonderland* are one card, which is correct, and the NDS 2010 *Alice* holds
  its own `igdb:` key and stays a second card, which is also correct.
- A per-entry resolution override (`entry_res`) already produces a distinct `game_key`, so
  the Portal split stays split with no extra rule.

## Edition fold

### What IGDB actually gives us

`igdb_mirror.py` already stores `game_type`, `parent_game` and `version_parent` for 371,978
games in `/data/igdb-catalog.sqlite`. The edition link exists locally and costs no API call.
Measured on the live mirror:

| game_type | meaning | rows | with `version_parent` | with `parent_game` |
|---|---|---:|---:|---:|
| 0 | main_game | 310,201 | 6,877 | 0 |
| 1 | dlc_addon | 17,571 | 23 | 17,571 |
| 2 | expansion | 1,726 | 13 | 1,726 |
| 3 | bundle | 7,088 | 911 | 0 |
| 8 | remake | 1,460 | 11 | 1,460 |
| 9 | remaster | 1,369 | 35 | 1,369 |
| 10 | expanded_game | 2,122 | 71 | 2,122 |
| 11 | port | 8,166 | 20 | 8,165 |
| 13 | pack | 8,915 | 0 | 8,915 |

The two columns are not interchangeable and IGDB uses them inconsistently, so the rule reads
both. Note that **6,877 plain type-0 games carry a `version_parent`**, so a type filter alone
would miss most editions. Verified rows:

    2155   Dark Souls                              type 0
    81085  Dark Souls: Remastered                  type 9   parent_game    2155
    21040  Dark Souls: Prepare to Die Edition      type 3   version_parent 2155
    2368   Dark Souls II                           type 0
    8222   Dark Souls II: Scholar of the First Sin type 10  parent_game    2368
    11133  Dark Souls III                          type 0
    912    Tomb Raider                             type 0                        1996
    43690  Tomb Raider: Collector's Edition        type 0   version_parent 912
    1164   Tomb Raider                             type 0                        2013
    74555  Tomb Raider: Collector's Edition        type 0   version_parent 1164

### The rule

Walk up from the entry's resolved IGDB id:

    while depth < 4:
        if version_parent is set and game_type != 8:   id = version_parent; continue
        if game_type in {9, 10, 11} and parent_game:   id = parent_game;    continue
        stop

Stop conditions, each deliberate:

- **Type 8 (remake) never folds.** Approved rule: a remake is a different game. All 1,460
  remake rows carry a `parent_game`, so without this clause every remake would fold into its
  original. `Gothic 1 Remake` stays separate from `Gothic` (2001).
- **Types 1 and 2 never fold.** Add-ons already leave the grid and list under their parent
  (`2026-08-22-addons-design.md`). This design does not touch them.
- **Type 13 (pack) never folds**, and type 3 folds only through `version_parent`. All 8,915
  packs carry a `parent_game`, but a pack is a multi-game compilation, which the collections
  engine owns. Folding one would file several distinct games under a single card.
- **A cycle, or depth over 4, stops the walk** and leaves `card_key = game_key`. A malformed
  provider graph must not hang a rebuild.

Applied to the six Dark Souls rows, the shelf becomes three cards: Dark Souls (Remastered on
PC and Switch, Prepare To Die on PC), Dark Souls II (base and Scholar of the First Sin), and
Dark Souls III. The two Tomb Raider Collector's Editions fold onto 912 and 1164 respectively,
so the 1996 game and the 2013 reboot stay two cards.

### The card title comes from the copies, never from the fold root

The fold root's name is frequently the regional original. `Mega Man 2` folds onto `Rockman 2:
Dr. Wily no Nazo`, and `Streets of Rage 3` folds onto `Bare Knuckle III`. Taking the title
from the root would rename 53 cards in this library alone, several into Japanese.

So: **`card_key` comes from the root, the displayed title comes from the owned copies.** The
card takes the representative copy's `canonical_title`, with a trailing edition suffix
stripped when the stripped form normalizes to the root's `norm_key`. `DARK SOULS: REMASTERED`
becomes `Dark Souls`, because the root is `Dark Souls`. `Mega Man 2` stays `Mega Man 2`,
because `Rockman 2` is not a suffix strip of it.

### Unmatched editions

`dark souls prepare to die` is unmatched in the live catalog, so it has no IGDB id for the
walk to start from. The fold handles this without touching identity:

- Normalize the title by stripping a known edition suffix set (`Remastered`, `Prepare To Die
  Edition`, `Game of the Year`, `GOTY`, `Definitive Edition`, `Complete Edition`, `Enhanced`,
  `Deluxe`, `HD`) and look the result up in the mirror by `norm_key`.
- A hit supplies the `card_key` only.
- The entry's `game_key`, its provider link and its `matched_by` are unchanged.

**`matchgate.py` is not modified.** The acceptance rule stays exactly as it is. A card
grouping is a display decision and must never be able to bind a wrong identity, fetch art, or
spend an API call. This is the "a match is not an ingest" separation task #21 established.

### Unfold override

Type 10 (`expanded_game`) is the loosest signal in the set, and it is the one that carries
Scholar of the First Sin, so it has to stay in. Measured cases it also pulls in are genuinely
arguable: `Bit Blaster XL` with `Super Bit Blaster XL`, `Arcade Paradise` with `Arcade
Paradise VR`, `Dead Rising 2` with `Off the Record`.

So the fold needs a manual reverse. A per-entry **unfold** override, stored in the same shape
as `entry_res` detach, pins an entry to its own card and is never overwritten by a rebuild. A
manual pin or detach likewise always wins and is never folded.

The Split assist AI area stays the escape hatch for cases neither rule settles, review-only.

The fold is recorded per copy as an `edition` attribute, so a card can state that you own
Prepare To Die on PC and Remastered on Switch.

## Query layer

`_query_games` in `server/app.py` is the single chokepoint. It already serves `/api/games`
and AI search, so both collapse together.

Changes:

- `GROUP BY g.card_key`.
- A deterministic **representative** row per group, ordered by servable art, then store
  source, then `n_sources`, then platform ascending. The representative supplies `cover_v`,
  `has_cover` and `entry_key`, so every card stays addressable and the art rule is preserved
  by construction: the cover shown is one specific entry's own servable art.
- Aggregates move from per-row to per-group: `platforms` unions, `n_sources` sums, `n_kinds`
  unions, tags and attributes union.
- `total`, the facet counts and `hidden_unidentified` count distinct `card_key`.

`_spotlight_rows` and the dashboard counts take the same grouping. A spotlight that offers
the same game twice on different systems is the same defect in a different surface.

## Detail

`GET /api/games/{key}` accepts a `card_key` and keeps accepting an `entry_key`, so existing
links and bookmarks resolve. It returns:

- one identity, one title, one metadata block, one score
- `copies[]`: one row per owned platform entry, each with its own `entry_key`, platform,
  sources, ownership formats, edition label and media kinds

`also_owned_on` becomes `copies` and is kept as an alias for one release. Art resolves per
copy exactly as it does today, keyed on `(norm_key, system)` with the neutral fallback gated
on `game_key`. Selecting a copy switches the art. Nothing in the media layer changes.

## What does not change

- `build_library`'s per-platform rows, `entry_key`, `base_key`, `game_key`.
- Publish, device plan and apply, publish rules, the install ledger. They address entries,
  which is correct: a device push targets one platform.
- Per-format ownership (`ownership.sqlite`, PK `(norm_key, form, platform, state)`).
- The media identity binding, per-console siloing, `media_choose` ranking, the filler and
  shape detectors.
- `matchgate.py` and the acceptance rule.

## Testing

Contract tests written before the change, per the repo's habit:

- `test_card_key_groups.py` — one card per `card_key`; the Dark Souls fixture yields three
  cards from six entries.
- `test_card_keeps_games_apart.py` — Portal Amiga 1986 and Portal PC 2007 stay two cards; Uno
  Game Boy and Uno Steam stay two cards; Tomb Raider 1996 and 2013 stay two cards with their
  Collector's Editions attached to the right one.
- `test_card_cover_is_own_art.py` — a card never reports `has_cover` from another console's
  art, and the representative's cover is the one served.
- `test_card_counts.py` — totals and every facet count equal distinct `card_key`.
- `test_edition_fold.py` — Remastered folds by `parent_game` (81085 to 2155); Prepare To Die
  folds by `version_parent` (21040 to 2155); Scholar folds by `parent_game` (8222 to 2368); a
  type-8 remake does not fold; a type-13 pack does not fold; a cyclic parent graph terminates.
- `test_card_title.py` — a card folded onto a regional root keeps the owned title (`Mega Man
  2`, never `Rockman 2`), and an edition suffix is stripped only when it lands on the root.
- `test_fold_does_not_bind.py` — an edition fold changes no `game_key`, no provider link and
  no `matched_by`, and spends no provider call.
- `test_unfold_override.py` — an unfolded entry keeps its own card across a rebuild.
- `test_publish_after_collapse.py` — a publish plan still targets exactly one platform entry.

`check_invariants.py` gains one invariant: every card's representative resolves to a live
entry, and every entry belongs to exactly one card.

## Rollout

No schema change beyond the added `card_key` column, which `build_library` computes. The
server degrades on a catalog without the column, the same way it already guards `entry_key`,
`base_key` and `game_key` with `_has_col`.

Order:

1. `card_key` column with the default `card_key = game_key`, plus the query-layer collapse.
   This ships the platform half alone and is testable without a re-resolve.
2. The edition fold, computed from the local mirror during `build_library`, plus the unfold
   override.
3. Frontend: grid card, detail `copies`, facet labels.
4. Docs. `README.md` sells per-platform entries as a feature with a screenshot
   (`docs/images/platforms.png`), and DESIGN §11 states the per-platform unit. Both need
   rewriting, and a new screenshot.

The catalog rebuild runs from the UI, never from the CLI.

## Risks

- **Unmatched same-title games collapse.** Two unmatched entries sharing a title with no year
  land on one `title:` card. That is a guess, and it is the same guess `base_key` already
  makes. If it bites, the fix is a marker on `card_key`, not a redesign.
- **Type 10 is loose.** It carries the Scholar case and also some arguable ones. The unfold
  override is the answer, and the measured set is small enough to review by eye.
- **IGDB's parent graph is inconsistent.** The rule reads both columns and caps the walk depth
  for that reason. Anything the graph does not link stays its own card, which is the safe
  failure.
- **Representative churn.** If the representative changes between rebuilds, a card's cover
  changes. The ordering is fully deterministic to prevent it, and a test pins it.

## Open

- Whether a collapsed card shows a platform-count badge. A UI call, not a design blocker.
