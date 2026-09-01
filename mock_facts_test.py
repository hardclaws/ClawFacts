"""Offline tests for funfacts.py (rotation, char limit, spicy db, dedupe).

Run:  python3 mock_facts_test.py
"""

import time

import json
import pathlib

import funfacts


def test_trim():
    long = "word " * 150  # 750 chars
    t = funfacts._trim(long, 200)
    assert len(t) <= 200, len(t)
    assert t.endswith("…"), t
    print(f"[PASS] trim -> {len(t)} chars")


def test_trim_keeps_whole_sentences():
    """Sarcoxie, MO posted "...contributing buildings that…" — chopped mid
    sentence, because the only clause break inside the first 200 characters
    landed at 89 chars, under the keep-if-long-enough threshold, so it fell
    through to a word chop. A complete 145-character sentence was right there."""
    fact = ("Sarcoxie's Public Square Historic District, a national historic district in "
            "Jasper County, is listed on the National Register of Historic Places. The "
            "district contains contributing buildings that date from the late nineteenth "
            "and early twentieth centuries, including the original courthouse.")
    out = funfacts._trim(fact, 200)
    assert len(out) <= 200, len(out)
    assert not out.endswith("…"), "still chopping a sentence in half: %r" % out
    assert out.endswith("Historic Places."), out
    # A single long sentence with no break still has to be cut somewhere.
    assert funfacts._trim("word " * 150, 200).endswith("…")
    # ...and text already inside the limit is untouched.
    assert funfacts._trim("Short and complete.", 200) == "Short and complete."
    # An abbreviation is not a sentence end: splitting here dropped the
    # punchline off the Frein fact.
    frein = ("Eric Matthew Frein was identified as the sole suspect of the ambush and was "
             "sought by federal and state authorities for the ambush, until his apprehension "
             "at 6 p.m. on Thursday, October 30, ending a 48-day manhunt.")
    assert funfacts._sentence_parts(frein) == [frein], funfacts._sentence_parts(frein)
    # One 214-char sentence cannot keep its tail inside 200, so it still ends
    # on a clause break - but the tail must not be a dangling word.
    out = funfacts._trim(frein, 200)
    assert len(out) <= 200 and out.endswith("…"), out
    tail = out[:-1].rsplit(" ", 1)[-1].lower()
    assert tail not in {"a", "the", "an", "and", "until", "at", "of", "on"}, out
    print(f"[PASS] trim keeps whole sentences -> {len(out)} chars, no ellipsis")


def test_smk_game():
    """!smk female|male|any — three distinct names from the right pool."""
    import extras
    import names
    for gender in ("female", "male"):
        pool = set(names.pool.available(gender))
        picks, label = extras.get_smk(gender)
        assert label == gender
        assert len(picks) == 3 and len(set(picks)) == 3, picks
        assert all(n in pool for n in picks), picks
    both = set(names.pool.available("any"))
    picks, label = extras.get_smk("any")
    assert label == "any" and len(set(picks)) == 3
    assert all(n in both for n in picks)
    # Unknown or missing argument falls back to a mixed round, never crashes.
    for arg in ("", "bogus", None):
        assert extras.get_smk(arg)[1] == "any"
    # Shorthands work too.
    assert extras.get_smk("f")[1] == "female"
    assert extras.get_smk("M")[1] == "male"

    # Every entry carries what the person is known for, so a round is
    # playable by people who do not recognise every name in the pool.
    for name, job in names.SEED_FEMALE + names.SEED_MALE:
        assert name.strip() and job.strip(), (name, job)
    picks, _ = extras.get_smk("any")
    line = extras.format_smk(picks)
    assert line.count("(") == 3 and line.count(")") == 3, line
    assert ", " in line and line.count(", ") == 2, line
    for name, job in picks:
        assert f"{name} ({job})" in line, line
    assert extras.format_smk([("Prince", "")]) == "Prince"
    print(f"[PASS] !smk draws three distinct names, each with a job: {line}")


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


def test_editorialising_is_not_a_rewrite():
    """A rewrite may re-word; it may not add character the source lacks.

    Live output was "Aubrey Plaza: actress, comedian, producer, writer - and
    deadpan delivered with extra deadpan." That invents no name, date, place,
    event or claim, so it cleared every existing check and reached chat. What
    it invented was an opinion, bolted on as a trailing clause.
    """
    seed = ["Aubrey Plaza (born June 26, 1984) is an American actress, "
            "comedian, producer, and writer."]
    quip = ("Aubrey Plaza: actress, comedian, producer, writer - and deadpan "
            "delivered with extra deadpan.")
    assert funfacts._grounded_filter([quip], "Aubrey Plaza", "Aubrey Plaza",
                                     seed) == [], "the quip reached chat"
    # A faithful re-wording of the same seed must survive.
    faithful = ("Aubrey Plaza is an American actress, comedian, producer "
                "and writer.")
    assert funfacts._grounded_filter([faithful], "Aubrey Plaza",
                                     "Aubrey Plaza", seed), "over-filtering"
    # So must a paraphrase that swaps in synonyms and an abbreviation - the
    # earlier word-count rule wrongly dropped both of these.
    girard = ["It is believed that Girard takes its name from Stephen Girard, "
              "a French American philanthropist who was the founder of the "
              "Girard Bank and Girard College in Philadelphia."]
    paraphrase = ("Some claim Girard owes its name to Stephen Girard, that "
                  "French American bank founder from Philly. Could be.")
    assert funfacts._grounded_filter([paraphrase], "Girard, Ohio",
                                     "girard, OH", girard), "abbreviation lost"
    assert funfacts._grounded_filter(
        ["The first public execution happened in 1888."], "X", "X",
        ["The town's first public execution drew a crowd in 1888."]), "synonym lost"
    # And praise appended as a clause is still dropped.
    praise = ("Girard is a village in Trumbull County with a thriving arts "
              "scene everyone loves.")
    assert funfacts._grounded_filter([praise], "Girard, Ohio", "girard, OH",
                                     girard) == []
    print("[PASS] an LLM rewrite cannot add character the source never had")


def test_explicit_filter():
    import llm
    orig_rw, orig_cfg = llm.rewrite_fact, llm.is_configured
    try:
        llm.is_configured = lambda o: True
        llm.rewrite_fact = lambda p, l, s, o: (
            "1. The saloon brawls here were legendary.\n"
            "2. A porn studio opened in town in 1972.\n"
            "3. The first public execution happened in 1888.\n")
        seed = ["The saloon brawls were legendary in 1888.",
                "The town's first public execution drew a crowd in 1888."]
        got = funfacts._llm_facts("X", "X", seed, {})
        assert got == ["The saloon brawls here were legendary.",
                       "The first public execution happened in 1888."], got
        # Every line explicit -> empty list, so the bot keeps the plain facts.
        llm.rewrite_fact = lambda p, l, s, o: "1. blowjob\n2. masturbation\n3. xxx\n"
        assert funfacts._llm_facts("X", "X", seed, {}) == []
    finally:
        llm.rewrite_fact, llm.is_configured = orig_rw, orig_cfg
    print("[PASS] explicit-content filter drops sexual lines, keeps the rest")


