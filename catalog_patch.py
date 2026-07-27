"""Surgical catalog patches for merge / split — apply a "Fix duplication" (merge) or
"Peel apart" (split) to the LIVE game-library.sqlite for JUST the affected games, instead
of a whole-catalog build_library rebuild.

Correctness contract: after a patch, the affected `games` rows (identity, denormalized
counts/flags, base_key, game_key) + their `sources` grouping must match what a full
build_library rebuild WOULD produce for those same keys. verify_merge.py / the harness in
tests diff a patched copy against a fully-rebuilt copy to guarantee this before deploy.

Scope note: the durable records (merges.sqlite / splits.sqlite) and the per-game user
stores (media/tags/scores/ownership via merges.rekey_user_data) are handled by the caller
BEFORE this runs; this module only re-derives the catalog rows. base_key era-separation
(the rare retro-handheld case) is approximated as base_key = norm_key — an era-separated
merge/split is corrected by the next full rebuild; it never applies to normal duplicates.
"""
import os
import sqlite3


def _load_resolutions(data_dir):
    """(title_ids, entry_ids) from the metadata cache: nk -> igdb_id (title-level) and
    (nk, platform) -> igdb_id (per-entry override). Empty if the cache/tables are absent."""
    title_ids, entry_ids = {}, {}
    cache = os.path.join(data_dir, "metadata-cache.sqlite")
    if not os.path.exists(cache):
        return title_ids, entry_ids
    con = sqlite3.connect(cache)
    try:
        try:
            for nk, iid in con.execute(
                    "SELECT norm_key, igdb_id FROM igdb_resolution WHERE igdb_id>0"):
                title_ids[nk] = iid
        except sqlite3.OperationalError:
            pass
        try:
            for nk, plat, iid in con.execute(
                    "SELECT norm_key, platform, igdb_id FROM entry_resolution WHERE igdb_id>0"):
                entry_ids[(nk, plat)] = iid
        except sqlite3.OperationalError:
            pass
    finally:
        con.close()
    return title_ids, entry_ids


def _game_key(nk, platform, title_ids, entry_ids, blocked=False):
    """Mirror build_library._game_key for a non-era-separated entry: per-entry override
    wins, else the title's igdb id, else the title bucket. `blocked` (homebrew/hack) forces
    the title bucket like build_library's blocked_entries guard."""
    eid = entry_ids.get((nk, platform))
    if eid:
        return "igdb:%d" % eid
    if blocked or nk not in title_ids:
        return "title:%s" % nk
    return "igdb:%d" % title_ids[nk]


def _recompute_denorm(con, gid, nk, platform, title_ids, entry_ids):
    """Recompute a game row's derived columns from its CURRENT sources (after a move),
    mirroring build_library's per-entry write: n_sources/n_kinds/sources_summary/has_*/
    wanted/entry_key/base_key/game_key. Returns False (and leaves the row) if the entry has
    no sources left — the caller deletes it. `blocked` is read from the row's release_type."""
    srcs = con.execute(
        "SELECT source, platform, source_id, title_raw, detail, state "
        "FROM sources WHERE game_id=?", (gid,)).fetchall()
    if not srcs:
        return False
    kinds = {}
    for s in srcs:
        kinds.setdefault(s[0], set())
        if s[0] in ("emulation", "archive"):
            kinds[s[0]].add(s[1])
    parts = []
    for grp in ("emulation", "archive"):
        if grp in kinds:
            parts.append(grp + ":" + ",".join(sorted(x for x in kinds[grp] if x)))
    for st in sorted(k for k in kinds if k not in ("emulation", "archive")):
        parts.append(st)
    summary = "; ".join(parts)
    owned = any(s[5] == "have" for s in srcs)
    blocked = bool(con.execute(
        "SELECT 1 FROM game_attributes WHERE game_id=? AND kind='release_type' "
        "AND value IN ('Homebrew','Hack','Unlicensed') LIMIT 1", (gid,)).fetchone())
    con.execute(
        "UPDATE games SET norm_key=?, platform=?, entry_key=?, base_key=?, game_key=?, "
        "n_sources=?, n_kinds=?, sources_summary=?, has_emulation=?, has_steam=?, "
        "has_gog=?, has_epic=?, has_itch=?, has_archive=?, in_playnite=?, in_launchbox=?, "
        "wanted=? WHERE id=?",
        (nk, platform, "%s@%s" % (nk, platform), nk,
         _game_key(nk, platform, title_ids, entry_ids, blocked),
         len(srcs), len(kinds), summary,
         int("emulation" in kinds), int("steam" in kinds), int("gog" in kinds),
         int("epic" in kinds), int("itch" in kinds), int("archive" in kinds),
         int("playnite" in kinds), int("launchbox" in kinds),
         0 if owned else 1, gid))
    return True


