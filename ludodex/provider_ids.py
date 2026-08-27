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
import os
import sqlite3
import sys
import threading
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

# Providers that file one record PER SYSTEM rather than one per GAME. Their identity is
# `(game, platform)`, so their table is keyed that way and every read must say which
# platform it is asking about.
#
# ScreenScraper keeps a separate record for every system a game shipped on, each with
# that release's art, year and metadata. Keyed on `norm_key` alone, a game owned on two
# platforms had ONE row, so whichever record the search happened to return served both
# and the other platform wore a different release. Measured live 2026-08-26: 61 norm_keys
# span more than one platform and 57 of them shared a single ScreenScraper record —
# invariants I10 and I11, and the structural reason they could not be fixed row by row.
#
# TheGamesDB belongs here on the same reasoning (one record per title/platform/region,
# 51% of its hits ambiguous) and is NOT listed yet: `tgdb_resolution` holds 0 rows on
# this install, so adding it would migrate a table nothing has written to and change a
# code path nothing exercises. Add it with the run that first populates it.
#
# Everything else — IGDB, SteamGridDB, MobyGames, ArcadeDB, ZXInfo — files one record
# per game. Their platform component is the empty string and nothing about them moves.
PLATFORM_KEYED = {"screenscraper"}

# The platform component of a row that has not been placed on a platform yet. Carried
# migration rows use it, and a platform-keyed READ never serves them: a legacy row is
# the answer to "which record does this game have", which is the question that had no
# single answer in the first place. See `legacy_rows` and `place_legacy`.
LEGACY = ""


def _is_string_id(provider):
    return provider in STRING_ID_PROVIDERS


def is_platform_keyed(provider):
    """Does this provider file one record per SYSTEM (as opposed to per game)?"""
    return provider in PLATFORM_KEYED


def _plat(provider, platform, required=False):
    """The platform component of the key.

    Always `''` for a provider that files one record per game — its identity has no
    platform and inventing one would split its single row. For a per-system provider the
    platform is part of the identity, so a caller that does not state one is asking a
    question with no single answer; `required` makes that a refusal rather than a guess.
    """
    if not is_platform_keyed(provider):
        return LEGACY
    p = (platform or "").strip().lower()
    if not p and required:
        raise ValueError(
            "provider_ids: %s files one record per SYSTEM, so an identity needs the "
            "platform it is for. Resolve it one platform at a time." % provider)
    return p


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


def _has_platform_pk(con, table):
    """Is `table` already keyed (norm_key, platform)?"""
    try:
        pk = {r[1] for r in con.execute("PRAGMA table_info(%s)" % table) if r[5]}
    except sqlite3.OperationalError:
        return False
    return pk == {"norm_key", "platform"}


def _migrate_to_platform_key(con, prov, table, idcol):
    """Re-key a per-system provider's table from `norm_key` to `(norm_key, platform)`.

    EVERY EXISTING ROW IS KEPT, and kept as LEGACY — platform `''`. It cannot simply be
    stamped with a platform here, because which platform a row describes is a question
    about the CATALOG (what the game is owned on) and the recorded system, neither of
    which this module can see. `place_legacy` does that with a library handle; until it
    runs, a platform-keyed read finds nothing and the game is re-matched per platform,
    which is the honest outcome rather than the old arbitrary one.

    sqlite cannot ALTER a primary key, so this is the standard rebuild. Idempotent: it
    does nothing once the key is already composite.
    """
    if _has_platform_pk(con, table):
        return False
    have = {r[1] for r in con.execute("PRAGMA table_info(%s)" % table)}
    if not have:
        return False                          # table does not exist yet; created below
    idtype = "TEXT" if _is_string_id(prov) else "INTEGER"
    tmp = "%s__platkey" % table
    con.execute("DROP TABLE IF EXISTS %s" % tmp)
    con.execute(
        "CREATE TABLE %s(norm_key TEXT NOT NULL, platform TEXT NOT NULL DEFAULT '', "
        "%s %s, name TEXT, matched_by TEXT, resolved_at INTEGER, year INTEGER, "
        "system TEXT, PRIMARY KEY(norm_key, platform))" % (tmp, idcol, idtype))
    carry = [c for c in ("norm_key", idcol, "name", "matched_by", "resolved_at",
                         "year", "system") if c in have]
    con.execute("INSERT INTO %s(%s) SELECT %s FROM %s"
                % (tmp, ",".join(carry), ",".join(carry), table))
    con.execute("DROP TABLE %s" % table)
    con.execute("ALTER TABLE %s RENAME TO %s" % (tmp, table))
    con.commit()
    return True


