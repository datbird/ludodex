# Nintendo ownership via Virtual Game Cards

Status: **VERIFIED against a live account**, 2026-08-22. datbird:

> "Ok ensure you have a solid understanding of all the needed caps and api comman
> structure and build the integration. I'm not at my computer but I want you to build
> out the theory as much as possible for testing later"

Written from the Playnite client's call structure, then confirmed the same day through
datbird's logged-in Chrome profile on <workstation> (see `xbrowse`). **179 owned titles came
back: 174 `switch`, 5 `switch2`.** Every shape below was observed, not inferred.

| assumption | live result |
|---|---|
| portal carries `data`/`meta`/`state` in `data-json` | all three present |
| the GraphQL endpoint is scraped, not hardcoded | `https://wb.lp1.savanna.srv.nintendo.net/graphql` |
| `shopId = 3` is accepted from a browser session | accepted |
| the cookie is the whole credential | 28 cookies, no OAuth, no f-token |
| paging by `offsetInfo.total` | **still unproven live** — 179 fits one page |

**CORS rules out a browser-only design.** The portal is on `accounts.nintendo.com` and the
API on `wb.lp1.savanna.srv.nintendo.net`, so a `fetch` from the page context is refused.
That is why the Playnite client uses a server-side HttpClient, and why the cookies have to
come out of the browser and the call be made from Python.

## Why the old verdict was wrong

`nintendo_owned.py` was built 2026-07-05 and later removed. The recorded verdict was that
server-side ownership is a dead end, and for the routes examined then it still is:

| route | status today |
|---|---|
| `hac.lp1.eshop.nintendo.net` purchase history | **NXDOMAIN off-console**, re-verified 2026-08-22 |
| `api.ec.nintendo.com` order paths | 404 `{code:4005}`, prices only |
| `api.accounts.nintendo.com/2.0.0/users/me` | works, but no library; `/purchases` 404 |
| NSO / Coral play-activity | reachable, needs an f-token from imink or nxapi |

What changed is that Nintendo shipped **Virtual Game Cards**, a per-account view of digital
titles with a web portal and a GraphQL backend. That surface did not exist when the
original research ran. `XenorPLxx/playnite-library-nintendo` uses it, and this design
copies its call structure.

Re-verified from this VM, 2026-08-22:

```
accounts.nintendo.com        302   reachable
api.ec.nintendo.com          404   reachable
hac.lp1.eshop.nintendo.net   NXDOMAIN
```

## The call structure

**No OAuth.** No PKCE, no client id, no f-token. The credential is a **browser session
cookie** for `accounts.nintendo.com`. That makes it the same shape as PSN's `npsso`, which
ludodex already handles with a paste flow.

### 1. Bootstrap: scrape the portal page

```
GET https://accounts.nintendo.com/portal/vgcs/?sort=activated_date&order=desc
Cookie: <the account session cookies>
```

The response is HTML carrying three JSON blobs in `data-json` attributes:

```
<div id="data"  data-json="{…}">   -> idToken, savannaClientId, shopGraphQLApiUrl
<div id="meta"  data-json="{…}">   -> countries[] : {id, code}
<div id="state" data-json="{…}">   -> lang, user.countryId
```

Derived params:

| param | source |
|---|---|
| `idToken` | `data.idToken` |
| `savannaClientId` | `data.savannaClientId` |
| `shopGraphQLApiUrl` | `data.shopGraphQLApiUrl` (the endpoint is **not** hardcoded) |
| `countryCode` | `meta.countries[] where id == state.user.countryId` -> `.code` |
| `languageCode` | `state.lang[:2]` |
| `nasLanguage` | `state.lang` (full, e.g. `en-US`) |
| `shopId` | **hardcoded 3**, the off-device shop |

A missing `idToken`, `savannaClientId` or `shopGraphQLApiUrl` means the session is not
signed in. That is the auth check, and there is no separate whoami.

