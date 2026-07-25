# Match Verification Across All Tiers — Design

Date: 2026-07-24
Status: approved (design) — amends `2026-07-23-tiered-store-ingest-design.md`

## Summary

Ingest currently asks a model about a game only when that game looks **incomplete**. A game
whose identity is *confidently wrong* is not incomplete, so no tier ever examines it. This
spec adds **match verification** as a first-class concern at every tier — deterministic at
Algo, AI-assisted at Light, exhaustive at Heavy — across **all 3 ingest tiers and both wand
tiers**, and retains the provider facts that make the Algo case possible.

It is an amendment, not a rewrite: the tier template, the spend guardrail, and DESIGN §13 are
unchanged. What changes is *which games ingest is willing to look at*.

---

## The defect this addresses

Observed live, 2026-07-24. The user owns Steam appids 223810 and 223870. Ground truth:

| Source | 223810 | 223870 |
|---|---|---|
| Steam **ownership** (`GetOwnedGames`) | `Ys I` | `Ys II` |
| Steam **store** (`appdetails`) name | `Ys I & II Chronicles+` | `Ys I & II Chronicles+` |
| Steam **canonical** `steam_appid` | **223810** (itself) | **223810** (a sub-app) |
| IGDB record matched | `21032` — `Ys I & II Chronicles+`, **`game_type: 3` (bundle)** | same |

Both appids matched the compilation's IGDB record, collapsed into one catalog entry
`norm_key='ys 2'`, `game_key='igdb:21032'`, titled **"Ys II"**, wearing the compilation's
description and art. There is **no `Ys I` entry** — the game was absorbed.

The entry is invisible to every tier's AI step:

```
metadata_links: 1        -> unmatched? False   ->  LIGHT skips it  (targets "unmatched")
supplement attrs: 8 of 8 -> missing?    False   ->  HEAVY skips it  (targets "missing")
                                                    ALGO runs no AI at all
```

Nothing is mis-wired. `_sync_worker` → `_start_aimeta_job` → `_aimeta_scan` →
`_auto_detect_collections` → `compilations.set_collection` is intact and works (see the live
`Zombie Army Trilogy` collection). The chain simply never receives this game, because the
eligibility filters select for *gaps* and this entry has none.

**Generalisation:** the pipeline can only find problems shaped like absence. A confident wrong
match presents as a complete, healthy record. This class is structurally undetectable today,
and a reset + re-import at *any* tier reproduces it exactly.

**This is not an oddball.** Steam's `appdetails.steam_appid` resolves a sub-app to its parent
product, so the relationship is explicit and machine-readable. Any multi-app product behaves
this way; Ys is simply the one that surfaced.

---

## The invariant

> **A record must be verified against the thing it describes, to the depth the tier allows —
> and anything that fails verification must never be permitted to define an entry.**

Two corollaries, one per artifact class:

- **Identity.** A compilation's identity may never be assigned to a game that is a member of
  it. Owning a bundle credits members (DESIGN §13); it must never consume them.
- **Media.** An asset may never be chosen for a kind without something having *examined the
  image*. Provider priority is a tie-breaker, not evidence.

Both defects are the same failure: **confidence without inspection.** The pipeline trusts a
match it never verified, and serves an image it never looked at. In the observed case the
cover happened to be correct — all four candidates measured portrait, and the chosen one was
the highest resolution of them — but that was luck, not judgment. Nothing looked.

---

## Tier design

Verification is present in every tier of both pipelines. Tiers differ in *depth*, never in
whether the concern applies.

### Algo (ingest) — deterministic, zero AI

Signals in strength order. Any hit is sufficient to refuse the merge and flag.

1. **Canonical app resolution (authoritative).** `appdetails.steam_appid` ≠ the requested
   appid ⇒ this owned app is a **sub-app of another owned product**. That parent is the
   collection entry; the sub-apps are its members. Free, exact, no guessing.
2. **Provider record type (authoritative).** IGDB `game_type: 3` (bundle) ⇒ the matched
   record is a compilation and **cannot be the identity of an individually-owned app**.
   `igdb.GAME_FIELDS` never requests this field today.
