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
    # materialize() holds the write connection across long downloads while the live
    # server reads/writes the same index — without a busy timeout a momentary lock
    # aborts the whole pass at commit time. Wait for the lock instead of failing.
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA journal_mode=WAL")   # concurrent-safe with background media jobs
    if "hidden" not in {r[1] for r in con.execute("PRAGMA table_info(media)")}:
        con.execute("ALTER TABLE media ADD COLUMN hidden INTEGER DEFAULT 0")
        con.commit()
    return con


def _load_pins():
    """Durable user art pins: {(norm_key, kind, provider, ref): rank}. A pinned asset
    is what the user dragged to a given priority in the media overlay; select() lets
    it win over provider priority so the served art matches what they picked."""
    p = os.path.join(DATA, "pins.sqlite")
    if not os.path.exists(p):
        return {}
    out = {}
    con = sqlite3.connect(p)
    try:
        for nk, kind, prov, ref, rk in con.execute(
                "SELECT norm_key, kind, provider, ref, rank FROM pins"):
            out[(nk, kind, prov, ref)] = rk
    except sqlite3.OperationalError:
        pass
    con.close()
    return out


def select(con, kinds=None, only=None):
    """Set chosen=1 on the best asset per (norm_key, scalar kind); 0 elsewhere.
    `kinds` restricts the pass to a subset of scalar kinds (non-destructive — other
    kinds keep their existing chosen flags), so a wand run can fill just covers."""
    scalar = [k for k in media.SCALAR_KINDS if not kinds or k in kinds]
    if not scalar:
        return 0
    # The reset must be scoped exactly like the re-rank below, or a scoped run would
    # clear `chosen` for the whole library and only restore it for `only`.
    _where, _wargs = [], []
    if kinds:
        _where.append("kind IN (%s)" % ",".join("'%s'" % k for k in scalar))
    if only:
        _ok = [k for k in only if k]
        if not _ok:
            return 0
        _where.append("norm_key IN (%s)" % ",".join("?" * len(_ok)))
        _wargs += _ok
    con.execute("UPDATE media SET chosen=0"
                + ((" WHERE " + " AND ".join(_where)) if _where else ""), _wargs)
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
    # User pins are AUTHORITATIVE: an explicitly-pinned asset (dragged to #1 in the
    # media overlay) wins over provider priority, so the served art follows the user's
    # choice on every re-select. Keyed by (norm_key, kind, provider, ref) -> pin rank.
    pin_rank = _load_pins()
    # `only` scopes the re-rank to specific norm_keys. Needed because measurement is
    # LAZY: dimensions and the filler verdict are stamped when an asset is first served,
    # which is AFTER the selection that ranked it. Without a cheap way to re-rank one
    # game, the pick made while the asset was unmeasured stands forever — a 460x215
    # screenshot keeps the cover slot while eight measured 484x680 covers sit unused,
    # because at ranking time nothing knew their shapes.
    _q = ("SELECT id, norm_key, system, kind, provider, ref, matched, ref_type, game_key, "
          "width, height, filler, ai_pick "
          "FROM media WHERE kind IN (%s) AND COALESCE(hidden,0)=0"
          % ",".join("'%s'" % k for k in scalar))
    _args = []
    if only:
        only = [k for k in only if k]
        if not only:
            return 0
        _q += " AND norm_key IN (%s)" % ",".join("?" * len(only))
        _args = list(only)
    rows = con.execute(_q, _args).fetchall()
    # chosen is per (norm_key, SYSTEM, kind): each console gets its own best asset, and
    # platform-neutral store art (system NULL/'') is its own bucket — so a per-platform
    # library entry serves its own console's art (DESIGN §11.4), the serve resolver
    # falling back to the neutral bucket when a console has none. The NEUTRAL bucket is
    # further split by game_key so a same-title split (DESIGN §11.9 — the 1986 Portal vs
    # Valve's) chooses one cover PER identity; console art is already siloed by system, so
    # game_key only sub-divides the neutral bucket (non-split games have one key → no-op).
    best = {}                       # (norm_key, system, game_key?, kind) -> (sortkey, id)
    for r in rows:
        pr = rank[r["kind"]].get(r["provider"], 99)
        pin = pin_rank.get((r["norm_key"], r["kind"], r["provider"], r["ref"]), 1 << 30)
        # SHAPE comes before provider priority: an asset whose orientation contradicts
        # its kind is disqualified no matter who supplied it (a landscape header can
        # never be a cover). Nothing examined the image before this — selection ranked
        # on provider order then row id, so a correct pick was luck, not judgment.
        #
        # Orientation may come from the URL (free, works on the first pass); RESOLUTION
        # may not — Steam's `library_600x900.jpg` is served at 300x450 for older titles,
        # so the name is reliable about shape and unreliable about size. Hence measured
        # dimensions only for the resolution term, leaving it neutral until an index or
        # materialize pass has actually measured the file.
        mw, mh = r["width"], r["height"]
        sw, sh = (mw, mh) if (mw and mh) else media.derived_dims(r["ref"])
        # A MEASURED wrong shape is disqualifying, not merely bad. Ranking it last still
        # elected it whenever nothing better existed, so a portrait IGDB artwork became
        # the `background` and a landscape grid became the `cover` — the slot filled
        # with something we had already measured and knew was wrong for it. An empty
        # slot falls back cleanly; a wrong-shaped one is displayed, stretched, as if
        # correct. shape_ok returns True for UNKNOWN dimensions, so this only ever
        # excludes assets we have actually looked at.
        if not media.shape_ok(r["kind"], sw, sh):
            continue
        bad_shape = 0
        # A confirmed letterboxed paste loses to ANY authored cover, whoever supplied it
        # — this is where Steam's "authoritative for its own games" precedence has to
        # yield, because the asset isn't Steam's art, it's Steam's placeholder. Only a
        # CONFIRMED filler (measured) is demoted; NULL means unmeasured, never assumed.
        filler = 1 if r["filler"] == 1 else 0
        px = -(mw * mh) if (mw and mh) else 0        # bigger wins; unknown stays neutral
        # pin first (user authority), then shape, then authored-vs-placeholder, then
        # the durable AI verdict (a paid vision pick must survive re-selects — but it
        # ranks BELOW shape/filler evidence, because a later measurement can prove the
        # AI's pick wrong), then provider priority, measured resolution, and the
        # original tie-breakers.
        # The IMAGE wins, then the provider. Resolution BAND sits above provider
        # priority so a 600x900 cover beats a 264x352 one whoever supplied it — the
        # live case that exposed this had IGDB's thumbnail outranking a SteamGridDB
        # cover more than five times its area purely on provider order. Banded rather
        # than raw pixels so an unmeasured asset lands in the middle instead of last;
        # `px` still breaks ties INSIDE a band.
        band = media.res_band(mw, mh)
        sk = (pin, bad_shape, filler, 0 if r["ai_pick"] else 1, band, pr, px,
              0 if r["matched"] else 1,
              0 if r["ref_type"] == "file" else 1, r["id"])
        _sys = r["system"] or ""
        _gk = (r["game_key"] or "") if not _sys else ""
        key = (r["norm_key"], _sys, _gk, r["kind"])
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


