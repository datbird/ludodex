# Media Magic Wand — one affordance, three choices, every media kind

Status: design approved 2026-08-01, not yet planned or built.

## Why

Three overlapping AI affordances exist on the media surface today and none of them
covers video:

| Where | What | Problem |
|---|---|---|
| Detail panel | "Smart art pick" section | cover ONLY; its apply is not durable |
| Category overlay | `✨ AI: pick best` | per-kind, but a different gesture and a different result path |
| All Media | *nothing* | no action at all |
| Any video | *nothing* | structurally excluded — see §5 |

The wand replaces all of it with ONE gesture that means the same thing everywhere, and
extends it to video, which has never had an AI path.

## 1. The affordance

A single `MediaWand` component in two placements:

- the **All Media** section header — scope: every kind the game has;
- each **category overlay** header — scope: that one kind.

Both open the same three-item menu. The menu states its own scope ("all categories" /
"marquee only") so the gesture is never ambiguous about what it will touch.

It REPLACES the per-category `✨ AI: pick best` button and the detail panel's cover-only
"Smart art pick" section. Consistency is the point: one gesture, not three.

## 2. The three choices

### ① Pick the best I already have

The deterministic layer is not a gate — it is the **evidence the model reasons over**.
For each in-scope kind with 2+ candidates, build a per-candidate table:

    provider · measured W×H · shape-valid for this kind · filler verdict ·
    ref_type · currently-chosen · (video: duration, resolution, codec, has-audio)

That table goes into the prompt beside the thumbnails, and the model makes the final
call.

This repairs a live defect. Today's `art` prompt asks the model to rank on "HIGHEST
resolution / sharpness" while showing it only 256px thumbnails — a property it cannot
possibly observe. It has been guessing. Measured dimensions already exist in
`media.width/height`; they simply were never handed over.

### ② Go find more

Per-kind targeted where the provider supports it (SteamGridDB exposes separate
grid / hero / logo / icon endpoints); a full game fetch where it does not (IGDB, Steam).

**Open web goes through the AI stack's own search — `server/ai.py::_complete_text_web`
— NOT a search API.** That function already unifies Gemini `google_search` grounding
(with grounding-chunk citations), Anthropic `web_search_20250305`, and OpenAI
`web_search`, and the Light/Heavy tiers already use it via `web_scores()` and the
metadata escalation path. The media wand MIRRORS that path; it does not add a parallel
search dependency.

Google Programmable Search (PSE/CSE) is **not** an option and must not be revisited:
it is discontinued and Google no longer issues API keys. `media_web.py`'s `GCSE`
constant and its docstring promise are a stub — no function implements it, nothing
calls it — and should be deleted rather than finished.

`wikimedia()` stays as the cheap deterministic first hit (keyless, the Wikipedia lead
image, usually the box art) with `page_images()` scraping that page for a few more.
Order: providers → wikimedia → grounded AI search for whatever kinds are still empty,
so the paid grounded call runs only against real gaps.

Grounded calls carry a known constraint recorded in `ai.py`: **Gemini cannot enforce
JSON while grounding**, so this path returns text plus sources and needs a parse step —
never a schema-enforced response.

Art returned for OTHER kinds is kept, never discarded — a fetch already paid for in
bandwidth must not be thrown away — and reported separately:

    +3 marquee · +7 other categories

### ③ Both

Runs ② then ①, in that order, so the pick judges the ENLARGED candidate set. Same
lesson as select-after-materialize from the 2026-07-26 audit: choosing before fetching
means choosing from a set you already knew was incomplete.

## 2.5 The deterministic sibling — "Fetch from <provider>"

**Decision 2026-08-01: a MATCH is not an INGEST.** A provider is matched whether or not
anything was ever taken from it. The match records "this game IS that record on
ScreenScraper / SteamGridDB / IGDB / Steam", and that identity is what makes a later
pull possible. Every configured provider is matched for every game — there are no
primary and secondary providers, and a provider contributing zero metadata and zero
media is still matched.

That makes a second button worth having, sitting NEXT TO the wand in both placements —
All Media and each category:

**🪄 Magic Wand** — AI. Judges, chooses, decides. Costs money.
**⬇ Fetch from…** — deterministic. No AI, no judgment, no spend. Lists the providers
this game is MATCHED to, and pulls everything they have for the scope you are in.

The use case in the user's words: *"I don't like any of these backgrounds, I want to
grab all from ScreenScraper."* Previous ingestion may never have taken a single asset
from ScreenScraper — irrelevant. The game is matched, so the pull is a straight fetch
against a known id.

- The menu lists ONLY providers with a real match for this game, each with what it
  holds if known ("ScreenScraper · background"). A provider with no match is shown
  disabled with "not matched" rather than hidden — absent is not the same as unmatched,
  and hiding it makes a missing match look like a missing feature.
- Scope follows the surface: in a category it fetches that kind; in All Media, all kinds.
- It is another caller of `_pull_media_sources` (§7), narrowed by `provider=` and
  `kinds=`. Not a new fetch path.
