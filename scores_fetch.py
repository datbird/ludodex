#!/usr/bin/env python3
"""Gather rating scores from every source ludodex knows and roll them into the
unified Ludodex score. Each source's ORIGINAL rating is retained verbatim in the
durable `ratings` table (scores.sqlite); `game_scores` holds the computed roll-up.

Sources
  igdb          critic (aggregated_rating) + user (derived from total_rating);
                vote counts only after an IGDB re-enrich that requests them.
  steam         user = % positive from the public appreviews API (+ review count,
                which powers the confidence weighting). --metacritic also pulls
                the Metacritic critic score from appdetails (doubles the calls).
  gog           user = average rating (reviews.gog.com), 0-5 -> 0-100.
  screenscraper user = note (0-20 -> 0-100), read from the local SS scrape cache.

  recompute     recompute game_scores from ratings (also runs after each source).

  python3 scores_fetch.py all            # every source, then recompute
  python3 scores_fetch.py igdb           # one source
  python3 scores_fetch.py steam --metacritic --refresh --limit 200
"""
import json
import os
import ssl
import sys
import time
import sqlite3
import urllib.request
import urllib.parse

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
import config          # noqa: E402
import scoring         # noqa: E402

SCORES_DB = os.path.join(DIR, "scores.sqlite")
CTX = ssl.create_default_context()
FRESH_DAYS = 7          # skip a (game,source) refreshed within this window


def log(m):
    print(m, file=sys.stderr, flush=True)


def _con():
    con = sqlite3.connect(SCORES_DB)
    con.execute("PRAGMA journal_mode=WAL")   # readers (server) don't block on our writes
    con.execute("""CREATE TABLE IF NOT EXISTS ratings(
        norm_key TEXT, source TEXT, kind TEXT, score REAL, votes INTEGER,
        raw TEXT, updated REAL, PRIMARY KEY(norm_key, source, kind))""")
    con.execute("""CREATE TABLE IF NOT EXISTS game_scores(
        norm_key TEXT PRIMARY KEY, universal INTEGER, critic INTEGER,
        user INTEGER, n_sources INTEGER, updated REAL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS steam_type(
        norm_key TEXT PRIMARY KEY, type TEXT, updated REAL)""")
    con.row_factory = sqlite3.Row
    return con


# Steam appdetails `type` values that are NOT games — excluded from scoring/top lists.
NON_GAME_TYPES = {"application", "tool", "music", "video", "hardware", "series", "mod"}


def _put(con, norm_key, source, kind, score, votes, raw):
    con.execute("INSERT OR REPLACE INTO ratings VALUES(?,?,?,?,?,?,?)",
                (norm_key, source, kind,
                 None if score is None else float(score),
                 None if votes is None else int(votes),
                 raw, time.time()))


def _recent(con, norm_key, source):
    r = con.execute("SELECT MAX(updated) FROM ratings WHERE norm_key=? AND source=?",
                    (norm_key, source)).fetchone()[0]
    return r is not None and (time.time() - r) < FRESH_DAYS * 86400


