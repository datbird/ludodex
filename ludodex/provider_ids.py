#!/usr/bin/env python3
"""Provider identity, recorded on its own — a MATCH IS NOT AN INGEST.

datbird's decision (2026-08-01): every configured provider is matched for every game,
whether or not any metadata or media is ever taken from it. The match is what makes a
later on-demand pull possible, and it is what the Matched-providers menu shows.

Both providers already HAD a working matcher and both threw the answer away:
ScreenScraper's ran inside `_pull_ss_media`, so an id existed only as a side effect of
pulling art; SteamGridDB's ran inside `fetch_steamgriddb_targets`, whose work list skips
any game that already has a cover/hero/logo — so a game with IGDB art never got an id at
all. Live before this: SS 151/2255 linked, SGDB 0.

This module is the identity CACHE and nothing else. It does not know how to search — the
caller injects the searcher — and it does not write `metadata_links`, because building a
provider page URL already has exactly one home (`_provider_page_url`) and this is not
going to become a second one. Same discipline that the rest of today's fixes were about:
one derivation per fact.

Semantics follow the #25 lesson. A real id (>0) is a decision. `matched_by='manual'` is a
decision, including a deliberate "this matches nothing". A recorded MISS is the ABSENCE
of a decision — cached so a sweep doesn't re-search 2000 titles every run, but it goes
stale, so a later and better-informed pass tries again. A miss must never become
permanent by being written down.
"""
import sqlite3
import sys
import time

# provider -> (table, id column). Adding one here is all a new provider needs from this
# layer; anything not listed is refused rather than silently dropped on the floor.
PROVIDERS = {
    "igdb": ("igdb_resolution", "igdb_id"),
    "screenscraper": ("ss_resolution", "ss_id"),
    "steamgriddb": ("sgdb_resolution", "sgdb_id"),
    "thegamesdb": ("tgdb_resolution", "tgdb_id"),
    # MobyGames and ArcadeDB are keyed by STRING, not integer — a Moby id is a slug
    # (`bulletstorm`) and an ArcadeDB id is a MAME set name (`pacman`). The shared layer
    # stores whatever the provider's own identifier is; forcing them to integers would
    # mean inventing a second id space and a mapping to maintain.
    "mobygames": ("moby_resolution", "moby_id"),
    "arcadedb": ("arcadedb_resolution", "arcadedb_id"),
    "zxinfo": ("zxinfo_resolution", "zxinfo_id"),
}

# Providers whose identifier is a STRING, not an integer. A MobyGames id is a slug
# (`bulletstorm`), an ArcadeDB id is a MAME set name (`pacman`), a ZXInfo id is a
# zero-padded string (`0002259` — and `int()` would eat the padding). This layer was
# built when every provider ided by number, and adding these three without saying so
# broke them SILENTLY: `record()` coerced the slug with `int(...)` , caught the
# ValueError, and wrote a MISS. A perfectly good id became "we looked and found
# nothing", which is the worst possible failure because it looks like an answer.
STRING_ID_PROVIDERS = {"mobygames", "arcadedb", "zxinfo"}


def _is_string_id(provider):
    return provider in STRING_ID_PROVIDERS


def _coerce(provider, provider_id):
    """The id as this provider expresses it, or a falsy value meaning MISS."""
    if _is_string_id(provider):
        return str(provider_id or "").strip()
    try:
        n = int(provider_id or 0)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


# Columns a specific provider needs that the shared layer does not. This is the uniform
# -provider rule in miniature: everything common lives above, and a provider declares
# only what is genuinely its own. IGDB's page URL is built from a slug, so it carries one;
# nothing else does, and nothing else should be given one to make the tables look alike.
EXTRA_COLUMNS = {"igdb": [("slug", "TEXT")]}

# How long a recorded miss suppresses a re-search. Long enough that a library sweep is
# cheap on repeat, short enough that a provider adding the game is picked up.
MISS_TTL = 30 * 24 * 3600


def _spec(provider):
    try:
        return PROVIDERS[provider]
    except KeyError:
        raise ValueError(
            "provider_ids: unsupported provider %r (known: %s). Add it to PROVIDERS "
            "rather than letting the identity be dropped silently."
            % (provider, ", ".join(sorted(PROVIDERS))))


