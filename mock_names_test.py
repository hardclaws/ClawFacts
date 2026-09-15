"""Tests for names.py - the !smk name pool.

The bug being fixed here is a small one with a big effect: a 44-name pool
handed out the same faces every night. These tests are about the properties
that make a big pool actually behave like a big pool.

Nothing reaches the network. The harvesting is exercised against a local fake
that returns the real Wikipedia categorymembers shape, including the junk that
shape contains - because a category listing is not a list of people, and
posting "List of American film actresses" into chat as someone to marry is
exactly the failure this module exists to prevent.
"""

import http.server
import json
import os
import tempfile
import threading
import time

import names


def _pool(**kw):
    """A pool on a throwaway path, so tests never touch a real names.json."""
    return names.NamePool(path=os.path.join(tempfile.mkdtemp(), "names.json"),
                          **kw)


# ---- the seed pool --------------------------------------------------------
def test_seed_pool_is_big_enough():
    p = _pool()
    counts = p.counts()
    # The old pool was 22 + 22 = 44. Anything near that is the bug, not a fix.
    assert counts["seed"] > 300, counts
    assert counts["female"] > 100, counts
    assert counts["male"] > 100, counts
    print(f"[PASS] seed pool: {counts['female']}f + {counts['male']}m "
          f"= {counts['seed']} names (was 44)")


def test_every_seed_entry_has_a_job():
    """A name with no occupation makes the round unplayable for anyone who
    does not already know the person."""
    for gender, rows in (("female", names.SEED_FEMALE),
                         ("male", names.SEED_MALE)):
        for entry in rows:
            assert isinstance(entry, tuple) and len(entry) == 2, entry
            name, job = entry
            assert name.strip(), entry
            assert job.strip(), f"{name} has no occupation"
    print("[PASS] every seed name carries an occupation")


def test_seed_pool_has_no_duplicates():
    for gender in ("female", "male"):
        rows = getattr(names, f"SEED_{gender.upper()}")
        lowered = [n.lower() for n, _ in rows]
        dupes = {n for n in lowered if lowered.count(n) > 1}
        assert not dupes, f"{gender}: {sorted(dupes)}"
    both = [n.lower() for n, _ in names.SEED_FEMALE + names.SEED_MALE]
    overlap = {n for n in both if both.count(n) > 1}
    assert not overlap, f"in both pools: {sorted(overlap)}"
    print("[PASS] no name appears twice, in a pool or across both")


# ---- filtering ------------------------------------------------------------
def test_category_junk_is_not_a_person():
    """Real contents of Category:American film actresses, verified against the
    API. The first one is genuinely in that category."""
    junk = [
        "List of American film actresses",
        "Academy Award for Best Actress",
        "Template:American actresses",
        "Category:American film actresses",
        "Jane Doe (actress)",          # namesake: picking one is a coin flip
        "Mary Jane 2",                 # a digit means it is not a person
        "lowercase name",
        "",
        "A" * 90,
        "John Smith (born 1955)",
        "National Film Awards",
        "Venice Film Festival",
        "The Godfather",
    ]
    for title in junk:
        assert names.person_name(title) is None, f"accepted {title!r}"
    print("[PASS] list pages, awards, namesakes and templates are rejected")


def test_real_people_survive_the_filter():
    """The filter must not be so eager that it empties the pool."""
    good = ["Emma Stone", "Aaliyah", "Daniel Day-Lewis", "Saoirse Ronan",
            "Timothée Chalamet", "Penélope Cruz", "Michael B. Jordan",
            "St. Vincent", "Björk", "Zoë Kravitz", "John Smith Jr."]
    for title in good:
        assert names.person_name(title) == title, f"rejected {title!r}"
    print("[PASS] real names, including accents and suffixes, are kept")


