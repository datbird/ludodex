#!/usr/bin/env python3
"""A "user" account is not an operator (#10).

`auth.ROLES` is ("admin", "user"), but `_require_admin` was called on the /api/auth/*
endpoints and nowhere else — so the role column decided who could add accounts and
nothing more. Every other operator power was open to any logged-in user:

  * POST /api/fs/delete  ->  devices.fs_delete  ->  `bash -c "rm -rf -- …"` as root
  * POST /api/devices/browse — list any directory on the host
  * POST /api/games/identify-folder — point a PAID vision model at any host folder
  * the ops endpoints (reset, restore, restart) and the stored store credentials

The check belongs in the SAME middleware that does authentication, matched on the path:
most of these handlers never take `request`, and a per-handler decorator is one
forgotten line away from a hole of exactly this shape reopening.

Offline. The requests are driven through Starlette's TestClient with `_current_user`
stubbed, so no account store and no network; the handlers are never reached — a 403 from
the gate is the whole assertion.
"""
import os
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-api-admin-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

from server import app                                         # noqa: E402
from fastapi.testclient import TestClient                      # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


# The powers a plain user must not have. Each is (method, path).
OPERATOR = [
    ("POST", "/api/fs/delete"),
    ("POST", "/api/fs/transfer"),
    ("POST", "/api/fs/mkdir"),
    ("POST", "/api/fs/stat"),
    ("POST", "/api/devices/browse"),
    ("POST", "/api/devices/browse-entries"),
    ("POST", "/api/games/identify-folder"),
    ("POST", "/api/fileops/plan"),
    ("POST", "/api/fileops/runbook/3/execute"),
    ("POST", "/api/fileops/manifest"),
    ("POST", "/api/media/scan-local"),
    ("POST", "/api/ops/reset"),
    ("POST", "/api/ops/restore"),
    ("POST", "/api/ops/restart"),
    ("POST", "/api/backups/restore"),
    ("POST", "/api/backingstore/restore"),
    ("POST", "/api/services/psn/npsso"),
    ("POST", "/api/services/nintendo/cookie"),
    ("POST", "/api/ai/config"),
    ("POST", "/api/matchindex/download"),
    ("POST", "/api/catalog/rebuild"),
    ("DELETE", "/api/ingest-hints"),
    ("POST", "/api/devices"),
    ("DELETE", "/api/devices/2"),
    ("POST", "/api/devices/2/sync"),
    ("POST", "/api/devices/managers"),
    ("POST", "/api/archives"),
]

# …and the ordinary catalog work a user MUST still be able to do.
CATALOG = [
    ("GET", "/api/stats"),
    ("GET", "/api/games"),
    ("GET", "/api/systems"),
    ("GET", "/api/services/screenscraper/tier"),
    ("GET", "/api/ai/config"),
    ("GET", "/api/devices"),
    ("POST", "/api/games/doom/tags"),
    ("POST", "/api/games/doom/ownership"),
    ("GET", "/api/prefs"),
    ("GET", "/api/health"),
]


def main():
    print("host-filesystem and destructive endpoints are admin-only")

    saved = app._current_user
    client = TestClient(app.app, raise_server_exceptions=False)
    try:
        # --- as a plain "user" ------------------------------------------------- #
        app._current_user = lambda request: {"id": 2, "username": "kid", "role": "user"}
        for method, path in OPERATOR:
            r = client.request(method, path, json={})
            check("user is refused %s %s" % (method, path), r.status_code == 403)

        for method, path in CATALOG:
            r = client.request(method, path, json={})
            check("user may still reach %s %s" % (method, path), r.status_code != 403)

        # --- as an admin ------------------------------------------------------- #
        app._current_user = lambda request: {"id": 1, "username": "me", "role": "admin"}
        for method, path in OPERATOR:
            r = client.request(method, path, json={})
            check("admin is not blocked from %s %s" % (method, path),
                  r.status_code != 403)

        # --- signed out -------------------------------------------------------- #
        app._current_user = lambda request: None
        r = client.request("POST", "/api/fs/delete", json={})
        check("a signed-out caller is still 401, not 403", r.status_code == 401)
    finally:
        app._current_user = saved

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
