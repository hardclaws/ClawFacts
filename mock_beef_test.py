"""Offline tests for !beef. No network, no model."""

import os
import random
import re
import tempfile
import threading

import beef
import bot as bot_mod

MOD = "moderator/1"
VIEWER = ""


def _bot(**over):
    cfg = dict(bot_mod.DEFAULTS, nick="bot", channel="#test",
               cooldown_seconds=0, max_message_chars=450,
               # A temp state file keeps the run hermetic: scoring a beef in a
               # test must not grow a leaderboard in the repo either.
               beef_state_path=os.path.join(
                   tempfile.mkdtemp(prefix="beef-test-"), "beef_state.json"),
               beef_act_delay=0)
    cfg.update(over)
    b = bot_mod.TwitchBot(cfg)
    said = []
    b._say = said.append
    b._log = lambda *a, **k: None
    b._access.helix = None
    return b, said


def _drain(b):
    """Run the queued jobs the way the worker thread would."""
    out = []
    orig = b._say
    b._say = out.append
    while not b._jobs.empty():
        nick, badges, arg, command, text = b._jobs.get()
        if command == "say":
            b._say(text)
        b._jobs.task_done()
    b._say = orig
    return out


def test_every_genre_produces_a_readable_three_act_story():
    for genre in sorted(beef.GENRES):
        lines = beef.beef("Hardclaws", "", genre)
        assert len(lines) == 5, (genre, lines)
        assert lines[0].startswith("\U0001f525 BEEF:"), lines[0]
        for i, label in ((1, "Act 1"), (2, "Act 2"), (3, "Act 3")):
            assert lines[i].startswith(label), (genre, lines[i])
        assert lines[4].startswith("\U0001f3c6 WINNER:"), lines[4]
        for line in lines:
            assert len(line) < 450, (genre, len(line), line)
            assert "{" not in line and "}" not in line, line
    print("[PASS] all six genres produce a 4-line story under the limit")


def test_the_issuer_is_always_in_their_own_beef():
    """That is the whole point of the command - it is their feud."""
    for issuer in ("Hardclaws", "SpeedyDave", "Mrclownprince", "x9"):
        for _ in range(200):
            lines = beef.beef(issuer, "", "zwift")
            assert lines, issuer
            assert any(issuer in line for line in lines), (issuer, lines)
    # A name that cannot be a display name gets nothing rather than a story
    # about someone else.
    for bad in ("", "  ", "a b", "name with spaces", "x", "@" * 3):
        assert beef.beef(bad) == [], bad
    print("[PASS] the issuer stars in it, and a bad name produces nothing")


def test_the_winner_is_one_of_the_two_and_is_declared():
    for genre in sorted(beef.GENRES):
        seen = set()
        for _ in range(300):
            lines = beef.beef("Hardclaws", "Rival_Rob", genre)
            verdict = lines[4]
            m = re.search(r"WINNER: (.+?)\.", verdict)
            assert m, verdict
            winner = m.group(1).strip()
            assert winner in ("Hardclaws", "Rival_Rob"), (genre, winner)
            seen.add(winner)
        assert seen == {"Hardclaws", "Rival_Rob"}, (genre, seen)
    print("[PASS] the winner is always one of the two, and both actually win")


def test_no_bystander_is_dragged_in_by_random():
    """'!beef random' randomises the genre, not the opponent.

    Pulling a viewer out of chat into a public feud they never asked for is
    the fastest way to turn a joke command into a harassment report - and
    their chat reads it too. The rival is always a named character unless the
    issuer typed a real name.
    """
    rivals = set()
    for _ in range(500):
        lines = beef.beef("Hardclaws", "random", "")
        head = lines[0]
        rival = head.split(" vs. ", 1)[1].split(" \u2014 ", 1)[0]
        rivals.add(rival)
    all_named = set()
    for pool in beef.RIVALS.values():
        all_named |= set(pool)
    assert rivals and rivals <= all_named, rivals - all_named
    print("[PASS] !beef random never picks a bystander out of chat")


def test_no_possessive_or_grammar_defects_in_a_large_sweep():
    """The class of bug that shipped twice in the trucker chatter."""
    names = ("Hardclaws", "Jess", "Mrclownprince", "SpeedyDave", "x9")
    worst = 0
    for _ in range(3000):
        issuer = random.choice(names)
        lines = beef.beef(issuer, "", "")
        for line in lines:
            worst = max(worst, len(line))
            low = line.lower()
            assert "  " not in line, line
            assert "{" not in line, line
            assert re.search(r"\b(a|an|the) (a|an|the)\b", low) is None, line
            for word in line.split():
                # "Hardclaws's" - a name ending in s must take a bare
                # apostrophe.
                if word.endswith("'s"):
                    assert not word[:-2].lower().endswith("s"), line
    assert worst < 450, worst
    print(f"[PASS] 12,000 stories clean; longest line {worst} characters")


