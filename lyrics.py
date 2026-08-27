"""!ftl — finish the lyric.

The bot posts one line of a song and chat finishes it; the next line is
revealed after a delay, exactly the way !riddle reveals its answer.

WHERE THE LYRICS COME FROM MATTERS
----------------------------------
Nothing in this file is a lyric. Every line is fetched at runtime from
LRCLIB (https://lrclib.net), a free, keyless, open lyrics library. That is a
deliberate choice and not a stylistic one: a pool of lyrics written out by
hand is a pool of lyrics that are *almost* right, posted into chat under a
real artist's name. "Almost right" is indistinguishable from a lie to the
person reading it, and it is the specific failure this bot keeps having to
unlearn elsewhere. So the bot never writes a lyric, it only quotes one.

Two consequences of that:

  * LRCLIB has no genre or year field, so the genre/decade filters have to
    come from our own song list. That list holds artist, title, genre and
    decade only — metadata, no lyrics — and a wrong pairing simply 404s and
    gets skipped, so it corrects itself rather than posting anything wrong.

  * `duration` is deliberately NOT sent. LRCLIB only matches a track when the
    duration is within ±2 seconds, and we have no reliable durations, so
    sending one would mostly produce 404s.

Only one line is posted as the prompt and one as the answer, never a verse.
Lyrics are cached in memory only: nothing copyrighted is written to disk.
"""

from __future__ import annotations

import json
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://lrclib.net"

# LRCLIB's terms ask for an identifying User-Agent. They are generous about
# rate limits precisely because clients do this.
USER_AGENT = "ClawFacts/1.0 (Twitch chat bot; finish-the-lyric rounds)"

CACHE_TTL = 86400.0        # a lyric is not going to change today
MISS_TTL = 3600.0          # do not re-ask for a track that is not there
MAX_LINE = 200             # keep a line well inside Twitch's 500-char message
MIN_WORDS = 3              # "Ooh" is not a prompt anybody can finish
RECENT_WINDOW = 40         # songs not to repeat

DECADES = {
    "60s": (1960, 1969), "1960s": (1960, 1969), "sixties": (1960, 1969),
    "70s": (1970, 1979), "1970s": (1970, 1979), "seventies": (1970, 1979),
    "80s": (1980, 1989), "1980s": (1980, 1989), "eighties": (1980, 1989),
    "90s": (1990, 1999), "1990s": (1990, 1999), "nineties": (1990, 1999),
    "00s": (2000, 2009), "2000s": (2000, 2009), "noughties": (2000, 2009),
    "10s": (2010, 2019), "2010s": (2010, 2019), "tens": (2010, 2019),
}

GENRES = {
    "rock", "classic rock", "pop", "country", "hip-hop", "hiphop", "rap",
    "r&b", "rnb", "soul", "metal", "punk", "disco", "folk", "blues",
    "reggae", "indie", "electronic", "dance", "grunge",
}

# Genre names chat types, mapped to the label used in SONGS.
GENRE_ALIASES = {
    "hiphop": "hip-hop", "rap": "hip-hop", "rnb": "r&b", "dance": "electronic",
    # "classic rock" is just how people say rock here. Left as its own genre
    # it was an accepted filter with an empty bucket behind it - a dead end.
    "classic rock": "rock", "classicrock": "rock", "classic": "rock",
}


class LyricsError(Exception):
    """The lyric could not be settled - as opposed to 'that track is absent'."""


# ---- the song list ---------------------------------------------------------
# (artist, title, genre, year). Metadata only - no lyrics live here.
# Weighted towards country and classic rock because that is the room this bot
# lives in, but broad enough that a genre filter is not a dead end.

