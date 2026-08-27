"""!whois <name> — a short, sourced blurb about a person.

Reads the lead of the person's Wikipedia article and posts it, trimmed on a
sentence boundary. Nothing here is written by a model: the whole point of the
command is that what it says can be checked, so the text is Wikipedia's own
words and the only thing done to it is cutting it down to fit chat.

Two calls, at most:

    GET /api/rest_v1/page/summary/<Title>      the article, if the name matches
    GET /w/api.php?action=query&list=search=…  otherwise, find the best title

A disambiguation page is not an answer - "John Smith" is forty people - so
that is reported instead of posting whichever one Wikipedia happened to list.
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


def lookup(query: str, max_chars: int = DEFAULT_MAX_CHARS,
           timeout: float = 6.0, now: float | None = None):
    """Find who `query` is.

    Returns a dict:
      found  -> {"found": True, "title", "text", "description"}
      no one -> {"found": False, "reason": "..."}
    Raises WhoisError when Wikipedia could not be reached, so the caller can
    say "try again" instead of claiming the person does not exist.
    """
    query = " ".join((query or "").split())[:MAX_QUERY]
    if len(query) < MIN_QUERY:
        return {"found": False, "reason": "who should I look up?"}

    now = time.time() if now is None else now
    key = query.lower()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit["t"] < hit["ttl"]:
            return dict(hit["v"])
        _cache.pop(key, None)

    page = _summary_for(query, timeout)
    if page is None:
        # The name as typed is not a page title. Wikipedia's own search is
        # better at "Aubrey Plaza" than guessing the capitalisation is.
        title = _search_title(query, timeout)
        if title:
            page = _summary_for(title, timeout)
    if page is None:
        result = {"found": False,
                  "reason": f"I couldn't find anyone called {query}"}
        _store(key, result, MISS_TTL, now)
        return result

    if page.get("type") == "disambiguation":
        result = {"found": False,
                  "reason": f"several people are called {query} - be more "
                            f"specific"}
        _store(key, result, MISS_TTL, now)
        return result

    extract = _clean(page.get("extract") or "")
    if not extract:
        result = {"found": False,
                  "reason": f"Wikipedia has a page for {query} but no summary"}
        _store(key, result, MISS_TTL, now)
        return result

    # Cut on a sentence boundary, never mid-phrase - the same rule the fact
    # engine uses.
    text = funfacts.trim_to_fit(extract, max(80, int(max_chars)))
    result = {"found": True,
              "title": page.get("title") or query,
              "description": (page.get("description") or "").strip(),
              "text": text}
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