def ensure_tables(con):
    """Create the identity caches. Safe to call on every open."""
    for prov, (table, idcol) in PROVIDERS.items():
        con.execute(
            "CREATE TABLE IF NOT EXISTS %s(norm_key TEXT PRIMARY KEY, %s %s, "
            "name TEXT, matched_by TEXT, resolved_at INTEGER)"
            % (table, idcol, "TEXT" if _is_string_id(prov) else "INTEGER"))
        # The matched record's YEAR. Without it a wrong-era match is undetectable after
        # the fact: Resident Evil 4 (2023) held ScreenScraper 4750, the 2005 game, and
        # the only way to find others like it was a norm_key heuristic that needed the
        # catalog to hold BOTH releases. What a provider told us is worth writing down,
        # or every audit of it means asking the provider again.
        # IGDB predates this module and its table was created elsewhere, with its own
        # columns. Adding the shared ones rather than demanding a matching schema is what
        # lets an existing provider join the common layer without a migration — and
        # joining it is the point: until now IGDB had no uniqueness guard, no recorded
        # year and no era invariant, because all three live here.
        have = {r[1] for r in con.execute("PRAGMA table_info(%s)" % table)}
        # The matched record's SYSTEM, for the same reason as its year and with sharper
        # teeth. ScreenScraper keeps one record PER SYSTEM, so the system is part of the
        # identity: a record for another system is a different RELEASE, carrying that
        # release's art and dates. An ERA test cannot catch it when the years agree —
        # live, a genesis game held a PC Windows record with no dates at all, and a ps1
        # game held a PC Windows record dated 1999, the same year as the PS1 release.
        # Both sat under a green era invariant. Recorded here so the audit is offline.
        for col, decl in ([("name", "TEXT"), ("matched_by", "TEXT"),
                           ("resolved_at", "INTEGER"), ("year", "INTEGER"),
                           ("system", "TEXT")]
                          + EXTRA_COLUMNS.get(prov, [])):
            if col not in have:
                con.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, decl))
    con.commit()


def cached(con, provider, norm_key):
    """(id, matched_by, resolved_at) for a recorded row, or None if never looked at.
    An id of 0 means a recorded MISS — see is_identified()."""
    table, idcol = _spec(provider)
    r = con.execute("SELECT %s, matched_by, resolved_at FROM %s WHERE norm_key=?"
                    % (idcol, table), (norm_key,)).fetchone()
    if not r:
        return None
    return (r[0] or ("" if _is_string_id(provider) else 0), r[1] or "", r[2] or 0)


def is_identified(con, provider, norm_key):
    """True only for a REAL id. A recorded miss is not an identity — the whole point of
    the igdb:0 incident is that a falsy id used as a key makes every entry carrying it
    share one identity."""
    row = cached(con, provider, norm_key)
    if not row:
        return False
    # A string id is identified when it is non-empty; a numeric one when it is positive.
    # `row[0] > 0` on a slug raises TypeError, which is how this went unnoticed.
    return bool(str(row[0]).strip()) if _is_string_id(provider) else bool(row[0] > 0)


def holder(con, provider, provider_id, norm_key=None):
    """The norm_key already holding `provider_id` on this provider, if it is another
    game. None when the id is free or already ours."""
    table, idcol = _spec(provider)
    pid = _coerce(provider, provider_id)
    if not pid:
        return None
    try:
        r = con.execute("SELECT norm_key FROM %s WHERE %s=? AND norm_key<>? LIMIT 1"
                        % (table, idcol), (pid, norm_key or "")).fetchone()
    except sqlite3.OperationalError:
        return None
    return r[0] if r else None


