#!/usr/bin/env python3
"""AI-assisted ingest ("lite") — read ROM paths, ask the model what the game really
is, and record the answer as an ingest hint.

This is the SUPPLEMENT to the algorithmic import, never a replacement for it. The
filename rules in romtags/build_romdb run first and produce a title for every ROM;
this pass looks at the ones those rules probably got wrong and asks a model to read
the path the way a person would.

Selection matters more than the model does. Sending all ~34k games costs real money
to be told "yes, 'Super Mario World' is Super Mario World". By default we send only
titles that look mangled (see `_suspect`), which is a few percent of a big library.
`--all` overrides that when you want the whole shelf re-read.

  python3 ludodex/ingest_ai.py                      # suspect titles only, every ROM index
  python3 ludodex/ingest_ai.py --mgr 3              # just one library manager's index
  python3 ludodex/ingest_ai.py --all --limit 2000   # re-read everything, bounded
  python3 ludodex/ingest_ai.py --estimate           # count targets + projected cost, no calls

Emits `PROG\\t<done>\\t<total>\\t<title>\\tingest` so the sync panel can show live
progress. Nothing here mutates the ROM index or the catalog — it only writes
ingest-hints.sqlite, which build_library applies on the next rebuild.
"""
import argparse
import glob
import os
import re
import sqlite3
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
# DIR is this package; DATA is the REPO ROOT above it, which is where local
# databases have always lived. Deriving DATA from DIR after the move would
# silently relocate an existing checkout's data.
DATA = os.environ.get("LUDODEX_DATA", os.path.dirname(DIR))
sys.path.insert(0, DIR)
# ai.py lives in the REPO's server/, a sibling of this package — never DIR/server,
# which does not exist. That typo only bit the PROCESS invocation (devices.py runs
# this as a child during a device sync): in-process callers already had server/ on
# sys.path, so `import ai` worked there and the breakage stayed invisible while the
# sync reported "Done" with the AI tier never having run.
sys.path.insert(0, os.path.join(os.path.dirname(DIR), "server"))
import config                                              # noqa: E402
import ingesthints                                         # noqa: E402
# romtags, NOT build_library: build_library is a SCRIPT — importing it runs a full
# catalog rebuild as a side effect. Both modules carry the same ROM_EXTS set.
import romtags                                             # noqa: E402

BATCH = 60            # paths per model call — big enough to amortize the system
                      # prompt, small enough that one bad response is cheap to lose
ROM_EXTS = sorted(romtags.ROM_EXTS)


def _rom_indexes(mgr=None):
    """Every ROM index, or one manager's. Mirrors build_library._rom_indexes so the
    two passes always see the same set of files."""
    out = []
    legacy = config.get("roms_index_db")
    if mgr is None and legacy and os.path.exists(legacy):
        out.append(legacy)
    pat = ("roms-index-mgr%d.sqlite" % mgr) if mgr is not None else "roms-index-mgr*.sqlite"
    out += sorted(glob.glob(os.path.join(DATA, pat)))
    return out


# A parsed title is "suspect" when it looks like something a rule mangled rather
# than a game a person would recognise. Each pattern is deliberately narrow — a
# false positive only costs a few tokens, but a false NEGATIVE means the mangled
# title silently becomes a catalog entry.
_VOWELS = re.compile(r"[aeiouy]", re.I)
_ROMAN = re.compile(r"^(?:[IVXLC]+)$", re.I)


def _suspect(title):
    t = (title or "").strip()
    if not t:
        return True
    if len(t) <= 3 and not _ROMAN.match(t):
        return True                      # "SMW", "FF7", "DK"
    if "_" in t:
        return True                      # "smw_u" — underscores are never a title
    words = [w for w in re.split(r"\s+", t) if w]
    if len(words) == 1 and re.search(r"[A-Za-z]", t) and re.search(r"\d", t):
        return True                      # "sonic2b", "FF7", "gradius3" — a lone word
                                         # mixing letters and digits is a filename,
                                         # not a title someone would write out
    if len(words) == 1 and len(t) >= 6 and not _VOWELS.search(t):
        return True                      # "SMBDLX"
    if len(words) == 1 and t.isupper() and len(t) > 3:
        return True                      # "GRADIUS3"
    if re.fullmatch(r"[\W\d]+", t):
        return True                      # digits/punctuation only
    return False


