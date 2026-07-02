#!/usr/bin/env python3
"""Devices + library managers — the "Connections › Devices" model.

A DEVICE is a machine that hosts game libraries (the Steam Deck, a Windows PC,
an Unraid box…). Each device has one or more LIBRARY MANAGERS (frontends):
RetroDECK/ES-DE, RetroBat, Playnite, LaunchBox, or a raw ROM folder — each with
its ROM and/or media path ON THAT DEVICE.

ludodex reaches a device over SSH (the transport this host has: ssh/rsync/scp)
and PULLS its library: it runs the same find→build_romdb the Deck does locally
(see update.sh), scp's the ROM index back, and rsyncs media into device-media/.
Auth modes: 'alias' (an entry in ~/.ssh/config — no creds stored), 'key'
(host+user+key file), or 'password' (host+user+password, via sshpass). Creds live
only in connections.sqlite (gitignored); ludodex never reads 1Password at runtime.

SMB is modelled but needs cifs-utils/smbclient on the server (not required for SSH).
"""
import os
import shlex
import sqlite3
import subprocess
import sys
import time

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)
import config

DB = os.path.join(DIR, "connections.sqlite")
MEDIA_DIR = os.path.join(DIR, "device-media")     # rsync'd device media lands here

# library-manager kind -> (label, provides_roms, provides_media)
LM_KINDS = {
    "retrodeck": ("RetroDECK / ES-DE", True, True),
    "esde":      ("EmulationStation-DE", True, True),
    "retrobat":  ("RetroBat", True, True),
    "playnite":  ("Playnite", True, False),
    "launchbox": ("LaunchBox", True, True),
    "roms":      ("ROM folder", True, False),
    # a plain storage bucket of game files/archives (e.g. an Unraid share) — not a
    # per-system ROM tree or a frontend; indexed as the catalog's `archive` source
    "archive":   ("Storage archive", False, False),
}

# game-file extensions to index for a storage archive (ROM/disc exts + containers)
try:
    from romtags import ROM_EXTS as _ROM_EXTS
except Exception:
    _ROM_EXTS = set()
ARCHIVE_EXTS = set(_ROM_EXTS) | {"zip", "7z", "rar", "iso", "chd", "cso", "rvz",
                                 "wbfs", "pbp", "cue", "gdi", "rom"}


