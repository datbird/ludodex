# Per-Entry Game Identity — Implementation Plan (task #8)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Resolve each `(entry, platform)` to its own correct IGDB identity — unify same-game-across-platforms, separate/re-identify different same-title games (PS3 reboot, NDS movie game), detach the impossible (2600 Star Fox), without over-separating legit ports.

**Architecture:** Extend the era-aware matcher to a per-entry `fits` selector (deterministic, using each IGDB candidate's platforms + per-platform release years), add an AI adjudication for the ambiguous cases behind two confidence gates, apply results per-entry via `entry_res`, and run it inside the wand scan on freshly-proposed matches. Fold the old backport-only contamination into this. See spec `docs/superpowers/specs/2026-07-20-per-entry-identity-design.md`.

**Tech Stack:** Python 3.12 / FastAPI / SQLite; IGDB (`igdb.GAME_FIELDS` already fetches `platforms.*` + `release_dates`); reuses `platmap`, `console_eras`, `entry_res`, `ai.detect_contamination`.

## Global Constraints

- Deterministic-first: AI only for genuinely ambiguous entries (0 or >1 candidates fit).
- Two gates: (A) separate/re-identify ONLY when confident an entry is a *different* game; when uncertain, keep it with the group (no over-separation). (B) confident correct id → set it; confident-different-but-unknown → detach.
- Never override a manual pin/detach (`matched_by` in {manual, detached-by-user}).
- No schema change. Per-entry ids/detaches via `entry_res`; `build_library._game_key` already honors them.
- AI verdicts act only at `confidence >= 0.75` (reuse the contamination threshold; tunable — task #3).

## Phases

- **Phase 1 (this plan, detailed): deterministic per-entry resolver.** A pure `igdb_enrich.per_entry_resolve(candidates, platform)` + `entry_fits(candidate, platform)` returning which candidate fits one platform (by platform membership + per-platform era), and a classification (`unique` / `ambiguous` / `none-impossible` / `none-compatible`). Fully unit-tested, no server wiring, no live-data effect.
- **Phase 2 (outline): AI adjudication + two gates.** `ai.adjudicate_entry` for ambiguous/none cases; combine deterministic + AI into a per-entry verdict `{igdb_id | detach | keep_primary, confidence}`.
- **Phase 3 (outline): apply in the wand scan.** Drive the resolver over the scan's entries, write per-entry ids/detaches via `entry_res`, reconcile media per-entry; respect manual overrides; run on freshly-proposed matches.
- **Phase 4 (outline): fold contamination + tighten SS match.** Retire/rewrap `_auto_fix_contamination`; add a candidate-name coverage floor to `_ss_match` (fixes #9's `journey`→`…Journey to Batuu`).

Each phase ends deployable; Phases 2-4 get their own detailed plans after Phase 1 lands and is reviewed.

---

### Task 1: Phase 1 — deterministic per-entry resolver

**Files:**
- Modify: `igdb_enrich.py` — add `per_entry_resolve` + `entry_fits` (near `_pick_era_aware`, igdb_enrich.py:214).
- Create: `verify_per_entry_identity.py` (repo root) — fixture unit test, `verify_*`-style.

**Interfaces:**
- Produces:
  - `entry_fits(cand: dict, platform: str) -> bool` — True when `platmap.canon(platform)` is in the candidate's platform-canon set AND the candidate's release year *for that platform* (or its earliest year) is not `console_eras.impossible(platform, year)`.
  - `per_entry_resolve(candidates: list[dict], platform: str, primary_id: int|None) -> dict` returning `{"kind": "unique"|"ambiguous"|"none_impossible"|"none_compatible", "igdb_id": int|None, "fit_ids": list[int]}` where:
    - `unique` → exactly one candidate fits; `igdb_id` = it.
    - `ambiguous` → >1 fit; `fit_ids` lists them (Phase 2 AI decides); `igdb_id`=None.
    - `none_impossible` → no candidate fits AND the platform is era-impossible for the primary → detach-worthy; `igdb_id`=None.
    - `none_compatible` → no candidate fits but era-compatible (likely a port IGDB doesn't list) → keep primary; `igdb_id`=`primary_id`.
  - Candidate dict shape (subset of an IGDB game record): `{"id": int, "name": str, "platforms": [{"name": str}], "release_dates": [{"y": int, "platform": int|str}] , "year": int|None}`.

- [ ] **Step 1: Write the failing test**

Create `verify_per_entry_identity.py`:

```python
#!/usr/bin/env python3
"""Verify per-entry identity resolution (Phase 1, deterministic). Standalone."""
import sys
import igdb_enrich as E

# Candidate games (subset of IGDB records). `pyear` maps a platform-canon to that
# platform's release year; `year` is the fallback earliest year.
TR_1996 = {"id": 1164, "name": "Tomb Raider", "year": 1996,
           "platforms": [{"name": "PlayStation"}, {"name": "Sega Saturn"}, {"name": "PC (Microsoft Windows)"}]}
TR_2013 = {"id": 2013, "name": "Tomb Raider", "year": 2013,
           "platforms": [{"name": "PlayStation 3"}, {"name": "Xbox 360"}, {"name": "PC (Microsoft Windows)"}]}
TR_GB   = {"id": 5555, "name": "Tomb Raider", "year": 2000,
           "platforms": [{"name": "Game Boy Color"}]}
SF_SNES = {"id": 700, "name": "Star Fox", "year": 1993,
           "platforms": [{"name": "Super Nintendo Entertainment System"}]}

def one(cands, platform, primary):
    return E.per_entry_resolve(cands, platform, primary)

def main():
    trs = [TR_1996, TR_2013, TR_GB]
    # PS1 -> 1996; PS3 -> 2013; GameBoy Color -> GB game
    assert one(trs, "psx", 1164)["igdb_id"] == 1164, "PS1 Tomb Raider -> 1996"
    assert one(trs, "ps3", 1164)["igdb_id"] == 2013, "PS3 Tomb Raider -> 2013 reboot"
    assert one(trs, "gameboy color", 1164)["igdb_id"] == 5555, "GBC Tomb Raider -> GB game"
    # PC fits BOTH 1996 and 2013 -> ambiguous (Phase 2 AI decides)
    amb = one(trs, "pc", 1164)
    assert amb["kind"] == "ambiguous" and set(amb["fit_ids"]) == {1164, 2013}, "PC TR is ambiguous"
    # Star Fox on Atari 2600: no candidate fits + era-impossible -> detach-worthy
    r = one([SF_SNES], "atari 2600", 700)
    assert r["kind"] == "none_impossible" and r["igdb_id"] is None, "2600 Star Fox -> detach"
    assert one([SF_SNES], "snes", 700)["igdb_id"] == 700, "SNES Star Fox -> SNES game"
    # Legit port IGDB doesn't list: game only lists PS1 but we own it on a compatible
    # platform (Sega Saturn, same era) -> keep primary, NOT detach (over-separation guard)
    only_ps1 = {"id": 800, "name": "X", "year": 1996, "platforms": [{"name": "PlayStation"}]}
    r2 = one([only_ps1], "sega saturn", 800)
    assert r2["kind"] == "none_compatible" and r2["igdb_id"] == 800, "compatible-era port keeps primary"
    print("verify_per_entry_identity: OK")

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd ~/gitrepos/ludodex && python3 ludodex/verify_per_entry_identity.py`
Expected: `AttributeError: module 'igdb_enrich' has no attribute 'per_entry_resolve'`.

- [ ] **Step 3: Implement `entry_fits` + `per_entry_resolve` in `igdb_enrich.py`**

Add after `_pick_era_aware` (igdb_enrich.py:~247). Uses `platmap` (import it at top if absent) and the existing `console_eras` + `_year_of`.

```python
import platmap  # if not already imported at top of igdb_enrich.py


def _cand_platform_canons(cand):
    return platmap.igdb_canons(cand.get("platforms"))


def _cand_year_for(cand, pcanon):
    """Release year of `cand` on the platform-canon `pcanon` if known, else its
    earliest/overall year."""
    best = None
    for rd in (cand.get("release_dates") or []):
        # release_dates entries carry a platform id/name we can't always canon here;
        # fall back to the candidate's overall year when unmatched.
        y = rd.get("y")
        if y and (best is None or y < best):
            best = y
    return best or cand.get("year") or _year_of(cand)


def entry_fits(cand, platform):
    """True when `cand` released on `platform` (platmap canon membership) AND is not
    era-impossible for it."""
    pc = platmap.canon(platform)
    if pc not in _cand_platform_canons(cand):
        return False
    yr = _cand_year_for(cand, pc)
    return not console_eras.impossible(platform, yr) if yr else True


def per_entry_resolve(candidates, platform, primary_id):
    """Resolve ONE (entry, platform) against the exact-title candidate set.
    Returns {kind, igdb_id, fit_ids}. See plan Task 1 interface."""
    fits = [c for c in candidates if entry_fits(c, platform)]
    if len(fits) == 1:
        return {"kind": "unique", "igdb_id": fits[0]["id"], "fit_ids": [fits[0]["id"]]}
    if len(fits) > 1:
        return {"kind": "ambiguous", "igdb_id": None, "fit_ids": [c["id"] for c in fits]}
    # no candidate fits this platform
    prim = next((c for c in candidates if c["id"] == primary_id), None)
    prim_year = _cand_year_for(prim, platmap.canon(platform)) if prim else None
    if prim_year and console_eras.impossible(platform, prim_year):
        return {"kind": "none_impossible", "igdb_id": None, "fit_ids": []}
    return {"kind": "none_compatible", "igdb_id": primary_id, "fit_ids": []}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd ~/gitrepos/ludodex && python3 ludodex/verify_per_entry_identity.py`
Expected: `verify_per_entry_identity: OK`

- [ ] **Step 5: Byte-compile the module**

Run: `cd ~/gitrepos/ludodex && python3 -m py_compile igdb_enrich.py && echo OK`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
cd ~/gitrepos/ludodex && git add igdb_enrich.py verify_per_entry_identity.py \
  docs/superpowers/plans/2026-07-20-per-entry-identity.md
git commit -m "feat(identity): per-entry deterministic resolver (task #8 phase 1)"
```

---

### Phase 2 (outline — detailed after Phase 1 review)

AI adjudication for `ambiguous` and `none_impossible` cases. New `ai.adjudicate_entry(entries, candidates)` (batched ≤20) returning per entry `{same_as_group, correct_igdb_id|null, detach, confidence, reason}`. Combine with Phase 1's deterministic kinds into a final verdict; apply the two gates + 0.75 threshold. Unit-test the combiner with mocked AI verdicts.

### Phase 3 (outline)

Drive `per_entry_resolve` over each scanned title's entries inside `_aimeta_scan`, on freshly-proposed matches. Write results via `entry_res.set_entry` / `set_detach`; per-entry media reconcile; skip manual overrides. Verify a full rebuild reproduces the wand's per-entry result (byte-diff like `verify_catalog_patch.py`). Live-copy test on the Tomb Raider / Alice / Star Fox groups.

### Phase 4 (outline)

Retire `_auto_fix_contamination` (now subsumed) or keep as a thin caller. Tighten `_ss_match` (add candidate-name coverage floor / length-ratio guard) so a short ROM name can't match a longer title — fixes task #9.

## Self-Review

- **Spec coverage (Phase 1 slice):** deterministic per-entry fit by platform+era ✓; over-separation guard (`none_compatible` keeps primary) ✓; era-impossible → detach-worthy (`none_impossible`) ✓; ambiguous surfaced for Phase-2 AI ✓. Later spec items (AI, apply, contamination fold, SS gate) mapped to Phases 2-4.
- **Placeholder scan:** Phase 1 tasks are complete code; Phases 2-4 are explicitly outlines to be detailed after Phase 1 review (not placeholders within an active task).
- **Type consistency:** `per_entry_resolve(candidates, platform, primary_id) -> {kind,igdb_id,fit_ids}` and `entry_fits(cand, platform) -> bool` used consistently in test + impl.
