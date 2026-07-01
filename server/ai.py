#!/usr/bin/env python3
"""AI features (HANDOFF.md §6.5) — BYOAI provider registry.

Phase 3 starts with natural-language search: an LLM turns a free-text question
into a structured catalog query (a subset of the /api/games filters), which the
API then runs deterministically against SQLite.

**All providers are API-key based** (see AI.md for why subscriptions can't power
an app backend). Four providers are supported, each picked per deployment:

    anthropic   — Claude direct (Anthropic API key)
    openai      — OpenAI API key
    gemini      — Google AI Studio API key (free tier available)
    openrouter  — OpenRouter key (OpenAI-compatible; reaches many models)

Resolution, per provider:  env var  →  config.sqlite key  →  unset.
Active provider:  env AI_PROVIDER → config `ai_provider` → first one with a key.

Keys are stored in config.sqlite (gitignored) via config.set_, or supplied by
env var. Never logged or returned by the API (only a masked "configured" flag).
"""
import json
import os
import re
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIR)
import config  # noqa: E402

# Interface areas/features that use an AI model. Each can be assigned a provider
# (config key `ai_area_<id>`); unassigned areas fall back to the active default.
AREAS = [
    {"id": "search", "name": "Natural-language search", "status": "live",
     "description": "Turns a plain-English query in the search bar into a catalog filter."},
    {"id": "art", "name": "Smart art / metadata pick", "status": "live",
     "description": "Picks the best cover/art when providers disagree (per-game, in the detail view)."},
    {"id": "dedupe", "name": "Dedupe assist", "status": "live",
     "description": "Flags likely same-game duplicates that title-matching missed."},
]
AREA_IDS = {a["id"] for a in AREAS}

# Curated model suggestions per provider (the UI offers these but allows any
# custom value via free-text). Exact ids change; keep these as hints only.
MODELS = {
    "anthropic": ["claude-haiku-4-5-20251001", "claude-sonnet-5",
                  "claude-opus-4-8", "claude-fable-5"],
    "openai": ["gpt-5-mini", "gpt-5", "gpt-4o-mini"],
    "gemini": ["gemini-flash-latest", "gemini-2.5-flash",
               "gemini-2.5-pro", "gemini-2.0-flash"],
    "openrouter": ["anthropic/claude-haiku-4.5", "anthropic/claude-sonnet-4.6",
                   "google/gemini-2.5-flash", "openai/gpt-5-mini"],
}

# provider id -> (env var for key, config key, default model, config model key)
PROVIDERS = {
    "anthropic":  ("ANTHROPIC_API_KEY",  "anthropic_api_key",  "claude-haiku-4-5-20251001", "anthropic_model"),
    "openai":     ("OPENAI_API_KEY",     "openai_api_key",     "gpt-5-mini",                "openai_model"),
    "gemini":     ("GEMINI_API_KEY",     "gemini_api_key",     "gemini-flash-latest",       "gemini_model"),
    "openrouter": ("OPENROUTER_API_KEY", "openrouter_api_key", "anthropic/claude-haiku-4.5", "openrouter_model"),
}


def key_for(provider):
    env, cfg, _, _ = PROVIDERS[provider]
    return os.environ.get(env) or config.get(cfg) or None


def model_for(provider):
    _, _, default_model, cfg_model = PROVIDERS[provider]
    return config.get(cfg_model) or default_model


def active_provider():
    """Which provider to use: env override → config → first configured."""
    p = os.environ.get("AI_PROVIDER") or config.get("ai_provider")
    if p in PROVIDERS and key_for(p):
        return p
    if p in PROVIDERS:                       # explicitly chosen but no key yet
        return p if key_for(p) else None
    for cand in PROVIDERS:                    # auto: first with a key
        if key_for(cand):
            return cand
    return None


def available():
    p = active_provider()
    return bool(p and key_for(p))


def provider_for_area(area_id):
    """Provider assigned to an interface area, else the active default."""
    p = config.get("ai_area_" + area_id)
    if p in PROVIDERS:
        return p
    return active_provider()


def model_for_area(area_id):
    """Model assigned to an area, else the effective provider's default model."""
    return config.get("ai_area_" + area_id + "_model") or model_for(provider_for_area(area_id))


def area_available(area_id):
    p = provider_for_area(area_id)
    return bool(p and key_for(p))


_MODELS_CACHE = {}


