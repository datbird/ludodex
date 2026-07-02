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

Override at runtime any time with env SS_DEVID / SS_DEVPASSWORD, or config keys
screenscraper_devid / screenscraper_devpassword (see config.screenscraper_creds).
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
