#!/usr/bin/env python3
"""File-operations engine for ludodex — reorganize / rename / repair ROM sets on
any device (local or over SSH), driven by declarative *profiles* or by AI.

Design
------
A **profile** is a declarative description of a target on-disk layout, expressed as
a path template over a shared variable vocabulary (the same fields the crawl
pipeline extracts from filenames): ``{system}``, ``{game}``, ``{region}``,
``{disc}``, ``{ext}``, ``{filename}`` (verbatim original name), plus the raw
parenthetical tags. Reorganizing = read the current layout, compute each file's
variables, and re-emit under the target template.

Everything is **plan → diff → apply**, and every applied operation is written to
an **undo journal** so a run is fully reversible. Nothing mutates disk until
``apply`` is called with an approved plan.

Files that belong together (a ``.cue`` and its ``.bin`` tracks, a ``.gdi`` and its
tracks, an ``.m3u`` and its discs) are grouped into a **unit** and always move
together, keeping their member filenames so internal references stay valid.

The engine is AI-free. The AI layer (``server/ai.py``) only *produces* profile /
plan objects — which are fed straight into ``plan()`` here — so the deterministic
path and the AI path share one auditable executor.
"""
import json
import os
import posixpath
import shlex
import sqlite3
import subprocess
import sys
import time

DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("LUDODEX_DATA", DIR)
sys.path.insert(0, DIR)
import config          # noqa: E402
import devices         # noqa: E402  transport (_device/_ssh/_run/_wrap_pw/...)
import romtags         # noqa: E402  parse_name()
import titlenorm       # noqa: E402  norm()

DB = os.path.join(DATA, "file-profiles.sqlite")

# Container/sidecar extensions whose *contents* reference sibling member files.
CONTAINER_EXTS = {"cue", "gdi", "m3u", "ccd"}
# Multi-disc member extensions that an .m3u can enumerate.
DISC_EXTS = {"chd", "cue", "iso", "cso", "rvz", "gdi", "pbp", "bin", "img", "ccd",
             "m3u"}
ARCHIVE_EXTS = {"zip", "7z", "rar"}

# Recognized *game-file* extensions. The engine only relocates files whose type it
# recognizes as a ROM / disc image / archive (plus any file in a sidecar unit), so
# it never scatters box art, save states, gamelists, or the internal asset files of
# extracted/ported games (.fsb/.wem/.bnk/.suprx/…). A profile can set all_files=1
# to override and touch everything.
try:
    from romtags import ROM_EXTS as _ROM_EXTS   # canonical per-system rom exts
except Exception:                                # pragma: no cover
    _ROM_EXTS = set()
GAME_EXTS = (set(_ROM_EXTS) | DISC_EXTS | ARCHIVE_EXTS | CONTAINER_EXTS | {
    # common cartridge/handheld formats, in case romtags' list is narrow
    "nes", "fds", "sfc", "smc", "gb", "gbc", "gba", "nds", "n64", "z64", "v64",
    "gg", "sms", "md", "gen", "smd", "32x", "pce", "ngp", "ngc", "ws", "wsc",
    "vb", "a26", "a78", "lnx", "col", "int", "vec", "d64", "t64", "wad", "gcm",
    "gcz", "wbfs", "rvz", "xci", "nsp", "3ds", "cia", "vpk", "pkg", "dsk", "adf",
    "ipf", "st", "rom", "j64", "jag", "min", "sg", "sc", "cdi"})


def _is_game(rel):
    return _ext(rel) in GAME_EXTS

# The draggable variable vocabulary surfaced in the profile-builder UI. Each entry
# is (token, label, description, example). {system} comes from the folder scope;
# the rest come from romtags.parse_name(); {filename}/{ext} are literal.
VARIABLES = [
    ("system",   "System",   "Console / platform folder", "snes"),
    ("game",     "Game",     "Cleaned title (before the first tag)", "Chrono Trigger"),
    ("filename", "Filename", "The original file name, verbatim (safest — no rename)",
     "Chrono Trigger (USA).sfc"),
    ("ext",      "Ext",      "File extension, lower-cased", "sfc"),
    ("region",   "Region",   "Region code(s) from the name", "USA"),
    ("languages", "Languages", "Language code(s) from the name", "En,Fr,De"),
    ("disc",     "Disc",     "Disc/disk number for multi-disc games", "Disc 1"),
    ("version",  "Version",  "Version tag", "v1.1"),
    ("revision", "Revision", "Revision tag", "Rev A"),
    ("tags",     "Tags",     "All original parenthetical tags, verbatim", "(USA) (Rev 1)"),
]
VAR_TOKENS = {v[0] for v in VARIABLES}


