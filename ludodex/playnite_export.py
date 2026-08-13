#!/usr/bin/env python3
"""Export the ludodex catalog to the canonical Playnite interchange JSON
(see playnite.py). scripts/playnite_bridge.ps1 -Import reads it to create/enrich games.

One record per deduped game, merging attributes across its sources. When a game
was originally imported from Playnite, its lossless source_attrs record seeds the
output (round-trip); otherwise the record is synthesized from ludodex data.

Media (cover/background/icon): ludodex's CHOSEN art is materialized into a portable
bundle beside the JSON (<out>_media/), and each record carries JSON-relative paths
plus per-slot booleans computed from two config knobs:
  playnite_media_overwrite = gaps | all | playnite-wins   (how to treat existing
      Playnite art: fill only empty slots / always replace / never clobber +
      Playnite art wins as the chosen pick everywhere — see media_choose)
  playnite_icon_source     = logo | cover | none          (what to use as the icon)
Copy the JSON AND its _media/ folder to the Playnite machine, then run the bridge.

  python3 ludodex/playnite_export.py [out.json] [--no-media]
"""
import os
import sys
import json
import shutil
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import media_choose
from playnite import LIST_KINDS, SCALAR_KINDS

# which ludodex source to claim as the game's provider identity, best first
PROVIDER_RANK = ["steam", "gog", "epic", "itch", "ea", "ubisoft", "battlenet",
                 "xbox", "amazon", "playnite", "archive", "emulation"]
_NUM = {"playtime", "play_count", "user_score", "critic_score",
        "community_score", "install_size"}


def chosen_media(mcon):
    """norm_key -> {kind: row} for the chosen cover/background/logo assets."""
    by = {}
    for r in mcon.execute(
            "SELECT id, norm_key, kind, provider, sha1, ext, ref, ref_type FROM media "
            "WHERE chosen=1 AND kind IN ('cover','background','logo')"):
        by.setdefault(r["norm_key"], {})[r["kind"]] = r
    return by


def existing_playnite_art():
    """name(lower) -> set of slots Playnite already has, from the last -Export JSON."""
    src = config.get("playnite_import_json")
    have = {}
    if src and os.path.exists(src):
        try:
            for r in json.load(open(src, encoding="utf-8")) or []:
                nm = ("" + (r.get("name") or "")).lower()
                slots = {s for s in ("cover", "background", "icon") if r.get(s)}
                if nm:
                    have[nm] = slots
        except (ValueError, OSError):
            pass
    return have


def _bundle_file(mcon, repo, bundle, row):
    """Materialize a chosen asset and copy it into the bundle; return its filename."""
    sha = row["sha1"] or media_choose._materialize_row(repo, row)
    if not sha:
        return None
    if not row["sha1"]:
        mcon.execute("UPDATE media SET sha1=? WHERE id=?", (sha, row["id"]))
    ext = (row["ext"] or "jpg").split("?")[0]
    src = os.path.join(repo, "%s.%s" % (sha, ext))
    if not os.path.exists(src):
        return None
    fn = "%s.%s" % (sha, ext)
    dest = os.path.join(bundle, fn)
    if not os.path.exists(dest):
        shutil.copy2(src, dest)
    return fn


