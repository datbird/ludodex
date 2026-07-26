# Image Orientation as a First-Class, Correctable Attribute — Design

Date: 2026-07-25
Status: draft — for review

## Summary

Give every image an **orientation** (portrait / landscape / square) that is measured
automatically, correctable by the user in one click, and usable when serving art to other
systems. Orientation becomes a second axis alongside `kind`, so a frontend that wants a
wide banner can be served the best *landscape* asset regardless of which kind bucket it
happens to sit in.

## Why this exists

The Steam-filler defect is the general case in disguise. Steam auto-generates a
`portrait.png` for games with no library art by pasting the ~460×215 header onto a 600×900
canvas and blur-filling the rest. It is **geometrically portrait and visually landscape**.

> Measured orientation and *effective* orientation disagree — and that gap is the bug.

`media.looks_padded()` now detects that specific case deterministically (146 of 150 live
candidates confirmed, 4 genuine covers correctly left alone). But a heuristic will always
have a residue, and there is currently no way for a human to say "you got this one wrong."
This spec generalises the idea and adds the override.

## Model

Two orthogonal axes, and conflating them would lose information:

- **`kind`** = the asset's ROLE (cover, hero, logo, box_back…). Already exists.
- **`orientation`** = the asset's SHAPE-AS-PERCEIVED. New.

A portrait image is not necessarily a cover — `box_back` and `box_spine` are portrait too.
So orientation must not collapse into kind.

Three sources of truth, in ascending authority:

| Source | Where from | Authority |
|---|---|---|
| **measured** | `width`/`height` at index/materialize | baseline |
| **effective** | content analysis (`looks_padded`, letterbox detection) | overrides measured when it can prove a disagreement |
| **override** | one user click | absolute |

`orientation_effective = override or effective or measured`.

## Storage

`media-index.sqlite` is rebuilt by the pipeline, so a user decision **cannot** live there —
same reasoning that put pins, framing, ownership and media-flags in durable sidecars.

- **Derived** (`width`, `height`, `filler`) stay on `media` — regenerable, already there.
- **Override** goes in a durable sidecar `orientation.sqlite`, keyed the way pins are
  (`norm_key`, `kind`, `provider`, `ref`) so it survives rebuilds without an id:

```sql
orientation_override(
  norm_key TEXT, kind TEXT, provider TEXT, ref TEXT,
  orientation TEXT,          -- 'portrait' | 'landscape' | 'square'
  origin TEXT,               -- 'user'
  updated REAL,
  PRIMARY KEY(norm_key, kind, provider, ref))
```

## Correction semantics

A user click does two things at once, which is what makes it worth having:

1. **Reclassifies** the image into the correct orientation bucket.
2. **Promotes** it to #1 within that bucket — the user just looked at it and made a
   judgement, which outranks every heuristic.

Everything below shifts up one to close the gap, and the bucket it *left* also closes.
This is exactly the existing pin semantics (`pin` is already the first term in
`media_choose.select()`'s sort key, ahead of shape, filler and provider priority), so the
promotion half is largely existing machinery rather than a new ordering concept.

Corollary worth stating plainly: correcting a Steam filler to "landscape" simultaneously
removes it from cover contention **and** makes it the preferred banner — one click fixes
two things, which is the whole appeal.

## Serving by orientation

The new capability, and the reason this is more than a bug fix. Consumers differ:

- ES-DE / RetroDECK want portrait box art in some views, wide marquees in others.
- Playnite and LaunchBox have their own per-slot expectations.
- A future device layer may want "the best wide image you have, whatever you call it."

So the resolver gains an orientation-qualified lookup alongside the existing kind lookup —
`best(norm_key, orientation='landscape')` — falling back to kind-based selection when a
consumer expresses no preference. Existing callers are unaffected.

## UI

In the media overlay, each image gets a small orientation control showing the current
value and its source (measured / detected / **yours**). One click cycles or sets it. The
same disclosure pattern as the tier ⓘ expander: state what it is and where it came from,
so a user can tell an automatic guess from their own decision.

## Open questions

1. **Does an override imply a `kind` change?** If a user marks a cover "landscape", should
   it move to `header`/`hero`, or stay a cover that is merely mis-shaped? Recommendation:
   no automatic kind change — orientation and role are separate, and silently re-bucketing
   would surprise. The demotion from cover contention happens naturally via the shape test.
2. **Square.** Tolerated everywhere today (`shape_ok`). Should a user be able to assert it,
   or is it purely derived?
3. **Propagation.** One appid's art is often shared across entries (`game_key` matching in
   the serve resolver). Does an override apply to the asset everywhere it appears
   (recommended — it's a fact about the image) or only in the entry where it was set?

## Out of scope

- Re-picking already-materialized art. Orientation is selection-time and applies on the
  next select pass.
- Any change to the tiered-ingest spend rules.

## Verification

1. An override survives a full catalog rebuild (the sidecar test every durable store gets).
2. Setting an override promotes that asset to `chosen` for its corrected orientation, and
   the previous holder falls to second without being deleted.
3. Clearing an override restores the derived value — a user decision must be reversible.
4. `best(nk, orientation=…)` returns a correctly-oriented asset across kinds, and falls
   back cleanly when a game has none.
5. Orientation never mutates `kind` (open question 1, once decided).
