#!/usr/bin/env python3
"""Resolve catalog games to IGDB and cache their metadata (igdb.com).

IGDB is a metadata PROVIDER, not a source. This reads the built catalog
(game-library.sqlite) for a worklist, resolves each game to an IGDB id
(by Steam appid via external_games, else by name search), fetches the record,
and caches everything in metadata-cache.sqlite. build_library.py then merges the
cache into game_attributes (FILL-GAPS — store/Playnite data always wins).

Production-grade caching: re-runs are cheap — only games not yet resolved and
records older than igdb_meta_ttl_days hit the network; resolutions persist.
Self-healing: --all ignores the caches and redoes everything.

  python3 igdb_enrich.py            # incremental (resolve new, refresh stale)
  python3 igdb_enrich.py --all      # ignore caches, re-resolve + re-fetch all
  python3 igdb_enrich.py --limit N  # cap name-searches this run (testing)
"""
import json
import os
import sqlite3
import sys
import time

DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("LUDODEX_DATA", DIR)
sys.path.insert(0, DIR)
import config
import console_eras
import igdb
import platmap
from titlenorm import norm

CACHE = os.path.join(DATA, "metadata-cache.sqlite")
INDEX = os.path.join(DATA, "media-index.sqlite")   # media_fetch/media_choose store
LIB = config.get("library_db")


def cache_con():
    con = sqlite3.connect(CACHE)
    con.execute("PRAGMA busy_timeout=120000")  # wait out the live server's reads (FUSE
    # can't do WAL, so metadata-cache stays DELETE-mode where a reader blocks the writer;
    # a generous busy_timeout + _commit() retry below keeps a batch enrich from dying on
    # "database is locked" while the server serves game-detail/ownership reads).
    con.executescript("""
    CREATE TABLE IF NOT EXISTS igdb_token(client_id TEXT PRIMARY KEY,
      token TEXT, expires_at INTEGER);
    CREATE TABLE IF NOT EXISTS igdb_resolution(norm_key TEXT PRIMARY KEY,
      igdb_id INTEGER, slug TEXT, matched_by TEXT, resolved_at INTEGER);
    CREATE TABLE IF NOT EXISTS igdb_meta(igdb_id INTEGER PRIMARY KEY,
      payload_json TEXT, fetched_at INTEGER);
    """)
    return con


def _commit(con, tries=6):
    """Commit, retrying on a transient 'database is locked' (DELETE-mode metadata-cache
    contends with the live server's reads). Backoff between attempts; re-raise if it
    never clears so a real error isn't swallowed."""
    for i in range(tries):
        try:
            con.commit()
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() or i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def token(con, cid, csec):
    now = int(time.time())
    row = con.execute("SELECT token,expires_at FROM igdb_token WHERE client_id=?",
                      (cid,)).fetchone()
    if row and row[1] - 60 > now:
        return row[0]
    tok, ttl = igdb.get_token(cid, csec)
    con.execute("INSERT INTO igdb_token(client_id,token,expires_at) VALUES(?,?,?) "
                "ON CONFLICT(client_id) DO UPDATE SET token=excluded.token, "
                "expires_at=excluded.expires_at", (cid, tok, now + ttl))
    con.commit()
    return tok


def _live_token():
    """(client_id, token) for live IGDB calls, or (None, None) when IGDB is
    disabled / uncredentialed. Reuses the cached OAuth token."""
    if not config.metadata_enabled("igdb"):
        return None, None
    cid, csec = config.igdb_creds()
    if not (cid and csec):
        return None, None
    con = cache_con()
    try:
        return cid, token(con, cid, csec)
    finally:
        con.close()


def _has_release_fields(g):
    return bool(g and (g.get("platforms") or g.get("release_dates")))


def releases_for(norm_key, allow_fetch=True):
    """A game's cross-platform release list from IGDB, cache-first + self-healing.
    Older cached records (fetched before platforms/release_dates were requested) are
    transparently re-fetched once. Returns
    {resolved, igdb_id, name, releases:[{id,name,abbr,year,human}], source}."""
    con = cache_con()
    try:
        row = con.execute("SELECT igdb_id FROM igdb_resolution WHERE norm_key=?",
                          (norm_key,)).fetchone()
        iid = row[0] if row else 0
        if not iid:
            return {"resolved": False, "releases": []}
        m = con.execute("SELECT payload_json FROM igdb_meta WHERE igdb_id=?",
                        (iid,)).fetchone()
        g = json.loads(m[0]) if m and m[0] else None
        source = "cache"
        if allow_fetch and not _has_release_fields(g):
            cid, tok = _live_token()
            if cid:
                body = "fields %s; where id = %d; limit 1;" % (igdb.GAME_FIELDS, iid)
                hits = igdb.query("games", body, cid, tok)
                if hits:
                    g = hits[0]
                    con.execute("INSERT OR REPLACE INTO igdb_meta"
                                "(igdb_id,payload_json,fetched_at) VALUES(?,?,?)",
                                (iid, json.dumps(g, ensure_ascii=False),
                                 int(time.time())))
                    con.commit()
                    source = "live"
        if not g:
            return {"resolved": True, "igdb_id": iid, "releases": [], "source": None}
        return {"resolved": True, "igdb_id": iid, "name": g.get("name"),
                "releases": igdb.releases(g), "source": source}
    finally:
        con.close()


