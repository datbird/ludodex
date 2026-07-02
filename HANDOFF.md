# ludodex — Handoff & Context

**One-stop orientation** for picking ludodex back up (esp. now that dev is moving to
the AI-server VM). Covers what ludodex is, what's built, where every document/script
lives, and — the main event — the **full plan for the last open task: the AI-forward
server (§6)**.

Repo: `github.com/datbird/ludodex` (private). Last pushed HEAD at time of writing:
see `git log -1`.

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

The next big arc is the **Device layer** (designed, not built — `DESIGN.md`). The
**immediate** next build is the **AI-forward server** (this doc, §6).

---

## 2. What's been done (built & in the repo)

| Area | State | Key scripts |
|---|---|---|
| Canonical catalog (dedup on `norm_key`) | ✅ | `build_library.py`, `titlenorm.py`, `romtags.py` |
| Ownership sources: Steam·Epic·GOG·itch·**EA** | ✅ | `steam_owned.py` `epic_owned.py` `gog_owned.py` `itch_owned.py` `ea_owned.py` |
| Emulation ROM index + local archives | ✅ | `build_romdb.py`, `crawl.py`, `process.py` |
| Metadata: **IGDB** (live) | ✅ | `igdb.py`, `igdb_enrich.py` |
| Metadata/media: **ScreenScraper** | ⏸ code done, **blocked on devid** | `screenscraper.py`, `ss_scrape.py` |
| Media layer: index → choose → materialize (hybrid) | ✅ | `media.py` `media_index.py` `media_fetch.py` `media_choose.py` |
| Frontend **Playnite** — both ways incl. media | ✅ | `playnite.py` `playnite_import.py` `playnite_export.py` `playnite_bridge.ps1` |
| Frontend **LaunchBox** — both ways incl. media | ✅ | `launchbox.py` `launchbox_import.py` `launchbox_export.py` |
| Remote sync (PocketBase / Firestore) | ✅ | `sync.py`, `requirements-firebase.txt` |
| Config + integrations registry | ✅ | `config.py` (`config.py integrations`) |
| Orchestration | ✅ | `update.sh`, `auth_status.sh`, `setup.sh` |

**Recent session highlights:** EA direct puller (3rd ownership leg), the whole media
layer (95k assets indexed, ~71% catalog has art, content-addressed chosen repo),
**Playnite & LaunchBox both-ways including media** (the frontend-sync hub), and the
`DESIGN.md` device-layer spec.

**Data artifacts (gitignored — live on the producer machine):**
`game-library.sqlite` (catalog), `media-index.sqlite` (assets by `norm_key`),
`metadata-cache.sqlite` (IGDB), `config.sqlite`, `screenscraper-cache.sqlite`, and
the `media/` content-addressed repo (`<sha1>.<ext>`). The Deck-local ROM index is
`/home/deck/roms-index.sqlite`.

---

## 3. Document & code index

**Docs (all in the repo root):**
- **`README.md`** — user-facing overview: why, quick start, how it works, schema, per-
  feature sections (media, Playnite, LaunchBox, sync), configuration, auth.
- **`AUTH.md`** — every integration's credentials: how to obtain each token/key, a
  quick-reference table, credential resolution (env > config), commercial-use posture.
- **`DESIGN.md`** — the canonical spec & roadmap: vision, IS/IS-NOT boundary, the
  **Device layer** model (Accounts, Devices, channels, install ledger, detect/pin,
  `origin` provenance, changelog, conflict awareness), and the Build-now / Next /
  Someday docket. **Selection policy** is the one open design decision.
- **`HANDOFF.md`** — this file.
- **`AI.md`** — how the AI features get model access (BYOAI): your own Claude
  subscription vs. a developer API key, what each allows, and the (currently paused)
  subscription Agent-SDK credit. Read before wiring AI auth.
- **`skills/*/SKILL.md`** — the 5 Claude skills: `games-update`, `games-query`,
  `games-auth`, `games-sync`, `games-playnite`.