def ensure_tables(con):
    """Create the identity caches. Safe to call on every open."""
    for prov, (table, idcol) in PROVIDERS.items():
        if is_platform_keyed(prov):
            # A per-system provider's identity is (game, platform). An existing table
            # keyed on norm_key alone is re-keyed first; every row it holds is carried
            # over as LEGACY and placed later by `place_legacy`, which needs the catalog.
            _migrate_to_platform_key(con, prov, table, idcol)
            con.execute(
                "CREATE TABLE IF NOT EXISTS %s(norm_key TEXT NOT NULL, "
                "platform TEXT NOT NULL DEFAULT '', %s %s, name TEXT, "
                "matched_by TEXT, resolved_at INTEGER, "
                "PRIMARY KEY(norm_key, platform))"
                % (table, idcol, "TEXT" if _is_string_id(prov) else "INTEGER"))
        else:
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


def cached(con, provider, norm_key, platform=None):
    """(id, matched_by, resolved_at) for a recorded row, or None if never looked at.
    An id of 0 means a recorded MISS — see is_identified().

    For a per-system provider `platform` selects the row, and a platform with no row is
    None — NEVER another platform's answer, which is the whole defect this key fixes. A
    LEGACY row (platform `''`, carried by the migration) is not served for the same
    reason: it is the answer to a question that had no single answer.
    """
    table, idcol = _spec(provider)
    if is_platform_keyed(provider) and _has_platform_pk(con, table):
        p = _plat(provider, platform, required=True)
        r = con.execute("SELECT %s, matched_by, resolved_at FROM %s "
                        "WHERE norm_key=? AND platform=?" % (idcol, table),
                        (norm_key, p)).fetchone()
    else:
        r = con.execute("SELECT %s, matched_by, resolved_at FROM %s WHERE norm_key=?"
                        % (idcol, table), (norm_key,)).fetchone()
    if not r:
        return None
    return (r[0] or ("" if _is_string_id(provider) else 0), r[1] or "", r[2] or 0)


def platforms_for(con, provider, norm_key):
    """Every platform this game has a PLACED identity row on. [] when it has none.

    A per-game provider has no platform on its single row, so this is [] for it — ask
    `cached` instead. Legacy rows are excluded: they are carried, not placed.
    """
    table, _idcol = _spec(provider)
    if not (is_platform_keyed(provider) and _has_platform_pk(con, table)):
        return []
    return [r[0] for r in con.execute(
        "SELECT platform FROM %s WHERE norm_key=? AND platform<>''" % table,
        (norm_key,))]


def legacy_rows(con, provider):
    """[(norm_key, id, name, matched_by, year, system)] for rows the migration carried
    over but has not placed on a platform yet. The input to `place_legacy`."""
    table, idcol = _spec(provider)
    if not (is_platform_keyed(provider) and _has_platform_pk(con, table)):
        return []
    return [tuple(r) for r in con.execute(
        "SELECT norm_key, %s, name, matched_by, year, system FROM %s "
        "WHERE platform='' ORDER BY norm_key" % (idcol, table))]


