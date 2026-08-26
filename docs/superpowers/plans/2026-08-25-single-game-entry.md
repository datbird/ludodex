# Single Game Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the ludodex library show one card per game instead of one card per (game, platform), folding ports, editions and remasters onto the game they belong to.

**Architecture:** A new `card_key` column on `games` is the grouping key. It defaults to the existing `game_key`, and a pure fold module rewrites it to the IGDB fold root for ports, editions and remasters. `_query_games` groups by it and picks a deterministic representative row. The per-platform rows stay exactly as they are underneath, so publish, ownership and the media identity binding are untouched.

**Tech Stack:** Python 3.12 (stdlib only, no new dependency), SQLite, FastAPI, React + TypeScript + Vite.

**Spec:** `docs/superpowers/specs/2026-08-25-single-game-entry-design.md`

## Global Constraints

- **Never modify `matchgate.py`.** A card grouping is a display decision. It must never bind an identity, fetch art, or spend a provider call.
- **Never modify `game_key`, `entry_key` or `base_key`**, nor any media serve gate that reads `game_key`.
- **Fold types are exactly `{9, 10, 11}` via `parent_game`, plus any type except `8` via `version_parent`.** Type 8 (remake) never folds. Types 1, 2 and 13 never fold.
- **Walk depth is capped at 4** and must terminate on a cycle.
- **The card title comes from the owned copies, never from the fold root.** The fold root is often the regional original (`Rockman 2`, `Bare Knuckle III`).
- **Tests are standalone scripts, not pytest.** They follow `tests/test_cover_rule.py`: a `check(label, cond)` helper, a `main()`, `sys.exit("FAILED: " + label)` on failure, and a final `RESULT:` line. Any test that touches `LUDODEX_DATA` calls `test_support.isolate()` first.
- **Run the suite with `./scripts/run_tests.sh`**, which uses a throwaway container. Never `docker exec` into the running `ludodex` container.
- **The catalog rebuild is run from the UI by the maintainer, never from the CLI.**
- Python style in this repo: 4-space indent, ~95 column lines, docstrings that explain why.

---

### Task 1: The fold rule as a pure module

**Files:**
- Create: `ludodex/cardkey.py`
- Test: `tests/test_edition_fold.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `FOLD_TYPES: frozenset[int]`, `REMAKE: int`, `MAX_DEPTH: int`
  - `fold_root(igdb_id: int, graph: dict[int, tuple[int|None, int|None, int|None]], max_depth: int = MAX_DEPTH) -> int`
  - `strip_edition(title: str) -> str`
  - `card_key_for(game_key: str | None, graph: dict) -> str` returns the card key for one entry
  - `graph` maps `igdb_id -> (game_type, version_parent, parent_game)`

- [ ] **Step 1: Write the failing test**

Create `tests/test_edition_fold.py`:

```python
#!/usr/bin/env python3
"""The edition fold rule, against IGDB's real linkage shape.

IGDB splits the edition relationship over TWO columns and uses them inconsistently:
remasters and expanded games link by `parent_game`, editions link by `version_parent`,
and 6,877 plain type-0 games carry a `version_parent` too. A rule that reads only one
column, or that filters on game_type first, misses most editions. These are the rows
measured on the live mirror on 2026-08-25.
"""
import os
import sys

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    sys.path.insert(0, os.path.join(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))), "ludodex"))
    import cardkey

    # (game_type, version_parent, parent_game) — verified rows from igdb-catalog.sqlite
    graph = {
        2155:  (0, None, None),      # Dark Souls
        81085: (9, None, 2155),      # Dark Souls: Remastered        -> parent_game
        21040: (3, 2155, None),      # Dark Souls: Prepare to Die    -> version_parent
        2368:  (0, None, None),      # Dark Souls II
        8222:  (10, None, 2368),     # Scholar of the First Sin      -> parent_game
        11133: (0, None, None),      # Dark Souls III
        912:   (0, None, None),      # Tomb Raider (1996)
        1164:  (0, None, None),      # Tomb Raider (2013)
        43690: (0, 912, None),       # Tomb Raider: Collector's Edition (2012)
        74555: (0, 1164, None),      # Tomb Raider: Collector's Edition (2013)
        148227: (8, None, 2261),     # Gothic 1 Remake               -> REMAKE, never folds
        2261:  (0, None, None),      # Gothic
        8915:  (13, None, 4242),     # a pack, carries parent_game    -> never folds
        4242:  (0, None, None),
        70001: (1, None, 2155),      # a DLC                          -> never folds
        70002: (2, None, 2368),      # an expansion                   -> never folds
        90001: (11, None, 90002),    # a cycle
        90002: (11, None, 90001),
    }

    check("remaster folds by parent_game", cardkey.fold_root(81085, graph) == 2155)
    check("edition folds by version_parent", cardkey.fold_root(21040, graph) == 2155)
    check("expanded_game folds by parent_game", cardkey.fold_root(8222, graph) == 2368)
    check("a base game is its own root", cardkey.fold_root(2155, graph) == 2155)
    check("Dark Souls II stays apart from Dark Souls",
          cardkey.fold_root(8222, graph) != cardkey.fold_root(81085, graph))
    check("Dark Souls III stays its own root", cardkey.fold_root(11133, graph) == 11133)

    # type 0 with a version_parent is the COMMON edition shape, not an exception
    check("2012 Collector's Edition folds onto the 1996 game",
          cardkey.fold_root(43690, graph) == 912)
    check("2013 Collector's Edition folds onto the 2013 game",
          cardkey.fold_root(74555, graph) == 1164)
    check("the two Tomb Raiders stay apart",
          cardkey.fold_root(43690, graph) != cardkey.fold_root(74555, graph))

    check("a remake never folds", cardkey.fold_root(148227, graph) == 148227)
    check("a pack never folds", cardkey.fold_root(8915, graph) == 8915)
    check("a dlc never folds", cardkey.fold_root(70001, graph) == 70001)
    check("an expansion never folds", cardkey.fold_root(70002, graph) == 70002)
    check("an unknown id is its own root", cardkey.fold_root(999999, graph) == 999999)
    check("a cycle terminates", cardkey.fold_root(90001, graph) in (90001, 90002))

    check("card_key_for folds an igdb key",
          cardkey.card_key_for("igdb:81085", graph) == "igdb:2155")
    check("card_key_for leaves a title key alone",
          cardkey.card_key_for("title:some rom", graph) == "title:some rom")
    check("card_key_for survives a null game_key",
          cardkey.card_key_for(None, graph) is None)
    check("card_key_for survives a malformed key",
          cardkey.card_key_for("igdb:not-a-number", graph) == "igdb:not-a-number")

    check("strip_edition removes a known suffix",
          cardkey.strip_edition("Dark Souls: Remastered") == "Dark Souls")
    check("strip_edition removes Prepare To Die Edition",
          cardkey.strip_edition("DARK SOULS: Prepare To Die Edition") == "DARK SOULS")
    check("strip_edition removes GOTY",
          cardkey.strip_edition("Fallout 3: Game of the Year Edition") == "Fallout 3")
    check("strip_edition leaves an ordinary title alone",
          cardkey.strip_edition("Mega Man 2") == "Mega Man 2")
    check("strip_edition does not eat a whole title",
          cardkey.strip_edition("Remastered") == "Remastered")

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `./scripts/run_tests.sh test_edition_fold`
Expected: FAIL with `ModuleNotFoundError: No module named 'cardkey'`

- [ ] **Step 3: Write the implementation**

Create `ludodex/cardkey.py`:

```python
#!/usr/bin/env python3
"""Which card an entry belongs to.

The library shows one card per GAME. An entry's `game_key` already answers "which game
is this" for ports of one release, so it is the default. What it does not answer is
"which game is this an EDITION of": Dark Souls: Remastered, Prepare To Die Edition and
plain Dark Souls are three IGDB ids and one game.

IGDB records that relationship over TWO columns and uses them inconsistently:

  * `version_parent` carries editions and bundles-of-one-game. 6,877 plain type-0 games
    have one, so filtering on game_type before reading it misses most editions.
  * `parent_game` carries remasters (9), expanded games (10) and ports (11) — and also
    DLC (1), expansions (2), packs (13) and remakes (8), none of which may fold.

So the rule reads both, and the type filter applies only to the `parent_game` branch.

WHAT NEVER FOLDS, each for its own reason:
  * type 8, remake — a remake is a different game. All 1,460 remake rows carry a
    parent_game, so without this clause every remake would fold into its original.
  * types 1 and 2, dlc and expansion — add-ons already leave the grid and list under
    their parent (2026-08-22-addons-design.md).
  * type 13, pack — a pack is a multi-game compilation, which the collections engine
    owns. All 8,915 carry a parent_game. Folding one would file several distinct games
    under a single card.

This module is PURE. It reads a graph dict and returns keys. It never opens a database,
never calls a provider, and never writes an identity. A card grouping is a display
decision; binding identity is `matchgate`'s job and stays there.
"""

# game_type values that fold through `parent_game`.
FOLD_TYPES = frozenset((9, 10, 11))     # remaster, expanded_game, port
REMAKE = 8
MAX_DEPTH = 4

# Trailing edition markers, longest first so "Game of the Year Edition" wins over
# "Edition". Matched case-insensitively against the end of a title, after an optional
# ":" or "-" separator.
EDITION_SUFFIXES = (
    "Game of the Year Edition", "Prepare To Die Edition", "Definitive Edition",
    "Complete Edition", "Enhanced Edition", "Remastered Edition", "Deluxe Edition",
    "Special Edition", "Anniversary Edition", "Game of the Year", "Remastered",
    "Enhanced", "Definitive", "Complete", "Deluxe", "GOTY", "HD",
)

_SEPARATORS = (":", "-", "–")


def fold_root(igdb_id, graph, max_depth=MAX_DEPTH):
    """The id of the game an edition belongs to. Returns `igdb_id` unchanged when the
    entry is already a root, is a type that never folds, or is absent from the graph.

    `graph` maps igdb_id -> (game_type, version_parent, parent_game).

    Terminates on a cycle and at `max_depth`. A malformed provider graph must not hang
    a catalog rebuild, and leaving the entry on its own card is the safe failure.
    """
    try:
        cur = int(igdb_id)
    except (TypeError, ValueError):
        return igdb_id
    seen = {cur}
    for _ in range(max_depth):
        row = graph.get(cur)
        if not row:
            return cur
        gtype, vparent, pparent = row
        nxt = None
        if vparent and gtype != REMAKE:
            nxt = int(vparent)
        elif not vparent and gtype in FOLD_TYPES and pparent:
            nxt = int(pparent)
        if nxt is None or nxt in seen:
            return cur
        seen.add(nxt)
        cur = nxt
    return cur


def card_key_for(game_key, graph, max_depth=MAX_DEPTH):
    """The card key for one entry. `igdb:<id>` folds to its root; every other shape —
    `title:<norm_key>`, None, a malformed key — is returned untouched, because only a
    resolved identity has a provider graph to walk."""
    if not game_key or not game_key.startswith("igdb:"):
        return game_key
    raw = game_key[5:]
    try:
        iid = int(raw)
    except ValueError:
        return game_key
    return "igdb:%d" % fold_root(iid, graph, max_depth)


def strip_edition(title):
    """Remove ONE trailing edition marker. Returns the title unchanged when nothing
    matches, and never returns an empty string — a game actually called "Remastered"
    keeps its name."""
    if not title:
        return title
    t = title.rstrip()
    low = t.lower()
    for suf in EDITION_SUFFIXES:
        if not low.endswith(suf.lower()):
            continue
        head = t[:len(t) - len(suf)].rstrip()
        while head and head[-1] in _SEPARATORS:
            head = head[:-1].rstrip()
        if head:
            return head
    return title
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `./scripts/run_tests.sh test_edition_fold`
Expected: `ok    test_edition_fold    RESULT: 22 checks, all passed`

- [ ] **Step 5: Commit**

```bash
git add ludodex/cardkey.py tests/test_edition_fold.py
git commit -m "feat(cardkey): the edition fold rule, read from both IGDB parent columns"
```

---

### Task 2: Assign card keys and titles to a set of entries

**Files:**
- Modify: `ludodex/cardkey.py`
- Test: `tests/test_card_title.py`

**Interfaces:**
- Consumes: `fold_root`, `card_key_for`, `strip_edition` from Task 1.
- Produces:
  - `card_key_for_title(game_key, canonical_title, title_index, graph) -> str`. `title_index` maps `norm_key -> igdb_id`.
  - `assign(entries, graph, unfolded=(), title_index=None) -> dict[str, str]` mapping `entry_key -> card_key`. `entries` is an iterable of `(entry_key, game_key, canonical_title)`.
  - `card_title(card_key, copy_titles, root_names) -> str`. `copy_titles` is the owned copies' `canonical_title` values in representative order; `root_names` maps `igdb_id -> name`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_card_title.py`:

```python
#!/usr/bin/env python3
"""A card's KEY comes from the fold root. Its TITLE does not.

The fold root is frequently the regional original: Mega Man 2 folds onto "Rockman 2:
Dr. Wily no Nazo", Streets of Rage 3 onto "Bare Knuckle III". Taking the display title
from the root renamed 53 cards in the live library, several into Japanese. So the title
comes from the owned copies, and an edition suffix is stripped only when the stripped
form is the root's own name.
"""
import os
import sys

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    sys.path.insert(0, os.path.join(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))), "ludodex"))
    import cardkey

    graph = {
        2155:  (0, None, None),
        81085: (9, None, 2155),
        21040: (3, 2155, None),
        1715:  (10, None, 170742),      # Mega Man 2 -> Rockman 2
        170742: (0, None, None),
    }
    names = {2155: "Dark Souls", 81085: "Dark Souls: Remastered",
             21040: "Dark Souls: Prepare to Die Edition",
             1715: "Mega Man 2", 170742: "Rockman 2: Dr. Wily no Nazo"}

    # --- the UNMATCHED edition, which has no id to walk from ---
    # This is the live Prepare To Die row: matchgate refused it, so its game_key is a
    # title bucket. The card still has to be Dark Souls, and finding that must not bind
    # an identity.
    tindex = {"dark souls": 2155, "mega man 2": 1715}
    check("an unmatched edition folds by its stripped title",
          cardkey.card_key_for_title(
              "title:dark souls prepare to die",
              "DARK SOULS: Prepare To Die Edition", tindex, graph) == "igdb:2155")
    check("an unmatched title with no hit stays a title card",
          cardkey.card_key_for_title(
              "title:some rom", "Some ROM", tindex, graph) == "title:some rom")
    check("an unmatched title that needs no strip still resolves",
          cardkey.card_key_for_title(
              "title:mega man 2", "Mega Man 2", tindex, graph) == "igdb:170742")
    check("an already-identified key is not re-derived from its title",
          cardkey.card_key_for_title(
              "igdb:81085", "Dark Souls: Remastered", tindex, graph) == "igdb:2155")

    # --- assign ---
    entries = [("dark souls@pc", "igdb:81085", "DARK SOULS: REMASTERED"),
               ("dark souls@switch", "igdb:81085", "DARK SOULS: REMASTERED"),
               ("dark souls prepare to die@pc", "title:dark souls prepare to die",
                "DARK SOULS: Prepare To Die Edition"),
               ("mega man 2@nes", "igdb:1715", "Mega Man 2"),
               ("some rom@snes", "title:some rom", "Some ROM")]
    got = cardkey.assign(entries, graph, title_index=tindex)
    check("both Dark Souls platforms land on one card",
          got["dark souls@pc"] == got["dark souls@switch"] == "igdb:2155")
    check("the unmatched edition lands on the same card",
          got["dark souls prepare to die@pc"] == "igdb:2155")
    check("Mega Man 2 folds onto the Rockman root", got["mega man 2@nes"] == "igdb:170742")
    check("an unidentified entry keeps its title key",
          got["some rom@snes"] == "title:some rom")
    check("assign works with no title index at all",
          cardkey.assign(entries, graph)["dark souls@pc"] == "igdb:2155")

    # --- unfold override ---
    got2 = cardkey.assign(entries, graph, unfolded={"dark souls prepare to die@pc"},
                          title_index=tindex)
    check("an unfolded entry keeps its own card",
          got2["dark souls prepare to die@pc"] == "title:dark souls prepare to die")
    check("unfolding one entry does not disturb the others",
          got2["dark souls@pc"] == "igdb:2155")

    # --- card_title ---
    check("the suffix is stripped when it lands on the root",
          cardkey.card_title("igdb:2155", ["DARK SOULS: REMASTERED"], names) == "DARK SOULS")
    check("a regional root never renames the card",
          cardkey.card_title("igdb:170742", ["Mega Man 2"], names) == "Mega Man 2")
    check("the first copy wins when nothing strips",
          cardkey.card_title("igdb:2155", ["Mega Man 2", "Other"], names) == "Mega Man 2")
    check("a title card uses its copy",
          cardkey.card_title("title:some rom", ["Some ROM"], names) == "Some ROM")
    check("no copies falls back to the root name",
          cardkey.card_title("igdb:2155", [], names) == "Dark Souls")
    check("no copies and no root name yields an empty title",
          cardkey.card_title("igdb:999999", [], names) == "")

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `./scripts/run_tests.sh test_card_title`
Expected: FAIL with `AttributeError: module 'cardkey' has no attribute 'assign'`

- [ ] **Step 3: Write the implementation**

First add the one import `cardkey` needs, at the top of `ludodex/cardkey.py` below the
docstring. `titlenorm` is a local stdlib-only module, so the module stays pure:

```python
from titlenorm import norm
```

Then append:

```python
def card_key_for_title(game_key, canonical_title, title_index, graph):
    """The card key for an entry no provider matched.

    An unmatched edition has no id for `fold_root` to start from. The live catalog's
    "DARK SOULS: Prepare To Die Edition" is exactly this: matchgate refused it, so its
    game_key is `title:dark souls prepare to die`, and it still belongs on the Dark
    Souls card. So the walk starts from the TITLE instead: strip a trailing edition
    marker and look the result up by norm_key.

    A hit supplies the CARD ONLY. The entry's game_key, its provider link and its
    matched_by are untouched, and no provider is called. Grouping a card is a display
    decision; binding an identity is matchgate's job and stays there.
    """
    if not game_key or not game_key.startswith("title:"):
        return card_key_for(game_key, graph)
    if not title_index or not canonical_title:
        return game_key
    stripped = strip_edition(canonical_title)
    iid = title_index.get(norm(stripped))
    if not iid:
        return game_key
    return "igdb:%d" % fold_root(int(iid), graph)


def assign(entries, graph, unfolded=(), title_index=None):
    """{entry_key: card_key} for a whole catalog.

    `entries` is an iterable of (entry_key, game_key, canonical_title). `unfolded` is
    the set of entry_keys the user has pinned to their own card; those keep their
    `game_key` and are never folded, which is the manual reverse for IGDB's looser
    links. `title_index` maps norm_key -> igdb_id and is what lets an UNMATCHED edition
    find its card; omit it and unmatched entries simply stay on their own cards.
    """
    unfolded = set(unfolded or ())
    out = {}
    for ekey, gkey, title in entries:
        if ekey in unfolded:
            out[ekey] = gkey
        else:
            out[ekey] = card_key_for_title(gkey, title, title_index, graph)
    return out


def card_title(card_key, copy_titles, root_names):
    """The title to display on a card.

    Rule: take the first owned copy's title, and strip a trailing edition marker ONLY
    when the stripped form is the fold root's own name. That turns "DARK SOULS:
    REMASTERED" into "DARK SOULS" (root: Dark Souls) while leaving "Mega Man 2" alone
    (root: Rockman 2: Dr. Wily no Nazo). Falls back to the root's name when the card
    has no copies, which happens only for a synthetic card.
    """
    root_name = ""
    if card_key and card_key.startswith("igdb:"):
        try:
            root_name = root_names.get(int(card_key[5:]), "") or ""
        except ValueError:
            root_name = ""
    titles = [t for t in (copy_titles or []) if t]
    if not titles:
        return root_name
    first = titles[0]
    if root_name:
        stripped = strip_edition(first)
        if stripped != first and _same_title(stripped, root_name):
            return stripped
    return first