- **Free by definition.** No AI area is consulted, so this button can never spend money
  — which is what makes it the right default action for "just get me more art".

### 2.5.1 What lands immediately vs what needs confirming

Fetching is ADDITIVE: new candidate rows, nothing overwritten, nothing deleted. So the
candidates land immediately — asking a user to confirm "may I add options?" is friction
with no risk behind it.

What DOES go through the standard review/accept diff is any change to the **chosen**
asset, because that is what the library actually displays. So a fetch reports
"+14 candidates · chosen unchanged", or "+14 candidates · cover would change" with the
before/after — and only that second half needs a click.

## 3. Output — a job like any other

The wand starts a job. **The job applies nothing.** It writes findings stating both
sides (current chosen vs proposed; added images flagged new-vs-already-held), rendered
with the existing `MediaDiffStrip`, reviewed and accepted on the standard review screen
and applied through the standard apply path.

No new confirm surface, no bespoke inline panel. The reason this is worth stating: the
review screen already carries the properties this needs — durable findings, both-sides
statements (§2026-07-26 "Unknown ≠ absent"), selection honoured per change row.

On accept the apply writes `chosen=1` **and** `ai_pick=1`, and marks `art_adjudicated`.

## 4. Spend rules

The app's #1 constraint is that paid AI must never fire by accident. This surface
satisfies it as follows:

1. **Scope is exactly one game** and exactly the kinds in scope — never the catalog.
   There is no multi-game entry point from this surface, and none may be added without
   revisiting this section.
2. **Only kinds with 2+ candidates** are sent. One candidate needs no judgment.
3. **The 6-candidate cap stays and becomes visible.** It is stated in the receipt, so
   truncation is never silent (a silent cap reads as "considered everything").
4. **`art_adjudicated` is consulted.** Re-wanding the same kind does not re-bill unless
   the candidate set actually changed.
5. **No automatic path gains an AI call.** Nothing here runs on a schedule, a sync, or
   a rebuild.

## 5. Video — the kind that never had a path

`video` is a real kind ("preview / trailer") with **3,224 live rows** — steam 3,190,
screenscraper 34 — and every one is `ref_type='url'` pointing at a direct `.webm` /
`.mp4` (Steam serves `movie_max.webm`). None are YouTube embeds, so no `yt-dlp` is
required for the existing corpus.

**Why there is no AI option:** `_thumb_bytes` builds the vision payload with PIL
`Image.open`, which throws on a video and returns `None`. The candidate is dropped
before the model sees it. Nothing is mis-wired; videos simply never reach the model.

### 5.1 Deterministic frame extraction

Same principle as §2① — deterministic tooling produces the evidence, the AI judges it.

- `ffprobe` yields duration, resolution, codec, bitrate, has-audio → straight into the
  evidence table, exactly as W×H does for images.
- `ffmpeg` samples **5 frames** tiled into ONE contact-sheet JPEG per video. The
  sampling window starts at **3s** (publisher logos and black frames carry no
  information about the game) and ends at `min(duration, 120s)`; the 5 frames are
  evenly spaced across it. A video shorter than 3s is sampled from 0.

One image per candidate keeps the vision payload the same shape as every other kind:
N candidates → N images, comparable side by side, one call.

`-ss` before `-i` on a seekable HTTP source means ffmpeg reads only the byte ranges it
needs — a 40 MB trailer costs a few hundred KB to sample, not a full download. Contact
sheets are cached content-addressed by video URL so a re-run re-samples nothing.

### 5.2 What the model decides

Given the contact sheet plus the probe data, the model answers: is this the RIGHT game;
what IS it (trailer / gameplay / teaser / cutscene); and which candidate is best to
feature.

Its determination is stored as evidence on the row — **not** as a new media kind. The
23-kind vocabulary is closed by deliberate design (DESIGN §11.1's reasoning applies to
media kinds too); sub-typing videos would reopen exactly the vocabulary growth that
rule exists to prevent.

### 5.3 Constraints this introduces

- **`ffmpeg`/`ffprobe` must be added to the Dockerfile.** They are not in the image
  (verified live). This is the one real cost of the video half — a meaningful addition
  to a 368 MB image. Prefer a headless/static build over the full `ffmpeg` package.
- **Sampling is bandwidth, not tokens.** Cap sampled candidates at the same 6, and put
  a per-sample time and byte ceiling on the ffmpeg call so an unreachable or
  pathological source cannot hang a job.
- **IGDB videos are YouTube ids**, which cannot be sampled or downloaded here. If ②
  ever surfaces them they are reference-only and must be excluded from ① rather than
  silently scored on absent evidence.
- Degradation is explicit: no ffmpeg → video kinds report "frame sampling unavailable"
  and are skipped, never scored blind.

## 6. Adjacent fix, same surface

`/api/ai/art-apply` writes only `chosen`:

```sql
UPDATE media SET chosen=0 WHERE norm_key=? AND kind=?
UPDATE media SET chosen=1 WHERE id=?
```

