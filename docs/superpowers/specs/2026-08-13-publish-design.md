# Publish — Design

Date: 2026-08-13
Status: draft (design) — not approved, not started

## Problem

ludodex knows what you own, what it is, and what it looks like. It cannot yet **put it
somewhere**.

The pieces exist but do not add up to a feature:

- `device_wants(device_id, norm_key)` is the intent queue, and its own comment says
  "intent only — no transfer". It is keyed by **`norm_key`**, so it cannot express
  "Rayman on PS1 but not Rayman on Saturn" — which is the central case.
- `devicesync.py` can already push one game to ES-DE: resolve ROMs, pick the best
  variant, group multi-disc sets, convert CD images to `.chd` and GameCube/Wii to
  `.rvz`, copy chosen media, and upsert a `gamelist.xml` entry. All of it is
  **hardcoded to ES-DE** — `CATALOG_TO_ESDE`, ES-DE media folder names, ES-DE's XML.
- There is no UI, no bulk selection, no diff, no record of what was placed, and no way
  to answer "what would this do?" before it does it.

So the gap is not "write a file copier". It is: **a second target costs as much as the
first**, and nothing today knows what is already on a device or what ludodex put there.

## Goal / End-state

One **Publish** tab. Pick a target, choose what belongs on it, see exactly what will
change, apply it.

Concretely, publishing to RetroBat on a Windows box should reason like this without
being told:

- Rayman is wanted; the target has the PS1 entry but not the Saturn one → copy Saturn,
  leave PS1 alone.
- The Saturn game is a multi-track `.cue`/`.bin` set → convert to `.chd`, write one
  entry, and an `.m3u` if it is multi-disc.
- This system's emulator cannot read a zipped ROM → unzip on the way in, even though
  the source is zipped and another system's would ship as-is.
- Box art, marquee and a video go to *this frontend's* media folders under *this
  frontend's* filename rules.
- Metadata goes into whatever file this frontend reads — `gamelist.xml` here, something
  else elsewhere.
- The device has 40 GB free and the plan is 62 GB → say so **before** copying anything.
- A game removed from the selection leaves the device — but only files ludodex placed.

Adding Batocera, Recalbox, a Steam Deck folder or a plain directory afterwards should be
a profile, not a rewrite.

## Non-goals (this iteration)

- **BIOS/firmware management.** Frontends need system BIOS; ludodex does not track them.
  Out of scope, and the plan should say so rather than silently produce a library that
  cannot boot.
- **Emulator installation or configuration.** We write games, media and metadata into an
  existing install.
- **Two-way sync.** Publish is one-way. Pulling a device's library into the catalog is
  `devices.sync_device()` and stays separate.
- **Save-game sync.** Different problem, different risk profile.

## Design

### 1. Intent moves to the entry

The one structural change everything else depends on.

```sql
CREATE TABLE publish_intent(
  target_id  INTEGER,
  entry_key  TEXT,      -- (game, platform) — NOT norm_key
  state      TEXT,      -- 'include' | 'exclude'
  source     TEXT,      -- 'manual' | 'rule:<rule_id>'
  added      REAL,
  note       TEXT,
  PRIMARY KEY(target_id, entry_key));
```

`exclude` is a real state, not the absence of `include`. "Everything SNES except these
four" has to survive re-evaluating the rule, and a user's *no* must outrank a rule's
*yes* — the same precedence the match index already uses for overrides.

### 2. Selection is rules plus overrides

"Select the entire library and mark it all" should not mean materialising 33,000 rows
that go stale the moment you ingest more.

```sql
CREATE TABLE publish_rule(
  id INTEGER PRIMARY KEY, target_id INTEGER, enabled INTEGER,
  expr TEXT,          -- the same filter grammar the library grid already uses
  ord INTEGER);
```

A target's set is: **evaluate rules → apply explicit intent on top**. Rules keep the set
live (a newly-ingested SNES game joins automatically); overrides keep it yours.

The UI surfaces this as: filter the library however you like → *Add these to target* →
optionally save as a rule.

### 3. Targets and profiles

```sql
CREATE TABLE publish_target(
  id INTEGER PRIMARY KEY,
  name TEXT, device_id INTEGER, profile TEXT, enabled INTEGER,
  rom_path TEXT, media_path TEXT, meta_path TEXT,
  options_json TEXT);
```

The **profile** is where a frontend's conventions live, declaratively:

```python
{
  "id": "retrobat",
  "systems":  {"sega genesis": "megadrive", "psx": "psx", ...},   # catalog -> target
  "media":    {"cover": ("images", "{base}-image.{ext}"),
               "marquee": ("images", "{base}-marquee.{ext}"),
               "video": ("videos", "{base}.{ext}")},
  "metadata": {"writer": "gamelist_xml", "path": "{system}/gamelist.xml"},
  "archives": {"default": "keep",              # ship .zip as-is
               "psx": "chd", "saturn": "chd",
               "n64": "unzip"},                # this emulator cannot read archives
  "extensions": {"psx": ["chd", "cue", "bin"], ...},
}
```

ES-DE's existing behaviour becomes the `esde` profile with no logic change; RetroBat is
a second dict. The three functions that are ES-DE-shaped today —
`CATALOG_TO_ESDE`, `convert_plan()`, `chosen_media_files()` — take a profile argument
instead of a constant.

**Profiles ship as data, and are user-editable**, because there are more frontends than
we will ever hardcode and someone's Batocera fork will differ by two folder names.

