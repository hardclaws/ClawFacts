"""End-to-end test for bot.py against a fake IRC server (no Twitch needed).

Run:  python3 mock_test.py
It boots a tiny IRC server on 127.0.0.1:6667, starts the bot against it,
sends a few chat messages, and prints everything the bot said back.
"""

import os
import pathlib
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
extras.get_riddle = lambda: ("What has keys but no locks?", "A piano.")
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
    # !riddle must post the question and then reveal the answer on the timer.
    (16.0, privmsg("viewer6", "!riddle", "subscriber/03")),
    # !help is answered inline and is open to everyone, badges or not.
    (18.0, privmsg("nobody", "!help", "")),
    (19.5, privmsg("viewer7", "!smk female", "vip/1")),
    (21.0, privmsg("viewer8", "!smk male", "moderator/1")),
    (22.5, privmsg("viewer9", "!smk", "subscriber/01")),
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
    while time.time() - start < 30:
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


def test_bad_config_is_reported_plainly():
    """A typo in config.json used to print a traceback and then loop forever
    on 'Restart in 5 seconds?'. It must name the line, the cause, and exit with
    code 2 so start-bot.bat stops instead of restarting."""
    import io
    import contextlib
    import tempfile

    good = pathlib.Path(__file__).parent.joinpath("config.example.json")
    lines = good.read_text().splitlines()
    i = next(n for n, l in enumerate(lines) if l.strip().startswith('"_llm_options"'))
    broken = "\n".join(lines[:i]) + '\n  "_llm_options": "x"\n' + "\n".join(lines[i + 1:])
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "config.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(broken)
        err = io.StringIO()
        code = None
        with contextlib.redirect_stderr(err):
            try:
                bot_mod.load_config(path)
            except SystemExit as exc:
                code = exc.code
        text = err.getvalue()
    assert code == 2, f"expected exit code 2, got {code}"
    assert "line 24, column 3" in text, text
    assert "does not end with a comma" in text, text
    print("[PASS] a broken config.json names the line and stops the restart loop")


def main():
    test_bad_config_is_reported_plainly()
    cfg = {
        **DEFAULTS,
        "nick": "funfactbot",
        "oauth_token": "oauth:testtoken",
        "channel": "#test",
        "cooldown_seconds": 1,
        "riddle_answer_delay": 3,   # short, so the test need not wait 20s
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
    time.sleep(28)   # long enough for the riddle timer and the new commands
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
    riddles = [l for l in bot_lines if "PRIVMSG" in l and "Riddle |" in l]
    answers = [l for l in bot_lines if "PRIVMSG" in l and "Answer |" in l]
    helps = [l for l in bot_lines if "PRIVMSG" in l and "commands:" in l]
    smk = [l for l in bot_lines if "PRIVMSG" in l and "ShagMarryKill" in l]
    # The badge-less viewer must be refused, and must not get a fact.
    turned_away = [l for l in bot_lines if "PRIVMSG" in l and "@stranger" in l]
    stranger_fact = [l for l in facts if "stranger" in l]
    print(f"\nfacts: {len(facts)}, usage replies: {len(usage)}, jokes: {len(jokes)}, "
          f"randomfacts: {len(rf)}, pongs: {len(pongs)}, "
          f"badge-less refused: {len(turned_away)}, "
          f"riddles: {len(riddles)}, answers: {len(answers)}")
    if bot_mod.DEFAULTS.get("riddle_answer_delay") != 20:
        print("FAIL: the shipped riddle delay is not 20s")
        return 1
    if not (riddles and answers):
        print("FAIL: !riddle did not post both the question and the answer")
        return 1
    if bot_lines.index(answers[0]) <= bot_lines.index(riddles[0]):
        print("FAIL: the riddle answer was posted before the question")
        return 1
    # !help must answer a viewer with no badges at all.
    if not any("@nobody" in l for l in helps):
        print("FAIL: !help did not answer the badge-less viewer")
        return 1
    if not any("!funfact <place>" in l and "!smk" in l for l in helps):
        print("FAIL: !help did not list the commands")
        return 1
    for want in ("[female]", "[male]", "[any]"):
        if not any(want in l for l in smk):
            print(f"FAIL: no !smk round for {want}")
            return 1
    for line in smk:
        body = line.split("ShagMarryKill", 1)[1]
        if body.count(",") < 2 or "shag one, marry one, kill one" not in body:
            print(f"FAIL: malformed !smk line: {line}")
            return 1
    print(f"[PASS] !help and !smk female/male/any -> "
          f"{len(helps)} help line(s), {len(smk)} round(s)")
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