def test_tasteless_filter():
    import llm
    orig_rw, orig_cfg = llm.rewrite_fact, llm.is_configured
    seed = ["A public hanging was carried out in the town square in 1888.",
            "The inn runs a murder mystery dinner every month."]
    try:
        llm.is_configured = lambda o: True
        llm.rewrite_fact = lambda p, l, s, o: (
            "1. The town threw a hanging party in 1888.\n"
            "2. A public hanging went down in the town square in 1888.\n"
            "3. The inn's murder mystery dinner is a monthly thing.\n")
        got = funfacts._llm_facts("X", "X", seed, {})
        # "hanging party" is dropped as tasteless even though it is grounded;
        # the plain telling of the same fact and the real murder-mystery
        # dinner survive.
        assert "hanging party" not in " ".join(got), got
        assert any("1888" in f for f in got), got
        assert any("murder mystery dinner" in f for f in got), got
    finally:
        llm.rewrite_fact, llm.is_configured = orig_rw, orig_cfg
    print("[PASS] taste filter drops 'hanging party' framing, keeps the fact")


def test_invented_claim_filter():
    """The exact two lines the bot posted for `!funfact girard, OH`.

    Girard's whole Wikipedia article yields one seed fact (the canal), plus a
    regional dig hit on Trumbull County — nowhere near a hanging or a drug
    mule. Neither line may reach chat.
    """
    import llm
    orig_rw, orig_cfg = llm.rewrite_fact, llm.is_configured
    seed = [
        "It was first settled in 1800 but remained static until the Ohio and "
        "Erie Canal was completed.",
        funfacts._AREA_PREFIX +
        "The Trumbull Correctional Institution is a medium-security prison for "
        "men located in Leavittsburg, Trumbull County, Ohio and operated by the "
        "Ohio Department of Rehabilitation and Correction.",
    ]
    try:
        llm.is_configured = lambda o: True
        llm.rewrite_fact = lambda p, l, s, o: (
            "Girard's no choir boy - it's the only place in Trumbull County "
            "where a hanging party went down.\n"
            "This town's so fast and loose with drugs, one local broke records "
            "as a mule.\n"
            "Girard's the only town in Trumbull County with a prison.\n"
            "Girard was first settled in 1800 and stayed quiet until the Ohio "
            "and Erie Canal was finished.\n")
        got = funfacts._llm_facts("Girard, Ohio", "girard, OH", seed, {})
        # The hanging boast ("hanging" is nowhere in the seeds), the drug-mule
        # boast ("drugs"/"mule"/"records" are nowhere in the seeds) and the
        # county-prison fact re-attributed to the town must all be dropped.
        assert got == ["Girard was first settled in 1800 and stayed quiet until "
                       "the Ohio and Erie Canal was finished."], got
        # Every line invented -> empty, so the bot posts the plain real facts.
        llm.rewrite_fact = lambda p, l, s, o: (
            "Girard's no choir boy - it's the only place in Trumbull County "
            "where a hanging party went down.\n"
            "This town's so fast and loose with drugs, one local broke records "
            "as a mule.\n")
        assert funfacts._llm_facts("Girard, Ohio", "girard, OH", seed, {}) == []
    finally:
        llm.rewrite_fact, llm.is_configured = orig_rw, orig_cfg
    print("[PASS] claim grounding drops the invented Girard hanging/mule facts")


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


def test_county_hanging_reattribution():
    """A county-wide hanging story must never become the town's claim to fame.

    The line below reached chat as `!funfact girard, OH`. The only hanging in
    Trumbull County was Ira West Gardner's 1830s execution in Warren — Girard
    has nothing to do with it — and the seeds can carry that sentence
    *without* the regional prefix (any harvest path can surface it), so the
    filter has to catch the re-attribution itself.
    """
    import llm
    orig_rw, orig_cfg = llm.rewrite_fact, llm.is_configured
    hang = ("Ira West Gardner was the only man hanged in Trumbull County, "
            "executed for the 1832 murder of his stepdaughter Maria Buel.")
    seeds = [
        "It was first settled in 1800 but remained static until the Ohio and "
        "Erie Canal was completed.",
        hang,  # unprefixed: region dig wasn't the source this time
    ]
    try:
        llm.is_configured = lambda o: True
        llm.rewrite_fact = lambda p, l, s, o: (
            "Girard, Ohio: home to Trumbull County's one and only hanging. "
            "That's right, they really dropped the axe on this one guy.\n")
        # "one and only" is scoped to the county, so it needs a fact that
        # names Girard — and "dropped the axe" is tasteless framing besides.
        assert funfacts._llm_facts("Girard, Ohio", "girard, OH", seeds, {}) == []
        # The honest regional telling still passes: it claims nothing for
        # Girard, so the county-level fact is enough to back it.
        llm.rewrite_fact = lambda p, l, s, o: (
            "Over in Trumbull County, Ira West Gardner was the only man hanged "
            "there, executed in 1832 for murdering his stepdaughter.\n")
        got = funfacts._llm_facts("Girard, Ohio", "girard, OH",
                                  seeds + [funfacts._AREA_PREFIX + hang], {})
        assert got and "Trumbull County" in got[0], got
    finally:
        llm.rewrite_fact, llm.is_configured = orig_rw, orig_cfg
    print("[PASS] county hanging can't be re-attributed to the town")


def test_search_seeds_and_query():
    """The debug log for `!funfact girard, OH`: the bot asked the web for
    "history crime scandal", got a truncated page title, a crime-stats SEO
    line and one police story, then let the model pad that into invented dark
    history. The query must ask for interesting, and the noise must never be
    treated as ground truth.
    """
    import json
    import urllib.request

    # 1. spicy mode must not ask the web for crime.
    seen = {}
    orig_get = funfacts._http_get_json

    def fake_get(url, params, timeout=8.0):
        seen["q"] = params.get("q")
        return {"items": []}

    funfacts._http_get_json = fake_get
    try:
        funfacts._google_search("girard, OH", True, 200,
                                {"google_api_key": "k", "google_cx": "cx"})
        assert "crime" not in seen["q"], seen
        assert seen["q"] == "girard, OH history facts famous landmark record", seen
    finally:
        funfacts._http_get_json = orig_get

    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"organic": []}).encode()

    def fake_open(req, timeout=10):
        captured["body"] = json.loads(req.data.decode())
        return _Resp()

    orig_open = urllib.request.urlopen
    urllib.request.urlopen = fake_open
    try:
        funfacts._serper_search("girard, OH", True, 200, {"serper_api_key": "k"})
        assert "crime" not in captured["body"]["q"], captured
        assert captured["body"]["q"] == ("girard, OH history facts famous "
                                         "landmark record"), captured
    finally:
        urllib.request.urlopen = orig_open

    # 2. search noise is never ranked as a fact.
    noise = [
        "The Only Man Ever Hanged in Trumbull County: A True ...",
        "Explore crime rates for Girard, OH including murder, assault, and "
        "property crime statistics.",
        "Past Times Arcade in Girard holds the Guinness World Record for the "
        "largest free-play pinball and arcade collection, over 600 machines.",
    ]
    assert funfacts._is_junk_seed(noise[0]), noise[0]
    assert funfacts._is_junk_seed(noise[1]), noise[1]
    assert not funfacts._is_junk_seed(noise[2]), noise[2]
    facts = funfacts._ranked_facts(noise, spice=True, limit=200, count=6)
    assert facts == [noise[2]], facts
    print("[PASS] search asks for interesting, and titles/SEO noise can't be facts")


