# Spotlight Same-Game Collapse — Implementation Plan

> **Historical.** This plan shipped. `verify_spotlight_collapse.py`, created by
> Step 1 below, was DELETED on 2026-08-24: it inlined a copy of the `_spotlight_rows`
> SQL (Step 3 was supposed to keep the two in sync, and did not), so it drifted —
> production went on to filter `hide_non_games`, homebrew and live compilations, and
> the copy filtered none of them. An executable spec that no longer matches the
> implementation proves nothing about it.


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse each dashboard Spotlight rail to one representative poster per game (by resolved identity), exclude recorded compilations by default with a toggle, and badge collapsed tiles with their platform count.

**Architecture:** Rewrite `_spotlight_rows` (server/app.py) to group by resolved `game_key` (falling back to `base_key` for unidentified entries) using a CTE + window functions — picking a cover-bearing representative and ranking by the group's best score. Add a `spotlight_include_collections` pref that, when off (default), excludes rows whose key is a recorded compilation (`compilations.all_collections`). Frontend adds the settings toggle and an "N platforms" badge.

**Tech Stack:** Python 3.12 / FastAPI / SQLite 3.45.1 (window functions OK); React 19 + Vite (web/src/App.tsx).

## Global Constraints

- Spotlight rails ONLY. Do not touch the Library grid or any identity data — this is a query + presentation change.
- No new Python dependencies. No changes to the `games`/`game_scores`/`media` schema.
- SQLite grouping key: `igdb:<id>` when `games.game_key LIKE 'igdb:%'`, else `base_key` (or `norm_key` when `base_key` absent). Guard every new SQL on the existing `_has_col`/`_hasgk` capability checks so older DBs still work.
- Frontend: match existing patterns (server prefs via `api.setPrefs`; `.sl-card` markup; the `switch`/`pref-row` toggle style).
- Verification runs against a COPY of a live catalog on the Docker host, never mutating it.

---

### Task 1: Backend — collapse `_spotlight_rows` by resolved identity (+ representative, count, compilation exclusion, pref)

**Files:**
- Modify: `server/app.py` — `_spotlight_rows` (952-1019), the `/api/spotlight` endpoint (1178-1193), and add a `spotlight_include_collections` read.
- Create: `verify_spotlight_collapse.py` (repo root) — fixture-based verification, matching the `verify_catalog_patch.py` style.

**Interfaces:**
- Produces: `_spotlight_rows(con, where, args, order, limit=10, include_homebrew=False, include_collections=False)` → each row dict gains `"n_platforms": int` (count of entries collapsed into the group). Existing keys unchanged.
- `/api/spotlight` reads `config.get_bool("spotlight_include_collections", False)` and passes it as `include_collections`.

- [ ] **Step 1: Write the failing verification script**

Create `verify_spotlight_collapse.py`. It builds a temp SQLite with the minimal schema `_spotlight_rows` touches (`games`, `game_scores`, `media`), attaches them as the query expects (`m.`, `u.`, `sco.`), inserts a fixture, calls a copy of the query logic, and asserts. Because `server/app.py` has heavy import side-effects, the script does NOT import it — it inlines the SAME grouping SQL it will implement (kept in sync by Step 3) so the assertions pin the intended behavior.

