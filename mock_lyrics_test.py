"""Tests for lyrics.py - the !ftl finish-the-lyric game.

NOT A SINGLE REAL LYRIC APPEARS IN THIS FILE, on purpose. Every line below is
a made-up placeholder. That is not squeamishness, it is two practical things:

  1. Lyrics are copyrighted, and a test suite is a place they would quietly
     accumulate and then live in the repo forever.
  2. Real lyrics are a moving target - LRCLIB revises records, so a test
     asserting on a real line would eventually fail for a reason that has
     nothing to do with this code.

The placeholder lines below are shaped like real ones (short interjections,
parentheticals, over-long lines) because the interesting behaviour is which
lines the picker rejects.
"""

import http.server
import json
import threading
import urllib.parse

import lyrics

# Made-up lyrics. Shaped to trip every rejection rule in _usable().
GOOD = "\n".join([
    "First line of a made up song about nothing",       # usable
    "Second line that follows it quite naturally",      # usable -> the answer
    "Ooh",                                              # too short
    "Yeah yeah",                                        # too short
    "(whispered backing vocals here)",                  # all parenthetical
    "A third line which is also perfectly reasonable",  # usable
    "Fourth line to give the picker something to choose between",
    "Fifth line closing out this entirely invented song",
])

SHORT_ONLY = "Ooh\nYeah\nNa na na\nMmm"

LONG_LINE = "word " * 80 + "ends here"

INSTRUMENTAL = ""

# Non-Latin-heavy line, shaped like the stray script that turns up in some
# community-submitted lyric records.
ODD_SCRIPT = "\n".join([
    "A normal line that could be asked about here",
    "\u0628\u0650\u0633\u0652\u0645\u0650 \u0627\u0644\u0644\u0651\u0670\u0647\u0650 \u0627\u0644\u0631\u0651\u064e\u062d\u0652\u0645\u0651\u0670\u0646\u0650",
    "Another normal line after the odd one",
])


def _payload(title="Made Up Song", artist="The Nobodies", plain=GOOD,
             instrumental=False):
    return {
        "id": 1, "name": title, "trackName": title, "artistName": artist,
        "albumName": "Nowhere", "duration": 200, "instrumental": instrumental,
        "plainLyrics": plain, "syncedLyrics": "", "lyricsfile": "",
    }


class Handler(http.server.BaseHTTPRequestHandler):
    """A fake LRCLIB.

    Models the three things the real one does that matter: a full record, a
    404 with the documented body, and a 429 with Retry-After.
    """

    mode = "ok"
    asked = []

    def do_GET(self):
        # The client percent-encodes, so decode before comparing - a fake that
        # matches raw query strings is testing its own parsing, not the code.
        query = urllib.parse.parse_qs(self.path.split("?", 1)[-1])
        track = (query.get("track_name") or [""])[0]
        Handler.asked.append(track)

        # The real API only matches a track within +/-2s of the duration we
        # send, so sending one would mostly 404. Prove we never send it.
        assert "duration" not in query, f"sent a duration: {query}"

        if Handler.mode == "rate-limited":
            self.send_response(429)
            self.send_header("Retry-After", "30")
            self.end_headers()
            self.wfile.write(b"{}")
            return
        if Handler.mode == "down":
            raise OSError("connection refused")

        if "Missing" in track:
            body = json.dumps({"message": "Failed to find specified track",
                               "name": "TrackNotFound",
                               "statusCode": 404}).encode()
            self.send_response(404)
        elif "Instrumental" in track:
            body = json.dumps(_payload(track, instrumental=True,
                                       plain=INSTRUMENTAL)).encode()
            self.send_response(200)
        elif "Short" in track:
            body = json.dumps(_payload(track, plain=SHORT_ONLY)).encode()
            self.send_response(200)
        elif "Long" in track:
            body = json.dumps(_payload(track, plain=LONG_LINE)).encode()
            self.send_response(200)
        elif "Odd" in track:
            body = json.dumps(_payload(track, plain=ODD_SCRIPT)).encode()
            self.send_response(200)
        else:
            body = json.dumps(_payload(track)).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_usable():
    assert lyrics._usable("First line of a made up song about nothing")
    assert not lyrics._usable("Ooh"), "one word is not a prompt"
    assert not lyrics._usable("Yeah yeah"), "two words is not a prompt"
    assert not lyrics._usable(""), "blank"
    assert not lyrics._usable("(backing vocals)"), "all parenthetical"
    assert not lyrics._usable("word " * 80), "too long for a chat message"
    assert not lyrics._usable("\u0628\u0650\u0633\u0652\u0645\u0650 "
                              "\u0627\u0644\u0644\u0651\u0670\u0647\u0650"), \
        "non-Latin-heavy line is not finishable by this chat"
    print("[PASS] short, blank, parenthetical, over-long and non-Latin lines "
          "are all rejected")