**Self-service:** `python3 config.py integrations` lists every integration with its
credential steps; `python3 config.py integrations <id>` drills in.

---

## 4. Dev is moving to the AI-server VM

Going forward the server lives on the **AI-server VM** (the locked decision — it's
always-on; the Deck can be off). Host address and SSH access creds are tracked
outside this repo (operational notes / your own secret store), never committed here.

**Producer/consumer split (important):** the **Deck stays the producer** for
Deck-local sources it alone can see — ES-DE/RetroDECK media on the microSD, the ROM
index. It builds those and **pushes** the SQLite DBs + the `media/` repo to the VM.
The **VM** runs the API/UI/AI and can itself run the remote-source pulls (Steam/IGDB/
ScreenScraper) since those are just network calls. Decide the exact push mechanism in
§6 (rsync DBs vs. run the pipeline on the VM vs. PocketBase as the shared store).

**Getting the repo onto the VM:**
```bash
ssh <user>@<ai-server>
git clone https://github.com/datbird/ludodex.git
cd ludodex
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-firebase.txt   # + FastAPI deps once §6 adds requirements.txt
# bring over the data artifacts (gitignored) from the Deck:
#   rsync -av deck:~/game-ownership/{*.sqlite,media} ./
python3 config.py init
```
Node 22 + pnpm are already on the Deck for the React build (`deck-build-toolchain`
memory); install the same on the VM, or build the SPA on the Deck and ship static.

---

## 5. Status of the task list

- ✅ Done: IGDB pass, media layer (local + remote + choose/materialize), EA puller,
  Playnite media both-ways, LaunchBox integration.
- ⏸ **ScreenScraper** — removed from the active list; **parked pending the forum
  devid/devpassword**. Code is complete; unblock steps are in `DESIGN.md` §11.
- ◻ **AI-forward server** — the only open task. Full plan below.

---

# 6. PLAN — AI-forward server (the last task)

> **Goal:** turn ludodex from a CLI catalog into a service: a **REST API** + **Web UI**
> that **serves the media itself**, hosted on the AI-server VM, with **AI** features
> (natural-language search, smart art/metadata picks, dedupe assist). Stack is locked:
> **Python FastAPI + React/Vite**, reusing the existing pipeline in-process.

### 6.1 Architecture