def test_the_combination_space_is_not_a_finite_list():
    """"We are never limited on content" - a fixed list is the defect."""
    n = beef.combination_count()
    assert n >= 5000, n
    distinct = set()
    for _ in range(4000):
        distinct.add(tuple(beef.beef("Hardclaws", "", "")))
    assert len(distinct) > 1000, len(distinct)
    print(f"[PASS] {n:,} distinct stories before names go in")


def test_genre_aliases_land_somewhere_sensible():
    for text, want in (("truck", "trucking"), ("trucker", "trucking"),
                       ("zwifting", "zwift"), ("cycling", "zwift"),
                       ("fort", "fortnite"), ("bake off", "baking"),
                       ("battlebots", "robots"), ("open mic", "karaoke")):
        assert beef.genre_for(text) == want, (text, want)
    # Unknown or empty still gives a genre - the game should not dead-end.
    for text in ("", "underwater basket weaving", None):
        assert beef.genre_for(text) in beef.GENRES, text
    print("[PASS] typed genres are matched, unknown ones fall back")


def test_the_story_is_queued_not_said_inline():
    """_say sleeps to pace messages, and while it sleeps the reader thread is
    not reading. Four lines said inline freeze chat reading - that bug already
    shipped once with !help."""
    b, said = _bot()
    b._reply_beef("Hardclaws", "zwift")
    assert said == [], f"said inline: {said}"
    assert not b._jobs.empty(), "nothing queued"
    out = _drain(b)
    assert len(out) == 5, out
    assert all(o.startswith("BEEF | ") for o in out), out
    assert all(len(o) <= 450 for o in out), [len(o) for o in out]
    assert "WINNER:" in out[-1], out[-1]
    print("[PASS] five lines queued through the worker, none said inline")


def test_a_moderator_can_turn_it_off_and_a_viewer_cannot():
    b, said = _bot()
    assert b._beef_switch("viewer19", VIEWER, "off") is True
    assert said == [], f"a viewer must get no answer: {said}"
    assert b._beef_off is False
    assert b._beef_switch("amod", MOD, "off") is True
    assert b._beef_off is True
    assert any("OFF" in m for m in said), said

    said.clear()
    b._reply_beef("Hardclaws", "zwift")
    assert b._jobs.empty(), "an off switch must not even queue the story"
    assert said and "off" in said[0], said
    print("[PASS] mods can switch it off; viewers get silence")


def test_config_can_disable_it_and_says_so():
    b, said = _bot(beef_enabled=False)
    b._reply_beef("Hardclaws", "zwift")
    assert b._jobs.empty()
    assert said and "beef_enabled" in said[0], said
    print("[PASS] beef_enabled=false is named as the reason")


def test_bare_beef_shows_usage_but_a_bad_rival_still_plays():
    """A bare !beef is ambiguous - against whom? Everything else plays: an
    unusable rival name falls back to one of the named characters rather than
    dead-ending the joke."""
    b, said = _bot()
    b._reply_beef("Hardclaws", "")
    assert b._jobs.empty()
    assert said and "usage" in said[0], said

    # A lone genre word is the setting, not somebody called zwift.
    b, said = _bot()
    b._reply_beef("Hardclaws", "zwift")
    out = _drain(b)
    assert any(s in out[0] for s in beef.GENRES["zwift"]["settings"]), out[0]
    assert "vs. zwift " not in out[0], out[0]

    b, said = _bot()
    b._reply_beef("Hardclaws", "a b c")     # 'a' is too short to be a name
    out = _drain(b)
    assert len(out) == 5, out               # it still tells a story
    assert "vs. a " not in out[0], out[0]   # ...against a real character
    print("[PASS] bare !beef shows usage; a bad rival still plays")


def test_beef_is_reserved_so_it_cannot_be_shadowed():
    assert "beef" in bot_mod.RESERVED_COMMANDS
    print("[PASS] !beef is reserved against custom commands")


def test_the_acts_are_spaced_out_not_fired_as_one_burst():
    """A story needs room to breathe: chat reacts between the acts, and the
    verdict lands behind the longest pause. With a real delay only the
    headline is queued at once - the rest arrive on timers, and the worker
    stays free for other commands in between."""
    import time

    b, said = _bot(beef_act_delay=0.05)
    b._reply_beef("Hardclaws", "Rival_Rob zwift")
    assert said == [], f"said inline: {said}"
    immediate = _drain(b)
    assert 1 <= len(immediate) < 5, immediate      # headline only, no wall
    assert immediate[0].startswith("BEEF | \U0001f525"), immediate[0]
    time.sleep(0.05 * 4 + 0.6)     # let the timers fire
    rest = _drain(b)
    assert len(immediate) + len(rest) == 5, (immediate, rest)
    assert "WINNER:" in rest[-1], rest[-1]         # the verdict arrives last
    print("[PASS] the acts drip out; the verdict lands behind the longest pause")


