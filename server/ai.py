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
import datetime
import json
import os
import re
import sqlite3
import sys
import time

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("LUDODEX_DATA", DIR)
sys.path.insert(0, DIR)
import config  # noqa: E402

# --- token usage tracking + monthly limits (per provider / per model) --------
USAGE_DB = os.path.join(DATA, "ai-usage.sqlite")


def _usage_con():
    con = sqlite3.connect(USAGE_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS usage(
        provider TEXT, model TEXT, day TEXT, calls INTEGER DEFAULT 0,
        input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
        PRIMARY KEY(provider, model, day))""")
    con.execute("""CREATE TABLE IF NOT EXISTS limits(
        scope TEXT, key TEXT, monthly_tokens INTEGER, PRIMARY KEY(scope, key))""")
    con.row_factory = sqlite3.Row
    return con


def record_usage(provider, model, in_tok, out_tok):
    """Add one call's token usage to today's row. Never raises (best-effort)."""
    if not (provider and model):
        return
    try:
        con = _usage_con()
        con.execute(
            "INSERT INTO usage(provider,model,day,calls,input_tokens,output_tokens) "
            "VALUES(?,?,?,1,?,?) ON CONFLICT(provider,model,day) DO UPDATE SET "
            "calls=calls+1, input_tokens=input_tokens+excluded.input_tokens, "
            "output_tokens=output_tokens+excluded.output_tokens",
            (provider, model, datetime.date.today().isoformat(),
             int(in_tok or 0), int(out_tok or 0)))
        con.commit()
        con.close()
    except Exception:
        pass


def _month_prefix():
    return datetime.date.today().isoformat()[:7]        # 'YYYY-MM'


def month_tokens(provider, model=None):
    """Total tokens used this calendar month by a provider (or a specific model)."""
    try:
        con = _usage_con()
        q = ("SELECT COALESCE(SUM(input_tokens+output_tokens),0) FROM usage "
             "WHERE provider=? AND day LIKE ?")
        args = [provider, _month_prefix() + "%"]
        if model is not None:
            q += " AND model=?"
            args.append(model)
        n = con.execute(q, args).fetchone()[0]
        con.close()
        return int(n or 0)
    except Exception:
        return 0


def limits_map():
    """{'provider': {id: cap}, 'model': {id: cap}} of configured monthly caps."""
    out = {"provider": {}, "model": {}}
    try:
        con = _usage_con()
        for r in con.execute("SELECT scope, key, monthly_tokens FROM limits"):
            if r["monthly_tokens"]:
                out.setdefault(r["scope"], {})[r["key"]] = r["monthly_tokens"]
        con.close()
    except Exception:
        pass
    return out


def _month_tokens_model(model):
    """This month's tokens for a model name across ALL providers."""
    try:
        con = _usage_con()
        n = con.execute("SELECT COALESCE(SUM(input_tokens+output_tokens),0) FROM "
                        "usage WHERE model=? AND day LIKE ?",
                        (model, _month_prefix() + "%")).fetchone()[0]
        con.close()
        return int(n or 0)
    except Exception:
        return 0


def limits_list():
    """Configured monthly caps as a flat list with this month's usage:
    [{scope, key, cap, month}]. Empty when nothing is capped."""
    lm = limits_map()
    out = [{"scope": "provider", "key": p, "cap": c, "month": month_tokens(p)}
           for p, c in sorted(lm.get("provider", {}).items())]
    out += [{"scope": "model", "key": m, "cap": c, "month": _month_tokens_model(m)}
            for m, c in sorted(lm.get("model", {}).items())]
    return out


def set_limit(scope, key, monthly_tokens):
    """Set (or clear, when falsy) a monthly token cap for a provider or model."""
    if scope not in ("provider", "model") or not key:
        raise ValueError("bad limit scope/key")
    con = _usage_con()
    if monthly_tokens and int(monthly_tokens) > 0:
        con.execute("INSERT INTO limits(scope,key,monthly_tokens) VALUES(?,?,?) "
                    "ON CONFLICT(scope,key) DO UPDATE SET monthly_tokens=excluded.monthly_tokens",
                    (scope, key, int(monthly_tokens)))
    else:
        con.execute("DELETE FROM limits WHERE scope=? AND key=?", (scope, key))
    con.commit()
    con.close()


def check_limit(provider, model):
    """Raise RuntimeError if this month's usage has hit the provider or model cap."""
    lm = limits_map()
    pcap = lm["provider"].get(provider)
    if pcap and month_tokens(provider) >= pcap:
        raise RuntimeError("monthly usage limit reached for provider %r "
                           "(%d tokens) — raise it in Settings › AI › Usage report"
                           % (provider, pcap))
    mcap = lm["model"].get(model)
    if mcap and month_tokens(provider, model) >= mcap:
        raise RuntimeError("monthly usage limit reached for model %r (%d tokens) — "
                           "raise it in Settings › AI › Usage report" % (model, mcap))


def usage_summary():
    """Per (provider, model): lifetime + this-month totals + configured cap."""
    lm = limits_map()
    try:
        con = _usage_con()
        rows = con.execute(
            "SELECT provider, model, SUM(calls) calls, SUM(input_tokens) inp, "
            "SUM(output_tokens) outp, MAX(day) last_day, COUNT(*) days "
            "FROM usage GROUP BY provider, model "
            "ORDER BY SUM(input_tokens+output_tokens) DESC").fetchall()
        con.close()
    except Exception:
        rows = []
    out = []
    for r in rows:
        inp, outp = r["inp"] or 0, r["outp"] or 0
        out.append({
            "provider": r["provider"], "model": r["model"], "calls": r["calls"] or 0,
            "input": inp, "output": outp, "total": inp + outp,
            "month": month_tokens(r["provider"], r["model"]),
            "last_day": r["last_day"], "active_days": r["days"] or 0,
            "model_cap": lm["model"].get(r["model"], 0)})
    prov = {}
    for r in out:
        p = prov.setdefault(r["provider"], {"provider": r["provider"], "month": 0,
                                            "total": 0, "cap": lm["provider"].get(r["provider"], 0)})
        p["total"] += r["total"]
    for p in prov.values():
        p["month"] = month_tokens(p["provider"])
    return {"models": out, "providers": sorted(prov.values(),
            key=lambda x: -x["total"])}


def usage_series(provider, model, days=31):
    """Daily token usage for a model over the last `days` (today + preceding)."""
    try:
        con = _usage_con()
        rows = {r["day"]: r for r in con.execute(
            "SELECT day, calls, input_tokens inp, output_tokens outp FROM usage "
            "WHERE provider=? AND model=?", (provider, model))}
        con.close()
    except Exception:
        rows = {}
    today = datetime.date.today()
    series = []
    for d in range(days - 1, -1, -1):
        day = (today - datetime.timedelta(days=d)).isoformat()
        r = rows.get(day)
        series.append({"day": day, "calls": r["calls"] if r else 0,
                       "input": r["inp"] if r else 0, "output": r["outp"] if r else 0})
    return series

# Interface areas/features that use an AI model. Each can be assigned a provider
# (config key `ai_area_<id>`); unassigned areas fall back to the active default.
AREAS = [
    {"id": "search", "name": "Natural-language search", "status": "live",
     "description": "Turns a plain-English query in the search bar into a catalog filter."},
    {"id": "art", "name": "Smart art / metadata pick", "status": "live", "vision": True,
     "description": "Picks the best cover/art when providers disagree (per-game, in the detail view)."},
    {"id": "identify", "name": "Add-by-image recognition", "status": "live", "vision": True,
     "description": "Recognizes games from photos/screenshots/box art in the library's "
                    "Add-game flow. Needs a vision-capable model."},
    {"id": "dedupe", "name": "Dedupe assist", "status": "live",
     "description": "Flags likely same-game duplicates that title-matching missed."},
    {"id": "fileprofile", "name": "File-layout inference", "status": "live",
     "description": "Crawls a ROM directory and proposes a file-organization profile "
                    "(target layout) for the file-operations engine."},
    {"id": "filecmd", "name": "File-ops natural language", "status": "live",
     "description": "Turns a plain-English request ('put every game in its own "
                    "folder and build m3u playlists') into a file-operations plan."},
    {"id": "metadata", "name": "Metadata search & supplement", "status": "live",
     "description": "Audits provider matches (catches wrong ones like a remake "
                    "matched to the original), identifies games no provider matched, "
                    "and fills attribute gaps from the model's game knowledge."},
]
AREA_IDS = {a["id"] for a in AREAS}
VISION_AREAS = {a["id"] for a in AREAS if a.get("vision")}

# Default system prompt per area, editable by the user (saved as ai_area_<id>_prompt).
# Placeholders use <<token>> (not {}) so the JSON braces inside the prompts don't clash.
DEFAULT_PROMPTS = {
    "search": (
        "You translate a natural-language request into a structured query over a "
        "personal video-game-ownership catalog (deduped across emulation ROMs and "
        "PC stores).\n"
        "Available sources: <<sources>>\n"
        "Available platforms (top): <<platforms>>\n\n"
        "Respond with ONLY a JSON object (no prose, no code fence) with these "
        "optional keys:\n"
        '  "q"         : title substring, if the user named a game\n'
        '  "source"    : ONE exact value from the sources list, if a store is implied\n'
        '  "platform"  : ONE exact value from the platforms list, if a system is implied\n'
        '  "has_kind"  : a media kind like "cover", if the user asks for games with art\n'
        '  "explanation": one short sentence on how you interpreted the request\n'
        "Omit any key you are not constraining. Use EXACT source/platform values."
    ),
    "art": (
        "You help pick the best <<kind>> image for the video game '<<title>>'. You "
        "will see <<count>> candidate images labeled 'Image N'. Choose the single "
        "best: correct game, highest quality, well-cropped, not a "
        "placeholder/blank/wrong-region. Respond ONLY with JSON: "
        '{"index": <1-based number>, "reason": "<short>"}.'
    ),
    "identify": (
        "You identify video games shown in images — box art, cartridges/discs, "
        "screenshots, store pages, or shelves/photos containing several games at "
        "once. Find EVERY distinct game across ALL of the images. Respond ONLY with "
        'a JSON array; each item: {"title": "<official game title>", "platform": '
        '"<console/system if visible, else empty>", "source": "<store if visible, '
        'else empty>", "confidence": <0..1>}. Use widely-known official titles, one '
        "entry per distinct game, no duplicates, no prose."
    ),
    "dedupe": (
        "You judge whether two video-game catalog entries are the SAME game. "
        "Same = regional title / punctuation / subtitle / edition / format differences. "
        "Different = sequels, remakes, unrelated games that share words. "
        "For each numbered pair, decide. Respond ONLY with a JSON array: "
        '[{"n": <num>, "same": true|false, "confidence": <0-1>, "reason": "<short>"}].'
    ),
    "fileprofile": (
        "You design a file-organization profile for a ROM/game library. You are given "
        "a sample of the current file paths, the systems present, and the available "
        "template variables. Infer a clean, consistent TARGET layout.\n"
        "Template variables (use ONLY these, in {curly} form): <<variables>>\n"
        "Systems present: <<systems>>\n"
        "Current shape looks like: <<current>>\n\n"
        "Respond with ONLY a JSON object (no prose, no code fence):\n"
        '  "name"        : short profile name\n'
        '  "description" : one sentence describing the layout\n'
        '  "target"      : path template, e.g. "{system}/{game}/{filename}"\n'
        '  "m3u"         : true to auto-build .m3u playlists for multi-disc games\n'
        '  "rename"      : true to rename single-file games (else keep {filename})\n'
        '  "prune_empty" : true to remove dirs left empty after moving\n'
        "Prefer {filename} over renaming unless the user clearly wants clean names. "
        "Keep multi-disc/multi-file games together (folder-per-game) when present."
    ),
    "filecmd": (
        "You convert a natural-language file-management request into a plan for a "
        "ROM-library file-operations engine. Available saved profiles:\n<<profiles>>\n"
        "Template variables (use ONLY these, {curly} form): <<variables>>\n"
        "Systems present: <<systems>>  |  Current shape: <<current>>\n\n"
        "Respond with ONLY a JSON object (no prose, no code fence):\n"
        '  "profile_id"  : id of a saved profile to use, OR omit and give a "target"\n'
        '  "target"      : ad-hoc path template if no saved profile fits\n'
        '  "m3u", "rename", "prune_empty" : booleans for an ad-hoc target\n'
        '  "scope"       : "multi_system" (path holds many system folders) or '
        '"single_system"\n'
        '  "system"      : the system name, only if scope is "single_system"\n'
        '  "explanation" : one short sentence on what will happen\n'
        "Choose a saved profile when one matches; otherwise craft a minimal target."
    ),
    "metadata": (
        "You are a video-game metadata expert auditing and enriching ONE game's "
        "catalog entry. You get the game's title, the systems/stores it appears on, "
        "its CURRENT provider match (if any), attributes already known, and a list of "
        "MISSING attributes to fill. Do all that apply:\n"
        "1) VERIFY the current match — is it truly the SAME game? Watch for remakes, "
        "remasters, 'Anniversary'/'HD'/'Definitive' editions, and sequels that share a "
        "name. Example: 'Tomb Raider: Anniversary' (2007) is NOT 'Tomb Raider' (1996) — "
        "flag that as wrong.\n"
        "2) If there is NO current match, IDENTIFY the game (canonical title + year).\n"
        "3) SUPPLEMENT — give best-known values ONLY for the listed missing attributes.\n"
        "You are shown which provider (IGDB, ScreenScraper, Steam…) supplied each "
        "attribute and each media kind, plus the gaps. CROSS-REFERENCE them: where "
        "providers agree, trust it; where one provider is missing a field another has "
        "or the web contradicts it, note that in \"notes\"; fill only the gaps no "
        "provider covers.\n"
        "Use only well-established facts. If unsure, LOWER the confidence and say so — "
        "never invent a value.\n"
        "Respond with ONLY a JSON object (no prose, no code fence):\n"
        '{"match": {"status": "ok"|"wrong"|"unmatched"|"unsure", "confidence": 0..1, '
        '"issue": "<short reason if wrong>", "suggested_title": "<canonical title>", '
        '"suggested_year": <int or null>}, '
        '"attributes": {"<missing_kind>": <string or array of strings>}, '
        '"notes": "<one short sentence>"}\n'
        "Attribute formats: release_year=\"YYYY\"; genres/themes/game_modes/"
        "player_perspectives/developers/publishers=arrays of strings; "
        "description=one paragraph string."
    ),
}
# <<token>> placeholders each area's prompt fills in (surfaced in the editor).
PROMPT_VARS = {
    "search": ["sources", "platforms"],
    "art": ["kind", "title", "count"],
    "identify": [], "dedupe": [],
    "fileprofile": ["variables", "systems", "current"],
    "filecmd": ["profiles", "variables", "systems", "current"],
    "metadata": [],
}


def default_prompt(area_id):
    return DEFAULT_PROMPTS.get(area_id, "")


def area_prompt(area_id, **ctx):
    """Effective system prompt for an area: the user's saved override, else the
    default template — with <<token>> placeholders filled from ctx."""
    tmpl = config.get("ai_area_%s_prompt" % area_id) or DEFAULT_PROMPTS.get(area_id, "")
    for k, v in ctx.items():
        tmpl = tmpl.replace("<<%s>>" % k, str(v))
    return tmpl

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


def vision_default_provider():
    """Global default provider for IMAGE analysis (vision), env → config → active."""
    p = os.environ.get("AI_VISION_PROVIDER") or config.get("ai_vision_provider")
    if p in PROVIDERS:
        return p
    return active_provider()


def vision_default_model():
    """Global default model for image analysis (its own override, else the vision
    provider's default model)."""
    p = vision_default_provider()
    return config.get("ai_vision_model") or (model_for(p) if p else None)


def provider_for_area(area_id):
    """Provider for an area: explicit per-area assignment → for vision areas the
    global image-analysis default → the active default."""
    p = config.get("ai_area_" + area_id)
    if p in PROVIDERS:
        return p
    if area_id in VISION_AREAS:
        return vision_default_provider()
    return active_provider()


def model_for_area(area_id):
    """Model for an area: per-area model → per-area provider's default → for vision
    areas the global image-analysis default → the active provider's default."""
    m = config.get("ai_area_" + area_id + "_model")
    if m:
        return m
    area_prov = config.get("ai_area_" + area_id)
    if area_prov in PROVIDERS:
        return model_for(area_prov)
    if area_id in VISION_AREAS:
        return vision_default_model()
    p = active_provider()
    return model_for(p) if p else None


def area_available(area_id):
    p = provider_for_area(area_id)
    return bool(p and key_for(p))


_MODELS_CACHE = {}


# Model-id substrings that mark NON-(image-analysis) models — audio, embeddings,
# moderation, and IMAGE-GENERATION models (which take text, not image, input).
_NON_VISION = ("embed", "whisper", "tts", "audio", "moderation", "rerank",
               "dall-e", "imagen", "speech", "transcribe", "veo", "sora",
               "-realtime", "image-generation", "gpt-image", "chatgpt-image",
               "image-latest", "image-1")


def is_vision_model(provider, mid):
    """Best-effort: is this model capable of image analysis? Used to curate the
    model list for the image-analysis default + vision areas (not a hard gate —
    the picker still accepts any typed model)."""
    m = (mid or "").lower()
    if any(x in m for x in _NON_VISION):
        return False
    if provider == "anthropic":       # every current Claude is multimodal
        return any(x in m for x in ("claude", "opus", "sonnet", "haiku", "fable"))
    if provider == "gemini":          # gemini 1.5/2.x are multimodal
        return "gemini" in m
    # openai / openrouter: known vision families
    return any(x in m for x in (
        "gpt-4o", "gpt-4.1", "gpt-4-turbo", "gpt-5", "o1", "o3", "o4", "chatgpt",
        "claude", "gemini", "llama-3.2", "llama-4", "pixtral", "qwen2-vl",
        "qwen2.5-vl", "-vl-", "-vl", "vision", "grok-2-vision", "grok-4",
        "internvl", "molmo", "mistral-small-3"))


def list_models(provider, refresh=False, vision_only=False):
    """All models the provider's API reports (merged with curated hints).
    Cached per process; falls back to the curated list on error / no key.
    vision_only → keep just the image-capable models (never returns empty)."""
    if not refresh and provider in _MODELS_CACHE:
        cached = _MODELS_CACHE[provider]
        if vision_only:
            vis = [m for m in cached if is_vision_model(provider, m)]
            return vis or cached
        return cached
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
    if vision_only:
        vis = [m for m in merged if is_vision_model(provider, m)]
        return vis or merged
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
        "vision_default": {
            "provider": vision_default_provider(),
            "model": vision_default_model(),
            "assigned": config.get("ai_vision_provider") or None,
            "assigned_model": config.get("ai_vision_model") or None,
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
             "effective_model": model_for_area(a["id"]),
             "prompt": (config.get("ai_area_" + a["id"] + "_prompt") or None),
             "default_prompt": default_prompt(a["id"]),
             "prompt_vars": PROMPT_VARS.get(a["id"], [])}
            for a in AREAS
        ],
    }