def list_models(provider, refresh=False):
    """All models the provider's API reports (merged with curated hints).
    Cached per process; falls back to the curated list on error / no key."""
    if not refresh and provider in _MODELS_CACHE:
        return _MODELS_CACHE[provider]
    key = key_for(provider)
    ids = []
    try:
        if key and provider == "anthropic":
            import anthropic
            c = anthropic.Anthropic(api_key=key)
            ids = [m.id for m in c.models.list(limit=1000).data]
        elif key and provider in ("openai", "openrouter"):
            import openai
            base = "https://openrouter.ai/api/v1" if provider == "openrouter" else None
            c = openai.OpenAI(api_key=key, base_url=base)
            ids = [m.id for m in c.models.list().data]
        elif key and provider == "gemini":
            from google import genai
            c = genai.Client(api_key=key)
            for m in c.models.list():
                actions = (getattr(m, "supported_actions", None)
                           or getattr(m, "supported_generation_methods", None) or [])
                if not actions or "generateContent" in actions:
                    ids.append((getattr(m, "name", "") or "").split("/")[-1])
    except Exception:
        ids = []
    merged = sorted(x for x in (set(ids) | set(MODELS.get(provider, []))) if x)
    if not merged:
        merged = MODELS.get(provider, [])
    _MODELS_CACHE[provider] = merged
    return merged


def _mask(key):
    """Obscured preview of a secret: first 3 + last 4 chars."""
    if not key:
        return None
    if len(key) <= 8:
        return key[0] + "…"
    return "%s…%s" % (key[:3], key[-4:])


def status():
    """Non-secret view of provider + per-area config for the settings UI / health."""
    default_provider = active_provider()
    return {
        "active": default_provider,
        "default": {
            "provider": default_provider,
            "model": model_for(default_provider) if default_provider else None,
        },
        "providers": [
            {"id": p, "configured": bool(key_for(p)),
             "masked": _mask(key_for(p)), "model": model_for(p),
             "models": MODELS.get(p, [])}
            for p in PROVIDERS
        ],
        "areas": [
            {**a,
             "assigned": (config.get("ai_area_" + a["id"]) or None),
             "assigned_model": (config.get("ai_area_" + a["id"] + "_model") or None),
             "effective": provider_for_area(a["id"]),
             "effective_model": model_for_area(a["id"])}
            for a in AREAS
        ],
    }


# --------------------------------------------------------------------- NL → query
def _system_prompt(sources, platforms):
    return (
        "You translate a natural-language request into a structured query over a "
        "personal video-game-ownership catalog (deduped across emulation ROMs and "
        "PC stores).\n"
        "Available sources: " + ", ".join(sources) + "\n"
        "Available platforms (top): " + ", ".join(platforms[:40]) + "\n\n"
        "Respond with ONLY a JSON object (no prose, no code fence) with these "
        "optional keys:\n"
        '  "q"         : title substring, if the user named a game\n'
        '  "source"    : ONE exact value from the sources list, if a store is implied\n'
        '  "platform"  : ONE exact value from the platforms list, if a system is implied\n'
        '  "has_kind"  : a media kind like "cover", if the user asks for games with art\n'
        '  "explanation": one short sentence on how you interpreted the request\n'
        "Omit any key you are not constraining. Use EXACT source/platform values."
    )


def _json(text):
    """Parse a JSON object/array from a model's reply (tolerates fences/prose)."""
    text = (text or "").strip()
    if not text:
        raise RuntimeError("empty model response")
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"[\{\[].*[\}\]]", text, re.DOTALL)
        if not m:
            raise RuntimeError("no JSON in model response: %r" % text[:200])
        return json.loads(m.group(0))


def _resolve(provider, model=None):
    """Validate provider + key, return (provider, key, model). Raises if unusable."""
    provider = provider or active_provider()
    if not provider:
        raise RuntimeError("no AI provider configured")
    key = key_for(provider)
    if not key:
        raise RuntimeError("provider %r has no API key" % provider)
    return provider, key, (model or model_for(provider))


def _complete_text(provider, key, model, system, user):
    """Single text completion dispatched to the provider's SDK."""
    if provider == "anthropic":
        return _call_anthropic(key, model, system, user)
    if provider in ("openai", "openrouter"):
        base = "https://openrouter.ai/api/v1" if provider == "openrouter" else None
        return _call_openai(key, model, system, user, base_url=base)
    if provider == "gemini":
        return _call_gemini(key, model, system, user)
    raise RuntimeError("unknown provider %r" % provider)


def _complete_vision(provider, key, model, system, user, images):
    """Multimodal completion. `images` = list of (mime, bytes)."""
    if provider == "anthropic":
        return _vision_anthropic(key, model, system, user, images)
    if provider in ("openai", "openrouter"):
        base = "https://openrouter.ai/api/v1" if provider == "openrouter" else None
        return _vision_openai(key, model, system, user, images, base_url=base)
    if provider == "gemini":
        return _vision_gemini(key, model, system, user, images)
    raise RuntimeError("unknown provider %r" % provider)


def nl_to_query(question, sources, platforms, provider=None, model=None):
    """Return (query_dict, explanation). Raises on no key / API error.

    `provider`/`model` override the backend (e.g. the area's assignment);
    default to the active provider's configured model.
    """
    provider, key, model = _resolve(provider, model)
    text = _complete_text(provider, key, model,
                          _system_prompt(sources, platforms), question)
    obj = _json(text)
    if not isinstance(obj, dict):
        raise RuntimeError("model did not return a JSON object")
    explanation = obj.pop("explanation", "") or ""
    query = {k: v for k, v in obj.items()
             if k in ("q", "source", "platform", "has_kind") and v}
    return query, explanation