def record(con, provider, norm_key, provider_id, name=None, matched_by="search",
           year=None, system=None):
    """Write an identity (or a miss, with a falsy provider_id). Idempotent.

    A SEARCHED id that another game already holds is refused. One provider id is one
    game, so two titles arriving at the same id means at least one of them is wrong —
    and a search is exactly where that happens: an AI-proposed alias drops a
    distinguishing word ("Ninja Gaiden Sigma 2" searched as "Ninja Gaiden 2"), the
    provider answers with its nearest record, and the acceptance gate judges against the
    alias rather than the game we own. That is how "Ninja Gaiden Sigma 2" and "Ninja
    Gaiden II Black" ended up sharing ScreenScraper 25266, and Hammerwatch II shared
    SteamGridDB 5462929 with Heroes of Hammerwatch II.

    Deliberately narrow. It does NOT apply to `steam_appid` or `manual` matches: an
    appid lookup is exact, and a DLC or beta appid legitimately resolves to its parent's
    record, which is the provider modelling one product where our catalog lists two.
    Refusing those would delete correct matches to satisfy a rule about wrong ones.

    A refusal writes nothing — not even a miss — so the game is re-asked later rather
    than being remembered as having no match.
    """
    table, idcol = _spec(provider)
    pid = _coerce(provider, provider_id)
    # 'hash' joins 'manual' and 'steam_appid' as EXACT evidence. A crc or sha1 match is
    # not a search: the dump database published that pairing, and the collision guard
    # below exists to catch a search's nearest-record guess. Two games sharing a hash
    # would mean the dump database is wrong, which is a different problem and not one
    # this guard can fix by discarding the match.
    if pid and matched_by not in ("manual", "steam_appid", "hash", "index"):
        other = holder(con, provider, pid, norm_key)
        if other:
            # Record it as a MISS tagged `collision`, not as nothing. A refusal is an
            # attempt with a known outcome — "the provider's best match belongs to
            # another game in this library" — and writing nothing made the game look
            # never-attempted, which is a different fact and one I7 rightly complains
            # about. As a miss it carries MISS_TTL, so it is re-asked once the provider
            # has had time to add a record of its own.
            pid, matched_by = ("" if _is_string_id(provider) else 0), "collision"
    try:
        yr = int(year) if str(year or "").strip().isdigit() else None
    except (TypeError, ValueError):
        yr = None
    con.execute(
        "INSERT INTO %s(norm_key,%s,name,year,system,matched_by,resolved_at) "
        "VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(norm_key) DO UPDATE SET %s=excluded.%s, name=excluded.name, "
        "year=excluded.year, system=excluded.system, matched_by=excluded.matched_by, "
        "resolved_at=excluded.resolved_at"
        % (table, idcol, idcol, idcol),
        (norm_key, pid, name, yr, (system or None),
         matched_by if pid else ("collision" if matched_by == "collision" else "none"),
         int(time.time())))
    con.commit()
    return pid


# provider_ids provider -> the namespace the match index files it under. `ss` is the
# index's name for ScreenScraper; this layer calls the same thing `screenscraper`. Two
# names for one thing is how a namespace query returns 0 and gets believed.
# provider -> the index namespace holding THAT PROVIDER'S OWN identifier, in the form
# this layer stores. A namespace is listed only when both are true; being able to look a
# thing up is not the same as being able to USE what comes back.
#
# MOBYGAMES IS DELIBERATELY ABSENT, and the reason is the whole point of this map. The
# index namespace `mobygames` carries TWO different handle forms for the same game:
# `_merge_moby` writes the numeric catalogue id (29845) and the Wikidata cross-reference
# writes the URL slug (`bioshock`). Both are real MobyGames handles, and BioShock live
# carries four of them. This layer stores one string, so answering from that namespace
# would hand back whichever form the index happened to list first — an id the caller
# cannot use, written down as a decision. Splitting the namespace at build time is the
# fix; until then Moby is searched normally and nothing is guessed.
INDEX_NS = {"igdb": "igdb", "screenscraper": "ss", "thegamesdb": "thegamesdb"}