def _same_title(a, b):
    """Loose title equality for the strip check. Compares alphanumerics only, so
    trademark symbols, punctuation, spacing and case cannot defeat the match —
    "DARK SOULS" and "Dark Souls" are the same name."""
    keep = lambda s: "".join(c for c in (s or "").lower() if c.isalnum())
    return keep(a) == keep(b)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `./scripts/run_tests.sh test_card_title`
Expected: `ok    test_card_title    RESULT: 17 checks, all passed`

- [ ] **Step 5: Run the whole suite to check nothing regressed**

Run: `./scripts/run_tests.sh`
Expected: 0 failures. Skips are fine (live-gated tests).

- [ ] **Step 6: Commit**

```bash
git add ludodex/cardkey.py tests/test_card_title.py
git commit -m "feat(cardkey): assign card keys per entry, and take the title from the copies"
```

---

### Task 3: Persist `card_key` during the catalog build

**Files:**
- Modify: `ludodex/build_library.py` (the `games` DDL; the three `INSERT INTO games` sites at roughly lines 1120, 1155 and 1250; add `_igdb_fold_graph()` beside `_igdb_addon_parents()` at roughly line 565)
- Test: `tests/test_card_key_persisted.py`

**Interfaces:**
- Consumes: `cardkey.assign` from Task 2.
- Produces: a `card_key TEXT` column on `games`, populated for every row, plus `build_library._igdb_fold_graph() -> dict[int, tuple]` and `build_library._igdb_names() -> dict[int, str]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_card_key_persisted.py`:

```python
#!/usr/bin/env python3
"""Every catalog row carries a card_key, and it defaults to the game_key.

The column is what the whole collapse groups on, so a row without one would silently
vanish from the library. This pins the DDL and the fold-graph reader; the grouping
behaviour itself is pinned by test_card_key_groups.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                      # noqa: E402
test_support.isolate()

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(root, "ludodex"))
    data = os.environ["LUDODEX_DATA"]

    # A mirror holding exactly the Dark Souls linkage, so the reader has real shapes.
    mir = sqlite3.connect(os.path.join(data, "igdb-catalog.sqlite"))
    mir.executescript("""
    CREATE TABLE games(id INTEGER PRIMARY KEY, name TEXT, norm_key TEXT,
                       game_type INTEGER, parent_game INTEGER, version_parent INTEGER);
    INSERT INTO games VALUES(2155,'Dark Souls','dark souls',0,NULL,NULL);
    INSERT INTO games VALUES(81085,'Dark Souls: Remastered','dark souls remastered',
                             9,2155,NULL);
    INSERT INTO games VALUES(21040,'Dark Souls: Prepare to Die Edition',
                             'dark souls prepare to die edition',3,NULL,2155);
    """)
    mir.commit()
    mir.close()

    import build_library

    graph = build_library._igdb_fold_graph()
    check("the graph reads game_type", graph[81085][0] == 9)
    check("the graph reads version_parent", graph[21040][1] == 2155)
    check("the graph reads parent_game", graph[81085][2] == 2155)
    check("a root has no parents", graph[2155] == (0, None, None))

    names = build_library._igdb_names()
    check("names are read from the mirror", names[2155] == "Dark Souls")

    tindex = build_library._igdb_title_index()
    check("the title index maps a main game", tindex.get("dark souls") == 2155)
    check("the title index excludes editions",
          2155 in tindex.values() and 21040 not in tindex.values())

    check("a missing mirror yields an empty graph, not a crash",
          isinstance(build_library._igdb_fold_graph(), dict))

    # The DDL must declare the column, or every grouped query silently returns nothing.
    con = sqlite3.connect(":memory:")
    ddl = [s for s in build_library.SCHEMA.split(";") if "CREATE TABLE games" in s]
    check("the games DDL declares card_key", ddl and "card_key" in ddl[0])
    con.executescript(build_library.SCHEMA)
    cols = [r[1] for r in con.execute("PRAGMA table_info(games)")]
    check("card_key is a real column", "card_key" in cols)
    check("game_key is untouched", "game_key" in cols)
    check("entry_key is untouched", "entry_key" in cols)

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `./scripts/run_tests.sh test_card_key_persisted`
Expected: FAIL with `AttributeError: module 'build_library' has no attribute '_igdb_fold_graph'`

Note: if `build_library` does not expose its DDL as a module-level `SCHEMA` string, the last four checks fail on `AttributeError` instead. Extracting the `CREATE TABLE` script into a module-level `SCHEMA = """..."""` constant is part of Step 3 and is a pure move, no text changes.

- [ ] **Step 3: Write the implementation**

3a. In `ludodex/build_library.py`, lift the schema script into a module-level constant `SCHEMA = """..."""` if it is not one already, keeping the SQL byte-identical, and have the existing `executescript` call use it.

3b. Add `card_key TEXT` to the `games` DDL, directly after the `game_key` line, with this comment:

```sql
  card_key TEXT,                   -- the LIBRARY GROUPING key (2026-08-25-single-game-entry-design.md):
                                   -- game_key by default, rewritten to the IGDB fold root for ports,
                                   -- editions and remasters so one game is one card. Display only —
                                   -- it never binds identity and never gates media.
```

3c. Add the graph readers beside `_igdb_addon_parents()`, following its mirror-reading pattern exactly:

```python
def _igdb_fold_graph():
    """{igdb_id: (game_type, version_parent, parent_game)} for the whole mirror.

    Feeds `cardkey.fold_root`. READ FROM THE MIRROR, NOT THE CACHE, for the same reason
    `_igdb_addon_parents` does: `igdb.GAME_FIELDS` never requested `version_parent`, so
    the cache cannot answer this at all.
    """
    out = {}
    _mir = os.path.join(DATA, "igdb-catalog.sqlite")
    if not os.path.exists(_mir):
        return out
    _c = sqlite3.connect("file:%s?mode=ro" % _mir, uri=True)
    try:
        for _iid, _t, _vp, _pg in _c.execute(
                "SELECT id, game_type, version_parent, parent_game FROM games"):
            out[int(_iid)] = (_t, _vp, _pg)
    except sqlite3.OperationalError:
        pass                                   # mirror predates the columns
    finally:
        _c.close()
    return out


def _igdb_names():
    """{igdb_id: name} from the mirror, for the card-title fallback."""
    out = {}
    _mir = os.path.join(DATA, "igdb-catalog.sqlite")
    if not os.path.exists(_mir):
        return out
    _c = sqlite3.connect("file:%s?mode=ro" % _mir, uri=True)
    try:
        for _iid, _nm in _c.execute("SELECT id, name FROM games"):
            out[int(_iid)] = _nm
    except sqlite3.OperationalError:
        pass
    finally:
        _c.close()
    return out


def _igdb_title_index():
    """{norm_key: igdb_id} for MAIN GAMES only, so an unmatched edition can find its
    card by title (`cardkey.card_key_for_title`).

    Restricted to `game_type=0` on purpose. The index answers "which game is this the
    edition OF", and an edition must never be the answer to that. A duplicate norm_key
    keeps the LOWEST id, which is the earliest record and in practice the original.

    This index feeds the CARD only. It never binds an identity, so it is deliberately
    looser than `matchgate`, which stays untouched.
    """
    out = {}
    _mir = os.path.join(DATA, "igdb-catalog.sqlite")
    if not os.path.exists(_mir):
        return out
    _c = sqlite3.connect("file:%s?mode=ro" % _mir, uri=True)
    try:
        for _nk, _iid in _c.execute(
                "SELECT norm_key, MIN(id) FROM games WHERE game_type=0 "
                "AND norm_key IS NOT NULL AND norm_key!='' GROUP BY norm_key"):
            out[_nk] = int(_iid)
    except sqlite3.OperationalError:
        pass                                   # mirror predates the column
    finally:
        _c.close()
    return out
```

3d. Add `import cardkey` to the imports at the top, beside `from titlenorm import norm`.

3e. Build the graph and the title index once, before the insert loop:

```python
_fold_graph = _igdb_fold_graph()
_title_index = _igdb_title_index()
```

3f. At each of the three `INSERT INTO games` sites, add `card_key` to the column list and
`cardkey.card_key_for_title(_gk, <canonical title>, _title_index, _fold_graph)` to the
values.

At the first site the game_key expression is the conditional
`("title:%s" % base if (base, plat) in blocked_entries else _game_key(base, plat, bkey))`.
Bind it to a local `_gk` first and pass `_gk` to the `game_key` column and to
`card_key_for_title`, so the two can never drift. The canonical title at that site is
`canonical`; at the wishlist site it is `w["title"]`; at the collection-member site it is
`_m["member_title"]`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `./scripts/run_tests.sh test_card_key_persisted`
Expected: `ok    test_card_key_persisted    RESULT: 11 checks, all passed`

- [ ] **Step 5: Run the whole suite**

Run: `./scripts/run_tests.sh`
Expected: 0 failures.

- [ ] **Step 6: Commit**

```bash
git add ludodex/build_library.py tests/test_card_key_persisted.py
git commit -m "feat(build): persist card_key per entry, folded from the IGDB mirror"
```

---

### Task 4: Collapse the library query

**Files:**
- Modify: `server/app.py`, `_query_games` (roughly lines 1019-1290)
- Test: `tests/test_card_key_groups.py`

**Interfaces:**
- Consumes: the `card_key` column from Task 3.
- Produces: `/api/games` returning one item per card, each item carrying `card_key`, the representative's `entry_key`, and unioned `platforms`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_card_key_groups.py`:

```python
#!/usr/bin/env python3
"""One card per game, and the counts that go with it.

Six Dark Souls rows in the live catalog were six tiles: two platforms of one game, an
unmatched edition, and two more editions filed as their own titles. Grouping on
card_key makes them three. The properties that must survive the grouping are the ones
the per-platform refactor bought in the first place, so they are pinned here too.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                      # noqa: E402
test_support.isolate()

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "ludodex"))
    from server import app as srv

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE games(id INTEGER PRIMARY KEY, canonical_title TEXT, norm_key TEXT,
      platform TEXT, entry_key TEXT, base_key TEXT, game_key TEXT, card_key TEXT,
      n_sources INTEGER DEFAULT 1, n_kinds INTEGER DEFAULT 1, sources_summary TEXT,
      has_emulation INT DEFAULT 0, wanted INT DEFAULT 0, parent_key TEXT,
      content_kind TEXT);
    CREATE TABLE sources(game_id INTEGER, source TEXT, platform TEXT, state TEXT DEFAULT 'have');
    CREATE TABLE metadata_links(game_id INTEGER, provider TEXT);
    CREATE TABLE game_attributes(game_id INTEGER, kind TEXT, value TEXT);
    CREATE TABLE game_tags(game_id INTEGER, origin TEXT, tag TEXT);
    """)
    con.execute("ATTACH DATABASE ':memory:' AS m")
    con.execute("ATTACH DATABASE ':memory:' AS u")
    con.execute("ATTACH DATABASE ':memory:' AS t")
    con.execute("ATTACH DATABASE ':memory:' AS sco")
    con.executescript("""
    CREATE TABLE m.media(norm_key TEXT, system TEXT, kind TEXT, chosen INT,
                         sha1 TEXT, game_key TEXT);
    CREATE TABLE u.user_media(norm_key TEXT, kind TEXT, sha1 TEXT, created INT);
    CREATE TABLE t.user_tags(norm_key TEXT, tag TEXT);
    CREATE TABLE sco.game_scores(norm_key TEXT, universal REAL);
    """)

    n = [0]

    def game(title, nk, plat, gk, ck):
        n[0] += 1
        con.execute("INSERT INTO games(id,canonical_title,norm_key,platform,entry_key,"
                    "base_key,game_key,card_key,sources_summary) "
                    "VALUES(?,?,?,?,?,?,?,?,'steam')",
                    (n[0], title, nk, plat, "%s@%s" % (nk, plat), nk, gk, ck))
        con.execute("INSERT INTO sources(game_id,source,platform) VALUES(?,'steam',?)",
                    (n[0], plat))
        con.execute("INSERT INTO metadata_links(game_id,provider) VALUES(?,'igdb')", (n[0],))

    # the six live rows, with the card_key the fold produces
    game("DARK SOULS: REMASTERED", "dark souls", "pc", "igdb:81085", "igdb:2155")
    game("DARK SOULS: REMASTERED", "dark souls", "switch", "igdb:81085", "igdb:2155")
    game("DARK SOULS: Prepare To Die Edition", "dark souls prepare to die", "pc",
         "title:dark souls prepare to die", "igdb:2155")
    game("Dark Souls II", "dark souls 2", "pc", "igdb:2368", "igdb:2368")
    game("Dark Souls II: Scholar of the First Sin", "dark souls 2 scholar of the first sin",
         "pc", "igdb:8222", "igdb:2368")
    game("DARK SOULS III", "dark souls 3", "pc", "igdb:11133", "igdb:11133")
    con.commit()

    res = srv._query_games(con, limit=100)
    keys = [it["card_key"] for it in res["items"]]
    check("six entries become three cards", len(res["items"]) == 3)
    check("the total counts cards, not entries", res["total"] == 3)
    check("no card key repeats", len(set(keys)) == len(keys))
    check("Dark Souls is one card", keys.count("igdb:2155") == 1)
    check("Dark Souls II is its own card", "igdb:2368" in keys)
    check("Dark Souls III is its own card", "igdb:11133" in keys)

    ds = [it for it in res["items"] if it["card_key"] == "igdb:2155"][0]
    check("the card unions its platforms",
          set(ds["platforms"].split(",")) == {"pc", "switch"})
    check("the card sums its sources", ds["n_sources"] == 3)
    check("the card carries an addressable entry_key",
          ds["entry_key"] in ("dark souls@pc", "dark souls@switch",
                              "dark souls prepare to die@pc"))

    # determinism: the representative must not move between identical queries
    again = srv._query_games(con, limit=100)
    ds2 = [it for it in again["items"] if it["card_key"] == "igdb:2155"][0]
    check("the representative is deterministic", ds["entry_key"] == ds2["entry_key"])

    # a filter still narrows to cards, not to entries
    sw = srv._query_games(con, platform="switch", limit=100)
    check("a platform filter returns the whole card", len(sw["items"]) == 1)
    check("and it is the Dark Souls card", sw["items"][0]["card_key"] == "igdb:2155")

    # an un-rebuilt catalog (no card_key column) must still serve
    old = sqlite3.connect(":memory:")
    old.row_factory = sqlite3.Row
    check("degrades without the column", srv._has_col(old, "games", "card_key") is False)

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `./scripts/run_tests.sh test_card_key_groups`
Expected: FAIL on `six entries become three cards`, because `_query_games` returns six rows.

- [ ] **Step 3: Write the implementation**

In `server/app.py::_query_games`:

3a. Detect the column beside the existing `has_ek` line:

```python
    has_ck = _has_col(con, "games", "card_key")
    # the grouping key: card_key on a rebuilt catalog, else the entry itself, so a
    # deploy before the first rebuild serves exactly what it served yesterday
    gkey = "g.card_key" if has_ck else ("g.entry_key" if has_ek else "g.norm_key")
```

3b. Replace the total with a card count:

```python
    total = con.execute(
        "SELECT COUNT(DISTINCT COALESCE(%s, g.entry_key)) FROM games g" % gkey
        if has_ck else "SELECT COUNT(*) FROM games g" + clause, args).fetchone()[0]
```

Write it as two explicit branches rather than the inline conditional above, so the
`clause` is appended in both:

```python
    if has_ck:
        total = con.execute(
            "SELECT COUNT(DISTINCT COALESCE(g.card_key, g.entry_key)) "
            "FROM games g" + clause, args).fetchone()[0]
    else:
        total = con.execute("SELECT COUNT(*) FROM games g" + clause, args).fetchone()[0]
