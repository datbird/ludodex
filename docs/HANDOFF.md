# ludodex — Handoff & Context

**One-stop orientation** for picking ludodex back up. Covers what ludodex is, what's
built, and where every document and script lives.

Repo: `github.com/datbird/ludodex` — **public**. Nothing about the maintainer's own
infrastructure belongs in this file; host addresses, credentials and operational notes
live outside the repo.

> **§6 of this document was a PLAN, and it shipped.** It described the AI-forward server
> — FastAPI, the React UI, Docker, `requirements.txt` — as the last open task. All of it
> exists now, and the API is ~230 endpoints rather than the eight sketched below. §6 is
> kept as the record of what was intended, with each subsection marked against what
> actually got built, because several of the decisions in it are still load-bearing and
> the *reasoning* is not written down anywhere else. Do not read it as a description of
> the current system: for that, read `README.md`, `DESIGN.md` and the per-topic pages.

---

## 1. What ludodex is (in one breath)

A single **source-of-truth catalog** of every game you own — emulation ROMs **and** PC
stores — deduped by normalized title (`norm_key`), enriched with metadata and media,
and able to **broker** that truth out to the frontends/devices you actually play on.
It is **not** a launcher, an installer, or a store client. See `DESIGN.md` for the
full vision, boundary, and roadmap.

Two intended uses:
1. **Source of truth** → push curated games + metadata/media to named devices.
2. **Bridge** between ecosystems (Playnite/LaunchBox/ES-DE/RetroDECK), ludodex as the
   broker.

The **Device layer is built**: devices, publish rules, plan/apply, and an install
ledger (`ludodex/publish*.py`, `/api/devices/{id}/publish/*`). `DESIGN.md` has the model.

---

## 2. What's been done (built & in the repo)

