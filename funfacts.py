"""Location fun-fact lookup for the Twitch bot.

Sources:

  1. spicy_facts.json — a small curated database of verified, adult-rated
     ("spicy") facts for famous trucking spots. Used only when spice="spicy".
  2. Wikipedia — full-text search, then the article lead + History, ranked by
     how "interesting" (and, in spicy mode, how "adult") each sentence is.
  3. DuckDuckGo — Instant Answer API abstract, used as a fallback.
  4. Optional LLM (Groq / OpenRouter / local Ollama — any OpenAI-compatible
     API) — in spicy mode it
     rewrites the real facts into adult-humored fun facts. This is the
     recommended way to get genuinely spicy, unique facts for any town.

Multiple facts are kept per location: repeated `!funfact` calls for the same
place rotate through them, so you don't get the same answer twice in a row.

    python3 funfacts.py "Milford, PA"            # clean mode
    python3 funfacts.py --spicy "Las Vegas, NV"  # adult mode
"""

from __future__ import annotations

import html
import json
import os
import random
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "TwitchFunFactBot/1.0 (hobby Twitch chat bot)"
WIKI_API = "https://en.wikipedia.org/w/api.php"
DDG_API = "https://api.duckduckgo.com/"
OSM_API = "https://nominatim.openstreetmap.org/search"  # free geocoder
GOOGLE_API = "https://www.googleapis.com/customsearch/v1"  # needs key + cx
SERPER_API = "https://google.serper.dev/search"  # needs one free key
SPICY_DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "spicy_facts.json"
)

# Words that make a sentence sound like a "fun fact" (higher score = better).
# "Trucker-flavoured" words are included since the bot targets a trucking stream.
_STRONG = re.compile(
    r"\b(visited|visits|emergency landing|rocking chair|"
    r"originally called|originally named|formerly known as|formerly called|renamed|"
    r"oldest|youngest|first|second|largest|smallest|tallest|longest|"
    r"shortest|deepest|highest|lowest|only|last|birthplace|famous|"
    r"known for|best known|home of|named after|named for|world|"
    r"national|record|haunted|legend|rare|unique|"
    r"truck stops?|interstates?|highways?|railroads?|railways?|junctions?|"
    r"crossroads|bridges?|tunnels?|turnpikes?|freeways?|freight|"
    r"mile markers?|rest stops?|route 66|museum|landmark|monument|memorial|"
    r"president|civil war|battle|national register|artifact|relic|"
    r"takes its name|takes their name|named for|namesake|eponym|eponymous|"
    r"philanthropist|billionaire|richest|"
    # Things a town is actually known for. Without these, the best fact about a
    # place ("600 pinball machines", "a state championship") scored 0 and was
    # cut by the "score < 2" tail-trim, while a census stub survived.
    r"arcade|pinball|amusement|roller coaster|roadside attraction|attraction|"
    r"hall of fame|championship|tavern|covered bridge|lighthouse|waterfall|"
    r"cave|hot springs|festival|collection)\b",
    re.IGNORECASE,
)
_WEAK = re.compile(
    r"\b(founded|established|incorporated|settled|originally|formerly|"
    r"once was|dates back|historic|population|located|site of|renamed)\b",
    re.IGNORECASE,
)
# Adult-rated flavour words — boost these sentences in "spicy" mode. These are
# risqué-but-legal barstool-humor topics (brothels, bootlegging, outlaws, …),
# deliberately NOT explicit sexual content or anything Twitch-bannable.
_SPICY = re.compile(
    r"\b(beer|brew|brewery|whiskey|whisky|bourbon|moonshine|bootleg|bootlegg|"
    r"prohibition|saloon|brothel|bordello|red.?light|prostitut|"
    r"strip club|stripper|cabaret|burlesque|casino|gambl|slot machine|"
    r"outlaw|gangster|mobster|gunfight|shootout|duel|"
    r"dead man|hanged|lynch|prison|jail|heist|robbery|murder|"
    r"haunted|ghost|voodoo|curse|scandal|affair|mistress|"
    r"sin city|vice|divorce|wedding chapel|speakeasy|bars?\b|"
    r"ambush|manhunt|shooting|shooter|killed|slain|homicide|sniper|massacre|"
    r"cop.?killer|honeymoon|heart.?shaped|whirlpool|champagne (glass|tower)|"
    r"couples-only|couples resort)\b",
    re.IGNORECASE,
)

# Weird / bizarre flavour words — the second rung of the fallback ladder when a
# place has no spicy history: oddities, mysteries, legends, hoaxes, records.
_WEIRD = re.compile(
    r"\b(odd|odder|oddest|strange|strangest|weird|weirdest|bizarre|unusual|"
    r"peculiar|quirky|eccentric|mystery|mysterious|unexplained|"
    r"cryptid|bigfoot|sasquatch|ufo|alien|hoax|"
    r"disappear\w*|vanished|rumored|rumoured|legend has it|"
    r"folklore|tall tale|myth|superstition|cursed|"
    r"guinness|world record|roadside attraction|novelty|"
    r"only one|one of a kind|last of its kind|mystery spot|gravity hill|"
    r"witch|vampire|secret tunnel|giant ball|largest collection)\b",
    re.IGNORECASE,
)

