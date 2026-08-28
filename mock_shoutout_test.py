"""Tests for the raid shoutout: shoutout.format_raid and bot._on_raid.

Run:  python3 mock_shoutout_test.py

The raid path is driven by a real USERNOTICE line through bot._handle, because
the interesting failures here are in the IRC parse - and USERNOTICE was not
handled at all before, so a raid was invisible to the bot.
"""

import threading
import re

import bot as bot_mod
import shoutout

# A verbatim Twitch raid notice, escaping and all.
RAID_TAGS = ("@badge-info=;badges=;color=;display-name=Trader;emotes=;id=abc;"
             "login=trader;mod=0;msg-id=raid;msg-param-displayName=Trader;"
             "msg-param-login=trader;"
             "msg-param-profileImageURL=https://static-cdn.jtvnw.net/x.png;"
             "msg-param-viewerCount=42;room-id=123;subscriber=0;"
             "system-msg=42\\sraiders\\sfrom\\sTrader\\shave\\sjoined\\n!;"
             "tmi-sent-ts=1;turbo=0;user-id=456;user-type=")
RAID_LINE = RAID_TAGS + " :tmi.twitch.tv USERNOTICE #test"

# A raid from a channel whose display name has capitals and a space, to prove
# the URL is built from the login and not from the name.
FANCY_LINE = ("@msg-id=raid;msg-param-displayName=Big\\sAl;"
              "msg-param-login=big_al;msg-param-viewerCount=7;login=big_al;"
              "display-name=Big\\sAl :tmi.twitch.tv USERNOTICE #test")

MOD = "moderator/1"
BC = "broadcaster/1"
SUB = "subscriber/12"


def _bot(**cfg):
    base = dict(bot_mod.DEFAULTS, nick="bot", channel="#test", prefix="!",
                shoutout_enabled=True)
    base.update(cfg)
    b = bot_mod.TwitchBot(base)
    said = []
    b._say = said.append
    b._access.helix = None          # no network: exercise the floor path
    return b, said


# --- the message ----------------------------------------------------------

def test_a_raid_says_the_name_the_count_and_the_link():
    line = shoutout.format_raid("DaniLikesDonuts", 42, "danilikesdonuts")
    assert "DaniLikesDonuts" in line, line
    assert "42" in line, line
    assert "twitch.tv/danilikesdonuts" in line, line
    assert line.endswith("twitch.tv/danilikesdonuts"), "the link goes last"
    print(f"[PASS] {line}")


def test_one_viewer_is_not_pluralised():
    """'Dropped 1 viewers' was a real output: the unknown-count path used
    replace(' with {count}', ''), which only matched one of five templates."""
    bad = [shoutout.format_raid("Trader", n, "trader")
           for n in (1,) for _ in range(300)]
    assert not any("1 viewers" in line for line in bad), bad[0]
    assert all("Trader" in line for line in bad)
    print("[PASS] one viewer never reads as '1 viewers'")


def test_an_unparseable_count_says_nothing_about_numbers():
    for raw in ("not-a-number", "", None, "abc"):
        for _ in range(100):
            line = shoutout.format_raid("Trader", raw, "trader")
            assert line, raw
            assert not re.search(r"\bwith \d", line), (raw, line)
            assert "not-a-number" not in line, line
    print("[PASS] a missing or junk count falls back to count-free wording")


def test_only_real_twitch_data_is_added():
    prof = {"broadcaster_type": "affiliate", "followers": 1284}
    line = shoutout.format_raid("DaniLikesDonuts", 42, "danilikesdonuts", prof)
    assert "Twitch Affiliate" in line, line
    assert "1,284 followers" in line, line
    one = shoutout.format_raid("Nott", 5, "nott",
                               {"broadcaster_type": "partner", "followers": 1})
    assert "1 follower" in one and "1 followers" not in one, one
    plain = shoutout.format_raid("Xray", 5, "xray",
                                 {"broadcaster_type": "", "followers": None})
    assert "Twitch" not in plain and "followers" not in plain, plain
    print("[PASS] affiliate/partner and follower counts appear only when "
          "Twitch supplied them")


