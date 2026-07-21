# Match Confidence + Low-Confidence Library Facet — Design

**Date:** 2026-07-21 · **Task:** #13 · **Status:** approved

## Goal

Give every identified game a **match-confidence score (0–100)** = "how sure are we this is
the right IGDB game?" (distinct from the existing Ludodex *quality* score). Surface
low-confidence matches as a **library filter facet** (`confidence:low`), combinable with every
other filter — exactly like the existing `unmatched` facet — plus a dashboard card. No
dedicated queue view. Per-game review actions (Keep / Clear / Let AI match) live in the game
detail, reusing existing identity controls.

## Why materialized (not derived-on-read)

Library filters are SQL clauses over `game-library.sqlite`. The score is computed in Python
(anchor-class string logic + platform comparison) and can't be a WHERE clause. So confidence
is **stored as a `game_attribute`**, recomputed at build/resolution time like `release_type`
and the other derived attributes. This makes it filterable, sortable, and combinable for free.

## Scoring (rule-based base + AI refinement of the gray zone)

### Base (deterministic) — `matchconf.match_confidence(...)`
Pure, testable. Inputs: `matched_by`, `norm_key`, the IGDB record's name(s) + platforms, the
entry platform(s), and the era-bound consoles.

```
source_base = { manual:100, steam_appid:96, era_reheal:88, name:85, ai_name:72, ai_entry:72 }  # else 65
anchor_penalty (skipped for manual/steam_appid — not title-based):
    exact 0 · anchored -12 · interior -50 · norun -45        # via igdb_enrich._name_anchor_class
platform_penalty:
    fits (entry platform in IGDB platforms, or no era-bound console) 0
    no fit, era-plausible -22
    no fit, generation-impossible (platmap.GEN gap) -42
score = clamp(source_base + anchor_penalty + platform_penalty, 0, 100)   # manual forced 100
reason = short human string of the dominant factors
```
Worked examples: `journey`(name,interior,arcade-impossible)=0 · `1943`(name,anchored,fits)=73
(stays above threshold — legit variant kept) · exact+fits=85 · `steam_appid`=96 ·
`ai_name`+interior=22 (low).

### AI layer (hybrid, during wand scans)
For scanned games whose base lands in the **AI band** (default 40–70), the wand scan's existing
AI pass also returns a 0–100 identity confidence + one-line reason (`ai.rate_match_confidence`).
Cached in `metadata-cache.sqlite` `match_confidence_ai(norm_key, igdb_id, score, reason, model,
at)`; **invalidated when igdb_id changes**. The AI score OVERRIDES the base for those games.

## Storage & data flow

- **`build_library`**: for each identified game, compute the base score (it already reads
  igdb_resolution + igdb_meta for game_key; add `matched_by` to that read). If a valid
  `match_confidence_ai` row exists (igdb_id matches), use it instead. Write
  `game_attributes(kind='match_confidence', value=str(score), origin='derived')` and
  `kind='match_reason'`.
- **Wand scan** (`_aimeta_scan`): after resolution, AI-score band games → write
  `match_confidence_ai`; surgically update the stored attribute (mirrors the existing surgical
  attribute writes) so it shows without a full rebuild.

## Config (Settings → new "Matching" section)

- `match_confidence_threshold` (default **60**) — below = low-confidence
- `match_ai_band_lo` / `match_ai_band_hi` (default **40** / **70**) — the AI-rescored band

## Library filter + dashboard

- New query field `confidence` in the QL parser: `confidence:low` → `match_confidence <
  threshold`; `confidence:high` → `>= threshold`; `confidence:<N` / `confidence:>N`. Reads the
  numeric `match_confidence` attribute. Combinable with all other filters.
- Dashboard: **"Low confidence · N · view →"** card mirroring the Unmatched card
  (`server/app.py` stats + `App.tsx` dash-card), linking to the filtered library.

## Frontend

- **Confidence chip** in the game-detail About block + the wand review strip (rides with the
  #6 provenance/release chips), colored by band (green ≥ threshold, amber gray-zone, red low).
- **Low-confidence badge** in the grid, shown when the `confidence:low` filter is active.
- **Detail actions** (reuse existing identity controls): **Keep** → confirm (pin → 100, leaves
  the low set; = a manual pin), **Clear** → unmatch (the scrub path), **Let AI match** → runs
  the AI matcher as a normal wand finding.

## Testing

- `verify_match_confidence.py` — the pure scorer across the rubric cases (journey, 1943, exact,
  steam_appid, ai+interior, platform-impossible, manual-forced-100).
- Live spot-check on the container after each phase; `tsc` for the frontend.

## Phases (each independently shippable)

1. **Scorer + build_library** — `matchconf.py`, store `match_confidence` attribute, test.
2. **Filter + dashboard** — `confidence:` QL field, stats count, dashboard card, grid badge.
3. **Config + Settings UI** — threshold/band knobs.
4. **AI layer** — `ai.rate_match_confidence`, `match_confidence_ai` cache, wand-scan wiring.
5. **Detail chip + actions** — confidence chip + Keep/Clear/Let-AI-match.

## Out of scope

A separate review-queue nav/view (explicitly rejected — it's a library facet); IGDB
`category`/`game_type` fetch.
