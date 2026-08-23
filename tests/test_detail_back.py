#!/usr/bin/env python3
"""The detail overlay remembers where it has been, so it can go back.

datbird, after clicking through to a DLC:

  "when I click on a dlc and look at its medadata/media, it should open up in the same
   window and have a back button for going back to the parent"

The same-window half already worked: `onNavigate` was `setSelected`, which swaps the
entry inside the one overlay. What was missing is history. Clicking Quake Mission Pack 1
left no way back to Quake except closing and finding it again.

ONE TRAIL, NOT A RETURN LINK PER FEATURE. Every in-overlay jump goes through
`onNavigate`: an add-on, a sibling platform ("also owned on"), a collection member, a
compilation. Giving each of those its own bespoke "back to X" affordance is the same
one-rule-many-homes mistake the 2026-08-21 audit was about, and they would drift. So the
trail lives beside `selected` and every jump pushes to it.

Two properties worth pinning, because both are easy to lose:
  * Back is offered ONLY when there is somewhere to return to, so it is never a dead end.
  * Closing the overlay clears the trail. A trail that outlives its overlay would send
    Back to a page the user never opened this time.
"""
import os
import re
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "ludodex"))
import test_support                              # noqa: E402
test_support.isolate("ludodex-back-")

APP = open(os.path.join(DIR, "web", "src", "App.tsx"), encoding="utf-8").read()
CSS = open(os.path.join(DIR, "web", "src", "App.css"), encoding="utf-8").read()


def check(label, cond):
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: " + label)


def main():
    print("1. the trail exists and every jump goes through it")
    check("a trail sits beside `selected`",
          re.search(r"const \[trail, setTrail\] = useState<string\[\]>\(\[\]\)", APP)
          is not None)
    check("openEntry pushes the entry being left",
          re.search(r"setTrail\(\(t\) => \(selected && selected !== key \? \[\.\.\.t, selected\] : t\)\)",
                    APP) is not None)
    check("it never pushes the page you are already on", "selected !== key" in APP)
    check("goBack pops one step", "setTrail(trail.slice(0, -1))" in APP)
    check("the overlay navigates via openEntry, not raw setSelected",
          "onNavigate={openEntry}" in APP and "onNavigate={setSelected}" not in APP)

    print("2. Back works even with NO history, which a stack alone cannot do")
    # A trail only exists if you arrived from another entry this session. Open an add-on
    # directly, or reload while on one, and React state is empty. The entry knows its
    # parent, so it can always go up. That is what was actually asked for, and the first
    # version missed it.
    check("onBack is undefined with an empty trail",
          "onBack={trail.length ? goBack : undefined}" in APP)
    check("Detail accepts onBack", re.search(r"function Detail\(\{[^}]*onBack", APP)
          is not None)
    check("the button renders when there is a trail OR a parent",
          "{(onBack || d?.extends) && (" in APP)
    check("with no trail it navigates to the parent",
          "onBack ? onBack() : onNavigate?.(d!.extends!.entry_key)" in APP)
    check("the title names where it goes", "Back to ${d?.extends?.title}" in APP)
    check("it is labelled for assistive tech", 'aria-label="Back"' in APP)

    print("2b. goBack does not mutate state from inside an updater")
    # setSelected inside a setTrail updater is a side effect during the update phase.
    check("goBack reads the trail directly",
          re.search(r"const goBack = \(\) => \{\s*if \(!trail\.length\) return", APP)
          is not None)
    check("and does not call setSelected inside setTrail",
          "setTrail((t) => {" not in APP.split("const goBack")[1].split("}")[0] + "")

    print("3. closing forgets the trail")
    check("onClose clears it",
          re.search(r"onClose=\{\(\) => \{ setSelected\(null\); setTrail\(\[\]\); ",
                    APP) is not None)

    print("4. it occupies a corner nothing else claims")
    # The first version used right:54px. That is .hero-tools' exact slot, and hero-tools
    # carries z-index 9 against the close button's 5, so the toolbar painted over the
    # arrow: elementFromPoint at its centre returned the toolbar, and on a phone it was
    # neither visible nor tappable. Measured in a real 390px viewport.
    check("detail-back is styled", ".detail-back" in CSS)
    rule = re.search(r"\.game-panel \.close\.detail-back \{([^}]*)\}", CSS)
    check("the rule exists", rule is not None)
    body = rule.group(1) if rule else ""
    check("it sits on the LEFT, away from close and hero-tools", "left: 10px" in body)
    check("and cancels the inherited right offset", "right: auto" in body)
    check("NEVER right:54px, which is hero-tools' slot", "right: 54px" not in body)
    tools = re.search(r"\.hero-tools \{([^}]*)\}", CSS)
    check("hero-tools still claims right:54px, so the clash was real",
          tools is not None and "right: 54px" in tools.group(1))
    check("back is raised to hero-tools' layer so nothing paints over it",
          "z-index: 9" in body)

    print("test_detail_back: all checks passed")


if __name__ == "__main__":
    main()
