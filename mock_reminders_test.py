"""Tests for reminders.py, haul.py and their bot.py wiring.

Run:  python3 mock_reminders_test.py
No network, no Twitch, no real clock dependence - every test passes its own
`now` and its own temp state file.
"""

import datetime
import json
import os
import tempfile
import time

import bot as bot_mod
import reminders
import storage
import haul


def _tmp(name):
    return os.path.join(tempfile.mkdtemp(prefix="clawfacts-"), name)


# ---- durations ----------------------------------------------------------
def test_parse_delay():
    cases = {
        "60mins": 3600.0, "60 mins": 3600.0, "60min": 3600.0, "1h": 3600.0,
        "90s": 90.0, "1h30m": 5400.0, "1h 30 m": 5400.0, "2d": 172800.0,
        "1.5h": 5400.0, "5": 300.0,          # a bare number is minutes
        "1 week": 604800.0,
        "1h30": 5400.0,               # a trailing bare number is minutes
    }
    for spec, want in cases.items():
        got, label = reminders.parse_delay(spec)
        assert got == want, (spec, got, want)
        assert label, spec
    for spec in ("", "abc", "0", "-5m", "10 parsecs", "soon", "1h 5x"):
        seconds, why = reminders.parse_delay(spec)
        assert seconds is None, (spec, seconds)
        assert why, spec
    # The bounds are real, not advisory.
    assert reminders.parse_delay("5s")[0] is None, "under the minimum"
    assert reminders.parse_delay("15d")[0] is None, "over the maximum"
    assert reminders.human_seconds(3661) == "1 hour 1 minute 1 second"
    print("[PASS] durations: 60mins, 1h30m, 90s, 2d, bare minutes, and rejections")


# ---- clock times --------------------------------------------------------
def test_parse_clock():
    now = time.time()

    def utc(due):
        return datetime.datetime.fromtimestamp(due, datetime.timezone.utc)

    # An abbreviation is a fixed offset: 01:30 PDT is always 08:30 UTC.
    due, label, _ = reminders.parse_clock("01:30PDT", now)
    assert label == "PDT" and utc(due).strftime("%H:%M") == "08:30", (label, utc(due))

    due, label, _ = reminders.parse_clock("1:30pm PDT", now)
    assert utc(due).strftime("%H:%M") == "20:30", utc(due)

    due, label, _ = reminders.parse_clock("1:30 p.m. EST", now)
    assert label == "EST" and utc(due).strftime("%H:%M") == "18:30", (label, utc(due))

    # An IANA name is accepted as-is, and is not mistaken for "am".
    due, label, _ = reminders.parse_clock("01:30 America/Los_Angeles", now)
    assert label == "America/Los_Angeles", label
    assert utc(due).hour in (8, 9), "PDT or PST depending on the season"

    due, label, _ = reminders.parse_clock("01:30 Australia/Sydney", now)
    assert label == "Australia/Sydney", label

    due, label, _ = reminders.parse_clock("01:30 UTC-7", now)
    assert label == "UTC-7" and utc(due).strftime("%H:%M") == "08:30", label

    # Seconds are honoured when given.
    due, _, _ = reminders.parse_clock("01:30:45 UTC", now)
    assert utc(due).second == 45, utc(due)

    # Midnight and noon in 12-hour form, the classic off-by-twelve.
    for spec, want in (("12:00am UTC", "00:00"), ("12:00pm UTC", "12:00"),
                       ("12:30am UTC", "00:30"), ("11:59pm UTC", "23:59")):
        due, _, _ = reminders.parse_clock(spec, now)
        assert utc(due).strftime("%H:%M") == want, (spec, utc(due))

    for spec in ("25:00", "13:30pm", "1:75", "01:30 XYZ", "notatime",
                 "01:30pm extra", ""):
        due, why, _ = reminders.parse_clock(spec, now)
        assert due is None, (spec, due)
        assert why, spec
    print("[PASS] clock times: PDT/EST, IANA names, UTC-7, am/pm, midnight, noon")