SONGS = [
    # country
    ("Dolly Parton", "Jolene", "country", 1973),
    ("Dolly Parton", "9 to 5", "country", 1980),
    ("Dolly Parton", "I Will Always Love You", "country", 1974),
    ("Johnny Cash", "Folsom Prison Blues", "country", 1956),
    ("Johnny Cash", "Ring of Fire", "country", 1963),
    ("Johnny Cash", "Hurt", "country", 2002),
    ("Johnny Cash", "Walk the Line", "country", 1956),
    ("Willie Nelson", "On the Road Again", "country", 1980),
    ("Willie Nelson", "Blue Eyes Crying in the Rain", "country", 1975),
    ("Waylon Jennings", "Mammas Don't Let Your Babies Grow Up to Be Cowboys",
     "country", 1978),
    ("Merle Haggard", "Okie from Muskogee", "country", 1969),
    ("Merle Haggard", "Mama Tried", "country", 1968),
    ("Loretta Lynn", "Coal Miner's Daughter", "country", 1970),
    ("Patsy Cline", "Crazy", "country", 1961),
    ("Patsy Cline", "Walking After Midnight", "country", 1957),
    ("Glen Campbell", "Rhinestone Cowboy", "country", 1975),
    ("Glen Campbell", "Wichita Lineman", "country", 1968),
    ("Kenny Rogers", "The Gambler", "country", 1978),
    ("Kenny Rogers", "Islands in the Stream", "country", 1983),
    ("George Jones", "He Stopped Loving Her Today", "country", 1980),
    ("Tammy Wynette", "Stand by Your Man", "country", 1968),
    ("Hank Williams", "Your Cheatin' Heart", "country", 1953),
    ("Hank Williams", "Hey Good Lookin'", "country", 1951),
    ("Alabama", "Mountain Music", "country", 1982),
    ("Randy Travis", "Forever and Ever, Amen", "country", 1987),
    ("Alan Jackson", "Chattahoochee", "country", 1993),
    ("Alan Jackson", "Don't Rock the Jukebox", "country", 1991),
    ("George Strait", "Amarillo by Morning", "country", 1983),
    ("George Strait", "Check Yes or No", "country", 1995),
    ("Garth Brooks", "Friends in Low Places", "country", 1990),
    ("Garth Brooks", "The Thunder Rolls", "country", 1991),
    ("Shania Twain", "Man! I Feel Like a Woman!", "country", 1999),
    ("Shania Twain", "You're Still the One", "country", 1998),
    ("Faith Hill", "Breathe", "country", 1999),
    ("Tim McGraw", "Live Like You Were Dying", "country", 2004),
    ("Toby Keith", "Should've Been a Cowboy", "country", 1993),
    ("Brooks & Dunn", "Boot Scootin' Boogie", "country", 1992),
    ("Chris Stapleton", "Tennessee Whiskey", "country", 2015),
    ("Zac Brown Band", "Chicken Fried", "country", 2008),
    ("Carrie Underwood", "Before He Cheats", "country", 2006),
    ("Brad Paisley", "Mud on the Tires", "country", 2004),
    ("Keith Urban", "Blue Ain't Your Color", "country", 2016),
    ("Kacey Musgraves", "Follow Your Arrow", "country", 2013),
    ("Luke Combs", "Hurricane", "country", 2016),
    ("John Denver", "Take Me Home, Country Roads", "country", 1971),
    ("John Denver", "Rocky Mountain High", "country", 1972),
    # classic rock
    ("Queen", "Bohemian Rhapsody", "rock", 1975),
    ("Queen", "We Will Rock You", "rock", 1977),
    ("Queen", "Don't Stop Me Now", "rock", 1978),
    ("Queen", "Somebody to Love", "rock", 1976),
    ("Led Zeppelin", "Stairway to Heaven", "rock", 1971),
    ("Led Zeppelin", "Whole Lotta Love", "rock", 1969),
    ("Led Zeppelin", "Immigrant Song", "rock", 1970),
    ("The Rolling Stones", "Paint It, Black", "rock", 1966),
    ("The Rolling Stones", "Start Me Up", "rock", 1981),
    ("The Rolling Stones", "Gimme Shelter", "rock", 1969),
    ("The Rolling Stones", "Sympathy for the Devil", "rock", 1968),
    ("The Beatles", "Hey Jude", "rock", 1968),
    ("The Beatles", "Let It Be", "rock", 1970),
    ("The Beatles", "Come Together", "rock", 1969),
    ("The Beatles", "Here Comes the Sun", "rock", 1969),
    ("The Beatles", "Twist and Shout", "rock", 1963),
    ("Pink Floyd", "Comfortably Numb", "rock", 1979),
    ("Pink Floyd", "Wish You Were Here", "rock", 1975),
    ("Pink Floyd", "Another Brick in the Wall, Part 2", "rock", 1979),
    ("Pink Floyd", "Money", "rock", 1973),
    ("AC/DC", "Back in Black", "rock", 1980),
    ("AC/DC", "Highway to Hell", "rock", 1979),
    ("AC/DC", "You Shook Me All Night Long", "rock", 1980),
    ("Aerosmith", "Dream On", "rock", 1973),
    ("Aerosmith", "Walk This Way", "rock", 1975),
    ("Aerosmith", "I Don't Want to Miss a Thing", "rock", 1998),
    ("Van Halen", "Jump", "rock", 1984),
    ("Van Halen", "Panama", "rock", 1984),
    ("Bon Jovi", "Livin' on a Prayer", "rock", 1986),
    ("Bon Jovi", "You Give Love a Bad Name", "rock", 1986),
    ("Bon Jovi", "Wanted Dead or Alive", "rock", 1986),
    ("Guns N' Roses", "Sweet Child o' Mine", "rock", 1987),
    ("Guns N' Roses", "Welcome to the Jungle", "rock", 1987),
    ("Guns N' Roses", "November Rain", "rock", 1991),
    ("Def Leppard", "Pour Some Sugar on Me", "rock", 1987),
    ("Def Leppard", "Photograph", "rock", 1983),
    ("Journey", "Don't Stop Believin'", "rock", 1981),
    ("Journey", "Faithfully", "rock", 1983),
    ("Boston", "More Than a Feeling", "rock", 1976),
    ("Kansas", "Carry On Wayward Son", "rock", 1976),
    ("Lynyrd Skynyrd", "Sweet Home Alabama", "rock", 1974),
    ("Lynyrd Skynyrd", "Free Bird", "rock", 1973),
    ("The Eagles", "Hotel California", "rock", 1977),
    ("The Eagles", "Take It Easy", "rock", 1972),
    ("The Eagles", "Desperado", "rock", 1973),
    ("Fleetwood Mac", "Go Your Own Way", "rock", 1977),
    ("Fleetwood Mac", "Dreams", "rock", 1977),
    ("Fleetwood Mac", "The Chain", "rock", 1977),
    ("The Doors", "Break On Through (To the Other Side)", "rock", 1967),
    ("The Doors", "Riders on the Storm", "rock", 1971),
    ("Jimi Hendrix", "Purple Haze", "rock", 1967),
    ("Jimi Hendrix", "All Along the Watchtower", "rock", 1968),
    ("Cream", "Sunshine of Your Love", "rock", 1967),
    ("Deep Purple", "Smoke on the Water", "rock", 1972),
    ("Black Sabbath", "Paranoid", "rock", 1970),
    ("The Who", "Baba O'Riley", "rock", 1971),
    ("The Who", "Pinball Wizard", "rock", 1969),
    ("Tom Petty", "Free Fallin'", "rock", 1989),
    ("Tom Petty", "American Girl", "rock", 1977),
    ("Tom Petty", "I Won't Back Down", "rock", 1989),
    ("Bruce Springsteen", "Born to Run", "rock", 1975),
    ("Bruce Springsteen", "Born in the U.S.A.", "rock", 1984),
    ("Bruce Springsteen", "Thunder Road", "rock", 1975),
    ("Bruce Springsteen", "Dancing in the Dark", "rock", 1984),
    ("Billy Joel", "Piano Man", "rock", 1973),
    ("Billy Joel", "We Didn't Start the Fire", "rock", 1989),
    ("Elton John", "Rocket Man", "rock", 1972),
    ("Elton John", "Tiny Dancer", "rock", 1971),
    ("Elton John", "Crocodile Rock", "rock", 1972),
    ("Rod Stewart", "Maggie May", "rock", 1971),
    ("Bryan Adams", "Summer of '69", "rock", 1984),
    ("Bryan Adams", "Everything I Do (I Do It for You)", "rock", 1991),
    ("Meat Loaf", "I'd Do Anything for Love (But I Won't Do That)",
     "rock", 1993),
    ("Heart", "Barracuda", "rock", 1977),
    ("Heart", "Alone", "rock", 1987),
    ("Pat Benatar", "Hit Me with Your Best Shot", "rock", 1980),
    ("Joan Jett", "I Love Rock 'n' Roll", "rock", 1981),
    ("Foreigner", "I Want to Know What Love Is", "rock", 1984),
    ("Survivor", "Eye of the Tiger", "rock", 1982),
    ("Europe", "The Final Countdown", "rock", 1986),
    ("Nirvana", "Smells Like Teen Spirit", "rock", 1991),
    ("Nirvana", "Come as You Are", "rock", 1991),
    ("Pearl Jam", "Alive", "rock", 1991),
    ("Soundgarden", "Black Hole Sun", "rock", 1994),
    ("Foo Fighters", "Everlong", "rock", 1997),
    ("Nirvana", "In Bloom", "grunge", 1991),
    ("Alice in Chains", "Man in the Box", "grunge", 1990),
    ("Alice in Chains", "Would?", "grunge", 1992),
    ("Stone Temple Pilots", "Plush", "grunge", 1992),
    ("Stone Temple Pilots", "Interstate Love Song", "grunge", 1994),
    ("Silverchair", "Tomorrow", "grunge", 1994),
    ("Temple of the Dog", "Hunger Strike", "grunge", 1991),
    ("Pearl Jam", "Jeremy", "grunge", 1991),
    ("Pearl Jam", "Even Flow", "grunge", 1991),
    ("Foo Fighters", "Learn to Fly", "rock", 1999),
    ("Green Day", "Basket Case", "rock", 1994),
    ("Green Day", "Boulevard of Broken Dreams", "rock", 2004),
    ("Red Hot Chili Peppers", "Under the Bridge", "rock", 1991),
    ("Red Hot Chili Peppers", "Californication", "rock", 1999),
    ("Oasis", "Wonderwall", "rock", 1995),
    ("Oasis", "Don't Look Back in Anger", "rock", 1995),
    ("U2", "With or Without You", "rock", 1987),
    ("U2", "One", "rock", 1991),
    ("The Police", "Every Breath You Take", "rock", 1983),
    ("Dire Straits", "Sultans of Swing", "rock", 1978),
    ("Dire Straits", "Money for Nothing", "rock", 1985),
    ("Phil Collins", "In the Air Tonight", "rock", 1981),
    ("Genesis", "Land of Confusion", "rock", 1986),
    # pop
    ("Michael Jackson", "Billie Jean", "pop", 1982),
    ("Michael Jackson", "Thriller", "pop", 1982),
    ("Michael Jackson", "Beat It", "pop", 1982),
    ("Michael Jackson", "Man in the Mirror", "pop", 1987),
    ("Prince", "Purple Rain", "pop", 1984),
    ("Prince", "When Doves Cry", "pop", 1984),
    ("Madonna", "Like a Prayer", "pop", 1989),
    ("Madonna", "Material Girl", "pop", 1984),
    ("Whitney Houston", "I Wanna Dance with Somebody", "pop", 1987),
    ("Whitney Houston", "Greatest Love of All", "pop", 1985),
    ("Cyndi Lauper", "Girls Just Want to Have Fun", "pop", 1983),
    ("Cyndi Lauper", "Time After Time", "pop", 1983),
    ("A-ha", "Take On Me", "pop", 1985),
    ("Tears for Fears", "Everybody Wants to Rule the World", "pop", 1985),
    ("Depeche Mode", "Enjoy the Silence", "pop", 1990),
    ("Toto", "Africa", "pop", 1982),
    ("Eurythmics", "Sweet Dreams (Are Made of This)", "pop", 1983),
    ("Britney Spears", "...Baby One More Time", "pop", 1998),
    ("Backstreet Boys", "I Want It That Way", "pop", 1999),
    ("NSYNC", "Bye Bye Bye", "pop", 2000),
    ("Spice Girls", "Wannabe", "pop", 1996),
    ("Robbie Williams", "Angels", "pop", 1997),
    ("Kylie Minogue", "Can't Get You Out of My Head", "pop", 2001),
    ("Rihanna", "Umbrella", "pop", 2007),
    ("Rihanna", "Diamonds", "pop", 2012),
    ("Beyoncé", "Halo", "pop", 2008),
    ("Beyoncé", "Crazy in Love", "pop", 2003),
    ("Lady Gaga", "Poker Face", "pop", 2008),
    ("Lady Gaga", "Bad Romance", "pop", 2009),
    ("Adele", "Someone Like You", "pop", 2011),
    ("Adele", "Rolling in the Deep", "pop", 2010),
    ("Taylor Swift", "Shake It Off", "pop", 2014),
    ("Taylor Swift", "Love Story", "pop", 2008),
    ("Ed Sheeran", "Shape of You", "pop", 2017),
    ("Ed Sheeran", "Thinking Out Loud", "pop", 2014),
    ("Bruno Mars", "Uptown Funk", "pop", 2014),
    ("Katy Perry", "Firework", "pop", 2010),
    ("ABBA", "Dancing Queen", "pop", 1976),
    ("ABBA", "Mamma Mia", "pop", 1975),
    ("ABBA", "Take a Chance on Me", "pop", 1978),
    ("Bee Gees", "Stayin' Alive", "disco", 1977),
    ("Bee Gees", "How Deep Is Your Love", "disco", 1977),
    ("Donna Summer", "Hot Stuff", "disco", 1979),
    ("Chic", "Le Freak", "disco", 1978),
    ("Village People", "Y.M.C.A.", "disco", 1978),
    ("Gloria Gaynor", "I Will Survive", "disco", 1978),
    # hip-hop
    ("Eminem", "Lose Yourself", "hip-hop", 2002),
    ("Eminem", "Without Me", "hip-hop", 2002),
    ("Eminem", "Stan", "hip-hop", 2000),
    ("Dr. Dre", "Still D.R.E.", "hip-hop", 1999),
    ("Snoop Dogg", "Gin and Juice", "hip-hop", 1994),
    ("2Pac", "California Love", "hip-hop", 1995),
    ("2Pac", "Changes", "hip-hop", 1998),
    ("The Notorious B.I.G.", "Juicy", "hip-hop", 1994),
    ("Jay-Z", "Empire State of Mind", "hip-hop", 2009),
    ("Kanye West", "Gold Digger", "hip-hop", 2005),
    ("Outkast", "Hey Ya!", "hip-hop", 2003),
    ("Outkast", "Ms. Jackson", "hip-hop", 2000),
    ("Run-DMC", "Walk This Way", "hip-hop", 1986),
    ("Beastie Boys", "Sabotage", "hip-hop", 1994),
    ("Wu-Tang Clan", "C.R.E.A.M.", "hip-hop", 1993),
    ("N.W.A", "Straight Outta Compton", "hip-hop", 1988),
    ("Missy Elliott", "Get Ur Freak On", "hip-hop", 2001),
    ("Kendrick Lamar", "HUMBLE.", "hip-hop", 2017),
    ("Drake", "Hotline Bling", "hip-hop", 2015),
    # r&b / soul
    ("Stevie Wonder", "Superstition", "soul", 1972),
    ("Stevie Wonder", "Signed, Sealed, Delivered (I'm Yours)", "soul", 1970),
    ("Marvin Gaye", "What's Going On", "soul", 1971),
    ("Marvin Gaye", "Let's Get It On", "soul", 1973),
    ("Aretha Franklin", "Respect", "soul", 1967),
    ("Aretha Franklin", "I Say a Little Prayer", "soul", 1968),
    ("Otis Redding", "Sittin' on the Dock of the Bay", "soul", 1968),
    ("Sam Cooke", "A Change Is Gonna Come", "soul", 1964),
    ("Al Green", "Let's Stay Together", "soul", 1971),
    ("Bill Withers", "Lean on Me", "soul", 1972),
    ("Bill Withers", "Ain't No Sunshine", "soul", 1971),
    ("Ray Charles", "Georgia on My Mind", "soul", 1960),
    ("Tina Turner", "What's Love Got to Do with It", "pop", 1984),
    ("Alicia Keys", "No One", "r&b", 2007),
    ("Usher", "Yeah!", "r&b", 2004),
    ("Frank Ocean", "Thinkin Bout You", "r&b", 2012),
    # metal / punk
    ("Metallica", "Enter Sandman", "metal", 1991),
    ("Metallica", "Nothing Else Matters", "metal", 1991),
    ("Iron Maiden", "The Trooper", "metal", 1983),
    ("Ozzy Osbourne", "Crazy Train", "metal", 1980),
    ("Motorhead", "Ace of Spades", "metal", 1980),
    ("Slipknot", "Duality", "metal", 2004),
    ("The Clash", "London Calling", "punk", 1979),
    ("Ramones", "Blitzkrieg Bop", "punk", 1976),
    ("Sex Pistols", "Anarchy in the U.K.", "punk", 1976),
    ("The Offspring", "Self Esteem", "punk", 1994),
    ("Blink-182", "All the Small Things", "punk", 1999),
    # folk / blues / reggae
    ("Bob Dylan", "Blowin' in the Wind", "folk", 1963),
    ("Bob Dylan", "Like a Rolling Stone", "folk", 1965),
    ("Simon & Garfunkel", "The Sound of Silence", "folk", 1965),
    ("Simon & Garfunkel", "Mrs. Robinson", "folk", 1968),
    ("Joni Mitchell", "Big Yellow Taxi", "folk", 1970),
    ("B.B. King", "The Thrill Is Gone", "blues", 1969),
    ("Stevie Ray Vaughan", "Pride and Joy", "blues", 1983),
    ("Bob Marley", "Three Little Birds", "reggae", 1977),
    ("Bob Marley", "No Woman, No Cry", "reggae", 1974),
    ("Bob Marley", "Redemption Song", "reggae", 1980),
    ("Jimmy Cliff", "I Can See Clearly Now", "reggae", 1994),
    # electronic
    ("Daft Punk", "Get Lucky", "electronic", 2013),
    ("Daft Punk", "Around the World", "electronic", 1997),
    ("The Prodigy", "Firestarter", "electronic", 1996),
    ("Avicii", "Wake Me Up", "electronic", 2013),
    ("Calvin Harris", "Summer", "electronic", 2014),
    ("Gorillaz", "Feel Good Inc.", "electronic", 2005),
    # indie
    ("The Smiths", "There Is a Light That Never Goes Out", "indie", 1986),
    ("The Smiths", "How Soon Is Now?", "indie", 1984),
    ("The Cure", "Just Like Heaven", "indie", 1987),
    ("The Cure", "Lovesong", "indie", 1989),
    ("Radiohead", "Creep", "indie", 1992),
    ("Radiohead", "Karma Police", "indie", 1997),
    ("Coldplay", "Yellow", "indie", 2000),
    ("Coldplay", "Fix You", "indie", 2005),
    ("Coldplay", "Viva la Vida", "indie", 2008),
    ("The Killers", "Mr. Brightside", "indie", 2003),
    ("The Killers", "Somebody Told Me", "indie", 2004),
    ("Arctic Monkeys", "I Bet You Look Good on the Dancefloor", "indie", 2005),
    ("Arctic Monkeys", "Do I Wanna Know?", "indie", 2013),
    ("The Strokes", "Last Nite", "indie", 2001),
    ("Florence + the Machine", "Dog Days Are Over", "indie", 2008),
    ("Mumford & Sons", "Little Lion Man", "indie", 2009),
    ("The Lumineers", "Ho Hey", "indie", 2012),
    ("Tame Impala", "The Less I Know the Better", "indie", 2015),
    ("Vampire Weekend", "A-Punk", "indie", 2008),
    ("MGMT", "Kids", "indie", 2007),
]


