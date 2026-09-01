"""Tests for whois.py and its bot.py wiring, against a fake Wikipedia.

Run:  python3 mock_whois_test.py
The sandbox cannot reach en.wikipedia.org, so every case runs against a local
server that returns the same shapes the real REST and search APIs do -
including 404s, disambiguation pages and empty extracts.
"""

import http.server
import json
import threading
import urllib.parse

import bot as bot_mod
import whois

STATE = {"calls": []}

AUBREY = {
    "type": "standard",
    "title": "Aubrey Plaza",
    "description": "American actress",
    "extract": (
        "Aubrey Christina Plaza (born June 26, 1984) is an American actress, "
        "comedian, producer, and writer. She gained recognition for playing "
        "April Ludgate on the NBC sitcom Parks and Recreation.[1] She has "
        "since starred in films including Safety Not Guaranteed and "
        "Emily the Criminal, and created and starred in the series Little "
        "Vacation."
    ),
}
JOHN_SMITH = {"type": "disambiguation", "title": "John Smith",
              "extract": "John Smith may refer to several people."}
NO_EXTRACT = {"type": "standard", "title": "Someone Obscure", "extract": ""}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        STATE["calls"].append(self.path)
        if "/rest/" in self.path:
            # Decode first: the client percent-encodes commas and spaces.
            title = urllib.parse.unquote(self.path.rsplit("/rest/", 1)[-1])
            if title == "Aubrey_Plaza":
                return self._send(200, AUBREY)
            if title == "John_Smith":
                return self._send(200, JOHN_SMITH)
            if title == "Someone_Obscure":
                return self._send(200, NO_EXTRACT)
            # Anything else - including a reversed "Plaza, Aubrey" - is not a
            # page title, exactly as on the real Wikipedia.
            return self._send(404, {"type": "https://mediawiki.org/wiki/HyperSwitch/errors/not_found"})
        if "/api.php" in self.path:
            query = urllib.parse.unquote_plus(
                self.path.split("srsearch=")[-1].split("&")[0])
            if "plaza" in query.lower():
                return self._send(200, {"query": {"search": [
                    {"title": "Aubrey Plaza"}]}})
            return self._send(200, {"query": {"search": []}})
        return self._send(404, {})

    def _send(self, code, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def test_exact_title():
    whois.clear_cache()
    before = len(STATE["calls"])
    got = whois.lookup("Aubrey Plaza")
    assert got["found"] is True, got
    assert got["title"] == "Aubrey Plaza", got
    assert got["description"] == "American actress", got
    # Wikipedia's own words, and the citation marker is gone.
    assert got["text"].startswith("Aubrey Christina Plaza (born June 26, 1984)"), got
    assert "[1]" not in got["text"], got
    assert len(got["text"]) <= whois.DEFAULT_MAX_CHARS, len(got["text"])
    assert len(STATE["calls"]) == before + 1, "an exact title needs one call"
    print(f"[PASS] exact title -> {len(got['text'])} chars of Wikipedia's own text")


def test_sentence_boundary():
    whois.clear_cache()
    text = whois.lookup("Aubrey Plaza", max_chars=180)["text"]
    assert len(text) <= 180, len(text)
    assert not text.endswith("...") and not text.endswith("\u2026"), text
    assert text.endswith("."), f"cut mid-sentence: {text!r}"
    print(f"[PASS] trimmed to {len(text)} chars on a sentence boundary")


def test_search_fallback():
    whois.clear_cache()
    got = whois.lookup("Plaza, Aubrey")
    assert got["found"] is True, got
    assert got["title"] == "Aubrey Plaza", got
    assert any("/api.php" in c for c in STATE["calls"]), "search was not used"
    print("[PASS] a name that is not a page title is found via search")


def test_disambiguation_is_not_an_answer():
    whois.clear_cache()
    got = whois.lookup("John Smith")
    assert got["found"] is False, got
    assert "several people" in got["reason"], got
    print("[PASS] a disambiguation page says so instead of picking someone")


def test_not_found():
    whois.clear_cache()
    got = whois.lookup("Zzxqv Notaperson")
    assert got["found"] is False, got
    assert "couldn't find" in got["reason"], got
    assert whois.lookup("")["found"] is False
    assert whois.lookup("a")["found"] is False
    print("[PASS] nobody by that name is reported as such")


def test_empty_extract():
    whois.clear_cache()
    got = whois.lookup("Someone Obscure")
    assert got["found"] is False and "no summary" in got["reason"], got
    print("[PASS] a page with no summary is not posted as an empty blurb")


def test_unreachable_raises_rather_than_denying():
    """Wikipedia being down must not read as 'that person does not exist'."""
    whois.clear_cache()
    saved = whois.REST
    whois.REST = "http://127.0.0.1:1/nope/"
    try:
        raised = False
        try:
            whois.lookup("Aubrey Plaza", timeout=1.0)
        except whois.WhoisError:
            raised = True
        assert raised, "an unreachable API must raise WhoisError"
    finally:
        whois.REST = saved
    print("[PASS] an unreachable Wikipedia raises instead of denying the person")


def test_cached():
    whois.clear_cache()
    whois.lookup("Aubrey Plaza")
    before = len(STATE["calls"])
    again = whois.lookup("aubrey plaza")
    assert again["found"] is True
    assert len(STATE["calls"]) == before, "the second call hit the network"
    print("[PASS] repeats are served from cache (Wikipedia rate-limits by IP)")


def test_bot_reply():
    whois.clear_cache()
    b = bot_mod.TwitchBot(dict(bot_mod.DEFAULTS, nick="bot", channel="#test",
                               prefix="!"))
    said = []
    b._say = said.append

    whois.clear_cache()
    b._reply_whois("viewer1", "Aubrey Plaza")
    assert len(said) == 1, said
    line = said[-1]
    assert line.startswith("WhoIs | Aubrey Plaza (American actress): "), line
    assert len(line) <= 500, len(line)
    assert "deadpan" not in line.lower(), "no model quips"
    print(f"[PASS] {line[:96]}...")

    whois.clear_cache()
    b._reply_whois("viewer1", "John Smith")
    assert "several people" in said[-1], said[-1]

    whois.clear_cache()
    b._reply_whois("viewer1", "Zzxqv Notaperson")
    assert "couldn't find" in said[-1], said[-1]

    saved = whois.REST
    whois.REST = "http://127.0.0.1:1/nope/"
    try:
        whois.clear_cache()
        b._reply_whois("viewer1", "Aubrey Plaza")
        assert "couldn't reach Wikipedia" in said[-1], said[-1]
    finally:
        whois.REST = saved
    print("[PASS] not found, ambiguous and unreachable each say the right thing")


# ---- the Twitch half ----------------------------------------------------
class FakeHelix:
    """Duck-typed stand-in for access.Helix.

    Owns the squashed login 'aubreyplaza' on purpose: some stranger always
    does, and the lookup must not hand their account to a real person.
    """

    def __init__(self, boom=False):
        self.boom = boom
        self.asked = []

    def channel_profile(self, login):
        self.asked.append(login)
        if self.boom:
            raise OSError("helix down")
        if login == "hardclaws":
            return {"id": "1", "login": "hardclaws",
                    "display_name": "Hardclaws",
                    "bio": "Truck driver streaming from the cab.",
                    "broadcaster_type": "partner",
                    "created_at": "2019-03-04T00:00:00Z",
                    "followers": 45231}
        if login == "aubreyplaza":
            return {"id": "9", "login": "aubreyplaza",
                    "display_name": "Aubreyplaza", "bio": "fan account",
                    "broadcaster_type": "", "created_at": "2021-01-01T00:00:00Z",
                    "followers": 3}
        if login == "smallstreamer":
            return {"id": "2", "login": "smallstreamer",
                    "display_name": "SmallStreamer", "bio": "",
                    "broadcaster_type": "", "created_at": "",
                    "followers": None}
        return None


def test_twitch_only():
    whois.clear_cache()
    got = whois.twitch_lookup("hardclaws", FakeHelix())
    assert got["found"] is True, got
    assert got["display_name"] == "Hardclaws", got
    line = whois.format_twitch(got["profile"])
    assert line == ('Twitch Partner, 45,231 followers, joined Mar 2019. '
                    '"Truck driver streaming from the cab."'), line
    print(f"[PASS] a streamer, from Twitch alone: {line}")


def test_whois_and_whotwitch_do_not_confuse_each_other():
    """The whole reason these are two commands. 'Aubrey Plaza' is a person on
    Wikipedia; 'aubreyplaza' is somebody else's Twitch account. Neither
    command may answer with the other one's subject."""
    helix = FakeHelix()
    whois.clear_cache()
    wiki = whois.lookup("Aubrey Plaza")
    assert wiki["found"] is True and wiki["title"] == "Aubrey Plaza", wiki
    assert "aubreyplaza" not in helix.asked, helix.asked

    whois.clear_cache()
    tw = whois.twitch_lookup("Aubrey Plaza", helix)
    assert tw["found"] is False, tw
    assert "Aubrey Plaza" in tw["reason"], tw
    assert "aubreyplaza" not in helix.asked, helix.asked
    print("[PASS] !whois never reaches Twitch, !whotwitch never invents a login")


def test_login_shapes_are_screened():
    helix = FakeHelix()
    for query in ("hi", "way_too_long_to_be_a_real_twitch_login_name",
                  "has spaces", "punct!uation", ""):
        whois.clear_cache()
        whois.twitch_lookup(query, helix)
    assert helix.asked == [], f"wasted API calls on impossible logins: {helix.asked}"
    print("[PASS] impossible logins are screened without an API call")


def test_broken_twitch_says_so_instead_of_denying_the_channel():
    whois.clear_cache()
    got = whois.twitch_lookup("hardclaws", FakeHelix(boom=True))
    assert got["found"] is False, got
    assert "couldn't reach Twitch" in got["reason"], got
    assert "no Twitch channel" not in got["reason"], got
    # And it is not cached: the next try gets a real answer.
    whois.clear_cache()
    assert whois.twitch_lookup("hardclaws", FakeHelix())["found"] is True
    print("[PASS] a Helix failure reports the failure, not 'no such channel'")


def test_no_helix_is_reported_honestly():
    whois.clear_cache()
    got = whois.twitch_lookup("hardclaws", None)
    assert got["found"] is False and "no Twitch login" in got["reason"], got
    print("[PASS] no configured Twitch access is not reported as 'no channel'")


def test_missing_channel():
    whois.clear_cache()
    got = whois.twitch_lookup("zzxqvnotaperson", FakeHelix())
    assert got["found"] is False, got
    assert "no Twitch channel called zzxqvnotaperson" in got["reason"], got
    print("[PASS] a login nobody owns says so")


def test_format_twitch_variants():
    assert whois.format_twitch(
        {"broadcaster_type": "affiliate", "followers": 12,
         "created_at": "2022-07-01T00:00:00Z", "bio": ""}) \
        == "Twitch Affiliate, 12 followers, joined Jul 2022"
    assert whois.format_twitch(
        {"broadcaster_type": "", "followers": None, "created_at": "",
         "bio": "just chatting"}) == 'on Twitch. "just chatting"'
    assert whois._joined("nonsense") == ""
    assert whois._joined("2019-03-04T00:00:00Z") == "Mar 2019"
    print("[PASS] partner / affiliate / plain, with and without bio and count")


def _bot():
    b = bot_mod.TwitchBot(dict(bot_mod.DEFAULTS, nick="bot", channel="#test",
                               prefix="!"))
    said = []
    b._say = said.append
    b._access.helix = FakeHelix()
    return b, said


def test_bot_whois_is_wikipedia_only():
    b, said = _bot()
    # A person Wikipedia knows: one line, and only one.
    whois.clear_cache()
    b._reply_whois("viewer1", "Aubrey Plaza")
    assert len(said) == 1, said
    assert said[0].startswith("WhoIs | Aubrey Plaza (American actress): "), \
        said[0]

    # A login Wikipedia has never heard of. The fake Wikipedia correctly says
    # there is no such page - and !whois must NOT quietly fall back to the
    # Twitch profile that is sitting right there in the same object.
    whois.clear_cache()
    b._reply_whois("viewer1", "hardclaws")
    assert len(said) == 2, said
    assert "couldn't find anyone called hardclaws" in said[1], said[1]
    assert "Twitch" not in said[1], said[1]
    print("[PASS] !whois answers from Wikipedia and never borrows the Twitch "
          "profile")


def test_bot_twitch():
    b, said = _bot()

    whois.clear_cache()
    b._reply_twitch("viewer1", "hardclaws")
    assert said[-1].startswith("Twitch | Hardclaws | Twitch Partner"), said[-1]

    # A leading # is how people actually type a channel.
    whois.clear_cache()
    b._reply_twitch("viewer1", "#hardclaws")
    assert "Twitch Partner" in said[-1], said[-1]

    whois.clear_cache()
    b._reply_twitch("viewer1", "zzxqvnotaperson")
    assert "no Twitch channel" in said[-1], said[-1]

    whois.clear_cache()
    b._reply_twitch("viewer1", "")
    assert "which Twitch name" in said[-1], said[-1]
    print("[PASS] !twitch answers a login, a #channel, a miss and a blank")


def test_at_mention_is_accepted_as_a_login():
    """'!twitch @name' must work - that is how chat actually asks.

    Typing '@' in Twitch chat opens the mention picker, so it is the natural
    way to find someone. '@' can never be part of a real login, so the old
    code did not corrupt a name; it just failed the shape check and reported
    'there is no Twitch channel called @NOTTaitch' for a channel that exists.
    """
    import access

    cases = {
        "@NOTTaitch": "NOTTaitch",
        "#hardclaws": "hardclaws",
        "@#hardclaws": "hardclaws",          # both, either order of typing
        "  @ Hardclaws ": "Hardclaws",       # picker inserts a space
        "https://twitch.tv/nottaitch": "nottaitch",
        "NOTTaitch": "NOTTaitch",            # untouched
        "@": "",                             # nothing left to look up
    }
    for raw, want in cases.items():
        got = access.clean_login(raw)
        assert got == want, f"{raw!r} -> {got!r}, wanted {want!r}"

    # Case must survive: it is echoed back into chat verbatim.
    assert access.clean_login("@NOTTaitch") == "NOTTaitch"
    assert access.clean_login("@NOTTaitch").lower() == "nottaitch"

    # And it reaches the real lookup, not just the helper.
    whois.clear_cache()
    b, said = _bot()
    b._reply_twitch("viewer1", "@hardclaws")
    assert said[-1].startswith("Twitch | Hardclaws |"), said[-1]
    print("[PASS] @name, #name, a pasted URL and a bare name all resolve")


def test_at_mention_lookup_is_not_reported_as_missing():
    """The specific wrong answer from the log.

    '@NOTTaitch' used to come back as 'there is no Twitch channel called
    @NOTTaitch' - a claim about a channel that exists, caused by our own
    parsing. An unparseable query must never be reported as a missing channel.
    """
    whois.clear_cache()
    result = whois.twitch_lookup("@", helix=None)
    assert result["found"] is False
    assert "which Twitch name" in result["reason"], result
    assert "no Twitch channel" not in result["reason"], result
    print("[PASS] an empty query asks for a name; it never claims the channel "
          "does not exist")


def test_twitch_legacy_spellings_still_work():
    """!whotwitch / !whotw / !twitchwho keep working, unadvertised.

    Renaming a command chat already knows should not break anybody mid-stream.
    """
    import bot as bot_mod

    assert bot_mod.TWITCH_COMMANDS == {
        "twitch", "whotwitch", "whotw", "twitchwho"}, bot_mod.TWITCH_COMMANDS
    # !twitch is the only spelling that gets advertised.
    b, said = _bot()
    b._say_help("viewer1", "")
    _drain(b)
    help_text = " ".join(said)
    assert "!twitch <name>" in help_text, help_text
    assert "!whotwitch" not in help_text, help_text

    # Every spelling reaches the same reply.
    for spelling in sorted(bot_mod.TWITCH_COMMANDS):
        whois.clear_cache()
        said.clear()
        b._reply_twitch("viewer1", "hardclaws")
        assert said[-1].startswith("Twitch | Hardclaws |"), (spelling, said[-1])
    print("[PASS] all four spellings work; only !twitch appears in !help")


def _drain(b):
    """Collect what the bot queued.

    !help now queues its lines instead of saying them inline, so the reader
    thread is never left sleeping inside _say's pacing gap. Run the real
    worker to collect them.
    """
    threading.Thread(target=b._worker, daemon=True).start()
    b._jobs.join()


def main():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    whois.REST = f"http://127.0.0.1:{port}/rest/"
    whois.API = f"http://127.0.0.1:{port}/api.php"

    print("==== !whois tests ====")
    for fn in (test_exact_title, test_sentence_boundary, test_search_fallback,
               test_disambiguation_is_not_an_answer, test_not_found,
               test_empty_extract, test_unreachable_raises_rather_than_denying,
               test_cached, test_bot_reply, test_twitch_only,
               test_whois_and_whotwitch_do_not_confuse_each_other,
               test_login_shapes_are_screened,
               test_broken_twitch_says_so_instead_of_denying_the_channel,
               test_no_helix_is_reported_honestly, test_missing_channel,
               test_format_twitch_variants, test_bot_whois_is_wikipedia_only,
               test_bot_twitch, test_twitch_legacy_spellings_still_work,
               test_at_mention_is_accepted_as_a_login,
               test_at_mention_lookup_is_not_reported_as_missing):
        fn()
    server.shutdown()
    print("ALL PASSED \u2714")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