# ------------------------------------------------------------- web-grounded calls
# Providers whose SDK can run live web searches (forums, Reddit, Wikipedia, the
# Internet Archive, fan wikis, blogs…) and return citations. openrouter proxies
# don't expose these tools, so they fall back to model knowledge.
WEB_PROVIDERS = {"gemini", "anthropic", "openai"}


def supports_web(provider):
    return provider in WEB_PROVIDERS


def _dedup_sources(src):
    seen, out = set(), []
    for s in src:
        u = s.get("url")
        if u and u not in seen:
            seen.add(u)
            out.append(s)
    return out[:20]


def _web_gemini(key, model, system, user):
    from google import genai
    from google.genai import types
    try:
        client = genai.Client(api_key=key,
                              http_options=types.HttpOptions(timeout=120_000))
    except Exception:
        client = genai.Client(api_key=key)
    cfg = types.GenerateContentConfig(
        system_instruction=system, max_output_tokens=2048,
        tools=[types.Tool(google_search=types.GoogleSearch())])
    resp = client.models.generate_content(model=model, contents=user, config=cfg)
    sources = []
    try:
        gm = resp.candidates[0].grounding_metadata
        for ch in (gm.grounding_chunks or []):
            w = getattr(ch, "web", None)
            if w and getattr(w, "uri", None):
                sources.append({"title": getattr(w, "title", None) or w.uri,
                                "url": w.uri})
    except Exception:
        pass
    u = getattr(resp, "usage_metadata", None)
    return (resp.text, getattr(u, "prompt_token_count", 0) or 0,
            getattr(u, "candidates_token_count", 0) or 0, _dedup_sources(sources))


