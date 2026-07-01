# ludodex — AI features & model access (BYOAI)

How the AI-forward server's Claude features (natural-language search, smart art/
metadata picks, dedupe assist — see `HANDOFF.md` §6.5) get their model access.

ludodex is **BYOAI**: each deployment picks one backend. The right choice depends on
**who uses the instance**, because Anthropic licenses *subscriptions* and the
*developer API* very differently.

> ⚠️ **Time-sensitive.** The subscription/credit details below were accurate as of
> **June 2026** and Anthropic changes them. Re-check the linked help-center pages
> before relying on specifics (especially the "paused" credit program).

---

## TL;DR — which backend for which deployment

| Your deployment | Use | Why |
|---|---|---|
| **You self-host for yourself only** (your machine, your Claude plan, only you task it) | **Subscription** (`claude_cli` / Agent SDK) | Allowed; no API bill; runs on the plan you already pay for |
| **Anyone else uses your instance** (multi-user, hosted, shared) | **API key** | A personal subscription may not serve other people |
| **Distributed app, each user brings their own plan** | API key (and optionally each user's own subscription) | Don't broker logins for others |

---

## Option A — Your own Claude subscription (Pro / Max / Team / Enterprise)

For the **single person who owns the subscription, self-hosting ludodex for their own
use**, the server can drive the official Claude tooling (the `claude` CLI in
non-interactive `claude -p` mode, or the Claude Agent SDK) authenticated by that
person's own subscription. No API key, no separate bill.

**What you get / how it bills (as of June 2026):**

- Anthropic announced a **monthly Agent SDK credit** tied to subscriptions —
  **Pro $20 · Max 5x $100 · Max 20x $200** per month (Team/Enterprise vary). It is
  meant to cover Agent SDK usage, `claude -p`, GitHub Actions, and *third-party apps
  that authenticate with your Claude subscription through the Agent SDK*. Raw
  developer-API-key usage is **not** covered by it.
- **This credit program is currently _paused_.** While paused, *"Claude Agent SDK,
  `claude -p`, and third-party app usage still draw from your subscription's usage
  limits."*
- **Practical consequence right now:** anything ludodex runs through your subscription
  **counts against your normal 5-hour / weekly plan limits** — the same budget as your
  interactive Claude Code and chat. There is **no separate pool today**. A heavy
  agentic job (e.g. "analyze 40 uploaded box-art photos") can draw those limits down.
- **When/if un-paused:** the design is that Agent SDK / `claude -p` usage *"no longer
  counts toward your Claude plan's usage limits"* and instead spends the dedicated
  monthly credit; once that's exhausted it falls through to pay-as-you-go usage
  credits **only if you've enabled them**.

**What is NOT allowed with a subscription:**

- Serving **other people** through one subscription (account sharing).
- *"Offering claude.ai login or rate limits for [your] products"* — i.e. routing other
  users through Claude on your plan. That requires API keys.
- A subscription includes **zero raw developer-API credits**; the API
  (`console.anthropic.com`) is billed separately, pay-as-you-go. (New API accounts get
  a one-time ~$5 starter credit; there is no recurring free API tier.)

**Setup:** install the official Claude Code CLI and log in with your subscription
(`claude` → follow the login flow). ludodex's `claude_cli` provider then shells out to
`claude -p`. No credential is stored by ludodex.

---

## Option B — A developer API key (provider-agnostic)

Works **everywhere**, including multi-user / hosted deployments, and keeps AI usage off
your interactive subscription limits. This is the required path for serving anyone but
yourself. Supported providers:

| Provider | Notes |
|---|---|
| **Anthropic API** | Claude direct; pay-as-you-go per token. The plan's locked default for Claude. |
| **OpenRouter** | Reaches Claude (and other models) via one key; billed through OpenRouter. |
| **OpenAI / Gemini / others** | True provider-agnostic BYOAI for non-Claude backends. |
| **Bedrock / Vertex / Azure** | Cloud-provider routing — also API billing, fine for products. |

The Anthropic **Agent SDK** is governed by Anthropic's **Commercial Terms** when used
to power products/services for your own customers — i.e. use **API-key auth** for that,
not subscription OAuth.

**Setup:** provide the key via environment variable or ludodex config (never commit it;
see `AUTH.md` for the credential-handling convention). Resolution order:
`ANTHROPIC_API_KEY` (env) → ludodex config `anthropic_api_key` → unset (AI disabled).

---

## How ludodex selects a backend

The AI layer is a **pluggable provider registry**; a deployment selects one. If no
backend is configured the AI endpoints return a clean "not configured" and the Web UI
falls back to ordinary text/faceted search — the catalog, media, and browsing work with
no AI at all.

Model tiering (when on Claude): cheap extraction (NL→query) uses **Haiku**; heavier
reasoning (art/dedupe assist) steps up to **Sonnet/Opus**.

---

## Sources (verify currency before relying on these)

- Subscription vs. API are billed separately — https://support.claude.com/en/articles/9876003
- Agent SDK monthly credit + the "paused" notice — https://support.claude.com/en/articles/15036540
- Usage credits for paid plans — https://support.claude.com/en/articles/12429409
- Claude Code with Pro/Max — https://support.claude.com/en/articles/11145838
- Agent SDK auth & Commercial Terms — https://code.claude.com/docs/en/agent-sdk/overview
