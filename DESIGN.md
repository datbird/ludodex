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

*Selection policy (§9) is the one unresolved design decision blocking the push UX.
Everything else above is converged.*