def test_clock_rolls_to_tomorrow():
    """A time already past today means tomorrow, not 'immediately'."""
    now = time.time()
    passed = (datetime.datetime.now() - datetime.timedelta(hours=2)
              ).strftime("%H:%M")
    due, _, rolled = reminders.parse_clock(passed, now)
    assert rolled and due > now, (passed, rolled, due - now)
    assert due - now > 20 * 3600, "should land roughly a day out"
    print("[PASS] a time that has passed today rolls to tomorrow")


# ---- the pending set ----------------------------------------------------
def test_add_list_cancel(tmp=None):
    rs = reminders.ReminderSet(_tmp("r.json"))
    assert len(rs) == 0

    item, why = rs.add("60mins", "Check the lights are working", "amod")
    assert item is None or why is None, why
    assert item.id == 1 and item.label == "in 1 hour", item.label

    item2, _ = rs.add("01:30PDT", "Check your lights", "amod")
    assert item2 is not None
    assert len(rs) == 2

    # Errors that must not create anything.
    for spec, message in (("60mins", ""), ("tomorrow", "do it"),
                          ("60mins", "x" * 500)):
        got, why = rs.add(spec, message, "amod")
        assert got is None and why, (spec, why)
    assert len(rs) == 2, "failed adds must not leave a reminder behind"

    # Cancel by number, then everything.
    removed, why = rs.cancel("2")
    assert removed == 1 and why is None, why
    assert [r.id for r in rs.pending()] == [1]
    assert rs.cancel("99") == (0, "there is no reminder #99")
    assert rs.cancel("") == (0, "cancel which one? !reminder list shows the numbers")
    assert rs.cancel("all")[0] == 1
    assert len(rs) == 0
    print("[PASS] create, list, cancel one, cancel all, and the error paths")


def test_pop_due_only_takes_what_is_ready():
    rs = reminders.ReminderSet(_tmp("r.json"))
    rs.add("90s", "soon", "amod")
    rs.add("2h", "later", "amod")
    now = time.time()

    assert rs.pop_due(now) == [], "nothing is due yet"
    assert len(rs) == 2

    ready = rs.pop_due(now + 120)
    assert [r.message for r in ready] == ["soon"], ready
    assert len(rs) == 1 and rs.pending()[0].message == "later"
    print("[PASS] pop_due takes only what has come due")


def test_pending_cap():
    rs = reminders.ReminderSet(_tmp("r.json"))
    for i in range(reminders.MAX_PENDING):
        item, why = rs.add("1h", f"item {i}", "amod")
        assert item is not None, why
    item, why = rs.add("1h", "one too many", "amod")
    assert item is None and "cancel one first" in why, why
    print(f"[PASS] the pending list stops at {reminders.MAX_PENDING}")


def test_survives_a_restart():
    """The point of persisting: a reminder set for tomorrow must not be lost
    to an update or a crash overnight."""
    path = _tmp("r.json")
    first = reminders.ReminderSet(path)
    first.add("3h", "Pay the registration", "amod")
    first.add("90s", "Check the lights", "amod")
    assert os.path.exists(path)

    second = reminders.ReminderSet(path)
    assert len(second) == 2, len(second)
    assert {r.message for r in second.pending()} == {
        "Pay the registration", "Check the lights"}

    # One comes due while the bot is down; it still fires on the way back up.
    time.sleep(0.01)
    rows = storage.load_json(path)
    rows[0]["due"] = time.time() - 60
    storage.save_json(path, rows)
    third = reminders.ReminderSet(path)
    assert len(third.pop_due()) == 1, "an overdue reminder should still fire"

    # But one from three days ago is stale news and is dropped, not posted.
    rows = storage.load_json(path)
    rows[0]["due"] = time.time() - 3 * 86400
    storage.save_json(path, rows)
    fourth = reminders.ReminderSet(path)
    assert fourth.dropped_on_load == 1, fourth.dropped_on_load
    assert len(fourth.pop_due()) == 0
    print("[PASS] reminders survive a restart; stale ones are dropped, not posted")


