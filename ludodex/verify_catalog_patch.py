"""Verify catalog_patch.merge / split against the authoritative build_library.

Method: copy live /data → a scratch copy, apply the SURGICAL patch, snapshot the affected
(norm_key, platform) rows, then run the REAL build_library on the same copy and re-read
those rows. If the surgical patch matched build_library's derivation, the rows are IDENTICAL
across the compared columns. Any diff is printed. Runs inside the container (PYTHONPATH=/app,
inputs read from LUDODEX_DATA=the copy). Scratch lives on the appdata volume, cleaned up.
"""
import os
import sys
import glob
import shutil
import sqlite3
import subprocess

sys.path.insert(0, "/app")
LIVE = "/data"
VC = "/data/_vc"
COLS = ("canonical_title", "norm_key", "platform", "base_key", "game_key",
        "n_sources", "n_kinds", "sources_summary", "has_emulation", "has_steam",
        "has_gog", "has_epic", "has_itch", "has_archive", "wanted")


def fresh_copy():
    subprocess.run(["rsync", "-a", "--delete", "--exclude", "media",
                    "--exclude", "device-media", "--exclude", "_vc", "--exclude", "tmp",
                    LIVE + "/", VC + "/"], check=True)


def rows_for(db, keys):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    out = {}
    for nk in keys:
        for r in con.execute("SELECT %s FROM games WHERE norm_key=?" % ",".join(COLS), (nk,)):
            out[(r["norm_key"], r["platform"])] = {c: r[c] for c in COLS}
        for r in con.execute(
                "SELECT s.source, s.source_id, s.platform FROM sources s JOIN games g "
                "ON s.game_id=g.id WHERE g.norm_key=?", (nk,)):
            out.setdefault(("__src__", nk), set()).add((r[0], str(r[1]), r[2]))
    con.close()
    return out


def diff(a, b, label):
    keys = set(a) | set(b)
    bad = 0
    for k in sorted(keys, key=str):
        if a.get(k) != b.get(k):
            bad += 1
            print("  DIFF %s %s\n    surgical: %s\n    rebuild : %s"
                  % (label, k, a.get(k), b.get(k)))
    return bad


def pick_merge_pair(db):
    con = sqlite3.connect(db)
    plats = {}
    for nk, p in con.execute("SELECT norm_key, platform FROM games"):
        plats.setdefault(nk, set()).add(p)
    con.close()
    items = list(plats.items())
    for a_nk, a_p in items:
        for b_nk, b_p in items:
            if a_nk == b_nk:
                continue
            if (a_p & b_p) and (b_p - a_p):     # shared plat + a plat to re-key
                return a_nk, b_nk               # to_key, from_key
    return None, None


def verify_merge():
    fresh_copy()
    db = VC + "/game-library.sqlite"
    to_key, from_key = pick_merge_pair(db)
    if not to_key:
        print("MERGE: no suitable pair found"); return
    print("MERGE to_key=%r  from_key=%r" % (to_key, from_key))
    con = sqlite3.connect(db)
    to_title = con.execute("SELECT canonical_title FROM games WHERE norm_key=? LIMIT 1",
                           (to_key,)).fetchone()[0]
    con.close()
    os.environ["LUDODEX_DATA"] = VC
    import importlib
    import merges
    importlib.reload(merges)
    import catalog_patch
    merges.DB = os.path.join(VC, "merges.sqlite")   # point durable store at the copy
    merges.add(from_key, to_key, "", to_title)
    merges.rekey_user_data(from_key, to_key)
    con = sqlite3.connect(db)
    catalog_patch.merge(con, from_key, to_key, to_title, VC)
    con.close()
    before = rows_for(db, [to_key, from_key])
    r = subprocess.run(["python", "/app/ludodex/build_library.py"], env={**os.environ,
                       "LUDODEX_DATA": VC}, capture_output=True, text=True)
    if r.returncode != 0:
        print("build_library FAILED:", r.stderr[-500:]); return
    after = rows_for(db, [to_key, from_key])
    n = diff(before, after, "merge")
    print("MERGE: %s (%d diffs)" % ("PASS" if n == 0 else "FAIL", n))


def verify_split():
    fresh_copy()
    db = VC + "/game-library.sqlite"
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    # a game with >= 2 sources → peel one off
    row = con.execute(
        "SELECT g.norm_key FROM games g JOIN sources s ON s.game_id=g.id "
        "GROUP BY g.id HAVING COUNT(*)>=2 LIMIT 1").fetchone()
    if not row:
        print("SPLIT: no multi-source game"); con.close(); return
    from_key = row["norm_key"]
    srcs = con.execute(
        "SELECT s.source, s.source_id FROM sources s JOIN games g ON s.game_id=g.id "
        "WHERE g.norm_key=?", (from_key,)).fetchall()
    con.close()
    picked = [(srcs[0]["source"], str(srcs[0]["source_id"]))]
    to_key = "zzverifysplit_" + from_key
    to_title = "Verify Split " + from_key
    print("SPLIT from_key=%r  to_key=%r  peel=%s" % (from_key, to_key, picked))
    os.environ["LUDODEX_DATA"] = VC
    import importlib
    import splits
    importlib.reload(splits)
    import catalog_patch
    splits.DB = os.path.join(VC, "splits.sqlite")
    splits.add_many(picked, to_key, to_title, from_key)
    con = sqlite3.connect(db)
    catalog_patch.split(con, from_key, to_key, to_title, picked, VC)
    con.close()
    before = rows_for(db, [from_key, to_key])
    r = subprocess.run(["python", "/app/ludodex/build_library.py"], env={**os.environ,
                       "LUDODEX_DATA": VC}, capture_output=True, text=True)
    if r.returncode != 0:
        print("build_library FAILED:", r.stderr[-500:]); return
    after = rows_for(db, [from_key, to_key])
    n = diff(before, after, "split")
    print("SPLIT: %s (%d diffs)" % ("PASS" if n == 0 else "FAIL", n))


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    if which in ("merge", "both"):
        verify_merge()
    if which in ("split", "both"):
        verify_split()
    shutil.rmtree(VC, ignore_errors=True)
