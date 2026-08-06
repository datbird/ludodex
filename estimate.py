#!/usr/bin/env python3
"""How long an ingest will take, from what this instance has actually measured.

Not a generic multiplier. Every rate below was timed on a real run of this library on
2026-08-04/05, and — more importantly — the estimate is computed against the CURRENT
state rather than the game count, because most of an ingest's cost is work it can skip:

  * a game with a recorded provider identity costs nothing to match again;
  * a game whose Steam attributes are cached costs no store call;
  * a game already vision-judged at this depth is never re-billed.

So the honest question is never "how long for 2,257 games" but "how long for the 400
that still need something", and that is what this answers.

Ranges, not point estimates, because the variance is real and one-sided: a ScreenScraper
HIT is about ten seconds and a MISS is about two minutes, since a title SS does not have
falls through to the slow cross-system search. A library of obscure PC titles genuinely
takes several times longer than the same number of well-known console games, and a
single number would be a lie in both directions.
"""
import os
import sqlite3

DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("LUDODEX_DATA", DIR)

# ---------------------------------------------------------------- measured rates
# SECONDS OF WORK per game, before concurrency. Divide by the worker count to get wall
# clock. Provenance is given for each so a later reading can be compared against the
# same thing rather than replacing a number nobody can source.
RATES = {
    # ScreenScraper name search. 209-title collision re-match took 40 min at 2 workers
    # (23 s/title of work); a later 83-title pass took 11 min at 2 workers (16 s/title).
    # The high end is the PC-heavy case: no `pc` system id, so every miss pays the
    # ~49 s cross-system search, and misses are common for modern PC titles.
    "match": (16.0, 30.0),
    # SteamGridDB resolves by Steam appid, which is a single fast lookup. Full library
    # re-verify: 2,257 titles in 3 min at 6 workers = 0.5 s/title.
    "sgdb": (0.4, 0.8),
    # Steam appdetails. 2,101 records in 54 min, single-threaded and deliberately so —
    # 1.5 s is Steam's own ~200-request/5-minute budget, not a number worth shaving.
    "steam_attrs": (1.5, 1.7),
    # Media fetch + measure + prune, per game. 203 games in 79 min and 73 in 22 min,
    # both serial: 18-23 s each, dominated by pulling and measuring candidate images.
    #
    # THE LEAST TRUSTWORTHY RATE HERE, and the one that dominates a reset. Both timings
    # come from the per-game `_enrich_media` path (a wand-style reconcile after dropping
    # each game's provider media), which is the right WORKLOAD for a reset but not the
    # code path a reset uses: the import runs `media_fetch.py` as whole-library
    # subprocesses instead, and batch work usually beats per-game work. Treat this as an
    # upper bound until a full import has been timed, and widen the low end to say so.
    "media": (12.0, 24.0),
    # One vision call. Full library: 2,257 in 262 min = 7.0 s. Console-art pass, which
    # judges more candidates per game: 218 in 37 min = 10.2 s.
    "vision": (7.0, 10.5),
    # Catalog rebuild — whole-library, not per game. Measured at 11 s on 2,257 games.
    "build": (10.0, 30.0),
}

# Wall-clock divisors. Steam attributes are deliberately NOT parallel (see above).
WORKERS = {"match": 6, "sgdb": 6, "steam_attrs": 1, "media": 1, "vision": 4}


def _count(db, sql, args=()):
    p = os.path.join(DATA, db)
    if not os.path.exists(p):
        return 0
    try:
        c = sqlite3.connect("file:%s?mode=ro" % p, uri=True)
        try:
            return c.execute(sql, args).fetchone()[0] or 0
        finally:
            c.close()
    except sqlite3.OperationalError:
        return 0


def _phase(name, games, workers=None, per=None):
    lo, hi = per or RATES[name]
    w = max(1, workers if workers is not None else WORKERS.get(name, 1))
    return {"phase": name, "games": games,
            "low": int(games * lo / w), "high": int(games * hi / w)}


def plan(tier="lite", workers=None, total=None, fresh=False):
    """Per-phase second estimates for the next ingest.

    `tier` — algo | lite | heavy. Algo makes no model calls at all, so its vision phase
    is zero rather than small: that is the tier's definition, and an estimate that showed
    Algo costing AI time would be describing a bug.

    `fresh` — a library RESET, where every cache is deleted, so nothing can be skipped
    and every game pays full price. This is the number worth showing before a reset,
    and it is very different from the resync number.
    """
    workers = workers or {}
    total = total if total is not None else _count(
        "game-library.sqlite", "SELECT COUNT(DISTINCT norm_key) FROM games")

    if fresh:
        need_match = need_sgdb = need_steam = need_media = need_vision = total
    else:
        # only what is not already recorded — the whole point
        have_match = _count("metadata-cache.sqlite",
                            "SELECT COUNT(*) FROM ss_resolution")
        have_sgdb = _count("metadata-cache.sqlite",
                           "SELECT COUNT(*) FROM sgdb_resolution")
        have_steam = _count("steam-meta.sqlite", "SELECT COUNT(*) FROM steam_meta")
        have_vision = _count("media-index.sqlite",
                             "SELECT COUNT(*) FROM art_adjudicated")
        need_match = max(0, total - have_match)
        need_sgdb = max(0, total - have_sgdb)
        need_steam = max(0, total - have_steam)
        need_vision = max(0, total - have_vision)
        # media is re-reconciled for everything touched, not only new games
        need_media = need_match + need_steam

    phases = [
        _phase("match", need_match, workers.get("match")),
        _phase("sgdb", need_sgdb, workers.get("sgdb")),
        _phase("steam_attrs", need_steam, workers.get("steam_attrs")),
        _phase("media", need_media, workers.get("media")),
        _phase("build", 1, 1),
    ]
    if tier in ("lite", "heavy"):
        # Heavy judges every kind, not just covers — roughly four kinds with a real
        # choice per game, hence the multiplier rather than a separate rate.
        mult = 4 if tier == "heavy" else 1
        phases.append(_phase("vision", need_vision * mult, workers.get("vision")))
    else:
        phases.append({"phase": "vision", "games": 0, "low": 0, "high": 0})

    return {"tier": tier, "games": total, "fresh": bool(fresh), "phases": phases,
            "low": sum(p["low"] for p in phases),
            "high": sum(p["high"] for p in phases)}


def human(seconds):
    """A duration a person can act on. Deliberately coarse — claiming "2h 14m" from
    rates with a two-to-one spread would be false precision."""
    s = max(0, int(seconds))
    if s < 90:
        return "under a minute" if s < 60 else "about a minute"
    m = s / 60.0
    if m < 60:
        return "%d min" % (5 * round(m / 5)) if m >= 10 else "%d min" % round(m)
    h = m / 60.0
    return "%.1f hours" % h if h < 10 else "%d hours" % round(h)


def summary(p):
    """One line: what the user is signing up for."""
    if p["high"] < 60:
        return "under a minute"
    lo, hi = human(p["low"]), human(p["high"])
    return lo if lo == hi else "%s to %s" % (lo, hi)
