#!/usr/bin/env python3
"""Build a SQLite index of an emulation ROM archive from a `find -printf`
metadata dump (size<TAB>mtime<TAB>relpath-under-roms). Filename tags are parsed
by the shared romtags module.

Usage: build_romdb.py <romscan.tsv> <out.sqlite> <roms_root_abs>
"""
import os
import sys
import sqlite3
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from romtags import parse_name, GENERIC_DIRS

TSV, DB, ROOT = sys.argv[1], sys.argv[2], sys.argv[3]

con = sqlite3.connect(DB)
cur = con.cursor()
cur.executescript("""
PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
DROP TABLE IF EXISTS roms;
CREATE TABLE roms (
  id INTEGER PRIMARY KEY,
  system TEXT, subdir TEXT, game TEXT, filename TEXT, ext TEXT,
  name TEXT, region TEXT, languages TEXT, version TEXT, revision TEXT,
  disc TEXT, flags TEXT, tags TEXT,
  relpath TEXT, fullpath TEXT, size_bytes INTEGER, mtime REAL
);
DROP TABLE IF EXISTS systems;
DROP TABLE IF EXISTS meta;
CREATE TABLE meta (key TEXT, value TEXT);
""")

root = ROOT.rstrip("/")
batch = []
n = 0
total_bytes = 0
INSERT = ("INSERT INTO roms(system,subdir,game,filename,ext,name,region,languages,"
          "version,revision,disc,flags,tags,relpath,fullpath,size_bytes,mtime)"
          " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
with open(TSV, "r", encoding="utf-8", errors="replace") as f:
    for line in f:
        line = line.rstrip("\n")
        if not line:
            continue
        try:
            size_s, mtime_s, rel = line.split("\t", 2)
        except ValueError:
            continue
        size = int(size_s) if size_s.isdigit() else 0
        try:
            mtime = float(mtime_s)
        except ValueError:
            mtime = 0.0
        parts = rel.split("/")
        system = parts[0] if parts else ""
        filename = parts[-1]
        mid = parts[1:-1]
        subdir = "/".join(mid)
        ext = filename.rsplit(".", 1)[1].lower() if "." in filename else ""
        name, region, languages, version, revision, disc, flags, tags = parse_name(filename)
        game = ""
        for c in mid:
            if c.lower() not in GENERIC_DIRS:
                game = c
                break
        if game:
            gn, gr, gl, gv, grev, gd, gf, gt = parse_name(game)
            region = region or gr
            languages = languages or gl
            version = version or gv
            revision = revision or grev
            disc = disc or gd
            flags = flags or gf
        else:
            game = name
        batch.append((system, subdir, game, filename, ext, name, region, languages,
                      version, revision, disc, flags, tags, rel,
                      root + "/" + rel, size, mtime))
        n += 1
        total_bytes += size
        if len(batch) >= 5000:
            cur.executemany(INSERT, batch)
            batch = []
if batch:
    cur.executemany(INSERT, batch)

cur.executescript("""
CREATE INDEX ix_system   ON roms(system);
CREATE INDEX ix_ext      ON roms(ext);
CREATE INDEX ix_name     ON roms(name);
CREATE INDEX ix_game     ON roms(system, game);
CREATE INDEX ix_region   ON roms(region);
CREATE TABLE systems AS
  SELECT system, COUNT(*) AS files, COUNT(DISTINCT game) AS games,
         SUM(size_bytes) AS bytes
  FROM roms GROUP BY system ORDER BY system;
""")
cur.execute("INSERT INTO meta(key,value) VALUES('root',?)", (root,))
cur.execute("INSERT INTO meta(key,value) VALUES('files',?)", (str(n),))
cur.execute("INSERT INTO meta(key,value) VALUES('total_bytes',?)", (str(total_bytes),))
cur.execute("INSERT INTO meta(key,value) VALUES('built_epoch',?)", (str(int(time.time())),))
con.commit()
cur.execute("VACUUM")
con.commit()
con.close()
print("rows=%d total_bytes=%d db=%s" % (n, total_bytes, DB))
