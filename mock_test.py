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

def privmsg(login, text, badges=""):
    """A PRIVMSG the way Twitch really sends it - with the tags capability on,
    every line carries badges. Access control reads them from there."""
    info = "subscriber/12" if "subscriber" in badges else ""
    tags = f"@badge-info={info};badges={badges};display-name={login};id=x"
    return f"{tags} :{login}!{login}@{login}.tmi.twitch.tv PRIVMSG #test :{text}"


SCRIPT = [
    (2.0, privmsg("viewer1", "!funfact Milford, PA", "moderator/1,subscriber/24")),
    (4.0, privmsg("viewer2", "!funfact", "subscriber/06")),
    (5.0, "PING :tmi.twitch.tv"),
    (6.0, privmsg("viewer2", "!help", "subscriber/06")),
    (9.0, privmsg("viewer3", "!funfact Breezewood, PA", "vip/1")),
    (10.5, privmsg("viewer4", "!joke", "subscriber/01")),
    (12.0, privmsg("viewer5", "!randomfact", "vip/1")),
    # No badges at all, and no way to verify the follow: must be turned away.
    (14.0, privmsg("stranger", "!funfact Girard, OH", "")),
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
    while time.time() - start < 22:
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
    time.sleep(20)   # long enough for the last scripted message (14s) to be answered
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
    # The badge-less viewer must be refused, and must not get a fact.
    turned_away = [l for l in bot_lines if "PRIVMSG" in l and "@stranger" in l]
    stranger_fact = [l for l in facts if "stranger" in l]
    print(f"\nfacts: {len(facts)}, usage replies: {len(usage)}, jokes: {len(jokes)}, "
          f"randomfacts: {len(rf)}, pongs: {len(pongs)}, "
          f"badge-less refused: {len(turned_away)}")
    if not turned_away:
        print("FAIL: a viewer with no badges was not turned away")
        return 1
    if stranger_fact:
        print("FAIL: the badge-less viewer received a fact")
        return 1
    if facts and usage and jokes and rf and pongs:
        print("END-TO-END TEST PASSED ✔")
        return 0
    print("TEST FAILED ✘")
    return 1


if __name__ == "__main__":
    sys.exit(main())
