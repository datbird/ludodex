#!/usr/bin/env python3
"""The rule that decides whether a provider's candidate IS the game we asked for.

One gate, shared by every provider, because each of them got this wrong in its own way:

  * ScreenScraper searched cleaned VARIANTS of a title and then scored the candidate
    against the variant it had searched — so a subtitle-stripped variant matched its own
    parent game perfectly. "Quake II: The Reckoning" became Quake II, "ELDEN RING
    NIGHTREIGN" became a Game Boy demake called "Elden Ring GB", and 191 titles ended up
    sharing 86 ids with each other.
  * SteamGridDB had no name check at all — `return items[0].get("id")`, the first
    autocomplete result whatever it was named.
  * IGDB alone was already strict (a literal exact-name lookup), which is why it has the
    fewest collisions, and it is the standard the other two are being held to here.

The rule: measure BOTH directions against the title the user owns.

  qc  how much of the OWNED title the candidate covers. Gated at 0.8 — a candidate
      missing "The Reckoning" is a different product however exactly it matches the rest.
      This is the direction that stops a parent, a sequel or a collection standing in.
  nc  how much of the CANDIDATE the owned title covers. Scored, never gated, because
      providers append edition, region and platform words we do not carry.

Search variants stay free to be as loose as they like; they exist to FIND rows a provider
spells differently. They just do not get to redefine what game was asked for.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import titlenorm      # noqa: E402

# Words that carry no identity: a candidate may omit them freely, and they must never be
# the reason a title is refused. Everything NOT in here is treated as distinguishing.
#
# 0.8 fractional coverage was the first attempt and is subtly wrong in both directions.
# It let "Mega Man Legacy Collection" stand in for "Mega Man X Legacy Collection" — the
# single token "x" is 20% of a five-word title, so it landed exactly on the threshold —
# while refusing "Mass Effect 2" for our own disambiguated "Mass Effect 2 (2021)",
# because the year we added counted as a real word. Whether a missing word matters is
# not a question of how long the title is.
NOISE = {
    # editions and packaging
    "edition", "editions", "deluxe", "premium", "ultimate", "complete", "definitive",
    "collectors", "collector", "goty", "anniversary", "enhanced", "remaster",
    "remastered", "remake", "redux", "hd", "sd", "4k", "uhd", "classic", "original",
    "standard", "special", "gold", "platinum", "digital", "bundle", "pack",
    # platform / distribution noise
    "pc", "steam", "windows", "version", "release", "the", "a", "an", "of", "and",
    # A lone "s" is what titlenorm leaves of a possessive — "Marvel's Spider-Man"
    # normalises to "marvel s spider man", and a provider filing it as "Marvel
    # Spider-Man" was refused for a missing apostrophe. "plus" joins the edition markers
    # beside it ("Disgaea 4 Complete+" vs "Disgaea 4 Complete").
    "s", "plus",
}
# A bare 4-digit year is ours, not the title's: entries are disambiguated as
# "Mass Effect 2 (2021)" / "(2023)" when a store lists the same game twice.
YEAR = __import__("re").compile(r"^(19|20)\d{2}$")

# How far two release years may differ and still be the same release. Regional launches
# straddle a new year routinely; an original and its remake do not.
YEAR_TOLERANCE = 1


def _year(v):
    """`v` as a 4-digit year, or None if it is absent or not one."""
    t = str(v or "").strip()
    return int(t) if t.isdigit() and len(t) == 4 else None


def _significant(tokens):
    """The tokens that actually identify a game."""
    return {t for t in tokens if t not in NOISE and not YEAR.match(t)}


# A SERIES NUMBER. Arabic, or a roman numeral — but never a bare "x", which is a
# character name at least as often as it is ten: "Mega Man X Legacy Collection" is not
# "Mega Man Legacy Collection", and that pair is already the cautionary tale in NOISE's
# comment above. Ambiguity here costs a wrong merge, so the ambiguous token stays
# distinguishing.
ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8,
         "ix": 9, "xi": 11, "xii": 12, "xiii": 13}
_NUM = __import__("re").compile(r"^\d{1,2}$")


def _is_numeral(tok):
    return bool(_NUM.match(tok)) or tok in ROMAN


def _numeral_value(tok):
    """A numeral token's VALUE, so "1" and "I" can be recognised as the same number.

    Databases disagree about the notation, not the number: ScreenScraper files "Quake
    Mission Pack 1: Scourge of Armagon" as "Quake Mission Pack No. I : Scourge of
    Armagon". Comparing the notation refused a correct match over a typographic choice.
    Comparing the VALUE keeps "Ys I" and "Ys II" apart, which is the only thing that
    matters here — 1 is still not 2.
    """
    if _NUM.match(tok):
        return int(tok)
    return ROMAN.get(tok)


def _covers(sig, ntok):
    """Every distinguishing word of the owned title appears in the candidate — with a
    numeral satisfied by an equivalent numeral in either notation."""
    if not sig:
        return False
    nvals = {_numeral_value(t) for t in ntok if _is_numeral(t)}
    for t in sig:
        if t in ntok:
            continue
        v = _numeral_value(t) if _is_numeral(t) else None
        if v is not None and v in nvals:
            continue
        return False
    return True


def _subtitle(name):
    """The part after the first subtitle separator, normalised to significant tokens.

    A series number is only forgivable when something ELSE identifies the entry, and that
    something is the subtitle. "Police Quest IV: Open Season" and "Police Quest: Open
    Season" are one game named two ways; "Ys I" and "Ys II" are two games, and the only
    reason we can tell is that neither carries a subtitle to agree on.
    """
    for sep in (":", " - ", " – "):
        if sep in name:
            return _significant(titlenorm.norm(name.split(sep, 1)[1]).split())
    return set()


def numbering_variant(owned, cand_name):
    """True when two titles differ ONLY by a series number and share a real subtitle.

    The gate refuses a missing distinguishing word, and a series number is normally
    exactly that. But storefronts and databases disagree about whether the number belongs
    in the title at all — IGDB files "Crash Bandicoot 3: Warped" as "Crash Bandicoot:
    Warped", "Police Quest IV: Open Season" as "Police Quest: Open Season" — and refusing
    those loses correct matches for no gain.

    Both guards are load-bearing:
      * the ONLY difference may be numeral tokens (anything else is a different game);
      * the number must be missing from ONE side, never DIFFERENT on the two. This is
        the guard that matters: a provider omitting a number it does not file ("Police
        Quest: Open Season") is one game named two ways, but two numbers that disagree
        are two games, and forgiving that would merge "Ys I: Ancient Ys Vanished" into
        "Ys II: Ancient Ys Vanished" — a same-subtitle sibling pair, which is exactly
        the shape this project has already been burned by;
      * the subtitle must be non-empty and EQUAL on both sides, so a bare "Ys I" and
        "Ys II" can never reach this rule at all.
    """
    o = _significant(titlenorm.norm(owned).split())
    c = _significant(titlenorm.norm(cand_name).split())
    o_only, c_only = o - c, c - o
    diff = o_only | c_only
    if not diff or any(not _is_numeral(t) for t in diff):
        return False
    if o_only and c_only:
        return False                              # both numbered, and they disagree
    osub, csub = _subtitle(owned), _subtitle(cand_name)
    return bool(osub) and osub == csub


def safe_aliases(owned, aliases):
    """The aliases that may widen ACCEPTANCE, not merely the search.

    An alias is a search key: it exists to FIND rows a provider spells differently, and
    it may be as loose as it likes for that. It became an acceptance key by accident —
    `_search_with_aliases` passes it to the matcher as the owned title, so the candidate
    gets judged against the alias instead of against the game we own. Live, that is the
    single worst class of bind in this library: `Deathmatch Classic` holds "DmC : Devil
    May Cry - Definitive Edition" because 'DMC' is one of its aliases and 'DMC' matches
    that perfectly; `Beyond Citadel` holds "The Citadel" through the alias 'Citadel'.
    Its own title refuses both, and so does every other alias it has.

    What separates those from a real alternate name is that they are DEGRADATIONS —
    an abbreviation or a truncation of the title we already hold, which can only lose the
    words that distinguish it. A genuine regional name is a different name, not a smaller
    one: "Probotector" for "Contra", "Akumajou Dracula" for "Castlevania".

    ONE signal, deliberately. Two broader rules were written first and measured against
    the live library, and the measurement refused them:

      * "materially shorter" refused 69 identities and killed correct ones —
        ScreenScraper genuinely files Wolfenstein 3D as "Wolf3d", and an alias is how
        that record is reached at all.
      * TRUNCATION (the alias's tokens a proper subset of the owned title's) is not
        decidable. `Beyond Citadel` <- "The Citadel" is a DIFFERENT game and must be
        refused; `Fallout 76 Public Test Server` <- "Fallout 76" is the SAME game and
        should be kept — and they are the identical shape, reached by the identical kind
        of alias. Blocking the shape fixed ~25 bad binds and broke ~40 good ones. A rule
        that cannot tell those apart should not pretend to; that class needs adjudication,
        not arithmetic (see docs/TASKS.md).

    What is left is the case with no such ambiguity:

      * INITIALISM — a lone token of four characters or less against a title with much
        more to say. An acronym is inherently collision-prone ('DMC' matching "DmC :
        Devil May Cry"), it expresses no base-game relationship the way a truncation
        does, and no provider needs one to find a game whose real name is also being
        searched with.

    Everything else — a respelling, a romanisation, a regional rename, a provider's own
    contraction — is kept. Aliases stay available for SEARCHING either way; this only
    governs what is allowed to say "yes, that is the game".
    """
    keep = []
    on = titlenorm.norm(owned or "")
    for a in aliases or []:
        an = titlenorm.norm(a or "")
        if not an:
            continue
        asig = _significant(an.split())
        # Measured against the RAW normalised length, not the significant token count:
        # 'classic' is a NOISE word, so "Deathmatch Classic" reduces to a single
        # significant token and a token-count guard never fired on its worst alias.
        _a, _o = an.replace(" ", ""), on.replace(" ", "")
        if len(asig) == 1 and len(_a) <= 4 and len(_o) >= 2 * len(_a):
            continue                              # initialism: 'DMC' for 'Deathmatch Classic'
        keep.append(a)
    return keep


def score(owned, cand_name, year=None, cand_year=None):
    """Is `cand_name` acceptable for one of the OWNED titles? Returns (ok, score). Split out of `_ss_match` because the acceptance test used to be
    applied to whichever cleaned VARIANT had been searched, not to the title the user
    owns. Variants exist so that "Mega Man X4" can find ScreenScraper's "Megaman X4";
    they must not also let a subtitle-stripped variant match its own parent game:
    "Half-Life: Opposing Force" searched as "Half-Life" scored a perfect 1.0 against
    Half-Life and was recorded as Opposing Force's identity. Live that bound 191 titles
    onto 86 ScreenScraper ids, and the loser of each collision inherited the winner's art.

    So both directions are measured against the owned title:
      qc  how much of the OWNED title the candidate covers — a candidate missing
          "opposing force" is a different game, however exactly it matches the rest;
      nc  how much of the candidate the owned title covers — tolerant, because
          providers append edition and region words we do not carry.
    """
    best = (False, 0.0)
    cn = titlenorm.norm(cand_name or "")
    ntok = set(cn.split())
    if not ntok:
        return best
    for t in (owned or []):
        qn = titlenorm.norm(t or "")
        qtok = set(qn.split())
        if not qtok:
            continue
        inter = len(qtok & ntok)
        qc = inter / len(qtok)
        nc = inter / len(ntok)
        # The gate is not fractional: EVERY distinguishing word of the owned title has
        # to appear in the candidate. "Boltgun" is not "Boltgun 2", "Cult of the Lamb"
        # is not its Heretic Pack, and "Hammerwatch II" is not "Heroes of Hammerwatch
        # II" — in each case exactly one word tells them apart, and that word is the
        # whole point. Noise words and our own year suffixes are exempt.
        sig = _significant(qtok)
        covered = _covers(sig, ntok)
        # A series number is a distinguishing word, except when the subtitle already
        # does the distinguishing — see numbering_variant. Applied here rather than by
        # loosening NOISE, because "iv" must stay distinguishing everywhere else.
        if not covered and numbering_variant(t, cand_name):
            covered = True
        # THE MIRROR OF THE RULE ABOVE, and the half that was missing. `_covers`
        # measures the OWNED title's words inside the candidate, so it catches
        # "Boltgun 2" <- "Boltgun" and is blind to "Boltgun" <- "Boltgun 2": a sequel
        # CONTAINS its original's whole title, so coverage is perfect and the extra
        # numeral was only ever scored, never gated. The docstring above already claims
        # this case is refused; it was not.
        #
        # Live: `evoland` took ScreenScraper 296456 — "Evoland II" — the id its real
        # sequel already held, and invariant I9 caught the collision. Measured across
        # all 934 name-derived identities, this refuses that one and nothing else.
        #
        # Scoped to an owned title carrying NO numeral of its own. When it has one and
        # they differ, `_covers` already refuses, because the owned numeral is a
        # significant word absent from the candidate.
        #
        # A numeral whose VALUE IS 1 is exempt, because the first entry in a series is
        # what an unnumbered owned title already means. Measured: without this it
        # refused "Being a DIK" <- "Being a DIK: Season 1" and "The Walking Dead" <-
        # "The Walking Dead : Saison 1", both correct links, while still refusing
        # "The Walking Dead: Saints & Sinners" <- "… - Chapter 2", which is a different
        # product. Value, not notation, so "II" and "2" behave alike.
        if covered and not numbering_variant(t, cand_name):
            extra = [x for x in (ntok - qtok) if _is_numeral(x)]
            if (extra and not any(_numeral_value(x) == 1 for x in extra)
                    and not any(_is_numeral(x) for x in qtok)):
                covered = False
        # Token sets cannot see that "Megaman X4" and "Mega Man X4" are the same game:
        # they share ONE token and score 0.50. Squashing the spaces out makes an equal
        # string an exact match whatever the word breaks.
        if qn.replace(" ", "") == cn.replace(" ", ""):
            qc = nc = 1.0
            covered = True
        # A year DISAGREEMENT is disqualifying, not merely unrewarded. The NOISE rule
        # above strips a 4-digit year so our own "Mass Effect 2 (2021)" matches the
        # provider's "Mass Effect 2" — correct for a duplicate store listing, and
        # exactly backwards for a remake, whose title is identical to its original and
        # whose year is the only thing separating them. Live, Resident Evil 4 (2023)
        # took ScreenScraper 4750, the 2005 GameCube game, and wore its box art.
        #
        # Only ever applied when BOTH years are known and numeric: an absent year is not
        # evidence of anything, and must never turn into a refusal.
        y1, y2 = _year(year), _year(cand_year)
        if y1 and y2 and abs(y1 - y2) > YEAR_TOLERANCE:
            continue
        score = qc + nc + (0.4 if y1 and y1 == y2 else 0)
        if covered and score > best[1]:
            best = (True, score)
        elif not best[0] and score > best[1]:
            best = (False, score)
    return best


def game_era(lib_con, cache_con, norm_key):
    """The year THE GAME came out — which is not the year a STORE listed it.

    `release_year` names two different facts. For a ROM it is the game's release. For a
    store entry it is when that STOREFRONT listing appeared, so Arcanum reads 2016 (its
    re-release), Caesar 3 reads 2016 (1998), Dungeon Keeper Gold reads 2024 (1997). Feed
    that into an era test with a +/-1 tolerance and every re-released PC game looks like
    a remake wearing its original's art.

    Live, all 123 of invariant I10's era disagreements were exactly this: correct
    ScreenScraper matches, flagged because a storefront date was standing in for a
    release year. They were recorded before this call site passed any year at all — so
    the same confusion was also about to make the GATE refuse all 123 on the next
    re-match, which is the more expensive half. One fact, one definition, both callers.

    Order:
      1. IGDB's `first_release_date` for the identified game — a provider's own statement
         about the GAME, which is the thing being compared.
      2. the catalog's release_year, but ONLY for an entry with no store source, where it
         is a release year rather than a listing date.
      3. None — no statement. An absent year is not evidence and must never refuse
         anything (see the disagreement rule above).

    It deliberately does NOT weaken the case it was built for: Resident Evil 4 (2023) is
    a different IGDB record from the 2005 game, so IGDB says 2023, ScreenScraper says
    2005, and the match is still refused.
    """
    import json
    try:
        row = cache_con.execute(
            "SELECT igdb_id FROM igdb_resolution WHERE norm_key=? AND "
            "COALESCE(igdb_id,0)>0 LIMIT 1", (norm_key,)).fetchone()
    except Exception:                              # noqa: BLE001 — cache may be absent
        row = None
    if row:
        try:
            p = cache_con.execute("SELECT payload_json FROM igdb_meta WHERE igdb_id=? "
                                  "LIMIT 1", (row[0],)).fetchone()
            ts = json.loads(p[0]).get("first_release_date") if p else None
            if ts:
                import datetime
                return datetime.datetime.fromtimestamp(
                    int(ts), datetime.timezone.utc).year
        except Exception:                          # noqa: BLE001 — a bad payload is a miss
            pass
    try:
        store = lib_con.execute(
            "SELECT 1 FROM games g JOIN sources s ON s.game_id=g.id WHERE g.norm_key=? "
            "AND s.source NOT IN ('emulation','archive') LIMIT 1", (norm_key,)).fetchone()
        if store:
            return None                            # a listing date is not a release year
        r = lib_con.execute(
            "SELECT ga.value FROM game_attributes ga JOIN games g ON g.id=ga.game_id "
            "WHERE g.norm_key=? AND ga.kind='release_year' LIMIT 1",
            (norm_key,)).fetchone()
        if r and str(r[0] or "").isdigit():
            return int(r[0])
    except Exception:                              # noqa: BLE001
        pass
    return None


def pick_by_year(cands, year):
    """The candidate a stated year identifies, or None.

    `cands` are exact-normalized-title matches already; the only question left is WHICH
    of them the game is. A year answers that, and nothing else does — so when the year
    fails to single one out, the honest answer is None.

    Live case (2026-08-21): six IGDB records are named exactly "Star Trek" — 1971, 1973,
    1987 and three carrying no year. The AI said 2013. The ranking this replaces scored
    every candidate identically ("does its year equal 2013" is false for all six), then
    broke the tie on `year or 9999` — earliest first — and bound the owned 2013 Steam
    game to the 1971 mainframe record.

    Earliest-first is right for its own case and wrong here. It exists so a buried
    original wins over a later remake, and IGDB's relevance search genuinely needs that
    ("Gradius" returns only sequels, "Contra" returns the 2006 remake). But it is a
    preference between candidates that could each be the game, and it was being asked to
    decide between candidates where only one could. That is the same "a miss is not
    consent" shape as the negative-cache read: absence of a matching year was treated as
    permission to choose.

    An undated candidate can never SATISFY a stated year, and equally can never be
    refused for lacking one — `game_era()`'s rule, arrived at from 123 live false
    refusals, holds on the candidate side too. So an undated record loses any contest a
    year decides, and a LONE undated record still binds, because its silence is not an
    argument against it. 23.8% of the IGDB mirror (88,453 of 371,978) carries no year and
    7,149 of those share a name with a dated record, so this is the common case and not
    the corner one; refusing all of them would drop real matches wholesale.

    The rules, in order:
      * no candidates                      -> None
      * exactly one candidate              -> it, whether or not a year was stated
      * a year is stated, one carries it   -> that one
      * a year is stated, none carries it  -> None (the defect above)
      * a year is stated, several carry it -> None (cannot be told apart)
      * no year stated, several candidates -> None (nothing to decide on)
    """
    cands = list(cands or [])
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    y = _year(year)
    if y is None:
        return None                # several candidates and nothing to separate them
    hits = [c for c in cands if _year(c.get("year")) == y]
    return hits[0] if len(hits) == 1 else None