def test_it_never_claims_to_know_what_they_stream():
    """channel_profile does not fetch the game, so nothing may imply it does.
    A made-up 'streaming X' about a real channel is exactly the kind of
    invented claim this bot is not allowed to make."""
    for profile in ({"broadcaster_type": "affiliate", "followers": 10,
                     "game_name": "Euro Truck Simulator 2"},
                    {"game_name": "Minecraft"}, {}, None):
        line = shoutout.format_raid("X", 5, "x", profile)
        assert "streaming" not in line.lower(), line
        assert "Euro Truck" not in line and "Minecraft" not in line, line
    print("[PASS] no line claims to know what the channel is streaming")


def test_the_url_comes_from_the_login_not_the_display_name():
    assert shoutout.url_for("Big Al") == ""      # a space is not a login
    assert shoutout.url_for("@Hardclaws") == "twitch.tv/hardclaws"
    assert shoutout.url_for("#trucker") == "twitch.tv/trucker"
    assert shoutout.url_for("ab") == ""           # too short to be a channel
    assert shoutout.url_for("some channel!") == ""
    line = shoutout.format_raid("Big Al", 7, "big_al")
    assert "twitch.tv/big_al" in line, line
    # With only a display name that cannot be a login, say nothing at all
    # rather than post twitch.tv/big-al into chat.
    assert shoutout.format_raid("Big Al", 7, "") == ""
    print("[PASS] the link is built from a real login, or not posted at all")


def test_it_stays_silent_rather_than_saying_nothing_true():
    assert shoutout.format_raid("", 5, "") == ""
    assert shoutout.format_raid("", 5, "someone") == ""
    # A plain display name that IS a valid login may stand in for one.
    assert "twitch.tv/someone" in shoutout.format_raid("Someone", 5, "")
    assert len(shoutout.format_raid("DaniLikesDonuts", 42, "danilikesdonuts"))\
        < 450
    print("[PASS] no name means no message; every line fits one IRC message")


# --- the raid notice ------------------------------------------------------

def test_a_raid_notice_produces_a_shoutout():
    b, said = _bot()
    b._handle(RAID_LINE)
    # the raid is queued, then drained by the worker
    nick, login, badges, command, argument = b._jobs.get()
    assert command == "raid", command
    assert nick == "Trader", nick
    assert login == "trader", login
    assert argument == "42", argument
    b._say_shoutout(nick, login, argument)
    assert len(said) == 1 and said[0].startswith("SO | "), said
    assert "Trader" in said[0] and "twitch.tv/trader" in said[0], said
    print(f"[PASS] {said[0]}")


def test_escaped_tags_are_unescaped():
    assert bot_mod.TwitchBot._unescape_tag("Big\\sAl") == "Big Al"
    assert bot_mod.TwitchBot._unescape_tag("") == ""
    b, said = _bot()
    b._handle(FANCY_LINE)
    nick, login, badges, command, argument = b._jobs.get()
    assert nick == "Big Al", nick
    assert login == "big_al", login
    b._say_shoutout(nick, login, argument)
    assert "twitch.tv/big_al" in said[0], said
    assert "Big Al" in said[0], said
    print("[PASS] 'Big\\sAl' arrives as 'Big Al' and links to big_al")


def test_a_raid_bypasses_the_access_gate():
    """The raider is usually not a follower and carries no badges here. Put
    through the normal gate the shoutout would be refused - the one thing that
    should always be answered."""
    import inspect
    src = inspect.getsource(bot_mod.TwitchBot._worker)
    assert src.index('command == "raid"') < src.index("_access.check"), \
        "the raid must be handled before the access check"
    print("[PASS] a raid is answered without a follower check")