def _web_anthropic(key, model, system, user):
    import anthropic
    client = anthropic.Anthropic(api_key=key)
    resp = client.messages.create(
        model=model, max_tokens=2048, system=system,
        messages=[{"role": "user", "content": user}],
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}])
    parts, sources = [], []
    for block in resp.content:
        bt = getattr(block, "type", None)
        if bt == "text":
            parts.append(block.text)
            for c in (getattr(block, "citations", None) or []):
                url = getattr(c, "url", None)
                if url:
                    sources.append({"title": getattr(c, "title", None) or url,
                                    "url": url})
        elif bt == "web_search_tool_result":
            for r in (getattr(block, "content", None) or []):
                url = getattr(r, "url", None)
                if url:
                    sources.append({"title": getattr(r, "title", None) or url,
                                    "url": url})
    u = resp.usage
    return ("".join(parts), u.input_tokens, u.output_tokens,
            _dedup_sources(sources))


def _web_openai(key, model, user, base_url=None):
    from openai import OpenAI
    client = OpenAI(api_key=key, base_url=base_url) if base_url \
        else OpenAI(api_key=key)
    resp = client.responses.create(
        model=model, tools=[{"type": "web_search"}], input=user, timeout=120)
    sources = []
    try:
        for item in resp.output:
            for c in (getattr(item, "content", None) or []):
                for a in (getattr(c, "annotations", None) or []):
                    url = getattr(a, "url", None)
                    if url:
                        sources.append({"title": getattr(a, "title", None) or url,
                                        "url": url})
    except Exception:
        pass
    u = getattr(resp, "usage", None)
    return (resp.output_text, getattr(u, "input_tokens", 0) or 0,
            getattr(u, "output_tokens", 0) or 0, _dedup_sources(sources))