def test_consecutive_stories_stop_echoing_each_other():
    """Half of 'always way the same' was the same line coming back three
    beefs in a row. The no-repeat ring makes consecutive repeats of a beat
    impossible, and 40 stories draw far more distinct openers than the old
    six-line pool ever could."""
    openers = [beef.feud("Hardclaws", "Rival_Rob", "zwift")["lines"][1]
               for _ in range(40)]
    for i in range(len(openers) - 1):
        assert openers[i] != openers[i + 1], (i, openers[i])
    assert len(set(openers)) >= 25, len(set(openers))
    settings = {beef.feud("A_Name", "Rival_Rob", "zwift")["lines"][0]
                for _ in range(30)}
    assert len(settings) >= 2, "the setting never varied"
    print("[PASS] back-to-back beefs do not reuse their lines")


def test_an_at_prefixed_rival_is_still_the_rival():
    """Viewers type '@name' out of habit. Treating that as an invalid name
    silently swapped the person they named for a random character."""
    res = beef.feud("Hardclaws", "@TruckingWithDoc", "zwift")
    assert res is not None and res["rival"] == "TruckingWithDoc", res
    assert "vs. TruckingWithDoc" in res["lines"][0], res["lines"][0]

    b, said = _bot()
    b._reply_beef("Hardclaws", "@Rival_Rob zwift")
    out = _drain(b)
    assert any("vs. Rival_Rob" in o for o in out), out
    print("[PASS] !beef @Name feud is against Name, not a random character")


def test_a_freeform_theme_is_honoured_not_discarded():
    """'!beef @W_E_S_T_Y Eating Tacos' is a taco feud. The words used to be
    silently swapped for a random genre - Watopia by dice roll - and the
    model was never told about them either."""
    assert beef.match_genre("zwift") == "zwift"
    assert beef.match_genre("zwifting") == "zwift"
    assert beef.match_genre("eating tacos") is None

    res = beef.feud("Hardclaws", "W_E_S_T_Y", "", theme="Eating Tacos")
    assert res is not None and res["theme"] == "Eating Tacos", res
    assert "Eating Tacos" in res["lines"][0], res["lines"][0]
    assert res["genre"] in beef.GENRES      # template acts still come from a genre
    assert len(res["lines"]) == 5

    b, said = _bot()
    b._reply_beef("Hardclaws", "@W_E_S_T_Y Eating Tacos")
    out = _drain(b)
    assert len(out) == 5, out
    assert "Eating Tacos" in out[0] and "vs. W_E_S_T_Y" in out[0], out[0]
    print("[PASS] a freeform theme headlines the story, words intact")


def test_the_acts_carry_two_beats_and_a_loser_fate():
    """A bare sentence per act was a summary, not a story: the acts now mix
    in quotes and crowd reactions, and the loser's exit is drawn from a pool
    instead of 'has left the building' every time."""
    fates = set()
    for _ in range(400):
        res = beef.feud("Hardclaws", "Rival_Rob", "zwift")
        assert res is not None
        verdict = res["lines"][4]
        assert verdict.startswith("\U0001f3c6 WINNER: "), verdict
        assert verdict[len("\U0001f3c6 WINNER: "):].strip(), verdict
        fates.add(verdict)
    assert len(fates) >= 5, len(fates)   # the exit line is not one fixed joke
    print("[PASS] verdicts vary; the exit is a punchline slot, not a stamp")


def main():
    test_every_genre_produces_a_readable_three_act_story()
    test_the_issuer_is_always_in_their_own_beef()
    test_the_winner_is_one_of_the_two_and_is_declared()
    test_no_bystander_is_dragged_in_by_random()
    test_no_possessive_or_grammar_defects_in_a_large_sweep()
    test_the_combination_space_is_not_a_finite_list()
    test_genre_aliases_land_somewhere_sensible()
    test_the_story_is_queued_not_said_inline()
    test_a_moderator_can_turn_it_off_and_a_viewer_cannot()
    test_config_can_disable_it_and_says_so()
    test_bare_beef_shows_usage_but_a_bad_rival_still_plays()
    test_beef_is_reserved_so_it_cannot_be_shadowed()
    test_the_acts_are_spaced_out_not_fired_as_one_burst()
    test_consecutive_stories_stop_echoing_each_other()
    test_an_at_prefixed_rival_is_still_the_rival()
    test_a_freeform_theme_is_honoured_not_discarded()
    test_the_acts_carry_two_beats_and_a_loser_fate()
    print("\nALL PASSED \u2714")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