def index_lookup(provider, anchors, systems=None):
    """A provider id taken from the match index, or None.

    ONLY EXACT ANCHORS. A store id or another provider's id is a pairing somebody
    PUBLISHED; a name is a conclusion we reached. Anchoring on a name would let an index
    collision hand back a confidently wrong id with no gate in front of it, which is the
    fail-open shape this codebase keeps paying for. So a name is never used here, and a
    miss simply falls through to the normal search.

    This is the whole point of holding an index: once any exact handle on a game is
    known, every other provider's id for that game is free and needs no request.

    ONE CANDIDATE, OR NOTHING. Several ids under one namespace is NORMAL and intended,
    not a merge fault: ScreenScraper keeps a separate record per system, TheGamesDB keeps
    one per (title, platform, region), and the build attaches every one of them on
    purpose, because choosing between them needs the platform and filename the CALLER
    holds. Measured on this library, that is 45% of ScreenScraper hits and 51% of
    TheGamesDB hits.

    SO THE CALLER'S PLATFORM IS USED, because it is the missing half of that sentence.
    `systems` is what the game actually runs on, and matchindex stamps each ScreenScraper
    key with the platform its record describes. Filtering by it is not a guess: it
    removes records for OTHER hardware, which are different products. Measured on 696
    ambiguous ScreenScraper answers, 418 (60%) hold exactly one record for the platform
    asked about. What is left over is editions of one game on one platform — "Alan Wake",
    "Alan Wake: Standard Edition", "Alan Wake (Collector's Edition)" — and no platform
    separates those, so they still fall through to a search.

    Anything the filter cannot settle is searched exactly as before. The index declining
    to answer costs one request; answering wrongly costs a wrong bind recorded as exact
    evidence, which nothing downstream would ever question.
    """
    ns = INDEX_NS.get(provider)
    if not ns or not anchors:
        return None
    try:
        import matchindex
    except Exception:                            # noqa: BLE001 — the index is optional
        return None
    con = None
    try:
        con = matchindex.connect()
        for a_ns, a_val in anchors.items():
            if not a_val:
                continue
            hit = matchindex.resolve(con, a_ns, str(a_val))
            if not hit:
                continue
            vals = [v for v in (hit.get(ns) or []) if _usable_id(provider, v)]
            if len(vals) > 1:
                vals = _on_platform(con, ns, vals, systems) or vals
            if len(vals) == 1:
                return vals[0]
    except Exception:                            # noqa: BLE001
        return None
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:                    # noqa: BLE001
                pass
    return None


def _on_platform(con, ns, vals, systems):
    """Those of `vals` whose index platform matches one the game runs on, or [].

    A NULL platform is UNKNOWN, NEVER "no platform". An index built before the column
    existed has every row NULL, and a downloaded one may not have been stamped yet.
    Reading NULL as a mismatch would drop every candidate and turn a working lookup into
    a permanent miss — the fail-open failure this codebase keeps paying for. So a row
    with no platform is kept, and an empty result means the filter had nothing to say,
    which the caller treats as "use the unfiltered list"."""
    if not systems:
        return []
    try:
        import platmap
        # Same rule on both sides: an unrecognised label canonicalises to itself, and
        # comparing two unmapped tokens would be matching noise against noise.
        want = {c for c in (platmap.canon(p) for p in systems if p)
                if c in platmap.KNOWN}
        if not want:
            return []
        rows = con.execute(
            "SELECT val, platform FROM ix.identity_key WHERE ns=? AND val IN (%s)"
            % ",".join("?" * len(vals)), [ns] + [str(v) for v in vals]).fetchall()
        known = {str(r["val"]): r["platform"] for r in rows}
        keep = [v for v in vals
                if known.get(str(v)) is None or known.get(str(v)) in want]
        # Every candidate kept means the filter separated nothing.
        return keep if 0 < len(keep) < len(vals) else []
    except Exception:                            # noqa: BLE001
        return []


def _usable_id(provider, val):
    """Is this index value an identifier THIS provider would accept?

    A namespace is a bag of handles, not a typed column, so a value that reads fine as
    text can still be the wrong KIND of handle. Every provider here ids by integer, so a
    non-integer under one of these namespaces is a slug or a URL fragment that arrived
    from a cross-reference, and passing it on would write a lookup key no request can
    ever use. Refuse it and search instead."""
    if _is_string_id(provider):
        return bool(str(val or "").strip())
    return str(val or "").strip().isdigit()


