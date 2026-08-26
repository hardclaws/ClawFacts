"""Offline tests for funfacts.py (rotation, char limit, spicy db, dedupe).

Run:  python3 mock_facts_test.py
"""

import time

import funfacts


def test_trim():
    long = "word " * 150  # 750 chars
    t = funfacts._trim(long, 200)
    assert len(t) <= 200, len(t)
    assert t.endswith("…"), t
    print(f"[PASS] trim -> {len(t)} chars")


def test_rotation():
    import random
    funfacts._cache.clear()
    calls = []

    def fake(place, options, spicy, limit):
        calls.append(place)
        return {"place": "Testville", "facts": ["fact one", "fact two", "fact three"]}

    random.seed(7)
    orig = funfacts._lookup_all
    funfacts._lookup_all = fake
    try:
        got = [funfacts.get_funfact("Testville")["fact"] for _ in range(6)]
        assert got[0] == "fact one", got          # best fact shown first
        assert set(got) == {"fact one", "fact two", "fact three"}, got
        for a, b in zip(got, got[1:]):
            assert a != b, got                    # no immediate repeat
        assert len(calls) == 1, "should be cached after first call"
        print(f"[PASS] rotation -> {got}")
    finally:
        funfacts._lookup_all = orig


def test_spicy_db():
    funfacts._cache.clear()
    r = funfacts._spicy_db("Las Vegas, NV", 200)
    assert r and r["place"] == "Las Vegas, Nevada", r
    assert len(r["facts"]) >= 3, r
    assert all(len(f) <= 200 for f in r["facts"])
    print(f"[PASS] spicy db -> {r['place']}: {len(r['facts'])} facts")
    # Milford must rotate the verified Gotti + Lincoln Flag facts.
    m = funfacts._spicy_db("Milford, PA", 200)
    assert m and any("Lincoln Flag" in f for f in m["facts"]), m
    # Lords Valley must surface the Poconos honeymoon-resort facts.
    lv = funfacts._spicy_db("Lords Valley, PA", 200)
    assert lv and any("heart-shaped bathtub" in f for f in lv["facts"]), lv
    # a plain place that isn't in the DB should return None
    assert funfacts._spicy_db("Nowheresville, ZZ", 200) is None
    print("[PASS] spicy db miss -> None")


def test_ranked_dedupe():
    sents = [
        "The town has a famous brewery.",
        "The town has a famous brewery, yes.",
        "Not much else to say about this place at all folks.",
    ]
    facts = funfacts._ranked_facts(sents, spice=True, limit=200, count=5)
    # near-duplicate deduped; the zero-score filler sentence is dropped
    assert facts == ["The town has a famous brewery."], facts
    assert all(len(f) <= 200 for f in facts)
    print(f"[PASS] ranked dedupe -> {facts}")


def test_ranked_filter():
    sents = [
        "The town is home to the world's largest truck stop.",
        "Its population was 1,551 at the 2020 census.",
    ]
    facts = funfacts._ranked_facts(sents, spice=False, limit=200, count=5)
    # interesting fact kept, boring population filler dropped
    assert facts == ["The town is home to the world's largest truck stop."], facts
    print(f"[PASS] ranked filter -> {facts}")


def test_parse_geocode():
    kulgera = {
        "lat": "-25.8408029", "lon": "133.2999162",
        "name": "Kulgera",
        "display_name": "Kulgera, Northern Territory, Australia",
        "address": {"village": "Kulgera", "territory": "Northern Territory",
                    "country": "Australia", "country_code": "au"},
    }
    g = funfacts._parse_geocode(kulgera)
    assert g and g["name"] == "Kulgera" and g["state"] == "Northern Territory", g
    assert g["country"] == "Australia" and g["lat"] == -25.8408029
    print(f"[PASS] parse geocode -> {g['name']}, {g['state']}, {g['country']}")

    hygiene = {
        "lat": "40.1887848", "lon": "-105.1780551",
        "display_name": "Hygiene, Boulder County, Colorado, United States",
        "address": {"hamlet": "Hygiene", "state": "Colorado", "country": "United States"},
    }
    h = funfacts._parse_geocode(hygiene)
    assert h and h["name"] == "Hygiene" and h["state"] == "Colorado"
    print(f"[PASS] parse geocode hamlet -> {h['name']}, {h['state']}")


