#!/usr/bin/env python3
"""Embedded ScreenScraper *developer* credentials — the app's identity to
screenscraper.fr. Shipping these with ludodex means every deployment (yours or
anyone else's) authenticates as this one recognizable client, so the
ScreenScraper team sees a single app rather than a swarm of anonymous callers.

IMPORTANT — this is deliberate OBFUSCATION, not secrecy. A credential that ships
inside a client can never be truly hidden: the de-obfuscation key travels with
it. Treat the devid as effectively public. This only keeps it out of plaintext
and out of the Settings UI. The end-user's ScreenScraper *account* login
(ssid/sspassword, which raises the request quota) is a different thing entirely —
it stays per-deployment in config/Settings and is never embedded here.

THERE IS NO ROTATION STORY, and nothing here should be read as implying one.
This credential is a product identity, not a session secret. ScreenScraper issues
a devid by manual approval on their dev forum, which takes days to weeks, and a
replacement would reach only installs that pull a new build — every copy running
the old one would simply stop scraping. So "rotate it" is not a mitigation that
exists; the mitigations that DO exist are the two below.

  * Blast radius is bounded by design. The devid sets which SOFTWARE is calling.
    The request tier and daily quota come from the END USER's own ssid/sspassword,
    which is never embedded. Someone abusing the shipped identity burns the
    shared software's allowance, not any individual user's account.
  * Anyone who wants a separate identity can supply one, without a code change:
    env SS_DEVID / SS_DEVPASSWORD, or config keys screenscraper_devid /
    screenscraper_devpassword. Resolution is env > config > embedded
    (see config.screenscraper_creds).
"""
import base64

# devid + devpassword, XOR'd against the key below then base64'd. Reversible by
# design — see the module docstring. Not encryption; do not treat as a secret.
_KEY = b"ludodex-screenscraper-app-identity"
_BLOB = "CBQQDQ0XHCQqGiE9VFY9NwADHQ=="


def _deobfuscate(blob, key):
    raw = base64.b64decode(blob)
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(raw)).decode()


def dev_credentials():
    """(devid, devpassword) for the ScreenScraper software identity."""
    devid, devpw = _deobfuscate(_BLOB, _KEY).split("\t", 1)
    return devid, devpw


if __name__ == "__main__":       # quick self-check (prints devid only)
    d, _ = dev_credentials()
    print("embedded ScreenScraper devid:", d)