def _complete_text_web(provider, key, model, system, user):
    """Web-grounded completion → (text, sources). Enforces the usage limit and
    records tokens, like _complete_text. Falls back to a plain completion (no
    sources) for providers without a web tool."""
    check_limit(provider, model)
    if provider == "gemini":
        text, i, o, src = _retry(lambda: _web_gemini(key, model, system, user))
    elif provider == "anthropic":
        text, i, o, src = _retry(lambda: _web_anthropic(key, model, system, user))
    elif provider == "openai":
        text, i, o, src = _retry(
            lambda: _web_openai(key, model, "%s\n\n%s" % (system, user)))
    else:                                   # no web tool (e.g. openrouter)
        text = _complete_text(provider, key, model, system, user)
        return text, []
    record_usage(provider, model, i, o)
    return text, src


# --------------------------------------------------------------------- NL → query
def _system_prompt(sources, platforms):
    return area_prompt("search", sources=", ".join(sources),
                       platforms=", ".join(platforms[:40]))


def _json(text):
    """Parse a JSON object/array from a model's reply (tolerates fences/prose)."""
    text = (text or "").strip()
    if not text:
        raise RuntimeError("empty model response")
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"[\{\[].*[\}\]]", text, re.DOTALL)
    frag = m.group(0) if m else text[text.find("{") if "{" in text else 0:]
    try:
        return json.loads(frag)
    except json.JSONDecodeError:
        pass
    # tolerate a truncated/unclosed object or array (some models drop the final
    # brace, or the completion is cut off): close any open strings/brackets.
    return json.loads(_repair_json(frag))


