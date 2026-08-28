"""Tests for customcmds.py and its bot.py wiring.

Run:  python3 mock_customcmds_test.py

No network and no Twitch. Every test passes its own temp state file, so a run
never touches the real custom_commands.json.

The end-to-end tests drive a real !cmd message through bot.TwitchBot._on_message
and let the real _worker dispatch it - the two places a new command can be
silently dropped (the unrecognised-command gate and the worker's if/elif chain)
are exactly the two places a unit test of the module alone would miss.
"""

import json
import os
import tempfile
import threading

import access as access_mod
import bot as bot_mod
import customcmds


def _tmp(name):
    return os.path.join(tempfile.mkdtemp(prefix="clawfacts-cc-"), name)


def _set(**kw):
    return customcmds.CommandSet(path=_tmp("custom_commands.json"),
                                 reserved=bot_mod.RESERVED_COMMANDS, **kw)


class _Allow:
    allowed, reason, tier, wait = True, "", "moderator", 0.0


class _Deny:
    allowed, reason, tier, wait = False, "not a follower", "viewer", 12.0


def _bot(**cfg):
    # cooldown_seconds is a channel-wide gate in _on_message, not a per-user
    # one: two commands in the same test would otherwise drop the second.
    base = dict(bot_mod.DEFAULTS, nick="bot", channel="#test", prefix="!",
                custom_commands_enabled=True, cooldown_seconds=0)
    base.update(cfg)
    b = bot_mod.TwitchBot(base)
    said, logs = [], []
    b._say = said.append
    b._log = logs.append
    b.custom_cmds = _set()
    b._resolve_broadcaster = lambda ch: None      # no Helix
    b._access.check = lambda login, badges: _Allow()
    b._access.commit = lambda login: None
    # A denial is announced by _note_denial, which is its own feature. Keep it
    # out of `said` so these tests can assert on the command's own output.
    b._note_denial = lambda *a, **k: logs.append(f"denial note: {a}")
    return b, said, logs


MOD = "moderator/1"
VIEWER = ""


def _run_worker(b):
    """Start the real worker and wait for the queued job to be handled."""
    threading.Thread(target=b._worker, daemon=True).start()
    b._jobs.join()


# --- the store ------------------------------------------------------------

def test_add_edit_delete_round_trip():
    s = _set()
    ok, why = s.add("discord", "Join us on Discord - discord.gg/example")
    assert ok and "created" in why, why
    assert "discord" in s
    assert s.get("DISCORD") == "Join us on Discord - discord.gg/example"

    ok, why = s.edit("discord", "New link: discord.gg/example")
    assert ok and "updated" in why, why
    assert s.get("discord") == "New link: discord.gg/example"

    ok, why = s.delete("discord")
    assert ok and "deleted" in why, why
    assert "discord" not in s and len(s) == 0
    print("[PASS] add, edit and delete all work and say what happened")


def test_a_custom_command_cannot_shadow_a_builtin():
    s = _set()
    # If this were allowed, "!help" typed by a moderator would stop meaning
    # help - the command would still look like it worked.
    for name in sorted(bot_mod.RESERVED_COMMANDS):
        ok, why = s.add(name, "hijacked")
        assert not ok, name
        assert "built-in" in why, (name, why)
        assert name not in s, name
    print("[PASS] no custom command can redefine a built-in")


def test_names_are_validated():
    s = _set()
    for bad in ("", "a", "1abc", "_abc", "two words", "with!bang",
                "under_score_is_fine_but_this_one_is_far_far-too-long",
                "a" * 26, "emoji🚚", "dots.in.it"):
        ok, why = s.add(bad, "x")
        assert not ok, bad
        assert "not a usable command name" in why, (bad, why)
    # Upper case is accepted and normalised, because chat does not care.
    ok, why = s.add("RoadRules", "x")
    assert ok and "roadrules" in s, why
    assert s.get("ROADRULES") == "x"
    assert s.names() == ["roadrules"], s.names()
    print("[PASS] names must look like a command; case is folded, not refused")


