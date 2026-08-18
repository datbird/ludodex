# Side acquisition — a background layer for data that is expensive to obtain

*2026-08-18. Approved shape: a single runner with a job registry (approach A).*

## The problem

Some provider data cannot be fetched when it is wanted. MobyGames rations 720 requests an
hour and holds 260,337 disc serials behind an endpoint with no batching — 424,384 requests,
about 25 days of continuous polling. That is not a thing a sync can do, an import can wait
for, or a user can be asked to sit through. Today it is simply switched off
(`mobygames_product_codes` defaults to `0`), which is correct and also permanent: there is
no path by which it ever gets obtained.

The same is true in smaller ways of ArcadeDB, ZXInfo and OpenVGDB. Clients exist for the
first two; none of the three has ever been walked.

**What is missing is not a client. It is somewhere for a month-long job to live.**

The three existing walkers (`ss_mirror`, `moby_mirror`, `tgdb_mirror`) each solved this
privately: a cursor in their own database, pacing in their own module, and a hand-written
shell loop in a hand-created container. That worked three times. It does not generalise —
seven containers, seven retry loops, and no single place to see what is accumulating.

## Goals

- A long-running job can make progress, be interrupted, and resume without re-spending.
- Adding a fifth source is a registry entry, not a container.
- One place to see what every background job has obtained and how far it has to go.
- Rate limits are never re-implemented; the runner defers to each module's own pacing.

## Non-goals

Stated explicitly because these are the obvious next things and they are **out of scope**:

- **No enrichment.** Nothing here feeds `build_library`, the match index, or a game's
  attributes. These jobs fill their own caches. Bringing that data into the library is a
  separate piece of work that must go through the one existing chain, never a new onramp.
- **Not Match Supplement 1.0.** The supplement packages the *existing* sources once the
  ScreenScraper walk completes. None of these four are in it.
- **No rewrite of the three existing walkers.** They work and one of them is mid-walk.
  They may be registered as jobs later; this design does not touch them.

## Architecture

A single module, `acquire.py`, holding a registry of jobs and a loop that runs them.

```
  acquire.py                     each job module
  ─────────────                  ────────────────
  JOBS = [...]                   step(budget)  -> Progress
  run_forever()      ──calls──>  status()      -> dict
  next_runnable()                (pacing lives HERE, not in the runner)
```

**The runner owns:** which job goes next, whether a job is enabled, whether a job is
finished, recording the outcome of each step, and the aggregate status.

**The runner does not own:** rate limiting, cursors, or schemas. Each job persists its own
cursor in its own database, exactly as the existing walkers do. A runner that held cursors
would become a second place the truth lives, which is the failure mode this codebase keeps
paying for.

### The job protocol

A job is a module exposing three things:

| member | contract |
|---|---|
| `step(budget)` | Do a **bounded** chunk of work — at most `budget` requests — persist the cursor, and return `Progress(done_units, complete, reason)`. Must be safe to kill between calls. |
| `status()` | A dict describing position and remaining work. Must be readable with the job stopped. |
| `ENABLED_KEY` | The config key gating the job. Absent or `0` means the runner never calls `step`. |

`step()` is bounded so the runner keeps control: a job cannot monopolise the process, and
a stop request is honoured within one step rather than in 25 days.

### Completion is permanent until asked again

The rule already written into all three walker containers, moved from shell into code: **a
job that reports `complete` is not called again.** Restarting a finished job because it
finished is how `ss_mirror` and `tgdb_mirror` both burned a walk. Re-running is an explicit
act (`--restart <job>`), which clears the completion flag and lets the cursor ask "is there
more?" from where it stopped.

### Pacing stays in the modules

Two mechanisms already exist and both are kept:

- `mobygames._pace()` — a **persisted** rolling-hour window with a reserve. It survives
  restarts, which is why a 25-day job can be killed freely.
- `config.rate_limits(service)` — `{cooldown_ms, per_min, per_day}`, already used by
  `arcadedb`, `zxinfo`, `ra` and `steam_tags`.

The runner calls `step()` and the module blocks itself. The runner never sleeps on a
provider's behalf.

## The four jobs

Their shapes differ more than they first appear, and two of them need an **enumeration
source** the existing client cannot provide. That is the substance of this design, not the
runner.

### 1. `moby_codes` — the reason this exists

Walks `/games/{id}/platforms/{pid}` for every (game, platform) pair. The pairs are already
in the local mirror (`moby_platforms`, 424,384 rows), so nothing needs enumerating from the
API — the cursor is a position in a local table.

The same response carries product codes **and** per-platform attributes, ratings and release
records, so this one pass also fills the per-platform detail the slim mirror deliberately
skipped. One walk, two payloads.

**Ordering is part of the design.** A serial matters most where a hash stops working: CHD
and RVZ conversion changes every CRC/MD5/SHA1 while the serial pressed into the disc does
not. So the queue is ordered disc-first:

| slice | pairs | time at 720/hr |
|---|---|---|
| CD-based (PS1/PS2/PSP/Wii/GC/Saturn/Dreamcast/SegaCD/PCE-CD/3DO) | ~15,500 | ~21 hours |
| rest of the cartridge/disc era | ~37,500 | ~2.2 days |
| everything else (Windows 112k, Mac, Linux, mobile, browser) | ~371,000 | ~21 days |

Same total, but the part that solves the problem lands on day one instead of day twenty-five.

Gated by the existing `mobygames_product_codes`, which stays `0` by default.

