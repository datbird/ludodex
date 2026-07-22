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
    # A limit row can cap several dimensions at once — any one being hit blocks
    # further calls. monthly_tokens = total-token cap (kept for back-compat);
    # usd_budget = $/month; in_cap/out_cap = separate input / output token caps.
    con.execute("""CREATE TABLE IF NOT EXISTS limits(
        scope TEXT, key TEXT, monthly_tokens INTEGER, PRIMARY KEY(scope, key))""")
    for c, decl in (("usd_budget", "REAL"), ("in_cap", "INTEGER"),
                    ("out_cap", "INTEGER")):
        if c not in {r[1] for r in con.execute("PRAGMA table_info(limits)")}:
            con.execute("ALTER TABLE limits ADD COLUMN %s %s" % (c, decl))
    # Editable per-model price table (USD per 1M tokens). source='manual' rows are
    # user-set and never overwritten by a refresh; 'openrouter' rows are fetched.
    con.execute("""CREATE TABLE IF NOT EXISTS prices(
        provider TEXT, model TEXT, in_usd REAL, out_usd REAL, cached_usd REAL,
        source TEXT, updated TEXT, PRIMARY KEY(provider, model))""")
    con.row_factory = sqlite3.Row
    return con


# ---- pricing: cost is tokens (authoritative, from each API response) × price ---
# NATIVE per-provider defaults, taken from each provider's OWN published pricing
# (not a router). USD per 1,000,000 tokens: (input, output, cached-input|None).
# These are shipped so cost works out of the box without any external fetch; the
# optional OpenRouter refresh is off by default. A model with NO price row has an
# UNKNOWN cost — its $ budget can't be enforced, but the token caps still can.
# Verify against the provider's page over time; every rate is user-editable.
DEFAULT_PRICES = {
    # Google Gemini — ai.google.dev/gemini-api/docs/pricing (global rates)
    ("gemini", "gemini-3.5-flash"): (1.50, 9.00, 0.15),
    ("gemini", "gemini-3.1-pro"): (2.00, 12.00, None),
    ("gemini", "gemini-2.5-pro"): (1.25, 10.00, None),
    ("gemini", "gemini-2.5-flash"): (0.30, 2.50, None),
    ("gemini", "gemini-2.5-flash-lite"): (0.10, 0.40, None),
    # Anthropic Claude — platform.claude.com/docs/en/about-claude/pricing
    # (cached input = 90% off input)
    ("anthropic", "claude-opus-4-8"): (5.00, 25.00, 0.50),
    ("anthropic", "claude-sonnet-5"): (2.00, 10.00, 0.20),
    ("anthropic", "claude-sonnet-4-6"): (3.00, 15.00, 0.30),
    ("anthropic", "claude-haiku-4-5"): (1.00, 5.00, 0.10),
    # OpenAI — developers.openai.com/api/docs/pricing
    ("openai", "gpt-5.5"): (5.00, 30.00, None),
    ("openai", "gpt-5.4"): (2.50, 15.00, None),
    ("openai", "gpt-5.4-mini"): (0.75, 4.50, None),
    ("openai", "gpt-5.4-nano"): (0.20, 1.25, None),
    ("openai", "gpt-5-mini"): (0.25, 2.00, None),
    ("openai", "gpt-5-nano"): (0.05, 0.40, None),
}


def prices_openrouter_enabled():
    """Whether the optional OpenRouter price fetch is allowed (off by default)."""
    return bool(config.get_bool("ai_prices_openrouter", False))


def prices_openrouter_set(on):
    config.set_("ai_prices_openrouter", "1" if on else "0")
    return prices_openrouter_enabled()


def price_schedule_get():
    """{daily, time} — the daily auto-refresh schedule. `time` is 'HH:MM' in the
    ludodex install's local timezone; defaults to 04:00."""
    return {"daily": bool(config.get_bool("price_update_daily", True)),
            "time": config.get("price_update_time") or "04:00"}


def price_schedule_set(daily=None, time=None):
    if daily is not None:
        config.set_("price_update_daily", "1" if daily else "0")
    if time is not None:
        m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", str(time).strip())
        if not m:
            raise ValueError("time must be HH:MM (24-hour)")
        config.set_("price_update_time", "%02d:%02d" % (int(m.group(1)), int(m.group(2))))
    return price_schedule_get()


def price_update_last():
    return config.get("price_update_last")


def run_daily_price_update(force=False):
    """The scheduled daily refresh. No-ops unless the OpenRouter source is enabled
    (the only auto-price feed) and — unless forced — there are limits worth keeping
    accurate. Returns a small status dict; never raises."""
    if not prices_openrouter_enabled():
        return {"skipped": "openrouter-off"}
    if not force and not limits_list():
        return {"skipped": "no-limits"}
    try:
        return prices_refresh()
    except Exception as e:                       # noqa: BLE001 — best-effort
        return {"error": str(e)}


def _seed_prices(con):
    for (prov, model), (i, o, c) in DEFAULT_PRICES.items():
        con.execute("INSERT OR IGNORE INTO prices"
                    "(provider,model,in_usd,out_usd,cached_usd,source,updated) "
                    "VALUES(?,?,?,?,?,'default',?)",
                    (prov, model, i, o, c, datetime.date.today().isoformat()))


def price_get(provider, model):
    """(in_usd, out_usd, cached_usd) per 1M tokens for a model, or None if unknown."""
    try:
        con = _usage_con()
        _seed_prices(con)
        r = con.execute("SELECT in_usd,out_usd,cached_usd FROM prices "
                        "WHERE provider=? AND model=?", (provider, model)).fetchone()
        con.close()
        return (r["in_usd"], r["out_usd"], r["cached_usd"]) if r else None
    except Exception:
        return None


def price_set(provider, model, in_usd, out_usd, cached_usd=None, source="manual"):
    con = _usage_con()
    con.execute("INSERT INTO prices(provider,model,in_usd,out_usd,cached_usd,source,"
                "updated) VALUES(?,?,?,?,?,?,?) ON CONFLICT(provider,model) DO UPDATE "
                "SET in_usd=excluded.in_usd, out_usd=excluded.out_usd, "
                "cached_usd=excluded.cached_usd, source=excluded.source, "
                "updated=excluded.updated",
                (provider, model, float(in_usd or 0), float(out_usd or 0),
                 (float(cached_usd) if cached_usd not in (None, "") else None),
                 source, datetime.date.today().isoformat()))
    con.commit()
    con.close()


def prices_list():
    con = _usage_con()
    _seed_prices(con)
    con.commit()
    rows = [dict(r) for r in con.execute(
        "SELECT provider,model,in_usd,out_usd,cached_usd,source,updated FROM prices "
        "ORDER BY provider, model")]
    con.close()
    return rows