```

Apply the same `COUNT(DISTINCT ...)` change to the `hidden` query directly below it.

3c. Add the representative ordering and the grouping to the main query. Immediately before `base = (`:

```python
    # The REPRESENTATIVE row of a card. A card shows one cover, so one entry has to own
    # it, and the choice must be stable across rebuilds or the grid's art churns for no
    # reason. Order: an entry with servable art first (so a card never shows a
    # placeholder while a sibling holds a real cover), then a store entry, then the
    # richest, then the platform alphabetically as the final tiebreak.
    rep_order = ("%s DESC, (g.has_emulation=0) DESC, g.n_sources DESC, "
                 "COALESCE(g.platform,'') ASC, g.id ASC"
                 % _has_cover_sql(has_ek, _has_col(con, "games", "game_key")))
```

3d. In the `base` SELECT, wrap the row set so grouping picks the representative. Replace
`"FROM games g" + clause` with:

```python
        "FROM games g" + clause + (
            (" GROUP BY COALESCE(%s, g.entry_key)" % gkey) if has_ck else "")
```

and change the per-row columns that must aggregate:

- `g.n_sources` becomes `SUM(g.n_sources) AS n_sources` when `has_ck`
- `g.n_kinds` becomes `MAX(g.n_kinds) AS n_kinds` when `has_ck`
- the `platforms` subquery becomes a union over the group:
  `"(SELECT group_concat(DISTINCT s.platform) FROM sources s WHERE s.game_id IN (SELECT id FROM games g2 WHERE COALESCE(g2.card_key,g2.entry_key)=COALESCE(g.card_key,g.entry_key)) AND s.platform IS NOT NULL AND s.platform!='') AS platforms"`
- every other `g.<col>` reference stays as it is; SQLite's bare-column rule returns them from the row chosen by the `ORDER BY` inside the group, which is why 3e is required

3e. Force the representative choice by ordering inside the group. SQLite picks bare
columns from the last row it sees per group, so add `rep_order` as the innermost sort by
selecting from an ordered subquery:

```python
        "FROM (SELECT * FROM games g" + clause + " ORDER BY " + rep_order + ") g"
```

with the outer `GROUP BY` applied to that. Keep the un-grouped path byte-identical when
`has_ck` is false.

3f. Add `card_key` to the returned item dict:

```python
        "card_key": (r["card_key"] if "card_key" in r.keys() else r["entry_key"]),
```

3g. Add `"card_key": "g.card_key"` handling to the facet counting so facets count
distinct cards. Find each facet `COUNT(*)` in the facets endpoint and change it to
`COUNT(DISTINCT COALESCE(g.card_key, g.entry_key))` under the same `has_ck` guard.

- [ ] **Step 4: Run the test to verify it passes**

Run: `./scripts/run_tests.sh test_card_key_groups`
Expected: `ok    test_card_key_groups    RESULT: 14 checks, all passed`

- [ ] **Step 5: Run the whole suite**

Run: `./scripts/run_tests.sh`
Expected: 0 failures. `test_apicleanup_queries`, `test_unknown_filter_is_not_everything` and `test_cover_rule` exercise this function and must still pass unchanged.

- [ ] **Step 6: Commit**

```bash
git add server/app.py tests/test_card_key_groups.py
git commit -m "feat(api): group the library by card_key, one card per game"
```

---

### Task 5: Keep the art rule intact under grouping

**Files:**
- Modify: `server/app.py` (only if Task 4's grouping broke a cover path)
- Test: `tests/test_card_cover_is_own_art.py`

**Interfaces:**
- Consumes: the grouped query from Task 4.
- Produces: no new interface. This task proves the property the per-platform refactor exists to protect.

- [ ] **Step 1: Write the failing test**

Create `tests/test_card_cover_is_own_art.py`:

```python
#!/usr/bin/env python3
"""A card never borrows another console's art.

This is the property the 2026-07-15 per-platform refactor was built for: a TurboGrafx
game must not show a Game Boy cover. Collapsing platforms into one card is exactly the
change that could give it back, because a card now spans consoles. The rule survives
because the CARD shows one REPRESENTATIVE ENTRY's art, and that entry's art is still
gated on its own system and its own game_key.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                      # noqa: E402
test_support.isolate()

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "ludodex"))
    from server import app as srv

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE games(id INTEGER PRIMARY KEY, canonical_title TEXT, norm_key TEXT,
      platform TEXT, entry_key TEXT, base_key TEXT, game_key TEXT, card_key TEXT,
      n_sources INTEGER DEFAULT 1, n_kinds INTEGER DEFAULT 1, sources_summary TEXT,
      has_emulation INT DEFAULT 1, wanted INT DEFAULT 0, parent_key TEXT, content_kind TEXT);
    CREATE TABLE sources(game_id INTEGER, source TEXT, platform TEXT, state TEXT DEFAULT 'have');
    CREATE TABLE metadata_links(game_id INTEGER, provider TEXT);
    CREATE TABLE game_attributes(game_id INTEGER, kind TEXT, value TEXT);
    CREATE TABLE game_tags(game_id INTEGER, origin TEXT, tag TEXT);
    """)
    con.execute("ATTACH DATABASE ':memory:' AS m")
    con.execute("ATTACH DATABASE ':memory:' AS u")
    con.execute("ATTACH DATABASE ':memory:' AS t")
    con.execute("ATTACH DATABASE ':memory:' AS sco")
    con.executescript("""
    CREATE TABLE m.media(norm_key TEXT, system TEXT, kind TEXT, chosen INT,
                         sha1 TEXT, game_key TEXT);
    CREATE TABLE u.user_media(norm_key TEXT, kind TEXT, sha1 TEXT, created INT);
    CREATE TABLE t.user_tags(norm_key TEXT, tag TEXT);
    CREATE TABLE sco.game_scores(norm_key TEXT, universal REAL);
    """)

    n = [0]

    def game(nk, plat, gk, ck, emu=1):
        n[0] += 1
        con.execute("INSERT INTO games(id,canonical_title,norm_key,platform,entry_key,"
                    "base_key,game_key,card_key,has_emulation,sources_summary) "
                    "VALUES(?,?,?,?,?,?,?,?,?,'emulation')",
                    (n[0], nk, nk, plat, "%s@%s" % (nk, plat), nk, gk, ck, emu))
        con.execute("INSERT INTO sources(game_id,source,platform) VALUES(?,'emulation',?)",
                    (n[0], plat))
        con.execute("INSERT INTO metadata_links(game_id,provider) VALUES(?,'igdb')", (n[0],))

    def art(nk, system, gk, sha):
        con.execute("INSERT INTO m.media(norm_key,system,kind,chosen,sha1,game_key) "
                    "VALUES(?,?,'cover',1,?,?)", (nk, system, sha, gk))

    # One game, two consoles. ONLY the Game Boy copy has art.
    game("klax", "gameboy", "igdb:70", "igdb:70")
    game("klax", "atari 2600", "igdb:70", "igdb:70")
    art("klax", "gameboy", "igdb:70", "gbcover0001")
    con.commit()

    res = srv._query_games(con, limit=100)
    check("the two consoles are one card", len(res["items"]) == 1)
    card = res["items"][0]
    check("the card reports a cover", card["has_cover"] is True)
    check("the cover is the Game Boy art", card["cover_v"] == "gbcover0001")
    check("the representative is the entry that owns the art",
          card["entry_key"] == "klax@gameboy")

    # Now a card whose ONLY art belongs to a console nobody on the card owns.
    con.execute("DELETE FROM m.media")
    art("klax", "snes", "igdb:70", "snescover001")
    res2 = srv._query_games(con, limit=100)
    card2 = res2["items"][0]
    check("foreign art does not count as a cover", card2["has_cover"] is False)
    check("and no foreign hash is offered", card2["cover_v"] is None)

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the test**

Run: `./scripts/run_tests.sh test_card_cover_is_own_art`
Expected: PASS if Task 4's `rep_order` is right. If it FAILs on "the representative is the entry that owns the art", the `_has_cover_sql` term is missing from `rep_order`, or the ordered-subquery wrapper in step 3e was not applied.

- [ ] **Step 3: Fix `rep_order` if the test failed**

The only correct fix is to make the representative ordering put a covered entry first. Do not weaken the test and do not add an "any system" fallback to the cover SQL. `_has_cover_sql` is the single definition of "has a cover" (task #28) and must not be bypassed.

- [ ] **Step 4: Run the test to verify it passes**

Run: `./scripts/run_tests.sh test_card_cover_is_own_art`
Expected: `RESULT: 6 checks, all passed`

- [ ] **Step 5: Commit**

```bash
git add server/app.py tests/test_card_cover_is_own_art.py
git commit -m "test(api): a collapsed card still never shows another console's art"
```

---

### Task 6: Keep genuinely different same-name games apart

**Files:**
- Test: `tests/test_card_keeps_games_apart.py`

**Interfaces:**
- Consumes: Tasks 1-4.
- Produces: no new interface. This is the regression gate for the whole feature.

- [ ] **Step 1: Write the test**

Create `tests/test_card_keeps_games_apart.py`:

```python
#!/usr/bin/env python3
"""The collapse must not undo the splits that took weeks to get right.

Three known-hard pairs, each a different mechanism:
  * Portal — a per-entry resolution override (entry_res) gives the 1986 Amiga text
    adventure its own igdb id, apart from Valve's 2007 game.
  * Uno — era separation gives the 1994 Game Boy game a title: key, apart from the
    identified Steam game.
  * Tomb Raider — two Collector's Editions fold onto DIFFERENT roots, 1996 and 2013.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                      # noqa: E402
test_support.isolate()

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "ludodex"))
    import cardkey

    graph = {
        71: (0, None, None),          # Portal (Valve, 2007)
        14546: (0, None, None),       # Portal (1986)
        912: (0, None, None),         # Tomb Raider 1996
        1164: (0, None, None),        # Tomb Raider 2013
        43690: (0, 912, None),        # Collector's Edition 2012
        74555: (0, 1164, None),       # Collector's Edition 2013
    }

    got = cardkey.assign([
        ("portal@pc", "igdb:71", "Portal"),
        ("portal@amiga", "igdb:14546", "Portal"),
        ("uno@pc", "igdb:5555", "UNO"),
        ("uno@gameboy", "title:uno", "Uno"),
        ("tomb raider@psx", "igdb:912", "Tomb Raider"),
        ("tomb raider collectors@pc", "igdb:43690", "Tomb Raider: Collector's Edition"),
        ("tomb raider@ps3", "igdb:1164", "Tomb Raider"),
        ("tomb raider collectors 2013@pc", "igdb:74555",
         "Tomb Raider: Collector's Edition"),
    ], graph)

    check("the two Portals stay apart", got["portal@pc"] != got["portal@amiga"])
    check("Valve's Portal keeps its identity", got["portal@pc"] == "igdb:71")
    check("the 1986 Portal keeps its own", got["portal@amiga"] == "igdb:14546")

    check("the two Unos stay apart", got["uno@pc"] != got["uno@gameboy"])
    check("the era-separated Uno keeps its title key", got["uno@gameboy"] == "title:uno")

    check("the 1996 Collector's Edition joins the 1996 game",
          got["tomb raider collectors@pc"] == got["tomb raider@psx"] == "igdb:912")
    check("the 2013 Collector's Edition joins the 2013 game",
          got["tomb raider collectors 2013@pc"] == got["tomb raider@ps3"] == "igdb:1164")
    check("the two Tomb Raiders stay apart",
          got["tomb raider@psx"] != got["tomb raider@ps3"])

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `./scripts/run_tests.sh test_card_keeps_games_apart`
Expected: `RESULT: 8 checks, all passed`. It should pass on the first run; if it does not, Tasks 1-2 are wrong and must be fixed there, not here.

- [ ] **Step 3: Write the "a fold is not a bind" test**

Create `tests/test_fold_does_not_bind.py`:

```python
#!/usr/bin/env python3
"""A card grouping is a display decision, and must stay one.

Task #21 established the rule this enforces: a MATCH IS NOT AN INGEST. The fold reads a
provider's parent graph to decide which tile a game sits on, which is exactly the kind of
convenience that grows into an identity binding if nobody pins it down. So: the module
does no I/O, and assigning a card never alters a game_key.
"""
import os
import sys

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(root, "ludodex"))
    import cardkey

    src = open(os.path.join(root, "ludodex", "cardkey.py")).read()
    for banned in ("import sqlite3", "import urllib", "import requests",
                   "import http", "import socket", "open("):
        check("cardkey does no I/O: no %r" % banned, banned not in src)
    check("cardkey never imports matchgate", "matchgate" not in src)

    graph = {2155: (0, None, None), 81085: (9, None, 2155)}
    entries = [("dark souls@pc", "igdb:81085", "Dark Souls: Remastered")]
    before = [tuple(e) for e in entries]
    got = cardkey.assign(entries, graph)
    check("assign does not mutate its input", [tuple(e) for e in entries] == before)
    check("the game_key is not rewritten", entries[0][1] == "igdb:81085")
    check("only the card moves", got["dark souls@pc"] == "igdb:2155")

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
```

Run: `./scripts/run_tests.sh test_fold_does_not_bind`
Expected: `RESULT: 10 checks, all passed`

- [ ] **Step 4: Write the "publish still addresses one entry" test**

Create `tests/test_publish_after_collapse.py`:

```python
#!/usr/bin/env python3
"""Publishing targets a platform, and the collapse must not take that away.

A device push copies one platform's files. The grid now shows one card spanning several
platforms, so the thing publish is handed has to keep resolving to exactly ONE entry
row. That is why the card carries its representative's entry_key rather than only a
card key.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                      # noqa: E402
test_support.isolate()

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "ludodex"))

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE games(id INTEGER PRIMARY KEY, canonical_title TEXT, norm_key TEXT,
      platform TEXT, entry_key TEXT, base_key TEXT, game_key TEXT, card_key TEXT);
    INSERT INTO games VALUES(1,'DARK SOULS: REMASTERED','dark souls','pc',
      'dark souls@pc','dark souls','igdb:81085','igdb:2155');
    INSERT INTO games VALUES(2,'DARK SOULS: REMASTERED','dark souls','switch',
      'dark souls@switch','dark souls','igdb:81085','igdb:2155');
    """)

    for ek in ("dark souls@pc", "dark souls@switch"):
        n = con.execute("SELECT COUNT(*) FROM games WHERE entry_key=?", (ek,)).fetchone()[0]
        check("%s addresses exactly one entry" % ek, n == 1)

    plats = [r[0] for r in con.execute(
        "SELECT platform FROM games WHERE card_key='igdb:2155' ORDER BY platform")]
    check("the card still knows both platforms", plats == ["pc", "switch"])
    check("a card key alone does NOT address one entry",
          con.execute("SELECT COUNT(*) FROM games WHERE card_key='igdb:2155'"
                      ).fetchone()[0] == 2)

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
```

Run: `./scripts/run_tests.sh test_publish_after_collapse`
Expected: `RESULT: 4 checks, all passed`

- [ ] **Step 5: Commit**

```bash
git add tests/test_card_keeps_games_apart.py tests/test_fold_does_not_bind.py \
        tests/test_publish_after_collapse.py
