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
one emoji, and no command syntax (the persona never sends viewers to
!funfact - it answers itself or says nothing). The rules a regex cannot
enforce live in the system prompt: tease topics, never people; no threats,
no creepiness, no medical or grief jokes; never state a fact it is not
certain of (factual questions are routed to the fact engine elsewhere);
never guess anything personal about anyone; never repeat your own recent
lines. A model with nothing worth saying replies NOTHING TO SAY and the
bot stays quiet.

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

#: Whose room this is. Every voice gets this, because a voice that
#: knows the streamer aims its lines at what is actually on screen -
#: the load, the run, the ride, the game - instead of generic chatter.
_CHANNEL = (
    "THE STREAMER: an army veteran - an airborne combat medic, two "
    "tours of Afghanistan - who now drives semi trucks across the "
    "country and makes it as fun as he can. He runs truck-stop 5Ks, "
    "streams indoor runs and cycling rides, and mixes in shooter "
    "nights: Fortnite, Warzone, Red Dead Redemption 2. His world is "
    "yours - draw on it when it fits, never recite his resume."
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
    "- Never state a fact you are not certain of - factual questions are "
    "answered elsewhere; you hold opinions and stories.\n"
    "- NEVER mention or point viewers at commands like !funfact or !ask. "
    "YOU are the one answering: answer yourself, or reply NOTHING TO "
    "SAY.\n"
    "- Vary every line. Do not recycle phrasing, metaphors or openers from "
    "your recent lines; necessary words from the current topic are fine. "
    "Not every line ends with a question.\n"
    "- Never force truck, coffee, cadence, mileage, workout or negative-split "
    "references into an unrelated reply. No generic motivation or slogans. "
    "Sound like a person responding to this conversation, not a coach or a "
    "scheduled quote bot.\n"
    "- When asked your opinion of a person or their news, give your take "
    "on the SITUATION - never pivot to a different subject.\n"
    "- Never guess, reveal or invent personal information about anyone.\n"
    "- If nothing is worth saying, reply with exactly: NOTHING TO SAY\n"
)

_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF]")

# Last-resort acknowledgement after both model attempts violate the output
# rails. This is intentionally not a guessed answer: it tells the viewer the
# bot heard them, and prevents a later message from looking like the answer.
DIRECT_FAILURE_LINE = (
    "I heard you, but my answer got mangled in the gears. Try me once more."
)

#: A bare dotted word autolinks in chat clients ('config.json' is a real
#: TLD) - the beef game already bans them, and so does the chat AI.
_DOMAIN = re.compile(r"\b[a-z0-9][a-z0-9-]*\.[a-z]{2,}\b")

MENTION = "mention"
CHIME = "chime"


def system_prompt(persona: str = "") -> str:
    """The persona plus the hard rules, as one system prompt. The
    streamer's story sits between them: a custom voice written by a mod
    in twelve characters still learns whose room it is talking in."""
    return ((persona or DEFAULT_PERSONA).strip() + "\n\n" + _CHANNEL
            + _RULES)


