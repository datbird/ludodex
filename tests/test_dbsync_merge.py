import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "ludodex"))
sys.path.insert(0, "/app")
import dbsync as d

cols = ["norm_key", "tag", "created"]


def row(nk, tag, ts):
    return {"norm_key": nk, "tag": tag, "created": ts}


def h(r):
    return d._hash(r, cols)


def run():
    # 1. local-only add -> push to remote
    lu, ld, ru, rd, ns = d.merge({"a": row("a", "x", 1)}, {}, {}, cols)
    assert "a" in ru and not lu and not ld and not rd, ("local-add", lu, ld, ru, rd)

    # 2. remote-only add -> pull to local
    lu, ld, ru, rd, ns = d.merge({}, {"a": row("a", "x", 1)}, {}, cols)
    assert "a" in lu and not ru, ("remote-add", lu, ru)

    # 3. local delete (was synced) -> delete remote
    r = row("a", "x", 1)
    lu, ld, ru, rd, ns = d.merge({}, {"a": r}, {"a": h(r)}, cols)
    assert rd == ["a"] and not ld and not lu and not ru, ("local-del", lu, ld, ru, rd)

    # 4. remote delete (was synced) -> delete local
    r = row("a", "x", 1)
    lu, ld, ru, rd, ns = d.merge({"a": r}, {}, {"a": h(r)}, cols)
    assert ld == ["a"] and not rd, ("remote-del", ld, rd)

    # 5. conflict edit/edit, local newer -> push local
    s = h(row("a", "z", 1))
    lu, ld, ru, rd, ns = d.merge({"a": row("a", "x", 5)}, {"a": row("a", "y", 3)}, {"a": s}, cols)
    assert "a" in ru and not lu, ("conflict-local-newer", lu, ru)

    # 6. conflict edit/edit, remote newer -> pull remote
    lu, ld, ru, rd, ns = d.merge({"a": row("a", "x", 2)}, {"a": row("a", "y", 9)}, {"a": s}, cols)
    assert "a" in lu and not ru, ("conflict-remote-newer", lu, ru)

    # 7. conflict local-delete vs remote-edit -> keep the edit (pull), never lose data
    lu, ld, ru, rd, ns = d.merge({}, {"a": row("a", "y", 3)}, {"a": s}, cols)
    assert "a" in lu and not rd, ("del-vs-edit", lu, rd)

    # 8. in-agreement -> no-op, shadow retained
    r = row("a", "x", 1)
    lu, ld, ru, rd, ns = d.merge({"a": r}, {"a": r}, {"a": h(r)}, cols)
    assert not (lu or ld or ru or rd) and ns["a"] == h(r), ("noop", lu, ld, ru, rd, ns)

    # 9. multi-record mixed round
    la = {"a": row("a", "x", 1), "b": row("b", "y", 1)}          # a synced, b new local
    re = {"a": row("a", "x", 1), "c": row("c", "z", 1)}          # a synced, c new remote
    sh = {"a": h(row("a", "x", 1))}
    lu, ld, ru, rd, ns = d.merge(la, re, sh, cols)
    assert "b" in ru and "c" in lu and not ld and not rd, ("mixed", lu, ru)
    assert set(ns) == {"a", "b", "c"}, ("mixed-shadow", ns)

    print("ALL 9 MERGE TESTS PASS")


run()
