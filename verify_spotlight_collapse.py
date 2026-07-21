#!/usr/bin/env python3
"""Verify Spotlight same-game collapse: one row per resolved identity, cover-preferring
representative, group-max ranking, compilation exclusion. Standalone (no server import)."""
import sqlite3
import sys


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
        (1, 'doom', 'Doom', 'jaguar', 'doom-j@jaguar', 'doom-j', 'igdb:1', 'emulation:jaguar', 1, 1),
        (2, 'doom pc', 'Doom', 'pc', 'doom-pc@pc', 'doom-pc', 'igdb:1', 'steam', 0, 1),
        (3, 'doom snes', 'Doom', 'snes', 'doom-s@snes', 'doom-s', 'igdb:1', 'emulation:snes', 1, 1),
        # Doom II: different id -> stays separate
        (4, 'doom 2', 'Doom II', 'pc', 'doom2@pc', 'doom 2', 'igdb:2', 'steam', 0, 1),
        # two UNIDENTIFIED same-title entries -> must NOT merge (grouped by base_key)
        (5, 'mystery', 'Mystery', 'nes', 'm@nes', 'mystery', 'title:mystery', 'emulation:nes', 1, 1),
        (6, 'mystery', 'Mystery', 'snes', 'm@snes', 'mystery\x1fsnes', 'title:mystery', 'emulation:snes', 1, 1),
        # a recorded compilation (excluded by default)
        (7, 'doom bundle', 'DOOM + DOOM II', 'pc', 'db@pc', 'doom bundle', 'igdb:9', 'steam', 0, 1),
    ]
    con.executemany(
        "INSERT INTO games(id,norm_key,canonical_title,platform,entry_key,"
        "base_key,game_key,sources_summary,has_emulation,n_sources) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
    con.executemany("INSERT INTO game_scores VALUES(?,?,?,?)", [
        ('doom', 89, 88, 90), ('doom pc', 89, 88, 90), ('doom snes', 85, 84, 86),
        ('doom 2', 88, 87, 89), ('mystery', 70, None, 70), ('doom bundle', 89, 88, 90)])
    # ONLY the lower-scored SNES port has art — an OWN-CONSOLE cover (system=snes). The
    # jaguar/pc ports have no cover (no neutral game_key art exists), so the representative
    # must prefer the cover-bearing SNES port DESPITE its lower score.
    con.execute("INSERT INTO media VALUES('doom snes','igdb:1','snes','cover',1,'abc123def456')")
    con.commit()
    return con


# Mirror the query _spotlight_rows will build (grouping + representative + count).
def collapse(con, where="", args=(), order="sc_universal DESC", limit=10, coll_exclude=()):
    clauses = [where] if where else []
    a = list(args)
    if coll_exclude:
        clauses.append("g.base_key NOT IN (%s)" % ",".join("?" * len(coll_exclude)))
        a += list(coll_exclude)
    clause = ("WHERE " + " AND ".join("(%s)" % c for c in clauses) + " ") if clauses else ""
    grpkey = "(CASE WHEN g.game_key LIKE 'igdb:%' THEN g.game_key ELSE g.base_key END)"
    # mirror the two main has_cover paths from _spotlight_rows: OWN-CONSOLE art
    # (norm_key + system=platform) OR platform-NEUTRAL art shared by game_key.
    hascov = (
        "(EXISTS(SELECT 1 FROM media md WHERE md.norm_key=g.norm_key AND md.chosen=1 "
        "AND md.kind='cover' AND COALESCE(md.system,'')=COALESCE(g.platform,'')) "
        "OR EXISTS(SELECT 1 FROM media md WHERE md.game_key=g.game_key AND md.chosen=1 "
        "AND md.kind='cover' AND COALESCE(md.system,'')=''))")
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
    return [dict(zip(('norm_key', 'title', 'has_cover', 'n_platforms'), r))
            for r in con.execute(sql, a + [limit])]


def main():
    con = build()
    all_rows = collapse(con, coll_exclude=('doom bundle',))
    titles = [r['title'] for r in all_rows]
    doom = next(r for r in all_rows if r['title'] == 'Doom')
    assert titles.count('Doom') == 1, f"Doom must collapse to 1 row, got {titles}"
    assert doom['n_platforms'] == 3, f"Doom should span 3, got {doom['n_platforms']}"
    assert doom['has_cover'] == 1, "representative must be the cover-bearing member"
    assert doom['norm_key'] == 'doom snes', \
        f"rep should be the cover member despite lower score, got {doom['norm_key']}"
    assert 'Doom II' in titles, "Doom II (different id) must stay separate"
    assert titles.count('Mystery') == 2, "unidentified same-title must NOT merge"
    assert 'DOOM + DOOM II' not in titles, "recorded compilation excluded by default"
    incl = [r['title'] for r in collapse(con)]  # no exclusion
    assert 'DOOM + DOOM II' in incl, "compilation returns when included"
    print("verify_spotlight_collapse: OK")


if __name__ == "__main__":
    main()
