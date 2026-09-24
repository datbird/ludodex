#!/usr/bin/env python3
"""List a GOG account's WISHLIST (wanted, not owned) via the GOG Galaxy OAuth flow.

The Discover/"Wanted" mirror of gog_owned.py — reuses the same cached token
(.gog/tokens.json). Wishlist product ids come from embed.gog.com/user/wishlist.json
and titles from the public api.gog.com/products/<id>. Prints a TSV (gog_id<TAB>title)
to stdout, status to stderr — same shape as gog_owned.py.

Set GOG up once via gog_owned.py (--code <code>); this reuses that login.
"""
import os
import sys
import time

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
import config
import gog_owned   # http_get + the shared token file and its refresh rule


def access_token():
    """The SHARED cached token, refreshed only when it has actually expired.

    gog_owned owns that rule (cached_access_token): GOG rotates the refresh token on
    every use, so there must be exactly one implementation of "refresh and save it back",
    and its save must not be allowed to fail silently, or the rotated token is lost."""
    tok = gog_owned.cached_access_token()
    if not tok:
        sys.exit("no cached GOG token — set up GOG first: "
                 "python3 ludodex/gog_owned.py --code <code>")
    return tok


def main():
    access = access_token()
    wl = gog_owned.http_get("https://embed.gog.com/user/wishlist.json", token=access)
    ids = [gid for gid, on in (wl.get("wishlist") or {}).items() if on]

    rows = []
    for gid in ids:
        title = ""
        try:                                 # public product info — no auth needed
            p = gog_owned.http_get("https://api.gog.com/products/%s" % gid)
            title = p.get("title", "") or ""
        except Exception:                    # noqa: BLE001 — leave title blank on miss
            pass
        rows.append((gid, title))
        time.sleep(0.15)

    rows.sort(key=lambda x: (x[1] or "~").lower())
    for gid, title in rows:
        print(config.tsv_row(gid, title))
    print("# GOG wishlist: %d titles (%d unresolved names)"
          % (len(rows), sum(1 for _, t in rows if not t)), file=sys.stderr)


if __name__ == "__main__":
    main()