def all_platforms(allow_fetch=True):
    """The full IGDB platform catalog (fetched once, then cached) as
    [{id,name,abbr}] — powers the ownership overlay's 'search any system'."""
    con = cache_con()
    try:
        con.execute("CREATE TABLE IF NOT EXISTS igdb_platforms("
                    "igdb_id INTEGER PRIMARY KEY, name TEXT, abbreviation TEXT,"
                    " fetched_at INTEGER)")
        con.commit()
        rows = con.execute("SELECT name, abbreviation FROM igdb_platforms").fetchall()
        if not rows and allow_fetch:
            cid, tok = _live_token()
            if cid:
                off, now = 0, int(time.time())
                while True:
                    hits = igdb.query(
                        "platforms",
                        "fields id,name,abbreviation; limit 500; offset %d;" % off,
                        cid, tok)
                    for p in hits:
                        con.execute("INSERT OR REPLACE INTO igdb_platforms"
                                    "(igdb_id,name,abbreviation,fetched_at) "
                                    "VALUES(?,?,?,?)", (p["id"], p.get("name"),
                                                        p.get("abbreviation"), now))
                    off += 500
                    if len(hits) < 500:
                        break
                con.commit()
                rows = con.execute(
                    "SELECT name, abbreviation FROM igdb_platforms").fetchall()
        seen, out = set(), []
        for n, a in rows:
            e = igdb.platform_entry({"name": n, "abbreviation": a})
            if e and e["id"] not in seen:
                seen.add(e["id"])
                out.append(e)
        return sorted(out, key=lambda e: e["name"].lower())
    finally:
        con.close()


def _year_of(hit):
    """First-release year of an IGDB search hit (unix `first_release_date`), or None."""
    d = hit.get("first_release_date")
    if not d:
        return None
    try:
        return time.gmtime(int(d)).tm_year
    except (TypeError, ValueError, OSError):
        return None


def _era_ok(consoles, year):
    """True if a candidate dated `year` is plausible for a game living on `consoles`.

    A candidate is rejected only when its year is impossible for EVERY one of the
    game's platforms — so an Apple II ROM (era 1977-1993) rejects a 2010 movie tie-in
    of the same name, while a title also owned on Steam ('pc', not an era-bound
    console) accepts any year. Unknown year or no era-bound console → accept."""
    if year is None or not consoles:
        return True
    return not all(console_eras.impossible(c, year) for c in consoles)


def _title_matches(h, nk):
    """True when the game's primary name OR any alternate name normalizes to `nk`."""
    if norm(h.get("name", "")) == nk:
        return True
    for a in (h.get("alternative_names") or []):
        an = a.get("name") if isinstance(a, dict) else a
        if an and norm(an) == nk:
            return True
    return False


def _contig_start(sub, seq):
    """Start index of `sub` as a contiguous sublist of `seq`, else -1."""
    if not sub or len(sub) > len(seq):
        return -1
    for i in range(len(seq) - len(sub) + 1):
        if seq[i:i + len(sub)] == sub:
            return i
    return -1


def _name_anchor_class(nk, names):
    """How a stored igdb match's name(s) relate to the norm_key — used to find LEGACY
    fuzzy mis-matches (the pre-2026-07-15 top-hit matcher bound e.g. 'journey' -> 'The
    Sims 4: Journey to Batuu'). Best (most-anchored) class across all names wins:
      exact    — a name normalizes exactly to nk (current strict matcher; always fine)
      anchored — nk is a contiguous run at the START or END of a name (subtitle/prefix
                 variant like '1943' -> '1943: The Battle of Midway'; legit, keep)
      interior — nk is a contiguous run BURIED mid-title ('journey' inside 'the sims 4
                 journey to batuu'); a common word in an unrelated title -> suspect
      norun    — nk isn't a contiguous run at all (reorder/semantic; murky, left alone)"""
    nkt = nk.split()
    best = "norun"
    order = {"norun": 0, "interior": 1, "anchored": 2, "exact": 3}
    for x in names:
        mt = norm(x or "").split()
        if mt == nkt:
            return "exact"
        idx = _contig_start(nkt, mt)
        if idx == 0 or (idx >= 0 and idx + len(nkt) == len(mt)):
            cls = "anchored"
        elif idx > 0:
            cls = "interior"
        else:
            cls = "norun"
        if order[cls] > order[best]:
            best = cls
    return best