`media_choose.select()` zeroes `chosen` on every row and recomputes it from
`(pin, bad_shape, filler, ai_pick, provider_priority, -pixels, …)`. A manual pick has no
term in that sort, so the next sync silently reverts it. This is the same bug class the
2026-07-26 audit fixed for the automated pass; the manual button was missed.

Fix: write `ai_pick=1` and mark `art_adjudicated`. Without it the wand's result leaks
away on the next sync and the paid call is re-bought.

## 7. Modularity — ONE discovery path, not a second one

**Requirement: the wand adds no fetch path and no pick path of its own.** It calls the
same functions the tiered ingest calls, so every future improvement to media discovery
reaches both on the same commit, with no one remembering to update a second copy.

This is not a stylistic preference. This repo has been bitten by exactly this twice:
the one-product-one-collection rule lived only in the scan path while the apply path
wrote duplicates, and `steam_meta.store_name` was captured by one path and read by
none. A parallel implementation is how a fix silently fails to reach half the app.

### 7.1 The seam that already exists

```
_pull_media_sources(con, nk, want_web=False)     <- THE fetch primitive
    ├── _fetch_media_for(nk, want_web)            (one-game hunt; /api/aimeta/refresh-media)
    └── _wand_fill_media(nks, want_web, stop)     (metadata wand's batch media step, ×2)
```

`_pull_media_sources` already pulls IGDB (incl. per-entry override ids), SteamGridDB,
ScreenScraper, and the open-web pass. **Option ② is a fourth caller of this function,
not a fifth implementation.**

### 7.2 Required changes to the shared primitive

- **`kinds=None` parameter.** Per-kind targeting (§2②) is an ARGUMENT, never a fork:
  `kinds=None` keeps today's whole-game behaviour for every existing caller;
  `kinds={'marquee'}` narrows provider queries where the provider supports it and
  still retains extras. No caller changes behaviour by default.
- **Grounded search goes INSIDE it.** The `_complete_text_web` open-web step (§2②)
  is added to `_pull_media_sources`, so the tiered ingest gains it at the same moment
  the wand does. Do not wire grounded search into the wand endpoint.
- **Its docstring's "Google" reference is stale** and must be corrected to name the
  provider-native grounded search — the Google half was never implemented.

### 7.3 The same rule for option ①

Option ① calls `_ai_adjudicate_game`, the existing vision pick used by `_ai_art_pass`
and `/api/aimeta/pick-art`. The evidence table (§2①) is added THERE, so the Light/Heavy
art pass gets the measured-dimension improvement at the same time as the wand. The
wand does not get its own prompt or its own candidate builder.

### 7.4 Extraction

`_pull_media_sources`, `_fetch_media_for` and `_ai_adjudicate_game` currently live in
`server/app.py`, which is far past the size where a shared primitive should hide inside
a route file. Move discovery to **`media_discover.py`** with a documented interface
(`pull(con, nk, kinds=None, want_web=False) -> {kind: added}`), leaving thin wrappers
at the call sites. This is what makes "shares the module" true structurally rather than
by convention, and it is what lets the discovery logic be tested without importing the
FastAPI app.

Keep the extraction mechanical and separate from the behaviour changes above — move
first, verify the existing callers are unchanged, then add `kinds=` and grounded
search. A move tangled with a rewrite is unreviewable.

### 7.5 Enforcement

A contract test asserts there is ONE path: a provider stubbed into `media_discover`
must appear in the results of BOTH the ingest caller and the wand caller. If someone
later adds a second fetch implementation, that test fails.

## 8. Testing

All offline, no live AI, no spend — the vision call is stubbed.

- evidence-table builder (pure): shape, filler, measured dims, provider surfaced
  correctly; unmeasured stays neutral rather than being asserted as zero.
- per-kind targeting: targeted where supported, full fetch where not, extras RETAINED
  and reported separately.
- findings carry both sides; apply writes `chosen` **and** `ai_pick`.
- frame extraction: a fixture video yields 5 frames past the skip window and one
  contact sheet; missing ffmpeg degrades to "unavailable" and skips rather than
  scoring blind.
- spend scoping: a wand run touches only the in-scope game and kinds — the regression
  guard for §4.

## 9. Sequencing

This design sits on top of the currently-uncommitted stack (apply-path collection
guard, platform-vocabulary closure, member-title collapse), particularly the `select()`
ranking that §6 depends on. Land and deploy that first; do not stack a second
undeployed layer on the first.

## Open

- `media_steamgrid_enabled=1` and `media_steamgriddb_enabled=0` both exist in live
  config. Two keys that look like one setting — worth confirming which one the fetch
  path actually reads before ② is built on it.
- The grounded-search prompt for images is new work: `_complete_text_web` is used today
  for SCORES (text answers). Asking it for image URLs means every returned URL must be
  validated as a live image before it is trusted — `media_web`'s existing validation
  discipline applies, and a model-supplied URL is a candidate, never a fact.
