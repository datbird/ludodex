"""Two-way round-trip against the live PocketBase, using a sentinel record cleaned up at
the end. Proves: local add -> push, remote add -> pull, local delete -> remote delete,
remote delete -> local delete."""
import sys
import os
import sqlite3
sys.path.insert(0, "/app")
import remote_db as s
import config
import dbsync

NK = "__dbsync_selftest__"
STORE = next(x for x in dbsync.STORES if x["name"] == "user_tags")
DBP = dbsync._db_path(STORE)
url = config.get("pocketbase_url").rstrip("/")
hdr = {"Authorization": s.pb_auth(url, config.get("pocketbase_admin_email"),
                                  config.pocketbase_password())}
COLL = "ludodex_user_tags"


def local_tags():
    c = sqlite3.connect(DBP)
    r = {t for (t,) in c.execute("SELECT tag FROM user_tags WHERE norm_key=?", (NK,))}
    c.close()
    return r


def remote_keys():
    return set(dbsync.BACKENDS["pocketbase"]().read_all(STORE, ["norm_key", "tag", "created"]))


def local_add(tag):
    c = sqlite3.connect(DBP)
    c.execute("INSERT OR REPLACE INTO user_tags(norm_key,tag,created) VALUES(?,?,?)",
              (NK, tag, 1000.0))
    c.commit(); c.close()


def local_del(tag):
    c = sqlite3.connect(DBP)
    c.execute("DELETE FROM user_tags WHERE norm_key=? AND tag=?", (NK, tag))
    c.commit(); c.close()


def remote_add(tag):
    import json
    key = NK + dbsync.SEP + tag
    body = {"id": s.pb_id(key), "k": key,
            "data": json.dumps({"norm_key": NK, "tag": tag, "created": "2000.0"},
                               sort_keys=True)}
    s.http("POST", "%s/api/collections/%s/records" % (url, COLL), headers=hdr, body=body)


def sync():
    dbsync.sync_all("pocketbase", only={"user_tags"})


fails = []
# --- 1. local add -> push ---
local_add("pushme")
sync()
rk = remote_keys()
print("1 local-add->push:", "PASS" if (NK + dbsync.SEP + "pushme") in rk else "FAIL")
if (NK + dbsync.SEP + "pushme") not in rk:
    fails.append("push")

# --- 2. local delete -> remote delete ---
local_del("pushme")
sync()
print("2 local-del->remote-del:", "PASS" if (NK + dbsync.SEP + "pushme") not in remote_keys() else "FAIL")
if (NK + dbsync.SEP + "pushme") in remote_keys():
    fails.append("push-delete")

# --- 3. remote add -> pull ---
remote_add("pullme")
sync()
print("3 remote-add->pull:", "PASS" if "pullme" in local_tags() else "FAIL")
if "pullme" not in local_tags():
    fails.append("pull")

# --- 4. remote delete -> local delete ---
# delete it on PB directly, then sync; local copy should go away
key = NK + dbsync.SEP + "pullme"
s.http("DELETE", "%s/api/collections/%s/records/%s" % (url, COLL, s.pb_id(key)), headers=hdr)
sync()
print("4 remote-del->local-del:", "PASS" if "pullme" not in local_tags() else "FAIL")
if "pullme" in local_tags():
    fails.append("pull-delete")

# --- cleanup: remove any sentinel rows from both sides + shadow ---
c = sqlite3.connect(DBP)
c.execute("DELETE FROM user_tags WHERE norm_key=?", (NK,))
c.commit(); c.close()
for k in list(remote_keys()):
    if k.startswith(NK):
        s.http("DELETE", "%s/api/collections/%s/records/%s" % (url, COLL, s.pb_id(k)), headers=hdr)
con = s._cache_con()
con.execute("DELETE FROM sync_state WHERE backend=? AND collection='user_tags' AND key LIKE ?",
            ("2way:pocketbase", NK + "%"))
con.commit(); con.close()

print("RESULT:", "ALL 4 ROUND-TRIP TESTS PASS" if not fails else ("FAILS: " + ",".join(fails)))
