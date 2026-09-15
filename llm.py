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

import io
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

#: A reply cut off mid-thought often ends on a glue word or a dangling
#: contraction (live-fire: 'If they try to slash wages, I'll' - longer
#: than the 12-character floor, so the old fragment check missed it and
#: the downstream gates had to stop it). A complete line never ends on
#: one, so it pays the same doubled-budget retry as an empty reply.
_DANGLING_TAIL = re.compile(
    r"\b(?:and|or|but|because|the|a|an|to|of|in|on|at|for|with|from|"
    r"be|been|being|do|does|did|have|has|had|will|would|can|could|"
    r"shall|should|may|might|must|"
    r"i['\u2019]ll|you['\u2019]ll|we['\u2019]ll|they['\u2019]ll)"
    r"[^A-Za-z0-9]*$", re.IGNORECASE)

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


#: The second provider. When the primary is rate-limited (Groq's free
#: tier 429s mid-stream), the chat voice used to go dark for the whole
#: breaker window - and the stream does not care whose fault it is. A
#: fallback provider carries chat until the window clears. Its breaker
#: is SEPARATE: a dead fallback key must never take the primary down
#: with it.
FALLBACK_BASE_URL = "https://openrouter.ai/api/v1"

_FALLBACK_DISABLED_UNTIL = 0.0
_ON_FALLBACK = False


def _fallback_unavailable() -> bool:
    return time.time() < _FALLBACK_DISABLED_UNTIL


def _disable_fallback(code: int) -> None:
    """The fallback's own breaker, mirroring _disable()."""
    global _FALLBACK_DISABLED_UNTIL
    if _fallback_unavailable():
        return
    if code in (401, 403):
        _FALLBACK_DISABLED_UNTIL = time.time() + 21600  # ~6h
        print("[llm] fallback key rejected (HTTP %d) - check "
              "llm_fallback_key in config.json. Fallback disabled for "
              "this session." % code, flush=True)
    elif code == 402:
        _FALLBACK_DISABLED_UNTIL = time.time() + 3600
        print("[llm] fallback account has no credits (HTTP 402). "
              "Fallback disabled for an hour.", flush=True)
    elif code == 404:
        _FALLBACK_DISABLED_UNTIL = time.time() + 21600
        print("[llm] fallback model not found (HTTP 404) - free slugs rotate; "
              "pick a live reasoning model from openrouter.ai/models. "
              "Fallback disabled for this session.", flush=True)
    elif code == 429:
        _FALLBACK_DISABLED_UNTIL = time.time() + 120
        print("[llm] fallback rate-limited (HTTP 429); backing off for "
              "2 minutes.", flush=True)


def fallback_endpoint(cfg: dict):
    """(base, key, model) for the second provider, or None when there
    is none.

    Active when llm_fallback_model names a model and either a key is
    set or the fallback base is a local Ollama (which needs no key).
    The base defaults to OpenRouter because that is what a fallback is
    for; point llm_fallback_base_url anywhere else. A fallback that is
    just the primary again (same base and model) is rejected - it would
    fail identically.
    """
    model = (cfg.get("llm_fallback_model")
             or cfg.get("llm_fallback_model_name") or "").strip()
    if not model:
        return None
    base = ((cfg.get("llm_fallback_base_url")
             or cfg.get("llm_fallback_url") or "").strip()
            or FALLBACK_BASE_URL).rstrip("/")
    # Accept the explicit example name and the common ``*_api_key`` spelling.
    # A silently ignored but present key is worse than accepting a harmless
    # alias: it looks exactly like failover is broken in the live log.
    key = (cfg.get("llm_fallback_key")
           or cfg.get("llm_fallback_api_key") or "").strip()
    if not key and not _is_local(base):
        return None
    pbase = ((cfg.get("llm_base_url") or "").strip()
             or DEFAULT_BASE_URL).rstrip("/")
    if base == pbase and model == (cfg.get("llm_model") or "").strip():
        return None
    return base, key, model


