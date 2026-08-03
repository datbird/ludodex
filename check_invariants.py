#!/usr/bin/env python3
"""Post-ingest invariant check. READ-ONLY — safe against a live instance.

Unit tests prove each fix in isolation. They do not prove the state a real ingest
actually leaves behind, which is the thing that kept going wrong: every wrong-art report
in this project traced to derived truth computed in two places and drifting, and the
symptom only ever appeared in the finished data.

So this asserts the finished data directly. Run it after any ingest, wand run, or
repair:

    docker exec -i ludodex python3 /app/check_invariants.py

Exit 0 = every invariant holds. Exit 1 = at least one violation, listed with examples.
"""
import os
import sqlite3
import sys

DATA = os.environ.get("LUDODEX_DATA", "/data")
LIB = os.path.join(DATA, "game-library.sqlite")
IDX = os.path.join(DATA, "media-index.sqlite")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/app")
import media                                            # noqa: E402

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
    bad = []
    for r in g.execute("SELECT base_key, platform, COALESCE(game_key,'') gk, "
                       "canonical_title t FROM games"):
        for kind in ("screenshot", "video", "cover"):
            held = m.execute("SELECT COUNT(*) FROM media WHERE norm_key=? AND kind=?",
                             (r["base_key"], kind)).fetchone()[0]
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
