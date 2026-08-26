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

import json
import os
import random
import re
import socket
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
    r"\b(oldest|youngest|first|second|largest|smallest|tallest|longest|"
    r"shortest|deepest|highest|lowest|only|last|birthplace|famous|"
    r"known for|best known|home of|named after|named for|world|"
    r"national|record|haunted|legend|rare|unique|"
    r"truck stops?|interstates?|highways?|railroads?|railways?|junctions?|"
    r"crossroads|bridges?|tunnels?|turnpikes?|freeways?|freight|"
    r"mile markers?|rest stops?|route 66|museum|landmark|monument|memorial|"
    r"president|civil war|battle|national register|artifact|relic|"
    r"takes its name|takes their name|named for|namesake|eponym|eponymous|"
    r"philanthropist|billionaire|richest)\b",
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
        if _is_filler(s):
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


def _query_core(query: str) -> str:
    """The place name with the region (after a comma) removed: 'Mount Cobb'."""
    core = re.split(r"[,;|]", query, maxsplit=1)[0]
    core = re.sub(r"[^a-z0-9\s]", " ", core.lower())
    return " ".join(core.split())


def _query_region(query: str) -> str:
    """The region part after the first comma, if any: 'PA', 'Iowa', ..."""
    parts = re.split(r"[,;|]", query, maxsplit=1)
    if len(parts) < 2:
        return ""
    return re.sub(r"[^a-z0-9\s]", " ", parts[1].lower()).strip()


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
    title that actually points at another state's place."""
    if not region:
        return False
    t = _title_tokens(text)
    requested = {region, _US_STATES.get(region, ""), _CA_PROVINCES.get(region, ""),
                 _COUNTRIES.get(region, "")}
    requested.discard("")
    for name in set(_US_STATES.values()) | set(_CA_PROVINCES.values()) | set(_COUNTRIES.values()):
        if name in t and name not in requested:
            return True
    return False


def _is_road_or_meta_title(title: str) -> bool:
    """Road/highway/route and meta pages are never the fun fact for a town."""
    t = title.lower()
    if t.startswith(("list of", "category:", "template:", "wikipedia:", "portal:", "file:")):
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
    # Fetch the start of the article (lead + beginning of History etc.),
    # not just the intro — the fun facts usually live a little deeper.
    try:
        data = _http_get_json(
            WIKI_API,
            {
                "action": "query",
                "prop": "extracts",
                "explaintext": 1,
                "exchars": exchars,
                "titles": title,
                "redirects": 1,
                "format": "json",
                "formatversion": "2",
            },
        )
    except (urllib.error.URLError, OSError, ValueError):
        return ""
    pages = data.get("query", {}).get("pages", [])
    if not pages:
        return ""
    return pages[0].get("extract", "") or ""


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
            if rel >= 70:
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
    pool, pool_norm = [], []

    def harvest(titles):
        for title in titles:
            extract = extracts.get(title, "")
            if not extract or _is_disambiguation(extract):
                continue
            # A bare redirect title may point at another state's place — check
            # the article's opening statement names the requested region.
            if _text_names_other_region(extract[:250], region):
                continue
            facts = _ranked_facts(_filter_definitions(_sentences(extract)),
                                  spice=spice, limit=limit, count=8)
            for f in facts:
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
        harvest(other_titles[:3])

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
        return None

    q = query
    if spice:
        q = f"{query} history crime scandal"  # nudge toward the racier results

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
        return None

    q = f"{query} history crime scandal" if spice else query
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
    for entry in _load_spicy_db():
        keys = [_norm(k) for k in entry.get("keys", [])]
        if full in keys or core in keys:
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

    existing_norm = [" ".join(re.sub(r"[^a-z0-9 ]", "", f.lower()).split()) for f in existing]
    core_title = _title_tokens(place_title)
    found = []

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
            for s in _sentences(extract):
                s_norm = " ".join(re.sub(r"[^a-z0-9 ]", " ", s.lower()).split())
                if core not in s_norm:
                    continue
                if _score(s, spice=True) < 1:
                    continue
                fact = _clip(s, 600)
                if not fact:
                    continue
                fn = " ".join(re.sub(r"[^a-z0-9 ]", "", fact.lower()).split())
                if any(_overlap(fn, e) > 0.7 for e in existing_norm):
                    continue
                if any(_overlap(fn, f) > 0.7 for f in found):
                    continue
                found.append(fact)
                if len(found) >= max_facts:
                    break
    return found


def _wiki_extracts(titles: list, exchars: int = 4000) -> dict:
    """Fetch the text of several articles in ONE request (title -> extract)."""
    if not titles or _wiki_blocked():
        return {}
    try:
        data = _http_get_json(
            WIKI_API,
            {
                "action": "query",
                "titles": "|".join(titles),
                "prop": "extracts",
                "explaintext": 1,
                "exchars": exchars,
                "exlimit": "max",
                "format": "json",
                "formatversion": "2",
            },
        )
    except (urllib.error.URLError, OSError, ValueError):
        return {}
    out = {}
    for page in data.get("query", {}).get("pages", []):
        t = page.get("title", "")
        if t:
            out[t] = page.get("extract", "") or ""
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
    r"\b(hanging|lynching|noose|gallows|murder|massacre)\b",
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
_CAP_WORD = re.compile(r"\b[A-Z][a-z]{2,}\b")
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