def _repair_json(s):
    """Best-effort close of an unterminated JSON object/array from a model reply."""
    s = s.rstrip()
    out, stack, in_str, esc = [], [], False, False
    for ch in s:
        out.append(ch)
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
    if in_str:
        out.append('"')
    # drop a dangling trailing comma before closing
    tail = "".join(out).rstrip()
    if tail.endswith(","):
        tail = tail[:-1]
    return tail + "".join(reversed(stack))


def _resolve(provider, model=None):
    """Validate provider + key, return (provider, key, model). Raises if unusable."""
    provider = provider or active_provider()
    if not provider:
        raise RuntimeError("no AI provider configured")
    key = key_for(provider)
    if not key:
        raise RuntimeError("provider %r has no API key" % provider)
    return provider, key, (model or model_for(provider))


_TRANSIENT = ("503", "429", "500", "unavailable", "overloaded", "rate limit",
              "timeout", "timed out", "temporarily", "try again")


def _retry(fn, tries=3, base=2.0):
    """Call fn(); retry transient provider errors (503/429/overload/timeout) with
    linear backoff. Permanent errors (bad key, 400) raise immediately."""
    for i in range(tries):
        try:
            return fn()
        except Exception as e:            # noqa: BLE001
            if i == tries - 1 or not any(t in str(e).lower() for t in _TRANSIENT):
                raise
            time.sleep(base * (i + 1))