def _measure(path):
    """(w, h) of a materialized file, or (None, None). Pillow reads only the header
    for size, so this costs no real decode. Never fatal: an unmeasurable asset just
    stays unmeasured, and shape_ok() treats unknown as acceptable."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:                       # noqa: BLE001  not an image / no Pillow
        return (None, None)


def stamp_measured(con, r, sha, repo=None):
    """The single write-back for a row whose bytes just landed in the repo: sha1 +
    measured dimensions + the filler verdict, together, always.

    Every path that materializes MUST go through this — materialize() only revisits
    rows whose sha1 is NULL, so a path that backfills sha1 alone (serve-time fetch,
    vision thumbnails) would permanently exclude the row from measurement: width/
    height/filler stay NULL forever and the shape test + filler demotion can never
    apply to it (in `ondemand` media mode that would kill the filler detector
    entirely, since serve-time is the ONLY materialization that mode ever does).

    filler stays tri-state: when the file can't be measured (no Pillow, not an
    image) it remains NULL — "unmeasured", never "measured clean"."""
    repo = repo or repo_dir()
    ext = (r["ext"] or "jpg").split("?")[0]
    path = os.path.join(repo, "%s.%s" % (sha, ext))
    w, h = _measure(path)
    fill = None
    if w is not None and media.KIND_ORIENT.get(r["kind"]) == "portrait":
        fill = 1 if media.looks_padded(path) else 0
    con.execute("UPDATE media SET sha1=?, width=COALESCE(?,width), "
                "height=COALESCE(?,height), filler=COALESCE(?,filler) "
                "WHERE id=?", (sha, w, h, fill, r["id"]))


def materialize(con, kind=None, limit=None, all_refs=False, progress=False):
    """Download/copy assets lacking sha1 into the repo; demote dead refs and
    re-pick. Default = only the chosen asset per (game, kind); all_refs=True
    pulls EVERY candidate (a full local archive). progress=True emits a
    machine-readable `PROG\\t<i>\\t<n>\\t<norm_key>\\t<kind>` line per item so a
    caller can show what's being pulled live."""
    repo = repo_dir()
    base = "(sha1 IS NULL OR sha1='')" if all_refs else "chosen=1 AND (sha1 IS NULL OR sha1='')"
    # Never download videos into the repo — trailers are tens of MB each and play fine
    # streamed live through the media-asset proxy. Keep them as references always.
    q = "SELECT * FROM media WHERE kind!='video' AND " + base
    if kind:
        q += " AND kind='%s'" % kind
    q += " ORDER BY ref_type"        # local files first (cheap), then URLs
    if limit:
        q += " LIMIT %d" % int(limit)
    rows = con.execute(q).fetchall()
    n = len(rows)
    ok = dead = 0
    for i, r in enumerate(rows, 1):
        sha = _materialize_row(repo, r)
        if sha:
            # Record the REAL dimensions while the bytes are in hand — the only
            # authoritative source (provider filenames lie: Steam serves
            # `library_600x900.jpg` at 300x450 for older titles). Feeds the shape test
            # and the resolution tie-break on the next select pass.
            stamp_measured(con, r, sha, repo)
            ok += 1
        else:
            # dead reference: drop it from contention and promote the next best
            con.execute("DELETE FROM media WHERE id=?", (r["id"],))
            _repick(con, r["norm_key"], r["kind"], r["system"])
            dead += 1
        if progress:
            sys.stdout.write("PROG\t%d\t%d\t%s\t%s\n" % (i, n, r["norm_key"], r["kind"]))
            sys.stdout.flush()
        if (ok + dead) % 200 == 0:
            con.commit()
            print("media_choose: materialized %d (%d dead) of %d"
                  % (ok, dead, n), file=sys.stderr)
    con.commit()
    return ok, dead


