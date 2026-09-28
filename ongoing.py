"""What is going on right now: the chat AI's working memory.

Long-term memory (memory.py) keeps durable facts about people. This is
the other kind - the short-lived, high-priority kind a person in the
room holds without effort: the trivia game the streamer asked the bot
to host and which round it is on; the count of Dirty Lepages it was
told to keep. Live-fire, the bot had none of it. It posed round one of
a three-round cycling quiz, then for round two asked chat the
temperature in Rolla, Missouri, then explained that it was "just the
on-stream info bot"; an hour after "keep count of Dirty Lepages" (the
count was at one) it had no idea what a Dirty Lepage was. Every prompt
was built from the last few HUMAN lines - the bot's own lines and every
earlier line addressed to it stripped out, on purpose, because old asks
were hijacking new answers - and the long-term distill keeps, by rule,
only what a viewer says about THEMSELVES. Nothing anywhere held "what
we are doing".

Two things live here, both saved to ongoing.json so a restart mid-game
is not amnesia:

  * ONE activity - a game the bot was asked to run: what it is, who
    asked, how many rounds, which round is open, the questions it has
    asked so far. Recognised from plain chat addressed to the bot
    ("lets do a test run. Topic would be Cycling and lets make it 3
    rounds", "start the first round", "lets get Round 2 going", "whats
    the answer", "game over"). The bot cannot know trivia itself, so
    the MODEL still writes the questions - but every prompt now tells
    it what it is hosting, where it is up to, and what its line must
    actually DO next, and a line that is not a question is not posted
    as one. A timing ask ("give 30 secs to answer then start round 3
    30 secs after that") becomes a cadence: the bot reveals the answer
    and moves on by itself, one scheduled step at a time.
  * TALLIES - named counters ("keep count of Dirty Lepages"), bumped
    ("spotted one", "+1", "add one to dirty lepages"), read back ("how
    many dirty lepages so far?") and closed ("stop counting ...") with
    no model in the loop at all: a count is arithmetic, and the model
    is the one part of this bot that cannot be trusted with it.

The parsers are pure functions (testable without a bot); the Ongoing
class holds the state under a lock and saves through storage.save_json.
bot.py owns the chat, the authorisation and the timers.
"""

from __future__ import annotations

import re
import threading
import time

import funfacts
import storage

STATE_PATH = "ongoing.json"
MAX_TALLIES = 12           # named counters kept at once, oldest dropped
MAX_ROUNDS = 20            # a 'game' longer than this is a typo
MAX_LOG = 8                # questions/answers remembered for the prompt
MIN_CADENCE = 10.0         # seconds - a shorter answer window is a stampede
MAX_CADENCE = 600.0        # ten minutes between steps is a forgotten game
DONE_MINUTES = 10.0        # a finished game stays 'another round?'-able this long
TALLY_PROMPT_HOURS = 12.0  # tallies older than this stay stored but leave the prompt

#: Who may do what. Starting or ending a game and opening, closing,
#: resetting or setting a count are for the mods and the broadcaster -
#: a viewer cannot make the bot run things. The round controls of a
#: running game are for its host and the mods. Reading a count or the
#: game's status is for anyone; bumping a count is for the mods and
#: whoever opened it unless the config says viewers may.
MOD_KINDS = ("start", "end", "tally_start", "tally_stop", "tally_reset",
             "tally_set")
HOST_KINDS = ("round", "reveal", "score")
COUNT_KINDS = ("tally_add", "tally_sub")
STEP_KINDS = ("start", "round", "reveal", "score", "end")

NOT_A_MOD_LINE = "that one's for the mods or the streamer."
NOT_THE_HOST_LINE = "the host calls the rounds and the answers - hang tight."
NOTHING_RUNNING_LINE = "nothing's running right now - no game, no count."


class Control:
    """One recognised instruction about the activity or a tally."""
    __slots__ = ("kind", "key", "label", "n", "topic", "rounds", "what",
                 "cadence", "go", "repeat", "after", "auto")

    def __init__(self, kind, key=None, label=None, n=None, topic=None,
                 rounds=None, what=None, cadence=None, go=False,
                 repeat=False, after=None, auto=False):
        self.kind = kind
        self.key = key
        self.label = label
        self.n = n
        self.topic = topic
        self.rounds = rounds
        self.what = what
        self.cadence = cadence
        self.go = go
        self.repeat = repeat
        self.after = after      # auto steps: the round they follow
        self.auto = auto        # scheduled by the cadence, not said by a person

    def __repr__(self):
        return "Control(%s)" % ", ".join(
            "%s=%r" % (k, getattr(self, k)) for k in self.__slots__
            if getattr(self, k) not in (None, False))


class Step:
    """What must happen now. `spoken` steps carry their own line
    (`cue` IS the line); the others carry the cue the model gets."""
    __slots__ = ("kind", "cue", "round", "last", "generation")

    def __init__(self, kind, cue, round_no=0, last=False, generation=0):
        self.kind = kind
        self.cue = cue
        self.round = round_no
        self.last = last
        self.generation = generation

    @property
    def spoken(self) -> bool:
        return self.kind in ("intro", "nudge", "end")


# ---- numbers ---------------------------------------------------------------
_NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
              "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
              "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
              "twenty": 20, "thirty": 30, "forty": 40, "fortyfive": 45,
              "sixty": 60, "ninety": 90, "another": 1}
_ORD_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
              "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
              "opening": 1}


def _num(token) -> int | None:
    t = re.sub(r"[\s-]+", "", (token or "").lower())
    if not t:
        return None
    m = re.match(r"^(\d+)(?:st|nd|rd|th)?$", t)
    if m:
        return int(m.group(1))
    if t in _ORD_WORDS:
        return _ORD_WORDS[t]
    return _NUM_WORDS.get(t)


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z]{4,}", (text or "").lower())
            if w not in ("what", "which", "that", "this", "with", "does",
                         "have", "from", "your", "their", "there", "they",
                         "were", "when", "where", "round", "question",
                         "name", "many")}