def _delete_entry(con, gid):
    """Remove an emptied game row and its per-game rows."""
    for tbl in ("sources", "game_attributes", "metadata_links", "game_tags",
                "source_attrs", "wanted"):
        try:
            con.execute("DELETE FROM %s WHERE game_id=?" % tbl, (gid,))
        except sqlite3.OperationalError:
            pass
    con.execute("DELETE FROM games WHERE id=?", (gid,))


def _fold_metadata(con, from_gid, to_gid):
    """Fold from_gid's attrs/links/tags/wanted onto to_gid (fill/dedupe — the canonical's
    rows win), mirroring build_library's union of the merged entries' metadata."""
    have_attr = {r[0] for r in con.execute(
        "SELECT DISTINCT kind FROM game_attributes WHERE game_id=?", (to_gid,))}
    for kind, value, origin in con.execute(
            "SELECT kind, value, origin FROM game_attributes WHERE game_id=?", (from_gid,)):
        if kind not in have_attr:
            con.execute("INSERT INTO game_attributes(game_id,kind,value,origin) "
                        "VALUES(?,?,?,?)", (to_gid, kind, value, origin))
    have_link = {r[0] for r in con.execute(
        "SELECT provider FROM metadata_links WHERE game_id=?", (to_gid,))}
    for provider, pid, slug, url in con.execute(
            "SELECT provider, provider_id, slug, url FROM metadata_links WHERE game_id=?",
            (from_gid,)):
        if provider not in have_link:
            con.execute("INSERT INTO metadata_links(game_id,provider,provider_id,slug,url) "
                        "VALUES(?,?,?,?,?)", (to_gid, provider, pid, slug, url))
    have_tag = {r[0] for r in con.execute(
        "SELECT tag FROM game_tags WHERE game_id=?", (to_gid,))}
    for tag, origin in con.execute(
            "SELECT tag, origin FROM game_tags WHERE game_id=?", (from_gid,)):
        if tag not in have_tag:
            con.execute("INSERT INTO game_tags(game_id,tag,origin) VALUES(?,?,?)",
                        (to_gid, tag, origin))


def merge(con, from_key, to_key, to_title, data_dir):
    """Fold from_key's entries into to_key in the live catalog. Per platform: if to_key
    already has that platform's entry, move from_key's sources onto it + fold metadata +
    delete the emptied from_key row; else re-key the from_key row to to_key (adopts to_key's
    identity + title). Recomputes every affected row's denormalized columns."""
    title_ids, entry_ids = _load_resolutions(data_dir)
    to_by_plat = {r[1]: r[0] for r in con.execute(
        "SELECT id, platform FROM games WHERE norm_key=?", (to_key,))}
    from_rows = con.execute(
        "SELECT id, platform FROM games WHERE norm_key=?", (from_key,)).fetchall()
    touched = set()
    for from_gid, plat in from_rows:
        to_gid = to_by_plat.get(plat)
        if to_gid:                              # merge into the existing to_key entry
            con.execute("UPDATE sources SET game_id=? WHERE game_id=?", (to_gid, from_gid))
            try:
                con.execute("UPDATE source_attrs SET game_id=? WHERE game_id=?",
                            (to_gid, from_gid))
            except sqlite3.OperationalError:
                pass
            _fold_metadata(con, from_gid, to_gid)
            _delete_entry(con, from_gid)
            touched.add(to_gid)
        else:                                   # re-key the from_key row onto to_key
            con.execute("UPDATE games SET canonical_title=? WHERE id=? AND NOT EXISTS("
                        "SELECT 1 FROM sources WHERE game_id=? AND source NOT IN "
                        "('emulation','archive'))", (to_title, from_gid, from_gid))
            to_by_plat[plat] = from_gid
            touched.add(from_gid)
    for gid in touched:
        r = con.execute("SELECT norm_key, platform FROM games WHERE id=?", (gid,)).fetchone()
        if r:
            _recompute_denorm(con, gid, to_key, r[1], title_ids, entry_ids)
    con.commit()
    return {"to_key": to_key, "entries": len(touched)}