### 2. `arcadedb` — lookup-only, needs a seed

`arcadedb.query(set_name)` takes a MAME set name. **There is no listing endpoint**, so the
job cannot walk anything until it is given a list of set names to ask about.

The obvious seed is not available: `libretro_dats.py` fetches the **Redump** (21) and
**No-Intro** (92) collections, which are disc and cartridge dumps. MAME is neither, so
nothing local currently enumerates arcade sets.

The seed is therefore **the library itself**. A MAME set name *is* the ROM filename —
`pacman.zip` is the set `pacman` — so the arcade entries already in the catalog enumerate
themselves, at zero cost, and the list grows as the library does. This obtains what is
useful now rather than interrogating thousands of sets nobody owns.

Adding a MAME DAT collection to `libretro_dats.py` would give full coverage later. It is
not required for this job to work and is not in this spec.

### 3. `zxinfo` — enumerable, but not naively

Measured against the live API on 2026-08-18:

- `GET /v3/search?query=*&mode=compact` returns results and reports
  `total: {value: 10000, relation: "gte"}`.
- Offset paging works at 0 and 500 and returns **HTTP 503 at 9,000 and beyond**.

That is Elasticsearch's deep-paging window surfacing as a server error. A naive offset walk
would therefore cover roughly the first 9,000 entries and then fail — and if the failure were
ever swallowed, it would look exactly like a completed catalogue.

So the job **partitions** the query space (by year of release, falling back to machine type)
so no single slice approaches the window, and walks each slice independently. A slice that
still returns 503 is a defect to surface, never a slice to skip.

### 4. `openvgdb` — a download, not a walk

No client exists. OpenVGDB publishes a SQLite database as a release artifact; the job
downloads it, reads the 20,264 ROM serials, and completes in a single step. It is in this
subsystem for uniformity of status and gating, not because it is slow.

Recorded so it is not re-litigated: OpenVGDB's cross-references are **GameFAQs**-derived, not
TheGamesDB. Its value here is serials only.

## Persistence

Each job owns its own SQLite file under `LUDODEX_DATA`, following the mirror convention:

| job | file |
|---|---|
| `moby_codes` | `moby-catalog.sqlite` (new tables alongside the existing mirror) |
| `arcadedb` | `arcadedb-cache.sqlite` |
| `zxinfo` | `zxinfo-cache.sqlite` |
| `openvgdb` | `openvgdb.sqlite` (the downloaded artifact itself) |

The runner keeps only its own small state — per-job completion flag, last step outcome,
last error — in `acquire.sqlite`. Nothing in it is a source of truth about a catalogue.

## Deployment

One container, `ludodex-acquire`, built from the same image and running
`python3 /app/ludodex/acquire.py --run`. Separate from the app container so a month-long
walk never competes with request handling and is not killed by an app redeploy.

`--restart unless-stopped` is correct here (unlike the individual walkers) because the
runner is a long-lived service, not a job that completes; completion is tracked per job
inside it.

## Status surface

`GET /api/acquire` returns each job's `status()` plus the runner's own view (enabled,
running, complete, last error). This is the one place the answer to "what is accumulating?"
lives.

A UI panel is **not** in this spec. The endpoint comes first; the panel is a follow-up once
there is something real to render.

## Error handling

- A `step()` that raises is recorded against that job and the runner moves on. A job that
  fails N consecutive steps is marked `stalled` and skipped until explicitly resumed, so one
  broken provider cannot starve the others.
- A job never marks itself complete on an error. **An error is not an ending** — the
  distinction this codebase has paid for repeatedly.
- `stalled` and `complete` are different states and are reported differently.

## Testing

Offline, in the existing `tests/` style, against fixtures — never the live providers:

- **Runner:** a fake job proves bounded stepping, that a `complete` job is never called
  again, that a raising job is isolated and marked `stalled` rather than complete, and that
  a disabled job is never called.
- **`moby_codes`:** the queue is ordered disc-first; the cursor resumes mid-walk without
  re-requesting; a killed step loses at most one step's work.
- **`zxinfo`:** partitioning keeps every slice under the deep-paging window; a 503 is
  surfaced, never treated as an empty slice.
- **`arcadedb`:** an unseeded run does nothing and says so, rather than reporting complete.

The last two are the specific guard against this project's recurring bug shape — an absence
read as an answer.

## Risks

- **MobyGames terms are non-commercial on every tier.** This data can never end up in
  anything sold, which constrains what a future supplement may contain. Flagged here because
  the supplement question is coming.
- **The Moby walk consumes the entire 720/hr budget for weeks.** Nothing uses the Moby API
  interactively today (`metadata_mobygames_enabled` is `0` and no enrichment path exists), so
  this costs nothing now — but the reserve in `_pace()` must be respected when it does.
- **ZXInfo's 503 is inferred to be the ES window**, from the offsets where it starts. If it
  turns out to be ordinary rate limiting, the partitioning is unnecessary but harmless.

## Decisions taken here, so they are not re-opened

- **`arcadedb` seeds from the library, not from a DAT.** No local source enumerates MAME
  sets today, and the set name is recoverable from the ROM filename. Full coverage would
  need a MAME collection added to `libretro_dats.py`; that is a separate, later choice.
- **`zxinfo` partitions its query rather than paging deeply.** Measured, not assumed.
- **`moby_codes` walks disc platforms first.** The ordering is the deliverable, not a
  detail — it is the difference between one useful day and twenty-five.
