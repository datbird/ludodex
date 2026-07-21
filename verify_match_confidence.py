#!/usr/bin/env python3
"""Verify the rule-based match-confidence scorer (task #13). Threshold default = 60."""
import matchconf as M

# IGDB record fixtures (name + platforms; alt names optional)
SIMS4 = {"name": "The Sims 4: Journey to Batuu",
         "platforms": [{"name": "PC (Microsoft Windows)"}, {"name": "PlayStation 4"}]}
TR_1943 = {"name": "1943: The Battle of Midway",
           "platforms": [{"name": "Arcade"}, {"name": "NES"}]}
EXACT = {"name": "Contra", "platforms": [{"name": "NES"}, {"name": "Arcade"}]}


def main():
    C = M.match_confidence
    # manual pin -> always 100 regardless of anything else
    s, _ = C("manual", "journey", SIMS4, "arcade")
    assert s == 100, "manual pin forced to 100"
    # steam appid -> high, no title/platform gating
    s, _ = C("steam_appid", "whatever", {}, "pc")
    assert s == 96, "steam_appid base"
    # exact title match on a listed platform -> high
    s, _ = C("name", "contra", EXACT, "nes")
    assert s == 85, "exact + fits = source base"
    # the journey case: name match, interior title, arcade impossible for a 2020 game -> ~0
    s, r = C("name", "journey", SIMS4, "arcade")
    assert s < 60, "journey -> low confidence (was %d)" % s
    assert "interior" in r and "impossible" in r, "reason names the factors: %r" % r
    # anchored subtitle variant on a listed platform -> stays ABOVE threshold (legit, kept)
    s, _ = C("name", "1943", TR_1943, "arcade")
    assert s >= 60, "1943 anchored variant stays high (was %d)" % s
    # AI-proposed + interior -> low
    s, _ = C("ai_name", "journey", SIMS4, "arcade")
    assert s < 60, "ai interior -> low (was %d)" % s
    # platform not listed but SAME generation (era-plausible) -> softer -22, not the -42
    # impossible penalty (an era-impossible exact match wouldn't survive resolution anyway).
    only_ps1 = {"name": "Contra", "platforms": [{"name": "PlayStation"}]}   # gen 5
    s_soft, _ = C("name", "contra", only_ps1, "sega saturn")               # gen 5, not impossible
    assert 60 <= s_soft < 85, "exact title, same-gen platform-not-listed = soft dip (was %d)" % s_soft
    # unknown/empty platforms -> no platform penalty (don't punish thin metadata)
    s, _ = C("name", "contra", {"name": "Contra"}, "nes")
    assert s == 85, "no platforms listed -> no platform penalty (was %d)" % s
    print("verify_match_confidence: OK")


if __name__ == "__main__":
    main()
