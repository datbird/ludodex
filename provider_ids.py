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
import time

# provider -> (table, id column). Adding one here is all a new provider needs from this
# layer; anything not listed is refused rather than silently dropped on the floor.
PROVIDERS = {
    "screenscraper": ("ss_resolution", "ss_id"),
    "steamgriddb": ("sgdb_resolution", "sgdb_id"),
}

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
    for table, idcol in PROVIDERS.values():
        con.execute(
            "CREATE TABLE IF NOT EXISTS %s(norm_key TEXT PRIMARY KEY, %s INTEGER, "
            "name TEXT, matched_by TEXT, resolved_at INTEGER)" % (table, idcol))
    con.commit()


def cached(con, provider, norm_key):
    """(id, matched_by, resolved_at) for a recorded row, or None if never looked at.
    An id of 0 means a recorded MISS — see is_identified()."""
    table, idcol = _spec(provider)
    r = con.execute("SELECT %s, matched_by, resolved_at FROM %s WHERE norm_key=?"
                    % (idcol, table), (norm_key,)).fetchone()
    return (r[0] or 0, r[1] or "", r[2] or 0) if r else None


def is_identified(con, provider, norm_key):
    """True only for a REAL id. A recorded miss is not an identity — the whole point of
    the igdb:0 incident is that a falsy id used as a key makes every entry carrying it
    share one identity."""
    row = cached(con, provider, norm_key)
    return bool(row and row[0] > 0)


def holder(con, provider, provider_id, norm_key=None):
    """The norm_key already holding `provider_id` on this provider, if it is another
    game. None when the id is free or already ours."""
    table, idcol = _spec(provider)
    try:
        pid = int(provider_id or 0)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    try:
        r = con.execute("SELECT norm_key FROM %s WHERE %s=? AND norm_key<>? LIMIT 1"
                        % (table, idcol), (pid, norm_key or "")).fetchone()
    except sqlite3.OperationalError:
        return None
    return r[0] if r else None


def record(con, provider, norm_key, provider_id, name=None, matched_by="search"):
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
    try:
        pid = int(provider_id or 0)
    except (TypeError, ValueError):
        pid = 0
    if pid > 0 and matched_by not in ("manual", "steam_appid"):
        other = holder(con, provider, pid, norm_key)
        if other:
            # Record it as a MISS tagged `collision`, not as nothing. A refusal is an
            # attempt with a known outcome — "the provider's best match belongs to
            # another game in this library" — and writing nothing made the game look
            # never-attempted, which is a different fact and one I7 rightly complains
            # about. As a miss it carries MISS_TTL, so it is re-asked once the provider
            # has had time to add a record of its own.
            pid, matched_by = 0, "collision"
    con.execute(
        "INSERT INTO %s(norm_key,%s,name,matched_by,resolved_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(norm_key) DO UPDATE SET %s=excluded.%s, name=excluded.name, "
        "matched_by=excluded.matched_by, resolved_at=excluded.resolved_at"
        % (table, idcol, idcol, idcol),
        (norm_key, pid, name,
         matched_by if pid else ("collision" if matched_by == "collision" else "none"),
         int(time.time())))
    con.commit()
    return pid


def resolve(con, provider, norm_key, title, systems, search, force=False):
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
    try:
        hit = search(title, systems)
    except Exception:                       # noqa: BLE001 — see docstring
        return 0
    if not hit:
        return record(con, provider, norm_key, 0, None, "none")
    pid = hit.get(idcol) or hit.get("id") or 0
    return record(con, provider, norm_key, pid, hit.get("name"), "search")


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
