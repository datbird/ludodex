#!/usr/bin/env python3
"""Match confidence (0-100) for an identified game ENTRY — how sure we are it's the RIGHT
IGDB game (identity certainty), which is NOT the Ludodex quality score (see scoring.py).

Rule-based from signals we already have: the match SOURCE (igdb_resolution.matched_by), the
TITLE anchor (igdb_enrich._name_anchor_class — exact/anchored/interior/norun), and PLATFORM
fit (does IGDB list this entry's platform for the game). The wand's AI pass refines the gray
zone separately (match_confidence_ai). Powers the `confidence:low` library facet (task #13)."""
import platmap
from igdb_enrich import _name_anchor_class, _cand_platform_canons

# base by match source; anything else (unknown/legacy) -> 65
_SOURCE_BASE = {"manual": 100, "steam_appid": 96, "era_reheal": 88,
                "name": 85, "ai_name": 72, "ai_entry": 72}
# sources whose identity came from the TITLE, so the anchor quality applies
_TITLE_BASED = {"name", "ai_name", "ai_entry", "era_reheal"}
_ANCHOR_PENALTY = {"exact": 0, "anchored": -12, "interior": -50, "norun": -45}


# ScreenScraper identity bases. SS is a retro/console DB that barely covers PC, so a
# Steam→SS miss is legitimate, not low confidence — we only score entries SS actually
# matched. (SteamGridDB is art-only — it has no identity to score, so no sgdb variant.)
_SS_SOURCE_BASE = {"manual": 100, "ss_id": 90, "ss_name": 80}


def ss_match_confidence(matched_by, nk, ss_names, ss_plat_canons, entry_platform):
    """(score:int 0-100, reason:str) for a ScreenScraper identity — the SS counterpart of
    match_confidence. `ss_names` = [primary title + region alternates]; `ss_plat_canons` =
    set of canon platform names SS lists for the game. Same title-anchor + platform-fit
    rules, SS-shaped inputs (the caller extracts them since SS's record differs from IGDB's)."""
    mb = matched_by or "ss_name"
    if mb == "manual":
        return 100, "pinned by hand"
    base = _SS_SOURCE_BASE.get(mb, 70)
    factors = []
    names = [n for n in (ss_names or []) if n]
    if names:
        anchor = _name_anchor_class(nk, names)
        base += _ANCHOR_PENALTY.get(anchor, 0)
        if anchor in ("interior", "norun"):
            factors.append("%s title match" % anchor)
    if entry_platform and ss_plat_canons \
            and platmap.canon(entry_platform) not in ss_plat_canons:
        base -= 22
        factors.append("platform not listed by ScreenScraper")
    score = max(0, min(100, base))
    if not factors:
        factors.append("matched by ScreenScraper")
    return score, "; ".join(factors)


def _impossible(entry_platform, cand):
    """True when the entry's hardware generation is strictly BELOW the game's earliest
    platform generation (a game can't ship on hardware older than its debut) — the strong
    'wrong game' signal, mirroring igdb_enrich.per_entry_resolve's none_impossible."""
    eg = platmap.GEN.get(platmap.canon(entry_platform))
    if eg is None:
        return False
    gens = [platmap.GEN.get(c) for c in _cand_platform_canons(cand)]
    gens = [g for g in gens if g is not None]
    return bool(gens) and eg < min(gens)


def match_confidence(matched_by, nk, cand, entry_platform):
    """(score:int 0-100, reason:str) for one identified entry. `cand` = the IGDB record dict
    ({name, alternative_names, platforms:[{name}]}); may be None/empty when metadata is thin.
    Manual pins are always 100 (user ground truth)."""
    mb = matched_by or ""
    if mb == "manual":
        return 100, "pinned by hand"
    base = _SOURCE_BASE.get(mb, 65)
    factors = []
    cand = cand or {}
    # title-anchor quality (only for title-derived matches, and only if we have a name)
    if mb in _TITLE_BASED and cand.get("name"):
        names = [cand.get("name", "")] + [
            (a.get("name") if isinstance(a, dict) else a)
            for a in (cand.get("alternative_names") or [])]
        anchor = _name_anchor_class(nk, names)
        base += _ANCHOR_PENALTY.get(anchor, 0)
        if anchor in ("interior", "norun"):
            factors.append("%s title match" % anchor)
    # platform fit — only for TITLE-based matches (a wrong platform hints at a wrong game).
    # steam_appid/manual identity is certain regardless of platform (a Jaguar Doom sharing a
    # Steam appid is still Doom), so they skip this. Only penalize when IGDB actually lists
    # platforms and ours isn't among them.
    cand_plats = _cand_platform_canons(cand)
    if mb in _TITLE_BASED and entry_platform and cand_plats \
            and platmap.canon(entry_platform) not in cand_plats:
        if _impossible(entry_platform, cand):
            base -= 42
            factors.append("platform impossible for this game's era")
        else:
            base -= 22
            factors.append("platform not listed by IGDB")
    score = max(0, min(100, base))
    if not factors:
        factors.append({"steam_appid": "matched by store ID",
                        "name": "exact title match"}.get(mb, "matched"))
    return score, "; ".join(factors)