def test_a_message_is_required():
    s = _set()
    for blank in ("", "   ", "\t"):
        ok, why = s.add("ok_name", blank)
        assert not ok, repr(blank)
        assert "something to say" in why, why
    print("[PASS] a command with nothing to say is refused")


def test_a_long_message_is_refused_not_truncated():
    s = _set()
    long = "w" * (customcmds.MAX_MESSAGE + 1)
    ok, why = s.add("longwinded", long)
    assert not ok
    assert str(len(long)) in why, why      # names the length it was given
    assert str(customcmds.MAX_MESSAGE) in why, why
    assert "longwinded" not in s
    # Exactly at the limit is fine.
    ok, _ = s.add("justfits", "w" * customcmds.MAX_MESSAGE)
    assert ok
    print("[PASS] an over-long message is refused, with both lengths named")


def test_edit_and_delete_name_what_is_missing():
    s = _set()
    ok, why = s.edit("ghost", "x")
    assert not ok and "no !ghost to edit" in why, why
    ok, why = s.delete("ghost")
    assert not ok and "no !ghost to delete" in why, why
    print("[PASS] editing or deleting something absent says so, with the name")


def test_add_points_at_edit_for_an_existing_name():
    s = _set()
    assert s.add("dup", "first")[0]
    ok, why = s.add("dup", "second")
    assert not ok and "edit" in why, why
    assert s.get("dup") == "first"          # the original is untouched
    print("[PASS] adding over an existing name refuses and points at edit")


def test_there_is_a_ceiling_on_how_many():
    s = _set()
    for i in range(customcmds.MAX_COMMANDS):
        assert s.add(f"cmd_{i}", "x")[0], i
    ok, why = s.add("one_too_many", "x")
    assert not ok and str(customcmds.MAX_COMMANDS) in why, why
    print(f"[PASS] a channel can define up to {customcmds.MAX_COMMANDS} and "
          f"no more")


def test_commands_survive_a_restart():
    path = _tmp("custom_commands.json")
    s = customcmds.CommandSet(path=path, reserved=bot_mod.RESERVED_COMMANDS)
    assert s.add("schedule", "We stream Tuesdays at 7pm AEST")[0]
    assert s.save() is True

    again = customcmds.CommandSet(path=path, reserved=bot_mod.RESERVED_COMMANDS)
    assert again.get("schedule") == "We stream Tuesdays at 7pm AEST"

    # And a reserved name smuggled into the file by hand is dropped on load.
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"commands": {"help": "hijacked", "okname": "kept"}}, fh)
    third = customcmds.CommandSet(path=path, reserved=bot_mod.RESERVED_COMMANDS)
    assert "help" not in third and third.get("okname") == "kept", third.names()
    print("[PASS] they persist, and a hand-edited file cannot hijack !help")


def test_a_corrupt_file_does_not_stop_the_bot():
    path = _tmp("custom_commands.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{not json at all")
    s = customcmds.CommandSet(path=path, reserved=bot_mod.RESERVED_COMMANDS)
    assert len(s) == 0
    assert s.add("recover", "still works")[0]
    # A file that is valid JSON but the wrong shape is ignored the same way.
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(["not", "a", "dict"], fh)
    assert len(customcmds.CommandSet(
        path=path, reserved=bot_mod.RESERVED_COMMANDS)) == 0
    print("[PASS] an unreadable or wrong-shaped file is ignored, not fatal")


def test_user_is_filled_in_and_unknown_placeholders_are_left_alone():
    r = customcmds.CommandSet.render
    assert r("Hi {user}!", "Trucker") == "Hi Trucker!"
    assert r("No placeholders here", "Trucker") == "No placeholders here"
    # A typo is visible rather than silently eating the text.
    assert r("{usr} typo", "Trucker") == "{usr} typo"
    assert r("{user} and {user}", "Al") == "Al and Al"
    assert r("{user}", "") == "{user}"       # no asker, nothing invented
    print("[PASS] {user} is filled in; an unknown placeholder is left visible")


# --- the chat surface -----------------------------------------------------

