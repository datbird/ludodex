#!/usr/bin/env python3
"""No-Intro and Redump dump databases, via libretro-database. Free, and the only source
here that carries a DISC SERIAL.

WHY THIS MATTERS MORE THAN ANOTHER METADATA PROVIDER: ludodex has no serial namespace at
all. It resolves on crc and sha1, and both of those describe a FILE — so the moment a disc
is re-encoded they stop matching. That is not a rare edge case: `publish` converts to CHD
and RVZ on purpose, and most people's PlayStation and GameCube collections are already
stored that way. A CHD of Final Fantasy VII has a checksum that appears in no dump
database on earth.

The serial does not have that problem. `SLUS-00594` is printed on the disc and written
inside the image, and it survives every re-encode, because it is a property of the GAME
rather than of the bytes we happen to be holding.

Redump publishes them and libretro vendors the DATs. Measured 2026-08-16: 22 Redump
systems (~60,659 dumps, PlayStation alone 13,592, every one carrying `serial "SLPS-01204"`)
and 92 No-Intro systems (~113,562 dumps). CC-BY-SA-4.0, one raw fetch per system, no key,
no quota, and the repository was pushed the day it was measured.

THE RULE THIS FILE FOLLOWS: A DAT ENRICHES AN IDENTITY, IT NEVER INVENTS ONE. These files
are keyed by ROM filename, and minting an identity per dump would add a hundred thousand
entries named after files rather than games. So a dump attaches its serial to whatever
identity already owns its hash, and a dump nobody recognises is simply skipped — it will
be there next rebuild, when the mirrors may know more.
"""
import io
import os
import re
import sys
import time
import urllib.parse
import urllib.request

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
import config                       # noqa: E402

DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
CACHE_DIR = os.path.join(DATA, "libretro-dats")
RAW = "https://raw.githubusercontent.com/libretro/libretro-database/master/metadat/%s/%s"
TREE = ("https://api.github.com/repos/libretro/libretro-database/git/trees/"
        "master?recursive=1")

# Redump first: it is the collection that carries serials, and it is a quarter the size.
COLLECTIONS = ("redump", "no-intro")

# The DATs change when a dump is added or corrected — real, but not weekly.
CACHE_TTL = 30 * 24 * 3600

SOURCE = {
    "name": "libretro-database (No-Intro / Redump DATs)",
    "url": "https://github.com/libretro/libretro-database",
    "license": "CC BY-SA 4.0",
    "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
    "provides": "canonical ROM/disc dumps: crc/md5/sha1, region, and disc serials",
}

# clrmamepro is not XML and not JSON. It is a nested s-expression-ish format, and the only
# two shapes we need are the `game (` header and the `rom (` line inside it.
_GAME = re.compile(r'^\s*game\s*\(\s*$')
_END = re.compile(r'^\s*\)\s*$')
_KV = re.compile(r'^\s*(\w+)\s+"([^"]*)"\s*$')
_ROM = re.compile(r'^\s*rom\s*\(\s*(.*?)\s*\)\s*$')
# Inside a rom line the values are a mix of bare tokens and quoted strings.
_ROM_KV = re.compile(r'(\w+)\s+(?:"([^"]*)"|(\S+))')


def enabled():
    return config.get_bool("matchindex_libretro_dats", True)


