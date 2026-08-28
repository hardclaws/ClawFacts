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