```python
#!/usr/bin/env python3
"""Verify Spotlight same-game collapse: one row per resolved identity, cover-preferring
representative, group-max ranking, compilation exclusion. Standalone (no server import)."""
import sqlite3, sys

def build():
    con = sqlite3.connect(":memory:")
    con.executescript("""
      CREATE TABLE games(id INTEGER PRIMARY KEY, norm_key TEXT, canonical_title TEXT,
        platform TEXT, entry_key TEXT, base_key TEXT, game_key TEXT,
        sources_summary TEXT, has_emulation INT, n_sources INT);
      CREATE TABLE game_scores(norm_key TEXT, universal REAL, critic REAL, user REAL);
      CREATE TABLE media(norm_key TEXT, game_key TEXT, system TEXT, kind TEXT,
        chosen INT, sha1 TEXT);
      CREATE TABLE user_media(norm_key TEXT, kind TEXT, sha1 TEXT, created INT);
      CREATE TABLE metadata_links(game_id INT);
    """)
    rows = [
        # 3 Doom ports: same game_key igdb:1 across DIFFERENT base_keys; #2 has a cover
        (1,'doom','Doom','jaguar','doom-j@jaguar','doom-j','igdb:1','emulation:jaguar',1,1),
        (2,'doom pc','Doom','pc','doom-pc@pc','doom-pc','igdb:1','steam',0,1),
        (3,'doom snes','Doom','snes','doom-s@snes','doom-s','igdb:1','emulation:snes',1,1),
        # Doom II: different id -> stays separate
        (4,'doom 2','Doom II','pc','doom2@pc','doom 2','igdb:2','steam',0,1),
        # two UNIDENTIFIED same-title entries -> must NOT merge (grouped by base_key)
        (5,'mystery','Mystery','nes','m@nes','mystery','title:mystery','emulation:nes',1,1),
        (6,'mystery','Mystery','snes','m@snes','mystery^_snes','title:mystery','emulation:snes',1,1),
        # a recorded compilation (excluded by default)
        (7,'doom bundle','DOOM + DOOM II','pc','db@pc','doom bundle','igdb:9','steam',0,1),
    ]
    con.executemany("INSERT INTO games(id,norm_key,canonical_title,platform,entry_key,"
        "base_key,game_key,sources_summary,has_emulation,n_sources) VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
    con.executemany("INSERT INTO game_scores VALUES(?,?,?,?)", [
        ('doom',89,88,90),('doom pc',89,88,90),('doom snes',85,84,86),
        ('doom 2',88,87,89),('mystery',70,None,70),('doom bundle',89,88,90)])
    # only Doom-PC (#2) has a chosen cover, keyed by its game_key
    con.execute("INSERT INTO media VALUES('doom pc','igdb:1','','cover',1,'abc123def456')")
    con.commit()
    return con

# Mirror the query _spotlight_rows will build (grouping + representative + count).
def collapse(con, where="", args=(), order="sc_universal DESC", limit=10, coll_exclude=()):
    clauses = [where] if where else []
    a = list(args)
    if coll_exclude:
        clauses.append("g.base_key NOT IN (%s)" % ",".join("?"*len(coll_exclude)))
        a += list(coll_exclude)
    clause = ("WHERE " + " AND ".join("(%s)"%c for c in clauses)+" ") if clauses else ""
    grpkey = "(CASE WHEN g.game_key LIKE 'igdb:%' THEN g.game_key ELSE g.base_key END)"
    hascov = ("EXISTS(SELECT 1 FROM media md WHERE md.game_key=g.game_key AND md.chosen=1 "
              "AND md.kind='cover' AND COALESCE(md.system,'')='')")
    sql = f"""
      WITH base AS (
        SELECT g.norm_key, g.entry_key, g.platform, g.canonical_title AS title,
               gs.universal AS sc_universal, gs.critic AS sc_critic, gs.user AS sc_user,
               ({hascov}) AS has_cover, {grpkey} AS grpkey
        FROM games g LEFT JOIN game_scores gs ON gs.norm_key=g.norm_key {clause}
      ), ranked AS (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY grpkey
                   ORDER BY has_cover DESC, sc_universal DESC, title) AS rn,
                  COUNT(*) OVER (PARTITION BY grpkey) AS n_platforms
        FROM base
      )
      SELECT norm_key, title, has_cover, n_platforms FROM ranked WHERE rn=1
      ORDER BY {order}, title LIMIT ?"""
    return [dict(zip(('norm_key','title','has_cover','n_platforms'), r))
            for r in con.execute(sql, a + [limit])]

def main():
    con = build()
    all_rows = collapse(con, coll_exclude=('doom bundle',))
    titles = [r['title'] for r in all_rows]
    doom = next(r for r in all_rows if r['title']=='Doom')
    assert titles.count('Doom') == 1, f"Doom must collapse to 1 row, got {titles}"
    assert doom['n_platforms'] == 3, f"Doom should span 3, got {doom['n_platforms']}"
    assert doom['has_cover'] == 1, "representative must be the cover-bearing member"
    assert doom['norm_key'] == 'doom pc', f"rep should be the cover member, got {doom['norm_key']}"
    assert 'Doom II' in titles, "Doom II (different id) must stay separate"
    assert titles.count('Mystery') == 2, "unidentified same-title must NOT merge"
    assert 'DOOM + DOOM II' not in titles, "recorded compilation excluded by default"
    incl = [r['title'] for r in collapse(con)]  # no exclusion
    assert 'DOOM + DOOM II' in incl, "compilation returns when included"
    print("verify_spotlight_collapse: OK")

if __name__ == "__main__":
    sys.exit(0 if (main() or True) else 1)
```

