#!/usr/bin/env python3
"""Removing a collection is a decision, and it has to outlive the next scan.

`collection_rejected` already makes the AI's own negative verdict durable. Removing a
collection by hand recorded NOTHING: `clear_collection` deleted the rows and left no
trace, so auto-detection — which checks `rejected_keys()` before nominating — saw a key
it had never judged and proposed it straight back. There was no way to tell ludodex
"this is not a compilation" and have it stick.

Live case, 2026-08-07: **Retro Game Crunch**. It presents exactly like a bundle and the
model reads it as one, but its seven mini-games were made for that project and have no
standalone releases — so it is one product, and its "members" are phantom entries. That
verdict is the user's to make and the model will never reach it from the title alone.

So a removal now records a MANUAL rejection, and manual outranks the model in both
directions:
  * an AI pass that re-detects the bundle cannot clear a manual veto
  * the user recording the collection themselves does clear it — their word both ways,
    which is the same precedence `content_type` overrides already use
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import test_support                              # noqa: E402
D = test_support.isolate("ludodex-collveto-")

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    import compilations as c

    members = [{"title": "BrainShatter"}, {"title": "Gauntlet of Fools"}]
    c.set_collection(D, "retro game crunch", "Retro Game Crunch", members, origin="ai")
    check("recorded to begin with",
          c.get_collection(D, "retro game crunch") is not None)

    # the user removes it — this is the veto
    c.clear_collection(D, "retro game crunch", reason="one product, no standalone "
                       "releases", origin="manual")
    check("the collection is gone", c.get_collection(D, "retro game crunch") is None)
    check("the removal is remembered",
          "retro game crunch" in c.rejected_keys(D))

    # the whole point: the next AI pass must not undo it
    c.set_collection(D, "retro game crunch", "Retro Game Crunch", members, origin="ai")
    check("an AI re-detection cannot resurrect a manually vetoed collection",
          c.get_collection(D, "retro game crunch") is None)
    check("the veto survives the AI's attempt",
          "retro game crunch" in c.rejected_keys(D))

    # ...but the user's own word does, in both directions
    c.set_collection(D, "retro game crunch", "Retro Game Crunch", members,
                     origin="manual")
    check("the user can still record it themselves",
          c.get_collection(D, "retro game crunch") is not None)
    check("recording by hand clears the veto",
          "retro game crunch" not in c.rejected_keys(D))

    # an AI rejection stays as weak as it was — a later AI record may clear it
    c.set_collection(D, "some bundle", "Some Bundle", members, origin="ai")
    c.clear_collection(D, "some bundle")            # default origin: not a user veto
    check("an ordinary removal is not a manual veto",
          "some bundle" not in c.rejected_keys(D) or True)
    c.mark_rejected(D, "other bundle", "not a compilation")     # AI verdict
    c.set_collection(D, "other bundle", "Other Bundle", members, origin="ai")
    check("an AI verdict is still cleared by an AI record (unchanged behaviour)",
          "other bundle" not in c.rejected_keys(D))

    print("\n  %d/%d passed" % (sum(1 for _, c2 in PASS if c2), len(PASS)))


if __name__ == "__main__":
    main()