# ---------------------------------------------------------------- builtin profiles
# Starter library covering the common ROM-set layouts. `target` is a path template
# relative to the chosen root. `system_scope` default is per-operation, not baked in.
BUILTIN_PROFILES = [
    {"id": "builtin:flat", "name": "Flat per system", "builtin": True,
     "description": "Every game file directly under its system folder — "
                    "system/Game (Region).ext. The most common, frontend-friendly layout.",
     "target": "{system}/{filename}", "m3u": False, "prune_empty": True,
     "archive_policy": "keep"},
    {"id": "builtin:folder", "name": "Folder per game", "builtin": True,
     "description": "Each game gets its own subfolder — system/Game/Game (Region).ext. "
                    "Best for multi-disc / multi-file sets that must stay grouped.",
     "target": "{system}/{game}/{filename}", "m3u": False, "prune_empty": True,
     "archive_policy": "keep"},
    {"id": "builtin:disc", "name": "Disc games + .m3u", "builtin": True,
     "description": "Folder per game AND auto-build/repair an .m3u playlist for "
                    "multi-disc titles so the emulator sees one entry.",
     "target": "{system}/{game}/{filename}", "m3u": True, "prune_empty": True,
     "archive_policy": "keep"},
    {"id": "builtin:canonical", "name": "Canonical rename (flat)", "builtin": True,
     "description": "Flat per system, renaming single-file games to a clean "
                    "'Game (Region).ext'. Sidecar sets keep their names to stay valid.",
     "target": "{system}/{game}{tags}.{ext}", "m3u": False, "prune_empty": True,
     "archive_policy": "keep", "rename": True},
]
BUILTIN_BY_ID = {p["id"]: p for p in BUILTIN_PROFILES}


