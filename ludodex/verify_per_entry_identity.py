#!/usr/bin/env python3
"""Verify per-entry identity resolution (task #8, Phase 1 — deterministic). Standalone.

entry_fits = does this IGDB candidate list this platform (IGDB is authoritative). The
no-fit classification uses the hardware-GENERATION gap (platmap.GEN), like the old
contamination check — a reliable "impossible backport" signal that the loose year-buffer
misses (Star Fox 1993 is only 1yr past the 2600's era end, but gen 2 << gen 4)."""
import igdb_enrich as E

TR_1996 = {"id": 1164, "name": "Tomb Raider", "year": 1996,
           "platforms": [{"name": "PlayStation"}, {"name": "Sega Saturn"},
                         {"name": "PC (Microsoft Windows)"}]}
TR_2013 = {"id": 2013, "name": "Tomb Raider", "year": 2013,
           "platforms": [{"name": "PlayStation 3"}, {"name": "Xbox 360"},
                         {"name": "PC (Microsoft Windows)"}]}
TR_GB = {"id": 5555, "name": "Tomb Raider", "year": 2000,
         "platforms": [{"name": "Game Boy Color"}]}
SF_SNES = {"id": 700, "name": "Star Fox", "year": 1993,
           "platforms": [{"name": "Super Nintendo Entertainment System"}]}


def main():
    trs = [TR_1996, TR_2013, TR_GB]
    R = E.per_entry_resolve
    # PS1 -> 1996; PS3 -> 2013 reboot; GBC -> the GB game (all deterministic, no AI)
    assert R(trs, "psx", 1164)["igdb_id"] == 1164, "PS1 Tomb Raider -> 1996"
    assert R(trs, "ps3", 1164)["igdb_id"] == 2013, "PS3 Tomb Raider -> 2013 reboot"
    assert R(trs, "gameboy color", 1164)["igdb_id"] == 5555, "GBC Tomb Raider -> GB game"
    # PC lists BOTH 1996 and 2013 -> ambiguous (Phase 2 AI decides)
    amb = R(trs, "pc", 1164)
    assert amb["kind"] == "ambiguous" and set(amb["fit_ids"]) == {1164, 2013}, "PC TR ambiguous"
    # Atari 2600 "Star Fox": no candidate lists 2600 AND gen 2 << SNES gen 4 -> detach-worthy
    r = R([SF_SNES], "atari 2600", 700)
    assert r["kind"] == "none_impossible" and r["igdb_id"] is None, "2600 Star Fox -> detach"
    assert R([SF_SNES], "snes", 700)["igdb_id"] == 700, "SNES Star Fox -> SNES game"
    # Compatible-era no-fit (game lists only PS1, we own it on Saturn, same gen 5): NOT a
    # deterministic detach -> none_uncertain (Phase 2 AI decides port-vs-different). This is
    # the over-separation guard: we never deterministically split a same-gen no-fit.
    only_ps1 = {"id": 800, "name": "X", "year": 1996, "platforms": [{"name": "PlayStation"}]}
    r2 = R([only_ps1], "sega saturn", 800)
    assert r2["kind"] == "none_uncertain", "compatible-era no-fit -> uncertain (AI), not detach"

    # --- combine_verdict: deterministic result + (mocked) AI verdict -> final action ---
    C = E.combine_verdict
    uniq = R(trs, "ps3", 1164)                      # deterministic unique -> 2013, no AI
    assert C(uniq, None, 1164) == {"action": "set", "igdb_id": 2013}, "unique applies w/o AI"
    ambig = R(trs, "pc", 1164)
    yes = {"same_as_group": False, "correct_igdb_id": 2013, "detach": False, "confidence": 0.9}
    assert C(ambig, yes, 1164)["igdb_id"] == 2013, "ambiguous: AI picks the release"
    low = {"same_as_group": False, "correct_igdb_id": 2013, "detach": False, "confidence": 0.4}
    assert C(ambig, low, 1164) == {"action": "keep", "igdb_id": 1164}, "low-conf AI -> keep (guard)"
    imp = R([SF_SNES], "atari 2600", 700)
    det = {"same_as_group": False, "correct_igdb_id": None, "detach": True, "confidence": 0.95}
    assert C(imp, det, 700) == {"action": "detach", "igdb_id": None}, "impossible+AI-confirm -> detach"
    assert C(imp, None, 700) == {"action": "keep", "igdb_id": 700}, "impossible w/o AI -> keep (guard)"
    unc = R([only_ps1], "sega saturn", 800)
    same = {"same_as_group": True, "correct_igdb_id": None, "detach": False, "confidence": 0.9}
    assert C(unc, same, 800) == {"action": "keep", "igdb_id": 800}, "uncertain+same-game -> keep port"

    # --- plan_title: orchestrate over a whole title's entries (mocked adjudicate) ---
    P = E.plan_title

    def adj_pc(items):  # the only ambiguous TR entry (PC) -> call it the 1996 original
        return [{"n": it["n"], "same_as_group": False, "correct_igdb_id": 1164,
                 "detach": False, "confidence": 0.9} for it in items]
    tr_entries = [{"platform": "psx"}, {"platform": "ps3"}, {"platform": "pc"},
                  {"platform": "gameboy color"}]
    plan = {p["platform"]: p for p in P("tomb raider", 1164, tr_entries, trs, adj_pc)}
    assert plan["psx"]["igdb_id"] == 1164 and plan["psx"]["kind"] == "unique", "PS1 unique 1996"
    assert plan["ps3"]["igdb_id"] == 2013 and plan["ps3"]["action"] == "set", "PS3 -> 2013 (no AI)"
    assert plan["gameboy color"]["igdb_id"] == 5555, "GBC -> GB game"
    assert plan["pc"]["kind"] == "ambiguous" and plan["pc"]["igdb_id"] == 1164, "PC AI-resolved to 1996"

    def adj_detach(items):
        return [{"n": it["n"], "same_as_group": False, "correct_igdb_id": None,
                 "detach": True, "confidence": 0.95} for it in items]
    sf_plan = {p["platform"]: p for p in
               P("star fox", 700, [{"platform": "snes"}, {"platform": "atari 2600"}],
                 [SF_SNES], adj_detach)}
    assert sf_plan["snes"]["action"] == "set" and sf_plan["snes"]["igdb_id"] == 700, "SNES kept"
    assert sf_plan["atari 2600"]["action"] == "detach", "2600 Star Fox detached via AI"
    print("verify_per_entry_identity: OK")


if __name__ == "__main__":
    main()