def _pick_era_aware(hits, nk, consoles, require_unique=False):
    """From IGDB `search` hits, pick the best EXACT normalized-title match that is
    era-plausible for `consoles`. Returns (igdb_id, slug) or (0, None).

    Guards the exact-name matcher against same-name/different-era collisions: two
    unrelated games can normalize identically ('Alice in Wonderland' the 1985 Apple II
    text adventure vs. the 2010 film game). If no exact match is era-plausible, we stay
    unmatched rather than adopt the wrong game.

    `require_unique` (set for STORE games whose appid IGDB couldn't resolve): refuse when
    2+ same-name games remain, because a generic store title is a coin-flip — IGDB ranks
    the 1973 mainframe "Star Trek" above the 2013 game you actually own. A lone exact
    match (a re-release like "System Shock Classic" → the 1994 original) is still safe.

    Earliest-preference (original over remake) applies ONLY to ERA-BOUND consoles, where
    an exact-name collision is original-vs-remake. For a store/PC-only title there is no
    era to anchor on, so we keep IGDB's own relevance order instead of blindly taking the
    oldest same-named game.

    Matches on the primary name OR any IGDB `alternate_names` — so a Japanese/romanized
    ROM title resolves to its real entry (Akumajou Dracula X: Chi no Rondo -> Castlevania:
    Rondo of Blood), which is what lets an English fan-translation inherit the base game's
    metadata + art."""
    exact = [h for h in hits if _title_matches(h, nk)]
    if not exact:
        return 0, None
    ok = [h for h in exact if _era_ok(consoles, _year_of(h))]
    if not ok:
        return 0, None
    if require_unique and len(ok) > 1:
        return 0, None
    if consoles and any(console_eras.era(c) for c in consoles):
        ok.sort(key=lambda h: (_year_of(h) is None, _year_of(h) or 9999))
    h = ok[0]
    return h["id"], h.get("slug")


# --- per-entry identity resolution (task #8, Phase 1) ------------------------------ #
# Resolve ONE (entry, platform) against the exact-title candidate set, rather than
# stamping the whole title group with one id. Deterministic: IGDB is authoritative about
# which platforms a game released on, so `entry_fits` is pure platform membership. The
# no-fit classification uses the hardware-GENERATION gap (platmap.GEN) — the same reliable
# "impossible backport" signal the contamination check used, which the loose console_eras
# year-buffer misses (Star Fox 1993 is 1yr past the 2600's era end, but gen 2 << gen 4).

def _cand_platform_canons(cand):
    return platmap.igdb_canons(cand.get("platforms"))


def entry_fits(cand, platform):
    """True when IGDB lists `cand` as released on `platform` (platmap-canon membership)."""
    return platmap.canon(platform) in _cand_platform_canons(cand)


def per_entry_resolve(candidates, platform, primary_id):
    """Resolve one (entry, platform) against the exact-title `candidates`.

    Returns {"kind", "igdb_id", "fit_ids"}:
      - "unique"          exactly one candidate lists this platform -> igdb_id = it.
      - "ambiguous"       >1 candidate lists it -> fit_ids for the Phase-2 AI to decide.
      - "none_impossible" no candidate lists it AND this platform is an older hardware
                          GENERATION than every candidate's earliest -> detach-worthy.
      - "none_uncertain"  no candidate lists it but generation-compatible (maybe a port
                          IGDB doesn't list) -> Phase-2 AI decides (never auto-separated:
                          the over-separation guard)."""
    fits = [c for c in candidates if entry_fits(c, platform)]
    if len(fits) == 1:
        return {"kind": "unique", "igdb_id": fits[0]["id"], "fit_ids": [fits[0]["id"]]}
    if len(fits) > 1:
        return {"kind": "ambiguous", "igdb_id": None, "fit_ids": [c["id"] for c in fits]}
    eg = platmap.generation(platform)
    cand_gens = [platmap.GEN[c] for cand in candidates
                 for c in _cand_platform_canons(cand) if c in platmap.GEN]
    if eg is not None and cand_gens and eg < min(cand_gens):
        return {"kind": "none_impossible", "igdb_id": None, "fit_ids": []}
    return {"kind": "none_uncertain", "igdb_id": None, "fit_ids": []}


