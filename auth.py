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
    con.execute("""CREATE TABLE IF NOT EXISTS email_map(
        email TEXT PRIMARY KEY COLLATE NOCASE, user_id INTEGER, created REAL)""")
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


# --------------------------------------------------------------------------- #
#  User management (admin)
# --------------------------------------------------------------------------- #
ROLES = ("admin", "user")


def list_users():
    con = _con()
    rows = con.execute(
        "SELECT id, username, role, created FROM users ORDER BY created").fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_user(uid):
    con = _con()
    r = con.execute("SELECT id, username, role, created FROM users WHERE id=?",
                    (uid,)).fetchone()
    con.close()
    return dict(r) if r else None


def admin_count():
    con = _con()
    n = con.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]
    con.close()
    return n


def delete_user(uid):
    con = _con()
    con.execute("DELETE FROM users WHERE id=?", (uid,))
    con.execute("DELETE FROM sessions WHERE user_id=?", (uid,))   # log them out too
    con.commit()
    con.close()


def set_password(uid, password):
    if len(password or "") < MIN_PASSWORD:
        raise ValueError("password must be at least %d characters" % MIN_PASSWORD)
    ph, salt = _hash_pw(password)
    con = _con()
    con.execute("UPDATE users SET pw_hash=?, pw_salt=? WHERE id=?", (ph, salt, uid))
    con.commit()
    con.close()


def set_role(uid, role):
    if role not in ROLES:
        raise ValueError("role must be one of %s" % ", ".join(ROLES))
    con = _con()
    con.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
    con.commit()
    con.close()


# --------------------------------------------------------------------------- #
#  Cloudflare Access: map a verified SSO email to a ludodex user
# --------------------------------------------------------------------------- #
def _norm_email(email):
    return (email or "").strip().lower()


def list_email_maps():
    con = _con()
    rows = con.execute(
        "SELECT m.email, m.user_id, m.created, u.username, u.role "
        "FROM email_map m JOIN users u ON u.id = m.user_id "
        "ORDER BY u.username, m.email").fetchall()
    con.close()
    return [dict(r) for r in rows]


def map_email(email, user_id):
    email = _norm_email(email)
    if "@" not in email:
        raise ValueError("enter a valid email address")
    con = _con()
    if not con.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
        con.close()
        raise ValueError("no such user")
    con.execute("INSERT INTO email_map(email,user_id,created) VALUES(?,?,?) "
                "ON CONFLICT(email) DO UPDATE SET user_id=excluded.user_id",
                (email, user_id, time.time()))
    con.commit()
    con.close()


def unmap_email(email):
    con = _con()
    con.execute("DELETE FROM email_map WHERE email=? COLLATE NOCASE", (_norm_email(email),))
    con.commit()
    con.close()


def user_for_email(email):
    """Resolve a Cloudflare-verified email to its mapped ludodex user, or None."""
    con = _con()
    row = con.execute(
        "SELECT u.id, u.username, u.role FROM email_map m "
        "JOIN users u ON u.id = m.user_id WHERE m.email=? COLLATE NOCASE",
        (_norm_email(email),)).fetchone()
    con.close()
    return dict(row) if row else None