def user_prompt(lines: list, nick: str, text: str,
                memories: list = None, quiet: bool = False,
                max_lines: int = 15, max_memories: int = 8,
                own: list = None, overheard: bool = False) -> str:
    """What the model sees: what it remembers, the room, the moment, the
    ask. Memories are [(nick, fact)] - the distilled facts about the
    people present, which is what makes the reply feel like it knows
    them. `quiet` is the dead-room case: no one said anything, and the
    bot's job is to get the conversation going.

    max_lines/max_memories trim the prompt: a local model on CPU has to
    READ every token of it before writing a word, and that read - not
    the generation - was the cost blowing past a 20s timeout on a warm
    model. Callers point these at smaller values for local models."""
    out = []
    if not quiet and not overheard:
        # Put the actual ask before the room as well as at the final answer
        # cue. Some reasoning models latched onto an older question in Recent
        # chat even though the old prompt named the latest one only at the end.
        out.append("MESSAGE TO ANSWER (highest priority):")
        out.append(f"{nick}: {text}")
        out.append("Answer this message only. Older chat is context, never a "
                   "different question to answer.")
        out.append("")
    if own:
        # The bot's own lines are in the room buffer too, and a small
        # model left alone with them mimics itself - the 'midnight
        # coffee and donuts' loop. Naming them makes the instruction
        # impossible to miss.
        out.append("Your own last lines - the room just saw these:")
        out.extend(f"- {l}" for l in own[-3:])
        out.append("Do not recycle their phrasing, imagery or opener. Words "
                   "needed for the CURRENT topic are allowed. Do not end with "
                   "a question merely because the last line did.")
        out.append("")
    if memories:
        out.append("What you remember about people here (from past chat,"
                   " may be stale). Each fact belongs to the person it is"
                   " listed under ONLY - never use one person's fact when"
                   " replying to somebody else:")
        out.extend(f"- {n}: {f}" for n, f in memories[:max_memories])
        out.append("")
    out.append("Recent chat (it has gone quiet):" if quiet
               else "Recent chat:")
    out.extend(f"{n}: {t}" for n, t in lines[-max_lines:])
    out.append("")
    if quiet:
        out.append(
            "Nobody has spoken for a while. Continue the MOST RECENT HUMAN "
            "topic above with one natural question or observation. It must "
            "clearly name that topic. Do not give motivation, a slogan, or "
            "generic truck/coffee/workout/cadence filler. If the last topic "
            "is not worth reopening, reply NOTHING TO SAY.")
    elif overheard:
        # A chime-in, not a reply: the model must know nobody addressed
        # it, or it treats an overheard remark like a question asked of
        # it and answers conversations it was never part of.
        out.append(f"{nick} said to the room (NOT to you): {text}")
        out.append("")
        out.append(
            "You are jumping in on your own initiative. Reply ONLY if "
            "you genuinely have something to add to exactly what was "
            "said - a relevant quip or a related story about THAT "
            "subject. Your line must be about what they actually said. No "
            "generic encouragement and no forced truck, coffee, workout, "
            "cadence or mileage references. Performing your persona at the "
            "room is NOTHING TO SAY. When in doubt, reply NOTHING TO SAY.")
    else:
        out.append(f"{nick} just said: {text}")
        out.append("They are talking to YOU: answer THIS message - the "
                   "earlier room chat is context, not the question.")
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


def direct_context(lines: list, names, prefix: str = "!",
                   bot_nick: str = "") -> list:
    """Human conversation context with commands and old bot asks removed.

    Older questions addressed to the bot are competing instructions, not
    context. Keeping them in the prompt caused "Docbot you ok?" to receive an
    answer to an earlier Zwift question. Commands are excluded for the same
    reason (the current ``!ask`` is supplied separately by its caller), and
    the bot's own lines are already supplied through the dedicated ``own``
    block. This clean human-only room is also what quiet openers continue.
    """
    out = []
    bot_low = (bot_nick or "").lower()
    for nick, text in lines or []:
        t = (text or "").strip()
        if not t or (bot_low and (nick or "").lower() == bot_low):
            continue
        if prefix and t.startswith(prefix):
            continue
        if mention_kind(t, names):
            continue
        out.append((nick, text))
    return out


def _blocked_output(line: str) -> bool:
    """Whether normalized model output crosses a non-length safety rail."""
    if "@" in line:                     # mentions are prepended by the bot
        return True
    if _DOMAIN.search(line):
        return True
    if len(_EMOJI.findall(line)) > 1:
        return True          # the rules always said at most one
    if re.search(r"![a-zA-Z]", line):
        return True          # command syntax belongs to viewers, not the bot
    if re.search(r"\b(?:funfact|ask)\b\s+(?:command|for (?:more|the lowdown))",
                 line, re.IGNORECASE):
        return True          # "check !funfact" died with the redirect rule
    return bool(funfacts._EXPLICIT.search(line)
                or funfacts._TASTELESS.search(line))


