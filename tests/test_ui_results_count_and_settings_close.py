#!/usr/bin/env python3
"""Two small UI defects the live browser suite (tests/browser/ui_full.py) found.

  * The results bar said "1 results". It now says "1 result".
  * On a 390px phone the Settings close button covered the section row. That row is a
    horizontal scroller, and it tried to clear the button with right PADDING, which in
    a scroll container only adds room after the last item: anything scrolled under the
    button was still covered. The row now stops short of the button (a margin the size
    of the button's gutter, per breakpoint), and a shadow in the row's own colour fills
    the gap so it still reads as one bar. The divider moves to the top of
    `.settings-main` so it still spans the full width.

Offline: App.tsx and App.css read as text.
"""
import os
import re
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_support                                            # noqa: E402
test_support.isolate("ludodex-ui-small-")

APP = open(os.path.join(DIR, "web", "src", "App.tsx"), encoding="utf-8").read()
CSS = open(os.path.join(DIR, "web", "src", "App.css"), encoding="utf-8").read()
PASS = []


def check(label, cond, detail=None):
    PASS.append((label, bool(cond)))
    print("  %s   %s" % ("ok " if cond else "FAIL", label))
    if not cond:
        sys.exit("FAILED: %s%s" % (label, "" if detail is None else "  (%r)" % (detail,)))


def media_block(query, needle):
    """The body of the `@media (<query>)` block that contains `needle`."""
    for m in re.finditer(r"@media \(%s\) \{" % re.escape(query), CSS):
        depth, i = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(CSS[i], 0)
            i += 1
        body = CSS[m.end():i - 1]
        if needle in body:
            return body
    return ""


def rule(block, selector):
    m = re.search(r"(?m)^\s*%s \{([^}]*)\}" % re.escape(selector), block)
    return m.group(1) if m else ""


def px(decl, block):
    m = re.search(r"%s:\s*(\d+)px" % re.escape(decl), block)
    return int(m.group(1)) if m else None


def main():
    print("1. the results count is pluralised")
    check("no literal '} results' left in the results bar",
          "{total.toLocaleString()} results" not in APP)
    check("1 reads 'result', anything else 'results'",
          "{total.toLocaleString()} result{total === 1 ? '' : 's'}" in APP)

    print("2. the Settings section row stops short of the close button")
    tab = media_block("max-width: 900px", ".settings-nav {")
    nav = rule(tab, ".settings-nav")
    check("the tablet block styles the section row", bool(nav))
    check("the row no longer relies on right padding to clear the button",
          not re.search(r"padding:\s*\S+\s+(4\d|5\d|6\d)px", nav), nav)
    check("it ends a gutter before the window edge",
          "margin-right: var(--settings-close-gutter)" in nav, nav)
    check("the gap behind the button is filled in the row's colour",
          "box-shadow: var(--settings-close-gutter) 0 0 var(--bg)" in nav)
    check("the divider moved to .settings-main so it still spans the width",
          "border-bottom: 0" in nav
          and "border-top: 1px solid var(--border)" in rule(tab, ".settings-main"))

    # The gutter has to cover the button at each width, or the bug is back.
    base_close = rule(CSS, ".close")
    gut_tab = px("--settings-close-gutter", tab)
    need_tab = px("right", base_close) + px("width", base_close)
    check("tablet gutter (%s) covers the %spx the close takes" % (gut_tab, need_tab),
          gut_tab is not None and gut_tab >= need_tab)
    phone = media_block("max-width: 600px", ".settings-window {")
    phone_close = rule(phone, ".close")
    gut_phone = px("--settings-close-gutter", phone)
    need_phone = px("right", phone_close) + px("width", phone_close)
    check("phone gutter (%s) covers the %spx the close takes" % (gut_phone, need_phone),
          gut_phone is not None and gut_phone >= need_phone)

    print("\nRESULT: %d checks, all passed" % len(PASS))


if __name__ == "__main__":
    main()