def test_remote_fallback():
    """No wiki/ddg hit -> geocode -> coordinate geosearch -> nearby fact."""
    funfacts._cache.clear()
    calls = {"wiki": 0}

    orig_try = funfacts._try_sources
    orig_geo = funfacts._osm_geocode
    orig_near = funfacts._wiki_geosearch
    try:
        def no_hit(*a, **k):
            calls["wiki"] += 1
            return None
        funfacts._try_sources = no_hit
        funfacts._osm_geocode = lambda q: {"name": "Kulgera", "state": "Northern Territory",
                                           "country": "Australia", "lat": -25.84, "lon": 133.3}
        funfacts._wiki_geosearch = lambda lat, lon, radius=10000: [
            {"title": "Mount Conner", "dist": 5200,
             "extract": "Mount Conner is a flat-topped mountain in central Australia. "
                        "It is often mistaken for Uluru by travellers."},
        ]
        r = funfacts._lookup_all("Kulgera, NT", {}, False, 200)
        assert r and r["place"] == "Kulgera, Northern Territory", r
        assert any("Mount Conner" in f for f in r["facts"]), r
        assert any("Just outside town" in f for f in r["facts"]), r
        assert calls["wiki"] == 2, calls  # original + canonical retry
        print(f"[PASS] remote fallback -> {r['place']}: {r['facts'][0]}")
    finally:
        funfacts._try_sources = orig_try
        funfacts._osm_geocode = orig_geo
        funfacts._wiki_geosearch = orig_near


def test_google_source():
    """Google CSE source: skipped without keys, and mined when keys present."""
    # No keys -> returns None without any network call.
    assert funfacts._google_search("Xyzzy, ZZ", False, 200, {}) is None
    assert funfacts._google_search("Xyzzy, ZZ", False, 200, {"google_api_key": "k", "google_cx": ""}) is None
    print("[PASS] google source skipped without keys")

    # With keys -> monkeypatch the HTTP layer and check snippet mining.
    hits = {
        "items": [
            {"title": "Xyzzy, Wyoming", "snippet": "Xyzzy is home to the world's largest jackalope statue. It was built in 1965 by a bar owner."},
            {"title": "Xyzzy News", "snippet": "Local diner claims the best pie on the interstate."},
        ]
    }
    orig = funfacts._http_get_json
    funfacts._http_get_json = lambda url, params, timeout=8: hits
    try:
        r = funfacts._google_search("Xyzzy, WY", False, 200,
                                    {"google_api_key": "k", "google_cx": "c"})
        assert r and r["facts"], r
        assert any("jackalope" in f for f in r["facts"]), r
        assert all(len(f) <= 200 for f in r["facts"])
        print(f"[PASS] google source -> {r['facts'][0]}")
    finally:
        funfacts._http_get_json = orig


def test_spicy_dig():
    funfacts._cache.clear()
    orig = funfacts._wiki_search_extracts
    try:
        EXTRACTS = {
            "Storyville, New Orleans": "Storyville was the red-light district of New Orleans from 1897 to 1917, until the Navy shut it down during WWI.",
            "Oasis Bordello": "The Oasis Bordello in Wallace, Idaho operated as a brothel for over a century until a federal raid in 1988.",
            "Wallace, Idaho": "Wallace is a town in Idaho.",
        }

        def fake(q, exchars=4000, limit=6):
            ql = q.lower()
            if "brothel" in q and "new orleans" in ql:
                return [{"title": "Storyville, New Orleans", "extract": EXTRACTS["Storyville, New Orleans"]}]
            if "brothel" in q:
                return [{"title": t, "extract": EXTRACTS[t]} for t in
                        ("Oasis Bordello", "Wallace, Idaho")]
            return []
        funfacts._wiki_search_extracts = fake

        dig = funfacts._spicy_dig("New Orleans", "New Orleans, LA", [], 200)
        assert any("red-light" in f for f in dig), dig
        assert all("new orleans" in f.lower() for f in dig), dig
        print(f"[PASS] spicy dig (New Orleans) -> {dig}")

        # The town's own article must be skipped; sentences must name the place.
        dig2 = funfacts._spicy_dig("Wallace, Idaho", "Wallace, ID", [], 200)
        assert all("wallace" in f.lower() for f in dig2), dig2
        print(f"[PASS] spicy dig (Wallace) -> {dig2}")
    finally:
        funfacts._wiki_search_extracts = orig