def test_the_switch_is_moderator_only():
    b, said = _bot()
    assert b._so_switch("viewer19", SUB, "off") is True
    assert said == [], f"a viewer must get no answer: {said}"
    assert b._so_off is False
    assert b._so_switch("amod", MOD, "off") is True
    assert b._so_off is True
    assert any("OFF" in m for m in said), said
    print("[PASS] !so off works for a moderator and is silent for a viewer")


def test_off_actually_stops_the_raid_shoutout():
    b, said = _bot()
    b._so_off = True
    b._handle(RAID_LINE)
    assert b._jobs.empty(), "an off switch must not even queue the raid"
    assert said == []
    b._so_off = False
    b._handle(RAID_LINE)
    assert not b._jobs.empty(), "back on, it must queue again"
    print("[PASS] !so off stops raids being announced, !so on resumes them")


def test_config_can_switch_it_off_entirely():
    b, said = _bot(shoutout_enabled=False)
    b._handle(RAID_LINE)
    assert b._jobs.empty()
    assert said == []
    # and !so on says so rather than promising something false
    said.clear()
    assert b._so_switch("amod", MOD, "on") is True
    assert "shoutout_enabled" in said[0], said
    assert "back ON" not in said[0], said
    print("[PASS] shoutout_enabled=false disables it and is named as the "
          "reason")


def test_status_reports_the_real_state():
    b, said = _bot()
    b._so_switch("amod", MOD, "status")
    assert "ON" in said[0], said
    b._so_switch("amod", MOD, "off")
    b._so_switch("amod", MOD, "status")
    assert "OFF" in said[-1], said
    print("[PASS] !so status reports the state it is actually in")


def test_so_with_a_name_is_moderator_only():
    b, said = _bot()
    b._reply_so("viewer19", SUB, "hardclaws")
    assert said == [], f"a viewer must get no answer: {said}"
    b._reply_so("amod", MOD, "hardclaws")
    assert len(said) == 1 and said[0].startswith("SO | "), said
    assert "twitch.tv/hardclaws" in said[0], said
    print(f"[PASS] !so <name> answers a moderator only: {said[0][:58]}...")


def test_so_with_no_name_shows_usage():
    b, said = _bot()
    b._reply_so("amod", MOD, "")
    assert said and "usage: !so <twitch name>" in said[0], said
    print("[PASS] a bare !so shows usage instead of guessing")


def test_help_mentions_it():
    b, said = _bot()
    b._say_help("viewer19")
    _drain(b)
    assert any("!so <name>" in m for m in said), said
    b, said = _bot(shoutout_enabled=False)
    b._say_help("viewer19")
    _drain(b)
    assert not any("!so" in m for m in said), said
    print("[PASS] !help advertises !so, and drops it when switched off")


def _drain(b):
    """Collect what the bot queued.

    !help now queues its lines instead of saying them inline, so the reader
    thread is never left sleeping inside _say's pacing gap. Run the real
    worker to collect them.
    """
    threading.Thread(target=b._worker, daemon=True).start()
    b._jobs.join()


def main():
    tests = [
        test_a_raid_says_the_name_the_count_and_the_link,
        test_one_viewer_is_not_pluralised,
        test_an_unparseable_count_says_nothing_about_numbers,
        test_only_real_twitch_data_is_added,
        test_it_never_claims_to_know_what_they_stream,
        test_the_url_comes_from_the_login_not_the_display_name,
        test_it_stays_silent_rather_than_saying_nothing_true,
        test_a_raid_notice_produces_a_shoutout,
        test_escaped_tags_are_unescaped,
        test_a_raid_bypasses_the_access_gate,
        test_the_switch_is_moderator_only,
        test_off_actually_stops_the_raid_shoutout,
        test_config_can_switch_it_off_entirely,
        test_status_reports_the_real_state,
        test_so_with_a_name_is_moderator_only,
        test_so_with_no_name_shows_usage,
        test_help_mentions_it,
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