def _complete_text(provider, key, model, system, user):
    """Single text completion dispatched to the provider's SDK. Enforces the
    monthly usage limit before calling, and records token usage after."""
    check_limit(provider, model)

    def call():
        if provider == "anthropic":
            return _call_anthropic(key, model, system, user)
        if provider in ("openai", "openrouter"):
            base = "https://openrouter.ai/api/v1" if provider == "openrouter" else None
            return _call_openai(key, model, system, user, base_url=base)
        if provider == "gemini":
            return _call_gemini(key, model, system, user)
        raise RuntimeError("unknown provider %r" % provider)
    text, i, o = _retry(call)
    record_usage(provider, model, i, o)
    return text


def _complete_vision(provider, key, model, system, user, images):
    """Multimodal completion. `images` = list of (mime, bytes). Enforces the
    monthly usage limit before calling, and records token usage after."""
    check_limit(provider, model)
    if provider == "anthropic":
        text, i, o = _vision_anthropic(key, model, system, user, images)
    elif provider in ("openai", "openrouter"):
        base = "https://openrouter.ai/api/v1" if provider == "openrouter" else None
        text, i, o = _vision_openai(key, model, system, user, images, base_url=base)
    elif provider == "gemini":
        text, i, o = _vision_gemini(key, model, system, user, images)
    else:
        raise RuntimeError("unknown provider %r" % provider)
    record_usage(provider, model, i, o)
    return text


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
    system = area_prompt("art", kind=kind, title=title, count=len(images))
    text = _complete_vision(provider, key, model, system,
                            "Pick the best image.", images)
    obj = _json(text)
    idx = int(obj.get("index", 1)) - 1
    idx = max(0, min(idx, len(images) - 1))
    return {"index": idx, "reason": obj.get("reason", "")}