def combine_verdict(det, ai_verdict, primary_id, threshold=0.75):
    """Combine a deterministic `per_entry_resolve` result with an optional AI verdict
    into a final action for one entry: {"action", "igdb_id"} where action is:
      - "set"    -> give this entry its own igdb_id (a per-entry override).
      - "detach" -> separate this entry (a different game we can't confidently name).
      - "keep"   -> inherit the group/title-level identity (primary_id); no override.

    Gate logic: a UNIQUE deterministic fit is applied directly (no AI). Every non-unique
    case is decided by the AI verdict, and the OVER-SEPARATION GUARD makes 'keep' the
    default whenever the AI is missing or below `threshold` confidence — so a legit port
    or an uncertain call is never auto-separated.

    `ai_verdict` shape: {same_as_group: bool, correct_igdb_id: int|None, detach: bool,
    confidence: float}."""
    if det["kind"] == "unique":
        return {"action": "set", "igdb_id": det["igdb_id"]}
    v = ai_verdict or {}
    if float(v.get("confidence") or 0) < threshold:
        return {"action": "keep", "igdb_id": primary_id}      # guard: default keep
    if v.get("same_as_group"):
        return {"action": "keep", "igdb_id": primary_id}
    if v.get("detach"):
        return {"action": "detach", "igdb_id": None}
    if v.get("correct_igdb_id"):
        return {"action": "set", "igdb_id": int(v["correct_igdb_id"])}
    return {"action": "keep", "igdb_id": primary_id}


def plan_title(nk, primary_id, entries, candidates, adjudicate=None, threshold=0.75):
    """Plan per-entry identity for one title group. Pure orchestration — the caller
    supplies real IGDB candidates and an `adjudicate` callable; this fetches nothing and
    writes nothing.

      entries    = [{platform, title?, filename?, year?}] — one per platform entry.
      candidates = [{id, name, year, platforms:[name,..]}] — the exact-title IGDB games.
      adjudicate = callable(items)->[{n, same_as_group, correct_igdb_id, detach,
                   confidence}] (e.g. ai.adjudicate_entry), or None.

    Returns [{platform, kind, action, igdb_id}] — action in {"set","detach","keep"}.
    Only NON-unique entries are sent to `adjudicate` (deterministic uniques never cost AI),
    batched in a single call."""
    dets = [per_entry_resolve(candidates, e["platform"], primary_id) for e in entries]
    verdicts = [None] * len(entries)
    need = [i for i, d in enumerate(dets) if d["kind"] != "unique"]
    if need and adjudicate:
        items = [{"n": k, "title": entries[i].get("title", nk),
                  "platform": entries[i]["platform"],
                  "filename": entries[i].get("filename"),
                  "year": entries[i].get("year"),
                  "primary_id": primary_id, "candidates": candidates}
                 for k, i in enumerate(need)]
        for v in (adjudicate(items) or []):
            n = v.get("n")
            if isinstance(n, int) and 0 <= n < len(need):
                verdicts[need[n]] = v
    out = []
    for i, e in enumerate(entries):
        vr = combine_verdict(dets[i], verdicts[i], primary_id, threshold)
        out.append({"platform": e["platform"], "kind": dets[i]["kind"], **vr})
    return out


def _consoles_by_norm():
    """{norm_key: {console, ...}} — every EMULATION console each game has a ROM on, for
    the era gate. ONLY emulation sources: console_eras is keyed by emulation platform
    labels, and a store 'xbox'/'pc' ownership row spans all generations (an Xbox One
    game is not the 2001 Xbox), so store platforms must never year-restrict."""
    lib = sqlite3.connect(LIB)
    out = {}
    for nk, plat in lib.execute(
            "SELECT g.norm_key, s.platform FROM games g JOIN sources s "
            "ON s.game_id=g.id WHERE s.source='emulation'"):
        if plat:
            out.setdefault(nk, set()).add(plat)
    lib.close()
    return out


