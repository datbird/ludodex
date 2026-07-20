"""Definitive auto-sync test: enable 1-min auto, drop a sentinel LOCAL tag, then watch the
backing store (PocketBase) for it to appear WITHOUT any manual sync — proving the scheduler
fired. Full cleanup + reset auto=0 at the end. Run inside the container."""
import sys
import time
import sqlite3
import os
sys.path.insert(0, "/app")
import config
import dbsync
import sync as _s

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
    config.set_("backingstore_auto_minutes", "0")


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
