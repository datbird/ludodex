#!/usr/bin/env python3
"""A trademark symbol SEPARATES words. It does not join them.

`norm()` deleted the symbol outright, so a title that sets it flush against the next
word lost the word break with it:

    "Puyo Puyo(TM)Tetris(R)"  ->  puyo puyotetris
    "ACE COMBAT(TM)7: SKIES UNKNOWN"  ->  ace combat7 skies unknown

Neither key matches the same game bought anywhere else. Live, the Steam copy of Puyo
Puyo Tetris filed under `puyo puyotetris` and the Switch copy under `puyo puyo tetris`,
and both then claimed IGDB 6866 — invariant I9, one provider id held by two titles. The
loser of that pair inherits the winner's art and metadata, which is the harm I9 names.

The symbol becomes a SPACE. Measured over the live 2,488-entry library, that changes
exactly two norm_keys, and both are the corrections above. A title that already has a
space around the symbol is unaffected, because the extra space collapses in `split()`.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "ludodex"))
import test_support                              # noqa: E402
D = test_support.isolate("ludodex-tmnorm-")

PASS = []


def check(label, cond):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    import titlenorm

    # --- the flush symbol keeps the word break -------------------------------------
    check("a flush trademark separates the words it sits between",
          titlenorm.norm("Puyo Puyo™Tetris®") == "puyo puyo tetris")
    check("a flush trademark before a number separates it too",
          titlenorm.norm("ACE COMBAT™7: SKIES UNKNOWN")
          == "ace combat 7 skies unknown")
    check("the copyright sign behaves the same way",
          titlenorm.norm("Foo©Bar") == "foo bar")

    # --- the two copies of one game now agree --------------------------------------
    # This is the whole point: the Steam title and the plain title reach ONE key, so one
    # IGDB id cannot be claimed by two of them.
    check("the trademarked title and the plain title reach the same key",
          titlenorm.norm("Puyo Puyo™Tetris® ")
          == titlenorm.norm("Puyo Puyo Tetris"))

    # --- nothing else moves --------------------------------------------------------
    # A symbol that already had whitespace around it, or that trails the title, must
    # produce the same key it always did. The added space collapses on split().
    check("a trailing symbol is still just dropped",
          titlenorm.norm("Half-Life™") == "half life")
    check("a symbol with a space already after it is unchanged",
          titlenorm.norm("BIOSHOCK™ INFINITE") == "bioshock infinite")
    check("a title with no symbol is untouched",
          titlenorm.norm("The Legend of Zelda") == "legend of zelda")

    print("RESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
