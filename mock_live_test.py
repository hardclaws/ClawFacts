"""End-to-end replay of a real lookup, using recorded API responses.

A sandbox without outbound HTTPS to Wikipedia can't run
`python3 funfacts.py "girard, OH"` for real — but that is exactly the path
that produced the fabricated Girard facts, so it needs to be testable offline.

`fixtures/wiki_girard.json` holds the MediaWiki responses recorded for that
query on 2026-08-27. This module replays them through the *unmodified* lookup
path — `_http_get_json` is the only thing replaced — so `_wiki_search_extracts`,
`_wikipedia`, `_ranked_facts`, the spicy/region digs and `get_funfact` all run
for real.

Run:  python3 mock_live_test.py
"""

import json
import os

import funfacts

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "wiki_girard.json")


class _Replay:
    """Stand in for the network, answering from the recorded fixture."""

    def __init__(self, path=FIXTURE):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.search = data["search"]
        self.pages = list(data["extract_pages"])
        self.requests = []

    def __call__(self, url, params, timeout=8.0):
        self.requests.append((url, dict(params)))
        if params.get("list") == "search":
            key = " ".join(str(params.get("srsearch", "")).split()).lower()
            return self.search.get(key, {"query": {"search": []}})
        if params.get("prop") == "extracts":
            # The API's own sequence: one page + a continue token, then the
            # next page. When the fixture runs out, behave like "no more".
            return self.pages.pop(0) if self.pages else {"query": {"pages": []}}
        if url.startswith(funfacts.OSM_API):
            return []          # no geocode recorded -> region dig finds nothing
        return {}

    def queries(self):
        return [p.get("srsearch") for _, p in self.requests if p.get("srsearch")]


def _run(fn, *a, **kw):
    """Call `fn` with the network replaced by the replay."""
    funfacts._cache.clear()
    funfacts._wiki_blocked_until = 0.0
    net = _Replay()
    orig = funfacts._http_get_json
    funfacts._http_get_json = net
    try:
        return fn(*a, **kw), net
    finally:
        funfacts._http_get_json = orig


def test_extracts_follow_continue():
    """A batched plain-text extract request returns ONE page.

    The recorded response carries the API's own warning ('"exlimit" was too
    large ... lowered to 1') and a continue token. Reading it once — what the
    bot used to do — yields a single article, so every *related* article that
    holds a town's real claim to fame came back empty.
    """
    got, net = _run(funfacts._wiki_extracts,
                    ["Girard, Ohio", "Stephen Girard", "Joe Girard"])
    assert "Girard, Ohio" in got, got
    assert "Stephen Girard" in got, got          # only reachable via `excontinue`
    assert len(got) >= 2, got
    assert not net.pages, "fixture pages left unreplayed"
    print(f"[PASS] extracts follow excontinue -> {sorted(got)}")


def test_end_to_end_clean():
    """The whole `!funfact girard, OH` lookup, offline."""
    r, net = _run(funfacts.get_funfact, "girard, OH", {"spice": "clean"})
    assert r and r.get("fact"), r
    assert r["place"] == "Girard, Ohio", r
    assert "Stephen Girard" in r["fact"] or "1800" in r["fact"], r
    # Nothing from a crime-stats page or a police story may leak in.
    assert "crime" not in r["fact"].lower(), r
    assert net.queries(), "no Wikipedia search was made"
    print(f"[PASS] end-to-end clean -> {r['place']}: {r['fact']}")


def test_end_to_end_spicy_drops_invention():
    """Spicy mode, same lookup, with a model that invents: the fabricated line
    must never be what reaches chat — the real facts are posted instead."""
    import llm

    fake = ("Girard's no choir boy - it's the only place in Trumbull County "
            "where a hanging party went down.")
    orig_rw, orig_cfg = llm.rewrite_fact, llm.is_configured

    def go():
        return funfacts.get_funfact("girard, OH", {"spice": "spicy"})

    try:
        llm.is_configured = lambda o: True
        llm.rewrite_fact = lambda p, l, s, o: fake
        r, _ = _run(go)
        assert r and r.get("fact"), r
        assert "hanging" not in r["fact"].lower(), r
        assert "Trumbull County's one and only" not in r["fact"], r
        print(f"[PASS] end-to-end spicy -> invented line dropped, posted: "
              f"{r['fact']}")
    finally:
        llm.rewrite_fact, llm.is_configured = orig_rw, orig_cfg


def main():
    test_extracts_follow_continue()
    test_end_to_end_clean()
    test_end_to_end_spicy_drops_invention()
    print("\nALL PASSED ✔")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