def _repick(con, norm_key, kind, system=None):
    """After a dead asset is removed, choose the next-best for this game+kind within
    the SAME system bucket (per-platform siloing, DESIGN §11.4)."""
    rank = {p: i for i, p in enumerate(media.priority(kind))}
    cands = con.execute("SELECT id, provider, matched, ref_type, ref, width, height, "
                        "filler, ai_pick FROM media "
                        "WHERE norm_key=? AND kind=? AND COALESCE(system,'')=? "
                        "AND COALESCE(hidden,0)=0",
                        (norm_key, kind, system or "")).fetchall()
    if not cands:
        return

    def _rk(r):
        # Same ordering as select(): a promotion after a dead asset must not install
        # a wrong-shaped replacement the main pass would have rejected.
        mw, mh = r["width"], r["height"]
        sw, sh = (mw, mh) if (mw and mh) else media.derived_dims(r["ref"])
        return (0 if media.shape_ok(kind, sw, sh) else 1,
                1 if r["filler"] == 1 else 0,
                0 if r["ai_pick"] else 1,
                rank.get(r["provider"], 99),
                -(mw * mh) if (mw and mh) else 0,
                0 if r["matched"] else 1,
                0 if r["ref_type"] == "file" else 1, r["id"])

    best = min(cands, key=_rk)
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
        ok, dead = materialize(con, kind, limit, all_refs="--all" in argv,
                               progress="--progress" in argv)
        # RE-SELECT. width/height and the letterboxed-paste flag are populated BY
        # materialize, so the pass above ran with none of them known — the shape test and
        # the filler demotion could not have applied. Without this the picks stay one pass
        # behind and an Algo import (which has no later AI step to re-choose) never
        # demotes anything at all. Cheap: pure SQL over the index, no network.
        if ok:
            n2 = select(con, kinds=kinds)
            print("media_choose: re-selected %d chosen assets with measured dimensions"
                  % n2, file=sys.stderr)
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
