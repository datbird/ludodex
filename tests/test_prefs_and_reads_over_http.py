#!/usr/bin/env python3
"""Two seams the live browser suite (tests/browser/ui_full.py) found between the UI and
the API, driven here through the real routes.

  * The Settings "Include collections" Spotlight switch never saved. The UI posted
    `spotlight_include_collections` to POST /api/prefs, which dropped it, and GET
    /api/prefs never returned it, so the switch came back off on every open. Spotlight
    itself has read that key all along; nothing wrote it.
  * Opening the Files tab sent POST /api/devices/browse-entries. It only reads a
    directory listing, so it is now a GET with the same two parameters as query params.
    Nothing else called the POST, so it is gone rather than kept as an alias. It stays
    admin-only: the gate matches the path whatever the method.

Offline. Starlette's TestClient with `_current_user` stubbed; browse-entries lists a
temp dir on this machine (device 0 is the local host).
"""
import os
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-prefs-reads-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

from server import app                                         # noqa: E402
import config                                                  # noqa: E402
from fastapi.testclient import TestClient                      # noqa: E402

API = open(os.path.join(DIR, "web", "src", "api.ts"), encoding="utf-8").read()
PASS = []


def check(label, cond, detail=None):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: %s%s" % (label, "" if detail is None else "  (%r)" % (detail,)))


ADMIN = {"id": 1, "username": "me", "role": "admin"}
USER = {"id": 2, "username": "kid", "role": "user"}


def main():
    saved = app._current_user
    client = TestClient(app.app, raise_server_exceptions=False)
    try:
        app._current_user = lambda request: ADMIN

        print("1. the Spotlight 'Include collections' switch persists")
        r = client.get("/api/prefs")
        check("GET /api/prefs returns the key", r.status_code == 200
              and "spotlight_include_collections" in r.json(), r.text[:200])
        check("and it defaults to off", r.json()["spotlight_include_collections"] is False)
        r = client.post("/api/prefs", json={"spotlight_include_collections": True})
        check("POST answers with it on", r.status_code == 200
              and r.json().get("spotlight_include_collections") is True, r.text[:200])
        check("the value Spotlight reads is now on",
              config.get_bool("spotlight_include_collections", False) is True)
        check("a fresh GET still says on",
              client.get("/api/prefs").json()["spotlight_include_collections"] is True)
        r = client.post("/api/prefs", json={"spotlight_seconds": 20})
        check("an unrelated prefs save leaves it alone",
              r.json().get("spotlight_include_collections") is True)
        client.post("/api/prefs", json={"spotlight_include_collections": False})
        check("and it turns back off",
              client.get("/api/prefs").json()["spotlight_include_collections"] is False
              and config.get_bool("spotlight_include_collections", True) is False)

        print("2. browse-entries is a GET")
        os.makedirs(os.path.join(DATA, "browse", "roms"), exist_ok=True)
        with open(os.path.join(DATA, "browse", "a.txt"), "w") as f:
            f.write("x")
        where = os.path.join(DATA, "browse")
        r = client.get("/api/devices/browse-entries",
                       params={"device_id": "0", "path": where})
        body = r.json() if r.status_code == 200 else {}
        check("GET lists the directory", r.status_code == 200 and body.get("ok"),
              r.text[:200])
        check("with its dirs and files",
              [d["name"] for d in body.get("dirs", [])] == ["roms"]
              and [f["name"] for f in body.get("files", [])] == ["a.txt"], body)
        r = client.get("/api/devices/browse-entries", params={"path": where})
        check("device_id is optional (0 = this host)", r.status_code == 200
              and r.json().get("ok"), r.text[:200])
        r = client.post("/api/devices/browse-entries",
                        json={"device_id": 0, "path": where})
        check("the old POST is gone (nothing else called it)", r.status_code == 405,
              r.status_code)
        check("api.ts reads it with get(), not postJson()",
              "get<{" in API.split("browseEntries:")[1].split("\n")[1]
              and "'/api/devices/browse-entries?' + new URLSearchParams(" in API)

        app._current_user = lambda request: USER
        r = client.get("/api/devices/browse-entries", params={"path": where})
        check("a plain user is still refused the GET", r.status_code == 403,
              r.status_code)
    finally:
        app._current_user = saved

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
