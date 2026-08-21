# Undated identities, and the guess that fills the gap

Status: design, 2026-08-21. datbird's decision, after the measurement below:

> "I cannot imagine why there would be an entry with no date... if there are we should
> create a list of with out dates, do some research and if all that can be discovered is
> a date range we should be capable of adding that as well."

Asked which of four options to spend effort on, the answer was all four.

## What started it

The review queue proposed binding the owned Steam game **Star Trek** (appid 203250) to
**igdb:11485**, whose record is the *1971* mainframe game. The AI itself had said 2013
and named Digital Extremes. Identity and attributes contradicted each other inside one
finding.

The deterministic path was not at fault and is worth stating plainly, because the
instinct on seeing a bad match is to distrust the index:

| matched_by | count | share of 2,260 |
|---|---|---|
| `steam_appid` | 2,080 | 92.0% |
| `name` | 128 | 5.7% |
| `none` (recorded miss) | 52 | 2.3% |

Star Trek is one of the 52, cached as `('star trek', 0, None, 'none')` — a correct
negative. IGDB's `external_ids` carries 173,641 Steam ids and **203250 is not one of
them**; no IGDB record named "Star Trek" carries a Steam id at all. The game was delisted
years ago and IGDB never recorded the link. The index is correct and incomplete in exactly
the way its source is incomplete. `matchindex.py:767` builds each store namespace with
`WHERE source_id=?`, so the namespaces are properly scoped — a uid collision does exist in
the raw table (`203250` is also a PSN id for Puyo Puyo Tetris) and the index does not fall
for it.

## The four findings, measured

Against the live mirrors on 2026-08-21.

**1. A quarter of IGDB has no year.**

| | count | share |
|---|---|---|
| IGDB mirror games | 371,978 | |
| undated | **88,453** | 23.8% |
| …of those, `game_type` 0 (a plain game) | 79,188 | 89.5% |
| undated *and* sharing a name with a dated record | 7,149 | the ones that misdirect a match |

**2. Only a tenth of them can be dated from catalogs already on disk.** Joining
ScreenScraper, MobyGames and TheGamesDB through the match index:

| | count |
|---|---|
| undated, recoverable from any mirror | **9,391** (10.6%) |
| …every mirror agrees on ONE year | 8,086 (86% of recoverable) |
| …mirrors disagree, 2–6 distinct years | 1,305 |
| recoverable within the risky 7,149 | 1,935 (27%) |

**3. The disagreements are mostly not uncertainty. They are over-merge.** IGDB 131474
"Star Trek" (undated) holds 7 MobyGames ids, 7 ScreenScraper ids and 3 TheGamesDB ids
under one identity. Their years read 1976, 1978, 1979, 1979, 1982, 1983, 1992. Those are
seven different games that share a name. **9,117 identities** carry more than three ids
from a single provider namespace — the same shape at scale.

The consequence for this design: a year range naively derived from mirror disagreement
would encode a name collision as knowledge. For Star Trek that range is 1970–1994, which
*accepts* the 1971 match rather than refusing it. **A range cannot be built before the
over-merge is split.**

**4. The library itself is nearly clean.** 33 of 2,255 entries lack a release year, and
they are almost exactly the games sitting in the review queue. An entry has no year
*because* identity failed, not the other way round. There is no library-side backfill
worth doing.

## The actual defect

`_provider_match()` in `server/app.py`, the tail of the no-consoles path:

```python
best = sorted(cands.values(),
              key=lambda h: (0 if (year and h.get("year") == year) else 1,
                             h.get("year") or 9999))[0]
return _pack_igdb(best)
```

Six IGDB records are named exactly "Star Trek": 1971, 1973, 1987, and three undated. The
AI said 2013. No candidate carries 2013, so every candidate falls to tier 1 and the
tiebreak takes the earliest — 1971.

Four lines above sits the principle this violates:

```python
if not cands:
    return None                 # no trustworthy IGDB entry — better none than wrong
```

That rule is applied to the candidate *set* and not to the *year*. A stated year that
matches nothing is evidence of a miss, and the code reads it as a starting point for a
guess instead.

Two gaps compound it:

* **A PC game gets no era gate.** `_emulation_consoles(nk)` returns empty for a
  store-only entry, so `consoles` is falsy and `igdb_enrich._pick_era_aware` — the
  selector that rejects era-impossible years — never runs. The weaker legacy path does.
