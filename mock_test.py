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
import lyrics as lyrics_mod
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
# !whois and !whotwitch would reach Wikipedia and api.twitch.tv; answer both
# locally and deterministically. Two functions because they are two commands.
_WIKI = {"title": "Aubrey Plaza", "description": "American actress",
         "text": "Aubrey Christina Plaza (born June 26, 1984) is an American "
                 "actress, comedian, producer, and writer. She gained "
                 "recognition for playing April Ludgate on Parks and "
                 "Recreation."}

_TWITCH = {"id": "1", "login": "hardclaws", "display_name": "Hardclaws",
           "bio": "Truck driver streaming from the cab.",
           "broadcaster_type": "partner",
           "created_at": "2019-03-04T00:00:00Z", "followers": 45231}


def _fake_lookup(q, max_chars=400, **kw):
    """Wikipedia only. It has never heard of a Twitch login."""
    if "plaza" in (q or "").lower():
        return {"found": True, **_WIKI}
    return {"found": False, "reason": f"I couldn't find anyone called {q}"}


def _fake_twitch_lookup(q, helix=None, **kw):
    """Twitch only. It has never heard of an actress."""
    login = (q or "").strip().lstrip("#").lower()
    if login == "hardclaws":
        return {"found": True, "display_name": "Hardclaws",
                "profile": dict(_TWITCH)}
    if not login:
        return {"found": False,
                "reason": "which Twitch name should I look up?"}
    return {"found": False,
            "reason": f"there is no Twitch channel called {login}"}


bot_mod.whois.lookup = _fake_lookup
bot_mod.whois.twitch_lookup = _fake_twitch_lookup

# !ftl would reach lrclib.net. Stub the round, not the HTTP: the point of this
# test is the bot's handling, and the lyric text here is invented placeholder
# text so no real lyric ever lands in the repo.
_FTL_ROUND = {"prompt": "First line of a made up song about nothing",
              "answer": "Second line that follows it quite naturally",
              "artist": "The Nobodies", "title": "Made Up Song",
              "genre": "rock", "year": 1985, "label": "80s rock"}


def _fake_get_round(argument="", **kw):
    """Same label rule as the real get_round, which is what the bot posts."""
    if (argument or "").strip() in ("zzznosuch", "nothing matches this"):
        return None
    filt = lyrics_mod.parse_filter(argument)
    if filt["genre"] and filt["decade"]:
        label = f"{filt['decade']} {filt['genre']}"
    else:
        label = filt["genre"] or filt["decade"] or "any"
    return dict(_FTL_ROUND, label=label)


bot_mod.lyrics_mod.get_round = _fake_get_round

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
    # !funfacts is the same command as !funfact.
    (24.0, privmsg("viewer2", "!funfacts Aliasville, ZZ", "subscriber/06")),
    # A viewer must not be able to switch the bot off - and gets no reply.
    (25.5, privmsg("viewer3", "!bot off", "vip/1")),
    (27.0, privmsg("viewer1", "!bot off", "moderator/1,subscriber/24")),
    # Everything is silent while it is off, moderators included.
    (28.5, privmsg("viewer1", "!funfacts Nowhere, ZZ", "moderator/1")),
    (30.0, privmsg("viewer1", "!bot status", "moderator/1")),
    (31.5, privmsg("viewer1", "!bot on", "moderator/1")),
    (33.0, privmsg("viewer1", "!funfacts Somewhere, ZZ", "moderator/1")),
    # The cargo board: readable by anyone, writable by moderators only.
    (34.5, privmsg("nobody", "!haul", "")),
    (36.0, privmsg("viewer1", "!haul update Produce", "moderator/1")),
    (37.5, privmsg("nobody", "!haul", "")),
    (39.0, privmsg("nobody", "!haul update Stolen goods", "")),
    # A reminder, its list, and the moment it fires. !reminders is an alias.
    (40.5, privmsg("viewer1", "!reminder 10s Check the lights are working",
                   "moderator/1")),
    (42.0, privmsg("viewer1", "!reminders list", "moderator/1")),
    (44.0, privmsg("nobody", "!reminder list", "")),
    (46.0, privmsg("viewer10", "!whois Aubrey Plaza", "subscriber/01")),
    (47.5, privmsg("viewer11", "!whois Zzxqv Notaperson", "subscriber/01")),
    (49.0, privmsg("nobody", "!whois", "")),
    # !whotwitch is its own command: a Twitch login, a miss, and a blank.
    (50.5, privmsg("viewer12", "!whotwitch hardclaws", "subscriber/01")),
    (52.0, privmsg("viewer13", "!whotwitch nosuchchannel", "subscriber/01")),
    (53.5, privmsg("nobody", "!whotwitch", "")),
    # !ftl posts one line now and the next one after ftl_answer_delay.
    (55.0, privmsg("viewer14", "!ftl 80s rock", "subscriber/01")),
    (56.5, privmsg("viewer15", "!ftl zzznosuch", "subscriber/01")),
    (58.0, privmsg("viewer16", "!finishthelyric", "subscriber/01")),
    (59.5, privmsg("viewer17", "!smk any", "vip/1")),
]