def fallback_problem(cfg: dict) -> str:
    """Why no second endpoint can be used, or an empty string when usable."""
    model = (cfg.get("llm_fallback_model")
             or cfg.get("llm_fallback_model_name") or "").strip()
    if not model:
        return "llm_fallback_model is empty"
    base = ((cfg.get("llm_fallback_base_url")
             or cfg.get("llm_fallback_url") or "").strip()
            or FALLBACK_BASE_URL).rstrip("/")
    key = (cfg.get("llm_fallback_key")
           or cfg.get("llm_fallback_api_key") or "").strip()
    if not key and not _is_local(base):
        return "fallback model is set but its API key is empty"
    pbase = ((cfg.get("llm_base_url") or "").strip()
             or DEFAULT_BASE_URL).rstrip("/")
    if base == pbase and model == (cfg.get("llm_model") or "").strip():
        return "fallback is the same provider and model as the primary"
    return ""


def _note_fallback_line(model: str, fallback_model: str) -> None:
    """Announce the switch once per outage, not once per line - the
    breaker already said why."""
    global _ON_FALLBACK
    if not _ON_FALLBACK:
        _ON_FALLBACK = True
        print(f"[llm] {model} is unavailable - LLM answers come from "
              f"{fallback_model} until it clears", flush=True)


def _note_primary_line() -> None:
    global _ON_FALLBACK
    _ON_FALLBACK = False


_NO_FALLBACK_WARNED_UNTIL = 0.0


def _note_no_fallback(cfg: dict) -> None:
    """Explain missing failover once per breaker window, not once per line."""
    global _NO_FALLBACK_WARNED_UNTIL
    now = time.time()
    if now < _NO_FALLBACK_WARNED_UNTIL:
        return
    _NO_FALLBACK_WARNED_UNTIL = now + 120
    print(f"[llm] NO USABLE FALLBACK: {fallback_problem(cfg)}. "
          "Set llm_fallback_model and llm_fallback_key in config.json "
          "(base URL defaults to OpenRouter), then restart.", flush=True)


_warned_404 = False

#: When the most recent chat_reply call timed out (cleared by the next
#: one succeeding). The !ask fallback reads this: stacking a second,
#: BIGGER model call (the question path sends the sources too) on a model
#: that just timed out on a small prompt is a guaranteed extra minute of
#: dead air before the records answer anyway.
_CHAT_TIMEOUT_AT = 0.0


def chat_timed_out() -> bool:
    """True when the most recent chat call timed out - the model is too
    busy or too slow right now (on a shared mini PC, usually another
    generation holding the CPU)."""
    return _CHAT_TIMEOUT_AT > 0.0


def _set_chat_timeout(flag: bool) -> None:
    global _CHAT_TIMEOUT_AT
    _CHAT_TIMEOUT_AT = time.time() if flag else 0.0


def _model_404_hint() -> None:
    """A 404 is not transient: the llm_model slug has no endpoints on
    this provider (OpenRouter retires models). The raw error body does
    not say what to DO about it, so say so once, loudly - every
    LLM-backed feature is quietly running on its fallback until the
    config is fixed."""
    global _warned_404
    if _warned_404:
        return
    _warned_404 = True
    print("[llm] model not found (HTTP 404) - the llm_model slug has no "
          "endpoints on this provider. Model slugs change (OpenRouter "
          "retires models); pick a live one from openrouter.ai/models, or "
          "point llm_base_url at your local Ollama. Until then every "
          "LLM-backed feature uses its fallback. This message prints once.",
          flush=True)


def reset_disable_state() -> None:
    """Testing hook: clear the transient 'LLM disabled' state."""
    global _DISABLED_UNTIL, _FALLBACK_DISABLED_UNTIL, _ON_FALLBACK
    global _NO_FALLBACK_WARNED_UNTIL
    _DISABLED_UNTIL = 0.0
    _FALLBACK_DISABLED_UNTIL = 0.0
    _ON_FALLBACK = False
    _NO_FALLBACK_WARNED_UNTIL = 0.0

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


