# Providers are uniform

Status: design direction, 2026-08-06. datbird's decision, stated twice:

> "then steam should be treated like any other provider"
>
> "each provider, the only differences they should have is the differentiating factors
> involved in usage such as API, auth, cooldown, timeout etc, but the normalized output
> from each provider and expectations of all providers should all be the same."

## The rule

A provider differs from another provider ONLY in transport: base URL, auth, rate limit,
timeout, pagination, and how its systems/platforms are named. Everything above that line
— what a match means, how a candidate is accepted, where an identity is cached, how a
link is derived, what media it contributes, what a miss means — is identical and lives
in shared code.

This is the same principle that has been paying out all week, applied one level up:
every defect this project has had traced to one derivation living in two places. Four
providers with four bespoke pipelines is that mistake institutionalised.

## Where the four stand today

| concern | IGDB | ScreenScraper | SteamGridDB | Steam |
|---|---|---|---|---|
| identity cache | `igdb_resolution` (own table, own writer) | `provider_ids` | `provider_ids` | **none** — appid only |
| acceptance gate | exact-name lookup, own rule | `matchgate` | `matchgate` | n/a |
| name search | `_igdb_by_name` | `_ss_match` | `_sgdb_game_id` | **not used** (exists: `storesearch`) |
| year recorded | no | yes (new) | yes (new) | n/a |
| links written by | `build_library` + `provider_links` fill | `provider_links` | `provider_links` | not linked |
| bundle/era judgement | bespoke, in `build_library` | `matchgate` | `matchgate` | n/a |
| media fetch | `media_fetch` per-provider branch | per-provider branch | per-provider branch | per-provider branch |
| enrichment entry point | `igdb_enrich.py` (separate script) | in-server | in-server | `fetch_steam_media` |

Nothing in that table is a transport difference. All of it is the layer that is supposed
to be shared.

## Target shape

One interface, implemented four times, thin:

```
class Provider:
    name, systems_map, auth(), cooldown, timeout       # the ONLY per-provider parts
    search(title, systems, year) -> [Candidate]        # Candidate: id, name, year, systems
    fetch_media(id, kinds)       -> [MediaRef]
    fetch_attrs(id)              -> {kind: value}
    page_url(id)                 -> str
```

and one shared layer above it:

* `matchgate.score()` decides acceptance for every provider, including IGDB. Its
  exact-name lookup becomes a search implementation detail, not a separate rule.
* `provider_ids` caches every identity, including IGDB's and Steam's, with `year` and
  `name`, so I10 covers all four instead of two.
* `provider_links.sync()` derives every link from that cache — IGDB stops being
  fill-only, which removes the `build_library`/`sync` split that has already produced
  two bugs (links wiped by a rebuild; links outliving their identity).
* One media contract, so `_enrich_media` stops branching per provider.
* The four review states (`matched` / `missed` / `unattempted` / `ineligible`) are
  reported identically for all four.

## What this unlocks, concretely

* **Steam becomes searchable by name**, so a ROM you own can draw Steam's store art and
  attributes — Contra: Hard Corps resolving to the Anniversary Collection's appid.
* **IGDB gets the era gate and the uniqueness guard** it currently has no access to,
  because both live in `matchgate`/`provider_ids`. The Ys I / Ys II collision on IGDB
  21032 is exactly the class that would have caught.
* **A fifth provider becomes a small job** rather than a fifth pipeline.

## The risk to design against

Steam's `storesearch` returns STORE PRODUCTS, so a search for a single game legitimately
returns a collection ("Contra Anniversary Collection" for "Contra: Hard Corps"). That is
the collection-vs-entry problem the catalog already models, and `matchgate` already
refuses it — a candidate missing `hard corps` fails the distinguishing-word rule. The
uniform design must keep that gate in front of Steam, not bypass it because an appid
"feels" authoritative. An appid is only authoritative when it came from OWNERSHIP; one
that came from a name search is a candidate like any other.

## Order of work

1. `Provider` interface + `matchgate` for IGDB (biggest correctness win, no new network).
2. IGDB identities into `provider_ids`; `provider_links` owns all four link types.
3. Steam name search behind the same gate, ownership-appids still preferred.
4. Collapse the `media_fetch` per-provider branches onto `fetch_media`.
5. Delete `igdb_enrich.py`'s separate path once (1)-(4) hold.

Each step is independently shippable and independently testable, and none of them
requires a reset to validate.
