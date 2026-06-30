#!/usr/bin/env python3
"""Export the ludodex catalog into a LaunchBox install — Platform XMLs + chosen art.

LaunchBox is plain files on disk, so there is NO bridge (unlike Playnite/LiteDB):
we read-modify-write `<root>/Data/Platforms/<Platform>.xml` and drop chosen media
into `<root>/Images/<Platform>/<MediaType>/<SanitizedTitle>.<ext>`.

Safe-write contract (from LaunchBox's own behavior):
  * Each game's <ID> is a deterministic GUID from its norm_key (idempotent upsert —
    re-export updates in place, never duplicates).
  * We READ the existing Platform.xml, keep every <Game> we don't own (your
    manually-added titles), and only upsert ours. Never leave a .bak in
    Data/Platforms (LaunchBox can restore it and resurrect deleted games).
  * Close LaunchBox before exporting; it rewrites the whole file on next edit.

Media modes (the Unraid space question):
  copy  (default) materialize chosen bytes and copy into the Images tree.
  link            symlink Images/.../<Title>.<ext> -> media_repo/<sha1>.<ext>, so
                  one stored copy (e.g. on Unraid) backs every frontend. Use when
                  the LaunchBox Images folder shares a filesystem with media_repo.

  python3 launchbox_export.py [LAUNCHBOX_ROOT] [--link] [--no-media]
                              [--platform NAME] [--limit N]
"""
import os
import sys
import shutil
import sqlite3
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import launchbox as lb
import media_choose

# which catalog source identifies a game's platform / ROM path, best first
PROVIDER_RANK = ["steam", "gog", "epic", "itch", "ea", "emulation"]
PC_SOURCES = {"steam", "gog", "epic", "itch", "ea"}
# multi-value catalog kinds -> LaunchBox tag; scalars handled inline
_MULTI = {"genres": "Genre", "developers": "Developer", "publishers": "Publisher",
          "series": "Series", "game_modes": "PlayMode"}


def _text(parent, tag, value):
    el = parent.find(tag)
    if el is None:
        el = ET.SubElement(parent, tag)
    el.text = "" if value is None else str(value)


def _release_date(attrs):
    rd = (attrs.get("release_date") or [None])[0]
    if rd and len(str(rd)) >= 10 and str(rd)[4] == "-":
        return str(rd)[:10] + "T00:00:00"
    yr = (attrs.get("release_year") or [None])[0]
    if yr and str(yr).isdigit():
        return "%s-01-01T00:00:00" % yr
    return None


def lb_platform_for(game, srcs):
    """The LaunchBox platform name a game lands under, and its emulation system."""
    rows = srcs.get(game["id"], [])
    if game["has_emulation"]:
        for r in rows:
            if r["source"] == "emulation" and r["platform"]:
                return lb.to_lb_platform(r["platform"]), r["platform"]
    return "Windows", None       # PC-store titles live under the Windows platform


def load_catalog(con):
    attrs = {}
    for r in con.execute("SELECT game_id, kind, value FROM game_attributes"):
        attrs.setdefault(r["game_id"], {}).setdefault(r["kind"], []).append(r["value"])
    srcs = {}
    for r in con.execute("SELECT game_id, source, platform, source_id FROM sources"):
        srcs.setdefault(r["game_id"], []).append(r)
    games = list(con.execute(
        "SELECT id, canonical_title, norm_key, has_emulation FROM games"))
    return games, attrs, srcs


def build_game_el(existing, game, attrs):
    """Create/refresh a <Game> element for one catalog game (upsert by our GUID)."""
    a = attrs.get(game["id"], {})
    guid = lb.game_guid(game["norm_key"])
    el = existing.get(guid)
    if el is None:
        el = ET.Element("Game")
        _text(el, "ID", guid)
    _text(el, "Title", game["canonical_title"])
    for kind, tag in _MULTI.items():
        if a.get(kind):
            _text(el, tag, "; ".join(sorted(set(a[kind]))))
    if a.get("description"):
        _text(el, "Notes", a["description"][0])
    rd = _release_date(a)
    if rd:
        _text(el, "ReleaseDate", rd)
    for kind, tag in (("community_score", "CommunityStarRating"),
                      ("critic_score", "Rating")):
        if a.get(kind):
            try:                       # catalog stores 0-100; LB stars are 0-5
                v = max(float(x) for x in a[kind])
                _text(el, tag, "%.1f" % (v / 20.0) if kind == "community_score"
                      else str(int(v)))
            except ValueError:
                pass
    return guid, el