def clean_line(line: str) -> str | None:
    """One safe line of chat, or None. The output gate."""
    line = " ".join((line or "").split()).strip('"\u201c\u201d')
    if not line or len(line) < 12 or len(line) > 280:
        return None
    return None if _blocked_output(line) else line


def recover_direct_line(line: str, limit: int = 240) -> str | None:
    """Recover a safe short answer from an overlong direct reply.

    Reasoning models occasionally ignore the 240-character instruction and
    return a paragraph. Throwing the whole answer away made a direct mention
    look broken. Only LENGTH is recoverable: if any part of the raw output
    crosses a safety rail, none of it is used. ``trim_to_fit`` keeps a whole
    sentence where possible and otherwise lands on a clause/word boundary.
    Ambient chimes never use this -- silence is fine when nobody asked us.
    """
    raw = " ".join((line or "").split()).strip('"\u201c\u201d')
    if len(raw) <= 280 or _blocked_output(raw):
        return None
    candidate = funfacts.trim_to_fit(raw, max(80, min(int(limit), 280)))
    # A pathological token wall ("xxxx..."), base64, etc. is not prose and
    # trim_to_fit cannot invent a boundary for it.
    if len(re.findall(r"\b[\w'\u2019-]+\b", candidate)) < 4:
        return None
    return clean_line(candidate)


def declined(raw: str) -> bool:
    """True when the model said it has nothing worth saying."""
    up = (raw or "").strip().upper()
    return (not up) or "NOTHING TO SAY" in up


def recent_chat_count(message_times, now: float, window: float = 300.0) -> int:
    """Number of human messages inside a rolling activity window."""
    return sum(1 for t in (message_times or []) if now - t <= window)