def place_legacy(con, provider, owned, systeme_id, apply=False):
    """Put each carried row on the platform it actually describes, or drop it.

    `owned` maps norm_key -> [platform] from the CATALOG, and `systeme_id(platform)`
    is the provider's own system id for one of our platform labels (None when the
    provider has no system for it, which is true of PC and must stay true).

    Three rules, in order, and a row that satisfies none is DROPPED:

      1. The row records a SYSTEM and it is the system of one of the platforms this
         game is owned on. That is the platform the record describes — place it.
      2. The row records a system that is the system of NONE of them, and every owned
         platform states a system. Then the record is for hardware this game is not
         owned on: it is a different release wearing its own art and dates, which is
         exactly what invariant I11 names. Drop it.
      3. No usable system evidence and the game is owned on exactly ONE platform. There
         is only one platform the row can be for — place it there.

    Anything left is a multi-platform game whose single row cannot be attributed, which
    is the ambiguity this whole key exists to remove. A DROP is an ABSENCE, not a miss:
    nothing is written down, so the next sweep asks the provider per platform instead of
    remembering a verdict it never reached. datbird's call, 2026-08-26.

    `apply=False` reports without writing. Returns
    {'placed': [(nk, platform, why)], 'dropped': [(nk, why)]}.
    """
    table, idcol = _spec(provider)
    out = {"placed": [], "dropped": []}
    if not (is_platform_keyed(provider) and _has_platform_pk(con, table)):
        return out
    for nk, pid, name, matched_by, year, system in legacy_rows(con, provider):
        plats = [p for p in (owned.get(nk) or []) if p]
        sysid = None
        if str(system or "").strip():
            sysid = (int(system) if str(system).strip().isdigit()
                     else systeme_id(system))
        fits = [p for p in plats if sysid and systeme_id(p) == sysid]
        silent = [p for p in plats if systeme_id(p) is None]
        if fits:
            target, why = fits[0], "system %s is %s" % (sysid, fits[0])
        elif sysid and plats and not silent:
            target, why = None, ("system %s is none of %s"
                                 % (sysid, "/".join(sorted(plats))))
        elif len(plats) == 1:
            target, why = plats[0], "the only platform it is owned on"
        elif plats:
            target, why = None, ("owned on %s, and the row does not say which"
                                 % "/".join(sorted(plats)))
        else:
            target, why = None, "owned on nothing in the catalog"
        # A row already placed on the target platform was written by a per-platform
        # search and is the better answer. The legacy row is then simply removed rather
        # than overwriting it — a carried row must never outrank a placed one.
        if target and con.execute(
                "SELECT 1 FROM %s WHERE norm_key=? AND platform=?" % table,
                (nk, target)).fetchone():
            target, why = None, "%s already has its own row" % target
        if target:
            out["placed"].append((nk, target, why))
            if apply:
                con.execute("UPDATE %s SET platform=? WHERE norm_key=? AND platform=''"
                            % table, (target, nk))
        else:
            out["dropped"].append((nk, why))
            if apply:
                con.execute("DELETE FROM %s WHERE norm_key=? AND platform=''" % table,
                            (nk,))
    if apply:
        con.commit()
    return out


def is_real_id(provider, provider_id):
    """Is this cached value a REAL id, as opposed to a recorded MISS?

    A string id is identified when it is non-empty; a numeric one when it is positive.
    `provider_id > 0` on a slug raises TypeError, and that is not hypothetical: `resolve`
    and `unlinked` each wrote the comparison out by hand and each died on the first
    mobygames/arcadedb/zxinfo row — for a hit AND for a miss. `server/app.py` sweeps every
    provider through `unlinked`, so one string row killed the sweep for all of them.
    The rule lives here so a fourth reader cannot get it wrong a fourth way.
    """
    if _is_string_id(provider):
        return bool(str(provider_id or "").strip())
    try:
        return int(provider_id or 0) > 0
    except (TypeError, ValueError):
        return False


