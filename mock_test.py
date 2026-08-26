"""End-to-end test for bot.py against a fake IRC server (no Twitch needed).

Run:  python3 mock_test.py
It boots a tiny IRC server on 127.0.0.1:6667, starts the bot against it,
sends a few chat messages, and prints everything the bot said back.
"""

import socket
import subprocess
import sys
import threading
import time

import bot as bot_mod
import extras
from bot import DEFAULTS, TwitchBot  # noqa: E402

# Patch the network-backed functions so the offline test is deterministic.
extras.get_joke = lambda: "Why did the truck stop? Because it needed a break."
extras.get_random_fact = lambda: "Honey never spoils."
bot_mod.get_funfact = lambda location, options=None: {
    "place": f"{location.title()}",
    "fact": f"A completely offline fact about {location}.",
}

HOST, PORT = "127.0.0.1", 6667

SCRIPT = [
    (2.0, ":viewer1!viewer1@viewer1.tmi.twitch.tv PRIVMSG #test :!funfact Milford, PA"),
    (4.0, ":viewer2!viewer2@viewer2.tmi.twitch.tv PRIVMSG #test :!funfact"),
    (5.0, "PING :tmi.twitch.tv"),
    (6.0, ":viewer2!viewer2@viewer2.tmi.twitch.tv PRIVMSG #test :!help"),
    (9.0, ":viewer3!viewer3@viewer3.tmi.twitch.tv PRIVMSG #test :!funfact Breezewood, PA"),
    (10.5, ":viewer4!viewer4@viewer4.tmi.twitch.tv PRIVMSG #test :!joke"),
    (12.0, ":viewer4!viewer4@viewer4.tmi.twitch.tv PRIVMSG #test :!randomfact"),
]


def server(bot_lines):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(1)
    conn, _ = srv.accept()
    conn.settimeout(1.0)
    conn.sendall(b":tmi.twitch.tv 001 funfactbot :Welcome, GLHF!\r\n")
    buf = b""
    start = time.time()
    next_idx = 0
    while time.time() - start < 16:
        try:
            data = conn.recv(4096)
        except socket.timeout:
            data = b""
        if data:
            buf += data
            while b"\r\n" in buf:
                line, buf = buf.split(b"\r\n", 1)
                text = line.decode("utf-8", "replace")
                bot_lines.append(text)
                print(f"  bot -> {text}")
        now = time.time() - start
        if next_idx < len(SCRIPT) and now >= SCRIPT[next_idx][0]:
            msg = SCRIPT[next_idx][1]
            next_idx += 1
            print(f"  srv -> {msg}")
            conn.sendall((msg + "\r\n").encode("utf-8"))
        time.sleep(0.05)
    conn.close()
    srv.close()


def main():
    cfg = {
        **DEFAULTS,
        "nick": "funfactbot",
        "oauth_token": "oauth:testtoken",
        "channel": "#test",
        "cooldown_seconds": 1,
        "host": HOST,
        "port": PORT,
        "use_tls": False,
    }
    bot = TwitchBot(cfg)
    bot_lines = []
    t = threading.Thread(target=server, args=(bot_lines,), daemon=True)
    t.start()
    time.sleep(0.4)
    runner = threading.Thread(target=bot.run, daemon=True)
    runner.start()
    time.sleep(14)
    bot.running = False
    bot._close()
    runner.join(timeout=5)

    print("\n==== Bot's outgoing lines ====")
    for line in bot_lines:
        print("  ", line)

    facts = [l for l in bot_lines if "PRIVMSG" in l and "FunFact" in l]
    usage = [l for l in bot_lines if "PRIVMSG" in l and "usage" in l]
    jokes = [l for l in bot_lines if "PRIVMSG" in l and "Joke |" in l]
    rf = [l for l in bot_lines if "PRIVMSG" in l and "RandomFact |" in l]
    pongs = [l for l in bot_lines if l.startswith("PONG")]
    print(f"\nfacts: {len(facts)}, usage replies: {len(usage)}, jokes: {len(jokes)}, "
          f"randomfacts: {len(rf)}, pongs: {len(pongs)}")
    if facts and usage and jokes and rf and pongs:
        print("END-TO-END TEST PASSED ✔")
        return 0
    print("TEST FAILED ✘")
    return 1


if __name__ == "__main__":
    sys.exit(main())
