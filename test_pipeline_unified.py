#!/usr/bin/env python3
"""There is ONE media pipeline, and every onramp runs it.

datbird, 2026-08-03: "these should be unified functions… no matter what onramp/offramp
we're taking to enrich, correct metadata/media the pipelines remain consistent, which
means any fixes, improvements and changes made easily apply to all onramps/offramps."

An audit of the eight entry points found what that ask predicted. Only ONE ran the full
chain:

    _sync_worker             match
    _wand_fill_media         fetch -> stamp -> select
    _scoped_media_reconcile  match -> fetch -> stamp -> select -> measure -> prune -> ai
    _ingest_new_members      fetch -> measure -> stamp -> select
    _media_worker            stamp -> select -> measure
    media_fetch_provider     fetch -> stamp -> select

The wand's own media step never measured and never pruned — it chose art it had never
looked at and could leave a blank placeholder as the pick. Member ingest never pruned.
The "Fetch from…" endpoint did neither. Each was a hand-copied subset, which is exactly
why the same defect had to be fixed three and four times over and still missed a path.

Now: `_enrich_media` = match + fetch + `_media_finish`, and `_media_finish` is
stamp -> select -> measure -> prune -> re-select. Every onramp calls one of those two.

These are source-level checks on purpose. The failure mode is "a code path that never
learned about a function", which no unit test of that function can detect — the function
works perfectly; nobody called it.
"""
import os
import re
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


SRC = open(os.path.join(DIR, "server", "app.py"), encoding="utf-8").read()
LINES = SRC.split("\n")

# Raw pipeline primitives. An onramp calling these directly is re-implementing the chain.
RAW = {
    "fetch": r"_pull_media_sources\(|fetch_igdb\(|fetch_screenscraper\(|"
             r"fetch_steamgriddb_targets\(|_pull_ss_media\(",
    "stamp": r"_backfill_game_key\(",
    "select": r"media_choose\.select\(",
    "prune": r"_prune_blank_media\(",
    "ai": r"_ai_adjudicate_game\(",
}

# Every user- or job-initiated way to enrich media. If one is added, it belongs here.
ONRAMPS = ["_wand_fill_media", "_scoped_media_reconcile", "_ingest_new_members",
           "media_fetch_provider", "_reconcile_media_now", "_fetch_media_for"]

# Allowed to touch primitives directly, each for a stated reason.
EXEMPT = {
    # the pipeline itself, and its shared tail
    "_enrich_media", "_media_finish",
    # the per-game fetch primitive the pipeline drives
    "_pull_media_sources",
    # bulk repo hydration: no fetch, no enrichment — it downloads what is already chosen
    "_media_worker",
    # the import: fetches via subprocesses for streamed whole-library progress, then
    # runs the shared tail (_media_finish) so it cannot end anywhere the pipeline would
    # not leave a game
    "_sync_worker",
    # serve-time repair of a single dead reference
    "media_asset",
    # the blank detector itself
    "_prune_blank_media",
}


def body_of(name):
    pat = re.compile(r"^(async )?def %s\(" % re.escape(name))
    start = next((i for i, l in enumerate(LINES) if pat.match(l)), None)
    if start is None:
        return None
    for j in range(start + 1, len(LINES)):
        if LINES[j].startswith(("def ", "async def ", "@app.")):
            return "\n".join(LINES[start:j])
    return "\n".join(LINES[start:])


def main():
    print("1. the pipeline and its tail exist, and the tail is shared")
    check("_enrich_media is defined", body_of("_enrich_media") is not None)
    check("_media_finish is defined", body_of("_media_finish") is not None)
    check("_enrich_media delegates its tail rather than repeating it",
          "_media_finish(" in (body_of("_enrich_media") or ""))

    print("2. the tail runs the steps in the order every bug this session established")
    fin = body_of("_media_finish") or ""
    order = []
    for label, pat in (("stamp", RAW["stamp"]), ("select", RAW["select"]),
                       ("measure", r"_asset_local_path\("), ("prune", RAW["prune"])):
        m = re.search(pat, fin)
        if m:
            order.append((m.start(), label))
    seq = [l for _, l in sorted(order)]
    check("stamp -> select -> measure -> prune: %s" % seq,
          seq == ["stamp", "select", "measure", "prune"])
    # and a re-select AFTER prune, which is the step whose absence showed wrong art
    check("a re-select follows prune",
          fin.rindex("media_choose.select(") > fin.index("_prune_blank_media("))

    print("3. no onramp re-implements the chain")
    for name in ONRAMPS:
        b = body_of(name)
        check("%s exists" % name, b is not None)
        uses_pipeline = "_enrich_media(" in b or "_media_finish(" in b
        check("%s runs the pipeline" % name, uses_pipeline)
        raw_hits = [k for k, pat in RAW.items()
                    if k != "fetch" and re.search(pat, b)]
        check("%s does not re-run pipeline steps itself: %s" % (name, raw_hits or "none"),
              not raw_hits)

    print("4. the import shares the tail even though it fetches differently")
    sw = body_of("_sync_worker") or ""
    check("_sync_worker matches providers", "_match_providers(" in sw)
    check("_sync_worker runs the shared tail", "_media_finish(" in sw)

    print("5. every exemption is deliberate, not accidental")
    # Anything else in app.py calling prune or select directly is a new divergence.
    offenders = []
    for i, line in enumerate(LINES):
        if not re.search(RAW["prune"] + "|" + RAW["stamp"], line):
            continue
        if line.lstrip().startswith("#"):
            continue
        owner = None
        for j in range(i, -1, -1):
            m = re.match(r"^(async )?def (\w+)\(", LINES[j])
            if m:
                owner = m.group(2)
                break
        if owner and owner not in EXEMPT:
            offenders.append("%s:%d" % (owner, i + 1))
    check("no unexpected caller of stamp/prune: %s" % (offenders or "none"), not offenders)

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