def _claims(text: str) -> set:
    """The claim concepts a piece of text makes ("hanging", "record", "only").

    Rewording a seed fact is fine; introducing a new crime, vice, disaster,
    record or superlative is not. Matching on concepts (not raw words) means
    "hanged"/"hanging"/"noose" all count as one claim, so a rewrite that says
    "hanging" is still supported by a seed that says "hanged".
    """
    return {name for name, rx in _CLAIM_RES if rx.search(text)}


def _uniqueness_ok(line_claims: set, town_seeds: list) -> bool:
    """Every "only/first/largest …" boast must come from the town's own facts.

    A county- or state-level "only" (the kind `_region_dig` returns) cannot be
    borrowed by the town — that is how a prison in Leavittsburg became "the
    only place in Trumbull County where a hanging party went down".
    """
    for claim in line_claims:
        if not any(claim in _claims(seed) for seed in town_seeds):
            return False
    return True


def _grounded_filter(lines: list, place: str, location: str, seed_facts: list) -> list:
    """Drop LLM lines that claim something the seed facts don't support.

    Three checks, in order of how often they fire:

      1. invented names/dates — a capitalised word or year that isn't in the
         seed facts ("Devil Jack Schramm", "in 1912");
      2. invented claims — a crime, vice, disaster, record or "only/first/
         largest" boast the seeds never make, written in lowercase so it slips
         past the name check ("a hanging party went down", "broke records as
         a drug mule");
      3. re-attributed boasts — a uniqueness claim about the town that only a
         county/state-level seed supports. Regional seeds arrive prefixed with
         `_AREA_PREFIX` and may not be upgraded into a claim about the town.

    A dropped line costs nothing: if every line goes, `_llm_facts` returns []
    and the bot posts the plain real facts instead.
    """
    corpus = " ".join([place, location] + list(seed_facts)).lower()
    years = set(_YEAR.findall(corpus))
    tokens = set(re.findall(r"[a-z]+", corpus))
    corpus_claims = _claims(corpus)
    # Only the town's own facts can back a boast about the town; regional
    # (county/state) dig results are prefixed and stay regional.
    town_seeds = [s for s in seed_facts if not s.startswith(_AREA_PREFIX)]

    def _ok(line: str) -> bool:
        for y in _YEAR.findall(line):
            if y not in years:
                return False
        for cap in _CAP_WORD.findall(line):
            w = cap.lower()
            if w in _GROUNDED_STOP:
                continue
            if w not in tokens:
                return False
        # 2. every claim the line makes must be a claim the seeds make.
        line_claims = _claims(line)
        if line_claims - corpus_claims:
            return False
        # 3. a "the only … / the first …" boast must be backed by one of the
        #    town's own facts, never by a county/state dig result.
        if (line_claims & _UNIQUE_CLAIMS) and not _uniqueness_ok(
                line_claims & _UNIQUE_CLAIMS, town_seeds):
            return False
        return True

    kept = [ln for ln in lines if _ok(ln)]
    if len(kept) != len(lines):
        print(f"[funfacts] grounded filter dropped {len(lines) - len(kept)} "
              f"line(s) with invented or unsupported claims", flush=True)
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
    lines = []
    for ln in text.splitlines():
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
    if lines:
        print(f"[funfacts] llm wrote {len(lines)} facts for {place}", flush=True)
    return lines[:10]


def _try_sources(location: str, spicy: bool, limit: int, options: dict = None):
    """Wikipedia → DuckDuckGo → Google (if configured). Returns result or None."""
    options = options or {}
    for name, fn in (("wikipedia", lambda q: _wikipedia(q, spicy, limit)),
                     ("duckduckgo", lambda q: _duckduckgo(q, spicy, limit)),
                     ("google", lambda q: _google_search(q, spicy, limit, options)),
                     ("serper", lambda q: _serper_search(q, spicy, limit, options))):
        try:
            result = fn(location)
        except urllib.error.URLError as exc:
            print(f"[funfacts] {name} unreachable: {exc}", flush=True)
        except Exception as exc:  # never let a bad source crash the bot
            print(f"[funfacts] {name} error: {exc!r}", flush=True)
        else:
            if result and result.get("facts"):
                return result
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
    try:
        limit = int(o.get("max_fact_chars") or 200)
    except (TypeError, ValueError):
        limit = 200
    limit = max(80, min(limit, 480))
    return spicy, limit, o


def get_funfact(location: str, options=None):
    """Look up a fun fact for `location`.

    Repeated calls for the same location return *different* facts (rotating
    through the ones found), until they've all been shown, then reshuffles.

    `options` may include: spice ("clean"/"spicy"), max_fact_chars (int),
    llm_api_key, llm_base_url, llm_model.
    """
    spicy, limit, opts = _norm_opts(options)
    key = ("spicy:" if spicy else "clean:") + " ".join(location.strip().lower().split())
    now = time.time()

    with _cache_lock:
        entry = _cache.get(key)
        if entry and now - entry["t"] >= entry["ttl"]:
            entry = None
        if entry is None:
            _cache.pop(key, None)

    if entry is None:
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
