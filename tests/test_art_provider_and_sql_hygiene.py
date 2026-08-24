#!/usr/bin/env python3
"""Small things in the media chain that are wrong in the same way: a fact stated twice,
or a value from outside used as if it came from inside.

  * `media.REMOTE_PROVIDERS` omitted `screenscraper` and `web`, and `LOCAL_PROVIDERS`
    omitted `gamelist` — all three write rows and all three appear in `PRIORITY`. The
    registry is what `config.MEDIA_PROVIDERS` is derived from, so a provider missing
    from it is invisible to `config.py enable <provider>` and to the per-provider scope
    settings while still filling slots.

  * `materialize(kind=…)` interpolated a CLI argument straight into SQL, and `ext` was
    taken raw from a provider URL — `url.rsplit(".", 1)[-1]`, which for a dotless path
    yields something like `host/grid/abc` — or from ScreenScraper's `format` field, then
    used as a FILENAME. Neither value is ours, and both were spliced into something that
    parses.

  * `STEAM_CDN` still pointed at the legacy `steamcdn-a.akamaihd.net` host, while
    `STEAM_MOVIE` two hundred lines below already used the current one.

Offline. No network.
"""
import os
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-hygiene-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import media                                                   # noqa: E402
import media_choose                                            # noqa: E402
import media_fetch                                             # noqa: E402
import media_index                                             # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    print("1. every provider that writes rows is in the registry")
    named = set()
    for kind in media.PRIORITY:
        named.update(media.PRIORITY[kind])
    named.update(media.DEFAULT_PRIORITY)
    missing = sorted(named - set(media.MEDIA_PROVIDERS))
    check("nothing in PRIORITY is unregistered (%s)" % missing, not missing)
    check("screenscraper is a remote provider", "screenscraper" in media.REMOTE_PROVIDERS)
    check("open-web art is a remote provider", "web" in media.REMOTE_PROVIDERS)
    check("gamelist is a LOCAL provider — it indexes files in the ROM tree",
          "gamelist" in media.LOCAL_PROVIDERS)
    check("no provider is in both halves",
          not (set(media.LOCAL_PROVIDERS) & set(media.REMOTE_PROVIDERS)))
    check("config derives its list from here rather than keeping a copy",
          "media.MEDIA_PROVIDERS" in
          open(os.path.join(DIR, "ludodex", "config.py"), encoding="utf-8").read())

    print("2. an extension is sanitised before it becomes a filename")
    check("a dotless SteamGridDB path does not become the extension",
          media.safe_ext("host/grid/abc") == "jpg")
    check("a query string is stripped", media.safe_ext("png?t=123") == "png")
    check("a path separator can never survive", "/" not in media.safe_ext("a/b/c"))
    check("nor can a traversal", media.safe_ext("../../etc/passwd") == "jpg")
    check("an empty format falls back", media.safe_ext(None) == "jpg")
    check("a real one is kept", media.safe_ext("WEBP") == "webp")
    check("a video extension is kept for a video ref",
          media.safe_ext("webm") == "webm")

    print("   and put() sanitises whatever a provider hands it")
    con = media_index.index_con()
    media_fetch.put(con, "g", "cover", "steamgriddb",
                    "https://cdn.steamgriddb.com/grid/abc", 1,
                    ext="cdn.steamgriddb.com/grid/abc")
    con.commit()
    ext = con.execute("SELECT ext FROM media WHERE norm_key='g'").fetchone()[0]
    check("the stored ext is a real extension (%r)" % ext, ext == "jpg")

    print("3. a CLI argument is bound, not spliced into SQL")
    con.execute("DELETE FROM media")
    con.execute("INSERT INTO media(norm_key,system,kind,provider,ref_type,ref,ext,"
                "matched,chosen,indexed_at) VALUES('g','','cover','esde','file',"
                "'/nope/a.png','png',1,1,0)")
    con.commit()
    con.row_factory = __import__("sqlite3").Row
    # the classic probe: if this reaches the parser as text, the table is gone
    ok, dead = media_choose.materialize(con, kind="cover'; DROP TABLE media; --")
    check("an injected --kind matches nothing rather than executing",
          (ok, dead) == (0, 0))
    check("the table is still there",
          con.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1)
    con.close()

    print("4. loose ends")
    check("STEAM_CDN uses the current host, like STEAM_MOVIE beside it",
          "akamaihd.net" not in media_fetch.STEAM_CDN
          and media_fetch.STEAM_CDN.split("/")[2] == media_fetch.STEAM_MOVIE.split("/")[2])
    mf = open(os.path.join(DIR, "ludodex", "media_fetch.py"), encoding="utf-8").read()
    check("json is imported once, at the top",
          mf.count("import json") == 1 and mf.splitlines()[20].strip() == "import json")

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