def prices_refresh():
    """Populate/refresh prices from OpenRouter's public models API (it lists
    per-token pricing for models across providers). Matches by model name, so it
    also seeds direct-provider models. NEVER overwrites source='manual' rows.
    Returns {updated, checked}."""
    import urllib.request
    req = urllib.request.Request("https://openrouter.ai/api/v1/models",
                                 headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    models = data.get("data") or []
    # name (last path segment) -> (in_usd_per_mtok, out_usd_per_mtok)
    by_name = {}
    for m in models:
        mid = m.get("id") or ""
        name = mid.split("/", 1)[1] if "/" in mid else mid
        pr = m.get("pricing") or {}
        try:                                   # OpenRouter prices are USD per token
            i = float(pr.get("prompt") or 0) * 1e6
            o = float(pr.get("completion") or 0) * 1e6
        except (TypeError, ValueError):
            continue
        if name and (i or o):
            by_name[name] = (i, o)
    con = _usage_con()
    updated = 0
    today = datetime.date.today().isoformat()
    # refresh existing non-manual rows + any usage model we can name-match
    seen = set(con.execute("SELECT provider, model FROM prices "
                           "WHERE source='manual'"))
    targets = set(con.execute("SELECT DISTINCT provider, model FROM usage"))
    targets |= {(r["provider"], r["model"]) for r in con.execute(
        "SELECT provider, model FROM prices WHERE source!='manual'")}
    for prov, model in targets:
        if (prov, model) in seen:              # never clobber a manual override
            continue
        pr = by_name.get(model)
        if pr:
            con.execute("INSERT INTO prices(provider,model,in_usd,out_usd,cached_usd,"
                        "source,updated) VALUES(?,?,?,?,NULL,'openrouter',?) "
                        "ON CONFLICT(provider,model) DO UPDATE SET in_usd=excluded.in_usd,"
                        " out_usd=excluded.out_usd, source='openrouter', updated=excluded.updated",
                        (prov, model, pr[0], pr[1], today))
            updated += 1
    con.commit()
    con.close()
    return {"updated": updated, "checked": len(by_name)}


def prices_missing():
    """[(provider, model)] for known models that have no usable price yet — both
    input and output are 0/NULL, plus any used model with no price row at all."""
    con = _usage_con()
    _seed_prices(con)
    con.commit()
    have = {(r["provider"], r["model"]) for r in con.execute(
        "SELECT provider, model FROM prices")}
    miss = {(r["provider"], r["model"]) for r in con.execute(
        "SELECT provider, model FROM prices "
        "WHERE COALESCE(in_usd,0)=0 AND COALESCE(out_usd,0)=0")}
    for r in con.execute("SELECT DISTINCT provider, model FROM usage"):
        if (r["provider"], r["model"]) not in have:
            miss.add((r["provider"], r["model"]))
    con.close()
    return sorted(miss)


def resolve_prices_ai(targets, note=None, provider=None, model=None):
    """Ask the configured 'prices' AI area to look up current per-1M-token prices
    for the given [(provider, model_id)] pairs — for models the price feed can't
    resolve (renamed / deprecated / brand-new). Honors the area's prompt + model
    override like every other AI call. `note` is the user's free-text description
    of the specific issue, appended to the area prompt so the model addresses it.
    Returns [{provider, model, input, output, cached, note}] for what it priced."""
    if not targets:
        return []
    provider, key, model = _resolve(provider or provider_for_area("prices"),
                                    model or model_for_area("prices"))
    system = area_prompt("prices")
    if note and str(note).strip():
        system += ("\n\nThe user is specifically having this issue — make sure your "
                   "answer addresses it:\n" + str(note).strip())
    tgt_prov = {m: p for p, m in targets}
    listing = "\n".join("%s / %s" % (p, m) for p, m in targets)
    text = _complete_text(provider, key, model, system, "Models:\n" + listing)
    obj = _json(text)
    items = obj if isinstance(obj, list) else (
        (obj.get("prices") or obj.get("results") or []) if isinstance(obj, dict) else [])
    out = []
    for it in items or []:
        if not isinstance(it, dict) or not it.get("model"):
            continue
        try:
            i = float(it.get("input") or 0)
            o = float(it.get("output") or 0)
        except (TypeError, ValueError):
            continue
        if not (i or o):
            continue
        c = it.get("cached")
        try:
            c = float(c) if c not in (None, "") else None
        except (TypeError, ValueError):
            c = None
        mdl = str(it["model"]).strip()
        out.append({"provider": str(it.get("provider") or tgt_prov.get(mdl) or "").strip(),
                    "model": mdl, "input": i, "output": o, "cached": c,
                    "note": str(it.get("note") or "").strip()})
    return out


def cost_usd(in_tok, out_tok, provider, model):
    """USD cost of one (in,out) token pair for a model, or None if unpriced."""
    p = price_get(provider, model)
    if not p:
        return None
    return (in_tok or 0) / 1e6 * (p[0] or 0) + (out_tok or 0) / 1e6 * (p[1] or 0)


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


def month_io(provider, model=None):
    """(input_tokens, output_tokens) used this month by a provider (or model)."""
    try:
        con = _usage_con()
        q = ("SELECT COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0) "
             "FROM usage WHERE provider=? AND day LIKE ?")
        args = [provider, _month_prefix() + "%"]
        if model is not None:
            q += " AND model=?"
            args.append(model)
        i, o = con.execute(q, args).fetchone()
        con.close()
        return int(i or 0), int(o or 0)
    except Exception:
        return 0, 0


def month_cost(provider, model=None):
    """(usd_cost, has_unpriced) for this month's usage — has_unpriced is True when a
    used model has no price row, so the $ total is a floor (and $ caps can't bind)."""
    try:
        con = _usage_con()
        q = ("SELECT model, COALESCE(SUM(input_tokens),0) i, "
             "COALESCE(SUM(output_tokens),0) o FROM usage "
             "WHERE provider=? AND day LIKE ?")
        args = [provider, _month_prefix() + "%"]
        if model is not None:
            q += " AND model=?"
            args.append(model)
        q += " GROUP BY model"
        rows = con.execute(q, args).fetchall()
        con.close()
    except Exception:
        return 0.0, False
    cost, unpriced = 0.0, False
    for r in rows:
        c = cost_usd(r["i"], r["o"], provider, r["model"])
        if c is None:
            if r["i"] or r["o"]:
                unpriced = True
        else:
            cost += c
    return round(cost, 4), unpriced


def limits_map():
    """{'provider': {id: capsdict}, 'model': {id: capsdict}} — each capsdict is
    {total, usd, input, output} (0/None where unset)."""
    out = {"provider": {}, "model": {}, "global": {}}
    try:
        con = _usage_con()
        for r in con.execute("SELECT scope, key, monthly_tokens, usd_budget, in_cap, "
                             "out_cap FROM limits"):
            caps = {"total": r["monthly_tokens"] or 0, "usd": r["usd_budget"] or 0,
                    "input": r["in_cap"] or 0, "output": r["out_cap"] or 0}
            if any(caps.values()):
                out.setdefault(r["scope"], {})[r["key"]] = caps
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


def _month_usage(scope, key):
    """(total, input, output, usd_cost, unpriced) used this month by a provider,
    a model (across providers), or everything combined (scope='global')."""
    if scope == "global":
        try:
            con = _usage_con()
            rows = con.execute(
                "SELECT provider, model, COALESCE(SUM(input_tokens),0) i, "
                "COALESCE(SUM(output_tokens),0) o FROM usage WHERE day LIKE ? "
                "GROUP BY provider, model", (_month_prefix() + "%",)).fetchall()
            con.close()
        except Exception:
            return 0, 0, 0, 0.0, False
        ti = to = 0
        cost, unp = 0.0, False
        for r in rows:
            ti += r["i"]
            to += r["o"]
            c = cost_usd(r["i"], r["o"], r["provider"], r["model"])
            if c is None:
                if r["i"] or r["o"]:
                    unp = True
            else:
                cost += c
        return ti + to, ti, to, round(cost, 4), unp
    if scope == "provider":
        i, o = month_io(key)
        cost, unp = month_cost(key)
        return i + o, i, o, cost, unp
    try:
        con = _usage_con()
        rows = con.execute("SELECT provider, COALESCE(SUM(input_tokens),0) i, "
                           "COALESCE(SUM(output_tokens),0) o FROM usage WHERE model=? "
                           "AND day LIKE ? GROUP BY provider",
                           (key, _month_prefix() + "%")).fetchall()
        con.close()
    except Exception:
        return 0, 0, 0, 0.0, False
    ti = to = 0
    cost, unp = 0.0, False
    for r in rows:
        ti += r["i"]
        to += r["o"]
        c = cost_usd(r["i"], r["o"], r["provider"], key)
        if c is None:
            if r["i"] or r["o"]:
                unp = True
        else:
            cost += c
    return ti + to, ti, to, round(cost, 4), unp


def limits_list():
    """Configured caps as a flat list, each with this month's usage per dimension:
    [{scope, key, caps:{total,usd,input,output}, used:{total,input,output,usd,unpriced}}]."""
    lm = limits_map()
    out = []
    for scope in ("global", "provider", "model"):
        for k, caps in sorted(lm.get(scope, {}).items()):
            total, i, o, cost, unp = _month_usage(scope, k)
            out.append({"scope": scope, "key": k, "caps": caps,
                        "used": {"total": total, "input": i, "output": o,
                                 "usd": cost, "unpriced": unp}})
    return out


def set_limit(scope, key, caps):
    """Set (or clear) a limit row for a provider/model. `caps` is a dict with any of
    {total, usd, input, output}; falsy dimensions are cleared, and a row with no
    active dimension is removed entirely."""
    if scope not in ("provider", "model", "global") or not key:
        raise ValueError("bad limit scope/key")
    if not isinstance(caps, dict):               # back-compat: a bare number = total
        caps = {"total": caps}
    total = int(caps.get("total") or 0)
    usd = float(caps.get("usd") or 0)
    inn = int(caps.get("input") or 0)
    outc = int(caps.get("output") or 0)
    con = _usage_con()
    if total or usd or inn or outc:
        con.execute("INSERT INTO limits(scope,key,monthly_tokens,usd_budget,in_cap,"
                    "out_cap) VALUES(?,?,?,?,?,?) ON CONFLICT(scope,key) DO UPDATE SET "
                    "monthly_tokens=excluded.monthly_tokens, usd_budget=excluded.usd_budget, "
                    "in_cap=excluded.in_cap, out_cap=excluded.out_cap",
                    (scope, key, total, usd, inn, outc))
    else:
        con.execute("DELETE FROM limits WHERE scope=? AND key=?", (scope, key))
    con.commit()
    con.close()


def _enforce(scope, key, tag):
    """Raise if any active cap on this (scope,key) is hit. The $ cap is skipped when
    a used model is unpriced (cost unknowable) — the token caps are the fallback."""
    caps = limits_map()[scope].get(key)
    if not caps:
        return
    total, i, o, cost, unpriced = _month_usage(scope, key)
    where = " — adjust it in Settings › AI › Budgets & limits"
    if caps["total"] and total >= caps["total"]:
        raise RuntimeError("monthly token cap reached for %s (%d tokens)%s"
                           % (tag, caps["total"], where))
    if caps["input"] and i >= caps["input"]:
        raise RuntimeError("monthly INPUT-token cap reached for %s (%d)%s"
                           % (tag, caps["input"], where))
    if caps["output"] and o >= caps["output"]:
        raise RuntimeError("monthly OUTPUT-token cap reached for %s (%d)%s"
                           % (tag, caps["output"], where))
    if caps["usd"] and not unpriced and cost >= caps["usd"]:
        raise RuntimeError("monthly budget reached for %s ($%.2f of $%.2f)%s"
                           % (tag, cost, caps["usd"], where))


def check_limit(provider, model):
    """Raise RuntimeError if this month's usage has hit any provider- or model-scoped
    cap (total / input / output tokens, or the $ budget)."""
    _enforce("global", "all", "the global AI budget")
    _enforce("provider", provider, "provider %r" % provider)
    _enforce("model", model, "model %r" % model)


def usage_summary():
    """Per (provider, model): lifetime + this-month totals, USD cost, price, caps."""
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
        price = price_get(r["provider"], r["model"])
        life_usd = cost_usd(inp, outp, r["provider"], r["model"])
        m_usd, m_unp = month_cost(r["provider"], r["model"])
        out.append({
            "provider": r["provider"], "model": r["model"], "calls": r["calls"] or 0,
            "input": inp, "output": outp, "total": inp + outp,
            "month": month_tokens(r["provider"], r["model"]),
            "month_usd": m_usd, "lifetime_usd": life_usd, "unpriced": price is None,
            "price": {"in": price[0], "out": price[1], "cached": price[2]} if price else None,
            "last_day": r["last_day"], "active_days": r["days"] or 0,
            "caps": lm["model"].get(r["model"])})
    prov = {}
    for r in out:
        p = prov.setdefault(r["provider"], {"provider": r["provider"], "month": 0,
                            "total": 0, "month_usd": 0.0, "unpriced": False,
                            "caps": lm["provider"].get(r["provider"])})
        p["total"] += r["total"]
    for p in prov.values():
        p["month"] = month_tokens(p["provider"])
        c, unp = month_cost(p["provider"])
        p["month_usd"], p["unpriced"] = c, unp
    return {"models": out, "providers": sorted(prov.values(), key=lambda x: -x["total"]),
            "currency": currency_get()}


def currency_get():
    """{code, fx} — fx = units of `code` per 1 USD (1.0 for USD). Budgets are stored
    in USD; the UI multiplies by fx for display and divides for entry."""
    code = (config.get("ai_budget_currency") or "USD").upper()
    try:
        fx = float(config.get("ai_budget_fx") or 1.0)
    except (TypeError, ValueError):
        fx = 1.0
    if code == "USD":
        fx = 1.0
    return {"code": code, "fx": fx or 1.0}


def currency_set(code, fx=None):
    code = (code or "USD").upper()[:3]
    config.set_("ai_budget_currency", code)
    if code == "USD":
        config.set_("ai_budget_fx", "1.0")
    elif fx not in (None, ""):
        config.set_("ai_budget_fx", str(float(fx)))
    return currency_get()


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
     "data": True,
     "description": "Picks the best cover/art when providers disagree (per-game, in the detail view)."},
    {"id": "identify", "name": "Add-by-image recognition", "status": "live", "vision": True,
     "description": "Recognizes games from photos/screenshots/box art in the library's "
                    "Add-game flow. Needs a vision-capable model."},
    {"id": "dedupe", "name": "Dedupe assist", "status": "live",
     "description": "Flags likely same-game duplicates that title-matching missed."},
    {"id": "split", "name": "Split assist (peel apart)", "status": "live",
     "description": "Looks at one catalog entry that merged two same-named but DIFFERENT "
                    "games (a remake / re-release — Uno 2006 vs 2016) and works out which "
                    "source rows belong to which game, so they can be peeled apart."},
    {"id": "fileprofile", "name": "File-layout inference", "status": "live",
     "description": "Crawls a ROM directory and proposes a file-organization profile "
                    "(target layout) for the file-operations engine."},
    {"id": "filecmd", "name": "File-ops natural language", "status": "live",
     "description": "Turns a plain-English request ('put every game in its own "
                    "folder and build m3u playlists') into a file-operations plan."},
    {"id": "filesource", "name": "File-layout source model", "status": "live",
     "description": "Describes the CURRENT on-disk layout (system/group folders, "
                    "and whether media is intermixed) so the Before panel understands "
                    "what it's reading."},
    {"id": "ingest", "name": "Ingest filename identification", "status": "live",
     "description": "Reads ROM file paths during an import and works out the real game "
                    "title, system and release year when the filename rules can't "
                    "(cryptic 8.3 names, abbreviations, romanisations, wrong folders)."},
    {"id": "metadata", "name": "Metadata search & supplement", "status": "live",
     "description": "Audits provider matches (catches wrong ones like a remake "
                    "matched to the original), identifies games no provider matched, "
                    "and fills attribute gaps from the model's game knowledge."},
    {"id": "prices", "name": "Model price lookup", "status": "live",
     "description": "Resolves current per-token prices for AI models the price feed "
                    "can't (renamed, deprecated, or brand-new) — used by the Auto-"
                    "resolve button in Model prices."},
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
    "split": (
        "One catalog entry titled \"<<title>>\" may have merged TWO OR MORE genuinely "
        "different games that happen to share a name — a remake or re-release, not a "
        "port or edition of one game (e.g. Uno 2006 vs Uno 2016; Tomb Raider 1996 vs "
        "2013). Below are its source rows (each an owned copy on some platform/store, "
        "with its listed title and any year hint). Use your knowledge of these games' "
        "release years and platforms to group the rows by which DISTINCT game each "
        "belongs to. If they are all really one game, say so.\n"
        "Respond ONLY with JSON: {\"multiple\": true|false, \"reason\": \"<short>\", "
        "\"games\": [{\"title\": \"<name with (year)>\", \"year\": <int|null>, "
        "\"rows\": [<row numbers>]}]}. Put the MOST-canonical/original game first. "
        "Every row number must appear in exactly one game."
    ),
    "ingest": (
        "You identify video games from ROM/disc file paths taken from a personal "
        "emulation library. For each numbered path decide the REAL game.\n"
        "The folder a file sits in is a HINT, not proof — collections misfile ROMs "
        "often. If the filename carries a hardware tag that contradicts the folder "
        "(\"Doom 32X\" under sega genesis), the FILENAME wins.\n"
        "Expand abbreviations (FF7 -> Final Fantasy VII), de-abbreviate 8.3 names, and "
        "give the widely-known official English title where one exists (keep the "
        "original for Japan-only releases).\n"
        "Ignore region/version/dump markers — (U), (E), [!], v1.1, Rev A — they are not "
        "part of the title.\n\n"
        "Respond ONLY with a JSON array, one item per numbered path; each item:\n"
        '  {"n": <number>, "title": "<official title, or empty string if the '
        'parsed-title is already right>", "platform": "<system, or empty string to '
        'keep the folder value>", "year": <release year int or null>, '
        '"confidence": <0..1>}\n'
        "Set confidence honestly: 1.0 only when you are certain of the game. Use a LOW "
        "confidence (below 0.5) rather than guessing at a title you do not recognise — "
        "a wrong title is worse than no answer, because the algorithmic one is kept."
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
    "filesource": (
        "You examine a sample of file paths from a ROM/game library and DESCRIBE its "
        "CURRENT on-disk layout — you are NOT proposing a new layout. Report where "
        "the system/console folder sits, any intermediate 'group' folders "
        "(Favorites, Archive, All, …), whether cover art / screenshots / other MEDIA "
        "are intermixed with the ROMs and how they're named, and how confident you "
        "are.\n"
        "Systems present: <<systems>>\n"
        "Current coarse shape: <<current>>\n\n"
        "Respond with ONLY a JSON object (no prose, no code fence):\n"
        '  "system_at"   : where the system name is, e.g. "first folder"\n'
        '  "groups"      : array of intermediate group-folder names seen (may be [])\n'
        '  "media"       : { "present": true|false, "where": "e.g. images/ '
        'subfolders", "naming": "e.g. <game>-thumb.png / -image / -marquee" }\n'
        '  "summary"     : one short human sentence describing the layout\n'
    ),
    "metadata": (
        "You are a video-game metadata expert auditing and enriching ONE game's "
        "catalog entry. You get the game's title (OFTEN a raw ROM/file name, so it may "
        "be mangled), the systems/stores it appears on, its CURRENT provider match (if "
        "any), attributes already known, and a list of MISSING attributes to fill. Do "
        "all that apply:\n"
        "1) VERIFY the current match — is it truly the SAME game? Watch for remakes, "
        "remasters, 'Anniversary'/'HD'/'Definitive' editions, and sequels that share a "
        "name. Example: 'Tomb Raider: Anniversary' (2007) is NOT 'Tomb Raider' (1996) — "
        "flag that as wrong.\n"
        "2) If there is NO current match, IDENTIFY the game — this is the priority, and "
        "you can almost always do it. The title is usually a raw ROM/file name, so be "
        "resourceful and DECODE it: strip dump-index number prefixes ('0006 '), "
        "region/language tags ([USA], (Europe), (En,Fr,De)), and revision/dump flags "
        "((Rev A), [!], (v1.1), (Proto)); treat underscores/dots as spaces; restore a "
        "trailing article ('Zelda, The' -> 'The Legend of Zelda'); and allow for "
        "reordered words, abbreviations, and minor misspellings. "
        "ROM region/dump codes are terse and CASE-INSENSITIVE ((u) == (U)) — decode "
        "them: (U)=USA, (J)=Japan, (E)=Europe, (W)=World, combos like (JU)=Japan+USA "
        "and (UE)=USA+Europe, plus (F)France (G)Germany (I)Italy (S)Spain (A)Australia "
        "(K)Korea (C)China (B)Brazil; dump/status flags [!]=verified-good [b]=bad "
        "[h]=hack [o]=overdump [t]=trained, (Unl)=unlicensed (PD)=public-domain, "
        "(Proto)/(Beta)/(Sample)=pre-release. A TRANSLATION tag — (English)/(T-Eng)/"
        "[T+Eng]/(English v1.01)/(... Translation) — marks a FAN LOCALIZATION: the "
        "underlying game IS the original commercial title (usually Japanese), so "
        "identify the BASE game by its real (often Western) name and note the language "
        "+ patch version. Japanese/romanized titles map to their Western release via the "
        "game's ALTERNATE NAMES — 'Akumajou Dracula X: Chi no Rondo' IS Castlevania: "
        "Rondo of Blood, 'Rockman' = Mega Man, 'Seiken Densetsu' = (Secret/Legend of) "
        "Mana — so resolve to that real entry. By contrast a (PD)/(Homebrew)/"
        "(Aftermarket)/hacked ROM is usually a FAN work, not a commercial game — return "
        "'unmatched' unless it is a well-known homebrew. Region can CHANGE a game's "
        "canonical title — Gradius shipped in Europe as 'Nemesis', Contra as 'Probotector', "
        "Star Soldier variants differ by region — so use the region to pick the CORRECT "
        "regional title, not just to strip it. Use the listed "
        "system(s) as a strong hint to pick the right game among same-named ones. "
        "When present, USE the ROM file name, its PARENT FOLDER (in a non-flat library "
        "the folder is often a fuller/cleaner game name than the file), the region/"
        "edition tags, and any NEIGHBORING files/sidecars (.nfo/.txt/gamelist, disc "
        "images, cover names) shown — these are frequently the clearest identity, and "
        "the system + region tags together imply the platform and the likely release "
        "year of THAT version. "
        "Return the real commercial game's canonical title + release year. Use status "
        "'unmatched' ONLY when the name is genuinely unrecognizable, not merely messy.\n"
        "3) SUPPLEMENT — give best-known values ONLY for the listed missing attributes.\n"
        "You are shown which provider (IGDB, ScreenScraper, Steam…) supplied each "
        "attribute and each media kind, plus the gaps. CROSS-REFERENCE them: where "
        "providers agree, trust it; where one provider is missing a field another has "
        "or the web contradicts it, note that in \"notes\"; fill only the gaps no "
        "provider covers.\n"
        "4) COLLECTION — decide if THIS entry is a COMPILATION that bundles multiple "
        "otherwise-standalone games (e.g. 'Sega Genesis Classics', 'Sonic Mega "
        "Collection', 'Mega Man Legacy Collection', 'Castlevania Anniversary "
        "Collection'). If so, set collection.is_collection=true, give its name, and "
        "list the STANDALONE games it contains (each: title + original platform + "
        "year). NOT a collection: a single game plus DLC/season pass; an 'Anniversary'/"
        "'HD'/'Definitive'/'Remastered' edition of ONE game; a franchise/series name. "
        "When unsure it's a real multi-game bundle, set is_collection=false.\n"
        "Use only well-established facts. If unsure, LOWER the confidence and say so — "
        "never invent a value.\n"
        "Respond with ONLY a JSON object (no prose, no code fence):\n"
        '{"match": {"status": "ok"|"wrong"|"unmatched"|"unsure", "confidence": 0..1, '
        '"issue": "<short reason if wrong>", "suggested_title": "<canonical title>", '
        '"suggested_year": <int or null>}, '
        '"attributes": {"<missing_kind>": <string or array of strings>}, '
        '"collection": {"is_collection": true|false, "name": "<compilation name>", '
        '"members": [{"title": "<game>", "platform": "<original system>", '
        '"year": <int or null>}]}, '
        '"notes": "<one short sentence>"}\n'
        "Attribute formats: release_year=\"YYYY\"; genres/themes/game_modes/"
        "player_perspectives/developers/publishers=arrays of strings; "
        "description=one paragraph string. Omit \"collection\" (or is_collection=false) "
        "for an ordinary single game."
    ),
    "prices": (
        "You are a pricing expert for large-language-model APIs. You are given a "
        "list of AI models (each as 'provider / model-id'). For EACH one, give its "
        "CURRENT public list price in US dollars PER 1,000,000 TOKENS: input "
        "(prompt), output (completion), and cached-input if the provider offers a "
        "cached/prompt-caching rate.\n"
        "If a model id is renamed, deprecated, or an alias, price the model it now "
        "resolves to (the closest current equivalent) and say which in \"note\". "
        "Use official provider pricing pages as your basis. If you genuinely do not "
        "know a model's price, OMIT it rather than guessing.\n"
        "Respond with ONLY a JSON array (no prose, no code fence), one object per "
        "model you can price:\n"
        '[{"provider": "<provider>", "model": "<model-id>", "input": <usd_per_1M>, '
        '"output": <usd_per_1M>, "cached": <usd_per_1M or null>, '
        '"note": "<short note if renamed/deprecated, else empty>"}]'
    ),
}
# <<token>> placeholders each area's prompt fills in (surfaced in the editor).
PROMPT_VARS = {
    "search": ["sources", "platforms"],
    "art": ["kind", "title", "count"],
    "identify": [], "dedupe": [], "split": ["title"],
    "fileprofile": ["variables", "systems", "current"],
    "filecmd": ["profiles", "variables", "systems", "current"],
    "filesource": ["systems", "current"],
    "metadata": [],
    "prices": [],
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


# Areas that ESCALATE to a second, harder pass (web-grounded / re-structured) and can
# therefore use a bigger "escalation model". Currently only metadata's analyze_game.
ESCALATE_AREAS = {"metadata"}


def escalation_model_for_area(area_id):
    """Optional larger/smarter model to use for this area's ESCALATED requests (the
    web-grounded / hard-case second pass). Unset → escalation reuses the area's normal
    model. Set in AI settings so tough games get a stronger model without paying for it
    on every routine call."""
    return config.get("ai_area_" + area_id + "_escalation_model") or None


def area_available(area_id):
    p = provider_for_area(area_id)
    return bool(p and key_for(p))


_MODELS_CACHE = {}


# Model-id substrings that mark NON-(image-analysis) models — audio, embeddings,
# moderation, and IMAGE-GENERATION models (which take text, not image, input).
_NON_VISION = ("embed", "whisper", "tts", "audio", "moderation", "rerank",
               "dall-e", "imagen", "speech", "transcribe", "veo", "sora",
               "-realtime", "image-generation", "gpt-image", "chatgpt-image",
               "image-latest", "image-1",
               # Gemini image-GENERATION/editing models ("…-flash-image", nano-banana):
               # they OUTPUT images and reject a JSON-analysis request (400), so they must
               # never be offered/kept for a vision-analysis area. This is what had the
               # 'art' + 'identify' areas 400-ing on every call.
               "-image")


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
             # modality badges: vision = analyzes images, data = works over the
             # catalog/text. Default data = "not vision"; an area can set both.
             "vision": bool(a.get("vision")),
             "data": bool(a.get("data", not a.get("vision"))),
             "assigned": (config.get("ai_area_" + a["id"]) or None),
             "assigned_model": (config.get("ai_area_" + a["id"] + "_model") or None),
             # bigger model for this area's escalated (web / hard-case) pass, if it has one
             "escalates": a["id"] in ESCALATE_AREAS,
             "escalation_model": (escalation_model_for_area(a["id"]) if a["id"] in
                                  ESCALATE_AREAS else None),
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


def find_media_pages(title, systems=None, year=None, provider=None, model=None):
    """Grounded web search for the PAGES a game's art lives on. Returns [{title,url}].

    This exists because the provider's search tool already hands back its real search
    results — `_web_gemini`/`_web_anthropic` return them as the 4th element — and
    find_media_urls was throwing them away, keeping only the model's prose. Asking a model
    to RECITE direct image-file URLs from memory is unreliable (it invents and misremembers
    them); asking a search tool which PAGES are about a game is exactly what search is good
    at. The caller then extracts the image from each page and validates it.

    Returns [] when the provider has no web search."""
    provider = provider or provider_for_area("metadata")
    if not supports_web(provider):
        return []
    try:
        provider, key, model = _resolve(provider, model or model_for_area("metadata"))
    except RuntimeError:
        return []
    sysline = (" for the %s" % systems[0]) if systems else ""
    yline = (" (%s)" % year) if year else ""
    system = ("You find web pages showing a video game's BOX ART / COVER. Search, then name "
              "the pages you found. Prefer database and wiki pages dedicated to that exact "
              "game — MobyGames, TheGamesDB, Wikipedia, archive.org, platform fan wikis. "
              "Answer in one short sentence; the sources matter, not the prose.")
    user = 'Where can I see the cover art for the video game "%s"%s%s?' % (title, yline, sysline)
    fn = _web_gemini if provider == "gemini" else _web_anthropic
    try:
        res = _retry(lambda: fn(key, model, system, user))
    except Exception:
        return []
    srcs = res[3] if len(res) > 3 else []
    return [s for s in (srcs or []) if (s or {}).get("url")]


def find_media_urls(title, systems=None, year=None, provider=None, model=None):
    """Best-effort OPEN-WEB image discovery: ask the web-search model for DIRECT image-file
    URLs (cover + a few screenshots) for a game. Returns [{"kind","url"}]. The CALLER must
    validate each url is a live image — models return dead/wrong/hotlink-blocked links
    routinely. Returns [] when the provider has no web search."""
    provider = provider or provider_for_area("metadata")
    if not supports_web(provider):
        return []
    try:
        provider, key, model = _resolve(provider, model or model_for_area("metadata"))
    except RuntimeError:
        return []
    sysline = (" (the %s version specifically, if its art differs)" % systems[0]) if systems else ""
    yline = (" released %s" % year) if year else ""
    system = ("You locate REAL, hotlinkable box-art and screenshot IMAGE-FILE urls for video "
              "games from the open web. Prefer Wikipedia / Wikimedia Commons, MobyGames, "
              "TheGamesDB, archive.org, and established fan wikis. Return ONLY direct links to "
              "image files (.jpg/.jpeg/.png/.webp) that actually resolve — NEVER a webpage "
              "url, NEVER a guessed or invented url.")
    user = ('Find the cover/box art and up to 4 in-game screenshots for the video game "%s"%s%s.\n'
            'Respond as STRICT JSON only: {"cover":"<direct image url or empty>",'
            '"screenshots":["<direct image url>", ...]}' % (title, yline, sysline))
    fn = _web_gemini if provider == "gemini" else _web_anthropic
    try:
        txt = _retry(lambda: fn(key, model, system, user))[0]
    except Exception:
        return []
    import re as _re
    m = _re.search(r"\{.*\}", txt or "", _re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except (ValueError, TypeError):
        return []
    out = []
    cov = (data.get("cover") or "").strip()
    if cov:
        out.append({"kind": "cover", "url": cov})
    for s in (data.get("screenshots") or [])[:4]:
        s = (s or "").strip()
        if s:
            out.append({"kind": "screenshot", "url": s})
    return out


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
def pick_art(title, kind, images, provider=None, model=None, language=None):
    """Pick the best of N candidate images. `images`=[(mime,bytes)].
    Returns {"index": <0-based>, "reason": str}. Raises on error. When `language`
    is set, ties break toward media in that language (logos/titles/box art)."""
    provider, key, model = _resolve(provider, model)
    system = area_prompt("art", kind=kind, title=title, count=len(images))
    instr = "Pick the best image."
    if language:
        instr += (" Prefer an image whose visible text, logo, or box-art language "
                  "is %s when overall quality is comparable." % language)
    text = _complete_vision(provider, key, model, system, instr, images)
    obj = _json(text) or {}
    raw = obj.get("index")
    idx = (int(raw) - 1) if raw not in (None, "") else 0   # tolerate a null/missing index
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


# ---------------------------------------------------- provider attribute adjudication
def adjudicate_attributes(title, conflicts, provider=None, model=None):
    """Two providers disagree on some fields — pick the better source per field.
    `conflicts` = {kind: {"igdb": value, "screenscraper": value}}. Returns
    {kind: "igdb"|"screenscraper"} naming the winning provider for each field."""
    provider, key, model = _resolve(provider or provider_for_area("metadata"),
                                    model or model_for_area("metadata"))
    system = (
        "You reconcile video-game metadata for the game \"%s\". For each field you "
        "are given two providers' values (IGDB and ScreenScraper). Choose the ONE "
        "provider whose value is more accurate, complete, and canonical for THAT "
        "field (a well-formed genre list, correct developer/publisher, a sensible "
        "release date, a better-written description, etc.). Respond ONLY with a JSON "
        "object mapping each field name to the chosen provider, exactly "
        '"igdb" or "screenscraper", e.g. '
        '{"genres":"igdb","developers":"screenscraper"}.' % title)
    text = _complete_text(provider, key, model, system,
                          json.dumps(conflicts, ensure_ascii=False))
    obj = _json(text)
    return obj if isinstance(obj, dict) else {}


# ---------------------------------------------------- cross-title contamination audit
def detect_contamination(items, provider=None, model=None):
    """Decide, per suspect, whether an emulation ROM is REALLY the IGDB game it was bound to
    or a DIFFERENT game that merely shares the title (the Atari-2600 "Dune" vs the 1992 Cryo
    "Dune"). `items` = [{n, title, platform, igdb_name, igdb_year, igdb_platforms:[..],
    summary}]. Returns [{n, contaminated: bool, confidence: 0..1, reason}]. Raises on error."""
    provider, key, model = _resolve(provider or provider_for_area("metadata"),
                                    model or model_for_area("metadata"))
    listing = "\n".join(
        '%d. ROM "%s" on %s  ->  bound to IGDB "%s" (%s; IGDB platforms: %s). Summary: %s'
        % (it["n"], it["title"], it["platform"], it.get("igdb_name", ""),
           it.get("igdb_year", "?"), ", ".join(it.get("igdb_platforms") or []) or "none",
           (it.get("summary") or "")[:220])
        for it in items)
    system = (
        "You audit a retro-game library for CROSS-CONTAMINATION: an emulation ROM bound to "
        "the WRONG game's identity because they share a title. For each item you get a ROM "
        "(title + the console it runs on) and the IGDB game it was matched to (name, year, "
        "the platforms IGDB lists, a summary). Decide if the ROM really IS that IGDB game, or "
        "a DIFFERENT game sharing the name. Contamination is when the console is a hardware "
        "generation that game never released on AND could not run — e.g. a 1992 CD-ROM "
        "adventure/RTS cannot be an Atari 2600 cartridge. But a legitimate port to a "
        "contemporary or later console is NOT contamination, and IGDB platform lists are "
        "OFTEN INCOMPLETE, so only flag contaminated when you're confident that console "
        "could not be that game. Respond ONLY with a JSON array of "
        '{"n": <num>, "contaminated": true|false, "confidence": 0..1, "reason": "<short>"}.')
    text = _complete_text(provider, key, model, system, "Items:\n" + listing)
    obj = _json(text)
    rows = obj if isinstance(obj, list) else (obj or {}).get("results", [])
    return rows if isinstance(rows, list) else []


def detect_collections(items, provider=None, model=None):
    """Decide, per candidate, whether a catalog entry is a COMPILATION that bundles several
    otherwise-standalone games, and if so list its members (task #12). `items` =
    [{n, title, platform, year?}]. Returns [{n, is_collection: bool, name, confidence: 0..1,
    members: [{title, platform, year}], reason}]. Raises on error.

    This is the systematic counterpart to the `collection` block in the per-game metadata
    prompt: that one only runs for games the scan flagged for an attribute/match gap, so a
    fully-enriched compilation was never asked about."""
    provider, key, model = _resolve(provider or provider_for_area("metadata"),
                                    model or model_for_area("metadata"))
    listing = "\n".join(
        '%d. "%s"%s%s' % (it["n"], it["title"],
                          " on %s" % it["platform"] if it.get("platform") else "",
                          " (%s)" % it["year"] if it.get("year") else "")
        for it in items)
    system = (
        "You identify COMPILATIONS in a video-game library. A compilation is ONE owned "
        "product that bundles multiple games which each also exist standalone — e.g. "
        "'Sega Genesis Classics', 'Sonic Mega Collection', 'Mega Man Legacy Collection', "
        "'Castlevania Anniversary Collection', 'Super Mario All-Stars'. "
        "NOT a compilation: a single game plus DLC or a season pass; an 'Anniversary' / "
        "'HD' / 'Definitive' / 'Remastered' edition of ONE game; a franchise or series "
        "name; a multi-disc release of one game; a 'Game of the Year' edition. "
        "A pirate/bootleg '150-in-1' cartridge IS a bundle, but its contents are unknowable "
        "— return is_collection false for those rather than inventing members. "
        "When it IS a compilation, list the standalone games it contains, using each game's "
        "own original platform and release year. List only members you are confident about; "
        "an incomplete member list is much better than an invented one. If unsure whether "
        "something is a real multi-game bundle, answer false. "
        'Respond ONLY with a JSON array of {"n": <num>, "is_collection": true|false, '
        '"name": "<compilation name>", "confidence": 0..1, '
        '"members": [{"title": "<game>", "platform": "<original system>", "year": <int|null>}], '
        '"reason": "<short>"}.')
    text = _complete_text(provider, key, model, system, "Entries:\n" + listing)
    obj = _json(text)
    rows = obj if isinstance(obj, list) else (obj or {}).get("results", [])
    return rows if isinstance(rows, list) else []


def adjudicate_entry(items, provider=None, model=None):
    """Per-entry identity adjudication (task #8). For each entry (a title on a specific
    console) given the exact-title IGDB CANDIDATES, decide whether it's the group's
    primary game, a DIFFERENT same-title candidate (which id), or a different game not in
    the list (detach). `items` = [{n, title, platform, filename?, year?, primary_id,
    candidates:[{id, name, year, platforms:[..]}]}]. Returns [{n, same_as_group: bool,
    correct_igdb_id: int|null, detach: bool, confidence: 0..1, reason}]. Raises on error."""
    provider, key, model = _resolve(provider or provider_for_area("metadata"),
                                    model or model_for_area("metadata"))

    def _cands(it):
        return "; ".join(
            '[id %s] "%s" (%s; platforms: %s)'
            % (c.get("id"), c.get("name", ""), c.get("year", "?"),
               ", ".join(p.get("name") or "" for p in (c.get("platforms") or [])) or "none")
            for c in (it.get("candidates") or [])) or "none"

    listing = "\n".join(
        '%d. ROM "%s" on console "%s"%s%s. Group primary game id: %s. '
        'Candidate games sharing this title: %s'
        % (it["n"], it["title"], it["platform"],
           (' file "%s"' % it["filename"]) if it.get("filename") else "",
           (" (~%s)" % it["year"]) if it.get("year") else "",
           it.get("primary_id"), _cands(it))
        for it in items)
    system = (
        "You resolve the IDENTITY of each entry in a retro-game library. Different games can "
        "share a title across eras (Tomb Raider 1996 vs the 2013 reboot; Alice in Wonderland "
        "the 1985 text-adventure vs the 2010 movie game). For each item you get a ROM (title + "
        "the console it runs on, maybe a filename/year) and the candidate IGDB games that "
        "share its title (each with id, year, the platforms IGDB lists). The group's 'primary' "
        "game id is given. For THIS console decide: is the entry the SAME game as the primary, "
        "a DIFFERENT candidate (give its id), or a different game not among the candidates "
        "(detach)? A legitimate port to a contemporary or later console is the SAME game, and "
        "IGDB platform lists are OFTEN INCOMPLETE, so DEFAULT to same_as_group unless you are "
        "confident it is different. Only detach when it plainly cannot be any candidate on "
        "that console (e.g. a 1993 SNES game 'ported' to a 1977 Atari 2600). Respond ONLY with "
        'a JSON array of {"n": <num>, "same_as_group": true|false, "correct_igdb_id": <id or '
        'null>, "detach": true|false, "confidence": 0..1, "reason": "<short>"}.')
    text = _complete_text(provider, key, model, system, "Items:\n" + listing)
    obj = _json(text)
    rows = obj if isinstance(obj, list) else (obj or {}).get("results", [])
    return rows if isinstance(rows, list) else []


def rate_match_confidence(items, provider=None, model=None):
    """Refine identity confidence for gray-zone matches (task #13). `items` =
    [{n, title, matched_name, platform, year?}] where `title` is the library/ROM title and
    `matched_name` is the IGDB game it's currently matched to. Returns [{n, confidence: 0..100,
    reason}] — how likely the entry really IS that game. Raises on error."""
    provider, key, model = _resolve(provider or provider_for_area("metadata"),
                                    model or model_for_area("metadata"))
    listing = "\n".join(
        '%d. Library entry "%s" on console "%s"%s is currently matched to IGDB game "%s".'
        % (it["n"], it["title"], it.get("platform", "?"),
           (" (~%s)" % it["year"]) if it.get("year") else "",
           it.get("matched_name", ""))
        for it in items)
    system = (
        "You judge whether each retro-game library entry is matched to the RIGHT game. For "
        "each item you get the library title (often a ROM filename), the console it's on, and "
        "the IGDB game it's currently matched to. Rate 0-100 how confident you are they are "
        "the SAME game. A shortened, regional, or translated title of the same game is a GOOD "
        'match (high). A common word matched into an unrelated longer title ("journey" -> '
        '"The Sims 4: Journey to Batuu"; "ball" -> "Dragon Ball Z") is a BAD match (low). A '
        "platform mismatch alone is weak evidence — IGDB platform lists are often incomplete, "
        "so don't punish it heavily on its own. Respond ONLY with a JSON array of "
        '{"n": <num>, "confidence": 0-100, "reason": "<short>"}.')
    text = _complete_text(provider, key, model, system, "Items:\n" + listing)
    obj = _json(text)
    rows = obj if isinstance(obj, list) else (obj or {}).get("results", [])
    return rows if isinstance(rows, list) else []


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


def identify_roms(items, provider=None, model=None):
    """Identify games from ROM paths. `items` = [{system, game, path}] — the folder's
    system label, the algorithmic title, and one real relative path.

    Returns [{n, title, platform, year, confidence}] with n 1-based into `items`.
    An empty title/platform means "the algorithmic value was already right", which is
    the common case and keeps the response cheap. Raises on error.

    Batching is the caller's job (see ingest_ai.py) — this sends exactly what it is
    given, so the caller controls token spend per call.
    """
    provider, key, model = _resolve(provider, model)
    listing = "\n".join(
        '%d. folder="%s"  parsed-title="%s"  path="%s"'
        % (i + 1, it.get("system", ""), it.get("game", ""), it.get("path", ""))
        for i, it in enumerate(items))
    system = area_prompt("ingest")
    text = _complete_text(provider, key, model, system, "Paths:\n" + listing)
    obj = _json(text)
    rows = obj if isinstance(obj, list) else (obj.get("results") or obj.get("games") or [])
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        try:
            n = int(r.get("n"))
        except (TypeError, ValueError):
            continue
        if not 1 <= n <= len(items):        # a hallucinated index would corrupt a title
            continue
        yr = r.get("year")
        try:
            yr = int(yr) if yr not in (None, "", "null") else None
        except (TypeError, ValueError):
            yr = None
        if yr is not None and not 1950 <= yr <= 2100:
            yr = None
        try:
            conf = float(r.get("confidence") or 0)
        except (TypeError, ValueError):
            conf = 0.0
        out.append({"n": n,
                    "title": (r.get("title") or "").strip(),
                    "platform": (r.get("platform") or "").strip(),
                    "year": yr,
                    "confidence": max(0.0, min(1.0, conf))})
    return out


def split_adjudicate(title, rows, provider=None, model=None):
    """Given one entry's source rows, decide whether it's really 2+ different games
    (a remake merged by name) and how the rows split. `rows`=[{source, platform,
    title_raw, year}]. Returns {multiple, reason, games:[{title, year, rows:[idx]}]}
    where row indices are 1-based into `rows`. Raises on error."""
    provider, key, model = _resolve(provider, model)
    listing = "\n".join(
        '%d. %s / %s — listed as "%s"%s'
        % (i + 1, r.get("source", "?"), r.get("platform", "?"),
           r.get("title_raw", ""),
           ("  (year hint: %s)" % r["year"]) if r.get("year") else "")
        for i, r in enumerate(rows))
    system = area_prompt("split", title=title)
    text = _complete_text(provider, key, model, system, "Source rows:\n" + listing)
    obj = _json(text)
    return obj if isinstance(obj, dict) else {"multiple": False, "games": []}


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


def model_source_layout(sample_text, systems_text, current, provider=None, model=None):
    """Describe the CURRENT on-disk layout (system/group folders, intermixed media)
    — for the Before panel. Returns a small dict {system_at, groups, media, summary}.
    Reuses the fileprofile area's provider/model config with the filesource prompt."""
    provider, key, model = _resolve(provider or provider_for_area("filesource"),
                                    model or model_for_area("filesource"))
    system = area_prompt("filesource", systems=systems_text, current=current)
    obj = _json(_complete_text(provider, key, model, system,
                               "Current file paths (sample):\n" + sample_text))
    if not isinstance(obj, dict):
        raise RuntimeError("model did not return a layout description")
    return obj


# ------------------------------------------------------- metadata audit/supplement
def _metadata_user(game):
    """Format one game's state into the user message for the metadata area.
    `game` = {title, systems:[...], sources:[...], match:{title,year,slug}|None,
              have:{kind:[values]}, missing:[kinds]}."""
    # unmatched games are usually bare ROMs — tell the model the title is a raw
    # filename so it decodes prefixes/region tags/reordering instead of trusting it.
    _label = ("Game title (RAW ROM/file name — may have a numeric prefix, region tags, "
              "or reordered words; decode it)" if not game.get("match") else "Game title")
    lines = ["%s: %s" % (_label, game.get("title", ""))]
    if game.get("systems"):
        lines.append("Systems/platforms: %s" % ", ".join(game["systems"]))
    if game.get("sources"):
        lines.append("Owned via: %s" % ", ".join(game["sources"]))
    # ROM filesystem signal (from the ROM index) — file name, parent folder (often a
    # fuller title in a non-flat library), region/edition tags, and neighbor sidecars.
    f = game.get("files") or {}
    if f.get("files"):
        lines.append("ROM file(s): %s" % "; ".join(f["files"]))
    if f.get("folders"):
        lines.append("Parent folder(s) (may be a fuller game name): %s"
                     % "; ".join(f["folders"]))
    if f.get("tags"):
        lines.append("Region/edition tags: %s" % "; ".join(f["tags"]))
    if f.get("siblings"):
        lines.append("Neighboring files in the same folder (identity hints): %s"
                     % ", ".join(f["siblings"]))
    for _fn, _txt in (f.get("sibling_text") or {}).items():
        lines.append("  from %s: %s" % (_fn, " ".join(_txt.split())[:300]))
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
    # user-supplied context from the review page's "add context & re-run" — the human
    # telling the model what it got wrong or what this actually is. Trust it strongly.
    hint = (game.get("user_hint") or "").strip()
    if hint:
        lines.append("\nIMPORTANT — CONTEXT PROVIDED BY THE USER (they know this game; "
                     "treat it as authoritative and reconcile your answer with it): "
                     + hint)
    # Reference material the user pulled from the web (Wikipedia/MobyGames/store/IGDB
    # pages, already fetched server-side). Ground truth — stronger than a blind web search.
    for r in (game.get("user_refs") or []):
        txt = (r.get("text") or "").strip()
        if txt:
            lines.append("\nAUTHORITATIVE REFERENCE the user provided (%s) — treat as "
                         "ground truth:\n%s" % (r.get("url", "link"), txt[:4000]))
    return "\n".join(lines)


WEB_GUIDANCE = (
    "\n\nSEARCH THE WEB to verify — forums, Reddit, Wikipedia, MobyGames/IGDB/"
    "GiantBomb, the Internet Archive, fan wikis, and blogs are all fair game. "
    "For a ROM HACK or FAN TRANSLATION specifically, ROMHACKING.NET (RHDN — and, if a "
    "page is unreachable, its Internet-Archive/Google-cache snapshots or community "
    "successors) is the AUTHORITATIVE database: look the patch up there and record its "
    "hack/translation NAME, AUTHOR or GROUP, VERSION, target LANGUAGE, and release date "
    "in the attributes/notes. Crucially, identify the BASE GAME the patch modifies and "
    "match to THAT real commercial entry — a translation inherits the original's "
    "developer/publisher/genre/year (an English patch of 'Akumajou Dracula X: Chi no "
    "Rondo' IS Castlevania: Rondo of Blood), so fill those from the base game, not the "
    "patch. "
    "Cross-check every claim across INDEPENDENT sources: prefer consensus and "
    "authoritative pages, and DISCOUNT a single unverified forum/blog post. In "
    "\"notes\", say how well the sources agreed and flag anything you couldn't "
    "confirm. ADD a \"sources\" key to the JSON: an array of the page URLs you "
    "actually consulted (empty if you did not need to search). Still respond with "
    "ONLY the JSON object (no prose outside it)."
)


def _parse_metadata(text):
    """Parse analyze_game()'s model reply into a normalized {match, attributes, ...}."""
    obj = _json(text)
    if not isinstance(obj, dict):
        raise RuntimeError("model did not return a metadata object")
    obj.setdefault("match", {})
    obj.setdefault("attributes", {})
    return obj


def _actionable_content(obj):
    """Does this analysis carry anything worth storing — filled attributes, a detected
    compilation, or a flagged match problem? Mirrors aimeta.store_finding so we can tell
    a real result from an empty 'looks fine, nothing added' reply."""
    attrs = obj.get("attributes") or {}
    if any(v not in (None, "", [], {}) for v in attrs.values()):
        return True
    coll = obj.get("collection") or {}
    if coll.get("is_collection") and coll.get("members"):
        return True
    return ((obj.get("match") or {}).get("status") or "").lower() in (
        "wrong", "unmatched", "unsure")


def _should_escalate(obj, game):
    """Whether to spend a web round-trip. Escalate when the reliable pass flags a match
    problem, OR leaves most of the game's KNOWN metadata gaps unfilled — that's where
    fresh web facts actually help. Self-reported confidence is NOT a good signal (the
    model is happily 1.0 even on a made-up title), so we key off whether it produced the
    missing data. If it already filled the gaps (e.g. Tec Toy's "20 em 1"), skip web —
    grounding a good answer tends to DEGRADE it (Gemini can't enforce JSON with its search
    tool, so grounded replies drop attributes and the whole collection)."""
    if ((obj.get("match") or {}).get("status") or "").lower() in (
            "unmatched", "unsure", "wrong"):
        return True
    gaps = game.get("missing") or []       # already the SUPPLEMENT_KINDS the game lacks
    if not gaps:
        return False
    attrs = obj.get("attributes") or {}
    filled = {k for k, v in attrs.items() if v not in (None, "", [], {})}
    unfilled = [k for k in gaps if k not in filled]
    return len(unfilled) > len(gaps) // 2          # more than half still empty


def analyze_game(game, provider=None, model=None, web=False, force_web=False):
    """Audit + enrich one game. `game` is the context dict built by the caller. Returns
    {match:{...}, attributes:{...}, notes, sources:[{title,url}], web:bool}.

    `force_web` skips the escalation heuristic and always does the web round when web is
    on — used by the review page's explicit "add context & re-run" so a user asking for
    a web-grounded retry actually gets one.

    Reliability-first: the structured extraction ALWAYS comes from the plain
    JSON-enforced completion, which is far more consistent than web-grounded output.
    When `web` is set AND the reliable pass is uncertain (unmatched/unsure/low-confidence,
    or empty on a game with gaps), we escalate: gather live-web research, then RE-STRUCTURE
    with that research as context so the answer stays complete JSON with citations. This
    fixes the old failure where the wand's web mode silently returned 0 findings for a
    clearly-identifiable game (e.g. the Tec Toy "20 em 1" Master System compilation)."""
    # an explicit `model` (e.g. the review page's model picker) wins for BOTH passes;
    # otherwise the first pass uses the area's normal model and the escalated pass uses
    # the configured bigger "escalation model" (falling back to the same model).
    explicit = model
    provider, key, model = _resolve(provider or provider_for_area("metadata"),
                                    explicit or model_for_area("metadata"))
    esc_model = explicit or escalation_model_for_area("metadata") or model
    user = _metadata_user(game)
    sysp = area_prompt("metadata")

    obj = _parse_metadata(_complete_text(provider, key, model, sysp, user))
    sources, used_web = [], False
    if web and supports_web(provider) and (force_web or _should_escalate(obj, game)):
        research, sources = _complete_text_web(provider, key, esc_model,
                                               sysp + WEB_GUIDANCE, user)
        used_web = True
        webbed = _parse_metadata(_complete_text(
            provider, key, esc_model, sysp,
            user + "\n\nWEB RESEARCH to incorporate (may be noisy or incomplete — trust "
            "it over guessing, but still answer with ONLY the JSON schema):\n"
            + (research or "")))
        # keep the web-informed structuring when it's at least as useful as the first pass
        if _actionable_content(webbed) or not _actionable_content(obj):
            obj = webbed

    # normalize sources (model-reported may be bare URL strings) + grounding citations
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


# Models observed to reject thinking_budget=0 — populated at run time by the first
# rejection, so a new model family self-heals without a code change.
_NO_THINKING = set()


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
    # truncate the JSON to just "{".
    #
    # Newer models (gemini-flash-latest and the 3.x line) REJECT thinking_budget=0
    # outright with a bare 400 INVALID_ARGUMENT. Constructing the object still
    # succeeds, so a try/except around the constructor alone never fires — the
    # failure only surfaces at call time, which silently broke every AI area for
    # anyone on such a model. Try it, and on rejection drop it and retry once,
    # remembering the model so the wasted round-trip happens at most once per
    # process.
    want_thinking = model not in _NO_THINKING
    if want_thinking:
        try:
            cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        except Exception:
            want_thinking = False

    def _go(c):
        return client.models.generate_content(
            model=model, contents=user, config=types.GenerateContentConfig(**c))

    try:
        resp = _go(cfg)
    except Exception as e:                  # noqa: BLE001
        if not want_thinking or "INVALID_ARGUMENT" not in str(e):
            raise
        _NO_THINKING.add(model)
        cfg.pop("thinking_config", None)
        resp = _go(cfg)
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