def attach_media(records, out):
    """Fill cover/background/icon paths + set_* flags on records, per policy."""
    policy = (config.get("playnite_media_overwrite") or "gaps").lower()
    icon_src = (config.get("playnite_icon_source") or "logo").lower()
    bundle_rel = os.path.basename(out).rsplit(".", 1)[0] + "_media"
    bundle = os.path.join(os.path.dirname(os.path.abspath(out)), bundle_rel)
    os.makedirs(bundle, exist_ok=True)

    mcon = media_choose.con_index()
    repo = media_choose.repo_dir()
    media = chosen_media(mcon)
    have = existing_playnite_art()
    n = 0
    for rec, nk in records:
        if not nk:
            continue
        avail = media.get(nk, {})
        pn_has = have.get(("" + rec["name"]).lower(), set())
        slots = [("cover", avail.get("cover")),
                 ("background", avail.get("background"))]
        if icon_src == "logo":
            slots.append(("icon", avail.get("logo")))
        elif icon_src == "cover":
            slots.append(("icon", avail.get("cover")))
        for slot, row in slots:
            if not row:
                continue
            # never round-trip Playnite's own art back into Playnite
            if row["provider"] == "playnite":
                continue
            # only materialize/bundle art we're actually going to write
            if not ((policy == "all") or (slot not in pn_has)):
                continue
            fn = _bundle_file(mcon, repo, bundle, row)
            if not fn:
                continue
            rec["%s_file" % slot] = "%s/%s" % (bundle_rel, fn)
            rec["set_%s" % slot] = True
            n += 1
    mcon.commit()
    mcon.close()
    print("playnite_export: bundled art for %d slot-writes -> %s/" % (n, bundle),
          file=sys.stderr)


def main(argv):
    do_media = "--no-media" not in argv
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    pos = [a for a in argv if not a.startswith("--")
           and argv[argv.index(a) - 1] != "--limit"]
    db = config.get("library_db")
    if not db or not os.path.exists(db):
        sys.exit("no catalog — run update.sh first")
    out = (pos[0] if pos else config.get("playnite_export_json")
           or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "ludodex_to_playnite.json"))
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    # game attributes (queryable, aggregated)
    attrs = {}
    for r in con.execute("SELECT game_id, kind, value FROM game_attributes"):
        attrs.setdefault(r["game_id"], {}).setdefault(r["kind"], []).append(r["value"])
    # lossless per-source records (for round-trip identity/fields)
    sattr = {}
    for r in con.execute("SELECT game_id, source, source_id, attrs_json FROM source_attrs"):
        sattr.setdefault(r["game_id"], []).append(r)
    # sources (for identity when no Playnite record exists)
    srcs = {}
    for r in con.execute("SELECT game_id, source, platform, source_id FROM sources"):
        srcs.setdefault(r["game_id"], []).append(r)

    records = []          # list of (rec, norm_key)
    q = "SELECT id, canonical_title, norm_key FROM games"
    if limit:
        q += " LIMIT %d" % limit
    for g in con.execute(q):
        gid = g["id"]
        rec = {"name": g["canonical_title"]}
        # seed identity/fields from an original Playnite record if present
        seed = None
        for sa in sattr.get(gid, []):
            try:
                seed = json.loads(sa["attrs_json"])
                break
            except ValueError:
                pass
        if seed:
            rec.update(seed)
            rec["name"] = g["canonical_title"]
        else:
            # synthesize identity from the best available source
            best = sorted(srcs.get(gid, []),
                          key=lambda s: PROVIDER_RANK.index(s["source"])
                          if s["source"] in PROVIDER_RANK else 99)
            if best:
                rec["source"] = best[0]["source"]
                rec["source_id"] = best[0]["source_id"]
        # overlay aggregated attributes (authoritative for lists; fill scalars)
        a = attrs.get(gid, {})
        for k in LIST_KINDS:
            if a.get(k):
                rec[k] = sorted(set(a[k]))
        for k in SCALAR_KINDS:
            if k in rec:
                continue
            vals = a.get(k)
            if not vals:
                continue
            if k in _NUM:
                rec[k] = max(int(v) for v in vals if str(v).lstrip("-").isdigit())
            elif k in ("favorite", "hidden"):
                rec[k] = any(v in ("True", "1", "true") for v in vals)
            elif k == "release_year":
                rec[k] = min(int(v) for v in vals if str(v).isdigit())
            else:
                rec[k] = vals[0]
        records.append((rec, g["norm_key"]))
    con.close()

    if do_media:
        attach_media(records, out)

    with open(out, "w", encoding="utf-8") as f:
        json.dump([r for r, _ in records], f, ensure_ascii=False, indent=1)
    print("wrote %d records -> %s" % (len(records), out), file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
