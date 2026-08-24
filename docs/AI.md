# ludodex — AI features & model access (BYOAI)

How ludodex's AI features get their model access, and what each one is for.

ludodex is **BYOAI**: you bring an API key. Every AI feature is optional — with no key
configured the AI endpoints return a clean "not configured" and the UI falls back to
ordinary text and faceted search. The catalog, media and browsing work with no AI at all.

---

## The four providers

`server/ai.py`'s `PROVIDERS` registry is the whole surface. All four are **API-key
based**:

| provider id | key resolution | default model |
|---|---|---|
| `anthropic` | env `ANTHROPIC_API_KEY` → config `anthropic_api_key` | `claude-haiku-4-5-20251001` |
| `openai` | env `OPENAI_API_KEY` → config `openai_api_key` | `gpt-5-mini` |
| `gemini` | env `GEMINI_API_KEY` → config `gemini_api_key` | `gemini-flash-latest` |
| `openrouter` | env `OPENROUTER_API_KEY` → config `openrouter_api_key` | `anthropic/claude-haiku-4.5` |

OpenRouter speaks the OpenAI-compatible protocol and reaches many vendors' models
through one key, so it is the route to a model ludodex has no direct adapter for.

The active provider is `AI_PROVIDER` (env) → config `ai_provider` → the first provider
that has a key. **An explicit choice is honoured or it is nothing**: if you name a
provider and it has no key, AI is off rather than quietly billing a different one.

Per-provider model overrides are config keys `anthropic_model`, `openai_model`,
`gemini_model`, `openrouter_model`.

> **There is no subscription, `claude_cli`, Agent SDK, Bedrock, Vertex or Azure
> provider.** This page used to describe all of them, at length, including setup
> instructions. None of it was ever implemented — `grep -rn 'claude_cli\|bedrock\|vertex'
> server ludodex` returns nothing. If you self-host for yourself and hoped to run this on
> a Claude Pro/Max plan rather than an API key: you cannot, today. An API key is the only
> way in. (The licensing reasoning that section carried was sound and is still worth
> knowing — a personal subscription may not serve other people, and powering a product
> means API-key auth under the Commercial Terms — but it described a code path that does
> not exist.)

---

## Areas: AI is configured per feature, not globally

There are **14 areas**. Each can use a different provider and a different model, and each
can be left off. Settings → AI lists them with their status and cost.

| area | what it does | vision |
|---|---|---|
| `search` | plain-English search bar → structured catalog filter | |
| `art` | picks the best cover/art when providers disagree | ✓ |
| `identify` | recognises games from photos / screenshots / box art | ✓ |
| `dedupe` | flags likely same-game duplicates title-matching missed | |
| `dedupe_media` | drops near-duplicate images across providers (Heavy import) | ✓ |
| `categorize` | classifies an ambiguous image into cover/hero/logo/screenshot | ✓ |
| `consensus` | adjudicates the best value per attribute across providers (Heavy) | |
| `split` | works out which source rows belong to which game when one entry merged two different games | |
| `fileprofile` | proposes a file-organization profile for a ROM directory | |
| `filecmd` | plain-English file-operations requests | |
| `filesource` | describes the CURRENT on-disk layout, so the Before panel knows what it is reading | |
| `ingest` | reads ROM paths during an import and works out the real title/system/year when the filename rules can't | |
| `metadata` | audits provider matches, identifies games no provider matched, fills attribute gaps — the one area that ESCALATES | |
| `prices` | resolves per-token prices the price feed can't (renamed, deprecated or brand-new models) | |

Config keys per area (`<id>` from the table above):

- `ai_area_<id>` — which provider this area uses
- `ai_area_<id>_model` — which model, overriding the provider default
- `ai_area_<id>_prompt` — the system prompt, editable; the shipped default is in
  `DEFAULT_PROMPTS`
- `ai_area_<id>_escalation_model` — only meaningful for `metadata`, the one area in
  `ESCALATE_AREAS`: a bigger model for the hard-case, web-grounded second pass, so tough
  games get a stronger model without paying for it on every routine call

## Which model an area actually gets

`model_for_area()` resolves in this order, and it matters:

1. `ai_area_<id>_model`, if you set one;
2. else the **default model of the area's provider** (`ai_area_<id>`), if you assigned
   one;
3. else, for the four vision areas, the global image-analysis default
   (`ai_vision_model`, else the default model of the vision provider — `AI_VISION_PROVIDER`
   env → config `ai_vision_provider` → the active provider);
4. else the active provider's default model.

**So by default every area runs on the same model** — the active provider's default,
which for `anthropic` means Haiku. Nothing steps up to Sonnet or Opus on its own. If you
want heavier reasoning on art or dedupe, you set `ai_area_art_model` yourself; there is no
automatic tiering. (This page previously claimed cheap extraction used Haiku and heavier
reasoning "steps up to Sonnet/Opus" automatically. It does not, and never has.)

---

## Spend

Paid AI must never fire by accident, so:

- every area is individually switchable, and off means off;
- an area carries a **monthly dollar budget** (`usd_budget`) and separate input/output
  **token caps** (`in_cap`, `out_cap`);
- a dollar budget is only enforceable if the model's price is known. If ludodex cannot
  resolve a price for the model right now, the budget **cannot be measured, so the area
  stops** rather than proceeding unmeasured. The token caps still apply either way.

---

## Setup

Provide the key by environment variable or in Settings → AI (persisted to
`/data/config.sqlite`, gitignored). Never commit one; see `AUTH.md` for the
credential-handling convention. Resolution order is env → config → unset (AI disabled).

```bash
python3 ludodex/config.py set ai_provider anthropic
python3 ludodex/config.py set anthropic_api_key sk-ant-...
# or leave the key out of config entirely and export ANTHROPIC_API_KEY
```
