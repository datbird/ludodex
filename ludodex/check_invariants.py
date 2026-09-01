#!/usr/bin/env python3
"""Post-ingest invariant check. READ-ONLY — safe against a live instance.

Unit tests prove each fix in isolation. They do not prove the state a real ingest
actually leaves behind, which is the thing that kept going wrong: every wrong-art report
in this project traced to derived truth computed in two places and drifting, and the
symptom only ever appeared in the finished data.

So this asserts the finished data directly. Run it after any ingest, wand run, or
repair:

    docker exec -i ludodex python3 /app/ludodex/check_invariants.py

Exit 0 = every invariant holds. Exit 1 = at least one violation, listed with examples.
"""
import os
import sqlite3
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
# Resolved the way every other module resolves it — DIR is this package, the repo root is
# above it. Hardcoding /data and /app made this the one check that could not be run on a
# checkout, which is exactly where an invariant regression is cheapest to catch.
DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
LIB = os.path.join(DATA, "game-library.sqlite")
IDX = os.path.join(DATA, "media-index.sqlite")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.dirname(DIR))
import media                                            # noqa: E402
import matchgate                                        # noqa: E402
import media_choose                                     # noqa: E402

VIOLATIONS = []


def report(name, rows, detail):
    """rows = list of example strings; empty means the invariant holds."""
    if rows:
        VIOLATIONS.append(name)
        print("  VIOLATED  %s — %d" % (name, len(rows)))
        print("            %s" % detail)
        for r in rows[:8]:
            print("              %s" % r)
        if len(rows) > 8:
            print("              ... and %d more" % (len(rows) - 8))
    else:
        print("  ok        %s" % name)


