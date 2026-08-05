#!/usr/bin/env python3
"""`metadata_links` derived from the identity cache — the ONE place a provider match
becomes a visible link.

`provider_ids` is deliberately only a cache: it records that ScreenScraper says this
title is 4321 and stops there. Something still has to turn that into the `metadata_links`
row the detail page renders, and until now that something was three unrelated pieces of
code, each with a different idea of the answer:

  * `_match_providers` wrote a row per provider it had just resolved — correct, but only
    for the games that pass happened to touch;
  * `build_library.py` REBUILT the whole table from its own inputs, writing IGDB from
    `igdb_resolution`, ScreenScraper only where a cached SS payload happened to exist,
    and SteamGridDB not at all;
  * the wand wrote its own rows for the game it touched.

So a rebuild silently deleted matches nothing else could restore. Measured live on
2026-08-04, on a rebuild that changed nothing else: steamgriddb 2255 -> 0, screenscraper
1807 -> 152. And the tiered ingest runs `build_library.py` again after the media phase,
so a match recorded early in an ingest was destroyed later in the same job.

This module is the fix and the whole fix: `sync()` writes the links FROM the identity
cache, so the rows are a pure function of what the providers actually said. Run it after
a rebuild and the links come back; run it twice and nothing changes.

IGDB is deliberately NOT here. Its link carries build_library's own judgement — bundle
refusal, blocked homebrew, rename-on-match, the entry-level identity split — none of
which is expressible as "the cache said N". Owning it here would mean re-deriving that
judgement a second time, which is the exact mistake this module exists to end.
"""
import os
import sqlite3

# provider -> page URL template. Adding a provider to `provider_ids.PROVIDERS` without
# adding it here would record identities that never become visible, so
# `test_links_survive_rebuild.py` asserts this covers that set.
PAGE_URL = {
    "screenscraper": "https://www.screenscraper.fr/gameinfos.php?gameid=%s",
    "steamgriddb": "https://www.steamgriddb.com/game/%s",
}


def page_url(provider, provider_id):
    """The provider's public page for a match, or None if it has no derivable page.

    Same rule the server's `_provider_page_url` applies — it delegates here for these
    providers rather than keeping a second copy of the templates.
    """
    t = PAGE_URL.get((provider or "").lower())
    return t % provider_id if t and str(provider_id or "").isdigit() else None


def sync(lib_con, cache_path, blocked_gids=(), only=None):
    """Rewrite `metadata_links` for every provider in PAGE_URL from the identity cache.

    `lib_con` is an open game-library connection; `cache_path` is metadata-cache.sqlite
    (opened read-only here, so this is safe to call while the server holds it).
    `blocked_gids` are games a provider match must never be attached to — homebrew,
    hacks and unlicensed dumps, which build_library already identifies and which are NOT
    the game the provider matched. `only` scopes to a set of norm_keys.

    Per provider the rows are replaced wholesale for the games it touches, so a
    corrected match cannot leave the old link behind. A recorded MISS (id <= 0) is an
    absence of a decision, never a link. Returns {provider: rows written}.
    """
    import provider_ids                       # local: keeps this importable standalone

    if not os.path.exists(cache_path):
        return {}
    cc = sqlite3.connect("file:%s?mode=ro" % cache_path, uri=True)
    blocked = set(blocked_gids or ())
    want = set(only) if only is not None else None
    out = {}
    try:
        gids = {}
        for gid, nk in lib_con.execute("SELECT id, norm_key FROM games"):
            if want is None or nk in want:
                gids.setdefault(nk, []).append(gid)

        for provider in PAGE_URL:
            table, idcol = provider_ids.PROVIDERS[provider]
            try:
                rows = cc.execute("SELECT norm_key, %s FROM %s WHERE COALESCE(%s,0)>0"
                                  % (idcol, table, idcol)).fetchall()
            except sqlite3.OperationalError:
                continue                       # provider never ran on this install
            # AUTHORITATIVE, not incremental. Clearing only the games we are about to
            # write leaves a link behind whenever an identity DISAPPEARS — a re-match
            # that correctly resolves to nothing, a detach, a provider retiring a record.
            # Live, `dune awakening` kept ScreenScraper 12706 (Dune: Imperium) after the
            # re-match had decided SS does not have it, because a game with no identity
            # was never visited. A link is a claim about an identity; with no identity
            # there is no claim, so the provider's rows are cleared across the whole
            # scope first and rebuilt from what the cache actually holds.
            if want is None:
                lib_con.execute("DELETE FROM metadata_links WHERE provider=?",
                                (provider,))
            else:
                for _nk in want:
                    for _gid in gids.get(_nk, ()):
                        lib_con.execute("DELETE FROM metadata_links WHERE game_id=? "
                                        "AND provider=?", (_gid, provider))
            n = 0
            for nk, pid in rows:
                for gid in gids.get(nk, ()):
                    if gid in blocked:
                        continue
                    lib_con.execute("DELETE FROM metadata_links WHERE game_id=? AND "
                                    "provider=?", (gid, provider))
                    lib_con.execute(
                        "INSERT INTO metadata_links(game_id,provider,provider_id,slug,"
                        "url) VALUES(?,?,?,?,?)",
                        (gid, provider, str(pid), None, page_url(provider, pid)))
                    n += 1
            out[provider] = n

        # IGDB, FILL ONLY. build_library writes the IGDB link itself, from
        # `igdb_resolution JOIN igdb_meta`, because that link carries judgement this
        # module has no business re-deriving (bundle refusal, rename-on-match). But the
        # join is also why a resolved game with no CACHED PAYLOAD rebuilt with no link:
        # the collection-member path records an identity without fetching the payload,
        # and live that silently cost 31 titles — the entire SSI gold-box run — which is
        # the "why no IGDB link on collection members?" report.
        #
        # So: never overwrite a link build_library wrote, but never let a recorded
        # identity render as "unmatched" either. An identity is a link.
        try:
            ig = cc.execute("SELECT norm_key, igdb_id, slug FROM igdb_resolution "
                            "WHERE COALESCE(igdb_id,0)>0").fetchall()
        except sqlite3.OperationalError:
            ig = []
        have = {r[0] for r in lib_con.execute(
            "SELECT DISTINCT game_id FROM metadata_links WHERE provider='igdb'")}
        n = 0
        for nk, iid, slug in ig:
            for gid in gids.get(nk, ()):
                if gid in blocked or gid in have:
                    continue
                lib_con.execute(
                    "INSERT INTO metadata_links(game_id,provider,provider_id,slug,url) "
                    "VALUES(?,?,?,?,?)",
                    (gid, "igdb", str(iid), slug,
                     "https://www.igdb.com/games/%s" % (slug or iid)))
                have.add(gid)
                n += 1
        if n:
            out["igdb"] = n
    finally:
        cc.close()
    return out
