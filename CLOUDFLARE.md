# Single sign-on with Cloudflare Access

ludodex can let **Cloudflare Access** authenticate your users and sign them in
automatically — no separate ludodex password. You put ludodex behind an Access
application; Cloudflare authenticates the user (OTP, Google, GitHub, whatever
your team uses) and forwards each request with a signed token. ludodex verifies
that token and signs the request in as the ludodex user you mapped the email to.

## How it works (and why it's safe)

Cloudflare adds two things to every request that reaches ludodex:

- `Cf-Access-Authenticated-User-Email` — the plaintext email (convenient).
- `Cf-Access-Jwt-Assertion` — a **signed JWT** proving the request came through
  your Access application.

ludodex ignores the plaintext header and **verifies the JWT** against your team's
public certificates and this application's **AUD** tag, then reads the email
from the *verified* token. A request that skips Cloudflare can't forge an
identity. Keep the origin private (a Cloudflare **Tunnel** — see below) so the
only way in is through Access.

## 1. Expose ludodex through a Cloudflare Tunnel

Run `cloudflared` (Docker or on the host) and create a tunnel with a public
hostname, e.g. `ludodex.example.com`, pointing at the ludodex origin
(`http://<host>:8001`). Do **not** open port 8001 to the internet directly — the
tunnel is the only public entrypoint.

## 2. Create an Access application

In the Cloudflare **Zero Trust** dashboard → **Access → Applications → Add an
application → Self-hosted**:

- **Application domain:** the hostname from step 1 (`ludodex.example.com`).
- **Policies:** add at least one (e.g. *Allow* the emails/groups who may use
  ludodex). This is who Cloudflare will let reach ludodex at all.
- Save.

## 3. Copy the Team domain and AUD

- **Team domain:** your Zero Trust team domain, `yourteam.cloudflareaccess.com`
  (Zero Trust → Settings → Custom Pages / General shows your team name).
- **AUD tag:** open the Access application → **Overview** → copy the
  **Application Audience (AUD) Tag** (a long hex string).

## 4. Turn it on in ludodex

**Settings → Account & Users → Cloudflare Access:**

1. Paste the **Team domain** and **AUD tag**, click **Save**.
2. Toggle **Enabled**.
3. Under **Email → user mappings**, map each Cloudflare email to a ludodex user
   (create the users first under the **Users** tab). Several emails can map to
   the same user; an email that isn't mapped simply isn't signed in (that person
   can still use a local ludodex login if they have one).

That's it. When someone opens `ludodex.example.com`, Cloudflare authenticates
them and ludodex logs them straight in as their mapped user.

## Notes

- **Local logins still work.** Cloudflare SSO is additive — the admin account and
  any local users keep working, which is handy for access from inside your LAN
  that doesn't traverse Cloudflare.
- **Bypassing SSO for LAN:** requests that don't come through Cloudflare simply
  won't carry a valid token, so they fall back to the normal ludodex login.
- Changing the team domain or AUD? Re-copy them from the Access app's Overview —
  a mismatched AUD makes every token fail verification (users drop to the login
  screen).