def test_short_year_grounding():
    """The line the bot actually posted from that log: the seeds said "Jan. 4"
    with no year at all, and the model supplied "back in '04"."""
    import llm
    orig_rw, orig_cfg = llm.rewrite_fact, llm.is_configured
    seeds = ["Kristen V. Schmidt, of Girard, Ohio, charged with murder",
             "Kristen V. Schmidt, 30, was originally charged with felonious "
             "assault after a Jan. 4 shooting on Dearborn Street."]
    try:
        llm.is_configured = lambda o: True
        llm.rewrite_fact = lambda p, l, s, o: (
            "The last thing Kristen V. Schmidt's boyfriend expected? A bullet "
            "from his own gal on Dearborn Street back in '04. True story.\n")
        assert funfacts._llm_facts("girard, OH", "girard, OH", seeds, {}) == []
        # The same line without the invented year survives.
        llm.rewrite_fact = lambda p, l, s, o: (
            "Kristen V. Schmidt of Girard was charged with murder after a "
            "shooting on Dearborn Street.\n")
        got = funfacts._llm_facts("girard, OH", "girard, OH", seeds, {})
        assert got and "Dearborn Street" in got[0], got
    finally:
        llm.rewrite_fact, llm.is_configured = orig_rw, orig_cfg
    print("[PASS] invented '04-style years are dropped, undated lines survive")


def test_region_word_boundaries():
    """'United States' is not another region.

    Nearly every US article lead reads "a city in X County, <State>, United
    States", and the substring check treated that as a foreign mention — so
    harvest() skipped the town's OWN article and the lookup fell through to web
    search (which is where the fabricated Girard crime facts came from).
    """
    cases = [
        ("Girard is a city in southern Trumbull County, Ohio, United States.", "oh", False),
        ("Milford is a borough in Pike County, Pennsylvania, United States.", "pa", False),
        ("Lakemont is a place in Blair County, Pennsylvania, United States.", "wa", True),
        # Word boundaries: Arkansas is not Kansas, West Virginia is not Virginia.
        ("Little Rock is the capital of Arkansas, United States.", "oh", True),
        ("Morgantown is a city in West Virginia, United States.", "wv", False),
        ("Girard is a city in Kansas, United States.", "oh", True),
    ]
    for text, region, want in cases:
        got = funfacts._text_names_other_region(text, region)
        assert got == want, (text, region, want, got)
    print("[PASS] region check: 'United States' is home, Kansas != Arkansas")


def test_attraction_ranking():
    """A town's actual claim to fame must out-rank its census stub.

    "The arcade boasts 600 pinball machines" scored 0 — no _STRONG word
    matched — so the "score < 2" tail-trim dropped it, which is why Girard's
    best fact (Past Times Arcade's Guinness record) never reached chat.
    """
    sents = [
        "The arcade boasts 600 pinball machines, some dating back almost a "
        "century and others just released.",
        "One of the oldest remaining buildings in Girard, the Henry Barnhisel "
        "House, shares tales of community and family history.",
        "The population was 9,603 at the 2020 census.",
        "Explore crime rates for Girard, OH including murder, assault, and "
        "property crime statistics.",
    ]
    facts = funfacts._ranked_facts(sents, spice=True, limit=200, count=6)
    assert any("pinball" in f for f in facts), facts
    assert any("Barnhisel" in f for f in facts), facts
    assert not any("population" in f for f in facts), facts
    assert not any("crime rates" in f for f in facts), facts
    print(f"[PASS] attraction facts rank -> {len(facts)} kept, pinball + Barnhisel in")


def test_llm_only_mode():
    """`"fact_source": "llm"` — one prompt, no retrieval at all.

    Opt-in. The grounded filter can't run (there are no source facts to
    compare against), so this asserts what still holds: the explicit/taste
    filters, the character limit, and the fallback when the model admits it
    knows nothing reliable.
    """
    import llm
    orig_free, orig_cfg = llm.freeform_facts, llm.is_configured
    orig_lookup = funfacts._lookup_all
    calls = []

    def fake_lookup(*a, **kw):
        calls.append(a)
        return {"place": "Girard, Ohio", "facts": ["It was first settled in 1800."]}

    try:
        funfacts._cache.clear()
        funfacts._lookup_all = fake_lookup
        llm.is_configured = lambda o: True
        llm.freeform_facts = lambda p, l, o: (
            "1. Girard is named for Stephen Girard, the Philadelphia "
            "philanthropist who founded Girard Bank.\n"
            "2. Past Times Arcade there holds the Guinness record for the "
            "largest free-play pinball collection.\n"
            "3. A porn studio opened in town in 1972.\n"
            "4. The town threw a hanging party back in the day.\n")
        r = funfacts.get_funfact("girard, OH",
                                 {"fact_source": "llm", "max_fact_chars": 200})
        assert r and r["fact"].startswith("Girard is named for Stephen Girard"), r
        assert not calls, "retrieval must be skipped in llm mode"
        facts = funfacts._cache["llm:girard, oh"]["facts"]
        assert len(facts) == 2, facts              # explicit + tasteless dropped
        assert all(len(f) <= 200 for f in facts), facts
        assert not any("porn" in f or "hanging party" in f for f in facts), facts

        # Model says it knows nothing reliable -> fall back to real sources.
        funfacts._cache.clear()
        llm.freeform_facts = lambda p, l, o: "NOTHING RELIABLE"
        r2 = funfacts.get_funfact("girard, OH", {"fact_source": "llm"})
        assert r2 and "1800" in r2["fact"], r2
        assert calls, "should have fallen back to the sources"
    finally:
        llm.freeform_facts, llm.is_configured = orig_free, orig_cfg
        funfacts._lookup_all = orig_lookup
    print("[PASS] fact_source=llm -> one prompt, filters still apply, fallback works")


def test_namesake_ranking():
    """"Named after <somebody>" is a real fun fact, not filler — Girard, OH is
    named for the Philadelphia philanthropist Stephen Girard, and that used to
    be scored below the census boilerplate and never posted."""
    sents = [
        "Girard is a city in southern Trumbull County, Ohio, United States, "
        "along the Mahoning River.",
        "The population was 9,603 at the 2020 census.",
        "It is believed that Girard takes its name from Stephen Girard, a "
        "French American philanthropist who was the founder of the Girard Bank "
        "and Girard College in Philadelphia.",
        "It was first settled in 1800 but remained static until the Ohio and "
        "Erie Canal was completed.",
    ]
    facts = funfacts._ranked_facts(funfacts._filter_definitions(sents),
                                   limit=200, count=8)
    assert any("Stephen Girard" in f for f in facts), facts
    assert any("1800" in f for f in facts), facts
    assert not any("population was" in f for f in facts), facts
    print(f"[PASS] namesake facts rank -> {len(facts)} facts, Stephen Girard kept")


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
        # Regional results are labelled so they are never read — or rewritten —
        # as facts about the town itself.
        assert all(f.startswith(funfacts._AREA_PREFIX) for f in r), r
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