def era_reheal(argv):
    """Fix ALREADY-resolved games whose IGDB match is era-impossible for their
    console(s) — the same-name/different-era collisions that predate the era-aware
    matcher (Apple II 'Alice in Wonderland' bound to the 2010 film game). For each we
    re-search era-aware (binding the correct earlier game if IGDB has one, else leaving
    it unmatched), then purge the game's stale platform-neutral remote art from the
    media index so its wrong cover/background drops on the next media rebuild.

      python3 igdb_enrich.py --era-reheal            # heal + purge
      python3 igdb_enrich.py --era-reheal --dry-run  # report only, change nothing
    """
    dry = "--dry-run" in argv
    if not config.metadata_enabled("igdb"):
        print("igdb: disabled — nothing to reheal", file=sys.stderr)
        return
    cid, csec = config.igdb_creds()
    if not (cid and csec):
        print("igdb: no credentials — cannot reheal", file=sys.stderr)
        return
    if not os.path.exists(LIB):
        print("igdb: no catalog yet — run build_library.py first.", file=sys.stderr)
        return
    con = cache_con()
    tok = token(con, cid, csec)
    now = int(time.time())
    consoles = _consoles_by_norm()

    # candidates: resolved AND living on at least one era-bound emulation console
    # (store-only games are never era-gated, so skip them).
    cand = []                       # [(norm_key, igdb_id)]
    for nk, iid in con.execute(
            "SELECT norm_key, igdb_id FROM igdb_resolution "
            "WHERE igdb_id>0 AND matched_by!='manual'"):   # never re-heal a hand pin
        cs = consoles.get(nk)
        if cs and any(console_eras.era(c) for c in cs):
            cand.append((nk, iid))

    # matched year per igdb_id: cache-first, then FETCH the rest. Most resolutions have
    # no cached metadata payload (meta is fetched lazily), so relying on the cache alone
    # would miss the vast majority of era-impossible matches — batch-fetch by id.
    yr = {}
    for iid, payload in con.execute("SELECT igdb_id, payload_json FROM igdb_meta"):
        try:
            g = json.loads(payload) if payload else None
        except (ValueError, TypeError):
            g = None
        if g:
            yr[iid] = _year_of(g)
    need_ids = sorted({iid for _, iid in cand})
    miss = [i for i in need_ids if i not in yr]
    for i in range(0, len(miss), 200):
        batch = miss[i:i + 200]
        body = ("fields id,first_release_date; where id = (%s); limit 500;"
                % ",".join(str(x) for x in batch))
        try:
            for g in igdb.query("games", body, cid, tok):
                yr[g["id"]] = _year_of(g)
        except Exception as e:
            print("igdb reheal: year fetch batch failed: %s" % e, file=sys.stderr)
    print("igdb reheal: %d resolved game(s) on era-bound consoles | %d years fetched"
          % (len(cand), len(miss)), file=sys.stderr)

    # resolved games whose matched year can't exist on ANY of their era-bound consoles
    suspect = []
    for nk, iid in cand:
        y = yr.get(iid)
        if y is not None and not _era_ok(consoles.get(nk), y):
            suspect.append((nk, iid, y))
    print("igdb reheal: %d resolved game(s) have an era-impossible match"
          % len(suspect), file=sys.stderr)

    affected, rebound = [], []
    for nk, old_iid, y in suspect:
        title = nk.replace('"', " ").strip()
        try:
            hits = igdb.query(
                "games",
                'search "%s"; fields id,name,slug,first_release_date,alternative_names.name; limit 8;'
                % title, cid, tok)
        except Exception:
            hits = []
        new_iid, slug = _pick_era_aware(hits, nk, consoles.get(nk))
        cons = ",".join(sorted(consoles.get(nk) or []))
        print("  %-40s [%s] #%d(y%s) -> %s"
              % (nk[:40], cons, old_iid, y,
                 ("#%d" % new_iid if new_iid else "UNMATCHED")), file=sys.stderr)
        affected.append(nk)
        if new_iid:
            rebound.append(nk)
        if not dry:
            con.execute(
                "INSERT OR REPLACE INTO igdb_resolution"
                "(norm_key,igdb_id,slug,matched_by,resolved_at) VALUES(?,?,?,?,?)",
                (nk, new_iid or 0, slug, "era_reheal" if new_iid else "era_reject",
                 now))
    if not dry:
        con.commit()
    con.close()

    # purge the affected games' stale platform-neutral remote art (the wrong cover /
    # background came from the old match). A subsequent media_fetch rebuilds igdb/steam
    # from the corrected resolutions; deleting steamgriddb neutral rows here stops a
    # name-collision grid re-winning. Own-console ROM art (system=<console>) is kept.
    purged = 0
    if not dry and affected and os.path.exists(INDEX):
        mi = sqlite3.connect(INDEX)
        ph = ",".join("?" * len(affected))
        cur = mi.execute(
            "DELETE FROM media WHERE norm_key IN (%s) "
            "AND COALESCE(system,'')='' AND provider IN ('igdb','steam','steamgriddb')"
            % ph, affected)
        purged = cur.rowcount
        mi.commit()
        mi.close()
    print("igdb reheal: %d affected | %d rebound to a correct game | %d unmatched | "
          "%d stale neutral media rows purged%s"
          % (len(affected), len(rebound), len(affected) - len(rebound), purged,
             "  (dry-run: no changes written)" if dry else ""), file=sys.stderr)


