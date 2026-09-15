"""The chat AI: an optional persona that can hold its own in chat.

Three layers, deliberately boring:

  * ``!ask <anything>`` - a viewer asks, the persona answers. Without an
    LLM key the ask falls through to the fact engine's question path,
    which answers questions from Wikipedia alone.
  * mention replies - someone says "doc, ..." and the bot answers, on a
    short cooldown so it cannot be wound up like a toy.
  * chime-ins - on a busy channel the bot occasionally adds a line of its
    own: a probability gate, a long cooldown and an hourly cap, so it
    never dominates the room. The streamer's own messages never trigger
    chime-ins - he already has the floor - but directly addressing the
    bot by name does get a reply.

Everything it says passes one cleaning gate: one short line, no @mentions
(they are prepended by the caller), no links, no explicit content, at most
two emoji. The rules a regex cannot enforce live in the system prompt:
tease topics, never people; no threats, no creepiness, no medical or grief
jokes; never state a fact it is not certain of (that is !funfact's job);
never guess anything personal about anyone. A model with nothing worth
saying replies NOTHING TO SAY and the bot stays quiet.

Off by default: ``chat_ai_enabled`` in config.json.
"""

import random
import re

import funfacts

#: The voice, when the config's bot_personality is empty.
DEFAULT_PERSONA = (
    "Doc, a dry-witted old trucker who has been everywhere twice. Decades "
    "of long-haul, one dog, strong coffee, zero patience for nonsense - "
    "but warm under the gravel, and he likes this chat."
)

#: The hard rules, appended to whatever persona is configured. These are
#: the lines the channel saw a stranger's bot cross: insults, threats,
#: creepiness, invented facts, personal guesses. Enforced by prompt here
#: and by clean_line() below; the two must never drift apart.
_RULES = (
    "\nYou are the chat member of a live trucking stream. HARD RULES:\n"
    "- ONE line, at most 240 characters, plain text.\n"
    "- At most one emoji. No hashtags, no links, no @mentions.\n"
    "- Tease topics, never people. No insults, no threats, nothing "
    "creepy, no politics, no medical or grief jokes.\n"
    "- Never state a fact you are not certain of. If chat wants a fact, "
    "point them at the !funfact command instead.\n"
    "- Never guess, reveal or invent personal information about anyone.\n"
    "- If nothing is worth saying, reply with exactly: NOTHING TO SAY\n"
)

_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF]")
#: A bare dotted word autolinks in chat clients ('config.json' is a real
#: TLD) - the beef game already bans them, and so does the chat AI.
_DOMAIN = re.compile(r"\b[a-z0-9][a-z0-9-]*\.[a-z]{2,}\b")

MENTION = "mention"
CHIME = "chime"


def system_prompt(persona: str = "") -> str:
    """The persona plus the hard rules, as one system prompt."""
    return (persona or DEFAULT_PERSONA).strip() + "\n" + _RULES


def user_prompt(lines: list, nick: str, text: str,
                memories: list = None, quiet: bool = False) -> str:
    """What the model sees: what it remembers, the room, the moment, the
    ask. Memories are [(nick, fact)] - the distilled facts about the
    people present, which is what makes the reply feel like it knows
    them. `quiet` is the dead-room case: no one said anything, and the
    bot's job is to get the conversation going."""
    out = []
    if memories:
        out.append("What you remember about people here (from past chat,"
                   " may be stale):")
        out.extend(f"- {n}: {f}" for n, f in memories[:8])
        out.append("")
    out.append("Recent chat (it has gone quiet):" if quiet
               else "Recent chat:")
    out.extend(f"{n}: {t}" for n, t in lines[-15:])
    out.append("")
    if quiet:
        out.append(
            "Nobody has spoken for a while. Say ONE line to get the "
            "conversation going - a question for chat, a hook from your "
            "trucking life, or an observation. Nothing like your last "
            "few lines.")
    else:
        out.append(f"{nick} just said: {text}")
    out.append("")
    out.append("Your line:")
    return "\n".join(out)


def mention_kind(text: str, names) -> str | None:
    """MENTION when the message names the bot, else None.

    Names match on word boundaries, so 'doc' does not fire inside
    'dockyard', and a bare 'docbot' or '@doc' both work.
    """
    low = " " + (text or "").lower() + " "
    for name in names:
        n = (name or "").strip().lower().lstrip("@")
        if n and re.search(r"(?<![a-z0-9])" + re.escape(n)
                           + r"(?![a-z0-9])", low):
            return MENTION
    return None