# ---- harvesting -----------------------------------------------------------
class Handler(http.server.BaseHTTPRequestHandler):
    """A fake Wikipedia that returns the real categorymembers shape."""

    def do_GET(self):
        if "categorymembers" not in self.query:
            self.send_response(400)
            self.end_headers()
            return
        body = json.dumps({"query": {"categorymembers": [
            {"pageid": 1, "ns": 0, "title": "List of American film actresses"},
            {"pageid": 2, "ns": 0, "title": "Beverly Aadland"},
            {"pageid": 3, "ns": 0, "title": "Mariann Aalda"},
            {"pageid": 4, "ns": 0, "title": "Aaliyah"},        # already a seed
            {"pageid": 5, "ns": 0, "title": "Academy Award for Best Actress"},
            {"pageid": 6, "ns": 0, "title": "Jane Doe (actress)"},
        ]}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @property
    def query(self):
        return self.path.split("?", 1)[-1]

    def log_message(self, *args):
        pass


def test_harvest_filters_and_dedupes(server_url):
    real = names.API
    names.API = server_url
    try:
        p = _pool()
        before = p.counts()["harvested"]
        added = p.add(names.harvest_category("American film actresses"),
                      "female", "actress")
        # Six titles in, three people out. The list page, the award and the
        # namesake are all filtered; none of the three survivors is a seed
        # name, so all three are new.
        harvested = sorted(n for n, _ in p.available("female")
                           if n not in {x for x, _ in names.SEED_FEMALE})
        assert harvested == ["Aaliyah", "Beverly Aadland", "Mariann Aalda"], \
            harvested
        assert added == 3, added
        assert p.counts()["harvested"] == before + 3, p.counts()
        # And adding the same category twice adds nothing.
        assert p.add(names.harvest_category("American film actresses"),
                     "female", "actress") == 0
        print("[PASS] 6 category titles -> 3 people, junk filtered, "
          "second pass adds none")
    finally:
        names.API = real


def test_harvest_raises_rather_than_returning_empty():
    """An outage and an empty category look identical from the outside. The
    caller needs to tell them apart, so a failure must not become []."""
    real = names.API
    names.API = "http://127.0.0.1:1/w/api.php"     # nothing listening
    try:
        try:
            names.harvest_category("American film actresses", timeout=1.0)
        except Exception as exc:
            assert not isinstance(exc, AssertionError)
            print(f"[PASS] a failed harvest raises ({type(exc).__name__}), "
                  f"it does not return []")
            return
        raise AssertionError("harvest_category swallowed the failure")
    finally:
        names.API = real


def test_top_up_survives_a_dead_api(server_url):
    """One bad category must not stop the rest, and none of it may raise."""
    p = _pool()
    cats = [("Not A Real Category", "female", "actress"),
            ("American film actresses", "female", "actress")]
    real = names.API
    names.API = server_url
    try:
        added = p.top_up(cats, delay=0)
    finally:
        names.API = real
    assert added == 3, added          # the fake yields the same 3 people
    print("[PASS] top_up keeps going past a bad category and never raises")


# ---- rate limiting --------------------------------------------------------
class ThrottlingHandler(http.server.BaseHTTPRequestHandler):
    """A fake Wikipedia that starts refusing after `allow` requests.

    Mirrors the real failure exactly: the first ten requests in a cycle are
    served, the rest come back 429 with a Retry-After.
    """

    allow = 10
    hits = 0
    retry_after = "300"

    def do_GET(self):
        cls = ThrottlingHandler
        cls.hits += 1
        if cls.hits > cls.allow:
            self.send_response(429)
            self.send_header("Retry-After", cls.retry_after)
            self.end_headers()
            return
        body = json.dumps({"query": {"categorymembers": [
            {"pageid": 1, "ns": 0, "title": "Beverly Aadland"}]}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_top_up_backs_off_on_a_rate_limit():
    """The bug: 16 requests per cycle against a 10/min limit.

    Six identical 429 lines every cycle, forever, with no backoff at all.
    A refusal must stop the cycle, be honoured for Retry-After, and cost
    nothing further until the cooldown expires.
    """
    import contextlib
    import io as _io

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ThrottlingHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/w/api.php"

    real_api, real_slow = names.API, names.SLOW_REPLY_SECONDS
    names.API = url
    names.SLOW_REPLY_SECONDS = 0.0
    ThrottlingHandler.hits = 0
    p = _pool()
    p._cooldown = (0.0, 0)
    try:
        log = _io.StringIO()
        with contextlib.redirect_stdout(log):
            p.top_up(delay=0)
        served = ThrottlingHandler.hits
        # It must have stopped at the first 429, not fired all 16.
        assert served == ThrottlingHandler.allow + 1, served
        # Retry-After=300 must be what it waits, not an invented number.
        left = p._cooldown[0] - time.time()
        assert 295 <= left <= 300, left

        # The next cycle must not touch Wikipedia at all.
        before = ThrottlingHandler.hits
        with contextlib.redirect_stdout(log):
            added = p.top_up(delay=0)
        assert added == 0, added
        assert ThrottlingHandler.hits == before, (
            "the cooldown was recorded but not enforced")

        out = log.getvalue()
        # One summary line per cycle, never one line per failing category.
        assert out.count("rate-limited") + out.count("cooling off") == 2, out
        assert "HTTPError 429" not in out, out
    finally:
        names.API, names.SLOW_REPLY_SECONDS = real_api, real_slow
        srv.shutdown()
    print("[PASS] a 429 stops the cycle, honours Retry-After, and logs once")


def test_rate_limit_backoff_doubles():
    """Without a Retry-After the wait must grow, not stay fixed."""
    p = _pool()
    p._cooldown = (0.0, 0)
    first = p._note_refusal(None)
    second = p._note_refusal(None)
    third = p._note_refusal(None)
    assert first < second < third, (first, second, third)
    assert third <= names.COOLDOWN_MAX, third
    # And the server's own number always wins over our doubling.
    p._cooldown = (0.0, 0)
    assert p._note_refusal(60.0) == 60.0
    # A successful fetch clears the doubling.
    p._note_success()
    assert p._resumable() is True
    assert p._cooldown == (0.0, 0), p._cooldown
    print("[PASS] the backoff doubles, honours Retry-After, and clears on success")


def test_user_agent_is_contactable():
    """Wikimedia allows an identifiable client 200 req/min; anything else 10.

    The old string had no URL or email in it, so we were held to the
    unidentified tier - which is exactly why six categories 429'd every cycle.
    """
    ua = names.USER_AGENT
    assert "http" in ua or "@" in ua, ua
    assert ua.startswith("ClawFacts/"), ua
    print(f"[PASS] the User-Agent is contactable: {ua}")


# ---- persistence ----------------------------------------------------------
def test_cache_round_trips(server_url):
    real = names.API
    names.API = server_url
    try:
        path = os.path.join(tempfile.mkdtemp(), "names.json")
        p = names.NamePool(path=path)
        p.add(names.harvest_category("American film actresses"),
              "female", "actress")
        p.updated = "2026-01-01T00:00:00Z"
        assert p._save() is True
        assert os.path.exists(path)

        reloaded = names.NamePool(path=path)
        assert reloaded.counts()["harvested"] == p.counts()["harvested"], \
            (reloaded.counts(), p.counts())
        assert reloaded.updated == "2026-01-01T00:00:00Z"
        print(f"[PASS] {p.counts()['harvested']} harvested names survive a "
              f"restart")
    finally:
        names.API = real


def test_corrupt_cache_is_ignored():
    """A half-written names.json must not stop the bot coming up."""
    path = os.path.join(tempfile.mkdtemp(), "names.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('{"harvested": {"female": [["only name"')
    p = names.NamePool(path=path)
    assert p.counts()["harvested"] == 0, p.counts()
    picks = p.draw("female")
    assert picks is not None, "the seed pool must still work"
    print("[PASS] a corrupt names.json falls back to the seed pool")


def test_malformed_rows_are_dropped():
    path = os.path.join(tempfile.mkdtemp(), "names.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"harvested": {"female": [["Good Name", "actress"],
                                            ["bad row"], [], None,
                                            ["Also Good", "singer"]]}}, fh)
    p = names.NamePool(path=path)
    got = sorted(n for n, _ in p.available("female"))
    assert "Good Name" in got and "Also Good" in got
    assert p.counts()["harvested"] == 2, p.counts()
    print("[PASS] malformed cache rows are dropped, good ones kept")


# ---- drawing --------------------------------------------------------------
def test_draw_shape():
    p = _pool()
    for gender, want in (("female", "female"), ("f", "female"),
                         ("male", "male"), ("m", "male"),
                         ("women", "female"), ("him", "male"),
                         ("any", "any"), ("nonsense", "any")):
        got = p.draw(gender)
        assert got is not None, gender
        picks, label = got
        assert label == want, (gender, label)
        assert len(picks) == 3, picks
        assert len({n for n, _ in picks}) == 3, f"duplicate in {picks}"
        assert all(job for _, job in picks), picks
    print("[PASS] every gender alias draws 3 distinct named-and-labelled picks")


def test_no_repeats_within_the_recency_window():
    """The complaint that started this: the same names every night."""
    p = _pool()
    seen, last = [], None
    for i in range(120):
        picks, _ = p.draw("female")
        flat = [n for n, _ in picks]
        if last:
            assert not set(flat) & last, f"repeat in round {i}: {set(flat) & last}"
        last = set(flat)
        seen += flat
    distinct = len(set(seen))
    # 120 rounds of 3 = 360 draws from a 185-name pool, so names must come
    # round again - but never back to back.
    assert distinct == p.counts()["female"], (distinct, p.counts())
    print(f"[PASS] 120 rounds, {distinct} distinct names, none repeated "
          f"back to back")


def test_recency_window_never_blocks_a_draw():
    """A window bigger than the pool used to make draw() return None."""
    p = _pool(seed={"female": [("Ann One", "actress"), ("Ann Two", "actress"),
                               ("Ann Three", "actress")],
                    "male": []})
    assert p.counts()["female"] == 3
    for i in range(300):
        got = p.draw("female")
        assert got is not None, f"draw() returned None on round {i}"
        assert len(got[0]) == 3, got
    print("[PASS] a 3-name pool still draws forever (window self-trims)")


def test_harvested_names_actually_reach_chat():
    """The whole point of the second tier. Drawing seed-first with a window
    smaller than the seed pool means harvested names are never reached and the
    top-up does nothing at all."""
    p = _pool()
    added = p.add([f"Zelda {w}" for w in
                   ("Abernathy", "Blackwood", "Carmichael", "Dunmore",
                    "Ellsworth", "Fairfax", "Grantham", "Holloway")],
                  "female", "actress")
    assert added == 8, added
    drawn = set()
    for _ in range(600):
        picks, _ = p.draw("female")
        drawn.update(n for n, _ in picks)
    reached = {d for d in drawn if d.startswith("Zelda ")}
    assert reached, "the harvested tier was never drawn from"
    print(f"[PASS] {len(reached)}/8 harvested names reached chat over 600 "
          f"rounds")


def test_seed_names_are_preferred():
    """Chat should mostly see names it recognises."""
    p = _pool()
    p.add([f"Zelda {w}" for w in
           ("Abernathy", "Blackwood", "Carmichael", "Dunmore", "Ellsworth",
            "Fairfax", "Grantham", "Holloway")], "female", "actress")
    total = harvested = 0
    for _ in range(600):
        picks, _ = p.draw("female")
        for n, _ in picks:
            total += 1
            harvested += n.startswith("Zelda ")
    share = 1 - harvested / total
    assert share > 0.5, f"only {share:.0%} of picks were seed names"
    print(f"[PASS] {share:.0%} of picks came from the seed pool")


def test_a_small_pool_relaxes_rather_than_refusing():
    p = _pool(seed={"female": [("Ann One", "actress"), ("Ann Two", "actress"),
                               ("Ann Three", "actress")], "male": []})
    for _ in range(50):
        picks, _ = p.draw("female")
        assert len(picks) == 3, picks
    # But genuinely too few names is reported, not papered over.
    tiny = _pool(seed={"female": [("Ann One", "actress"),
                                  ("Ann Two", "actress")], "male": []})
    assert tiny.draw("female") is None
    print("[PASS] too few names returns None; a tight pool relaxes instead")


# ---- the bot --------------------------------------------------------------
def test_bot_keeper():
    import bot as bot_mod
    b = bot_mod.TwitchBot(dict(bot_mod.DEFAULTS, nick="bot", channel="#test"))
    assert hasattr(b, "_names_tick") and hasattr(b, "_names_keeper")
    # Disabled means disabled, and it must not touch the network.
    b.cfg = dict(b.cfg, names_topup_enabled=False)
    assert b._names_tick() == 0
    print("[PASS] the top-up keeper exists and honours names_topup_enabled")


def main():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_address[1]}/w/api.php"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print("==== names pool tests ====")
    for fn in (test_seed_pool_is_big_enough,
               test_every_seed_entry_has_a_job,
               test_seed_pool_has_no_duplicates,
               test_category_junk_is_not_a_person,
               test_real_people_survive_the_filter):
        fn()
    for fn in (test_harvest_filters_and_dedupes,
               test_top_up_survives_a_dead_api, test_cache_round_trips):
        fn(url)
    for fn in (test_top_up_backs_off_on_a_rate_limit,
               test_rate_limit_backoff_doubles, test_user_agent_is_contactable):
        fn()
    for fn in (test_harvest_raises_rather_than_returning_empty,
               test_corrupt_cache_is_ignored, test_malformed_rows_are_dropped,
               test_draw_shape, test_no_repeats_within_the_recency_window,
               test_recency_window_never_blocks_a_draw,
               test_harvested_names_actually_reach_chat,
               test_seed_names_are_preferred,
               test_a_small_pool_relaxes_rather_than_refusing,
               test_bot_keeper):
        fn()
    server.shutdown()
    print("ALL PASSED \u2714")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