* **The acceptance gate cannot see it either.** `matchgate.game_era()` returns `None`
  for any entry with a store source, by design, because a storefront listing date is not
  a release year. None refuses nothing. That design is correct and stays; it just means
  the refusal has to happen at selection.

## The rule

**A stated year that matches no candidate is a refusal, not a tiebreak.**

And its companion, which the same live case demands:

**Candidates that cannot be told apart are a refusal.** Six records named "Star Trek",
three of them undated, is not a ranking problem. It is an absence of evidence.

## Where it lives

`ludodex/matchgate.py` — the one acceptance gate, already shared by every provider, and
free of any fastapi import so it is testable outside the container. The selection becomes
a pure function there; `server/app.py` calls it. This is the same "one derivation, one
home" rule that every defect in this project has traced back to.

```python
def pick_by_year(cands, year):
    """The candidate a stated year identifies, or None.

    cands: [{igdb_id, name, year, ...}] — already filtered to exact-title matches.
    Returns the single candidate whose year IS `year`; None if no candidate carries it,
    if two do, or if `year` is None and more than one candidate exists.
    """
```

Note what it does *not* do: when `year` is None and exactly one candidate exists, that
candidate still binds. The Gradius and Contra cases the current tiebreak was written for
keep working, because those have a single exact-title record once the search and the
name index are merged. What dies is the arbitrary pick among many.

## The durability constraint

`matchindex.build()` opens with:

```python
con.execute("DELETE FROM identity_key")
con.execute("DELETE FROM identity")
```

It wipes and rebuilds from the mirrors every run. So a backfilled year, a split identity
and a year range are all destroyed on the next rebuild unless they live in a **durable
overlay table that `build()` reads and re-applies**. This is the same reason
`review_decided` lives in the findings db rather than in `identity_review`, which is
rebuilt with every catalog build.

Overlay shape, in `match-index.sqlite`, never truncated by `build()`:

```sql
CREATE TABLE identity_overlay(
  identity_id INTEGER PRIMARY KEY,
  year INTEGER,           -- a single agreed year
  year_lo INTEGER,        -- or a range, when the evidence is genuinely a range
  year_hi INTEGER,
  split_of INTEGER,       -- set when this identity was carved out of an over-merge
  basis TEXT,             -- 'mirror_agree' | 'mirror_range' | 'manual' | 'split'
  sources TEXT,           -- the provider ids the year came from, for audit
  at REAL
)
```

`basis` is load-bearing. A year the mirrors agreed on and a year a person typed are
different claims, and a later, stricter rule has to be able to re-judge the first without
touching the second — the same scoping `provider_ids.rescore()` already applies to
`search` / `name` / `steam_appid` / `manual`.

## Steps, in dependency order

**1. The gate refuses instead of guessing.** `matchgate.pick_by_year()` plus its call
site in `_provider_match()`. Independent of the other three; ships first. Test pins the
live six-candidate Star Trek case, and pins that a single undated exact-title candidate
still binds.

**2. Split the over-merge.** Find identities holding 4+ ids from one provider namespace
whose only join evidence is a name, and split them. 9,117 candidates today. Writes
`split_of` rows to the overlay. Must precede 3 and 4: a year backfilled onto an
over-merged identity is a wrong year, and a range derived from one is a fiction.

**3. Backfill the agreed years.** One offline pass over ScreenScraper, MobyGames and
TheGamesDB through the index. Writes a year **only where every mirror agrees** — 8,086
records at today's measurement, and more once step 2 removes the false disagreements. No
network, no AI, no spend.

**4. The year range.** `year_lo` / `year_hi`, fed by the disagreements that survive step
2, and a range comparison in `matchgate.game_era()` so "this record cannot be 2013"
becomes usable where a single year does not exist.

## What is deliberately not in scope

Researching the ~79,000 undated records with no local evidence. That is IGDB's whole
undated tail, it cannot be done offline, and the measurement says it would not repay the
spend — the 7,149 that actually misdirect a match are the only ones that change an
outcome, and 1,935 of those are already reachable for free.

Related: `2026-07-24-match-verification-all-tiers-design.md`,
`2026-08-04-match-map-design.md`.