_CITE = re.compile(r"\[\d+\]")
_TEMPLATE = re.compile(r"\{\{.*?\}\}", re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_COORD = re.compile(r"^(coordinates|gps|latitude|\d{1,3}°\d)", re.IGNORECASE)
# A "Notable people" style list entry: "Zygmunt S. Leymel (1883–1947) was a…"
_BIO = re.compile(r"^[A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+){1,3}\s*\([^)]*\d{4}")
# A pure definition sentence: "Milford is a borough that is located in Pike
# County, Pennsylvania, United States, and the county seat." — not a fun fact.
_DEFINITION = re.compile(
    r"^(?:[\w.'-]+\s+){0,4}\bis\s+(?:a|an)\s+(?:borough|city|town|village|hamlet|"
    r"municipality|unincorporated community|community|census-designated place|"
    r"cdp|county seat)\b[^.!?]{0,160}\b(?:located|county|united states|county seat)\b",
    re.IGNORECASE,
)

# Demographic / geography boilerplate that is never a fun fact: census counts,
# incomes, area, zip/area codes, elevation, and bare "It is located …" filler.
_FILLER = re.compile(
    r"(population was|the population (of|was|is)|population density|"
    r"median household income|per capita income|household income|racial makeup|"
    r"as of the \d{4} census|\d{4} census|has a total area|total area of|"
    r"zip code|area code|elevation of|the median income|"
    r"school district|a portion of the cdp|portion of the cdp|"
    r"it is located|is located (in|on|along|east|west|north|south|between|at)|"
    r"is situated (in|on|along)|lies (in|on|along|east|west|north|south|between)|"
    r"located directly|directly north|directly south|directly east|directly west|"
    r"a suburb|suburb in|metropolitan area)",
    re.IGNORECASE,
)


# Search snippets and page titles are not facts. These two kinds were reaching
# the LLM as "ground truth": truncated page titles ("The Only Man Ever Hanged
# in Trumbull County: A True ...") and SEO boilerplate from crime-stats and
# real-estate pages ("Explore crime rates for Girard, OH including murder,
# assault, and property crime statistics."). A model asked to be witty about
# that will invent the rest.
_JUNK_SEED = re.compile(
    r"(\.\.\.|\u2026)\s*$|"
    r"\b(explore|view|browse|check out|see|compare|read|search|find)\b[^.]{0,40}"
    r"\b(crime rates?|crime grade|statistics|stats|data|reviews?|photos?)\b|"
    r"\bcrime (rates?|grade|statistics|stats)\b|"
    r"\bis it safe\b|\bsafest (places|cities|towns)\b|"
    r"\bbest places to live\b|\bcost of living\b|"
    r"\bhomes for sale\b|\breal estate\b|\bapartments?\b|\bzillow\b|"
    r"\brentals?\b|\bweather (forecast|today)\b|"
    # The standard NRHP boilerplate is a list of buildings, not a fact, and it
    # scored 5 for 'national register' — outranking Cuba MO's World's Largest
    # Rocking Chair and its Bette Davis / Amelia Earhart visits.
    r"\bare listed on the national register\b",
    re.IGNORECASE,
)


def _is_junk_seed(sentence: str) -> bool:
    """True for search-result noise that must never be treated as a fact."""
    return bool(_JUNK_SEED.search(sentence))


# A bare "Name, epithet, epithet" stub is a Wikipedia *title*, not a fact about
# the town — and when the person merely shares the town's name it is actively
# dangerous. DuckDuckGo's results for "girard, OH" led with "Joe Girard,
# Guinness Book of World Records winning American salesman" (born Detroit, 1928)
# and "Hugo Girard, Canadian Strongman, former World Champion", and the model
# turned the first into "Joe Girard called Girard home". Neither stub names the
# town, states a date, or contains a verb — so they are dropped, while real
# sentences ("It is believed that Girard takes its name from...") are untouched.
_STUB_EPITHET = re.compile(
    r"\b(former|current|retired|american|canadian|british|australian|world|"
    r"national|professional|record|champion\w*|salesman|actor|actress|"
    r"politician|senator|governor|mayor|musician|singer|songwriter|composer|"
    r"player|coach|pitcher|quarterback|boxer|wrestler|strongman|driver|racer|"
    r"author|writer|poet|artist|painter|inventor|scientist|engineer|general|"
    r"businessman|entrepreneur|philanthropist|priest|bishop|outlaw|gangster)\b",
    re.IGNORECASE,
)
_STUB_NAME = re.compile(r"^[A-Z][\w'.-]*(?:\s+[A-Z][\w'.-]*){0,3}$")
_STUB_VERB = re.compile(
    r"\b(is|are|was|were|be|been|being|has|have|had|became|becomes|serves?|"
    r"served|founded|opened|built|established|born|died|lived|located|stands?|"
    r"holds?|hosted|won|named|settled|incorporated)\b", re.IGNORECASE)


# "It is located on the Gasconade River near Interstate 44, and is approximately
# ten miles west of Rolla" is where-a-place-is, not a fun fact — but it scored 6
# because 'interstate' is a _STRONG word, which also suppressed the filler check,
# so it outranked Jerome MO's only piece of history (platted 1867 as Fremont
# Town) and reached the model as the sole seed.
_LOCATION_ONLY = re.compile(
    r"^\s*(?:it|there|the\s+(?:community|town|city|village|cdp|hamlet))\s+"
    r"(?:is\s+|was\s+)?(?:located|situated|lies|sits)\b", re.IGNORECASE)


def _is_person_stub(sentence: str) -> bool:
    """True for a bare "Firstname Lastname, epithet, epithet" title stub."""
    t = sentence.strip()
    if not t or len(t) > 110 or "." in t or t.count(",") < 1:
        return False
    if not _STUB_NAME.match(t.split(",")[0].strip()):
        return False
    if _STUB_VERB.search(t):
        return False
    return bool(_STUB_EPITHET.search(t))


def _is_filler(sentence: str) -> bool:
    """True for census/location boilerplate that is never a fun fact.

    Sentences that ALSO contain a strong 'fun fact' word (e.g. 'the world's
    oldest X is located in …') are not filler — the interesting bit wins.
    """
    return bool(_FILLER.search(sentence)) and not _STRONG.search(sentence)


def _filter_definitions(sentences: list) -> list:
    """Drop boring 'X is a borough located in Y County…' definition sentences.

    If every sentence is a definition, the original list is returned unchanged
    so the bot still has something to say.
    """
    kept = [s for s in sentences if not _DEFINITION.match(s)]
    return kept if kept else sentences

# Tiny in-memory cache so repeated !funfact spam doesn't hammer the APIs.
# Each entry also stores the rotation state for multiple facts.
_cache: dict = {}
_cache_lock = threading.Lock()
_HIT_TTL = 3600     # cache successful lookups for 1 hour
_MISS_TTL = 300     # cache failed lookups for 5 minutes
_BUSY_TTL = 30      # cache "sources rate-limited" for 30s (retry soon)
_log_once = set()   # one-time warnings

# Wikipedia rate-limit handling: after a 429, back off globally so a burst of
# lookups doesn't turn into a slow 429-retry storm (which was taking 30+ s).
_wiki_lock = threading.Lock()
_wiki_blocked_until = 0.0


# Set True by bot.py when --debug / TWITCH_DEBUG=1 is on: prints which source
# answered and the exact seed pool handed to the LLM, so a bad fact can be
# traced to its source instead of guessed at.
DEBUG = (os.environ.get("TWITCH_DEBUG", "").strip().lower()
         in ("1", "true", "yes", "on")) or ("--debug" in sys.argv)


def _wiki_blocked() -> bool:
    return time.time() < _wiki_blocked_until


def _note_wiki_429() -> None:
    global _wiki_blocked_until
    with _wiki_lock:
        now = time.time()
        first = _wiki_blocked_until <= now
        _wiki_blocked_until = max(_wiki_blocked_until, now + 45)
    if first:
        print("[funfacts] wikipedia rate-limited — backing off 45s, using fallbacks",
              flush=True)


# Pace Wikipedia API calls so a spicy lookup's burst of searches (dig + region
# dig + harvest) doesn't trip Wikipedia's anonymous rate limit and take every
# source down at once. Anonymous API use should stay well under ~1 req/s.
_wiki_pace_lock = threading.Lock()
_wiki_last_req = 0.0
_EXTRACT_PAGE_CAP = 4   # follow `excontinue` at most this many pages
# Bound on how much of a full article we keep in memory. Cuba, Missouri's is
# ~7.6 KB; big cities run to a few hundred KB and everything past the first
# sections is references and census tables anyway.
_EXTRACT_CHAR_CAP = 20000
_WIKI_PACE = 0.25


def _pace_wiki() -> None:
    """Sleep briefly so consecutive Wikipedia calls stay under ~4 req/s."""
    global _wiki_last_req
    with _wiki_pace_lock:
        now = time.time()
        wait = _WIKI_PACE - (now - _wiki_last_req)
        if wait > 0:
            time.sleep(wait)
        _wiki_last_req = time.time()


def _http_get_json(url: str, params: dict, timeout: float = 8.0) -> dict:
    if url.startswith(WIKI_API):
        _pace_wiki()
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(url + "?" + qs, headers={"User-Agent": USER_AGENT})
    last_exc = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and url.startswith(WIKI_API):
                _note_wiki_429()  # fail fast; the 45s cooldown handles the rest
                raise
            if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                last_exc = exc
                time.sleep(0.8 * (attempt + 1))  # back off, then retry
                continue
            raise
        except (urllib.error.URLError, socket.timeout) as exc:
            if attempt < 2:
                last_exc = exc
                time.sleep(0.8 * (attempt + 1))
                continue
            raise
    raise last_exc


# Common abbreviations whose trailing period must NOT split a sentence
# ("U.S. Route 40", "St. Louis", "e.g." ...).
_ABBREV = {
    "US", "USA", "UK", "UAE", "EU", "St", "Mt", "Dr", "Mr", "Mrs", "Ms",
    "Jr", "Sr", "Rev", "vs", "etc", "eg", "ie", "approx", "No", "Co",
    "Inc", "Ltd", "Corp", "Ave", "Blvd", "Rd", "Hwy", "Rte", "Ft", "Nm",
    "N", "S", "E", "W", "Gen", "Gov", "Sen", "Rep", "Capt", "Lt", "Col",
    "Maj", "Prof", "Dept", "Univ", "Jan", "Feb", "Mar", "Apr", "Jun",
    "Jul", "Aug", "Sep", "Sept", "Oct", "Nov", "Dec",
}
_SENT_END = re.compile(r"([.!?])(\s+)(?=[A-Z0-9])")


def _sentence_split(paragraph: str) -> list:
    """Split one paragraph into sentences, ignoring abbreviation periods."""
    out = []
    start = 0
    for m in _SENT_END.finditer(paragraph):
        punct = m.start(1)
        prev = paragraph[max(start, punct - 30):punct]
        word = re.search(r"([A-Za-z0-9&]+(?:[.'][A-Za-z0-9&]+)*)$", prev)
        token = word.group(1) if word else ""
        core = token.replace(".", "").replace("'", "")
        if core and (core in _ABBREV or (len(core) == 1 and core.isupper())):
            continue  # abbreviation / initial — not a sentence boundary
        out.append(paragraph[start:punct + 1].strip())
        start = m.end()
    tail = paragraph[start:].strip()
    if tail:
        out.append(tail)
    return out


def _sentences(text: str) -> list:
    """Split raw extract text into clean, readable sentences."""
    # Search APIs hand back HTML entities ("Jan &amp; Dean"); the chat should
    # never see them.
    text = html.unescape(text or "")
    text = _CITE.sub("", text)
    text = _TEMPLATE.sub(" ", text)
    text = _TAG.sub(" ", text)
    out = []
    for para in text.split("\n"):
        para = _WS.sub(" ", para).strip()
        if not para:
            continue
        # Skip short, punctuation-free lines (Wikipedia section headings).
        if not re.search(r"[.!?]", para) and len(para) < 45:
            continue
        for s in _sentence_split(para):
            s = s.strip()
            if len(s) >= 12 and not _COORD.match(s) and not _BIO.match(s):
                out.append(s)
    return out


def _score(sentence: str, spice: bool = False) -> int:
    """Interest score. In spicy mode the score encodes the fallback ladder:
    spicy facts (brothels/crime/…) rank first, then weird/bizarre facts, then
    the usual popular fun facts."""
    score = 3 * len(_STRONG.findall(sentence)) + len(_WEAK.findall(sentence))
    if spice:
        score += 1000 * len(_SPICY.findall(sentence))
        score += 100 * len(_WEIRD.findall(sentence))
    # Slight penalty for sentences that start with a bare pronoun — they read
    # as context-less when pulled out of the article ("Some are listed ...").
    if re.match(r"^(some|it|they|this|these|those|there|their|he|she|his|her|its)\b",
                sentence, re.IGNORECASE):
        score -= 1
    return score


def _clip(text: str, cap: int) -> str:
    """Hard-cap whitespace-normalized text WITHOUT an ellipsis. Used to store
    full candidate facts so the final step can summarise them properly."""
    text = " ".join(text.split())
    if len(text) <= cap:
        return text
    return text[:cap].rsplit(" ", 1)[0]


def _trim(text: str, limit: int) -> str:
    """Fit text to at most `limit` chars, ending at a clause boundary when
    possible so we never chop mid-phrase ('ending a…')."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    for sep in ("; ", " — ", ", "):
        head = text[:limit]
        if sep not in head:
            continue
        cut = head.rsplit(sep, 1)[0].rstrip(" ,;:-—")
        if len(cut) >= int(limit * 0.55):
            return cut + "…"
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-—") + "…"


def _fit_fact(fact: str, limit: int, opts: dict) -> str:
    """Fit a fact to the char limit. Prefer an LLM summary (a complete, true
    sentence within the limit); fall back to a clause-boundary trim."""
    fact = " ".join(fact.split())
    if len(fact) <= limit:
        return fact
    try:
        import llm
    except Exception:
        llm = None
    if llm and llm.is_configured(opts):
        try:
            s = llm.summarize(fact, limit, opts)
            if s:
                s = " ".join(s.split()).strip('"“”')
                # stay grounded: the summary may not introduce names/dates
                # that the source fact didn't contain.
                s2 = _grounded_filter([s], "", "", [fact])
                s = s2[0] if s2 else ""
                if s and len(s) <= limit:
                    return s
        except Exception as exc:
            print(f"[funfacts] llm summarize failed: {exc!r}", flush=True)
    return _trim(fact, limit)


def _overlap(a: str, b: str) -> float:
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / min(len(sa), len(sb))


def _ranked_facts(sentences: list, spice: bool = False,
                  limit: int = 200, count: int = 6) -> list:
    """Rank sentences and return up to `count` distinct, trimmed facts.

    The top sentence is always kept (so every place gets an answer), but
    lower-ranked sentences are only kept if they're interesting (score >= 2) —
    this filters out dull "population was..." / "incorporated in..." filler.
    """
    ranked = sorted(((s, _score(s, spice)) for s in sentences),
                    key=lambda p: (-p[1], len(p[0])))
    out, seen_norm = [], []
    for s, sc in ranked:
        # All four of these are skipped outright, even when nothing else
        # survives: an empty pool makes the caller fall through to the next
        # source (or a related article) instead of posting a census line, a
        # "it is located near..." line, SEO boilerplate or a namesake person.
        if (_is_filler(s) or _is_junk_seed(s) or _is_person_stub(s)
                or _LOCATION_ONLY.match(s)):
            continue
        if sc < 2 and out:
            break
        fact = _clip(s, 600)
        if not fact:
            continue
        norm = " ".join(re.sub(r"[^a-z0-9 ]", "", fact.lower()).split())
        if any(_overlap(norm, prev) > 0.7 for prev in seen_norm):
            continue
        out.append(fact)
        seen_norm.append(norm)
        if len(out) >= count:
            break
    return out


# ---- US states / regions, for picking the right "Springfield" -------------

_US_STATES = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas",
    "ca": "california", "co": "colorado", "ct": "connecticut", "de": "delaware",
    "fl": "florida", "ga": "georgia", "hi": "hawaii", "id": "idaho",
    "il": "illinois", "in": "indiana", "ia": "iowa", "ks": "kansas",
    "ky": "kentucky", "la": "louisiana", "me": "maine", "md": "maryland",
    "ma": "massachusetts", "mi": "michigan", "mn": "minnesota",
    "ms": "mississippi", "mo": "missouri", "mt": "montana", "ne": "nebraska",
    "nv": "nevada", "nh": "new hampshire", "nj": "new jersey",
    "nm": "new mexico", "ny": "new york", "nc": "north carolina",
    "nd": "north dakota", "oh": "ohio", "ok": "oklahoma", "or": "oregon",
    "pa": "pennsylvania", "ri": "rhode island", "sc": "south carolina",
    "sd": "south dakota", "tn": "tennessee", "tx": "texas", "ut": "utah",
    "vt": "vermont", "va": "virginia", "wa": "washington",
    "wv": "west virginia", "wi": "wisconsin", "wy": "wyoming",
    "dc": "district of columbia",
}

_CA_PROVINCES = {
    "ab": "alberta", "bc": "british columbia", "mb": "manitoba",
    "nb": "new brunswick", "nl": "newfoundland", "ns": "nova scotia",
    "on": "ontario", "pe": "prince edward island", "qc": "quebec",
    "sk": "saskatchewan",
}

_COUNTRIES = {
    "usa": "united states", "us": "united states", "uk": "united kingdom",
    "uae": "united arab emirates", "nz": "new zealand",
}


# Region names, longest first, matched on word boundaries (see
# _text_names_other_region for why substring matching is not safe here).
_REGION_NAMES = sorted(set(_US_STATES.values()) | set(_CA_PROVINCES.values())
                       | set(_COUNTRIES.values()), key=len, reverse=True)
_REGION_NAME_RES = [(n, re.compile(r"\b" + re.escape(n) + r"\b"))
                    for n in _REGION_NAMES]
# The country a US state lives in — never "another region".
_US_COUNTRY_WORDS = {"united states", "united states of america", "usa"}
# Reverse of _US_STATES / _CA_PROVINCES: viewers type full state names
# ('Indian Lake, Missouri'), and the checks below are keyed by abbreviation.
_US_STATE_BY_NAME = {v: k for k, v in _US_STATES.items()}
_CA_PROVINCE_BY_NAME = {v: k for k, v in _CA_PROVINCES.items()}



# Words that are part of a place name, not a place on their own — so
# "Kansas City" is never split into "Kansas" + "City", and "Cuba City
# Wisconsin" keeps "Cuba City" as the town.
_GENERIC_PLACE_WORDS = {
    "city", "town", "township", "village", "borough", "county", "lake",
    "lakes", "springs", "falls", "beach", "heights", "junction", "station",
    "park", "rapids", "hill", "hills", "creek", "river", "point", "fork",
}


def _split_trailing_region(query: str):
    """('Cuba Missouri' -> ('Cuba', 'missouri')); ('', '') if not applicable.

    Viewers type the state without a comma. Losing the region is worse than a
    cosmetic problem: with no region, 'Cuba, Missouri' scores 118 and the
    island nation scores 120, so the country outranks the town, and every
    region guard in this module switches itself off.
    """
    words = query.strip().split()
    if len(words) < 2:
        return "", ""
    known = set(_US_STATES) | set(_US_STATE_BY_NAME) | set(_CA_PROVINCES) \
        | set(_CA_PROVINCE_BY_NAME)
    for n in (2, 1):                      # "north carolina" before "carolina"
        if len(words) <= n:
            continue
        cand = " ".join(words[-n:]).lower().strip(".")
        if cand not in known:
            continue
        rest = " ".join(words[:-n]).strip(" ,;")
        if rest and rest.lower().strip(".") not in _GENERIC_PLACE_WORDS:
            return rest, cand
    return "", ""


def _query_core(query: str) -> str:
    """The place name with the region removed: 'Mount Cobb'."""
    if not re.search(r"[,;|]", query):
        rest, region = _split_trailing_region(query)
        if region:
            query = rest
    core = re.split(r"[,;|]", query, maxsplit=1)[0]
    core = re.sub(r"[^a-z0-9\s]", " ", core.lower())
    return " ".join(core.split())


def _query_region(query: str) -> str:
    """The region, with or without a comma: 'PA', 'Iowa', 'missouri'."""
    parts = re.split(r"[,;|]", query, maxsplit=1)
    if len(parts) >= 2:
        return re.sub(r"[^a-z0-9\s]", " ", parts[1].lower()).strip()
    return _split_trailing_region(query)[1]


def _title_tokens(title: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", title.lower()).split())


def _title_matches_region(title: str, region: str) -> bool:
    """True if the article title is for the requested region (state/province/
    country). With no region given, everything matches."""
    if not region:
        return True
    t = _title_tokens(title)
    region_full = (_US_STATES.get(region) or _CA_PROVINCES.get(region)
                   or _COUNTRIES.get(region))
    if region_full and region_full in t:
        return True
    if region in t.split():
        return True
    return False


def _text_names_other_region(text: str, region: str) -> bool:
    """True if the text names a *different* region (state/province/country)
    than the one requested. Used two ways: on a title, to reject same-named
    places in other states ('Lakemont, Washington' when the viewer asked for
    Lakemont, PA); and on an article's opening, to reject a bare redirect
    title that actually points at another state's place.

    Two traps this has to avoid:

    * "United States" is not another region — nearly every US article lead
      reads "a city in X County, <State>, United States", and treating that as
      a foreign mention discarded the town's own article outright, so the
      lookup fell through to web search.
    * Names must match on word boundaries and must not fire on a longer
      requested name: "Arkansas" is not "Kansas", and "West Virginia" is not
      "Virginia".
    """
    if not region:
        return False
    text_norm = _title_tokens(text)
    # Viewers type either form ("Girard, OH" or "Girard, Ohio"), and _US_STATES
    # is keyed by abbreviation — so accept both, and treat the country as home
    # for either. Getting this wrong for full state names discarded the town's
    # own article again ("Indian Lake ... Missouri, United States"), which is
    # exactly what left the 09:29 lookup with nothing but a song article.
    us_abbr = region if region in _US_STATES else _US_STATE_BY_NAME.get(region, "")
    ca_abbr = region if region in _CA_PROVINCES else _CA_PROVINCE_BY_NAME.get(region, "")
    requested = {region, us_abbr, _US_STATES.get(us_abbr, ""),
                 ca_abbr, _CA_PROVINCES.get(ca_abbr, ""),
                 _COUNTRIES.get(region, "")}
    requested.discard("")
    if us_abbr:
        requested |= _US_COUNTRY_WORDS      # "United States" == same place
    elif ca_abbr:
        requested.add("canada")
    for name, rx in _REGION_NAME_RES:
        if name in requested:
            continue
        # "virginia" inside a requested "west virginia" is not another region.
        if any(name in req for req in requested):
            continue
        if rx.search(text_norm):
            return True
    return False


# Works and name pages that share a place's name. "Indian Lake (song)" is the
# 1968 Cowsills single; its Cover-versions list says "Jan & Dean included it on
# their 1985 album Silver Summer", and that sentence was posted as a fun fact
# about Indian Lake, Ohio and then about Indian Lake, Missouri — where "it"
# meant the song all along. The title matches the place exactly, so neither the
# region check nor the name-the-place check can catch it.
_WORK_TITLE = re.compile(
    r"\((?:song|songs|single|album|band|music|film|movie|tv series|television "
    r"series|episode|novel|book|poem|play|musical|opera|painting|sculpture|"
    r"video game|game|character|surname|given name|name|disambiguation|ship|"
    r"company|brand|magazine|newspaper|award)\)", re.IGNORECASE)


def _is_road_or_meta_title(title: str) -> bool:
    """Road/highway/route, meta and works pages are never the fun fact."""
    t = title.lower()
    if t.startswith(("list of", "category:", "template:", "wikipedia:", "portal:", "file:")):
        return True
    if _WORK_TITLE.search(title):
        return True
    return any(w in t for w in (" route ", " route", "highway", "interstate",
                                "county road", "state road", "turnpike",
                                "freeway", "expressway", "bypass"))


def _title_relevance(title: str, core: str, region: str) -> int:
    """How likely `title` is the place the viewer asked about. 70+ = accepted."""
    t = _title_tokens(title)
    if not core:
        return 0
    score = 0
    if t == core:
        score += 120
    elif t.startswith(core):
        score += 110
    elif core in t:
        score += 100
    else:
        words = core.split()
        present = [w for w in words if w in t]
        score += 70 if (words and len(present) == len(words)) else 20 * len(present)

    region = region.rstrip(".")
    if region:
        region_full = (_US_STATES.get(region) or _CA_PROVINCES.get(region)
                       or _COUNTRIES.get(region))
        if region_full and region_full in t:
            score += 100
        elif region in (set(_US_STATES.values()) | set(_CA_PROVINCES.values())
                        | set(_COUNTRIES.values())) and region in t:
            score += 100
        elif region in t.split():
            score += 60

    if ", " in title:
        score += 8
    return score


# Harvested articles must actually be about a place. The bare-name search that
# finds related articles ("Lakemont Park" for Lakemont, PA) also returns every
# person who shares the town's name, and each of those titles scores 120 in
# _title_relevance (an exact match on the core word) so they clear the >= 70
# gate. For "Jerome, Missouri" that poured in Saint Jerome of Stridon ("He is
# best known for his translation of the Bible into Latin"), the writer Jerome
# K. Jerome, and Jerome Barnes the Missouri state representative.
_BIOGRAPHICAL = re.compile(
    # "was an early Christian priest", "is an American politician",
    # "(2 May 1859 - 14 June 1927) was an English writer and humorist".
    r"\b(?:was|is|were|are)\s+(?:an?\s+)?(?:early|former|late|retired|current)?\s*"
    r"(?:american|english|british|irish|scottish|welsh|canadian|australian|"
    r"new zealand|french|german|italian|spanish|portuguese|dutch|swedish|"
    r"norwegian|danish|polish|russian|japanese|chinese|indian|mexican|brazilian|"
    r"african|greek|roman|byzantine|czech|hungarian|turkish|israeli|egyptian)?\s*"
    r"(?:christian|catholic|orthodox|protestant|jewish|muslim)?\s*"
    r"(?:politician|actor|actress|writer|author|poet|novelist|playwright|"
    r"screenwriter|journalist|editor|publisher|critic|singer|songwriter|"
    r"musician|composer|conductor|pianist|guitarist|drummer|violinist|rapper|"
    r"dancer|choreographer|priest|theologian|bishop|saint|historian|"
    r"philosopher|scientist|physicist|chemist|mathematician|astronomer|"
    r"biologist|geologist|doctor|physician|surgeon|nurse|professor|teacher|"
    r"lawyer|attorney|judge|architect|engineer|inventor|artist|painter|"
    r"sculptor|photographer|filmmaker|director|producer|animator|comedian|"
    r"model|athlete|sportsman|coach|referee|umpire|manager|businessman|"
    r"entrepreneur|financier|banker|diplomat|ambassador|governor|senator|"
    r"representative|congressman|congresswoman|mayor|monarch|king|queen|"
    r"emperor|prince|princess|duke|duchess|count|countess|baron|general|"
    r"admiral|soldier|sailor|officer|veteran|explorer|aviator|astronaut|"
    r"baseball|football|basketball|hockey|cricket|soccer|golf|tennis|boxing|"
    r"racing|swimming|cyclist|serial killer|criminal|gangster|outlaw|sheriff|"
    r"marshal|detective|spy|monk|nun|missionary|evangelist|preacher|rabbi|"
    r"translator|confessor|historiographer)\b",
    re.IGNORECASE,
)
# A lifespan in the lead is the other reliable sign of a biography:
# "Jerome of Stridon (... c. 342-347 - 30 September 420)", "(born 16 March 1963)".
_LIFESPAN = re.compile(
    r"\((?:[^()]{0,80}?(?:\bb\.\s?|\bborn\b|\bd\.\s?|\bdied\b|\bc\.\s?\d|"
    r"\b(?:1[5-9]|20)\d\d\s*[–—-]\s*(?:1[5-9]|20)\d\d|\bfl\.))[\s\S]{0,60}?\)"
    r"|\bborn\s+in\s+(?:january|february|march|april|may|june|july|august|"
    r"september|october|november|december)\b",
    re.IGNORECASE,
)


# Not a person either: an organisation, a work, a ship. "Conway, missouri" at
# 10:52 posted "this town's got quite the maritime library stacked up" — two
# sentences lifted from Conway Publishing (pageid 29203197), a British imprint
# of Bloomsbury, thousands of miles from the Ozarks.
_NOT_A_PLACE = re.compile(
    r"\b(?:is|was|were|are)\s+(?:an?\s+)?(?:imprint|subsidiary|division|brand|"
    r"label|company|corporation|firm|conglomerate|publisher|publishing house|"
    r"newspaper|magazine|journal|periodical|broadcaster|network|charity|"
    r"organisation|organization|nonprofit|non-profit|band|record label|"
    r"ship|schooner|frigate|warship|vessel|aircraft|film|movie|"
    r"television (?:series|show)|song|album|book|novel|video game|award|"
    r"prize|competition|festival|brand name|trademark)\b|"
    r"\bfounded in (?:1[6-9]|20)\d\d as an?\b",
    re.IGNORECASE,
)


def _is_non_place_article(extract: str) -> bool:
    """True if an article's opening describes a person, company or work rather
    than a place.

    Applied to every harvested title. Related-article harvesting is what lets a
    tiny town borrow facts from a bigger article that shares its name — that is
    how Lakemont, PA gets Leap-The-Dips from "Lakemont Park". But the same
    search returns namesakes, and their sentences carry nothing tying them to
    the town: "Barnes was born in Mississippi" (Jerome Barnes), "It is best
    known for its publications dealing with nautical subjects" (Conway
    Publishing). Requiring the place name instead is not an option: the
    Leap-The-Dips sentence never says "Lakemont" either.
    """
    lead = " ".join((extract or "").split())[:300]
    if not lead:
        return False
    return bool(_LIFESPAN.search(lead) or _BIOGRAPHICAL.search(lead)
                or _NOT_A_PLACE.search(lead))


# Kept so the earlier name still resolves in checks and older tests.
_is_person_article = _is_non_place_article


def _is_disambiguation(extract: str) -> bool:
    head = " ".join(extract.split())[:140].lower()
    return any(k in head for k in (
        "may refer to", "is the name of several", "can refer to",
        "most commonly refers to", "refers to more than one",
    ))


# ---- sources --------------------------------------------------------------

def _wiki_search(query: str) -> list:
    if _wiki_blocked():
        return []
    try:
        data = _http_get_json(
            WIKI_API,
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": 6,
                "format": "json",
                "formatversion": "2",
            },
        )
    except (urllib.error.URLError, OSError, ValueError):
        return []
    hits = data.get("query", {}).get("search", [])
    return [h.get("title", "") for h in hits if h.get("title")]


def _wiki_extract(title: str, exchars: int = 4000) -> str:
    if _wiki_blocked():
        return ""
    # exchars=0 means "the whole article". MediaWiki caps exchars at 1200 and
    # silently clamps anything larger, so asking for 4000 or 7000 returns the
    # lead only — which is why every town's best material was invisible. Omit
    # the parameter entirely and a single-title request returns everything.
    params = {
                "action": "query",
                "prop": "extracts",
                "explaintext": 1,
                "titles": title,
                "redirects": 1,
                "format": "json",
                "formatversion": "2",
    }
    if exchars:
        params["exchars"] = exchars
    try:
        data = _http_get_json(WIKI_API, params)
    except (urllib.error.URLError, OSError, ValueError):
        return ""
    pages = data.get("query", {}).get("pages", [])
    if not pages:
        return ""
    return (pages[0].get("extract", "") or "")[:_EXTRACT_CHAR_CAP]


def _wikipedia(query: str, spice: bool = False, limit: int = 200):
    core = _query_core(query)
    region = _query_region(query)
    full = " ".join(query.strip().split())

    items, seen = [], set()

    def gather(q):
        # ONE request returns the top search hits AND their text.
        for it in _wiki_search_extracts(q, exchars=7000 if spice else 4000, limit=6):
            title = it["title"]
            if title in seen:
                continue
            seen.add(title)
            if "(disambiguation)" in title.lower():
                continue
            if _is_road_or_meta_title(title):
                continue
            rel = _title_relevance(title, core, region)
            if rel >= 70 and not _is_non_place_article(it["extract"]):
                items.append((rel, title, it["extract"]))

    gather(full)
    if core and core != full.lower():
        # Also search the bare place name: the region-attached query can miss
        # the related article that actually holds the fun fact (e.g. "lakemont,
        # pa" doesn't surface "Lakemont Park" where Leap-The-Dips lives).
        gather(core)

    items.sort(key=lambda p: -p[0])
    if not items:
        return None

    # The place's own article is usually a stub whose only sentence is census
    # filler — but the real fun facts (e.g. "Leap-The-Dips, the world's oldest
    # roller coaster" for tiny Lakemont, PA) live in a *related* article that
    # the search also returned. So harvest every title that shares the place's
    # name, then — only if we still have almost nothing — dip into the
    # county/state/neighbour articles too.
    core_words = core.split()
    core_titles = [t for _, t, _ in items
                   if core and all(w in _title_tokens(t).split() for w in core_words)]
    # Prefer the article that is actually in the requested region; only dip
    # into same-named places in other states (e.g. "Lakemont, Washington" when
    # the viewer asked for Lakemont, PA) if we have almost nothing better.
    region_titles = [t for t in core_titles if _title_matches_region(t, region)]
    same_name_titles = [t for t in core_titles
                        if t not in region_titles and not _text_names_other_region(t, region)]
    other_titles = [t for _, t, _ in items
                    if t not in core_titles and _title_matches_region(t, region)]
    place = (region_titles or core_titles or [items[0][1]])[0]

    extracts = {t: e for _, t, e in items}
    # The combined search+extract call is capped at 1200 chars a page, so the
    # place's own article arrived lead-only. Re-fetch just that one article in
    # full: Cuba MO's World's Largest Rocking Chair, Bette Davis and Amelia
    # Earhart all sit below character 1200.
    if place and not _wiki_blocked():
        full = _wiki_extract(place, exchars=0)
        if len(full) > len(extracts.get(place, "")):
            extracts[place] = full
    pool, pool_norm = [], []

    def harvest(titles, require_core=False):
        for title in titles:
            extract = extracts.get(title, "")
            if not extract or _is_disambiguation(extract):
                continue
            if title != place and _is_non_place_article(extract):
                continue
            # A bare redirect title may point at another state's place — check
            # the article's opening statement names the requested region.
            if _text_names_other_region(extract[:250], region):
                continue
            facts = _ranked_facts(_filter_definitions(_sentences(extract)),
                                  spice=spice, limit=limit, count=8)
            for f in facts:
                # Sentences from an article that merely shares a word with the
                # place ("Avon Lake, Ohio" / "Lake County, Ohio" both score 128
                # for a query about Indian Lake, Ohio) are only usable if they
                # actually name the place. Without this the 09:19 lookup posted
                # "Its county seat is Painesville" — Lake County, 100 miles away.
                if require_core and core and core not in f.lower():
                    continue
                fn = " ".join(re.sub(r"[^a-z0-9 ]", "", f.lower()).split())
                if any(_overlap(fn, pn) > 0.7 for pn in pool_norm):
                    continue
                pool.append((_score(f, spice), f))
                pool_norm.append(fn)
            # Stop once we have plenty of interesting facts — no need to
            # fetch every related article.
            if sum(1 for sc, _ in pool if sc >= 2) >= 5 or len(pool) >= 10:
                break

    harvest(region_titles[:5])
    if len(pool) < 3:
        harvest(same_name_titles[:3])
    if len(pool) < 3:
        harvest(other_titles[:3], require_core=True)

    if not pool:
        return None
    pool.sort(key=lambda p: (-p[0], len(p[1])))
    return {"place": place, "facts": [f for _, f in pool[:8]]}


def _duckduckgo(query: str, spice: bool = False, limit: int = 200):
    data = _http_get_json(
        DDG_API,
        {"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
    )
    abstract = (data.get("AbstractText") or data.get("Abstract") or "").strip()
    heading = (data.get("Heading") or "").strip()
    if abstract:
        facts = _ranked_facts(_filter_definitions(_sentences(abstract)),
                              spice=spice, limit=limit)
        if facts:
            return {"place": heading or query, "facts": facts}
    return None


def _google_search(query: str, spice: bool, limit: int, options: dict):
    """Google Programmable Search (Custom Search JSON API).

    Optional: needs google_api_key + google_cx in config. With safe=off it can
    surface the racier local-news stuff that Wikipedia politely skips.
    Note the free tier is 100 queries/day.
    """
    key = (options.get("google_api_key") or "").strip()
    cx = (options.get("google_cx") or "").strip()
    if not key or not cx:
        if DEBUG:
            print("[funfacts] google source not configured "
                  "(needs BOTH google_api_key and google_cx)", flush=True)
        return None

    # Ask the web what is *interesting* about the place. Spicy mode used to
    # append "history crime scandal", which filled the seed list with
    # crime-stats boilerplate and one recent police story — and the model then
    # padded that thin, dark grist into invented dark history.
    q = query if not spice else f"{query} history facts famous landmark record"

    data = _http_get_json(
        GOOGLE_API,
        {"key": key, "cx": cx, "q": q, "num": 8, "safe": "off"},
        timeout=10,
    )
    items = data.get("items") or []
    if not items:
        return None

    sentences = []
    for it in items:
        for field in ("title", "snippet"):
            text = (it.get(field) or "").strip()
            if text:
                sentences.extend(_sentences(text))
    if not sentences:
        # Snippets are often short fragments without sentence punctuation.
        sentences = [t for t in (it.get("snippet") or "" for it in items)
                     if len(t.strip()) >= 12]

    facts = _ranked_facts(sentences, spice=spice, limit=limit, count=4)
    if not facts:
        return None
    return {"place": query, "facts": facts}


def _serper_search(query: str, spice: bool, limit: int, options: dict):
    """Serper (serper.dev) web search — Google-quality results via a tiny key.

    A real second search source that is independent of Wikipedia's rate limit,
    so the bot still answers when Wikipedia is throttled. Free tier: 2,500
    queries (no card needed at signup).
    """
    key = (options.get("serper_api_key") or "").strip()
    if not key:
        if DEBUG:
            print("[funfacts] serper source not configured (no serper_api_key)",
                  flush=True)
        return None

    # Same as _google_search: ask for interesting, not for crime.
    q = query if not spice else f"{query} history facts famous landmark record"
    body = json.dumps({"q": q, "num": 8}).encode("utf-8")
    req = urllib.request.Request(
        SERPER_API,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-API-KEY": key,
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        print(f"[funfacts] serper HTTP {exc.code}", flush=True)
        return None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"[funfacts] serper error: {exc!r}", flush=True)
        return None

    items = data.get("organic") or []
    if not items:
        return None

    sentences = []
    for it in items:
        for field in ("title", "snippet"):
            text = (it.get(field) or "").strip()
            if text:
                sentences.extend(_sentences(text))
    if not sentences:
        # Snippets are often short fragments without sentence punctuation.
        sentences = [t for t in (it.get("snippet") or "" for it in items)
                     if len(t.strip()) >= 12]

    facts = _ranked_facts(sentences, spice=spice, limit=limit, count=4)
    if not facts:
        return None
    return {"place": query, "facts": facts}


# ---- curated spicy database ------------------------------------------------

def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", s.lower()).split())


_spicy_db_cache = None


def _load_spicy_db() -> list:
    global _spicy_db_cache
    if _spicy_db_cache is None:
        try:
            with open(SPICY_DB_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            _spicy_db_cache = data.get("entries", data) if isinstance(data, dict) else data
        except (OSError, ValueError):
            _spicy_db_cache = []
    return _spicy_db_cache


def _spicy_db(location: str, limit: int):
    full = _norm(location)
    core = _norm(re.split(r"[,;|]", location, maxsplit=1)[0])
    region = _query_region(location)
    for entry in _load_spicy_db():
        keys = [_norm(k) for k in entry.get("keys", [])]
        if full not in keys and core not in keys:
            continue
        # A curated entry describes ONE place, but its keys are often
        # stateless ("girard", "indian lake"). Without a region check,
        # 'Girard, PA' was served the Girard, Ohio facts and 'Indian Lake,
        # Missouri' the Ohio lake's — the same wrong-place error the harvest
        # fixes were made to stop, reintroduced by the curated data.
        if region and not _title_matches_region(entry.get("name") or "", region):
            continue
        facts = [f for f in (_trim(x, limit) for x in entry.get("facts", [])) if f]
        if facts:
            return {"place": entry.get("name") or location, "facts": facts}
    return None


_SPICY_HINTS = ["murder", "brothel", "scandal", "crime", "prostitution", "saloon", "bootlegging", "haunted"]


def _spicy_dig(place_title: str, location: str, existing: list, limit: int,
               max_facts: int = 3) -> list:
    """Mine *other* Wikipedia articles for racy facts about the place.

    The town's own article is often dry, but searching `<place> brothel`,
    `<place> scandal`, `<place> murder` … surfaces crime/brothel/scandal pages
    that mention the town. We pull sentences that name the place and score
    them for spice. No LLM needed.
    """
    core = " ".join(re.sub(r"[^a-z0-9 ]", " ", _query_core(location).lower()).split())
    if not core or len(core) < 2:
        return []
    region = _query_region(location)

    existing_norm = [" ".join(re.sub(r"[^a-z0-9 ]", "", f.lower()).split()) for f in existing]
    core_title = _title_tokens(place_title)
    found = []
    found_norm = []

    for hint in _SPICY_HINTS[:3]:
        if len(found) >= max_facts or _wiki_blocked():
            break
        try:
            items = _wiki_search_extracts(f'"{place_title}" {hint}',
                                          exchars=7000, limit=2)
        except Exception as exc:
            print(f"[funfacts] spicy dig search error: {exc!r}", flush=True)
            break
        for it in items[:2]:
            if len(found) >= max_facts:
                break
            title = it["title"]
            tl = title.lower()
            if ("(disambiguation)" in tl or tl.startswith("list of")
                    or "category:" in tl or _title_tokens(title) == core_title):
                continue  # skip the town's own article — we already have it
            extract = it["extract"]
            if not extract or _is_disambiguation(extract):
                continue
            # Same-name places are everywhere, and this dig searches by name
            # alone. "Mount Vernon, MO" surfaced the Mount Vernon Place Historic
            # District in Baltimore, Maryland - listed on the National Register
            # 11 Nov 1971 - and posted it as one of the Missouri town's facts,
            # because the only gate here was "the sentence contains the name".
            # harvest() has always checked the region; this path never did.
            if _text_names_other_region(extract[:250], region):
                continue
            if _is_non_place_article(extract):
                continue
            for s in _sentences(extract):
                s_norm = " ".join(re.sub(r"[^a-z0-9 ]", " ", s.lower()).split())
                if core not in s_norm:
                    continue
                # A sentence can name the wrong state even inside a correct
                # article, so check the sentence too.
                if _text_names_other_region(s, region):
                    continue
                if _score(s, spice=True) < 1:
                    continue
                fact = _clip(s, 600)
                if not fact:
                    continue
                fn = " ".join(re.sub(r"[^a-z0-9 ]", "", fact.lower()).split())
                if any(_overlap(fn, e) > 0.7 for e in existing_norm):
                    continue
                # Compare normalised to normalised: `found` holds the raw
                # clipped text, so this used to match nothing and the same
                # sentence came back once per search hint.
                if any(_overlap(fn, f) > 0.7 for f in found_norm):
                    continue
                found.append(fact)
                found_norm.append(fn)
                if len(found) >= max_facts:
                    break
    return found


def _wiki_extracts(titles: list, exchars: int = 4000) -> dict:
    """Fetch the text of several articles (title -> extract).

    Plain-text extracts (`explaintext=1`) are capped at **one page per
    request** for anonymous callers: the API answers with a warning
    ('"exlimit" was too large ... lowered to 1'), returns the first page only
    and hands back a `continue` token. Requesting `exlimit=max` and reading the
    reply once therefore silently yields a single extract — every other title
    comes back empty and gets dropped, which is how a town's real claim to
    fame (living in a *related* article) went missing. So follow the token,
    bounded so one lookup can't turn into a dozen requests.

    `exchars` is likewise capped at 1200 by the API for plain-text extracts.
    """
    if not titles or _wiki_blocked():
        return {}
    params = {
        "action": "query",
        "titles": "|".join(titles),
        "prop": "extracts",
        "explaintext": 1,
        "exchars": min(exchars, 1200),
        "exlimit": "max",
        "format": "json",
        "formatversion": "2",
    }
    out = {}
    for _ in range(min(len(titles), _EXTRACT_PAGE_CAP)):
        if _wiki_blocked():
            break
        try:
            data = _http_get_json(WIKI_API, params)
        except (urllib.error.URLError, OSError, ValueError):
            break
        for page in data.get("query", {}).get("pages", []):
            t = page.get("title", "")
            if t and page.get("extract") and t not in out:
                out[t] = page["extract"]
        cont = data.get("continue") or {}
        if not cont.get("excontinue") or len(out) >= len(titles):
            break
        params.update(cont)  # excontinue + continue: "||"
    return out


def _wiki_search_extracts(query: str, exchars: int = 4000, limit: int = 6) -> list:
    """Search Wikipedia AND fetch each hit's text — two batched requests.

    A lookup used to cost `1 search + N extracts`; this is now `1 search + 1
    batched extract` for all hits at once, which keeps the bot comfortably
    under Wikipedia's anonymous per-IP rate limit.
    """
    titles = _wiki_search(query)[:limit]
    if not titles:
        return []
    extracts = _wiki_extracts(titles, exchars)
    return [{"title": t, "extract": extracts.get(t, "")} for t in titles]


def _wiki_geosearch(lat: float, lon: float, radius: int = 10000) -> list:
    """Wikipedia articles near a lat/lon, with their text, in ONE request."""
    if _wiki_blocked():
        return []
    try:
        data = _http_get_json(
            WIKI_API,
            {
                "action": "query",
                "generator": "geosearch",
                "ggscoord": f"{lat}|{lon}",
                "ggsradius": max(10, min(int(radius), 10000)),
                "ggslimit": 8,
                "prop": "extracts",
                "explaintext": 1,
                "exintro": 1,
                "format": "json",
                "formatversion": "2",
            },
        )
    except (urllib.error.URLError, OSError, ValueError):
        return []
    out = []
    for page in data.get("query", {}).get("pages", []):
        if not page.get("title"):
            continue
        out.append({
            "title": page.get("title"),
            "dist": page.get("dist") or 0,
            "extract": page.get("extract", "") or "",
        })
    return out


# ---- geocoding fallback (OpenStreetMap / Nominatim, free, no key) -----------

def _parse_geocode(item: dict):
    """Turn one Nominatim result into a small, useful dict (or None)."""
    addr = item.get("address") or {}
    name = (addr.get("village") or addr.get("hamlet") or addr.get("town")
            or addr.get("city") or addr.get("municipality") or addr.get("suburb")
            or addr.get("locality") or addr.get("road") or item.get("name")
            or "").strip()
    state = (addr.get("state") or addr.get("territory") or "").strip()
    county = (addr.get("county") or "").strip()
    country = (addr.get("country") or "").strip()
    try:
        lat, lon = float(item["lat"]), float(item["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    if not name and not state and not country:
        return None
    return {
        "name": name,
        "state": state,
        "county": county,
        "country": country,
        "lat": lat,
        "lon": lon,
        "display_name": item.get("display_name", "").strip(),
    }


def _osm_geocode(query: str):
    """Geocode an arbitrary place name. Covers even very remote villages."""
    try:
        data = _http_get_json(
            OSM_API,
            {"q": query, "format": "json", "limit": 1, "addressdetails": 1},
            timeout=10,
        )
    except Exception as exc:
        print(f"[funfacts] nominatim error: {exc!r}", flush=True)
        return None
    if not isinstance(data, list) or not data:
        return None
    return _parse_geocode(data[0])


# ---- optional LLM writer (OpenRouter / OpenAI-compatible) -------------------

# Hard backstop against sexually explicit chat output. The LLM prompt forbids
# this, but if a model ignores the instruction, any line matching these words is
# dropped — and if all lines are dropped, the bot keeps the plain real facts.
_EXPLICIT = re.compile(
    r"\b(porn\w*|xxx|blowjob\w*|pussy|masturbat\w*|ejaculat\w*|orgasm\w*|"
    r"penis\w*|vagina\w*|cunt\w*|erection\w*|gangbang\w*|bukkake|fisting|"
    r"cumshot\w*|sex\s+toy)\b",
    re.IGNORECASE,
)

# Taste backstop: turning a real killing, execution or lynching into
# entertainment ("a hanging party went down") is never acceptable, even when
# the underlying fact happens to be true. Matched lines are dropped like
# explicit ones — and if everything is dropped the bot posts the plain facts.
# A literal "murder mystery" dinner/party is a real attraction, so it is
# excluded from the first alternative.
_TASTELESS = re.compile(
    r"\b(hang(?:ing|ed)?|lynch(?:ing|ed)?|noose|gallows|execution|murder|"
    r"massacre|slaughter|suicide|corpse)\b(?:\s+(?!mystery\b)\w+){0,2}\s*"
    r"\b(party|parties|bash|festival|hoedown|celebration|soiree|rager)\b|"
    r"\b(party|bash|festival|hoedown|celebration)\b(?:\s+\w+){0,2}\s*"
    r"\b(hanging|lynching|noose|gallows|murder|massacre)\b|"
    # Flippant idiom for killing somebody: "they really dropped the axe on
    # this one guy", "a necktie party", "took him for a ride".
    r"\b(dropp\w+|hand\w*|serv\w+|giv\w+|took)\b(?:\s+\w+){0,3}\s*"
    r"\b(the axe|him for a ride|a ride)\b|"
    r"\bnecktie (party|parties|social)\b|\brope party\b|"
    r"\bstretched (his|her|their) neck\b|\bsent (him|her|them) up the river\b",
    re.IGNORECASE,
)

# Reasoning models (and OpenRouter's "openrouter/free" auto-router) sometimes
# return their chain-of-thought as text before the actual facts — "Here's a
# thinking process:", "1. Analyze the input:", "Final answer:" … These are meta
# chatter, never facts, so strip them before anything reaches chat.
_META_LINE = re.compile(
    r"^(here'?s?\b|here is\b|let me\b|i'?ll\b|i will\b|i'?m\b|we'?ll\b|"
    r"thinking\b|reasoning\b|analy[sz]e\b|analysis\b|"
    r"the (user|viewer|assistant|prompt|question|input)\b|"
    r"final answer\b|step\s*\d+\b|note:?\b|below\b|"
    r"sure[!,.]?\b|certainly\b|absolutely\b|okay?[!,.]?\b)",
    re.IGNORECASE,
)

# Grounding filter: a low-refusal LLM will happily invent names, dates and
# events when a town has no spicy history. This drops any LLM line that
# mentions a year or a capitalized name not already present in the seed facts,
# so the chat never gets a fabricated "Devil Jack Schramm"-style fact.
_YEAR = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})s?\b")
# "'04" style year references — the model's favourite way to smuggle in a date
# that no source fact contains (the seeds said "Jan. 4", not 2004).
_SHORT_YEAR = re.compile(r"['\u2019](\d{2})\b")
_CAP_WORD = re.compile(r"\b[A-Z][a-z]{2,}\b")
# Common shortenings of place names the model likes to use. Without this the
# filter drops a TRUE line — "...bank founder from Philly" was dropped because
# the seed said "Philadelphia" — while letting invented lines through.
_CAP_ALIASES = {
    "philly": "philadelphia", "phila": "philadelphia", "cincy": "cincinnati",
    "frisco": "san francisco", "chi": "chicago", "detriot": "detroit",
    "columbus ohio": "columbus", "kc": "kansas city", "nola": "new orleans",
}
# Reputation, significance and genre labels are claims too. "Surf rock legends
# Jan & Dean ... putting this sleepy Missouri spot on the musical map" adds four
# assertions no source made — the band's genre, that they are legends, that the
# town is sleepy, and that the album made it famous. _claims() saw none of them
# because they are ordinary words, so the line was posted verbatim.
_REPUTATION = re.compile(
    r"\bclaim(?:s|ed)?\s+to\s+fame\b|\bput(?:s|ting)?\s+.{0,40}?\bon\s+the\s+"
    r"(?:\w+\s+)?map\b|\bmade?\s+.{0,30}?famous\b|\blegend(?:s|ary)?\b|"
    r"\biconic\b|\bworld[- ](?:famous|renowned)\b|\bfamous(?:ly)?\b|"
    r"\brenowned\b|\bcelebrated\b|\bnotorious(?:ly)?\b|\bhidden\s+gem\b|"
    r"\bmust[- ]see\b|\bsleepy\b|\bquaint\b|\bcharming\b|\bidyllic\b|"
    r"\bpicturesque\b|\btimeless\b|\bbeloved\b|\bstoried\b|"
    r"\bthe\s+star\b|\bproud\b|\bcrown\b|\bbustling\b|\bheart\s+of\s+the\b|"
    r"\bgem\s+of\b|\bshowpiece\b|\bboasts\b|"
    r"\bsurf\s+rock\b|\brock\s+(?:legends?|icons?)\b|\bpunk\s+rock\b|"
    r"\bhip\s+hop\b|\bcountry\s+music\b|\bheavy\s+metal\b",
    re.IGNORECASE,
)
# A residence claim about a person is the namesake trap: "Joe Girard ... called
# Girard home" adds a fact (that he lived there) which no source states, and
# slips past the name check because "Girard" is already in the corpus. Such a
# line is only allowed if some seed actually places someone in the town.
_RESIDENCE = re.compile(
    r"\b(?:called|made|makes|claims?|claiming|counts?)\s+\S+\s+"
    r"(?:his|her|their)?\s*home\b|\bhometown\b|\bhome\s+town\b|"
    r"\bhailed?\s+from\b|\bgrew\s+up\s+in\b|\bnative\s+of\b|"
    r"\b(?:born|raised|reared)\s+in\b|"
    r"\blived\s+in\b|\blocal\s+(?:legend|hero|boy|girl|son|daughter)\b",
    re.IGNORECASE,
)
_GROUNDED_STOP = {
    # determiners / pronouns
    "the", "a", "an", "this", "that", "these", "those", "some", "many", "most",
    "every", "each", "both", "neither", "either", "all", "any", "no", "another",
    "other", "own", "same", "such", "few", "several", "it", "its", "he", "she",
    "they", "them", "their", "theirs", "his", "her", "hers", "we", "our", "ours",
    "you", "your", "yours", "my", "mine", "who", "whom", "whose", "what", "which",
    # prepositions / particles
    "in", "on", "at", "by", "for", "from", "to", "of", "with", "without",
    "within", "into", "onto", "over", "under", "above", "below", "between",
    "among", "against", "along", "across", "around", "about", "through",
    "throughout", "during", "before", "after", "until", "since", "behind",
    "beyond", "near", "beside", "despite", "per", "via", "plus", "minus", "off",
    "up", "down", "out", "away", "back",
    # conjunctions
    "and", "but", "or", "nor", "so", "yet", "if", "as", "than", "though",
    "although", "while", "when", "where", "because", "however", "meanwhile",
    "instead", "indeed", "rather", "unless",
    # common verbs / adverbs that legitimately start or join sentences
    "is", "are", "was", "were", "be", "been", "being", "has", "have", "had",
    "do", "does", "did", "will", "would", "can", "could", "should", "may",
    "might", "must", "shall", "built", "founded", "named", "called", "known",
    "opened", "said", "became", "made", "got", "kept", "used", "once", "still",
    "even", "just", "only", "also", "too", "very", "quite", "almost", "nearly",
    "always", "never", "often", "sometimes", "usually", "again", "then", "now",
    "later", "here", "there", "today", "yesterday", "home",
    # numbers / ordinals
    "one", "two", "three", "four", "five", "first", "second", "third", "fourth",
    "fifth",
}

# Regional (county/state) dig results are marked with this prefix, both so chat
# readers can tell the fact isn't about the town itself and so the grounding
# filter knows not to let the LLM re-attribute it to the town.
_AREA_PREFIX = "In the area: "

# Claim concepts: the substance of a fact. A rewrite may reword freely, but a
# concept in this table that appears in the LLM's line and nowhere in the seed
# facts is an invented claim — the lowercase equivalent of "Devil Jack
# Schramm", which the capitalised-name check above cannot see.
_CLAIM_CONCEPTS = (
    ("hanging", r"\bhang(?:s|ing|ed)?\b|\bnoose\w*\b|\bgallows\b"),
    ("lynching", r"\blynch\w*\b"),
    ("killing", r"\bmurder\w*\b|\bkill\w*\b|\bslay\w*\b|\bslain\b|\bhomicide\w*\b|"
                r"\bmanslaughter\b"),
    ("shooting", r"\bshoot\w*\b|\bshots?\b|\bsniper\w*\b|\bgunfight\w*\b|"
                 r"\bshootout\w*\b|\bmassacre\w*\b|\bambush\w*\b"),
    ("robbery", r"\brobber\w*\b|\bheist\w*\b|\bburglar\w*\b|\bholdup\w*\b|"
                r"\bstickup\w*\b"),
    ("prison", r"\bprison\w*\b|\bjail\w*\b|\binmate\w*\b|\bpenitentiary\w*\b|"
               r"\bexecution\w*\b|\belectric chair\b|\bdeath row\b"),
    ("arson", r"\barson\w*\b|\bburned down\b|\bburnt down\b"),
    ("vice", r"\bbrothel\w*\b|\bbordello\w*\b|\bprostitut\w*\b|\bred.?light\b|"
             r"\bspeakeasy\w*\b|\bsaloon\w*\b|\bmoonshin\w*\b|\bbootleg\w*\b"),
    ("gambling", r"\bgambl\w*\b|\bcasino\w*\b|\bslot machine\w*\b"),
    ("drugs", r"\bdrugs?\b|\bheroin\b|\bcocaine\b|\bfentanyl\b|\bmeth\w*\b|"
              r"\bopioid\w*\b|\bnarcotic\w*\b|\boverdose\w*\b"),
    ("smuggling", r"\bsmuggl\w*\b|\bmules?\b|\btraffick\w*\b"),
    ("corruption", r"\bcorrupt\w*\b|\bgraft\b|\bbribe\w*\b|\bracketeer\w*\b|"
                   r"\bmobster\w*\b|\bgangster\w*\b|\boutlaw\w*\b"),
    ("scandal", r"\bscandal\w*\b|\baffair\w*\b|\bmistress\w*\b"),
    ("haunting", r"\bhaunt\w*\b|\bghost\w*\b|\bcursed?\b|\bcurses\b|\bcryptid\w*\b|"
                 r"\bbigfoot\b|\bsasquatch\b|\bufos?\b|\bhoax\w*\b"),
    ("disaster", r"\btornado\w*\b|\bflood\w*\b|\bhurricane\w*\b|\bexplos\w*\b|"
                 r"\bcollapsed?\b|\bplague\b|\bepidemic\w*\b"),
    ("unrest", r"\bstrikes?\b|\bunion\w*\b|\briots?\b"),
    ("record", r"\brecords?\b|\bguinness\b"),
    ("only", r"\bonly\b|\bsole\b|\bunique\b|\bone of a kind\b"),
    ("first", r"\bfirst\b"),
    ("largest", r"\blargest\b|\bbiggest\b"),
    ("smallest", r"\bsmallest\b"),
    ("oldest", r"\boldest\b"),
    ("tallest", r"\btallest\b"),
    ("longest", r"\blongest\b"),
    ("deadliest", r"\bdeadliest\b"),
    ("richest", r"\brichest\b"),
)
_CLAIM_RES = [(name, re.compile(pat, re.IGNORECASE))
              for name, pat in _CLAIM_CONCEPTS]
# The subset that makes a uniqueness boast — these also need an attribution
# check, because a county-wide "only" is routinely rewritten as a town-wide one.
_UNIQUE_CLAIMS = {"only", "first", "largest", "smallest", "oldest", "tallest",
                  "longest", "deadliest", "richest", "record"}

# Words that scope a boast to a region instead of to the town. A line that
# scopes its "only/first" claim this way ("home to Trumbull County's one and
# only hanging") must be backed by a fact that names the town — otherwise a
# county or state story is being handed to the town as its own.
_SCOPE_WORDS = ({"county", "counties", "parish", "state", "province", "region",
                 "valley", "area", "district", "statewide", "countywide"}
                | {w for v in _US_STATES.values() for w in v.split() if len(w) >= 4}
                | {w for v in _CA_PROVINCES.values() for w in v.split() if len(w) >= 4}
                | {w for v in _COUNTRIES.values() for w in v.split() if len(w) >= 4})


def _claims(text: str) -> set:
    """The claim concepts a piece of text makes ("hanging", "record", "only").

    Rewording a seed fact is fine; introducing a new crime, vice, disaster,
    record or superlative is not. Matching on concepts (not raw words) means
    "hanged"/"hanging"/"noose" all count as one claim, so a rewrite that says
    "hanging" is still supported by a seed that says "hanged".
    """
    return {name for name, rx in _CLAIM_RES if rx.search(text)}


def _place_words(place: str, location: str) -> set:
    """Words that identify the town itself, for attribution checks.

    Two-letter state codes are skipped — "OH"/"IN"/"OR" would match ordinary
    English and make every line look like it names the town.
    """
    words = set(re.findall(r"[a-z]+", f"{place} {location}".lower()))
    return {w for w in words if len(w) >= 3} - _GROUNDED_STOP


def _uniqueness_ok(line: str, line_claims: set, seed_facts: list,
                   place_words: set) -> bool:
    """Every "only / first / largest" boast must be backed by a real source
    fact - and a boast that hands a region's story to the town must be backed
    by a fact about the town.

    "Girard, Ohio: home to Trumbull County's one and only hanging" names the
    town AND scopes the boast to the county, so it needs a fact that makes the
    same boast and names Girard. A line that keeps the regional framing ("In
    the area: the only man hanged in Trumbull County ...") claims nothing for
    the town, so an area fact is enough to back it.
    """
    words = set(re.findall(r"[a-z]+", line.lower()))
    # Only a boast pinned on the town itself is held to the stricter standard.
    strict = bool(words & _SCOPE_WORDS) and bool(place_words & words)
    for claim in line_claims:
        for seed in seed_facts:
            if claim not in _claims(seed):
                continue
            if strict and (seed.startswith(_AREA_PREFIX) or not (
                    place_words & set(re.findall(r"[a-z]+", seed.lower())))):
                continue
            break
        else:
            return False
    return True


# Unfalsifiable praise. The prompt forbids padding a bland fact with "made-up
# puns or cute filler", and models do it anyway: Mount Vernon, MO came back as
# "keeps its past alive through a historic downtown square and those classic
# small-town traditions everyone loves". There is no seed fact behind any of
# that, and no amount of grounding can check a compliment. Such a line is
# dropped whole - a dropped line costs nothing, the plain real facts post
# instead.
_VAGUE = re.compile(
    r"\b(?:everyone|everybody) loves\b|\bkeeps? (?:its|their) past alive\b|"
    r"\bsmall[- ]town (?:charm|traditions?|values|feel|vibe)\b|"
    r"\bclassic small[- ]town\b|\bsteeped in history\b|\brich history\b|"
    r"\bhidden gems?\b|\bmust[- ]see\b|\bworth (?:a visit|the trip)\b|"
    r"\bquaint\b|\bpic(?:ture)?[- ]perfect\b|\bstep back in time\b|"
    r"\boozes? (?:charm|character)\b|\bfull of character\b|"
    r"\bstrong sense of community\b|\btreasure trove\b|\btime capsule\b",
    re.IGNORECASE,
)


def _grounded_filter(lines: list, place: str, location: str, seed_facts: list) -> list:
    """Drop LLM lines that claim something the seed facts don't support.

    Three checks, in order of how often they fire:

      1. invented names/dates — a capitalised word or year that isn't in the
         seed facts ("Devil Jack Schramm", "in 1912");
      2. invented claims — a crime, vice, disaster, record or "only/first/
         largest" boast the seeds never make, written in lowercase so it slips
         past the name check ("a hanging party went down", "broke records as
         a drug mule");
      3. re-attributed boasts — a "the only / the first ..." claim pinned on
         the town and scoped to a region ("home to Trumbull County's one and
         only hanging") must be backed by a fact that names the town. Facts
         dug out of the county or state arrive prefixed with `_AREA_PREFIX`
         and may not be upgraded into a claim about the town.

    A dropped line costs nothing: if every line goes, `_llm_facts` returns []
    and the bot posts the plain real facts instead.
    """
    corpus = " ".join([place, location] + list(seed_facts)).lower()
    years = set(_YEAR.findall(corpus))
    tokens = set(re.findall(r"[a-z]+", corpus))
    corpus_claims = _claims(corpus)
    place_words = _place_words(place, location)

    def _ok(line: str) -> bool:
        for y in _YEAR.findall(line):
            if y not in years:
                return False
        for yy in _SHORT_YEAR.findall(line):
            if not any(y.endswith(yy) for y in years):
                return False
        for cap in _CAP_WORD.findall(line):
            w = cap.lower()
            if w in _GROUNDED_STOP:
                continue
            if w not in tokens:
                alias = _CAP_ALIASES.get(w)
                if not alias or not all(a in tokens for a in alias.split()):
                    return False
        # 1a. praise is not a fact — drop the whole line rather than post it.
        if _VAGUE.search(line):
            return False
        # 1b. a residence claim needs a seed that places someone in the town.
        if _RESIDENCE.search(line) and not _RESIDENCE.search(corpus):
            return False
        # 1c. reputation / genre labels need the same backing — "surf rock
        #     legends" and "put this sleepy spot on the musical map" are
        #     invented framing, not a rewrite of the supplied fact.
        if _REPUTATION.search(line) and not _REPUTATION.search(corpus):
            return False
        # 2. every claim the line makes must be a claim the seeds make.
        line_claims = _claims(line)
        if line_claims - corpus_claims:
            return False
        # 3. a "the only … / the first …" boast must be backed by one of the
        #    town's own facts, never by a county/state dig result — and a boast
        #    scoped to a region ("the county's one and only …") must come from
        #    a fact that names the town itself.
        if (line_claims & _UNIQUE_CLAIMS) and not _uniqueness_ok(
                line, line_claims & _UNIQUE_CLAIMS, seed_facts, place_words):
            return False
        return True

    kept = [ln for ln in lines if _ok(ln)]
    if len(kept) != len(lines):
        print(f"[funfacts] grounded filter dropped {len(lines) - len(kept)} "
              f"line(s) with invented, unsupported or padded claims", flush=True)
    return kept


def _llm_facts(place: str, location: str, seed_facts: list, options: dict) -> list:
    try:
        import llm
    except Exception as exc:
        print(f"[funfacts] llm import error: {exc!r}", flush=True)
        return []
    if not llm.is_configured(options):
        if "llm_missing" not in _log_once:
            _log_once.add("llm_missing")
            print("[funfacts] spicy mode: no LLM configured — facts will be plain "
                  "(not adult). Set llm_api_key / GROQ_API_KEY / OPENROUTER_API_KEY, "
                  "or run a local Ollama model (llm_base_url http://localhost:11434/v1).",
                  flush=True)
        return []
    try:
        if options.get("debug"):
            print(f"[funfacts] asking LLM to rewrite {len(seed_facts)} seed facts "
                  f"for {place}", flush=True)
        text = llm.rewrite_fact(place, location, seed_facts, options)
    except Exception as exc:
        print(f"[funfacts] llm error: {exc!r}", flush=True)
        return []
    if not text:
        return []
    # A bulleted reply usually opens with a chatty preamble ("Girard, Ohio's
    # got some real characters for the record books... Here's the real deal:")
    # that is not a fact at all. Once the first bullet appears, everything
    # above it is discarded so the preamble can't be posted as fact #1.
    raw_lines = text.splitlines()
    first_bullet = next((i for i, ln in enumerate(raw_lines)
                         if re.match(r"^\s*(?:\d{1,2}[.)]\s*|[-\u2022*]\s*)\S", ln)),
                        None)
    if first_bullet is not None:
        raw_lines = raw_lines[first_bullet:]
    lines = []
    for ln in raw_lines:
        ln = ln.strip().strip('"\u201c\u201d')
        # Strip "1." / "-" / "•" list prefixes and markdown the model may add.
        ln = re.sub(r"^\s*(?:\d{1,2}[.)]\s*|[-•*]\s*)", "", ln)
        ln = ln.replace("**", "").replace("`", "").strip()
        if not ln:
            continue
        # Drop chain-of-thought / meta chatter before it can reach chat.
        if _META_LINE.match(ln):
            continue
        lines.append(ln)
    kept = [ln for ln in lines if not _EXPLICIT.search(ln)
            and not _TASTELESS.search(ln)]
    if len(kept) != len(lines):
        print(f"[funfacts] dropped {len(lines) - len(kept)} explicit/tasteless "
              f"LLM line(s) for {place}", flush=True)
    lines = _grounded_filter(kept, place, location, seed_facts)
    # The model often restates the same fact twice in other words (the 09:19
    # reply said the 1786 Moravian settlement twice back to back).
    deduped, seen = [], []
    for ln in lines:
        ln_norm = " ".join(re.sub(r"[^a-z0-9 ]", "", ln.lower()).split())
        if any(_overlap(ln_norm, pn) > 0.7 for pn in seen):
            continue
        seen.append(ln_norm)
        deduped.append(ln)
    lines = deduped
    if lines:
        print(f"[funfacts] llm wrote {len(lines)} facts for {place}", flush=True)
    return lines[:10]


def _try_sources(location: str, spicy: bool, limit: int, options: dict = None):
    """Wikipedia → configured web search → DuckDuckGo. First usable pool wins.

    Order matters and is not arbitrary. Wikipedia is first because it is free
    and usually sufficient, so a paid key is only spent on towns it cannot
    serve. A configured search key is then tried *before* DuckDuckGo: Serper and
    Google return real ranked web results, while DDG's Instant Answer is a
    single Wikipedia-style blurb. It used to run last, which meant one dull
    DuckDuckGo sentence ended the ladder and the key was never consulted — for
    "Jerome, Missouri" DDG returned "It is located on the Gasconade River near
    Interstate 44" and the bot posted that instead of searching.
    """
    options = options or {}
    for name, fn in (("wikipedia", lambda q: _wikipedia(q, spicy, limit)),
                     ("serper", lambda q: _serper_search(q, spicy, limit, options)),
                     ("google", lambda q: _google_search(q, spicy, limit, options)),
                     ("duckduckgo", lambda q: _duckduckgo(q, spicy, limit))):
        try:
            result = fn(location)
        except urllib.error.URLError as exc:
            print(f"[funfacts] {name} unreachable: {exc}", flush=True)
        except Exception as exc:  # never let a bad source crash the bot
            print(f"[funfacts] {name} error: {exc!r}", flush=True)
        else:
            if result and result.get("facts"):
                if DEBUG:
                    print(f"[funfacts] source={name} place={result.get('place')!r} "
                          f"facts={len(result['facts'])}", flush=True)
                    for i, f in enumerate(result["facts"], 1):
                        print(f"[funfacts]   seed {i}: {f[:150]}", flush=True)
                return result
            if DEBUG:
                print(f"[funfacts] source={name} -> nothing usable", flush=True)
    return None


def _finalize(result, location, options, spicy, limit):
    """In spicy mode, hand the real facts to the LLM to write adult fun facts.

    If no LLM is configured (or it fails), the plain ranked facts are returned
    as-is — never with tacked-on joke comments.
    """
    if spicy:
        extra = _llm_facts(result["place"], location, result["facts"], options)
        if extra:
            result["facts"] = [_trim(f, limit) for f in extra if _trim(f, limit)][:10]
    return result


def _merge_curated(curated, result):
    """Prepend curated (hand-verified) facts to discovered ones, deduplicating."""
    if not curated:
        return result
    if not result:
        return curated
    seen = [_norm(f) for f in curated["facts"]]
    extra = []
    for f in result["facts"]:
        fn = _norm(f)
        if any(_overlap(fn, s) > 0.7 for s in seen):
            continue
        extra.append(f)
        seen.append(fn)
    return {"place": curated["place"], "facts": (curated["facts"] + extra)[:8]}


def _package(curated, result, location, options, spicy, limit):
    """LLM-rewrite the discovered facts when appropriate, then put any curated
    facts on top. Curated towns skip the LLM — their facts are already final."""
    if curated:
        return _merge_curated(curated, result)
    return _finalize(result, location, options, spicy, limit)


# Hints for the region dig — crime first (so a famous county murder/manhunt is
# found), then the weird/regional angle.
_REGION_HINTS = ["murder", "crime", "scandal", "legend", "honeymoon", "resort", "haunted", "record"]


def _region_dig(location: str, existing: list, limit: int, max_facts: int = 2) -> list:
    """When a town's own article is dry, mine its county / state for spicy or
    weird facts (e.g. the Poconos honeymoon resorts for a tiny Pike County
    town). Only sentences that actually name the county/state are kept, and
    every result is prefixed with `_AREA_PREFIX`, so a county or state story is
    never mistaken for — or rewritten as — one about the town itself."""
    geo = _osm_geocode(location)
    if not geo:
        return []
    scopes = [s for s in (geo.get("county"), geo.get("state")) if s]
    if not scopes:
        return []

    existing_norm = [_norm(f) for f in existing]
    found, found_norm = [], []
    for scope in scopes:
        if len(found) >= max_facts or _wiki_blocked():
            break
        scope_norm = _norm(scope)
        for hint in _REGION_HINTS[:5]:
            if len(found) >= max_facts or _wiki_blocked():
                break
            try:
                items = _wiki_search_extracts(f'"{scope}" {hint}',
                                              exchars=7000, limit=1)
            except Exception as exc:
                print(f"[funfacts] region dig search error: {exc!r}", flush=True)
                break
            for it in items[:1]:
                if len(found) >= max_facts:
                    break
                title = it["title"]
                tl = title.lower()
                if ("(disambiguation)" in tl or tl.startswith("list of")
                        or "category:" in tl):
                    continue
                extract = it["extract"]
                if not extract or _is_disambiguation(extract):
                    continue
                for s in _sentences(extract):
                    s_norm = _norm(s)
                    if scope_norm not in s_norm:
                        continue
                    if _score(s, spice=True) < 1:
                        continue
                    fact = _clip(s, 600)
                    if not fact:
                        continue
                    fn = _norm(fact)
                    if any(_overlap(fn, e) > 0.7 for e in existing_norm):
                        continue
                    if any(_overlap(fn, f) > 0.7 for f in found_norm):
                        continue
                    # Labelled as regional: in chat it reads honestly, and the
                    # grounding filter won't let the LLM re-attribute a county
                    # or state story to the town itself.
                    found.append(_AREA_PREFIX + fact)
                    found_norm.append(fn)
                    if len(found) >= max_facts:
                        break
    return found


def _give_up(location: str):
    """What to return when a lookup found nothing.

    If Wikipedia was recently rate-limited, this is "sources busy" — NOT a
    genuine "there are no facts" — so the bot shouldn't tell the viewer the
    place has no fun facts and shouldn't cache a 5-minute miss.
    """
    if _wiki_blocked():
        return {"place": location, "facts": [], "unavailable": True}
    return None


def _lookup_all(location: str, options: dict, spicy: bool, limit: int):
    # Curated spicy facts stay at the TOP of the pool, but the web is still
    # searched so significant / weird facts (e.g. the Lincoln Flag in Milford)
    # are discovered automatically for any town — no database entry needed.
    curated = _spicy_db(location, limit) if spicy else None

    # 2. Wikipedia / DuckDuckGo / Google text search (handles most places).
    result = _try_sources(location, spicy, limit, options)
    if result:
        if spicy:
            dig = _spicy_dig(result["place"], location, result["facts"], limit)
            if dig:
                result["facts"] = dig + result["facts"]
            if len(result["facts"]) < 3:
                rdig = _region_dig(location, result["facts"], limit)
                if rdig:
                    result["facts"] = result["facts"] + rdig
            result["facts"] = result["facts"][:8]
        return _package(curated, result, location, options, spicy, limit)

    # 3. Remote / tiny town fallback: geocode the name, retry with the
    #    canonical "Name, State", then use coordinate-based geosearch for the
    #    nearest notable place. This covers even hamlets and roadhouses that
    #    have no exact text-search hit.
    geo = _osm_geocode(location)
    if not geo:
        return curated or _give_up(location)

    canonical = ", ".join(x for x in (geo["name"], geo["state"] or geo["country"]) if x)
    if canonical and _norm(canonical) != _norm(location):
        result = _try_sources(canonical, spicy, limit, options)
        if result:
            if spicy:
                rdig = _region_dig(location, result["facts"], limit)
                if rdig:
                    result["facts"] = (result["facts"] + rdig)[:8]
            return _package(curated, result, location, options, spicy, limit)

    nearby = _wiki_geosearch(geo["lat"], geo["lon"])
    if not nearby:
        if spicy:
            rdig = _region_dig(location, [], limit)
            if rdig:
                result = {"place": canonical or location, "facts": rdig}
                return _package(curated, result, location, options, spicy, limit)
        return curated or _give_up(location)

    target = _title_tokens(geo["name"]) if geo["name"] else ""
    ordered = sorted(
        nearby,
        key=lambda n: (not (target and target in _title_tokens(n["title"])),
                       n.get("dist", 1e9)),
    )
    for item in ordered:
        title = item["title"]
        extract = item.get("extract", "")
        if not extract or _is_disambiguation(extract):
            continue
        facts = _ranked_facts(_filter_definitions(_sentences(extract)),
                              spice=spicy, limit=limit)
        if not facts:
            continue
        if target and target in _title_tokens(title):
            return _package(curated, {"place": title, "facts": facts},
                            location, options, spicy, limit)
        # Nearest notable place, framed relative to the town.
        prefixed = [f"Just outside town: {f}" for f in facts]
        return _package(curated,
                        {"place": canonical or location, "facts": prefixed},
                        canonical or location, options, spicy, limit)
    # Last resort in spicy mode: dig the county/state before giving up.
    if spicy:
        rdig = _region_dig(location, [], limit)
        if rdig:
            return _package(curated, {"place": canonical or location, "facts": rdig},
                            location, options, spicy, limit)
    return curated or _give_up(location)


# ---- public API -------------------------------------------------------------

def _norm_opts(options):
    o = options or {}
    spice = str(o.get("spice") or "").strip().lower()
    spicy = spice in ("spicy", "adult", "r", "on", "true", "1", "yes")
    # "fact_source": "llm" skips retrieval entirely and asks the model for
    # facts from its own knowledge. See _llm_only_facts for the trade-off.
    source = str(o.get("fact_source") or o.get("source") or "").strip().lower()
    llm_only = source in ("llm", "llm-only", "llmonly", "model", "ai")
    try:
        limit = int(o.get("max_fact_chars") or 200)
    except (TypeError, ValueError):
        limit = 200
    limit = max(80, min(limit, 480))
    return spicy, limit, o, llm_only


def _llm_only_facts(location: str, limit: int, opts: dict) -> list:
    """Facts straight from the model's own knowledge — no sources consulted.

    This is the simple mode: one prompt, no Wikipedia, no search, no ranking.
    It is also the mode with no safety net: the grounded filter compares a line
    against the facts that were found, and here nothing was found, so there is
    nothing to compare against. Only the explicit/taste filters and the
    character limit still apply. That is why it is opt-in.
    """
    try:
        import llm
    except Exception as exc:
        print(f"[funfacts] llm import error: {exc!r}", flush=True)
        return []
    if not llm.is_configured(opts):
        print("[funfacts] fact_source='llm' but no LLM is configured — set "
              "llm_api_key / GROQ_API_KEY / OPENROUTER_API_KEY, or a local "
              "Ollama (llm_base_url http://localhost:11434/v1).", flush=True)
        return []
    try:
        text = llm.freeform_facts(location, location, opts)
    except Exception as exc:
        print(f"[funfacts] llm error: {exc!r}", flush=True)
        return []
    if not text or "NOTHING RELIABLE" in text.upper():
        return []
    lines = []
    for ln in text.splitlines():
        ln = ln.strip().strip('"\u201c\u201d')
        ln = re.sub(r"^\s*(?:\d{1,2}[.)]\s*|[-\u2022*]\s*)", "", ln)
        ln = ln.replace("**", "").replace("`", "").strip()
        if not ln or _META_LINE.match(ln):
            continue
        if _EXPLICIT.search(ln) or _TASTELESS.search(ln):
            continue
        fact = _trim(ln, limit)
        if fact:
            lines.append(fact)
    if lines:
        print(f"[funfacts] llm-only wrote {len(lines)} facts for {location} "
              f"(unsourced)", flush=True)
    return lines[:10]


def get_funfact(location: str, options=None):
    """Look up a fun fact for `location`.

    Repeated calls for the same location return *different* facts (rotating
    through the ones found), until they've all been shown, then reshuffles.

    `options` may include: spice ("clean"/"spicy"), max_fact_chars (int),
    llm_api_key, llm_base_url, llm_model.
    """
    spicy, limit, opts, llm_only = _norm_opts(options)
    key = (("llm:" if llm_only else "spicy:" if spicy else "clean:")
           + " ".join(location.strip().lower().split()))
    now = time.time()

    with _cache_lock:
        entry = _cache.get(key)
        if entry and now - entry["t"] >= entry["ttl"]:
            entry = None
        if entry is None:
            _cache.pop(key, None)

    if entry is None:
        result = None
        if llm_only:
            facts = _llm_only_facts(location.strip(), limit, opts)
            if facts:
                result = {"place": location.strip(), "facts": facts}
            else:
                # The model had nothing reliable: fall back to real sources
                # rather than tell the viewer the place has no facts.
                result = _lookup_all(location.strip(), opts, spicy, limit)
        if result is None:
            result = _lookup_all(location.strip(), opts, spicy, limit)
        with _cache_lock:
            if result and result.get("unavailable"):
                # Sources are rate-limited right now — retry soon instead of
                # claiming the place has no facts.
                entry = {"place": result.get("place") or location, "facts": [],
                         "shown": 0, "t": now, "ttl": _BUSY_TTL, "busy": True}
            elif result and result.get("facts"):
                # facts arrive ranked best-first; show facts[0] on the first
                # call so the reply matches the place's most famous story.
                entry = {"place": result["place"], "facts": list(result["facts"]),
                         "shown": 0, "t": now, "ttl": _HIT_TTL}
            else:
                entry = {"place": None, "facts": [], "shown": 0,
                         "t": now, "ttl": _MISS_TTL}
            _cache[key] = entry
            if len(_cache) > 500:  # keep memory bounded
                oldest = sorted(_cache, key=lambda k: _cache[k]["t"])[:100]
                for old in oldest:
                    _cache.pop(old, None)

    with _cache_lock:
        if entry.get("busy"):
            return {"busy": True, "place": entry.get("place") or location}
        facts = entry["facts"]
        if not facts:
            return None
        place = entry["place"]
        shown = entry.get("shown", 0)
        if shown == 0:
            # First answer: the top-ranked fact, so the reply matches the
            # place's most famous story (e.g. the Frein ambush for Blooming
            # Grove) instead of a random lesser one.
            fact = facts[0]
        else:
            # Later answers: a random fact, never the one just shown.
            last = entry.get("last")
            choices = [f for f in facts if f != last] or facts
            fact = random.choice(choices)
        entry["shown"] = shown + 1
        entry["last"] = fact

    return {"place": place, "fact": _fit_fact(fact, limit, opts)}


if __name__ == "__main__":
    import os
    import sys

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    spicy = "--spicy" in sys.argv
    debug = "--debug" in sys.argv or os.environ.get("TWITCH_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
    opts = {
        "spice": "spicy" if spicy else "clean",
        "max_fact_chars": 200,
        "debug": debug,
        # Reuse any AI setup present in the environment, so --debug shows the
        # real prompts the bot would send.
        "llm_api_key": os.environ.get("GROQ_API_KEY", "").strip() or os.environ.get("OPENROUTER_API_KEY", "").strip(),
        "llm_base_url": os.environ.get("GROQ_API_BASE", "").strip() or os.environ.get("OPENROUTER_API_BASE", "").strip() or "https://api.groq.com/openai/v1",
        "llm_model": os.environ.get("GROQ_MODEL", "").strip() or os.environ.get("OPENROUTER_MODEL", "").strip(),
    }
    for q in args or ["Milford, PA"]:
        print(f"\nQ: {q!r} ({opts['spice']})")
        r = get_funfact(q, opts)
        if r:
            print(f"  place: {r['place']}")
            print(f"  fact : {r['fact']}  ({len(r['fact'])} chars)")
        else:
            print("  (no result)")
