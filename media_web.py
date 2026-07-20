#!/usr/bin/env python3
"""Structured OPEN-WEB media discovery — reliable sources that return REAL image URLs,
unlike asking an LLM to guess links.

  * Wikimedia (keyless): the game's Wikipedia lead image — usually the box/cover.
  * Google Programmable Search, image mode (needs an API key + search-engine id `cx`
    in config): broad image results the caller AI-picks + validates.

The caller validates every url is a live image before trusting it, and (for Google)
uses vision to pick the right one. For a self-hosted single-user catalog — found art for
your OWN private library, not redistribution.
"""
import json
import urllib.parse
import urllib.request

_UA = {"User-Agent": "ludodex media finder (https://github.com/datbird/ludodex)"}
WP_API = "https://en.wikipedia.org/w/api.php"
GCSE = "https://www.googleapis.com/customsearch/v1"


def _get_json(url, timeout=15):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def wikimedia(title, year=None):
    """The game's Wikipedia lead image (usually the box/cover art), keyless. [] on miss.
    Searches for the game's page then returns its `original` page image."""
    q = '"%s" video game' % title
    if year:
        q += " %d" % year
    # pilicense=any is REQUIRED: game covers are non-free (fair-use) images, and pageimages
    # defaults to free-license only — so the cover comes back empty without it.
    params = {"action": "query", "format": "json", "redirects": "1",
              "generator": "search", "gsrsearch": q, "gsrlimit": "1",
              "prop": "pageimages", "piprop": "original", "pilicense": "any"}
    try:
        d = _get_json(WP_API + "?" + urllib.parse.urlencode(params))
    except Exception:
        return []
    out = []
    for p in ((d.get("query") or {}).get("pages") or {}).values():
        src = (p.get("original") or {}).get("source")
        if src:
            out.append({"kind": "cover", "url": src, "source": "wikimedia"})
    return out


def google_images(query, key, cx, n=6):
    """Google Programmable Search image results ([{url,w,h}]). Needs an API key + a search
    engine id (`cx`). [] if unconfigured or on error."""
    if not (key and cx):
        return []
    params = {"key": key, "cx": cx, "searchType": "image", "q": query,
              "num": str(min(max(n, 1), 10)), "safe": "off"}
    try:
        d = _get_json(GCSE + "?" + urllib.parse.urlencode(params))
    except Exception:
        return []
    out = []
    for it in (d.get("items") or [])[:n]:
        link = it.get("link")
        if link:
            img = it.get("image") or {}
            out.append({"url": link, "source": "google",
                        "w": img.get("width"), "h": img.get("height")})
    return out