def is_identified(con, provider, norm_key, platform=None):
    """True only for a REAL id. A recorded miss is not an identity — the whole point of
    the igdb:0 incident is that a falsy id used as a key makes every entry carrying it
    share one identity."""
    row = cached(con, provider, norm_key, platform=platform)
    if not row:
        return False
    return is_real_id(provider, row[0])


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
           year=None, system=None, commit=True, platform=None):
    """Write an identity (or a miss, with a falsy provider_id). Idempotent.

    `commit=False` for a BULK writer. This committed on every call, and the hash pass
    calls it once per (game, provider) across hundreds of thousands of files — one fsync
    each, while `romhash.scan()` directly above it batches 2,000 rows per commit. A
    single-identity caller still commits by default, because for it the write and the
    durability are the same act.

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
    keyed = is_platform_keyed(provider) and _has_platform_pk(con, table)
    if keyed:
        p = _plat(provider, platform, required=True)
        con.execute(
            "INSERT INTO %s(norm_key,platform,%s,name,year,system,matched_by,"
            "resolved_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(norm_key,platform) DO UPDATE SET %s=excluded.%s, "
            "name=excluded.name, year=excluded.year, system=excluded.system, "
            "matched_by=excluded.matched_by, resolved_at=excluded.resolved_at"
            % (table, idcol, idcol, idcol),
            (norm_key, p, pid, name, yr, (system or None),
             matched_by if pid else ("collision" if matched_by == "collision"
                                     else "none"),
             int(time.time())))
    else:
        con.execute(
            "INSERT INTO %s(norm_key,%s,name,year,system,matched_by,resolved_at) "
            "VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(norm_key) DO UPDATE SET %s=excluded.%s, name=excluded.name, "
            "year=excluded.year, system=excluded.system, "
            "matched_by=excluded.matched_by, resolved_at=excluded.resolved_at"
            % (table, idcol, idcol, idcol),
            (norm_key, pid, name, yr, (system or None),
             matched_by if pid else ("collision" if matched_by == "collision"
                                     else "none"),
             int(time.time())))
    if commit:
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

    THE ANCHOR BEING EXACT DOES NOT MAKE THE ANSWER EXACT. The key that carries the answer
    may itself have been concluded — the index's ScreenScraper, MobyGames and TheGamesDB
    merges reach an identity through matchgate — so `index_answer()` returns the key's
    `kind` alongside the id and `resolve()` records the two cases differently. This
    function keeps the plain answer for callers that only want the id.
    """
    return (index_answer(provider, anchors, systems=systems) or (None,))[0]


# One match-index handle PER THREAD, held open. `matchindex.connect()` runs an
# executescript that creates three tables and an index, commits, ATTACHes the ~1 GB index
# and reads config — and `resolve()` asks the index once per game per provider, so opening
# it per call paid all of that tens of thousands of times in a single sweep. Per thread
# because a sqlite connection is not shareable across threads and the server is threaded.
_IX = threading.local()


def _ix_con():
    """The cached index handle for this thread, reopened when the index appears or goes.

    ABSENCE MUST STAY REACHABLE. A cached handle keeps a DELETED file open, so an index
    that was removed would keep answering — the exact inverse of the fail-open rule this
    module is built on, and something no caller could detect. The presence of the file is
    therefore re-checked on every call (one stat, against three CREATE TABLEs and a 1 GB
    ATTACH) and the handle is dropped whenever that answer changes.
    """
    import matchindex
    have = os.path.exists(matchindex.index_path())
    con = getattr(_IX, "con", None)
    if con is not None and getattr(_IX, "had", None) != have:
        try:
            con.close()
        except Exception:                        # noqa: BLE001
            pass
        con = None
    if con is None:
        con = matchindex.connect()
        _IX.con, _IX.had = con, have
    return con