git commit -m "test(cards): the splits hold, a fold binds nothing, publish keeps its entry"
```

---

### Task 7: The unfold override

**Files:**
- Create: `ludodex/unfold.py`
- Modify: `ludodex/build_library.py` (pass `unfolded` into the assignment), `server/app.py` (two endpoints)
- Test: `tests/test_unfold_override.py`

**Interfaces:**
- Consumes: `cardkey.assign(..., unfolded=...)` from Task 2.
- Produces:
  - `unfold.ensure(con)`, `unfold.set_unfold(con, entry_key)`, `unfold.clear_unfold(con, entry_key)`, `unfold.load(con) -> set[str]`
  - `POST /api/cards/unfold {entry_key}` and `DELETE /api/cards/unfold/{entry_key}`

- [ ] **Step 1: Write the failing test**

Create `tests/test_unfold_override.py`:

```python
#!/usr/bin/env python3
"""The manual reverse for a fold the user disagrees with.

IGDB's `expanded_game` (type 10) is the loosest link in the fold set, and it is the one
that carries Dark Souls II: Scholar of the First Sin, so it cannot be dropped. It also
pulls in arguable pairs — Bit Blaster XL with Super Bit Blaster XL, Arcade Paradise with
Arcade Paradise VR. The answer is a per-entry pin that a rebuild never overwrites.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                      # noqa: E402
test_support.isolate()

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(root, "ludodex"))
    import cardkey
    import unfold

    con = sqlite3.connect(":memory:")
    unfold.ensure(con)
    check("an empty store unfolds nothing", unfold.load(con) == set())

    unfold.set_unfold(con, "super bit blaster xl@pc")
    check("a pin is stored", unfold.load(con) == {"super bit blaster xl@pc"})
    unfold.set_unfold(con, "super bit blaster xl@pc")
    check("pinning twice is idempotent", len(unfold.load(con)) == 1)

    graph = {33733: (0, None, None), 33734: (10, None, 33733)}
    entries = [("bit blaster xl@pc", "igdb:33733", "Bit Blaster XL"),
               ("super bit blaster xl@pc", "igdb:33734", "Super Bit Blaster XL")]

    folded = cardkey.assign(entries, graph)
    check("without the pin they share a card",
          folded["bit blaster xl@pc"] == folded["super bit blaster xl@pc"])

    pinned = cardkey.assign(entries, graph, unfolded=unfold.load(con))
    check("with the pin they do not",
          pinned["bit blaster xl@pc"] != pinned["super bit blaster xl@pc"])
    check("the pinned entry keeps its own identity",
          pinned["super bit blaster xl@pc"] == "igdb:33734")
    check("the other entry is unaffected", pinned["bit blaster xl@pc"] == "igdb:33733")

    unfold.clear_unfold(con, "super bit blaster xl@pc")
    check("clearing removes the pin", unfold.load(con) == set())
    refolded = cardkey.assign(entries, graph, unfolded=unfold.load(con))
    check("and they share a card again",
          refolded["bit blaster xl@pc"] == refolded["super bit blaster xl@pc"])

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `./scripts/run_tests.sh test_unfold_override`
Expected: FAIL with `ModuleNotFoundError: No module named 'unfold'`

- [ ] **Step 3: Write the implementation**

Create `ludodex/unfold.py`, modelled on `entry_res.py`:

```python
#!/usr/bin/env python3
"""Entries the user has pinned to their own card.

The edition fold reads IGDB's parent graph, and IGDB's `expanded_game` type is loose:
it links Scholar of the First Sin to Dark Souls II, which is right, and Arcade Paradise
VR to Arcade Paradise, which is arguable. A rule that reads a provider will always have
a tail of cases the user disagrees with, so the user gets a reverse.

A pin here is durable and a rebuild never overwrites it, exactly like `entry_res`'s
per-entry resolution. It changes the CARD only: the entry's game_key, its provider link
and its art are untouched.

Lives in metadata-cache.sqlite alongside entry_resolution.
"""
import time


def ensure(con):
    con.execute(
        "CREATE TABLE IF NOT EXISTS card_unfold("
        "entry_key TEXT PRIMARY KEY, pinned_at INTEGER)")


def set_unfold(con, entry_key):
    ensure(con)
    con.execute("INSERT OR REPLACE INTO card_unfold(entry_key,pinned_at) VALUES(?,?)",
                (entry_key, int(time.time())))


def clear_unfold(con, entry_key):
    ensure(con)
    con.execute("DELETE FROM card_unfold WHERE entry_key=?", (entry_key,))


def load(con):
    """{entry_key} for every entry pinned to its own card — for build_library."""
    ensure(con)
    return {r[0] for r in con.execute("SELECT entry_key FROM card_unfold")}
```

In `build_library.py`, add `import unfold` beside `import cardkey`, and load the pins on
the same metadata-cache connection `entry_res.load()` already uses:

```python
_unfolded = unfold.load(_meta_con)
```

Then at each of the three insert sites from Task 3 step 3f, wrap the call:

```python
_ck = (_gk if _ekey in _unfolded
       else cardkey.card_key_for_title(_gk, canonical, _title_index, _fold_graph))
```

where `_ekey` is the `"%s@%s" % (base, plat)` expression already being inserted as
`entry_key`. Bind it to a local so it is computed once.

In `server/app.py`, add the two endpoints. There is no shared metadata-cache helper in
this file; the established pattern is a direct connect, as at line 5199:

```python
@app.post("/api/cards/unfold")
def card_unfold(body: dict = Body(...)):
    """Pin an entry to its own card. Display only — identity, providers and art are
    untouched. Takes effect on the next catalog rebuild, like every other build-time
    decision in this codebase, so the response says so."""
    ekey = (body or {}).get("entry_key")
    if not ekey:
        raise HTTPException(400, "entry_key required")
    con = sqlite3.connect(os.path.join(DATA, "metadata-cache.sqlite"))
    try:
        unfold.set_unfold(con, ekey)
        con.commit()
    finally:
        con.close()
    return {"ok": True, "entry_key": ekey, "rebuild_required": True}


@app.delete("/api/cards/unfold/{entry_key:path}")
def card_refold(entry_key: str):
    con = sqlite3.connect(os.path.join(DATA, "metadata-cache.sqlite"))
    try:
        unfold.clear_unfold(con, entry_key)
        con.commit()
    finally:
        con.close()
    return {"ok": True, "entry_key": entry_key, "rebuild_required": True}
```

`{entry_key:path}` is required on the DELETE route: an entry key contains `@` and may
contain `/` in a norm_key derived from a path-like title.

- [ ] **Step 4: Run the test to verify it passes**

Run: `./scripts/run_tests.sh test_unfold_override`
Expected: `RESULT: 10 checks, all passed`

- [ ] **Step 5: Run the whole suite**

Run: `./scripts/run_tests.sh`
Expected: 0 failures.

- [ ] **Step 6: Commit**

```bash
git add ludodex/unfold.py ludodex/build_library.py server/app.py tests/test_unfold_override.py
git commit -m "feat(cards): a per-entry unfold pin, for the folds IGDB gets wrong"
```

---

### Task 8: Detail returns `copies[]`

**Files:**
- Modify: `server/app.py`, the detail endpoint (roughly lines 8780-8815, the `also_owned_on` block), `_resolve_entry` and `_split_entry_key` (roughly lines 10049-10060)
- Test: `tests/test_card_detail_copies.py`

**Interfaces:**
- Consumes: `card_key` from Task 3.
- Produces:
  - `_card_key_lookup(key) -> str | None`
  - `_edition_label(copy_title, card_title) -> str`
  - `_card_copies(con, card_key, card_title) -> list[dict]`
  - `GET /api/games/{key}` accepting a `card_key`, returning `copies` plus `also_owned_on` as an alias.

Note: `game_detail` opens six ATTACHed database files through `lib()`, so it is not
testable in memory. The copies query therefore lives in `_card_copies`, a helper that
takes a connection, which is what the test drives. No test in this suite calls an
endpoint function directly, and this task does not start.

- [ ] **Step 1: Write the failing test**

Create `tests/test_card_detail_copies.py`:

```python
#!/usr/bin/env python3
"""One card, several copies.

