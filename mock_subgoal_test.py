"""Offline tests for the !subgoal command - no network, no IRC.

Run:  python3 mock_subgoal_test.py
"""

import os
import tempfile

import bot as bot_mod


MOD = "moderator/1"


def _bot(**over):
    cfg = {**bot_mod.DEFAULTS,
           "nick": "TruckingWithDocBot", "channel": "#t",
           "subgoal_state_path": os.path.join(tempfile.mkdtemp(), "sg.json"),
           **over}
    b = bot_mod.TwitchBot(cfg)
    b.said = []
    b._say = b.said.append
    b._log = lambda *a, **k: None
    b._access.helix = None
    return b


def _last(b):
    return b.said[-1] if b.said else ""


def test_anyone_can_read_no_goal():
    b = _bot()
    b._say_subgoal("kvack")
    assert "no sub goal set" in b.said[0], b.said
    print("[PASS] with no goal set, the read says so")


def test_mods_set_the_goal():
    b = _bot()
    b._subgoal_mutation("amod", MOD, "set 50 wear a clown costume for a "
                                     "full driving shift")
    assert "0/50" in _last(b) and "clown costume" in _last(b), b.said
    assert "50 to go" in _last(b), b.said
    # Bad usage is corrected, not crashed on.
    b._subgoal_mutation("amod", MOD, "set fifty lol")
    assert "usage" in _last(b), b.said
    b._subgoal_mutation("amod", MOD, "set 50")      # no label
    assert "usage" in _last(b), b.said
    print("[PASS] mods set a goal with a number and a payoff")


def test_count_add_sub_keep_it_moving():
    b = _bot()
    b._subgoal_mutation("amod", MOD, "set 50 clown costume shift")
    b._subgoal_mutation("amod", MOD, "add 12")
    assert "12/50" in _last(b) and "38 to go" in _last(b), b.said
    b._subgoal_mutation("amod", MOD, "sub 2")       # a sub lapsed
    assert "10/50" in _last(b), b.said
    b._subgoal_mutation("amod", MOD, "count 40")    # sync from the dashboard
    assert "40/50" in _last(b) and "10 to go" in _last(b), b.said
    assert len(_last(b)) < 450, _last(b)
    # Reaching it changes the message.
    b._subgoal_mutation("amod", MOD, "count 50")
    assert "GOAL REACHED" in _last(b) and "clown costume" in _last(b), b.said
    # Past the goal still reads as reached, never negative "to go".
    b._subgoal_mutation("amod", MOD, "add 3")
    assert "GOAL REACHED" in _last(b) and "53/50" in _last(b), b.said
    print("[PASS] count/add/sub move the number; reaching it celebrates")


def test_viewers_read_but_cannot_write():
    b = _bot()
    b._subgoal_mutation("amod", MOD, "set 50 clown costume shift")
    said = list(b.said)
    assert b._subgoal_mutation("kvack", "", "set 2 ruin the goal") is True
    assert b.said == said, "a viewer's mutation was answered"
    assert b._subgoal.get("goal") == 50, "a viewer changed the goal"
    b._say_subgoal("kvack")
    assert "50/50" not in _last(b), b.said
    print("[PASS] viewers can read the goal but never change it")


def test_numbers_before_a_goal_are_rejected():
    b = _bot()
    b._subgoal_mutation("amod", MOD, "add 12")
    assert "no goal set yet" in _last(b), b.said
    print("[PASS] count/add/sub before a set goal name the real problem")


def test_the_goal_survives_a_restart():
    b = _bot()
    b._subgoal_mutation("amod", MOD, "set 50 clown costume shift")
    b._subgoal_mutation("amod", MOD, "add 12")
    b2 = _bot(subgoal_state_path=b.cfg["subgoal_state_path"])
    assert b2._subgoal.get("goal") == 50
    assert b2._subgoal.get("current") == 12
    assert "clown costume" in b2._subgoal.get("label", "")
    b2._say_subgoal("kvack")
    assert "12/50" in _last(b2), b2.said
    # And clear removes it for the next one.
    b2._subgoal_mutation("amod", MOD, "clear")
    b2._say_subgoal("kvack")
    assert "no sub goal set" in _last(b2), b2.said
    print("[PASS] the goal survives a restart; clear resets it")


def test_the_full_command_path_routes():
    """_on_message -> queue -> the command actually answers in chat."""
    b = _bot()
    b._on_message("amod", "#t", "!subgoal set 50 clown costume shift",
                  "amod", MOD)
    b._on_message("kvack", "#t", "!subgoal", "kvack", "")
    while not b._jobs.empty():
        nick, login, badges, command, argument = b._jobs.get()
        if command == "subgoal":
            if not b._subgoal_mutation(nick, badges, argument):
                b._say_subgoal(nick)
    assert len(b.said) == 2, b.said
    assert "clown costume" in b.said[0] and "0/50" in b.said[0], b.said
    assert "50 to go" in b.said[1], b.said
    print("[PASS] the full !subgoal path answers in chat")


def main():
    test_anyone_can_read_no_goal()
    test_mods_set_the_goal()
    test_count_add_sub_keep_it_moving()
    test_viewers_read_but_cannot_write()
    test_numbers_before_a_goal_are_rejected()
    test_the_goal_survives_a_restart()
    test_the_full_command_path_routes()
    print("\nALL PASSED \u2714")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