def test_bio_filter():
    # Wikipedia renders "Notable people" entries as separate lines — a bio line
    # must be dropped while a real fact line is kept.
    text = ("Zygmunt Siegfried Leymel (November 27, 1883 \u2013 May 9, 1947) was a "
            "teacher, veteran of the Spanish\u2013American War and World War I.\n"
            "Fresno is the largest city in the San Joaquin Valley.")
    sents = funfacts._sentences(text)
    assert len(sents) == 1, sents
    assert "Fresno" in sents[0], sents
    print(f"[PASS] bio filter -> dropped list entry, kept {sents[0]!r}")


def test_wiki_cooldown():
    import time as _t
    funfacts._wiki_blocked_until = _t.time() + 10
    try:
        assert funfacts._wiki_search("anything") == []
        assert funfacts._wiki_extract("Anything") == ""
        assert funfacts._wiki_search_extracts("anything") == []
        assert funfacts._wiki_geosearch(0, 0) == []
    finally:
        funfacts._wiki_blocked_until = 0.0
    print("[PASS] wiki cooldown -> search/extract/geosearch all skip while blocked")


def test_definition_filter():
    sents = [
        "Milford is a borough that is located in Pike County, Pennsylvania, United States, and the county seat.",
        "Milford was founded in 1796 by Judge John Biddis, one of Pennsylvania's first four circuit judges.",
    ]
    f = funfacts._filter_definitions(sents)
    assert len(f) == 1 and "1796" in f[0], f
    # If everything is a definition, keep the original (never return nothing).
    assert funfacts._filter_definitions([sents[0]]) == [sents[0]]
    print(f"[PASS] definition filter -> kept {f[0]!r}")


def test_explicit_filter():
    import llm
    orig_rw, orig_cfg = llm.rewrite_fact, llm.is_configured
    try:
        llm.is_configured = lambda o: True
        llm.rewrite_fact = lambda p, l, s, o: (
            "1. The saloon brawls here were legendary.\n"
            "2. A porn studio opened in town in 1972.\n"
            "3. The first public execution happened in 1888.\n")
        seed = ["The saloon brawls were legendary in 1888."]
        got = funfacts._llm_facts("X", "X", seed, {})
        assert got == ["The saloon brawls here were legendary.",
                       "The first public execution happened in 1888."], got
        # Every line explicit -> empty list, so the bot keeps the plain facts.
        llm.rewrite_fact = lambda p, l, s, o: "1. blowjob\n2. masturbation\n3. xxx\n"
        assert funfacts._llm_facts("X", "X", seed, {}) == []
    finally:
        llm.rewrite_fact, llm.is_configured = orig_rw, orig_cfg
    print("[PASS] explicit-content filter drops sexual lines, keeps the rest")


def test_grounded_filter():
    import llm
    orig_rw, orig_cfg = llm.rewrite_fact, llm.is_configured
    seed = ["Milford was founded in 1796 by Judge John Biddis, "
            "one of Pennsylvania's first four circuit judges."]
    try:
        llm.is_configured = lambda o: True
        # Invented person + invented city/mob story must be dropped; the line
        # grounded in the seed fact must survive.
        llm.rewrite_fact = lambda p, l, s, o: (
            "1. A notorious safe-cracker called Devil Jack Schramm hid out in these hills.\n"
            "2. Milford was founded in 1796 by Judge John Biddis.\n"
            "3. Mobsters ran backroom gambling joints up in New York.\n")
        got = funfacts._llm_facts("Milford, Pennsylvania", "Milford, PA", seed, {})
        assert got == ["Milford was founded in 1796 by Judge John Biddis."], got

        # A reword of the seed fact survives; an invented river does not.
        llm.rewrite_fact = lambda p, l, s, o: (
            "1. Milford was founded in 1796 by Judge John Biddis.\n"
            "2. Judge Biddis was one of Pennsylvania's first circuit judges.\n"
            "3. It sits on the Delaware.\n")
        got2 = funfacts._llm_facts("Milford, Pennsylvania", "Milford, PA", seed, {})
        assert "Delaware" not in " ".join(got2) and len(got2) == 2, got2
    finally:
        llm.rewrite_fact, llm.is_configured = orig_rw, orig_cfg
    print("[PASS] grounded filter drops invented names/dates, keeps seed-grounded lines")