def _ago(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return "just now"
    m = s // 60
    if m < 60:
        return "%d min ago" % m
    h, m = divmod(m, 60)
    return ("%dh %dmin ago" % (h, m)) if m else ("%dh ago" % h)


def _strip_names(text: str, names) -> str:
    """Every bot name out of the line, wherever it sits: 'hey docbot lets
    do trivia' parses like 'hey lets do trivia'."""
    t = " ".join((text or "").split())
    ns = sorted({str(n).lower().lstrip("@") for n in (names or ()) if n},
                key=len, reverse=True)
    if ns:
        t = re.sub(r"(?<![a-z0-9])@?(?:" + "|".join(re.escape(n) for n in ns)
                   + r")(?![a-z0-9])[,:!\s-]*", " ", t, flags=re.IGNORECASE)
    return " ".join(t.split()).strip()


# ---- tallies: the parsers --------------------------------------------------
#: 'keep count of X', 'keep a tally of X', 'keep track of X', 'start
#: counting X', 'run a count on X', 'count the X for us'.
_TALLY_START = re.compile(
    r"\b(?:keep|start|run|open|begin|maintain)\s+(?:a\s+|the\s+)?(?:running\s+)?"
    r"(?:count|tally|counter|score|tab|record)\s+(?:of|on|for)\s+"
    r"(?:the\s+)?(?:number\s+of\s+|how\s+many\s+)?(?P<w1>.+)"
    r"|\bkeep\s+track\s+of\s+(?:the\s+)?(?:number\s+of\s+|how\s+many\s+)?(?P<w2>.+)"
    r"|\bstart\s+(?:counting|tallying|tracking)\s+(?:the\s+)?(?P<w3>.+)"
    r"|\bcount\s+(?:the\s+)?(?P<w4>.+?)\s+(?:for\s+(?:us|me)|from\s+now(?:\s+on)?|"
    r"tonight|today|this\s+stream|as\s+(?:we|they)\s+(?:go|come|happen))\b",
    re.IGNORECASE)
#: Where the thing being counted ends and the instructions about it begin:
#: 'Dirty Lepages and we will tell the bot when we spot one'.
_LABEL_CUT = re.compile(
    r"\s+(?:and|&)\s+(?:we|i|you|u|ill|i'll|we'll|well|then|let|lets|let's|"
    r"tell|say|shout|call)\b.*$"
    r"|\s*[,.;!?].*$"
    r"|\s+-+\s+.*$"
    r"|\s+(?:when|whenever|every\s+time|each\s+time|as\s+(?:we|they|you)|"
    r"so\s+(?:we|that|when)|please|plz|pls|for\s+(?:us|me|the\s+stream|tonight|"
    r"today)|from\s+now|tonight|today|this\s+stream|on\s+stream|starting\s+now)\b.*$"
    r"|\s+(?:we|you|i|u)\s+(?:see|spot|hear|get|catch|find|notice|call)\b.*$",
    re.IGNORECASE)
_LABEL_LEAD = re.compile(
    r"^(?:the|of|all|any|every|our|how\s+many|number\s+of|amount\s+of|"
    r"times\s+(?:we\s+)?(?:see|hear|spot)|times|instances\s+of)\s+",
    re.IGNORECASE)
_LABEL_TAIL = re.compile(
    r"\s+(?:count|counts|tally|so\s+far|tonight|today|this\s+stream|we\s+see|"
    r"spotted|sightings?|happens?)$", re.IGNORECASE)


def tally_label(raw: str) -> str | None:
    """The thing being counted, as chat will see it, or None when the
    payload is unusable (too short, too long, no letters, or something
    the output rails would refuse to post)."""
    t = " ".join((raw or "").split())
    t = _LABEL_CUT.sub("", t).strip()
    for _ in range(3):
        t2 = _LABEL_TAIL.sub("", _LABEL_LEAD.sub("", t)).strip(" \"'\u201c\u201d")
        if t2 == t:
            break
        t = t2
    t = t.strip(" \"'\u201c\u201d\u2018\u2019")
    if not 2 <= len(t) <= 40 or not re.search(r"[A-Za-z]", t):
        return None
    if funfacts._EXPLICIT.search(t) or funfacts._TASTELESS.search(t):
        return None
    return t


def _singular(word: str) -> str:
    w = word.lower()
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _norm(text: str) -> str:
    return " ".join(_singular(w) for w in
                    re.findall(r"[a-z0-9']+", (text or "").lower()))


def tally_key(label: str) -> str:
    """'Dirty Lepages' / 'dirty lepage' / 'DIRTY  LEPAGES' -> one key."""
    return _norm(label)


def tally_find(text: str, tallies: dict) -> str | None:
    """The key of the tally the text names, or None. Plural-blind
    ('lepage spotted' finds 'Dirty Lepages'), longest label first so
    'dirty lepages' beats a tally called 'lepages'."""
    norm = _norm(text)
    if not norm or not tallies:
        return None
    for key in sorted(tallies, key=len, reverse=True):
        if re.search(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])", norm):
            return key
    for key in sorted(tallies, key=len, reverse=True):
        words = key.split()
        if len(words) >= 2 and len(words[-1]) >= 4 and re.search(
                r"(?<![a-z0-9])" + re.escape(words[-1]) + r"(?![a-z0-9])", norm):
            return key
    return None


_INC = re.compile(
    r"(?:^|[\s(])\+\s*(?P<n1>\d+)?(?![\w.])"
    r"|\bplus\s+(?P<n2>\d+|one|two|three|four|five|another)\b"
    r"|\badd\s+(?:on\s+|in\s+)?(?P<n3>\d+|one|two|three|four|five|another|a|an)\b"
    r"|\bchalk\s+(?:(?P<n4>\d+|one|two|three|another)\s+)?up\b"
    r"|\bchalk\s+up\s+(?P<n5>\d+|one|two|three|another)\b"
    r"|\b(?:another\s+one|one\s+more|and\s+another|1\s+more|another)\b"
    r"|\b(?P<n6>\d+|two|three|four|five)\s+more\b"
    r"|\b(?:spotted|sighted|sighting|seen\s+(?:one|another|a|an)|"
    r"saw\s+(?:one|another|a|an|\d+)|caught\s+(?:one|another|a|an)|"
    r"found\s+(?:one|another|a|an)|got\s+(?:one|another|a|an)|"
    r"heard\s+(?:one|another|a|an)|clocked\s+(?:one|another|a|an))\b"
    r"|\b(?:that|there|here)(?:'s|s|\s+is|\s+was|\s+goes|\s+went)\s+"
    r"(?P<n7>\d+|one|two|three|another|a|an)\b"
    r"|\b(?:mark|count|log|tick|note|score)\s+(?:it|that|this|one|him|her|them)\b"
    r"|\bthat\s+counts\b|\bcounts\s+as\s+one\b|\bbump\s+(?:it|the\s+(?:count|tally))\b"
    r"|\bup\s+(?:by\s+)?(?:one|1)\b|\bincrement\b"
    r"|\b(?:one|1)\s+(?:to|for|on)\s+the\s+(?:count|tally|board|list)\b",
    re.IGNORECASE)
#: The increments that are unmistakable even when the message does not
#: name the count: '+1', 'plus one', 'add one', 'another one', 'spotted
#: one'. 'another' alone is not one of them - 'tell us another joke'.
_INC_PLAIN = re.compile(
    r"^\W*(?:\+\s*\d*|plus\s+(?:\d+|one|two|three|another)|add\s+(?:\d+|one|two|"
    r"three|another|a|an)|another\s+one|one\s+more|\d+\s+more|spotted(?:\s+(?:one|"
    r"another|a|an|\d+))?|(?:saw|seen|caught|found|got|heard|clocked)\s+(?:one|"
    r"another|a|an|\d+)|(?:that|there|here)(?:'s|s|\s+is|\s+was|\s+goes)\s+"
    r"(?:one|another(?:\s+one)?|\d+)|that\s+counts|count\s+(?:it|that|this|one)|"
    r"mark\s+(?:it|that|"
    r"one)|chalk\s+(?:one\s+)?up|bump\s+it|up\s+(?:by\s+)?(?:one|1)|"
    r"(?:one|1)\s+(?:to|for|on)\s+the\s+(?:count|tally|board))\W*"
    r"(?:lol|haha|omg|boom|ding|yes|yep|there|please|pls|plz)?\W*$",
    re.IGNORECASE)
_DEC = re.compile(
    r"(?:^|\s)-\s*(?P<n1>\d+)\b"
    r"|\bminus\s+(?P<n2>\d+|one|two|three)\b"
    r"|\b(?:take|knock)\s+(?P<n3>\d+|one|two|three|that|it)\s+(?:off|away|back|out)\b"
    r"|\b(?:subtract|remove|drop)\s+(?P<n4>\d+|one|two|three)\b"
    r"|\b(?:scratch|undo|cancel|ignore|strike)\s+(?:that|the\s+last(?:\s+one)?|one|it)\b"
    r"|\bfalse\s+alarm\b|\bnever\s*mind\s+(?:that|the\s+last)\b"
    r"|\bdown\s+(?:by\s+)?(?:one|1)\b|\bdecrement\b",
    re.IGNORECASE)
_SET = re.compile(
    r"\b(?:set|put|make|correct|fix|change|update|adjust)\b[^?]*?"
    r"\b(?:to|at|=|is|as)\s*(?P<n1>\d+)\b"
    r"|\b(?:is|are|should\s+be|shouldve\s+been|should've\s+been|was|were)\s+"
    r"(?:now\s+|actually\s+|at\s+|really\s+|up\s+to\s+)?(?P<n2>\d+)\s*"
    r"(?:so\s+far|now|actually|total|by\s+now|already)?\s*[.!]*$"
    r"|\b(?:count|tally|total|counter)\s*(?:is|=|:)\s*(?P<n3>\d+)\b"
    r"|^\W*[A-Za-z][\w' -]{1,40}?\s*[:=]\s*(?P<n4>\d+)\W*$",
    re.IGNORECASE)
