#!/usr/bin/env python3
"""The "Identified via" chips on a game page: every one links, none can strand you.

Three defects the live browser suite (tests/browser/ui_full.py) found in one strip:

  * Disabling a metadata provider could not be undone. game_detail dropped a disabled
    provider from `metadata_links`, the UI builds its chips from that list, so the chip
    went away and took its "off" button, the only control that turns it back on, with
    it. The link now stays, marked `disabled`; its attributes, confidence and favicon
    shortcut are still withheld exactly as before.
  * The IGDB chip had no link. `metadata_links` carried the stored `url`, which for IGDB
    is NULL, while `provider_links` derived igdb.com/games/<slug> for the same match.
    Both now carry the one derived URL (`_provider_page_url`).
  * A game owned twice on one store showed one store chip. BioShock is Steam 7670 AND
    Steam 409710; the UI de-duplicated its store chips by store NAME. The server always
    sent both sources; the chips are now keyed by store + id.

Offline: an isolated data dir, the empty catalog the server seeds, and App.tsx read as
text for the UI half.
"""
import os
import re
import sqlite3
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-id-chips-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

from server import app                                         # noqa: E402
import identity_disable                                        # noqa: E402

APP = open(os.path.join(DIR, "web", "src", "App.tsx"), encoding="utf-8").read()
PASS = []


def check(label, cond, detail=None):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: %s%s" % (label, "" if detail is None else "  (%r)" % (detail,)))


NK = "bioshock"


def seed():
    lc = sqlite3.connect(app.LIBRARY_DB)
    lc.execute("INSERT INTO games(id,canonical_title,norm_key,platform,entry_key,base_key,"
               "game_key,n_sources,n_kinds,sources_summary,wanted,has_steam) "
               "VALUES(1,'BioShock',?,'pc',?,?,'igdb:20',2,0,'steam',0,1)",
               (NK, NK + "@pc", NK))
    for appid in ("7670", "409710"):
        lc.execute("INSERT INTO sources(game_id,source,platform,source_id,title_raw) "
                   "VALUES(1,'steam','pc',?,'BioShock')", (appid,))
    # IGDB is stored the way it overwhelmingly is live: an id and a slug, no url
    lc.execute("INSERT INTO metadata_links(game_id,provider,provider_id,slug,url) "
               "VALUES(1,'igdb','20','bioshock',NULL)")
    lc.execute("INSERT INTO metadata_links(game_id,provider,provider_id,slug,url) "
               "VALUES(1,'mobygames','1234',NULL,NULL)")
    lc.execute("INSERT INTO game_attributes(game_id,kind,value,origin) "
               "VALUES(1,'developers','Irrational Games','mobygames')")
    lc.commit()
    lc.close()


def detail():
    return app.game_detail(NK + "@pc")


def main():
    seed()

    print("1. the IGDB chip links to the same page as its favicon")
    d = detail()
    ml = {l["provider"]: l for l in d["metadata_links"]}
    pl = {l["provider"]: l["url"] for l in d["provider_links"]}
    check("metadata_links' IGDB entry carries a url",
          ml["igdb"]["url"] == "https://www.igdb.com/games/bioshock", ml["igdb"])
    check("and it is exactly the provider_links url", ml["igdb"]["url"] == pl.get("igdb"))
    check("no provider is marked disabled yet",
          not any(l["disabled"] for l in d["metadata_links"]))

    print("2. a disabled provider keeps its chip, and only its chip")
    check("the fixture attribute comes from mobygames",
          d["attributes"].get("developers") == ["Irrational Games"])
    identity_disable.set_disabled(NK, "mobygames", True)
    d = detail()
    ml = {l["provider"]: l for l in d["metadata_links"]}
    check("the disabled provider is still in metadata_links", "mobygames" in ml,
          sorted(ml))
    check("marked disabled", ml["mobygames"]["disabled"] is True)
    check("the other one is not", ml["igdb"]["disabled"] is False)
    check("disabled_identity still names it", d["disabled_identity"] == ["mobygames"])
    check("its attributes stay withheld", "developers" not in d["attributes"],
          d["attributes"])
    check("its favicon shortcut stays withheld",
          "mobygames" not in {l["provider"] for l in d["provider_links"]})
    identity_disable.set_disabled(NK, "mobygames", False)
    d = detail()
    check("re-enabling restores its attribute",
          d["attributes"].get("developers") == ["Irrational Games"])

    print("3. the UI draws the disabled chip and does not count it as identifying")
    check("metaChips is built from every metadata link (disabled ones included)",
          "const metaChips = d.metadata_links.filter((l) => META_PROVIDERS.has(l.provider))"
          in APP)
    check("the chip's re-enable control is still there", "'Re-enable this provider'" in APP)
    check("a disabled link does not make the game 'identified'",
          "(d?.metadata_links ?? []).some((l) => !l.disabled)" in APP)

    print("4. one store chip per store ENTRY")
    ids = sorted(s["source_id"] for s in d["sources"] if s["source"] == "steam")
    check("the server sends both Steam entries", ids == ["409710", "7670"], ids)
    m = re.search(r"const storeChips = Array\.from\(new Map\((.*?)\.values\(\)\)", APP, re.S)
    check("the store chip builder is where the test expects it", m is not None)
    check("store chips are keyed by store AND id, not store name alone",
          "[sc.source + '\\u0000' + sc.source_id," in m.group(1)
          and "[sc.source, {" not in m.group(1))

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