def _cache_path(collection, name):
    d = os.path.join(CACHE_DIR, collection)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def systems(collection, timeout=60):
    """Every .dat filename in a collection, from the repo tree. [] on failure."""
    try:
        req = urllib.request.Request(TREE, headers={"User-Agent": "ludodex",
                                                    "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            import json
            tree = json.loads(r.read().decode("utf-8"))
        pre = "metadat/%s/" % collection
        return sorted(t["path"][len(pre):] for t in tree.get("tree", [])
                      if t.get("path", "").startswith(pre)
                      and t["path"].endswith(".dat"))
    except Exception as e:                          # noqa: BLE001
        print("libretro_dats: tree listing failed (%s)" % str(e)[:110], file=sys.stderr)
        return []


def fetch(collection, name, timeout=120):
    """One DAT, cached. Returns its path, or None. Never raises."""
    p = _cache_path(collection, name)
    if os.path.exists(p) and (time.time() - os.path.getmtime(p)) < CACHE_TTL:
        return p
    url = RAW % (collection, urllib.parse.quote(name))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ludodex"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
        if len(body) < 40:
            raise ValueError("empty (%d bytes)" % len(body))
        tmp = p + ".part"
        with open(tmp, "wb") as fh:
            fh.write(body)
        os.replace(tmp, p)
        return p
    except Exception as e:                          # noqa: BLE001
        print("libretro_dats: %s/%s failed (%s)" % (collection, name, str(e)[:90]),
              file=sys.stderr)
        return p if os.path.exists(p) else None     # a stale copy beats none


def parse(path):
    """Yield {name, region, serial, crc, md5, sha1} per ROM entry.

    A game block can hold several rom lines (a multi-track disc), and each carries its own
    hashes — so every track is yielded separately, all sharing the game's serial. That is
    deliberate: a user who owns only track 1 still has a file whose hash we can resolve."""
    if not path or not os.path.exists(path):
        return
    game = {}
    in_game = False
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if _GAME.match(line):
                game, in_game = {}, True
                continue
            if not in_game:
                continue
            if _END.match(line):
                in_game = False
                continue
            m = _ROM.match(line)
            if m:
                kv = {}
                for k, qv, bv in _ROM_KV.findall(m.group(1)):
                    kv[k.lower()] = qv if qv else bv
                yield {"name": game.get("name") or kv.get("name") or "",
                       "region": game.get("region") or "",
                       # The rom line carries its own serial on multi-disc sets; the
                       # game-level one is the fallback, never the override.
                       "serial": (kv.get("serial") or game.get("serial") or "").strip(),
                       "crc": (kv.get("crc") or "").lower(),
                       "md5": (kv.get("md5") or "").lower(),
                       "sha1": (kv.get("sha1") or "").lower()}
                continue
            m = _KV.match(line)
            if m:
                game[m.group(1).lower()] = m.group(2)


def all_rows(collections=COLLECTIONS, refresh=False, progress=False):
    """Every dump across every cached system. Fetches what is missing."""
    for coll in collections:
        names = systems(coll) if refresh or not os.path.isdir(
            os.path.join(CACHE_DIR, coll)) else sorted(
                os.listdir(os.path.join(CACHE_DIR, coll)))
        names = [n for n in names if n.endswith(".dat")]
        for i, name in enumerate(names):
            p = fetch(coll, name)
            if not p:
                continue
            if progress:
                print("libretro_dats: %s %d/%d %s" % (coll, i + 1, len(names), name),
                      file=sys.stderr)
            for row in parse(p):
                row["collection"] = coll
                row["system"] = name[:-4]
                yield row


def stats():
    n = serials = hashed = 0
    systems_seen = set()
    for r in all_rows():
        n += 1
        systems_seen.add((r["collection"], r["system"]))
        if r["serial"]:
            serials += 1
        if r["sha1"] or r["crc"]:
            hashed += 1
    return {"dumps": n, "with_serial": serials, "with_hash": hashed,
            "systems": len(systems_seen)}


def main(argv):
    refresh = "--refresh" in argv
    if "--fetch" in argv or refresh:
        for coll in COLLECTIONS:
            names = systems(coll)
            print("libretro_dats: %s — %d systems" % (coll, len(names)), file=sys.stderr)
            for name in names:
                fetch(coll, name)
    s = stats()
    print("dumps        : %s" % "{:,}".format(s["dumps"]))
    print("with a serial: %s" % "{:,}".format(s["with_serial"]))
    print("with a hash  : %s" % "{:,}".format(s["with_hash"]))
    print("systems      : %s" % s["systems"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
