# Add-ons belong to the game they extend

Status: design, 2026-08-22. datbird:

> "Dlc/extention/addons are/should be an array attribute of an owner. What ever it takes
> to normalize that data into that structure is the right answer"

So: a game entry carries a list of the add-ons you own for it. Normalized storage, array
presentation. Not a JSON blob in `game_attributes`, because that is not queryable and
"whatever it takes to normalize" rules it out.

A second message settled HOW, and changed the answer. See "Storage" below.

## What the library actually holds today

Measured 2026-08-22, 2,488 entries.

```
content_type in game_attributes    Game 2169 · Dlc 3 · Mod 1 · Hardware 1 · Advertising 1
```

Three DLC rows, used only to HIDE non-games. Nothing links an add-on to what it extends.

The IGDB mirror already has both halves, locally, no network:

| owned entries by IGDB `game_type` | count |
|---|---|
| `standalone_expansion` (4) | 22 |
| `expansion` (2) | 13 |
| `dlc_addon` (1) | 2 |
| **total with a `parent_game`** | **37** |
| …whose parent is also owned | 30 |

Real examples: Doom 3: Resurrection of Evil under Doom 3. Quake Mission Packs 1 and 2
under Quake. Civilization IV: Beyond the Sword under Civilization IV. Shovel Knight:
Plague of Shadows under Shovel Knight.

## The finding that changes the design

**`standalone_expansion` is 22 of the 37, and it must NOT be filed under a parent.**

A standalone expansion runs without the base game. You can own and play Quake II Mission
Pack: The Reckoning with no Quake II. Two of the measured rows prove the point: both Quake
II mission packs are owned and **Quake II is not**. Filing those under a parent that is not
in the library would take them out of the grid and put them nowhere.

So the rule is by TYPE, not by the presence of a parent link:

| IGDB `game_type` | treatment |
|---|---|
| 1 `dlc_addon` | listed under the parent |
| 2 `expansion` | listed under the parent |
| 4 `standalone_expansion` | **stays in the grid as a game** |

That leaves 15 true add-ons today, of which 13 have an owned parent. Small, and it grows
with every source connected.

## What each source can contribute

| source | names the add-on | names the parent |
|---|---|---|
| IGDB | yes, the entry IS the add-on | yes, `parent_game` (54,687 records carry one) |
| Steam | yes, an owned appid with `content_type='Dlc'` | **no** — `steam_meta.payload_json` is a reduced extract without `fullgame`; needs a refetch |
| Nintendo VGC | **no** | yes, the card IS the parent |

Nintendo is the odd one and must not be forced into the list. Its card for Breath of the
Wild says `hasApplication=False, hasAddOnContents=True`: you own add-on content for that
game, and the card never says WHICH. That is a boolean about the parent, not a list member.
Inventing an unnamed entry for it would be fabricating data.

So Nintendo sets `has_addon_content` on the parent entry. Five games today: Breath of the
Wild, Splatoon 3, Pokemon Shield, Mario + Rabbids Kingdom Battle, Capcom Arcade Stadium.

## Storage: they stay in `games`

datbird, immediately after the first draft:

> "those entries should be clickable, show its release date description meta data/media.
> It's a new thing that needs to be added to ludodex"

That settles it, and it overturns the first draft. An add-on with its own year,
description and art needs identity resolution, provider matching, attribute merge, media
fetch, shape measurement, selection and a detail page. A separate `addons.sqlite` holding
titles would mean a SECOND implementation of every one of those. That is the exact
duplication the 2026-08-21 audit was about, and it would be self-inflicted.

**So an add-on is a normal row in `games`.** It keeps its `norm_key`, its `entry_key`, its
attributes, its media and its detail page. Everything already works on it. Two columns are
added, and one view rule:

```sql
ALTER TABLE games ADD COLUMN parent_key   TEXT;   -- the base game's base_key, NULL for a game
ALTER TABLE games ADD COLUMN content_kind TEXT;   -- 'dlc' | 'expansion', NULL for a game
```

* The default library grid and every count filter `parent_key IS NULL`. An add-on is not a
  game you own, it is content for one.
* `game_detail(parent)` returns `addons: [...]`, each a real entry reference, so the UI
  links straight to its detail page.
* An add-on with no owned parent keeps `parent_key = NULL` and stays a plain entry. Nothing
  you own can be hidden by a link to something you do not.

`parent_key` is derived at build time and therefore rebuildable, like `base_key`. A MANUAL
override is the exception and lives in a durable overlay next to `identity_disable`,
because `identity_review` already taught us that a decision rebuilt away is a decision
lost.

## What this makes possible that a name-only list could not

* Click Doom 3: Resurrection of Evil and get its own 2005 date, description and box art.
* Its media rides the ONE pipeline (`_enrich_media` / `_media_finish`), so the shape,
  filler and AI adjudication rules already apply to it.
* Per-add-on ownership stays expressible through `ownership.sqlite`, which is already keyed
  by `(norm_key, form, platform, state)` and needs no change.

## Steps

1. `games.parent_key` + `games.content_kind`, derived in `build_library` from IGDB
   `game_type` in (1, 2) with a `parent_game` resolving to an owned entry. Free, local,
   deterministic, and rebuilt like every other derived column.
2. The library grid, the counts and Spotlight filter `parent_key IS NULL`.
3. Detail API returns `addons: [...]`, and the UI lists them under the game, each linking
   to its own detail page.
4. Nintendo sets `has_addon_content` on its parent entry.
5. Steam's `fullgame` needs an appdetails refetch to carry it. Deferred, because it buys 3
   rows today and costs a full re-pull.

Deliberately NOT copying `collections.sqlite`. That store exists because a compilation's
members may not be owned as entries at all, so they have to be materialized. An add-on is
already an owned entry, so it needs a link, not a store.

Related: `ludodex-per-format-ownership`, `ludodex-collections` (the case this is NOT),
`2026-08-22-nintendo-vgc-design.md`.