def index_answer(provider, anchors, systems=None):
    """(id, kind, name, year, platform) from the match index, or None. See index_lookup.

    The KIND travels with the answer because it decides how the identity is recorded. An
    index key is `exact` only when the source PUBLISHED the pairing; the catalogue merges
    CONCLUDE one with matchgate and used to stamp that `exact` too, which exempted a
    name-derived bind from the collision guard and from `rescore()` forever.

    THE PLATFORM TRAVELS WITH IT FOR THE SAME REASON. For a provider that files one record
    per system the key's platform is half its identity, and dropping it here recorded the
    answer with `system=None`. Live, DOOM 3 took one ScreenScraper key on BOTH its PC and
    Switch rows and neither could be judged afterwards, because nothing had written down
    which release the key was for. NULL still means UNKNOWN, never "no platform".
    """
    ns = INDEX_NS.get(provider)
    if not ns or not anchors:
        return None
    try:
        import matchindex
    except Exception:                            # noqa: BLE001 — the index is optional
        return None
    try:
        con = _ix_con()
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
                return (vals[0], matchindex.key_kind(con, ns, vals[0]),
                        hit.get("_name"), hit.get("_year"),
                        _key_platform(con, ns, vals[0]))
    except Exception:                            # noqa: BLE001
        return None
    return None


