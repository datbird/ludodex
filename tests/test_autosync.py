"""Definitive auto-sync test: enable 1-min auto, drop a sentinel LOCAL tag, then watch the
backing store (PocketBase) for it to appear WITHOUT any manual sync — proving the scheduler
fired. Full cleanup + restore of the prior interval at the end. Run inside the container.

LIVE TEST. It writes to the running instance's databases and to the real PocketBase
collection. That is the point — the scheduler cannot be observed any other way — but it
means a blanket "run every test_*.py" sweep must not fire it. Hence the opt-in below:
set LUDODEX_LIVE_TESTS=1 to run it deliberately.
"""
import sys
import time
import sqlite3
import os

if os.environ.get("LUDODEX_LIVE_TESTS") != "1":
    sys.exit("SKIPPED: live test. It mutates the running instance and the real backing "
             "store. Re-run with LUDODEX_LIVE_TESTS=1 if that is what you want.")

sys.path.insert(0, "/app")
import config
import dbsync
import remote_db as _s

NK = "__autosync_selftest__"
STORE = next(x for x in dbsync.STORES if x["name"] == "user_tags")
DBP = dbsync._db_path(STORE)
KEY = NK + dbsync.SEP + "auto"

url = config.get("pocketbase_url").rstrip("/")
hdr = {"Authorization": _s.pb_auth(url, config.get("pocketbase_admin_email"),
                                   config.pocketbase_password())}
COLL = "ludodex_user_tags"


def remote_has():
    return dbsync.BACKENDS["pocketbase"]().read_all(STORE, ["norm_key", "tag", "created"])


# Capture the interval the instance was actually running on. Cleanup used to hardcode
# "0", so the test silently DISABLED a user's scheduled backing-store sync as its parting
# act — it did exactly that on 2026-08-02, leaving auto-sync off with 5 minutes configured.
PRIOR_AUTO = config.get("backingstore_auto_minutes")


def cleanup():
    c = sqlite3.connect(DBP)
    c.execute("DELETE FROM user_tags WHERE norm_key=?", (NK,))
    c.commit(); c.close()
    for k in list(remote_has()):
        if k.startswith(NK):
            _s.http("DELETE", "%s/api/collections/%s/records/%s"
                    % (url, COLL, _s.pb_id(k)), headers=hdr)
    con = _s._cache_con()
    con.execute("DELETE FROM sync_state WHERE collection='user_tags' AND key LIKE ?",
                (NK + "%",))
    con.commit(); con.close()
    config.set_("backingstore_auto_minutes", PRIOR_AUTO if PRIOR_AUTO not in (None, "")
                else "0")


cleanup()                                   # start clean
config.set_("backingstore_backend", "pocketbase")
config.set_("backingstore_auto_minutes", "1")
c = sqlite3.connect(DBP)
c.execute("INSERT OR REPLACE INTO user_tags(norm_key,tag,created) VALUES(?,?,?)",
          (NK, "auto", 1000.0))
c.commit(); c.close()
print("sentinel added locally; auto=1min; waiting for the scheduler to push it (no manual sync)...")

ok = False
for i in range(13):                         # ~130s (scheduler sleeps 60s then fires)
    time.sleep(10)
    if KEY in remote_has():
        print("AUTO-SYNC FIRED: sentinel reached PocketBase after ~%ds (no manual sync)" % ((i + 1) * 10))
        ok = True
        break

cleanup()
print("RESULT:", "PASS — periodic auto-sync works" if ok else "FAIL — sentinel never pushed")
