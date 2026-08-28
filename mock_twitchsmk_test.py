"""Tests for twitchnames.py and the !smk twitch wiring.

Run:  python3 mock_twitchsmk_test.py

No network. Every test passes its own fake Helix and its own temp state file.

The thing these tests guard hardest is the "never invent a name" rule: there
is no seeded list of famous streamers in this feature, so a dead API has to
produce an honest "I could not get names" and nothing else.
"""

import os
import tempfile
import threading

import bot as bot_mod
import extras
import twitchnames


def _tmp(name):
    return os.path.join(tempfile.mkdtemp(prefix="clawfacts-tw-"), name)


class _FakeHelix:
    """Stands in for access.Helix. None from a read means 'could not look'."""

    def __init__(self, streams=None, followers=None):
        self.streams = streams
        self.followers = followers
        self.calls = []

    def top_streams(self, first=100):
        self.calls.append(("streams", first))
        return self.streams

    def channel_followers(self, broadcaster_id="", pages=3, per_page=100):
        self.calls.append(("followers", broadcaster_id, pages))
        return self.followers


class _Allow:
    allowed, reason, tier, wait = True, "", "moderator", 0.0


STREAMS = [
    ("KaiCenat", "Just Chatting", 118000),
    ("Jinnytty", "League of Legends", 24000),
    ("Nmplol", "", 9000),
    ("Asmongold", "World of Warcraft", 41000),
    ("pokimane", "", 0),
]
FOLLOWERS = [
    ("OldTimer", "2019-05-02T11:00:00Z"),
    ("NewHere", "2025-11-30T08:15:00Z"),
]


def _pool(**kw):
    return twitchnames.TwitchNamePool(path=_tmp("twitch_names.json"), **kw)


def _bot(helix=None, **cfg):
    base = dict(bot_mod.DEFAULTS, nick="bot", channel="#test", prefix="!",
                cooldown_seconds=0, smk_twitch_enabled=True,
                smk_twitch_followers=False, smk_twitch_refresh_minutes=30)
    base.update(cfg)
    b = bot_mod.TwitchBot(base)
    said, logs = [], []
    b._say = said.append
    b._log = logs.append
    b._twitch_pool = _pool()
    b._resolve_broadcaster = lambda ch: None
    b._access.helix = helix
    b._access.check = lambda login, badges: _Allow()
    b._access.commit = lambda login: None
    b._note_denial = lambda *a, **k: None
    return b, said, logs


def _run_worker(b):
    threading.Thread(target=b._worker, daemon=True).start()
    b._jobs.join()


# --- labels: every descriptor must come from real response fields ---------

def test_since_label_reads_the_timestamp():
    assert twitchnames.since_label("2024-03-11T07:22:31Z") == \
        "followed since Mar 2024"
    assert twitchnames.since_label("2019-01-01T00:00:00Z") == \
        "followed since Jan 2019"
    for bad in ("", None, "not a date", "2024-13-01T00:00:00Z"):
        assert twitchnames.since_label(bad) == "", bad
    # Twitch launched in 2011, so an earlier stamp is a bad parse, not a fact.
    assert twitchnames.since_label("1998-06-01T00:00:00Z") == ""
    print("[PASS] 'followed since Mar 2024' comes from followed_at, or nothing")


def test_streamer_label_prefers_the_game():
    assert twitchnames._streamer_label("Just Chatting", 118000) == \
        "streaming Just Chatting"
    # No category set, but the viewer count is in the same response.
    assert twitchnames._streamer_label("", 9000) == "live with 9,000 watching"
    assert twitchnames._streamer_label("", 0) == "live now"
    assert twitchnames._streamer_label("   ", 0) == "live now"
    print("[PASS] a streamer is described by their game, then by viewer count")


# --- refresh --------------------------------------------------------------

def test_refresh_builds_the_pool_from_the_api():
    p = _pool()
    got = p.refresh(_FakeHelix(streams=STREAMS), "12345")
    assert got["streamers"] == 5 and got["failed"] == [], got
    assert got["followers"] == 0, got          # followers were not requested
    assert p.counts() == {"streamers": 5, "followers": 0, "total": 5}
    assert "KaiCenat" in p.names()
    assert p.stale() is False
    print("[PASS] a refresh fills the pool from Get Streams")


def test_followers_are_only_pulled_when_asked():
    p = _pool()
    helix = _FakeHelix(streams=STREAMS, followers=FOLLOWERS)
    p.refresh(helix, "12345", include_followers=False)
    assert not any(c[0] == "followers" for c in helix.calls), helix.calls
    assert p.counts()["followers"] == 0

    p2 = _pool()
    helix2 = _FakeHelix(streams=STREAMS, followers=FOLLOWERS)
    got = p2.refresh(helix2, "12345", include_followers=True)
    assert got["followers"] == 2, got
    assert p2.counts()["followers"] == 2, p2.counts()
    assert p2.names().count("OldTimer") == 1
    # The descriptor is the real follow date, not the word "follower".
    row = [r for r in p2._available() if r[0] == "OldTimer"][0]
    assert row[1] == "followed since May 2019", row
    print("[PASS] the follower list is only fetched when it is switched on")


