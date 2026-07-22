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
import re
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


_OG = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image(?::secure_url)?|twitter:image(?::src)?)["\']'
    r'[^>]+content=["\']([^"\']+)["\']', re.I)
_OG_REV = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']'
    r'(?:og:image(?::secure_url)?|twitter:image(?::src)?)["\']', re.I)
_LINK_IMG = re.compile(r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)["\']', re.I)
_IMG_SRC = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.I)
_IMG_EXT = re.compile(r'\.(jpe?g|png|webp)(?:[?#]|$)', re.I)


# Real images that are never the game's art. Content-type and size cannot catch these —
# they are genuine, correctly-served image files — so they must be filtered by what they ARE.
# Observed live while testing grounded search: bot-challenge mascots (Anubis serves its
# challenge page's og:image), database placeholders, site banners and YouTube video
# thumbnails all validated perfectly and were all wrong.
_JUNK_PART = (
    "/anubis/", "within.website", "placeholder", "site-community", "site-background",
    "/favicon", "sprite", "avatar", "/emoji/", "captcha", "cloudflare",
    "wikia-beacon", "/static/img/", "/assets/icons/",
    "/img/flags/", "resources/images/logo",   # site chrome seen on launchbox-app.com
)
_JUNK_HOST = ("i.ytimg.com", "img.youtube.com", "gravatar.com")


def _is_junk_image(url):
    u = url.lower()
    host = urllib.parse.urlparse(u).netloc
    return any(h in host for h in _JUNK_HOST) or any(j in u for j in _JUNK_PART)


def page_images(page_url, limit=4, timeout=15):
    """Candidate image URLs from a WEB PAGE, best first.

    This is the other half of grounded search: a search tool hands back the PAGE a game's
    art lives on, not the image itself. og:image / twitter:image come first because that is
    the publisher's own declaration of the page's representative image — for a game database
    or wiki entry that is nearly always the cover. Plain <img> tags are a weak fallback and
    are filtered to real image extensions to avoid sprites, tracking pixels and SVG chrome.

    Returns absolute URLs. The CALLER must still fetch and validate each one: a page can
    advertise an og:image that 404s, is hotlink-blocked, or is a placeholder."""
    try:
        req = urllib.request.Request(page_url, headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = (r.headers.get("Content-Type") or "").lower()
            if "html" not in ctype:
                # the "page" is already an image — hand it straight back
                return [page_url] if "image" in ctype else []
            html = r.read(1_500_000).decode("utf-8", "replace")
            base = r.geturl()
    except Exception:
        return []
    out, seen = [], set()

    def _push(u):
        if not u:
            return
        u = urllib.parse.urljoin(base, u.strip().replace("&amp;", "&"))
        if _is_junk_image(u):
            return
        if u.startswith(("http://", "https://")) and u not in seen:
            seen.add(u)
            out.append(u)

    for rx in (_OG, _OG_REV, _LINK_IMG):
        for m in rx.findall(html):
            _push(m)
    if len(out) < limit:
        for m in _IMG_SRC.findall(html):
            if _IMG_EXT.search(m):
                _push(m)
    return out[:limit]
