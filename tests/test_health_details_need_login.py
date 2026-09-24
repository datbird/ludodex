#!/usr/bin/env python3
"""/api/health gives liveness to anyone and its details only to a signed-in user.

It sits outside the login gate so a container health check or an uptime monitor can
reach it. Before this, "outside the gate" also meant the details: data paths and the
whole AI setup, every prompt plus a masked provider key, for anyone who could reach the
port. Now a signed-out caller gets {"ok": true} and nothing else, unless an admin turns
on `public_health_details`. The switch itself is admin-only.

Offline: TestClient with `_current_user` stubbed, an isolated data dir.
"""
import os
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-health-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

from server import app                                         # noqa: E402
from fastapi.testclient import TestClient                      # noqa: E402

PASS = []


def check(label, cond, detail=None):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: %s%s" % (label, "" if detail is None else "  (%r)" % (detail,)))


def as_user(user):
    app._current_user = lambda request: user


def main():
    client = TestClient(app.app, raise_server_exceptions=False)
    saved = app._current_user
    try:
        print("1. signed out, switch off (the default)")
        as_user(None)
        r = client.get("/api/health")
        check("answers 200 without a login", r.status_code == 200, r.status_code)
        check("and says only that it is up", r.json() == {"ok": True}, r.json())

        print("2. signed in")
        as_user({"id": 2, "username": "u", "role": "user"})
        body = client.get("/api/health").json()
        check("a signed-in user gets the details",
              {"library", "repo", "ai"} <= set(body), sorted(body))

        print("3. the switch is admin-only")
        check("a user cannot read it",
              client.get("/api/auth/public-health").status_code == 403)
        check("a user cannot set it",
              client.post("/api/auth/public-health", json={"enabled": True}).status_code == 403)
        as_user({"id": 1, "username": "a", "role": "admin"})
        r = client.post("/api/auth/public-health", json={"enabled": True})
        check("an admin can turn it on", r.status_code == 200 and r.json() == {"enabled": True},
              (r.status_code, r.text))

        print("4. signed out, switch on")
        as_user(None)
        body = client.get("/api/health").json()
        check("the details are public now", "ai" in body, sorted(body))

        as_user({"id": 1, "username": "a", "role": "admin"})
        client.post("/api/auth/public-health", json={"enabled": False})
        as_user(None)
        check("and private again once it is off",
              client.get("/api/health").json() == {"ok": True})
    finally:
        app._current_user = saved

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