def scrub_fuzzy(argv):
    """One-time cleanup for LEGACY fuzzy mis-matches — `matched_by='name'` resolutions
    left by the pre-2026-07-15 top-hit matcher, where the norm_key sits only as an
    INTERIOR word of an unrelated longer IGDB title ('journey' -> 'The Sims 4: Journey to
    Batuu', 'ball' -> 'Dragon Ball Z'). A normal resolve pass never revisits these (they're
    already 'resolved'), so they need an explicit sweep. Anchored subtitle/prefix variants
    ('1943' -> '1943: The Battle of Midway') are KEPT — the strict matcher would wrongly
    drop those. Dry-run by default; --apply resets the hits to unmatched so a rebuild
    re-derives game_key and the strict resolver retries.

      --max-tokens N   only norm_keys with <= N tokens (default 1 = single common words,
                       the highest-precision egregious class; multi-word interior can be a
                       real match, e.g. 'ghost recon future soldier')
      --include-norun  also clear non-contiguous 'norun' matches (riskier; default off)
      --apply          write the changes (default: print what WOULD change)"""
    max_tokens = int(argv[argv.index("--max-tokens") + 1]) if "--max-tokens" in argv else 1
    include_norun = "--include-norun" in argv
    do_apply = "--apply" in argv
    con = cache_con()
    meta = {}
    for iid, pj in con.execute("SELECT igdb_id, payload_json FROM igdb_meta"):
        try:
            meta[iid] = json.loads(pj)
        except Exception:
            pass
    hit = []
    for nk, iid in con.execute("SELECT norm_key, igdb_id FROM igdb_resolution "
                               "WHERE igdb_id>0 AND matched_by='name'"):
        if len(nk.split()) > max_tokens:
            continue
        g = meta.get(iid)
        if not g:
            continue
        names = [g.get("name", "")] + [
            (a.get("name") if isinstance(a, dict) else a)
            for a in (g.get("alternative_names") or [])]
        cls = _name_anchor_class(nk, names)
        if cls == "interior" or (include_norun and cls == "norun"):
            hit.append((nk, iid, g.get("name"), cls))
    hit.sort()
    for nk, iid, name, cls in hit:
        print("  %-8s %-30s -> igdb:%-8s %s" % (cls, nk, iid, name))
    print("scrub-fuzzy: %d legacy fuzzy mis-match(es) [max_tokens=%d, include_norun=%s]"
          % (len(hit), max_tokens, include_norun), file=sys.stderr)
    if do_apply and hit:
        now = int(time.time())
        for nk, _iid, _name, _cls in hit:
            con.execute("UPDATE igdb_resolution SET igdb_id=0, slug=NULL, "
                        "matched_by='scrubbed', resolved_at=? WHERE norm_key=?", (now, nk))
        _commit(con)
        print("scrub-fuzzy: cleared %d -> unmatched. Rebuild the catalog to re-derive "
              "game_key (build_library), then they read as their filename title." % len(hit),
              file=sys.stderr)
    elif hit:
        print("scrub-fuzzy: DRY-RUN — pass --apply to clear these.", file=sys.stderr)
    con.close()