def test_a_failed_refresh_does_not_empty_a_working_pool():
    p = _pool()
    p.refresh(_FakeHelix(streams=STREAMS), "12345")
    before = p.counts()["total"]
    got = p.refresh(_FakeHelix(streams=None), "12345")
    assert got["streamers"] == 0 and got["failed"] == \
        ["the top-streams lookup"], got
    assert p.counts()["total"] == before, p.counts()
    assert len(p.draw(3)) == 3
    print("[PASS] an API failure keeps the last good pool instead of wiping it")


def test_partial_success_is_kept():
    p = _pool()
    got = p.refresh(_FakeHelix(streams=STREAMS, followers=None), "12345",
                    include_followers=True)
    assert got["streamers"] == 5 and got["followers"] == 0, got
    assert got["failed"] == ["the follower list"], got
    assert len(p.draw(3)) == 3          # the game still runs
    print("[PASS] losing the follower list does not cost the game")


def test_a_dead_api_never_produces_invented_names():
    # The point of the whole feature: there is no seeded roster to fall back
    # on, so a dead API has to be honest about it.
    p = _pool()
    got = p.refresh(_FakeHelix(streams=None, followers=None), "12345",
                    include_followers=True)
    assert got["failed"] == ["the top-streams lookup", "the follower list"]
    assert p.draw(3) == [], p.names()

    b, said, logs = _bot(helix=_FakeHelix(streams=None))
    b._reply_smk_twitch("viewer19")
    assert said and "could not get three Twitch names" in said[0], said
    assert "(0 cached)" in said[0], said
    # Nothing that looks like a streamer name made it into chat.
    for famous in ("Ninja", "Pokimane", "xQc", "Shroud", "KaiCenat"):
        assert famous.lower() not in said[0].lower(), said[0]
    print("[PASS] with no API answer the game says so and names nobody")


def test_there_is_no_seeded_streamer_list():
    # Structural: a hardcoded roster is exactly the finite local pool that was
    # rejected for these games. Only MONTHS may be a module-level list.
    lists = [n for n in dir(twitchnames)
             if n.isupper() and isinstance(getattr(twitchnames, n),
                                           (list, tuple))]
    assert lists == ["MONTHS"], lists
    print("[PASS] no hardcoded streamer roster - the pool is API-fed only")


def test_a_streamer_who_also_follows_appears_once():
    p = _pool()
    streams = STREAMS + [("OldTimer", "Just Chatting", 5)]
    p.refresh(_FakeHelix(streams=streams, followers=FOLLOWERS), "12345",
              include_followers=True)
    assert p.names().count("OldTimer") == 1, p.names()
    print("[PASS] someone in both pools is not nominated twice in one game")


def test_the_pool_survives_a_restart():
    path = _tmp("twitch_names.json")
    p = twitchnames.TwitchNamePool(path=path)
    p.refresh(_FakeHelix(streams=STREAMS), "12345")
    again = twitchnames.TwitchNamePool(path=path)
    assert again.counts()["total"] == 5, again.counts()
    assert again.stale() is False        # the timestamp came back too
    print("[PASS] the cached names and their timestamp persist")


def test_a_corrupt_file_is_ignored():
    path = _tmp("twitch_names.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{broken")
    p = twitchnames.TwitchNamePool(path=path)
    assert p.counts()["total"] == 0
    assert p.stale() is True             # so it refills rather than stays dead
    p.refresh(_FakeHelix(streams=STREAMS), "12345")
    assert p.counts()["total"] == 5
    print("[PASS] an unreadable cache file is ignored and refilled")


def test_stale_and_the_refresh_window():
    p = _pool(refresh_seconds=1800)
    assert p.stale() is True, "an empty pool must always refill"
    p.refresh(_FakeHelix(streams=STREAMS), "12345")
    stamp = p.updated
    assert p.stale(now=stamp + 1799) is False
    assert p.stale(now=stamp + 1800) is True
    print("[PASS] the pool is refetched after the window, not on every command")


def test_rounds_do_not_repeat_the_same_people():
    p = _pool()
    p.refresh(_FakeHelix(streams=STREAMS), "12345")
    seen = set()
    for _ in range(12):
        for name, _label in p.draw(3):
            seen.add(name)
    assert len(seen) == 5, seen          # the whole pool gets used, not three
    print("[PASS] every name in the pool gets its turn")


def test_fewer_than_three_names_is_not_a_round():
    p = _pool()
    p.refresh(_FakeHelix(streams=STREAMS[:2]), "12345")
    assert p.counts()["total"] == 2
    assert p.draw(3) == [], "two names is not a shag/marry/kill"
    print("[PASS] two names is not passed off as a three-name round")


# --- the chat surface -----------------------------------------------------