def _key_platform(con, ns, val):
    """The platform an index key describes, or None for UNKNOWN.

    None is not "no platform". An index built before the column existed has every row
    NULL, and reading that as a mismatch would refuse every index answer.
    """
    try:
        r = con.execute("SELECT platform FROM ix.identity_key WHERE ns=? AND val=? "
                        "LIMIT 1", (ns, str(val))).fetchone()
        return (r["platform"] or None) if r else None
    except Exception:                            # noqa: BLE001 — absence is unknown
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

    ONE PLATFORM AT A TIME for a per-system provider. `systems` is the platform this
    identity is FOR, and for ScreenScraper it must name exactly one, because its record
    is per system and "which of these three platforms is this id for" is the question
    that had no answer before this key existed. Handing it several raises rather than
    picking one: a guess here is the original bug written back down.
    """
    _table, idcol = _spec(provider)
    plat = None
    if is_platform_keyed(provider):
        _plats = [p for p in (systems or []) if p]
        if len(_plats) != 1:
            raise ValueError(
                "provider_ids: %s files one record per SYSTEM, so resolve it one "
                "platform at a time, not %d at once (%s)"
                % (provider, len(_plats), norm_key))
        plat = _plats[0]
    row = cached(con, provider, norm_key, platform=plat)
    if row and not force:
        pid, matched_by, at = row
        if is_real_id(provider, pid) or matched_by == "manual":
            return pid                      # a decision — never re-search
        if (time.time() - at) < MISS_TTL:
            return 0                        # a fresh miss — don't hammer the provider
        # a STALE miss falls through and is retried: a recorded miss is the absence of a
        # decision, not a permanent verdict (#25).
    # THE INDEX BEFORE THE NETWORK. If any exact handle we already hold identifies this
    # game, the index already knows every other provider's id for it. That costs one
    # local lookup instead of a rate-limited round trip and an acceptance gate.
    #
    # AND IT IS ONLY AS EXACT AS THE KEY IT CAME FROM. `matched_by='index'` claims the
    # pairing was published, which buys exemption from the collision guard and from
    # rescore() — permanently, because nothing re-judges a decision already written down.
    # The index's catalogue merges CONCLUDE a pairing with matchgate, so an answer off one
    # of those is recorded as the name-derived judgement it is, and its name and year are
    # carried across so rescore() has something to re-judge it against.
    ix = index_answer(provider, anchors, systems=systems)
    if ix and ix[0]:
        from_ix, kind, ix_name, ix_year, ix_plat = ix
        how = "index" if kind == "exact" else "name"
        # A KEY FOR ANOTHER SYSTEM IS A DIFFERENT RELEASE. For a per-system provider an
        # index key that states a platform, and states one this identity is not for, is
        # refused rather than taken: it is the same discipline `system_fits` applies to a
        # search candidate. Live, DOOM 3's single ScreenScraper key was handed to its PC
        # row AND its Switch row. A key that says nothing is UNKNOWN and still answers.
        if not (is_platform_keyed(provider) and ix_plat
                and _plat(provider, ix_plat) != plat):
            return record(con, provider, norm_key, from_ix, ix_name, how,
                          year=ix_year, system=ix_plat, platform=plat)

    try:
        hit = search(title, systems)
    except Exception:                       # noqa: BLE001 — see docstring
        return 0
    if not hit:
        return record(con, provider, norm_key, 0, None, "none", platform=plat)
    pid = hit.get(idcol) or hit.get("id") or 0
    return record(con, provider, norm_key, pid, hit.get("name"), "search",
                  year=hit.get("year"), system=hit.get("system"), platform=plat)


def unlinked(con, provider, norm_keys, platforms=None):
    """Of `norm_keys`, those with no recorded identity yet (or a stale miss) — the work
    list for a sweep. Keeps the caller from re-deriving this rule.

    For a per-system provider the unit of work is a (game, platform), not a game, so
    `platforms` maps each norm_key to the platforms it is owned on and the return is a
    list of `(norm_key, platform)` pairs. Without that map a per-system provider has
    nothing to enumerate and returns nothing, which is honest: this function cannot
    invent the platforms a game is owned on.
    """
    out = []
    now = time.time()

    def _stale(row):
        return (row is None
                or (not is_real_id(provider, row[0]) and row[1] != "manual"
                    and (now - row[2]) >= MISS_TTL))

    if is_platform_keyed(provider):
        for nk in norm_keys:
            for p in (platforms or {}).get(nk) or []:
                if _stale(cached(con, provider, nk, platform=p)):
                    out.append((nk, p))
        return out
    for nk in norm_keys:
        if _stale(cached(con, provider, nk)):
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
        # A PER-SYSTEM PROVIDER IS RE-JUDGED PER PLATFORM, and cleared per platform. The
        # refusal below used to `DELETE ... WHERE norm_key=?`, which on a platform-keyed
        # table would take every platform's identity with it — including the ones today's
        # gate still accepts. A refusal is about ONE match, so it removes ONE row.
        keyed = is_platform_keyed(provider) and _has_platform_pk(cache_con, table)
        rows = cache_con.execute(
            "SELECT norm_key, %s, matched_by, name, year%s FROM %s "
            "WHERE COALESCE(%s,0)>0"
            % (idcol, ", platform, system" if keyed else ", '', ''", table,
               idcol)).fetchall()
        for nk, pid, how, nm, yr, plat, sysrow in rows:
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
            # SAME EXEMPTION AS THE MATCHER, or the scrub deletes what the matcher would
            # happily record. A per-system provider's record is dated for ITS system, so
            # when the row's system is the platform it is filed under, a LATER year is a
            # port date. Live 2026-08-26 this scrub proposed refusing APE OUT, Bayonetta
            # 2, Overwatch, Fallout Shelter and Titan Quest — every one an identical
            # title on its own system's record. An EARLIER year is still refused.
            later_ok = False
            if keyed and plat and sysrow:
                import screenscraper as _ss_rs
                later_ok = (_ss_rs.systeme_id(plat) is not None
                            and _ss_rs.system_id_fits(plat, sysrow))
            ok, _score = matchgate.score(queries, cand, era, yr, later_ok=later_ok)
            if ok:
                continue
            out["refused"].append((provider, "%s%s" % (nk, " on " + plat if plat else ""),
                                   title, cand))
            if apply:
                if keyed:
                    cache_con.execute(
                        "DELETE FROM %s WHERE norm_key=? AND platform=?" % table,
                        (nk, plat))
                else:
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
    # The package dir is NOT the data dir. Every sibling module says so in as many words:
    # __file__ is this package, DATA is the REPO ROOT above it, which is where the
    # databases live. Falling back to the package dir pointed the scrub at a directory
    # holding no game-library.sqlite at all.
    data = (os.environ.get("LUDODEX_DATA")
            or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
