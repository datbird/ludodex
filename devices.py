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
DATA = os.environ.get("LUDODEX_DATA", DIR)
sys.path.insert(0, DIR)
import config

DB = os.path.join(DATA, "connections.sqlite")
MEDIA_DIR = os.path.join(DATA, "device-media")     # rsync'd device media lands here

# library-manager kind -> (label, provides_roms, provides_media). A plain directory
# of game files (e.g. an Unraid share) is just a "ROM folder" — the same SSH-find +
# filename index as any other ROM tree, so there's no separate "storage archive" kind.
LM_KINDS = {
    "retrodeck": ("RetroDECK / ES-DE", True, True),
    "esde":      ("EmulationStation-DE", True, True),
    "retrobat":  ("RetroBat", True, True),
    "playnite":  ("Playnite", True, False),
    "launchbox": ("LaunchBox", True, True),
    "roms":      ("ROM folder", True, False),
    # a folder of just media (box art / video / manuals…), ES-DE-structured;
    # media_kinds picks which of the ~23 kinds to ingest from it (empty = all)
    "media":     ("Media folder", False, True),
}


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
    # which media kinds a "media" folder should ingest (comma-joined; '' = all)
    if "media_kinds" not in {r[1] for r in
                             con.execute("PRAGMA table_info(library_managers)")}:
        con.execute("ALTER TABLE library_managers ADD COLUMN media_kinds TEXT DEFAULT ''")
    # the former "storage archive" kind was folded into "ROM folder" — migrate any
    # existing rows so they sync via the normal ROM path
    con.execute("UPDATE library_managers SET kind='roms' WHERE kind='archive'")
    con.commit()
    con.row_factory = sqlite3.Row
    return con


def local_device_id():
    """The 'This server (local)' device id, created on demand — the home for local
    storage now that emulation-storage locations live in Connections."""
    con = _con()
    r = con.execute("SELECT id FROM devices WHERE transport='local' ORDER BY id "
                    "LIMIT 1").fetchone()
    if r:
        con.close()
        return r["id"]
    cur = con.execute("INSERT INTO devices(name,transport,enabled,created) "
                      "VALUES('This server (local)','local',1,?)", (time.time(),))
    did = cur.lastrowid
    con.commit()
    con.close()
    return did


def migrate_storage():
    """One-time, idempotent: fold legacy Emulation-storage locations into
    Connections as managers on the local device, then drop the old rows. ROM
    archives → ROM folder managers; ES-DE media mounts (not device-created) →
    Media folder managers. Returns {roms, media} migrated counts."""
    if config.get("storage_migrated") == "1":
        return {"roms": 0, "media": 0, "skipped": True}
    did = None
    n_roms = n_media = 0
    for a in config.archives_list():
        did = did or local_device_id()
        manager_set({"device_id": did, "kind": "roms", "name": a["name"],
                     "rom_path": a["path"], "enabled": a["enabled"]})
        config.archive_rm(a["name"])
        n_roms += 1
    for m in config.media_mounts_list(provider="esde"):
        if (m["name"] or "").startswith("device-"):     # already a device mount
            continue
        did = did or local_device_id()
        manager_set({"device_id": did, "kind": "media", "name": m["name"],
                     "media_path": m["path"], "media_kinds": m.get("kinds") or [],
                     "enabled": m["enabled"]})
        config.media_mount_rm(m["name"])
        n_media += 1
    config.set_("storage_migrated", "1")
    return {"roms": n_roms, "media": n_media}


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
        lm["media_kinds"] = [k for k in (lm.get("media_kinds") or "").split(",") if k]
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
    # media_kinds may arrive as a list (from the UI picker) or a string
    mk = m.get("media_kinds")
    mk_str = ",".join(mk) if isinstance(mk, (list, tuple)) else (mk or "")
    fields = ("device_id", "kind", "name", "rom_path", "media_path",
              "media_kinds", "enabled")
    vals = {"device_id": m.get("device_id"), "kind": m.get("kind"),
            "name": m.get("name"), "rom_path": m.get("rom_path"),
            "media_path": m.get("media_path"), "media_kinds": mk_str,
            "enabled": 1 if m.get("enabled", True) else 0}
    if m.get("id"):
        con.execute("UPDATE library_managers SET %s WHERE id=?"
                    % ",".join("%s=?" % f for f in fields),
                    [vals[f] for f in fields] + [int(m["id"])])
        mid = int(m["id"])
    else:
        con.execute("INSERT INTO library_managers(%s) VALUES(%s)"
                    % (",".join(fields), ",".join("?" * len(fields))),
                    [vals[f] for f in fields])
        mid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.commit()
    con.close()
    return mid


def manager_rm(mid):
    con = _con()
    con.execute("DELETE FROM library_managers WHERE id=?", (mid,))
    con.commit()
    con.close()
    try:                                   # drop this manager's ROM index, if any
        os.remove(os.path.join(DATA, "roms-index-mgr%d.sqlite" % int(mid)))
    except (OSError, ValueError):
        pass


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


def _rom_index_path(lm):
    return os.path.join(DATA, "roms-index-mgr%d.sqlite" % lm["id"])