def _con():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS devices(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, transport TEXT DEFAULT 'ssh',
        host TEXT, port INTEGER DEFAULT 22, username TEXT, auth TEXT DEFAULT 'alias',
        key_path TEXT, password TEXT, share TEXT, enabled INTEGER DEFAULT 1,
        created REAL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS library_managers(
        id INTEGER PRIMARY KEY AUTOINCREMENT, device_id INTEGER, kind TEXT,
        name TEXT, rom_path TEXT, media_path TEXT, enabled INTEGER DEFAULT 1)""")
    con.row_factory = sqlite3.Row
    return con


# --------------------------------------------------------------------------- #
#  CRUD
# --------------------------------------------------------------------------- #
def devices_list():
    con = _con()
    devs = [dict(r) for r in con.execute("SELECT * FROM devices ORDER BY name")]
    lms = [dict(r) for r in con.execute("SELECT * FROM library_managers")]
    con.close()
    by_dev = {}
    for lm in lms:
        lm["kind_label"] = LM_KINDS.get(lm["kind"], (lm["kind"], True, True))[0]
        by_dev.setdefault(lm["device_id"], []).append(lm)
    for d in devs:
        d.pop("password", None)                        # never expose the secret
        d["has_password"] = bool(_dev_password(d["id"]))
        d["managers"] = sorted(by_dev.get(d["id"], []), key=lambda x: x["name"] or "")
    return devs


def _dev_password(dev_id):
    con = _con()
    r = con.execute("SELECT password FROM devices WHERE id=?", (dev_id,)).fetchone()
    con.close()
    return (r["password"] if r else "") or ""


def _device(dev_id):
    con = _con()
    r = con.execute("SELECT * FROM devices WHERE id=?", (dev_id,)).fetchone()
    con.close()
    return dict(r) if r else None


def device_set(d):
    """Insert/update a device. d: name, transport, host, port, username, auth,
    key_path, password, share, id?. Empty password on update keeps the stored one."""
    con = _con()
    fields = ("name", "transport", "host", "port", "username", "auth",
              "key_path", "password", "share", "enabled")
    if d.get("id"):
        did = int(d["id"])
        cur = {k: v for k, v in (_device(did) or {}).items()}
        vals = {f: d.get(f, cur.get(f)) for f in fields}
        if not (d.get("password") or "").strip():      # blank = keep existing secret
            vals["password"] = cur.get("password")
        con.execute("UPDATE devices SET %s WHERE id=?" % ",".join("%s=?" % f for f in fields),
                    [vals[f] for f in fields] + [did])
    else:
        vals = {f: d.get(f) for f in fields}
        vals["enabled"] = 1 if d.get("enabled", True) else 0
        con.execute("INSERT INTO devices(name,transport,host,port,username,auth,"
                    "key_path,password,share,enabled,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    [vals[f] for f in fields] + [time.time()])
        did = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.commit()
    con.close()
    return did


def device_rm(dev_id):
    con = _con()
    con.execute("DELETE FROM devices WHERE id=?", (dev_id,))
    con.execute("DELETE FROM library_managers WHERE device_id=?", (dev_id,))
    con.commit()
    con.close()


def manager_set(m):
    con = _con()
    fields = ("device_id", "kind", "name", "rom_path", "media_path", "enabled")
    if m.get("id"):
        con.execute("UPDATE library_managers SET %s WHERE id=?"
                    % ",".join("%s=?" % f for f in fields),
                    [m.get(f) for f in fields] + [int(m["id"])])
        mid = int(m["id"])
    else:
        con.execute("INSERT INTO library_managers(device_id,kind,name,rom_path,"
                    "media_path,enabled) VALUES(?,?,?,?,?,?)",
                    [m.get("device_id"), m.get("kind"), m.get("name"),
                     m.get("rom_path"), m.get("media_path"),
                     1 if m.get("enabled", True) else 0])
        mid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.commit()
    con.close()
    return mid


def manager_rm(mid):
    con = _con()
    con.execute("DELETE FROM library_managers WHERE id=?", (mid,))
    con.commit()
    con.close()


# --------------------------------------------------------------------------- #
#  SSH transport (system ssh/rsync/scp; auth: alias / key / password)
# --------------------------------------------------------------------------- #
def _target(dev):
    return "%s@%s" % (dev["username"], dev["host"]) if dev.get("username") else dev["host"]


def _conn_opts(dev, port_flag):
    # ssh takes -p for port; scp takes -P (lowercase -p means "preserve times")
    opts = ["-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=accept-new",
            port_flag, str(dev.get("port") or 22)]
    if dev.get("auth") == "key" and dev.get("key_path"):
        opts += ["-i", os.path.expanduser(dev["key_path"])]
    if dev.get("auth") != "password":
        opts += ["-o", "BatchMode=yes"]                # alias/key: never prompt
    return opts


def _ssh_opts(dev):
    return _conn_opts(dev, "-p")


def _scp_opts(dev):
    return _conn_opts(dev, "-P")


def _wrap_pw(dev, argv):
    if dev.get("auth") == "password" and dev.get("password"):
        return ["sshpass", "-p", dev["password"]] + argv
    return argv


def _run(argv, timeout=120):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def _ssh(dev, remote_cmd, timeout=180):
    return _run(_wrap_pw(dev, ["ssh"] + _ssh_opts(dev) + [_target(dev), remote_cmd]),
                timeout=timeout)


def test_connection(dev):
    """Live reachability check. Returns {ok, detail}."""
    if dev.get("transport") == "local":
        return {"ok": True, "detail": "local host"}
    if dev.get("transport") == "smb":
        return {"ok": False, "detail": "SMB support needs cifs-utils/smbclient "
                "installed on the server — use SSH, or ask to add SMB tooling."}
    if not dev.get("host"):
        return {"ok": False, "detail": "no host set"}
    try:
        r = _ssh(dev, "echo ludodex-ok", timeout=20)
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"ok": False, "detail": str(e)[:140]}
    if r.returncode == 0 and "ludodex-ok" in r.stdout:
        return {"ok": True, "detail": "connected"}
    return {"ok": False, "detail": (r.stderr or r.stdout or "connection failed").strip()[:180]}


# --------------------------------------------------------------------------- #
#  Pull a device's library (roms via find→build_romdb→scp back; media via rsync)
# --------------------------------------------------------------------------- #
def _rsync_ssh_e(dev):
    e = "ssh -p %d -o StrictHostKeyChecking=accept-new" % (dev.get("port") or 22)
    if dev.get("auth") == "key" and dev.get("key_path"):
        e += " -i " + shlex.quote(os.path.expanduser(dev["key_path"]))
    return e


def pull_roms(dev, lm):
    """Run find→build_romdb on the device and scp the ROM index back. Mirrors
    update.sh's --roms path. Returns the ROM count."""
    root = (lm.get("rom_path") or "").strip()
    if not root:
        raise RuntimeError("no ROM path set for this library manager")
    tgt = _target(dev)
    sopts = _scp_opts(dev)
    # the pulled index lives on THIS server (the consumer); point the catalog at it
    out = os.path.join(DIR, "roms-index.sqlite")
    # ship the builder, scan + build on the device, fetch the index back
    scp = _wrap_pw(dev, ["scp"] + sopts + [os.path.join(DIR, "build_romdb.py"),
                   os.path.join(DIR, "romtags.py"), tgt + ":/tmp/"])
    r = _run(scp, timeout=60)
    if r.returncode != 0:
        raise RuntimeError("scp builder failed: " + (r.stderr or "")[:160])
    remote = ("find %s -type f -printf '%%s\\t%%T@\\t%%P\\n' > /tmp/ldx_romscan.tsv "
              "&& python3 /tmp/build_romdb.py /tmp/ldx_romscan.tsv "
              "/tmp/ldx-roms-index.sqlite %s" % (shlex.quote(root), shlex.quote(root)))
    r = _ssh(dev, remote, timeout=300)
    if r.returncode != 0:
        raise RuntimeError("remote scan failed: " + (r.stderr or r.stdout or "")[:200])
    back = _wrap_pw(dev, ["scp"] + sopts + [tgt + ":/tmp/ldx-roms-index.sqlite", out])
    r = _run(back, timeout=120)
    if r.returncode != 0:
        raise RuntimeError("scp index back failed: " + (r.stderr or "")[:160])
    con = sqlite3.connect(out)
    try:
        n = con.execute("SELECT COUNT(*) FROM roms").fetchone()[0]
    finally:
        con.close()
    if config.get("roms_index_db") != out:            # catalog reads the local index
        config.set_("roms_index_db", out)
    return n


def pull_media(dev, lm):
    """rsync the device's media folder locally and register it as an ES-DE media
    mount. Returns the local path (or None if no media path)."""
    mpath = (lm.get("media_path") or "").strip()
    if not mpath:
        return None
    dest = os.path.join(MEDIA_DIR, "dev%d" % lm["id"])
    os.makedirs(dest, exist_ok=True)
    argv = _wrap_pw(dev, ["rsync", "-a", "--delete", "-e", _rsync_ssh_e(dev),
                    "%s:%s/" % (_target(dev), mpath), dest + "/"])
    r = _run(argv, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError("rsync media failed: " + (r.stderr or "")[:200])
    config.media_mount_set("device-%d" % lm["id"], dest, "esde", 1)
    return dest


def pull_archive(dev, lm):
    """Index a remote storage archive into the catalog's `archive` source. SSH-find
    the game files on the device, populate crawl-index.sqlite with their (remote)
    paths, register the archive name, and run process.py — which extracts titles
    from filenames only, so no file contents need transferring. Returns file count."""
    root = (lm.get("rom_path") or lm.get("media_path") or "").strip()
    if not root:
        raise RuntimeError("no path set for this storage archive")
    name = "device-%d" % lm["id"]
    r = _ssh(dev, "find %s -type f -printf '%%s\\t%%T@\\t%%P\\n'" % shlex.quote(root),
             timeout=300)
    if r.returncode != 0:
        raise RuntimeError("remote find failed: " + (r.stderr or r.stdout or "")[:200])
    now = time.time()
    crawl = os.path.join(DIR, "crawl-index.sqlite")
    con = sqlite3.connect(crawl)
    con.execute("""CREATE TABLE IF NOT EXISTS files(
        id INTEGER PRIMARY KEY, archive TEXT, kind TEXT, fullpath TEXT UNIQUE,
        filename TEXT, ext TEXT, size_bytes INTEGER, mtime REAL,
        first_seen REAL, last_seen REAL, processed INTEGER DEFAULT 0)""")
    con.execute("DELETE FROM files WHERE archive=?", (name,))     # fresh each pull
    n = 0
    for line in r.stdout.splitlines():
        try:
            size_s, mtime_s, rel = line.split("\t", 2)
        except ValueError:
            continue
        fn = rel.rsplit("/", 1)[-1]
        ext = fn.rsplit(".", 1)[1].lower() if "." in fn else ""
        if ext not in ARCHIVE_EXTS:
            continue
        con.execute("INSERT OR REPLACE INTO files(archive,kind,fullpath,filename,ext,"
                    "size_bytes,mtime,first_seen,last_seen,processed) "
                    "VALUES(?,?,?,?,?,?,?,?,?,0)",
                    (name, "rom", os.path.join(root, rel), fn, ext,
                     int(size_s) if size_s.isdigit() else 0,
                     float(mtime_s) if mtime_s.replace(".", "", 1).isdigit() else 0.0,
                     now, now))
        n += 1
    con.commit()
    con.close()
    # register the archive so build_library includes it (name-gated), then extract
    config.archive_set(name, root, "rom", 1)
    pr = _run([sys.executable, os.path.join(DIR, "process.py")], timeout=300)
    if pr.returncode != 0:
        raise RuntimeError("process.py failed: " + (pr.stderr or "")[:160])
    return n


def sync_device(dev_id):
    """Pull every enabled library manager on a device. Returns a per-manager report."""
    dev = _device(dev_id)
    if not dev:
        raise RuntimeError("no such device")
    con = _con()
    lms = [dict(r) for r in con.execute(
        "SELECT * FROM library_managers WHERE device_id=? AND enabled=1", (dev_id,))]
    con.close()
    report = []
    rebuild = False       # did any manager change catalog inputs (roms/archive)?
    for lm in lms:
        prov = LM_KINDS.get(lm["kind"], ("", True, True))
        item = {"manager": lm["name"] or lm["kind"], "kind": lm["kind"]}
        try:
            if lm["kind"] == "archive":
                item["archive"] = pull_archive(dev, lm)     # → catalog 'archive' source
                rebuild = True
            else:
                if prov[1] and lm.get("rom_path"):
                    item["roms"] = pull_roms(dev, lm)
                    rebuild = True
                if prov[2] and lm.get("media_path"):
                    item["media"] = pull_media(dev, lm)
            item["ok"] = True
        except Exception as e:
            item["ok"] = False
            item["error"] = str(e)[:200]
        report.append(item)
    out = {"device": dev["name"], "results": report}
    if rebuild:      # rebuild the catalog so pulled roms/archive titles appear (build_library
        try:         # runs the consumer carry-over pass internally — the blessed rebuild path)
            r = _run([sys.executable, os.path.join(DIR, "build_library.py")], timeout=900)
            out["rebuilt"] = (r.returncode == 0)
            if r.returncode != 0:
                out["rebuild_error"] = (r.stderr or "")[:200]
        except (subprocess.TimeoutExpired, OSError) as e:
            out["rebuilt"] = False
            out["rebuild_error"] = str(e)[:200]
    return out


if __name__ == "__main__":               # tiny CLI for testing
    import json
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        print(json.dumps(devices_list(), indent=2, default=str))
    elif cmd == "test":
        print(json.dumps(test_connection(_device(int(sys.argv[2])))))
    elif cmd == "sync":
        print(json.dumps(sync_device(int(sys.argv[2])), indent=2))