def main():
    m = sqlite3.connect("file:%s?mode=ro" % IDX, uri=True)
    m.row_factory = sqlite3.Row
    g = sqlite3.connect("file:%s?mode=ro" % LIB, uri=True)
    g.row_factory = sqlite3.Row

    print("invariants for %s\n" % DATA)

    # ---------------------------------------------------------------- I1: identity
    # Neutral art only serves when media.game_key = games.game_key (DESIGN §11.9). A
    # disagreement makes the asset invisible while it still occupies the slot: the entry
    # renders a monogram, or shows Screenshots 0 while holding forty of them.
    bad = []
    for r in g.execute("SELECT base_key, MIN(COALESCE(game_key,'')) gk, "
                       "COUNT(DISTINCT COALESCE(game_key,'')) n, MIN(canonical_title) t "
                       "FROM games GROUP BY base_key"):
        if r["n"] != 1 or not r["gk"]:
            continue            # ambiguous or unidentified: nothing to disagree with
        n = m.execute("SELECT COUNT(*) FROM media WHERE norm_key=? AND "
                      "COALESCE(system,'')='' AND COALESCE(game_key,'')!=?",
                      (r["base_key"], r["gk"])).fetchone()[0]
        if n:
            bad.append("%s — entry %s, %d neutral rows stamped otherwise"
                       % (r["t"][:40], r["gk"], n))
    report("I1 neutral media identity matches its entry", bad,
           "these entries hold art the serve resolver will never show")

    # ---------------------------------------------------------------- I2: falsy identity
    bad = ["%s" % r["t"] for r in g.execute(
        "SELECT canonical_title t, game_key FROM games "
        "WHERE game_key IN ('igdb:0','igdb:','igdb:None')")]
    bad += ["media row %d" % r["id"] for r in m.execute(
        "SELECT id FROM media WHERE game_key IN ('igdb:0','igdb:','igdb:None')")]
    report("I2 no falsy identity is used as a key", bad,
           "every entry sharing 'igdb:0' would share one identity and swap art")

    # ---------------------------------------------------------------- I3: shape
    # A measured wrong shape must never be chosen: an empty slot falls back cleanly, a
    # wrong-shaped one is displayed stretched as if correct.
    bad = []
    for r in m.execute("SELECT id, norm_key, kind, provider, width, height FROM media "
                       "WHERE chosen=1 AND width IS NOT NULL AND height IS NOT NULL"):
        if not media.shape_ok(r["kind"], r["width"], r["height"]):
            bad.append("%s %s %s %dx%d" % (r["norm_key"][:32], r["kind"], r["provider"],
                                           r["width"], r["height"]))
    report("I3 no chosen asset has a known-wrong shape", bad,
           "these render stretched into a slot they do not fit")

    # ---------------------------------------------------------------- I4: no starvation
    # An entry with candidates must have a pick. A candidate set that elects nothing is
    # the signature of the selection having been wiped (or having raised mid-pass).
    bad = []
    for r in g.execute("SELECT DISTINCT base_key, canonical_title t FROM games"):
        for kind in media.SCALAR_KINDS:
            n = m.execute("SELECT COUNT(*) FROM media WHERE norm_key=? AND kind=? "
                          "AND COALESCE(hidden,0)=0", (r["base_key"], kind)).fetchone()[0]
            if not n:
                continue
            c = m.execute("SELECT COUNT(*) FROM media WHERE norm_key=? AND kind=? "
                          "AND chosen=1", (r["base_key"], kind)).fetchone()[0]
            if not c:
                # legitimate only if EVERY candidate is a measured wrong shape
                ok_any = False
                for cand in m.execute("SELECT kind, width, height FROM media WHERE "
                                      "norm_key=? AND kind=? AND COALESCE(hidden,0)=0",
                                      (r["base_key"], kind)):
                    if cand["width"] is None or cand["height"] is None or \
                            media.shape_ok(cand["kind"], cand["width"], cand["height"]):
                        ok_any = True
                        break
                if ok_any:
                    bad.append("%s / %s — %d candidates, none chosen"
                               % (r["t"][:36], kind, n))
    report("I4 every viable candidate set elects a winner", bad,
           "these games have usable art and are showing none of it")

    # ---------------------------------------------------------------- I5: one per bucket
    bad = ["%s %s sys=%s gk=%s -> %d chosen" % (r["norm_key"][:30], r["kind"],
                                                r["sys"] or "-", r["gk"] or "-", r["c"])
           for r in m.execute(
               "SELECT norm_key, kind, COALESCE(system,'') sys, "
               "CASE WHEN COALESCE(system,'')='' THEN COALESCE(game_key,'') ELSE '' END gk,"
               " COUNT(*) c FROM media WHERE chosen=1 AND kind IN (%s) "
               "GROUP BY norm_key, kind, sys, gk HAVING c > 1"
               % ",".join("'%s'" % k for k in media.SCALAR_KINDS))]
    report("I5 exactly one chosen asset per (game, system, identity, kind)", bad,
           "two winners in one bucket means the serve resolver picks arbitrarily")

    # ---------------------------------------------------------------- I6: visible media
    # The user-facing consequence of I1, stated directly: an entry that holds media of a
    # kind but can SEE none of it.
    #
    # "Holds" means media ELIGIBLE for this entry — its own console's art, or
    # platform-neutral art. Another console's art is siloed away on purpose (DESIGN
    # §11.4): a PC entry legitimately shows none of the PS2 video ScreenScraper returned
    # for the same title, and counting that as a violation would report the design
    # working. What remains catchable is real: a neutral row whose identity does not
    # match, or an own-console row hidden for some other reason.
    bad = []
    for r in g.execute("SELECT base_key, platform, COALESCE(game_key,'') gk, "
                       "canonical_title t FROM games"):
        for kind in ("screenshot", "video", "cover"):
            held = m.execute(
                "SELECT COUNT(*) FROM media WHERE norm_key=? AND kind=? "
                "AND (COALESCE(system,'')='' OR COALESCE(system,'')=?)",
                (r["base_key"], kind, r["platform"])).fetchone()[0]
            if not held:
                continue
            seen = m.execute(
                "SELECT COUNT(*) FROM media WHERE norm_key=? AND kind=? AND ("
                "COALESCE(system,'')=? OR (COALESCE(system,'')='' AND game_key=?))",
                (r["base_key"], kind, r["platform"], r["gk"])).fetchone()[0]
            if not seen:
                bad.append("%s (%s) — holds %d %s, shows 0"
                           % (r["t"][:36], r["platform"], held, kind))
    report("I6 media an entry holds is media an entry can show", bad,
           "this is what 'Screenshots 0' looks like on an entry with forty of them")

    # ---------------------------------------------------------------- I7: provider match
    # A MATCH IS NOT AN INGEST (datbird, 2026-08-01): every configured provider is matched
    # for every game, whether or not any metadata or media is taken from it. The failure
    # this catches is a game never ATTEMPTED — not a game legitimately not found, which is
    # a recorded miss and a perfectly good outcome.
    try:
        import provider_ids
        import config as _cfg
        mc = sqlite3.connect("file:%s?mode=ro"
                             % os.path.join(DATA, "metadata-cache.sqlite"), uri=True)
        keys = [r[0] for r in g.execute("SELECT DISTINCT norm_key FROM games")]
        configured = []
        if _cfg.screenscraper_creds():
            configured.append("screenscraper")
        if _cfg.steamgriddb_key():
            configured.append("steamgriddb")
        # A PER-SYSTEM PROVIDER IS ATTEMPTED PER (game, platform). ScreenScraper files
        # one record per system, so "this game was attempted" is not a question that has
        # one answer for a game owned on three platforms — two of them can still be
        # unmatched behind a third that is not.
        owned = {}
        for _nk, _p in g.execute("SELECT DISTINCT norm_key, platform FROM games "
                                 "WHERE platform IS NOT NULL AND platform<>''"):
            owned.setdefault(_nk, []).append(_p)
        bad = []
        for prov in configured:
            keyed = provider_ids.is_platform_keyed(prov)
            unit = "(entry, platform) pairs" if keyed else "entries"
            try:
                if keyed:
                    todo = [(k, p) for k in keys for p in owned.get(k) or []]
                    never = ["%s on %s" % (k, p) for k, p in todo
                             if provider_ids.cached(mc, prov, k, platform=p) is None]
                    total = len(todo)
                else:
                    never = [k for k in keys
                             if provider_ids.cached(mc, prov, k) is None]
                    total = len(keys)
            except sqlite3.OperationalError:
                never, total = list(keys), len(keys)   # table absent = never matched
            if never:
                bad.append("%s — %d of %d %s never attempted (e.g. %s)"
                           % (prov, len(never), total, unit, ", ".join(never[:3])))
        mc.close()
        report("I7 every configured provider is attempted for every game", bad,
               "a provider that is never asked can never be a provider")
    except Exception as e:              # noqa: BLE001
        print("  skipped   I7 provider match (%s)" % str(e)[:60])

    # ---------------------------------------------------------------- I8: displayed==used
    # The panel labels one asset "#1 USED" and the page renders one asset. If those are
    # not the same row the user is looking at a contradiction on one screen — which is
    # exactly what Beyond Oasis showed for `logo`. That bug was in the UI's own rule, but
    # the two computations can also diverge in the DATA: game_media() drops `file` refs
    # whose bytes are missing locally (the serve resolver does not), and when a bucket
    # holds more than one chosen row both sides pick "the first" by different orderings.
    #
    # This replicates BOTH and compares them per (entry, kind) — every kind, not just the
    # one that was reported.
    bad = []
    per_kind = {}
    for r in g.execute("SELECT base_key, platform, COALESCE(game_key,'') gk, "
                       "canonical_title t FROM games"):
        base, plat, gk = r["base_key"], r["platform"] or "", r["gk"] or "\x00"
        # what game_media() offers this entry (the picker's candidate set)
        cands = m.execute(
            "SELECT id, kind, COALESCE(system,'') sys, ref_type, ref, sha1 FROM media "
            "WHERE norm_key=? AND (COALESCE(system,'')=? OR (COALESCE(system,'')='' "
            "AND COALESCE(game_key,'')=?)) AND chosen=1 ORDER BY kind, id",
            (base, plat, gk)).fetchall()
        by_kind = {}
        for c in cands:
            # game_media() hides a local file that is not present on this host
            if c["ref_type"] == "file" and not c["sha1"] and not os.path.exists(c["ref"]):
                continue
            by_kind.setdefault(c["kind"], []).append(c)
        for kind, lst in by_kind.items():
            own = [c for c in lst if c["sys"] == plat]
            used_id = (own or lst)[0]["id"]
            # call the REAL rule, do not restate it — this checker having its own copy
            # of the serve query is the very defect it exists to catch, and it did
            # exactly that: the resolver was fixed and this kept asserting the old rule.
            srv_id = media_choose.serve_pick(m, base, plat, r["gk"], kind)
            if srv_id and srv_id != used_id:
                bad.append("%s (%s) %s — panel says %d, serve returns %d"
                           % (r["t"][:30], plat, kind, used_id, srv_id))
                per_kind[kind] = per_kind.get(kind, 0) + 1
    if per_kind:
        print("  (by kind: %s)" % ", ".join("%s %d" % kv for kv in
                                            sorted(per_kind.items(), key=lambda x: -x[1])))
    report("I8 the asset labelled USED is the asset actually served", bad,
           "the panel and the page would show different art for the same entry")

    # ------------------------------------------------------- I9: one id, one game
    # A provider id identifies ONE game. Two different titles holding the same id means
    # the matcher bound one of them wrong, and the consequence is silent: the loser
    # inherits the winner's art and metadata and looks merely mediocre rather than
    # broken. Live this was Police Quest: In Pursuit of the Death Angel and Police Quest
    # II: The Vengeance both on ScreenScraper 31435 — PQ1 showed PQ2's cover.
    #
    # Same-title-different-platform is legitimate (one game, several entries), so the
    # comparison is on norm_key, not on entry.
    #
    # A REFUSED identity is excluded, because for those the link is not a claim of
    # identity. `provider_links.sync` keeps it on purpose: an entry build_library left on
    # a `title:` key has had the identity refused for art and metadata, but IGDB really
    # does have a page for that bundle and the link is still true and useful — keying
    # removal off game_key once dropped 104 of them. So the shared id there is a recorded
    # decision, not a collision, and the harm this invariant names cannot occur: art
    # follows game_key, which correctly reads `title:<nk>`. Reporting it anyway sent a
    # reviewer to "fix" Fallout 76 + its Public Test Server and Ys I + Ys II, all four of
    # which the catalog had already handled exactly right.
    #
    # Matched per (provider, id) rather than per norm_key: a game whose IGDB identity was
    # refused can still collide on ScreenScraper, and that collision is real.
    try:
        refused = {}                     # norm_key -> [detail, ...]
        for _nk, _d in g.execute("SELECT norm_key, detail FROM identity_review"):
            refused.setdefault(_nk, []).append(_d or "")
    except sqlite3.OperationalError:
        refused = {}                     # catalog predates identity_review
    claims = {}                          # (provider, id) -> {norm_key: title}
    for prov, pid, nk, title in g.execute(
            "SELECT m.provider, m.provider_id, gg.norm_key, gg.canonical_title "
            "FROM metadata_links m JOIN games gg ON gg.id=m.game_id"):
        tag = "%s:%s" % (prov, pid)      # the refusal detail names the id it refused
        if any(tag in d for d in refused.get(nk, ())):
            continue
        claims.setdefault((prov, pid), {})[nk] = title
    bad = []
    for (prov, pid), per_nk in sorted(claims.items(), key=lambda kv: -len(kv[1])):
        if len(per_nk) > 1:
            bad.append("%s %s claimed by %d titles: %s"
                       % (prov, pid, len(per_nk),
                          ",".join(sorted(v or "" for v in per_nk.values()))[:90]))
    report("I9 a provider id identifies exactly one game", bad,
           "the loser silently inherits the winner's art and metadata")

    # ------------------------------------------------- I10: the match is the right ERA
    # A remake shares its original's title exactly, so nothing in the NAME separates
    # them — only the year does. Resident Evil 4 (2023) held ScreenScraper 4750, the
    # 2005 GameCube game, and displayed its box, and the only reason it was ever noticed
    # is that a person looked at a cover. Now that the matched year is recorded, the
    # class is checkable. Rows with no recorded year predate that and are skipped rather
    # than guessed at.
    # Compared against the GAME's era, never the storefront listing date — see
    # matchgate.game_era. `release_year` on a store entry is when that store listed it,
    # so Arcanum reads 2016 against a 2001 game; comparing THAT to a provider's record
    # reported 123 correct ScreenScraper matches as era disagreements, every one of them
    # a re-released PC game. An entry with no statement of its era is skipped, because
    # "we do not know" is not a violation.
    bad = []
    cat = {}                             # norm_key -> the GAME's era (lazily resolved)
    import matchgate as _mg
    import provider_ids as _pi
    # A FRESH INSTALL HAS NO METADATA CACHE, and this used to raise here rather than
    # skip, which aborted the run and took every invariant BELOW it with it. An absent
    # cache means there are no provider matches to check, which is "nothing to say",
    # not a violation, and certainly not a reason to stop checking everything else.
    _mcp = os.path.join(DATA, "metadata-cache.sqlite")
    _mc = (sqlite3.connect("file:%s?mode=ro" % _mcp, uri=True)
           if os.path.exists(_mcp) else None)
    # A PER-SYSTEM PROVIDER'S YEAR IS THAT PLATFORM'S RELEASE DATE, not the game's.
    # ScreenScraper files one record per system, so the Switch record for a 2019 PC game
    # is dated when the Switch port shipped. Reading that as an era disagreement
    # reported 8 correct Switch matches as violations live on 2026-08-26 — Ape Out,
    # Among Us, Bayonetta 2 and the rest, every one of them the right record.
    #
    # So when the record's system IS the platform its row is for, a LATER year is a port
    # date and not evidence of anything. An EARLIER year still is: that is the harm this
    # invariant was built for — Resident Evil 4 (2023) wearing the 2005 GameCube record —
    # and the system agreeing does not excuse a record that predates the game.
    import screenscraper as _ss10
    for prov, (table, idcol) in (sorted(_pi.PROVIDERS.items()) if _mc else []):
        keyed = _pi.is_platform_keyed(prov)
        cols = "norm_key, year, platform, system" if keyed else "norm_key, year"
        try:
            rows = _mc.execute("SELECT %s FROM %s WHERE COALESCE(%s,0)>0 "
                               "AND year IS NOT NULL" % (cols, table, idcol)).fetchall()
        except sqlite3.OperationalError:
            continue
        for row in rows:
            nk, yr = row[0], row[1]
            if nk not in cat:
                cat[nk] = _mg.game_era(g, _mc, nk)
            own = cat[nk]
            if not (own and yr):
                continue
            # THE ERA IS A SPAN. `first_release_date` names one release and is not always
            # the earliest one IGDB records, so Golf With Your Friends read 2020 here
            # while its own release_dates hold the 2016 early access ScreenScraper had
            # correctly matched. Judged against the whole span, that is not a
            # disagreement. See matchgate.game_era for why only a PERSON may widen it.
            lo, hi = own[0], own[-1]
            if int(yr) - hi > matchgate.YEAR_TOLERANCE and keyed \
                    and _ss10.system_id_fits(row[2], row[3]) and row[2] and row[3]:
                continue                  # a later release on the system it is filed for
            if not (lo - matchgate.YEAR_TOLERANCE <= int(yr)
                    <= hi + matchgate.YEAR_TOLERANCE):
                bad.append("%s %s%s — the game is from %s, the match is %s"
                           % (prov, nk[:38],
                              (" (%s)" % row[2]) if keyed and row[2] else "",
                              lo if lo == hi else "%d-%d" % (lo, hi), yr))
    if _mc:
        _mc.close()
    report("I10 a provider match is the same ERA as the game", bad,
           "a remake silently wears its original's art")

    # --------------------------------------------- I11: the match is the right SYSTEM
    # ScreenScraper keeps one record PER SYSTEM, so the system is part of the identity.
    # A record for another system is a different RELEASE — the 2008 PSN port of a 1998
    # PS1 game, the PC Windows build of a 1993 Genesis one — carrying that release's
    # art, dates and metadata.
    #
    # I10 cannot cover this. A re-release usually has a later date, which is how Crash
    # Bandicoot 3 and Phantasy Star IV were caught — but Shinobi III held a PC Windows
    # record with NO dates at all, and Soul Reaver held a PC Windows record dated 1999,
    # the same year as the PS1 release. Both sat under a green era invariant. When the
    # years agree, only the system disagrees.
    #
    # Same discipline as I10: judged only where BOTH sides state a system. An entry
    # whose platform ScreenScraper has no system for (PC, by design) and a row recorded
    # before the system was written down are unjudged, not violations.
    import screenscraper as _ss
    bad = []
    _mc = (sqlite3.connect("file:%s?mode=ro" % _mcp, uri=True)
           if os.path.exists(_mcp) else None)
    # ONE ROW PER (game, platform), so the record is judged against the platform it was
    # matched FOR. This check used to build the wanted system ids from a game's whole
    # platform set, which had two faults and both produced noise instead of findings:
    # `ss_resolution` held one row for a multi-platform game so there was no platform to
    # judge against, and `pc` contributes no system id, so it silently dropped out of the
    # set rather than making the check abstain. Live 2026-08-26, 12 of 16 reported
    # violations were PC records held by pc+switch games — every one of them correct.
    # The rule itself now lives in `screenscraper.system_id_fits`, beside the one the
    # matcher uses, so the two cannot drift again.
    try:
        rows = [] if _mc is None else _mc.execute(
            "SELECT norm_key, ss_id, system, platform FROM ss_resolution "
            "WHERE COALESCE(ss_id,0)>0 AND system IS NOT NULL AND system<>'' "
            "AND platform<>''"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    for nk, sid, sysname, plat in rows:
        if not _ss.system_id_fits(plat, sysname):
            got = (int(sysname) if str(sysname).strip().isdigit()
                   else _ss.systeme_id(sysname))
            # name the system rather than print its id — "a 21 record" tells nobody
            # that a Genesis entry is wearing Game Gear art
            label = _ss.system_label(got) or ("system %s" % got)
            bad.append("screenscraper %s — the %s copy is on a %s record"
                       % (nk[:38], plat, label))
    if _mc:
        _mc.close()
    report("I11 a provider match is for the same SYSTEM as the game", bad,
           "another system's release wears its own art and dates")

    # ---------------------------------------------------------------- I12: cards
    # Every entry belongs to exactly one card. A row whose card_key is NULL vanishes
    # from a GROUPed grid with NO ERROR AT ALL, which is the failure mode worth an
    # invariant: silent absence, not a crash. The second half catches a title: card
    # that has swallowed two different identities, the one guess the fold design
    # knowingly makes (2026-08-25-single-game-entry-design.md, Risks).
    bad = []
    _cols = [r[1] for r in g.execute("PRAGMA table_info(games)")]
    if "card_key" in _cols:
        for r in g.execute("SELECT entry_key FROM games "
                           "WHERE card_key IS NULL OR card_key='' LIMIT 200"):
            bad.append("%s has no card_key" % r["entry_key"])
        for r in g.execute(
                "SELECT card_key, COUNT(DISTINCT game_key) n FROM games "
                "WHERE card_key LIKE 'title:%' GROUP BY card_key "
                "HAVING n > 1 LIMIT 200"):
            bad.append("%s spans %d identities" % (r["card_key"], r["n"]))
        report("I12 every entry belongs to exactly one resolvable card", bad,
               "a row with no card_key disappears from the grid without an error")
    else:
        print("  skipped   I12 cards (catalog predates card_key)")

    m.close()
    g.close()
    print()
    if VIOLATIONS:
        print("FAILED: %d invariant(s) violated — %s" % (len(VIOLATIONS),
                                                         ", ".join(VIOLATIONS)))
        return 1
    print("ALL INVARIANTS HOLD")
    return 0


if __name__ == "__main__":
    sys.exit(main())