def _hard_nothink(cfg: dict, base: str) -> bool:
    """Ollama's `think: false` hard switch, for local thinking models.

    /no_think in the prompt is only a soft request - qwen3:4b ignored it
    and thought anyway, and every capped generation died inside the think
    block (stripped to an empty reply; even a 200-token warm-up retry).
    The engine-level switch cannot be overruled by the model. Only sent
    to local endpoints: hosted providers would 400 on the unknown field."""
    return bool(cfg.get("llm_no_think")) and _is_local(base)


def _maybe_nothink(user: str, cfg: dict) -> str:
    """Qwen3-family models think before answering - on a CPU mini PC that
    turns a one-line chat reply into a half-minute stall, and every
    timeout we have goes off. '/no_think' is Qwen3's documented soft
    switch; llm_no_think=true in the config appends it to every prompt.
    Harmless no-op for models that do not know the switch."""
    if cfg.get("llm_no_think"):
        return (user or "").rstrip() + " /no_think"
    return user


def _build_body(model: str, user_prompt: str, system: str = None,
                max_tokens: int = None, hard_nothink: bool = False,
                reasoning_budget: int = None,
                temperature: float = None) -> str:
    messages = [
        {"role": "system", "content": system or SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    body = {"model": model, "messages": messages}
    if hard_nothink:
        body["think"] = False
    if _REASONING.search(model):
        # The completion budget covers thinking AND answer for a
        # reasoning model - too tight and the answer is what gets
        # squeezed out (an empty 200; live-fire: a held mention
        # 'answered' at 17:33:23 came back with nothing at 17:33:24).
        body["max_completion_tokens"] = reasoning_budget or 300
        body["reasoning_effort"] = "low"
    else:
        body["max_tokens"] = max_tokens or 300
        body["temperature"] = 0.9 if temperature is None else temperature
    return json.dumps(body).encode("utf-8")


class _ResponseFormatError(ValueError):
    """A 2xx response that is neither a completion nor an API error."""


def _error_as_http(url: str, data, raw: bytes, headers) -> None:
    """Turn an error envelope delivered with HTTP 200 into HTTPError.

    OpenRouter can commit a successful HTTP status before an upstream model
    fails. For a non-streaming request it then returns ``{"error": ...}`` with
    no ``choices``. Treating that as a malformed completion produced the
    useless live message ``KeyError('choices')`` and, more importantly, hid a
    401/429 from the fallback's independent circuit breaker.
    """
    if not isinstance(data, dict) or not data.get("error"):
        return
    error = data["error"]
    if isinstance(error, dict):
        message = str(error.get("message") or error.get("type")
                      or "provider returned an error")
        raw_code = error.get("code")
    else:
        message = str(error)
        raw_code = None
    try:
        code = int(raw_code)
    except (TypeError, ValueError):
        code = 502
    if code < 400 or code > 599:
        code = 502
    raise urllib.error.HTTPError(
        url, code, message[:200], headers or {}, io.BytesIO(raw))


def _request(base: str, key: str, body: bytes,
             timeout: float = 60.0) -> str:
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
    req = urllib.request.Request(base + "/chat/completions", data=body,
                                 headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        response_headers = getattr(resp, "headers", {})
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise _ResponseFormatError(
            f"non-JSON response from {base}: {exc}") from exc
    _error_as_http(req.full_url, data, raw, response_headers)
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        keys = sorted(str(k) for k in data) if isinstance(data, dict) else []
        shape = f"; top-level fields: {', '.join(keys)}" if keys else ""
        raise _ResponseFormatError(
            f"response has no assistant choice{shape}") from exc
    if not isinstance(content, str):
        raise _ResponseFormatError(
            f"assistant content is {type(content).__name__}, not text")
    text = content.strip()
    # Qwen3 and other thinking models wrap the answer in a <think> block
    # even with the /no_think soft switch. Ollama usually strips it, but
    # not every stack does - and a generation cut mid-think leaves the
    # block unclosed, which means the answer never started. Keep only
    # what follows the last close tag.
    if "<think>" in text:
        if "</think>" in text:
            text = text.rsplit("</think>", 1)[1].strip()
        else:
            text = ""
    return text


def _call(base: str, model: str, key: str, user_prompt: str,
          system: str = None, timeout: float = 60.0,
          max_tokens: int = None, hard_nothink: bool = False,
          reasoning_budget: int = None,
          temperature: float = None) -> str:
    return _request(base, key,
                    _build_body(model, user_prompt, system,
                                max_tokens=max_tokens,
                                hard_nothink=hard_nothink,
                                reasoning_budget=reasoning_budget,
                                temperature=temperature),
                    timeout=timeout)


def chat_reply(system: str, user: str, cfg: dict,
                max_tokens: int = 120) -> str | None:
    """One line of chat personality, or None on any failure.

    The persona's own endpoint: same circuit breaker as everything
    else, plus a SECOND PROVIDER (see fallback_endpoint) so a
    rate-limited primary does not mute the voice for its breaker
    window. SHORT timeout either way - a chime-in that arrives a minute
    after the moment it was for is worse than silence, and chat will
    not wait for it.

    ``max_tokens`` caps the response length. The default 120 is enough
    for one cleaned line; creative requests (songs, poems, stories)
    pass a larger budget so the model has room to write several lines.
    """
    key = (cfg.get("llm_api_key") or "").strip()
    base = (cfg.get("llm_base_url") or DEFAULT_BASE_URL).rstrip("/")
    model = cfg.get("llm_model") or (
        OLLAMA_MODEL if _is_local(base) else DEFAULT_MODEL)
    fb = fallback_endpoint(cfg)
    primary_configured = bool(key) or _is_local(base)
    primary_up = primary_configured and not _unavailable()
    if not primary_up and not (fb and not _fallback_unavailable()):
        if primary_configured and _unavailable() and not fb:
            _note_no_fallback(cfg)
        return None
    _set_chat_timeout(False)
    # A local model on CPU needs a budget a hosted API does not: the chat
    # prompt is the big one (persona + memories + the room), and reading
    # it alone can run 10s+ on a mini PC. 8s was tuned for hosted APIs
    # and kept timing out warm local models; local now self-defaults to
    # the full 30s, and chat_ai_timeout in config still overrides both.
    default_to = 30.0 if _is_local(base) else 8.0
    try:
        timeout = max(2.0, min(float(cfg.get("chat_ai_timeout")
                                      or default_to), 30.0))
    except (TypeError, ValueError):
        timeout = default_to
    prompt = _maybe_nothink(user, cfg)
    if primary_up:
        if cfg.get("debug"):
            print(f"[llm] POST {base}/chat/completions  model={model} "
                  f"(chat, timeout {timeout}s)", flush=True)
            print(f"[llm] ---- chat prompt ----\n{user}", flush=True)
        try:
            # One cleaned line is <= 280 chars (~60 words). 120 tokens is
            # generous for that, and caps the damage a rambling model can
            # do: on CPU, 300 tokens of nobody-will-read-this costs the
            # whole timeout budget.
            text = _call(base, model, key, prompt, system,
                         timeout=timeout, max_tokens=max_tokens,
                         hard_nothink=_hard_nothink(cfg, base))
            if not text or len(text) < 12 \
                    or _DANGLING_TAIL.search(text):
                # An empty 200 - or a reply cut off before the answer
                # finished ('The', 'CyclingWith', 'If they try to slash
                # wages, I'll', all live-fire) - is the reasoning model
                # thinking past its cap, or a filter fluke; not a
                # decline (that is the literal NOTHING TO SAY). One
                # retry at a doubled thinking budget; a cheap second,
                # not a loop. 12 is the cleaner's floor: below it the
                # line could never post; the tail check catches cuts
                # that made it past that floor.
                print(f"[llm] {model} returned an empty chat reply - "
                      f"one retry with a bigger thinking budget",
                      flush=True)
                text = _call(base, model, key, prompt, system,
                             timeout=timeout, max_tokens=max_tokens,
                             hard_nothink=_hard_nothink(cfg, base),
                             reasoning_budget=600)
            if text:
                _note_primary_line()
                return text
            # Still nothing: fall through to the fallback, if there is
            # one - an unanswered mention is the worst silence the bot
            # has (live-fire: the streamer's held message, 17:33:24).
            if fb:
                print(f"[llm] {model} returned nothing twice - the "
                      f"fallback takes this one", flush=True)
        except urllib.error.HTTPError as exc:
            _disable(exc.code)
            if exc.code == 404:
                _model_404_hint()
            elif exc.code not in (401, 402, 403, 429):
                # 400 (a parameter this Ollama build rejects?) and 5xx used
                # to vanish without a line - the single worst way to debug a
                # silent bot.
                detail = ""
                try:
                    detail = exc.read().decode(
                        "utf-8", "replace").strip()[:200]
                except Exception:
                    pass
                print(f"[llm] chat call failed (HTTP {exc.code})"
                      f"{': ' + detail if detail else ''}", flush=True)
        except TimeoutError as exc:
            _set_chat_timeout(True)
            print(f"[llm] chat error: {exc!r} - the model is too busy or too "
                  f"slow right now", flush=True)
        except Exception as exc:
            print(f"[llm] chat error: {exc!r}", flush=True)
    # The primary could not answer - it just failed, or its breaker
    # window from an earlier 429 is still open. A second provider
    # carries the line instead of the room going quiet.
    if fb and not _fallback_unavailable():
        fbase, fkey, fmodel = fb
        # The fallback pays for the primary's budget only when it is the
        # same kind of endpoint: a local Ollama fallback behind a hosted
        # primary would inherit 8s and time out on the prompt read alone.
        try:
            default_to_fb = 30.0 if _is_local(fbase) else 8.0
            fb_timeout = max(timeout, min(float(cfg.get("chat_ai_timeout")
                                                  or default_to_fb), 30.0))
        except (TypeError, ValueError):
            fb_timeout = max(timeout, 30.0 if _is_local(fbase) else 8.0)
        if cfg.get("debug"):
            print(f"[llm] POST {fbase}/chat/completions  model={fmodel} "
                  f"(chat fallback, timeout {fb_timeout}s)", flush=True)
        try:
            text = _call(fbase, fmodel, fkey, prompt, system,
                         timeout=fb_timeout, max_tokens=max_tokens,
                         hard_nothink=_hard_nothink(cfg, fbase))
            _note_fallback_line(model, fmodel)
            return text
        except urllib.error.HTTPError as exc:
            _disable_fallback(exc.code)
            if exc.code == 404:
                _model_404_hint()
            elif exc.code not in (401, 402, 403, 429):
                # A 400/5xx must never vanish silently - the fallback
                # failing quietly reads as 'the bot just stopped'.
                detail = ""
                try:
                    detail = exc.read().decode(
                        "utf-8", "replace").strip()[:200]
                except Exception:
                    pass
                print(f"[llm] fallback chat call failed (HTTP "
                      f"{exc.code}){': ' + detail if detail else ''}",
                      flush=True)
            return None
        except TimeoutError as exc:
            print(f"[llm] fallback chat error: {exc!r} - still too busy",
                  flush=True)
            return None
        except Exception as exc:
            print(f"[llm] fallback chat error: {exc!r}", flush=True)
            return None
    if not fb and _unavailable():
        _note_no_fallback(cfg)
    return None


def _warm_probe(base: str, model: str, key: str, cfg: dict,
                fallback: bool = False) -> str:
    """One warm-up attempt against one provider.

    ``fallback`` makes failures identify and update the fallback's separate
    breaker instead of making a configured-but-dead endpoint look ready.
    """
    started = time.time()
    try:
        text = _call(base, model, key,
                     _maybe_nothink("Reply with exactly: OK", cfg),
                     "You are a warm-up probe. Reply with exactly: OK.",
                     timeout=90.0, max_tokens=24,
                     hard_nothink=_hard_nothink(cfg, base))
    except urllib.error.HTTPError as exc:
        # A warm-up failure must SAY so. OpenRouter may put this error in a
        # 200 body; _request normalises that envelope into the same path.
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace").strip()[:300]
        except Exception:
            pass
        if exc.code in (401, 402, 403, 429) or (fallback and exc.code == 404):
            (_disable_fallback if fallback else _disable)(exc.code)
        readiness = " - fallback NOT READY" if fallback else ""
        print(f"[llm] warm-up of {model} failed (HTTP {exc.code})"
              f"{': ' + detail if detail else ''}{readiness}", flush=True)
        return ""
    except Exception as exc:
        readiness = " - fallback NOT READY" if fallback else ""
        print(f"[llm] warm-up of {model} failed: {exc!r}{readiness}",
              flush=True)
        return ""
    if not text:
        # qwen3 opens with an EMPTY <think></think> block even with
        # /no_think, and the token cap counts the stripped block too - a
        # tight cap can cut the answer right out of the budget. One
        # generous retry, still in the background where nobody waits.
        try:
            text = _call(base, model, key,
                         _maybe_nothink("Reply with exactly: OK", cfg),
                         "You are a warm-up probe. Reply with exactly: OK.",
                         timeout=90.0, max_tokens=200,
                         hard_nothink=_hard_nothink(cfg, base))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode(
                    "utf-8", "replace").strip()[:300]
            except Exception:
                pass
            if exc.code in (401, 402, 403, 429) or \
                    (fallback and exc.code == 404):
                (_disable_fallback if fallback else _disable)(exc.code)
            readiness = " - fallback NOT READY" if fallback else ""
            print(f"[llm] warm-up retry of {model} failed (HTTP {exc.code})"
                  f"{': ' + detail if detail else ''}{readiness}", flush=True)
        except Exception as exc:
            readiness = " - fallback NOT READY" if fallback else ""
            print(f"[llm] warm-up retry of {model} failed: {exc!r}"
                  f"{readiness}", flush=True)
    if text:
        readiness = " - fallback READY" if fallback else ""
        print(f"[llm] warm-up OK - {model} is loaded and answering "
              f"({time.time() - started:.1f}s){readiness}", flush=True)
        return text
    # An empty reply is odd but not fatal - chat lines will try anyway.
    # It must not be SILENT, though: a missing warm-up line in the log is
    # indistinguishable from the feature being off.
    readiness = " - fallback NOT READY" if fallback else ""
    print(f"[llm] warm-up got an empty reply from {model} - chat lines "
          f"will try anyway{readiness}", flush=True)
    return ""


def warm_up(cfg: dict) -> bool:
    """Load the model at startup so the first real line of chat does not
    pay the cold start.

    A local Ollama takes 10-25s to load an 8B model into RAM - longer
    than any chat timeout is allowed to be (chat_reply clamps at 30s) -
    but only the first request pays it. This pays it in the background,
    where nobody is waiting, with a timeout no chat line would ever get.
    Hosted APIs pay nothing but one tiny request - and as a side effect
    a dead model slug (a retired OpenRouter ID) surfaces at startup,
    not at the first mention.

    A configured fallback is warmed too: the moment it is needed - the
    primary just got rate-limited mid-stream - is the worst possible
    time to discover a cold local model or a dead slug.
    """
    ok = False
    base = (cfg.get("llm_base_url") or DEFAULT_BASE_URL).rstrip("/")
    key = (cfg.get("llm_api_key") or "").strip()
    if (key or _is_local(base)) and not _unavailable():
        model = cfg.get("llm_model") or (
            OLLAMA_MODEL if _is_local(base) else DEFAULT_MODEL)
        ok = bool(_warm_probe(base, model, key, cfg))
    fb = fallback_endpoint(cfg)
    if fb and not _fallback_unavailable():
        fbase, fkey, fmodel = fb
        fok = bool(_warm_probe(fbase, fmodel, fkey, cfg, fallback=True))
        if fok and not ok:
            print("[llm] the primary could not be warmed - chat will "
                  "run on the fallback", flush=True)
        ok = ok or fok
    return ok


def summarize(fact: str, max_chars: int, cfg: dict) -> str | None:
    """Shorten ``fact`` through either provider, keeping its details."""
    if not any_configured(cfg):
        return None
    key = (cfg.get("llm_api_key") or "").strip()
    base = (cfg.get("llm_base_url") or DEFAULT_BASE_URL).rstrip("/")
    model = cfg.get("llm_model") or (
        OLLAMA_MODEL if _is_local(base) else DEFAULT_MODEL)
    user = f"Max characters: {max_chars}\nFact: {fact}\n\nShortened fact:"
    if cfg.get("debug"):
        print(f"[llm] POST {base}/chat/completions  model={model} "
              f"(summarize)", flush=True)
        print("[llm] ---- user prompt ----\n" + user, flush=True)
    text = _complete(base, model, key, user, cfg, tag="summarize",
                     system=_SUMMARIZE_SYSTEM, max_tokens=120,
                     reasoning_budget=120, temperature=0.3)
    return (text or "").strip().strip('"“”') or None


def _is_local(base: str) -> bool:
    b = (base or "").lower()
    return ("localhost" in b or "127.0.0.1" in b or "ollama" in b or ":11434" in b)


def is_configured(options: dict) -> bool:
    """True if the primary is an API key or a local Ollama endpoint."""
    if (options.get("llm_api_key") or "").strip():
        return True
    return _is_local(options.get("llm_base_url") or "")


def any_configured(options: dict) -> bool:
    """True when the primary or configured second provider is usable."""
    return is_configured(options) or fallback_endpoint(options) is not None


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
    if not any_configured(cfg):
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


def _complete_provider(base: str, model: str, key: str, user: str,
                       cfg: dict, tag: str, system: str = None,
                       timeout: float = None, fallback: bool = False,
                       max_tokens: int = None,
                       reasoning_budget: int = None,
                       temperature: float = None) -> str | None:
    """Try one provider, including its compatible spare-model slug.

    ``fallback`` selects the provider's independent circuit breaker. A broken
    fallback must never disable the primary, and a primary 429 must never stop
    the configured second provider from taking the same request.
    """
    candidates = [model]
    # The explicitly configured second-provider model is the last resort; do
    # not silently spend money on that provider's generic spare slug.
    spare = None if fallback else _fallback_model(base, model)
    if spare:
        candidates.append(spare)

    last_detail = ""
    for m in candidates:
        try:
            call_options = {
                "timeout": timeout if timeout is not None else 60.0,
                "hard_nothink": _hard_nothink(cfg, base),
            }
            if max_tokens is not None:
                call_options["max_tokens"] = max_tokens
            if reasoning_budget is not None:
                call_options["reasoning_budget"] = reasoning_budget
            if temperature is not None:
                call_options["temperature"] = temperature
            text = _call(base, m, key, _maybe_nothink(user, cfg), system,
                         **call_options)
            if cfg.get("debug"):
                print(f"[llm] ---- response ----\n" + text, flush=True)
            return text or None
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", "replace").strip()[:300]
            except Exception:
                detail = ""
            last_detail = detail
            if exc.code in (401, 403, 402, 429):
                # Auth / billing / rate-limit: another model on THIS provider
                # fails the same way. Open only this provider's breaker; the
                # outer wrapper can immediately try the other provider.
                (_disable_fallback if fallback else _disable)(exc.code)
                return None
            if fallback and exc.code == 404:
                _disable_fallback(404)
                return None
            if exc.code in (400, 404, 422) and m != candidates[-1]:
                print(f"[llm] model '{m}' failed (HTTP {exc.code}); trying "
                      f"same-provider spare...", flush=True)
                continue
            if exc.code == 404 and not fallback:
                _model_404_hint()
            label = "fallback " if fallback else ""
            print(f"[llm] {label}HTTP {exc.code}: {detail}", flush=True)
            return None
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            last_detail = repr(exc)
            if m != candidates[-1]:
                print(f"[llm] bad response from '{m}': {exc!r}; trying "
                      f"same-provider spare...", flush=True)
                continue
            label = "fallback " if fallback else ""
            print(f"[llm] {label}bad response from '{m}': {exc!r}",
                  flush=True)
            return None
        except Exception as exc:
            label = "fallback " if fallback else ""
            print(f"[llm] {label}error: {exc!r}", flush=True)
            return None
    print(f"[llm] all models failed: {last_detail}", flush=True)
    return None


def _complete(base: str, model: str, key: str, user: str, cfg: dict,
              tag: str, system: str = None, timeout: float = None,
              max_tokens: int = None, reasoning_budget: int = None,
              temperature: float = None) -> str | None:
    """Complete through the primary and then the configured second provider.

    This is the shared path for sourced answers, fact rewriting, freeform facts
    and summarisation. Previously only ``chat_reply`` knew about the second
    provider, so a Groq 429 during a factual question ignored a perfectly good
    fallback.
    """
    primary_configured = bool(key) or _is_local(base)
    if primary_configured and not _unavailable():
        text = _complete_provider(
            base, model, key, user, cfg, tag, system=system, timeout=timeout,
            max_tokens=max_tokens, reasoning_budget=reasoning_budget,
            temperature=temperature)
        if text:
            _note_primary_line()
            return text

    fb = fallback_endpoint(cfg)
    if fb and not _fallback_unavailable():
        fbase, fkey, fmodel = fb
        text = _complete_provider(
            fbase, fmodel, fkey, user, cfg, tag, system=system,
            timeout=timeout, fallback=True, max_tokens=max_tokens,
            reasoning_budget=reasoning_budget, temperature=temperature)
        if text:
            _note_fallback_line(model, fmodel)
            return text
    if not fb and _unavailable():
        _note_no_fallback(cfg)
    return None


ANSWER_SYSTEM = (
    "You answer one question in one line for a Twitch chat bot.\n"
    "Rules, and they are absolute:\n"
    "- Answer ONLY from the sources supplied. Do not add a name, a number, a\n"
    "  date, a unit or a place that is not in them, even if you are sure.\n"
    "- If the sources do not actually answer the question, reply with exactly\n"
    "  NOTHING RELIABLE and nothing else.\n"
    "- Never name a person, channel or thing the sources do not name.\n"
    "- Give the concrete fact itself - the name, number, date or place the "
    "sources state - never a statement that the answer exists.\n"
    "- One line, no numbering, no preamble, no 'according to'.\n"
)


def answer_question(question: str, sources: list, cfg: dict) -> str | None:
    """One answer line drawn only from `sources`, or None.

    The difference between this and freeform_facts is the whole point: that
    one asks the model what it knows, which is how the bot came to post that
    Stinker was the world's most famous landmark according to Explore
    magazine. This one gives it text and asks it to use nothing else, and the
    caller then checks the answer against that same text.
    """
    if not any_configured(cfg) or not sources:
        return None
    key = (cfg.get("llm_api_key") or "").strip()
    base = (cfg.get("llm_base_url") or DEFAULT_BASE_URL).rstrip("/")
    model = cfg.get("llm_model") or (
        OLLAMA_MODEL if _is_local(base) else DEFAULT_MODEL)

    try:
        max_chars = int(cfg.get("max_fact_chars") or 200)
    except (TypeError, ValueError):
        max_chars = 200
    max_chars = max(60, min(max_chars, 480))

    user = (
        f"Question: {question}\n"
        f"Answer in at most {max_chars} characters, using only the sources "
        f"below.\n\nSources:\n"
    )
    # A local model on CPU reads every source line before writing a
    # word; 8 sentences is 400-800 tokens of reading before the answer
    # even starts. Fewer sources, and a local-sized budget instead of
    # _complete's 60s default.
    local = _is_local(base)
    user += "\n".join(f"- {s}" for s in sources[:5 if local else 8])

    if cfg.get("debug"):
        print(f"[llm] POST {base}/chat/completions  model={model}", flush=True)
        print(f"[llm] ---- answer prompt ----\n{user}", flush=True)

    if local:
        return _complete(base, model, key, user, cfg, tag="",
                         system=ANSWER_SYSTEM, timeout=30.0)
    return _complete(base, model, key, user, cfg, tag="", system=ANSWER_SYSTEM)


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
    if not any_configured(cfg):
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

