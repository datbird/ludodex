#!/usr/bin/env python3
"""The capability matrix is a promise made to the user, so it has to be kept.

A tooltip saying "IGDB can fill this" sends someone to switch IGDB on. If IGDB cannot
actually fill it, that is worse than saying nothing: the user has spent a scrape, a wait
and some trust to arrive back at a blank row. So every claim in `provider_caps.CAPS` has
to be one we would defend, and this file is where that is enforced.

The rules:

  * NO CLAIM FOR A PROVIDER THAT IS NOT REAL, and none for an attribute kind outside the
    editable vocabulary — a tooltip on a kind the UI never renders is dead weight that
    nobody will notice going stale.
  * "NO PROVIDER SUPPLIES THIS" IS AN ASSERTION, not the absence of one. `unsupplied` is
    returned as an explicit boolean so the UI never has to infer it from an empty list,
    and so this test can hold the honest cases honest.
  * ENABLED, CONFIGURED AND CAPABLE ARE THREE DIFFERENT FACTS. A provider switched on
    with no credentials is not ready, and reporting it as ready is the same broken
    promise in a different costume.
  * THE MEASURED ZEROES STAY HONEST. `esrb_rating`, `regions` and `os` had no rows from
    any provider on the live library before TheGamesDB; the matrix must not quietly
    credit an older provider with them.
"""
import os
import sys

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "tests"))
    import test_support
    test_support.isolate("ludodex-caps-")
    sys.path.insert(0, os.path.join(root, "ludodex"))
    import config
    import provider_caps as PC

    print("1. every claim names a real provider and a real attribute kind")
    known = set(PC.LABEL)
    bad = {k: [p for p in v if p not in known] for k, v in PC.CAPS.items()}
    bad = {k: v for k, v in bad.items() if v}
    check("no unknown providers: %s" % (bad or "none"), not bad)
    check("every provider has a human label",
          all(PC.LABEL.get(p) for v in PC.CAPS.values() for p in v))
    check("every note is real prose, not a placeholder",
          all(len(n) > 8 for v in PC.CAPS.values() for n in v.values()))

    print()
    print("2. the kinds it speaks about are kinds the UI actually renders")
    srv = os.path.join(root, "server", "app.py")
    src = open(srv, encoding="utf-8").read()
    block = src.split("_EDITABLE_ATTR_KINDS = [", 1)[1].split("]", 1)[0]
    vocab = {x.strip().strip('"\'') for x in block.replace("\n", "").split(",")
             if x.strip().strip('"\'')}
    # Two kinds are deliberately outside the editable vocabulary: they are consumed by
    # nongame/homebrew and by the PC panel rather than edited as facets.
    extra = {k for k in PC.CAPS if k not in vocab}
    check("no orphan kinds: %s" % (sorted(extra) or "none"), not extra)

    print()
    print("3. the measured zeroes are still zeroes")
    # Live on 2026-08-16: no provider had EVER written these. Only TheGamesDB may claim
    # them, and if another provider ever genuinely gains one, the measurement — not this
    # test — is what should change first.
    for kind in ("esrb_rating", "regions", "os"):
        owners = set(PC.CAPS.get(kind) or {})
        check("%-12s is not credited to igdb or steam" % kind,
              not (owners & {"igdb", "steam"}))
        check("%-12s IS claimed by thegamesdb" % kind, "thegamesdb" in owners)
    check("regions also credits screenscraper, which really does carry rom regions",
          "screenscraper" in PC.CAPS["regions"])

    print()
    print("4. nothing is claimed for a media-only provider")
    check("steamgriddb supplies no attributes",
          not any("steamgriddb" in v for v in PC.CAPS.values()))

    print()
    print("5. 'no provider supplies this' is asserted, never inferred")
    m = PC.matrix(["release_year", "completion_status"])
    check("a supplied kind is not unsupplied", m["release_year"]["unsupplied"] is False)
    check("and lists who", [p["id"] for p in m["release_year"]["providers"]])
    check("an unsupplied kind says so explicitly",
          m["completion_status"]["unsupplied"] is True
          and m["completion_status"]["providers"] == [])
    check("the tooltip for it tells the user what to do instead",
          "manual" in PC.tooltip("completion_status").lower()
          or "yourself" in PC.tooltip("completion_status").lower())

    print()
    print("6. ENABLED, CONFIGURED and CAPABLE are three different facts")
    config.set_("metadata_thegamesdb_enabled", "0")
    config.set_("thegamesdb_api_key", "")
    os.environ.pop("TGDB_API_KEY", None)
    check("capable but switched off", "thegamesdb" in PC.CAPS["esrb_rating"]
          and not PC.enabled("thegamesdb"))
    check("the tooltip marks it off rather than promising it",
          "(off)" in PC.tooltip("esrb_rating"))

    config.set_("metadata_thegamesdb_enabled", "1")
    check("switched on but with no key is NOT ready",
          PC.enabled("thegamesdb") and not PC.configured("thegamesdb"))
    check("and the tooltip says which of the two is missing",
          "no credentials" in PC.tooltip("esrb_rating"))

    config.set_("thegamesdb_api_key", "0123456789abcdef")
    check("on and credentialled reads as ready",
          PC.enabled("thegamesdb") and PC.configured("thegamesdb"))
    t = PC.tooltip("esrb_rating")
    check("with no caveat left in the tooltip: %s" % t[:56],
          "(off)" not in t and "no credentials" not in t)
    config.set_("metadata_thegamesdb_enabled", "0")

    print()
    print("7. the matrix answers for every kind it is asked about")
    m = PC.matrix(["genres", "not_a_real_kind"])
    check("an unknown kind is reported unsupplied rather than dropped",
          "not_a_real_kind" in m and m["not_a_real_kind"]["unsupplied"] is True)
    check("and a known one carries the live state",
          all({"id", "label", "note", "enabled", "configured"} <= set(p)
              for p in m["genres"]["providers"]))

    print()
    print("8. TheGamesDB's genre caveat is stated where someone will read it")
    # The seven non-genres are a real trap; the tooltip is where a user finds out that
    # ludodex splits them off rather than filing them as genres.
    note = PC.CAPS["genres"]["thegamesdb"]
    check("the genres note mentions the split", "split" in note.lower())
    import tgdb_normalize as N
    check("and the splitter actually implements it", len(N.GENRE_MARKERS) == 7)
    check("every marker named in the note is one the splitter knows",
          all(x in N.GENRE_MARKERS for x in ("Demo", "Unofficial", "Virtual Console")))

    print()
    print("9. the UI is wired to it")
    ui = open(os.path.join(root, "web", "src", "App.tsx"), encoding="utf-8").read()
    check("the panel fetches the matrix", "attrCapabilities()" in ui)
    check("it is fetched once, not per game", "_capsCache" in ui)
    check("the row carries a tooltip", "caps?.kinds?.[kind]?.tooltip" in ui)
    check("a blank unsupplied row is marked 'manual only'", "manual only" in ui)
    check("and a blank row a switched-off provider could fill says who",
          "can fill this" in ui)
    css = open(os.path.join(root, "web", "src", "App.css"), encoding="utf-8").read()
    check("the marks are styled", ".ap-cap" in css)
    check("and stack on a phone instead of pushing the value off screen",
          ".ap-cap { display: block" in css.replace("\n", " "))

    print()
    print("%d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