def server(bot_lines):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(1)
    conn, _ = srv.accept()
    conn.settimeout(1.0)
    conn.sendall(b":tmi.twitch.tv 001 funfactbot :Welcome, GLHF!\r\n")
    # Twitch sends USERSTATE on join, carrying the bot's OWN badges. It has
    # only three fields, so it also guards the line parser: a parse that
    # demanded four silently dropped every one of these.
    conn.sendall(
        b"@badge-info=;badges=moderator/1;color=;display-name=funfactbot;"
        b"emote-sets=0;mod=1;subscriber=0;user-type=mod "
        b":tmi.twitch.tv USERSTATE #test\r\n")
    buf = b""
    start = time.time()
    next_idx = 0
    while time.time() - start < 66:
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
        "ftl_answer_delay": 3,      # short, so the reveal lands in the run
        "host": HOST,
        "port": PORT,
        "use_tls": False,
    }
    bot = TwitchBot(cfg)
    # Fresh state files. Reminders and the cargo board persist across restarts
    # by design, so a second run would otherwise start with the first run's
    # "Produce" still on the board and the test would not be repeatable.
    import tempfile
    import reminders as reminders_mod
    import haul as haul_mod
    state_dir = tempfile.mkdtemp(prefix="clawfacts-mock-")
    bot.reminders = reminders_mod.ReminderSet(
        os.path.join(state_dir, "reminders.json"))
    bot.cargo = haul_mod.Cargo(
        os.path.join(state_dir, "haul.json"))
    assert not bot.cargo.is_set, "the test must start from an empty board"
    assert len(bot.reminders) == 0, "the test must start with no reminders"
    bot_lines = []
    t = threading.Thread(target=server, args=(bot_lines,), daemon=True)
    t.start()
    time.sleep(0.4)
    runner = threading.Thread(target=bot.run, daemon=True)
    runner.start()
    time.sleep(64)   # long enough for the riddle timer, a 10s reminder, !whois
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
    switches = [l for l in bot_lines if "PRIVMSG" in l and "!bot" in l]
    cargo = [l for l in bot_lines if "PRIVMSG" in l
             and ("transporting" in l or "haul" in l)]
    reminds = [l for l in bot_lines if "PRIVMSG" in l
               and ("Reminder |" in l or "reminder #" in l
                    or "reminders:" in l or "no reminders" in l)]
    whois_lines = [l for l in bot_lines if "PRIVMSG" in l and "WhoIs |" in l]
    whotw_lines = [l for l in bot_lines if "PRIVMSG" in l
                   and "WhoTwitch |" in l]
    ftl_lines = [l for l in bot_lines if "PRIVMSG" in l
                 and ("FinishTheLyric" in l or l.split(":", 2)[-1].strip()
                      .startswith("Answer |"))]
    # The badge-less viewer must be refused, and must not get a fact.
    turned_away = [l for l in bot_lines if "PRIVMSG" in l and "@stranger" in l]
    stranger_fact = [l for l in facts if "stranger" in l]
    # !funfacts must reach the fact engine exactly like !funfact.
    alias_fact = [l for l in facts if "Aliasville" in l]
    if not alias_fact:
        print("FAIL: !funfacts did not produce a fact")
        return 1
    print("[PASS] !funfacts is handled as !funfact")

    # The moderator kill switch: refused for viewers, silent while off, and it
    # must still answer !bot on - otherwise switching off is a one-way trip.
    if any("@viewer3" in l and "!bot" in l for l in bot_lines):
        print("FAIL: a viewer was answered for !bot")
        return 1
    if not any("commands are now OFF" in l for l in bot_lines):
        print("FAIL: !bot off did not confirm to the moderator")
        return 1
    if any("Nowhere" in l for l in bot_lines):
        print("FAIL: a command was answered while the bot was switched off")
        return 1
    if not any("commands are OFF" in l and "status" not in l for l in switches):
        print("FAIL: !bot status did not report OFF")
        return 1
    if not any("commands are back ON" in l for l in bot_lines):
        print("FAIL: !bot on did not resume")
        return 1
    if not any("Somewhere" in l for l in facts):
        print("FAIL: commands did not resume after !bot on")
        return 1
    print(f"[PASS] !bot off/on is moderator-only and silences every command "
          f"({len(switches)} switch line(s))")

    # The cargo board is open to read and closed to write.
    if not any("@nobody nothing is logged" in l for l in cargo):
        print("FAIL: !haul did not answer a badge-less viewer")
        return 1
    if not any("@nobody we are transporting Produce." in l for l in cargo):
        print("FAIL: !haul did not report the load")
        return 1
    if any("@nobody haul updated" in l for l in bot_lines):
        print("FAIL: a viewer was allowed to change the haul board")
        return 1
    print(f"[PASS] !haul is readable by all, writable by mods "
          f"({len(cargo)} line(s))")

    # A reminder is created, listed, and actually reaches chat.
    if not any("reminder #1 set for" in l for l in reminds):
        print("FAIL: !reminder did not confirm")
        return 1
    if not any("reminders: #1" in l for l in reminds):
        print("FAIL: !reminders list did not show the pending reminder")
        return 1
    fired = [l for l in bot_lines if "Reminder | Check the lights are working" in l]
    if not fired:
        print("FAIL: the reminder never posted to chat")
        return 1
    if "(set by @viewer1)" not in fired[0]:
        print(f"FAIL: the reminder did not say who set it: {fired[0]}")
        return 1
    if any(l for l in reminds if "@nobody reminders:" in l or "@nobody no reminders" in l):
        print("FAIL: a viewer was answered for !reminder")
        return 1
    if any(len(l) > 500 for l in bot_lines):
        print("FAIL: a line exceeded Twitch's 500-character limit")
        return 1
    print(f"[PASS] !reminder set / listed / fired, and viewers are not answered "
          f"({len(reminds)} line(s))")

    # !whois answers with the article text, and says so when there is none.
    if not any(l.startswith("PRIVMSG #test :WhoIs | Aubrey Plaza "
                            "(American actress): Aubrey Christina")
               for l in whois_lines):
        print(f"FAIL: !whois did not post the article text: {whois_lines}")
        return 1
    if any("deadpan" in l.lower() for l in bot_lines):
        print("FAIL: an editorial quip reached chat")
        return 1
    if not any("couldn't find anyone called Zzxqv" in l for l in bot_lines):
        print("FAIL: !whois did not report a name it could not find")
        return 1
    if not any("usage: !whois <name>" in l for l in bot_lines):
        print("FAIL: !whois with no argument did not show usage")
        return 1
    if any(len(l) > 500 for l in whois_lines):
        print("FAIL: !whois exceeded Twitch's 500-character limit")
        return 1
    print(f"[PASS] !whois answered, refused a bad name, and showed usage "
          f"({len(whois_lines)} blurb(s))")

    # !whotwitch is separate, and neither command answers for the other.
    if not any(l.startswith("PRIVMSG #test :WhoTwitch | Hardclaws | Twitch "
                            "Partner, 45,231 followers, joined Mar 2019.")
               for l in whotw_lines):
        print(f"FAIL: !whotwitch did not post the channel: {whotw_lines}")
        return 1
    if any("Twitch" in l for l in whois_lines):
        print(f"FAIL: !whois leaked Twitch data: {whois_lines}")
        return 1
    if not any("no Twitch channel called nosuchchannel" in l for l in bot_lines):
        print("FAIL: !whotwitch did not report a login it could not find")
        return 1
    if not any("usage: !whotwitch <twitch name>" in l for l in bot_lines):
        print("FAIL: !whotwitch with no argument did not show usage")
        return 1
    if any(len(l) > 500 for l in whotw_lines):
        print("FAIL: !whotwitch exceeded Twitch's 500-character limit")
        return 1
    print(f"[PASS] !whotwitch answered a channel, refused a miss, showed "
          f"usage ({len(whotw_lines)} profile(s))")

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
        # The round belongs to the whole chat: no "you're up", and no mention
        # of whoever typed the command.
        if "you're up" in line or "you\u2019re up" in line:
            print(f"FAIL: !smk nominated someone: {line}")
            return 1
        for asker in ("viewer7", "viewer8", "viewer9"):
            if asker in line:
                print(f"FAIL: !smk named the person who asked: {line}")
                return 1
        # Each of the three names must say who they are.
        if line.count("(") < 3 or line.count(")") < 3:
            print(f"FAIL: !smk did not give every name an occupation: {line}")
            return 1
    # !ftl quotes one line, reveals the next, and says so when nothing matches.
    prompts = [l for l in ftl_lines if "FinishTheLyric" in l]
    answers = [l for l in ftl_lines if "Answer |" in l]
    if len(prompts) < 2:
        print(f"FAIL: expected a !ftl round and an alias round: {prompts}")
        return 1
    if not any("FinishTheLyric [80s rock] | " in l for l in prompts):
        print(f"FAIL: !ftl did not label the round with its filter: {prompts}")
        return 1
    if not any('"First line of a made up song about nothing"' in l
               for l in prompts):
        print(f"FAIL: !ftl did not post the prompt line: {prompts}")
        return 1
    if not answers:
        print("FAIL: !ftl never revealed the next line")
        return 1
    if not any("The Nobodies - Made Up Song" in l for l in answers):
        print(f"FAIL: the answer did not credit the song: {answers}")
        return 1
    if not any("no songs match 'zzznosuch'" in l for l in bot_lines):
        print("FAIL: !ftl did not say so when nothing matched")
        return 1
    if any(len(l) > 500 for l in ftl_lines):
        print("FAIL: !ftl exceeded Twitch's 500-character limit")
        return 1
    print(f"[PASS] !ftl quoted a line, revealed the next, and refused a "
          f"dead filter ({len(prompts)} prompt(s), {len(answers)} answer(s))")

    # The bot learns whether it is a moderator from USERSTATE, not the API.
    if bot_mod.TwitchBot and not hasattr(bot, "_bot_is_mod"):
        print("FAIL: the bot does not track its own moderator status")
        return 1
    if bot._bot_is_mod is not True:
        print(f"FAIL: USERSTATE was not read (got {bot._bot_is_mod!r})")
        return 1
    print("[PASS] the bot read its own moderator badge from USERSTATE on join")

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
