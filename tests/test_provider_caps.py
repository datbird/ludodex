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
  * CLAIMS ARE CHECKED AGAINST THE MAPPERS, NOT AGAINST PROSE. The first cut of this
    matrix was written from what the live `game_attributes` HAPPENED to contain, and
    that was wrong in both directions: it invented three claims no mapper backs
    (steam/features, igdb/platforms, screenscraper/regions) and denied three real ones
    (igdb emits esrb_rating, content_descriptors and age_ratings — zero rows exist only
    because the cached payloads predate the field being requested). Observed rows are
    evidence of what has run, never of what a provider can do.
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
    # `tags` is rendered by the library rather than the attribute editor, so it is a
    # legitimate exception rather than an orphan. Anything else outside the vocabulary is
    # a tooltip on a row the UI never draws.
    extra = {k for k in PC.CAPS if k not in vocab} - {"tags"}
    check("no orphan kinds: %s" % (sorted(extra) or "none"), not extra)

    print()
    print("3. EVERY CLAIM IS CHECKED AGAINST THE PROVIDER'S REAL MAPPER")
    # The check that matters. Prose about what a provider gives you drifts from the code
    # that maps it; calling the mapper does not. This found three claims that were
    # invented (steam/features, igdb/platforms, screenscraper/regions) and three real
    # capabilities that were missing (igdb's esrb_rating, content_descriptors,
    # age_ratings), which is the whole argument for doing it this way.
    import igdb
    import screenscraper as ss
    import aimeta
    import tgdb_normalize as TN

    emitted = {}
    emitted["igdb"] = set(igdb.map_record({
        "genres": [{"name": "Platform"}], "themes": [{"name": "Action"}],
        "game_modes": [{"name": "Single player"}],
        "player_perspectives": [{"name": "Side view"}],
        "franchises": [{"name": "Sonic"}],
        "involved_companies": [{"company": {"name": "Sega"}, "developer": True,
                                "publisher": True}],
        "first_release_date": 722476800, "summary": "x",
        "total_rating": 80.0, "aggregated_rating": 85.0,
        "age_ratings": [
            {"organization": {"name": "ESRB"}, "rating_category": {"rating": "M"},
             "rating_content_descriptions": [{"description": "Blood"}]},
            {"organization": {"name": "PEGI"}, "rating_category": {"rating": "18"}}]}))
    emitted["screenscraper"] = set(ss.extract_metadata({
        "noms": [{"region": "us", "text": "Sonic"}],
        "synopsis": [{"langue": "en", "text": "x"}],
        "genres": [{"noms": [{"langue": "en", "text": "Platform"}]}],
        "developpeur": {"text": "Sonic Team"}, "editeur": {"text": "Sega"},
        "joueurs": {"text": "2"}, "note": {"text": "18"},
        "dates": [{"region": "us", "text": "1992-11-24"}]}))
    emitted["ai"] = set(aimeta.SUPPLEMENT_KINDS)
    emitted["thegamesdb"] = set(TN.to_attributes(
        {"release_date": "1992-11-24", "overview": "x", "rating": "E - Everyone",
         "region_id": 2, "players": 2, "coop": "Yes", "os": "Win", "processor": "p",
         "ram": "r", "hdd": "h", "video": "v", "sound": "s", "youtube": "abc",
         "rating_community": 4.2},
        genre_names=["Platform"], developer_names=["Sega"], publisher_names=["Sega"]))
    try:
        import media_fetch
        emitted["steam"] = set(media_fetch._extract_steam_attrs({
            "genres": [{"id": 1, "description": "Action"}],
            "categories": [{"description": "Single-player"}],
            "developers": ["Valve"], "publishers": ["Valve"],
            "release_date": {"date": "12 Nov, 2007"}, "short_description": "x",
            "type": "game"}))
    except Exception as e:                                  # noqa: BLE001
        print("      (steam mapper unavailable here: %s)" % str(e)[:60])

    # These fill attributes without a mapper function to interrogate — the write is
    # inline in build_library. Asserted against that source instead, so a rename there
    # still breaks this test rather than silently orphaning a tooltip.
    bl = open(os.path.join(root, "ludodex", "build_library.py"), encoding="utf-8").read()
    for kind, origin in (("release_type", "rom"), ("language", "rom"),
                         ("version", "rom"), ("os", "xbox"), ("device", "xbox")):
        check("build_library really writes %-12s with origin %-4s"
              % (kind, origin), '"%s"' % kind in bl and '"%s"' % origin in bl)
    check("steamspy really supplies tags",
          "steamspy" in open(os.path.join(root, "ludodex", "steam_tags.py"),
                             encoding="utf-8").read())

    for provider, kinds in sorted(emitted.items()):
        claimed = {k for k, v in PC.CAPS.items() if provider in v}
        overclaimed = sorted(claimed - kinds)
        check("%-14s claims nothing its mapper cannot emit%s"
              % (provider, (" (invented: %s)" % overclaimed) if overclaimed else ""),
              not overclaimed)

    print()
    print("3b. and nothing REAL is left unclaimed")
    # The opposite failure, and the one that had actually shipped: igdb.map_record emits
    # esrb_rating / content_descriptors / age_ratings and the matrix credited none of
    # them, so three kinds it can fill read as "manual only".
    INTERNAL = {"name", "players", "genre_ids", "min_spec", "video_url"}
    for provider, kinds in sorted(emitted.items()):
        claimed = {k for k, v in PC.CAPS.items() if provider in v}
        missing = sorted(kinds - claimed - INTERNAL)
        check("%-14s claims everything its mapper emits%s"
              % (provider, (" (missing: %s)" % missing) if missing else ""),
              not missing)
    check("igdb IS credited with esrb_rating — the capability it had and we denied",
          "igdb" in PC.CAPS["esrb_rating"])

    print()
    print("3c. STEAM is an enrichment provider, not only a source")
    steam_kinds = sorted(k for k, v in PC.CAPS.items() if "steam" in v)
    check("it fills %d kinds: %s" % (len(steam_kinds), steam_kinds),
          len(steam_kinds) >= 8)
    check("every Steam note says it only covers titles you own there",
          all(PC.STEAM_NOTE.strip() in v["steam"]
              for v in PC.CAPS.values() if "steam" in v))
    check("SteamSpy is credited separately, for the tags it actually fetches",
          "steamspy" in PC.CAPS.get("tags", {}))

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
    # `esrb_rating` has two providers now, and IGDB has no credentials in an isolated
    # test dir — so assert on TheGamesDB's own segment rather than the whole sentence.
    seg = [x for x in PC.tooltip("esrb_rating").split("; ") if "TheGamesDB" in x][0]
    check("no caveat left on the TheGamesDB segment: %s" % seg[:52],
          "(off)" not in seg and "no credentials" not in seg)
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