def targets(mgr=None, take_all=False, limit=0, skip_hinted=True):
    """[{system, game, path}] worth asking about, deduped by (system, game) — the
    catalog groups ROMs that way, so one answer covers every file of that game."""
    exts = list(ROM_EXTS)
    ph = ",".join("?" * len(exts))
    have = ingesthints.have_keys() if skip_hinted else set()
    seen, out = set(), []
    for db in _rom_indexes(mgr):
        con = sqlite3.connect(db)
        try:
            # The hash rides along, so identify_from_index() can answer for free. It is
            # only carried here — targets() stays read-only, because --estimate calls it
            # and an estimate that writes hints is not an estimate.
            #
            # MAX(h.crc) IS NOT THE ARBITRARY PICK IT RESEMBLES, and it is deliberately
            # not tied to the MIN(relpath) row. A group is one game's files — regions,
            # revisions, discs — and the index maps every one of their hashes to the SAME
            # identity, so any member is an equally valid witness to what the game is.
            # Taking the hash of the alphabetically-first file instead would throw away
            # a real answer whenever that particular file happens to be unhashed.
            has_h = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                "AND name='rom_hashes'").fetchone()
            sel = ("SELECT r.system, r.game, MIN(r.relpath), MAX(h.crc), MAX(h.sha1) "
                   "FROM roms r LEFT JOIN rom_hashes h ON h.relpath = r.relpath "
                   "WHERE r.ext IN (%s) AND r.game<>'' GROUP BY r.system, r.game" % ph
                   ) if has_h else (
                   "SELECT system, game, MIN(relpath), NULL, NULL FROM roms "
                   "WHERE ext IN (%s) AND game<>'' GROUP BY system, game" % ph)
            rows = con.execute(sel, exts)
            for system, game, path, crc, sha1 in rows:
                k = (system, game)
                if k in seen or k in have:
                    continue
                if not take_all and not _suspect(game):
                    continue
                seen.add(k)
                out.append({"system": system, "game": game, "path": path or game,
                            "crc": crc, "sha1": sha1})
        except sqlite3.OperationalError:
            pass
        con.close()
        if limit and len(out) >= limit:
            break
    return out[:limit] if limit else out


def identify_from_index(items, write=True):
    """Answer what the match index already knows, and return what is left to ask a model.

    -> (records_written, remaining_items)

    A CRC HIT ENDS THE QUESTION. The index holds 829,779 CRC and 769,759 SHA1 keys taken
    from ScreenScraper and the No-Intro/Redump DATs. Those dumps state what a file IS.
    Paying a model to read the same filename and guess is strictly worse: slower, priced
    per token, and less certain than the answer already sitting in a local table.

    THIS IS THE AI-SPEND RULE IN ITS MOST LITERAL FORM. The one thing that must never
    happen is a paid call firing when the answer was free. Every item this removes is a
    call not made — and the selection heuristic makes it likely, because `_suspect`
    targets mangled filenames, which is exactly what a dump-verified hash is for.

    Confidence is 1.0 and the model field reads `match-index`, so the provenance of a
    hint is never mistaken for something a model said.

    FAIL-OPEN. No index, no hashes, or a bad lookup all leave the item in the list to be
    asked about normally. The index not knowing a rom is the absence of an answer."""
    if not items:
        return 0, items
    try:
        import matchindex
        import romhash
    except Exception:                            # noqa: BLE001 — the index is optional
        return 0, items
    con = None
    written, rest = 0, []
    try:
        con = matchindex.connect()
        if not matchindex.has_index(con):
            return 0, items
        for it in items:
            got = {}
            if it.get("crc") or it.get("sha1"):
                try:
                    got = romhash.identify(con, crc=it.get("crc"),
                                           sha1=it.get("sha1"))
                except Exception:                # noqa: BLE001
                    got = {}
            name = (got or {}).get("_name")
            if not name:
                rest.append(it)                  # no answer here — ask normally
                continue
            if not write:
                written += 1
                continue
            try:
                # put() refuses a hint that asserts nothing. Dropping the item on that
                # refusal would remove it from the model's list AND leave no hint — the
                # game would simply be forgotten, which is worse than paying for it.
                if ingesthints.put(it["system"], it["game"], to_title=name,
                                   year=(got or {}).get("_year"), confidence=1.0,
                                   model="match-index", sample_path=it["path"]):
                    written += 1
                else:
                    rest.append(it)
            except Exception:                    # noqa: BLE001
                rest.append(it)
    except Exception:                            # noqa: BLE001
        return 0, items
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:                    # noqa: BLE001
                pass
    return written, rest