_TALLY_Q = re.compile(
    r"\bhow\s+many\b|\bhow\s+much\b"
    r"|\bwhat(?:'s|s|\s+is|\s+was|\s+are)\s+(?:the\s+|our\s+)?"
    r"(?:current\s+|running\s+|final\s+|latest\s+)?"
    r"(?:(?!count\b|tally\b|total\b|number\b)\w+\s+){0,3}?"
    r"(?:count|tally|total|number|damage)\b"
    r"|\b(?:count|tally|total)\s*(?:so\s+far|check|update|please|pls|plz|"
    r"now|right\s+now|status|report)?\s*\?"
    r"|\bwhere\s+(?:are\s+we|is\s+the\s+(?:count|tally)|we\s+at|do\s+we\s+stand)\b"
    r"|\b(?:current|running|latest|final)\s+(?:count|tally|total)\b"
    r"|\b(?:give|read|tell)\s+(?:me|us)?\s*(?:back\s+)?the\s+(?:count|tally|total|"
    r"number)\b"
    r"|\b(?:count|tally)\s+(?:so\s+far|check|update|status|report|please|pls|plz)\b"
    r"|\bwhat\s+are\s+we\s+(?:at|up\s+to|on)\b|\bwhere\s+are\s+we\s+(?:at|up\s+to)\b",
    re.IGNORECASE)
#: 'how many?' / 'how many so far' with exactly one count running: the
#: count is the only thing that question can be about.
_TALLY_Q_BARE = re.compile(
    r"^\W*how\s+many\s*(?:so\s+far|now|total|have\s+we\s+(?:had|got|gotten|seen|"
    r"spotted|counted|logged)|are\s+we\s+(?:at|on|up\s+to)|is\s+(?:it|that)(?:\s+"
    r"(?:now|at))?|do\s+we\s+have|did\s+we\s+get)?\s*\??\W*$", re.IGNORECASE)
_TALLY_STOP = re.compile(
    r"\b(?:stop|quit|end|close|finish|drop|forget|kill|cancel|scrap|done)\s+"
    r"(?:with\s+)?(?:the\s+)?(?:counting|tallying|tracking|"
    r"count(?:ing)?\s+(?:of|on|for)|tally(?:ing)?\s+(?:of|on|for)|"
    r"keeping\s+(?:count|track|tally|score)\s+of|(?:count|tally|counter)\b)"
    r"|\bno\s+more\s+counting\b|\bwe(?:'re|re|\s+are)\s+done\s+(?:counting|"
    r"with\s+the\s+(?:count|tally))\b|\bclose\s+(?:out\s+)?the\s+(?:count|tally|books)\b",
    re.IGNORECASE)
_TALLY_RESET = re.compile(
    r"\b(?:reset|zero(?:\s+out)?|clear|restart|wipe|start\s+over(?:\s+on|\s+with)?)\b"
    r"|\bback\s+to\s+(?:zero|0)\b|\bfrom\s+(?:zero|0)\s+again\b",
    re.IGNORECASE)
_COUNT_WORD = re.compile(r"\b(?:count|counts|counter|tally|tallies|tracking|"
                         r"total|so\s+far)\b", re.IGNORECASE)
_FILLER = frozenset(("another", "one", "a", "an", "yes", "yep", "yup", "there",
                     "its", "it's", "thats", "that's", "that", "is", "was", "lol",
                     "haha", "omg", "again", "here", "hey", "oh", "ok", "okay",
                     "just", "we", "have", "got", "had", "the", "and", "boom",
                     "ding", "seen", "saw", "spotted", "alert", "incoming",
                     "confirmed", "official", "big", "fat", "classic", "proper"))


def _bare_label(low: str, key: str) -> bool:
    """True when the message is just the tally's name plus filler:
    'docbot dirty lepage!' is the streamer calling one out."""
    norm = _norm(low)
    rest = re.sub(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])", " ", norm)
    words = key.split()
    if len(words) >= 2 and rest == norm:
        rest = re.sub(r"(?<![a-z0-9])" + re.escape(words[-1]) + r"(?![a-z0-9])",
                      " ", norm)
    leftover = [w for w in rest.split() if not w.isdigit()]
    return all(w in _FILLER or _singular(w) in _FILLER for w in leftover)


def _amount(m, default: int = 1) -> int:
    for _name, value in (m.groupdict() or {}).items():
        if value:
            n = _num(value)
            if n:
                return max(1, min(100, n))
    return default


def _tally_control(t: str, low: str, tallies: dict) -> Control | None:
    m = _TALLY_START.search(t)
    if m:
        raw = next((g for g in m.groups() if g), "")
        label = tally_label(raw)
        if not label:
            return None
        return Control("tally_start", key=tally_key(label), label=label)
    if not tallies:
        return None
    key = tally_find(low, tallies)
    one = key or (next(iter(tallies)) if len(tallies) == 1 else None)
    counted = bool(_COUNT_WORD.search(low))
    named = bool(key)
    is_q = low.rstrip().endswith("?")
    if _TALLY_STOP.search(low) and (named or (one and counted)):
        return Control("tally_stop", key=one)
    if _TALLY_RESET.search(low) and (named or (one and counted)):
        return Control("tally_reset", key=one)
    sm = None if is_q else _SET.search(low)
    if sm and (named or (one and counted)):
        return Control("tally_set", key=one, n=max(0, min(100000, _amount(sm, 0))))
    if _TALLY_Q.search(low) and (named or counted):
        return Control("tally_query", key=key)
    if one and len(tallies) == 1 and _TALLY_Q_BARE.match(low):
        return Control("tally_query", key=one)
    if is_q:
        if named and _bare_label(low, key):
            return Control("tally_query", key=key)
        return None
    dm = _DEC.search(t)
    if dm and one and (named or len(low.split()) <= 6):
        return Control("tally_sub", key=one, n=_amount(dm))
    im = _INC.search(t)
    if im and one and (named or _INC_PLAIN.match(low)):
        return Control("tally_add", key=one, n=_amount(im))
    if named and _bare_label(low, key):
        lead = re.match(r"^\W*(\d+|two|three|four|five)\b", low)
        n = _num(lead.group(1)) if lead else 1
        return Control("tally_add", key=key, n=max(1, min(100, n or 1)))
    return None


# ---- the activity: the parsers --------------------------------------------
#: The games the bot can host: question-shaped ones. 'challenge',
#: 'contest' and 'bingo' are not here on purpose - 'lets do the ice
#: bucket challenge' is not a request for a quiz.
_GAME_WORD = re.compile(
    r"\b(?P<game>trivia(?:\s+game|\s+night|\s+round)?|quiz(?:\s+show|\s+game)?|"
    r"pop\s+quiz|game\s+show|guessing\s+game|word\s+game|riddle\s+game|"
    r"(?:test|dry|trial|practice)\s+run|round\s+of\s+(?:trivia|questions))\b",
    re.IGNORECASE)
#: The ask has to be an ask: an imperative or a 'lets' / 'can you' at
#: the start of a clause. 'you are the trivia host' and 'we played
#: trivia last night' name a game without asking for one.
_GAME_ASK = re.compile(
    r"(?:^|[,.!;:?]\s*|\b(?:please|pls|plz|ok|okay|so|alright|hey|yo|now|and|"
    r"then|should|shall|gonna|going\s+to|wanna|want\s+to|like\s+to|love\s+to|"
    r"time\s+to|ready\s+to|down\s+to|up\s+for)\s+)"
    r"(?:let'?s|lets|let\s+us|can\s+(?:you|u|we|ya)|could\s+(?:you|u|we)|"
    r"would\s+(?:you|u)|will\s+(?:you|u)|wanna|want\s+to|how\s+about|time\s+for|"
    r"shall\s+we|ready\s+for|you\s+(?:host|run|do)|host|run|start|kick\s+off|do|"
    r"play|try|have|set\s+up|put\s+on|give\s+us|hit\s+us|throw\s+(?:us|out)|"
    r"make\s+it|topic|quiz\s+us|test\s+us)\b"
    r"|\b(?:trivia|quiz|game)\s+time\b", re.IGNORECASE)
