# Golden corpus

Images the live conformance suite judges. Committed rather than fetched, because a test
that depends on a provider still hosting a URL fails for reasons that have nothing to do
with the code under test — and a wrong-art regression is exactly the thing you cannot
afford to have masked by a 404.

Each is a downscaled thumbnail (~256px, tens of KB) because that is what the vision path
actually sends: `_thumb_bytes` downscales before the model ever sees an image, so
judging full-resolution originals would be testing a payload the product never builds.

Provenance is recorded in `manifest.json` — where each came from and what it depicts —
so a later reader can re-derive the set instead of trusting the filenames.
