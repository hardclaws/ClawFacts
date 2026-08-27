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