def main(argv):
    if "--era-reheal" in argv:
        return era_reheal(argv)
    if "--scrub-fuzzy" in argv:
        return scrub_fuzzy(argv)
    do_all = "--all" in argv
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None

    if not config.metadata_enabled("igdb"):
        print("igdb: disabled (config.py enable igdb)", file=sys.stderr)
        return
    cid, csec = config.igdb_creds()
    if not (cid and csec):
        print("igdb: no Twitch credentials — set igdb_client_id/igdb_client_secret. "
              "See README. Skipping.", file=sys.stderr)
        return
    if not os.path.exists(LIB):
        print("igdb: no catalog yet — run build_library.py first.", file=sys.stderr)
        return

    con = cache_con()
    tok = token(con, cid, csec)
    ttl = int(config.get("igdb_meta_ttl_days") or 30) * 86400
    now = int(time.time())

    # ---- worklist: each game's norm_key, title, and a Steam appid if present ----
    lib = sqlite3.connect(LIB)
    games = {nk: {"title": title, "appid": None, "consoles": set()}
             for nk, title in lib.execute(
                 "SELECT norm_key, canonical_title FROM games")}
    # Every EMULATION console a norm_key has a ROM on — the era-aware matcher rejects
    # candidates whose release year is impossible for ALL of them (an Apple II ROM
    # can't be a 2010 game). ONLY emulation sources feed this: console_eras is keyed by
    # emulation platform labels, and a store 'xbox'/'pc' ownership row spans every
    # generation (Xbox One games are not the 2001 Xbox), so store platforms must never
    # year-restrict. A game owned only on stores has no era-bound console -> never gated.
    for nk, plat in lib.execute(
            "SELECT g.norm_key, s.platform FROM games g JOIN sources s "
            "ON s.game_id=g.id WHERE s.source='emulation'"):
        if nk in games and plat:
            games[nk]["consoles"].add(plat)
    for nk, sid in lib.execute(
            "SELECT g.norm_key, s.source_id FROM games g JOIN sources s "
            "ON s.game_id=g.id WHERE s.source='steam'"):
        if nk in games and sid and str(sid).isdigit():
            games[nk]["appid"] = str(sid)
    # Wishlist-WANTED Steam games carry their appid in the `wanted` table (they
    # have no owned source row) — use it so wanted items resolve by appid too,
    # not just by name-search.
    try:
        for nk, sid in lib.execute(
                "SELECT g.norm_key, w.store_id FROM games g JOIN wanted w "
                "ON w.game_id=g.id WHERE w.store='steam'"):
            if nk in games and sid and str(sid).isdigit() and not games[nk]["appid"]:
                games[nk]["appid"] = str(sid)
    except sqlite3.OperationalError:
        pass                                # older catalog without a wanted table
    # --source <id> (repeatable): restrict the worklist to games owned OR wanted
    # via those store sources. A store's Full refresh scopes IGDB to that store's
    # games instead of re-resolving the whole (ROM-dominated) catalog.
    srcs = [argv[i + 1] for i, a in enumerate(argv)
            if a == "--source" and i + 1 < len(argv)]
    if srcs:
        ph = ",".join("?" * len(srcs))
        keep = {nk for (nk,) in lib.execute(
            "SELECT DISTINCT g.norm_key FROM games g JOIN sources s "
            "ON s.game_id=g.id WHERE s.source IN (%s)" % ph, srcs)}
        try:                                # include wishlist-wanted games for the store
            keep |= {nk for (nk,) in lib.execute(
                "SELECT DISTINCT g.norm_key FROM games g JOIN wanted w "
                "ON w.game_id=g.id WHERE w.store IN (%s)" % ph, srcs)}
        except sqlite3.OperationalError:
            pass
        games = {nk: v for nk, v in games.items() if nk in keep}
        print("igdb: scoped to %s (owned+wanted) -> %d games"
              % (",".join(srcs), len(games)), file=sys.stderr)
    lib.close()

    resolved = {}                       # norm_key -> igdb_id (found, >0)
    failed_at = {}                      # norm_key -> when a name-search last failed (id=0)
    # A hand-pinned identity is the user's explicit decision and outranks anything we can
    # work out. --all ("Full refresh") re-resolves everything EXCEPT these, otherwise a
    # routine full sync silently reverts every manual pin — and can even leave one
    # UNMATCHED if the name search now misses. The wand's own apply already skips them;
    # this is the CLI/full-sync path catching up.
    pinned = {nk for (nk,) in con.execute(
        "SELECT norm_key FROM igdb_resolution WHERE matched_by='manual'")}
    if pinned:
        print("igdb: %d manually pinned game(s) left untouched" % len(pinned),
              file=sys.stderr)
    if not do_all:
        for nk, iid, rat in con.execute(
                "SELECT norm_key, igdb_id, resolved_at FROM igdb_resolution"):
            if iid and iid > 0:
                resolved[nk] = iid
            else:
                failed_at[nk] = rat or 0
    # Re-attempt previously-FAILED matches so a normal ("New") sync keeps trying
    # to fill unmatched games — picking up titles IGDB has since added. Default 0
    # = retry every run (a name search is one cheap request; failures are few);
    # set igdb_fail_retry_days>0 to only retry misses older than N days if IGDB
    # call volume ever matters. --all (Full) re-resolves everything regardless.
    fail_ttl = int(config.get("igdb_fail_retry_days") or 0) * 86400
    todo = [nk for nk in games
            if nk not in resolved and nk not in pinned
            and (nk not in failed_at or now - failed_at[nk] >= fail_ttl)]
    print("igdb: %d games | %d already resolved | %d to resolve"
          % (len(games), len(resolved), len(todo)), file=sys.stderr)

    # ---- pass 1: resolve by Steam appid via external_games (batched) ----
    appid_map = {}                      # appid -> norm_key
    for nk in todo:
        a = games[nk]["appid"]
        if a:
            appid_map.setdefault(a, nk)
    appids = list(appid_map)
    for i in range(0, len(appids), 200):
        batch = appids[i:i + 200]
        uids = ",".join('"%s"' % a for a in batch)
        body = ("fields game,uid; where external_game_source = %d "
                "& uid = (%s); limit 500;" % (igdb.STEAM_SOURCE, uids))
        for row in igdb.query("external_games", body, cid, tok):
            nk, gid = appid_map.get(str(row.get("uid"))), row.get("game")
            if nk and gid:
                con.execute(
                    "INSERT OR REPLACE INTO igdb_resolution"
                    "(norm_key,igdb_id,slug,matched_by,resolved_at) "
                    "VALUES(?,?,?,?,?)", (nk, gid, None, "steam_appid", now))
                resolved[nk] = gid
        _commit(con)
    if appids:
        print("igdb: resolved %d of %d games that had a Steam appid"
              % (sum(1 for a in appids if appid_map[a] in resolved), len(appids)),
              file=sys.stderr)

    # ---- pass 2: resolve the rest by name search (1 request each) ----
    remaining = [nk for nk in todo if nk not in resolved]
    if limit is not None:
        remaining = remaining[:limit]
    for n, nk in enumerate(remaining, 1):
        title = games[nk]["title"].replace('"', " ").strip()
        iid, slug = 0, None
        try:
            hits = igdb.query(
                "games",
                'search "%s"; fields id,name,slug,first_release_date,alternative_names.name; limit 8;'
                % title, cid, tok)
        except Exception as e:          # one bad title shouldn't abort the run
            # ...but it must not be recorded as an ANSWER either. This used to set
            # hits=[] and fall through to the write below, stamping matched_by='none' —
            # so a transient IGDB failure became a stored "IGDB has no match for this
            # game", indistinguishable from a real miss and sticky until something
            # cleared it. Skip the row entirely; the next run retries it.
            print("igdb_enrich: search failed for %r (%s) — leaving unresolved, will "
                  "retry" % (nk, str(e)[:80]), file=sys.stderr)
            continue
        # Accept ONLY an exact normalized-title match that is era-plausible for the
        # game's console(s). We used to fall back to IGDB's top fuzzy hit when nothing
        # matched, but `search` ranks by relevance and would bind e.g. "gods" (Bitmap
        # Bros, 1991) -> "God of War Ragnarök", renaming the ROM and poisoning its
        # metadata. norm() folds case, punctuation, articles, roman numerals, &/+, and
        # editions, so an exact match still catches real spelling variants; the era
        # gate then rejects same-name/different-era collisions (Apple II 'Alice in
        # Wonderland' vs. the 2010 film game). A genuine miss stays unmatched (keeps its
        # filename title) rather than adopting a wrong game. (User decisions 2026-07-15/16.)
        iid, slug = _pick_era_aware(hits, nk, games[nk]["consoles"],
                                    require_unique=bool(games[nk]["appid"]))
        con.execute("INSERT OR REPLACE INTO igdb_resolution"
                    "(norm_key,igdb_id,slug,matched_by,resolved_at) "
                    "VALUES(?,?,?,?,?)",
                    (nk, iid or 0, slug, "name" if iid else "none", now))
        if iid:
            resolved[nk] = iid
        print("PROG\t%d\t%d\t%s\tresolve" % (n, len(remaining), nk), flush=True)
        if n % 50 == 0:
            _commit(con)
            print("igdb: name-search %d/%d" % (n, len(remaining)), file=sys.stderr)
    _commit(con)

    # ---- fetch metadata for resolved ids that are missing or stale ----
    want = sorted({iid for iid in resolved.values() if iid})
    have = {}
    if not do_all:
        for iid, ts in con.execute("SELECT igdb_id, fetched_at FROM igdb_meta"):
            have[iid] = ts
    need = [iid for iid in want if iid not in have or now - have[iid] > ttl]
    print("igdb: %d games linked to a record | fetching %d record(s)"
          % (len(want), len(need)), file=sys.stderr)
    for i in range(0, len(need), 200):
        batch = need[i:i + 200]
        body = ("fields %s; where id = (%s); limit 500;"
                % (igdb.GAME_FIELDS, ",".join(str(x) for x in batch)))
        for g in igdb.query("games", body, cid, tok):
            con.execute("INSERT OR REPLACE INTO igdb_meta"
                        "(igdb_id,payload_json,fetched_at) VALUES(?,?,?)",
                        (g["id"], json.dumps(g, ensure_ascii=False), now))
        _commit(con)
        print("PROG\t%d\t%d\t\tfetch" % (min(i + 200, len(need)), len(need)), flush=True)
        print("igdb: fetched %d/%d" % (min(i + 200, len(need)), len(need)),
              file=sys.stderr)
    con.close()
    print("igdb: done.", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