_GAME_NOT = re.compile(
    r"\b(?:do|did|does|have|has|are|were|was|is)\s+(?:you|u|he|she|they|doc|ya)\b"
    r"|\bwhat\s+game\b|\bwhich\s+game\b|\bplaying\s+(?:tonight|now|today)\b"
    r"|\bgame\s+(?:is|was)\s+(?:he|she|doc)\b", re.IGNORECASE)
_ROUNDS = re.compile(
    r"\b(?P<n1>\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|twelve)"
    r"\s*(?:-\s*)?(?:rounds?|questions?|qs|rnds?)\b"
    r"|\bmake\s+it\s+(?P<n2>\d{1,2}|one|two|three|four|five|six|seven|eight|"
    r"nine|ten)\b(?!\s*(?:rounds?|questions?|seconds?|secs?|s\b|mins?|minutes?|"
    r"points?|pts?))", re.IGNORECASE)
_TOPIC_END = (r"(?=\s+(?:and|with|for|then|make|lets|let's|please|pls|plz|"
              r"tonight|today|now|\d+\s+(?:rounds?|questions?))\b|[.,!?;]|$)")
_TOPIC_NAMED = re.compile(
    r"\b(?:topic|theme|subject|category)\s*(?:would\s+be|will\s+be|should\s+be|"
    r"could\s+be|can\s+be|is|:|=|of|-|being)?\s*[:\-]?\s*"
    r"(?P<t>[A-Za-z0-9][\w'&/ -]{1,40}?)" + _TOPIC_END, re.IGNORECASE)
_TOPIC_ABOUT = re.compile(
    r"\b(?:about|around|regarding|on\s+the\s+subject\s+of|"
    r"(?:quiz|trivia|questions?|game|round)\s+(?:(?:us|me|them|chat|everyone)"
    r"\s+)?on)\s+(?P<t>[A-Za-z0-9][\w'&/ -]{1,40}?)" + _TOPIC_END,
    re.IGNORECASE)
_TOPIC_STOP = (
    "a an the quick little test dry trial practice another some new fun pop "
    "short small of round rounds do run host play start us me good big are is "
    "you your our my his her their was were be been being am this that it its "
    "for with and lets let's to in on at time hey yo hi hello ok okay so "
    "alright right well now cool sweet great awesome nice please pls plz easy "
    "hard tough proper real actual full mini bit little quickfire rapid fire "
    "speed lightning tonight today next first second last one two three four "
    "five ten").split()
_TOPIC_BEFORE = re.compile(
    r"\b(?P<t>(?!(?:" + "|".join(re.escape(w) for w in _TOPIC_STOP) + r")\b)"
    r"[A-Za-z][\w'&/-]*(?:\s+(?!(?:trivia|quiz)\b)[A-Za-z][\w'&/-]*)?)"
    r"\s+(?:trivia|quiz)\b", re.IGNORECASE)
_TOPIC_TAIL = re.compile(
    r"\s+(?:trivia|quiz|game|questions?|rounds?|round|please|pls|plz|tonight|"
    r"today|for\s+(?:us|me|chat|the\s+room)|with\s+.*|and\s+.*|then\s+.*|"
    r"lets\s+.*|let's\s+.*|make\s+.*|\d+\s+rounds?.*)$", re.IGNORECASE)
_GO_NOW = re.compile(
    r"\b(?:start\s+now|go\s+ahead|first\s+question|kick\s+(?:it\s+)?off|"
    r"begin\s+now|right\s+now|fire\s+away|start\s+with\s+(?:round|question)\s*"
    r"(?:1|one)|straight\s+in|ask\s+away|hit\s+(?:us|me))\b", re.IGNORECASE)

_ROUND_N = re.compile(
    r"\b(?:round|question|q|rnd)\s*(?:#|number|no\.?)?\s*"
    r"(?P<n>\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\b",
    re.IGNORECASE)
_ROUND_N_GO = re.compile(
    r"\b(?:round|question|q|rnd)\s*(?:#|number|no\.?)?\s*"
    r"(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s*[!:,.-]*\s*"
    r"(?:go|going|now|please|pls|plz|time|start|begin|whenever|when\s+"
    r"(?:you're|your|ur)\s+ready|lets\s+go|let'?s\s+go|it\s+is|is\s+go)\b",
    re.IGNORECASE)
#: 'start round 2', 'lets get Round 2 going', 'hit us with question 3',
#: 'on to round 3': a calling verb within two words of the round.
_ROUND_CALL = re.compile(
    r"\b(?:start|begin|kick\s+(?:it\s+|us\s+)?off|fire\s+(?:off|away)|run|do|"
    r"go\s+(?:with|for|to)|get|hit\s+(?:us|me|em|them)(?:\s+with)?|"
    r"give\s+(?:us|me|em|them)|ask|post|drop|throw\s+(?:out|us|me)|serve\s+up|"
    r"bring\s+on|on\s+to|onto|time\s+for|ready\s+for|open|launch|roll|play|"
    r"send|move\s+(?:on\s+)?to|up\s+next|next|continue|carry\s+on|"
    r"keep\s+(?:it\s+)?going|(?:lets|let'?s)\s+(?:get|do|have|see|go(?:\s+with|"
    r"\s+to|\s+for)?|start|begin|run|play|open|roll|try|hit|move\s+(?:on\s+)?to))"
    r"\s+(?:\w+\s+){0,2}?(?:round|question|q|rnd)\s*(?:#|number|no\.?)?\s*"
    r"(?P<n>\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\b",
    re.IGNORECASE)
#: 'round 2 was hard', 'question 3 is up on screen' - talk ABOUT a
#: round, not a call for it.
_ROUND_TALK = re.compile(
    r"\b(?:round|question|q|rnd)\s*(?:#|number|no\.?)?\s*(?:\d{1,2}|one|two|"
    r"three|four|five|six|seven|eight|nine|ten)\s+(?:was|were|is|has|had|went|"
    r"got|seemed|felt|looked|sounded|took|should|would|could|will|might|"
    r"answer|winner)\b", re.IGNORECASE)
_ROUND_ORD = re.compile(
    r"\b(?P<ord>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|"
    r"tenth|\d{1,2}(?:st|nd|rd|th)|next|last|final|another|opening|closing)\s+"
    r"(?:round|question|q|rnd|one)\b", re.IGNORECASE)
_NEXT = re.compile(
    r"^\W*(?:next|next\s+one|next\s+up|again|go\s+again|another|one\s+more|"
    r"hit\s+me|hit\s+us|keep\s+(?:it\s+|em\s+|them\s+)?(?:going|coming)|"
    r"carry\s+on|continue|go|go\s+ahead|go\s+go\s+go|lets\s+go|let'?s\s+go|"
    r"start|begin|kick\s+(?:it\s+)?off|fire\s+away|ready|we(?:'re|re|\s+are)\s+"
    r"ready|whenever\s+(?:you're|your|ur)\s+ready|ask\s+away|proceed)\W*$",
    re.IGNORECASE)
_START_GAME = re.compile(
    r"\b(?:start|begin|kick\s+off|open|launch)\s+(?:the\s+|this\s+|our\s+)?"
    r"(?:game|quiz|trivia|test\s+run|show|thing)\b|\bkick\s+(?:it|us)\s+off\b",
    re.IGNORECASE)
_INTERROG = re.compile(
    r"^(?:what|who|which|when|where|why|how|was|did|is|are|does|do|can\s+"
    r"(?:i|we)|could\s+(?:i|we))\b", re.IGNORECASE)