3. **Many-to-one match.** Two or more distinct owned `source_id`s resolving to one provider
   identity is by definition a duplicate or a bundle. Pure SQL over `sources` +
   `metadata_links`.
4. **Shared store name across appids** (fallback, when 1 is unavailable for a non-Steam
   store). Two owned apps reporting one store `name` are one purchased product.
5. **Store name vs ownership name.** A material difference means the ownership name is a
   member/edition label.

Algo's obligation on detection: **do not merge, do not silently pick a title.** Keep the owned
apps as distinct entries, create/attach the parent product entry when signal 1 identifies it,
and record a review flag with the reason. Algo never *guesses* membership — it acts on
authoritative provider statements and otherwise leaves evidence.

### Light (ingest + wand) — AI verification of suspect matches

Adds `aimeta.targets("matched")` — which already exists and is invoked from nowhere in ingest
— scoped to entries Algo flagged, not the whole catalog. Spend guardrail unchanged: scoped to
the run's games, bounded by `ai.check_limit`.

The `metadata` prompt already performs this job — *"VERIFY the current match — is it truly the
SAME game? Watch for remakes, remasters, 'Anniversary'/'HD'/'Definitive' editions"* — and
already emits the `collection` block feeding `compilations.set_collection`. No new prompt is
required; only new *eligibility*.

Light resolves what Algo flagged: confirms the bundle, names it, and enumerates members.

**Standard of proof (explicit).** The AI must not mark something a collection because it
*seems* like one. A title resembling a bundle is a prompt to **investigate**, not a
conclusion. It must establish the actual member list — from provider data, the store record,
or its own researched knowledge of the product — and say so. An unproven suspicion is
reported as a review flag, never written to `collections.sqlite` as fact. This is the
existing §13.4 guardrail applied to *detection confidence*, not just to what qualifies.

### Media verification — the same ladder

Selection today is **blind**: `media_choose.select()` ranks on
`(user_pin, provider_priority, matched, file-before-url, lowest_id)`. No ratio, no
orientation, no resolution — and it *could* not have them, because `media.width`/`height` are
NULL on all 60,488 rows. Only user-uploaded art (`user_media`) ever records dimensions. So the
winner is decided by provider order and insertion order.

Measured workload on the live library, which drove the tier split below:

```
media rows                : 60,488     images if swept blindly : 57,301
(game,system,kind) sets   : 13,390     ...with >1 candidate    :  6,939
ambiguous: cover 2,011 · background 1,998 · screenshot 2,081 · logo 37 · hero 30 · header 22
```

A blind vision sweep is ~57k images; even restricted to ambiguous scalar kinds it is ~4,100
calls per import. That is precisely the large automatic spend the guardrail exists to prevent
— the original decision to gate the picker off was sound. What changes it is that the Algo
shape test resolves most sets **for free**: where only one candidate is correctly oriented for
its kind, there is nothing left to judge. Vision is needed only for the residue — several
correctly-shaped candidates differing on resolution, officialness, or "coolest" — which is a
quality refinement, not a correctness fix.

- **Algo — deterministic inspection (all tiers inherit this).** Record `width`/`height` at
  index/materialize time (the columns already exist), then implement the ratio/resolution pick
  the tiered-ingest spec called for and never delivered: score orientation and resolution *per
  kind*, and **reject an asset whose orientation is wrong for its kind** (a landscape image can
  never be a `cover`; a portrait one can never be a `hero`). Measurement, not judgment — zero
  AI, and it catches every "obviously the wrong ratio" case.
- **Light — vision on `cover` only,** and only for sets where the shape test leaves **more than
  one valid candidate**. Bounded by 2,011 sets worst case and far fewer after Algo prunes. The
  cover is the one asset the user actually looks at in the grid; leaving it to insertion-order
  luck is what surfaced this whole defect. Everything else stays deterministic at Light.
- **Heavy — vision across all kinds,** same residue rule.

Uses the existing `art` area (already scores ratio, orientation, resolution, official-first),
scoped to the run's games and bounded by `ai.check_limit`. `ai_art_auto_pick` remains the
manual-override switch; it is no longer the *only* path to an examined image.

Provider priority remains the tie-breaker **after** the shape test, never before it.

### Heavy (ingest + wand) — verification without a gap precondition