The detail page used to be one platform entry with an `also_owned_on` chip strip
pointing at its siblings. Now the card IS the game, so the siblings are its copies, and
the page has to accept the card key as well as the entry key so old links keep working.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                      # noqa: E402
test_support.isolate()

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "ludodex"))
    from server import app as srv

    # --- the two key shapes cannot collide ---
    check("_split_entry_key still splits an entry key",
          srv._split_entry_key("dark souls@pc") == ("dark souls", "pc"))
    check("an igdb card key is recognised",
          srv._card_key_lookup("igdb:2155") == "igdb:2155")
    check("a title card key is recognised",
          srv._card_key_lookup("title:some rom") == "title:some rom")
    check("an entry key is not a card key",
          srv._card_key_lookup("dark souls@pc") is None)
    check("a bare norm_key is not a card key",
          srv._card_key_lookup("dark souls") is None)

    # --- the edition label ---
    check("the label is what the card title does not carry",
          srv._edition_label("DARK SOULS: REMASTERED", "DARK SOULS") == "REMASTERED")
    check("an exact match has no label",
          srv._edition_label("Dark Souls", "Dark Souls") == "")
    check("an unrelated title has no label",
          srv._edition_label("Mega Man 2", "Dark Souls") == "")
    check("an empty title is safe", srv._edition_label("", "Dark Souls") == "")

    # --- copies ---
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE games(id INTEGER PRIMARY KEY, canonical_title TEXT, norm_key TEXT,
      platform TEXT, entry_key TEXT, base_key TEXT, game_key TEXT, card_key TEXT,
      content_kind TEXT);
    INSERT INTO games VALUES(1,'DARK SOULS: REMASTERED','dark souls','pc',
      'dark souls@pc','dark souls','igdb:81085','igdb:2155',NULL);
    INSERT INTO games VALUES(2,'DARK SOULS: REMASTERED','dark souls','switch',
      'dark souls@switch','dark souls','igdb:81085','igdb:2155',NULL);
    INSERT INTO games VALUES(3,'DARK SOULS: Prepare To Die Edition',
      'dark souls prepare to die','pc','dark souls prepare to die@pc',
      'dark souls prepare to die','title:dark souls prepare to die','igdb:2155',NULL);
    INSERT INTO games VALUES(4,'Dark Souls II','dark souls 2','pc',
      'dark souls 2@pc','dark souls 2','igdb:2368','igdb:2368',NULL);
    """)

    copies = srv._card_copies(con, "igdb:2155", "DARK SOULS")
    check("the card has three copies", len(copies) == 3)
    check("every copy is separately addressable",
          len({c["entry_key"] for c in copies}) == 3)
    check("no copy from another card leaks in",
          all(c["entry_key"] != "dark souls 2@pc" for c in copies))
    plats = sorted(c["platform"] for c in copies)
    check("the copies carry their platforms", plats == ["pc", "pc", "switch"])
    labels = {c["entry_key"]: c["edition"] for c in copies}
    check("the Remastered copies are labelled",
          labels["dark souls@pc"] == "REMASTERED")
    check("the Prepare To Die copy is labelled",
          labels["dark souls prepare to die@pc"] == "Prepare To Die Edition")
    check("copies are ordered deterministically",
          [c["entry_key"] for c in copies]
          == [c["entry_key"] for c in srv._card_copies(con, "igdb:2155", "DARK SOULS")])

    check("a card with one copy still returns a list",
          len(srv._card_copies(con, "igdb:2368", "Dark Souls II")) == 1)
    check("an unknown card returns nothing",
          srv._card_copies(con, "igdb:999999", "Nothing") == [])

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
```

The copy ordering the test pins is `ORDER BY COALESCE(platform,''), id`, so `pc` sorts
before `switch` and the two `pc` copies keep insert order.

- [ ] **Step 2: Run the test to verify it fails**

Run: `./scripts/run_tests.sh test_card_detail_copies`
Expected: FAIL with `AttributeError: module 'server.app' has no attribute '_card_key_lookup'`

- [ ] **Step 3: Write the implementation**

3a. Add a resolver beside `_split_entry_key`:

```python
def _card_key_lookup(key):
    """True when `key` addresses a CARD rather than a platform entry.

    Entry keys are `<norm_key>@<platform>`; card keys are `igdb:<id>` or
    `title:<norm_key>`. The two shapes cannot collide, because a norm_key never contains
    a colon prefix of that form and a card key never contains '@'.
    """
    return key if (key.startswith("igdb:") or key.startswith("title:")) else None
```

3b. Add `_card_copies`, the query the detail endpoint calls. It is a helper rather than
inline code because `game_detail` opens six ATTACHed files through `lib()` and cannot be
driven from a test, while this can:

```python
def _card_copies(con, card_key, card_title):
    """Every owned copy on one card: one row per platform entry, each separately
    addressable so the UI can open it, publish it, or fix it on its own.

    This replaces the `also_owned_on` sibling strip. That strip grouped by `base_key`,
    which answers "same title group"; a card groups by identity, which answers "same
    game", and those differ exactly where an edition was filed under its own title.
    """
    if not _has_col(con, "games", "card_key"):
        return []
    out = []
    for row in con.execute(
            "SELECT entry_key, norm_key, platform, canonical_title, content_kind "
            "FROM games WHERE COALESCE(card_key, entry_key)=? "
            "ORDER BY COALESCE(platform,''), id", (card_key,)):
        out.append({
            "entry_key": row["entry_key"], "norm_key": row["norm_key"],
            "platform": row["platform"], "title": row["canonical_title"],
            # which edition this copy is, so the UI can say "Prepare To Die on PC,
            # Remastered on Switch" instead of listing the same title three times
            "edition": _edition_label(row["canonical_title"], card_title),
        })
    return out
```

In `game_detail`, when `_card_key_lookup(norm_key)` returns a card key, resolve the
representative entry with the same `rep_order` `_query_games` uses, then set
`copies = _card_copies(con, card, g["canonical_title"])`. When it returns None, keep the
existing `_resolve_entry` path and set `copies` from the entry's own `card_key`.

3c. Add `_edition_label(copy_title, card_title)` returning the part of the copy's title
that the card title does not carry, or `""` when they are the same:

```python
def _edition_label(copy_title, card_title):
    """"DARK SOULS: REMASTERED" against a card titled "DARK SOULS" -> "REMASTERED"."""
    if not copy_title or not card_title:
        return ""
    if copy_title.lower().startswith(card_title.lower()):
        rest = copy_title[len(card_title):].lstrip(" :-–")
        return rest
    return ""
```

3d. Return `"copies": copies` and keep `"also_owned_on": copies` as the alias for one
release, with a comment naming the removal release.

- [ ] **Step 4: Run the test to verify it passes**

Run: `./scripts/run_tests.sh test_card_detail_copies`
Expected: `ok    test_card_detail_copies    RESULT: 18 checks, all passed`

- [ ] **Step 5: Run the whole suite**

Run: `./scripts/run_tests.sh`
Expected: 0 failures. `test_detail_back.py` and `test_collection_member_links.py` touch this endpoint.

- [ ] **Step 6: Commit**

```bash
git add server/app.py tests/test_card_detail_copies.py
git commit -m "feat(api): detail addresses a card and returns its copies"
```

---

### Task 9: Spotlight, dashboard and the invariant

**Files:**
- Modify: `server/app.py` (`_spotlight_rows`, roughly line 1319, and the dashboard count queries), `ludodex/check_invariants.py`
- Test: `tests/test_card_counts.py`

**Interfaces:**
- Consumes: Tasks 3-4.
- Produces: invariant `I12` in `check_invariants.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_card_counts.py`:

```python
#!/usr/bin/env python3
"""Every count in the app counts CARDS, and Spotlight never offers one twice.