def test_weird_fallback():
    spicy = "The saloon hosted a notorious gunfight in 1880."
    weird = "The town's odd legend involves a mysterious giant pumpkin."
    popular = "The town was founded in 1850 and named after a president."
    boring = "Its population was 1,103 at the 2020 census."
    # With spice: spicy first, then weird, then popular; boring filler dropped.
    facts = funfacts._ranked_facts([boring, popular, weird, spicy],
                                   spice=True, limit=200, count=6)
    assert "gunfight" in facts[0], facts
    assert "pumpkin" in facts[1], facts
    assert "president" in facts[2], facts
    assert len(facts) == 3, facts
    # No spicy sentence at all: weird bubbles to the top, popular second.
    facts2 = funfacts._ranked_facts([boring, popular, weird],
                                    spice=True, limit=200, count=6)
    assert "pumpkin" in facts2[0], facts2
    assert "president" in facts2[1], facts2
    print("[PASS] fallback ladder -> spicy, then weird, then popular")


def test_merge_curated():
    curated = {"place": "Milford, Pennsylvania",
               "facts": ["Gambino crime boss John Gotti kept a hilltop estate in Milford.",
                         "Milford's Columns Museum holds the 'Lincoln Flag'."]}
    discovered = {"place": "Milford, Pennsylvania",
                  "facts": ["Milford's Columns Museum holds the Lincoln Flag — the blood-stained flag.",
                            "Milford was founded in 1796 by Judge John Biddis."]}
    m = funfacts._merge_curated(curated, discovered)
    assert m["facts"][0] == curated["facts"][0]
    assert any("1796" in f for f in m["facts"])
    assert sum("Lincoln Flag" in f for f in m["facts"]) == 1, m["facts"]
    assert funfacts._merge_curated(curated, None) is curated
    assert funfacts._merge_curated(None, discovered) is discovered
    print("[PASS] merge curated -> curated first, duplicates dropped")


def test_region_dig():
    orig_geo = funfacts._osm_geocode
    orig = funfacts._wiki_search_extracts
    try:
        funfacts._osm_geocode = lambda q: {"name": "Lords Valley", "state": "Pennsylvania",
                                           "county": "Pike County", "country": "US",
                                           "lat": 0, "lon": 0}
        def fake(q, exchars=4000, limit=6):
            if "Pennsylvania" in q and "honeymoon" in q:
                return [{"title": "Morris Wilkins", "extract": (
                    "Morris Wilkins is credited with helping establish the Pocono Mountains "
                    "in northeast Pennsylvania as the honeymoon capital of the world.")}]
            return []
        funfacts._wiki_search_extracts = fake
        r = funfacts._region_dig("Lords Valley, PA", [], 200)
        assert any("honeymoon capital" in f for f in r), r
        # A sentence that doesn't name the county/state must be ignored.
        funfacts._wiki_search_extracts = lambda q, exchars=4000, limit=6: (
            [{"title": "Somewhere, Ohio", "extract":
              "A totally unrelated town in Ohio has a giant ball of twine."}]
            if "Pennsylvania" in q else [])
        assert funfacts._region_dig("Lords Valley, PA", [], 200) == []
    finally:
        funfacts._osm_geocode = orig_geo
        funfacts._wiki_search_extracts = orig
    print("[PASS] region dig -> finds regional facts, ignores unrelated ones")