```
   PRODUCER (Steam Deck)                 AI-SERVER VM (<user>@<ai-server>)
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

### 6.2 Data access

- **Read-mostly** over the SQLite DBs (WAL mode for concurrent reads).
- Join everything on **`norm_key`** (the stable key; game_id is rebuilt each run).
- The `media/` repo is the content-addressed store (`<sha1>.<ext>`); the index's
  `chosen` rows point at the best asset per `(norm_key, kind)`.
- **Push mechanism (decide):**
  - **(a) rsync the DBs + repo** Deck→VM after each `update.sh` (simplest; what the
    locked plan assumed).
  - **(b) PocketBase/Firestore as the shared store** — `sync.py` already mirrors the
    catalog; the API could read PocketBase instead of SQLite (adds a hop, gains a
    ready API + auth).
  - **(c) run the whole pipeline on the VM** and have the Deck only push its ES-DE
    media + ROM index. Cleanest long-term; needs the store creds on the VM.
  - **Recommendation:** start with (a) — fewest moving parts; revisit (c) once the
    server is real.

### 6.3 API surface (v1)

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

### 6.4 Web UI (React/Vite)

- **Library grid** — covers, filter/search bar, source & platform facets, "has art"
  toggles; click → detail.
- **Detail** — all media kinds (cover/background/logo/screenshot…), attributes,
  sources, install state (once the device layer exists).
- **Admin/Conflicts** (later) — the `DESIGN.md` conflict surface: open conflicts,
  acknowledge/un-acknowledge, changelog viewer.
- Build static; serve from FastAPI or nginx. Talks only to `/api/*`.

### 6.5 AI features (Claude — phased)

Tier the models by cost: **Haiku 4.5** for cheap
classification/extraction, **Sonnet 4.6** for most reasoning, **Opus 4.8** for the
hardest calls. **Model access is BYOAI — see `AI.md`** for the full rules: a self-hosting
subscriber can run it on their own Claude **subscription** (Agent SDK / `claude -p`;
currently draws from plan usage limits), while any multi-user/hosted deployment needs a
**developer API key** (Anthropic / OpenRouter / etc.); a subscription includes no raw API
credits. Store any API key per `AUTH.md`'s credential convention (add an integration entry).

1. **NL search** — "co-op platformers I own playable on the Deck" → the model emits a
   structured catalog query (function-calling against the schema) + optional semantic
   rerank of titles/descriptions. Endpoint: `GET /api/search`.
2. **Smart art/metadata pick** — when providers disagree or a kind is missing, an AI
   assist proposes the best cover/metadata (or flags low-quality art). Augments the
   deterministic `media_choose` priority, never silently overrides it.
3. **Dedupe assist** — surface likely same-game pairs that `norm_key` missed (regional
   titles, punctuation, sub-titles); the model adjudicates merge/no-merge, a human
   confirms. Feeds back into `titlenorm` rules.

### 6.6 Deployment on the VM

- `uvicorn` under **systemd**; **nginx/caddy** for TLS + static SPA.
- SQLite **WAL**; the DBs are read-mostly (writes only for ledger edits + serve-cache
  sha1 backfill).
- Secrets live in `config.sqlite` (env var > config value); ludodex never reads
  1Password at runtime.
- Optionally containerize (Docker) for a one-command deploy — the original vision
  mentioned a "single Linux/Docker deployment."
- Add a `requirements.txt` (fastapi, uvicorn, pillow, anthropic, …) when build starts.

### 6.7 Phasing (maps to the locked plan: server core → UI → AI)

1. **API core + media resolver** — `/api/games`, `/api/games/{nk}`, `/api/media/...`
   (materialize-on-serve), `/api/stats`. The DB-push mechanism (§6.2). Read-only.
2. **Web UI** — library grid + detail over the API.
3. **AI** — NL search first (highest value), then art-pick, then dedupe assist.
4. **Device-layer API** — once that's built (`DESIGN.md`): devices/accounts/conflicts
   endpoints + the manual-edit POSTs writing the ledger + changelog. Enables a future
   **multi-user client** (auth + per-user actor — Someday).

### 6.8 Open questions (decide before/while building)

- **DB push mechanism** — §6.2 (a)/(b)/(c). *Recommend (a) to start.*
- **Auth** — v1 single-user (a static token / LAN-only) vs. real multi-user now. The
  `DESIGN.md` actor model wants per-user attribution eventually; don't over-build it
  for v1.
- **Selection policy** (`DESIGN.md` §9) — not a server blocker, but the device-layer
  endpoints need it.
- **Serve-cache eviction** — when the lazily-materialized repo grows, LRU-evict
  non-chosen/thumb assets (chosen originals are cheap to keep).

---

## 7. Parked / Someday (don't lose these)

- **ScreenScraper** — finish the moment the devid arrives (`DESIGN.md` §11).
- **Device layer v1** (push-only) — full model in `DESIGN.md`; build after/with the
  server.
- **New sources** — Sony **PlayStation** (PSN console + PC via partner stores) and
  Microsoft **Xbox / Microsoft Store** (PC Game Pass + Xbox console); both
  account-aware. Ubisoft/Battle.net/Amazon as they come.
- **Bidirectional / hub-elsewhere sync** and the **multi-user client app**.
- **Install-triggering** (legendary/store installs) — explicitly out of scope.

---

*Everything above is in the repo. The one open task is §6; the one open design
decision is selection policy (`DESIGN.md` §9). Start the server at Phase 1 (§6.7).*