def write_platform_xml(path, owned_by_guid):
    """Read-modify-write: keep foreign <Game>s, upsert ours, write whole doc."""
    if os.path.exists(path):
        tree = ET.parse(path)
        root = tree.getroot()
    else:
        root = ET.Element("LaunchBox")
    existing = {}
    for g in root.findall("Game"):
        gid = g.findtext("ID")
        if gid:
            existing[gid] = g
    for guid, el in owned_by_guid.items():
        old = existing.get(guid)
        if old is not None and old is not el:
            root.remove(old)
        if el.find("Platform") is None:      # set by caller before this
            pass
        if old is not el:
            root.append(el)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def export_media(root, platform, title, norm_key, system, mode, mcon, repo):
    """Place chosen art for one game into Images/<Platform>/<MediaType>/."""
    q = ("SELECT * FROM media WHERE norm_key=? AND chosen=1 "
         "AND kind IN (%s)" % ",".join("'%s'" % k for k in lb.KIND_TO_LB))
    params = [norm_key]
    if system:
        q += " AND system=?"
        params.append(system)
    n = 0
    for r in mcon.execute(q, params).fetchall():
        folder = lb.KIND_TO_LB.get(r["kind"])
        if not folder:
            continue
        sha = r["sha1"] or media_choose._materialize_row(repo, r)
        if not sha:
            continue
        if not r["sha1"]:
            mcon.execute("UPDATE media SET sha1=? WHERE id=?", (sha, r["id"]))
        ext = (r["ext"] or "jpg").split("?")[0]
        src = os.path.join(repo, "%s.%s" % (sha, ext))
        if not os.path.exists(src):
            continue
        dest_dir = os.path.join(root, "Images", platform, folder)
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, lb.media_filename(title, ext))
        if os.path.lexists(dest):
            os.remove(dest)
        if mode == "link":
            os.symlink(os.path.abspath(src), dest)
        else:
            shutil.copy2(src, dest)
        n += 1
    return n


def main(argv):
    mode = "link" if "--link" in argv else "copy"
    do_media = "--no-media" not in argv
    only = argv[argv.index("--platform") + 1] if "--platform" in argv else None
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    pos = [a for a in argv if not a.startswith("--")
           and argv[argv.index(a) - 1] not in ("--platform", "--limit")]
    root = (pos[0] if pos else config.get("launchbox_path"))
    if not root:
        sys.exit("set launchbox_path (config.py set launchbox_path <LaunchBox root>) "
                 "or pass it as an argument")

    db = config.get("library_db")
    if not db or not os.path.exists(db):
        sys.exit("no catalog — run update.sh first")
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    games, attrs, srcs = load_catalog(con)

    mcon = repo = None
    if do_media:
        mcon = media_choose.con_index()
        repo = media_choose.repo_dir()

    # bucket games by LaunchBox platform, build their <Game> elements
    by_platform = {}                 # platform -> {guid: el}
    sys_of = {}                      # (platform, guid) -> emulation system
    title_of = {}                    # guid -> (title, norm_key)
    for g in games:
        platform, system = lb_platform_for(g, srcs)
        if only and platform != only:
            continue
        guid, el = build_game_el({}, g, attrs)
        _text(el, "Platform", platform)
        by_platform.setdefault(platform, {})[guid] = el
        sys_of[(platform, guid)] = system
        title_of[guid] = (g["canonical_title"], g["norm_key"])
        if limit and sum(len(v) for v in by_platform.values()) >= limit:
            break

    n_games = n_media = 0
    for platform, owned in sorted(by_platform.items()):
        path = os.path.join(root, "Data", "Platforms", "%s.xml" % platform)
        write_platform_xml(path, owned)
        n_games += len(owned)
        if do_media:
            for guid in owned:
                title, nk = title_of[guid]
                n_media += export_media(root, platform, title, nk,
                                        sys_of[(platform, guid)], mode, mcon, repo)
        print("launchbox: %-34s %4d games" % (platform, len(owned)),
              file=sys.stderr)
    if mcon:
        mcon.commit()
        mcon.close()
    con.close()
    print("launchbox_export: %d games across %d platforms, %d media files (%s) -> %s"
          % (n_games, len(by_platform), n_media, mode, root), file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
