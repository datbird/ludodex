"""Run a database's schema setup once per process, not on every connection.

Keyed by the file's identity and schema cookie, so a replaced, recreated or re-pointed
file (a restore, a test's temp dir) is set up again. The DDL is idempotent; this only
stops repeating it on hot paths.
"""
import os

_DONE = set()


def _key(con, path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (os.path.realpath(path), st.st_dev, st.st_ino,
            con.execute("PRAGMA schema_version").fetchone()[0])


def ensure(con, path, setup):
    """Call setup(con) unless this file's schema was already set up in this process."""
    key = _key(con, path)
    if key is None or key not in _DONE:
        setup(con)
        key = _key(con, path)
        if key is not None:
            _DONE.add(key)
