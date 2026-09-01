"""Optional LLM pass for beef stories. The templates stay the engine of record.

The deal, and it is a strict one:

* The story is SCHEDULED before the model is asked: the headline (template,
  with its @-tag decision) goes out immediately and the acts are drip-fed on
  gaps of seconds. That makes the first gap the model's deadline - if it
  delivers in time chat gets its lines, if it does not the template lines
  fill the slot and nobody can tell the difference.
* The winner is rolled BEFORE the model is called and handed over as a fact
  the output must match. The leaderboard and the !revenge window are scored
  off that roll, never off model text - a story that disagrees with the roll
  is not a story, it is a validation failure, and the templates take over.
* Everything the templates guarantee, validate() demands: four lines, the
  Act prefixes, the exact winner, both names, length limits, no @-pings
  (pinging is the bot's decision, not the model's), no URLs, hashtags or
  markdown. One miss, one fallback.
* No LLM configured, key rejected, out of credits, rate-limited, slow,
  or creative with the rules: templates. The game cannot wait on this module
  and cannot break because of it - which is why beef.py and beefstats.py
  still import nothing but the standard library, and check_fixes keeps it
  that way.
"""

from __future__ import annotations

import json
import random
import re
import urllib.error
import urllib.request

import beef
import llm

__all__ = ["available", "write_story", "validate"]

#: Hard ceiling for one line, leaving room for the "BEEF | " head the bot
#: adds. Over this, the whole story is rejected - a split model line would
#: break the five-message shape the pacing is built on.
MAX_LINE = 440

SYSTEM = (
    "You write one short comic feud story for a Twitch chat. The tone is "
    "barstool trash-talk: specific, silly, escalating. Funny, never cruel.\n"
    "HARD RULES:\n"
    "- Use ONLY the two names supplied. Never add another person's name, and "
    "never invent a real-world detail about either player (no crimes, drugs, "
    "affairs, no comments on looks, race, gender, religion or health).\n"
    "- The winner is FIXED and given to you. The story must end with exactly "
    "that winner. You do not choose the winner.\n"
    "- Reply with EXACTLY 4 lines and nothing else - no preamble, no "
    "numbered list, no markdown, no code fences:\n"
    "  Act 1 — <the incident that started it, one or two sentences>\n"
    "  Act 2 — <the escalation, one or two sentences>\n"
    "  Act 3 — <the showdown; the fixed winner wins it>\n"
    "  🏆 WINNER: <fixed winner>. <one short exit line for the loser>\n"
    "- Each line at most 200 characters.\n"
    "- No @ mentions, no hashtags, no URLs, no markdown, no emoji except the "
    "trophy in the last line.\n"
    "- ABSOLUTELY BANNED: sexual content, slurs, hate speech, threats. "
    "Fictional chaos only."
)


def _enabled(cfg: dict) -> bool:
    v = cfg.get("beef_llm", "auto")
    if isinstance(v, str):
        return v.strip().lower() in ("auto", "on", "yes", "true", "1")
    return bool(v)


def available(cfg: dict) -> bool:
    """True if the LLM pass should even be tried for a beef story."""
    if not _enabled(cfg):
        return False
    return llm.is_configured(cfg) and not llm._unavailable()


def _deadline(cfg: dict) -> float:
    """Seconds the model gets. Never more than the gap before Act 1, so the
    pacing of the story is guaranteed regardless of what the model does."""
    try:
        timeout = float(cfg.get("beef_llm_timeout", 3.0) or 3.0)
    except (TypeError, ValueError):
        timeout = 3.0
    try:
        delay = float(cfg.get("beef_act_delay", 4.0) or 0.0)
    except (TypeError, ValueError):
        delay = 4.0
    first_gap = 0.75 * delay if delay > 0 else 0.0
    return max(1.0, min(timeout, first_gap or timeout))


