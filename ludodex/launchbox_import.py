#!/usr/bin/env python3
"""Import an existing LaunchBox install into ludodex.

Two outputs, mirroring how Playnite is consumed:
  1. A canonical interchange JSON (`launchbox_import_json`, default
     ludodex_from_launchbox.json) that build_library.py merges as a frontend
     META-LAYER — each game maps to its underlying provider; "in LaunchBox" is
     provenance only (in_launchbox flag).
  2. Its `Images/<Platform>/<MediaType>/` art indexed by REFERENCE into
     media-index.sqlite as provider 'launchbox', so your hand-curated LaunchBox
     art can win/seed the chosen set just like ES-DE or Steam.

LaunchBox is plain XML + media folders, so no bridge is needed (cf. Playnite).

  python3 ludodex/launchbox_import.py [LAUNCHBOX_ROOT] [--no-media] [--json OUT]
"""
import os
import sys
import json
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import launchbox as lb
from titlenorm import norm

# LaunchBox <Game> multi-value tag -> interchange list field (semicolon-split)
_MULTI = {"Genre": "genres", "Developer": "developers", "Publisher": "publishers",
          "Series": "series", "PlayMode": "game_modes"}
# infer the underlying provider from a LaunchBox <Source> / launcher hint
_SOURCE_HINT = {"steam": "steam", "gog": "gog", "epic": "epic",
                "origin": "ea", "ea": "ea", "itch": "itch"}


def _provider_of(g, platform):
    src = (g.findtext("Source") or g.findtext("Status") or "").lower()
    for hint, prov in _SOURCE_HINT.items():
        if hint in src:
            return prov
    app = (g.findtext("ApplicationPath") or "").lower()
    for hint, prov in _SOURCE_HINT.items():
        if hint in app:
            return prov
    # otherwise treat by platform: Windows-ish => a PC manual entry, else emulation
    if lb.from_lb_platform(platform) == platform.lower() and "windows" in platform.lower():
        return "manual"
    return "emulation"


def parse_platforms(root):
    """Yield interchange records from every Data/Platforms/*.xml."""
    pdir = os.path.join(root, "Data", "Platforms")
    if not os.path.isdir(pdir):
        return
    for fn in sorted(os.listdir(pdir)):
        if not fn.lower().endswith(".xml"):
            continue
        try:
            tree = ET.parse(os.path.join(pdir, fn))
        except ET.ParseError as e:
            print("launchbox_import: skip %s (%s)" % (fn, e), file=sys.stderr)
            continue
        for g in tree.getroot().findall("Game"):
            title = (g.findtext("Title") or "").strip()
            if not title:
                continue
            platform = (g.findtext("Platform") or os.path.splitext(fn)[0]).strip()
            provider = _provider_of(g, platform)
            rec = {"name": title,
                   "source": provider,
                   "source_id": g.findtext("DatabaseID") or g.findtext("ID") or "",
                   "platforms": [lb.from_lb_platform(platform)]}
            for tag, field in _MULTI.items():
                v = g.findtext(tag)
                if v:
                    rec[field] = [s.strip() for s in v.split(";") if s.strip()]
            notes = g.findtext("Notes")
            if notes:
                rec["description"] = notes.strip()
            rd = g.findtext("ReleaseDate")
            if rd and len(rd) >= 4 and rd[:4].isdigit():
                rec["release_year"] = int(rd[:4])
                rec["release_date"] = rd[:10]
            yield rec


def scan_images(con, root, owned, now):
    """Index Images/<Platform>/<MediaType>/<Title>.<ext> as provider 'launchbox'."""
    con.execute("DELETE FROM media WHERE provider='launchbox'")
    idir = os.path.join(root, "Images")
    rows = matched = 0
    if not os.path.isdir(idir):
        return 0, 0
    for platform in sorted(os.listdir(idir)):
        pdir = os.path.join(idir, platform)
        if not os.path.isdir(pdir):
            continue
        system = lb.from_lb_platform(platform)
        for mtype in sorted(os.listdir(pdir)):
            mdir = os.path.join(pdir, mtype)
            if not os.path.isdir(mdir):
                continue
            kind = lb.lb_kind(mtype)     # unknown folders -> 'other' (+ logged)
            for dirpath, _d, files in os.walk(mdir):   # recurse Region subfolders
                for fn in files:
                    base, ext = os.path.splitext(fn)
                    ext = ext.lower().lstrip(".")
                    if ext not in ("png", "jpg", "jpeg", "webp", "gif"):
                        continue
                    # strip LaunchBox's -01/-02 multi-image suffix before matching
                    stem = base.rsplit("-", 1)[0] if base[-3:-2] == "-" and \
                        base[-2:].isdigit() else base
                    nk = norm(stem)
                    if not nk:
                        continue
                    is_match = nk in owned
                    con.execute(
                        "INSERT OR REPLACE INTO media(norm_key,system,kind,provider,"
                        "mount,ref_type,ref,ext,matched,indexed_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (nk, system, kind, "launchbox", "launchbox", "file",
                         os.path.join(dirpath, fn), ext, int(is_match), now))
                    rows += 1
                    matched += int(is_match)
    con.commit()
    return rows, matched


def main(argv):
    do_media = "--no-media" not in argv
    pos = [a for a in argv if not a.startswith("--")
           and argv[argv.index(a) - 1] != "--json"]
    root = (pos[0] if pos else config.get("launchbox_path"))
    if not root or not os.path.isdir(root):
        sys.exit("set launchbox_path or pass the LaunchBox root (not found: %r)" % root)
    out = (argv[argv.index("--json") + 1] if "--json" in argv
           else config.get("launchbox_import_json")
           or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "ludodex_from_launchbox.json"))

    records = list(parse_platforms(root))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)
    print("launchbox_import: %d games -> %s" % (len(records), out), file=sys.stderr)

    if do_media:
        import time
        import media_index
        owned = {norm(r["name"]) for r in records}
        db = config.get("library_db")
        if db and os.path.exists(db):
            import sqlite3
            c = sqlite3.connect(db)
            owned |= {k for (k,) in c.execute("SELECT norm_key FROM games")}
            c.close()
        con = media_index.index_con()
        n, m = scan_images(con, root, owned, int(time.time()))
        con.close()
        print("launchbox_import: indexed %d media files (%d matched) as 'launchbox'"
              % (n, m), file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