def test_only_a_moderator_can_change_them():
    b, said, logs = _bot()
    b._cmd_command("viewer19", VIEWER, "add secret hi")
    assert b.custom_cmds.get("secret") is None
    assert said == [], said                  # silent, so it cannot be flooded
    assert any("not a mod" in l for l in logs), logs

    b._cmd_command("amod", MOD, "add secret hi")
    assert b.custom_cmds.get("secret") == "hi"
    assert len(said) == 1 and "created" in said[0], said

    b._cmd_command("theboss", "broadcaster/1", "delete secret")
    assert b.custom_cmds.get("secret") is None
    print("[PASS] add/edit/delete are moderator-only and silent for viewers")


def test_list_is_open_to_everyone():
    b, said, logs = _bot()
    b._cmd_command("viewer19", VIEWER, "list")
    assert len(said) == 1 and "no custom commands yet" in said[0], said
    b.custom_cmds.add("discord", "x")
    b.custom_cmds.add("rules", "y")
    b._cmd_command("viewer19", VIEWER, "list")
    assert "!discord" in said[1] and "!rules" in said[1], said[1]
    assert "2 custom commands" in said[1], said[1]
    b.custom_cmds.delete("rules")
    b._cmd_command("viewer19", VIEWER, "list")
    assert "1 custom command:" in said[2], said[2]   # not "1 custom commands"
    print("[PASS] !cmd list is readable by anyone, and pluralises correctly")


def test_bad_subcommand_and_missing_arguments_are_answered():
    b, said, logs = _bot()
    b._cmd_command("amod", MOD, "frobnicate discord")
    assert "I don't do" in said[0] and "add, edit, delete or list" in said[0]
    b._cmd_command("amod", MOD, "")
    assert "!cmd add <name> <message>" in said[1], said[1]
    b._cmd_command("amod", MOD, "add")
    assert "!cmd add <name> <what it should say>" in said[2], said[2]
    b._cmd_command("amod", MOD, "delete")
    assert "needs the command name" in said[3], said[3]
    b._cmd_command("amod", MOD, "add noname")
    assert "!cmd add <name> <what it should say>" in said[4], said[4]
    print("[PASS] a wrong or half-typed !cmd is answered, not swallowed")


def test_a_mod_created_command_answers_in_chat():
    b, said, logs = _bot()
    b._cmd_command("amod", MOD, "add discord Join us - discord.gg/example")
    said.clear()

    # The real path: the gate must let !discord through to the queue.
    b._on_message("viewer19", "#test", "!discord", login="viewer19",
                  badges=VIEWER)
    assert b._jobs.qsize() == 1, "the unrecognised-command gate dropped it"
    _run_worker(b)
    assert said == ["@viewer19 Join us - discord.gg/example"], said

    # {user} is the asker, not the mod who wrote it.
    assert b.custom_cmds.add("hi", "G'day {user}, welcome aboard")[0]
    b._on_message("trucker42", "#test", "!hi", login="trucker42", badges=VIEWER)
    _run_worker(b)
    assert said[-1] == "@trucker42 G'day trucker42, welcome aboard", said[-1]
    print("[PASS] a mod-created command answers in chat, through the real gate")


def test_a_custom_command_still_passes_the_access_gate():
    b, said, logs = _bot()
    b._cmd_command("amod", MOD, "add secret only for followers")
    said.clear()
    b._access.check = lambda login, badges: _Deny()

    b._on_message("driveby", "#test", "!secret", login="driveby", badges=VIEWER)
    _run_worker(b)
    assert said == [], said        # no free bypass of the rate limit
    assert any("secret denied for driveby" in l for l in logs), logs
    print("[PASS] a custom command obeys the same access gate as !funfact")


def test_a_custom_command_obeys_bot_off():
    b, said, logs = _bot()
    b._cmd_command("amod", MOD, "add discord hi")
    b.paused = True
    said.clear()
    b._on_message("viewer19", "#test", "!discord", login="viewer19",
                  badges=VIEWER)
    assert b._jobs.qsize() == 0, "ran while the bot was switched off"
    assert said == []
    # But defining one still works while paused - a mod is not locked out.
    b._cmd_command("amod", MOD, "add rules Be nice")
    assert b.custom_cmds.get("rules") == "Be nice"
    print("[PASS] they go quiet with !bot off; defining one still works")


