"""Offline tests for the beef game's state: !revenge, !beef stats, tagging.

No network, no model - that is not just how these tests run, it is one of the
things they assert: the game must always run on what the bot already has, so
a dead Ollama, an empty OpenRouter account or a 402 that disabled the LLM
cannot take the feuds down with the fun facts.
"""

import os
import pathlib
import random
import tempfile
import time

import beef
import beefstats
import bot as bot_mod

MOD = "moderator/1"


def _bot(**over):
    cfg = dict(bot_mod.DEFAULTS, nick="bot", channel="#test",
               cooldown_seconds=0, max_message_chars=450,
               beef_state_path=os.path.join(
                   tempfile.mkdtemp(prefix="beefstats-"), "beef_state.json"),
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


def _state():
    """A BeefState on a temp file with a movable clock."""
    t = [1000.0]
    st = beefstats.BeefState(
        path=os.path.join(tempfile.mkdtemp(prefix="beefstats-"), "s.json"),
        clock=lambda: t[0])
    return st, t


def test_feud_reports_the_winner_beef_still_tells_the_story():
    outcomes = set()
    for _ in range(300):
        res = beef.feud("Hardclaws", "Rival_Rob", "zwift")
        assert res is not None
        lines = beef.beef("Hardclaws", "Rival_Rob", "zwift")
        assert isinstance(lines, list) and len(lines) == 5, lines
        assert lines[0].startswith("\U0001f525 BEEF:"), lines[0]
        assert res["winner"] in ("Hardclaws", "Rival_Rob")
        assert res["issuer_won"] == (res["winner"] == "Hardclaws")
        assert res["winner"] in res["lines"][3]
        assert res["loser"] in res["lines"][3]
        outcomes.add(res["issuer_won"])
    assert outcomes == {True, False}
    assert beef.feud("") is None and beef.beef("") == []
    print("[PASS] feud() reports its outcome; beef() still returns the lines")


def test_rematch_reads_as_a_rematch_and_stays_four_sound_lines():
    outcomes = set()
    for _ in range(300):
        res = beef.feud("Hardclaws", "Diesel Dan", "trucking", revenge=True)
        lines = res["lines"]
        assert len(lines) == 5, lines
        assert lines[0].startswith("\U0001f525 REMATCH:"), lines[0]
        # The headline closes with the second flame; a stake line may follow.
        assert lines[0].count("\U0001f525") >= 2, lines[0]
        for i, label in ((1, "Act 1"), (2, "Act 2"), (3, "Act 3")):
            assert lines[i].startswith(label), lines[i]
        assert "WINNER:" in lines[4], lines[4]
        for line in lines:
            assert len(line) < 450, (len(line), line)
            assert "{" not in line and "}" not in line, line
            assert "  " not in line, line
        outcomes.add(res["issuer_won"])
    assert outcomes == {True, False}
    # The rematch opener is a rematch opener, not a genre spark.
    sparks = beef.GENRES["trucking"]["sparks"]
    res = beef.feud("Hardclaws", "Diesel Dan", "trucking", revenge=True)
    assert res is not None
    print("[PASS] a rematch is a fresh 4-line roll under a REMATCH banner")


def test_a_window_opens_only_on_a_loss_and_closes_on_a_win():
    st, t = _state()
    st.record("Hardclaws", "Diesel Dan", "trucking", True)
    assert st.window_for("Hardclaws") is None, "a win left a window open"
    t[0] += 5
    st.record("Hardclaws", "Diesel Dan", "trucking", False)
    w = st.window_for("Hardclaws")
    assert w and w["rival"] == "Diesel Dan" and w["genre"] == "trucking", w
    assert 0 < w["seconds_left"] <= 60, w
    t[0] += 61
    assert st.window_for("Hardclaws") is None, "an expired window survived"
    # ...and the win that settles a loss clears any window the loss opened.
    st.record("Hardclaws", "Diesel Dan", "trucking", False)
    t[0] += 5
    st.record("Hardclaws", "Diesel Dan", "trucking", True)
    assert st.window_for("Hardclaws") is None
    # A loss on a freeform theme arms the window WITH the theme, so the
    # rematch is still a taco feud.
    st.record("Hardclaws", "Rival_Rob", "trucking", False,
              theme="Eating Tacos")
    w = st.window_for("Hardclaws")
    assert w and w["theme"] == "Eating Tacos", w
    print("[PASS] the window opens on a loss, expires in 60s, closes on a "
          "win, keeps the theme")


def test_scoring_counts_what_actually_happened():
    st, t = _state()
    st.record("Ann", "Diesel Dan", "trucking", True)          # +2, streak 1
    t[0] += 1
    st.record("Ann", "Diesel Dan", "trucking", True)          # +2, streak 2
    t[0] += 1
    st.record("Ann", "Diesel Dan", "trucking", False)         # -1, streak 0
    t[0] += 2
    row = st.record("Ann", "Diesel Dan", "trucking", True, revenge=True)
    assert row["beefs"] == 4 and row["wins"] == 3 and row["losses"] == 1
    assert row["points"] == 2 + 2 - 1 + beefstats.REVENGE_WIN_POINTS
    assert row["streak"] == 1 and row["best_streak"] == 2
    assert row["revenges"] == 1 and row["revenge_wins"] == 1
    print("[PASS] +2 a win, -1 a loss, +3 an avenged loss; streaks tracked")


def test_the_scoreboard_survives_a_restart():
    st, t = _state()
    st.record("Hardclaws", "Diesel Dan", "trucking", False)
    again, _ = _state()
    # Same file, fresh clock well past the window: the row survives, the
    # expired window does not.
    again.path = st.path
    again._load()
    assert again.is_player("Hardclaws")
    assert again.card("hardclaws").startswith("Hardclaws:")
    assert again.leader_line().startswith("1. Hardclaws")
    print("[PASS] the leaderboard persists; expired windows do not")


def test_the_leaderboard_orders_and_formats():
    st, t = _state()
    st.record("Ann", "x", "zwift", True)              # 2 pts
    st.record("Bob", "x", "zwift", True)              # 2 pts, 1 win
    st.record("Cid", "x", "zwift", True, revenge=True)  # 3 pts
    st.record("Dot", "x", "zwift", False)             # -1 pt
    line = st.leader_line()
    assert line.startswith("1. Cid 3pts (1W-0L"), line
    assert "2. " in line and "3. " in line
    # Same points: more wins first. Ann and Bob both have 2; a second win
    # for Bob settles it.
    st.record("Bob", "x", "zwift", True)
    line = st.leader_line()
    assert line.index("Bob") < line.index("Ann"), line
    assert st.card("nope") is None
    print("[PASS] the board orders on points then wins; unknowns have no card")


def test_titles_track_wins():
    for wins, want in ((0, "Fresh Meat"), (1, "Garden-Variety Grudge-Holder"),
                       (3, "Certified Beefstarter"), (6, "Feud Professional"),
                       (10, "Chat Menace"), (15, "Legendary Grudge")):
        assert beefstats.title_for({"wins": wins}) == want, wins
    st, t = _state()
    for _ in range(3):
        st.record("Ann", "x", "zwift", True)
    assert "Certified Beefstarter" in st.card("Ann")
    print("[PASS] titles run from Fresh Meat to Legendary Grudge")


def test_stats_subcommand_answers_instead_of_starting_a_feud():
    b, said = _bot()
    b._reply_beef("Hardclaws", "stats")
    assert b._jobs.empty(), "stats must not start a feud"
    assert said and "nobody has started a beef" in said[0], said
    for word in ("top", "leaderboard", "lb", "scoreboard"):
        said.clear()
        b._reply_beef("Hardclaws", word)
        assert b._jobs.empty() and said, word
    b._reply_beef("Hardclaws", "stats NoSuchPlayer")
    assert "no beefs on file for NoSuchPlayer" in said[-1], said
    # And a beef still happens for a name that merely starts with 'stat'.
    b._reply_beef("Hardclaws", "Statler zwift")
    out = _drain(b)
    assert len(out) == 5 and "vs. Statler" in out[0], out
    print("[PASS] stats words answer; they are never taken for a rival")


def test_stats_answer_even_while_the_game_is_off():
    b, said = _bot(beef_enabled=False)
    b._reply_beef("Hardclaws", "stats")
    assert said and "nobody has started" in said[0], said
    said.clear()
    b._beef_off = True
    b._reply_beef("Hardclaws", "top")
    assert said and "nobody has started" in said[0], said
    print("[PASS] the standings stay readable while the feuds are off")


def test_a_beef_records_and_the_board_names_you():
    b, said = _bot()
    played = False
    for _ in range(300):
        b._reply_beef("Hardclaws", "Rival_Rob zwift")
        _drain(b)
        if b.beef_state.is_player("Hardclaws"):
            played = True
            break
    assert played
    b._reply_beef("Anyone", "stats Hardclaws")
    assert "Hardclaws:" in said[-1] and "1 beef" in said[-1], said[-1]
    b._reply_beef("Anyone", "stats")
    assert "1. Hardclaws" in said[-1], said[-1]
    print("[PASS] playing a beef puts you on the board by your own name")


def test_revenge_replays_a_loss_inside_the_window():
    b, said = _bot()
    lost = False
    for _ in range(300):
        b._reply_beef("Hardclaws", "Rival_Rob zwift")
        out = _drain(b)
        if any("WINNER: Rival_Rob" in ln for ln in out):
            lost = True
            break
    assert lost, "the roll never produced a loss to avenge"
    said.clear()
    b._reply_revenge("Hardclaws")
    assert said == [], f"said inline: {said}"
    out = _drain(b)
    assert len(out) >= 4 and all(o.startswith("BEEF | ") for o in out), out
    assert "REMATCH" in out[0] and "Rival_Rob" in out[0], out[0]
    assert any("WINNER:" in ln for ln in out), out
    card = b.beef_state.card("Hardclaws")
    row = b.beef_state.players["hardclaws"]
    assert card and row["revenges"] == 1 and row["beefs"] >= 2, (card, row)
    print("[PASS] !revenge queues a 4-line rematch against the same rival")


def test_revenge_expires_and_says_so_plainly():
    b, said = _bot()
    b._reply_revenge("Newbie")
    assert b._jobs.empty()
    assert said and "nothing to avenge" in said[0], said
    for _ in range(300):
        b._reply_beef("Hardclaws", "Rival_Rob zwift")
        out = _drain(b)
        if any("WINNER: Rival_Rob" in ln for ln in out):
            break
    else:
        raise AssertionError("no loss to arm a window")
    real = b.beef_state.clock
    b.beef_state.clock = lambda: real() + 61
    said.clear()
    b._reply_revenge("Hardclaws")
    assert b._jobs.empty() and "nothing to avenge" in said[0], said
    b.beef_state.clock = real
    print("[PASS] an expired window is no window; the reply says how to start one")


def test_revenge_respects_the_off_switch():
    b, said = _bot(beef_enabled=False)
    b._reply_revenge("Hardclaws")
    assert b._jobs.empty()
    assert said and "beef_enabled" in said[0], said
    said.clear()
    b2, said2 = _bot()
    b2._beef_off = True
    b2._reply_revenge("Hardclaws")
    assert b2._jobs.empty()
    assert said2 and "switched off" in said2[0], said2
    print("[PASS] !revenge is off whenever the beef game is off")


def test_bystanders_are_never_tagged_only_players_who_are_present():
    b, said = _bot()
    # Named characters are never chat members: no beef may ever @ one.
    for _ in range(120):
        b._reply_beef("Hardclaws", "random")
        out = _drain(b)
        assert out and "@" not in out[0], out[0]
    # A typed real name is not tagged while they have never played...
    for _ in range(30):
        b._reply_beef("Hardclaws", "SpeedyDave zwift")
        out = _drain(b)
        assert out and "vs. SpeedyDave" in out[0] and "@" not in out[0], out[0]
    # ...nor while they have played but are not around...
    b.beef_state.record("SpeedyDave", "x", "zwift", True)
    b._reply_beef("Hardclaws", "SpeedyDave zwift")
    out = _drain(b)
    assert out and "@" not in out[0], out[0]
    # ...but a player who has been in chat gets the ping.
    b._beef_seen.note("SpeedyDave")
    b._reply_beef("Hardclaws", "SpeedyDave zwift")
    out = _drain(b)
    assert out and "vs. @SpeedyDave" in out[0], out[0]
    print("[PASS] tags go only to present players, never to bystanders")


def test_the_broadcaster_is_always_taggable():
    b, said = _bot(channel="#truckdoc")
    b._reply_beef("Hardclaws", "truckdoc zwift")
    out = _drain(b)
    assert out and "vs. @truckdoc" in out[0], out[0]
    # Presence still caps it for everyone else, broadcaster or not.
    print("[PASS] the channel's own broadcaster can always be tagged")


def test_the_game_runs_with_the_llm_dead():
    """The one requirement that outlives every implementation detail: a
    viewer's !beef, !revenge and !beef stats must answer with the LLM
    disabled and unconfigured - the game runs on what the bot already has."""
    for fname in ("beef.py", "beefstats.py"):
        src = pathlib.Path(fname).read_text(encoding="utf-8")
        for banned in ("import llm", "from llm", "urllib", "http", "socket",
                       "openrouter", "groq", "api_key"):
            assert banned not in src, (fname, banned)
    import llm
    old_until, old_cfg = llm._DISABLED_UNTIL, llm.is_configured
    llm._DISABLED_UNTIL = time.time() + 9999
    llm.is_configured = lambda cfg: False
    try:
        b, _ = _bot()
        for _ in range(300):
            b._reply_beef("Hardclaws", "Rival_Rob zwift")
            out = _drain(b)
            assert len(out) >= 4, out
            if any("WINNER: Rival_Rob" in ln for ln in out):
                break
        else:
            raise AssertionError("no loss to revenge")
        b._reply_revenge("Hardclaws")
        assert len(_drain(b)) >= 4
        b._reply_beef("Anyone", "stats")
        card = b.beef_state.card("Hardclaws")
        assert card and "beefs" in card
    finally:
        llm._DISABLED_UNTIL, llm.is_configured = old_until, old_cfg
    print("[PASS] the whole game answers with the LLM dead and unconfigured")


def test_revenge_is_reserved_against_custom_commands():
    assert "revenge" in bot_mod.RESERVED_COMMANDS
    assert "revenge" in bot_mod.REVENGE_COMMANDS
    assert bot_mod.REVENGE_COMMANDS not in (bot_mod.BEEF_COMMANDS,)
    print("[PASS] !revenge is reserved, so a mod cannot shadow it")


def main():
    test_feud_reports_the_winner_beef_still_tells_the_story()
    test_rematch_reads_as_a_rematch_and_stays_four_sound_lines()
    test_a_window_opens_only_on_a_loss_and_closes_on_a_win()
    test_scoring_counts_what_actually_happened()
    test_the_scoreboard_survives_a_restart()
    test_the_leaderboard_orders_and_formats()
    test_titles_track_wins()
    test_stats_subcommand_answers_instead_of_starting_a_feud()
    test_stats_answer_even_while_the_game_is_off()
    test_a_beef_records_and_the_board_names_you()
    test_revenge_replays_a_loss_inside_the_window()
    test_revenge_expires_and_says_so_plainly()
    test_revenge_respects_the_off_switch()
    test_bystanders_are_never_tagged_only_players_who_are_present()
    test_the_broadcaster_is_always_taggable()
    test_the_game_runs_with_the_llm_dead()
    test_revenge_is_reserved_against_custom_commands()
    print("\nALL PASSED \u2714")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
