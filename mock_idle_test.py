"""Tests for the idle-chat poster: a quiet channel gets something to react to.

Run:  python3 mock_idle_test.py
Covers bot._idle_chat_tick and access.Helix.is_live against a fake Helix. The
entertainment APIs are stubbed, so nothing reaches the network.
"""

import http.server
import json
import threading
import time

import access
import bot as bot_mod
import extras

STATE = {"live": True, "calls": []}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        STATE["calls"].append(self.path)
        if "/helix/streams" in self.path:
            body = {"data": [{"id": "s", "type": "live"}]} if STATE["live"] \
                else {"data": []}
        else:
            body = {"data": []}
        data = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


JOKE = {"type": "general", "setup": "Why did the truck stop?",
        "punchline": "Because it needed a break."}
FACT = {"id": "x", "text": "Honey never spoils.", "language": "en"}
RIDDLE = {"riddle": "What has keys but no locks?", "answer": "A piano."}
WYR = {"question": "Would you rather drive at night or in the rain?"}


def _bot(live=True, **cfg):
    base = dict(bot_mod.DEFAULTS, nick="bot", channel="#test", prefix="!",
                idle_chat_minutes=10, riddle_answer_delay=0)
    base.update(cfg)
    b = bot_mod.TwitchBot(base)
    said = []
    b._say = said.append
    b._access.helix.is_live = lambda bid="": live
    return b, said


T = 1_000_000.0


def test_fires_after_ten_minutes_only():
    b, said = _bot()
    b._last_chat = T
    assert b._idle_chat_tick(T + 9 * 60) is None, "fired early"
    assert said == []
    posted = b._idle_chat_tick(T + 10 * 60)
    assert posted in bot_mod.IDLE_COMMANDS, posted
    assert len(said) >= 1 and said[0], "posted nothing"
    print(f"[PASS] quiet for 10 minutes -> !{posted}: {said[0][:56]}...")


def test_posts_once_per_window_not_every_tick():
    """Resetting the clock is what stops it firing on every 15s pass."""
    b, said = _bot()
    b._last_chat = T
    assert b._idle_chat_tick(T + 30 * 60) is not None
    first = len(said)
    for _ in range(5):
        assert b._idle_chat_tick(T + 30 * 60 + 15) is None
    assert len(said) == first, "kept posting into the same silence"
    # ...and another full window later it goes again.
    assert b._idle_chat_tick(T + 40 * 60) is not None
    print("[PASS] one post per idle window, not one per tick")


def test_offline_channel_is_left_alone():
    """A bot posting jokes into an offline room every ten minutes is not a
    feature - it is a channel that looks broken when the streamer returns."""
    b, said = _bot(live=False)
    b._last_chat = T
    for offset in (10, 20, 30, 600):
        assert b._idle_chat_tick(T + offset * 60) is None
    assert said == [], said
    # Once the stream comes up, it works again.
    b._access.helix.is_live = lambda bid="": True
    b._last_chat = T
    assert b._idle_chat_tick(T + 10 * 60) is not None
    print("[PASS] silent while the channel is offline, live again when it is not")


def test_unsettled_live_check_still_posts():
    """Never switch the feature off on a guess. No token, no broadcaster_id or
    a network failure all mean 'unknown', and unknown must not mean 'off'."""
    b, said = _bot()
    b._access.helix.is_live = lambda bid="": None
    b._last_chat = T
    assert b._idle_chat_tick(T + 30 * 60) is not None
    assert said, "the feature silently stopped because a check could not run"
    print("[PASS] an unsettled live check posts anyway rather than going quiet")


def test_respects_the_switches():
    b, said = _bot()
    b._last_chat = T
    b.paused = True
    assert b._idle_chat_tick(T + 60 * 60) is None, "posted while !bot off"
    b.paused = False
    assert b._idle_chat_tick(T + 60 * 60) is not None

    for key, value in (("idle_chat_enabled", False), ("fun_commands", False)):
        b, said = _bot()
        b.cfg[key] = value
        b._last_chat = T
        assert b._idle_chat_tick(T + 60 * 60) is None, f"{key}={value} ignored"
    print("[PASS] honours !bot off, idle_chat_enabled and fun_commands")