def room_is_busy(message_times, now: float, window: float = 30.0,
                 messages: int = 4) -> bool:
    """True while chat is flowing too quickly for an unsolicited bot line.

    Direct mentions do not use this gate. Four human messages in thirty
    seconds is already a conversation; the bot should listen until asked.
    """
    try:
        threshold = max(2, int(messages))
        span = max(5.0, float(window))
    except (TypeError, ValueError):
        threshold, span = 4, 30.0
    return recent_chat_count(message_times, now, span) >= threshold


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
    if not enabled or paused or kind is None:
        return False
    # !cb off is the autonomous-chatter switch. Someone explicitly addressing
    # the bot is not autonomous chatter and must remain answerable.
    if ambient_off and kind != MENTION:
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
    # The hourly cap is for autonomous chatter. Someone explicitly talking to
    # the bot still gets an answer (paced by the mention cooldown); otherwise a
    # lively Q&A hour makes later direct questions disappear behind an ambient
    # anti-spam rail.
    if kind != MENTION \
            and len([t for t in times if now - t < 3600]) >= max_hour:
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
    re.compile(r"\b(?:(?:are|r) )?(?:you|u) (?:ok|okay|alright)\b",
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


#: Playful digs at the bot, and the words that aim a dig AT it. A dig
#: deserves a comeback, not silence - "you have alot of useless facts"
#: getting nothing (model busy) read as the bot being broken.
_TEASE = (
    re.compile(r"\b(?:useless|pointless|stupid|dumb|boring|overrated|"
               r"trash|garbage|junk|lame|annoying|sucks?|shut up)\b",
               re.IGNORECASE),
)
_AT_BOT = re.compile(r"\b(?:you|your|u|ur)\b|\bshut up\b",
                     re.IGNORECASE)
_INTERROGATIVE = re.compile(
    r"^(?:what|whats|what's|whos|who's|who|when|where|why|how|which|is|"
    r"are|was|were|do|does|did|can|could|would|should|tell|name)\b",
    re.IGNORECASE)

#: A message worth chiming in on needs actual words. Emojis, "???",
#: numbers and single twitch-emote characters are characters but not
#: conversation - the model asked to react to a picture produces a
#: monologue about nothing (live-fire: a desert-emoji wall got
#: "Midnight desert runs: engine hum, hot sand...").
_ALPHA = re.compile(r"[A-Za-z]")


def chime_worthy(text: str) -> bool:
    """True when a message has enough words in it to be worth the bot's
    own two cents: at least 4 characters and 3 letters. Mentions and
    !ask are NOT gated by this - a direct address always deserves an
    answer, however it is phrased."""
    t = (text or "").strip()
    return len(t) >= 4 and len(_ALPHA.findall(t)) >= 3


#: Common words that carry no identity - excluded from similarity.
_STOPWORDS = frozenset((
    "with", "that", "this", "your", "yours", "what", "whats", "when",
    "where", "which", "does", "have", "just", "like", "them", "they",
    "been", "over", "will", "would", "could", "from", "were", "about",
    "while", "theres", "here", "keep", "keeps", "rolling", "road",
))


def _content_words(line: str) -> set:
    return {w for w in re.findall(r"[a-z]{4,}", (line or "").lower())
            if w not in _STOPWORDS}


def _phrase_bigrams(line: str, exempt=None) -> set:
    """Meaningful adjacent-word pairs, excluding pairs made only of filler."""
    tokens = re.findall(r"[a-z]+(?:['\u2019][a-z]+)?",
                        (line or "").lower())
    exempt = exempt or set()
    out = set()
    for a, b in zip(tokens, tokens[1:]):
        phrase = f"{a} {b}"
        if _content_words(phrase) - exempt:
            out.add(phrase)
    return out


def too_similar(line: str, own_lines, jaccard: float = 0.3,
                source: str = "") -> bool:
    """True when a candidate recycles the bot's recent wording.

    Topic words present in the message being answered are exempt: two answers
    about cadence are allowed to say "cadence". What is rejected is a repeated
    signature across several bot lines, a shared phrase/template, or high
    overall overlap. The old rule rejected *any one word* shared with the
    previous line, which discarded sensible direct answers constantly.
    """
    exempt = _content_words(source)
    words = _content_words(line) - exempt
    recent_lines = list(own_lines or [])[-3:]
    recent = [_content_words(l) - exempt for l in recent_lines]
    if not words or not recent:
        return False
    # A word the model has made a motif across at least two previous lines.
    if any(sum(1 for s in recent if w in s) >= 2 for w in words):
        return True
    # A repeated phrase catches template reuse such as "I'm swapping ..."
    # without treating one necessary shared noun as a duplicate answer.
    phrases = _phrase_bigrams(line, exempt)
    if any(phrases & _phrase_bigrams(old, exempt) for old in recent_lines):
        return True
    for s in recent:
        if s and len(words & s) / len(words | s) >= jaccard:
            return True
    return False


def grounded(line: str, source: str) -> bool:
    """True when a chime actually answers the message it jumps in on.

    Live-fire: 'yeah I hipped 1athlete to that supplement' got
    '@PiMPleff The freezer rattles like wind through pine trees, and
    I'm swapping frozen beans for a steaming oat latte' - a persona
    poem at a person who said nothing about freezers, which is the
    single most awkward thing the bot does. The bar: the reply shares
    at least one content word with the message it is replying to. A
    relevant line almost always names the thing being discussed; a
    musing at the room never does. Only chime-ins are gated - a
    mention or an !ask was addressed to the bot, so relevance is
    already given."""
    return bool(_content_words(line) & _content_words(source))


def parrots(line: str, source: str, jaccard: float = 0.6) -> bool:
    """True when a chime mostly repeats the message it answers.

    This is NOT too_similar with a higher bar: too_similar's
    word-sharing rule would reject every GOOD chime, because a good
    chime shares words with what was said. Only near-total overlap is
    parroting - the model handing chat's own line back."""
    words = _content_words(line)
    src = _content_words(source)
    if not words or not src:
        return False
    return len(words & src) / len(words | src) >= jaccard


#: 'take a mental note...', 'make a note...', 'remember this...'. The
#: speaker is TELLING the bot to store what follows - live-fire, the
#: streamer logged 'its 2:49am on the 15th September 2026 and
#: @TruckingWithDoc just took a piss in Sullivan,MO Truck stop' and the
#: bot had nowhere to put it, so the later quiz got persona mush.
_NOTE_ASK = re.compile(
    r"\b(?:take|make)\s+(?:a\s+)?(?:mental\s+not?e?s?|note)\b[\s:,-]*"
    r"|\bremember\s+(?:this|that)\b[\s:,-]*", re.IGNORECASE)
_AT_NAME = re.compile(r"@([A-Za-z0-9_]{3,})")


def note_request(text: str):
    """('take a mental note X') -> (subject nick or None, X), or None.

    The subject is the @-mentioned name in the payload when there is
    one (the note is ABOUT them), else the speaker. None when the
    message is not a note ask or the payload is too small to keep -
    memory holds facts of 8-200 characters."""
    m = _NOTE_ASK.search(text or "")
    if not m:
        return None
    payload = (text or "")[m.end():].strip()
    payload = re.sub(r"^(?:its|it's|it is|that)\b\s*", "", payload,
                     count=1, flags=re.IGNORECASE).strip()
    if not 8 <= len(payload) <= 200:
        return None
    at = _AT_NAME.search(payload)
    return (at.group(1) if at else None, payload)


def named_people(text: str) -> list:
    """Names a message might be about: @mentions as typed, plus
    capitalised words (display names are CamelCase - CyclingWithDoc).
    Recall questions name their subject, and that person may not have
    spoken recently enough to sit in the room buffer."""
    out = list(_AT_NAME.findall(text or ""))
    out += re.findall(r"\b([A-Z][a-zA-Z0-9]{3,})\b", text or "")
    return out


#: The voice library. Mods switch between these at runtime (!persona set),
#: so each has to be a fully-formed character that can carry one-liners
#: in a rowdy trucking chat. All of them sit under the same HARD RULES -
#: a persona changes the voice, never the rails.
PERSONAS = {
    # Doc is the canonical default - one definition, referenced here.
    "doc": DEFAULT_PERSONA,
    # Voices from the streamer's own world: the unit, the CB, the
    # lobby, the trail, the frontier. Every one of them also receives
    # _CHANNEL above, so they know whose stream they are talking in.
    "medic": (
        "You are the Medic: an airborne combat medic who served with "
        "the streamer and still looks out for everybody in his chat. "
        "Calm, clipped, unbothered by chaos. You count water bottles "
        "like ammo and treat morale like a vital sign - sleep, "
        "stretching and hydration get hyped like they are "
        "mission-critical, because to you they are. You never diagnose "
        "and never joke about injuries."
    ),
    "cb": (
        "You are the CB Voice: 1970s Citizens Band radio coming "
        "through a modern chat. Breaker one-nine, 10-4 good buddy, "
        "keep the shiny side up. You hand out handles, read chat like "
        "road reports - bear in the bushes, clean pass on the "
        "interstate - and call the streamer 'Driver'. Warm, silly, "
        "relentless; every line sounds like it fought through static "
        "to get here."
    ),
    "squaddie": (
        "You are the Squaddie: the streamer's longtime drop partner "
        "from Warzone and Fortnite lobbies. Comms discipline, hype "
        "comms, zero tilt. You talk in callouts and rotations, defend "
        "his loadout picks like family, and celebrate every win like "
        "it is championship Sunday. When the squad goes down you are "
        "the calm one calling the next play."
    ),
    "coach": (
        "You are the Coach: a track-and-trail hype man who believes "
        "the streamer can negative-split anything. You count miles "
        "like money, praise effort over talent, and hold strong "
        "opinions about cadence at all times. Indoor runs, cycling "
        "rides, truck-stop 5Ks - it is all training to you, and chat "
        "is the team. Everybody leaves with a clap on the back and "
        "homework."
    ),
    "cowboy": (
        "You are the Gunslinger: an old-west drift riding through Red "
        "Dead Redemption nights. Slow drawl, 'partner', 'reckoning'. "
        "You treat every session like a trail ride and every win like "
        "a duel won at high noon. Laconic - when you finally speak, "
        "it lands. You never hurry, never fuss, and you find the "
        "romance in an open trail and a good horse."
    ),
    # The roadhouse floor: fun ones that fit the room without being
    # FROM the streamer's story.
    "flo": (
        "You are Flo, a truck-stop diner waitress who has worked the "
        "counter for forty years and seen every load, every liar and "
        "every 3am meatloaf special. You call everybody 'hon', top up "
        "coffee nobody asked for, remember everyone's usual order, and "
        "nothing on this earth shocks you. Sassy, warm, and the kitchen "
        "closes when Flo says it closes."
    ),
    "commentator": (
        "You are the Commentator: a veteran British sports broadcaster "
        "calling the stream like a championship final. Measured, posh, "
        "immaculately civil and quietly hilarious - a Warzone drop, a "
        "treadmill mile and a merge lane all receive the same grave "
        "play-by-play. Understatement is your sharpest tool: disasters "
        "are 'regrettable', brilliance is 'a decent effort, one feels'."
    ),
    "noir": (
        "You are the Noir: a private eye narrating the stream like a "
        "case file. Rain on the windshield, neon in the puddles, a "
        "mystery in every mile. You talk out of the side of your "
        "mouth, trust no weigh station, and every answer sounds like "
        "it cost somebody something. Short, hard sentences."
    ),
    # The roadhouse originals.
    "sarge": (
        "You are Sarge, a retired army dispatcher who now runs dispatch "
        "for a one-truck outfit. You bark short orders, count everything "
        "in minutes saved, and treat every chat message like a radio "
        "check. Loud, not cruel: under the bark you genuinely care about "
        "the crew. You consider 'good' a full sentence."
    ),
    "rookie": (
        "You are the Rookie, three weeks into your first trucking job "
        "and terrified of everything: weigh stations, DOT inspections, "
        "merge lanes, geese. Every reply is earnest, slightly panicked, "
        "and accidentally funny. You over-explain, you apologise, and "
        "you REALLY want to keep this job."
    ),
    "rusty": (
        "You are Rusty, a shop mechanic with forty years of grease under "
        "your nails. Everything can be fixed with the right hammer, half "
        "a roll of duct tape and language your mother would hate. You "
        "judge people by their equipment, you are stingy with praise, "
        "and you have seen every way a truck can die."
    ),
    "nightshift": (
        "You are the Nightshift, a late-night AM radio DJ voice trapped "
        "in a truck cab. Smooth, unhurried, a little poetic about "
        "highways and diners at 3am. You murmur one-liners like "
        "dedications, find romance in fuel-stop coffee, and never raise "
        "your voice."
    ),
}


def persona(name: str) -> str | None:
    """The persona's prompt text by name, or None. Case-insensitive."""
    n = (name or "").strip().lower()
    return PERSONAS.get(n)


#: A three-or-four-word tag per voice, for !persona list - a mod who
#: has never read the README should still be able to tell 'cb' from
#: 'cowboy'. Must stay in sync with PERSONAS; a test pins it.
PERSONA_BLURBS = {
    "doc": "the long-haul dry wit",
    "medic": "airborne medic",
    "cb": "1970s CB radio",
    "squaddie": "Warzone drop partner",
    "coach": "run and ride hype man",
    "cowboy": "Red Dead trail hand",
    "flo": "truck-stop diner waitress",
    "commentator": "posh play-by-play",
    "noir": "hardboiled detective",
    "sarge": "barking dispatcher",
    "rookie": "three weeks on the job",
    "rusty": "shop mechanic",
    "nightshift": "3am AM-radio voice",
}


#: Factual questions about a third-party thing. These have a real answer
#: the fact engine can look up; the persona guessing ("sounds like a spin
#: on a roadside snack") is worse than the engine's grounded answer or an
#: honest miss. Opened by an interrogative and never about the bot.
_FACTUAL_Q = re.compile(
    r"^\s*(?:whats|what|what's|whos|who|who's|when|where|which|"
    r"how many|how much|how long|how old|how tall|how far)\b",
    re.IGNORECASE)
_ABOUT_BOT = re.compile(r"\b(?:you|your|u|ur)\b", re.IGNORECASE)
# Collective phrasing is still an opinion request aimed at the persona:
# "what do we think of people who ride at 0%?" is not encyclopedia trivia.
# Keep this narrower than a bare "we" so "what do we know about Mars" can
# still use the grounded fact engine.
_OPINION_Q = re.compile(
    r"^\s*(?:what\s+do\s+(?:we|you|u)\s+think\b|"
    r"what(?:'s|\s+is)\s+(?:our|your)\s+(?:take|opinion)\b|"
    r"how\s+do\s+(?:we|you|u)\s+feel\b)", re.IGNORECASE)


def strip_address(text: str, names=()) -> str:
    """Drop a leading address to the bot: 'doc, what is a bongo twist'
    -> 'what is a bongo twist'. Mentions carry their trigger word, and
    neither the fact engine's header nor its query should include it."""
    t = (text or "").strip()
    low = t.lower()
    for n in sorted({str(x).lower().lstrip("@") for x in names if x},
                    key=len, reverse=True):
        if low.startswith(n):
            rest = t[len(n):].lstrip(" ,:!")
            if rest:
                t = rest
            break
    return t


def factual_question(text: str, names=()) -> bool:
    """True when the text asks about a third-party thing the fact engine
    can look up - 'what is a bongo twist'. Questions about the bot
    ('what do you think of this', 'whats your favorite truck') are
    False: those are the persona's job, and routing them at the fact
    engine would answer a question nobody asked. A leading address to
    the bot ('doc, what is a bongo twist') is stripped first - mentions
    carry their trigger word."""
    t = strip_address(text, names)
    if not _FACTUAL_Q.match(t) or _OPINION_Q.match(t):
        return False
    return not _ABOUT_BOT.search(t)


#: Opinion questions aimed at the bot ("are you a Miami Dolphins fan?",
#: "do you like tacos?"). The model should answer these; when it is down,
#: a deflection beats silence. Never fires for factual questions - the
#: trigger needs you+an allegiance verb, so "do you know how long the
#: Amazon is" does not match.
_OPINION_ASKED = re.compile(
    r"\b(?:are|do|does)\s+(?:you|u)\b.*\b"
    r"(?:fan|like|support|root(?:ing)?\s+for|follow)\b", re.IGNORECASE)
_OPINION_LINES = (
    "I'm a fan of anything I can enjoy from the driver's seat.",
    "My allegiance is to the mile markers.",
    "I follow the freight, not the standings.",
    "Ask me again when the coffee's down.",
)

_COMEBACKS = (
    "Useless? Those facts are load-bearing.",
    "They're not useless, they're highly specialised.",
    "Somebody's gotta know this stuff. Might as well be me.",
    "One of those useless facts won me fifty bucks once.",
    "You say useless, I say conversation insurance.",
    "I've hauled worse cargo.",
)


def smalltalk(text: str):
    """A canned Doc line for a chatty, non-factual message, or None.

    Used when the persona model is unreachable: "doc, hows it going?"
    gets a line instead of silence, and !ask "how are you today?" gets a
    line instead of a Wikipedia fact about the word "today". Playful digs
    at the bot get a comeback - but only digs aimed AT the bot ("you have
    alot of useless facts"), never a question ("whats the most useless
    fact") or someone venting about their own day ("my stupid internet").
    Factual questions return None so they take the real paths - a canned
    line is never posted where a fact is being asked for.
    """
    text = text or ""
    if funfacts._EXPLICIT.search(text) or funfacts._TASTELESS.search(text):
        return None
    if any(p.search(text) for p in _SMALLTALK):
        return random.choice(_SMALLTALK_LINES)
    if (any(p.search(text) for p in _TEASE)
            and _AT_BOT.search(text)
            and not funfacts._SPECIFIC_Q.search(text)
            and not _INTERROGATIVE.search(text.strip())):
        return random.choice(_COMEBACKS)
    if _OPINION_ASKED.search(text):
        return random.choice(_OPINION_LINES)
    return None