def test_corrupt_state_does_not_stop_the_bot():
    path = _tmp("r.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{not json at all")
    rs = reminders.ReminderSet(path)
    assert len(rs) == 0
    assert rs.add("1h", "still works", "amod")[0] is not None
    print("[PASS] a corrupt state file is ignored, not fatal")


# ---- the cargo board ----------------------------------------------------
def test_cargo():
    path = _tmp("t.json")
    c = haul.Cargo(path)
    assert not c.is_set
    assert c.delete() == (False, "nothing is logged as the haul right now")
    assert c.update("") == (False, "update to what? !haul update Produce")
    assert c.update("x" * 400)[0] is False

    ok, why = c.update("Produce")
    assert ok and why is None, why
    assert c.phrase() == "Produce" and c.is_set

    # Persists, and comes back with who set it.
    c.set_by = "amod"
    c.save()
    again = haul.Cargo(path)
    assert again.phrase() == "Produce" and again.set_by == "amod"

    c.set_at = time.time() - 4 * 60
    assert c.age() == "4 minutes ago", c.age()
    c.set_at = time.time()
    assert c.age() == "just now", c.age()

    assert c.delete()[0] is True and not c.is_set
    assert haul.Cargo(path).phrase() == ""
    print("[PASS] !haul update/delete, persistence, and the age line")


def test_storage_is_atomic_and_lossless():
    path = _tmp("s.json")
    payload = [{"id": 1, "message": "Check the lights — café", "due": 1.5}]
    assert storage.save_json(path, payload) is True
    assert storage.load_json(path) == payload
    # The temp file is renamed into place, so nothing partial is left behind.
    leftovers = [f for f in os.listdir(os.path.dirname(path))
                 if f.startswith(".tmp-")]
    assert not leftovers, leftovers
    assert storage.load_json(_tmp("absent.json"), default=[]) == []
    print("[PASS] state files round-trip and leave no temp files behind")


# ---- the bot wiring -----------------------------------------------------
def _bot():
    b = bot_mod.TwitchBot(dict(bot_mod.DEFAULTS, nick="bot", channel="#test",
                               prefix="!"))
    b.reminders = reminders.ReminderSet(_tmp("r.json"))
    b.cargo = haul.Cargo(_tmp("t.json"))
    said = []
    b._say = said.append
    return b, said


def test_reminders_are_moderator_only():
    b, said = _bot()
    b._reminder_command("rando", "", "60mins steal the show")
    b._reminder_command("subguy", "subscriber/12", "60mins nope")
    b._reminder_command("vipguy", "vip/1", "list")
    assert said == [], f"a viewer was answered: {said}"
    assert len(b.reminders) == 0

    b._reminder_command("amod", "moderator/1", "60mins Check the lights")
    assert len(said) == 1 and "reminder #1 set" in said[0], said
    assert "tomorrow" not in said[0] and "today" in said[0], said[0]
    assert not said[0].endswith('"'), f"stray quote: {said[0]}"
    assert len(said[0]) <= 500
    print("[PASS] !reminder is silent for viewers and confirms for moderators")


def test_reminder_reaches_chat_on_time():
    b, said = _bot()
    b._reminder_command("amod", "moderator/1", "90s Check the lights are working")
    said.clear()
    b.reminders._items[0].due = time.time() - 1
    for item in b.reminders.pop_due():
        b._fire_reminder(item)
    assert len(said) == 1, said
    assert said[0].startswith("Reminder | Check the lights are working"), said[0]
    assert "(set by @amod)" in said[0], said[0]
    print("[PASS] a due reminder posts to chat with who set it")