def _prompt(result: dict) -> str:
    pools = beef.GENRES[result["genre"]]
    examples = random.sample(list(pools["sparks"]) + list(pools["climaxes"]),
                             min(2, len(pools["sparks"]) + len(pools["climaxes"])))
    ex = "\n".join("- " + e.format(a=result["issuer"], b=result["rival"],
                                   w=result["winner"], l=result["loser"])
                   for e in examples)
    setting = random.choice(beef.GENRES[result["genre"]]["settings"])
    return (
        f"Setting: {setting}.\n"
        f"Player A: {result['issuer']}\n"
        f"Player B: {result['rival']}\n"
        f"WINNER (fixed, do not change): {result['winner']}\n"
        f"LOSER (fixed, do not change): {result['loser']}\n"
        f"\nTone examples from this genre - match the energy, do not copy the "
        f"lines:\n{ex}\n"
    )


def _request(cfg: dict, body: bytes, timeout: float) -> str | None:
    base = (cfg.get("llm_base_url") or llm.DEFAULT_BASE_URL).rstrip("/")
    headers = {"Content-Type": "application/json",
               "User-Agent": llm.USER_AGENT,
               "Accept": "application/json"}
    key = (cfg.get("llm_api_key") or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if "openrouter" in base:
        headers["HTTP-Referer"] = "https://localhost"
        headers["X-Title"] = "TruckingWithDoc FunFact Bot"
    req = urllib.request.Request(base + "/chat/completions", data=body,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return (data["choices"][0]["message"]["content"] or "").strip()
    except urllib.error.HTTPError as exc:
        # Keep the fact engine's backoff semantics: a dead key or empty
        # account pauses the LLM everywhere, not just here.
        try:
            llm._disable(exc.code)
        except Exception:
            pass
        return None
    except Exception:
        return None          # slow, unreachable, malformed: quietly a fallback


def write_story(result: dict, cfg: dict) -> list | None:
    """Four story lines (acts + verdict) from the model, or None to fall back.

    `result` is what beef.feud() returned - names, genre, and the pre-rolled
    winner the output must agree with.
    """
    if not available(cfg):
        return None
    model = cfg.get("llm_model") or (
        llm.OLLAMA_MODEL if llm._is_local(cfg.get("llm_base_url") or "")
        else llm.DEFAULT_MODEL)
    body = {"model": model, "messages": [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": _prompt(result)}]}
    if llm._REASONING.search(model):
        body["max_completion_tokens"] = 500
        body["reasoning_effort"] = "low"
    else:
        body["max_tokens"] = 400
        body["temperature"] = 1.0
    text = _request(cfg, json.dumps(body).encode("utf-8"), _deadline(cfg))
    if not text:
        return None
    return validate(text, result)


_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


def validate(raw: str, result: dict) -> list | None:
    """Check a model's story against every invariant the templates guarantee.

    Returns the four cleaned lines, or None - and None always means "use the
    templates", never "post something unchecked".
    """
    text = (raw or "").strip()
    if not text:
        return None
    if "```" in text:
        text = _FENCE.sub("", text).strip()

    lines = [re.sub(r"^[\s\-\u2022\d.)]+", "", ln).strip()
             for ln in text.splitlines()]
    lines = [re.sub(r"  +", " ", ln) for ln in lines if ln.strip()]
    if len(lines) != 4:
        return None

    winner, loser = result["winner"], result["loser"]
    issuer, rival = result["issuer"], result["rival"]
    for i, prefix in ((0, "Act 1"), (1, "Act 2"), (2, "Act 3")):
        if not lines[i].startswith(prefix):
            return None
    if not lines[3].startswith(f"\U0001f3c6 WINNER: {winner}."):
        return None

    # Both players star in the acts themselves. The verdict always names the
    # pair, so checking the whole story would let acts about strangers
    # through - exactly the substitution validate() exists to stop.
    acts = " ".join(lines[:3])
    if issuer not in acts or rival not in acts:
        return None

    for ln in lines:
        if not ln or len(ln) > MAX_LINE:
            return None
        low = ln.lower()
        for bad in ("@", "http", "#", "*", "{", "}"):
            if bad in ln:
                return None
        # The model does not get to address the room or break character.
        if low.startswith(("sure", "here", "okay", "note:")):
            return None
    return lines
