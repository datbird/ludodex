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
import medialang

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
    # Heal the columns select() READS, not just the ones this module writes: the
    # canonical schema lives in media_index.index_con(), but the CLI opens the index
    # here, so an index built before a column existed would fail the ranking query
    # rather than simply ranking without it.
    _cols = {r[1] for r in con.execute("PRAGMA table_info(media)")}
    for _c, _decl in (("hidden", "INTEGER DEFAULT 0"), ("filler", "INTEGER"),
                      ("ai_pick", "INTEGER"), ("detail", "REAL"), ("frame", "TEXT")):
        if _c not in _cols:
            con.execute("ALTER TABLE media ADD COLUMN %s %s" % (_c, _decl))
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


def _offlang_yield(picked, prefs):
    """Ids the CONSOLE bucket should stand down on, in favour of the neutral bucket.

    Ranking orders the candidates inside one bucket; it cannot reach across buckets, and
    the serve resolver takes own-console art before neutral art unconditionally
    (DESIGN §11.4). So a game whose only shape-valid own-console cover is the Japanese
    box serves that box, while the English store cover it already holds sits unused —
    and no ordering term can say otherwise, because the two never meet in one bucket.
    Live: Castlevania Dracula X served ScreenScraper's 478x864 Japanese SFC box with a
    600x900 English SteamGridDB cover chosen and idle beside it. `region_rank` was doing
    its job — it rated that asset WORST of the six — but the US and EU boxes are full
    box scans including the spine, so they are landscape and `shape_ok` had already
    disqualified them. Ranking only orders survivors.

    Standing down means electing NOTHING for that console bucket, so the existing
    COALESCE falls through to neutral. Deliberately not `hidden`: the asset stays
    visible in the media panel and pinnable, exactly like a demoted pack member.

    Only ever fires when a REPLACEMENT exists, so nothing is emptied — a box_back that
    exists solely as a Japanese scan keeps serving it. And never over a user PIN, which
    is the top term in the sort for a reason."""
    if not prefs:
        return set()
    covered = set()          # (norm_key, kind) whose NEUTRAL winner is on-language
    for (nk, sysv, _gk, kind), (_sk, _id, r, _pin) in picked.items():
        if not sysv and not medialang.is_off_language(r["meta"], r["provider"], prefs):
            covered.add((nk, kind))
    out = set()
    for (nk, sysv, _gk, kind), (_sk, rid, r, pin) in picked.items():
        if not sysv or (nk, kind) not in covered:
            continue
        if pin != (1 << 30):                     # an explicit user pin outranks this
            continue
        if medialang.is_off_language(r["meta"], r["provider"], prefs):
            out.add(rid)
    return out