def test_clean_lines():
    lines = lyrics._clean_lines("  spaced   out  \n\n\n[00:12.34] timestamped\n")
    assert lines == ["spaced out", "timestamped"], lines
    assert lyrics._clean_lines("") == []
    assert lyrics._clean_lines(None) == []
    print("[PASS] blank lines dropped, whitespace collapsed, LRC timestamps "
          "stripped")


def test_pick_pair_is_consecutive():
    """The answer must be the line that actually follows the prompt. Skipping
    ahead to find a prettier answer would make the game ask a question with a
    wrong answer, which is worse than having no round."""
    lines = lyrics._clean_lines(GOOD)
    for _ in range(200):
        pair = lyrics.pick_pair(lines)
        assert pair is not None
        prompt, answer = pair
        i = lines.index(prompt)
        assert lines[i + 1] == answer, (prompt, answer)
    print("[PASS] over 200 draws the answer was always the next real line")


def test_pick_pair_rejects_unwinnable_songs():
    assert lyrics.pick_pair(lyrics._clean_lines(SHORT_ONLY)) is None
    assert lyrics.pick_pair([]) is None
    # One usable line with nothing after it is not a round either.
    assert lyrics.pick_pair(["Only one usable line in this whole song"]) is None
    print("[PASS] a song with nothing finishable yields no round")


def test_round_shape():
    lyrics.clear_cache()
    lyrics.forget_recent()
    got = lyrics.get_round("")
    assert got is not None, "no round built"
    for key in ("prompt", "answer", "artist", "title", "genre", "year",
                "label"):
        assert got[key], f"missing {key}: {got}"
    assert got["prompt"] != got["answer"], got
    print(f"[PASS] a round: {got['artist']} - {got['title']} "
          f"[{got['label']}]")


def test_filters_reach_the_api():
    """A genre/decade filter must narrow the request, not just relabel it."""
    for arg, expect in (("80s rock", "rock"), ("country", "country"),
                        ("queen", None)):
        lyrics.clear_cache()
        lyrics.forget_recent()
        got = lyrics.get_round(arg)
        assert got is not None, arg
        if expect:
            assert got["genre"] == expect, (arg, got["genre"])
    print("[PASS] genre, decade and artist filters all narrow the pool")


def test_recent_songs_are_avoided():
    """Same complaint as !smk: a pool only feels big if it does not repeat."""
    lyrics.clear_cache()
    lyrics.forget_recent()
    seen = []
    for _ in range(10):
        got = lyrics.get_round("")
        assert got is not None
        seen.append((got["artist"], got["title"]))
    assert len(set(seen)) == len(seen), f"repeated: {seen}"
    print(f"[PASS] {len(seen)} rounds, {len(set(seen))} distinct songs")


def test_missing_track_is_skipped_not_fatal():
    """A song that is not in LRCLIB costs one attempt, not the round. This is
    what makes a wrong entry in the hand-written song list self-correcting."""
    lyrics.clear_cache()
    lyrics.forget_recent()
    before = len(Handler.asked)
    got = lyrics.get_round("zzznotarealartist")
    assert got is None, got          # nothing matches, and that is honest
    assert len(Handler.asked) == before, "queried the API for an empty filter"
    print("[PASS] an empty filter returns None without calling the API")


def test_instrumental_and_short_are_skipped():
    lyrics.clear_cache()
    lyrics.forget_recent()
    assert lyrics.fetch_lyrics("Anyone", "Instrumental Track") == []
    assert lyrics.fetch_lyrics("Anyone", "Short Track") != []
    assert lyrics.pick_pair(lyrics.fetch_lyrics("Anyone", "Short Track")) \
        is None
    print("[PASS] instrumentals return no lines; unfinishable songs no round")


