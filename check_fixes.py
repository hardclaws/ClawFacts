#!/usr/bin/env python3
"""Print which of the Girard-era fixes are present in the code you are running.

    python3 check_fixes.py

Run this after copying files out of a zip / applying a patch. If a line says
False (or `_EXTRACT_PAGE_CAP` is not 4), the copy you are running is older than
the fix it names — no need to guess from chat behaviour.
"""
import pathlib
import time
import sys

import funfacts


def main() -> int:
    # Built once so the checks below can exercise the real classes rather than
    # grep for a string that happens to appear in a comment.
    import os
    import tempfile

    import auth as _auth
    import access as access_mod
    import bot as _bot
    import customcmds as _cc_mod
    import shoutout as _so
    import llm as _llm2
    import chatai as _ch2
    _bot2 = pathlib.Path("bot.py").read_text(encoding="utf-8")
    _auth_src = pathlib.Path("auth.py").read_text(encoding="utf-8")

    def _fresh():
        return _cc_mod.CommandSet(
            path=os.path.join(tempfile.mkdtemp(prefix="clawfacts-check-"),
                              "cc.json"),
            reserved=_bot.RESERVED_COMMANDS)

    _cc = _fresh()

    def _headlines_are_top_stories():
        """'whats the leading headlines for today' was searched as the
        words 'leading headlines' and quoted a roundup page's title.
        A subject-less headline ask reads the top-stories feed, three
        real titles a message, and roundup titles are never quoted."""
        for fn in ("_news_generic", "_roundup", "_google_news_top",
                   "_pack_headlines"):
            if not callable(getattr(funfacts, fn, None)):
                return False
        if not (funfacts._news_generic("whats the leading headlines for today")
                and funfacts._news_generic("that is not a headline where the news")
                and funfacts._news_generic("whats the news")
                and not funfacts._news_generic("any news on the LA bus crash")
                and not funfacts.news_question("whats your news source")):
            return False
        if not funfacts._roundup("Top news of the day September 16 2026") \
                or funfacts._roundup("Fed holds rates steady as inflation cools"):
            return False
        saved = (funfacts._google_news_top, funfacts._google_news_rss,
                 funfacts._tavily_news)
        funfacts._google_news_top = lambda limit=12, options=None: [
            ("Top news of the day September 16 2026", "thehindu.com",
             "Tue, 15 Sep 2026 20:00:00 GMT"),
            ("Fed holds rates steady as inflation cools", "Reuters",
             "Wed, 16 Sep 2026 14:07:49 GMT"),
            ("Senate passes stopgap funding bill", "CNN",
             "Wed, 16 Sep 2026 12:01:00 GMT")]
        funfacts._google_news_rss = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("search feed used for a generic ask"))
        funfacts._tavily_news = lambda *a, **k: []
        try:
            with funfacts._cache_lock:
                funfacts._cache.clear()
            got = funfacts.get_funfact("whats the leading headlines for today",
                                       {"max_message_chars": 450})
        except Exception:
            return False
        finally:
            (funfacts._google_news_top, funfacts._google_news_rss,
             funfacts._tavily_news) = saved
            with funfacts._cache_lock:
                funfacts._cache.clear()
        return bool(got and got.get("news") and got.get("place") == "top headlines"
                    and got["fact"].startswith("Fed holds rates steady")
                    and "Senate passes" in got["fact"]
                    and "Top news of the day" not in got["fact"])

    def _misspelt_town_is_that_town():
        """'sunrise in Hintok, ok' was answered for a footpath in
        Thailand, labelled in Thai. The typed state pins the search,
        a road is not a settlement, labels are English, and a
        misspelling gets a fuzzy match on places in that state."""
        for fn in ("_region_of", "_geo_in_region", "_photon_geocode",
                   "_close_name"):
            if not callable(getattr(funfacts, fn, None)):
                return False
        if funfacts._region_of("Hintok, ok") != ("ok", "oklahoma", "us"):
            return False
        thai = [{"lat": "14.36", "lon": "98.94", "type": "footway",
                 "addresstype": "road", "name": "Hintok Cut",
                 "display_name": "Hintok Cut, Sai Yok, Thailand",
                 "address": {"road": "Hintok Cut", "municipality": "Sai Yok",
                             "province": "Kanchanaburi Province",
                             "country": "Thailand", "country_code": "th"}}]
        photon = {"features": [{"properties": {
            "osm_key": "place", "osm_value": "town", "name": "Hinton",
            "state": "Oklahoma", "country": "United States",
            "countrycode": "US"}, "geometry": {"coordinates": [-98.36, 35.47]}}]}

        def http(url, params, timeout=8.0):
            if url == funfacts.OSM_API:
                if params.get("accept-language") != "en":
                    raise AssertionError("labels must be asked for in English")
                return [] if params.get("countrycodes") == "us" else thai
            if url == funfacts.PHOTON_API:
                return photon
            raise AssertionError(url)

        saved = funfacts._http_get_json
        funfacts._http_get_json = http
        try:
            geo = funfacts._osm_geocode("Hintok, ok")
        except Exception:
            return False
        finally:
            funfacts._http_get_json = saved
        return bool(geo and geo.get("name") == "Hinton"
                    and geo.get("state") == "Oklahoma")

    def _working_memory_holds():
        import ongoing as _og
        import tempfile as _tf
        names = ("doc", "docbot")
        d = _tf.mkdtemp(prefix="clawfacts-check-")
        og = _og.Ongoing(os.path.join(d, "og.json"))
        start = _og.control("Docbot lets do a test run. Topic would be Cycling "
                            "and lets make it 3 rounds", og.snapshot(), names)
        if not (start and start.kind == "start" and start.rounds == 3):
            return False
        og.begin(start, "Hardclaws", 0.0)
        r2 = _og.control("lets get Round 2 going. Give 30secs to answer then "
                         "start round 3 30secs after that", og.snapshot(), names)
        if not (r2 and r2.kind == "round" and r2.n == 2
                and r2.cadence == (30.0, 30.0)):
            return False
        tally = _og.control("keep count of Dirty Lepages and we will tell the "
                            "bot when we spot one", og.snapshot(), names)
        if not (tally and tally.kind == "tally_start"
                and tally.label == "Dirty Lepages"):
            return False
        og.apply_tally(tally, "Hardclaws", 0.0)
        bump = _og.control("dirty lepage!", og.snapshot(), names)
        if not (bump and bump.kind == "tally_add"):
            return False
        og.apply_tally(bump, "Hardclaws", 1.0)
        prompt = _ch2.user_prompt([], "kvack", "hows it going",
                                  ongoing=og.prompt_lines(now=2.0))
        fresh = _og.Ongoing(og.path)
        return ("WHAT IS GOING ON" in prompt and "Dirty Lepages: 1" in prompt
                and "hosting a 3-round Cycling quiz" in prompt
                and fresh.tallies.get("dirty lepage", {}).get("count") == 1
                and _og.control("what is the current temperature in Rolla, "
                                "Missouri?", og.snapshot(), names) is None
                and hasattr(_bot.TwitchBot, "_do_step"))

    def _admin_panel_is_locked_down():
        import adminpanel as _ap
        import tempfile as _tf
        d = _tf.mkdtemp(prefix="clawfacts-check-")
        users = _ap.Users(os.path.join(d, "u.json"))
        ok, _ = users.set("doc", "correct horse battery", "admin")
        raw = pathlib.Path(users.path).read_text(encoding="utf-8")
        return (ok and "correct horse" not in raw
                and users.verify("doc", "correct horse battery") == "admin"
                and users.verify("doc", "nope") is None
                and not users.set("x", "short", "mod")[0]
                and _ap._bind_is_safe("127.0.0.1")[0]
                and _ap._bind_is_safe("100.101.102.103")[0]
                and not _ap._bind_is_safe("0.0.0.0")[0]
                and not _ap._bind_is_safe("8.8.8.8")[0]
                and _ap.start_panel({"admin_panel_enabled": True,
                                     "admin_panel_bind": "0.0.0.0"},
                                    _ap.BotControl(), users) is None
                and _bot.DEFAULTS.get("admin_panel_enabled") is False
                and _bot.DEFAULTS.get("admin_panel_bind") == "127.0.0.1"
                and "mod" in _ap.ROLES
                and "--admin-user" in pathlib.Path("bot.py").read_text(
                    encoding="utf-8")
                and "admin_users.json" in pathlib.Path(".gitignore").read_text(
                    encoding="utf-8"))

    def _survives_restart():
        path = os.path.join(tempfile.mkdtemp(prefix="clawfacts-check-"),
                            "cc.json")
        made = _cc_mod.CommandSet(path=path, reserved=_bot.RESERVED_COMMANDS)
        made.add("survivor", "still here")
        if not made.save():
            return False
        again = _cc_mod.CommandSet(path=path, reserved=_bot.RESERVED_COMMANDS)
        return again.get("survivor") == "still here"

    _bot_src = pathlib.Path("bot.py").read_text(encoding="utf-8")

    def _shoutout_says_nothing_false():
        """Sweep the shoutout pools for claims and clause collisions."""
        import random

        import shoutout as _so

        rng = random.Random(20260828)
        streams = [None, {"game_name": "Fortnite"}, {"game_name": ""}]
        counts = [1, 42, None, "nope"]
        for theme in _so.THEMES:
            for _ in range(400):
                stream = rng.choice(streams)
                line = _so.format_raid("RoadDog_88", rng.choice(counts),
                                       "roaddog_88", {}, theme=theme,
                                       raider_stream=stream)
                low = line.lower()
                game = (stream or {}).get("game_name") or ""
                if "last seen" in low:
                    # No last_game is passed here, so any past-tense claim is
                    # unsourced and wrong.
                    return False
                if game:
                    # A confirmed game must always be named.
                    if game.lower() not in low:
                        return False
                else:
                    # No stream row, so no game and no present-tense claim.
                    for phrase in ("fortnite", "live right now", "right now",
                                   "currently on", "currently out"):
                        if phrase in low:
                            return False
                if low.count("show them some love") != 1:
                    return False
                if low.count("raid") > 1:
                    return False
        return True

    def _last_seen_is_sourced():
        """The two game claims must come from different endpoints, and the
        tense must say which. Checked on the pools rather than by looking for
        one phrase, so rewording the copy cannot silently break it.

        Get Channel Information's game_name is documented as the game the
        broadcaster "is playing or last played"; Get Streams answers only for
        channels live right now and returns an empty data array otherwise.
        """
        import shoutout as _so

        present = ("right now", "currently", "this minute", "as we speak",
                   "very moment", "live on")
        past = ("last ", "were on", "was the last", "logged off",
                "signed off")
        for theme in _so.THEMES:
            for line in _so.PRAISE_LAST[theme]:
                low = line.lower()
                if any(w in low for w in present):
                    return False
                if not any(w in low for w in past):
                    return False
            for line in _so.PRAISE_LIVE[theme]:
                low = line.lower()
                if any(w in low for w in past):
                    return False
                if not any(w in low for w in present):
                    return False

        for theme in _so.THEMES:
            last = _so.format_raid("RoadDog_88", 5, "roaddog_88", {},
                                   theme=theme, last_game="Fortnite").lower()
            if "fortnite" not in last or any(w in last for w in present):
                return False
            live = _so.format_raid("RoadDog_88", 5, "roaddog_88", {},
                                   theme=theme,
                                   raider_stream={"game_name": "Fortnite"},
                                   last_game="Minecraft").lower()
            # Being live wins: no stale category alongside a current one.
            if "minecraft" in live or any(w in live for w in past):
                return False
            none = _so.format_raid("RoadDog_88", 5, "roaddog_88", {},
                                   theme=theme).lower()
            if any(w in none for w in past + present):
                return False
        return True

    def _facts_stand_alone():
        """The eight real defects from the #hardclaws transcript stay blocked,
        and the facts worth posting still get through."""
        import funfacts as _ff

        bad = (
            "But in that same year, the Latter Day Saint movement founder, "
            "Joseph Smith, was killed in the Carthage Jail, about 30 miles "
            "away from Nauvoo.",
            "Of the fifty U.S. states, Illinois has the fifth-largest gross "
            "domestic product (GDP), the sixth-largest population, and the "
            "25th-most land area.",
            "In 1840, one hundred of those residents who did not have "
            "passports were arrested, leading to the Graham Affair.",
            "What did Grima do?",
            "Previously the Portswood Hotel, it was named after J. R. R. "
            "Tolkien's book The Hobbit in 1989.",
            "The National Register of Historic Places is the official list of "
            "the Nation's historic places worthy of preservation.",
            "Historic Landmark plaque.",
            "Seeds, such as pumpkin seeds or sunflower seeds",
        )
        good = (
            "One of the oldest remaining buildings in Girard, the Henry "
            "Barnhisel House, shares tales of community and family history.",
            "Jerome was originally called Fremont Town, and under the latter "
            "name was platted in 1867 when the railroad was extended.",
            "Cuba, Missouri is home to the world's largest rocking chair.",
        )
        for sentence in bad:
            if _ff._ranked_facts([sentence], limit=200):
                return False
        for sentence in good:
            if not _ff._ranked_facts([sentence], limit=200):
                return False
        return True

    def _topic_lookup_answers_anything():
        """A topic lookup finds a non-place article, prefers the one asked
        for over a namesake, and returns nothing rather than guessing."""
        articles = {
            "hobbit": [
                {"title": "The Hobbit Inn", "extract":
                 "The Hobbit Inn is a pub in Southampton, Hampshire, England. "
                 "It serves ales and hosts live music at weekends."},
                {"title": "Hobbit", "extract":
                 "Hobbits are a fictional humanoid race appearing in the "
                 "works of J. R. R. Tolkien. They average between two and "
                 "four feet tall and are fond of farming."},
            ],
            "low watts": [
                {"title": "Watts Towers", "extract":
                 "The Watts Towers are a collection of sculptural towers in "
                 "the Watts neighbourhood of Los Angeles."},
            ],
        }
        saved = funfacts._wiki_search_extracts
        funfacts._wiki_search_extracts = (
            lambda q, exchars=4000, limit=6: articles.get(q, []))
        try:
            got = funfacts._wikipedia_topic("hobbit")
            if not got or got["place"] != "Hobbit":
                return False
            if funfacts._wikipedia_topic("low watts") is not None:
                return False
        finally:
            funfacts._wiki_search_extracts = saved
        return True

    def _snippet_must_be_about_the_subject():
        """A search snippet about something else is not an answer.

        DuckDuckGo labels its answer with the query rather than the article it
        found, which is how the bot posted 'Stinker claims to be the world's
        most famous landmark, according to Explore magazine and U.S. News
        Travel.' Every word was real; only the subject had changed.
        """
        landmark = ("The Eiffel Tower claims to be the world's most famous "
                    "landmark, according to Explore magazine and U.S. News "
                    "Travel.")
        real = ("Trail mix is a snack of dried fruit, nuts and sometimes "
                "chocolate, developed to be taken along on hikes.")
        saved = funfacts._http_get_json
        funfacts._http_get_json = (
            lambda url, params, timeout=8.0:
            {"Heading": "", "AbstractText": landmark})
        try:
            if funfacts._duckduckgo("stinker") is not None:
                return False
            funfacts._http_get_json = (
                lambda url, params, timeout=8.0:
                {"Heading": "Trail mix", "AbstractText": real})
            if not funfacts._duckduckgo("trail mix"):
                return False
        finally:
            funfacts._http_get_json = saved
        return funfacts._names_subject("A Huorn is a tree-like being.", "huorns")


    def _questions_are_answered_from_sources():
        """A free-form question is answered from search results, and the
        answer may not contain anything the sources do not."""
        import llm as _llm

        ddg = {"AbstractText": "", "RelatedTopics": [
            {"Text": "The dew point is the temperature to which air must be "
                     "cooled to become saturated with water vapour."},
            {"Text": "Why Does My Car Have Condensation Inside?"}]}
        orig = (funfacts._http_get_json, _llm.is_configured,
                _llm.answer_question)
        funfacts._http_get_json = lambda u, p, timeout=8.0: ddg
        _llm.is_configured = lambda o: True
        try:
            sources = funfacts._question_sources("dew point", {})
            if any(src.endswith("?") for src in sources):
                return False
            _llm.answer_question = lambda q, src, cfg: (
                "Condensation stops once the glass warms above the dew point.")
            if funfacts._answer_question("dew point", {"llm_api_key": "k"},
                                         200) is None:
                return False
            _llm.answer_question = lambda q, src, cfg: (
                "Condensation stops at 41 degrees Fahrenheit.")
            if funfacts._answer_question("dew point", {"llm_api_key": "k"},
                                         200) is not None:
                return False
        finally:
            (funfacts._http_get_json, _llm.is_configured,
             _llm.answer_question) = orig
        return True

    def _an_echo_is_not_a_fact():
        """'!funfact twitch degenerates' must not answer 'twitch degenerates.'"""
        if not funfacts._is_echo("twitch degenerates.", "twitch degenerates"):
            return False
        if funfacts._ranked_facts(["twitch degenerates."],
                                  subject="twitch degenerates",
                                  require_subject=True):
            return False
        return not funfacts._is_echo(
            "Illinois was admitted as a state in 1818.", "illinois")

    def _beef_is_sound():
        """!beef must queue, name its issuer, and never pick a bystander."""
        import beef as _beef

        if _beef.combination_count() < 5000:
            return False
        named = set()
        for pool in _beef.RIVALS.values():
            named |= set(pool)
        for genre in _beef.GENRES:
            for _ in range(40):
                lines = _beef.beef("Hardclaws", "random", genre)
                if len(lines) != 5:
                    return False
                if not lines[4].startswith("\U0001f3c6 "):
                    return False
                if not any("Hardclaws" in ln for ln in lines):
                    return False
                rival = lines[0].split(" vs. ", 1)[1].split(" \u2014 ", 1)[0]
                if rival not in named:
                    return False
                for ln in lines:
                    if "{" in ln or len(ln) >= 450:
                        return False
                    for word in ln.split():
                        if word.endswith("'s") and word[:-2].lower().endswith("s"):
                            return False
        return True

    def _revenge_window_and_scoreboard():
        """!revenge's window is a timestamp in a file, not a Timer on the
        worker thread - and the scoreboard the window feeds survives a
        restart. Presence alone never earns a tag."""
        import os
        import tempfile

        import beefstats as _bs
        t = [1000.0]
        path = os.path.join(tempfile.mkdtemp(prefix="clawfacts-beef-"),
                            "bs.json")
        st = _bs.BeefState(path, clock=lambda: t[0])
        st.record("Hardclaws", "Rival_Rob", "trucking", False)
        t[0] += 5.0
        w = st.window_for("Hardclaws")
        if not w or w["rival"] != "Rival_Rob" or w["genre"] != "trucking":
            return False
        if not 0 < w["seconds_left"] <= 60:
            return False
        t[0] += 61.0
        if st.window_for("Hardclaws"):
            return False
        again = _bs.BeefState(path, clock=lambda: t[0])
        if not again.is_player("Hardclaws") or not again.leader_line():
            return False
        if not (again.card("hardclaws") or "").startswith("Hardclaws:"):
            return False
        # The tagging gate: seen-in-chat is necessary but not sufficient, and
        # presence alone is never consent.
        rc = _bs.RecentChatters(clock=lambda: t[0])
        rc.note("Lurker_Lou")
        return rc.seen("lurker_lou") and not rc.seen("NeverSeen")

    def _beef_game_is_self_contained():
        """The beef game must run on what the bot already has: no model, no
        network, no key. A game that a dead Ollama or a 402 on the fun facts
        could take down is a dependency, not a game."""
        import beef as _beef
        for fname in ("beef.py", "beefstats.py"):
            src = pathlib.Path(fname).read_text(encoding="utf-8")
            for marker in ("import llm", "from llm", "urllib", "http",
                           "socket", "api_key"):
                if marker in src:
                    return False
        res = _beef.feud("Hardclaws", "Rival_Rob", "trucking", revenge=True)
        return bool(res) and len(res["lines"]) == 5 \
            and res["lines"][0].startswith("\U0001f525 REMATCH:") \
            and res["lines"][4].startswith("\U0001f3c6 ")

    def _beef_stories_carry_no_chrome():
        """No labels anywhere in a beef story: no 'BEEF |' prefix, no 'Act n'
        lines, no 'WINNER:' - the verdict speaks English (' takes it.').
        Swept across genres, a rematch and a freeform theme."""
        import re as _re

        import beef as _beef
        label = _re.compile(r"^(act|line|scene)\s*\d\b", _re.IGNORECASE)
        stories = [_beef.feud("Hardclaws", "Rival_Rob", g)
                   for g in list(_beef.GENRES) for _ in range(6)]
        stories += [_beef.feud("Hardclaws", "Rival_Rob", "trucking",
                               revenge=True) for _ in range(6)]
        stories.append(_beef.feud("Hardclaws", "W_E_S_T_Y", "",
                                  theme="Eating Tacos"))
        for res in stories:
            if not res:
                return False
            for ln in res["lines"]:
                if "BEEF |" in ln or "WINNER:" in ln or label.match(ln):
                    return False
            if not res["lines"][4].startswith("\U0001f3c6 "):
                return False
        return True

    def _beef_theme_fallback_stays_on_theme():
        """A freeform-theme beef must keep its words in every story line,
        even on the template fallback - a 'poledancing' headline over robot
        batteries is the broken beef that shipped."""
        import beef as _beef
        res = _beef.feud("Hardclaws", "W_E_S_T_Y", "", theme="poledancing")
        if not res or "poledancing" not in res["lines"][0]:
            return False
        # The showdown line (the last body line) may omit the topic by
        # design; the spark and escalation lines must always carry it.
        # Requiring it of the showdown made this check flake about one run
        # in four, for a beef that was working exactly as shipped.
        return all("poledancing" in ln for ln in res["lines"][1:3])

    def _beef_chat_copy_never_autolinks():
        """.json is a real TLD: chat clients auto-link the bare word
        'config.json' to a stranger's website. No beef message may carry
        any bare dotted word."""
        import re as _re
        import tempfile as _tf

        cfg = dict(_bot.DEFAULTS, nick="b", channel="#t", beef_act_delay=0,
                   beef_state_path=os.path.join(_tf.mkdtemp(), "bs.json"))
        bot_ = _bot.TwitchBot(cfg)
        said = []
        bot_._say = said.append
        bot_._log = lambda *a, **k: None
        bot_._access.helix = None

        def _fire(fn, *a):
            before = len(said)
            fn(*a)
            while not bot_._jobs.empty():
                _, _, _, command, text = bot_._jobs.get()
                if command == "say":
                    said.append(text)
                bot_._jobs.task_done()
            return said[before:]

        msgs = []
        msgs += _fire(bot_._beef_switch, "mod", "moderator/1", "status")
        msgs += _fire(bot_._reply_beef, "Hardclaws", "")
        msgs += _fire(bot_._reply_beef, "Hardclaws", "Rival_Rob poledancing")
        msgs += _fire(bot_._reply_beef, "Hardclaws", "stats")
        msgs += _fire(bot_._reply_revenge, "Hardclaws")
        domain = _re.compile(r"\b[a-z0-9][a-z0-9-]*\.[a-z]{2,}\b",
                             _re.IGNORECASE)
        return bool(msgs) and not any(domain.search(m) for m in msgs)

    def _beef_fates_never_repeat():
        """Shared pools (fates, stakes, verbs) draw from ONE ring each, so
        two beefs cannot land on the same exit line back-to-back."""
        import beef as _beef
        a = _beef.feud("Hardclaws", "Rival_Rob", "zwift")
        b_ = _beef.feud("Hardclaws", "W_E_S_T_Y", "", theme="poledancing")
        c = _beef.feud("Hardclaws", "Rival_Rob", "trucking")
        return (a["lines"][4] != b_["lines"][4]
                and b_["lines"][4] != c["lines"][4]
                and a["lines"][4] != c["lines"][4])

    def _beef_rival_name_resolves_via_helix():
        """A rival nobody has seen in chat still gets the display name
        Twitch holds - one cached, scope-free Get Users call."""
        import tempfile as _tf

        cfg = dict(_bot.DEFAULTS, nick="b", channel="#t", beef_act_delay=0,
                   beef_state_path=os.path.join(_tf.mkdtemp(), "bn.json"))
        bot_ = _bot.TwitchBot(cfg)

        class _H:
            @staticmethod
            def display_name(login):
                return ("TruckingWithDoc"
                        if login == "truckingwithdoc" else None)
        bot_._access.helix = _H()
        return bot_._beef_canonical_rival("@truckingwithdoc") \
            == "TruckingWithDoc"

    def _beef_theme_pools_are_deep_and_grammar_safe():
        """Theme fallbacks must not sound stamped out: 20+ openers, and the
        topic rides every line inside quotes so a gerund theme ('using
        webcams with Zwift') can never produce 'attempting using...'."""
        import beef as _beef
        if len(_beef.THEME_SPARKS) < 20 or len(_beef.THEME_QUOTES) < 10:
            return False
        pools = (_beef.THEME_SPARKS, _beef.THEME_ESCALATIONS,
                 _beef.THEME_CLIMAXES, _beef.THEME_QUOTES, _beef.THEME_CROWD)
        for pool in pools:
            for line in pool:
                idx = line.find("{topic}")
                while idx != -1:
                    if line[idx - 1] != '"':
                        return False
                    idx = line.find("{topic}", idx + 1)
        return True

    def _beef_at_names_do_not_eat_the_theme():
        """'!beef @a @truckingwithdoc beer alpe' once matched the trucking
        genre via the 'truck' inside the leftover @name - theme lost."""
        import tempfile as _tf

        cfg = dict(_bot.DEFAULTS, nick="b", channel="#t", beef_act_delay=0,
                   beef_state_path=os.path.join(_tf.mkdtemp(), "bt.json"))
        bot_ = _bot.TwitchBot(cfg)
        said = []
        bot_._say = said.append
        bot_._log = lambda *a, **k: None
        bot_._access.helix = None
        bot_._reply_beef("smithkxx", "@Hardclaws @truckingwithdoc beer alpe")
        while not bot_._jobs.empty():
            _, _, _, command, text = bot_._jobs.get()
            if command == "say":
                said.append(text)
            bot_._jobs.task_done()
        return bool(said) and "beer alpe" in said[0] \
            and "interstate" not in said[0]

    def _funfact_survives_a_typo_and_answers_questions():
        """'!funfact quesobirria' and '!funfact whats the best usa trucking
        route and why' both returned 'couldn't find any fun facts'. Now a
        one-typo query matches its article, and free-form questions search
        Wikipedia (by subject) for their sources."""
        if not funfacts._topic_match("Quesabirria", "quesobirria"):
            return False
        if not funfacts._names_subject(
                "Quesabirria is a Mexican dish.", "quesobirria"):
            return False
        if funfacts._topic_match("Watts Towers", "low watts"):
            return False
        subj = funfacts._question_subject(
            "whats the best usa trucking route and why")
        return subj == "usa trucking route"

    def _funfact_namesakes_and_variety():
        """Trucking was answered by a Backhaul article's Broadway gossip,
        and american truckers posted the same one sentence twice. A namesake
        must not match the head word, its lines must name the subject, and
        the topic path pools every matching article instead of one."""
        import inspect
        if funfacts._topic_match("Backhaul (trucking)", "trucking"):
            return False
        if not funfacts._topic_match("Trucking industry in the United States",
                                     "trucking"):
            return False
        if funfacts._names_subject(
                "Mislove also invited Deutsch to the cabaret.", "trucking"):
            return False
        topic_src = inspect.getsource(funfacts._wikipedia_topic)
        return "groups.sort" in topic_src and "reach" in topic_src

    def _funfact_sentences_stand_alone():
        """Yorkshire was answered with an anchorless History line ("Tostig
        and Hardrada were both killed..."), a listicle number ("... UK \u00b7
        2."), a bare heading ("North Yorkshire Historic Sites ; 1."), a
        truncated parenthetical ("...complete\u2026") and a pronoun-first
        answer ("It is entirely psychological..."). None of those shapes may
        reach chat again."""
        import inspect
        if funfacts._sentences(
                "Yorkshire is the largest county in the UK \u00b7 2."
        ) != ["Yorkshire is the largest county in the UK."]:
            return False
        if not funfacts._is_fragment("North Yorkshire Historic Sites."):
            return False
        if funfacts._ranked_facts(funfacts._sentences(
                "North Yorkshire Historic Sites ; 1.")):
            return False
        got = funfacts._sentences(
            "Only one Mexican Train is built per round (some say it starts "
            "after the opening turns are complete\u2026")
        if not got or "(" in got[0] or "\u2026" in got[0]:
            return False
        if "it|they|he|she" not in inspect.getsource(
                funfacts._answer_question_llm):
            return False
        wiki_src = inspect.getsource(funfacts._wikipedia)
        return "deep" in wiki_src and "require_subject=True" in wiki_src

    def _funfact_countries_and_substrings():
        """'Yorkshire united kingdom' fell through to a one-line listicle
        because no country ever counted as a trailing region, and the
        camera question matched its 'source' on 'look' inside 'looks'."""
        if funfacts._query_core("Yorkshire united kingdom") != "yorkshire":
            return False
        if funfacts._query_region(
                "Yorkshire united kingdom") != "united kingdom":
            return False
        if funfacts._names_subject(
                "a photo of you looks far worse",
                "why do look fatter on camera?"):
            return False
        if not funfacts._names_subject("Huorns are tree-beings.", "huorns"):
            return False
        import inspect
        return "_question_place" in inspect.getsource(
            funfacts._answer_question)

    def _funfact_stories_first():
        """Size statements, what-is-it leads and inventories outranked every
        story ("the largest by area in the United Kingdom" beat the Harrying
        of the North), and compound entities spoke for the subject. Stories
        now lead; demoted lines survive only when nothing better exists."""
        story = ("The Harrying of the North that followed devastated much "
                 "of Yorkshire.")
        for dull in (
                "Yorkshire is the largest county by area in the UK.",
                "North Yorkshire is a ceremonial county in Northern "
                "England.",
                "Yorkshire contains two national parks and three areas of "
                "natural beauty.",
                "Countryside, including the Dales and the Moors, fills it."):
            if funfacts._score(dull) >= funfacts._score(story):
                return False
        if funfacts._score(
                "Cuba is home to the world's largest rocking chair.") < 6:
            return False
        if funfacts._score(
                "Quesabirria is a Mexican dish of braised meat.") != 0:
            return False
        if funfacts._query_core("North Yorkshire England") != \
                "north yorkshire":
            return False
        return True

    def _lead_moderators_are_moderators():
        """Twitch's Lead Moderator role REPLACES the moderator badge in IRC
        tags, so a lead mod arrives as lead_moderator/1 with no moderator/1 -
        and every mod-gated command silently ignored them (!haul update did
        nothing at all). The tier table and the bot's own state check both
        recognise the badge now."""
        import access as _access
        if _access.tier_from_badges("lead_moderator/1") != "moderator":
            return False
        if _access.tier_from_badges(
                "subscriber/12,lead_moderator/1") != "moderator":
            return False
        if _access.tier_from_badges(
                "broadcaster/1,lead_moderator/1") != "broadcaster":
            return False
        import bot as _bot
        import inspect
        return "lead_moderator/1" in inspect.getsource(
            _bot.TwitchBot._note_own_state)

    def _funfact_specific_answers():
        """The 'longest truck' question was answered with 'The longest road
        train in history still holds the world record.' - no number, no
        name, no date, because the records live below the lead cap the
        model was given. The question path digs into the full article now,
        and a specific question refuses contentless answers."""
        import inspect
        if not funfacts._SPECIFIC_Q.search("what is the longest truck"):
            return False
        if funfacts._SPECIFIC_Q.search("what temperature does it stop"):
            return False
        if "_wiki_extract" not in inspect.getsource(
                funfacts._question_sources):
            return False
        return "nothing specific" in inspect.getsource(
            funfacts._answer_question_llm)

    def _funfact_no_promises_or_teasers():
        """The promise returned via the fact path (a snippet passes every
        fact filter, so the gated question path never ran), and the retry
        produced a scraped teaser ('meet the world's longest truck ...').
        A contentless pool for a specific question is no pool, and teasers
        never post - though they may feed the model as sources."""
        teaser = ("meet the world's longest truck \u2026 a 175-foot road "
                  "train powered by over 1,000 horsepower.")
        if not funfacts._is_fragment(teaser):
            return False
        if funfacts._is_fragment("eBay is an online marketplace."):
            return False
        if not funfacts._TEASE.match("Meet the world's longest truck."):
            return False
        import inspect
        if "contentless" not in inspect.getsource(funfacts.get_funfact):
            return False
        return "_STRONG" in inspect.getsource(funfacts._question_sources)

    def _funfact_record_claims_split():
        """The promise outscored the real record 13 to 6 and posted even
        with a dated line in the pool, and the retry posted a heading and
        two list items glued by middots. A figureless record claim is junk
        in its own right, and middot joins are split like sentences."""
        promise = ("The longest road train in history still holds the "
                   "world record.")
        if not funfacts._is_contentless_claim(promise):
            return False
        if funfacts._ranked_facts([promise], subject="longest truck"):
            return False
        buddo = ("In 1989, a trucker named \"Buddo\" tugged 12 trailers "
                 "down the main street of Winton.")
        if funfacts._is_contentless_claim(buddo):
            return False
        glued = ("World's longest road trains \u00b7 In 1989, Buddo tugged "
                 "12 trailers down the main street of Winton. \u00b7 In "
                 "1993, Plugger Bowden took the record.")
        sents = funfacts._sentences(glued)
        if len(sents) < 2 or any("\u00b7" in s for s in sents):
            return False
        return True

    def _funfact_headings_and_captions():
        """Split from its glued list, the section heading "World's longest
        road trains." posted alone (sentence case beats the title-case
        rule, and "trains" passes the verb catch-all), and "this mighty
        truck is named ..." posted as a caption. Headings need a closed
        verb or a digit; captions are rejected outright."""
        if not funfacts._is_fragment("World's longest road trains."):
            return False
        if not funfacts._is_fragment("Notable people."):
            return False
        if funfacts._is_fragment("Huorns are tree-beings."):
            return False
        caption = ("One of the longest trucks in the world, this mighty "
                   "truck is named Lindsay Transport B Double.")
        if funfacts._ranked_facts([caption], subject="longest truck"):
            return False
        return True

    def _funfact_cuts_are_clean():
        """Seligman posted '...to the Cafe and the\u2026' - the 55% clause
        threshold rejected the only comma cut and the word chop left a
        dangler. The longest clause cut wins, and word cuts strip dangling
        connectors."""
        fact = ('The "Seligman Depot" and the "1860 Arizona Territorial '
                'Jail" are not authentic historical buildings, but owned '
                'by the Roadkill Cafe owners and were built to attract '
                'tourists to the Cafe.')
        got = funfacts._fit_fact(fact, 120, {})
        if not got.endswith("historical buildings\u2026"):
            return False
        if " and the" in got[-12:]:
            return False
        chop = ("The bridge carried coal trucks eastward toward the "
                "furnaces and the loading docks beyond the river bend "
                "every single winter morning.")
        got = funfacts._trim(chop, 90)
        return got.endswith("loading docks\u2026")

    def _funfact_hype_and_demonyms():
        """'Get ready to meet the world's longest truck - an absolute beast
        tearing across the wild Australian outback!' posted as the only
        answer: the hook word was not first, and the demonym 'Australian'
        counted as a name. Hype openers are refused, and demonyms are not
        names."""
        hype = ("Get ready to meet the world's longest truck - an absolute "
                "beast tearing across the wild Australian outback!")
        if not funfacts._TEASE.match(hype):
            return False
        if funfacts._has_specific(hype):
            return False
        if not funfacts._has_specific("The record was set in Australia."):
            return False
        if funfacts._has_specific("The Australian record stands."):
            return False
        if not funfacts._is_contentless_claim(
                "The Australian record still stands."):
            return False
        if funfacts._ranked_facts([hype], subject="longest truck"):
            return False
        return True

    def _funfact_records_miner():
        """The longest-truck question declined outright after the hype
        refusals: the model path dead-ends and nothing else could answer.
        The article's own record sentences are posted directly now - with
        or without an LLM. And 'Feb of 2018' caption dates never post."""
        if not hasattr(funfacts, "_mine_records"):
            return False
        if funfacts._question_subject(
                "what is the longest truck in the world transporting goods"
                ) != "longest truck transporting goods":
            return False
        if not funfacts._is_junk_seed(
                "These fingerling potatoes were planted on Feb of 2018."):
            return False
        if funfacts._is_junk_seed("The harvest began in February 2018."):
            return False
        import inspect
        return "_mine_records" in inspect.getsource(
            funfacts._answer_question)

    def _chat_ai_bounded_and_safe():
        """The chat AI: !ask with a persona, mention replies, and chime-ins
        gated by roll, room size, cooldowns and an hourly cap. Off by
        default, one cleaned line at a time, and the model is told the
        rules a regex cannot check."""
        import chatai as _ch
        import bot as _bot
        if _bot.DEFAULTS.get("chat_ai_enabled") is not False:
            return False
        if not callable(getattr(_llm2, "chat_reply", None)):
            return False
        if _ch.clean_line("check config.json for details") is not None:
            return False
        if _ch.clean_line("@kvack look at this") is not None:
            return False
        if _ch.clean_line("x" * 300) is not None:
            return False
        if _ch.clean_line("Graphics are free with the job.") is None:
            return False
        if not _ch.declined("NOTHING TO SAY"):
            return False
        rules = _ch.system_prompt("")
        if "NOTHING TO SAY" not in rules or "never people" not in rules:
            return False
        # Every gate, one at a time.
        base = dict(enabled=True, paused=False, ambient_off=False,
                    kind="mention", roll=0.0, chance=0.25, now=1000.0,
                    last=0.0, mention_last=0.0, mention_cd=60, chime_cd=600,
                    times=[], max_hour=6, buffer_len=10, min_chat=5)
        # A quiet opener must not mute a mention: separate clocks.
        if not _ch.should_speak(**{**base, "last": 990.0}):
            return False
        if not _ch.should_speak(**base):
            return False
        for key, value in (("paused", True), ("enabled", False),
                           ("kind", None), ("mention_last", 990.0)):
            if _ch.should_speak(**{**base, key: value}):
                return False
        # Autonomous switches/caps never strand an explicit question.
        if not _ch.should_speak(**{**base, "ambient_off": True}):
            return False
        if not _ch.should_speak(**{**base, "times": [900.0] * 6}):
            return False
        chime = {**base, "kind": "chime", "roll": 0.9}
        if _ch.should_speak(**chime):
            return False
        if not _ch.should_speak(**{**chime, "roll": 0.1}):
            return False
        if _ch.should_speak(**{**chime, "roll": 0.1,
                               "times": [900.0] * 6}):
            return False
        if _ch.should_speak(**{**chime, "roll": 0.1, "last": 990.0}):
            return False                      # chimes wait out their clock
        if _ch.should_speak(**{**chime, "roll": 0.1, "buffer_len": 2}):
            return False
        if "_reply_ask" not in _bot_src:
            return False
        # !ask is a core command, enqueued like !funfact whatever the
        # chat-AI and fun-command switches say.
        if 'command in ("funfact", "ask")' not in _bot_src:
            return False
        # ...and its answers never record memory while the AI is off.
        _distill_src = _bot_src.split("def _distill")[1].split("def ")[0]
        if "chat_ai_enabled" not in _distill_src:
            return False
        return '"chime"' in _bot_src

    def _dead_model_degrades_gracefully():
        """A live-fire transcript: an OpenRouter slug with no endpoints
        (404). The bot must degrade, not break - specific questions still
        get the record answer (the records miner no longer waits for NO
        model to be configured, it runs whenever the model path has
        nothing), chatty questions get a canned Doc line instead of
        silence or a Wikipedia fact about the word 'today', the 404 says
        so once and loudly on every path, and the streamer can address
        the bot by name (chime-ins stay off for him)."""
        import chatai as _ch
        fsrc = pathlib.Path("funfacts.py").read_text(encoding="utf-8")
        if "def _answer_question_llm" not in fsrc:
            return False
        wrapper = fsrc.split("def _answer_question(", 1)[1]
        wrapper = wrapper.split("def _answer_question_llm", 1)[0]
        if "_mine_records" not in wrapper:
            return False
        if not callable(getattr(_ch, "smalltalk", None)):
            return False
        if _ch.smalltalk("whats the longest truck") is not None:
            return False
        if _ch.smalltalk("how are you today") not in _ch._SMALLTALK_LINES:
            return False
        bsrc = pathlib.Path("bot.py").read_text(encoding="utf-8")
        if bsrc.count("chatai.smalltalk") < 2:
            return False
        if "chime_worthy(text) else None" not in bsrc:
            return False          # emoji walls must not be chime triggers
        lsrc = pathlib.Path("llm.py").read_text(encoding="utf-8")
        if lsrc.count("_model_404_hint(") < 4:
            return False          # def + the three failure paths
        return True

    def _subgoal_command_works():
        """!subgoal: anyone reads the progress, mods maintain it, and
        the arithmetic on the way to the goal is right."""
        b = _bot.TwitchBot(
            dict(_bot.DEFAULTS, nick="n", channel="#c",
                 oauth_token="oauth:x",
                 subgoal_state_path=os.path.join(
                     tempfile.mkdtemp(), "sg.json")))
        said = []
        b._say = said.append
        if not b._subgoal_mutation("amod", "moderator/1",
                                   "set 50 wear a clown costume"):
            return False
        if not b._subgoal_mutation("amod", "moderator/1", "add 12"):
            return False
        b._say_subgoal("kvack")
        return "12/50" in said[-1] and "38 to go" in said[-1]

    def _chat_falls_back():
        """Groq's free tier 429s mid-stream: a configured second provider
        carries chat and sourced answers, and while the primary's breaker
        window is open it is not even asked."""
        import io as _io
        import json as _json
        import urllib.error as _ue

        cfg = {"llm_api_key": "gsk", "llm_base_url":
               "https://api.groq.com/openai/v1",
               "llm_model": "openai/gpt-oss-120b",
               "llm_fallback_key": "or", "llm_fallback_base_url":
               "https://openrouter.ai/api/v1",
               "llm_fallback_model": "mistralai/mistral-nemo"}
        hits = []

        def _fake(req, timeout=60):
            hits.append(req.full_url)
            if "groq" in req.full_url:
                raise _ue.HTTPError(req.full_url, 429, "rate", {},
                                    _io.BytesIO(b"{}"))
            return _io.BytesIO(_json.dumps(
                {"choices": [{"message": {"content": "Line."}}]}
            ).encode("utf-8"))

        _orig = _llm2.urllib.request.urlopen
        _llm2.urllib.request.urlopen = _fake
        try:
            _llm2.reset_disable_state()
            got = _llm2.chat_reply("s", "u" * 20, cfg)
            # Groq's spare model (its own daily bucket) is tried before
            # the second provider; both 429 here, so OpenRouter answers.
            ok1 = got == "Line." and hits == [
                "https://api.groq.com/openai/v1/chat/completions",
                "https://api.groq.com/openai/v1/chat/completions",
                "https://openrouter.ai/api/v1/chat/completions"]
            hits.clear()
            got = _llm2.chat_reply("s", "u" * 20, cfg)
            ok2 = got == "Line." and hits == [
                "https://openrouter.ai/api/v1/chat/completions"]
            # Sourced answers used to return before reaching the provider
            # fallback. With the primary breaker open this goes straight to it.
            hits.clear()
            got = _llm2.answer_question("q?", ["source"], cfg)
            ok3 = got == "Line." and hits == [
                "https://openrouter.ai/api/v1/chat/completions"]
            return ok1 and ok2 and ok3
        finally:
            _llm2.urllib.request.urlopen = _orig
            _llm2.reset_disable_state()

    def _openrouter_200_error_is_explicit():
        """A provider error after HTTP 200 must retain its code and reason."""
        import io as _io
        import json as _json
        import urllib.error as _ue

        original = _llm2.urllib.request.urlopen

        def fake(_req, timeout=60):
            return _io.BytesIO(_json.dumps({
                "error": {"code": 429, "message": "provider overloaded"}
            }).encode("utf-8"))

        _llm2.urllib.request.urlopen = fake
        try:
            try:
                _llm2._request("https://openrouter.ai/api/v1", "k", b"{}")
            except _ue.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")
                return exc.code == 429 and "provider overloaded" in detail
            return False
        finally:
            _llm2.urllib.request.urlopen = original

    def _held_question_is_acknowledged():
        """A held question is announced with the wait quoted.

        From the room a rail and a crash look identical, which is why the
        same question gets asked three times. Live line: 'mention from
        marblehead9 held 44s - their own cooldown'.
        """
        import threading as _th

        cfg = dict(
            _bot.DEFAULTS, nick="Docbot", channel="#t",
            chat_ai_enabled=True, llm_api_key="k",
            memory_db_path=os.path.join(tempfile.mkdtemp(), "m.db"),
            beef_state_path=os.path.join(tempfile.mkdtemp(), "b.json"),
            persona_state_path=os.path.join(tempfile.mkdtemp(), "p.json"),
            subgoal_state_path=os.path.join(tempfile.mkdtemp(), "s.json"),
        )
        b = _bot.TwitchBot(cfg)
        said = []
        b._say = said.append
        b._log = lambda *a, **k: None
        b._access.helix = None
        t0 = time.time()
        b._mark_mention_reply("marblehead9", t0 - 16)     # 44s of the 60
        b._on_message("marblehead9", "#t", "docbot how long is the tow "
                      "rope?", "marblehead9", "")
        for t in _th.enumerate():
            if t.name == "ack":
                t.join(5)
        if said != ["@marblehead9 on it - give me about 45 seconds to "
                    "look that up."]:
            return False
        if len(b._chat_ai_pending) != 1 or not b._jobs.empty():
            return False                       # held, not answered twice
        # A repeat ask gets no second promise - that is the point.
        b._on_message("marblehead9", "#t", "docbot???", "marblehead9", "")
        for t in _th.enumerate():
            if t.name == "ack":
                t.join(5)
        return len(said) == 1

    def _mods_can_ban():
        """A moderator's word bans somebody - a viewer's word does nothing.

        Twitch switched the IRC /ban commands off in Feb 2023, so this is
        a Helix POST naming the bot as moderator_id; a lead moderator's
        badge counts, and a private ask is answered privately.
        """
        import io as _io
        import json as _json
        import threading as _th
        import urllib.error as _ue

        import moderation as _mod

        calls = []

        def _fake(req, timeout=8):
            calls.append((req.get_method(), req.full_url,
                          (req.data or b"").decode("utf-8")))
            if "/helix/users" in req.full_url:
                login = req.full_url.split("login=")[-1]
                return _io.BytesIO(_json.dumps(
                    {"data": [{"id": "999", "login": login}]}
                ).encode("utf-8"))
            return _io.BytesIO(_json.dumps({"data": [{
                "user_id": "999", "end_time": None}]}).encode("utf-8"))

        cfg = dict(
            _bot.DEFAULTS, nick="Docbot", channel="#doc",
            mod_logins=["Leadmod"],
            memory_db_path=os.path.join(tempfile.mkdtemp(), "m.db"),
            beef_state_path=os.path.join(tempfile.mkdtemp(), "b.json"),
            persona_state_path=os.path.join(tempfile.mkdtemp(), "p.json"),
            subgoal_state_path=os.path.join(tempfile.mkdtemp(), "s.json"),
        )
        b = _bot.TwitchBot(cfg)
        b._distill = lambda *a, **k: None
        b._log = lambda line: None
        said = []
        b._say = said.append
        b._moderator = _mod.Moderator(
            access_mod.Helix("cid", "tok", "111"), nick="docbot")
        b._moderator.moderator_id = "777"
        _orig = _mod.urllib.request.urlopen
        _mod.urllib.request.urlopen = _fake
        try:
            if not b._mod_authorised("leadmod", "lead_moderator/1"):
                return False
            if b._mod_authorised("viewer", "subscriber/3"):
                return False
            if not b._mod_authorised("leadmod", "", private=True):
                return False
            if b._mod_authorised("stranger", "", private=True):
                return False

            def _join():
                for t in _th.enumerate():
                    if t.name == "mod-action":
                        t.join(5)

            b._on_message("Viewer", "#doc", "!ban spammer", "viewer",
                          "subscriber/3")
            _join()
            if calls or said:
                return False
            b._on_message("Leadmod", "#doc", "!ban Spammer link spam",
                          "leadmod", "lead_moderator/1")
            _join()
            bans = [c for c in calls if "moderation/bans" in c[1]]
            if len(bans) != 1 or bans[0][0] != "POST":
                return False
            if "broadcaster_id=111&moderator_id=777" not in bans[0][1]:
                return False
            body = _json.loads(bans[0][2])
            if body != {"data": {"user_id": "999",
                                 "reason": "link spam"}}:
                return False
            if not any("banned" in line for line in said):
                return False
            # A private ask stays private: no line in the room.
            said.clear()
            calls.clear()
            whispers = []
            b._moderator.whisper = lambda login, text: (
                whispers.append((login, text)), True)[1]
            b._pm_last.clear()
            b._on_message("Leadmod", "docbot", "!unban spammer", "leadmod",
                          "")
            _join()
            if said or not whispers:
                return False
            return [c[0] for c in calls
                    if "moderation/bans" in c[1]] == ["DELETE"]
        finally:
            _mod.urllib.request.urlopen = _orig

    def _provider_chain_walks():
        """An ordered list of providers: one dead key is skipped, not fatal.

        Groq spent -> NVIDIA NIM key rejected -> Gemini answers; and on the
        NEXT line NIM is not asked again, because its own breaker is open
        while Gemini's is not. Each provider also gets its own request
        shape (NIM wants max_tokens; Gemini 3.x gets the reasoning budget).
        """
        import io as _io
        import json as _json
        import urllib.error as _ue

        nim = "https://integrate.api.nvidia.com/v1"
        gem = "https://generativelanguage.googleapis.com/v1beta/openai"
        groq = "https://api.groq.com/openai/v1"
        cfg = {"llm_api_key": "gsk", "llm_base_url": groq,
               "llm_model": "openai/gpt-oss-120b",
               "llm_fallback_providers": [
                   {"base_url": nim, "key": "nvapi-x",
                    "model": "openai/gpt-oss-120b"},
                   {"base_url": gem, "key": "gem-x",
                    "model": "gemini-3.8-flash"}]}
        bodies, hits = [], []

        def _fake(req, timeout=60):
            hits.append(req.full_url)
            bodies.append(req.data.decode("utf-8"))
            if req.full_url.startswith(groq):
                raise _ue.HTTPError(req.full_url, 429, "rate", {},
                                    _io.BytesIO(b"{}"))
            if req.full_url.startswith(nim):
                raise _ue.HTTPError(req.full_url, 401, "bad key", {},
                                    _io.BytesIO(b"{}"))
            return _io.BytesIO(_json.dumps({"choices": [{"message": {
                "content": "Gemini line."}}]}).encode("utf-8"))

        _orig = _llm2.urllib.request.urlopen
        _llm2.urllib.request.urlopen = _fake
        try:
            _llm2.reset_disable_state()
            if [p[0] for p in _llm2.fallback_providers(cfg)] != [nim, gem]:
                return False
            got = _llm2.chat_reply("s", "u" * 20, cfg)
            if got != "Gemini line." or hits != [
                    groq + "/chat/completions", groq + "/chat/completions",
                    nim + "/chat/completions", gem + "/chat/completions"]:
                return False
            gem_body = _json.loads(bodies[-1])
            if not (gem_body.get("reasoning_effort") == "low"
                    and "max_completion_tokens" in gem_body
                    and "temperature" not in gem_body):
                return False
            if not (_llm2._fallback_unavailable(nim)
                    and not _llm2._fallback_unavailable(gem)):
                return False
            # Next line: NIM is skipped for the session, Gemini answers.
            hits.clear()
            got = _llm2.chat_reply("s", "u" * 20, cfg)
            return got == "Gemini line." and hits == [
                gem + "/chat/completions"]
        finally:
            _llm2.urllib.request.urlopen = _orig
            _llm2.reset_disable_state()

    def _bot_forwards_chat_options():
        """The provider test can pass while the real bot still drops the
        fallback fields when it builds _opts. Exercise that handoff itself."""
        cfg = dict(
            _bot.DEFAULTS, nick="n", channel="#c", chat_ai_enabled=True,
            llm_api_key="primary", llm_fallback_key="fallback-key",
            llm_fallback_base_url="https://openrouter.ai/api/v1",
            llm_fallback_model="fallback/model", llm_no_think=True,
            memory_db_path=os.path.join(tempfile.mkdtemp(), "m.db"),
            beef_state_path=os.path.join(tempfile.mkdtemp(), "b.json"),
            persona_state_path=os.path.join(tempfile.mkdtemp(), "p.json"),
            subgoal_state_path=os.path.join(tempfile.mkdtemp(), "s.json"),
        )
        b = _bot.TwitchBot(cfg)
        return (_llm2.fallback_endpoint(b._opts) == (
                    "https://openrouter.ai/api/v1", "fallback-key",
                    "fallback/model")
                and b._opts.get("llm_no_think") is True)

    def _rough_direct_ask_cannot_go_stale():
        rough = ("Docbot what do we think of people who ride zwift with 0% "
                 "trainer difficulty? Pussy or its ok?")
        cfg = dict(
            _bot.DEFAULTS, nick="TruckingWithDocBot", channel="#c",
            chat_ai_enabled=True,
            memory_db_path=os.path.join(tempfile.mkdtemp(), "m.db"),
            beef_state_path=os.path.join(tempfile.mkdtemp(), "b.json"),
            persona_state_path=os.path.join(tempfile.mkdtemp(), "p.json"),
            subgoal_state_path=os.path.join(tempfile.mkdtemp(), "s.json"),
        )
        b = _bot.TwitchBot(cfg)
        context = _ch2.direct_context(
            [("Hardclaws", rough),
             ("Hardclaws", "docbot old zwift question"),
             ("kvack", "ordinary room context")],
            b._chat_ai_names)
        return (b._chat_ai_kind("Hardclaws", "broadcaster/1", rough)
                == _ch2.MENTION
                and not _ch2.factual_question(rough, b._chat_ai_names)
                and context == [("kvack", "ordinary room context")]
                and _ch2.recover_direct_line("Safe prose with enough words. "
                                             * 20) is not None
                and _ch2.recover_direct_line("x" * 400) is None)

    def _subs_count_themselves():
        """Twitch lets only the broadcaster's own token read the sub
        count, so the bot counts what chat SEES: sub, resub and gift
        notices bump the goal, the community-gift banner does not
        double-count, and crossing the goal queues the payoff line."""
        b = _bot.TwitchBot(
            dict(_bot.DEFAULTS, nick="n", channel="#c",
                 oauth_token="oauth:x",
                 subgoal_state_path=os.path.join(
                     tempfile.mkdtemp(), "sg.json")))
        said = []
        b._say = said.append
        b._log = lambda *a, **k: None
        if not b._subgoal_mutation("amod", "moderator/1",
                                   "set 2 wear a clown costume"):
            return False
        b._handle("@display-name=NewSub;login=newsub;msg-id=sub :newsub!"
                  "newsub@newsub.tmi.twitch.tv USERNOTICE #c :hi")
        b._handle("@display-name=Loyal;login=loyal;msg-id=resub :loyal!"
                  "loyal@loyal.tmi.twitch.tv USERNOTICE #c :6 months")
        if b._subgoal.get("current") != 2:
            return False
        if b._jobs.empty():
            return False
        _, _, _, command, argument = b._jobs.get()
        if command != "say" or "GOAL REACHED" not in argument:
            return False
        b._handle("@display-name=G;login=g;msg-id=submysterygift;msg-"
                  "param-mass-gift-count=5 :g!g@g.tmi.twitch.tv "
                  "USERNOTICE #c")
        return b._subgoal.get("current") == 2 and b._jobs.empty()

    def _empty_reply_retried():
        """A reasoning model that thinks past its completion budget
        returns an empty 200 (live-fire: a held mention 'answered' at
        17:33:23 came back with nothing at 17:33:24). One retry at a
        doubled budget, then the fallback carries the line."""
        import io as _io
        import json as _json

        cfg = {"llm_api_key": "gsk", "llm_base_url":
               "https://api.groq.com/openai/v1",
               "llm_model": "openai/gpt-oss-120b",
               "llm_fallback_key": "or", "llm_fallback_base_url":
               "https://openrouter.ai/api/v1",
               "llm_fallback_model": "mistralai/mistral-nemo"}
        hits = []

        def _fake(req, timeout=60):
            hits.append(req.full_url)
            r = "Line." if "openrouter" in req.full_url else ""
            return _io.BytesIO(_json.dumps(
                {"choices": [{"message": {"content": r}}]}
            ).encode("utf-8"))

        _orig = _llm2.urllib.request.urlopen
        _llm2.urllib.request.urlopen = _fake
        try:
            _llm2.reset_disable_state()
            got = _llm2.chat_reply("s", "u" * 20, cfg)
            return (got == "Line."
                    and len(hits) == 3
                    and hits[-1] == "https://openrouter.ai/api/v1/"
                                   "chat/completions")
        finally:
            _llm2.urllib.request.urlopen = _orig
            _llm2.reset_disable_state()

    def _follows_question_works():
        """'How many follows this stream?' is answered from Helix with
        the startup baseline - the number the old bot printed at boot
        and then threw away."""
        from types import SimpleNamespace
        b = _bot.TwitchBot(
            dict(_bot.DEFAULTS, nick="n", channel="#c",
                 oauth_token="oauth:x", chat_ai_enabled=True,
                 llm_api_key="k",
                 memory_db_path=os.path.join(tempfile.mkdtemp(), "m.db"),
                 beef_state_path=os.path.join(
                     tempfile.mkdtemp(), "b.json"),
                 persona_state_path=os.path.join(
                     tempfile.mkdtemp(), "p.json"),
                 subgoal_state_path=os.path.join(
                     tempfile.mkdtemp(), "s.json")))
        said = []
        b._say = said.append
        b._log = lambda *a, **k: None
        b._access.helix = SimpleNamespace(follow_total=lambda: 1372)
        b._follows_start = 1368
        b._chat_ai_mention_last = 0.0
        b._chat_ai_mention_by.clear()
        b._do_chime("Hardclaws",
                    "docbot how many follows have we received this stream")
        return (len(said) == 1 and "1,372 followers" in said[0]
                and "4 new" in said[0])

    def _current_weather_is_live():
        """Weather is current Open-Meteo data, never an archive snippet."""
        saved = (funfacts._osm_geocode, funfacts._http_get_json,
                 funfacts._lookup_all)
        funfacts._osm_geocode = lambda _place: {
            "name": "Marshall", "state": "Illinois",
            "country": "United States", "lat": 39.39, "lon": -87.69}

        def live(url, params=None, timeout=0):
            if url != funfacts.OPEN_METEO_API or \
                    "temperature_2m" not in params.get("current", ""):
                raise AssertionError((url, params))
            return {"current": {
                "temperature_2m": 68.2, "apparent_temperature": 65.8,
                "relative_humidity_2m": 59, "precipitation": 0,
                "weather_code": 2, "wind_speed_10m": 11.6,
                "wind_direction_10m": 250, "wind_gusts_10m": 18.7}}

        funfacts._http_get_json = live
        funfacts._lookup_all = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("weather reached search"))
        try:
            with funfacts._cache_lock:
                funfacts._cache.clear()
            result = funfacts.get_funfact(
                "what is the weather in Marshall, IL",
                {"answer_questions": True})
            return (result.get("kind") == "Weather"
                    and result.get("place") == "Marshall, Illinois"
                    and "Currently 68°F" in result.get("fact", "")
                    and "wind WSW at 12 mph" in result.get("fact", ""))
        finally:
            (funfacts._osm_geocode, funfacts._http_get_json,
             funfacts._lookup_all) = saved
            with funfacts._cache_lock:
                funfacts._cache.clear()

    def _weather_and_miner_behave():
        """Live weather is data, not trivia - its header says Weather.
        And the records miner refuses an off-topic article: the US
        freight-lane question was answered with Ivory Coast's GDP."""
        wplace, kind = funfacts._weather_header(
            "whats the weather like in Saint Clair, Mo")
        if wplace != "Saint Clair, Mo" or kind != "Weather":
            return False
        if funfacts._weather_header("whats the capital of Australia") != \
                (None, None):
            return False
        if funfacts._clock_12h("2026-09-15T06:38") != "6:38 AM":
            return False
        if "OPEN_METEO_API" not in pathlib.Path(
                "funfacts.py").read_text(encoding="utf-8"):
            return False
        if funfacts._records_on_topic(
                "Ivory Coast",
                "Ivory Coast is a country on the southern coast of West "
                "Africa.",
                "common produce move west coat east coast usa"):
            return False
        return funfacts._records_on_topic(
            "Road train", "A road train is a trucking vehicle.",
            "longest truck transporting goods")

    def _notes_are_kept():
        """'docbot take a mental note X' stores X (mods only, under the
        person it is about), and a question about a person is answered
        from memory, never routed at the fact engine."""
        b = _bot.TwitchBot(
            dict(_bot.DEFAULTS, nick="n", channel="#truckingwithdoc",
                 oauth_token="oauth:x", chat_ai_enabled=True,
                 llm_api_key="k",
                 memory_db_path=os.path.join(tempfile.mkdtemp(), "m.db"),
                 beef_state_path=os.path.join(
                     tempfile.mkdtemp(), "b.json"),
                 persona_state_path=os.path.join(
                     tempfile.mkdtemp(), "p.json"),
                 subgoal_state_path=os.path.join(
                     tempfile.mkdtemp(), "s.json")))
        b._log = lambda *a, **k: None
        b._access.helix = None
        parsed = _ch2.note_request(
            "docbot take a mental not its 2:49am and @TruckingWithDoc "
            "took a piss in Sullivan,MO truck stop")
        if not parsed or parsed[0] != "TruckingWithDoc":
            return False
        b._maybe_chime("Hardclaws", "hardclaws", _ch2.MENTION,
                       "docbot take a mental note its 2:49am and "
                       "@TruckingWithDoc took a piss in Sullivan,MO "
                       "truck stop", "broadcaster/1")
        _, _, _, command, argument = b._jobs.get_nowait()
        if command != "say" or "Noted" not in argument:
            return False
        got = b._memory.recall(["TruckingWithDoc"])
        if not got or "Sullivan" not in got[0][1]:
            return False
        return b._asks_about_someone(
            "when and where did @TruckingWithDoc last take a piss?")

    def _chat_ai_remembers_and_forgets():
        """The chat AI's memory: a log pruned to 90 days, distilled
        per-viewer facts injected into its prompts, a 25-fact cap, and
        !forget (mods) erasing a viewer entirely. Nothing is recorded
        while the feature is off."""
        import memory as _mem
        import tempfile as _tf
        import os as _os
        m = _mem.Memory(_os.path.join(_tf.mkdtemp(), "m.db"))
        if not m.ok:
            return False
        m.remember("kvack", ["sleeps on the floor by choice"])
        if m.recall(["kvack"]) != [("kvack", "sleeps on the floor by choice")]:
            return False
        if m.purge("kvack") != 1 or m.recall(["kvack"]):
            return False
        if _mem.parse_facts("NOTHING WORTH KEEPING") != []:
            return False
        if _mem.parse_facts("- drives a Kenworth") != ["drives a Kenworth"]:
            return False
        import chatai as _ch
        prompt = _ch.user_prompt([("a", "hi")], "a", "hello",
                                 memories=[("kvack",
                                            "sleeps on the floor")])
        if "sleeps on the floor" not in prompt:
            return False
        if "FORGET_COMMANDS" not in _bot_src:
            return False
        return '"memory_db_path"' in _bot_src

    def _llm_no_think_switch():
        """Qwen3-family models think before answering, and on a CPU mini
        PC that turns a one-line chat reply into a half-minute stall -
        every timeout goes off. llm_no_think=true appends Qwen3's
        documented /no_think switch to every prompt: chat lines, question
        answers, summaries and beef stories."""
        import llm as _l
        if _l._maybe_nothink("hello", {}) != "hello":
            return False
        if not _l._maybe_nothink(
                "hello", {"llm_no_think": True}).endswith("/no_think"):
            return False
        if _bot.DEFAULTS.get("llm_no_think") is not False:
            return False
        if "llm._maybe_nothink" not in pathlib.Path(
                "beefllm.py").read_text(encoding="utf-8"):
            return False
        return True

    def _beef_llm_never_breaks_the_game():
        """The optional LLM pass writes body lines only, behind validate(),
        and every failure mode means templates. Unconfigured must mean None
        immediately, and a story that swaps the pre-rolled winner must be
        rejected - the leaderboard is scored off the roll, never the text."""
        import beef as _beef
        import beefllm as _bl
        res = _beef.feud("Hardclaws", "Rival_Rob", "zwift")
        if _bl.write_story(res, {"beef_llm": "auto"}) is not None:
            return False                      # no key/base configured
        other = dict(res, winner=res["loser"], loser=res["winner"])
        good = "\n".join([
            f"{res['issuer']} and {res['rival']} fell out over a pun.",
            f"{res['rival']} escalated by bringing a leafblower.",
            f"{res['winner']} settled it with one perfect move.",
            f"\U0001f3c6 {res['winner']} takes it. "
            f"{res['loser']} left mid-sentence."])
        if _bl.validate(good, res) is None:
            return False
        if _bl.validate(good, other) is not None:
            return False                      # wrong pre-rolled winner
        if _bl.validate(good + "\nAct 4 — more", res) is not None:
            return False                      # shape must be exactly four
        return hasattr(_bot.TwitchBot, "_tell_beef")

    def _beef_freeform_theme_is_kept():
        """'!beef @W_E_S_T_Y Eating Tacos' must stay a taco feud: the words
        headline the story and travel to the LLM - never silently re-genred
        to a random setting."""
        import beef as _beef
        if _beef.match_genre("eating tacos") is not None:
            return False
        if _beef.match_genre("zwift") != "zwift":
            return False
        res = _beef.feud("Hardclaws", "W_E_S_T_Y", "", theme="Eating Tacos")
        return bool(res) and "Eating Tacos" in res["lines"][0] \
            and res.get("theme") == "Eating Tacos"

    def _beef_gap_is_literal():
        """beef_act_delay is THE gap, in seconds, between every part - the
        growing-multiplier design made '10' produce a 7.5s first gap and
        read as broken on stream."""
        return getattr(_bot.TwitchBot, "_BEEF_GAPS", None) is None \
            and hasattr(_bot.TwitchBot, "_beef_gap")

    def _reminder_clock_zones_are_self_contained():
        """'01:30PDT' and '01:30 UTC-7' are fixed offsets that reminders.py
        resolves on its own; they must never depend on zoneinfo. Windows
        ships no IANA database, so ZoneInfo("America/Los_Angeles") raises
        there unless tzdata was pip-installed, and asserting on that name
        made this line read as a missing fix on every Windows box. zoneinfo
        is switched off for the duration so the abbreviation and numeric
        paths are proven self-contained even on a machine that has it."""
        import datetime as _dt
        import reminders as _rem

        def utc_clock(stamp):
            return _dt.datetime.fromtimestamp(
                stamp, _dt.timezone.utc).strftime("%H:%M")

        saved = _rem.zoneinfo
        _rem.zoneinfo = None
        try:
            for spec, label, clock in (
                    ("01:30PDT", "PDT", "08:30"),        # PDT is UTC-7
                    ("1:30pm PDT", "PDT", "20:30"),      # meridiem, then zone
                    ("01:30 UTC-7", "UTC-7", "08:30"),   # numeric offset
                    ("01:30 +0930", "+0930", "16:00")):  # half-hour offset
                due, got, _ = _rem.parse_clock(spec)
                if due is None or got != label or utc_clock(due) != clock:
                    return False
            # An IANA name is still told apart from "am". Without tz data
            # it is refused with a reason - never misread, never a crash.
            due, why, _ = _rem.parse_clock("01:30 America/Los_Angeles")
            return due is None and "not a timezone I know" in str(why)
        finally:
            _rem.zoneinfo = saved

    def _state_files_are_written_atomically():
        """storage.save_json() lands the file through a temp + os.replace().
        tempfile.mkstemp() returns an OPEN descriptor as well as a path; the
        old one-liner kept it open, and Windows will not replace a file that
        another handle holds (WinError 32), so the save failed there - and
        it leaked one temp file per run everywhere. Close the descriptor
        first, save, read it back, then remove everything that was made."""
        import json
        import shutil
        import storage as _storage
        folder = tempfile.mkdtemp(prefix="clawfacts-check-")
        fd, path = tempfile.mkstemp(dir=folder, suffix=".json")
        os.close(fd)
        try:
            if not _storage.save_json(path, {"ok": 1}):
                return False
            with open(path, encoding="utf-8") as fh:
                if json.load(fh) != {"ok": 1}:
                    return False
            # The swap must leave no .tmp-* file beside the real one.
            return os.listdir(folder) == [os.path.basename(path)]
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def _sing_me_a_song_is_a_song():
        """'Docbot sing me a song' / 'make me a poem' used to get one
        rambling line ABOUT a song. A performance ask is recognised,
        written whole at a bigger completion budget, cleaned line by
        line under the same rails as any chat line, and delivered over
        several messages a gap apart - the first tagged to the asker.
        Questions about real songs and narration are never performances,
        and an overheard 'sing me a song' never starts one."""
        import chatai as _ch
        import llm as _llm
        names = ("doc", "docbot")
        if _ch.performance_request("Docbot sing me a song", names) != \
                ("song", ""):
            return False
        if _ch.performance_request("doc make me a poem about kvack",
                                   names) != ("poem", "kvack"):
            return False
        for text in ("doc who sang that song", "doc I wrote a song yesterday",
                     "doc what's the story with the lights", "doc tell them"):
            if _ch.performance_request(text, names) is not None:
                return False
        song = ("Sure! Here's a song:\nRolling down the I-80 line,\n"
                "Coffee's cold but the load's on time,\nChorus:\n"
                "Oh the night shift hums,\nWhere the diesel goes.")
        if _ch.clean_performance(song, "song") != [
                "Rolling down the I-80 line,",
                "Coffee's cold but the load's on time,",
                "Oh the night shift hums,", "Where the diesel goes."]:
            return False
        if _ch.clean_performance("@kvack line\nline two\nline three",
                                 "song"):
            return False
        if getattr(_llm, "PERFORMANCE_MAX_TOKENS", 0) <= getattr(
                _llm, "CHAT_MAX_TOKENS", 999):
            return False
        if "max_tokens" not in _llm.chat_reply.__code__.co_varnames:
            return False
        if not hasattr(_bot.TwitchBot, "_perform") \
                or not hasattr(_bot.TwitchBot, "_drip"):
            return False
        # End to end, off the network: four paced messages, first tagged.
        import os as _os
        import tempfile as _tf
        b = _bot.TwitchBot(dict(
            _bot.DEFAULTS, nick="n", channel="#c", chat_ai_enabled=True,
            llm_api_key="k", chat_ai_perform_delay=0,
            beef_state_path=_os.path.join(_tf.mkdtemp(), "bs.json"),
            memory_db_path=_os.path.join(_tf.mkdtemp(), "m.db"),
            persona_state_path=_os.path.join(_tf.mkdtemp(), "p.json"),
            subgoal_state_path=_os.path.join(_tf.mkdtemp(), "sg.json")))
        said = []
        b._say = said.append
        b._log = lambda *a, **k: None
        b._access.helix = None
        orig = _llm.chat_reply
        _llm.chat_reply = lambda s, u, c, max_tokens=None: song
        try:
            b._on_message("kvack", "#c", "doc sing me a song", "kvack", "")
            while not b._jobs.empty():
                nick, login, badges, command, argument = b._jobs.get()
                if command == "chime":
                    b._do_chime(nick, argument)
                elif command == "say":
                    b._say(argument)
        finally:
            _llm.chat_reply = orig
        return said[:1] == ["@kvack Rolling down the I-80 line,"] \
            and len(said) == 4 and not said[1].startswith("@")

    def _scratch_bot(**over):
        """A TwitchBot on scratch state files, mute and off the network."""
        import os as _os
        import tempfile as _tf
        import bot as _bot
        b = _bot.TwitchBot(dict(
            _bot.DEFAULTS, nick="TruckingWithDocBot", channel="#c",
            chat_ai_enabled=True,
            beef_state_path=_os.path.join(_tf.mkdtemp(), "bs.json"),
            memory_db_path=_os.path.join(_tf.mkdtemp(), "m.db"),
            persona_state_path=_os.path.join(_tf.mkdtemp(), "p.json"),
            subgoal_state_path=_os.path.join(_tf.mkdtemp(), "sg.json"),
            **over))
        b._say = lambda *a, **k: None
        b._log = lambda *a, **k: None
        b._access.helix = None
        return b

    def _weather_is_one_sentence_from_weatherapi():
        """With weatherapi_key set, a weather question is answered from
        weatherapi.com as one sentence to the asker - 'kvack, it is
        currently Clear in Wilkes-Barre, Pennsylvania. 63°F (17°C). Feels
        like ... Wind is blowing from the SW at ... humidity. Visibility:
        ... Precipitation: ...' - never trimmed by max_fact_chars. No key
        (or a rejected one) keeps the Open-Meteo path exactly as it was."""
        import io as _io
        import urllib.error as _ue
        import chatai as _ch
        if not _ch.factual_question("docbot weather in paris?",
                                    ("doc", "docbot")):
            return False
        wapi = {"location": {"name": "Wilkes-Barre", "region": "Pennsylvania",
                             "country": "United States of America"},
                "current": {"temp_c": 17.2, "temp_f": 63.0,
                            "condition": {"text": "Clear"},
                            "wind_mph": 4.3, "wind_kph": 6.8, "wind_dir": "SW",
                            "precip_mm": 0.0, "precip_in": 0.0,
                            "humidity": 61, "feelslike_c": 16.1,
                            "feelslike_f": 61.0, "vis_km": 10.0,
                            "vis_miles": 6.0}}
        meteo = {"current": {
            "temperature_2m": 63.0, "apparent_temperature": 61.0,
            "relative_humidity_2m": 61, "precipitation": 0,
            "weather_code": 0, "wind_speed_10m": 4.3,
            "wind_direction_10m": 230, "wind_gusts_10m": 6}}

        def live(url, params=None, timeout=0):
            if url == funfacts.WEATHERAPI_API:
                if params.get("key") != "good":
                    raise _ue.HTTPError(url, 401, "x", {}, _io.BytesIO(
                        b'{"error":{"code":2006,"message":"invalid"}}'))
                return wapi
            if url == funfacts.OPEN_METEO_API:
                return meteo
            raise AssertionError(url)

        saved = (funfacts._http_get_json, funfacts._osm_geocode,
                 funfacts._lookup_all)
        funfacts._http_get_json = live
        funfacts._osm_geocode = lambda _p: {
            "name": "Wilkes-Barre", "state": "Pennsylvania",
            "country": "United States", "lat": 41.25, "lon": -75.88}
        funfacts._lookup_all = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("weather reached search"))
        said = []
        try:
            out = []
            for key, limit in (("good", 80), ("", 200), ("bad", 200)):
                with funfacts._cache_lock:
                    funfacts._cache.clear()
                b = _scratch_bot(weatherapi_key=key, max_fact_chars=limit)
                said.clear()
                b._say = said.append
                b._do_chime("kvack",
                            "Docbot whats the weather in wilkes barre, pa")
                out.append(said[-1] if said else "")
        finally:
            (funfacts._http_get_json, funfacts._osm_geocode,
             funfacts._lookup_all) = saved
            with funfacts._cache_lock:
                funfacts._cache.clear()
        want = ("kvack, it is currently Clear in Wilkes-Barre, Pennsylvania. "
                "63°F (17°C). Feels like 61°F (16°C). Wind is blowing from "
                "the SW at 4 mph (7 km/h). 61% humidity. Visibility: 6 miles "
                "(10 km). Precipitation: 0.0 in (0.0 mm).")
        meteo_line = ("Weather | Wilkes-Barre, Pennsylvania: Currently 63°F "
                      "with clear skies; feels like 61°F; humidity 61%; wind "
                      "SW at 4 mph.")
        return out == [want, meteo_line, meteo_line]

    def _live_data_takes_the_fast_lane():
        """'Docbot whats the weather currently in Brewster, NY' got nothing
        live: a different mention answered 40s earlier had the 60s
        cooldown holding it, and the one worker can drop a held reading
        without a trace. Weather/sunrise questions are answered at once
        on their own thread, no model, no mention clock, paced per viewer."""
        import threading as _th
        import chatai as _ch
        import llm as _llm
        import bot as _bot_mod
        if not hasattr(_ch, "live_data_question") \
                or not hasattr(_bot_mod.TwitchBot, "_answer_live_data"):
            return False
        wapi = {"location": {"name": "Brewster", "region": "New York",
                             "country": "United States of America"},
                "current": {"temp_f": 65.0, "temp_c": 18.3,
                            "condition": {"text": "Partly cloudy"},
                            "humidity": 55}}
        saved = (funfacts._http_get_json, funfacts._osm_geocode,
                 _llm.chat_reply)
        funfacts._http_get_json = lambda url, params=None, timeout=0: wapi
        funfacts._osm_geocode = lambda _p: None
        _llm.chat_reply = lambda s, u, c, **k: "Copy that, hon - still here."
        said = []
        try:
            with funfacts._cache_lock:
                funfacts._cache.clear()
            b = _scratch_bot(llm_api_key="k", weatherapi_key="abc")
            b._say = said.append

            def pump():
                while not b._jobs.empty():
                    nick, login, badges, command, argument = b._jobs.get()
                    if command == "chime":
                        b._do_chime(nick, argument)
                    elif command == "say":
                        b._say(argument)

            b._on_message("Hardclaws", "#c", "docbot you there?",
                          "hardclaws", "moderator/1")
            pump()
            b._on_message("Hardclaws", "#c", "Docbot whats the weather "
                          "currently in Brewster, NY", "hardclaws",
                          "moderator/1")
            for t in _th.enumerate():
                if t.name == "live-data":
                    t.join(5)
            pump()
        finally:
            (funfacts._http_get_json, funfacts._osm_geocode,
             _llm.chat_reply) = saved
            with funfacts._cache_lock:
                funfacts._cache.clear()
        return said == ["@Hardclaws Copy that, hon - still here.",
                        "Hardclaws, it is currently Partly cloudy in "
                        "Brewster, New York. 65°F (18°C). 55% humidity."]

    def _mention_cooldown_is_per_viewer():
        """Live-fire 14:07-14:14: ten direct questions from four people,
        four answered. One 60s mention clock for the whole channel held
        everyone behind the last person's reply, the held queue kept
        three and dropped the oldest silently, and the worker dropped a
        released question without a word. Now the cooldown is per viewer
        with an 8s channel pace, the queue holds eight (one per person)
        and every drop is logged."""
        import llm as _llm
        import bot as _bot_mod
        if not hasattr(_bot_mod.TwitchBot, "_mention_wait") \
                or _bot_mod.DEFAULTS.get("chat_ai_mention_pace") != 8:
            return False
        src = pathlib.Path("bot.py").read_text(encoding="utf-8")
        if "dropped at the worker" not in src:
            return False
        saved = _llm.chat_reply
        answers = iter(["Forty-two, final answer.",
                        "Seventeen, no refunds on that one."])
        _llm.chat_reply = lambda s, u, c, **k: next(answers)
        said, logs = [], []
        try:
            b = _scratch_bot(llm_api_key="k")
            b._say = said.append
            b._log = logs.append
            t0 = time.time()
            b._mark_mention_reply("yeyeboi", t0 - 30)   # his reply 30s ago
            b._on_message("Yeyeboi", "#c", "docbot pick a number", "yeyeboi",
                          "")
            held = b._jobs.empty() and len(b._chat_ai_pending) == 1
            # Another viewer inside Yeyeboi's minute: her own clock is
            # clear, so she is answered now.
            b._on_message("Dani", "#c", "docbot pick one for me", "dani", "")
            while not b._jobs.empty():
                nick, login, badges, command, argument = b._jobs.get()
                if command == "chime":
                    b._do_chime(nick, argument)
            # The held asker is now also told to wait, on its own thread;
            # the point of this check is that DANI is answered at once.
            ok_dani = [l for l in said if "on it - give me" not in l] == [
                "@Dani Forty-two, final answer."]
            ok_log = any("their own cooldown" in l for l in logs)
            # Nine people asking: the queue holds eight and names the drop.
            b2 = _scratch_bot(llm_api_key="k")
            logs2 = []
            b2._log = logs2.append
            names = ["a1", "b2", "c3", "d4", "e5", "f6", "g7", "h8", "i9"]
            for n in names:
                b2._mark_mention_reply(n, t0 - 30)
            for n in names:
                b2._on_message(n, "#c", "docbot pick a number", n, "")
            ok_queue = len(b2._chat_ai_pending) == 8 and any(
                "queue full - dropped a1" in l for l in logs2)
            return held and ok_dani and ok_log and ok_queue
        finally:
            _llm.chat_reply = saved

    def _distilling_is_paced():
        """Every persona reply used to spend a second model call (~400
        prompt tokens) distilling the same twenty lines into memory -
        that is how the day's Groq budget was gone before the stream.
        A viewer is distilled on first contact, then only after ten
        minutes AND four new lines of theirs."""
        import llm as _llm
        import bot as _bot_mod
        if _bot_mod.DEFAULTS.get("chat_ai_distill_lines") != 4 \
                or _bot_mod.DEFAULTS.get("chat_ai_distill_minutes") != 10:
            return False
        saved = _llm.chat_reply
        calls = {"distill": 0}
        words = ("diesel chrome sunrise kansas coffee weigh station polka "
                 "windshield cruise showers payday moon fuel cargo snacks "
                 "gravel thunder ledger biscuit canyon lantern harbor velvet "
                 "pepper walnut saddle meadow copper anchor ribbon tundra "
                 "orbit falcon marble cactus timber glacier pickle trumpet "
                 "quartz badger nickel willow comet dagger fossil helmet"
                 ).split()
        n = len(words) // 4
        state = {"i": 0}

        def _model(s, u, c, **k):
            if "extract durable facts" in s:
                calls["distill"] += 1
                return "NOTHING WORTH KEEPING"
            i = state["i"]
            state["i"] += 1
            group = words[(i % 4) * n:(i % 4 + 1) * n]
            return " ".join(group[(i // 4 + j) % n]
                            for j in range(5)).capitalize() + "."

        _llm.chat_reply = _model
        try:
            b = _scratch_bot(llm_api_key="k", chat_ai_mention_cooldown=0,
                             chat_ai_mention_pace=0)
            b._say = lambda _l: None

            def pump():
                while not b._jobs.empty():
                    nick, login, badges, command, argument = b._jobs.get()
                    if command == "chime":
                        b._do_chime(nick, argument)

            for i in range(8):
                b._on_message("kvack", "#c", f"docbot thing {i} about my rig",
                              "kvack", "")
                pump()
            first_only = calls["distill"] == 1
            last_t, seen = b._distilled["kvack"]
            b._distilled["kvack"] = (last_t - 601, seen)   # 10 min pass
            b._on_message("kvack", "#c", "docbot and my dog", "kvack", "")
            pump()
            again = calls["distill"] == 2
            last_t, seen = b._distilled["kvack"]
            b._distilled["kvack"] = (last_t - 601, seen)   # 10 more min,
            b._on_message("kvack", "#c", "docbot lol", "kvack", "")  # 1 line
            pump()
            return first_only and again and calls["distill"] == 2
        finally:
            _llm.chat_reply = saved

    def _rate_limits_walk_the_chain():
        """Groq's 429 is per MODEL (gpt-oss-120b's 200k/day is not
        gpt-oss-20b's own bucket), and 'tokens per day' means hours, not
        two minutes. A 429 now rests THAT model for as long as the error
        says and the line moves on: the same-provider spare first, then
        each model of the fallback chain (llm_fallback_model takes a
        comma-separated list)."""
        import io as _io
        import json as _json
        import urllib.error as _ue
        if not hasattr(_llm2, "_rate_limit_window") \
                or not hasattr(_llm2, "fallback_models"):
            return False
        tpd = (b'{"error":{"message":"Rate limit reached for model '
               b'openai/gpt-oss-120b on tokens per day (TPD): Limit 200000, '
               b'Used 199706, Requested 392. Please try again in '
               b'2h7m3.5s."}}')
        if not 7000 < _llm2._rate_limit_window(tpd.decode()) < 8000:
            return False
        if _llm2._rate_limit_window("tokens per minute (TPM) ... 3s") != 120:
            return False
        cfg = {"llm_api_key": "gsk",
               "llm_base_url": "https://api.groq.com/openai/v1",
               "llm_model": "openai/gpt-oss-120b",
               "llm_fallback_key": "or",
               "llm_fallback_base_url": "https://openrouter.ai/api/v1",
               "llm_fallback_model": "nvidia/nemotron-3-super-120b-a12b:free,"
                                     " nex-agi/nex-n2.5-pro:free"}
        if _llm2.fallback_models(cfg) != [
                "nvidia/nemotron-3-super-120b-a12b:free",
                "nex-agi/nex-n2.5-pro:free"]:
            return False
        models = []

        def _fake(req, timeout=60):
            model = _json.loads(req.data.decode("utf-8"))["model"]
            models.append(model)
            if model in ("openai/gpt-oss-120b",
                         "nvidia/nemotron-3-super-120b-a12b:free"):
                raise _ue.HTTPError(req.full_url, 429, "rate", {},
                                    _io.BytesIO(tpd))
            if model == "openai/gpt-oss-20b":
                raise _ue.HTTPError(req.full_url, 429, "rate", {},
                                    _io.BytesIO(b'{"error":{"message":'
                                                b'"tokens per minute (TPM)'
                                                b' try again in 4s"}}'))
            return _io.BytesIO(_json.dumps(
                {"choices": [{"message": {"content": "Line from " + model}}]}
            ).encode("utf-8"))

        _orig = _llm2.urllib.request.urlopen
        _llm2.urllib.request.urlopen = _fake
        try:
            _llm2.reset_disable_state()
            got = _llm2.chat_reply("s", "u" * 20, cfg)
            ok1 = got == "Line from nex-agi/nex-n2.5-pro:free" and models == [
                "openai/gpt-oss-120b", "openai/gpt-oss-20b",
                "nvidia/nemotron-3-super-120b-a12b:free",
                "nex-agi/nex-n2.5-pro:free"]
            # The spent models rest on their own clocks; the next line
            # goes straight to the one that answered - one request.
            models.clear()
            got = _llm2.chat_reply("s", "u" * 20, cfg)
            ok2 = got == "Line from nex-agi/nex-n2.5-pro:free" and models == [
                "nex-agi/nex-n2.5-pro:free"]
            # llama's TPM rest (2 min) is shorter than gpt-oss's TPD rest:
            # when it clears, chat comes back to Groq's fast lane first.
            _llm2._MODEL_DISABLED_UNTIL[
                ("https://api.groq.com/openai/v1",
                 "openai/gpt-oss-20b")] = 0.0
            _llm2._DISABLED_UNTIL = 0.0
            models.clear()
            _llm2.urllib.request.urlopen = lambda req, timeout=60: (
                models.append(_json.loads(req.data.decode())["model"])
                or _io.BytesIO(_json.dumps({"choices": [{"message": {
                    "content": "Back on Groq."}}]}).encode()))
            got = _llm2.chat_reply("s", "u" * 20, cfg)
            ok3 = got == "Back on Groq." and models == [
                "openai/gpt-oss-20b"]
            return ok1 and ok2 and ok3
        finally:
            _llm2.urllib.request.urlopen = _orig
            _llm2.reset_disable_state()

    def _commands_match_the_operators_os():
        """Every user-facing command comes from auth.PY.

        A stock Windows install has no python3 - it has python - and a
        Windows operator was told to "run python3 bot.py --login" at the
        exact moment their token was missing a scope, which is to say at
        the moment when following the instruction was the whole point.

        Walks the AST rather than grepping: comments and docstrings keep
        the canonical python3 spelling and are not shown to anybody, while
        a real string literal is what gets printed. An f-string splits
        into Constant pieces around the interpolation, so none of them can
        hold the hardcoded command either.
        """
        import ast as _ast
        a = __import__("auth")
        if not (hasattr(a, "PY") and a.PY in ("python", "python3")):
            return False
        if "os.name" not in _auth_src:
            return False
        for name in ("access.py", "bot.py", "moderation.py",
                     "adminpanel.py"):
            src = pathlib.Path(name).read_text(encoding="utf-8")
            if "auth.PY" not in src:
                return False
            tree = _ast.parse(src)
            docstrings = set()
            for node in _ast.walk(tree):
                if isinstance(node, (_ast.Module, _ast.FunctionDef,
                                     _ast.AsyncFunctionDef, _ast.ClassDef)):
                    body = getattr(node, "body", None)
                    if (body and isinstance(body[0], _ast.Expr)
                            and isinstance(body[0].value, _ast.Constant)
                            and isinstance(body[0].value.value, str)):
                        docstrings.add(id(body[0].value))
            for node in _ast.walk(tree):
                if (isinstance(node, _ast.Constant)
                        and isinstance(node.value, str)
                        and id(node) not in docstrings
                        and "python3 bot.py" in node.value):
                    return False
        return True

    def _news_questions_get_headlines():
        """'Docbot who got into a helicopter crash today 15th September
        2026 in California' was answered with a Wikipedia line about a
        2012 airworthiness certificate. What happened lately is looked
        up in a headline feed, quoted with outlet and age, on the
        weather fast lane - no model, no encyclopedia."""
        import threading as _th
        import urllib.request as _ur
        import llm as _llm
        if not hasattr(funfacts, "news_question"):
            return False
        if not funfacts.news_question("who got into a helicopter crash today "
                                      "15th September 2026 in California") \
                or funfacts.news_question("whats the weather today in scranton") \
                or funfacts.news_question("what is a bongo twist"):
            return False
        rss = (b'<?xml version="1.0"?><rss version="2.0"><channel><title>x'
               b"</title><item><title>Three dead in Los Angeles helicopter "
               b"crash - BBC</title><pubDate>Wed, 16 Sep 2026 03:20:20 GMT"
               b'</pubDate><source url="https://www.bbc.com">BBC</source>'
               b"</item></channel></rss>")

        class _Resp:
            def read(self):
                return rss

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        saved = (_ur.urlopen, _llm.chat_reply)
        _ur.urlopen = lambda req, timeout=8: _Resp()
        _llm.chat_reply = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no model on the news path"))
        said = []
        try:
            with funfacts._cache_lock:
                funfacts._cache.clear()
            b = _scratch_bot(llm_api_key="k")
            b._say = said.append
            b._on_message("Hardclaws", "#c", "Docbot who got into a helicopter "
                          "crash today 15th September 2026 in California",
                          "hardclaws", "moderator/1")
            for t in _th.enumerate():
                if t.name == "live-data":
                    t.join(5)
            return len(said) == 1 and said[0].startswith(
                "News | helicopter crash California: Three dead in Los "
                "Angeles helicopter crash (BBC, ")
        except AssertionError:
            return False
        finally:
            _ur.urlopen, _llm.chat_reply = saved
            with funfacts._cache_lock:
                funfacts._cache.clear()

    def _leaked_reasoning_is_caught():
        """'The user is asking me (Docbot) who my favorite NFL team is. I
        need to answer as the Commentator persona...' three times in a
        row, 44 s, no answer: a thinking model's reasoning delivered as
        the reply. It is recognised, never recovered as a trimmed slice,
        and the retry is told not to narrate."""
        import chatai as _ch
        import llm as _llm
        if not hasattr(_ch, "is_narration"):
            return False
        leak = ("The user Hardclaws is asking me (Docbot) who my favorite "
                "NFL team is. I need to answer as the Commentator persona - "
                "a veteran British sports broadcaster. Let me craft a witty "
                "line about the Bills. " * 2)
        if not _ch.is_narration(leak) or _ch.recover_direct_line(leak) \
                or _ch.is_narration("Chiefs, and I will not be taking "
                                    "questions at this time."):
            return False
        prompts = []
        replies = iter([leak, "Bills. Next question."])

        def _model(s, u, c, **k):
            if "extract durable facts" in s:
                return "NOTHING WORTH KEEPING"
            prompts.append(s)
            return next(replies)

        saved = _llm.chat_reply
        _llm.chat_reply = _model
        said, logs = [], []
        try:
            b = _scratch_bot(llm_api_key="k")
            b._say = said.append
            b._log = logs.append
            b._on_message("Hardclaws", "#c", "docbot who is your favorite "
                          "NFL team", "hardclaws", "moderator/1")
            while not b._jobs.empty():
                nick, login, badges, command, argument = b._jobs.get()
                if command == "chime":
                    b._do_chime(nick, argument)
            return (said == ["@Hardclaws Bills. Next question."]
                    and len(prompts) == 2
                    and "Do not narrate, plan or explain" in prompts[1]
                    and any("narrated its reasoning" in l for l in logs))
        finally:
            _llm.chat_reply = saved

    def _general_knowledge_goes_to_the_model():
        """'whats the avg time for someone to run 5k' / 'how long it
        take to run 5k' are general knowledge the chat model answers
        from what it knows - not encyclopedia lookups. They were routed
        at the fact engine (a Reddit thread title, then the race's
        distance). Now they reach the model, told what kind of ask it
        is; named things and live data still go to the engine first."""
        import chatai as _ch
        import bot as _bot
        import llm as _llm
        if not callable(getattr(_ch, "knowledge_question", None)):
            return False
        names = ("doc", "docbot")
        for q in ("whats the avg time for someone to run 5k",
                  "how long it take to run 5k home boy?",
                  "why is the sky blue", "how do air brakes work"):
            if not _ch.knowledge_question(q, names) \
                    or _ch.factual_question(q, names):
                return False
        for q in ("what is a bongo twist", "how tall is Mount Everest",
                  "how many trailers can a truck pull",
                  "docbot weather in paris?"):
            if _ch.knowledge_question(q, names) \
                    or not _ch.factual_question(q, names):
                return False
        b = _scratch_bot(llm_api_key="k")
        b._distill = lambda *a, **k: None
        said, prompts, engine = [], [], []
        b._say = said.append
        saved = (_bot.get_funfact, _llm.chat_reply)
        _bot.get_funfact = lambda q, o: (engine.append(q) or None)
        _llm.chat_reply = lambda s, u, c=None, **k: (
            prompts.append(u) or "Most people finish a 5K in 30 to 40 "
                                 "minutes; around 34 is typical.")
        try:
            b._on_message("kvack", "#c", "Docbot how long it take to run "
                          "5k home boy?", "kvack", "")
            while not b._jobs.empty():
                nick, login, badges, command, argument = b._jobs.get()
                if command == "chime":
                    b._do_chime(nick, argument)
            return (said == ["@kvack Most people finish a 5K in 30 to 40 "
                             "minutes; around 34 is typical."]
                    and engine == []
                    and any("general-knowledge question" in p
                            for p in prompts))
        finally:
            _bot.get_funfact, _llm.chat_reply = saved

    def _answers_are_the_kind_asked_for():
        """'whats the avg time to run 5k' -> a Reddit thread title (the
        same question, asked back); 'how long it take to run 5k' -> the
        race's DISTANCE. A question is never a source or an answer, and
        the engine posts for a how-long / how-far / how-much question
        only a line carrying that kind of figure - else nothing, so the
        chat model gets the question."""
        import llm as _llm
        if not hasattr(funfacts, "answer_kind") \
                or not hasattr(funfacts, "_is_forum_title"):
            return False
        if not funfacts._is_forum_title(
                "Whats a good average time to do 5K? : r/C25K."):
            return False
        Q = "how long it take to run 5k home boy?"
        defn = ("The 5K run is a long-distance road running competition "
                "over a distance of five kilometres (3.107 mi).")
        if funfacts.answers_kind(defn, Q) or not funfacts.answers_kind(
                "Most runners finish a 5K in 30 to 40 minutes.", Q):
            return False
        if funfacts.answer_kind("what temperature does condensation stop"):
            return False                    # 'the dew point' stays legal
        wiki = defn + (" The 5 km road distance was introduced by IAAF as "
                       "a world record event in November 2017.")

        def serve(url, params, timeout=8.0):
            if "wikipedia.org" in url:
                if params.get("list") == "search":
                    return {"query": {"search": [{"title": "5K run"}]}}
                return {"query": {"pages": [{"title": "5K run",
                                             "extract": wiki}]}}
            return {"AbstractText": "", "RelatedTopics": [
                {"Text": "Whats a good average time to do 5K? : r/C25K."}]}

        saved = (funfacts._http_get_json, _llm.is_configured,
                 _llm.any_configured, _llm.answer_question)
        funfacts._http_get_json = serve
        _llm.is_configured = _llm.any_configured = lambda o: True
        _llm.answer_question = lambda q, src, cfg: defn
        try:
            with funfacts._cache_lock:
                funfacts._cache.clear()
            got = funfacts.get_funfact(Q, {"llm_api_key": "k",
                                           "max_fact_chars": 200})
            return got is None
        finally:
            (funfacts._http_get_json, _llm.is_configured,
             _llm.any_configured, _llm.answer_question) = saved
            with funfacts._cache_lock:
                funfacts._cache.clear()

    def _a_notice_answers_the_confused_room():
        """A mod had the bot announce 'Doc is on the phone, radio silence';
        two lines later 'Your mic is muted' got nothing - not addressed,
        so an ambient chime behind a 10% roll and a cooldown the
        announcement itself had started. Now the announcement stands as
        a notice: anyone confused about the quiet stream gets it once,
        no roll, no cooldown; the persona sees it; 'doc is back' clears
        it; a plain viewer cannot plant one."""
        import chatai as _ch
        import llm as _llm
        orig = _llm.chat_reply
        _llm.chat_reply = lambda s, u, c, **k: "Copy that, radio silence."
        said = []
        try:
            b = _scratch_bot(llm_api_key="k")
            b._say = said.append

            def pump():
                while not b._jobs.empty():
                    nick, login, badges, command, argument = b._jobs.get()
                    if command == "chime":
                        b._do_chime(nick, argument)
                    elif command == "say":
                        b._say(argument)

            b._on_message("Hardclaws", "#c",
                          "Docbot can you tell every one that @TruckingWithDoc"
                          " is currently on the phone so we are in radio "
                          "silence", "hardclaws", "moderator/1")
            pump()
            b._on_message("Etched", "#c", "Your mic is muted", "etched", "")
            pump()
            b._on_message("Etched", "#c",
                          "I assume because your codriver is sleeping",
                          "etched", "")
            b._on_message("someone", "#c", "great climb earlier",
                          "someone", "")
            pump()
            if said != ["@Hardclaws Copy that, radio silence.",
                        "@Etched heads up: TruckingWithDoc is currently on "
                        "the phone so we are in radio silence"]:
                return False
            b._on_message("Hardclaws", "#c", "docbot tell everyone doc is "
                          "back", "hardclaws", "moderator/1")
            pump()
            if b._chat_ai_notice is not None:
                return False
            b = _scratch_bot(llm_api_key="k")
            b._on_message("troll", "#c", "docbot tell everyone that the "
                          "stream is over go home", "troll", "")
            pump()
            return b._chat_ai_notice is None \
                and _ch.stream_confusion("hello? no audio") \
                and not _ch.stream_confusion("the baby is sleeping")
        finally:
            _llm.chat_reply = orig

    checks = [
        ("wikipedia extract paging (excontinue)",
         getattr(funfacts, "_EXTRACT_PAGE_CAP", None) == 4),
        ("region word boundaries ('United States' is not another region)",
         hasattr(funfacts, "_US_COUNTRY_WORDS")),
        ("interesting-facts query (not 'history crime scandal')",
         "history facts famous landmark record" in open(
             funfacts.__file__, encoding="utf-8").read()),
        ("attraction vocabulary in the ranker (arcade/pinball/...)",
         "arcade" in funfacts._STRONG.pattern),
        ("namesake person stubs rejected (Joe Girard of Detroit)",
         hasattr(funfacts, "_is_person_stub")),
        ("residence claims need a source ('called Girard home')",
         hasattr(funfacts, "_RESIDENCE")),
        ("place-name abbreviations grounded (Philly = Philadelphia)",
         hasattr(funfacts, "_CAP_ALIASES")),
        ("LLM preamble can't be posted as a fact",
         "first_bullet" in open(funfacts.__file__, encoding="utf-8").read()),
        ("retrieval tracing under --debug (source + seed pool)",
         hasattr(funfacts, "DEBUG")),
        ("opt-in fact_source='llm' (ask the model directly)",
         hasattr(funfacts, "_llm_only_facts")),
        ("curated Girard, Ohio facts (arcade, Barnhisel, 1993 title)",
         bool(funfacts._spicy_db("girard, OH", 200))),
        ("unrelated articles can't lend facts ('county seat is Painesville')",
         "require_core" in open(funfacts.__file__, encoding="utf-8").read()),
        ("HTML entities unescaped ('Jan &amp; Dean' -> 'Jan & Dean')",
         funfacts._sentences("Jan &amp; Dean played there.") and
         "&amp;" not in funfacts._sentences("Jan &amp; Dean played there.")[0]),
        ("duplicate LLM lines collapsed",
         "deduped" in open(funfacts.__file__, encoding="utf-8").read()),
        ("curated Indian Lake, Ohio facts (Sandy Beach, Lewistown Reservoir)",
         bool(funfacts._spicy_db("Indian Lake, OH", 200))),
        ("works pages excluded ('Indian Lake (song)' is the Cowsills single)",
         funfacts._is_road_or_meta_title("Indian Lake (song)")),
        ("reputation/genre claims need a source ('surf rock legends')",
         hasattr(funfacts, "_REPUTATION")),
        ("curated facts are region-matched ('Girard, PA' != Girard, Ohio)",
         funfacts._spicy_db("Girard, PA", 200) is None),
        ("full state names work ('Missouri' behaves like 'MO')",
         not funfacts._text_names_other_region(
             "a village in Crawford County, Missouri, United States.", "missouri")),
        ("comma-less regions ('Cuba Missouri' = 'Cuba, Missouri')",
         funfacts._query_region("Cuba Missouri") == "missouri"
         and funfacts._query_core("Kansas City") == "kansas city"),
        ("significance padding dropped ('holds the crown', 'the star')",
         "the\\s+star" in funfacts._REPUTATION.pattern),
        ("full article read, not the 1200-char lead (exchars omitted)",
         hasattr(funfacts, "_EXTRACT_CHAR_CAP")),
        ("curated Cuba, Missouri facts (Red Rocker, Big Red Apple)",
         bool(funfacts._spicy_db("Cuba, Missouri", 200))),
        ("'It is located on...' no longer outranks real history",
         hasattr(funfacts, "_LOCATION_ONLY")),
        ("curated Jerome, Missouri facts (Stony Dell, Trail of Tears)",
         bool(funfacts._spicy_db("Jerome, Missouri", 200))),
        ("a configured search key is consulted before DuckDuckGo",
         open(funfacts.__file__, encoding="utf-8").read().index(
             '"serper", lambda q: _serper_search')
         < open(funfacts.__file__, encoding="utf-8").read().index(
             '"duckduckgo", lambda q: _duckduckgo')),
        ("missing search key is reported in the log, not silent",
         "serper source not configured" in open(
             funfacts.__file__, encoding="utf-8").read()),
        ("startup reports which fact sources are live",
         "fact sources:" in open(
             pathlib.Path(__file__).parent / "bot.py", encoding="utf-8").read()),
        ("namesake people rejected (Saint Jerome != Jerome, Missouri)",
         hasattr(funfacts, "_is_person_article")
         and funfacts._is_person_article(
             "Jerome Barnes is an American politician in the Missouri House of "
             "Representatives from the 28th district.")
         and not funfacts._is_person_article(
             "Jerome is an unincorporated community in Phelps County, Missouri.")),
        ("namesake companies rejected (Conway Publishing != Conway, MO)",
         hasattr(funfacts, "_is_non_place_article")
         and funfacts._is_non_place_article(
             "Conway Publishing, formerly Conway Maritime Press, is an imprint "
             "of Bloomsbury Publishing.")
         and not funfacts._is_non_place_article(
             "Lakemont Park opened in 1894 as a trolley park.")),
        ("curated Conway, Missouri facts (Stanley Ketchel, 1910)",
         bool(funfacts._spicy_db("Conway, Missouri", 200))
         and funfacts._spicy_db("Conway, Arkansas", 200) is None),
        ("tokens.json and config.json are git-ignored",
         "tokens.json" in (pathlib.Path(funfacts.__file__).parent
                           / ".gitignore").read_text()),
        ("role-based rate limiting (access.py tiers)",
         __import__("access").tier_from_badges("subscriber/24,moderator/1") == "moderator"
         and __import__("access").DEFAULT_TIER_COOLDOWNS["moderator"] == 30.0
         and __import__("access").DEFAULT_TIER_COOLDOWNS["follower"] == 300.0
         and __import__("access").DEFAULT_MIN_FOLLOW_AGE == 86400.0),
        ("follower check has the Helix scope it needs",
         "moderator:read:followers" in __import__("auth").SCOPES),
        ("!riddle answer revealed in 20s (was 45s)",
         'self.cfg.get("riddle_answer_delay", 20)' in open(
             pathlib.Path(__file__).parent / "bot.py", encoding="utf-8").read()
         and "45.0, self._say" not in open(
             pathlib.Path(__file__).parent / "bot.py", encoding="utf-8").read()),
        ("broken config.json is explained, not a traceback",
         "_fail_config_syntax" in open(
             pathlib.Path(__file__).parent / "bot.py", encoding="utf-8").read()
         and "does not end with a comma" in open(
             pathlib.Path(__file__).parent / "bot.py", encoding="utf-8").read()),
        ("the spicy dig checks the region (Baltimore != Mount Vernon, MO)",
         open(funfacts.__file__, encoding="utf-8").read().count(
             "_text_names_other_region(extract[:250], region)") >= 2),
        ("padded praise dropped ('everyone loves', 'small-town charm')",
         hasattr(funfacts, "_VAGUE")
         and funfacts._VAGUE.search("those classic small-town traditions everyone loves")
         and not funfacts._VAGUE.search("Past Times Arcade holds a Guinness record.")),
        ("curated Mount Vernon, Missouri facts (Julia Butterfly Hill)",
         bool(funfacts._spicy_db("Mount Vernon, MO", 200))
         and funfacts._spicy_db("Mount Vernon, Massachusetts", 200) is None),
        ("an unauthorised token reports 'unknown', not 'not following'",
         hasattr(__import__("access").Helix("c", "t", "1"), "self_test")
         and "elif self.authorised is not True:" in open(
             pathlib.Path(__file__).parent / "access.py", encoding="utf-8").read()),
        ("facts are trimmed on sentence boundaries, not mid-sentence",
         not funfacts._trim(
             "A first sentence that is complete. A second one that runs on and "
             "on until it is far past any sensible limit for a chat message.",
             60).endswith("\u2026")),
        ("!smk female|male|any game, names carry an occupation",
         hasattr(__import__("extras"), "get_smk")
         and __import__("extras").get_smk("female")[1] == "female"
         and __import__("extras").get_smk("any")[1] == "any"
         and __import__("extras").format_smk(
             __import__("extras").get_smk("any")[0]).count("(") == 3),
        ("!help lists the commands",
         "_say_help" in open(
             pathlib.Path(__file__).parent / "bot.py", encoding="utf-8").read()),
        ("!funfacts works as !funfact",
         "funfacts" in __import__("bot").FUNFACT_ALIASES
         and "funfact" in __import__("bot").FUNFACT_ALIASES),
        ("!bot on|off moderator kill switch",
         "_bot_switch" in open(
             pathlib.Path(__file__).parent / "bot.py", encoding="utf-8").read()
         and hasattr(__import__("bot").TwitchBot(
             dict(__import__("bot").DEFAULTS, nick="n", channel="#c",
                  oauth_token="oauth:x")), "paused")),
        ("a refreshed token reaches the follower-check client",
         hasattr(__import__("access").Helix("c", "t", "1"), "set_token")),
        ("the startup log names why follow checks fail",
         hasattr(__import__("access").Helix("c", "t", "1"), "describe_token")
         and hasattr(__import__("access").Helix("c", "t", "1"), "moderator_of")
         and hasattr(__import__("bot").TwitchBot(
             dict(__import__("bot").DEFAULTS, nick="n", channel="#c",
                  oauth_token="oauth:x")), "_diagnose_access")),
        ("only scopes Twitch actually has are requested at login",
         # "moderation:read:moderators" is not a Twitch scope, and neither
         # is "user:write:whispers" - Send Whisper wants user:MANAGE:
         # whispers (dev.twitch.tv/docs/api/reference#send-whisper). One
         # invented name aborts the whole device flow with "invalid scope
         # requested", so the bot could not log in at all.
         "moderation:read:moderators" not in __import__("auth").SCOPES
         and "user:write:whispers" not in __import__("auth").SCOPES
         and all(x in {"chat:read", "chat:edit", "moderator:read:followers",
                       "moderation:read", "channel:moderate",
                       "moderator:read:chatters",
                       "moderator:manage:banned_users",
                       "user:manage:whispers"}
                 for x in __import__("auth").SCOPES.split())),
        ("the bot learns its own moderator status from chat, not the API",
         hasattr(__import__("bot").TwitchBot(
             dict(__import__("bot").DEFAULTS, nick="n", channel="#c")),
             "_note_own_state")),
        ("!reminder takes a duration (60mins, 1h30m)",
         __import__("reminders").parse_delay("60mins")[0] == 3600.0
         and __import__("reminders").parse_delay("1h30m")[0] == 5400.0),
        ("!reminder takes a clock time with a timezone (01:30PDT)",
         _reminder_clock_zones_are_self_contained()),
        ("reminders survive a restart",
         hasattr(__import__("reminders").ReminderSet, "save")
         and hasattr(__import__("bot").TwitchBot(
             dict(__import__("bot").DEFAULTS, nick="n", channel="#c",
                  oauth_token="oauth:x")), "reminders")),
        ("!haul update/delete, readable by everyone",
         hasattr(__import__("haul").Cargo, "update")
         and hasattr(__import__("haul").Cargo, "delete")
         and "haul" in __import__("bot").HAUL_COMMANDS
         and hasattr(__import__("bot").TwitchBot(
             dict(__import__("bot").DEFAULTS, nick="n", channel="#c",
                  oauth_token="oauth:x")), "_say_haul")),
        ("!whois is Wikipedia, !twitch is Twitch, kept apart",
         hasattr(__import__("whois"), "lookup")
         and hasattr(__import__("whois"), "twitch_lookup")
         and hasattr(__import__("whois"), "format_twitch")
         and hasattr(__import__("access").Helix("c", "t", "1"),
                     "channel_profile")
         and "whois" in __import__("bot").WHOIS_COMMANDS
         and "twitch" in __import__("bot").TWITCH_COMMANDS
         and "helix" not in __import__("whois").lookup.__code__.co_varnames),
        ("!twitch never invents a login out of a two-word name",
         __import__("whois").twitch_lookup(
             "Aubrey Plaza", type("H", (), {"channel_profile":
                 lambda self, l: {"display_name": l}})())["found"] is False),
        ("a Helix failure is not reported as 'no such channel'",
         "couldn't reach Twitch" in __import__("whois").twitch_lookup(
             "hardclaws", type("H", (), {"channel_profile":
                 lambda self, l: (_ for _ in ()).throw(OSError())})())[
             "reason"]),
        ("!smk draws from a pool far bigger than the old 44 names",
         __import__("names").NamePool(
             path=__import__("os").path.join(
                 __import__("tempfile").mkdtemp(), "n.json")
         ).counts()["seed"] > 300),
        ("!smk tops itself up from Wikipedia in the background",
         hasattr(__import__("names"), "harvest_category")
         and hasattr(__import__("names"), "CATEGORIES")
         and __import__("bot").DEFAULTS["names_topup_enabled"] is True
         and hasattr(__import__("bot").TwitchBot(
             dict(__import__("bot").DEFAULTS, nick="n", channel="#c")),
             "_names_keeper")),
        ("the Wikipedia top-up respects rate limits",
         # Wikimedia allows an identifiable client 200 requests/min and an
         # unidentifiable one 10. Sixteen categories at 1.1s apart is ~55/min,
         # so a User-Agent with no contact in it put the bot in the 10/min tier
         # and the last six categories of every cycle came back 429.
         ("http" in __import__("names").USER_AGENT
          or "@" in __import__("names").USER_AGENT)
         and hasattr(__import__("names"), "RateLimited")
         and hasattr(__import__("names").NamePool(
             path=__import__("os").path.join(
                 __import__("tempfile").mkdtemp(), "n.json")), "_note_refusal")
         and hasattr(__import__("names"), "_retry_after")),
        ("the games survive a dead API instead of giving up",
         all(len(getattr(__import__("extras"), n)) >= 20 for n in
             ("JOKES", "FACTS", "RIDDLES", "WOULD_YOU_RATHER"))
         and "_fell_back" in __import__("extras").__dict__),
        ("!smk avoids names it has used recently",
         __import__("names").RECENT_WINDOW >= 200
         and "SEED_WEIGHT" in __import__("names").__dict__),
        ("an LLM rewrite cannot add character the source never had",
         __import__("funfacts")._grounded_filter(
             ["Aubrey Plaza: actress - and deadpan delivered with extra "
              "deadpan."], "Aubrey Plaza", "Aubrey Plaza",
             ["Aubrey Plaza is an American actress, comedian and writer."])
         == []),
        ("a quiet channel gets the chat AI's opener, offline-gated",
         hasattr(__import__("bot").TwitchBot(
             dict(__import__("bot").DEFAULTS, nick="n", channel="#c",
                  oauth_token="oauth:x")), "_chat_ai_tick")
         and "chat_ai_quiet_seconds" in __import__("bot").DEFAULTS
         and hasattr(__import__("access").Helix("c", "t", "1"), "is_live")),
        ("!cb command is wired into the dispatch",
         "CB_COMMANDS" in pathlib.Path("bot.py").read_text(encoding="utf-8")),
        ("!cb clears the _on_message allowlist, not just the dispatch",
         pathlib.Path("bot.py").read_text(encoding="utf-8").count(
             "CB_COMMANDS") >= 3),
        ("trucker radio: over a million distinct lines",
         __import__("trucker").combination_count() > 1_000_000),
        ("every CB template slot resolves (a missing pool is a KeyError)",
         all(__import__("trucker")._ways(x) > 0
             for ts in __import__("trucker").REGISTERS.values() for x in ts)),
        ("no explicit CB slang can be generated",
         not any(term in line.lower()
                 for lines in
                 list(__import__("trucker").REGISTERS.values())
                 + [list(pool)
                    for pool in __import__("trucker")._POOLS.values()]
                 for line in lines
                 for term in ("lot lizard", "sleeper creeper",
                              "male buffalo", "pickle park"))),
        ("!cb can be switched off on its own (ambient keeps going)",
         "cb_command_enabled" in
         pathlib.Path("bot.py").read_text(encoding="utf-8")),
        ("!cb can be limited to moderators or the broadcaster",
         "cb_command_access" in
         pathlib.Path("bot.py").read_text(encoding="utf-8")),
        ("a moderator can switch the random chatter from chat",
         hasattr(__import__("bot").TwitchBot, "_cb_switch")),
        ("CB lines are labelled, so chat can tell the modes apart",
         hasattr(__import__("trucker"), "LABELS")
         and set(__import__("trucker").LABELS.values()) == {"CB", "WINDOW"}),
        ("the bot can yell out the window at a car",
         "yell" in __import__("trucker").REGISTERS
         and "cb_yell_enabled" in
         pathlib.Path("bot.py").read_text(encoding="utf-8")),
        ("a 401 from Helix refreshes the token and retries once",
         hasattr(__import__("access").Helix("c", "t", "1"), "on_unauthorized")
         and hasattr(__import__("access").Helix("c", "t", "1"), "_fetch")),
        ("the token is renewed BEFORE it expires, not after",
         __import__("auth").REFRESH_MARGIN > 1800),
        ("a dead token says so instead of blaming the viewer's follow",
         "unauthorized" in
         pathlib.Path("bot.py").read_text(encoding="utf-8")),
        ("a client_id mismatch is reported, not silently ignored",
         "client-id-mismatch" in
         pathlib.Path("auth.py").read_text(encoding="utf-8")),
        ("'python3 bot.py --doctor' explains why the login dies",
         hasattr(__import__("bot"), "run_doctor")
         and hasattr(__import__("auth"), "describe_login")),
        ("a raid is answered (USERNOTICE msg-id=raid is parsed at all)",
         hasattr(_bot.TwitchBot, "_on_raid") and "USERNOTICE" in _bot_src),
        ("the shoutout link comes from the login, never the display name",
         _so.url_for("Big Al") == ""
         and _so.url_for("@Hardclaws") == "twitch.tv/hardclaws"),
        ("moderators can switch raid shoutouts off in chat",
         hasattr(_bot.TwitchBot, "_so_switch")),
        ("a moderator can create a new command in chat",
         _cc.add("checkfix", "hello")[0] and _cc.get("checkfix") == "hello"),
        ("a custom command cannot hijack a built-in like !help",
         not _cc.add("help", "hijacked")[0]),
        ("a mod-created command survives a restart", _survives_restart()),
        ("an over-long command message is refused, not truncated",
         not _cc.add("toolong", "w" * (_cc_mod.MAX_MESSAGE + 1))[0]),
        ("shoutouts follow what the channel is streaming (trucking/zwift/...)",
         hasattr(_bot.TwitchBot, "_so_theme")
         and __import__("shoutout").theme_for_game("Euro Truck Simulator 2")
         == "trucking"
         and __import__("shoutout").theme_for_game("Zwift") == "zwift"
         and __import__("shoutout").theme_for_game("Fortnite") == "fortnite"),
        ("a shoutout names a game only when Twitch confirmed the stream",
         hasattr(__import__("access").Helix("c", "t", "1"), "stream_info")
         and _shoutout_says_nothing_false()),
        ("!beef queues, names its issuer, and never picks a bystander",
         hasattr(_bot.TwitchBot, "_reply_beef")
         and "beef" in _bot.RESERVED_COMMANDS
         and "beef" in _bot.BEEF_COMMANDS
         and _beef_is_sound()),
        ("!revenge replays a lost beef inside its window",
         hasattr(_bot.TwitchBot, "_reply_revenge")
         and "revenge" in _bot.RESERVED_COMMANDS
         and "revenge" in _bot.REVENGE_COMMANDS
         and _revenge_window_and_scoreboard()),
        ("!beef stats keeps a leaderboard that survives a restart",
         hasattr(_bot.TwitchBot, "_say_beef_stats")
         and hasattr(__import__("beefstats"), "BeefState")
         and hasattr(__import__("beefstats"), "title_for")),
        ("the beef game runs without the LLM (no model, no network)",
         _beef_game_is_self_contained()),
        ("the beef LLM pass validates or falls back (never breaks the game)",
         _beef_llm_never_breaks_the_game()),
        ("beef stories carry no chrome (no BEEF |, Act n or WINNER: labels)",
         _beef_stories_carry_no_chrome()),
        ("a freeform-theme beef keeps its theme in every line (fallback too)",
         _beef_theme_fallback_stays_on_theme()),
        ("no beef message carries a bare linkable domain ('config.json')",
         _beef_chat_copy_never_autolinks()),
        ("the loser's exit line never repeats back-to-back",
         _beef_fates_never_repeat()),
        ("a rival's display name resolves via Twitch when chat never saw them",
         _beef_rival_name_resolves_via_helix()),
        ("theme fallbacks are deep, and the topic is quoted (grammar-safe)",
         _beef_theme_pools_are_deep_and_grammar_safe()),
        ("@names in the argument no longer swallow the theme",
         _beef_at_names_do_not_eat_the_theme()),
        ("funfact survives a one-typo query; questions search Wikipedia",
         _funfact_survives_a_typo_and_answers_questions()),
        ("funfact namesakes cannot speak for the subject; pools rotate",
         _funfact_namesakes_and_variety()),
        ("funfact sentences stand alone (debris, headings, anchors, pronouns)",
         _funfact_sentences_stand_alone()),
        ("funfact countries strip; substrings do not name the subject",
         _funfact_countries_and_substrings()),
        ("funfact stories outrank sizes, inventory and definitions",
         _funfact_stories_first()),
        ("lead moderators count as moderators everywhere",
         _lead_moderators_are_moderators()),
        ("funfact specific questions get specific answers",
         _funfact_specific_answers()),
        ("funfact posts neither promises nor teasers",
         _funfact_no_promises_or_teasers()),
        ("funfact record claims need figures; middot glue splits",
         _funfact_record_claims_split()),
        ("funfact headings and captions never post",
         _funfact_headings_and_captions()),
        ("funfact cuts land on clauses, never danglers",
         _funfact_cuts_are_clean()),
        ("funfact refuses hype; demonyms are not names",
         _funfact_hype_and_demonyms()),
        ("funfact mines the records when the model will not answer",
         _funfact_records_miner()),
        ("chat AI: !ask + chime-ins, bounded and safe (off by default)",
         _chat_ai_bounded_and_safe()),
        ("chat AI remembers viewers; !forget erases them",
         _chat_ai_remembers_and_forgets()),
        ("llm_no_think: Qwen3 answers instead of thinking",
         _llm_no_think_switch()),
        ("a dead model degrades gracefully: records, quips, loud 404",
         _dead_model_degrades_gracefully()),
        ("mods can switch the bot's voice at runtime (presets + custom)",
         len(_ch2.PERSONAS) >= 5
         and _ch2.PERSONAS["doc"] == _ch2.DEFAULT_PERSONA
         and callable(_ch2.persona) and _ch2.persona("sarge")
         and "_persona_command" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")
         and "_persona_text" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")),
        ("the voices come from the streamer's world, and every voice knows his story",
         len(_ch2.PERSONAS) >= 10
         and _ch2.persona("medic") and _ch2.persona("cb")
         and _ch2.persona("squaddie") and _ch2.persona("coach")
         and _ch2.persona("cowboy")
         and set(_ch2.PERSONA_BLURBS) == set(_ch2.PERSONAS)
         and "airborne" in _ch2.system_prompt()
         and "Red Dead Redemption 2" in _ch2.system_prompt()),
        ("!subgoal tracks the sub goal: set/count/add/sub/clear",
         _subgoal_command_works()),
        ("overheard questions never get FunFacts; chimes hold a bar",
         "NOT to you" in pathlib.Path(
             "chatai.py").read_text(encoding="utf-8")
         and "overheard=not (quiet or addressed)" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")
         and "strip_address" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")
         and "build unknown" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")),
        ("chat and facts survive a rate-limited provider: fallback answers",
         callable(_llm2.fallback_endpoint)
         and callable(_llm2.fallback_problem)
         and _llm2.fallback_endpoint({}) is None
         and _llm2.fallback_endpoint({"llm_fallback_key": "k",
                                      "llm_fallback_model": "m"}) is not None
         and "fallback READY" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")
         and "llm_fallback_key" in pathlib.Path(
             "config.example.json").read_text(encoding="utf-8")
         and _chat_falls_back()),
        ("a held question is acknowledged, with the wait it quotes",
         "chat_ai_ack_held" in pathlib.Path(
             "config.example.json").read_text(encoding="utf-8")
         and _held_question_is_acknowledged()),
        ("a moderator's word bans; a viewer's word does nothing",
         "moderator:manage:banned_users" in _auth.SCOPES
         and "user:manage:whispers" in _auth.SCOPES
         and "mod_logins" in pathlib.Path(
             "config.example.json").read_text(encoding="utf-8")
         and _mods_can_ban()),
        ("an ordered provider list: one dead key is skipped, not fatal",
         callable(_llm2.fallback_providers)
         and _llm2.fallback_providers({}) == []
         and "llm_fallback_providers" in pathlib.Path(
             "config.example.json").read_text(encoding="utf-8")
         and "NVIDIA_API_KEY" in pathlib.Path(
             "deploy/bot.env.example").read_text(encoding="utf-8")
         and _provider_chain_walks()),
        ("OpenRouter HTTP-200 errors keep their code and explanation",
         _openrouter_200_error_is_explicit()
         and "fallback NOT READY" in pathlib.Path(
             "llm.py").read_text(encoding="utf-8")
         and "MISSING FIX" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")),
        ("a selected free reasoning model is never randomly replaced",
         (lambda body: body.get("model") ==
             "nvidia/nemotron-3-ultra-550b-a55b:free"
             and "models" not in body)(
                 __import__("json").loads(_llm2._build_body(
                     "nvidia/nemotron-3-ultra-550b-a55b:free", "u")))
         and "fallback_model_chain" not in pathlib.Path(
             "llm.py").read_text(encoding="utf-8")
         and "fallback model not found" in pathlib.Path(
             "llm.py").read_text(encoding="utf-8")),
        ("the live bot forwards fallback/no-think options to the chat client",
         _bot_forwards_chat_options()),
        ("rough direct asks answer; an old ask cannot hijack the next reply",
         _rough_direct_ask_cannot_go_stale()),
        ("subs the bot sees in chat count toward the sub goal",
         "subgoal_auto_count" in pathlib.Path(
             "config.example.json").read_text(encoding="utf-8")
         and "subgift" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")
         and _subs_count_themselves()),
        ("the roadhouse floor: flo, commentator and noir join the voices",
         len(_ch2.PERSONAS) >= 13
         and _ch2.persona("flo") and _ch2.persona("commentator")
         and _ch2.persona("noir")
         and set(_ch2.PERSONA_BLURBS) == set(_ch2.PERSONAS)),
        ("chimes answer what was said, not a poem at nobody",
         callable(_ch2.grounded) and callable(_ch2.parrots)
         and not _ch2.grounded(
             "The freezer rattles and I'm swapping frozen beans for "
             "an oat latte while the highway whispers",
             "yeah I hipped 1athlete to that supplement")
         and _ch2.grounded("that supplement worked for him",
                           "yeah I hipped 1athlete to that supplement")
         and _ch2.too_similar(
             "I'm swapping stale jerky for a caramel macchiato",
             ["I'm swapping frozen beans for a steaming oat latte"])
         and not _ch2.parrots("that supplement worked for him",
                              "yeah I hipped him to that supplement")
         and "not about what was said" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")),
        ("an empty chat reply is retried once, then the fallback takes it",
         "reasoning_budget" in pathlib.Path(
             "llm.py").read_text(encoding="utf-8")
         and "empty chat reply" in pathlib.Path(
             "llm.py").read_text(encoding="utf-8")
         and _empty_reply_retried()),
        ("startup names its fix count - a build number even without git",
         "fixes self-check" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")
         and "check_fixes.py" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")),
        ("the follower count is one question away (Helix + baseline)",
         callable(getattr(_bot.TwitchBot, "_say_follows", None))
         and "follow_total" in pathlib.Path(
             "access.py").read_text(encoding="utf-8")
         and _follows_question_works()),
        ("current weather comes from Open-Meteo, never search snippets",
         callable(getattr(funfacts, "_weather_answer", None))
         and _current_weather_is_live()),
        ("live weather/sunrise are data; the records miner stays on topic",
         callable(getattr(funfacts, "_weather_header", None))
         and callable(getattr(funfacts, "_records_on_topic", None))
         and "kind" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")
         and _weather_and_miner_behave()),
        ("cut-off replies retry; generated half-quotes are repaired",
         "_DANGLING_TAIL" in pathlib.Path(
             "llm.py").read_text(encoding="utf-8")
         and bool(_llm2._DANGLING_TAIL.search(
             "If they try to slash wages, I\u2019ll"))
         and bool(_llm2._DANGLING_TAIL.search(
             "The last thing I would want to be"))
         and not _llm2._DANGLING_TAIL.search(
             "Running I-80 tonight, keep the hammer down")
         and callable(getattr(funfacts, "_finish_line", None))
         and funfacts._finish_line(
             'Daft Punk split, saying: "the last thing I want') ==
             "Daft Punk split."),
        ("a direct ask never goes mute on an unusable reply",
         "one retry" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")
         and "They are talking to YOU" in pathlib.Path(
             "chatai.py").read_text(encoding="utf-8")),
        ("a failed warm-up says so (the fallback was failing silently)",
         "warm-up of {model} failed" in pathlib.Path(
             "llm.py").read_text(encoding="utf-8")),
        ("notes taken in chat are kept; person-questions skip the encyclopedia",
         callable(_ch2.note_request) and callable(_ch2.named_people)
         and "asks_about_someone" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")
         and "len(text) < 12" in pathlib.Path(
             "llm.py").read_text(encoding="utf-8")
         and _notes_are_kept()),
        ("held mentions queue up and are answered late, in order",
         "_chat_ai_pending" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")
         and "answering" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")
         and "held-question queue full - dropped" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")),
        ("a message names a command that exists on the operator's OS",
         _commands_match_the_operators_os()),
        ("a dropped connection says why, not just that it dropped",
         # Live-fire: three drops in one evening, each logging only
         # "server closed the connection" - which cannot tell a PONG we
         # were too slow to send (our bug) from Twitch letting go on its
         # own (needs nothing). The drop now reports the silence, the
         # keep-alive age, whether a PING of ours went unanswered, and the
         # longest stall inside _handle.
         all(t in _bot2 for t in (
             "def _drop_forensics",
             "pong outstanding",
             "worst stall in _handle",
             "_irc_slowest_handle",
             "_pong_due",
             'startswith("PONG")',
             "self._log(self._drop_forensics())"))
         and "_pong_due = True" in _bot2),
        ("a direct answer is posted, not refused for reusing a word",
         (lambda _live, _own, _q: (
             # Live-fire: "Docbot tell us what a boomer is" was refused with
             # "my answer got mangled in the gears" because the reply said
             # "twenty years on the road" and the persona had said "years" in
             # two of its last three lines. Anti-echo is for UNSOLICITED
             # chatter, where declining is free; a person who asked is owed
             # an answer, so the direct bar drops the motif rule and keeps
             # only real duplication.
             not _ch2.too_similar(_live, _own, source=_q, direct=True)
             and _ch2.too_similar(_live, _own, source=_q)
             # A verbatim echo is still caught on the direct path.
             and _ch2.too_similar(
                 "Midnight coffee, fresh donuts, and the road",
                 ["Midnight coffee, fresh donuts, and the road"],
                 source="doc whats up", direct=True)
             # The apology path it replaced is gone, and the honest log
             # line is in.
             and "posted anyway rather than apologising" in _bot2
             and "declined after repetition retry" not in _bot2))(
             "A boomer is an old-school trucker - twenty years on the road, "
             "set in his ways, and he has run every mile you are about to.",
             ["@kvack Twenty years of nights and the coffee still does the "
              "steering.",
              "@tayfta Some roads you just eat and keep the wheels turning.",
              "@marblehead9 Thirty years on the road and I still laugh."],
             "Docbot tell us what a boomer is")),
        ("the bot cannot repeat itself or redirect to commands",
         _ch2.too_similar("Midnight snacks and that endless horizon",
                          ["Midnight coffee, fresh donuts, and the road",
                           "Midnight brew, fresh donuts, and a diesel"])
         and not _ch2.too_similar("Weighed the rig at the scale", [])
         and _ch2.clean_line("check !funfact for the lowdown") is None
         and "_chat_ai_own" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")
         and "Your own last lines" in pathlib.Path(
             "chatai.py").read_text(encoding="utf-8")
         and "point them at the !funfact command" not in pathlib.Path(
             "chatai.py").read_text(encoding="utf-8")),
        ("emoji walls never chime; chime cadence is retuned down",
         callable(getattr(_ch2, "chime_worthy", None))
         and not _ch2.chime_worthy("\U0001f3dc\ufe0f\U0001f3dc\ufe0f")
         and _ch2.chime_worthy("crushed a few tootsie rolls today")
         and _bot.DEFAULTS.get("chat_ai_chance") == 0.10
         and _bot.DEFAULTS.get("chat_ai_cooldown") == 600
         and _bot.DEFAULTS.get("chat_ai_max_hour") == 6
         and _bot.DEFAULTS.get("chat_ai_busy_messages") == 4),
        ("factual questions get the grounded answer before the persona",
         callable(getattr(_ch2, "factual_question", None))
         and _ch2.factual_question("what is a bongo twist")
         and not _ch2.factual_question("whats your favorite truck")
         and "_answer_factual" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")),
        ("the build is stamped in the log (a paste names its build)",
         "rev-parse" in pathlib.Path("bot.py").read_text(encoding="utf-8")),
        ("no silent chat failures: every dead end leaves a log line",
         "rejected by the cleaner" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")
         and "returned nothing" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")
         and "mention from {nick} held" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")
         and "HTTP {exc.code}" in pathlib.Path(
             "llm.py").read_text(encoding="utf-8")
         and "_OPINION_LINES" in pathlib.Path(
             "chatai.py").read_text(encoding="utf-8")),
        ("qwen3:4b ignoring /no_think gets the hard think:false switch",
         'body["think"] = False' in pathlib.Path(
             "llm.py").read_text(encoding="utf-8")
         and "_hard_nothink" in pathlib.Path(
             "llm.py").read_text(encoding="utf-8")),
        ("qwen3's empty think block cannot eat the answer",
         'rsplit("</think>"' in pathlib.Path("llm.py").read_text(
             encoding="utf-8")
         and "max_tokens=200" in pathlib.Path(
             "llm.py").read_text(encoding="utf-8")
         and "_STAMPED" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")),
        ("the log survives the window: log_file tees console to a file",
         "class _Tee" in pathlib.Path("bot.py").read_text(encoding="utf-8")
         and _bot.DEFAULTS.get("log_file") == ""
         and "_Tee(sys.stdout" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")
         and "empty reply" in pathlib.Path(
             "llm.py").read_text(encoding="utf-8")),
        ("a tease gets a Doc comeback when the model is down",
         (lambda: (lambda _ch: _ch.smalltalk(
             "you have alot of useless facts") in _ch._COMEBACKS
             and _ch.smalltalk("whats the most useless fact") is None
             and _ch.smalltalk("my stupid internet") is None)(
             __import__("chatai")))),
        ("a timed-out model is not asked twice (the 2-minute !ask)",
         callable(getattr(_llm2, "chat_timed_out", None))
         and "_skip_llm" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")
         and "_skip_llm" in pathlib.Path(
             "funfacts.py").read_text(encoding="utf-8")
         and "TimeoutError" in pathlib.Path(
             "llm.py").read_text(encoding="utf-8")),
        ("local prompts are trimmed: 8 room lines, 4 memories",
         "max_lines=8 if local" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")
         and "max_memories=4 if local" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")
         and "max_lines: int = 15" in pathlib.Path(
             "chatai.py").read_text(encoding="utf-8")),
        ("local models get a local-sized chat budget (30s, 120 tokens)",
         "default_to" in pathlib.Path("llm.py").read_text(encoding="utf-8")
         and "max_tokens=120" in pathlib.Path(
             "llm.py").read_text(encoding="utf-8")
         and "chat_ai_timeout" not in _bot.DEFAULTS),
        ("the model is warmed at startup, not on the first chat line",
         callable(getattr(_llm2, "warm_up", None))
         and "chat-ai-warmup" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")
         and "_chat_ai_warmup" in pathlib.Path(
             "bot.py").read_text(encoding="utf-8")),
        ("a freeform theme is kept, not silently re-genred",
         _beef_freeform_theme_is_kept()),
        ("beef_act_delay is the literal gap (no multipliers)",
         _beef_gap_is_literal()),
        ("an echoed question is never posted back as the fact",
         hasattr(funfacts, "_is_echo") and _an_echo_is_not_a_fact()),
        ("a free-form question is answered, but only from its sources",
         hasattr(funfacts, "_answer_question")
         and hasattr(funfacts, "_question_sources")
         and hasattr(__import__("llm"), "answer_question")
         and _questions_are_answered_from_sources()),
        ("a search snippet about something else is never the answer",
         hasattr(funfacts, "_names_subject")
         and _snippet_must_be_about_the_subject()),
        ("!funfact answers anything with an article, or says it cannot",
         hasattr(funfacts, "_wikipedia_topic")
         and hasattr(funfacts, "_topic_match")
         and "hardclaws/ClawFacts" in funfacts.USER_AGENT
         and _topic_lookup_answers_anything()),
        ("a harvested fun fact can stand on its own (8 real defects)",
         hasattr(funfacts, "_is_dangling")
         and hasattr(funfacts, "_is_fragment")
         and hasattr(funfacts, "_is_boring")
         and _facts_stand_alone()),
        ("'last seen playing' is sourced from Get Channel Information",
         hasattr(__import__("access").Helix("c", "t", "1"), "channel_info")
         and _last_seen_is_sourced()),
        ("state files are written atomically",
         _state_files_are_written_atomically()),
        ("'docbot sing me a song' gets a song, over several messages",
         _sing_me_a_song_is_a_song()
         and "chat_ai_perform_delay" in _bot_src
         and "chat_ai_perform_delay" in pathlib.Path(
             "config.example.json").read_text(encoding="utf-8")),
        ("weather is one sentence to the asker from weatherapi.com",
         _weather_is_one_sentence_from_weatherapi()
         and "weatherapi_key" in _bot_src
         and "weatherapi_key" in pathlib.Path(
             "config.example.json").read_text(encoding="utf-8")),
        ("a mod's announcement answers 'your mic is muted'",
         _a_notice_answers_the_confused_room()
         and "chat_ai_notice_minutes" in pathlib.Path(
             "config.example.json").read_text(encoding="utf-8")),
        ("weather/sunrise questions are answered at once, ahead of the "
         "chat AI's cooldown", _live_data_takes_the_fast_lane()),
        ("the mention cooldown is per viewer; held questions are not "
         "dropped silently", _mention_cooldown_is_per_viewer()),
        ("memory distilling is paced, not run after every reply",
         _distilling_is_paced()),
        ("what-happened questions are answered from today's headlines",
         _news_questions_get_headlines()
         and "GOOGLE_NEWS_RSS" in pathlib.Path(
             "funfacts.py").read_text(encoding="utf-8")),
        ("a how-long question gets a duration, never a distance or a "
         "question", _answers_are_the_kind_asked_for()),
        ("general knowledge ('how long to run 5k') is the chat model's "
         "question, not the fact engine's", _general_knowledge_goes_to_the_model()),
        ("a retired Groq slug is skipped for the session; the spare is "
         "gpt-oss-20b",
         getattr(_llm2, "DEFAULT_GROQ_FALLBACK", "") == "openai/gpt-oss-20b"
         and callable(getattr(_llm2, "_retire_model", None))
         and callable(getattr(_llm2, "check_models", None))
         and "llama-3.3-70b-versatile" in getattr(_llm2, "GROQ_RETIRED", {})
         and "check_models" in pathlib.Path("bot.py").read_text(
             encoding="utf-8")),
        ("one emoji with a skin tone or a ZWJ sequence counts as one",
         _ch2.clean_line("Clueless is my default setting, hon - keeps the "
                         "warranty valid. \U0001f937\u200d\u2642\ufe0f")
         is not None
         and _ch2.clean_line("Road trip then, pal \U0001f1fa\U0001f1f8")
         is not None
         and _ch2.clean_line("Two here \U0001f600 and \U0001f60e") is None),
        ("a model narrating its reasoning is caught, retried and never posted",
         _leaked_reasoning_is_caught()
         and "Never narrate, plan or explain" in __import__(
             "chatai").system_prompt("")),
        ("a rate-limited model rests alone; chat walks the fallback chain",
         _rate_limits_walk_the_chain()
         and "nemotron" in _llm2._REASONING.pattern
         and "llm_fallback_model" in pathlib.Path(
             "config.example.json").read_text(encoding="utf-8")),
        ("the admin panel: hashed logins, loopback-only by default, mod role",
         _admin_panel_is_locked_down()),
        ("working memory: the quiz it hosts and the counts it keeps, in every prompt",
         _working_memory_holds()),
        ("'whats the headlines' is the day's top stories, never a roundup page title",
         _headlines_are_top_stories()
         and "news_country" in pathlib.Path(
             "config.example.json").read_text(encoding="utf-8")),
        ("'sunrise in Hintok, ok' is Hinton, Oklahoma - never a footpath in Thailand",
         _misspelt_town_is_that_town()),
        ("the big top and middle-earth join the voices; the list goes out by crew",
         len(_ch2.PERSONAS) >= 28
         and all(_ch2.persona(v) for v in (
             "clown", "lotlizard", "spin", "yoda", "smeagol", "gandalf",
             "gimli", "samwise", "legolas", "treebeard"))
         and set(_ch2.PERSONA_BLURBS) == set(_ch2.PERSONAS)
         and callable(getattr(_ch2, "persona_name", None))
         and _ch2.persona_name("Lot Lizard") == "lotlizard"
         and _ch2.persona_name("gollum") == "smeagol"
         and sorted(n for _, ns in getattr(_ch2, "PERSONA_GROUPS", ())
                    for n in ns) == sorted(_ch2.PERSONAS)
         and "PERSONA_GROUPS" in pathlib.Path("bot.py").read_text(
             encoding="utf-8")),
    ]
    width = max(len(name) for name, _ in checks)
    missing = 0
    print("ClawFacts fix check\n")
    for name, ok in checks:
        print("  [%s] %s" % ("x" if ok else " ", name.ljust(width)))
        missing += not ok
    print("\n%d/%d present" % (len(checks) - missing, len(checks)))
    if missing:
        print("The lines above marked [ ] are missing from this copy of the code.")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
