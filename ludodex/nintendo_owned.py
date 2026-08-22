#!/usr/bin/env python3
"""Nintendo digital ownership, read from the Virtual Game Card portal.

  python3 ludodex/nintendo_owned.py --cookie '<pasted cookies>'   # connect
  python3 ludodex/nintendo_owned.py --whoami                      # prove the session
  python3 ludodex/nintendo_owned.py                               # TSV to stdout
  python3 ludodex/nintendo_owned.py --exclude-addons              # drop DLC-only cards

VERIFIED against a live account 2026-08-22: 184 titles, matching the portal exactly.
The call structure is read from `XenorPLxx/playnite-library-nintendo`, which does this
for Playnite. Design: docs/superpowers/specs/2026-08-22-nintendo-vgc-design.md.

WHY THIS EXISTS AT ALL, given the source was removed once. The 2026-07-05 work proved
there is no server-side purchase API: `hac.lp1.eshop.nintendo.net` does not resolve off
console (still true, re-checked 2026-08-22), `api.ec.nintendo.com` serves prices and 404s
every order path, and the account API has no library. The only reachable "your games"
surface was NSO play-activity, which needs an f-token from a third party. All of that
stands. What changed is that Nintendo shipped Virtual Game Cards, a per-account view of
digital titles with a web portal and a GraphQL backend, and that surface did not exist
when the original research ran.

THE CREDENTIAL IS A BROWSER COOKIE, not OAuth. No PKCE, no client id, no f-token. That
makes this the same shape as PSN's npsso: the user signs in normally, copies the session,
and pastes it. Every pasted cookie is kept and replayed rather than picking one by name,
because the session cookie's name is not documented and guessing it is how this breaks
silently six months from now.

TWO STEPS, and the endpoint is NOT hardcoded:
  1. GET the portal page with the cookies. It carries three JSON blobs in `data-json`
     attributes, which yield idToken, savannaClientId and the GraphQL URL to call.
  2. POST `getVgcs` to that URL. `idToken` goes in the query VARIABLES, not a bearer
     header; the only header is x-nintendo-savanna-client-id.

WHAT IT DOES NOT RETURN: a physical cart, unless its DLC was bought digitally. A card
holding only add-on content IS kept, because it proves the base game is owned in some
form. How completely VGC covers older or delisted digital purchases is still UNKNOWN.
"""
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))

NIN_DIR = os.path.join(DATA, ".nintendo")
COOKIEFILE = os.path.join(NIN_DIR, "cookies.json")

PORTAL_URL = ("https://accounts.nintendo.com/portal/vgcs/"
              "?sort=activated_date&order=desc")
# The off-device shop. The portal does not state it; the Playnite client hardcodes 3 and
# it is the only value known to work from a browser session rather than a console.
SHOP_ID = 3
PAGE_LIMIT = 300
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# apparentPlatform is Nintendo's internal codename. OUNCE is Switch 2.
_PLAT = {"NX": "switch", "OUNCE": "switch2"}

VGC_QUERY = """query getVgcs(
  $idToken: String!
  $country: CountryCode!
  $language: LanguageCode!
  $shopId: Int!
  $limit: Int!
  $nasLanguage: String!
  $offset: Int!
  $order: RequestableVgcViewOrder!
  $sortBy: RequestableVgcViewSortBy!
  $vgcViewType: VgcViewTypeInput
  $vgcViewStatus: VgcViewStatusInput
) @inContext(country: $country, language: $language, shopId: $shopId) {
  account {
    vgc {
      vgcViews(
        idToken: $idToken,
        limit: $limit,
        nasLanguage: $nasLanguage,
        offset: $offset,
        order: $order,
        sortBy: $sortBy,
        isHidden: false,
        vgcViewType: $vgcViewType,
        vgcViewStatus: $vgcViewStatus,
      ) {
        offsetInfo { total offset }
        views {
          id
          applicationId
          applicationName
          apparentPlatform
          publisher
          icon { url upgradedIconUrl sizes }
          ownerNaId
          userNaId
          isHidden
          isLending
          isPartialLending
          lendingExpireDatetime
          insertedNsDeviceId
          hasApplication
          hasAddOnContents
          hasUpgrade
          hasNxApplication
          hasNxAddOnContents
          hasOunceApplication
          hasOunceAddOnContents
          containsReleased
        }
      }
    }
  }
}"""


