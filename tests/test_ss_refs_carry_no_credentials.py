#!/usr/bin/env python3
"""A stored media reference must never carry the ScreenScraper credentials.

ScreenScraper hands back media URLs with the caller's OWN auth already baked into
the query string:

    https://neoclone.screenscraper.fr/api2/mediaJeu.php
        ?devid=...&devpassword=...&softname=ludodex
        &ssid=...&sspassword=...&systemeid=1&jeuid=123&media=box-2D

`extract_media` stored `m["url"]` verbatim, so those strings landed in
`media-index.sqlite` as `media.ref`, were copied into `pins.sqlite` as `pins.ref`,
and were rendered into the media panel. On the live instance that was 747 media
rows and 7 pins holding the account password in cleartext. Anything that exports,
shares, backs up or merely displays a reference carried the credentials with it.

The devid half is only an app identity and ships embedded anyway. The ssid/
sspassword half is the USER'S OWN screenscraper.fr login, and it has no business
being written to a database at all.

The fix is not to stop using the URLs. It is to store the addressing half and
re-attach auth at fetch time, which `media_url_with_auth` already did. So:

  * `extract_media` strips the auth params before the ref is recorded;
  * `media_url_with_auth` strips before it appends, which makes it idempotent and
    also repairs a legacy ref that still carries credentials;
  * every addressing param (jeuid, systemeid, media, groupid, companyid) survives,
    because a stripped URL that no longer identifies the asset is a worse bug than
    the one being fixed.

Offline. Nothing here touches the network.
"""
import os
import sys
import urllib.parse

PASS = []
SECRETS = ("devid", "devpassword", "ssid", "sspassword", "softname")


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def qs(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))


AUTHED = ("https://neoclone.screenscraper.fr/api2/mediaJeu.php"
          "?devid=datbird&devpassword=hunter2&softname=ludodex&output=json"
          "&ssid=someone&sspassword=alsosecret"
          "&systemeid=1&jeuid=1234&media=box-2D")
GROUP = ("https://neoclone.screenscraper.fr/api2/mediaGroup.php"
         "?devid=datbird&devpassword=hunter2&groupid=77&media=logo-monochrome")
COMPANY = ("https://neoclone.screenscraper.fr/api2/mediaCompagnie.php"
           "?companyid=9&media=logo&ssid=someone&sspassword=alsosecret")


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    sys.path.insert(0, os.path.join(root, "tests"))
    import test_support
    test_support.isolate("ludodex-ss-refs-")
    sys.path.insert(0, os.path.join(root, "ludodex"))
    import screenscraper as ss

    print("a stored ScreenScraper ref carries no credentials")

    jeu = {"medias": [
        {"type": "box-2D", "url": AUTHED, "region": "us", "format": "png"},
        {"type": "logo-monochrome", "url": GROUP, "region": "wor"},
        {"type": "screenmarquee", "url": COMPANY},
    ]}
    got = ss.extract_media(jeu)
    check("every media is still returned", len(got) == 3)

    for m in got:
        p = qs(m["url"])
        for k in SECRETS:
            check("extract_media drops %s from a %s ref" % (k, m["type"]),
                  k not in p)

    # ---- and it is still a usable address ------------------------------------ #
    box, grp, cmp_ = got
    check("the jeu ref keeps jeuid", qs(box["url"]).get("jeuid") == "1234")
    check("the jeu ref keeps systemeid", qs(box["url"]).get("systemeid") == "1")
    check("the jeu ref keeps media", qs(box["url"]).get("media") == "box-2D")
    check("the group ref keeps groupid", qs(grp["url"]).get("groupid") == "77")
    check("the company ref keeps companyid", qs(cmp_["url"]).get("companyid") == "9")
    check("the endpoint is untouched",
          box["url"].startswith("https://neoclone.screenscraper.fr/api2/mediaJeu.php?"))

    # ---- auth goes back on at fetch time ------------------------------------- #
    creds = {"devid": "D", "devpassword": "P", "softname": "ludodex",
             "ssid": "U", "sspassword": "S"}
    full = ss.media_url_with_auth(box["url"], creds)
    p = qs(full)
    check("fetching re-attaches the devid", p.get("devid") == "D")
    check("fetching re-attaches the account", p.get("sspassword") == "S")
    check("and still addresses the same asset", p.get("jeuid") == "1234")

    # ---- a legacy ref is repaired, not doubled -------------------------------- #
    repaired = ss.media_url_with_auth(AUTHED, creds)
    check("a legacy authed ref does not keep the OLD password",
          "hunter2" not in repaired and "alsosecret" not in repaired)
    for k in ("devid", "devpassword", "ssid", "sspassword", "softname"):
        check("%s appears exactly once after re-auth" % k,
              repaired.count("%s=" % k) == 1)
    check("re-auth is idempotent",
          ss.media_url_with_auth(repaired, creds) == repaired)

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