# --------------------------------------------------------------- add-by-image (vision)
def identify_games(images, provider=None, model=None):
    """Identify every video game shown across the given images — box art,
    cartridges/discs, screenshots, store pages, or a shelf/photo with many games.
    `images`=[(mime,bytes)]. Returns a list of
    {"title": str, "platform": str, "source": str, "confidence": float}."""
    provider, key, model = _resolve(provider or provider_for_area("identify"),
                                    model or model_for_area("identify"))
    system = area_prompt("identify")
    text = _complete_vision(provider, key, model, system,
                            "Identify every game you can see.", images)
    obj = _json(text)
    items = obj if isinstance(obj, list) else (
        obj.get("games") or obj.get("results") if isinstance(obj, dict) else [])
    out = []
    for it in (items or []):
        if not (isinstance(it, dict) and it.get("title")):
            continue
        try:
            conf = float(it.get("confidence") or 0)
        except (TypeError, ValueError):
            conf = 0.0
        out.append({"title": str(it["title"]).strip(),
                    "platform": str(it.get("platform") or "").strip(),
                    "source": str(it.get("source") or "").strip(),
                    "confidence": conf})
    return out


# ------------------------------------------------------------------- dedupe assist
def dedupe_pairs(pairs, provider=None, model=None):
    """Adjudicate candidate duplicate pairs. `pairs`=[{a,b,a_src,b_src}].
    Returns list of {n, same, confidence, reason}. Raises on error."""
    provider, key, model = _resolve(provider, model)
    listing = "\n".join(
        '%d. A="%s" [%s]  vs  B="%s" [%s]'
        % (i + 1, p["a"], p.get("a_src", ""), p["b"], p.get("b_src", ""))
        for i, p in enumerate(pairs))
    system = area_prompt("dedupe")
    text = _complete_text(provider, key, model, system, "Pairs:\n" + listing)
    obj = _json(text)
    return obj if isinstance(obj, list) else obj.get("results", [])


# ------------------------------------------------------------- file-ops (text AI)
def _clean_profile(obj):
    """Coerce a model-proposed profile into the shape fileops expects."""
    if not isinstance(obj, dict):
        raise RuntimeError("model did not return a profile object")
    target = str(obj.get("target") or "").strip()
    if not target:
        raise RuntimeError("model did not propose a target layout")
    return {
        "name": str(obj.get("name") or "AI-inferred layout")[:80],
        "description": str(obj.get("description") or "")[:240],
        "target": target,
        "m3u": bool(obj.get("m3u")),
        "rename": bool(obj.get("rename")),
        "prune_empty": obj.get("prune_empty", True) is not False,
        "archive_policy": obj.get("archive_policy") or "keep",
    }


def infer_file_profile(sample_text, systems_text, variables_text, current,
                       provider=None, model=None):
    """Crawl-sample → a proposed file-organization profile (dict). Raises on error."""
    provider, key, model = _resolve(provider or provider_for_area("fileprofile"),
                                    model or model_for_area("fileprofile"))
    system = area_prompt("fileprofile", variables=variables_text,
                         systems=systems_text, current=current)
    text = _complete_text(provider, key, model, system,
                          "Current file paths (sample):\n" + sample_text)
    return _clean_profile(_json(text))


def file_command(command, profiles_text, systems_text, variables_text, current,
                 provider=None, model=None):
    """Natural-language request → a plan intent dict: either {profile_id,...} or an
    ad-hoc {target, m3u, rename, prune_empty}, plus scope/system/explanation."""
    provider, key, model = _resolve(provider or provider_for_area("filecmd"),
                                    model or model_for_area("filecmd"))
    system = area_prompt("filecmd", profiles=profiles_text, variables=variables_text,
                         systems=systems_text, current=current)
    obj = _json(_complete_text(provider, key, model, system, command))
    if not isinstance(obj, dict):
        raise RuntimeError("model did not return a plan object")
    return obj


