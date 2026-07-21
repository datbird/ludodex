# ludodex — task queue

The working backlog. Numbers are stable task IDs referenced in commit messages
(`feat(match-confidence): … (#13)`). Per-task design docs live in
`docs/superpowers/specs/`; execution plans in `docs/superpowers/plans/`.

Last reviewed: 2026-07-21.

---

## Open

Nothing is open pending code. Two items are **data-gated** — the work shipped, but the
remaining half can only be done against a populated catalog with a configured AI provider:

| # | Task | What's left |
|---|---|---|
| 2 | Firebase backing store | Adapter is written, registered, and protocol-tested offline (`test_dbsync_firestore.py`). A **live round-trip** needs a Firebase project id + service-account key entered in Settings → Connections → Backup & restore. |
| 3 | Auto-fix confidence tuning | The gate is now a setting (Settings → Library → "Automatic fixes") instead of a hardcoded 0.75. Choosing the *right* value needs a real library to observe false positives against. |

## Recently completed

| # | Task | Commit |
|---|---|---|
| 2 | Firestore adapter protocol test (pagination, batching, doc ids, no-op re-sync) | `21d7da3` |
| 3 | `auto_fix_confidence` setting driving all three AI-gated auto-fixes | `93982dc` |
| 4 | Live cover preview before applying a smart art pick | `11b66c9` |
| 10 | Click-to-enlarge review thumbnails (`ImageLightbox`, ← / → / Esc) | `b6658bb` |
| 11 | Entrance animation on every overlay, not just game detail | `b6658bb` |
| 12 | Systematic compilation detection during a wand scan | `5a6313e` |
| 14 | Error boundaries + `hooksweep.mjs` build guard | `3f76969` |
| 15 | Files → Browse opens at a remembered/library path, not `/` | `b9b130f` |
| 16 | "Backup & restore" vs "Publish catalog" renaming | `b9b130f` |
| 5 | Amiga CD32 hardware-token strip / re-platform | `84d2ecf` |
| 6 | Wand provenance + release-type + mismatch warning in review | `9cfdcd1` |
| 8 | Per-entry identity resolution wired into the wand scan | `bc5cd8f` |
| 9 | Legacy fuzzy-match scrub (`igdb_enrich --scrub-fuzzy`) | `b34eb63` |
| 13 | Match confidence (score, facet, dashboard card, chips, settings) | `4ca9dda` … `9641575` |
| — | First-run fixes: catalog-seed 500, phantom Sources count, Settings white-screen | `6d757c4` `6019cbb` `17be9ce` |

Task IDs 1 and 7 are not recorded in any surviving note — treat those numbers as retired
rather than assuming there is missing work behind them.

## Rebuild-dependent

These shipped but only take effect on the next full catalog build (`build_library`, via the
Server-ops → Rebuild button or a scan). On a freshly-wiped install they stay at zero until
the first sync + build:

- Match-confidence rule-based baseline across all identified entries (#13)
- CD32 re-key (#5)
- Fuzzy-match scrub results (#9)

## Guards

- `web/scripts/hooksweep.mjs` runs inside `pnpm build`: a React hook after an early return
  (or in a conditional/loop/callback) fails the build. It exists because oxlint has no
  `react-hooks/rules-of-hooks` rule and this repo has no eslint, so nothing caught the
  white-screen bug of `17be9ce`. Run it alone with `pnpm lint:hooks`.
- Error boundaries wrap the app root and each settings panel, so a render error degrades to
  a recoverable inline message rather than a blank page.

## Parked / blocked

- **ScreenScraper** — code complete, blocked on a forum devid/devpassword. See `DESIGN.md` §11.
- **Nintendo source** — PKCE login verified and UI-wired; `fetch_owned()` is still a TODO.
- **Device layer v1** (push-only) — designed in `DESIGN.md`, not built.
- **Discover / want-vs-have capstone** — store sale-watching over the wanted list. Wishlist
  import (Steam, GOG) and the Wanted attribute exist; the universe browser and price watching
  do not.
- **Art importers for Epic / EA / GOG / itch** — provider art-type → kind maps are researched
  and in place, but no fetch module exists; those stores are ownership-only today.
- **Magic-animations research** — evaluate a reference site's scroll animations for possible UI
  use. Discussion first, nothing started.

## Open design decision

- **Selection policy** (`DESIGN.md` §9) — which games push to a device: allowlist / tag /
  platform / all-playable. Gates the device-layer push UX, not the server.

---

## Doc hygiene

`HANDOFF.md` predates the server build and still describes the AI-forward server as the one
open task. It needs a rewrite (or retirement in favour of `DESIGN.md` + this file) before it
misleads anyone picking the project up.