def resolve(con, provider, norm_key, title, systems, search, force=False, anchors=None):
    """The id for this game on this provider, searching only when we have to.

    `search(title, systems)` returns the provider's own match dict (whatever key the
    provider uses for its id — `ss_id`, `sgdb_id`, or a plain `id`) or None. Returns the
    id, or 0 for "no match". Never raises on a search failure: a provider being down is
    not a reason to record a miss, so that case leaves the cache untouched and returns 0
    so the caller can carry on.
    """
    _table, idcol = _spec(provider)
    row = cached(con, provider, norm_key)
    if row and not force:
        pid, matched_by, at = row
        if pid > 0 or matched_by == "manual":
            return pid                      # a decision — never re-search
        if (time.time() - at) < MISS_TTL:
            return 0                        # a fresh miss — don't hammer the provider
        # a STALE miss falls through and is retried: a recorded miss is the absence of a
        # decision, not a permanent verdict (#25).
    # THE INDEX BEFORE THE NETWORK. If any exact handle we already hold identifies this
    # game, the index already knows every other provider's id for it. That costs one
    # local lookup instead of a rate-limited round trip and an acceptance gate, and it
    # cannot be a wrong bind because the pairing was published, not concluded.
    from_ix = index_lookup(provider, anchors, systems=systems)
    if from_ix:
        return record(con, provider, norm_key, from_ix, None, "index", system=None)

    try:
        hit = search(title, systems)
    except Exception:                       # noqa: BLE001 — see docstring
        return 0
    if not hit:
        return record(con, provider, norm_key, 0, None, "none")
    pid = hit.get(idcol) or hit.get("id") or 0
    return record(con, provider, norm_key, pid, hit.get("name"), "search",
                  year=hit.get("year"), system=hit.get("system"))


def unlinked(con, provider, norm_keys):
    """Of `norm_keys`, those with no recorded identity yet (or a stale miss) — the work
    list for a sweep. Keeps the caller from re-deriving this rule."""
    out = []
    now = time.time()
    for nk in norm_keys:
        row = cached(con, provider, nk)
        if row is None:
            out.append(nk)
        elif row[0] <= 0 and row[1] != "manual" and (now - row[2]) >= MISS_TTL:
            out.append(nk)
    return out


# How an identity was ESTABLISHED decides whether the name gate governs it. A name search
# is a judgement about two strings and can be re-judged; an exact id lookup is not.
# Mirrors record()'s exemptions deliberately — if these two lists ever disagree, the
# scrub deletes the very matches record() went out of its way to protect.
NAME_DERIVED = ("search", "name")


def _candidate_name(con, provider, provider_id, stored):
    """The provider record's own name, for re-judging a recorded match.

    Every provider stores it on the identity row except IGDB, whose resolutions predate
    the shared column and carry NULL; its name lives in the cached payload. Explicit
    special case rather than a backfill, because the payload is already the authority.
    """
    if stored:
        return stored
    if provider != "igdb":
        return None
    try:
        import json
        r = con.execute("SELECT payload_json FROM igdb_meta WHERE igdb_id=? LIMIT 1",
                        (provider_id,)).fetchone()
        return (json.loads(r[0]).get("name") or "").strip() if r else None
    except Exception:                            # noqa: BLE001 — a bad payload is a miss
        return None