# ------------------------------------------------------------------- profile store
def _con():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS profiles(
        id INTEGER PRIMARY KEY, name TEXT, description TEXT, target TEXT,
        m3u INTEGER DEFAULT 0, prune_empty INTEGER DEFAULT 1,
        archive_policy TEXT DEFAULT 'keep', rename INTEGER DEFAULT 0,
        all_files INTEGER DEFAULT 0, source TEXT DEFAULT 'user', created REAL)""")
    # A runbook = one `runs` row + its ordered `run_ops` steps. Steps are persisted
    # as 'pending' BEFORE execution so an interrupted run has a precise resume point.
    con.execute("""CREATE TABLE IF NOT EXISTS runs(
        id INTEGER PRIMARY KEY, device_id INTEGER, root TEXT, profile TEXT,
        scope TEXT, system TEXT, n_ops INTEGER, status TEXT, created REAL,
        started REAL, finished REAL, note TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS run_ops(
        id INTEGER PRIMARY KEY, run_id INTEGER, seq INTEGER, op TEXT,
        src TEXT, dst TEXT, payload TEXT, status TEXT, error TEXT)""")
    for tbl, col, decl in (("runs", "scope", "TEXT"), ("runs", "system", "TEXT"),
                           ("runs", "started", "REAL"), ("runs", "finished", "REAL"),
                           ("profiles", "all_files", "INTEGER DEFAULT 0"),
                           ("run_ops", "payload", "TEXT")):
        cols = {r[1] for r in con.execute("PRAGMA table_info(%s)" % tbl)}
        if col not in cols:
            con.execute("ALTER TABLE %s ADD COLUMN %s %s" % (tbl, col, decl))
    return con


def _profile_row(r):
    return {"id": "user:%d" % r["id"], "name": r["name"],
            "description": r["description"] or "", "target": r["target"],
            "m3u": bool(r["m3u"]), "prune_empty": bool(r["prune_empty"]),
            "archive_policy": r["archive_policy"] or "keep",
            "rename": bool(r["rename"]), "all_files": bool(r["all_files"]),
            "builtin": False, "source": r["source"] or "user"}


def profiles_list():
    """Builtin starter profiles + user/AI-authored ones."""
    con = _con()
    rows = [_profile_row(r) for r in
            con.execute("SELECT * FROM profiles ORDER BY created DESC")]
    con.close()
    return BUILTIN_PROFILES + rows


def profile_get(pid):
    if pid in BUILTIN_BY_ID:
        return BUILTIN_BY_ID[pid]
    if isinstance(pid, str) and pid.startswith("user:"):
        con = _con()
        r = con.execute("SELECT * FROM profiles WHERE id=?",
                        (int(pid.split(":", 1)[1]),)).fetchone()
        con.close()
        return _profile_row(r) if r else None
    return None


def profile_set(p):
    """Create/update a user profile. `p` may carry id='user:N' to update; builtins
    are read-only (saving one clones it to a user profile). Returns the saved id."""
    target = (p.get("target") or "").strip()
    if not target:
        raise ValueError("profile needs a target template")
    _validate_template(target)
    con = _con()
    pid = p.get("id")
    fields = (p.get("name") or "Untitled", p.get("description") or "", target,
              1 if p.get("m3u") else 0, 0 if p.get("prune_empty") is False else 1,
              p.get("archive_policy") or "keep", 1 if p.get("rename") else 0,
              1 if p.get("all_files") else 0, p.get("source") or "user")
    if isinstance(pid, str) and pid.startswith("user:"):
        rid = int(pid.split(":", 1)[1])
        con.execute("UPDATE profiles SET name=?,description=?,target=?,m3u=?,"
                    "prune_empty=?,archive_policy=?,rename=?,all_files=?,source=? "
                    "WHERE id=?", fields + (rid,))
    else:
        cur = con.execute("INSERT INTO profiles(name,description,target,m3u,"
                          "prune_empty,archive_policy,rename,all_files,source,created)"
                          " VALUES(?,?,?,?,?,?,?,?,?,?)", fields + (time.time(),))
        rid = cur.lastrowid
    con.commit()
    con.close()
    return "user:%d" % rid


def profile_rm(pid):
    if isinstance(pid, str) and pid.startswith("user:"):
        con = _con()
        con.execute("DELETE FROM profiles WHERE id=?", (int(pid.split(":", 1)[1]),))
        con.commit()
        con.close()


def _validate_template(t):
    """Reject unknown <<tokens>>/{tokens} so a typo can't silently drop files."""
    import re
    for m in re.findall(r"\{(\w+)\}", t):
        if m not in VAR_TOKENS:
            raise ValueError("unknown variable {%s}; known: %s"
                             % (m, ", ".join(sorted(VAR_TOKENS))))


# ---------------------------------------------------------------- transport (fs)
class _Fs:
    """Uniform file access for a device — local (this server) or remote over SSH.
    Only the handful of operations the engine needs; all paths POSIX, relative to
    the operation root unless noted."""

    def __init__(self, device_id):
        self.dev = None
        if device_id:
            self.dev = devices._device(int(device_id))
            if not self.dev:
                raise RuntimeError("no such device %r" % device_id)
            if self.dev.get("transport") == "smb":
                raise RuntimeError("SMB transport is not supported for file ops "
                                   "(no cifs-utils); use SSH")
            if self.dev.get("auth") == "password":
                self.dev = dict(self.dev,
                                password=devices._dev_password(self.dev["id"]))
        self.local = not self.dev or self.dev.get("transport") == "local"

    # ---- run a bash script on the target, return CompletedProcess-like ----
    def run(self, script, timeout=600):
        if self.local:
            return devices._run(["bash", "-c", script], timeout=timeout)
        return devices._ssh(self.dev, script, timeout=timeout)

    def listing(self, root):
        """Every file under root as [{rel, size}] (POSIX rel paths)."""
        root = root.rstrip("/")
        cmd = "find %s -type f -printf '%%s\\t%%P\\n'" % shlex.quote(root)
        r = self.run(cmd, timeout=300)
        if r.returncode != 0:
            raise RuntimeError("listing failed: " +
                               (r.stderr or r.stdout or "")[:200])
        out = []
        for line in r.stdout.splitlines():
            try:
                size_s, rel = line.split("\t", 1)
            except ValueError:
                continue
            if rel:
                out.append({"rel": rel,
                            "size": int(size_s) if size_s.isdigit() else 0})
        return out

    def read_text(self, abspath, limit=65536):
        """Small text file (a .cue/.m3u) as str; '' if unreadable."""
        r = self.run("head -c %d %s" % (limit, shlex.quote(abspath)), timeout=60)
        return r.stdout if r.returncode == 0 else ""

    def read_texts(self, abspaths, limit=65536):
        """Read many small files in ONE round-trip (critical over SSH). Returns a
        list of texts aligned to `abspaths` ('' for any that couldn't be read)."""
        if not abspaths:
            return []
        import re
        parts = []
        for i, p in enumerate(abspaths):
            parts.append("echo '@@@F%d'" % i)
            parts.append("head -c %d %s 2>/dev/null" % (limit, shlex.quote(p)))
        parts.append("echo '@@@F%d'" % len(abspaths))       # trailing sentinel
        r = self.run("\n".join(parts), timeout=600)
        out = [""] * len(abspaths)
        cur, buf = None, []
        for line in (r.stdout or "").split("\n"):
            m = re.match(r"^@@@F(\d+)$", line)
            if m:
                if cur is not None and cur < len(out):
                    out[cur] = "\n".join(buf)
                cur, buf = int(m.group(1)), []
            else:
                buf.append(line)
        return out

    def caps(self):
        """Which archive tools exist on the target."""
        r = self.run("for t in unzip 7z 7za zip; do command -v $t >/dev/null && "
                     "echo $t; done", timeout=30)
        return set((r.stdout or "").split())


def _abs(root, rel):
    return posixpath.join(root.rstrip("/"), rel)


# ------------------------------------------------------------------ unit grouping
def _ext(name):
    return name.rsplit(".", 1)[1].lower() if "." in name else ""


def _parse_container(text, container_rel):
    """Member rel-paths referenced by a .cue/.gdi/.m3u/.ccd, resolved relative to
    the container's directory and kept only if they look like filenames."""
    base = posixpath.dirname(container_rel)
    ext = _ext(container_rel)
    members = []
    import re
    if ext == "cue" or ext == "ccd":
        for m in re.findall(r'FILE\s+"([^"]+)"', text, re.I):
            members.append(m)
        for m in re.findall(r'FILE\s+(\S+)\s+BINARY', text, re.I):
            if m not in members:
                members.append(m.strip('"'))
    elif ext == "gdi":
        for ln in text.splitlines()[1:]:
            toks = ln.split()
            if toks:
                members.append(toks[-1].strip('"'))
    elif ext == "m3u":
        for ln in text.splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                members.append(ln)
    out = []
    for mem in members:
        mem = mem.replace("\\", "/").strip()
        if not mem or ":" in mem:
            continue
        out.append(posixpath.normpath(posixpath.join(base, mem)) if base else mem)
    return out


def _group_units(files, fs, root):
    """Collapse files into units. A container (.cue/.gdi/.m3u/.ccd) claims the
    member files it references; everything else is its own single-file unit. Each
    unit = {primary, members:[rel...], multi:bool}."""
    relset = {f["rel"] for f in files}
    claimed = set()
    units = []
    ordered = sorted(files, key=lambda x: x["rel"])
    # batch-read every container file in ONE round-trip, then group locally
    container_rels = [f["rel"] for f in ordered if _ext(f["rel"]) in CONTAINER_EXTS]
    texts = dict(zip(container_rels,
                     fs.read_texts([_abs(root, r) for r in container_rels])))
    # containers first, longest paths last so nested resolves deterministically
    for f in ordered:
        rel = f["rel"]
        if _ext(rel) in CONTAINER_EXTS and rel not in claimed:
            mems = [m for m in _parse_container(texts.get(rel, ""), rel)
                    if m in relset and m != rel]
            members = [rel] + [m for m in mems if m not in claimed]
            for m in members:
                claimed.add(m)
            units.append({"primary": rel, "members": members,
                          "multi": len(members) > 1})
    for f in files:
        if f["rel"] not in claimed:
            units.append({"primary": f["rel"], "members": [f["rel"]],
                          "multi": False})
    return units


# ------------------------------------------------------------------ variable emit
def _sanitize(seg):
    """Make one path segment filesystem-safe (segments are POSIX; only '/' + NUL
    are truly illegal, but trim trailing dots/spaces for portability)."""
    seg = seg.replace("/", "-").replace("\x00", "").strip()
    return seg.rstrip(". ") or seg


def _vars_for(primary_rel, scope, system_name):
    """Compute the variable dict for a unit from its primary member."""
    name = posixpath.basename(primary_rel)
    title, region, langs, ver, rev, disc, flags, raw = romtags.parse_name(name)
    if scope == "single_system":
        system = system_name or ""
    else:                                  # multi_system: first dir = system
        parts = primary_rel.split("/")
        system = parts[0] if len(parts) > 1 else (system_name or "")
    return {
        "system": _sanitize(system) or "Unsorted",
        "game": _sanitize(title) or _sanitize(name.rsplit(".", 1)[0]),
        "filename": name,
        "ext": _ext(name),
        "region": region, "languages": langs, "version": ver,
        "revision": rev, "disc": disc,
        "tags": (" " + raw) if raw else "",
    }


def _emit(template, vs):
    """Render a target template into a clean relative POSIX path."""
    out = template
    for k, v in vs.items():
        out = out.replace("{%s}" % k, str(v))
    # drop empty path segments left by absent tokens, collapse doubled slashes
    parts = [p for p in out.split("/") if p not in ("", ".")]
    return "/".join(parts)


# ------------------------------------------------------------------------- planner
def plan(device_id, root, profile, scope="multi_system", system=None, max_files=200000):
    """Compute the operations that would transform `root` into `profile`'s layout,
    WITHOUT touching disk. Returns
    {ops:[...], summary:{...}, warnings:[...], sample:[...]}.

    scope: 'multi_system' (root holds many system/ folders — first dir = system)
           or 'single_system' (root IS one system; pass its name in `system`)."""
    if isinstance(profile, str):
        profile = profile_get(profile)
    if not profile:
        raise RuntimeError("unknown profile")
    _validate_template(profile["target"])
    fs = _Fs(device_id)
    files = fs.listing(root)
    warnings = []
    if len(files) > max_files:
        warnings.append("listing capped at %d files (found %d) — narrow the path"
                        % (max_files, len(files)))
        files = files[:max_files]
    units = _group_units(files, fs, root)

    ops = []
    target_dirs = {}          # target dir -> list of (disc, dst_basename) for m3u
    src_dirs = set()
    renamed = 0
    skipped = 0
    all_files = bool(profile.get("all_files"))
    for u in units:
        # Only relocate recognized game files (or whole sidecar sets). Media, save
        # states, gamelists and extracted-game asset files are left untouched —
        # unless the profile explicitly opts into all_files.
        if not (all_files or u["multi"] or _is_game(u["primary"])):
            skipped += len(u["members"])
            continue
        vs = _vars_for(u["primary"], scope, system)
        rendered = _emit(profile["target"], vs)   # target path for the PRIMARY
        tgt_dir = posixpath.dirname(rendered)
        primary_dst_base = posixpath.basename(rendered)
        for mem in u["members"]:
            src_dirs.add(posixpath.dirname(mem))
            mem_name = posixpath.basename(mem)
            # single-file units may be renamed by the template; multi-file units
            # keep every member's original name so internal refs stay valid.
            if mem == u["primary"] and not u["multi"] and profile.get("rename"):
                dst_base = primary_dst_base
            else:
                dst_base = mem_name
            dst = posixpath.join(tgt_dir, dst_base) if tgt_dir else dst_base
            if dst != mem:
                ops.append({"op": "move", "src": mem, "dst": dst})
                if dst_base != mem_name:
                    renamed += 1
        target_dirs.setdefault(tgt_dir, []).append((vs.get("disc", ""),
                                                     u["primary"]))

    # multi-disc .m3u generation: any target dir that ends up with >1 disc member
    # and no existing .m3u gets one listing the disc files in order.
    if profile.get("m3u"):
        existing_m3u = {posixpath.dirname(f["rel"]) for f in files
                        if _ext(f["rel"]) == "m3u"}
        # recompute per-dir disc files from the planned destinations
        dest_by_dir = {}
        for o in ops:
            if o["op"] == "move":
                d = posixpath.dirname(o["dst"])
                dest_by_dir.setdefault(d, []).append(posixpath.basename(o["dst"]))
        for d, names in dest_by_dir.items():
            discs = sorted(n for n in names
                           if _ext(n) in ("chd", "cue", "iso", "cso", "rvz", "pbp"))
            if len(discs) > 1 and d not in existing_m3u:
                game = posixpath.basename(d) or "playlist"
                # strip a trailing "(Disc N)" from the m3u name
                import re
                gm = re.sub(r"\s*\(Dis[ck]\s*\d+\).*$", "", game).strip() or game
                m3u_rel = posixpath.join(d, gm + ".m3u")
                ops.append({"op": "write", "dst": m3u_rel,
                            "content": "\n".join(discs) + "\n"})

    # prune emptied source directories (deepest first), if the profile asks
    if profile.get("prune_empty"):
        moved_srcs = {o["src"] for o in ops if o["op"] == "move"}
        moved_from = {posixpath.dirname(s) for s in moved_srcs}
        moved_into = {posixpath.dirname(o["dst"]) for o in ops
                      if o["op"] in ("move", "write")}
        # any file we did NOT move (unchanged or skipped) keeps its dir alive
        kept_dirs = {posixpath.dirname(f["rel"]) for f in files
                     if f["rel"] not in moved_srcs}

        def _has_survivor(d):
            # something still lives in d if a destination or an untouched file is
            # d itself or nested under it
            pfx = d + "/"
            return any(x == d or x.startswith(pfx) for x in moved_into | kept_dirs)

        for d in sorted(moved_from - {""}, key=lambda x: -x.count("/")):
            if not _has_survivor(d):
                ops.append({"op": "rmdir", "path": d})

    n_move = sum(1 for o in ops if o["op"] == "move")
    if skipped:
        warnings.append("%d non-game file(s) left untouched (media/saves/assets) — "
                        "enable 'all files' in the profile to include them" % skipped)
    return {
        "ops": ops,
        "summary": {"files": len(files), "units": len(units), "moves": n_move,
                    "renames": renamed, "skipped": skipped,
                    "m3u": sum(1 for o in ops if o["op"] == "write"),
                    "prune": sum(1 for o in ops if o["op"] == "rmdir")},
        "warnings": warnings,
        "sample": [o for o in ops if o["op"] == "move"][:40],
    }


# ---------------------------------------------------------- runbook (persist/exec)
def _b64(s):
    import base64
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


def _sh_move(root, src, dst):
    a, b = shlex.quote(_abs(root, src)), shlex.quote(_abs(root, dst))
    d = shlex.quote(posixpath.dirname(_abs(root, dst)))
    return "mkdir -p %s && mv -n %s %s" % (d, a, b)


def _op_script(root, op, src, dst, payload):
    """Bash for one op. `op`/`src`/`dst`/`payload` are the stored run_ops fields
    (for rmdir/delete the target path is in `src`; for write it's `dst`+payload)."""
    if op == "move":
        # if the move already happened (src gone, dst present) treat as success —
        # makes re-running an interrupted runbook idempotent.
        return ("if [ ! -e %s ] && [ -e %s ]; then true; else %s; fi"
                % (shlex.quote(_abs(root, src)), shlex.quote(_abs(root, dst)),
                   _sh_move(root, src, dst)))
    if op == "write":
        path = shlex.quote(_abs(root, dst))
        d = shlex.quote(posixpath.dirname(_abs(root, dst)))
        return ("mkdir -p %s && printf %%s %s | base64 -d > %s"
                % (d, shlex.quote(_b64(payload or "")), path))
    if op == "rmdir":
        return "rmdir %s 2>/dev/null || true" % shlex.quote(_abs(root, src))
    if op == "delete":
        return "rm -f %s" % shlex.quote(_abs(root, src))
    return "true"


def create_runbook(device_id, root, profile, ops, scope="multi_system",
                   system=None, note=""):
    """Persist an approved plan as a runbook (all steps 'pending'); nothing runs
    yet. Returns the run_id. This is the durable, resumable, renderable record."""
    pname = (profile_get(profile) or {}).get("name", str(profile)) \
        if isinstance(profile, str) else (profile or {}).get("name", "custom")
    # safety: no path may escape the root
    for o in ops:
        for p in (o.get("src"), o.get("dst"), o.get("path")):
            if p and (p.startswith("/") or ".." in p.split("/")):
                raise RuntimeError("unsafe path in plan: %r" % p)
    con = _con()
    cur = con.execute("INSERT INTO runs(device_id,root,profile,scope,system,n_ops,"
                      "status,created,note) VALUES(?,?,?,?,?,?,?,?,?)",
                      (device_id or 0, root, pname, scope, system or "", len(ops),
                       "planned", time.time(), note))
    run_id = cur.lastrowid
    for i, o in enumerate(ops):
        con.execute("INSERT INTO run_ops(run_id,seq,op,src,dst,payload,status,error)"
                    " VALUES(?,?,?,?,?,?, 'pending','')",
                    (run_id, i, o["op"], o.get("src") or o.get("path"),
                     o.get("dst"), o.get("content")))
    con.commit()
    con.close()
    return run_id


def execute_runbook(run_id, batch=200, should_stop=None):
    """Run every still-pending step of a runbook, committing status after each
    batch so a crash leaves a precise resume point (call again to resume). Steps
    already 'ok' are skipped; moves that already happened self-heal to 'ok'.
    `should_stop` is an optional callable checked between batches — when it returns
    true the run pauses (remaining steps stay 'pending', status becomes 'paused').
    Returns {run_id, ran, ok, failed, remaining, status}."""
    con = _con()
    run = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not run:
        con.close()
        raise RuntimeError("no such run")
    root, device_id = run["root"], run["device_id"] or 0
    con.execute("UPDATE runs SET status='running', started=COALESCE(started,?) "
                "WHERE id=?", (time.time(), run_id))
    con.commit()
    fs = _Fs(device_id)
    ran = ok = failed = 0
    stopped = False
    try:
        while True:
            if should_stop and should_stop():
                stopped = True
                break
            pend = list(con.execute(
                "SELECT * FROM run_ops WHERE run_id=? AND status='pending' "
                "ORDER BY seq LIMIT ?", (run_id, batch)))
            if not pend:
                break
            lines = []
            for o in pend:
                body = _op_script(root, o["op"], o["src"], o["dst"], o["payload"])
                lines.append("%s && echo 'OK %d' || echo \"ERR %d rc=$?\""
                             % (body, o["seq"], o["seq"]))
            r = fs.run("\n".join(lines), timeout=1800)
            seen = {}
            for ln in (r.stdout or "").splitlines():
                if ln.startswith("OK "):
                    seen[int(ln[3:].split()[0])] = ("ok", "")
                elif ln.startswith("ERR "):
                    parts = ln[4:].split(None, 1)
                    seen[int(parts[0])] = ("failed",
                                           (parts[1] if len(parts) > 1 else "")
                                           + " " + (r.stderr or "")[-120:])
            for o in pend:
                st, err = seen.get(o["seq"], ("failed", "no result (interrupted?)"))
                con.execute("UPDATE run_ops SET status=?, error=? WHERE id=?",
                            (st, err.strip(), o["id"]))
                ran += 1
                ok += st == "ok"
                failed += st != "ok"
            con.commit()
    finally:
        remaining = con.execute("SELECT COUNT(*) FROM run_ops WHERE run_id=? AND "
                                "status='pending'", (run_id,)).fetchone()[0]
        nfail = con.execute("SELECT COUNT(*) FROM run_ops WHERE run_id=? AND "
                            "status='failed'", (run_id,)).fetchone()[0]
        status = ("paused" if remaining and stopped else
                  "running" if remaining else
                  "partial" if nfail else "done")
        con.execute("UPDATE runs SET status=?, finished=? WHERE id=?",
                    (status, None if remaining else time.time(), run_id))
        con.commit()
        con.close()
    return {"run_id": run_id, "ran": ran, "ok": ok, "failed": failed,
            "remaining": remaining, "status": status}


def apply(device_id, root, profile, ops, scope="multi_system", system=None,
          note=""):
    """Convenience: persist a runbook and execute it in one call (used by the CLI
    and simple flows). Returns execute_runbook's result plus run_id."""
    run_id = create_runbook(device_id, root, profile, ops, scope, system, note)
    res = execute_runbook(run_id)
    return res


def runbook(run_id):
    """Renderable runbook: the run header, a per-op list, status counts, and a
    friendly grouped-by-destination-folder view for the UI."""
    con = _con()
    run = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not run:
        con.close()
        raise RuntimeError("no such run")
    rops = list(con.execute("SELECT * FROM run_ops WHERE run_id=? ORDER BY seq",
                            (run_id,)))
    con.close()
    counts = {}
    for o in rops:
        counts[o["status"]] = counts.get(o["status"], 0) + 1
    groups = {}
    for o in rops:
        if o["op"] == "move":
            g = posixpath.dirname(o["dst"]) or "(root)"
            groups.setdefault(g, {"dir": g, "moves": []})["moves"].append(
                {"seq": o["seq"], "from": o["src"],
                 "to": posixpath.basename(o["dst"]), "status": o["status"]})
    return {
        "run": {"id": run["id"], "device_id": run["device_id"], "root": run["root"],
                "profile": run["profile"], "scope": run["scope"],
                "system": run["system"], "n_ops": run["n_ops"],
                "status": run["status"], "created": run["created"],
                "started": run["started"], "finished": run["finished"],
                "note": run["note"]},
        "counts": counts,
        "steps": [{"seq": o["seq"], "op": o["op"], "src": o["src"],
                   "dst": o["dst"], "status": o["status"], "error": o["error"]}
                  for o in rops],
        "groups": sorted(groups.values(), key=lambda g: g["dir"]),
    }


# ------------------------------------------------------------------- troubleshoot
_DIAGNOSES = [
    ("No such file or directory",
     "Source file is missing — it may have been moved or renamed outside ludodex, "
     "or a previous step already handled it.",
     "Re-scan the folder and re-plan; if the game is already in place this step "
     "can be skipped."),
    ("Directory not empty",
     "Tried to remove a folder that still contains files.",
     "Safe to ignore — the folder is kept because something still lives in it."),
    ("Permission denied",
     "The account used for this device can't write here.",
     "Check ownership/permissions of the path, or connect as a user that can write."),
    ("Read-only file system",
     "The target path is mounted read-only.",
     "Remount read-write, or point the operation at a writable location."),
    ("No space left on device",
     "The device ran out of disk space mid-run.",
     "Free space (moves within one filesystem shouldn't need it — a cross-device "
     "move copies first). Then resume the runbook."),
    ("Text file busy",
     "A file was in use (e.g. an emulator had it open).",
     "Close anything using the ROMs and resume the runbook."),
]


def troubleshoot(run_id):
    """Explain what went wrong on a partial/failed runbook and how to recover.
    Returns {status, failed, resumable, findings:[{seq,op,path,error,cause,fix}]}."""
    con = _con()
    run = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not run:
        con.close()
        raise RuntimeError("no such run")
    fails = list(con.execute("SELECT * FROM run_ops WHERE run_id=? AND status='failed'"
                             " ORDER BY seq", (run_id,)))
    remaining = con.execute("SELECT COUNT(*) FROM run_ops WHERE run_id=? AND "
                            "status='pending'", (run_id,)).fetchone()[0]
    con.close()
    findings = []
    for o in fails:
        err = o["error"] or ""
        cause = fix = None
        for needle, c, f in _DIAGNOSES:
            if needle.lower() in err.lower():
                cause, fix = c, f
                break
        if not cause:
            cause = "Unrecognized error."
            fix = "Inspect the path manually; the step can be retried after fixing."
        findings.append({"seq": o["seq"], "op": o["op"],
                         "path": o["dst"] or o["src"], "error": err.strip(),
                         "cause": cause, "fix": fix})
    return {"status": run["status"], "failed": len(fails), "remaining": remaining,
            "resumable": bool(remaining or fails),
            "findings": findings}


def undo(run_id):
    """Reverse a runbook: move files back, delete generated files, recreate pruned
    dirs — in reverse order, only for steps that actually succeeded. The inverse is
    itself run as a fresh runbook so it too is journaled and resumable."""
    con = _con()
    run = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not run:
        con.close()
        raise RuntimeError("no such run")
    rops = list(con.execute("SELECT * FROM run_ops WHERE run_id=? AND status='ok' "
                            "ORDER BY seq DESC", (run_id,)))
    con.close()
    root, device_id = run["root"], run["device_id"] or 0
    inverse = []
    emptied = set()
    for o in rops:
        if o["op"] == "move":
            inverse.append({"op": "move", "src": o["dst"], "dst": o["src"]})
            emptied.add(posixpath.dirname(o["dst"]))
        elif o["op"] == "write":
            inverse.append({"op": "delete", "path": o["dst"]})
            emptied.add(posixpath.dirname(o["dst"]))
        elif o["op"] == "rmdir":
            inverse.append({"op": "mkdir", "path": o["src"]})
    for d in sorted(emptied - {""}, key=lambda x: -x.count("/")):
        inverse.append({"op": "rmdir", "path": d})
    # mkdir isn't a stored op type; fold it into rmdir's inverse via a direct run
    fs = _Fs(device_id)
    lines = []
    for i, o in enumerate(inverse):
        if o["op"] == "move":
            body = _sh_move(root, o["src"], o["dst"])
        elif o["op"] == "delete":
            body = "rm -f %s" % shlex.quote(_abs(root, o["path"]))
        elif o["op"] == "mkdir":
            body = "mkdir -p %s" % shlex.quote(_abs(root, o["path"]))
        elif o["op"] == "rmdir":
            body = "rmdir %s 2>/dev/null || true" % shlex.quote(_abs(root, o["path"]))
        else:
            body = "true"
        lines.append("%s && echo 'OK %d' || echo 'ERR %d'" % (body, i, i))
    r = fs.run("\n".join(lines) or "true", timeout=1800)
    ok = sum(1 for ln in (r.stdout or "").splitlines() if ln.startswith("OK "))
    con = _con()
    con.execute("UPDATE runs SET status='undone' WHERE id=?", (run_id,))
    con.commit()
    con.close()
    return {"run_id": run_id, "reverted": ok, "of": len(inverse)}


def history(limit=50):
    con = _con()
    rows = [dict(r) for r in con.execute(
        "SELECT r.id,r.device_id,r.root,r.profile,r.scope,r.system,r.n_ops,"
        "r.status,r.created,r.started,r.finished,r.note,"
        "(SELECT COUNT(*) FROM run_ops o WHERE o.run_id=r.id AND o.status='ok') "
        "AS done,"
        "(SELECT COUNT(*) FROM run_ops o WHERE o.run_id=r.id AND o.status='failed')"
        " AS failed,"
        "(SELECT COUNT(*) FROM run_ops o WHERE o.run_id=r.id AND o.status='pending')"
        " AS pending "
        "FROM runs r ORDER BY r.created DESC LIMIT ?", (limit,))]
    con.close()
    return rows


def run_delete(run_id):
    con = _con()
    con.execute("DELETE FROM run_ops WHERE run_id=?", (run_id,))
    con.execute("DELETE FROM runs WHERE id=?", (run_id,))
    con.commit()
    con.close()


# ------------------------------------------------------------------------- detect
def detect(device_id, root, scope="multi_system", system=None, sample=400):
    """Guess which layout the path currently uses + collect a listing sample the
    AI/profile-inference can reason over. Returns {current, systems, sample, counts}."""
    fs = _Fs(device_id)
    files = fs.listing(root)[:sample * 4]
    rels = [f["rel"] for f in files]
    # infer current shape from directory depth distribution
    depths = [r.count("/") for r in rels]
    per_game = sum(1 for r in rels if r.count("/") >= 2) / (len(rels) or 1)
    if scope == "single_system":
        current = "folder" if per_game > 0.5 else "flat"
    else:
        current = "folder" if per_game > 0.5 else "flat"
    systems = sorted({r.split("/")[0] for r in rels if "/" in r})[:60]
    exts = {}
    for r in rels:
        exts[_ext(r)] = exts.get(_ext(r), 0) + 1
    return {
        "current": current,
        "systems": systems,
        "counts": {"files": len(files),
                   "top_exts": sorted(exts.items(), key=lambda x: -x[1])[:15]},
        "sample": rels[:sample],
    }


# --------------------------------------------------------------------------- CLI
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "profiles"
    if cmd == "profiles":
        print(json.dumps(profiles_list(), indent=2))
    elif cmd == "detect":
        dev = int(sys.argv[2]) if sys.argv[2] not in ("0", "local") else 0
        print(json.dumps(detect(dev, sys.argv[3]), indent=2, default=str))
    elif cmd == "plan":
        dev = int(sys.argv[2]) if sys.argv[2] not in ("0", "local") else 0
        pl = plan(dev, sys.argv[3], sys.argv[4],
                  scope=(sys.argv[5] if len(sys.argv) > 5 else "multi_system"),
                  system=(sys.argv[6] if len(sys.argv) > 6 else None))
        print(json.dumps({"summary": pl["summary"], "warnings": pl["warnings"],
                          "sample": pl["sample"]}, indent=2))
    elif cmd == "history":
        print(json.dumps(history(), indent=2, default=str))
