#!/usr/bin/env python3
"""Every script, module and doc page the documentation names must actually exist.

THE DOCS DESCRIBED SOFTWARE THAT DID NOT EXIST, for a month, across eight pages.

`sync.py` — the one-way "publish catalog" mirror — was retired on 2026-07-21 and the
file deleted. On 2026-08-24, `docs/SYNC.md` still documented `python3 sync.py
--reconcile` from top to bottom; `scripts/update.sh` still ran it after every rebuild
behind `|| echo "sync FAILED"`; `docs/AUTH.md`, `docs/HANDOFF.md`, the games-sync skill
and `requirements.txt` all still pointed at it. `--reconcile` was not even a flag its
replacement accepts.

That is not a typo, it is a class: prose is never executed, so a name in it can outlive
the thing it names indefinitely. Anything shaped like a path CAN be checked, and this
checks all of it — every `.py`, every `.sh`, and every cross-reference between doc
pages.

What this canNOT check is the harder half of the same problem: a doc that names only
real files and still describes behaviour the code does not have (a flag that was never
implemented, a provider that was only ever planned, a feature called "next" years after
it shipped). Those need a human with `grep`. This catches the mechanical half for free
and keeps it caught.
"""
import os
import re
import sys

PASS = []


def check(label, cond):
    PASS.append(bool(cond))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Where a bare `foo.py` in prose is allowed to live.
PKG_DIRS = ("ludodex", "server", "scripts", "tests", "web/scripts", "")

# Pages that are DELIBERATELY historical: dated plans and specs, kept as a record of
# what was decided and why. They name files that were proposed, renamed or deleted, and
# rewriting them would destroy the record. Excluded on purpose, not overlooked.
HISTORICAL = ("docs/superpowers/",)

# Names that appear in prose as illustrations rather than as references.
IGNORE_PY = {
    "sync.py",            # named ONLY to say it is gone; the guard for that is case 4
    "config.py",          # written bare constantly, meaning ludodex/config.py
    "app.py", "ai.py",    # written bare, meaning server/*
}


def _docs():
    out = []
    for base, _dirs, files in os.walk(ROOT):
        rel_base = os.path.relpath(base, ROOT)
        if rel_base.startswith((".git", ".venv", "web/node_modules", "media")):
            continue
        for f in files:
            if not f.endswith(".md"):
                continue
            rel = os.path.normpath(os.path.join(rel_base, f))
            if any(rel.startswith(h) for h in HISTORICAL):
                continue
            out.append(rel)
    return sorted(out)


def _text(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def _exists_py(name):
    return any(os.path.exists(os.path.join(ROOT, d, name)) for d in PKG_DIRS)


def main():
    docs = _docs()
    print()
    print("1. there are docs to check")
    check("found %d markdown pages (excluding %s)" % (len(docs), ", ".join(HISTORICAL)),
          len(docs) >= 10)

    print()
    print("2. every .py a doc names exists somewhere in the tree")
    bad = []
    seen = 0
    for rel in docs:
        for name in sorted(set(re.findall(r"\b([a-z_][a-z0-9_]*\.py)\b", _text(rel)))):
            if name in IGNORE_PY:
                continue
            seen += 1
            if not _exists_py(name):
                bad.append("%s -> %s" % (rel, name))
    check("the docs name some scripts: %d" % seen, seen >= 30)
    check("all %d resolve (missing: %s)" % (seen, bad or "none"), not bad)

    print()
    print("3. every path-shaped reference a doc names exists")
    # `scripts/update.sh`, `ludodex/dbsync.py`, `docker/entrypoint.sh` — written with a
    # directory, so there is no ambiguity about what was meant.
    bad = []
    seen = 0
    for rel in docs:
        for path in sorted(set(re.findall(
                r"\b((?:ludodex|server|scripts|docker|tests|web)/[\w./-]+\.(?:py|sh|ps1|mjs|ts|tsx|json|lock|txt))\b",
                _text(rel)))):
            if "*" in path or "<" in path:
                continue
            seen += 1
            if not os.path.exists(os.path.join(ROOT, path)):
                bad.append("%s -> %s" % (rel, path))
    check("the docs name some paths: %d" % seen, seen >= 10)
    check("all %d resolve (missing: %s)" % (seen, bad or "none"), not bad)

    print()
    print("4. the retired sync.py is only ever named as retired")
    # It is legitimate to say "sync.py is gone" — that is what a reader who remembers it
    # needs. It is NOT legitimate to tell anyone to run it. So: no doc may show it being
    # invoked.
    bad = []
    for rel in docs:
        for line in _text(rel).splitlines():
            if re.search(r"python3?\s+(?:\S*/)?sync\.py\b", line) and "retired" not in line:
                bad.append("%s: %s" % (rel, line.strip()[:70]))
    check("no doc invokes sync.py (%s)" % (bad or "none"), not bad)
    check("sync.py really is deleted",
          not _exists_py("sync.py"))

    print()
    print("5. every doc-to-doc link resolves")
    bad = []
    seen = 0
    for rel in docs:
        here = os.path.dirname(os.path.join(ROOT, rel))
        for target in re.findall(r"\]\(([^)#]+\.md)(?:#[^)]*)?\)", _text(rel)):
            if target.startswith(("http://", "https://")):
                continue
            seen += 1
            if not os.path.exists(os.path.normpath(os.path.join(here, target))):
                bad.append("%s -> %s" % (rel, target))
    check("the docs link to each other: %d links" % seen, seen >= 5)
    check("all %d resolve (broken: %s)" % (seen, bad or "none"), not bad)

    print()
    print("6. skills point at this repo's real layout")
    # Every command in all five skills used to begin `~/game-ownership/...` — the
    # pre-rename project directory, which has not existed since the repo became ludodex.
    # The skills are shipped IN the repo, so they must be written against the repo.
    skills = [os.path.join("skills", d, "SKILL.md")
              for d in sorted(os.listdir(os.path.join(ROOT, "skills")))
              if os.path.isdir(os.path.join(ROOT, "skills", d))]
    check("found the skills: %d" % len(skills), len(skills) >= 5)
    # Match the PATH form only. "game-ownership library" is a fine phrase in a
    # description; `~/game-ownership/update.sh` is a command that cannot run.
    stale = [s for s in skills
             if re.search(r"[~/\w.]*game-ownership/", _text(s))]
    check("none reference the pre-rename directory (%s)" % (stale or "none"), not stale)
    # ...and the entry points they DO name must be the real ones.
    missing = []
    for sk in skills:
        for path in set(re.findall(r"(scripts/[\w.-]+\.sh|ludodex/[\w.-]+\.py)", _text(sk))):
            if not os.path.exists(os.path.join(ROOT, path)):
                missing.append("%s -> %s" % (sk, path))
    check("every script a skill invokes exists (missing: %s)" % (missing or "none"),
          not missing)

    print()
    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
