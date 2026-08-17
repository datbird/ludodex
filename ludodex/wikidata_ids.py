#!/usr/bin/env python3
"""Cross-database ids from Wikidata — free, CC0, and nobody's content but a coordinate.

A CROSS-REFERENCE TABLE IS A POINTER, NOT CONTENT. "This game is MobyGames #1234" carries
none of MobyGames' prose, art or curation; it is the coordinate you use to go and ASK
them. That distinction is what lets the shipped supplement carry ids from sources whose
DATA it could never redistribute — and Wikidata itself is CC0, so the pointers have no
strings on them at all.

Measured 2026-08-17: 33,956 items carry both a MobyGames id and an IGDB id, 60,376 carry
a Redump id, 128,502 a Steam appid, 1,460 a TheGamesDB id. That last number is why this
is not a substitute for a TheGamesDB walk — but 33,956 MobyGames pointers for zero
requests and zero dollars is a real head start on one.

THE JOIN IS ON THE IGDB SLUG, AND THAT IS DELIBERATE. Wikidata stores IGDB's slug
(`bulletstorm`), not its numeric id, and the IGDB mirror already carries both — so the
slug is an EXACT key we can resolve locally, exactly like a hash. Nothing here matches on
a title, and nothing here invents an identity: a row whose IGDB slug we do not recognise
is skipped, because the whole value of this file is that every link is anchored to a game
we already know.

ONE IGDB GAME CAN CARRY SEVERAL POINTERS. `bulletstorm` maps to both `bulletstorm` and
`bulletstorm-full-clip-edition` on MobyGames — an edition split, and both are legitimate
coordinates for the same identity. They are all attached; picking one would be inventing
an opinion about someone else's catalogue.
"""
import io
import os
import sys
import time
import urllib.parse
import urllib.request

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
import config                       # noqa: E402

DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
CACHE = os.path.join(DATA, "wikidata-ids.csv")
ENDPOINT = "https://query.wikidata.org/sparql"

# Wikidata property -> the namespace we file it under. Only properties that are an
# EXACT external identifier belong here; anything fuzzy would undo the point.
PROPS = [
    ("P1933", "mobygames"),
    ("P7622", "thegamesdb"),
    ("P1733", "steam"),
    ("P2725", "gog"),
    ("P9968", "redump"),
]

# Their endpoint is free and unauthenticated, and it is also a shared public service with
# a 60-second query timeout. One query for the whole set would time out; one per property
# keeps each well inside it and makes a partial failure cost one namespace, not all five.
QUERY = """SELECT ?igdb ?val WHERE {
  ?i wdt:P5794 ?igdb .
  ?i wdt:%s ?val .
}"""

CACHE_TTL_DAYS = 30
# Wikidata asks for a descriptive User-Agent with contact details; an anonymous scraper
# is what gets a tool blocked from a service that costs nothing to use politely.
UA = "ludodex/1.0 (https://github.com/datbird/ludodex)"


def enabled():
    return config.get_bool("matchindex_wikidata_ids", True)


def cache_ttl():
    try:
        d = int((config.get("wikidata_ids_cache_days") or "").strip())
    except (TypeError, ValueError):
        d = 0
    return max(1, d or CACHE_TTL_DAYS) * 24 * 3600


def wanted():
    """Which namespaces to pull. Steam and GOG are OFF by default — IGDB already
    publishes 666,417 store ids and its own are authoritative, so re-importing them
    from a third party adds rows without adding knowledge."""
    raw = (config.get("wikidata_ids_namespaces") or "").strip()
    got = [x.strip() for x in raw.split(",") if x.strip()]
    return got or ["mobygames", "thegamesdb", "redump"]


def fetch(force=False, timeout=180):
    """Pull every wanted namespace into one CSV cache. Returns its path, or None.

    NEVER RAISES. This is an optional layer of an optional index; a rebuild must not die
    because a public SPARQL endpoint was busy, and yesterday's copy is still true."""
    if os.path.exists(CACHE) and not force:
        if (time.time() - os.path.getmtime(CACHE)) < cache_ttl():
            return CACHE
    rows = []
    for prop, ns in PROPS:
        if ns not in wanted():
            continue
        try:
            url = ENDPOINT + "?" + urllib.parse.urlencode({"query": QUERY % prop})
            req = urllib.request.Request(url, headers={"Accept": "text/csv",
                                                       "User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", "replace")
        except Exception as e:                      # noqa: BLE001
            print("wikidata_ids: %s (%s) failed — %s" % (ns, prop, str(e)[:90]),
                  file=sys.stderr)
            continue
        n = 0
        for line in body.splitlines()[1:]:          # skip the CSV header
            slug, _, val = line.partition(",")
            slug, val = slug.strip().strip('"'), val.strip().strip('"')
            if slug and val:
                rows.append((ns, slug, val))
                n += 1
        print("wikidata_ids: %-12s %d pointers" % (ns, n), file=sys.stderr)
    if not rows:
        return CACHE if os.path.exists(CACHE) else None
    tmp = CACHE + ".part"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        fh.write("ns,igdb_slug,value\n")
        for ns, slug, val in rows:
            fh.write("%s,%s,%s\n" % (ns, slug.replace(",", ""), val.replace(",", "")))
    os.replace(tmp, CACHE)
    return CACHE


def rows(path=None):
    """Yield (ns, igdb_slug, value). Malformed lines are skipped, never fatal."""
    p = path or CACHE
    if not p or not os.path.exists(p):
        return
    with io.open(p, encoding="utf-8", errors="replace") as fh:
        first = True
        for line in fh:
            if first:
                first = False
                continue
            parts = line.rstrip("\n").split(",")
            if len(parts) < 3:
                continue
            ns, slug, val = parts[0].strip(), parts[1].strip(), parts[2].strip()
            if ns and slug and val:
                yield ns, slug, val


def stats(path=None):
    import collections
    c = collections.Counter(ns for ns, _s, _v in rows(path))
    return {"total": sum(c.values()), "by_namespace": dict(c),
            "cached": os.path.exists(CACHE)}


def main(argv):
    if "--fetch" in argv:
        p = fetch(force="--force" in argv)
        print("wikidata_ids: %s" % (p or "unavailable"))
    s = stats()
    print("pointers : %s" % "{:,}".format(s["total"]))
    for ns, n in sorted(s["by_namespace"].items()):
        print("  %-12s %s" % (ns, "{:,}".format(n)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
