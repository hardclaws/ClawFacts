"""LLM source for adult-humor fun facts.

Works with Groq (default), OpenRouter, a local Ollama server, or any
OpenAI-compatible chat-completions API.

IMPORTANT for "spicy" mode: hosted models (Groq, OpenRouter, OpenAI…) are
alignment-filtered — they refuse or water down risqué output. For genuinely
adult (but ban-safe) trucker facts, run a LOCAL uncensored model with Ollama
(no API key needed):

    ollama pull llama3.1:8b     # or dolphin-llama3:8b for a less-filtered tune

Config in config.json (or the matching environment variables):

    "llm_api_key":  "",                                # or env GROQ_API_KEY
    "llm_base_url": "https://api.groq.com/openai/v1",  # or env GROQ_API_BASE
    "llm_model":    "openai/gpt-oss-120b",             # or env GROQ_MODEL
    # Local model (no key):
    "llm_base_url": "http://localhost:11434/v1",       # or env OLLAMA_BASE_URL
    "llm_model":    "llama3.1:8b",                     # or env OLLAMA_MODEL

If you already run another app (like Dayforge) that uses GROQ_API_KEY /
OPENROUTER_API_KEY, the bot picks those up automatically — no config needed.
Without any LLM configured, the bot posts the plain (real) facts instead — it
never appends made-up comments.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"          # Groq-hosted OpenAI open model
DEFAULT_GROQ_FALLBACK = "llama-3.3-70b-versatile"      # free on Groq

# OpenRouter default: a cheap, low-refusal model. NOTE: OpenRouter's ":free"
# tier no longer carries uncensored models (Hermes/Dolphin :free were
# deprecated July 2026), so this default is a paid model that costs fractions
# of a cent per fact. Good alternatives:
#   sao10k/l3.3-euryale-70b              (~$0.75/1M out — the classic unfiltered pick)
#   cognitivecomputations/dolphin-mistral-24b-venice-edition   (literally "Uncensored")
#   thedrummer/unslopnemo-12b            (~$0.40/1M out — abliterated Mistral Nemo)
#   mistralai/mistral-nemo               (~$0.03/1M out — dirt cheap, quite permissive)
DEFAULT_OPENROUTER_FALLBACK = "nousresearch/hermes-4-70b"

# Local Ollama (uncensored, no key) — the recommended engine for spicy mode.
OLLAMA_BASE_URL = "http://localhost:11434/v1"
OLLAMA_MODEL = "llama3.1:8b"

# Cloudflare sits in front of Groq and returns "403 error code: 1010" for the
# default Python-urllib User-Agent, so we must send a browser-like one.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Reasoning models (o1/o3/gpt-oss/deepseek-r1/…) reject `temperature` and want
# `max_completion_tokens` instead of `max_tokens`. Detect them by name.
_REASONING = re.compile(
    r"(^|[/.])(o1|o3|o4|gpt-oss|gpt-5|deepseek-r1|deepseek-reasoner)\b",
    re.IGNORECASE,
)

# Once the provider rejects our key (or we hit no-credits / a rate limit), stop
# hammering the API: mark the LLM unavailable until a timestamp and post plain
# facts instead. A bad key won't fix itself mid-stream, so the window is long.
_DISABLED_UNTIL = 0.0


def _unavailable() -> bool:
    return time.time() < _DISABLED_UNTIL


def _disable(code: int) -> None:
    """Record an API failure once and print a single clear console message."""
    global _DISABLED_UNTIL
    if _unavailable():
        return
    if code in (401, 403):
        _DISABLED_UNTIL = time.time() + 21600  # ~6h ≈ rest of the session
        print("[llm] API key rejected (HTTP %d) — check llm_api_key in config.json "
              "or your GROQ_API_KEY / OPENROUTER_API_KEY env var. LLM disabled for "
              "this session; posting plain facts instead." % code, flush=True)
    elif code == 402:
        _DISABLED_UNTIL = time.time() + 3600
        print("[llm] LLM account has no credits (HTTP 402). LLM disabled for an hour; "
              "posting plain facts instead.", flush=True)
    elif code == 429:
        _DISABLED_UNTIL = time.time() + 120
        print("[llm] LLM rate-limited (HTTP 429); backing off for 2 minutes.", flush=True)


def reset_disable_state() -> None:
    """Testing hook: clear the transient 'LLM disabled' state."""
    global _DISABLED_UNTIL
    _DISABLED_UNTIL = 0.0

SYSTEM_PROMPT = (
    "You write fun facts about places for a trucker's Twitch stream watched by "
    "adults. The tone is ADULT-ALIGNED, NOT sexual: grown-up, dry, barstool "
    "storytelling about what actually makes the place interesting — its "
    "history, its claims to fame, its oddities — and its real rowdy past "
    "(crime, vice, gambling, booze, scandal) only when the supplied facts "
    "contain one.\n\n"
    "PRIORITY:\n"
    "- Interesting beats edgy. If the supplied facts are about a record, a "
    "landmark, a famous local or a strange local tradition, lead with that. "
    "Never reach for darkness the facts don't contain, and never treat a "
    "quiet town as a disappointment.\n"
    "- A recent crime involving a named private person is news, not comedy: "
    "state it plainly or leave it out.\n\n"
    "HARD RULES:\n"
    "- Rewrite ONLY the supplied facts. You may reword and punch up the tone "
    "with dry barstool wit, but you must NOT add any new claim: no new person's "
    "name, no new date, year, number, place or event. If a name or date is not "
    "in the supplied facts, it must not appear in your answer.\n"
    "- No new crime, vice, disaster, record or superlative either. If no "
    "supplied fact mentions a hanging, a lynching, drugs, smuggling, a record "
    "or a 'the only / the first / the largest', then your answer must not "
    "mention one — not as a joke, not as flavour.\n"
    "- Facts supplied with an 'In the area:' prefix are about the surrounding "
    "county or state, NOT about the town. Keep that framing and name the "
    "county or state; never say the town did it, and never turn a county-wide "
    "story into 'the only place in the county'.\n"
    "- Each line is ONE self-contained TRUE fact, at most the given character "
    "limit, no trailing '...'.\n"
    "- Never pad a bland fact with made-up puns or cute filler like 'colonial "
    "charm', 'creaky floorboards' or 'souvenir shop'.\n"
    "- If the supplied facts have no rowdy material, give the single most "
    "interesting supplied fact, told with dry wit. Do NOT invent a criminal, "
    "mobster, scandal or legend to spice it up.\n"
    "- Real violence gets no jokes: never frame a hanging, lynching, execution, "
    "murder or disaster as a party, a celebration or a good time. State it "
    "plainly or leave it out.\n"
    "- Voice: blunt barstool storyteller. Salty language and a dry, rowdy wit "
    "are welcome, as long as they point at a real supplied fact.\n"
    "- ABSOLUTELY BANNED (these get a Twitch channel banned): any sexual "
    "content — no explicit sex, no porn/XXX, no sexual acts or body parts, no "
    "lewd come-ons — plus slurs and hate speech. Grown-up topics are fine; "
    "explicit sexual content is never allowed.\n"
    "- No emoji, no hashtags, no markdown, no list numbering.\n"
    "Return up to 10 one-line facts, the most interesting supplied fact first."
)

_SUMMARIZE_SYSTEM = (
    "You shorten a fact so it fits a Twitch chat message. "
    "Shorten the fact to at most the given number of characters. "
    "Keep it one complete sentence with no trailing '...'. "
    "Keep every specific detail — names, dates, numbers, places. "
    "Do not add anything and do not invent details. "
    "Reply with only the shortened fact."
)


def _build_body(model: str, user_prompt: str, system: str = None) -> str:
    messages = [
        {"role": "system", "content": system or SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    body = {"model": model, "messages": messages}
    if _REASONING.search(model):
        body["max_completion_tokens"] = 300
        body["reasoning_effort"] = "low"
    else:
        body["max_tokens"] = 300
        body["temperature"] = 0.9
    return json.dumps(body).encode("utf-8")


def _request(base: str, key: str, body: bytes) -> str:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    # Local Ollama needs no key; hosted APIs do.
    if key:
        headers["Authorization"] = f"Bearer {key}"
    # OpenRouter (not Groq/Ollama) asks for these for ranking/attribution.
    if "openrouter" in base:
        headers["HTTP-Referer"] = "https://localhost"
        headers["X-Title"] = "TruckingWithDoc FunFact Bot"
    req = urllib.request.Request(base + "/chat/completions", data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    return (data["choices"][0]["message"]["content"] or "").strip()


def _call(base: str, model: str, key: str, user_prompt: str,
          system: str = None) -> str:
    return _request(base, key, _build_body(model, user_prompt, system))


def summarize(fact: str, max_chars: int, cfg: dict) -> str | None:
    """Shorten `fact` to <= max_chars, keeping its details. None on failure."""
    if not is_configured(cfg) or _unavailable():
        return None
    key = (cfg.get("llm_api_key") or "").strip()
    base = (cfg.get("llm_base_url") or DEFAULT_BASE_URL).rstrip("/")
    model = cfg.get("llm_model") or (OLLAMA_MODEL if _is_local(base) else DEFAULT_MODEL)
    user = f"Max characters: {max_chars}\nFact: {fact}\n\nShortened fact:"
    messages = [
        {"role": "system", "content": _SUMMARIZE_SYSTEM},
        {"role": "user", "content": user},
    ]
    body = {"model": model, "messages": messages}
    if _REASONING.search(model):
        body["max_completion_tokens"] = 120
    else:
        body["max_tokens"] = 120
        body["temperature"] = 0.3
    if cfg.get("debug"):
        print(f"[llm] POST {base}/chat/completions  model={model} (summarize)", flush=True)
        print("[llm] ---- user prompt ----\n" + user, flush=True)
    try:
        text = _request(base, key, json.dumps(body).encode("utf-8"))
    except urllib.error.HTTPError as exc:
        _disable(exc.code)
        return None
    except Exception as exc:
        print(f"[llm] summarize error: {exc!r}", flush=True)
        return None
    text = (text or "").strip().strip('"“”')
    if cfg.get("debug"):
        print("[llm] ---- response ----\n" + text, flush=True)
    return text or None


def _is_local(base: str) -> bool:
    b = (base or "").lower()
    return ("localhost" in b or "127.0.0.1" in b or "ollama" in b or ":11434" in b)


def is_configured(options: dict) -> bool:
    """True if a usable LLM is configured: an API key, or a local Ollama URL."""
    if (options.get("llm_api_key") or "").strip():
        return True
    return _is_local(options.get("llm_base_url") or "")


def _fallback_model(base: str, model: str) -> str | None:
    """A sane spare model for the provider, or None if already on it."""
    if _is_local(base):
        return None
    if "groq" in base:
        return None if model == DEFAULT_GROQ_FALLBACK else DEFAULT_GROQ_FALLBACK
    return None if model == DEFAULT_OPENROUTER_FALLBACK else DEFAULT_OPENROUTER_FALLBACK


def rewrite_fact(place: str, location: str, seed_facts: list, cfg: dict) -> str | None:
    """Return up to 10 adult-humor fun facts (newline-separated), or None.

    The supplied `seed_facts` are the ground truth the model must rewrite; it is
    never asked to invent history on its own.
    """
    if not is_configured(cfg) or _unavailable():
        return None
    key = (cfg.get("llm_api_key") or "").strip()

    base = (cfg.get("llm_base_url") or DEFAULT_BASE_URL).rstrip("/")
    model = cfg.get("llm_model") or (OLLAMA_MODEL if _is_local(base) else DEFAULT_MODEL)

    try:
        max_chars = int(cfg.get("max_fact_chars") or 200)
    except (TypeError, ValueError):
        max_chars = 200
    max_chars = max(60, min(max_chars, 480))

    # "openrouter/free" is OpenRouter's auto-router: it picks a random free
    # model per request, which is often a reasoning model (whose "thinking"
    # text leaks into the reply) and is heavily rate-limited. Prefer a
    # specific model instead.
    if "openrouter/free" in model.lower() and "_warned_free" not in cfg:
        cfg["_warned_free"] = True
        print("[llm] note: model 'openrouter/free' is OpenRouter's auto-router "
              "(random free model per call — can be slow, rate-limited, or a "
              "reasoning model). Set OPENROUTER_MODEL to a specific model, e.g. "
              "nousresearch/hermes-4-70b.", flush=True)

    user = (
        f"Place: {place}\n"
        f"What the viewer asked for: {location}\n"
        f"Write up to 10 interesting true facts about this place: what it is "
        f"known for, its history, its oddities, and any rowdy stories the "
        f"supplied facts actually contain. Each fact must be at most "
        f"{max_chars} characters, one complete line each, no numbering.\n"
        f"Real facts found (ground truth — rewrite ONLY these, never add new "
        f"names, dates, places or events):\n"
    )
    user += "\n".join(f"- {f}" for f in seed_facts[:10])

    if cfg.get("debug"):
        print(f"[llm] POST {base}/chat/completions  model={model}", flush=True)
        print("[llm] ---- system prompt ----\n" + SYSTEM_PROMPT, flush=True)
        print("[llm] ---- user prompt ----\n" + user, flush=True)

    return _complete(base, model, key, user, cfg, tag="")


def _complete(base: str, model: str, key: str, user: str, cfg: dict,
              tag: str, system: str = None) -> str | None:
    """One chat completion with the provider fallbacks. `tag` labels debug logs."""
    candidates = [model]
    spare = _fallback_model(base, model)
    if spare:
        candidates.append(spare)

    last_detail = ""
    for m in candidates:
        try:
            text = _call(base, m, key, user, system)
            if cfg.get("debug"):
                print(f"[llm] ---- response ----\n" + text, flush=True)
            return text
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace").strip()[:300]
            last_detail = detail
            if exc.code in (401, 403, 402, 429):
                # Auth / billing / rate-limit: no model swap will help.
                _disable(exc.code)
                return None
            if exc.code in (400, 404, 422) and m != candidates[-1]:
                # Model not found / param error -> try the spare.
                print(f"[llm] model '{m}' failed (HTTP {exc.code}); trying fallback\u2026",
                      flush=True)
                continue
            print(f"[llm] HTTP {exc.code}: {detail}", flush=True)
            return None
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            last_detail = repr(exc)
            if m != candidates[-1]:
                print(f"[llm] bad response from '{m}': {exc!r}; trying fallback\u2026",
                      flush=True)
                continue
            print(f"[llm] bad response from '{m}': {exc!r}", flush=True)
            return None
        except Exception as exc:
            print(f"[llm] error: {exc!r}", flush=True)
            return None
    print(f"[llm] all models failed: {last_detail}", flush=True)
    return None


FREEFORM_SYSTEM = (
    "You write short, TRUE fun facts about places for a Twitch chat. Accuracy "
    "matters more than entertainment: a viewer will look these up.\n\n"
    "HARD RULES:\n"
    "- Only facts you are confident are documented: founding and what the "
    "place is named for, a landmark, a record, a well-known local, a famous "
    "event. If you are not sure, leave it out. An omitted fact is fine; a "
    "wrong one is not.\n"
    "- Never invent a name, date, number or story to fill space. If you don't "
    "know a year, say 'in the 1800s' rather than guess.\n"
    "- No crime, scandal, disaster or dark history unless you are certain of "
    "it, and never about a recent, named private person.\n"
    "- Don't pad. Four confident facts beat ten padded ones.\n"
    "- If you know nothing reliable about this place, reply with exactly: "
    "NOTHING RELIABLE\n"
    "- Each line is ONE self-contained fact, at most the given character "
    "limit, no numbering, no markdown, no emoji.\n"
    "Return up to 10 one-line facts, most interesting first."
)


def freeform_facts(place: str, location: str, cfg: dict) -> str | None:
    """Ask the model for facts from its own knowledge (no source facts).

    Opt-in only (`"fact_source": "llm"`). There is nothing to ground the output
    against, so the caller must not promise the viewer these are sourced.
    """
    if not is_configured(cfg) or _unavailable():
        return None
    key = (cfg.get("llm_api_key") or "").strip()
    base = (cfg.get("llm_base_url") or DEFAULT_BASE_URL).rstrip("/")
    model = cfg.get("llm_model") or (OLLAMA_MODEL if _is_local(base) else DEFAULT_MODEL)
    try:
        max_chars = max(60, min(int(cfg.get("max_fact_chars") or 200), 480))
    except (TypeError, ValueError):
        max_chars = 200

    user = (
        f"Place: {place}\n"
        f"What the viewer asked for: {location}\n"
        f"Write up to 10 true, interesting fun facts about this place, each at "
        f"most {max_chars} characters, one per line, no numbering. Only facts "
        f"you are confident are documented."
    )
    if cfg.get("debug"):
        print(f"[llm] POST {base}/chat/completions  model={model} (freeform)",
              flush=True)
        print("[llm] ---- system prompt ----\n" + FREEFORM_SYSTEM, flush=True)
        print("[llm] ---- user prompt ----\n" + user, flush=True)
    return _complete(base, model, key, user, cfg, tag="freeform",
                     system=FREEFORM_SYSTEM)