def test_reminders_are_held_while_the_bot_is_off():
    """Pausing the bot is not cancelling its reminders."""
    b, said = _bot()
    b._reminder_command("amod", "moderator/1", "90s still pending")
    b.reminders._items[0].due = time.time() - 1
    said.clear()

    b.paused = True
    assert b._tick_reminders() == 0, "a paused bot must not post reminders"
    assert len(b.reminders) == 1, "a paused bot must not drop its reminders"
    assert said == [], said

    b.paused = False
    assert b._tick_reminders() == 1, "the held reminder fires on resume"
    assert len(b.reminders) == 0
    assert said and said[0].startswith("Reminder | still pending"), said
    print("[PASS] reminders are held, not dropped, while the bot is switched off")


def test_haul_is_readable_by_everyone():
    b, said = _bot()
    b._say_haul("viewer1")
    assert "nothing is logged" in said[0], said[0]

    # A viewer's attempt to change it is recognised and refused, silently.
    assert b._haul_mutation("rando", "", "update Stolen goods") is True
    assert not b.cargo.is_set
    assert len(said) == 1, "a viewer must not be answered"

    assert b._haul_mutation("amod", "moderator/1", "update Produce") is True
    b._say_haul("viewer2")
    assert said[-1] == "@viewer2 we are transporting Produce.  " \
                       "(set by @amod, just now)", said[-1]

    b._haul_mutation("amod", "broadcaster/1", "delete")
    b._say_haul("viewer2")
    assert "nothing is logged" in said[-1]
    print("[PASS] !haul is open to read and moderator-only to change")


def test_long_cargo_stays_inside_the_chat_limit():
    b, said = _bot()
    b._haul_mutation("amod", "moderator/1", "update " + "word " * 60)
    b._say_haul("viewer1")
    assert all(len(line) <= 500 for line in said), [len(x) for x in said]
    assert not said[-1].endswith("...") or len(said[-1]) <= 500
    print("[PASS] long entries are trimmed to the chat limit")


def test_legacy_state_file_is_carried_over():
    """The board shipped for one revision as transporting.json. Renaming the
    command must not silently blank what the truck is hauling."""
    here = os.path.dirname(os.path.abspath(haul.__file__))
    legacy = os.path.join(here, "transporting.json")
    current = haul.HAUL_PATH
    backed_up = {}
    for path in (legacy, current):
        if os.path.exists(path):
            backed_up[path] = open(path, encoding="utf-8").read()
            os.remove(path)
    try:
        with open(legacy, "w", encoding="utf-8") as fh:
            fh.write('{"text": "Produce", "set_by": "amod", "set_at": 1.0}')
        carried = haul.Cargo()
        assert carried.text == "Produce" and carried.is_set, carried.text

        # Once haul.json exists it wins, legacy file or not.
        carried.update("Livestock")
        carried.save()
        assert haul.Cargo().text == "Livestock"
    finally:
        for path in (legacy, current):
            if os.path.exists(path):
                os.remove(path)
        for path, text in backed_up.items():
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
    print("[PASS] a transporting.json left by the old name is carried over")


def main():
    print("==== reminder and haul tests ====")
    for fn in (test_parse_delay, test_parse_clock, test_clock_rolls_to_tomorrow,
               test_add_list_cancel, test_pop_due_only_takes_what_is_ready,
               test_pending_cap, test_survives_a_restart,
               test_corrupt_state_does_not_stop_the_bot, test_cargo,
               test_storage_is_atomic_and_lossless,
               test_reminders_are_moderator_only,
               test_reminder_reaches_chat_on_time,
               test_reminders_are_held_while_the_bot_is_off,
               test_haul_is_readable_by_everyone,
               test_long_cargo_stays_inside_the_chat_limit,
               test_legacy_state_file_is_carried_over):
        fn()
    print("ALL PASSED \u2714")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
