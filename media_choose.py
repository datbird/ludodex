#!/usr/bin/env python3
"""Choose the single best media asset per (game, kind), then materialize it.

The hybrid model: media-index.sqlite holds every asset by REFERENCE; this script
(1) marks the winning asset per game+scalar-kind via media.PRIORITY (instant,
pure SQL), and (2) materializes ONLY those chosen assets into a local
content-addressed repo (media_repo/<sha1>.<ext>) — copying local files and
downloading URLs, verifying as it goes and demoting any dead reference to the
next-best candidate. The server/exporters then serve chosen assets from local
bytes, falling back to the live reference for anything not yet materialized.

  python3 media_choose.py                       # select chosen (no downloads)
  python3 media_choose.py --materialize          # + pull chosen bytes into repo
  python3 media_choose.py --materialize --kind cover --limit 500
"""
import hashlib
import os
import shutil
import sqlite3
import sys
import time
import urllib.request

DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("LUDODEX_DATA", DIR)
sys.path.insert(0, DIR)
import config
import media

INDEX = os.path.join(DATA, "media-index.sqlite")


def repo_dir():
    # Bulk, regenerable media (content-addressed <sha1>.<ext>). Its own knob so it
    # can live on separate/larger storage than the small critical DBs in DATA:
    #   env LUDODEX_MEDIA  >  config media_repo  >  <DATA>/media (default)
    p = (os.environ.get("LUDODEX_MEDIA", "").strip()
         or config.get("media_repo") or os.path.join(DATA, "media"))
    os.makedirs(p, exist_ok=True)
    return p


def con_index():
    con = sqlite3.connect(INDEX)
    con.row_factory = sqlite3.Row
    return con


def select(con, kinds=None):
    """Set chosen=1 on the best asset per (norm_key, scalar kind); 0 elsewhere.
    `kinds` restricts the pass to a subset of scalar kinds (non-destructive — other
    kinds keep their existing chosen flags), so a wand run can fill just covers."""
    scalar = [k for k in media.SCALAR_KINDS if not kinds or k in kinds]
    if not scalar:
        return 0
    if kinds:
        con.execute("UPDATE media SET chosen=0 WHERE kind IN (%s)"
                    % ",".join("'%s'" % k for k in scalar))
    else:
        con.execute("UPDATE media SET chosen=0")
    # playnite_media_overwrite=playnite-wins: your hand-curated Playnite art beats
    # every other provider for the slots Playnite owns, so it becomes the canonical
    # pick that propagates to the other frontends and the server.
    pn_wins = (config.get("playnite_media_overwrite") or "").lower() == "playnite-wins"
    rank = {}                       # (kind) -> {provider: order}
    for kind in scalar:
        order = list(media.priority(kind))
        if pn_wins and kind in ("cover", "background", "icon"):
            order = ["playnite"] + [p for p in order if p != "playnite"]
        rank[kind] = {p: i for i, p in enumerate(order)}
    rows = con.execute(
        "SELECT id, norm_key, kind, provider, matched, ref_type FROM media "
        "WHERE kind IN (%s)" % ",".join("'%s'" % k for k in scalar)
    ).fetchall()
    best = {}                       # (norm_key, kind) -> (sortkey, id)
    for r in rows:
        pr = rank[r["kind"]].get(r["provider"], 99)
        # tie-breakers: prefer a catalog-matched asset, then a local file over a
        # URL (faster, offline-safe), then lowest id (stable).
        sk = (pr, 0 if r["matched"] else 1, 0 if r["ref_type"] == "file" else 1,
              r["id"])
        key = (r["norm_key"], r["kind"])
        if key not in best or sk < best[key][0]:
            best[key] = (sk, r["id"])
    ids = [i for _, i in best.values()]
    con.executemany("UPDATE media SET chosen=1 WHERE id=?", [(i,) for i in ids])
    con.commit()
    return len(ids)