def _http_json(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "ludodex/1.0"})
    with urllib.request.urlopen(req, context=CTX, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _library_ids(source):
    """[(norm_key, source_id)] for a store source, from the game library."""
    db = config.get("library_db")
    if not db or not os.path.exists(db):
        sys.exit("no catalog at %r — build the library first" % db)
    c = sqlite3.connect(db)
    rows = c.execute(
        "SELECT g.norm_key, s.source_id FROM sources s JOIN games g ON g.id=s.game_id "
        "WHERE s.source=? AND s.source_id!=''", (source,)).fetchall()
    c.close()
    # de-dup norm_key -> first id
    seen, out = set(), []
    for nk, sid in rows:
        if nk not in seen:
            seen.add(nk)
            out.append((nk, sid))
    return out


# --------------------------------------------------------------------------- IGDB
def fetch_igdb(con, refresh=False, limit=None):
    cache = os.path.join(DIR, "metadata-cache.sqlite")
    if not os.path.exists(cache):
        log("igdb: no metadata cache")
        return 0
    mc = sqlite3.connect(cache)
    try:
        rows = mc.execute(
            "SELECT r.norm_key, m.payload_json FROM igdb_resolution r "
            "JOIN igdb_meta m ON m.igdb_id=r.igdb_id WHERE r.igdb_id>0").fetchall()
    except sqlite3.OperationalError:
        rows = []
    mc.close()
    n = 0
    for nk, payload in rows:
        if limit and n >= limit:
            break
        try:
            g = json.loads(payload)
        except ValueError:
            continue
        crit = g.get("aggregated_rating")
        total = g.get("total_rating")
        user = g.get("rating")
        if user is None and crit is not None and total is not None:
            user = max(0.0, min(100.0, 2 * total - crit))   # recover from the blend
        elif user is None and total is not None and crit is None:
            user = total
        if crit is not None:
            _put(con, nk, "igdb", "critic", round(crit),
                 g.get("aggregated_rating_count"), "%d/100" % round(crit))
            n += 1
        if user is not None:
            _put(con, nk, "igdb", "user", round(user),
                 g.get("rating_count"), "%d/100" % round(user))
            n += 1
    con.commit()
    log("igdb: %d rating rows from cache" % n)
    return n


# ---------------------------------------------------------------------- ScreenScraper
def fetch_ss(con, refresh=False, limit=None):
    cache = os.path.join(DIR, "screenscraper-cache.sqlite")
    if not os.path.exists(cache):
        log("screenscraper: no scrape cache on this host (Deck-side) — skipped")
        return 0
    sc = sqlite3.connect(cache)
    try:
        rows = sc.execute("SELECT norm_key, payload_json FROM ss_game "
                          "WHERE status='ok' AND payload_json IS NOT NULL").fetchall()
    except sqlite3.OperationalError:
        rows = []
    sc.close()
    n = 0
    for nk, payload in rows:
        if limit and n >= limit:
            break
        try:
            note = json.loads(payload).get("note")
        except ValueError:
            note = None
        val = note.get("text") if isinstance(note, dict) else note
        try:
            v = float(val)
        except (TypeError, ValueError):
            continue
        _put(con, nk, "screenscraper", "user", round(v / 20 * 100), None, "%s/20" % val)
        n += 1
    con.commit()
    log("screenscraper: %d note rows" % n)
    return n


# --------------------------------------------------------------------------- Steam
def fetch_steam(con, refresh=False, limit=None, metacritic=False):
    ids = _library_ids("steam")
    cooldown = int(os.environ.get("STEAM_STORE_COOLDOWN_MS", "1300")) / 1000.0
    n = done = 0
    for nk, appid in ids:
        if limit and done >= limit:
            break
        if not refresh and _recent(con, nk, "steam"):
            continue
        done += 1
        # user score: % positive + review count
        try:
            q = _http_json("https://store.steampowered.com/appreviews/%s?json=1"
                           "&language=all&purchase_type=all&filter=summary"
                           "&num_per_page=0" % appid).get("query_summary", {})
            tot = q.get("total_reviews") or 0
            pos = q.get("total_positive") or 0
            if tot:
                _put(con, nk, "steam", "user", round(pos / tot * 100), tot,
                     q.get("review_score_desc") or ("%d%% of %d" % (round(pos/tot*100), tot)))
                n += 1
        except Exception as e:
            log("  steam appreviews %s failed: %s" % (appid, e))
        time.sleep(cooldown)
        if metacritic:
            try:
                d = _http_json("https://store.steampowered.com/api/appdetails?appids=%s"
                               "&filters=metacritic" % appid).get(str(appid), {})
                mc = (d.get("data") or {}).get("metacritic") if d.get("success") else None
                if mc and mc.get("score"):
                    _put(con, nk, "metacritic", "critic", round(mc["score"]), None,
                         "%d (Metacritic)" % round(mc["score"]))
                    n += 1
            except Exception as e:
                log("  steam appdetails %s failed: %s" % (appid, e))
            time.sleep(cooldown)
        if done % 50 == 0:
            con.commit()
            log("  steam: %d/%d games…" % (done, len(ids)))
    con.commit()
    log("steam: %d rating rows across %d games" % (n, done))
    return n


# -------------------------------------------------------------------- Steam app type
def fetch_steam_types(con, refresh=False, limit=None):
    """Record each Steam entry's appdetails `type` (game / application / tool / …)
    so non-games can be excluded from scores and top lists."""
    ids = _library_ids("steam")
    cooldown = int(os.environ.get("STEAM_STORE_COOLDOWN_MS", "1300")) / 1000.0
    n = done = 0
    for nk, appid in ids:
        if limit and done >= limit:
            break
        if not refresh:
            r = con.execute("SELECT updated FROM steam_type WHERE norm_key=?",
                            (nk,)).fetchone()
            if r and (time.time() - r[0]) < FRESH_DAYS * 86400:
                continue
        done += 1
        try:
            d = _http_json("https://store.steampowered.com/api/appdetails?appids=%s"
                           "&filters=basic" % appid).get(str(appid), {})
            typ = (d.get("data") or {}).get("type") if d.get("success") else None
            if typ:
                con.execute("INSERT OR REPLACE INTO steam_type VALUES(?,?,?)",
                            (nk, typ.lower(), time.time()))
                n += 1
        except Exception as e:
            log("  steam type %s failed: %s" % (appid, e))
        time.sleep(cooldown)
        if done % 50 == 0:
            con.commit()
            log("  steam types: %d/%d…" % (done, len(ids)))
    con.commit()
    log("steam types: %d recorded" % n)
    return n


# ---------------------------------------------------------------------------- GOG
def fetch_gog(con, refresh=False, limit=None):
    ids = _library_ids("gog")
    cooldown = int(os.environ.get("GOG_COOLDOWN_MS", "800")) / 1000.0
    n = done = 0
    for nk, pid in ids:
        if limit and done >= limit:
            break
        if not refresh and _recent(con, nk, "gog"):
            continue
        done += 1
        try:
            avg = _http_json("https://reviews.gog.com/v1/products/%s/averageRating" % pid)
            val = avg.get("value")
            if val:
                _put(con, nk, "gog", "user", round(float(val) / 5 * 100), None,
                     "%.1f/5" % float(val))
                n += 1
        except Exception as e:
            log("  gog %s failed: %s" % (pid, e))
        time.sleep(cooldown)
    con.commit()
    log("gog: %d rating rows across %d games" % (n, done))
    return n


# ----------------------------------------------------------------------- recompute
def _params():
    def f(key, d):
        try:
            return float(config.get(key) or d)
        except (TypeError, ValueError):
            return d
    return {
        "w_critic": f("ludodex_critic_weight", 0.6),
        "prior_mean": f("ludodex_score_prior", 70.0),
        "n_full_user": f("ludodex_score_full_votes", 400.0),
        "no_critic_factor": f("ludodex_no_critic_factor", 0.6),
    }


def recompute(con):
    params = _params()
    non_games = {r[0] for r in con.execute(
        "SELECT norm_key FROM steam_type WHERE type IN (%s)"
        % ",".join("?" * len(NON_GAME_TYPES)), tuple(NON_GAME_TYPES))}
    by_game = {}
    for r in con.execute("SELECT norm_key, kind, score, votes FROM ratings"):
        by_game.setdefault(r["norm_key"], []).append(
            {"kind": r["kind"], "score": r["score"], "votes": r["votes"]})
    con.execute("DELETE FROM game_scores")
    n = skipped = 0
    for nk, rs in by_game.items():
        if nk in non_games:            # Steam utility/app, not a game
            skipped += 1
            continue
        u, b = scoring.compute(rs, params)
        if u is None:
            continue
        con.execute("INSERT INTO game_scores VALUES(?,?,?,?,?,?)",
                    (nk, u, b.get("critic"), b.get("user"), len(rs), time.time()))
        n += 1
    con.commit()
    log("recompute: %d games scored, %d non-game apps excluded "
        "(w_critic=%.2f, n_full=%.0f, prior=%.0f, no_critic=%.2f)"
        % (n, skipped, params["w_critic"], params["n_full_user"],
           params["prior_mean"], params["no_critic_factor"]))
    return n


FETCHERS = {"igdb": fetch_igdb, "ss": fetch_ss, "steam": fetch_steam,
            "steamtype": fetch_steam_types, "gog": fetch_gog}


def main(argv):
    refresh = "--refresh" in argv
    metacritic = "--metacritic" in argv
    limit = None
    for i, a in enumerate(argv):
        if a == "--limit" and i + 1 < len(argv):
            limit = int(argv[i + 1])
    cmds = [a for a in argv if not a.startswith("--") and not a.isdigit()]
    cmd = cmds[0] if cmds else "all"
    con = _con()
    try:
        if cmd == "recompute":
            recompute(con)
            return
        targets = ["igdb", "ss", "gog", "steam", "steamtype"] if cmd == "all" else [cmd]
        for t in targets:
            fn = FETCHERS.get(t)
            if not fn:
                sys.exit("unknown source %r" % t)
            if t == "steam":
                fn(con, refresh=refresh, limit=limit, metacritic=metacritic)
            else:
                fn(con, refresh=refresh, limit=limit)
        recompute(con)
    finally:
        con.close()


if __name__ == "__main__":
    main(sys.argv[1:])