def test_spicy_dig_respects_the_region():
    """'!funfact Mount Vernon, MO' posted "the Mount Vernon Place Historic
    District scored a spot on the National Register back on November 11, 1971."
    That district is in Baltimore, Maryland (NRHP 71001037), around Baltimore's
    Washington Monument - it has nothing to do with the Lawrence County town.

    _spicy_dig searches by place name alone and its only gate was "the sentence
    contains the name", so any Mount Vernon anywhere qualified. harvest() has
    always checked the region; this path never did."""
    fixture = json.loads((pathlib.Path(__file__).resolve().parent
                          / "fixtures" / "wiki_mount_vernon_mo.json").read_text())
    orig = funfacts._wiki_search_extracts
    funfacts._wiki_search_extracts = (
        lambda q, exchars=7000, limit=2: fixture["queries"].get(q.strip(), []))
    try:
        found = funfacts._spicy_dig(
            "Mount Vernon, Missouri", "Mount Vernon, MO",
            ["Mount Vernon was platted in 1845."], 200)
    finally:
        funfacts._wiki_search_extracts = orig

    joined = " ".join(found).lower()
    for bad in ("baltimore", "maryland", "november 11, 1971"):
        assert bad not in joined, "wrong-place fact leaked: %r in %r" % (bad, found)
    assert any("Lawrence County Courthouse" in f for f in found), found
    assert len(found) == len(set(found)), "the same line came back twice: %r" % found
    print("[PASS] the spicy dig can't borrow another state's landmark")


def test_padded_praise_is_dropped():
    """The prompt forbids "made-up puns or cute filler" and the model does it
    anyway: Mount Vernon came back as "keeps its past alive through a historic
    downtown square and those classic small-town traditions everyone loves."
    No seed supports any of that, and a compliment cannot be grounded."""
    padded = [
        "Founded way back in 1845, Mt. Vernon keeps its past alive through a "
        "historic downtown square and those classic small-town traditions "
        "everyone loves.",
        "Girard has a small-town charm everyone loves.",
        "This hidden gem is steeped in history.",
        "It's a quaint, picture-perfect spot worth a visit.",
    ]
    seeds = ["Mount Vernon was platted in 1845.", "Girard was platted in 1803.",
             "The downtown square dates to 1845."]
    assert funfacts._grounded_filter(
        padded, "Mount Vernon, Missouri", "Mount Vernon, MO", seeds) == []

    # Positive control: real facts the seeds actually contain must survive.
    real = [
        "Stony Dell Resort opened in 1932: a spring-fed pool, stone cabins and "
        "a restaurant built in Ozark giraffe stone.",
        "Stanley Ketchel, the Michigan Assassin, was shot in the back at a "
        "ranch near Conway on October 15, 1910.",
        "Past Times Arcade in Girard holds a Guinness record for its 600 "
        "pinball machines.",
    ]
    kept = funfacts._grounded_filter(real, "Girard, Ohio", "Girard, OH", list(real))
    assert len(kept) == len(real), kept
    print("[PASS] padded praise dropped, real facts kept")


def test_namesake_company_is_not_harvested():
    """'!funfact Conway, missouri' at 10:52 posted "this town's got quite the
    maritime library stacked up despite being nowhere near the ocean."

    Both supporting seeds came from Conway Publishing (pageid 29203197), a
    British imprint of Bloomsbury:
      "It is best known for its publications dealing with nautical subjects."
      "Over its history, it has built an extensive catalogue of books
       specialising in maritime heritage, ship design and construction..."
    Neither sentence names Conway, and requiring the place name is not the fix:
    the Lakemont Park / Leap-The-Dips sentence never says "Lakemont" either, and
    that harvest is the good case. The discriminator is whether the article is
    about a place at all."""
    fixture = json.loads((pathlib.Path(__file__).resolve().parent
                          / "fixtures" / "wiki_conway_mo.json").read_text())
    orig = (funfacts._wiki_search_extracts, funfacts._wiki_extract)
    funfacts._wiki_search_extracts = (
        lambda q, exchars=4000, limit=6: fixture["queries"].get(q.strip(), []))
    funfacts._wiki_extract = lambda t, exchars=0: ""
    try:
        facts = (funfacts._wikipedia("Conway, missouri", spice=True, limit=200)
                 or {}).get("facts") or []
    finally:
        funfacts._wiki_search_extracts, funfacts._wiki_extract = orig

    joined = " ".join(facts).lower()
    for bad in ("maritime", "nautical", "ship design", "bloomsbury", "publisher"):
        assert bad not in joined, "publisher fact leaked: %r in %r" % (bad, facts)

    pub = fixture["queries"]["conway"][1]["extract"]
    assert "Conway Publishing" in pub
    assert funfacts._is_non_place_article(pub), "the imprint must be rejected"
    # ...while a genuine attraction article sharing the town's name stays.
    assert not funfacts._is_non_place_article(
        "Lakemont Park opened in 1894 as a trolley park. Among its notable "
        "attractions is Leap-The-Dips, the world's oldest surviving roller coaster.")
    print("[PASS] a namesake company can't lend a town its facts")


def test_namesake_articles_are_not_harvested():
    """'!funfact Jerome, Missouri' at 10:40 posted four seeds and three of them
    were about other Jeromes: Saint Jerome of Stridon ("He is best known for his
    translation of the Bible into Latin") and Jerome Barnes, a Missouri state
    representative ("Barnes was born in Mississippi"). Searching the bare word
    returns every person who shares the town's name, and each of those titles
    scores 120 in _title_relevance, so they cleared the >= 70 gate."""
    fixture = json.loads((pathlib.Path(__file__).resolve().parent
                          / "fixtures" / "wiki_jerome_mo.json").read_text())
    orig = (funfacts._wiki_search_extracts, funfacts._wiki_extract)
    funfacts._wiki_search_extracts = (
        lambda q, exchars=4000, limit=6: fixture["queries"].get(q.strip(), []))
    funfacts._wiki_extract = lambda t, exchars=0: ""
    try:
        facts = (funfacts._wikipedia("Jerome, Missouri", spice=True, limit=200)
                 or {}).get("facts") or []
    finally:
        funfacts._wiki_search_extracts, funfacts._wiki_extract = orig

    assert facts, "the town's own history fact must survive"
    assert "Fremont Town" in facts[0], facts
    joined = " ".join(facts).lower()
    for bad in ("bible", "vulgate", "pope", "christian moral", "barnes", "mississippi"):
        assert bad not in joined, "namesake fact leaked: %r in %r" % (bad, facts)

    # The gate itself: biographies rejected, places and attractions kept.
    assert funfacts._is_person_article(fixture["queries"]["jerome"][0]["extract"])
    assert funfacts._is_person_article(
        "Jerome Barnes is an American politician who was a member of the "
        "Missouri House of Representatives.")
    assert not funfacts._is_person_article(
        "Jerome is an unincorporated community in western Phelps County, Missouri.")
    assert not funfacts._is_person_article(
        "Lakemont Park is an amusement park in Altoona, Pennsylvania, home to "
        "Leap-The-Dips, the world's oldest surviving roller coaster.")
    print("[PASS] namesake people can't lend a town its facts")