| Area | State | Key scripts |
|---|---|---|
| Canonical catalog (dedup on `norm_key`) | ✅ | `build_library.py`, `titlenorm.py`, `romtags.py` |
| Ownership sources: Steam·Epic·GOG·itch·EA·**PSN·Xbox·Nintendo** | ✅ | `steam_owned.py` `epic_owned.py` `gog_owned.py` `itch_owned.py` `ea_owned.py` `psn_owned.py` `xbox_owned.py` `nintendo_owned.py` |
| Emulation ROM index + local archives | ✅ | `build_romdb.py`, `crawl.py`, `process.py` |
| Metadata: **IGDB** (live) | ✅ | `igdb.py`, `igdb_enrich.py` |
| Metadata/media: **ScreenScraper** | ✅ live (devid ships embedded) | `screenscraper.py`, `ss_scrape.py`, `ss_mirror.py` |
| Metadata: TheGamesDB · MobyGames · ArcadeDB · ZXInfo · libretro · Wikidata | ✅ | see `SOURCES.md` |
| Media layer: index → choose → materialize (hybrid) | ✅ | `media.py` `media_index.py` `media_fetch.py` `media_choose.py` |
| Frontend **Playnite** — both ways incl. media | ✅ | `playnite.py` `playnite_import.py` `playnite_export.py` `scripts/playnite_bridge.ps1` |
| Frontend **LaunchBox** — both ways incl. media | ✅ | `launchbox.py` `launchbox_import.py` `launchbox_export.py` |
| Backing store: two-way sync (PocketBase/Postgres/Supabase/MySQL/Firestore) | ✅ | `dbsync.py`, `remote_db.py` — see `SYNC.md` |
| **FastAPI server + React SPA + Docker** (this doc's §6) | ✅ | `server/app.py`, `web/`, `Dockerfile`, `requirements.txt` |
| **Device publishing** (rules → plan → apply → ledger) | ✅ | `publish.py` `publish_plan.py` `publish_apply.py` `publish_profiles.py` |
| Config + integrations registry | ✅ | `config.py` (`config.py integrations`) |
| Orchestration | ✅ | `scripts/update.sh`, `scripts/auth_status.sh`, `scripts/setup.sh` |

**Recent session highlights:** EA direct puller (3rd ownership leg), the whole media
layer (95k assets indexed, ~71% catalog has art, content-addressed chosen repo),
**Playnite & LaunchBox both-ways including media** (the frontend-sync hub), and the
`DESIGN.md` device-layer spec.

**Data artifacts (gitignored — they live in `$LUDODEX_DATA`, `/data` in the container):**
`game-library.sqlite` (catalog), `media-index.sqlite` (assets by `norm_key`),
`metadata-cache.sqlite`, `config.sqlite`, `roms-index.sqlite`, `screenscraper-cache.sqlite`
and the rest — `SCHEMA.md` has the full list — plus the `media/` content-addressed repo
(`<sha1>.<ext>`).

---

## 3. Document & code index

**Docs (all in `docs/`, except `README.md`):**
- **`README.md`** — user-facing overview: why, quick start, how it works, schema, per-
  feature sections (media, Playnite, LaunchBox, sync), configuration, auth.
- **`AUTH.md`** — every integration's credentials: how to obtain each token/key, a
  quick-reference table, credential resolution (env > config), commercial-use posture.
- **`DESIGN.md`** — the canonical spec & roadmap: vision, IS/IS-NOT boundary, the
  **Device layer** model (Accounts, Devices, channels, install ledger, detect/pin,
  `origin` provenance, changelog, conflict awareness), and the Build-now / Next /
  Someday docket. **Selection policy** is the one open design decision.
- **`HANDOFF.md`** — this file.
- **`AI.md`** — how the AI features get model access (BYOAI: an API key, from one of
  four providers), the 14 configurable areas, and how a model is chosen per area.
- **`SYNC.md`** — the two-way backing store (`dbsync.py`).
- **`DOCKER.md`** · **`CONFIG.md`** · **`PIPELINE.md`** · **`SOURCES.md`** ·
  **`SCHEMA.md`** · **`FRONTENDS.md`** · **`CLOUDFLARE.md`** · **`TASKS.md`**.
- **`skills/*/SKILL.md`** — the 5 Claude skills: `games-update`, `games-query`,
  `games-auth`, `games-sync`, `games-playnite`.

**Self-service:** `python3 ludodex/config.py integrations` lists every integration with its
credential steps; `python3 ludodex/config.py integrations <id>` drills in.

---

## 4. Where it runs

ludodex runs as a **single container** (`Dockerfile`, `docker-compose.yml`, see
`DOCKER.md`): FastAPI + the built React SPA + every OS tool the pipeline shells out to.
All durable state is one volume at `/data`. Host addresses and credentials are tracked
outside this repo.

The historical **producer/consumer split** is worth knowing because it still shapes the
code: the Steam Deck was the producer for Deck-local sources it alone could see (ES-DE /
RetroDECK media, the ROM index) and pushed its SQLite DBs to the server. That is why the
pipeline scripts are standalone, and why everything joins on `norm_key` rather than a row
id — `game_id` is rebuilt on every run. Today one container runs the whole pipeline, and
other machines are reached as **devices** (`ludodex/devices.py`) rather than as a second
half of the build.

**Getting a checkout running without Docker:**
```bash
git clone https://github.com/datbird/ludodex.git
cd ludodex
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -c requirements.lock
python3 ludodex/config.py init
bash scripts/setup.sh            # guided credential walkthrough
```

---

## 5. Status of the task list

The live backlog is **`TASKS.md`**, not this file. As of this rewrite everything section
6 planned is shipped, ScreenScraper is live (the devid ships embedded, so there is
nothing to unblock), and the device layer is built.

---

# 6. PLAN — AI-forward server — **SHIPPED**

> **HISTORICAL.** This was the plan; it was built. Each subsection below is marked
> against what exists. Kept because the *reasoning* behind several still-live decisions
> is recorded nowhere else. For the current system read `README.md`, `DESIGN.md`,
> `DOCKER.md` and `AI.md`.

> **Goal:** turn ludodex from a CLI catalog into a service: a **REST API** + **Web UI**
> that **serves the media itself**, hosted on the AI-server VM, with **AI** features
> (natural-language search, smart art/metadata picks, dedupe assist). Stack is locked:
> **Python FastAPI + React/Vite**, reusing the existing pipeline in-process.

### 6.1 Architecture — **BUILT, differently**

*What shipped:* one Docker container, not a systemd + nginx deployment on a VM, and the
Deck→server push was replaced by the device layer. The in-process reuse of the pipeline
modules is exactly as planned and is still how it works.


```
   PRODUCER (Steam Deck)                 SERVER HOST (<user>@<ai-server-host>)
   ─────────────────────                 ──────────────────────────────────
   builds Deck-local data:               FastAPI app (uvicorn)
     ES-DE/RetroDECK media   ── push ──►   ├─ reads game-library.sqlite (catalog)
     ROM index                 (rsync/      ├─ reads media-index.sqlite (assets)
   media/ chosen repo          PocketBase)  ├─ reads metadata-cache.sqlite
                                            ├─ media/ chosen repo (+ serve cache)
   remote pulls can run on EITHER box       ├─ runs remote pulls (steam/igdb/ss)
   (network calls): steam/igdb/ss           └─ AI calls → Anthropic API (Claude)
                                          React/Vite SPA (static) behind nginx/caddy (TLS)
                                          systemd service; SQLite in WAL mode
```

FastAPI imports the existing modules (`config`, `build_library` helpers, `media`,
`media_choose`, the `*_owned`/`*_export` scripts) **in-process** — no rewrite; the
pipeline becomes a library the API calls.

### 6.2 Data access — **BUILT: (c)**

*What shipped:* option **(c)**, not the recommended (a). The whole pipeline runs on the
server; other machines are devices. Option (b) — PocketBase as the shared store — became
something better than a push: the two-way **backing store** (`dbsync.py`, `SYNC.md`),
where SQLite is the local cache and the remote holds the durable truth.


- **Read-mostly** over the SQLite DBs (WAL mode for concurrent reads).
- Join everything on **`norm_key`** (the stable key; game_id is rebuilt each run).
- The `media/` repo is the content-addressed store (`<sha1>.<ext>`); the index's
  `chosen` rows point at the best asset per `(norm_key, kind)`.
- **Push mechanism (decide):**
  - **(a) rsync the DBs + repo** Deck→VM after each `scripts/update.sh` (simplest; what the
    locked plan assumed).
  - **(b) PocketBase/Firestore as the shared store** — the one-way `sync.py` mirror (now
    retired) already pushed the catalog; the API could read PocketBase instead of SQLite
    (adds a hop, gains a ready API + auth).
  - **(c) run the whole pipeline on the VM** and have the Deck only push its ES-DE
    media + ROM index. Cleanest long-term; needs the store creds on the VM.
  - **Recommendation:** start with (a) — fewest moving parts; revisit (c) once the
    server is real.

### 6.3 API surface (v1) — **BUILT, ~230 endpoints**

*What shipped:* everything below, plus a great deal more (devices, publish, collections,
fileops, backups, AI settings and spend, provider mirrors, review queues). Two
corrections to the sketch: **`/api/search` is a `POST`**, not a `GET` — a
natural-language query with its filters does not belong in a URL — and every `/api/*`
route sits behind the session middleware.


```
GET  /api/games            list/search; filters: q, source, platform, has_kind=cover,
                           account, installed_on=<device>; pagination
GET  /api/games/{norm_key} full detail: sources, attributes, all media kinds, links
GET  /api/media/{norm_key}/{kind}    resolve + STREAM the chosen asset (the core value)
                           ?size=thumb|full  ?format=  → materialize-on-serve + cache
GET  /api/stats            counts + media coverage (per kind / per source)
GET  /api/search?q=...     natural-language / semantic search (AI; see 6.5)
# Device layer (later, per DESIGN.md):
GET  /api/devices /accounts /conflicts          read the ledger
POST /api/ownership /pins /ack                  manual edits → write ledger + changelog
```

**Media serving = the headline feature.** `GET /api/media/{norm_key}/{kind}`:
1. Look up the `chosen` row for `(norm_key, kind)` in `media-index.sqlite`.
2. If `sha1` present and `media/<sha1>.<ext>` exists → stream it.
3. Else **materialize on serve** (`media_choose._materialize_row`: copy local / fetch
   remote, incl. signed ScreenScraper URLs), persist to the repo, cache, stream.
4. Optional on-the-fly **resize/format** for thumbnails (Pillow) with a thumb cache.
This realizes the "hybrid" media decision: eager-all is ~17 GB, so the server keeps
the chosen repo small and fills it lazily as assets are requested.

### 6.4 Web UI (React/Vite) — **BUILT**


- **Library grid** — covers, filter/search bar, source & platform facets, "has art"
  toggles; click → detail.
- **Detail** — all media kinds (cover/background/logo/screenshot…), attributes,
  sources, install state (once the device layer exists).
- **Admin/Conflicts** (later) — the `DESIGN.md` conflict surface: open conflicts,
  acknowledge/un-acknowledge, changelog viewer.
- Build static; serve from FastAPI or nginx. Talks only to `/api/*`.

### 6.5 AI features — **BUILT, 14 areas**

*What shipped:* far more than the three below — see the area table in `AI.md`. Two things
this section got wrong and that `AI.md` used to repeat:

- **There is no subscription / Agent SDK / `claude -p` path.** All four providers
  (`anthropic`, `openai`, `gemini`, `openrouter`) are API-key based. Nothing else was
  ever implemented.
- **Cost tiering is not automatic.** Every area defaults to the active provider's default
  model — Haiku, on `anthropic`. A bigger model is a per-area setting
  (`ai_area_<id>_model`) you choose; nothing steps up on its own.

Store any API key per `AUTH.md`'s credential convention.

1. **NL search** — "co-op platformers I own playable on the Deck" → the model emits a
   structured catalog query (function-calling against the schema) + optional semantic
   rerank of titles/descriptions. Endpoint: `POST /api/search` (this said `GET`).
2. **Smart art/metadata pick** — when providers disagree or a kind is missing, an AI
   assist proposes the best cover/metadata (or flags low-quality art). Augments the
   deterministic `media_choose` priority, never silently overrides it.
3. **Dedupe assist** — surface likely same-game pairs that `norm_key` missed (regional
   titles, punctuation, sub-titles); the model adjudicates merge/no-merge, a human
   confirms. Feeds back into `titlenorm` rules.

### 6.6 Deployment — **BUILT as Docker**

*What shipped:* the "optionally containerize" line at the bottom of this list is what
happened, and it replaced the rest. `uvicorn` runs as the container's `CMD`; the SPA is
built in a Docker stage and served by FastAPI; TLS is the reverse proxy's job outside the
container (`CLOUDFLARE.md`). `requirements.txt` exists, and is pinned by
`requirements.lock`.

- ~~`uvicorn` under **systemd**; **nginx/caddy** for TLS + static SPA.~~
- SQLite **WAL**; the DBs are read-mostly (writes only for ledger edits + serve-cache
  sha1 backfill).
- Secrets live in `config.sqlite` (env var > config value); ludodex never reads
  1Password at runtime.
- Optionally containerize (Docker) for a one-command deploy — the original vision
  mentioned a "single Linux/Docker deployment." **← this is what was done.**
- ~~Add a `requirements.txt` (fastapi, uvicorn, pillow, anthropic, …) when build starts.~~
  Done; see `requirements.txt` + `requirements.lock`.

### 6.7 Phasing — **all four phases done**


1. **API core + media resolver** — `/api/games`, `/api/games/{nk}`, `/api/media/...`
   (materialize-on-serve), `/api/stats`. The DB-push mechanism (§6.2). Read-only.
2. **Web UI** — library grid + detail over the API.
3. **AI** — NL search first (highest value), then art-pick, then dedupe assist.
4. **Device-layer API** — once that's built (`DESIGN.md`): devices/accounts/conflicts
   endpoints + the manual-edit POSTs writing the ledger + changelog. Enables a future
   **multi-user client** (auth + per-user actor — Someday).

### 6.8 Open questions — **answered**

- ~~**DB push mechanism**~~ → (c), plus the two-way backing store. See 6.2.
- ~~**Auth**~~ → real accounts with session middleware over every `/api/*` route, plus
  Cloudflare Access as an optional outer gate (`CLOUDFLARE.md`).
- **Selection policy** (`DESIGN.md` §9) — not a server blocker, but the device-layer
  endpoints need it.
- **Serve-cache eviction** — when the lazily-materialized repo grows, LRU-evict
  non-chosen/thumb assets (chosen originals are cheap to keep).

---

## 7. Parked / Someday (don't lose these)

- ~~**ScreenScraper**~~ — live; the devid ships embedded.
- ~~**Device layer v1**~~ — built, including publish rules/plan/apply and the ledger.
- ~~**New sources: PlayStation, Xbox**~~ — both built (`psn_owned.py`, `xbox_owned.py`),
  as is Nintendo (`nintendo_owned.py`). Ubisoft/Battle.net/Amazon as they come.
- **Bidirectional / hub-elsewhere sync** and the **multi-user client app**.
- **Install-triggering** (legendary/store installs) — explicitly out of scope.

---

*Section 6 is a record, not a roadmap — it shipped. The live backlog is `TASKS.md`.*
