"""ludodex app authentication — local username/password accounts + sessions.

Self-contained (stdlib only): passwords are scrypt-hashed with a per-user random
salt; session tokens are random and stored *hashed* (a DB leak grants no logins).
All state lives in auth.sqlite on the data volume. No external secret store.

The first account created on a fresh install is the admin (there are no users
until then — that's what drives the first-run "create admin" screen).
"""
import os
import sqlite3
import hashlib
import hmac
import secrets
import time

DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("LUDODEX_DATA", DIR)
DB = os.path.join(DATA, "auth.sqlite")

SESSION_TTL = 30 * 24 * 3600          # 30 days
MIN_PASSWORD = 8


def _con():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE COLLATE NOCASE,
        pw_hash TEXT, pw_salt TEXT,
        role TEXT DEFAULT 'admin', created REAL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS sessions(
        token_hash TEXT PRIMARY KEY, user_id INTEGER,
        created REAL, expires REAL)""")
    con.row_factory = sqlite3.Row
    return con


_con().close()   # ensure the file + schema exist at import


def _hash_pw(password, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt),
                       n=16384, r=8, p=1, dklen=32)
    return h.hex(), salt


def _tok_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def user_count():
    con = _con()
    n = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    con.close()
    return n


def needs_setup():
    """True on a fresh install — no accounts yet, so prompt to create the admin."""
    return user_count() == 0


def create_user(username, password, role="admin"):
    username = (username or "").strip()
    if not username:
        raise ValueError("username is required")
    if len(password or "") < MIN_PASSWORD:
        raise ValueError("password must be at least %d characters" % MIN_PASSWORD)
    ph, salt = _hash_pw(password)
    con = _con()
    try:
        con.execute("INSERT INTO users(username,pw_hash,pw_salt,role,created) "
                    "VALUES(?,?,?,?,?)", (username, ph, salt, role, time.time()))
        con.commit()
        uid = con.execute("SELECT id FROM users WHERE username=? COLLATE NOCASE",
                          (username,)).fetchone()[0]
    except sqlite3.IntegrityError:
        raise ValueError("that username already exists")
    finally:
        con.close()
    return uid


def verify(username, password):
    """Return the user dict on a correct password, else None."""
    con = _con()
    row = con.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE",
                      ((username or "").strip(),)).fetchone()
    con.close()
    if not row:
        return None
    ph, _ = _hash_pw(password or "", row["pw_salt"])
    if hmac.compare_digest(ph, row["pw_hash"]):
        return {"id": row["id"], "username": row["username"], "role": row["role"]}
    return None


def create_session(user_id):
    token = secrets.token_urlsafe(32)
    now = time.time()
    con = _con()
    con.execute("INSERT INTO sessions(token_hash,user_id,created,expires) "
                "VALUES(?,?,?,?)", (_tok_hash(token), user_id, now, now + SESSION_TTL))
    con.execute("DELETE FROM sessions WHERE expires < ?", (now,))   # gc expired
    con.commit()
    con.close()
    return token


def session_user(token):
    """Resolve a session cookie to its user, or None if missing/expired."""
    if not token:
        return None
    con = _con()
    row = con.execute(
        "SELECT u.id, u.username, u.role, s.expires FROM sessions s "
        "JOIN users u ON u.id = s.user_id WHERE s.token_hash=?",
        (_tok_hash(token),)).fetchone()
    con.close()
    if not row or row["expires"] < time.time():
        return None
    return {"id": row["id"], "username": row["username"], "role": row["role"]}


def delete_session(token):
    if not token:
        return
    con = _con()
    con.execute("DELETE FROM sessions WHERE token_hash=?", (_tok_hash(token),))
    con.commit()
    con.close()
