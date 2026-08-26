# Testing

```bash
./scripts/run_tests.sh          # everything offline, in a throwaway container
```

That is the whole routine answer. The rest of this page is about the tests that need a
running instance, and about why they exist.

## Why there are three kinds

On 2026-08-26 this repo had 180 passing test files while the app was visibly broken three
separate times in one day. The wand silently did nothing, the hero preference wrote to a
key matching no game, and every game page lost its hero and background art.

Every one of those bugs lived in the same place: the **seam** between the UI and the API.
The UI started sending a new key shape and several server paths still expected the old
one. Each returned a well-formed, empty, `200` answer, so nothing raised.

A unit test cannot catch that, and it is worth being precise about why. A unit test builds
its own fixture, so it can only ever prove what its author already believed. If the author
believed the key was `doom@gba`, the test says so, and says nothing about the day the UI
starts sending `igdb:2155`.

So there are three kinds, and each catches what the others cannot.

| kind | needs | catches |
|---|---|---|
| **unit** (~180 files) | nothing | logic, in isolation, fast |
| **contract** (`test_live_ui_contract.py`) | a running instance | the UI/API seam |
| **render** (`test_live_browser_detail.py`) | a browser too | whether anything reached the screen |

Plus one **lint** with no dependencies at all: `test_routes_resolve_their_keys.py` reads
`server/app.py` and fails if a route takes a game key and queries by `norm_key` without
resolving it first. That is the media bug, caught at the moment somebody writes it.

## The live tests

Both are **read-only** and **free**. They perform no writes and touch no AI endpoint,
deliberately: the wand is checked by proving the key it *would* send resolves to a real
game, never by running a scan. Nothing here can spend money.

Both skip unless you ask for them, so a routine sweep never opens a socket:

```bash
export LUDODEX_LIVE_TESTS=1
export LUDODEX_URL=http://<your-instance>:8001
export LUDODEX_USER=<a non-admin user>   LUDODEX_PASS=<their password>

python3 tests/test_live_ui_contract.py
```

Use a **non-admin** account. Nothing here needs more, and a test that runs as an
administrator is a test that can do more damage than it can prove.

### The browser one

Playwright is not a dependency of this project and does not need to be installed into it.
The browser may run on a completely different machine; point the test at an endpoint.

```bash
export LUDODEX_PLAYWRIGHT=/path/to/some/node_modules   # any install that has playwright
export LUDODEX_BROWSER_WS=ws://127.0.0.1:9223/         # a playwright run-server endpoint

python3 tests/test_live_browser_detail.py
```

The assertions are in `tests/browser/detail-render.mjs`.

**It never asserts `isVisible()`.** That is not proof: an element can be "visible" while
painted over, and a broken image is still visible. Every assertion is physical instead.
`naturalWidth > 0` means the bytes arrived and decoded. `document.elementFromPoint` at an
element's own centre means nothing is covering it. The one exception is the hero
background, which is *meant* to have the title on top of it, so that check asks only that
the image decoded.

It also watches the network and fails on any `/api/media` request that errors, which is
the signal the missing-hero regression never produced on its own.

## Writing one that is worth having

**A regression test nobody has ever seen fail is worth very little.** Most of the checks
above assert "not empty", and the bug they guard against was a `200` with an empty body.
So `test_live_ui_contract.py` ends with a **canary**: it asks the same endpoints about a
game that cannot exist and confirms they answer the way the broken state answered. If the
canary stops holding, the checks above have gone blind and are passing for free.

Two habits, both learned the hard way:

- **Never construct the key.** Ask the API for a game, then use the key the API handed
  back. A test that builds its own key is a test that proves only what you assumed.
- **Wait for a state, do not probe for one.** `count()` on a React page that has not
  rendered returns `0`, which reads as "already signed in" and then times out somewhere
  else entirely. That is a flaky test writing itself.
