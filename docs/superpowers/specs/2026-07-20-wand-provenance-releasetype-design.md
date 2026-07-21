# Wand diff: identification provenance + release-type rows — Design

**Date:** 2026-07-20 · **Task:** #6 · **Status:** approved

## Goal

In the wand review overlay (`MetadataReview`), show two read-only signals per game so a
reviewer understands the wand's proposal:

1. **Provenance** — *how* the game is currently identified (`matched_by`): IGDB title match,
   AI match, Steam App ID, era-corrected, set by hand, etc.
2. **Release type** — the `release_type` attribute (Homebrew / Hack / Translation / Prototype /
   Demo / Unlicensed; blank = commercial), surfaced as a chip.

Plus the value the release-type signal exists for: a **mismatch warning** when a
block-type release (Homebrew / Hack / Unlicensed) is being matched to a commercial IGDB title
— "⚠ Homebrew matched to a commercial title — verify".

These are descriptive, not changes to accept/reject → no checkboxes. They render in the
existing factual strip, consistent with the "facts on this ROM, not the AI's guess" ethos.

## Data (all already exists)

- **Provenance:** `igdb_resolution.matched_by` (metadata-cache.sqlite), keyed by `norm_key`.
  Values: `name`, `ai_name`, `steam_appid`, `era_reheal`, `era_reject`, `manual`, `none`.
- **Release type:** `game_attributes(kind='release_type')` (game-library.sqlite) via
  `homebrew.py` classification. Read distinct value(s) for the norm_key's entries.
- **Block set:** `build_library.BLOCK_RELEASE_TYPES = {"Homebrew","Hack","Unlicensed"}` — drives
  the mismatch warning. Mirrored server-side into `context.release_block: bool` (single source
  of truth; the frontend does not re-hardcode the set).

## Changes

### Server — `server/app.py`
- New helper `_identity_provenance(nk)` → `{provenance, release_type, release_block}`:
  - `provenance` = `igdb_resolution.matched_by` (or `None`)
  - `release_type` = first non-null `game_attributes` `release_type` value for the nk (or `None`)
  - `release_block` = `release_type in BLOCK_RELEASE_TYPES`
- In `aimeta_findings()`, merge the three keys into the per-nk `context` dict (only where
  context is already built, `len(findings) <= 60`; bounded + cached like the rest).
- Import `BLOCK_RELEASE_TYPES` from `build_library` (or re-declare a tiny shared constant).

### TS — `web/src/api.ts`
- Extend `FindingContext` with `provenance?: string | null`, `release_type?: string | null`,
  `release_block?: boolean`.

### Frontend — `web/src/App.tsx`
- `PROVENANCE_LABEL` map: `matched_by` code → `{icon,label}` (name→🔗 IGDB title match,
  ai_name→🤖 AI match, steam_appid→🎯 Steam App ID, era_reheal→🕓 Era-corrected, manual→✋ Set by
  hand, none→❔ Unmatched).
- `FindingContextStrip`: append a provenance chip (`fc-chip fc-prov`) and, when
  `release_type` is set, a release chip (`fc-chip fc-release`, `.fc-release-block` styling when
  `release_block`).
- Group scaffolding (after the "wrong match" warning, `App.tsx:~8437`): a `chg-warn`
  mismatch callout shown when `ctx.release_block && hasIgdbMatch(f.payload)`.
- Minimal CSS for `.fc-prov` / `.fc-release` / `.fc-release-block`.

## Testing

- `verify_wand_provenance.py` — unit test for `_identity_provenance` mapping (fixture DBs:
  a name-matched commercial game, a manual pin, a Homebrew ROM) asserting the three-field
  output incl. `release_block`.
- Frontend: `tsc` build passes; visual check post-deploy.

## Out of scope

IGDB `category`/`game_type` fetch (would need `GAME_FIELDS` + enrich changes) — not needed;
`release_type` already covers the Homebrew signal.