_REVEAL = re.compile(
    r"\bwhat(?:'s|s|\s+is|\s+was)\s+(?:the\s+)?(?:right\s+|correct\s+)?answer\b"
    r"|\bthe\s+answer\s*(?:\?|please|pls|plz|is\s*\?|was\s*\?|$)"
    r"|^\W*answer\W*$|\banswers?\s+(?:please|pls|plz|now|time)\b"
    r"|\breveal\b|\btime(?:'s|s|\s+is)\s+up\b|\btimes\s+up\b|^\W*time\W*$"
    r"|\bpencils\s+down\b|\bwho\s+(?:got\s+(?:it|that|this)(?:\s+right)?|had\s+it|"
    r"was\s+right|is\s+right|nailed\s+it|wins?\s+(?:that|the\s+round|round\s+\d+|"
    r"it))\b|\bdid\s+(?:anyone|anybody|someone)\s+get\s+it\b|\bcorrect\s+answer\b"
    r"|\b(?:tell|give)\s+(?:us|them|me|chat)\s+the\s+answer\b"
    r"|\bcall\s+(?:it|the\s+round|round\s+\d+)\b"
    r"|\b(?:close|end)\s+(?:out\s+)?(?:the\s+|this\s+)?round\b|\bround\s+(?:is\s+)?over\b"
    r"|\bstop\s+the\s+clock\b|\bclock(?:'s|s|\s+is)\s+(?:up|done|out)\b",
    re.IGNORECASE)
_SCORE = re.compile(
    r"\bwho(?:'s|s|\s+is)\s+(?:winning|ahead|in\s+the\s+lead|leading|on\s+top)\b"
    r"|\bscores?\b|\bstandings\b|\bleaderboard\b|\bwho\s+won\b|\bwinner\b"
    r"|\bpoints\s+(?:so\s+far|table|tally)\b|\bhow\s+is\s+everyone\s+doing\b",
    re.IGNORECASE)
_STATUS = re.compile(
    r"\bwhat\s+round\b|\bwhich\s+round\b|\bhow\s+many\s+rounds\b"
    r"|\brounds?\s+(?:left|to\s+go|remaining)\b|\bwhat\s+are\s+we\s+(?:doing|"
    r"playing|on)\b|\bwhat(?:'s|s|\s+is)\s+the\s+game\b|\bwhat\s+game\s+(?:is\s+"
    r"this|are\s+we)\b|\bwhere\s+are\s+we\s+(?:at|up\s+to)\b|^\W*status\W*$"
    r"|\bwhat\s+was\s+the\s+question\b|\brepeat\s+(?:the\s+)?(?:question|that|q)\b"
    r"|\bsay\s+(?:the\s+question\s+)?again\b|\bwhat(?:'s|s|\s+is)\s+the\s+question\b"
    r"|\bread\s+(?:the\s+)?question\s+(?:again|back)\b",
    re.IGNORECASE)
_END = re.compile(
    r"\bgame\s+over\b|\bthat(?:'s|s|\s+is)\s+(?:the\s+)?(?:game|quiz|trivia|"
    r"it\s+for\s+(?:the\s+)?(?:game|trivia|quiz|test|test\s+run))\b"
    r"|\b(?:end|stop|cancel|kill|forget|close|scrap|finish)\s+(?:of\s+)?(?:the\s+|"
    r"this\s+|our\s+)?(?:game|quiz|trivia|test\s+run|test|show)\b"
    r"|\bstop\s+hosting\b|\bno\s+more\s+(?:rounds|questions)\b|\bthat\s+was\s+the\s+"
    r"last\s+round\b|\bwrap\s+(?:up\s+)?the\s+(?:game|quiz|trivia)\b|\bcall\s+it\s+"
    r"a\s+game\b|\bwe(?:'re|re|\s+are)\s+done\s+(?:with\s+)?(?:the\s+)?(?:game|quiz|"
    r"trivia|playing)\b|\bgame(?:'s|s|\s+is)\s+(?:over|done|finished)\b",
    re.IGNORECASE)
_TIME = re.compile(
    r"\b(?P<n>\d{1,3}|a|one|half\s+a|thirty|sixty|ninety|fifteen|twenty|"
    r"forty[\s-]?five)\s*(?P<u>s\b|secs?\b|seconds?\b|m\b|mins?\b|minutes?\b)",
    re.IGNORECASE)
#: A time is a cadence only in a timing sentence - '80s music' is a
#: decade, '30s to answer' is a clock.
_TIME_CTX = re.compile(
    r"\b(?:to\s+(?:answer|guess|respond|reply|think)|answer|guess|then|after|"
    r"between|wait|give|timer|each|per|every|later|before|countdown|clock|"
    r"window|apart|gap|delay|pause|in\s+(?=\d|a\s|an\s|half|thirty|sixty|"
    r"fifteen|twenty)|allow|limit)\b", re.IGNORECASE)
_AUTO = re.compile(
    r"\b(?:then|after\s+that|after\s+which|and\s+then|next\s+round\s+after|"
    r"auto(?:matically)?|on\s+your\s+own|by\s+yourself|keep\s+(?:it\s+)?going|"
    r"run\s+it|every\s+\d+|each\s+round|between\s+rounds|and\s+so\s+on|"
    r"repeat|same\s+(?:for|with)\s+(?:the\s+)?(?:rest|others)|per\s+round)\b",
    re.IGNORECASE)


def cadence(text: str):
    """(answer_seconds, gap_seconds_or_None) from 'give 30 secs to answer
    then start round 3 30 secs after that', or None when no timing was
    asked for. One time alone runs the reveal only; a second time - or
    'then' / 'after that' / 'keep going' - runs the next round too."""
    text = text or ""
    if not _TIME_CTX.search(text):
        return None
    times = []
    for m in _TIME.finditer(text):
        unit = m.group("u").lower()
        if unit == "s" or unit == "m":
            # '30s' / '2m' only inside a timing phrase: '80s music' is not.
            window = text[max(0, m.start() - 24):m.end() + 28]
            if not _TIME_CTX.search(window):
                continue
        n = m.group("n").lower()
        secs = 30.0 if n.startswith("half") else float(_num(n) or 0)
        if unit.startswith("m"):
            secs *= 60
        if secs > 0:
            times.append(max(MIN_CADENCE, min(MAX_CADENCE, secs)))
    if not times:
        return None
    answer = times[0]
    gap = times[1] if len(times) > 1 else (answer if _AUTO.search(text) else None)
    return (answer, gap)


def _topic(t: str) -> str | None:
    for rx in (_TOPIC_NAMED, _TOPIC_ABOUT, _TOPIC_BEFORE):
        m = rx.search(t)
        if not m:
            continue
        raw = _TOPIC_TAIL.sub("", " ".join(m.group("t").split())).strip(" \"'-:")
        raw = re.sub(r"^(?:the|some|a|an)\s+", "", raw, flags=re.IGNORECASE)
        if not 2 <= len(raw) <= 40 or raw.lower() in ("it", "that", "this",
                                                        "them", "us"):
            continue
        if _num(raw) is not None:
            continue
        return raw
    return None


def _rounds(low: str) -> int | None:
    m = _ROUNDS.search(low)
    if not m:
        return None
    n = _num(m.group("n1") or m.group("n2"))
    return n if n and 1 <= n <= MAX_ROUNDS else None


def _describe(game: str | None, topic: str | None, rounds: int | None) -> str:
    g = re.sub(r"\s+", " ", (game or "quiz").lower())
    if g in ("test run", "dry run", "trial run", "practice run"):
        base = ((topic + " quiz") if topic else "quiz") + " (a %s)" % g
    elif g.startswith("trivia"):
        base = (topic + " trivia game") if topic else "trivia game"
    elif g.startswith("quiz") or g == "pop quiz":
        base = (topic + " quiz") if topic else "quiz"
    elif g.startswith("round of"):
        base = (topic + " trivia") if topic else "round of trivia"
    else:
        base = (topic + " " + g) if topic else g
    return ("a %d-round " % rounds if rounds else "a ") + base