# ------------------------------------------------------- metadata audit/supplement
def _metadata_user(game):
    """Format one game's state into the user message for the metadata area.
    `game` = {title, systems:[...], sources:[...], match:{title,year,slug}|None,
              have:{kind:[values]}, missing:[kinds]}."""
    lines = ["Game title: %s" % game.get("title", "")]
    if game.get("systems"):
        lines.append("Systems/platforms: %s" % ", ".join(game["systems"]))
    if game.get("sources"):
        lines.append("Owned via: %s" % ", ".join(game["sources"]))
    m = game.get("match")
    if m:
        yr = (" (%s)" % m.get("year")) if m.get("year") else ""
        lines.append("Current provider match: \"%s\"%s [igdb:%s]"
                     % (m.get("title", "?"), yr, m.get("slug", "")))
    else:
        lines.append("Current provider match: NONE (no provider matched this game)")
    have = game.get("have") or {}
    if have:
        shown = "; ".join("%s=%s" % (k, ", ".join(map(str, v))[:80])
                          for k, v in have.items())
        lines.append("Known attributes: " + shown)
    # per-provider coverage so the AI can cross-reference what each source supplies
    by_source = game.get("by_source") or {}
    if by_source:
        lines.append("Attributes BY PROVIDER (who supplied what):")
        for src, kinds in by_source.items():
            lines.append("  - %s: %s" % (src, ", ".join(kinds)))
    media = game.get("media") or {}
    if media.get("by_provider"):
        parts = ["%s(%s)" % (p, ", ".join(k))
                 for p, k in media["by_provider"].items()]
        lines.append("Media present by provider: " + "; ".join(parts))
    if media.get("missing"):
        lines.append("Media MISSING (no provider has it): %s"
                     % ", ".join(media["missing"]))
    lines.append("MISSING attributes to fill: %s"
                 % (", ".join(game.get("missing") or []) or "(none)"))
    return "\n".join(lines)


WEB_GUIDANCE = (
    "\n\nSEARCH THE WEB to verify — forums, Reddit, Wikipedia, MobyGames/IGDB/"
    "GiantBomb, the Internet Archive, fan wikis, and blogs are all fair game. "
    "Cross-check every claim across INDEPENDENT sources: prefer consensus and "
    "authoritative pages, and DISCOUNT a single unverified forum/blog post. In "
    "\"notes\", say how well the sources agreed and flag anything you couldn't "
    "confirm. ADD a \"sources\" key to the JSON: an array of the page URLs you "
    "actually consulted (empty if you did not need to search). Still respond with "
    "ONLY the JSON object (no prose outside it)."
)


def analyze_game(game, provider=None, model=None, web=False):
    """Audit + enrich one game. `game` is the context dict built by the caller.
    When `web` is set and the provider supports it, the model searches the live
    web and the result carries a `sources` list of citations. Returns
    {match:{...}, attributes:{...}, notes, sources:[{title,url}], web:bool}."""
    provider, key, model = _resolve(provider or provider_for_area("metadata"),
                                    model or model_for_area("metadata"))
    user = _metadata_user(game)
    used_web = bool(web and supports_web(provider))
    if used_web:
        text, sources = _complete_text_web(provider, key, model,
                                           area_prompt("metadata") + WEB_GUIDANCE,
                                           user)
    else:
        text = _complete_text(provider, key, model, area_prompt("metadata"), user)
        sources = []
    obj = _json(text)
    if not isinstance(obj, dict):
        raise RuntimeError("model did not return a metadata object")
    obj.setdefault("match", {})
    obj.setdefault("attributes", {})
    # model-reported sources may be bare URL strings — normalize, then merge with
    # the provider's grounding citations
    reported = []
    for s in (obj.get("sources") or []):
        if isinstance(s, str):
            reported.append({"title": s, "url": s})
        elif isinstance(s, dict) and s.get("url"):
            reported.append({"title": s.get("title") or s["url"], "url": s["url"]})
    obj["sources"] = _dedup_sources(sources + reported)
    obj["web"] = used_web
    return obj


# ------------------------------------------------------------------ provider calls
def _call_anthropic(key, model, system, user):
    import anthropic
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model=model, max_tokens=400, system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
    u = getattr(msg, "usage", None)
    return text, getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0


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
    u = getattr(resp, "usage", None)
    return (resp.choices[0].message.content,
            getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0)


def _call_gemini(key, model, system, user):
    from google import genai
    from google.genai import types
    # cap the HTTP wait so a hung request can never stall a long batch/scan
    try:
        client = genai.Client(api_key=key,
                              http_options=types.HttpOptions(timeout=90_000))
    except Exception:
        client = genai.Client(api_key=key)
    cfg = dict(
        system_instruction=system,
        response_mime_type="application/json",
        max_output_tokens=2048,
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
    u = getattr(resp, "usage_metadata", None)
    return (resp.text, getattr(u, "prompt_token_count", 0) or 0,
            getattr(u, "candidates_token_count", 0) or 0)


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
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
    u = getattr(msg, "usage", None)
    return text, getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0


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
    u = getattr(resp, "usage", None)
    return (resp.choices[0].message.content,
            getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0)


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
    u = getattr(resp, "usage_metadata", None)
    return (resp.text, getattr(u, "prompt_token_count", 0) or 0,
            getattr(u, "candidates_token_count", 0) or 0)
