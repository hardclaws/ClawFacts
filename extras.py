"""Extra free, keyless fun commands — inspired by free API lists used by other
Twitch chatbots (e.g. abdullahmorrison/twitch-chatbot).

Every endpoint below was tested and is currently live. All return plain text
(no keys, no auth). Each function returns None on any failure so the bot can
just say "couldn't fetch that" instead of crashing.

    !joke            official-joke-api.appspot.com
    !randomfact      uselessfacts.jsph.pl
    !riddle          riddles-api.vercel.app  (answer revealed by the bot later)
    !wouldyourather  api.truthordarebot.xyz/v1/wyr
"""

from __future__ import annotations

import json
import urllib.error
import random
import urllib.request

USER_AGENT = "TwitchFunFactBot/1.0 (hobby Twitch chat bot)"


def _get_json(url: str, timeout: float = 8.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _clean(s: str) -> str:
    return " ".join((s or "").split())


def get_joke() -> str | None:
    """A random one-liner-style joke (setup + punchline)."""
    try:
        d = _get_json("https://official-joke-api.appspot.com/random_joke")
    except (OSError, ValueError, KeyError):
        return None
    setup, punchline = _clean(d.get("setup")), _clean(d.get("punchline"))
    if not setup or not punchline:
        return None
    return f"{setup} {punchline}"


def get_random_fact() -> str | None:
    """A random (useless) fact — good filler for long stretches of road."""
    try:
        d = _get_json("https://uselessfacts.jsph.pl/api/v2/facts/random?language=en")
    except (OSError, ValueError, KeyError):
        return None
    text = _clean(d.get("text"))
    return text or None


def get_riddle() -> tuple[str, str] | None:
    """Returns (riddle, answer). The bot shows the answer after a delay."""
    try:
        d = _get_json("https://riddles-api.vercel.app/random")
    except (OSError, ValueError, KeyError):
        return None
    riddle, answer = _clean(d.get("riddle")), _clean(d.get("answer"))
    if not riddle or not answer:
        return None
    return riddle, answer


def get_wyr() -> str | None:
    """A 'would you rather' question."""
    try:
        d = _get_json("https://api.truthordarebot.xyz/v1/wyr")
    except (OSError, ValueError, KeyError):
        return None
    question = _clean(d.get("question"))
    return question or None

# ---- shag / marry / kill ---------------------------------------------------
# A local pool rather than an API: no third-party dependency, no waiting on a
# network call mid-command, and every name is a public figure so nobody in chat
# gets named by accident.
_SMK_FEMALE = [
    "Rihanna", "Beyoncé", "Dolly Parton", "Florence Pugh", "Margot Robbie",
    "Sandra Bullock", "Zendaya", "Viola Davis", "Charlize Theron", "Aubrey Plaza",
    "Betty White", "Lucille Ball", "Jamie Lee Curtis", "Olivia Colman",
    "Hayley Atwell", "Kate McKinnon", "Tina Fey", "Sofia Vergara",
    "Emma Thompson", "Sigourney Weaver", "Rachel McAdams", "Awkwafina",
]
_SMK_MALE = [
    "Idris Elba", "Keanu Reeves", "Danny DeVito", "Pedro Pascal", "Steve Buscemi",
    "Matthew McConaughey", "Bryan Cranston", "Oscar Isaac", "Jeff Goldblum",
    "Paul Rudd", "Harrison Ford", "Morgan Freeman", "Tom Hanks", "Jason Momoa",
    "Dev Patel", "Giancarlo Esposito", "Willem Dafoe", "Chris Pratt",
    "Daniel Kaluuya", "Rami Malek", "Viggo Mortensen", "Seth Rogen",
]


def get_smk(gender: str = "any"):
    """Three names for shag / marry / kill.

    `gender` is "female", "male" or "any" (mixed). Returns None if the pool is
    too small to draw three distinct names.
    """
    g = (gender or "any").strip().lower()
    if g in ("f", "female", "women", "woman", "her"):
        pool = list(_SMK_FEMALE)
        label = "female"
    elif g in ("m", "male", "men", "man", "him"):
        pool = list(_SMK_MALE)
        label = "male"
    else:
        pool = list(_SMK_FEMALE) + list(_SMK_MALE)
        label = "any"
    if len(pool) < 3:
        return None
    picks = random.sample(pool, 3)
    return picks, label
