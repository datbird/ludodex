# ludodex — task queue

The working backlog. Numbers are stable task IDs referenced in commit messages
(`feat(match-confidence): … (#13)`). Per-task design docs live in
`docs/superpowers/specs/`; execution plans in `docs/superpowers/plans/`.

Last reviewed: 2026-07-21.

---

## Open

| # | Task | Notes |
|---|---|---|
| 2 | Firebase two-way backing-store adapter + live test | The merge engine and SQL/PocketBase adapters are done; Firebase is the remaining backend. Also unbuilt: auto/periodic sync, conflict log/UI. |
| 3 | Contamination confidence-threshold tuning | Detach fires at AI confidence ≥ 0.75. Revisit only if false positives/negatives show up in real use. |
| 4 | Live cover-view preview | Preview a cover change before committing it. |
| 10 | Tap-to-enlarge review thumbnails | Wand review strip thumbs are too small to judge art. |
| 11 | Hero-expand to all overlays | The hero-expand interaction currently applies to a subset of overlays. |
| 12 | Compilation auto-detection | AI detects collections during a metadata scan today; make it a systematic pass rather than incidental. |
| 14 | Hook-after-early-return sweep + React error boundary | `17be9ce` fixed one instance (React #310, Settings → Library white-screen). Sweep for siblings and add a boundary so a single bad component can't blank the app. |
| 15 | Files → Browse defaults to server root | Fresh-install UX: opening at `/` is unhelpful. Default to a configured library path. |
| 16 | "Backing store" vs "Database sync" naming | Two distinct external-DB features with confusingly similar names. Backing store = two-way durable sync of user stores; Database sync = one-way catalog mirror. Rename and/or merge the Settings surfaces. |

## Recently completed

| # | Task | Commit |
|---|---|---|
| 5 | Amiga CD32 hardware-token strip / re-platform | `84d2ecf` |
| 6 | Wand provenance + release-type + mismatch warning in review | `9cfdcd1` |
| 8 | Per-entry identity resolution wired into the wand scan | `bc5cd8f` |
| 9 | Legacy fuzzy-match scrub (`igdb_enrich --scrub-fuzzy`) | `b34eb63` |
| 13 | Match confidence (score, facet, dashboard card, chips, settings) | `4ca9dda` `8327ea6` `37d85f6` `7c26ef6` `88da2ac` `9641575` |
| — | First-run fixes: catalog-seed 500, phantom Sources count, Settings → Library white-screen | `6d757c4` `6019cbb` `17be9ce` |

Task IDs 1 and 7 are not recorded in any surviving note — treat those numbers as retired
rather than assuming there is missing work behind them.

## Rebuild-dependent

These shipped but only take effect on the next full catalog build (`build_library`, via the
Server-ops → Rebuild button or a scan). On a freshly-wiped install they stay at zero until
the first sync + build:

- Match-confidence rule-based baseline across all identified entries (#13)
- CD32 re-key (#5)
- Fuzzy-match scrub results (#9)

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