def pull_roms(dev, lm):
    """Build a ROM index for this manager's rom_path → roms-index-mgr<id>.sqlite,
    which build_library merges into the emulation source. Runs build_romdb in place
    for a local device, or over SSH (scan+build on the device, scp the index back)
    for a remote one. Returns the ROM count."""
    root = (lm.get("rom_path") or "").strip()
    if not root:
        raise RuntimeError("no ROM path set for this library manager")
    out = _rom_index_path(lm)                          # per-manager index
    if dev.get("transport") == "local":
        # scan + build directly on this server (no SSH)
        scan = os.path.join(DATA, "ldx_romscan_mgr%d.tsv" % lm["id"])
        r = _run(["bash", "-c", "find %s -type f -printf '%%s\\t%%T@\\t%%P\\n' > %s"
                  % (shlex.quote(root), shlex.quote(scan))], timeout=300)
        if r.returncode != 0:
            raise RuntimeError("local scan failed: " + (r.stderr or "")[:200])
        r = _run([sys.executable, os.path.join(DIR, "build_romdb.py"), scan, out,
                  root], timeout=600)
        if r.returncode != 0:
            raise RuntimeError("build_romdb failed: " + (r.stderr or "")[:200])
        try:
            os.remove(scan)
        except OSError:
            pass
    else:
        tgt = _target(dev)
        sopts = _scp_opts(dev)
        scp = _wrap_pw(dev, ["scp"] + sopts + [os.path.join(DIR, "build_romdb.py"),
                       os.path.join(DIR, "romtags.py"), tgt + ":/tmp/"])
        r = _run(scp, timeout=60)
        if r.returncode != 0:
            raise RuntimeError("scp builder failed: " + (r.stderr or "")[:160])
        remote = ("find %s -type f -printf '%%s\\t%%T@\\t%%P\\n' > /tmp/ldx_romscan.tsv"
                  " && python3 /tmp/build_romdb.py /tmp/ldx_romscan.tsv "
                  "/tmp/ldx-roms-index.sqlite %s"
                  % (shlex.quote(root), shlex.quote(root)))
        r = _ssh(dev, remote, timeout=300)
        if r.returncode != 0:
            raise RuntimeError("remote scan failed: " + (r.stderr or r.stdout or "")[:200])
        back = _wrap_pw(dev, ["scp"] + sopts + [tgt + ":/tmp/ldx-roms-index.sqlite",
                        out])
        r = _run(back, timeout=120)
        if r.returncode != 0:
            raise RuntimeError("scp index back failed: " + (r.stderr or "")[:160])
    con = sqlite3.connect(out)
    try:
        n = con.execute("SELECT COUNT(*) FROM roms").fetchone()[0]
    finally:
        con.close()
    return n


def pull_media(dev, lm):
    """Register a device media folder as an ES-DE media mount (filtered to the
    manager's chosen media_kinds). Remote folders are rsync'd local first so the
    media indexer can walk them; a local device's path is used in place. Returns
    the mount path (or None if no media path)."""
    mpath = (lm.get("media_path") or "").strip()
    if not mpath:
        return None
    kinds = lm.get("media_kinds")
    if isinstance(kinds, str):
        kinds = [k for k in kinds.split(",") if k]
    if dev.get("transport") == "local":
        dest = mpath                                   # walk the folder in place
    else:
        dest = os.path.join(MEDIA_DIR, "dev%d" % lm["id"])
        os.makedirs(dest, exist_ok=True)
        argv = _wrap_pw(dev, ["rsync", "-a", "--delete", "-e", _rsync_ssh_e(dev),
                        "%s:%s/" % (_target(dev), mpath), dest + "/"])
        r = _run(argv, timeout=1800)
        if r.returncode != 0:
            raise RuntimeError("rsync media failed: " + (r.stderr or "")[:200])
    config.media_mount_set("device-%d" % lm["id"], dest, "esde", 1, kinds or None)
    return dest


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
    rebuild = False       # did any manager pull ROMs (→ catalog needs a rebuild)?
    reindex = False       # did any manager pull media (→ media index needs a refresh)?
    for lm in lms:
        prov = LM_KINDS.get(lm["kind"], ("", True, True))
        item = {"manager": lm["name"] or lm["kind"], "kind": lm["kind"]}
        try:
            if prov[1] and lm.get("rom_path"):
                item["roms"] = pull_roms(dev, lm)
                rebuild = True
            if prov[2] and lm.get("media_path"):
                item["media"] = pull_media(dev, lm)
                reindex = True
            item["ok"] = True
        except Exception as e:
            item["ok"] = False
            item["error"] = str(e)[:200]
        report.append(item)
    out = {"device": dev["name"], "results": report}
    if reindex:      # refresh the media index so newly-mounted media is picked up
        try:
            r = _run([sys.executable, os.path.join(DIR, "media_index.py")], timeout=900)
            out["reindexed"] = (r.returncode == 0)
            if r.returncode != 0:
                out["reindex_error"] = (r.stderr or "")[:200]
        except (subprocess.TimeoutExpired, OSError) as e:
            out["reindexed"] = False
            out["reindex_error"] = str(e)[:200]
    if rebuild:      # rebuild the catalog so pulled ROM titles appear (build_library
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