def test_fit_fact():
    import llm
    orig_sum = llm.summarize
    long = ("Eric Matthew Frein was identified as the sole suspect of the ambush "
            "and was sought by federal and state authorities for the ambush, until "
            "his apprehension at 6 p.m. on Thursday, October 30, ending a 48-day manhunt.")
    try:
        # No LLM -> clause-boundary trim; must not end in a dangling fragment.
        t = funfacts._fit_fact(long, 200, {})
        assert len(t) <= 200 and t.endswith("…"), t
        tail = t[:-1].rsplit(" ", 1)[-1].lower()
        assert tail not in {"a", "the", "an", "and", "during", "until", "ending",
                            "of", "in", "on", "by", "to", "for"}, t
        # Mocked LLM summary is used (and fits).
        llm.summarize = lambda fact, n, cfg: "Eric Frein was caught after a 48-day manhunt."
        s = funfacts._fit_fact(long, 200, {"llm_api_key": "gsk_x",
                                           "llm_base_url": "https://api.groq.com/openai/v1"})
        assert s == "Eric Frein was caught after a 48-day manhunt.", s
        # A summary that invents a name/date is rejected -> trim fallback.
        llm.summarize = lambda fact, n, cfg: "Al Capone robbed the town in 1929."
        s2 = funfacts._fit_fact(long, 200, {"llm_api_key": "gsk_x",
                                            "llm_base_url": "https://api.groq.com/openai/v1"})
        assert "Capone" not in s2 and "1929" not in s2, s2
    finally:
        llm.summarize = orig_sum
    print("[PASS] fit fact -> LLM summary when configured, clause-trim otherwise")


def test_filler_filter():
    assert funfacts._is_filler("As of the 2010 census, the population was 1,868 residents.")
    assert funfacts._is_filler("It is located east of I-99 and is located between Altoona and Hollidaysburg.")
    assert funfacts._is_filler("The median household income was $41,000.")
    assert not funfacts._is_filler("Leap-The-Dips is the world's oldest surviving roller coaster.")
    # interesting fact with a "located" phrase still passes
    assert not funfacts._is_filler("The world's largest ball of twine is located in the town.")
    # a pool of only filler yields nothing (so the caller falls through to
    # related articles instead of posting a census line)
    assert funfacts._ranked_facts(
        ["As of the 2010 census, the population was 1,868 residents."],
        spice=False, limit=200) == []
    # the suburb/location line DDG returns for small towns is also filler
    assert funfacts._is_filler(
        "Located directly north of Youngstown, it is a suburb in the "
        "Youngstown–Warren metropolitan area.")
    # but a suburb fact with a real hook survives
    assert not funfacts._is_filler(
        "Girard is home to the largest suburban flagpole in Ohio.")
    print("[PASS] filler filter drops census/location boilerplate")


def test_meta_line_filter():
    raw = (
        "Here's a thinking process:\n"
        "1. **Analyze User Input:** the place is Girard, Ohio.\n"
        "2. Extract the relevant facts from the supplied list.\n"
        "Final answer:\n"
        "Located directly north of Youngstown, it is a suburb in the Youngstown–Warren metropolitan area.\n"
        "The Trumbull Correctional Institution is a medium-security prison for men located in Leavittsburg, Trumbull County, Ohio."
    )
    import llm
    orig = llm.rewrite_fact
    llm.rewrite_fact = lambda place, loc, seeds, cfg: raw
    try:
        facts = funfacts._llm_facts("Girard, Ohio", "girard, OH",
                                    ["Located directly north of Youngstown, it is a suburb in the Youngstown–Warren metropolitan area.",
                                     "The Trumbull Correctional Institution is a medium-security prison for men located in Leavittsburg, Trumbull County, Ohio."],
                                    {"llm_api_key": "gsk_x", "llm_base_url": "https://api.groq.com/openai/v1"})
        assert facts, facts
        assert all(not f.lower().startswith(("here's", "1.", "2.", "final answer",
                                             "analyze")) for f in facts), facts
        assert any("Trumbull" in f for f in facts), facts
        print(f"[PASS] meta-line filter strips reasoning preamble -> {facts}")
    finally:
        llm.rewrite_fact = orig


