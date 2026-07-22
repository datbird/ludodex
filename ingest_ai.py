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

  python3 ingest_ai.py                      # suspect titles only, every ROM index
  python3 ingest_ai.py --mgr 3              # just one library manager's index
  python3 ingest_ai.py --all --limit 2000   # re-read everything, bounded
  python3 ingest_ai.py --estimate           # count targets + projected cost, no calls

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
DATA = os.environ.get("LUDODEX_DATA", DIR)
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "server"))
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
            rows = con.execute(
                "SELECT system, game, MIN(relpath) FROM roms WHERE ext IN (%s) "
                "AND game<>'' GROUP BY system, game" % ph, exts)
            for system, game, path in rows:
                k = (system, game)
                if k in seen or k in have:
                    continue
                if not take_all and not _suspect(game):
                    continue
                seen.add(k)
                out.append({"system": system, "game": game, "path": path or game})
        except sqlite3.OperationalError:
            pass
        con.close()
        if limit and len(out) >= limit:
            break
    return out[:limit] if limit else out


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
    total = len(items)
    if not total:
        return {"targets": 0, "hinted": 0, "batches": 0}
    model = ai.model_for_area("ingest") or ""
    hinted = done = batches = 0
    for i in range(0, total, BATCH):
        chunk = items[i:i + BATCH]
        try:
            res = ai.identify_roms(chunk)
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
    return {"targets": total, "hinted": hinted, "batches": batches, "done": done}


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
        est = _estimate(len(targets(a.mgr, a.all, a.limit)))
        print("targets=%(targets)d calls=%(calls)d in=%(in_tokens)d out=%(out_tokens)d "
              "provider=%(provider)s model=%(model)s" % est)
        print("cost_usd=%s" % ("%.2f" % est["cost_usd"] if est["cost_usd"] is not None
                               else "unknown (model not priced)"))
        return 0
    r = run(a.mgr, a.all, a.limit, a.min_confidence, a.progress)
    print("ingest_ai: %d target(s), %d hint(s) recorded in %d batch(es)"
          % (r["targets"], r.get("hinted", 0), r.get("batches", 0)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