def _estimate(n):
    """Rough projected spend. Deliberately reported as a RANGE — real token counts
    depend on path length, and a single confident number here would be a lie."""
    import ai
    provider, model = ai.provider_for_area("ingest"), ai.model_for_area("ingest")
    calls = (n + BATCH - 1) // BATCH
    in_tok = calls * (700 + BATCH * 28)       # system prompt + ~28 tok/path
    out_tok = calls * (BATCH * 22)            # ~22 tok per JSON record
    try:
        cost = ai.cost_usd(in_tok, out_tok, provider, model)
    except Exception:                         # noqa: BLE001  (unpriced model)
        cost = None
    return {"targets": n, "calls": calls, "in_tokens": in_tok, "out_tokens": out_tok,
            "provider": provider, "model": model, "cost_usd": cost}


def run(mgr=None, take_all=False, limit=0, min_conf=0.5, progress=False):
    import ai
    items = targets(mgr, take_all, limit)
    # THE INDEX BEFORE THE MODEL. Anything a dump-verified hash already identifies is
    # answered here, for nothing, and never reaches a priced call.
    from_index, items = identify_from_index(items)
    total = len(items)
    if not total:
        return {"targets": 0, "hinted": from_index, "batches": 0,
                "from_index": from_index}
    # THE ESTIMATE AND THE SPEND MUST NAME THE SAME MODEL. _estimate() prices the
    # "ingest" area and the hints are stamped with it, but identify_roms() defaults to
    # the ACTIVE provider and its default model when it is handed neither — so a
    # library configured to ingest on a cheap model was quoted that price and billed
    # the default one. Resolving the area here is the whole fix.
    provider = ai.provider_for_area("ingest")
    model = ai.model_for_area("ingest") or ""
    hinted = done = batches = 0
    for i in range(0, total, BATCH):
        chunk = items[i:i + BATCH]
        try:
            res = ai.identify_roms(chunk, provider=provider, model=model or None)
        except Exception as e:                # noqa: BLE001
            # A budget cap trips here. Stop cleanly and keep what we already learned
            # rather than losing the whole pass to one refusal.
            print("ingest_ai: stopped after %d/%d — %s" % (done, total, e))
            break
        batches += 1
        for r in res:
            it = chunk[r["n"] - 1]
            if r["confidence"] < min_conf:
                continue
            # "same as parsed" is the expected majority answer — storing it would
            # bloat the hint table and change nothing on rebuild.
            same = (not r["title"] or r["title"].strip().lower() == it["game"].strip().lower())
            if same and not r["platform"] and not r["year"]:
                continue
            if ingesthints.put(it["system"], it["game"],
                               to_title="" if same else r["title"],
                               to_platform=r["platform"], year=r["year"],
                               confidence=r["confidence"], model=model,
                               sample_path=it["path"]):
                hinted += 1
        done = min(i + len(chunk), total)
        if progress:
            print("PROG\t%d\t%d\t%s\tingest" % (done, total, chunk[-1]["game"][:60]),
                  flush=True)
    return {"targets": total, "hinted": hinted + from_index, "batches": batches,
            "done": done, "from_index": from_index}


def main():
    ap = argparse.ArgumentParser(description="AI-assisted ROM ingest (lite)")
    ap.add_argument("--mgr", type=int, default=None,
                    help="only this library manager's ROM index")
    ap.add_argument("--all", action="store_true",
                    help="re-read every title, not just suspect ones")
    ap.add_argument("--limit", type=int, default=0, help="cap targets (0 = no cap)")
    ap.add_argument("--min-confidence", type=float, default=0.5,
                    help="discard hints below this confidence (default 0.5)")
    ap.add_argument("--estimate", action="store_true",
                    help="report targets + projected cost and exit without calling")
    ap.add_argument("--progress", action="store_true", help="emit PROG lines")
    a = ap.parse_args()
    if a.estimate:
        # write=False: an estimate that records hints is not an estimate. The count is
        # still reported, because a projection that ignores the free answers overstates
        # the bill and would push someone away from a run that costs almost nothing.
        _items = targets(a.mgr, a.all, a.limit)
        free, _rest = identify_from_index(_items, write=False)
        est = _estimate(len(_rest))
        print("free_from_index=%d (of %d found)" % (free, len(_items)))
        print("targets=%(targets)d calls=%(calls)d in=%(in_tokens)d out=%(out_tokens)d "
              "provider=%(provider)s model=%(model)s" % est)
        print("cost_usd=%s" % ("%.2f" % est["cost_usd"] if est["cost_usd"] is not None
                               else "unknown (model not priced)"))
        return 0
    r = run(a.mgr, a.all, a.limit, a.min_confidence, a.progress)
    print("ingest_ai: %d target(s), %d hint(s) recorded in %d batch(es); "
          "%d answered free by the match index"
          % (r["targets"], r.get("hinted", 0), r.get("batches", 0),
             r.get("from_index", 0)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
