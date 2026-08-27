"""!whois <name> — a short, sourced blurb about a person.

Two sources, neither of them a model. Wikipedia's lead for the person, and -
because this is a Twitch chat and most names typed into it are streamers - the
Twitch profile for that login. The whole point of the command is that what it
says can be checked, so both are the source's own words and the only thing done
to them is cutting them down to fit chat.

Two calls, at most:

    GET /api/rest_v1/page/summary/<Title>      the article, if the name matches
    GET /w/api.php?action=query&list=search=…  otherwise, find the best title

A disambiguation page is not an answer - "John Smith" is forty people - so
that is reported instead of posting whichever one Wikipedia happened to list.

The Twitch half is optional and duck-typed: pass anything with a
`channel_profile(login)` method (access.Helix has one) and it is consulted,
pass nothing and the command is Wikipedia-only. That keeps this module free of
a Helix dependency and testable without one.
"""

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import funfacts

REST = "https://en.wikipedia.org/api/rest_v1/page/summary/"
API = "https://en.wikipedia.org/w/api.php"

USER_AGENT = "ClawFacts/1.0 (Twitch bot; !whois lookups)"

HIT_TTL = 21600.0     # 6 hours
MISS_TTL = 900.0      # don't hammer Wikipedia for a name that isn't there
DEFAULT_MAX_CHARS = 400
MIN_QUERY = 2
MAX_QUERY = 80

_cache = {}
_cache_lock = threading.Lock()

_TITLE_OK = re.compile(r"^[^\[\]{}<>|#]*$")


class WhoisError(Exception):
    """The lookup could not be settled - as opposed to 'no such person'."""


def _get(url: str, timeout: float = 6.0):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _clean(text: str) -> str:
    """Wikipedia's extract, minus the markup that does not survive chat."""
    text = re.sub(r"\[[0-9]+\]", "", text or "")
    text = re.sub(r"\((?:[^()]|\([^()]*\))*\)", lambda m: m.group(0), text)
    return " ".join(text.split())