def rescore(cache_con, lib_con, aliases_for=None, apply=False):
    """Re-decide recorded identities under TODAY's acceptance gate.

    An acceptance rule that gets stricter leaves everything it would now refuse sitting
    in the cache, indistinguishable from a match it would make. Those identities are
    invisible: nothing re-judges a decision already written down, so the library keeps
    binds no fresh ingest would ever produce. Live, that was 93 ScreenScraper identities
    — `Deathmatch Classic` holding DmC: Devil May Cry, `Beyond Citadel` holding The
    Citadel — recorded before the gate existed and unreachable by every later pass.

    Scoped by HOW the identity was established, not by how old it is:

      * `search` / `name`  — a judgement about two strings. Re-judgeable, so in scope.
      * `steam_appid`      — ownership. An appid IS the identity; a DLC or re-titled
                             store SKU legitimately resolves to its parent record, and
                             judging that on names would delete correct matches (GTA V
                             Legacy, DOOM + DOOM II). Never touched.
      * `manual`           — a person decided. Never touched, per #25.
      * a recorded MISS    — already the absence of a decision. Nothing to re-judge.

    Aliases are included in the re-score because the matcher rescues through them: a
    regional title that only matches as "Probotector" must not be refused here for
    failing to match as "Contra". `aliases_for(norm_key, title) -> [str]` is injected so
    this module stays standalone — it does not know how to search, and it does not know
    how to alias.

    Refusals are DELETED, not marked. A cleared identity is the absence of a decision,
    so the next match pass re-searches and records whatever today's rule actually
    concludes — a better match, or an honest miss. Writing a tombstone instead would
    make "we refused this once" permanent, which is the mistake #25 was about.

    Read-only unless `apply=True`. Returns
    {'checked': n, 'refused': [(provider, norm_key, title, name)], 'cleared': n}.
    """
    import matchgate
    titles = {nk: t for nk, t in lib_con.execute(
        "SELECT norm_key, canonical_title FROM games")}
    out = {"checked": 0, "refused": [], "cleared": 0}
    for provider, (table, idcol) in sorted(PROVIDERS.items()):
        try:
            cols = {r[1] for r in cache_con.execute("PRAGMA table_info(%s)" % table)}
        except sqlite3.OperationalError:
            continue                             # provider never ran on this install
        if not {"matched_by", "name"} <= cols:
            continue                             # table predates the shared columns
        rows = cache_con.execute(
            "SELECT norm_key, %s, matched_by, name, year FROM %s "
            "WHERE COALESCE(%s,0)>0" % (idcol, table, idcol)).fetchall()
        for nk, pid, how, nm, yr in rows:
            if how not in NAME_DERIVED:
                continue
            title = titles.get(nk)
            cand = _candidate_name(cache_con, provider, pid, nm)
            if not title or not cand:
                continue                         # nothing to re-judge it against
            out["checked"] += 1
            queries = [title]
            if aliases_for:
                try:
                    queries += [a for a in (aliases_for(nk, title) or []) if a]
                except Exception:                # noqa: BLE001 — aliases are a bonus
                    pass
            era = matchgate.game_era(lib_con, cache_con, nk)
            ok, _score = matchgate.score(queries, cand, era, yr)
            if ok:
                continue
            out["refused"].append((provider, nk, title, cand))
            if apply:
                cache_con.execute("DELETE FROM %s WHERE norm_key=?" % table, (nk,))
                out["cleared"] += 1
        if apply:
            cache_con.commit()
    return out


def _main(argv):
    """`python3 ludodex/provider_ids.py --scrub [--apply] [--no-aliases]`

    Re-decide recorded identities under today's gate. Dry-run by default: it prints what
    it WOULD clear and changes nothing, because the destructive direction of this tool is
    deleting correct matches.

    `--no-aliases` judges against the owned title alone. Use it deliberately: aliases are
    what the matcher rescues through, so excluding them reports matches the product would
    legitimately make — but INCLUDING them re-judges a match against the alias rather
    than the game we own, which is the very thing that produced the worst binds in this
    library (`Deathmatch Classic` accepted DmC: Devil May Cry because 'DMC' is one of its
    aliases). Neither answer is "the" answer until that is settled; the flag exists so
    the two are not silently conflated.
    """
    import os
    if "--scrub" not in argv:
        print(_main.__doc__.strip())
        return 2
    data = os.environ.get("LUDODEX_DATA") or os.path.dirname(os.path.abspath(__file__))
    lib = sqlite3.connect("file:%s?mode=ro" % os.path.join(data, "game-library.sqlite"),
                          uri=True)
    apply_it = "--apply" in argv
    cache = sqlite3.connect(os.path.join(data, "metadata-cache.sqlite"))
    aliases_for = None
    if "--no-aliases" not in argv:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from server import app as _app
            aliases_for = (lambda nk, t:
                           _app._title_aliases(nk, t, [], allow_ai=False))
        except Exception as e:                   # noqa: BLE001 — aliases are optional
            print("aliases unavailable (%s); judging on the owned title alone"
                  % str(e)[:80])
    res = rescore(cache, lib, aliases_for=aliases_for, apply=apply_it)
    print("checked %d name-derived identit(y/ies); %d refused by today's gate"
          % (res["checked"], len(res["refused"])))
    for prov, nk, title, cand in res["refused"]:
        print("  %-13s %-34s <- %s" % (prov, (title or "")[:34], (cand or "")[:44]))
    print("cleared: %d%s" % (res["cleared"], "" if apply_it else "  (dry run — pass --apply)"))
    cache.close()
    lib.close()
    return 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(_main(_sys.argv[1:]))