def split(con, from_key, to_key, to_title, picked, data_dir):
    """Peel `picked` [(source, source_id)] off from_key into a NEW to_key entry (per
    platform). The peeled entry starts unidentified (game_key title:to_key) — the user
    identifies it afterward, like build_library produces it. Recomputes both sides."""
    title_ids, entry_ids = _load_resolutions(data_dir)
    pick = {(s, str(sid)) for s, sid in picked}
    from_rows = con.execute(
        "SELECT id, platform FROM games WHERE norm_key=?", (from_key,)).fetchall()
    new_by_plat = {}
    touched_from = set()
    for from_gid, plat in from_rows:
        moving = [r for r in con.execute(
            "SELECT rowid, source, source_id FROM sources WHERE game_id=?", (from_gid,))
            if (r[1], str(r[2])) in pick]
        if not moving:
            continue
        # target row for the peeled game on this platform (create once)
        to_gid = new_by_plat.get(plat)
        if to_gid is None:
            con.execute(
                "INSERT INTO games(canonical_title,norm_key,platform,entry_key,base_key,"
                "game_key,n_sources,n_kinds,sources_summary,has_emulation,has_steam,has_gog,"
                "has_epic,has_itch,has_archive,in_playnite,in_launchbox,wanted) "
                "VALUES(?,?,?,?,?,?,0,0,'',0,0,0,0,0,0,0,0,0)",
                (to_title, to_key, plat, "%s@%s" % (to_key, plat), to_key,
                 "title:%s" % to_key))
            to_gid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            new_by_plat[plat] = to_gid
        for rowid, _s, _sid in moving:
            con.execute("UPDATE sources SET game_id=? WHERE rowid=?", (to_gid, rowid))
        touched_from.add(from_gid)
    # recompute both the shrunken from_key entries and the new to_key entries
    for gid in touched_from:
        r = con.execute("SELECT platform FROM games WHERE id=?", (gid,)).fetchone()
        if r and not _recompute_denorm(con, gid, from_key, r[0], title_ids, entry_ids):
            _delete_entry(con, gid)             # from entry left with no sources
    for plat, gid in new_by_plat.items():
        _recompute_denorm(con, gid, to_key, plat, title_ids, entry_ids)
    con.commit()
    return {"to_key": to_key, "new_entries": len(new_by_plat)}


def materialize_members(con, data_dir):
    """Insert catalog entries for the members of OWNED collections that have none.

    DESIGN §13's defining property is that recording a collection takes effect
    IMMEDIATELY — credit was computed at read time, so nothing had to be rebuilt. Making
    members real entries put that property at risk: an accept would record 28 collections
    and the library would show none of their games until someone happened to run a full
    build. This is the surgical half, so the property holds.

    Mirrors build_library's pass exactly (same ownership test, same skip rule, same
    platform normalisation) per this module's correctness contract: a patched catalog and
    a rebuilt one must agree. Idempotent — safe to call after every apply.
    """
    import compilations
    from media import norm_system
    if not any(r[1] == "via_collection" for r in con.execute("PRAGMA table_info(sources)")):
        return 0                        # catalog predates the column; a rebuild will do it
    owned = {r[0] for r in con.execute(
        "SELECT DISTINCT g.base_key FROM games g JOIN sources s ON s.game_id=g.id "
        "WHERE s.state='have'")}
    have = {r[0] for r in con.execute("SELECT DISTINCT base_key FROM games")}
    made = 0
    for c in compilations.all_collections(data_dir):
        if c["coll_key"] not in owned:
            continue                    # only an OWNED bundle credits anything
        full = compilations.get_collection(data_dir, c["coll_key"]) or {}
        row = con.execute(
            "SELECT g.platform, (SELECT s.source FROM sources s WHERE s.game_id=g.id "
            "AND s.state='have' LIMIT 1) FROM games g WHERE g.base_key=? LIMIT 1",
            (c["coll_key"],)).fetchone()
        cplat, csrc = (row[0] or "pc", row[1] or "") if row else ("pc", "")
        for m in full.get("members") or []:
            mk = m["member_key"]
            if not mk or mk in have:
                continue                # a real entry wins; it keeps read-time credit
            mplat = norm_system((m.get("member_platform") or cplat) or "pc") or "pc"
            cur = con.execute(
                "INSERT INTO games(canonical_title,norm_key,platform,entry_key,base_key,"
                "game_key,n_sources,n_kinds,sources_summary,has_emulation,has_steam,"
                "has_gog,has_epic,has_itch,has_archive,in_playnite,in_launchbox,wanted) "
                "VALUES(?,?,?,?,?,?,1,0,?,0,0,0,0,0,0,0,0,0)",
                (m["member_title"], mk, mplat, "%s@%s" % (mk, mplat), mk,
                 "title:%s" % mk, "via:" + c["name"]))
            con.execute(
                "INSERT INTO sources(game_id,source,platform,source_id,title_raw,detail,"
                "state,via_collection) VALUES(?,?,?,?,?,?,'have',?)",
                (cur.lastrowid, csrc, mplat, "", m["member_title"], c["name"],
                 c["coll_key"]))
            have.add(mk)
            made += 1
    con.commit()
    return made