# ---- the prompt picker -----------------------------------------------------
_BLANK = re.compile(r"^[\s\W_]*$")
_PAREN = re.compile(r"^\(.*\)$")
_NON_LATIN = re.compile(r"[^\x00-\x7F]")


def _clean_lines(plain: str) -> list:
    """Plain lyrics -> a list of real lines, in order."""
    out = []
    for raw in (plain or "").split("\n"):
        line = " ".join(raw.split())
        # Strip an LRC timestamp if one slipped through.
        line = re.sub(r"^\[\d{1,2}:\d{2}(?:\.\d+)?\]\s*", "", line).strip()
        if line:
            out.append(line)
    return out


def _usable(line: str) -> bool:
    """Is this line something chat could actually be asked to finish?"""
    if not line or len(line) > MAX_LINE:
        return False
    if len(line.split()) < MIN_WORDS:
        return False                       # "Ooh", "Yeah yeah", "Na na na"
    if _BLANK.match(line):
        return False
    if _PAREN.match(line):
        return False                       # "(any way the wind blows)"
    # A line that is mostly non-Latin script is not finishable by this chat.
    if len(_NON_LATIN.findall(line)) > len(line) / 4:
        return False
    return True


def pick_pair(lines: list):
    """Choose (prompt, answer) from a song's lines, or None.

    The answer must be the line that *actually* follows the prompt, so only
    consecutive pairs are considered - skipping ahead to find a nicer-looking
    answer would make the game ask a question with a wrong answer, which is
    worse than having no round at all.

    Both lines have to be usable: a prompt followed by "Ooh" is not a round
    anybody can win.
    """
    pairs = [(a, b) for a, b in zip(lines, lines[1:])
             if _usable(a) and _usable(b)]
    if not pairs:
        return None

    def score(pair):
        prompt, answer = pair
        # Prefer prompts of a length chat can hold in its head: long enough to
        # be recognisable, short enough to fit in a message with room to spare.
        words = len(prompt.split())
        return -abs(words - 8) - (1 if len(answer) > 120 else 0)

    pairs.sort(key=score)
    # Take from the best few rather than always the single best, so the same
    # song does not always produce the same round.
    return random.choice(pairs[:max(1, min(5, len(pairs)))])