# ----------------------------------------------------------------- art pick (vision)
def pick_art(title, kind, images, provider=None, model=None):
    """Pick the best of N candidate images. `images`=[(mime,bytes)].
    Returns {"index": <0-based>, "reason": str}. Raises on error."""
    provider, key, model = _resolve(provider, model)
    system = (
        "You help pick the best %s image for the video game '%s'. You will see %d "
        "candidate images labeled 'Image N'. Choose the single best: correct game, "
        "highest quality, well-cropped, not a placeholder/blank/wrong-region. "
        'Respond ONLY with JSON: {"index": <1-based number>, "reason": "<short>"}.'
        % (kind, title, len(images))
    )
    text = _complete_vision(provider, key, model, system,
                            "Pick the best image.", images)
    obj = _json(text)
    idx = int(obj.get("index", 1)) - 1
    idx = max(0, min(idx, len(images) - 1))
    return {"index": idx, "reason": obj.get("reason", "")}


# ------------------------------------------------------------------- dedupe assist
def dedupe_pairs(pairs, provider=None, model=None):
    """Adjudicate candidate duplicate pairs. `pairs`=[{a,b,a_src,b_src}].
    Returns list of {n, same, confidence, reason}. Raises on error."""
    provider, key, model = _resolve(provider, model)
    listing = "\n".join(
        '%d. A="%s" [%s]  vs  B="%s" [%s]'
        % (i + 1, p["a"], p.get("a_src", ""), p["b"], p.get("b_src", ""))
        for i, p in enumerate(pairs))
    system = (
        "You judge whether two video-game catalog entries are the SAME game. "
        "Same = regional title / punctuation / subtitle / edition / format differences. "
        "Different = sequels, remakes, unrelated games that share words. "
        "For each numbered pair, decide. Respond ONLY with a JSON array: "
        '[{"n": <num>, "same": true|false, "confidence": <0-1>, "reason": "<short>"}].'
    )
    text = _complete_text(provider, key, model, system, "Pairs:\n" + listing)
    obj = _json(text)
    return obj if isinstance(obj, list) else obj.get("results", [])


# ------------------------------------------------------------------ provider calls
def _call_anthropic(key, model, system, user):
    import anthropic
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model=model, max_tokens=400, system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")


def _call_openai(key, model, system, user, base_url=None):
    import openai
    client = openai.OpenAI(api_key=key, base_url=base_url)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        response_format={"type": "json_object"},
        max_tokens=400,
    )
    return resp.choices[0].message.content


def _call_gemini(key, model, system, user):
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=key)
    cfg = dict(
        system_instruction=system,
        response_mime_type="application/json",
        max_output_tokens=1024,
    )
    # Disable "thinking" — 2.5-flash spends output tokens on thinking and can
    # truncate the JSON to just "{". Not all models accept it, so degrade safely.
    try:
        cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
    except Exception:
        pass
    resp = client.models.generate_content(
        model=model, contents=user,
        config=types.GenerateContentConfig(**cfg),
    )
    return resp.text


# ------------------------------------------------------------------- vision calls
def _vision_anthropic(key, model, system, user, images):
    import base64
    import anthropic
    client = anthropic.Anthropic(api_key=key)
    content = []
    for i, (mime, data) in enumerate(images):
        content.append({"type": "text", "text": "Image %d:" % (i + 1)})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": mime,
            "data": base64.b64encode(data).decode()}})
    content.append({"type": "text", "text": user})
    msg = client.messages.create(
        model=model, max_tokens=300, system=system,
        messages=[{"role": "user", "content": content}])
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")


def _vision_openai(key, model, system, user, images, base_url=None):
    import base64
    import openai
    client = openai.OpenAI(api_key=key, base_url=base_url)
    content = []
    for i, (mime, data) in enumerate(images):
        content.append({"type": "text", "text": "Image %d:" % (i + 1)})
        b64 = base64.b64encode(data).decode()
        content.append({"type": "image_url",
                        "image_url": {"url": "data:%s;base64,%s" % (mime, b64)}})
    content.append({"type": "text", "text": user})
    resp = client.chat.completions.create(
        model=model, max_tokens=300,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": content}],
        response_format={"type": "json_object"})
    return resp.choices[0].message.content


def _vision_gemini(key, model, system, user, images):
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=key)
    parts = []
    for i, (mime, data) in enumerate(images):
        parts.append(types.Part.from_text(text="Image %d:" % (i + 1)))
        parts.append(types.Part.from_bytes(data=data, mime_type=mime))
    parts.append(types.Part.from_text(text=user))
    cfg = dict(system_instruction=system,
               response_mime_type="application/json", max_output_tokens=512)
    try:
        cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
    except Exception:
        pass
    resp = client.models.generate_content(
        model=model, contents=parts,
        config=types.GenerateContentConfig(**cfg))
    return resp.text