def select(con, kinds=None, only=None):
    """Set chosen=1 on the best asset per (norm_key, scalar kind); 0 elsewhere.
    `kinds` restricts the pass to a subset of scalar kinds (non-destructive — other
    kinds keep their existing chosen flags), so a wand run can fill just covers."""
    scalar = [k for k in media.SCALAR_KINDS if not kinds or k in kinds]
    if not scalar:
        return 0
    # This function reads rows BY NAME, so a caller handing it a plain connection would
    # blow up on r["kind"] — AFTER the chosen=0 reset below had already run. A caller
    # that swallowed the error then left the game with NO art at all, which is exactly
    # what the serve-time re-rank did: every image viewed wiped that game's picks.
    # Own the requirement here rather than trusting five call sites to remember it.
    con.row_factory = sqlite3.Row
    # Same reasoning as the row_factory line above, for the same reason: own the
    # requirement here rather than in every caller. `frame` is younger than most of the
    # connections that reach this function — con_index() and index_con() heal it, but a
    # plain sqlite3 handle (five test fixtures, and anything built before the column
    # existed) does not, and the ranking query below would abort the whole pass AFTER
    # the chosen=0 reset had already run, leaving those games with no art at all.
    if "frame" not in {r[1] for r in con.execute("PRAGMA table_info(media)")}:
        con.execute("ALTER TABLE media ADD COLUMN frame TEXT")
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
    # Resolved once: a per-row config read would turn selection into thousands of
    # SQLite opens, and the preference cannot change mid-pass anyway.
    _regions = medialang.preferred_regions()
    # Resolved once, same reasoning: a per-row config read would turn selection into
    # thousands of SQLite opens, and the preference cannot change mid-pass.
    _lang_prefs = medialang.preferred_languages()
    # `only` scopes the re-rank to specific norm_keys. Needed because measurement is
    # LAZY: dimensions and the filler verdict are stamped when an asset is first served,
    # which is AFTER the selection that ranked it. Without a cheap way to re-rank one
    # game, the pick made while the asset was unmeasured stands forever — a 460x215
    # screenshot keeps the cover slot while eight measured 484x680 covers sit unused,
    # because at ranking time nothing knew their shapes.
    # TEMPLATE frames, resolved once per pass. Whether a frame is a template is a
    # property of the WHOLE corpus, so this query is deliberately unscoped: it ignores
    # `kinds` and `only` and asks the entire index. Scoping it to the rows being ranked
    # would be the fail-open shape — a re-rank of one game sees its frame exactly once,
    # concludes "shared by 1 game, not a template", and hands the pack back the slot it
    # just lost. `chosen` is likewise irrelevant: a pack's members count whether or not
    # they currently hold a slot.
    _templates = {row[0] for row in con.execute(
        "SELECT frame FROM media WHERE frame IS NOT NULL AND COALESCE(hidden,0)=0 "
        "GROUP BY frame HAVING COUNT(DISTINCT norm_key) >= ?",
        (media.TEMPLATE_MIN_GAMES,))}
    _q = ("SELECT id, norm_key, system, kind, provider, ref, matched, ref_type, game_key, "
          "width, height, filler, detail, ai_pick, meta, frame "
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
    best = {}       # (norm_key, system, game_key?, kind) -> [candidate tuples]
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
        # A frame shared with two or more OTHER games is a themed pack's plate, not this
        # game's art — the same class of evidence as `filler` (measured, about the image
        # itself), so it sits beside it, above provider priority. DEMOTION, never
        # exclusion: `continue` here would leave a game whose only asset is a pack member
        # with no art at all, and the pack art is still the user's to pin, pull and view.
        template = 1 if (r["frame"] and r["frame"] in _templates) else 0
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
        #
        # The line is PER KIND. With one global line the band was constant for 8 of 13
        # scalar kinds, and a constant term decides nothing — so `background`, with no
        # filler verdict either (band_energy is undefined for a landscape canvas), had
        # NOTHING above provider order: 1,808 of its slots were settled with a larger
        # candidate sitting unused and no evidence against it.
        band = media.res_band(mw, mh, r["kind"])
        # REGION sits above the AI verdict for the same reason shape and filler do: it
        # is measured evidence, and measured evidence can prove a paid pick wrong.
        # Contra: Hard Corps had its Japanese box vision-picked while the US box sat
        # beside it, same size, same type, region tagged `us` in the row we already had.
        # A model reading artwork is the fallback for this question, not the answer.
        rrank = medialang.region_rank(r["meta"], _regions)
        _sys = r["system"] or ""
        _gk = (r["game_key"] or "") if not _sys else ""
        key = (r["norm_key"], _sys, _gk, r["kind"])
        # Held per bucket rather than reduced on the spot: whether `filler` DISCRIMINATES
        # is a property of the whole candidate set, and cannot be known one row at a time.
        best.setdefault(key, []).append(
            (r, pin, bad_shape, filler, template, rrank, band, pr, px))

    picked = {}
    for key, cands in best.items():
        # A ranking term that is the same for every candidate has decided nothing, and a
        # term that decides nothing must not silently hand the choice to an unrelated one.
        # When EVERY candidate is a confirmed paste, `filler` is constant — and ranking
        # used to fall through to the resolution band, so Steam's 600x900 auto-portrait
        # beat a 300x450 authored cover on size alone (Insurgency, Arx Fatalis).
        #
        # Two distinct causes produced the same constant: a bright wordmark inflating a
        # peak-relative threshold, and art that is genuinely hazy and low-contrast. No
        # third threshold separates the second case from a real paste, so the fix is not
        # another heuristic — it is to stop pretending a constant term ranked anything.
        # DETAIL DENSITY (median band energy) breaks the tie instead: high for art that
        # carries detail throughout, low for a blurred paste, and unmoved by one bright
        # band. Ranked above the resolution band, below everything that is real evidence.
        #
        # ALL PASTES, not merely "constant" — and that restriction is load-bearing, not
        # an oversight. Widening it to "the term fails to discriminate" reads like the
        # same principle applied consistently, and it is wrong: `detail_density` is edge
        # energy PER PIXEL, so a downscaled copy of one image scores HIGHER than the
        # original (measured: 8 of 8 covers, monotonically, halving size ~+40%). Between
        # two CLEAN candidates it therefore prefers the thumbnail. Dry-run on the live
        # library, that widening moved 244 cover picks, every one from a 300x450 to a
        # 264x352 IGDB thumbnail — reintroducing precisely the defect `res_band` was
        # added to stop. Detail is only meaningful where the difference it reads dwarfs
        # the scaling bias, which is the all-pastes case and nothing else.
        _blind = len(cands) > 1 and all(c[3] == 1 for c in cands)
        for (r, pin, bad_shape, filler, template, rrank, band, pr, px) in cands:
            # -detail so more detail sorts first; unmeasured (NULL) must not win by being
            # unknown, so it sorts last within the blind bucket rather than neutral.
            dt = -(r["detail"] or 0.0) if _blind else 0
            sk = (pin, bad_shape, filler, template, rrank, 0 if r["ai_pick"] else 1,
                  dt, band, pr, px, 0 if r["matched"] else 1,
                  0 if r["ref_type"] == "file" else 1, r["id"])
            if key not in picked or sk < picked[key][0]:
                picked[key] = (sk, r["id"], r, pin)
    yielded = _offlang_yield(picked, _lang_prefs)
    ids = [i for _sk, i, _r, _p in picked.values() if i not in yielded]
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
    fill = dens = None
    if w is not None and media.KIND_ORIENT.get(r["kind"]) == "portrait":
        fill = 1 if media.looks_padded(path) else 0
        # written with the filler verdict, never apart from it: the two are read
        # together by select() and a row carrying one without the other would let a
        # blind bucket rank on a value nothing measured. Both stay portrait-only —
        # `detail_density` is not scale-invariant and cannot be a general tiebreak.
        dens = media.detail_density(path)
    # The frame signature is stamped for EVERY kind, not just the portrait ones: a
    # themed pack ships logos, icons, marquees and bezels together, and gating this on
    # orientation is what left `logo` with no image-fitness evidence of any sort.
    frame = media.frame_sig(path) if w is not None else None
    con.execute("UPDATE media SET sha1=?, width=COALESCE(?,width), "
                "height=COALESCE(?,height), filler=COALESCE(?,filler), "
                "detail=COALESCE(?,detail), frame=COALESCE(?,frame) WHERE id=?",
                (sha, w, h, fill, dens, frame, r["id"]))


def remeasure(con, kinds=None, progress=False):
    """Re-derive width/height/filler from bytes ALREADY in the repo. Returns the number
    of rows re-derived.

    `materialize()` only revisits rows whose sha1 is NULL — deliberately, so a re-run
    costs no network. The consequence is that a row measured once keeps its verdict
    forever, which is right while the rule is right and wrong the moment it is fixed:
    correcting `looks_padded` changed nothing for the 5,245 covers already stamped,
    because no path ever looked at them again.

    The bytes on disk are the source of truth, so this re-derives from them. No network
    and no deletions: a row whose file is absent keeps whatever it has. Callers should
    `select()` afterwards — the verdict is an input to ranking.

    Covers EVERY scalar kind, not only the portrait ones. It was portrait-only while
    `filler` was the only verdict it could re-derive; the frame signature applies to
    all of them, and a landscape-only kind like `logo` would otherwise have no way to
    ever be backfilled."""
    scalar = [k for k in media.SCALAR_KINDS if not kinds or k in kinds]
    if not scalar:
        return 0
    con.row_factory = sqlite3.Row
    repo = repo_dir()
    rows = con.execute(
        "SELECT id, kind, ext, sha1 FROM media WHERE sha1 IS NOT NULL AND kind IN (%s)"
        % ",".join("'%s'" % k for k in scalar)).fetchall()
    n = 0
    for r in rows:
        ext = (r["ext"] or "jpg").split("?")[0]
        path = os.path.join(repo, "%s.%s" % (r["sha1"], ext))
        if not os.path.exists(path):
            continue                    # nothing local to re-derive from
        w, h = _measure(path)
        if w is None:
            continue                    # unreadable stays as it was, never "clean"
        # filler and detail stay portrait-only (band_energy is undefined for a landscape
        # canvas, and detail is not scale-invariant anyway); `frame` is measured for
        # every kind, which is why this pass had to widen beyond portrait at all.
        # COALESCE so a landscape row's NULL never overwrites a real verdict.
        portrait = media.KIND_ORIENT.get(r["kind"]) == "portrait"
        fill = dens = None
        if portrait:
            fill = 1 if media.looks_padded(path) else 0
            dens = media.detail_density(path)
        con.execute("UPDATE media SET width=?, height=?, frame=?, "
                    "filler=COALESCE(?,filler), detail=COALESCE(?,detail) WHERE id=?",
                    (w, h, media.frame_sig(path), fill, dens, r["id"]))
        n += 1
        if progress and n % 500 == 0:
            print("media_choose: re-measured %d" % n, file=sys.stderr)
    con.commit()
    return n


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
            drop_dead(con, r)
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


def drop_dead(con, row):
    """A reference whose bytes will not come down loses its slot, and the next-best
    takes it.

    Batch materialization always did this. The SERVE path — the only materialization
    that happens at all in `ondemand` media mode — did not: it raised 502 and left the
    dead row `chosen`, so the entry showed a monogram on every subsequent request while
    good candidates sat unchosen, and nothing self-healed it short of a manual pass.
    One rule, one implementation, called from both.

    `row` needs id / norm_key / kind (a sqlite3.Row or a plain dict both work).
    """
    rid = row["id"] if not isinstance(row, dict) else row.get("id")
    nk = row["norm_key"] if not isinstance(row, dict) else row.get("norm_key")
    kind = row["kind"] if not isinstance(row, dict) else row.get("kind")
    con.execute("DELETE FROM media WHERE id=?", (rid,))
    if nk and kind:
        _repick(con, nk, kind)
    con.commit()


def serve_pick(con, base, platform, game_key, kind):
    """THE rule for "which asset does this entry actually display for this kind".

    Precedence, most specific first, and deterministic:
      1. this entry's own-console art (system = the entry's platform)
      2. this entry's own platform-neutral art whose identity matches (DESIGN §11.9)
      3. neutral art from ANOTHER norm_key sharing the identity — the deliberate rescue
         for a title that parsed into two norm_keys but has one fetched cover
      4. ties broken by id, so the answer is stable rather than whatever the query
         planner happens to return

    It lives here, once, because it was previously written inline in the serve endpoint
    AND copied into the invariant checker AND approximated a third time in the UI. (3)
    used to outrank (2) by accident — the neutral branch has no norm_key constraint —
    so "Battlerite Public Test" served "Battlerite"'s background while its own media
    panel showed its own, the page and the panel disagreeing on one screen.

    Returns the media row id, or None.
    """
    r = con.execute(
        "SELECT id FROM media WHERE kind=? AND chosen=1 AND ("
        "(norm_key=? AND COALESCE(system,'')=?) "
        "OR (COALESCE(system,'')='' AND game_key=?)) "
        "ORDER BY (norm_key=? AND COALESCE(system,'')=?) DESC, (norm_key=?) DESC, id "
        "LIMIT 1",
        (kind, base, platform or "", game_key, base, platform or "", base)).fetchone()
    return (r[0] if not hasattr(r, "keys") else r["id"]) if r else None


def _repick(con, norm_key, kind, system=None):
    """After a dead asset is removed, re-elect this game+kind.

    This used to carry a hand-copied sort key "same as select()", and the copy went
    stale exactly as you'd expect: no resolution band, provider priority above size,
    and a measured wrong shape merely ranked last rather than disqualified. So every
    defect fixed in the real ranker came back the instant a provider URL 404'd. Two
    implementations of one rule is the bug — call the ranker, scoped.

    `system` is now unused: the scoped select re-elects every system bucket for this
    game+kind, which is a superset of the old single-bucket behaviour and keeps the
    per-platform siloing (DESIGN §11.4) that select() already implements.
    """
    del system                                          # noqa: F841 — see docstring
    select(con, kinds=[kind], only=[norm_key])


def main(argv):
    kind = argv[argv.index("--kind") + 1] if "--kind" in argv else None
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    # --kinds a,b,c restricts the choose pass to those scalar kinds (non-destructive)
    kinds = (argv[argv.index("--kinds") + 1].split(",")
             if "--kinds" in argv else None)
    con = con_index()
    # --remeasure re-derives dimensions and the filler verdict from bytes already in
    # the repo, BEFORE selecting — for when the rule that produced them has changed.
    if "--remeasure" in argv:
        rm = remeasure(con, kinds=kinds, progress="--progress" in argv)
        print("media_choose: re-measured %d assets from local bytes" % rm,
              file=sys.stderr)
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