### 4. Plan, then apply — never one call

Publishing writes to someone else's disk. The plan is a first-class artifact, computed
without touching the target beyond reading it:

```
PlanItem = {
  entry_key, title, platform,
  action:  copy | convert | update_media | update_metadata | remove | skip,
  reason:  "not present on target" | "source newer" | "emulator cannot read archives" | …
  source:  [paths],  dest: [paths],
  convert: {from: "cue+bin", to: "chd", tool: "chdman"} | None,
  bytes_in, bytes_out_estimate,
  blockers: ["no ROM file resolved", "chdman not available"],
}
```

The tab shows the plan grouped by action with totals, free space on the target, and
every blocker. **Dry-run is the default and Apply is a separate, explicit act.**

This is also what makes the feature testable: a plan is a pure function of (catalog,
selection, profile, observed target state) and can be asserted against fixtures with no
device present.

### 5. The ledger — what did *we* put there?

```sql
CREATE TABLE publish_state(
  target_id INTEGER, entry_key TEXT,
  dest_path TEXT,           -- the entry file we wrote
  extra_paths TEXT,         -- json: tracks, m3u, media, all of it
  src_sig TEXT,             -- source hash/size+mtime, to detect a changed source
  dest_sig TEXT,            -- what we wrote, to detect someone changing it
  converted TEXT,           -- 'cue->chd' etc, so a re-plan does not redo it
  meta_rev TEXT,
  published_at REAL,
  PRIMARY KEY(target_id, entry_key));
```

Two things this buys, and both are safety rather than speed:

- **A diff against reality**, not against last intent. The device is not ours; someone
  deletes things, an SD card corrupts, another tool writes to the same folder.
- **Removal is only ever of files we placed.** A file on the target with no ledger row
  is *the user's*, and Publish must never delete it. Un-publishing an entry removes its
  ledger paths and nothing else. Anything unledgered is reported as "found on target,
  not managed by ludodex" and left alone.

### 6. Apply is resumable and per-item transactional

Reuse `devices.transfer_run()` for the job/progress plumbing. Each item: stage → verify
→ swap → ledger, so an interrupted run leaves no half-written ROM that the frontend will
happily index and fail to launch. The same `.part`-then-`os.replace` discipline the
match-index download already uses.

Conversion runs **where the tools are**. `chdman` and `dolphin-tool` are not in the
container today — the plan must detect their absence and report it as a blocker rather
than failing halfway through an apply. (Options: add them to the image, or run
conversion on the target device over the existing transport. Decide before building
§6; it changes the transfer shape.)

## Open questions

1. **Where does conversion run?** In-container (add tools, pay image size, pull the
   source over the wire twice) or on the target (needs the tool there, but no double
   transfer). Leaning: in-container, with a per-target override.
2. **Does `publish_intent` replace `device_wants` or sit beside it?** `device_wants`
   is norm_key-keyed and already in use. Leaning: migrate it — expand each row to its
   entry_keys once, then drop it, rather than maintaining two intent tables.
3. **Media kinds per target.** ES-DE and RetroBat want overlapping but not identical
   sets. The profile covers layout; does it also cap *which kinds* get pushed, or is
   that a per-target setting? Leaning: profile declares what it supports, target
   narrows it.
4. **What does "already there" mean for a converted file?** The source is a `.cue`; the
   destination is a `.chd`. Signature comparison has to be source-side, which the
   ledger's `src_sig` handles — but a re-converted file will not be byte-identical, so
   dest verification must be size/existence, not hash.

## Phasing

| phase | delivers | independently useful? |
|---|---|---|
| 1 | `publish_intent` at entry granularity + migration from `device_wants` | yes — fixes the Rayman case in existing sync |
| 2 | Profile abstraction; ES-DE becomes `esde` profile, behaviour unchanged | yes — proves the seam without a new target |
| 3 | Planner + ledger, dry-run only, no writes | yes — "what would this do" is valuable alone |
| 4 | Publish tab: selection, rules, plan review | yes |
| 5 | Apply, resumable, with removal | the feature |
| 6 | RetroBat profile + first real target test | validates 2 |

Phases 1–3 are worth doing even if the tab never ships.

## Test plan

Following the house pattern — properties that are easy to get wrong, not happy paths:

- A plan for a target with **no device reachable** still computes from the ledger, and
  says so.
- An entry present on one platform and absent on another produces **exactly one** copy
  action. (The Rayman case; it is the reason for phase 1.)
- A file on the target with **no ledger row is never deleted**, in any plan, including
  a full un-publish.
- A source whose signature is unchanged produces `skip`, not `update`.
- A system with `archives: unzip` produces a `convert` action for a zipped source and
  `copy` for an unzipped one; a system with `keep` produces `copy` for both.
- A multi-disc CD set produces one entry per disc **plus** an `.m3u`, and the m3u lists
  the converted names, not the source ones.
- A plan larger than free space is **blocked before any write**, not partway through.
- Two profiles asked for the same game produce different destination paths from the
  same catalog row — the seam actually works.
- An apply interrupted mid-item leaves either the old file or the new one, never a
  partial.

## Naming

**Publish**, not Sync or Deploy. "Sync" already means the backing-store two-way mirror
(`dbsync`) and the in-UI library sync; "Devices" is already a Connections subtab.
Publish is unambiguous here and correctly implies one-way, curated, and to an audience.