def test_search_key_beats_duckduckgo():
    """A configured Serper key must be consulted before DuckDuckGo. It used to
    sit last in the ladder, which returns on the first source that yields
    anything at all - so one dull DDG blurb ended the search and the key was
    never used. That is what happened for Jerome, Missouri."""
    order = []
    orig = (funfacts._wikipedia, funfacts._duckduckgo,
            funfacts._google_search, funfacts._serper_search)
    funfacts._wikipedia = lambda *a, **k: None
    funfacts._duckduckgo = lambda *a, **k: (
        order.append("duckduckgo"),
        {"place": "Jerome, Missouri",
         "facts": ["It is located on the Gasconade River near Interstate 44."]})[1]
    funfacts._google_search = lambda *a, **k: (order.append("google"), None)[1]
    funfacts._serper_search = lambda *a, **k: (
        order.append("serper"),
        {"place": "Jerome, Missouri",
         "facts": ["Stony Dell Resort opened in 1932."]})[1]
    try:
        with_key = funfacts._try_sources(
            "Jerome, Missouri", True, 200, {"serper_api_key": "k"})
        # Serper answers, so the ladder stops there and DDG is never spent on.
        assert "serper" in order, order
        assert "duckduckgo" not in order or order.index("serper") < order.index("duckduckgo"), order
        assert with_key["facts"] == ["Stony Dell Resort opened in 1932."], with_key

        # Without a key the ladder still degrades gracefully to DuckDuckGo.
        order.clear()
        funfacts._serper_search = lambda *a, **k: (order.append("serper"), None)[1]
        no_key = funfacts._try_sources("Jerome, Missouri", True, 200, {})
        assert "duckduckgo" in order and no_key, (order, no_key)
    finally:
        (funfacts._wikipedia, funfacts._duckduckgo,
         funfacts._google_search, funfacts._serper_search) = orig
    print("[PASS] a configured Serper key is consulted before DuckDuckGo")


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



def test_namesake_person_stubs():
    """A bare 'Name, epithet, epithet' stub is a Wikipedia title, not a fact
    about the town — and a namesake person is a false fact waiting to happen.
    DuckDuckGo's results for 'girard, OH' led with Joe Girard (born Detroit)
    and Hugo Girard (Canadian), and the model turned one into a local."""
    stubs = [
        "Joe Girard, Guinness Book of World Records winning American salesman",
        "Hugo Girard, Canadian Strongman, former World Champion",
    ]
    for s in stubs:
        assert funfacts._is_person_stub(s), f"stub not caught: {s}"
    real = [
        "It is believed that Girard takes its name from Stephen Girard, a French "
        "American philanthropist who founded the Girard Bank in Philadelphia.",
        "Girard's first high school was opened in 1861 as Girard Union High "
        "School; its current variation was originally opened in the 1920s.",
        "It was first settled in 1800 but remained static until the Ohio and Erie "
        "Canal was completed.",
        "Past Times Arcade in Girard has more than 600 pinball machines.",
    ]
    for s in real:
        assert not funfacts._is_person_stub(s), f"real fact wrongly dropped: {s}"
    ranked = funfacts._ranked_facts(stubs + real, spice=True, limit=200)
    assert ranked, "ranking dropped everything"
    for f in ranked:
        assert not funfacts._is_person_stub(f), f"stub reached the pool: {f}"
    assert "Joe Girard" not in " ".join(ranked)
    assert "Hugo Girard" not in " ".join(ranked)


def test_residence_claim_needs_a_seed():
    """The exact lines from the 08:51 debug log: the model adopted a Detroit
    salesman as a local ('called Girard home'), while the true Stephen Girard
    naming fact was dropped over the abbreviation 'Philly'. Both fixed."""
    seeds = [
        "Joe Girard, Guinness Book of World Records winning American salesman",
        "It is believed that Girard takes its name from Stephen Girard, a French "
        "American philanthropist who was the founder of the Girard Bank and "
        "Girard College in Philadelphia.",
        "Girard's first high school was opened in 1861 as Girard Union High "
        "School; its current variation was originally opened in the 1920s.",
        "Hugo Girard, Canadian Strongman, former World Champion",
        "It was first settled in 1800 but remained static until the Ohio and Erie "
        "Canal was completed.",
    ]
    lines = [
        "Joe Girard, Guinness Book of World Records winning American salesman, "
        "called Girard home—talk about local flavor.",
        "The settlement dates back to 1800, got moving when the Ohio and Erie "
        "Canal finished up here.",
        "Some claim Girard owes its name to Stephen Girard, that French American "
        "bank founder from Philly. Could be.",
    ]
    kept = funfacts._grounded_filter(lines, "Girard, Ohio", "girard, OH", seeds)
    joined = " ".join(kept)
    assert "called Girard home" not in joined, "namesake adopted as a local"
    assert "Philly" in joined, "true line dropped over an abbreviation"
    # A residence claim is fine when a seed actually places someone in town.
    placed = seeds + ["Suffragist Elizabeth Hauser was born in Girard in 1873 and "
                      "edited the Girard Grit."]
    line = "Suffragist Elizabeth Hauser was born in Girard in 1873 and edited the Girard Grit."
    assert funfacts._grounded_filter([line], "Girard, Ohio", "girard, OH", placed), \
        "a sourced residence claim must survive"


def test_curated_girard_facts():
    """The facts we verified by hand must reach chat even when Wikipedia is
    rate-limited and the web sources return namesakes."""
    res = funfacts._spicy_db("girard, OH", 200)
    assert res, "no curated entry for Girard, OH"
    assert res["place"] == "Girard, Ohio", res["place"]
    text = " ".join(res["facts"])
    for needle in ("Past Times Arcade", "Guinness", "1,041", "Barnhisel",
                   "1993", "1836", "Elizabeth Hauser"):
        assert needle in text, f"missing from curated facts: {needle}"
    assert all(len(f) <= 200 for f in res["facts"]), "curated fact over 200 chars"
    assert not any(funfacts._is_person_stub(f) for f in res["facts"])


def test_llm_preamble_dropped():
    """The 08:51 reply opened with 'Girard, Ohio's got some real characters for
    the record books...' and that preamble was posted as the first fact."""
    reply = ("Girard, Ohio's got some real characters for the record books, and "
             "not just the strongman type. Just don't ask us how it got the "
             "name\u2014sources disagree. Here's the real deal:\n\n"
             "- It was first settled in 1800 but remained static until the Ohio "
             "and Erie Canal was completed.\n"
             "- Girard Union High School first opened its doors way back in "
             "1861; the current building's been around since the 1920s.")
    import llm
    orig = (llm.is_configured, llm.rewrite_fact)
    llm.is_configured = lambda opts=None: True
    llm.rewrite_fact = lambda *a, **k: reply
    try:
        out = funfacts._llm_facts("Girard, Ohio", "girard, OH",
                                  ["It was first settled in 1800 but remained "
                                   "static until the Ohio and Erie Canal was "
                                   "completed."],
                                  {"spice": "spicy", "llm_api_key": "x"})
    finally:
        llm.is_configured, llm.rewrite_fact = orig
    assert out, "every line was dropped"
    joined = " ".join(out)
    assert "real characters" not in joined, "preamble reached the pool"
    assert "real deal" not in joined, "preamble reached the pool"
    assert any("1800" in f for f in out)


