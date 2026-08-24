#!/usr/bin/env python3
"""A URL proven alive must not be re-probed on every sync, forever.

`prune_dead` selects `ref_type='url' AND (sha1 IS NULL OR sha1='')` and HEAD-checks the
lot with 16 threads. Only the CHOSEN asset per (game, kind) is ever materialized, so a
non-chosen candidate never gets a sha1 — and "no sha1" was being read as "never
verified". The same handful of speculative Steam CDN URLs per game were therefore
re-HEADed on every single sync, for the entire life of the library, to learn the answer
they gave last time.

`sha1` is the wrong flag for the question. It records that the bytes were DOWNLOADED,
not that the reference answered. So the probe records its own result: a ref that
answered is remembered, and re-probed only once the record goes stale — a TTL and not a
permanent pass, because a URL that worked in January can 404 in June.

The dead ones must still die on the first look, and a transient failure must still leave
the ref alone: "I could not ask" is not "it is gone".

Offline. Every HEAD is answered by a stub, no sockets.
"""
import os
import sys
import time

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402

DATA = test_support.isolate("ludodex-prune-")
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))

import media_fetch                                             # noqa: E402
import media_index                                             # noqa: E402

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


ANSWER = {}          # url -> status, or an Exception instance to raise
ASKED = []


class _Resp:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _urlopen(req, timeout=None):
    url = req.full_url if hasattr(req, "full_url") else req
    ASKED.append(url)
    a = ANSWER.get(url, 200)
    if isinstance(a, Exception):
        raise a
    if a >= 400:
        import urllib.error
        raise urllib.error.HTTPError(url, a, "no", None, None)
    return _Resp(a)


def seed(con, rows):
    con.execute("DELETE FROM media")
    for i, (url, chosen) in enumerate(rows, 1):
        con.execute("INSERT INTO media(id,norm_key,system,kind,provider,ref_type,ref,"
                    "ext,matched,chosen,indexed_at) VALUES(?,'g','','cover','steam',"
                    "'url',?,'jpg',1,?,0)", (i, url, chosen))
    con.commit()


def refs(con):
    return {r[0] for r in con.execute("SELECT ref FROM media")}


def main():
    import urllib.request
    urllib.request.urlopen = _urlopen        # every HEAD in this test is a stub

    con = media_index.index_con()
    live = "https://cdn/live.jpg"
    dead = "https://cdn/dead.jpg"
    flaky = "https://cdn/flaky.jpg"
    ANSWER[dead] = 404
    ANSWER[flaky] = OSError("connection reset")

    print("1. the first pass asks about all three and drops only the dead one")
    seed(con, [(live, 0), (dead, 0), (flaky, 0)])
    del ASKED[:]
    n = media_fetch.prune_dead(con, workers=2)
    check("it asked about every un-probed ref", set(ASKED) == {live, dead, flaky})
    check("one dead ref removed", n == 1)
    check("the 404 is gone", dead not in refs(con))
    check("the live one stays", live in refs(con))
    check("the flaky one stays — 'could not ask' is not 'gone'", flaky in refs(con))

    print("2. the NEXT sync does not ask again about the one that answered")
    del ASKED[:]
    media_fetch.prune_dead(con, workers=2)
    check("the proven-live ref is not re-probed", live not in ASKED)
    check("but the one that never answered IS retried", flaky in ASKED)
    del ASKED[:]
    for _ in range(5):
        media_fetch.prune_dead(con, workers=2)
    check("and it stays quiet across repeat syncs", live not in ASKED)

    print("3. it is a TTL, not a permanent pass — a live URL can die later")
    con.execute("UPDATE media SET probed=? WHERE ref=?",
                (int(time.time()) - media_fetch.PROBE_TTL - 60, live))
    con.commit()
    ANSWER[live] = 410
    del ASKED[:]
    n = media_fetch.prune_dead(con, workers=2)
    check("a stale record is re-probed", live in ASKED)
    check("and the now-dead ref is dropped", live not in refs(con) and n == 1)

    print("4. a materialized ref is still skipped — its bytes are proof enough")
    ANSWER.clear()
    seed(con, [("https://cdn/have.jpg", 1)])
    con.execute("UPDATE media SET sha1='abc'")
    con.commit()
    del ASKED[:]
    media_fetch.prune_dead(con, workers=2)
    check("nothing to ask about", ASKED == [])
    con.close()

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