def _start_control(t: str, low: str) -> Control | None:
    g = _GAME_WORD.search(t)
    rounds = _rounds(low)
    topic = _topic(t)
    if not g and not (rounds and topic):
        return None
    if not _GAME_ASK.search(low):
        return None
    if _GAME_NOT.search(low) and low.rstrip().endswith("?"):
        return None
    game = g.group("game") if g else None
    if game and re.search(r"\brun$", game.lower()) and not (rounds or topic):
        # 'lets do a test run' alone is testing the bot, not a quiz.
        return None
    if game and re.match(r"trivia|quiz|contest|competition|challenge|tournament",
                         game, re.IGNORECASE) and low.rstrip().endswith("?") \
            and not rounds and not topic and not re.match(
                r"^\W*(?:can|could|would|will|shall)\b", low):
        # 'is trivia tonight?' asks about a game; it does not start one.
        return None
    go = bool(_GO_NOW.search(low)) or bool(_ROUND_ORD.search(t)) \
        or bool(_ROUND_N.search(t))
    return Control("start", topic=topic, rounds=rounds,
                   what=_describe(game, topic, rounds), go=go,
                   cadence=cadence(t))


def _round_control(t: str, low: str, activity: dict,
                   strict: bool = False) -> Control | None:
    rounds = activity.get("rounds")
    cur = int(activity.get("round") or 0)
    pending = activity.get("pending")
    is_q = low.rstrip().endswith("?")
    interrog = bool(_INTERROG.match(low))
    cad = cadence(t)
    if not (is_q and interrog):
        m = _ROUND_CALL.search(t)
        if m:
            n = _num(m.group("n"))
            if n and 1 <= n <= MAX_ROUNDS:
                return Control("round", n=n, cadence=cad)
        m = _ROUND_N.search(t)
        if m and not _ROUND_TALK.search(t):
            n = _num(m.group("n"))
            if n and 1 <= n <= MAX_ROUNDS:
                if _ROUND_N_GO.search(low):
                    return Control("round", n=n, cadence=cad)
                # 'round 3' alone, or 'round 3, 45 secs to answer': the
                # round must be the next one (or the one still due) -
                # 'round 2 was hard, give them 30 secs' is not a call.
                if (cad or len(low.split()) <= 3) and (
                        n == pending or n > cur):
                    return Control("round", n=n, cadence=cad)
    m = _ROUND_ORD.search(t)
    if m and not (is_q and interrog):
        w = m.group("ord").lower()
        if w in ("next", "another"):
            n = pending or cur + 1
        elif w in ("last", "final", "closing"):
            n = rounds if rounds and rounds > cur else cur + 1
        else:
            n = _num(w)
        if n and 1 <= n <= MAX_ROUNDS:
            return Control("round", n=n, cadence=cad)
    if not strict and (_NEXT.match(low) or _START_GAME.search(low)):
        n = pending or (cur + 1)
        if n <= MAX_ROUNDS:
            return Control("round", n=n, cadence=cad)
    return None


def _activity_control(t: str, low: str, activity: dict | None,
                      strict: bool = False) -> Control | None:
    if activity:
        if _END.search(low):
            return Control("end")
        if _STATUS.search(low):
            return Control("status", repeat=bool(re.search(
                r"question|repeat|again|read", low)))
        if activity.get("open") and not activity.get("done") \
                and _REVEAL.search(low):
            return Control("reveal")
        r = _round_control(t, low, activity, strict)
        if r:
            return r
        if strict:
            return None
        if _SCORE.search(low):
            return Control("score")
        if _REVEAL.search(low) and activity.get("round"):
            # No round open: begin() answers with where things stand.
            return Control("reveal")
        # A fresh game while one is running replaces it (mods only, and
        # 'lets do round 2' was caught above - this is 'lets do a movie
        # quiz next, 5 rounds').
    return _start_control(t, low)


def control(text: str, state: dict, names=(), strict: bool = False) -> Control | None:
    """What this line asks of the activity or a tally, or None when it
    is ordinary chat. `state` is Ongoing.snapshot(); `names` are the
    bot's names, removed wherever they sit in the line. `strict` is for
    a line NOT addressed to the bot (the host talking to the room while
    their game runs): only a round named outright ('round 3', 'next
    question'), an answer call or a game-over counts - a bare 'lets go'
    or 'next' is hype, not a control. Pure."""
    t = _strip_names(text, names)
    if not t:
        return None
    low = t.lower()
    state = state or {}
    ctl = _tally_control(t, low, state.get("tallies") or {})
    if ctl:
        return ctl
    return _activity_control(t, low, state.get("activity"), strict)


def looks_like_question(line: str) -> bool:
    """A trivia question has a question mark or an asking shape ('Name
    the rider who...'). 'Round 2 is right here' has neither."""
    l = (line or "").strip()
    return "?" in l or bool(re.match(
        r"^(?:name|which|what|who|whom|whose|when|where|why|how|guess|"
        r"true\s+or\s+false|identify|spell|fill\s+in|finish|complete|in\s+what|"
        r"in\s+which|by\s+what|riddle\s+me)\b", l, re.IGNORECASE))


def step_failure_line(step: Step) -> str:
    """The honest line when the model could not produce a step: it says
    what was due so the host can call it again - never a fake question,
    never silence."""
    if step.kind == "ask":
        return ("my round %d question got stuck in the gears - say 'round %d' "
                "again and I'll ask it." % (step.round, step.round))
    if step.kind == "reveal":
        return ("the answer to round %d got stuck in the gears - ask me for "
                "it once more." % step.round)
    if step.kind == "score":
        return "I wasn't keeping score - the chat log has the answers."
    return "I lost my place for a second - tell me again what you need."