def test_config_can_switch_them_off():
    b, said, logs = _bot(custom_commands_enabled=False)
    b.custom_cmds.add("discord", "hi")
    b._on_message("viewer19", "#test", "!discord", login="viewer19",
                  badges=VIEWER)
    _run_worker(b)
    assert said == [], said
    b._cmd_command("viewer19", VIEWER, "list")
    assert "switched off in config.json" in said[0], said[0]
    assert "custom_commands_enabled" in said[0], said[0]
    print("[PASS] custom_commands_enabled=false stops them and names itself")


def test_a_command_deleted_before_it_is_handled_says_nothing():
    b, said, logs = _bot()
    b.custom_cmds.add("gone", "bye")
    b._on_message("viewer19", "#test", "!gone", login="viewer19", badges=VIEWER)
    b.custom_cmds.delete("gone")          # mod deletes it while queued
    _run_worker(b)
    assert said == [], said
    print("[PASS] a command deleted before it is handled says nothing")


def test_help_mentions_it():
    b, said, logs = _bot()
    b._say_help("amod", MOD)
    _run_worker(b)              # help lines are queued, not said inline
    joined = " ".join(said)
    assert "!cmd add <name> <message>" in joined, joined
    assert "!cmd delete <name>" in joined, joined
    # Viewers are not advertised a command they cannot use.
    b2, said2, _ = _bot()
    b2._say_help("viewer19", VIEWER)
    _run_worker(b2)
    assert "!cmd add" not in " ".join(said2), said2
    print("[PASS] !help tells moderators about !cmd, and only moderators")


def test_help_never_exceeds_the_message_limit():
    # Regression: the command list used to be one "|" line, so every command
    # added pushed the same message closer to Twitch's limit. Adding !so took
    # it to exactly 500 - over the limit once "PRIVMSG #channel :" is counted.
    for limit in (450, 300, 120):
        b, said, logs = _bot(max_message_chars=limit)
        b._say_help("amod", MOD)
        _run_worker(b)
        over = [l for l in said if len(l) > limit]
        assert not over, (limit, [len(l) for l in over])
        # Nothing was dropped to make it fit: every command still appears.
        joined = " ".join(said)
        for name in ("!funfact", "!smk", "!joke", "!riddle", "!wyr", "!haul",
                     "!whois", "!twitch", "!cb", "!so", "!cmd add"):
            assert name in joined, (limit, name)
    # A single over-long entry is kept whole rather than cut in half.
    one = bot_mod.TwitchBot._chunks(["a very long single entry here"], 5)
    assert one == ["a very long single entry here"], one
    assert bot_mod.TwitchBot._chunks(["", "x", None if False else "", "y"], 50) \
        == ["x | y"]
    print("[PASS] !help is split to fit, and no command is lost doing it")


def main():
    tests = [
        test_add_edit_delete_round_trip,
        test_a_custom_command_cannot_shadow_a_builtin,
        test_names_are_validated,
        test_a_message_is_required,
        test_a_long_message_is_refused_not_truncated,
        test_edit_and_delete_name_what_is_missing,
        test_add_points_at_edit_for_an_existing_name,
        test_there_is_a_ceiling_on_how_many,
        test_commands_survive_a_restart,
        test_a_corrupt_file_does_not_stop_the_bot,
        test_user_is_filled_in_and_unknown_placeholders_are_left_alone,
        test_only_a_moderator_can_change_them,
        test_list_is_open_to_everyone,
        test_bad_subcommand_and_missing_arguments_are_answered,
        test_a_mod_created_command_answers_in_chat,
        test_a_custom_command_still_passes_the_access_gate,
        test_a_custom_command_obeys_bot_off,
        test_config_can_switch_them_off,
        test_a_command_deleted_before_it_is_handled_says_nothing,
        test_help_mentions_it,
        test_help_never_exceeds_the_message_limit,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception as exc:
            failed += 1
            print(f"[FAIL] {test.__name__}: {exc!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
