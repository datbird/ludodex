#!/usr/bin/env python3
"""LIVE conformance: what the model actually DECIDES (#35).

Every other test here proves the plumbing — that a verdict is parsed, applied, bounded,
recorded. None of them can fail when the model is wrong, and the model being wrong is
what produced every defect the user has actually seen: Police Quest I wearing Police
Quest II's cover, Beyond Oasis wearing The Story of Thor's, and 624 correct covers
deleted in a single pass because "rejects" was read as "runners-up".

So this one spends tokens on purpose. It is:

  * OPT-IN — skipped unless LUDODEX_LIVE_AI=1, so it can never surprise anyone with a
    bill. The spend guardrail is the project's first rule and a test suite is not
    exempt from it.
  * BOUNDED — one call per case, a handful of cases, the cheapest configured model. The
    whole run is a few cents.
  * DETERMINISTIC TO RUN, not deterministic in output. A model varies, so every
    assertion is about BEHAVIOUR that must hold regardless of wording: which index came
    back, whether a reject was raised. Never about the text of a reason.

Corpus is committed under tests/corpus/ with a manifest recording what each image
actually depicts — verified by eye, after a first attempt guessed four IGDB ids wrong
and produced a corpus where "Contra" was Godfall.

    LUDODEX_LIVE_AI=1 python3 test_vision_live.py
"""
import json
import os
import sys

import test_support

PASS = []
CORPUS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corpus")


def check(l, c):
    PASS.append(bool(c)); print("  %s   %s" % ("ok " if c else "FAIL", l))


def img(name):
    with open(os.path.join(CORPUS, name), "rb") as f:
        return ("image/jpeg", f.read())


def main():
    if os.environ.get("LUDODEX_LIVE_AI") != "1":
        sys.exit("SKIPPED: live AI test — set LUDODEX_LIVE_AI=1 to spend tokens")
    d = test_support.isolate("ludodex-vlive-")
    # The API key lives in the real config, and `isolate()` rightly refuses to point
    # LUDODEX_DATA at a live directory. Copying ONLY config.sqlite in gives the test
    # credentials while leaving every database it could damage out of reach — the
    # isolation guard exists because a test once wiped a 66,280-row media index, and
    # "but I need the key" is exactly the reasoning that would undo it.
    import shutil
    src = os.environ.get("LUDODEX_LIVE_CONFIG", "/data/config.sqlite")
    if os.path.exists(src):
        shutil.copy2(src, os.path.join(d, "config.sqlite"))
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from server import ai
    if not ai.area_available("art"):
        sys.exit("SKIPPED: no art provider configured")

    man = {m["file"]: m for m in json.load(open(os.path.join(CORPUS, "manifest.json")))}
    print("  corpus: %d images, %d bytes\n"
          % (len(man), sum(m["bytes"] for m in man.values())))

    def pick(title, files, **kw):
        return ai.pick_art(title, "cover", [img(f) for f in files],
                           provider=ai.provider_for_area("art"),
                           model=ai.model_for_area("art"), **kw)

    # F7 — the 624-cover lesson, and the most valuable assertion here. Correct art in,
    # NO reject out. A model that rejects good covers destroys a library.
    for f, title in (("aoe2_de.jpg", "Age of Empires II: Definitive Edition"),
                     ("across_the_obelisk.jpg", "Across the Obelisk"),
                     ("actraiser_renaissance.jpg", "Actraiser Renaissance")):
        r = pick(title, [f])
        check("F7 %-38s correct art is NOT rejected" % title[:38],
              not r.get("rejects") and r["index"] == 0)

    # F6 — a sibling's art. Contra: Hard Corps vs the Japanese Contra box.
    r = pick("Contra: Hard Corps",
             ["contra_nes.jpg", "contra_hard_corps_genesis.jpg"],
             year=1994, platform="genesis")
    check("F6 the Hard Corps box is chosen over its sibling's", r["index"] == 1)
    check("F6 if anything is rejected it is the sibling, never the right one",
          all(x["index"] == 0 for x in (r.get("rejects") or [])))

    # ERA — same title, 18 years apart. This is the RE4 failure.
    r = pick("Resident Evil 4", ["re4_2005.jpg", "re4_remake.jpg"],
             year=2023, platform="pc")
    check("ERA the 2023 remake is chosen for the 2023 release", r["index"] == 1)
    r = pick("Resident Evil 4", ["re4_remake.jpg", "re4_2005.jpg"],
             year=2005, platform="ps2")
    check("ERA the 2005 original is chosen for the 2005 release", r["index"] == 1)

    # WHOLE WRONG SET — a provider matched the wrong game, so every candidate belongs to
    # another title. Saying "none of these" must be reachable, or the Contra Force
    # failure repeats: six wrong covers in, the least-wrong one promoted.
    r = pick("Contra: Hard Corps", ["re4_2005.jpg", "aoe2_de.jpg"],
             year=1994, platform="genesis")
    check("SET a wholly wrong candidate set is refused, not ranked",
          r["index"] is None or bool(r.get("rejects")))

    # F5 — the owned regional title. Beyond Oasis is 'The Story of Thor' elsewhere; the
    # aliases are supplied exactly so a variant is KEPT but not featured.
    r = pick("Beyond Oasis", ["beyond_oasis.jpg"], year=1994, platform="genesis",
             aliases=["The Story of Thor"])
    check("F5 art bearing the owned name is accepted", r["index"] == 0)
    check("F5 and is not rejected as a different game", not r.get("rejects"))

    n_ok, n = sum(PASS), len(PASS)
    print("\n%d/%d passed" % (n_ok, n))
    if n_ok != n:
        sys.exit("FAILED: %d live conformance check(s)" % (n - n_ok))


main()