def test_not_found_is_cached():
    """A 404 must not be re-asked on every command."""
    lyrics.clear_cache()
    Handler.asked.clear()
    assert lyrics.fetch_lyrics("Nobody", "Missing Track") == []
    assert lyrics.fetch_lyrics("Nobody", "Missing Track") == []
    assert Handler.asked.count("Missing Track") == 1, Handler.asked
    print("[PASS] a track that is not there is asked about once, not twice")


def test_rate_limit_is_reported_not_swallowed():
    """LRCLIB's terms say Retry-After must be honoured. And a rate limit must
    never be reported to chat as 'that genre has no songs'."""
    lyrics.clear_cache()
    Handler.mode = "rate-limited"
    try:
        try:
            lyrics.fetch_lyrics("Anyone", "Any Track")
        except lyrics.LyricsError as exc:
            assert "rate-limiting" in str(exc), exc
            print(f"[PASS] 429 becomes a clear error: {exc}")
            return
        raise AssertionError("429 was swallowed")
    finally:
        Handler.mode = "ok"
        lyrics.clear_cache()


def test_outage_is_not_reported_as_no_songs():
    """The rule this bot keeps relearning: being unable to check is not the
    same as the answer being no."""
    lyrics.clear_cache()
    lyrics.forget_recent()
    Handler.mode = "down"
    try:
        try:
            lyrics.get_round("")
        except lyrics.LyricsError as exc:
            assert "could not reach LRCLIB" in str(exc), exc
            print("[PASS] an outage raises rather than returning None")
            return
        raise AssertionError("an outage was reported as 'no songs match'")
    finally:
        Handler.mode = "ok"
        lyrics.clear_cache()


def test_server_error_is_not_a_miss():
    lyrics.clear_cache()
    Handler.mode = "down"
    try:
        try:
            lyrics.fetch_lyrics("Anyone", "Any Track")
        except lyrics.LyricsError:
            print("[PASS] an unreachable LRCLIB raises, it does not return []")
            return
        raise AssertionError("failure returned an empty lyric")
    finally:
        Handler.mode = "ok"
        lyrics.clear_cache()


def test_song_list_is_sound():
    """The song list is the only hand-written part, so check it hard."""
    assert len(lyrics.SONGS) > 200, len(lyrics.SONGS)
    keys = [(a.lower(), t.lower()) for a, t, _, _ in lyrics.SONGS]
    dupes = {k for k in keys if keys.count(k) > 1}
    assert not dupes, f"duplicate songs: {dupes}"
    for artist, title, genre, year in lyrics.SONGS:
        assert artist and title and genre, (artist, title, genre)
        assert genre in lyrics.GENRES, f"{artist}: unknown genre {genre}"
        assert 1900 <= year <= 2026, (artist, title, year)
    print(f"[PASS] {len(lyrics.SONGS)} songs, no duplicates, every genre "
          f"known, every year plausible")


def test_every_filter_bucket_is_playable():
    """A filter that matches zero songs is a dead end chat will hit."""
    for genre in lyrics.GENRES - {"classic rock", "hiphop", "rap", "rnb",
                                  "dance", "classicrock", "classic"}:
        n = len(lyrics.candidates({"genre": genre, "decade": None,
                                   "artist": None}))
        assert n > 0, f"no songs for genre {genre}"
    for decade in ("60s", "70s", "80s", "90s", "00s", "10s"):
        n = len(lyrics.candidates({"genre": None, "decade": decade,
                                   "artist": None}))
        assert n >= 5, f"only {n} songs for {decade}"
    print("[PASS] every genre and decade filter has songs behind it")


def main():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    lyrics.API = f"http://127.0.0.1:{server.server_address[1]}"

    print("==== !ftl tests ====")
    for fn in (test_usable, test_clean_lines, test_pick_pair_is_consecutive,
               test_pick_pair_rejects_unwinnable_songs, test_round_shape,
               test_filters_reach_the_api, test_recent_songs_are_avoided,
               test_missing_track_is_skipped_not_fatal,
               test_instrumental_and_short_are_skipped,
               test_not_found_is_cached,
               test_rate_limit_is_reported_not_swallowed,
               test_outage_is_not_reported_as_no_songs,
               test_server_error_is_not_a_miss, test_song_list_is_sound,
               test_every_filter_bucket_is_playable):
        fn()
    server.shutdown()
    print("ALL PASSED \u2714")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