A collapsed grid with an entry-counting stats card is worse than either, because the
number on the dashboard stops matching the number of tiles below it. Spotlight has the
same defect in a different surface: offering the same game once per system it is on.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                      # noqa: E402
test_support.isolate()

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "ludodex"))
    from server import app as srv

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE games(id INTEGER PRIMARY KEY, canonical_title TEXT, norm_key TEXT,
      platform TEXT, entry_key TEXT, base_key TEXT, game_key TEXT, card_key TEXT,
      n_sources INTEGER DEFAULT 1, n_kinds INTEGER DEFAULT 1, sources_summary TEXT,
      has_emulation INT DEFAULT 0, wanted INT DEFAULT 0, parent_key TEXT,
      content_kind TEXT);
    CREATE TABLE sources(game_id INTEGER, source TEXT, platform TEXT,
                         state TEXT DEFAULT 'have');
    CREATE TABLE metadata_links(game_id INTEGER, provider TEXT);
    CREATE TABLE game_attributes(game_id INTEGER, kind TEXT, value TEXT);
    CREATE TABLE game_tags(game_id INTEGER, origin TEXT, tag TEXT);
    """)
    con.execute("ATTACH DATABASE ':memory:' AS m")
    con.execute("ATTACH DATABASE ':memory:' AS u")
    con.execute("ATTACH DATABASE ':memory:' AS t")
    con.execute("ATTACH DATABASE ':memory:' AS sco")
    con.executescript("""
    CREATE TABLE m.media(norm_key TEXT, system TEXT, kind TEXT, chosen INT,
                         sha1 TEXT, game_key TEXT);
    CREATE TABLE u.user_media(norm_key TEXT, kind TEXT, sha1 TEXT, created INT);
    CREATE TABLE t.user_tags(norm_key TEXT, tag TEXT);
    CREATE TABLE sco.game_scores(norm_key TEXT, universal REAL);
    """)

    n = [0]

    def game(title, nk, plat, gk, ck):
        n[0] += 1
        con.execute("INSERT INTO games(id,canonical_title,norm_key,platform,entry_key,"
                    "base_key,game_key,card_key,sources_summary) "
                    "VALUES(?,?,?,?,?,?,?,?,'steam')",
                    (n[0], title, nk, plat, "%s@%s" % (nk, plat), nk, gk, ck))
        con.execute("INSERT INTO sources(game_id,source,platform) VALUES(?,'steam',?)",
                    (n[0], plat))
        con.execute("INSERT INTO metadata_links(game_id,provider) VALUES(?,'igdb')",
                    (n[0],))

    game("DARK SOULS: REMASTERED", "dark souls", "pc", "igdb:81085", "igdb:2155")
    game("DARK SOULS: REMASTERED", "dark souls", "switch", "igdb:81085", "igdb:2155")
    game("DARK SOULS: Prepare To Die Edition", "dark souls prepare to die", "pc",
         "title:dark souls prepare to die", "igdb:2155")
    game("Dark Souls II", "dark souls 2", "pc", "igdb:2368", "igdb:2368")
    game("Dark Souls II: Scholar of the First Sin",
         "dark souls 2 scholar of the first sin", "pc", "igdb:8222", "igdb:2368")
    game("DARK SOULS III", "dark souls 3", "pc", "igdb:11133", "igdb:11133")
    con.commit()

    res = srv._query_games(con, limit=100)
    check("the grid shows three cards", len(res["items"]) == 3)
    check("the total agrees with the grid", res["total"] == len(res["items"]))

    # The facets endpoint opens its own connection through lib(), so the SQL it now uses
    # is asserted directly. This is the exact expression the implementation must adopt.
    pc = con.execute(
        "SELECT COUNT(DISTINCT COALESCE(g.card_key, g.entry_key)) FROM games g "
        "WHERE EXISTS(SELECT 1 FROM sources s WHERE s.game_id=g.id AND s.platform='pc')"
    ).fetchone()[0]
    check("the pc facet counts cards, not entries", pc == 3)
    sw = con.execute(
        "SELECT COUNT(DISTINCT COALESCE(g.card_key, g.entry_key)) FROM games g "
        "WHERE EXISTS(SELECT 1 FROM sources s WHERE s.game_id=g.id "
        "AND s.platform='switch')").fetchone()[0]
    check("the switch facet counts one card", sw == 1)
    entries_pc = con.execute(
        "SELECT COUNT(*) FROM games g WHERE EXISTS(SELECT 1 FROM sources s "
        "WHERE s.game_id=g.id AND s.platform='pc')").fetchone()[0]
    check("and that is genuinely different from counting entries", entries_pc == 5)

    spot = srv._spotlight_rows(con, [], [], limit=10)
    keys = [(r["card_key"] if "card_key" in r.keys() else r["entry_key"]) for r in spot]
    check("spotlight returns something", len(keys) > 0)
    check("spotlight never offers one card twice", len(set(keys)) == len(keys))
    check("spotlight offers at most one row per card", len(keys) <= 3)

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `./scripts/run_tests.sh test_card_counts`
Expected: FAIL on the spotlight duplicate check, because `_spotlight_rows` still selects per entry.

- [ ] **Step 3: Add the grouping to spotlight and the dashboard**

Apply the same `GROUP BY COALESCE(g.card_key, g.entry_key)` and the same `rep_order`
ordered-subquery wrapper from Task 4 step 3e to `_spotlight_rows` (roughly line 1319).
Add `card_key` to the rows it returns.

In the `facets()` endpoint (`@app.get("/api/facets")`, roughly line 775), change every
per-facet `COUNT(*)` to `COUNT(DISTINCT COALESCE(g.card_key, g.entry_key))`, guarded by
`_has_col(con, "games", "card_key")` so an un-rebuilt catalog counts exactly as it does
today.

Do the same to every dashboard `COUNT(*) FROM games`. Find them with
`grep -n "COUNT(\*) FROM games" server/app.py` and change each under the same guard. A
dashboard number that disagrees with the number of tiles below it is the defect this
step exists to prevent.

- [ ] **Step 4: Run the test to verify it passes**

Run: `./scripts/run_tests.sh test_card_counts`
Expected: `ok    test_card_counts    RESULT: 9 checks, all passed`

- [ ] **Step 5: Add invariant I12**

In `ludodex/check_invariants.py`, add this at the end of `main()`, following the
`report(...)` pattern of I1-I11. The catalog connection in that function is `g`, and the
file has no column guard, so this one declares its own:

```python
    # ---------------------------------------------------------------- I12: cards
    # Every entry belongs to exactly one card. A row whose card_key is NULL vanishes
    # from a GROUPed grid with no error at all, which is the failure mode worth an
    # invariant: silent absence, not a crash. The second half catches a title: card
    # that has swallowed two different identities, which is the one guess the fold
    # design knowingly makes (see the spec's Risks).
    bad = []
    _cols = [r[1] for r in g.execute("PRAGMA table_info(games)")]
    if "card_key" in _cols:
        for r in g.execute("SELECT entry_key FROM games "
                           "WHERE card_key IS NULL OR card_key='' LIMIT 200"):
            bad.append("%s has no card_key" % r["entry_key"])
        for r in g.execute(
                "SELECT card_key, COUNT(DISTINCT game_key) n FROM games "
                "WHERE card_key LIKE 'title:%' GROUP BY card_key "
                "HAVING n > 1 LIMIT 200"):
            bad.append("%s spans %d identities" % (r["card_key"], r["n"]))
        report("I12 every entry belongs to exactly one resolvable card", bad,
               "a row with no card_key disappears from the grid without an error")
    else:
        print("  skipped   I12 cards (catalog predates card_key)")
```

- [ ] **Step 6: Verify the invariant runs clean on a checkout**

Run: `./scripts/run_tests.sh` then
`LUDODEX_DATA=$(mktemp -d) python3 ludodex/check_invariants.py`
Expected: the suite passes with 0 failures, and the invariant check prints `ok        I12 ...` (or exits 0 with every invariant reported).

- [ ] **Step 7: Commit**

```bash
git add server/app.py ludodex/check_invariants.py tests/test_card_counts.py
git commit -m "feat(api): spotlight and every count group by card, plus invariant I12"
```

---

### Task 10: Frontend

**Files:**
- Modify: `web/src/api.ts` (`GameRow` at line 8, `GameDetail` at line 70), `web/src/App.tsx` (the card component at line 486, the table rows at 1434-1460, the detail panel's `alsoOwnedOn` at 7349)
- Test: `pnpm build` plus the repo's `hooksweep.mjs` guard

**Interfaces:**
- Consumes: `card_key` on `GameRow`, `copies` on `GameDetail`.
- Produces: no new interface.

- [ ] **Step 1: Add the types**

In `web/src/api.ts`, add to `GameRow`:

```ts
  card_key?: string        // the library grouping key — one card per GAME, folding
                           // ports, editions and remasters (2026-08-25 design)
```

and to `GameDetail`:

```ts
  copies?: { entry_key: string; norm_key: string; platform: string; title: string
             edition?: string; via?: string }[]
```

Keep `also_owned_on` declared, marked deprecated in a comment.

- [ ] **Step 2: Key the grid on the card**

In `web/src/App.tsx`, replace every `g.entry_key ?? g.norm_key` used as a REACT KEY or a
selection key with `g.card_key ?? g.entry_key ?? g.norm_key`. That is lines 515, 921, 923,
1434, 1459, 1460 and 1550.

**Do not change line 501.** `api.mediaUrl(g.entry_key ?? g.norm_key, ...)` must keep using
the entry key: the card's art is the representative ENTRY's art, and asking for art by a
card key would have no system to gate on.

- [ ] **Step 3: Navigate by card key**

In the `onPick`/`onCard` handlers, pass `g.card_key ?? g.entry_key` to the detail view.
The server accepts both after Task 8.

- [ ] **Step 4: Render copies in the detail panel**

Replace the `alsoOwnedOn` chip strip at line 7349 with a `copies` list, rendering each
copy's platform and its `edition` label when non-empty. Fall back to `also_owned_on` when
`copies` is absent, so the UI works against a server that has not been redeployed.

- [ ] **Step 5: Build**

Run: `cd web && pnpm build`
Expected: build succeeds. `hooksweep.mjs` runs as part of it and fails the build on a
rules-of-hooks violation; if it fires, the fix is in the component, not in the guard.

- [ ] **Step 6: Commit**

```bash
git add web/src/api.ts web/src/App.tsx
git commit -m "feat(web): the grid keys on the card, and detail lists its copies"
```

---

### Task 11: Documentation

**Files:**
- Modify: `README.md` (the "One entry per game *and* platform" section), `docs/DESIGN.md` (§11), `docs/SCHEMA.md`, `docs/TASKS.md`
- Delete and replace: `docs/images/platforms.png`

- [ ] **Step 1: Rewrite the README section**

The section currently titled "One entry per game *and* platform" states the opposite of
what ships. Replace it with a section that says one entry per game, that ports, editions
and remasters fold onto it, and that a remake stays separate. Keep the existing voice: no
em dashes, no emoji, short sentences.

- [ ] **Step 2: Replace the screenshot**

`docs/images/platforms.png` shows the same SteamWorld titles listed once per platform,
which is now the defect rather than the feature. Take a new screenshot of a collapsed card
showing its copies, using the repo's existing screenshot scripts, and replace the file at
the same path so the README reference does not change.

- [ ] **Step 3: Update DESIGN.md §11**

Add a subsection §11.10 describing `card_key`, the fold rule, its stop conditions, the
title rule and the unfold pin. State plainly that §11's per-platform entry model is
unchanged underneath and that `card_key` is a display layer over it.

- [ ] **Step 4: Update SCHEMA.md**

Document the `card_key` column on `games` and the `card_unfold` table in
metadata-cache.sqlite.

- [ ] **Step 5: Update TASKS.md**

Add a `## Shipped 2026-08-25` section titled "one card per game", following the heading
format the file already uses, with the measured before and after numbers from the spec.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/DESIGN.md docs/SCHEMA.md docs/TASKS.md docs/images/platforms.png
git commit -m "docs: one card per game, and the screenshot that shows it"
```

---

## Deployment (maintainer, not the implementer)

The catalog rebuild is the maintainer's to run, from the UI, never from the CLI. After
the code is merged:

1. Build the image on bigboss (clean egress; the Mac's TLS inspection breaks npm and pip
   inside `docker build`).
2. Redeploy with `/boot/config/ludodex-redeploy.sh`, preserving all volumes.
3. Rebuild the catalog from the UI.
4. Run `check_invariants.py` immediately after, and confirm I12 is green.

Before the rebuild, the server serves exactly what it served before, because every new
path is behind a `_has_col(con, "games", "card_key")` guard.