# ------------------------------------------------------------------ credential
def extract_cookies(raw):
    """A `Cookie:` header value from whatever the user pasted.

    Three shapes are accepted, matching how the PSN and EA paste flows already behave:
      * a raw header value            `a=1; b=2`
      * `document.cookie` output      (identical in practice)
      * devtools JSON                 `[{"name":"a","value":"1"}, …]`

    EVERY cookie is kept. The session cookie's name is not documented, so filtering to a
    guessed name is how this silently returns an empty library after a Nintendo change.
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    if raw.startswith("Cookie:"):
        raw = raw.split(":", 1)[1].strip()
    if raw[:1] in "[{":
        try:
            doc = json.loads(raw)
        except ValueError:
            doc = None
        if isinstance(doc, dict):
            doc = doc.get("cookies") if isinstance(doc.get("cookies"), list) else [doc]
        if isinstance(doc, list):
            pairs = []
            for c in doc:
                if isinstance(c, dict) and c.get("name"):
                    pairs.append("%s=%s" % (c["name"], c.get("value", "")))
            if pairs:
                return "; ".join(pairs)
    # collapse newlines a copy/paste may introduce, then normalise separators
    parts = [p.strip() for p in re.split(r"[;\n\r]+", raw) if "=" in p]
    return "; ".join(parts)


def save_cookies(cookie):
    os.makedirs(NIN_DIR, exist_ok=True)
    tmp = COOKIEFILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"cookie": cookie, "saved_at": int(time.time())}, fh)
    os.replace(tmp, COOKIEFILE)
    try:
        os.chmod(COOKIEFILE, 0o600)
    except OSError:
        pass


def load_cookies():
    try:
        with open(COOKIEFILE, encoding="utf-8") as fh:
            return (json.load(fh) or {}).get("cookie") or ""
    except (OSError, ValueError):
        return ""


def connected():
    return bool(load_cookies())


# ------------------------------------------------------------------ transport
def _get(url, cookie):
    req = urllib.request.Request(url, method="GET")
    req.add_header("Cookie", cookie)
    req.add_header("User-Agent", UA)
    req.add_header("Accept", "text/html,application/xhtml+xml")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def _post_json(url, payload, headers, cookie):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Cookie", cookie)
    req.add_header("User-Agent", UA)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


# ------------------------------------------------------------------ bootstrap
def _portal_blob(page, element_id):
    """One `<div id="x" data-json="…">` blob, or None.

    The attribute is HTML-escaped in the page, so it has to be unescaped before parsing.
    """
    m = re.search(r'<div id="%s" data-json="(.*?)"' % re.escape(element_id), page, re.S)
    if not m:
        return None
    try:
        return json.loads(html.unescape(m.group(1)))
    except ValueError:
        return None


def query_params(page):
    """The GraphQL call parameters carried by the portal page.

    Raises RuntimeError when the page is not a signed-in portal, which is also the auth
    check: there is no separate whoami endpoint. A signed-out session redirects or serves
    a page with no `data` blob, and either way idToken is absent.
    """
    data = _portal_blob(page, "data") or {}
    meta = _portal_blob(page, "meta") or {}
    state = _portal_blob(page, "state") or {}
    p = {
        "idToken": data.get("idToken") or "",
        "savannaClientId": data.get("savannaClientId") or "",
        "shopGraphQLApiUrl": data.get("shopGraphQLApiUrl") or "",
        "shopId": SHOP_ID,
    }
    if not (p["idToken"] and p["savannaClientId"] and p["shopGraphQLApiUrl"]):
        raise RuntimeError("Nintendo: the portal did not return a signed-in session "
                           "(no idToken). The cookie is missing or expired.")
    country_id = ((state.get("user") or {}).get("countryId"))
    country = next((c for c in (meta.get("countries") or [])
                    if c.get("id") == country_id), None)
    lang = state.get("lang") or ""
    if not (country and country.get("code") and len(lang) >= 2):
        raise RuntimeError("Nintendo: the portal returned incomplete locale "
                           "information (country/language).")
    p["countryCode"] = country["code"]
    p["languageCode"] = lang[:2]
    p["nasLanguage"] = lang
    return p


def bootstrap(cookie=None):
    cookie = cookie or load_cookies()
    if not cookie:
        raise RuntimeError("Nintendo: not connected — no cookie saved.")
    return query_params(_get(PORTAL_URL, cookie)), cookie


# ------------------------------------------------------------------ the library
def _page(params, cookie, offset):
    payload = {
        "query": VGC_QUERY,
        "variables": {
            "country": params["countryCode"],
            "idToken": params["idToken"],
            "language": params["languageCode"],
            "limit": PAGE_LIMIT,
            "nasLanguage": params["nasLanguage"],
            "offset": offset,
            "order": "ASC",
            "shopId": params["shopId"],
            "sortBy": "ACTIVATED_DATE",
        },
    }
    doc = _post_json(params["shopGraphQLApiUrl"], payload,
                     {"x-nintendo-savanna-client-id": params["savannaClientId"]},
                     cookie)
    if doc.get("errors"):
        raise RuntimeError("Nintendo VGC: %s" % json.dumps(doc["errors"])[:300])
    views = (((doc.get("data") or {}).get("account") or {})
             .get("vgc") or {}).get("vgcViews") or {}
    return views.get("views") or [], int((views.get("offsetInfo") or {}).get("total") or 0)


def platform_of(view):
    """`switch` / `switch2`, or None when the record states neither.

    The apparent platform is authoritative when present; the has* flags are the fallback,
    because a card can carry content for a platform the summary field does not name.
    """
    ap = (view.get("apparentPlatform") or "").upper()
    if ap in _PLAT:
        return _PLAT[ap]
    if view.get("hasOunceApplication") or view.get("hasOunceAddOnContents"):
        return "switch2"
    if view.get("hasNxApplication") or view.get("hasNxAddOnContents"):
        return "switch"
    return None


def is_addon_only(view):
    """The card holds DLC and no base game.

    NOT junk, and NOT a reason to drop the title. Live on datbird's account these are
    Breath of the Wild, Splatoon 3, Pokemon Shield, Mario + Rabbids and Capcom Arcade
    Stadium: games he owns on a CART, whose expansion he bought digitally. The card is
    therefore evidence he owns the base game in some form, which is precisely the fact an
    ownership catalog exists to record.

    Excluding these by default is what made a 184-title portal import as 179. The Playnite
    client offers it as an opt-in setting; this copied it as the default, which was wrong.
    """
    return not view.get("hasApplication") and bool(view.get("hasAddOnContents"))


_TM = dict.fromkeys(map(ord, "™®©"), None)


def clean_title(name):
    """Trademark symbols out, the store's "full game" qualifier out, spaces collapsed."""
    t = (name or "").translate(_TM)
    t = re.sub(r"(?i)\bfull game\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def fetch_owned(cookie=None, exclude_addons=False):
    """[(applicationId, title, platform)] for the account's Virtual Game Cards.

    Lending state is deliberately ignored: a card loaned to another account is still
    owned by this one, so `isLending` never excludes a row.
    """
    params, cookie = bootstrap(cookie)
    rows, seen, offset = [], set(), 0
    while True:
        views, total = _page(params, cookie, offset)
        if not views:
            break
        for v in views:
            if exclude_addons and is_addon_only(v):
                continue
            plat = platform_of(v)
            if not plat:
                continue
            app = v.get("applicationId") or v.get("id") or ""
            key = (app, plat)
            if not app or key in seen:
                continue
            seen.add(key)
            rows.append((app, clean_title(v.get("applicationName")), plat))
        offset += PAGE_LIMIT
        if offset >= total:
            break
    return rows


# ------------------------------------------------------------------ cli
def main(argv):
    if len(argv) > 1 and argv[1] == "--cookie":
        cookie = extract_cookies(argv[2] if len(argv) > 2 else "")
        if not cookie:
            raise SystemExit("Nintendo: no cookies provided")
        params, _ = bootstrap(cookie)          # prove it before saving
        save_cookies(cookie)
        print("# Nintendo connected (%s / %s)"
              % (params["countryCode"], params["nasLanguage"]), file=sys.stderr)
        return
    if len(argv) > 1 and argv[1] == "--whoami":
        params, _ = bootstrap()
        print("# Nintendo session OK — country %s, language %s, shop %s"
              % (params["countryCode"], params["nasLanguage"], params["shopId"]),
              file=sys.stderr)
        return
    rows = fetch_owned(exclude_addons="--exclude-addons" in argv)
    for app, title, plat in rows:
        print("%s\t%s\t%s" % (app, title, plat))
    print("# owned Nintendo games: %d" % len(rows), file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv)