_INDIAN_LAKE_ITEMS = [
    {"title": "Indian Lake (Ohio)", "extract": (
        "Indian Lake (formerly Lewistown Reservoir) is a reservoir in Logan "
        "County, western Ohio, in the United States. The outlet of the lake, at "
        "the bulkhead built in the 1850s by Irish laborers, is the beginning of "
        "the Great Miami River. At 5,104 acres (2,066 ha), Indian Lake is the "
        "second largest inland lake in Ohio.")},
    # Stand-in for whichever northeast-Ohio article the live search returned
    # for these two sentences (the 1786 Moravian settlement is Pilgerruh in the
    # Cuyahoga Valley, near Cleveland — nps.gov). The exact title is not known;
    # the sentences are verbatim from the 09:19 debug log.
    {"title": "Cuyahoga Valley, Ohio", "extract": (
        "The valley is in northeast Ohio. The first European settlement in the "
        "area was founded in 1786 by Moravian missionaries. Jan &amp; Dean "
        "included it on their 1985 album Silver Summer.")},
    {"title": "Avon Lake, Ohio", "extract": (
        "Avon Lake is a city in Lorain County, Ohio, United States. Avon Lake "
        "was first settled in the 17th century and was, along with Avon, Bay "
        "Village, and Westlake, inhabited by the Erie.")},
    {"title": "Lake County, Ohio", "extract": (
        "Lake County is a county in the U.S. state of Ohio. Its county seat is "
        "Painesville, and its largest city is Mentor.")},
]


def test_wrong_place_harvest():
    """The 09:19 lookup posted 'Its county seat is Painesville' and an Avon Lake
    sentence for Indian Lake, Ohio. _title_relevance scores any Ohio article
    containing the word 'lake' at 128 (20 for 'lake' + 100 for 'Ohio' + 8 for
    the comma), so those titles enter the harvest — and before the fix every
    sentence from them was treated as a fact about Indian Lake."""
    assert funfacts._title_relevance("Avon Lake, Ohio", "indian lake", "oh") >= 70
    assert funfacts._title_relevance("Lake County, Ohio", "indian lake", "oh") >= 70

    orig = funfacts._wiki_search_extracts
    funfacts._wiki_search_extracts = lambda q, exchars=4000, limit=6: [
        dict(it) for it in _INDIAN_LAKE_ITEMS]
    try:
        res = funfacts._wikipedia("indian lake, oh", spice=True, limit=200)
    finally:
        funfacts._wiki_search_extracts = orig
    assert res, "no facts at all"
    joined = " ".join(res["facts"])
    for wrong in ("Painesville", "Mentor", "Avon", "Erie", "Moravian",
                  "Silver Summer", "1786"):
        assert wrong not in joined, f"another place's fact leaked: {wrong}"
    assert "5,104 acres" in joined or "Great Miami River" in joined, \
        "Indian Lake's own facts were lost"


def test_html_entities_unescaped():
    out = funfacts._sentences("Jan &amp; Dean included it on their 1985 album.")
    assert out and "&amp;" not in out[0], out


def test_llm_duplicate_lines_deduped():
    """The 09:19 reply stated the 1786 settlement twice in different words."""
    reply = ("- Moravian missionaries were the first Europeans to settle the "
             "area way back in 1786.\n"
             "- The first European settlement in the region was founded by "
             "Moravian missionaries in 1786.")
    import llm
    orig = (llm.is_configured, llm.rewrite_fact)
    llm.is_configured = lambda opts=None: True
    llm.rewrite_fact = lambda *a, **k: reply
    try:
        out = funfacts._llm_facts(
            "Indian Lake (Ohio)", "Indian Lake, OH",
            ["The first European settlement in the area was founded in 1786 by "
             "Moravian missionaries."],
            {"spice": "spicy", "llm_api_key": "x"})
    finally:
        llm.is_configured, llm.rewrite_fact = orig
    assert len(out) == 1, f"duplicate survived: {out}"


def test_curated_indian_lake_facts():
    res = funfacts._spicy_db("Indian Lake, OH", 200)
    assert res, "no curated entry for Indian Lake, OH"
    text = " ".join(res["facts"])
    for needle in ("Lewistown Reservoir", "Irish laborers", "Miami and Erie Canal",
                   "5,104 acres", "Sandy Beach", "1924", "Minnewawa", "1931",
                   "1961", "1975", "Great Miami River"):
        assert needle in text, f"missing from curated facts: {needle}"
    assert all(len(f) <= 200 for f in res["facts"])


_SONG_ITEMS = [
    {"title": "Indian Lake (Ohio)", "extract": (
        "Indian Lake is a reservoir in Logan County, western Ohio. At 5,104 "
        "acres, Indian Lake is the second largest inland lake in Ohio.")},
    {"title": "Indian Lake (song)", "extract": (
        "Indian Lake is a song written by Tony Romeo. It was recorded by the "
        "pop band The Cowsills, and included on their 1968 album Captain Sad "
        "and His Ship of Fools. Jan & Dean included it on their 1985 album "
        "Silver Summer.")},
]


def test_work_titles_not_harvested():
    """'Indian Lake (song)' is the 1968 Cowsills single. Its cover-versions list
    says 'Jan & Dean included it on their 1985 album Silver Summer' — where 'it'
    is the song. That sentence was posted as a fun fact about Indian Lake, Ohio
    (09:19) and Indian Lake, Missouri (09:29). The title matches the place
    exactly, so neither the region check nor a name-the-place check catches it."""
    assert funfacts._is_road_or_meta_title("Indian Lake (song)")
    assert funfacts._is_road_or_meta_title("Girard (surname)")
    assert not funfacts._is_road_or_meta_title("Indian Lake (Ohio)")
    assert not funfacts._is_road_or_meta_title("Indian Lake State Park")

    orig = funfacts._wiki_search_extracts
    funfacts._wiki_search_extracts = lambda q, exchars=4000, limit=6: [
        dict(it) for it in _SONG_ITEMS]
    try:
        res = funfacts._wikipedia("indian lake, oh", spice=True, limit=200)
    finally:
        funfacts._wiki_search_extracts = orig
    assert res, "no facts at all"
    joined = " ".join(res["facts"])
    for wrong in ("Jan & Dean", "Silver Summer", "Cowsills", "Tony Romeo", "1985"):
        assert wrong not in joined, f"song-article fact leaked: {wrong}"
    assert "5,104 acres" in joined


def test_reputation_claims_need_a_source():
    """The 09:29 reply called Jan & Dean 'surf rock legends' and said the album
    put 'this sleepy Missouri spot on the musical map' — four claims no seed
    made, all invisible to _claims() because they are ordinary words."""
    seed = "Jan & Dean included it on their 1985 album Silver Summer."
    line = ("Indian Lake got its claim to fame in 1985 when surf rock legends "
            "Jan & Dean featured it on their album Silver Summer, putting this "
            "sleepy Missouri spot on the musical map.")
    assert not funfacts._grounded_filter(
        [line], "Indian Lake, Missouri", "Indian Lake, Missouri", [seed]), \
        "invented framing survived"
    # A reputation claim the seeds actually make must still pass.
    sourced = ["Sandy Beach Amusement Park was famous as the Midwest's Million "
               "Dollar Playground."]
    assert funfacts._grounded_filter(
        ["Sandy Beach was famous as the Midwest's Million Dollar Playground."],
        "Indian Lake (Ohio)", "Indian Lake, OH", sourced)