### 2. Query: GraphQL `getVgcs`

```
POST <shopGraphQLApiUrl>
Content-Type: application/json
x-nintendo-savanna-client-id: <savannaClientId>
```

The operation uses an `@inContext(country:, language:, shopId:)` directive and passes
`idToken` as an argument rather than a bearer header. Variables:

```json
{"country": "US", "idToken": "…", "language": "en", "limit": 300,
 "nasLanguage": "en-US", "offset": 0, "order": "ASC",
 "shopId": 3, "sortBy": "ACTIVATED_DATE"}
```

Selection set, per view:

```
id applicationId applicationName apparentPlatform publisher
icon { url upgradedIconUrl sizes }
ownerNaId userNaId isHidden isLending isPartialLending lendingExpireDatetime
insertedNsDeviceId
hasApplication hasAddOnContents hasUpgrade
hasNxApplication hasNxAddOnContents hasOunceApplication hasOunceAddOnContents
containsReleased
```

Paging: `data.account.vgc.vgcViews.offsetInfo.total` against a 300 limit, `isHidden: false`
fixed in the query.

### 3. Mapping to ludodex

| VGC field | ludodex |
|---|---|
| `applicationId` | `source_id` |
| `applicationName` | title, trademark-stripped, "full game" removed |
| `apparentPlatform == "NX"` or `hasNx*` | platform `switch` |
| `apparentPlatform == "OUNCE"` or `hasOunce*` | platform `switch2` |
| `!hasApplication && hasAddOnContents` | DLC-only, skipped by default |

`OUNCE` is the internal codename for Switch 2. `platmap` knows `switch`; `switch2` is new
and needs adding before entries land, or they fall through to an unmapped platform.

## What this does NOT give you

Stated plainly, because the value of the integration depends on it and none of it is
measured:

* **Virtual Game Cards are not the purchase history.** VGC covers digital titles eligible
  for the card system. Physical carts will never appear. How completely it covers older
  or delisted digital purchases is **unknown** and is the first thing to measure.
* **Cookies expire.** No refresh token, so this needs periodic re-auth. PSN's npsso lasts
  about two months; the Nintendo session is expected to be shorter, and is unmeasured.
* **The bootstrap is HTML scraping.** `savannaClientId` and `shopGraphQLApiUrl` come from
  `data-json` attributes on divs with specific ids. A portal redesign breaks it, and it
  will break loudly rather than silently, which is the right failure.
* **Lending state is carried but ignored.** `isLending` / `isPartialLending` mean the card
  is currently loaned to another account. Those titles are still owned, so they are kept.

## Credential handling

Stored at `/data/.nintendo/cookies.json`, gitignored and dockerignored, as
`{"cookie": "<raw Cookie header>", "saved_at": <epoch>}`.

The paste accepts three shapes, matching how the PSN and EA flows already behave:
the raw `Cookie:` header value, the output of `document.cookie`, or a JSON array of
`{name, value}` objects as devtools exports them. **Every cookie is kept and replayed**
rather than picking one by name, because the session cookie's name is not documented and
guessing it is how this breaks silently.

## Testing plan (steps 1 and 2 are DONE, 3 is not)

1. `python3 ludodex/nintendo_owned.py --whoami` — proves the cookie reaches the portal and
   the three JSON blobs parse. Expect the country and language it derived.
2. `python3 ludodex/nintendo_owned.py` — prints the TSV. Compare the count against what
   the Switch itself shows under the account.
3. Check a physical-only game is absent, and a known digital game is present. That
   measures the coverage question above.
4. Only then wire the sync and let it build.

Offline until then: `tests/test_nintendo_vgc.py` runs the whole chain against a fake
portal and a fake GraphQL endpoint, so the parsing, paging and mapping are exercised
without an account.

Related: `2026-08-06-uniform-providers.md`. Supersedes the "dead end" verdict in the
`ludodex-nintendo-source` memory.
