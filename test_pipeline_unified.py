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
    # `_parallel_match` fans `_match_providers` across a pool — same matcher, one
    # concurrency policy shared with the standalone job.
    check("_sync_worker matches providers",
          "_match_providers(" in sw or "_parallel_match(" in sw)
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

    print("6. METADATA: one identity-consequence chain, same rule")
    # An identity is not one fact, it is four that must move together: games.game_key
    # (or neutral art goes invisible, §11.9), the metadata_links row, the canonical title,
    # and the provider-record ATTRIBUTES. `_member_identity` wrote the key and the link and
    # stopped, so a game identified as a collection member got no genres, no developer and
    # no publisher, while the same game pinned by hand got all of them — two different
    # meanings of "identified" depending on which door you came through.
    ident = body_of("_apply_identity") or ""
    check("_apply_identity is defined", bool(ident))
    for piece, pat in (("moves games.game_key", r"UPDATE games SET game_key"),
                       ("writes the provider link", r"INSERT INTO metadata_links"),
                       ("fills provider attributes", r"_fill_provider_attrs\(")):
        check("_apply_identity %s" % piece, bool(re.search(pat, ident)))

    IDENT_ONRAMPS = ["_member_identity", "aimeta_pin", "resolve_per_entry_identity"]
    for name in IDENT_ONRAMPS:
        b = body_of(name)
        check("%s exists" % name, b is not None)
        check("%s routes through _apply_identity" % name, "_apply_identity(" in b)
        # and does not hand-write the consequences itself
        raw = [lbl for lbl, pat in
               (("game_key", r"UPDATE games SET game_key"),
                ("links", r"INSERT INTO metadata_links"))
               if re.search(pat, b)]
        check("%s does not re-write identity consequences: %s" % (name, raw or "none"),
              not raw)

    print("7. the UI asks the SERVER which asset is primary, it does not re-derive it")
    # `used` is computed server-side with the serve resolver's own rule (own-console art
    # over neutral, §11.4/§11.9). Every surface that shows "the" asset for a kind must
    # read it. The detail hero had its own `pinned ?? of[0]` rule and therefore rendered
    # a different logo than the picker labelled #1 USED — the panel and the page
    # disagreeing about the same asset, on the same screen.
    app = open(os.path.join(DIR, "web", "src", "App.tsx"), encoding="utf-8").read()
    i = app.index("const pickKind = (kind: string)")
    body = app[i:i + 420]
    check("pickKind consults `used`", "a.used" in body)
    check("and no longer falls straight to the first array element",
          "a.pinned) ?? of[0]" not in body)
    # the picker's own ordering must agree
    check("the media picker ranks by `used` too", "a.used ? -2" in app)

    print("8. ONE serve rule — the endpoint, the checker and the UI all defer to it")
    # This one is personal: check_invariants.py had its OWN copy of the serve query, so
    # fixing the resolver left the checker asserting the old behaviour. The checker whose
    # whole purpose is catching duplicated rules had duplicated a rule.
    mc = open(os.path.join(DIR, "media_choose.py"), encoding="utf-8").read()
    check("media_choose.serve_pick exists", "def serve_pick(" in mc)
    ma = body_of("media_asset") or ""
    check("the serve endpoint calls it", "media_choose.serve_pick(" in ma)
    check("and no longer inlines the query",
          "OR (COALESCE(system,'')='' AND game_key=?)" not in ma)
    ci = open(os.path.join(DIR, "check_invariants.py"), encoding="utf-8").read()
    check("the invariant checker calls it too", "media_choose.serve_pick(" in ci)
    check("and does not restate the query",
          "ORDER BY (norm_key=? AND COALESCE(system,'')=?) DESC" not in ci)

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