Heavy currently targets `missing`. It gains match verification over **all** games the run
touched, not merely those with attribute holes, plus the existing consensus/web passes. This
is the tier expected to catch a wrong-but-complete match even absent an Algo signal, and to do
the deeper research the Light standard of proof requires when the cheap sources are silent.

---

## Capture changes (prerequisites)

Both are ingest-time and must land **before** any from-scratch import intended as a clean
baseline.

- **Retain `appdetails.steam_appid`** per owned app — enables signal 1, the whole
  deterministic path. Currently discarded.
- **Retain Steam's store `name`.** `_extract_steam_attrs` (`media_fetch.py`) pulls genres,
  categories, developers, publishers, release_date, release_year, description and
  content_type but discards `name`, though the response carries it. Without it, signals 4–5
  are impossible and §13 has no display name for the collection entry.
- **`igdb.GAME_FIELDS` += `game_type`.** One field, enables signal 2, costs nothing.
  (`included_games` is not valid on the current API version — the member list stays a
  Light/Heavy concern.)

---

## Relationship to DESIGN §13

§13 is unchanged and correct. This spec removes the two obstacles preventing it from applying
to store-granted bundles: the collection was **unnameable** (store name discarded), and its
identity **consumed its members** instead of crediting them.

Target end state, matching the Sonic / *Sega Genesis Classics* model:

```
Ys I & II Chronicles+   <- own catalog entry = appid 223810, the owned product (coll_key)
   Ys I                 <- member, "also owned on PC (via Ys I & II Chronicles+)"
   Ys II                <- member (appid 223870), same credit
```

Note the structural inversion from §13's reference case, which the original design did not
contemplate: for *Sega Genesis Classics* the collection is the owned appid and members are
inferred; here **the members are the owned appids and the collection is one of them.**

---

## Decisions (resolved)

1. **What the collection entry's ownership hangs off — RESOLVED.** It has a real appid.
   `steam_appid` names the canonical product (223810), so the collection entry is a genuinely
   owned app, not a synthetic one. The earlier "derive from shared store name" recommendation
   is retained only as the **fallback** (signal 4) for stores lacking canonical-id resolution.
2. **Algo's refusal semantics — RESOLVED.** Two plain entries plus a review flag, matching the
   `confidence:low` precedent. No new UI state.
3. **Back-fill — RESOLVED: none.** Forward-only. The user will re-run a **Light** import after
   finishing a manual pass over the library for further issues; a rebuild re-derives identity
   anyway.

---

## Out of scope

- Any change to tier labels, the spend guardrail, or per-area AI assignment.
- Re-picking existing art. Media verification is **selection-time**, so it applies on the next
  select/materialize pass and needs no re-import — but dimension capture only fills for assets
  the pipeline touches again.

---

## Verification

1. Deterministic unit: an owned app whose `appdetails.steam_appid` differs from its own appid
   must become a **member**, never an independent identity, and never the parent's title.
2. Deterministic unit: a matched IGDB record with `game_type: 3` must not become the
   `game_key` of an entry holding a single owned `source_id`.
3. Regression fixture from the live case: appids 223810 + 223870 must yield **two** member
   entries plus one collection entry named `Ys I & II Chronicles+`, never one entry titled
   "Ys II", and `Ys I` must exist.
4. `Zombie Army Trilogy` must behave exactly as it does today (no regression in the working
   §13 path).
5. Algo must still make **zero** model calls — assert no AI invocation during an Algo import.
6. A "seems like a collection" title with no establishable member list must produce a review
   flag and **no** `collections.sqlite` write.
7. Media: an asset whose orientation contradicts its kind must never be `chosen=1` while a
   correctly-oriented candidate exists — asserted with a landscape ringer injected into a
   `cover` candidate set.
8. Media: after an index pass, `width`/`height` must be non-NULL for every touched asset;
   selection must be provably shape-aware, not correct-by-insertion-order (the Ys case passes
   today by luck and is therefore **not** a valid fixture on its own).
9. Media tier scoping: a Light run must issue vision calls for `cover` sets only, and only
   where >1 candidate survives the shape test — assert zero vision calls for `background`,
   `hero`, `logo`, `header` at Light, and zero for any set the shape test already resolved.