def clean_line(line: str) -> str | None:
    """One safe line of chat, or None. The output gate."""
    line = " ".join((line or "").split()).strip('"\u201c\u201d')
    if not line or len(line) < 12 or len(line) > 280:
        return None
    if "@" in line:                     # mentions are prepended by the bot
        return None
    if _DOMAIN.search(line):
        return None
    if len(_EMOJI.findall(line)) > 2:
        return None
    if funfacts._EXPLICIT.search(line) or funfacts._TASTELESS.search(line):
        return None
    return line


def declined(raw: str) -> bool:
    """True when the model said it has nothing worth saying."""
    up = (raw or "").strip().upper()
    return (not up) or "NOTHING TO SAY" in up


def should_speak(*, enabled: bool, paused: bool, ambient_off: bool,
                 kind: str | None, roll: float, chance: float,
                 now: float, last: float, mention_last: float,
                 mention_cd: float, chime_cd: float, times: list,
                 max_hour: int, buffer_len: int, min_chat: int) -> bool:
    """May the bot speak right now? Pure, so every gate is testable.

    Mention replies are cheap (someone addressed the bot by name) and only
    wait out the short cooldown, on their OWN clock: a quiet-room opener
    followed five seconds later by a human answering the bot is exactly
    the conversation the feature exists for, and sharing one clock with
    the openers used to mute it for a minute. Chime-ins are the bot's own
    initiative: they must win the probability roll, the room must have
    enough chatter to be worth joining, and they wait out the long
    cooldown.
    """
    if not enabled or paused or ambient_off or kind is None:
        return False
    if kind == MENTION:
        if now - mention_last < mention_cd:
            return False
    else:
        if roll >= chance:
            return False
        if buffer_len < min_chat:
            return False
        if now - last < chime_cd:
            return False
    if len([t for t in times if now - t < 3600]) >= max_hour:
        return False
    return True


#: Chatty, non-factual things people say to the bot. The persona answers
#: these when it is up; when it is not, a canned Doc line keeps the bot
#: from going mute on direct address - and keeps the fact engine from
#: answering "how are you today?" with the history of the word "today".
_SMALLTALK = (
    re.compile(r"\bhow (?:are|r) (?:you|u)\b", re.IGNORECASE),
    re.compile(r"\bhow(?:'s| is|s) it going\b", re.IGNORECASE),
    re.compile(r"\bhow (?:you|u) doin\b", re.IGNORECASE),
    re.compile(r"\bwhat(?:'s| is|s) up\b", re.IGNORECASE),
    re.compile(r"\bsup\b", re.IGNORECASE),
    re.compile(r"\bwho are (?:you|u)\b", re.IGNORECASE),
    re.compile(r"\bwhat(?:'s| is|s) your name\b", re.IGNORECASE),
    re.compile(r"\bare (?:you|u) (?:a bot|real|human|alive|an ai)\b",
               re.IGNORECASE),
    re.compile(r"\bhow old are (?:you|u)\b", re.IGNORECASE),
    re.compile(r"\bsay something\b", re.IGNORECASE),
    re.compile(r"\bwhere are (?:you|u) (?:from|at)\b", re.IGNORECASE),
    re.compile(r"\bi love (?:you|u)\b", re.IGNORECASE),
)

_SMALLTALK_LINES = (
    "Running fine. Ask me something with a fact in it.",
    "Better than the freight, worse than the coffee.",
    "Still here. Me and the truck both idle a little rough.",
    "Old enough to remember when diesel was cheap.",
    "Doc. I drive, I talk, I mostly drive.",
    "On the road in my head even when I'm parked.",
)


def smalltalk(text: str):
    """A canned Doc line for a chatty, non-factual message, or None.

    Used when the persona model is unreachable: "doc, hows it going?"
    gets a line instead of silence, and !ask "how are you today?" gets a
    line instead of a Wikipedia fact about the word "today". Factual
    questions return None so they take the real paths - a canned line is
    never posted where a fact is being asked for.
    """
    text = text or ""
    if funfacts._EXPLICIT.search(text) or funfacts._TASTELESS.search(text):
        return None
    if any(p.search(text) for p in _SMALLTALK):
        return random.choice(_SMALLTALK_LINES)
    return None