def test_smk_twitch_answers_through_the_real_gate():
    b, said, logs = _bot(helix=_FakeHelix(streams=STREAMS))
    b._on_message("viewer19", "#test", "!smk twitch", login="viewer19",
                  badges="")
    assert b._jobs.qsize() == 1, "the gate dropped !smk twitch"
    _run_worker(b)
    assert len(said) == 1, said
    line = said[0]
    assert line.startswith("ShagMarryKill [twitch] | "), line
    assert "shag one, marry one, kill one." in line, line
    # Every name carries a real descriptor, as the celebrity round does.
    body = line.split("|", 1)[1].split(" - shag one")[0]
    assert body.count("(") == 3 and body.count(")") == 3, body
    assert any("streaming " in part for part in body.split(", ")), body
    # And it came from the fake API, not from the celebrity pool.
    assert any(n in line for n in ("KaiCenat", "Jinnytty", "Nmplol",
                                   "Asmongold", "pokimane")), line
    print(f"[PASS] !smk twitch answers: {line[:96]}...")


def test_the_alias_spellings_all_reach_the_twitch_pool():
    for spelling in ("!smk twitch", "!smk TWITCH", "!smk streamers",
                     "!smk streamer", "!smk tw", "!shagmarrykill twitch"):
        b, said, logs = _bot(helix=_FakeHelix(streams=STREAMS))
        b._on_message("viewer19", "#test", spelling, login="viewer19",
                      badges="")
        _run_worker(b)
        assert said and said[0].startswith("ShagMarryKill [twitch] | "), \
            (spelling, said)
    print("[PASS] twitch / streamers / streamer / tw all mean the Twitch game")


def test_plain_smk_still_uses_the_celebrity_pool():
    for spelling in ("!smk", "!smk any", "!smk female", "!smk male"):
        b, said, logs = _bot(helix=_FakeHelix(streams=STREAMS))
        b._on_message("viewer19", "#test", spelling, login="viewer19",
                      badges="")
        _run_worker(b)
        assert said and said[0].startswith("ShagMarryKill ["), (spelling, said)
        assert "[twitch]" not in said[0], (spelling, said[0])
        assert not any(c[0] == "streams" for c in
                       b._access.helix.calls), (spelling, b._access.helix.calls)
    print("[PASS] !smk and !smk female/male/any are untouched")


def test_followers_are_off_unless_configured():
    helix = _FakeHelix(streams=STREAMS, followers=FOLLOWERS)
    b, said, logs = _bot(helix=helix)
    b._on_message("viewer19", "#test", "!smk twitch", login="viewer19",
                  badges="")
    _run_worker(b)
    assert not any(c[0] == "followers" for c in helix.calls), helix.calls
    for follower in ("OldTimer", "NewHere"):
        assert follower not in said[0], said[0]

    helix2 = _FakeHelix(streams=[], followers=FOLLOWERS)
    b2, said2, _ = _bot(helix=helix2, smk_twitch_followers=True)
    b2._on_message("viewer19", "#test", "!smk twitch", login="viewer19",
                   badges="")
    _run_worker(b2)
    assert any(c[0] == "followers" for c in helix2.calls), helix2.calls
    print("[PASS] the follower pool is opt-in, and off means off")


def test_the_config_switch_says_why_it_is_quiet():
    b, said, logs = _bot(helix=_FakeHelix(streams=STREAMS),
                         smk_twitch_enabled=False)
    b._reply_smk_twitch("viewer19")
    assert "switched off in config.json" in said[0], said[0]
    assert "smk_twitch_enabled" in said[0], said[0]
    print("[PASS] smk_twitch_enabled=false is named as the reason")


def test_help_advertises_it_and_drops_it_when_off():
    b, said, logs = _bot()
    b._say_help("viewer19", "")
    _run_worker(b)
    assert any("!smk twitch" in m for m in said), said
    b2, said2, _ = _bot(smk_twitch_enabled=False)
    b2._say_help("viewer19", "")
    _run_worker(b2)
    assert not any("!smk twitch" in m for m in said2), said2
    print("[PASS] !help lists !smk twitch, and drops it when switched off")


def main():
    tests = [
        test_since_label_reads_the_timestamp,
        test_streamer_label_prefers_the_game,
        test_refresh_builds_the_pool_from_the_api,
        test_followers_are_only_pulled_when_asked,
        test_a_failed_refresh_does_not_empty_a_working_pool,
        test_partial_success_is_kept,
        test_a_dead_api_never_produces_invented_names,
        test_there_is_no_seeded_streamer_list,
        test_a_streamer_who_also_follows_appears_once,
        test_the_pool_survives_a_restart,
        test_a_corrupt_file_is_ignored,
        test_stale_and_the_refresh_window,
        test_rounds_do_not_repeat_the_same_people,
        test_fewer_than_three_names_is_not_a_round,
        test_smk_twitch_answers_through_the_real_gate,
        test_the_alias_spellings_all_reach_the_twitch_pool,
        test_plain_smk_still_uses_the_celebrity_pool,
        test_followers_are_off_unless_configured,
        test_the_config_switch_says_why_it_is_quiet,
        test_help_advertises_it_and_drops_it_when_off,
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