def test_serper_source():
    # No key -> None without any network call.
    assert funfacts._serper_search("Xyzzy, ZZ", False, 200, {}) is None
    print("[PASS] serper skipped without key")

    hits = {"organic": [
        {"title": "Xyzzy, Wyoming", "snippet": "Xyzzy is home to the world's largest jackalope statue, built in 1965."},
        {"title": "Xyzzy News", "snippet": "The town's diner claims the best pie on the interstate."},
    ]}
    orig = funfacts.urllib.request.urlopen
    import io
    class _Resp:
        def __init__(self, data): self._d = data
        def read(self): return self._d
        def __enter__(self): return self
        def __exit__(self, *a): return False
    funfacts.urllib.request.urlopen = lambda req, timeout=10: _Resp(
        b'{"organic":[{"title":"Xyzzy, Wyoming","snippet":"Xyzzy is home to the world\'s largest jackalope statue, built in 1965."}]}')
    try:
        r = funfacts._serper_search("Xyzzy, WY", False, 200, {"serper_api_key": "k"})
        assert r and r["facts"], r
        assert any("jackalope" in f for f in r["facts"]), r
        print(f"[PASS] serper source -> {r['facts'][0]}")
    finally:
        funfacts.urllib.request.urlopen = orig


def test_wiki_multi_title():
    extracts = {
        "Lakemont, Pennsylvania":
            "Lakemont is a census-designated place in Blair County, Pennsylvania. "
            "As of the 2010 census, the population was 1,868 residents.",
        "Lakemont Park":
            "Lakemont Park opened in 1894 as a trolley park. Among its notable "
            "attractions is Leap-The-Dips, the world's oldest surviving roller "
            "coaster, which first opened in 1902.",
        "Lakemont, Washington":
            "Lakemont borders the nearby cities of Issaquah and Newport.",
        "Pennsylvania": "Pennsylvania is a U.S. state.",
    }

    def fake_search_extracts(q, exchars=4000, limit=6):
        return [{"title": t, "extract": extracts.get(t, "")} for t in (
            "Lakemont, Pennsylvania", "Lakemont Park",
            "Lakemont, Washington", "Pennsylvania")]

    os_ = funfacts._wiki_search_extracts
    funfacts._wiki_search_extracts = fake_search_extracts
    try:
        r = funfacts._wikipedia("Lakemont, PA", spice=False, limit=200)
        assert r and r["place"] == "Lakemont, Pennsylvania", r
        assert any("Leap-The-Dips" in f for f in r["facts"]), r["facts"]
        assert r["facts"][0] and "Leap-The-Dips" in r["facts"][0], r["facts"]
        assert all("1,868" not in f for f in r["facts"]), r["facts"]
        assert all("Issaquah" not in f for f in r["facts"]), r["facts"]  # wrong state rejected
        print(f"[PASS] wiki multi-title harvest -> {r['facts'][0][:60]}...")
    finally:
        funfacts._wiki_search_extracts = os_


def test_busy_unavailable():
    funfacts._cache.clear()
    funfacts._wiki_blocked_until = time.time() + 45  # simulate a live 429
    assert funfacts._give_up("X") is not None and funfacts._give_up("X").get("unavailable")
    orig = funfacts._lookup_all
    funfacts._lookup_all = lambda loc, opts, spicy, limit: {"place": loc, "facts": [], "unavailable": True}
    try:
        r = funfacts.get_funfact("Testville")
        assert r and r.get("busy") is True, r
        assert not r.get("fact"), r
    finally:
        funfacts._lookup_all = orig
        funfacts._wiki_blocked_until = 0.0
    funfacts._wiki_blocked_until = 0.0
    assert funfacts._give_up("X") is None  # not blocked -> real miss
    print("[PASS] busy marker only while sources are rate-limited")


def main():
    test_trim()
    test_rotation()
    test_busy_unavailable()
    test_spicy_db()
    test_ranked_dedupe()
    test_ranked_filter()
    test_parse_geocode()
    test_remote_fallback()
    test_google_source()
    test_spicy_dig()
    test_bio_filter()
    test_definition_filter()
    test_explicit_filter()
    test_grounded_filter()
    test_weird_fallback()
    test_merge_curated()
    test_region_dig()
    test_fit_fact()
    test_filler_filter()
    test_meta_line_filter()
    test_serper_source()
    test_wiki_multi_title()
    test_wiki_cooldown()
    print("\nALL PASSED ✔")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