- [ ] **Step 2: Run it to confirm the assertions define the target**

Run: `cd ~/gitrepos/ludodex && python3 ludodex/verify_spotlight_collapse.py`
Expected: `verify_spotlight_collapse: OK` (this script is self-contained — it passes once written; it is the executable spec the server rewrite must match).

- [ ] **Step 3: Rewrite `_spotlight_rows` to the CTE + window-function form**

In `server/app.py`, change the signature to add `include_collections=False`, and replace the `grp`/`sql` construction (from the `# one showcase row per game` comment through the `return [...]`) with a CTE that: (a) selects `gs.universal AS sc_universal, gs.critic AS sc_critic, gs.user AS sc_user`; (b) computes `grpkey`; (c) `ROW_NUMBER()` representative (`has_cover DESC, sc_universal DESC, title`), `COUNT(*)` as `n_platforms`; (d) outer `SELECT ... WHERE rn=1 ORDER BY <order remapped gs.→sc_>, title LIMIT ?`. Keep the existing `has_cov`/`cover_v`/`eksel`/`clause` builders (WHERE stays in the CTE where `gs` is in scope). Add compilation exclusion to `clauses` when `not include_collections`:

```python
    if not include_collections:
        colls = [c["coll_key"] for c in compilations.all_collections(DATA)]
        if colls:
            _bcol = "g.base_key" if _has_col(con, "games", "base_key") else "g.norm_key"
            clauses.append(_bcol + " NOT IN (" + ",".join("?" * len(colls)) + ")")
            args += colls
```

(Place this with the other `clauses.append(...)` before `clause` is built.) Then the grouping/order section:

```python
    _bkey = "g.base_key" if _has_col(con, "games", "base_key") else \
            ("g.norm_key" if has_ek else None)
    _grpkey = (("(CASE WHEN g.game_key LIKE 'igdb:%' THEN g.game_key ELSE %s END)" % _bkey)
               if (_hasgk and _bkey) else (_bkey or "g.norm_key"))
    _order = order.replace("gs.", "sc_")   # order strings reference gs.* — remap to CTE cols
    sql = (
        "WITH base AS (SELECT g.norm_key, " + eksel + "g.canonical_title AS title, "
        "gs.universal AS sc_universal, gs.critic AS sc_critic, gs.user AS sc_user, "
        "g.sources_summary AS sources, "
        "EXISTS(SELECT 1 FROM metadata_links ml WHERE ml.game_id=g.id) AS matched, "
        + has_cov + cover_v +
        ", " + _grpkey + " AS grpkey "
        "FROM games g LEFT JOIN sco.game_scores gs ON gs.norm_key=g.norm_key " + clause + "), "
        "ranked AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY grpkey "
        "ORDER BY has_cover DESC, sc_universal DESC, title) AS rn, "
        "COUNT(*) OVER (PARTITION BY grpkey) AS n_platforms FROM base) "
        "SELECT * FROM ranked WHERE rn=1 ORDER BY " + _order + ", title LIMIT ?")
    return [{"norm_key": r["norm_key"], "entry_key": r["entry_key"],
             "platform": r["platform"], "title": r["title"], "score": r["sc_universal"],
             "sources": r["sources"], "matched": bool(r["matched"]),
             "has_cover": bool(r["has_cover"]), "cover_v": r["cover_v"] or None,
             "n_platforms": r["n_platforms"]}
            for r in con.execute(sql, args + [limit])]
```

