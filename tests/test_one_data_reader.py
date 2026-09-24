#!/usr/bin/env python3
"""Exactly one Python file reads LUDODEX_DATA from the environment: ludodex/paths.py.

About forty modules used to compute the data directory themselves, and they did not
agree: most used `os.environ.get("LUDODEX_DATA", default)`, which takes an EMPTY value
as a real path, while a few used `or`, which falls back, and one read it at call time
instead of import time. Every module now takes `paths.DATA` (directly, or through
`config.DATA`, which is `paths.DATA`).

This fails the moment a second reader appears anywhere outside tests/ (tests set the
variable for the code under test, which is their job). Shell scripts and the Docker
entrypoint are not Python and are not checked.

It also proves the one reader behaves: set and non-empty wins, empty or unset falls
back to the repo root, and the server, the AI module, config and the pipeline modules
all resolve to the same directory.
"""
import ast
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                      # noqa: E402
DATA = test_support.isolate("ludodex-onedata-")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "tests", "dist"}
ALLOWED = {os.path.join("ludodex", "paths.py")}
VAR = "LUDODEX_DATA"

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def _is_environ(node):
    """`os.environ` or a bare `environ`."""
    return ((isinstance(node, ast.Attribute) and node.attr == "environ")
            or (isinstance(node, ast.Name) and node.id == "environ"))


def env_reads(tree):
    """Line numbers where LUDODEX_DATA is READ from the environment: environ.get(...),
    environ[...] in a load context, os.getenv(...), environ.setdefault(...), and
    `"LUDODEX_DATA" in os.environ`."""
    hits = []

    def names_var(n):
        return isinstance(n, ast.Constant) and n.value == VAR

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            args = node.args[:1]
            if (isinstance(f, ast.Attribute) and f.attr in ("get", "setdefault", "pop")
                    and _is_environ(f.value) and args and names_var(args[0])):
                hits.append(node.lineno)
            elif (((isinstance(f, ast.Attribute) and f.attr == "getenv")
                   or (isinstance(f, ast.Name) and f.id == "getenv"))
                  and args and names_var(args[0])):
                hits.append(node.lineno)
        elif (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load)
              and _is_environ(node.value) and names_var(node.slice)):
            hits.append(node.lineno)
        elif (isinstance(node, ast.Compare) and names_var(node.left)
              and any(_is_environ(c) for c in node.comparators)):
            hits.append(node.lineno)
    return hits


def main():
    print("1. no Python file outside tests/ reads LUDODEX_DATA except ludodex/paths.py")
    offenders, scanned, allowed_hits = [], 0, 0
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, ROOT)
            with open(p, encoding="utf-8") as fh:
                try:
                    tree = ast.parse(fh.read(), p)
                except SyntaxError:
                    continue
            scanned += 1
            for line in env_reads(tree):
                if rel in ALLOWED:
                    allowed_hits += 1
                else:
                    offenders.append("%s:%d" % (rel, line))
    check("scanned the tree (%d Python files)" % scanned, scanned > 100)
    check("paths.py is found reading it (the scan works)", allowed_hits == 1)
    check("no other reader: %s" % (", ".join(offenders) or "none"), not offenders)

    print("2. the detector catches every shape of read")
    for src in ('import os\nx = os.environ.get("LUDODEX_DATA", "/x")\n',
                'import os\nx = os.environ["LUDODEX_DATA"]\n',
                'import os\nx = os.getenv("LUDODEX_DATA")\n',
                'from os import environ\nx = environ.get("LUDODEX_DATA") or "/x"\n',
                'import os\nx = "LUDODEX_DATA" in os.environ\n'):
        check("flags %r" % src.split("\n")[1], env_reads(ast.parse(src)))
    check("does not flag a write",
          not env_reads(ast.parse('import os\nos.environ["LUDODEX_DATA"] = "/x"\n')))

    print("3. the one reader: set wins, empty or unset falls back to the repo root")
    probe = ("import sys; sys.path.insert(0, %r); import paths; print(paths.DATA)"
             % os.path.join(ROOT, "ludodex"))

    def resolved(value):
        env = dict(os.environ)
        env.pop(VAR, None)
        if value is not None:
            env[VAR] = value
        return subprocess.run([sys.executable, "-c", probe], env=env,
                              capture_output=True, text=True).stdout.strip()
    check("set -> that directory", resolved(DATA) == DATA)
    check("unset -> the repo root", resolved(None) == ROOT)
    check("empty -> the repo root, not the current directory", resolved("") == ROOT)

    print("4. the server, the AI module, config and pipeline modules agree")
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "ludodex"))
    from server import app as srv
    from server import ai
    import config
    import overrides
    import publish
    import media_choose
    import backups
    for name, mod in (("server.app", srv), ("server.ai", ai), ("config", config),
                      ("overrides", overrides), ("publish", publish),
                      ("media_choose", media_choose), ("backups", backups)):
        check("%s.DATA is the isolated dir" % name, mod.DATA == DATA)

    print("\n%d/%d passed" % (sum(1 for _, ok in PASS if ok), len(PASS)))


if __name__ == "__main__":
    main()