def _materialize_row(repo, r):
    """Pull one asset's bytes into the repo; return sha1 or None on failure."""
    try:
        if r["ref_type"] == "file":
            if not os.path.exists(r["ref"]):
                return None
            with open(r["ref"], "rb") as f:
                data = f.read()
        else:
            url = r["ref"]
            if r["provider"] == "screenscraper":   # ScreenScraper media needs auth
                creds = config.screenscraper_creds()
                if creds:
                    import screenscraper as ss
                    url = ss.media_url_with_auth(url, creds)
            req = urllib.request.Request(url, headers={"User-Agent": "ludodex"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
        if not data:
            return None
        sha = hashlib.sha1(data).hexdigest()
        ext = (r["ext"] or "jpg").split("?")[0]
        dest = os.path.join(repo, "%s.%s" % (sha, ext))
        if not os.path.exists(dest):
            tmp = dest + ".tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            shutil.move(tmp, dest)
        return sha
    except Exception:
        return None


def materialize(con, kind=None, limit=None, all_refs=False):
    """Download/copy assets lacking sha1 into the repo; demote dead refs and
    re-pick. Default = only the chosen asset per (game, kind); all_refs=True
    pulls EVERY candidate (a full local archive)."""
    repo = repo_dir()
    base = "(sha1 IS NULL OR sha1='')" if all_refs else "chosen=1 AND (sha1 IS NULL OR sha1='')"
    q = "SELECT * FROM media WHERE " + base
    if kind:
        q += " AND kind='%s'" % kind
    q += " ORDER BY ref_type"        # local files first (cheap), then URLs
    if limit:
        q += " LIMIT %d" % int(limit)
    rows = con.execute(q).fetchall()
    ok = dead = 0
    for r in rows:
        sha = _materialize_row(repo, r)
        if sha:
            con.execute("UPDATE media SET sha1=? WHERE id=?", (sha, r["id"]))
            ok += 1
        else:
            # dead reference: drop it from contention and promote the next best
            con.execute("DELETE FROM media WHERE id=?", (r["id"],))
            _repick(con, r["norm_key"], r["kind"])
            dead += 1
        if (ok + dead) % 200 == 0:
            con.commit()
            print("media_choose: materialized %d (%d dead) of %d"
                  % (ok, dead, len(rows)), file=sys.stderr)
    con.commit()
    return ok, dead


def _repick(con, norm_key, kind):
    """After a dead asset is removed, choose the next-best for this game+kind."""
    rank = {p: i for i, p in enumerate(media.priority(kind))}
    cands = con.execute("SELECT id, provider, matched, ref_type FROM media "
                        "WHERE norm_key=? AND kind=?", (norm_key, kind)).fetchall()
    if not cands:
        return
    best = min(cands, key=lambda r: (rank.get(r["provider"], 99),
                                     0 if r["matched"] else 1,
                                     0 if r["ref_type"] == "file" else 1, r["id"]))
    con.execute("UPDATE media SET chosen=1 WHERE id=?", (best["id"],))


def main(argv):
    kind = argv[argv.index("--kind") + 1] if "--kind" in argv else None
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    # --kinds a,b,c restricts the choose pass to those scalar kinds (non-destructive)
    kinds = (argv[argv.index("--kinds") + 1].split(",")
             if "--kinds" in argv else None)
    con = con_index()
    n = select(con, kinds=kinds)
    print("media_choose: selected %d chosen assets" % n, file=sys.stderr)
    if "--materialize" in argv:
        ok, dead = materialize(con, kind, limit, all_refs="--all" in argv)
        repo = repo_dir()
        sz = sum(os.path.getsize(os.path.join(repo, f)) for f in os.listdir(repo)
                 if not f.endswith(".tmp"))
        print("media_choose: materialized %d assets (%d dead refs demoted) -> %s "
              "(%.1f MB)" % (ok, dead, repo, sz / 1e6), file=sys.stderr)
    # coverage summary
    cov = con.execute(
        "SELECT kind, COUNT(*) FROM media WHERE chosen=1 GROUP BY kind "
        "ORDER BY 2 DESC").fetchall()
    for k, c in cov:
        print("    chosen %-13s %d" % (k, c), file=sys.stderr)
    con.close()


if __name__ == "__main__":
    main(sys.argv[1:])