def test_curated_entry_region_match():
    """A curated entry describes ONE place, but its keys are stateless
    ('girard', 'indian lake'). Without a region check, 'Girard, PA' was served
    the Girard, Ohio facts and 'Indian Lake, Missouri' the Ohio lake's — the
    same wrong-place error the harvest fixes exist to stop."""
    assert funfacts._spicy_db("Girard, OH", 200)["place"] == "Girard, Ohio"
    assert funfacts._spicy_db("Girard, PA", 200) is None
    assert funfacts._spicy_db("Indian Lake, OH", 200)["place"] == "Indian Lake, Ohio"
    assert funfacts._spicy_db("Indian Lake, Missouri", 200) is None
    # A stateless request still gets the curated entry.
    assert funfacts._spicy_db("Indian Lake", 200) is not None


_MO_ITEMS = [
    {"title": "Indian Lake, Missouri", "extract": (
        "Indian Lake is an unincorporated community and census-designated place "
        "in Crawford County, Missouri, United States. It is in the northwestern "
        "part of the county, surrounding a lake of the same name. The community "
        "is 5 miles northwest of Cuba and Interstate 44.")},
    {"title": "Indian Lake (song)", "extract": (
        "Indian Lake is a song written by Tony Romeo. Jan & Dean included it on "
        "their 1985 album Silver Summer.")},
]


def test_full_state_name_region():
    """Typing the state out must behave like the abbreviation. _US_STATES is
    keyed by abbreviation, so for 'Indian Lake, Missouri' the 'United States'
    in the lead counted as a foreign region and the place's own article was
    discarded — which is what left the 09:29 lookup with nothing but the song
    article to quote."""
    extract = ("Indian Lake is an unincorporated community in Crawford County, "
               "Missouri, United States.")
    assert not funfacts._text_names_other_region(extract, "mo")
    assert not funfacts._text_names_other_region(extract, "missouri")
    assert funfacts._text_names_other_region(extract, "oh")
    assert funfacts._text_names_other_region(extract, "ohio")
    # Cross-state rejection must survive both spellings.
    assert funfacts._text_names_other_region("Indian Lake (Ohio)", "missouri")
    assert funfacts._text_names_other_region("Girard, Ohio", "pennsylvania")
    # And the word-boundary guards must not regress.
    assert not funfacts._text_names_other_region("a town in Virginia", "west virginia")
    assert funfacts._text_names_other_region("a town in West Virginia", "virginia")
    assert not funfacts._text_names_other_region("a town in Kansas", "arkansas")

    orig = funfacts._wiki_search_extracts
    funfacts._wiki_search_extracts = lambda q, exchars=4000, limit=6: [
        dict(it) for it in _MO_ITEMS]
    try:
        res = funfacts._wikipedia("Indian Lake, Missouri", spice=True, limit=200)
    finally:
        funfacts._wiki_search_extracts = orig
    assert res, "the place's own article was discarded again"
    assert res["place"] == "Indian Lake, Missouri", res["place"]
    assert "Jan & Dean" not in " ".join(res["facts"])


def test_comma_less_region():
    """Viewers type 'Cuba Missouri', not 'Cuba, Missouri'. Losing the region
    made the island nation outrank the town (120 vs 118) and switched off every
    region guard, so Wikipedia returned nothing usable."""
    assert funfacts._query_region("Cuba Missouri") == "missouri"
    assert funfacts._query_core("Cuba Missouri") == "cuba"
    assert funfacts._query_region("Cuba MO") == "mo"
    assert funfacts._query_core("girard oh") == "girard"
    assert funfacts._query_region("Raleigh North Carolina") == "north carolina"
    assert funfacts._query_core("Cuba City Wisconsin") == "cuba city"
    # Place names that merely end in a state word must not be split.
    for q in ("Kansas City", "New York", "Los Angeles", "Oklahoma City"):
        assert funfacts._query_region(q) == "", q
        assert funfacts._query_core(q) == q.lower(), q
    # And the region bonus must put the town above the country again.
    assert funfacts._title_relevance("Cuba, Missouri", "cuba", "missouri") > \
        funfacts._title_relevance("Cuba", "cuba", "missouri")


def test_padding_claims_dropped():
    """'Holds the crown', 'bustling heart of the region', 'the star' and
    'proud to be' are significance the sources never claimed."""
    seeds = ["Cuba is the largest city in Crawford County.",
             "Interstate 44 now runs through Cuba.",
             "It was named after the island of Cuba."]
    padded = [
        "Cuba, Missouri holds the crown as the largest city in Crawford County, "
        "making it the bustling heart of the region.",
        "You'll find Cuba along Interstate 44 these days, but Route 66's the star.",
        "Crawford County's largest city is proud to be Cuba, Missouri.",
    ]
    for ln in padded:
        assert not funfacts._grounded_filter(
            [ln], "Cuba, Missouri", "Cuba, Missouri", seeds), ln
    plain = ["Cuba, Missouri got its name from the island of Cuba.",
             "You'll find Cuba along Interstate 44 these days."]
    for ln in plain:
        assert funfacts._grounded_filter(
            [ln], "Cuba, Missouri", "Cuba, Missouri", seeds), ln


def _cuba_fixture():
    import json, os
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "fixtures", "wiki_cuba_mo.json"), encoding="utf-8") as fh:
        return json.load(fh)


def test_full_article_extract():
    """MediaWiki clamps exchars to 1200 and says nothing about it, so asking
    for 4000 or 7000 returned the lead only. Everything Cuba, Missouri is
    actually known for — the World's Largest Rocking Chair, Bette Davis and
    Amelia Earhart, the Wagon Wheel Motel — sits below character 1200 of the
    real article. Recorded from the live API in fixtures/wiki_cuba_mo.json."""
    full = _cuba_fixture()["extract"]
    lead = full[:1200]

    def ranked(text):
        return funfacts._ranked_facts(
            funfacts._filter_definitions(funfacts._sentences(text)),
            spice=True, limit=200)

    assert "Rocking Chair" not in " ".join(ranked(lead)), "the lead was not the problem"
    assert any("Rocking Chair" in f for f in ranked(full)), "full article lost it too"
    assert any("Wagon Wheel" in f for f in ranked(full))

    # The single-title request must omit exchars entirely.
    seen = {}
    orig = funfacts._http_get_json
    funfacts._http_get_json = lambda url, params, timeout=8.0: (
        seen.update(params) or {"query": {"pages": [{"extract": full}]}})
    try:
        got = funfacts._wiki_extract("Cuba, Missouri", exchars=0)
    finally:
        funfacts._http_get_json = orig
    assert "exchars" not in seen, f"exchars still sent: {seen}"
    assert "Rocking Chair" in got


def test_curated_cuba_missouri():
    res = funfacts._spicy_db("Cuba, Missouri", 200)
    assert res, "no curated entry for Cuba, Missouri"
    text = " ".join(res["facts"])
    for needle in ("Red Rocker", "42 feet", "Big Red Apple", "Bette Davis",
                   "Amelia Earhart", "Mural City", "Wagon Wheel", "1857"):
        assert needle in text, f"missing: {needle}"
    assert all(len(f) <= 200 for f in res["facts"])
    # Bare "Cuba" is the island nation — it must not match.
    assert funfacts._spicy_db("Cuba", 200) is None


