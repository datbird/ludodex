#!/usr/bin/env python3
"""A machine must never overwrite a value a person chose.

`overrides` is where the user's corrections live. Every write went through one
unconditional upsert with no idea who was writing, so the Heavy wand's attribute
consensus and the IGDB-vs-ScreenScraper adjudicator silently replaced hand-set values.
There is no undo and no record: the review page shows the machine's answer as though the
user had picked it.

`origin` cannot answer "who wrote this", because it names the SOURCE of the value, not
the actor. A user who picks IGDB's release year in the UI stores origin='igdb', which is
exactly what the adjudicator stores when it decides the same thing on its own. So the
actor is recorded separately: `set_by` is 'user' for anything a person asked for and
'auto' for anything a pass decided, and an 'auto' write over a 'user' row is refused.

Rows written before that column existed are read as 'user' when their origin is manual
(a hand-typed value, protect it) and 'auto' otherwise, which is the best the old data
supports.

Offline. No network.
"""
import os
import re
import sqlite3
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-override-actor-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import overrides                                               # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def val(nk, kind):
    return (overrides.overrides_for(nk).get(kind) or {}).get("value")


def main():
    print("a person's override outranks a machine's")

    # ---- hand-typed value vs the wand ---------------------------------------- #
    overrides.set_override("sonic mania", "genres", "Platformer")
    wrote = overrides.set_override("sonic mania", "genres", "Shooter",
                                   origin="ai-consensus", by="auto")
    check("an automatic pass cannot overwrite a hand-set value", wrote is False)
    check("and the hand-set value is still there", val("sonic mania", "genres") == "Platformer")

    # ---- the user stays in charge of their own value -------------------------- #
    wrote = overrides.set_override("sonic mania", "genres", "Puzzle")
    check("the user can still change their mind", wrote is True)
    check("and the new value sticks", val("sonic mania", "genres") == "Puzzle")

    # ---- a deliberate PROVIDER pick is still a person's choice ---------------- #
    # origin names where the value came from, not who chose it. Picking IGDB's year in
    # the UI stores origin='igdb', the same origin the adjudicator writes on its own.
    overrides.set_override("celeste", "release_year", "2018", origin="igdb")
    wrote = overrides.set_override("celeste", "release_year", "2019",
                                   origin="screenscraper", by="auto")
    check("an automatic pass cannot overturn a deliberate provider pick", wrote is False)
    check("and the picked value is still there", val("celeste", "release_year") == "2018")

    # ---- machine over machine is fine ---------------------------------------- #
    overrides.set_override("hades", "genres", "Action", origin="igdb", by="auto")
    wrote = overrides.set_override("hades", "genres", "Roguelike",
                                   origin="ai-consensus", by="auto")
    check("one pass may correct another pass", wrote is True)
    check("and the newer machine value is stored", val("hades", "genres") == "Roguelike")

    # ---- rows written before the column existed ------------------------------ #
    con = sqlite3.connect(os.path.join(DATA, "attr-overrides.sqlite"))
    con.execute("INSERT INTO overrides(norm_key,kind,value,origin,created) "
                "VALUES('doom','genres','Shooter','manual',1.0)")
    con.execute("INSERT INTO overrides(norm_key,kind,value,origin,created) "
                "VALUES('quake','genres','Shooter','igdb',1.0)")
    con.commit()
    con.close()
    check("a legacy hand-typed row is protected",
          overrides.set_override("doom", "genres", "Puzzle", origin="ai-consensus",
                                 by="auto") is False)
    check("a legacy provider-sourced row stays correctable",
          overrides.set_override("quake", "genres", "Action", origin="ai-consensus",
                                 by="auto") is True)

    # ---- the two AI writers actually say so ---------------------------------- #
    # Behavioural coverage stops at the module boundary here: reaching these lines needs
    # a paid provider. What can be checked offline is that neither of them writes as a
    # user, which is the whole defect.
    app = open(os.path.join(DIR, "server", "app.py"), encoding="utf-8").read()
    calls = []
    for m in re.finditer(r"overrides\.set_override\(", app):
        i, depth = m.end(), 1                     # walk to the matching close paren
        while i < len(app) and depth:
            depth += {"(": 1, ")": -1}.get(app[i], 0)
            i += 1
        calls.append(app[m.start():i])
    auto = [c for c in calls if "ai-consensus" in c or "origin=prov" in c]
    check("both AI attribute writers were found", len(auto) == 2)
    check("and both write as an automatic pass",
          all('by="auto"' in c or "by='auto'" in c for c in auto))

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
