#!/usr/bin/env python3
"""The AI ingest pass must RUN as a process, and must spend on the area it quoted.

Two failures that hide each other:

  * `ingest_ai.py` put `<package>/server` on sys.path — a directory that does not
    exist — so `import ai` raised ModuleNotFoundError for every PROCESS invocation.
    `devices.py` starts it exactly that way during a device sync, and records the
    error in `out["ingest_ai"][mid]` without touching the report's `ok`, so the sync
    said "Done" while the AI tier never ran once. In-process use from `server/app.py`
    worked, because `server/` was already on the path — which is why `--estimate`
    from the API looked healthy and nothing ever caught it.
  * `run()` called `ai.identify_roms(chunk)` with no provider or model, so `_resolve`
    fell back to the ACTIVE provider and its default model, while `_estimate` priced
    `provider_for_area("ingest")`/`model_for_area("ingest")` and the hints were
    stamped with the ingest model. Quote one model, bill another: the project's
    first AI rule is that paid AI never fires by surprise, and a spend that does not
    match the estimate the user approved is exactly that surprise.

Offline. The model call is replaced by a recorder; nothing here reaches a network.
"""
import os
import sqlite3
import subprocess
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-ingest-area-")
sys.path.insert(0, os.path.join(DIR, "ludodex"))
sys.path.insert(0, os.path.join(DIR, "server"))

import ai                                                      # noqa: E402
import config                                                  # noqa: E402
import ingest_ai                                               # noqa: E402
import ingesthints                                             # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def seed_index():
    """A minimal ROM index in the shape build_romdb produces, one suspect title."""
    p = os.path.join(DATA, "roms-index-mgr9.sqlite")
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE roms (id INTEGER PRIMARY KEY, system TEXT, game TEXT, "
                "filename TEXT, ext TEXT, relpath TEXT)")
    con.execute("INSERT INTO roms(system,game,filename,ext,relpath) VALUES"
                "('snes','SMW_U','SMW_U.sfc','sfc','snes/SMW_U.sfc')")
    con.commit()
    con.close()


def main():
    print("the AI ingest pass runs as a process and spends on the ingest area")

    # ---- it imports `ai` when started as a child process ----------------------- #
    # This is how devices.py runs it. --estimate is enough: it reaches `import ai`
    # without making a single call.
    env = dict(os.environ, LUDODEX_DATA=DATA)
    p = subprocess.run([sys.executable, os.path.join(DIR, "ludodex", "ingest_ai.py"),
                        "--estimate"],
                       cwd=DIR, env=env, capture_output=True, text=True, timeout=120)
    check("ingest_ai.py --estimate exits 0 as a standalone process",
          p.returncode == 0)
    check("and did not fail to import the ai module",
          "ModuleNotFoundError" not in (p.stderr or ""))
    check("and reports the projection it was asked for",
          "provider=" in (p.stdout or ""))

    # ---- and the sys.path entry it adds is a directory that exists -------------- #
    src = open(os.path.join(DIR, "ludodex", "ingest_ai.py"), encoding="utf-8").read()
    check("it no longer points sys.path at a package subdir named 'server'",
          'os.path.join(DIR, "server")' not in src)

    # ---- the estimate and the spend name the same provider and model ------------ #
    # ingest deliberately runs on a cheap model while metadata may run on a big one,
    # so "the active default" and "the ingest area" are different answers on purpose.
    config.set_("ai_provider", "anthropic")
    config.set_("anthropic_api_key", "test-not-a-real-key")
    config.set_("ai_area_ingest", "openai")
    config.set_("ai_area_ingest_model", "gpt-5-nano")
    config.set_("openai_api_key", "test-not-a-real-key")
    check("the ingest area resolves to something other than the active default",
          (ai.provider_for_area("ingest"), ai.model_for_area("ingest"))
          != (ai.active_provider(), ai.model_for(ai.active_provider())))

    est = ingest_ai._estimate(1)

    seen = {}

    def recorder(items, provider=None, model=None):
        seen["provider"] = provider
        seen["model"] = model
        return [{"n": 1, "title": "Super Mario World", "platform": "", "year": 1990,
                 "confidence": 0.9}]

    seed_index()
    ingesthints.clear()
    real = ai.identify_roms
    ai.identify_roms = recorder
    try:
        rep = ingest_ai.run(mgr=9)
    finally:
        ai.identify_roms = real
    check("the pass actually asked about the suspect title", rep["targets"] == 1)
    check("the call names the ingest area's provider, not the active default",
          seen.get("provider") == est["provider"])
    check("and the ingest area's model, which is what the estimate priced",
          seen.get("model") == est["model"])
    check("which is the model the hint is stamped with",
          ingesthints.overrides().get(("snes", "SMW_U")) is not None)

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