def test_location_line_never_outranks_history():
    """Jerome, Missouri's entire article is one 'It is located on the Gasconade
    River near Interstate 44...' sentence and one history sentence. The location
    line scored 6 (because 'interstate' is a ranking word) and the history 4, so
    the dull line was the only seed the model got."""
    both = ["It is located on the Gasconade River near Interstate 44.",
            'Jerome was originally called "Fremont Town", and under the latter '
            "name was platted in 1867 when the railroad was extended."]
    out = funfacts._ranked_facts(both, spice=True, limit=200)
    assert out and "Fremont Town" in out[0], out
    assert not any("It is located" in f for f in out)
    # A pool of nothing but a location line stays empty, so the caller falls
    # through to the next source rather than posting "it is near the interstate".
    assert funfacts._ranked_facts(
        ["It is located on the Gasconade River near Interstate 44."],
        spice=True, limit=200) == []
    # ...and a namesake person stub is never posted either.
    assert funfacts._ranked_facts(
        ["Joe Girard, Guinness Book of World Records winning American salesman"],
        spice=True, limit=200) == []

# Every string below is a line the bot actually posted in #hardclaws. They are
# kept verbatim because paraphrasing them is how a regression test stops
# matching the thing it was written for.
_REAL_BAD_FACTS = (
    ("But in that same year, the Latter Day Saint movement founder, Joseph "
     "Smith, was killed in the Carthage Jail, about 30 miles away from "
     "Nauvoo.", "dangling"),
    ("Of the fifty U.S. states, Illinois has the fifth-largest gross domestic "
     "product (GDP), the sixth-largest population, and the 25th-most land "
     "area.", "boring"),
    ("In 1840, one hundred of those residents who did not have passports were "
     "arrested, leading to the Graham Affair, which was resolved in part with "
     "the intercession of Royal Navy officials.", "dangling"),
    ("What did Grima do?", "fragment"),
    ("Previously the Portswood Hotel, it was named after J. R. R. Tolkien's "
     "book The Hobbit in 1989.", "dangling"),
    ("The National Register of Historic Places is the official list of the "
     "Nation's historic places worthy of preservation.", "boring"),
    ("Historic Landmark plaque.", "fragment"),
    ("Seeds, such as pumpkin seeds or sunflower seeds", "fragment"),
)


def test_harvested_facts_must_stand_alone():
    """A fact is posted alone in chat, with no article around it.

    All eight of these reached chat and passed every filter that existed,
    because those filters were written for small-town census boilerplate. The
    complaint was 'they arent facts or they are just boring', and these are
    the two halves of it: fragments and questions are not facts, and a ranking
    table in prose form or a sentence that opens with 'But in that same year'
    is not readable on its own.
    """
    checks = {"dangling": funfacts._is_dangling,
              "fragment": funfacts._is_fragment,
              "boring": funfacts._is_boring}
    for sentence, why in _REAL_BAD_FACTS:
        assert checks[why](sentence), (why, sentence)
        # And through the funnel they actually travel, not just the predicate.
        assert funfacts._ranked_facts([sentence], limit=200) == [], sentence


def test_real_facts_survive_the_stand_alone_gate():
    """The other half: the gate must not eat the facts worth posting.

    Two of these were dropped by the first draft - 'the latter' was treated as
    an unresolvable reference when the antecedent is in the same sentence, and
    a missing present-tense verb pattern made a 122-character sentence look
    like a fragment. Both are real facts about real places.
    """
    good = (
        "One of the oldest remaining buildings in Girard, the Henry Barnhisel "
        "House, shares tales of community and family history.",
        "Jerome was originally called \"Fremont Town\", and under the latter "
        "name was platted in 1867 when the railroad was extended.",
        "Leap-The-Dips in Lakemont is the oldest operating roller coaster in "
        "the world.",
        "Cuba, Missouri is home to the world's largest rocking chair.",
        "It was platted in 1867 as Fremont Town.",
        "The arcade boasts 600 pinball machines, some dating back almost a "
        "century and others just released.",
        "Bette Davis and Amelia Earhart both visited the town in 1937.",
    )
    for sentence in good:
        assert not funfacts._is_dangling(sentence), sentence
        assert not funfacts._is_fragment(sentence), sentence
        assert not funfacts._is_boring(sentence), sentence
        assert funfacts._ranked_facts([sentence], limit=200), sentence


def test_facts_are_ranked_by_relevance_to_what_was_asked():
    """'!funfact huorns' and '!funfact trail mix' both answered with
    sentences that had nothing to do with the query.

    This is a ranking bonus, not a hard drop: a stub article whose only usable
    sentence does not repeat the name must still produce an answer.
    """
    about = "Trail mix is a snack of dried fruit, nuts and sometimes chocolate."
    unrelated = ("The National Register of Historic Places is a federal list "
                 "of districts, sites and structures worthy of preservation.")
    out = funfacts._ranked_facts([unrelated, about], limit=200,
                                 subject="trail mix")
    assert out and "Trail mix" in out[0], out
    # With no subject given, nothing is penalised and nothing is invented.
    assert funfacts._ranked_facts([unrelated, about], limit=200), "still answers"
    print("[PASS] the fact that names the subject outranks one that does not")


def main():
    test_trim()
    test_trim_keeps_whole_sentences()
    test_smk_game()
    test_rotation()
    test_busy_unavailable()
    test_spicy_db()
    test_curated_girard_facts()
    test_curated_indian_lake_facts()
    test_curated_entry_region_match()
    test_curated_cuba_missouri()
    test_ranked_dedupe()
    test_ranked_filter()
    test_parse_geocode()
    test_remote_fallback()
    test_google_source()
    test_spicy_dig()
    test_bio_filter()
    test_definition_filter()
    test_region_word_boundaries()
    test_full_state_name_region()
    test_comma_less_region()
    test_namesake_ranking()
    test_namesake_person_stubs()
    test_location_line_never_outranks_history()
    test_attraction_ranking()
    test_editorialising_is_not_a_rewrite()
    test_explicit_filter()
    test_tasteless_filter()
    test_grounded_filter()
    test_residence_claim_needs_a_seed()
    test_reputation_claims_need_a_source()
    test_padding_claims_dropped()
    test_invented_claim_filter()
    test_county_hanging_reattribution()
    test_search_seeds_and_query()
    test_short_year_grounding()
    test_llm_only_mode()
    test_weird_fallback()
    test_merge_curated()
    test_region_dig()
    test_fit_fact()
    test_filler_filter()
    test_meta_line_filter()
    test_llm_preamble_dropped()
    test_llm_duplicate_lines_deduped()
    test_serper_source()
    test_search_key_beats_duckduckgo()
    test_namesake_articles_are_not_harvested()
    test_namesake_company_is_not_harvested()
    test_spicy_dig_respects_the_region()
    test_padded_praise_is_dropped()
    test_wiki_multi_title()
    test_full_article_extract()
    test_wrong_place_harvest()
    test_work_titles_not_harvested()
    test_html_entities_unescaped()
    test_wiki_cooldown()
    test_harvested_facts_must_stand_alone()
    test_real_facts_survive_the_stand_alone_gate()
    test_facts_are_ranked_by_relevance_to_what_was_asked()
    print("\nALL PASSED ✔")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
