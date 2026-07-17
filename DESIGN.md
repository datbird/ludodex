# ludodex — Design & Roadmap

The canonical spec. The CLI pipeline + media layer + frontend adapters are **built**
(see `README.md`); the **Device layer** below is **designed, not yet built**. This
doc is the editable source of truth we sharpen against.

---

## 1. Vision

ludodex is a single source of truth for *what games you own, where they are, and the
metadata/media that describe them* — and a broker that pushes curated slices of that
truth out to the places you actually play.

Two ways it gets used:

1. **Source of truth.** ludodex is authoritative. It pushes selected games +
   metadata/media to named **Devices**, each rendered into that device's native
   ecosystem layout.
2. **Bridge between ecosystems.** A user's real hub may be Playnite/LaunchBox on a
   gaming PC; ludodex brokers between that hub and other devices (a basement arcade
   cabinet, a Steam Deck running RetroDECK/EmuDeck, etc.). *(Bidirectional/"hub
   elsewhere" mode is **Someday** — see §11.)*

A possible end-game is a **multi-user client app** that fills the Playnite/LaunchBox
*UI* role on top of the ludodex API. That client is a **consumer of the API** — it
does not change what the backend is.

---

## 2. Boundary — what ludodex IS / IS NOT

```
  ✅ ludodex IS                              ❌ ludodex IS NOT
  ───────────────────────────────────────   ─────────────────────────────────────────
  the canonical catalog of what you own,     a game launcher or runtime
   where, + its metadata/media                (the device's ecosystem plays games)

  a router that pushes curated subsets to    an installer. It DETECTS install state
   devices in each device's native layout     (read-only) and lets you PIN an override,
                                               but NEVER performs a store/disc install

  an orchestrator of delivery actions        a store/DRM client replacement
   (copy ROM · write media · register a       (it observes those stores; it doesn't
    shortcut · render frontend metadata)        re-implement their installs)

  a multi-user system of record: every       a new frontend UI
   fact has provenance + history              (ES-DE/Playnite/LaunchBox stay the UI;
                                               a future ludodex client is a separate app)

  the authority in push mode                 a general file-sync tool
                                              (it moves game payloads + media, not
                                               arbitrary files)
```

**One-line scope:** ludodex ships ROMs + media + frontend metadata, keeps a ledger of
what's installed where (detected / pinned / hand-added), and never installs
store/disc games for you.

---

## 3. Architecture — the layer cake

```
              ┌─────────────────────────────────────────────────────┐
  INGEST      │ SOURCES     owned games: steam·gog·epic·itch·ea·emu  │  BUILT
  (read in)   │ FRONTENDS   playnite·launchbox  (read as meta-layers)│  BUILT
              │ PROVIDERS   igdb·screenscraper · esde/steam/sgdb art │  BUILT
              └────────────────────────┬────────────────────────────┘
                                       │  dedupe on norm_key
                                       ▼
              ┌─────────────────────────────────────────────────────┐
  CORE        │        CANONICAL CATALOG   ← source of truth         │  BUILT
  (own it)    │   games · metadata · chosen-media repo (<sha1>)      │
              └────────────────────────┬────────────────────────────┘
                                       │  select → render → ship
                                       ▼
              ┌─────────────────────────────────────────────────────┐
  DELIVER     │   DEVICES   named push targets, each hosting         │  DESIGN
  (write out) │   one or more ECOSYSTEM CHANNELS (account-aware)     │  (this doc)
              └─────────────────────────────────────────────────────┘
```

The matching key throughout is **`norm_key`** (stable across rebuilds); game_id is
reassigned every build, so media and all device-layer records key on `norm_key`.

---

## 4. Devices & Accounts

### Account (first-class identity)
A store/ecosystem identity that **owns** games. There can be **many per ecosystem**
(multiple Steam accounts, multiple EA accounts, the household GOG, etc.).

```
ACCOUNT
├─ ecosystem   steam | ea | gog | epic | itch | psn | xbox | …
├─ label       "Bob's main" · "Bob's alt" · "household EA"
├─ identity    steamid / email / gamertag / …
└─ creds       config.sqlite value (each account its own creds; env var > config)
        └─ owns ──► games        (detection runs PER ACCOUNT)
```

Ownership is therefore *"account X owns game G"*, not just *"steam owns G"*.

### Device (a push target)
```
DEVICE
├─ name        "Steam Deck" · "Basement Arcade" · "Gaming PC"
├─ kind        handheld | desktop | arcade-cabinet | server
├─ transport   how to reach it: local | ssh/rsync | smb/nfs mount   (creds in config)
├─ selection   which games go here  (see §9 — OPEN)
└─ channels[]  the ecosystems this device hosts (each account-aware)
```

Both Device and Account are **registries** in config, following the existing
`media_mounts` / `archives` pattern (SQLite table + add/list/enable CLI).

---

## 5. Channels & capabilities

A **channel** is one ecosystem a device hosts, optionally bound to an account. A
device "serving multiple ecosystems" simply has multiple channels. Each channel
declares capabilities (any combination):

```
  capability   meaning
  ──────────   ───────────────────────────────────────────────
  PUSH         ludodex WRITES content to the device
  DETECT       ludodex READS the device to discover install state (read-only)
  LEDGER       state is set by hand (no automation)

  CHANNEL            push?   detect?   ledger?   notes
  ────────────────   ─────   ───────   ───────   ─────────────────────────────────
  retrodeck/emudeck   ✅       ✅        –        push ROM+media+gamelist · detect ROMs
  steam               (art)    ✅        –        detect appmanifests · push shortcuts+grid
  playnite/launchbox  ✅       –         –        render+ship metadata/media (built adapters)
  manual              –        –         ✅        hand-entered install state
```

Render and ship are **orthogonal**: a channel writer produces the correct on-disk
layout locally (the "render"); the device's **transport** delivers it (the "ship").
The same ES-DE writer output goes to a local path, an ssh target, or a mounted share
unchanged.

---

## 6. The install ledger

The heart of the device layer. Every `(game × device × channel)` resolves to one
**effective** install state, plus whether physical media is required.

```
   EFFECTIVE STATE  =  PIN  (if set)   else   DETECTED   else   UNKNOWN
                       └─ human ─┘             └─ machine ─┘

   ① PIN        user override — WINS, sticky, survives every probe, clearable
   ② DETECTED   default source of truth — refreshed each probe (a push sets it too)
   ③ UNKNOWN    never probed / can't probe
```

The **pin overrides in both directions**:
- detection can't see it but it IS there → `pin = installed` (your hand-ripped 1999 CD)
- detection says installed but it's broken → `pin = not-installed`

```
INSTALL RECORD   game × device × channel
├─ detected     installed | not-found | unknown    ← machine: probe or push
├─ pin          installed | not-installed | (none)  ← human override, sticky + clearable
├─ effective    = pin ?? detected ?? unknown        ← what everything downstream reads
├─ physical     true | false                         ← needs media inserted to play
└─ provenance   detected_at · pinned_reason
```

`physical` is real signal: it separates "playable right now" from "playable only if I
dig out the disc/cart." Useful query: *"what can I play on the Deck without hunting
for media?"*

---

## 7. Universal provenance — `origin`

Any game/fact can be added to **any channel** by hand — even Steam (let users assert
ownership the store doesn't show). So every assertion carries one omnipresent
attribute:

```
   origin = detected | manual          (on EVERY fact)

   FACT                       detected means…            manual means…
   ────────────────────────   ────────────────────────   ──────────────────────
   owns game on a channel      probe found it              you added it by hand
   install state (the pin)     probe saw it                you pinned it
   (later) a metadata value    pulled from a provider      you typed it
```

The "manual ledger," the install "pin," and "manually-added ownership" are **the same
mechanism** — `origin = manual` on different facts. One concept, not three.

---

## 8. History & conflicts

### Changelog (append-only audit)
Every mutation is logged and **actor-attributed** (multi-user ready):

```
CHANGELOG
├─ when      timestamp
├─ actor     a user, or "system" for detection
├─ device    (nullable — some changes aren't device-scoped)
├─ entity    install_state / ownership / pin / conflict / metadata
├─ change    field: old → new
└─ origin    detected | manual

   Query: "last N changes on the Steam Deck, and who made each."
```

### Conflict awareness
A conflict requires a **positive contradiction**, never an absence — this is the rule
that keeps the surface quiet:

```
   CONFLICT = a MANUAL assertion ⊕ a DETECTED result that positively disagrees

   asserted (manual)   detected               conflict?
   ─────────────────   ────────────────────   ─────────
   owns it             not-found              ✅ real contradiction
   pinned installed    probe: not installed   ✅
   owns it             UNKNOWN / can't probe   ❌ absence ≠ disagreement
   owns it             detected: owns it       ❌ they agree
```

The pin still wins the *value* throughout; the conflict only raises *awareness* that
reality diverged. Lifecycle:

```
        detection positively disagrees
                     │
                     ▼
   ┌───────────┐  accept   ┌──────────────────┐
   │   OPEN    │ ────────► │  ACKNOWLEDGED    │  "yup, this is how I want it"
   │ (alerts)  │ ◄──────── │  (hidden, stored)│
   └─────┬─────┘ un-accept └────────┬─────────┘
         │ now agrees               │ NEW disagreement (different signature)
         ▼                          ▼
   ┌───────────┐                back to OPEN
   │ RESOLVED  │ (auto-clears)
   └───────────┘
```

The acknowledgment is **keyed to the conflict signature**
`(game, device, channel, asserted-value, detected-value)`:
- same disagreement re-detected → stays silent (won't nag again)
- detection flips to a *different* value → new signature → re-opens (you'd want to know)
- detection comes into agreement → auto-resolves

```
CONFLICT   game × device × channel
├─ asserted    value · origin=manual · actor
├─ detected    value · detected_at
├─ signature   hash(game,device,channel,asserted,detected)   ← dedupe + ack matching
├─ status      open | acknowledged | resolved
└─ ack         actor · ts · note      (also a changelog entry)
```

- Main surface shows `status = open` only, deduped to one row per genuine divergence.
- Acknowledged conflicts are viewable; un-acknowledging flips back to `open`.
- **Default:** an explicit pin that already contradicts current detection
  auto-acknowledges (the user obviously knows); it re-opens only if detection later
  *shifts*.

---

## 9. Selection policy — THE OPEN QUESTION

Which games get pushed to a device. Candidates, least→most automatic control:

- **per-device allowlist** — explicit; most control, most effort.
- **by collection / tag** — curate sets ("Deck favorites"), assign to devices.
- **by platform** — e.g. all SNES → the arcade cabinet.
- **"everything this device's ecosystems can play"** — most automatic.

This choice shapes the UX more than anything else and is **not yet decided.**

---

## 10. Data model (assembled)

```
ACCOUNT      ecosystem · label · identity · creds                 — owns games (per account)
DEVICE       name · kind · transport · creds · selection · channels[]
CHANNEL      device · ecosystem · account? · capabilities · destination · opts
OWNERSHIP    game × (account | channel) · origin(detected|manual) · actor · ts
INSTALL      game × device × channel · detected · pin · effective · physical · provenance
CONFLICT     game × device × channel · asserted · detected · signature · status · ack
CHANGELOG    ts · actor · device? · entity · field old→new · origin     — append-only
```

Two axes run through all of it: **`origin`** (detected vs manual) and **`actor`**
(who) — exactly what a multi-user client needs.

---

## 11. Per-platform library entries — the ports model

**Decision (2026-07-15).** The library unit is **one entry per `(game, platform)`**, not
one entry per game. Bubsy on Genesis, SNES, Game Boy, TurboGrafx and PC is **five
entries**, cross-linked. This is the KISS fix for platform-blind media (a TurboGrafx
entry can only ever hold TurboGrafx art) and matches what collectors expect — each
platform is its own thing.

### 11.1 Identity — platform is the axis, source and OS are not

Three axes, only one is identity:

| Axis | Field | Role | Cardinality per entry |
|------|-------|------|-----------------------|
| **Platform** | `sources.platform` (derived) | **identity** — one entry per value | 1 |
| **Source** | `sources.source` | provenance ("owned via") | many |
| **OS** | `os.sqlite` (win/mac/linux) | support metadata | many, attribute only |

- Two sources on the **same** platform **dedupe** into one entry (Steam **and** GOG of
  the same PC game → one `pc` entry with two source rows).
- OS never splits an entry (a PC game on Windows+Linux is still one `pc` entry).

### 11.2 Platform derivation — inherent to the source

The store *is* the platform; only Xbox needed a judgment call. `build_library.add()`
derives the entry platform (replacing the old `else source` default):

| source | → entry platform |
|--------|------------------|
| steam, gog, epic, itch, ea | **`pc`** |
| xbox | **`xbox`** (default) or `pc` — per the `xbox_platform` setting |
| psn | as emitted (`ps4` / `ps5`) |
| emulation / archive | as emitted (per console) |

`PC_STORES = {steam, gog, epic, itch, ea}` → always `pc`. Nintendo-account source is
**removed** (§11.6); Switch ownership comes via manual per-platform ownership.

### 11.3 Entry key & cross-reference

`norm_key` splits into two roles:

- **`base_key`** — the old title `norm_key`. Retained on every entry. **Groups
  ports:** "also owned on" = *other entries sharing `base_key`*.
- **`entry_key = f"{base_key}@{platform}"`** — the per-entry identity used everywhere a
  single opaque id is passed (API routes, media/ownership/pin/framing keys). Split on
  the **last** `@` (base_key is normalised without `@`; platform has no `@`).

**Shared vs per-entry.** Title-level metadata (IGDB description/genre/attributes) is the
same across platforms → keyed by **`base_key`** and fanned across its entries at merge
time. Ownership, installs, pins, framing and **media choice** are **per-entry**
(platform-specific).

### 11.4 Media siloing (fixes the wrong-cover bug)

`media` already carries `system`. An entry's media = `media WHERE norm_key=base_key AND
system ⇔ platform`; the chooser/`_repick` key on **`(base_key, system, kind)`** and the
`/api/media/{entry_key}/{kind}` resolver serves that entry's system only, falling back
to platform-neutral store art (`system IS NULL`) when a console has none.

### 11.5 Xbox platform setting

Config **`xbox_platform`** = `xbox` (default) | `pc`, surfaced at **Settings → Stores →
Xbox**. It only sets the **bulk-inbound** bucket for the Xbox sync. Independent of it,
**manual per-platform ownership can mark a game owned on Xbox AND PC** (or either) — the
setting is the default, manual is unconstrained.

### 11.6 Migration & consequences

- `build_library` regenerates the catalog from `sources` every run → the split happens
  on the next rebuild; no catalog data migration. **`ownership.sqlite` re-keys** from
  `norm_key` → `(base_key, platform)` (one-time).
- **Game count rises** (multi-platform titles multiply; ROM-count grouping shifts) —
  expected, not a bug.
- By this rule a game owned on **PS4 and PS5 becomes two entries** (same logic as PC,
  applied to console generations). Foldable later via a one-row change to §11.2 if
  desired.
- The **Uno/Bubsy era-split** logic (peeling platforms *within* an entry) largely
  retires — platforms are separate entries now; it degrades to optional cross-ref
  hygiene.

### 11.7 Implementation surface

`build_library.py` (derivation, entry key, write-out, `has_*`/summary per entry, attr
fan-out) → `media_choose.py` + media resolver (key on system) → `server/app.py` (queries
+ routes on `entry_key`, "also owned on" grouped by `base_key`) → `ownership.sqlite`
schema → exporters / PocketBase sync (`entry_key`) → UI (Xbox setting, "also owned on"
strip). Verified end-to-end via a rebuild against real data.

### 11.8 Rebuild concurrency — build-to-temp, atomic-swap (invariant)

`build_library` is a full catalog regeneration and runs while the server keeps serving
reads. **It must never write `game-library.sqlite` in place.** Doing so held the db locked
for the entire rebuild (~10 min on the array) and every concurrent `/api` read threw
`database is locked`. The rule: **build into `game-library.sqlite.building`, then
`os.replace()` it in atomically at the very end** — readers always see a complete catalog
(the old one until the swap, the new one after), and a crashed rebuild leaves the old
catalog intact. Any large SQLite writer added here follows the same pattern. Server read
connections (`ro()`) also carry `busy_timeout` so a brief lock from another pipeline
writer (scores / media backfill) waits rather than erroring.

### 11.9 Media identity binding — the durable exit from the read-time heuristic stack

**The soft spot.** §11.4 silos media by `(norm_key, system)`; §11.6 peels era/handheld-
mismatched entries off the shared title. Both are *read-time heuristics* compensating for
one root fact: **media is keyed by title, not by resolved game identity.** Concretely —
`igdb_resolution` is `norm_key PRIMARY KEY → igdb_id` (exactly one resolved game per
title-string), and the `media` table carries only `(norm_key, system, kind, …)` with **no
identity column**. So whenever a title-string maps to more than one real game — an *era
collision* like Alice/Apple II (1985) sharing `norm_key` with the 2010 NDS movie game, or
Portal/Amiga vs. Valve's Portal — the serve layer has to *guess* which cover belongs to
which entry from platform + era alone. Each new collision shape has needed another rule:
system-silo → forfeit-neutral-on-separation → era-impossible vs. retro-handheld-stray →
the `\x1f`/`\x1e` base_key markers (§11.6). The stack is correct but it **grows per new
shape** — that is the ~20% of foundation that is heuristic rather than structural. This
section is the bounded exit that takes it to 95%+.

**Why title-keying exists (don't just delete it).** ~45% of the catalog is *unidentified*
(ROMs matched only by name+system, no IGDB id). The pipeline needs **one universal key**
that exists for identified and unidentified games alike, and title-norm is the only thing
both have. Title-keying is therefore *forced by the unidentified tail*, not a shortcut. The
fix is a hybrid, not a rewrite.

**Root change — a `game_key` per resolved identity, stamped on entries AND media.** Give
every catalog entry and every media row a stable identity string drawn from two
namespaces:

- **Identified:** `game_key = "igdb:<id>"` (extensible: `"ss:<id>"`, etc. — provider-
  qualified so two providers never collide).
- **Unidentified:** `game_key = "title:<norm_key>@<system>"` — the current title+system
  bucket, now explicitly namespaced so it can **never** match an identified key.

Identity is assigned **per entry** in `build_library`, reusing the era classification that
§11.6 already computes — only its *output* moves from a base_key marker to a `game_key`:

| Entry class (per platform)                         | `game_key`                    | Gets identity's art + metadata? |
|----------------------------------------------------|-------------------------------|---------------------------------|
| Identified, era-compatible                         | `igdb:<id>`                   | yes                             |
| Stray retro-handheld **port** of an identified game (today's `\x1e`) | `igdb:<id>` (adopts parent identity) | yes — same game, so it *should* |
| Era-impossible collision (today's `\x1f`)          | `title:<nk>@<platform>`       | no — a distinct identity        |
| Unidentified                                        | `title:<nk>@<system>`         | n/a (title bucket)              |

Media rows are stamped at **fetch time**, where the target is already known: `media_fetch`
already loads `{norm_key → igdb_id}` (it uses it to pull IGDB art by id) — so IGDB/store
art → `igdb:<id>`; ScreenScraper art for an identified game → `igdb:<id>` with its
`system` set (console-specific box art *within* that identity); art for an unidentified
game → `title:<nk>@<system>`. `media_choose.select()` picks `chosen` per **`(game_key,
system, kind)`** instead of `(norm_key, system, kind)`.

**Serve becomes a trivial match — the whole heuristic stack retires.** An entry serves
`media WHERE media.game_key = entry.game_key`, preferring `system = platform` (console-
specific) over `system = ''` (neutral) over nothing, then `chosen`. That's it. The four
gate sites in `server/app.py` (list `cover_v`/`has_cover`, detail `media_kinds`, the
`/api/media` serve resolver) collapse to that one predicate. Gone: the neutral-forfeit
gate, the `base_key<>norm_key` checks, the `instr(base_key, char(31))` marker tests, and
the era-vs-stray split *for art*. **Cross-identity contamination becomes structurally
impossible** — a cover literally cannot be served to a non-matching `game_key`, so Alice
can't borrow the NDS cover no matter what new same-title console shows up. New collision
shapes need **zero** new serve rules; they only need correct `game_key` assignment, which
happens in exactly one place.

**Bonus — metadata unifies with art.** Today §11.6 excludes *all* separated entries from
the metadata fan-out (`base_to_gids` is keyed by `bkey`, so a marker-bearing entry gets no
IGDB link, score, or title). Under `game_key`, the fan-out keys on identity: a **stray
port now correctly inherits its parent game's score/description** (it *is* that game),
while an **era-collision stays bare** (distinct identity, no link) — one key drives both
metadata and media, so "adopt identity" and "own identity" are the only two states and
they behave consistently. `base_key` is then freed to mean *only* cross-ref "also owned on"
grouping (§11.3), which is orthogonal display grouping.

**What this deliberately does NOT solve (the honest remaining ~5%).**
- **One identified game per title.** `igdb_resolution` stays `norm_key`-keyed, so a title
  that is genuinely *two different identified games* on two platforms still gets a single
  resolution; `build_library` can only *adopt or reject* it per entry, not assign a second
  identity. Rare, and a later change (resolution keyed by `entry_key`) slots in without
  disturbing the `game_key` serve model.
- **The unidentified tail is still title+system.** For the ~45% with no id, `game_key`
  falls back to `title:<nk>@<system>` — the same siloing as today. The win is *namespace
  isolation*: an unidentified ROM can no longer contaminate a **known** game's art, even
  when they share a title. Within the unidentified tail, same-title/same-system collisions
  remain possible (no identity exists to separate them) — inherent to being unidentified.

**Migration (non-destructive, re-derives on rebuild).**
1. `media` gains a `game_key TEXT` column + index `(game_key, system, kind)`; a one-time
   backfill stamps existing rows from `{norm_key → igdb_id}` (identified) else
   `title:<nk>@<system>`. Old rows without it fall back to the title bucket — safe.
2. `build_library` writes `game_key` per entry (the table above) alongside `entry_key` /
   `base_key`; regenerates every rebuild, so no catalog data migration (per §11.6/§11.8).
3. `media_fetch` / `media_choose` stamp + key on `game_key`.
4. Serve sites swap to the single predicate; markers/forfeit logic deleted.
5. Ships behind the same build-to-temp atomic swap (§11.8); verified end-to-end against
   real data (Alice stays bare, Smash T.V./Game Gear + KOF 2001/NGPC show covers *and*
   inherit metadata, BG3 multi-platform art unchanged).

**Implementation surface.** `igdb_enrich.py` (unchanged — resolution stays per-title) →
`build_library.py` (per-entry `game_key` derivation, replacing the `\x1f`/`\x1e` marker
branch; metadata fan-out keyed on `game_key`) → `media_fetch.py` (stamp `game_key` at put
time — it already has `{norm_key→igdb_id}`) → `media_choose.py` (`select`/`_repick` key on
`(game_key, system, kind)`) → `server/app.py` (four gate sites collapse to one `game_key`
predicate; `base_key` retained only for "also owned on").

**Rollout — three shippable, reversible phases:**
- **(1) DONE** — add `media.game_key` + `ix_media_gk`, one-time backfill, stamp on fetch.
  No read-path change → invisible. (117,939 `igdb:*` / 11,036 `title:*` / 0 NULL on live.)
- **(2) DONE** — `build_library` writes `games.game_key` per entry (identified & stray
  ports adopt `igdb:<id>`; era-collision & unidentified take `title:<nk>@<platform>`).
  Still no consumer → invisible; validated against real entries before Phase 3.
- **(3) TODO** — flip `media_choose` **and** the four serve sites to `game_key` **together**,
  then delete the marker / neutral-forfeit code. *`media_choose` moved here from Phase 2
  on a coupling found in implementation:* ~309 of ~15,700 igdb identities span **two
  norm_keys** (title variants — "…link's awakening dx" vs "…the link's awakening dx"). If
  `chosen` were keyed on `game_key` while serve still queried `chosen` by `norm_key`, the
  losing variant would find no chosen asset and blank. Keying `chosen` and the serve query
  on `game_key` in the *same* phase closes that window (and actually *unifies* those
  variants' art — a Phase-3 win, not just a hazard).

---

## 12. Roadmap / docket

### ✅ Built (foundation)
- Canonical deduped catalog (`norm_key`); sources steam·gog·epic·itch·**ea**·emulation.
- Frontends as meta-layers, **both ways incl. media**: Playnite, LaunchBox.
- Metadata: IGDB (live); ScreenScraper (code done, blocked on devid).
- Media layer: index → choose → materialize, hybrid (reference + content-addressed
  repo), reference/symlink mode for shared storage.
- Remote sync: PocketBase / Firestore.

### 🔜 Build now
**Finish in flight:**
- **ScreenScraper integration (metadata + media)** — code complete (`screenscraper.py`,
  `ss_scrape.py`, wired into `build_library.py` + `update.sh`, tier/quota-aware,
  resumable). **BLOCKED** on the developer `devid`/`devpassword` (forum request
  pending). The moment it arrives: set the creds, `config.py enable screenscraper`,
  run a scrape pass, validate metadata+media merge, done.

**Device layer v1 (push-only, no install-triggering):**
1. **This `DESIGN.md`** as the spec. ✅
2. **Account registry** — multi-account per ecosystem (config table + CLI).
3. **Device registry** — name/kind/transport/creds/channels (config table + CLI).
4. **Install ledger** schema — detected/pin/effective/physical + `origin` + `actor`.
5. **Changelog** table — append-only, actor-attributed; "last N per device" query.
6. **Conflict** model — open/acknowledged/resolved, signature dedupe, auto-ack rule.
7. **First push channel: RetroDECK/ES-DE writer** — ROM copy + media + gamelist; local
   transport; fully testable on this Deck.
8. **First detect probe** — Steam `appmanifest` read and/or RetroDECK ROM presence.
9. **Decide selection policy** (§9) — gates the push UX.

### 🔭 Next (after v1)
- More push channels: **Steam shortcuts.vdf + grid art** (non-Steam games on the Deck).
- More detect probes per account/channel; reconcile loop.
- Remote transports (ssh/rsync to the arcade cabinet; network/Unraid shares).
- **AI-forward server (task #6):** FastAPI exposing catalog + ledger + conflicts +
  media serving (materialize-on-serve); React/Vite UI; AI (NL search, art/dedupe assist).

### 🗄️ Someday (reserved — maybe never)
- **Install-triggering** (legendary / store installs). **Explicitly out of scope** for
  now; ludodex detects + pins but never installs.
- **Bidirectional / "hub-elsewhere" sync** (scenario #2) with per-device authority +
  conflict-resolution *direction* (who wins when both sides changed).
- **Multi-user client app** that fills the Playnite/LaunchBox UI role over the API
  (auth, per-user actor, sharing).
- **New ownership sources (account-aware):**
  - **Sony PlayStation** — PSN ownership on **console** (PS4/PS5); PlayStation **PC**
    titles currently ship via Steam/Epic, so capture PSN as a console source and PC
    ports through their partner store.
  - **Microsoft Xbox / Microsoft Store** — both **PC** (Xbox app / Microsoft Store /
    Game Pass) and **console** (Xbox Live entitlements).
  - Others as they come up: Ubisoft Connect, Battle.net, Amazon Games.
- **EmuDeck-specific channel** (today the generic path-configurable ES-DE writer covers
  it; revisit if its layout diverges).
- Deeper artwork/media discussion (SteamGridDB name-search for weak emulation
  backgrounds; commercial-art licensing if ludodex ever goes commercial).

---

## 13. Collections & compilations — ownership fan-out

A **compilation** (a single owned product that bundles multiple otherwise-standalone
games — *Sega Genesis Classics*, *Sonic Mega Collection*, *Mega Man Legacy Collection*,
*Castlevania Anniversary Collection*) is neither one game nor N owned games. Modeling it
as a normal entry credits nothing to the games inside; marking each member "owned" would
be wrong (they were never separately purchased). The model: **the collection stays its own
catalog entry, but its membership is recorded and ownership is *fanned out* (credited) to
each member game.** This is a *form of ownership*, so it lives as provenance on the
member's "In your library" rows — not as a phantom entry.

### 13.1 Data model — `collections.sqlite` (durable, survives rebuilds like ownership/tags)
```sql
collections(
  coll_key TEXT PRIMARY KEY,  -- the collection's OWN catalog entry (its norm_key/base_key)
  name     TEXT,              -- display name, e.g. "Sega Genesis Classics"
  origin   TEXT,              -- 'ai' | 'provider' | 'manual'
  updated  REAL)
collection_members(
  coll_key TEXT,              -- FK -> collections.coll_key
  member_key   TEXT,          -- norm(member title) = the member game's base_key
  member_title TEXT,          -- as named by the AI/provider
  member_platform TEXT,       -- platform the collection provides it on (usually the coll's)
  member_year  INTEGER,
  origin   TEXT,              -- 'ai' | 'provider' | 'manual'
  added    REAL,
  PRIMARY KEY(coll_key, member_key))
```
`member_key` is the **normalized title** (same `titlenorm.norm` as the catalog), so a member
row links to a standalone member entry by `base_key` without needing an id — robust across
rebuilds. A collection is **owned** when its own `coll_key` entry has an owned source.

### 13.2 Phase 1 — detect + provenance
- **AI detection.** The `metadata` area result gains an optional block:
  `"collection": {"is_collection": bool, "name": str, "members": [{"title","platform","year"}]}`.
  Prompt: *a COMPILATION bundling multiple standalone games → is_collection=true, name it,
  list the standalone games (title + original platform + year). A single game with
  DLC/editions/"Anniversary" of ONE game is NOT a collection.* Emitted as a finding kind
  **`collection`** (`aimeta.store_finding`); on accept it writes `collections.sqlite`.
- **Manual path.** Endpoints to mark an entry `is_collection` and add/edit members, so a
  human can curate what the AI missed.
- **"In your library" gains a `Collection` column** — `—` for a standalone copy, the
  collection's name for a fanned-out row; that row's `Listed as` shows the collection name
  (how the store actually lists it), not the member's own title.

### 13.3 Phase 2 — fan-out, cross-credit, want-satisfaction
- **Credit.** In `game_detail(G)` (base_key `BK`): find collections `C` where a
  `collection_members` row has `member_key = BK` **and** `C.coll_key` is owned. For each,
  emit a synthetic ownership row `{source: <C's store>, platform: <C's platform>,
  listed_as: <C.name>, collection: <C.name>, state: 'have', via_collection: C.coll_key}`
  and add `C`'s platform to `also_owned_on`. So owning *Sega Genesis Classics* (PC) makes
  standalone Genesis Sonic read **"also owned on: PC (via Sega Genesis Classics)."**
- **Reverse lookup (title → collections)** — the second AI cross-check: "what compilations
  contain this title, and does the user own one?" Owned ⇒ the credit above; not-owned ⇒ an
  optional *"available in <collection>"* hint that feeds Discover / want-vs-have.
- **Want-satisfaction.** A want for `G` is satisfied when `G` is owned via a collection —
  the derived "owned" state (and Discover) treats a collection credit as ownership.

### 13.4 What a collection is NOT (guardrails)
A collection is a bundle of **separately-recognizable games**. NOT: a game + its DLC/season
pass; an "Anniversary/Definitive/HD" edition of ONE game (that's the era/edition logic in
§11); a franchise/series grouping (that's IGDB `collection` = series, unrelated). The AI is
told this explicitly, and `member_key`s that collapse to the collection's own key are
dropped (a compilation never "contains itself").

### 13.5 Implementation surface
`compilations.py` (durable store + normalize-on-write; named to avoid shadowing the
stdlib `collections` package, since the repo dir is on `sys.path`) → `server/ai.py` metadata prompt +
`analyze_game` result (`collection` block) → `aimeta.store_finding`/apply (kind
`collection`) → `server/app.py` `game_detail` (fan-out synthetic rows + `also_owned_on`
credit) + collection CRUD routes → `web/src/api.ts` (`GameDetail.sources[].collection`,
`also_owned_on[].via`) → UI (`Collection` column, "via" chip). Durable store is preserved
across catalog rebuilds; credit is computed at read time so no rebuild is needed to reflect
a newly-recorded collection.

*Selection policy (§9) is the one unresolved design decision blocking the push UX.
Everything else above is converged.*