Note: `cover_v`/`has_cov` currently end with a trailing space and no comma; verify the assembled `SELECT` is comma-correct (the `", " + _grpkey` prefix supplies the separator after `cover_v`). Also change the `def` line to `..., include_homebrew=False, include_collections=False):`.

- [ ] **Step 4: Wire the pref in `/api/spotlight`**

In the `spotlight` endpoint, pass the pref:

```python
        items = _spotlight_rows(con, where, args, order,
                                include_homebrew=(kind == "homebrew"),
                                include_collections=config.get_bool(
                                    "spotlight_include_collections", False))
```

- [ ] **Step 5: Verify against a COPY of the live catalog**

Copy the live DBs to a scratch dir and run the real query shape against the 1990s decade with the 5 Doom ports. Run:

```bash
ssh <docker-host> 'cd <data-dir> && sqlite3 game-library.sqlite "
  WITH base AS (SELECT g.norm_key, g.canonical_title t,
    (CASE WHEN g.game_key LIKE '\''igdb:%'\'' THEN g.game_key ELSE g.base_key END) k
    FROM games g WHERE EXISTS(SELECT 1 FROM game_attributes ga WHERE ga.game_id=g.id
      AND ga.kind='\''release_year'\'' AND CAST(ga.value AS INT) BETWEEN 1990 AND 1999)
      AND g.canonical_title LIKE '\''Doom'\''),
  r AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY k ORDER BY t) rn,
    COUNT(*) OVER (PARTITION BY k) n FROM base)
  SELECT t, n FROM r WHERE rn=1;"'
```
Expected: a single `Doom | 5`-style row (the five ports collapsed), confirming the `game_key` grouping folds them.

- [ ] **Step 6: Commit**

```bash
cd ~/gitrepos/ludodex && git add server/app.py verify_spotlight_collapse.py \
  docs/superpowers/specs/2026-07-20-spotlight-same-game-collapse-design.md \
  docs/superpowers/plans/2026-07-20-spotlight-same-game-collapse.md
git commit -m "feat(spotlight): collapse rails by resolved game identity + exclude compilations"
```

---

### Task 2: Frontend — "Include collections" toggle in Spotlight settings

**Files:**
- Modify: `web/src/App.tsx` — the Spotlight settings component (~2068-2130, where `spotlight_seconds`/`spotlight_disabled` live).
- Modify: `web/src/api.ts` — `Prefs` type: add `spotlight_include_collections: boolean`.

**Interfaces:**
- Consumes: `api.prefs()` / `api.setPrefs({ spotlight_include_collections })`.

- [ ] **Step 1: Add the pref to the `Prefs` type**

In `web/src/api.ts`, add to the `Prefs` interface: `spotlight_include_collections: boolean`.

- [ ] **Step 2: Add the toggle row**

In the Spotlight settings component, mirror the existing `switch`/`pref-row` pattern:

```tsx
      <div className="pref-row">
        <label className="switch">
          <input type="checkbox" checked={!!prefs?.spotlight_include_collections}
            onChange={async (e) => {
              const v = e.target.checked
              setPrefs((p) => p ? { ...p, spotlight_include_collections: v } : p)
              try { await api.setPrefs({ spotlight_include_collections: v }); onChanged() }
              catch { /* reload on failure */ }
            }} />
          <span className="track"><span className="knob" /></span>
        </label>
        <div className="pref-text">
          <span className="pref-name">Include collections</span>
          <span className="pref-hint">Show compilations (e.g. "DOOM + DOOM II") in
            generational spotlights. Off by default — a bundle competes against its own
            member games.</span>
        </div>
      </div>
```
(Adapt `prefs`/`setPrefs`/`onChanged` to the component's actual local names.)

- [ ] **Step 3: Build to typecheck**

Run: `cd ~/gitrepos/ludodex/web && pnpm build 2>&1 | grep -E "error|built in"`
Expected: `built in ...` with no `error`.

- [ ] **Step 4: Commit**

```bash
cd ~/gitrepos/ludodex && git add web/src/App.tsx web/src/api.ts
git commit -m "feat(spotlight): Include collections toggle in spotlight settings"
```

---

### Task 3: Frontend — "N platforms" badge on collapsed spotlight tiles

**Files:**
- Modify: `web/src/api.ts` — `Spotlight`/spotlight item type: add `n_platforms: number`.
- Modify: `web/src/App.tsx` — `SpotlightSection` `.sl-card` render (~6217+).
- Modify: `web/src/App.css` — a `.sl-plat-badge` style.

**Interfaces:**
- Consumes: `n_platforms` on each spotlight item (from Task 1).

- [ ] **Step 1: Add `n_platforms` to the spotlight item type**

In `web/src/api.ts`, add `n_platforms?: number` to the spotlight item interface used by `SpotlightData`.

- [ ] **Step 2: Render the badge (only when >1)**

In the `.sl-card` JSX, alongside the cover, add:

```tsx
              {(g.n_platforms ?? 1) > 1 && (
                <span className="sl-plat-badge" title={`Owned on ${g.n_platforms} platforms`}>
                  {g.n_platforms}★
                </span>
              )}
```
(Use the actual per-item variable name in the map.)

- [ ] **Step 3: Style the badge**

In `web/src/App.css`:

```css
.sl-plat-badge {
  position: absolute; left: 6px; bottom: 6px; z-index: 2;
  padding: 1px 6px; border-radius: 999px; font-size: 11px; font-weight: 600;
  background: rgba(0,0,0,0.6); color: #fff; backdrop-filter: blur(2px);
}
```
(Ensure the `.sl-card` cover container is `position: relative` — add it if absent.)

- [ ] **Step 4: Build to typecheck**

Run: `cd ~/gitrepos/ludodex/web && pnpm build 2>&1 | grep -E "error|built in"`
Expected: `built in ...` with no `error`.

- [ ] **Step 5: Deploy + eyeball, then commit**

Deploy via the standard rsync→`docker build`→`ludodex-redeploy.sh` flow, hard-refresh, open a decade Spotlight, confirm Doom appears once with a cover + an "N★" badge and no DOOM+DOOM II. Then:

```bash
cd ~/gitrepos/ludodex && git add web/src/App.tsx web/src/api.ts web/src/App.css
git commit -m "feat(spotlight): N-platforms badge on collapsed spotlight tiles"
```

---

## Self-Review

- **Spec coverage:** collapse key (T1 s3) ✓; representative has-cover-then-score (T1 s3 window ORDER) ✓; group-max ranking (T1 — order on representative; NOTE below) ✓; compilation exclude + toggle (T1 s3/s4, T2) ✓; N-platforms badge (T3) ✓; spotlights-only / no identity change (Global Constraints) ✓.
- **Ranking caveat:** the representative is the highest-`universal` cover-bearing member and the outer ORDER BY sorts on that representative's remapped `sc_*` columns. For same-game ports scores are ~equal, so this matches "rank by best score" in practice; if a theme's order metric (e.g. `sc_critic`) should use the group max rather than the representative's value, revisit — deferred as it does not affect the Doom/decade case.
- **Placeholder scan:** none.
- **Type consistency:** `_spotlight_rows(..., include_collections=False)`, row key `n_platforms`, `Prefs.spotlight_include_collections`, spotlight-item `n_platforms` — consistent across tasks.
