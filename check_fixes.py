#!/usr/bin/env python3
"""Print which of the Girard-era fixes are present in the code you are running.

    python3 check_fixes.py

Run this after copying files out of a zip / applying a patch. If a line says
False (or `_EXTRACT_PAGE_CAP` is not 4), the copy you are running is older than
the fix it names — no need to guess from chat behaviour.
"""
import pathlib
import sys

import funfacts


def main() -> int:
    # Built once so the checks below can exercise the real classes rather than
    # grep for a string that happens to appear in a comment.
    import os
    import tempfile

    import bot as _bot
    import customcmds as _cc_mod
    import shoutout as _so

    def _fresh():
        return _cc_mod.CommandSet(
            path=os.path.join(tempfile.mkdtemp(prefix="clawfacts-check-"),
                              "cc.json"),
            reserved=_bot.RESERVED_COMMANDS)

    _cc = _fresh()

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
        return all("poledancing" in ln for ln in res["lines"][1:4])

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
         # "moderation:read:moderators" is not a Twitch scope. One invented
         # name aborts the whole device flow with "invalid scope requested",
         # so the bot could not log in at all.
         "moderation:read:moderators" not in __import__("auth").SCOPES
         and all(x in {"chat:read", "chat:edit", "moderator:read:followers",
                       "moderation:read", "channel:moderate",
                       "moderator:read:chatters"}
                 for x in __import__("auth").SCOPES.split())),
        ("the bot learns its own moderator status from chat, not the API",
         hasattr(__import__("bot").TwitchBot(
             dict(__import__("bot").DEFAULTS, nick="n", channel="#c")),
             "_note_own_state")),
        ("!reminder takes a duration (60mins, 1h30m)",
         __import__("reminders").parse_delay("60mins")[0] == 3600.0
         and __import__("reminders").parse_delay("1h30m")[0] == 5400.0),
        ("!reminder takes a clock time with a timezone (01:30PDT)",
         __import__("reminders").parse_clock("01:30PDT")[1] == "PDT"
         and __import__("reminders").parse_clock(
             "01:30 America/Los_Angeles")[1] == "America/Los_Angeles"),
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
        ("a quiet channel gets something to react to",
         {"smk", "riddle", "joke", "randomfact", "wyr"}
         <= set(__import__("bot").IDLE_COMMANDS)
         and hasattr(__import__("bot").TwitchBot(
             dict(__import__("bot").DEFAULTS, nick="n", channel="#c",
                  oauth_token="oauth:x")), "_idle_chat_tick")
         and hasattr(__import__("access").Helix("c", "t", "1"), "is_live")),
        ("!cb command is wired into the dispatch",
         "CB_COMMANDS" in pathlib.Path("bot.py").read_text(encoding="utf-8")),
        ("!cb clears the _on_message allowlist, not just the dispatch",
         pathlib.Path("bot.py").read_text(encoding="utf-8").count(
             "CB_COMMANDS") >= 3),
        ("the CB clock is re-rolled, never a fixed period",
         "_cb_next_delay" in pathlib.Path("bot.py").read_text("utf-8")
         and "random.uniform" in
         pathlib.Path("bot.py").read_text(encoding="utf-8")),
        ("trucker chatter: over a million distinct lines",
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
         __import__("storage").save_json(
             __import__("tempfile").mkstemp(suffix=".json")[1], {"ok": 1})),
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