# ---- fetching --------------------------------------------------------------
_cache = {}
_cache_lock = threading.RLock()


class _NotFound(Exception):
    pass


def _get(path: str, params: dict, timeout: float = 8.0) -> dict:
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise _NotFound(path)
        if exc.code == 429:
            # Their terms say the Retry-After header must be honoured; ignoring
            # it is how a client gets banned.
            wait = exc.headers.get("Retry-After") if exc.headers else None
            raise LyricsError(f"LRCLIB is rate-limiting us (retry after "
                              f"{wait or 'unknown'}s)")
        raise LyricsError(f"LRCLIB returned HTTP {exc.code}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise LyricsError(f"could not reach LRCLIB ({exc!r})")


def fetch_lyrics(artist: str, track: str, timeout: float = 8.0) -> list:
    """The lines of a song, or [] if LRCLIB does not have it.

    Raises LyricsError when the request could not be settled at all. Those are
    different situations: "not in the database" means try another song, while
    "unreachable" means the whole game is down and we should say so.
    """
    key = f"{artist.lower()}|{track.lower()}"
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit["t"] < hit["ttl"]:
            return list(hit["v"])
        _cache.pop(key, None)

    try:
        data = _get("/api/get", {"artist_name": artist, "track_name": track},
                    timeout)
    except _NotFound:
        with _cache_lock:
            _cache[key] = {"v": [], "t": now, "ttl": MISS_TTL}
        return []

    lines = _clean_lines(data.get("plainLyrics") or "")
    if data.get("instrumental") or not lines:
        lines = []
    with _cache_lock:
        _cache[key] = {"v": list(lines), "t": now,
                       "ttl": CACHE_TTL if lines else MISS_TTL}
    return lines


# ---- filters ---------------------------------------------------------------
def parse_filter(argument: str = "") -> dict:
    """'80s rock' -> {"genre": "rock", "decade": "80s", "artist": None}.

    Anything unrecognised is treated as an artist or title fragment rather
    than rejected, so `!ftl queen` and `!ftl jolene` both do something useful.
    """
    out = {"genre": None, "decade": None, "artist": None}
    leftovers = []
    for token in (argument or "").split():
        low = token.lower().strip(",.")
        if low in DECADES:
            out["decade"] = low
        elif low in GENRES or low in GENRE_ALIASES:
            out["genre"] = GENRE_ALIASES.get(low, low)
        else:
            leftovers.append(token)
    if leftovers:
        out["artist"] = " ".join(leftovers).lower()
    return out


def candidates(filt: dict) -> list:
    """The songs matching a parsed filter."""
    out = []
    span = DECADES.get(filt.get("decade") or "")
    for artist, title, genre, year in SONGS:
        if filt.get("genre") and genre != filt["genre"]:
            continue
        if span and not span[0] <= year <= span[1]:
            continue
        if filt.get("artist"):
            needle = filt["artist"]
            if needle not in artist.lower() and needle not in title.lower():
                continue
        out.append((artist, title, genre, year))
    return out


# ---- the round -------------------------------------------------------------
_recent = []
_recent_lock = threading.Lock()


def _mark(song_key: str) -> None:
    with _recent_lock:
        if song_key in _recent:
            _recent.remove(song_key)
        _recent.append(song_key)
        del _recent[:-RECENT_WINDOW]


def forget_recent() -> None:
    with _recent_lock:
        _recent.clear()


def get_round(argument: str = "", tries: int = 6, timeout: float = 8.0):
    """Build one round for `!ftl <filter>`.

    Returns {"prompt", "answer", "artist", "title", "genre", "year", "label"}
    or None if nothing usable could be found. Raises LyricsError only when
    LRCLIB could not be reached at all, so the caller can tell "that genre is
    empty" apart from "the lyrics library is down".

    A song that 404s or has no finishable pair is simply skipped and another
    is tried - a bad entry in the song list should cost one attempt, not the
    round.
    """
    filt = parse_filter(argument)
    pool = candidates(filt)
    if not pool:
        return None

    with _recent_lock:
        recent = set(_recent)
    ordered = [s for s in pool
               if f"{s[0]}|{s[1]}".lower() not in recent] or list(pool)
    random.shuffle(ordered)

    unreachable = 0
    for artist, title, genre, year in ordered[:tries]:
        try:
            lines = fetch_lyrics(artist, title, timeout)
        except LyricsError as exc:
            unreachable += 1
            print(f"[ftl] {artist} - {title}: {exc}", flush=True)
            continue
        if not lines:
            continue                       # not in the database: move on
        pair = pick_pair(lines)
        if not pair:
            continue                       # nothing finishable in it
        _mark(f"{artist}|{title}".lower())
        label = filt.get("genre") or (filt.get("decade") or "")
        if filt.get("genre") and filt.get("decade"):
            label = f"{filt['decade']} {filt['genre']}"
        return {"prompt": pair[0], "answer": pair[1], "artist": artist,
                "title": title, "genre": genre, "year": year,
                "label": label or "any"}

    # Every attempt failed. If any of them were outages rather than misses,
    # that is an outage - do not let it read as "no songs match".
    if unreachable and unreachable == min(tries, len(ordered)):
        raise LyricsError("could not reach LRCLIB")
    return None


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def stats() -> dict:
    """Sizes of each filter bucket, for tests and !help."""
    out = {}
    for genre in sorted({g for _, _, g, _ in SONGS}):
        out[genre] = sum(1 for _, _, g, _ in SONGS if g == genre)
    return out