def test_any_chat_resets_the_clock():
    b, said = _bot()
    b._last_chat = T
    b._on_message("someone", "#test", "hello everyone", "", "")
    assert b._last_chat > T, "plain chat did not count"
    # Not just commands, and not just messages the bot acts on.
    assert b._idle_chat_tick(b._last_chat + 9 * 60) is None
    assert b._idle_chat_tick(b._last_chat + 10 * 60) is not None
    print("[PASS] anything anyone says resets the clock, not just commands")


def test_all_five_are_in_the_pool():
    seen = {}
    for _ in range(600):
        b, said = _bot()
        b._last_chat = T
        command = b._idle_chat_tick(T + 30 * 60)
        seen[command] = seen.get(command, 0) + 1
        assert said, f"!{command} posted nothing"
    assert set(seen) == set(bot_mod.IDLE_COMMANDS), seen
    assert min(seen.values()) > 30, f"one command is being starved: {seen}"
    print(f"[PASS] all five get their turn: {seen}")


def test_pool_is_configurable():
    b, said = _bot(idle_chat_commands=["joke", "wyr"])
    for _ in range(40):
        b._last_chat = T
        assert b._idle_chat_tick(T + 30 * 60) in ("joke", "wyr")
    # Junk in the list is ignored rather than crashing the keeper thread.
    b, said = _bot(idle_chat_commands=["joke", "funfact", "nonsense"])
    b._last_chat = T
    assert b._idle_chat_tick(T + 30 * 60) == "joke"
    b, said = _bot(idle_chat_commands=[])
    b._last_chat = T
    assert b._idle_chat_tick(T + 30 * 60) is None, "an empty pool should idle"
    print("[PASS] idle_chat_commands narrows the pool and ignores junk")


def test_riddle_still_reveals_its_answer():
    b, said = _bot(idle_chat_commands=["riddle"])
    b._last_chat = T
    assert b._idle_chat_tick(T + 30 * 60) == "riddle"
    assert any(l.startswith("Riddle | ") for l in said), said
    # The answer timer is armed even for an auto-post.
    time.sleep(0.2)
    assert any(l.startswith("Answer | ") for l in said), said
    print("[PASS] an auto-posted riddle still reveals its answer")


def test_is_live_against_a_fake_helix(port):
    h = access.Helix("cid", "token", "12345")
    STATE["live"] = True
    assert h.is_live() is True
    STATE["live"] = False
    assert h.is_live() is False
    # Without the pieces it needs, the answer is 'unknown', never 'offline' -
    # the caller must not read a missing config as a dead channel.
    assert access.Helix("", "token", "12345").is_live() is None
    assert access.Helix("cid", "", "12345").is_live() is None
    assert access.Helix("cid", "token", "").is_live() is None
    assert any("/helix/streams" in c for c in STATE["calls"])
    print("[PASS] is_live: live, offline, and honestly unknown")


def main():
    extras._get_json = lambda url, timeout=8: (
        RIDDLE if "riddle" in url else WYR if "either" in url
        else FACT if "fact" in url else JOKE)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    access.HELIX = f"http://127.0.0.1:{port}"

    print("==== idle-chat tests ====")
    for fn in (test_fires_after_ten_minutes_only,
               test_posts_once_per_window_not_every_tick,
               test_offline_channel_is_left_alone,
               test_unsettled_live_check_still_posts,
               test_respects_the_switches,
               test_any_chat_resets_the_clock,
               test_all_five_are_in_the_pool,
               test_pool_is_configurable,
               test_riddle_still_reveals_its_answer):
        fn()
    test_is_live_against_a_fake_helix(port)
    server.shutdown()
    print("ALL PASSED \u2714")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