def _summary_for(title: str, timeout: float):
    """The REST summary for an exact title, or None if there is no such page."""
    quoted = urllib.parse.quote(title.replace(" ", "_"), safe="")
    try:
        data = _get(REST + quoted, timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise WhoisError(f"Wikipedia returned HTTP {exc.code}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise WhoisError(f"could not reach Wikipedia ({exc!r})")
    if not isinstance(data, dict):
        return None
    return data


def _search_title(query: str, timeout: float):
    """The best-matching article title for a query, or None."""
    qs = urllib.parse.urlencode({
        "action": "query", "list": "search", "srsearch": query,
        "srlimit": "1", "format": "json", "formatversion": "2",
        "srnamespace": "0",
    })
    try:
        data = _get(f"{API}?{qs}", timeout)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError,
            ValueError) as exc:
        raise WhoisError(f"could not reach Wikipedia ({exc!r})")
    hits = ((data.get("query") or {}).get("search")) or []
    if not hits:
        return None
    return hits[0].get("title") or None


def _joined(iso: str) -> str:
    """'2019-03-04T...' -> 'Mar 2019'."""
    try:
        return time.strftime("%b %Y", time.strptime((iso or "")[:7], "%Y-%m"))
    except (ValueError, TypeError):
        return ""


def format_twitch(profile: dict) -> str:
    """The Twitch line: what they are, how big, how long, and their bio."""
    parts = []
    kind = (profile.get("broadcaster_type") or "").lower()
    if kind == "partner":
        parts.append("Twitch Partner")
    elif kind == "affiliate":
        parts.append("Twitch Affiliate")
    else:
        parts.append("on Twitch")
    if profile.get("followers") is not None:
        parts.append(f"{int(profile['followers']):,} followers")
    joined = _joined(profile.get("created_at") or "")
    if joined:
        parts.append(f"joined {joined}")
    line = ", ".join(parts)
    bio = (profile.get("bio") or "").strip()
    if bio:
        line += f'. "{bio}"'
    return line


def _twitch_profile(query: str, helix):
    """The Twitch profile for `query`, or None.

    The login is matched exactly as typed, spaces and all. Squashing the
    spaces to catch "Aubrey Plaza" as "aubreyplaza" was tried and removed: it
    happily matched whatever account happened to hold that login and titled
    the answer after it, which is a claim about a stranger wearing a real
    person's name. A two-word name simply is not a Twitch login - Twitch does
    not allow spaces in them - so the honest answer is that Twitch has no one
    by that name and Wikipedia carries it instead.
    """
    if helix is None:
        return None
    login = query.strip().lstrip("#").lower()
    # Twitch logins are 4-25 characters of [a-z0-9_]. Anything else cannot be
    # one, so do not spend an API call finding that out.
    if not login or not 4 <= len(login) <= 25 or " " in login \
            or not re.match(r"^[a-z0-9_]+$", login):
        return None
    try:
        return helix.channel_profile(login)
    except Exception as exc:          # a broken Helix must not sink the lookup
        print(f"[whois] twitch lookup failed: {exc!r}", flush=True)
        return None


def _wikipedia(query: str, max_chars: int, timeout: float):
    """The Wikipedia half. (dict, None) on a hit, (None, reason) otherwise."""
    page = _summary_for(query, timeout)
    if page is None:
        # The name as typed is not a page title. Wikipedia's own search is
        # better at "Aubrey Plaza" than guessing the capitalisation is.
        title = _search_title(query, timeout)
        if title:
            page = _summary_for(title, timeout)
    if page is None:
        return None, "no Wikipedia page"
    if page.get("type") == "disambiguation":
        return None, "disambiguation"
    extract = _clean(page.get("extract") or "")
    if not extract:
        return None, "no summary"
    return {"title": page.get("title") or query,
            "description": (page.get("description") or "").strip(),
            # Cut on a sentence boundary, never mid-phrase - the same rule the
            # fact engine uses.
            "text": funfacts.trim_to_fit(extract, max(80, int(max_chars)))}, None


def lookup(query: str, max_chars: int = DEFAULT_MAX_CHARS,
           timeout: float = 6.0, now: float | None = None, helix=None):
    """Find who `query` is, from Twitch and Wikipedia.

    Returns a dict with "found", plus "twitch" and/or "wiki" when either
    source has them, or "reason" when neither does.

    Raises WhoisError when Wikipedia could not be reached *and* Twitch has
    nothing either, so the caller can say "try again" instead of claiming the
    person does not exist.
    """
    query = " ".join((query or "").split())[:MAX_QUERY]
    if len(query) < MIN_QUERY:
        return {"found": False, "reason": "who should I look up?"}

    now = time.time() if now is None else now
    # Whether Twitch was consulted changes the answer, so it is part of the
    # cache key - otherwise a Wikipedia-only result gets served to a caller
    # that could have had the streamer's profile too.
    key = query.lower() + ("|tw" if helix is not None else "")
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit["t"] < hit["ttl"]:
            return dict(hit["v"])
        _cache.pop(key, None)

    twitch = _twitch_profile(query, helix)

    wiki_error = False
    try:
        wiki, why = _wikipedia(query, max_chars, timeout)
    except WhoisError:
        wiki, why = None, "unreachable"
        wiki_error = True

    if twitch is None and wiki is None:
        if wiki_error:
            # Do not cache, and do not report "no such person": Wikipedia being
            # down is not evidence that someone does not exist.
            raise WhoisError("could not reach Wikipedia")
        # Each failure needs a different answer: "nobody by that name" and
        # "forty people share that name" send the viewer in different
        # directions, and so does "the page exists but is empty".
        if why == "disambiguation":
            reason = f"several people are called {query} - be more specific"
        elif why == "no summary":
            reason = f"Wikipedia has a page for {query} but no summary"
        else:
            reason = f"I couldn't find anyone called {query}"
        result = {"found": False, "reason": reason}
        _store(key, result, MISS_TTL, now)
        return result

    result = {"found": True, "twitch": twitch, "wiki": wiki,
              # Twitch wins the title when it has them: in a Twitch chat, a
              # name that is a real login is almost certainly that streamer.
              "title": (twitch or {}).get("display_name")
                       or (wiki or {}).get("title") or query,
              "description": (wiki or {}).get("description", ""),
              "text": (wiki or {}).get("text", "")}
    _store(key, result, HIT_TTL, now)
    return result


def _store(key: str, value: dict, ttl: float, now: float) -> None:
    with _cache_lock:
        _cache[key] = {"v": dict(value), "t": now, "ttl": ttl}
        if len(_cache) > 300:
            for old in sorted(_cache, key=lambda k: _cache[k]["t"])[:60]:
                _cache.pop(old, None)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