# ---- the state -------------------------------------------------------------
class Ongoing:
    """What the bot is doing right now, saved on every change."""

    def __init__(self, path: str = STATE_PATH, activity_minutes: float = 45.0,
                 log=None):
        self.path = path
        try:
            self.activity_minutes = max(0.0, float(activity_minutes))
        except (TypeError, ValueError):
            self.activity_minutes = 45.0
        self._lock = threading.RLock()
        self._log = log or (lambda *a, **k: None)
        data = storage.load_json(path, {}) if path else {}
        if not isinstance(data, dict):
            data = {}
        act = data.get("activity")
        self.activity = act if isinstance(act, dict) and act.get("what") else None
        self.tallies = {}
        tallies = data.get("tallies")
        if isinstance(tallies, dict):
            for k, v in tallies.items():
                if isinstance(v, dict) and isinstance(v.get("count"), int) \
                        and v.get("label"):
                    self.tallies[str(k)] = v
        # Bumps whenever the game changes hands (start, finish, end,
        # lapse): a timer scheduled for the old game checks it and stands
        # down instead of asking round 3 of a game that is over.
        self.generation = 0
        self.saved = True

    # -- persistence
    def _save(self) -> None:
        if not self.path:
            return
        ok = storage.save_json(self.path, {"activity": self.activity,
                                           "tallies": self.tallies})
        if not ok and self.saved:
            self._log("ongoing state could not be saved - kept in memory only")
        self.saved = ok

    # -- the activity
    def _lapse(self, now: float) -> None:
        a = self.activity
        if not a:
            return
        last = float(a.get("updated") or a.get("since") or now)
        if a.get("done"):
            limit = DONE_MINUTES * 60
        elif self.activity_minutes > 0:
            limit = self.activity_minutes * 60
        else:
            return
        if now - last > limit:
            self._log("activity lapsed after %g min without a step: %s"
                      % (limit / 60, a.get("what")))
            self.activity = None
            self.generation += 1
            self._save()

    def snapshot(self, now: float = None) -> dict:
        now = time.time() if now is None else now
        with self._lock:
            self._lapse(now)
            return {"activity": dict(self.activity) if self.activity else None,
                    "tallies": {k: dict(v) for k, v in self.tallies.items()}}

    def current(self, now: float = None) -> dict | None:
        return self.snapshot(now)["activity"]

    def host(self) -> str:
        with self._lock:
            return ((self.activity or {}).get("asked_by") or "")

    def open_round(self, now: float = None, window: float = 300.0) -> int:
        """The round whose answer is still owed, else 0. While one is
        open the fact engine must not be a back door to the answer -
        for `window` seconds after the question, not for the 45 minutes
        a forgotten game takes to lapse."""
        now = time.time() if now is None else now
        with self._lock:
            a = self.activity
            if not a or a.get("done") or not a.get("open"):
                return 0
            if now - float(a.get("asked_at") or 0) > window:
                return 0
            return int(a["open"])

    def start(self, ctl: Control, nick: str, now: float) -> None:
        with self._lock:
            self.activity = {
                "what": ctl.what or "a game", "topic": ctl.topic,
                "rounds": ctl.rounds, "round": 0, "pending": None,
                "open": None, "question": None, "asked_by": nick,
                "since": now, "updated": now, "log": [], "done": False,
                "cadence": list(ctl.cadence) if ctl.cadence else None,
            }
            self.generation += 1
            self._save()

    def begin(self, ctl: Control, nick: str, now: float) -> Step | None:
        """Apply a step control and say what must happen. None when
        nothing is running (the game lapsed or ended between the ask
        and the worker's turn) or when an auto step is stale."""
        with self._lock:
            if ctl.kind == "start":
                self.start(ctl, nick, now)
                if not ctl.go:
                    return Step("intro", self._intro_line(self.activity), 0,
                                False, self.generation)
                ctl = Control("round", n=1, cadence=ctl.cadence)
            self._lapse(now)
            a = self.activity
            if not a:
                return None
            if ctl.auto:
                # Scheduled by the cadence for a state that may have moved
                # on: the host revealed early, or asked round 3 by hand.
                if ctl.kind == "reveal" and a.get("open") != ctl.after:
                    return None
                if ctl.kind == "round" and (
                        a.get("open") or a.get("pending") or a.get("done")
                        or int(a.get("round") or 0) != ctl.after):
                    return None
            a["updated"] = now
            if ctl.cadence:
                a["cadence"] = [ctl.cadence[0], ctl.cadence[1]]
            rounds = a.get("rounds")
            if ctl.kind == "round":
                n = int(ctl.n or 1)
                if a.get("done"):
                    a["done"] = False
                    if rounds and n <= rounds:
                        n = rounds + 1
                if rounds and n > rounds:
                    a["rounds"] = rounds = n
                    a["what"] = re.sub(r"^a \d+-round ", "a %d-round " % n,
                                       a["what"])
                unanswered = a.get("open") if a.get("open") not in (None, n) else None
                a["round"] = n
                a["pending"] = n
                self._save()
                return Step("ask", self._ask_cue(a, n, unanswered), n,
                            bool(rounds and n >= rounds), self.generation)
            if ctl.kind == "reveal":
                if a.get("done"):
                    self._save()
                    return Step("nudge", (
                        "that game's finished - all %d rounds answered. Say "
                        "'another round' if you want one more."
                        % int(a.get("round") or 0)), 0, False, self.generation)
                if not a.get("open"):
                    n = int(a.get("pending") or a.get("round") or 0)
                    self._save()
                    if n:
                        return Step("nudge", (
                            "round %d's question never went out - say 'round "
                            "%d' and I'll ask it." % (n, n)), n, False,
                            self.generation)
                    return Step("nudge", "no round is open yet - say 'round 1' "
                                "and I'll ask the first question.", 0, False,
                                self.generation)
                n = int(a["open"])
                last = bool(rounds and n >= rounds)
                self._save()
                return Step("reveal", self._reveal_cue(a, n, last), n, last,
                            self.generation)
            if ctl.kind == "score":
                self._save()
                return Step("score", (
                    "They want the standings. From the recent chat below "
                    "ONLY, say who has answered correctly so far. If you "
                    "cannot tell from the chat, say you were not keeping "
                    "score - never invent names or numbers."),
                    int(a.get("round") or 0), False, self.generation)
            if ctl.kind == "end":
                n = int(a.get("round") or 0)
                line = ("that's the game - %s, %s. Thanks for playing."
                        % (a["what"], ("%d round%s done" % (n, "" if n == 1
                                                             else "s"))
                           if n else "called before round 1"))
                return Step("end", line, n, True, self.generation)
            return None

    @staticmethod
    def _intro_line(a: dict) -> str:
        return ("on it - %s%s. Say 'round 1' (or 'go') and I'll ask the first "
                "question." % (a["what"], (", %d rounds" % a["rounds"])
                               if a.get("rounds") and "-round" not in a["what"]
                               else ""))

    def _ask_cue(self, a: dict, n: int, unanswered) -> str:
        rounds = a.get("rounds")
        topic = a.get("topic") or "general trivia"
        out = ("Round %d%s: your line IS the round-%d question - ONE real %s "
               "question with a definite, checkable answer, in one line, "
               "ending with a question mark. Nothing else: no 'here comes "
               "round %d', no answer, no hints."
               % (n, (" of %d" % rounds) if rounds else "", n, topic, n))
        if unanswered:
            out += (" Round %d's answer was never given - give it in a few "
                    "words first, then ask round %d." % (unanswered, n))
        asked = self._questions(a)
        if asked:
            out += (" You already asked: " + " | ".join(asked[-4:])
                    + " - ask something different.")
        return out

    def _reveal_cue(self, a: dict, n: int, last: bool) -> str:
        q = a.get("question")
        out = ("Time is up on round %d. Your line: the correct answer to the "
               "question you asked%s, stated plainly, then who in the recent "
               "chat below got it right - only names that actually appear "
               "there, or 'nobody'. If you are not certain of the answer, say "
               "so rather than guess."
               % (n, (" (%s)" % q) if q else ""))
        if last:
            out += (" This was the last round - close the game out in the "
                    "same line: a winner if the chat shows one, thanks to "
                    "the players.")
        elif a.get("cadence") and (a["cadence"] or [None, None])[1]:
            out += " The next round follows on its own - do not ask it now."
        else:
            out += " Do not ask the next question yet."
        return out

    @staticmethod
    def _questions(a: dict) -> list:
        out = []
        for entry in a.get("log") or []:
            m = re.match(r"Round (\d+) question: (.*)$", entry)
            if m:
                out.append(m.group(2))
        return out

    def record(self, step: Step, line: str, now: float) -> None:
        """The step's line posted: book-keep it."""
        with self._lock:
            a = self.activity
            if not a or step.generation != self.generation:
                return
            a["updated"] = now
            log = list(a.get("log") or [])
            if step.kind == "ask":
                a["open"] = step.round
                a["pending"] = None
                a["question"] = line
                a["asked_at"] = now
                log.append("Round %d question: %s" % (step.round, line))
            elif step.kind == "reveal":
                a["open"] = None
                log.append("Round %d answer: %s" % (step.round, line))
            a["log"] = log[-MAX_LOG:]
            if step.kind == "end":
                self._finish("ended")
                return
            if step.kind == "reveal" and step.last:
                # Finished, not gone: 'another round?' can extend it for
                # a while, and the prompt still knows what just happened.
                a["done"] = True
                a["pending"] = None
                self.generation += 1
                self._log("activity finished: %s (%d rounds)"
                          % (a.get("what"), int(a.get("round") or 0)))
            self._save()

    def failed(self, step: Step, now: float) -> None:
        """The model produced nothing usable for the step. The round stays
        DUE (a pending question) or OPEN (an answer still owed), so the
        prompt keeps saying so and the host can call it again."""
        with self._lock:
            a = self.activity
            if not a or step.generation != self.generation:
                return
            a["updated"] = now
            self._save()

    def _finish(self, how: str) -> None:
        a = self.activity
        if a:
            self._log("activity %s: %s (%d round(s))"
                      % (how, a.get("what"), int(a.get("round") or 0)))
        self.activity = None
        self.generation += 1
        self._save()

    def end(self) -> bool:
        with self._lock:
            had = self.activity is not None
            self._finish("cleared")
            return had

    def drop_tally(self, key: str) -> bool:
        with self._lock:
            if key in self.tallies:
                del self.tallies[key]
                self._save()
                return True
            return False

    def clear_all(self) -> None:
        with self._lock:
            self.activity = None
            self.tallies = {}
            self.generation += 1
            self._save()

    def next_auto(self, step: Step):
        """(seconds, Control) for the step the cadence schedules after
        this one, or None. One at a time, on purpose: a dead model
        cannot fire a cascade, and every auto step re-checks the game
        when it runs."""
        with self._lock:
            a = self.activity
            if not a or step.generation != self.generation:
                return None
            cad = a.get("cadence") or None
            if not cad:
                return None
            answer, gap = (list(cad) + [None, None])[:2]
            if step.kind == "ask" and answer:
                return (float(answer),
                        Control("reveal", after=step.round, auto=True))
            if step.kind == "reveal" and gap and not step.last:
                return (float(gap),
                        Control("round", n=step.round + 1, after=step.round,
                                auto=True))
            return None

    def repeats(self, line: str) -> bool:
        """True when a new question is an earlier question again."""
        with self._lock:
            a = self.activity
            if not a:
                return False
            words = _words(line)
            if not words:
                return False
            for q in self._questions(a):
                old = _words(q)
                if old and len(words & old) / len(words | old) >= 0.6:
                    return True
            return False

    def describe(self, now: float = None) -> str:
        """One plain sentence for the log and the admin panel."""
        a = self.current(now)
        if not a:
            return ""
        return "%s for %s - %s" % (a["what"], a["asked_by"], self._round_status(a))

    @staticmethod
    def _round_status(a: dict) -> str:
        rounds = a.get("rounds")
        of = (" of %d" % rounds) if rounds else ""
        if a.get("done"):
            return ("finished - all %d rounds asked and answered"
                    % int(a.get("round") or 0))
        if a.get("pending"):
            return ("round %d%s is DUE - its question has not been asked yet"
                    % (a["pending"], of))
        if a.get("open"):
            return ("round %d%s is OPEN - you asked: \"%s\"; answers are coming in"
                    % (a["open"], of, a.get("question") or "?"))
        r = int(a.get("round") or 0)
        if r == 0:
            return "not started - round 1 is asked when they say go"
        return ("round %d%s has been answered; round %d comes when they say go"
                % (r, of, r + 1))

    def status_line(self, ctl: Control, now: float = None) -> str:
        """The deterministic answer to 'what round is it' / 'repeat the
        question' / 'what are we doing'."""
        now = time.time() if now is None else now
        a = self.current(now)
        if not a:
            tallies = self._recent_tallies(now)
            if tallies:
                return "No game running. Counting: " + "; ".join(
                    "%s: %d" % (v["label"], v["count"]) for _, v in tallies) + "."
            return NOTHING_RUNNING_LINE
        if ctl.repeat and a.get("open") and a.get("question"):
            return "Round %d: %s" % (a["open"], a["question"])
        status = self._round_status(a).replace("you asked", "I asked")
        return "%s for %s: %s." % (a["what"][0].upper() + a["what"][1:],
                                   a["asked_by"], status)

    # -- tallies
    def _recent_tallies(self, now: float) -> list:
        out = [(k, v) for k, v in self.tallies.items()
               if now - float(v.get("updated") or 0) < TALLY_PROMPT_HOURS * 3600]
        out.sort(key=lambda kv: -float(kv[1].get("updated") or 0))
        return out

    def tally_owner(self, key: str) -> str:
        with self._lock:
            return ((self.tallies.get(key) or {}).get("by") or "")

    def tally_state(self, key: str) -> str:
        with self._lock:
            v = self.tallies.get(key)
            return ("%s is at %d" % (v["label"], v["count"])) if v else "that count"

    def apply_tally(self, ctl: Control, nick: str, now: float) -> str:
        """Do the arithmetic and say what happened. No model anywhere."""
        with self._lock:
            k = ctl.key
            v = self.tallies.get(k) if k else None
            if ctl.kind == "tally_start":
                if v:
                    return "already counting %s - it's at %d." % (v["label"], v["count"])
                if len(self.tallies) >= MAX_TALLIES:
                    oldest = min(self.tallies,
                                 key=lambda x: self.tallies[x].get("updated", 0))
                    del self.tallies[oldest]
                self.tallies[k] = {"label": ctl.label, "count": 0, "by": nick,
                                   "since": now, "updated": now, "last": None}
                self._save()
                return ("counting %s from now - it's at 0. Tell me when you spot "
                        "one, and ask me for the count any time." % ctl.label)
            if ctl.kind == "tally_query":
                if v:
                    return self._tally_report(v, now)
                recent = self._recent_tallies(now) or list(self.tallies.items())
                if not recent:
                    return "I'm not keeping count of anything right now."
                return "; ".join("%s: %d" % (t["label"], t["count"])
                                 for _, t in recent) + " so far."
            if not v:
                return "I'm not keeping count of that."
            if ctl.kind == "tally_add":
                v["count"] += int(ctl.n or 1)
                v["updated"] = v["last"] = now
                self._save()
                return "%s: %d." % (v["label"], v["count"])
            if ctl.kind == "tally_sub":
                v["count"] = max(0, v["count"] - int(ctl.n or 1))
                v["updated"] = now
                self._save()
                return "%s: %d." % (v["label"], v["count"])
            if ctl.kind == "tally_set":
                v["count"] = int(ctl.n or 0)
                v["updated"] = now
                self._save()
                return "%s set to %d." % (v["label"], v["count"])
            if ctl.kind == "tally_reset":
                v["count"] = 0
                v["updated"] = now
                v["last"] = None
                self._save()
                return "%s back to 0." % v["label"]
            if ctl.kind == "tally_stop":
                del self.tallies[k]
                self._save()
                return "done counting %s - final count %d." % (v["label"], v["count"])
            return "I'm not keeping count of that."

    @staticmethod
    def _tally_report(v: dict, now: float) -> str:
        since = _ago(now - float(v.get("since") or now))
        if v["count"] == 0:
            return "%s: 0 so far (counting since %s, none yet)." % (v["label"], since)
        last = _ago(now - float(v.get("last") or v.get("updated") or now))
        return "%s: %d so far (counting since %s, last one %s)." % (
            v["label"], v["count"], since, last)

    # -- what the model sees
    def prompt_lines(self, now: float = None, local: bool = False) -> list:
        """The block for chatai.user_prompt: [] when nothing is going on."""
        now = time.time() if now is None else now
        with self._lock:
            self._lapse(now)
            out = []
            a = self.activity
            if a:
                if a.get("done"):
                    out.append("- You hosted %s for %s - it is finished (%d rounds "
                               "asked and answered). No new questions unless "
                               "they ask for another round."
                               % (a["what"], a["asked_by"],
                                  int(a.get("round") or 0)))
                else:
                    out.append("- You are hosting %s for %s: %s."
                               % (a["what"], a["asked_by"], self._round_status(a)))
                for entry in (a.get("log") or [])[-(1 if local else 4):]:
                    out.append("  earlier - " + entry)
            tallies = self._recent_tallies(now)
            if tallies:
                out.append("- Counts you are keeping (exact - quote them, never "
                           "adjust them): " + "; ".join(
                               "%s: %d" % (v["label"], v["count"])
                               for _, v in tallies[:6]))
            return out

    def summary(self, now: float = None) -> dict:
        """For the admin panel."""
        now = time.time() if now is None else now
        with self._lock:
            self._lapse(now)
            a = self.activity
            return {
                "activity": ({"what": a["what"], "asked_by": a["asked_by"],
                              "status": self._round_status(a),
                              "since": float(a.get("since") or now),
                              "cadence": a.get("cadence")} if a else None),
                "tallies": [{"key": k, "label": v["label"], "count": v["count"],
                             "by": v.get("by", ""),
                             "updated": float(v.get("updated") or 0)}
                            for k, v in sorted(
                                self.tallies.items(),
                                key=lambda kv: -float(kv[1].get("updated") or 0))],
            }
