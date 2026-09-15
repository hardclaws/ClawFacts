"""Names for !smk and anything else that needs recognisable people.

A fixed list is the problem being fixed here. Forty-four names, three drawn a
round, and chat sees the same faces every night no matter how the draw is
shuffled - the pool is simply smaller than the number of rounds people play.

So there are two layers:

  SEED        ~350 hand-picked public figures. Always available, no network,
              and every one of them is famous enough that a round is still
              playable for someone who does not follow that person's field.

  harvested   Wikipedia's own category listings - "Category:American film
              actresses" and friends - fetched in the background and cached to
              names.json. Thousands more names, and the list grows every time
              the bot tops up.

Nothing here blocks on the network mid-command. `top_up()` is called from a
keeper thread; `draw()` only ever reads what is already in memory.

The draw also remembers who it has used recently and avoids them, so a big
pool actually behaves like a big pool instead of cycling through its first few
entries.
"""

from __future__ import annotations

import datetime
import json
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import storage

NAMES_PATH = "names.json"

API = "https://en.wikipedia.org/w/api.php"
# Wikimedia sorts clients into two rate-limit tiers by User-Agent. An
# *identifiable* one - a contactable URL or email - is allowed 200 requests per
# minute; anything else is treated as unidentified and capped at 10. The old
# string had no contact in it, so we were in the 10/min tier while asking for
# 55/min, and the last six categories of every cycle came back 429.
USER_AGENT = ("ClawFacts/1.0 (+https://github.com/hardclaws/ClawFacts; "
              "hobby Twitch chat bot, name pools)")

# 1.1s between requests is ~55/min: comfortably inside the identified tier,
# and still polite. Anything that gets a 429 anyway is handled by backoff
# below rather than by retrying at the same speed.
FETCH_DELAY = 1.1
# How long to leave a category alone after Wikipedia refuses it, and how far
# that stretches if it keeps refusing. Stops the same six categories being
# retried every single cycle forever.
COOLDOWN_SECONDS = 3600
COOLDOWN_MAX = 24 * 3600
# Wikipedia's "expensive request" guidance: wait this long after a slow reply.
SLOW_REPLY_SECONDS = 5.0
CMLIMIT = 500            # the API maximum; one call per category
MAX_HARVESTED = 4000     # cap the cache so names.json stays a small file
RECENT_WINDOW = 240      # names not to reuse, across all rounds
SEED_WEIGHT = 0.75       # chance a given name comes from the seed pool


# ---- the seed pool ---------------------------------------------------------
# (name, what they are known for). Hand-picked: recognisable, uncontroversial
# to describe, and public figures so nobody in chat gets named by accident.

SEED_FEMALE = [
    # musicians
    ("Rihanna", "singer"), ("Beyoncé", "singer"), ("Taylor Swift", "singer"),
    ("Dolly Parton", "singer"), ("Madonna", "singer"), ("Adele", "singer"),
    ("Lady Gaga", "singer"), ("Ariana Grande", "singer"),
    ("Dua Lipa", "singer"), ("Billie Eilish", "singer"), ("P!nk", "singer"),
    ("Katy Perry", "singer"), ("Shakira", "singer"), ("Sia", "singer"),
    ("Lizzo", "singer"), ("Cardi B", "rapper"), ("Nicki Minaj", "rapper"),
    ("Missy Elliott", "rapper"), ("Sade", "singer"),
    ("Whitney Houston", "singer"), ("Aretha Franklin", "singer"),
    ("Tina Turner", "singer"), ("Diana Ross", "singer"),
    ("Stevie Nicks", "singer"), ("Björk", "singer"),
    ("Alanis Morissette", "singer"), ("Shania Twain", "singer"),
    ("Gwen Stefani", "singer"), ("Christina Aguilera", "singer"),
    ("Mariah Carey", "singer"), ("Miley Cyrus", "singer"),
    ("Doja Cat", "singer"), ("Olivia Rodrigo", "singer"), ("SZA", "singer"),
    ("Norah Jones", "singer"), ("Amy Winehouse", "singer"), ("Enya", "singer"),
    ("Annie Lennox", "singer"), ("Debbie Harry", "singer"),
    ("Kacey Musgraves", "singer"), ("Carrie Underwood", "singer"),
    ("Reba McEntire", "singer"), ("Loretta Lynn", "singer"),
    ("Patsy Cline", "singer"), ("Janis Joplin", "singer"),
    ("Joni Mitchell", "singer"), ("Kate Bush", "singer"),
    ("Sinéad O'Connor", "singer"), ("Kylie Minogue", "singer"),
    ("Cher", "singer"), ("Barbra Streisand", "singer"), ("Céline Dion", "singer"),
    ("Olivia Newton-John", "singer"), ("Florence Welch", "singer"),
    ("Lana Del Rey", "singer"), ("Hayley Williams", "singer"),
    ("St. Vincent", "musician"), ("Courtney Barnett", "singer"),
    ("Halsey", "singer"), ("Kesha", "singer"), ("Dido", "singer"),
    # actresses
    ("Margot Robbie", "actress"), ("Scarlett Johansson", "actress"),
    ("Sandra Bullock", "actress"), ("Zendaya", "actress"),
    ("Viola Davis", "actress"), ("Charlize Theron", "actress"),
    ("Aubrey Plaza", "actress"), ("Florence Pugh", "actress"),
    ("Emma Stone", "actress"), ("Natalie Portman", "actress"),
    ("Anne Hathaway", "actress"), ("Meryl Streep", "actress"),
    ("Julia Roberts", "actress"), ("Reese Witherspoon", "actress"),
    ("Nicole Kidman", "actress"), ("Cate Blanchett", "actress"),
    ("Kate Winslet", "actress"), ("Helen Mirren", "actress"),
    ("Judi Dench", "actress"), ("Maggie Smith", "actress"),
    ("Olivia Colman", "actress"), ("Emma Thompson", "actress"),
    ("Keira Knightley", "actress"), ("Rachel McAdams", "actress"),
    ("Amy Adams", "actress"), ("Jennifer Lawrence", "actress"),
    ("Emma Watson", "actress"), ("Mila Kunis", "actress"),
    ("Cameron Diaz", "actress"), ("Drew Barrymore", "actress"),
    ("Winona Ryder", "actress"), ("Uma Thurman", "actress"),
    ("Sigourney Weaver", "actress"), ("Jamie Lee Curtis", "actress"),
    ("Jodie Foster", "actress"), ("Susan Sarandon", "actress"),
    ("Geena Davis", "actress"), ("Sandra Oh", "actress"),
    ("Awkwafina", "actress"), ("Mindy Kaling", "actress"),
    ("Melissa McCarthy", "actress"), ("Kristen Wiig", "actress"),
    ("Amy Poehler", "actress"), ("Whoopi Goldberg", "actress"),
    ("Taraji P. Henson", "actress"), ("Octavia Spencer", "actress"),
    ("Lupita Nyong'o", "actress"), ("Zoe Saldaña", "actress"),
    ("Salma Hayek", "actress"), ("Penélope Cruz", "actress"),
    ("Marion Cotillard", "actress"), ("Sofia Vergara", "actress"),
    ("Betty White", "actress"), ("Goldie Hawn", "actress"),
    ("Diane Keaton", "actress"), ("Jane Fonda", "actress"),
    ("Glenn Close", "actress"), ("Frances McDormand", "actress"),
    ("Laura Dern", "actress"), ("Gwyneth Paltrow", "actress"),
    ("Anya Taylor-Joy", "actress"), ("Saoirse Ronan", "actress"),
    ("Carey Mulligan", "actress"), ("Tilda Swinton", "actress"),
    ("Rachel Weisz", "actress"), ("Emily Blunt", "actress"),
    ("Jessica Chastain", "actress"), ("Brie Larson", "actress"),
    ("Zoë Kravitz", "actress"), ("Sydney Sweeney", "actress"),
    ("Jenna Ortega", "actress"), ("Millie Bobby Brown", "actress"),
    ("Gal Gadot", "actress"), ("Ana de Armas", "actress"),
    ("Bryce Dallas Howard", "actress"), ("Elisabeth Moss", "actress"),
    ("Christina Ricci", "actress"), ("Sarah Paulson", "actress"),
    ("Hayley Atwell", "actress"),
    # comedians and presenters
    ("Lucille Ball", "comedienne"), ("Kate McKinnon", "comedian"),
    ("Tina Fey", "comedian"), ("Amy Schumer", "comedian"),
    ("Wanda Sykes", "comedian"), ("Ellen DeGeneres", "presenter"),
    ("Oprah Winfrey", "presenter"), ("Carol Burnett", "comedian"),
    ("Betty Gilpin", "actress"), ("Ali Wong", "comedian"),
    # athletes
    ("Serena Williams", "tennis player"), ("Venus Williams", "tennis player"),
    ("Naomi Osaka", "tennis player"), ("Ash Barty", "tennis player"),
    ("Maria Sharapova", "tennis player"),
    ("Martina Navratilova", "tennis player"),
    ("Billie Jean King", "tennis player"), ("Steffi Graf", "tennis player"),
    ("Simone Biles", "gymnast"), ("Sha'Carri Richardson", "sprinter"),
    ("Allyson Felix", "sprinter"), ("Cathy Freeman", "sprinter"),
    ("Shelly-Ann Fraser-Pryce", "sprinter"), ("Sam Kerr", "footballer"),
    ("Megan Rapinoe", "footballer"), ("Alex Morgan", "footballer"),
    ("Ronda Rousey", "fighter"), ("Katie Ledecky", "swimmer"),
    ("Emma McKeon", "swimmer"), ("Jessica Ennis-Hill", "athlete"),
    ("Laura Kenny", "cyclist"), ("Anna Meares", "cyclist"),
    ("Anna Netrebko", "opera singer"),
    # beyond entertainment
    ("Michelle Obama", "author"), ("Malala Yousafzai", "activist"),
    ("Greta Thunberg", "activist"), ("Jane Goodall", "primatologist"),
    ("Marie Curie", "scientist"), ("Amelia Earhart", "aviator"),
    ("Ruth Bader Ginsburg", "judge"), ("Jacinda Ardern", "politician"),
    ("Kamala Harris", "politician"), ("Queen Elizabeth II", "monarch"),
    ("Frida Kahlo", "artist"), ("Georgia O'Keeffe", "artist"),
]

SEED_MALE = [
    # actors
    ("Tom Hanks", "actor"), ("Morgan Freeman", "actor"),
    ("Denzel Washington", "actor"), ("Leonardo DiCaprio", "actor"),
    ("Brad Pitt", "actor"), ("George Clooney", "actor"),
    ("Will Smith", "actor"), ("Samuel L. Jackson", "actor"),
    ("Robert De Niro", "actor"), ("Al Pacino", "actor"),
    ("Anthony Hopkins", "actor"), ("Harrison Ford", "actor"),
    ("Keanu Reeves", "actor"), ("Idris Elba", "actor"),
    ("Danny DeVito", "actor"), ("Pedro Pascal", "actor"),
    ("Steve Buscemi", "actor"), ("Matthew McConaughey", "actor"),
    ("Bryan Cranston", "actor"), ("Oscar Isaac", "actor"),
    ("Jeff Goldblum", "actor"), ("Paul Rudd", "actor"),
    ("Jason Momoa", "actor"), ("Dev Patel", "actor"),
    ("Giancarlo Esposito", "actor"), ("Willem Dafoe", "actor"),
    ("Chris Pratt", "actor"), ("Daniel Kaluuya", "actor"),
    ("Rami Malek", "actor"), ("Viggo Mortensen", "actor"),
    ("Ryan Gosling", "actor"), ("Christian Bale", "actor"),
    ("Heath Ledger", "actor"), ("Russell Crowe", "actor"),
    ("Hugh Jackman", "actor"), ("Chris Hemsworth", "actor"),
    ("Tom Hiddleston", "actor"), ("Benedict Cumberbatch", "actor"),
    ("Matt Damon", "actor"), ("Ben Affleck", "actor"),
    ("Mark Wahlberg", "actor"), ("Dwayne Johnson", "actor"),
    ("Vin Diesel", "actor"), ("Jason Statham", "actor"),
    ("Liam Neeson", "actor"), ("Daniel Day-Lewis", "actor"),
    ("Gary Oldman", "actor"), ("Colin Firth", "actor"),
    ("Michael Caine", "actor"), ("Ian McKellen", "actor"),
    ("Patrick Stewart", "actor"), ("Sean Connery", "actor"),
    ("Clint Eastwood", "actor"), ("Robert Redford", "actor"),
    ("Paul Newman", "actor"), ("Marlon Brando", "actor"),
    ("James Dean", "actor"), ("Steve McQueen", "actor"),
    ("Jack Nicholson", "actor"), ("Robin Williams", "actor"),
    ("Jim Carrey", "actor"), ("Eddie Murphy", "actor"),
    ("Adam Sandler", "actor"), ("Ben Stiller", "actor"),
    ("Owen Wilson", "actor"), ("Will Ferrell", "actor"),
    ("Steve Carell", "actor"), ("John Krasinski", "actor"),
    ("Jonah Hill", "actor"), ("Zac Efron", "actor"),
    ("Channing Tatum", "actor"), ("Ryan Reynolds", "actor"),
    ("Jake Gyllenhaal", "actor"), ("Timothée Chalamet", "actor"),
    ("Tom Holland", "actor"), ("Andrew Garfield", "actor"),
    ("Michael B. Jordan", "actor"), ("Chadwick Boseman", "actor"),
    ("John Boyega", "actor"), ("Daniel Craig", "actor"),
    ("Pierce Brosnan", "actor"), ("Roger Moore", "actor"),
    ("Sean Bean", "actor"), ("Mads Mikkelsen", "actor"),
    ("Christoph Waltz", "actor"), ("Javier Bardem", "actor"),
    ("Benicio del Toro", "actor"), ("Andy Serkis", "actor"),
    ("Henry Cavill", "actor"), ("Robert Pattinson", "actor"),
    ("Karl Urban", "actor"), ("Sam Neill", "actor"),
    ("Russell Brand", "comedian"), ("Craig Robinson", "actor"),
    # musicians
    ("Elton John", "singer"), ("Freddie Mercury", "singer"),
    ("David Bowie", "singer"), ("Prince", "musician"),
    ("Michael Jackson", "singer"), ("Stevie Wonder", "singer"),
    ("Marvin Gaye", "singer"), ("Bob Dylan", "singer"),
    ("Bruce Springsteen", "singer"), ("Ed Sheeran", "singer"),
    ("Harry Styles", "singer"), ("Bruno Mars", "singer"),
    ("Justin Timberlake", "singer"), ("Usher", "singer"),
    ("The Weeknd", "singer"), ("Drake", "rapper"),
    ("Kendrick Lamar", "rapper"), ("Eminem", "rapper"),
    ("Snoop Dogg", "rapper"), ("Jay-Z", "rapper"),
    ("Kanye West", "rapper"), ("Pharrell Williams", "singer"),
    ("John Legend", "singer"), ("Frank Ocean", "singer"),
    ("Post Malone", "singer"), ("Travis Scott", "rapper"),
    ("Bad Bunny", "singer"), ("Rod Stewart", "singer"),
    ("Mick Jagger", "singer"), ("Keith Richards", "guitarist"),
    ("Ozzy Osbourne", "singer"), ("Paul McCartney", "singer"),
    ("John Lennon", "singer"), ("George Harrison", "singer"),
    ("Ringo Starr", "drummer"), ("Eric Clapton", "guitarist"),
    ("Phil Collins", "singer"), ("Sting", "singer"),
    ("Billy Joel", "singer"), ("Lionel Richie", "singer"),
    ("Michael Bublé", "singer"), ("Chris Martin", "singer"),
    ("Bono", "singer"), ("Andrea Bocelli", "opera singer"),
    ("Luciano Pavarotti", "opera singer"), ("Lenny Kravitz", "singer"),
    ("John Mayer", "singer"), ("Sam Fender", "singer"),
    # comedians and presenters
    ("Ricky Gervais", "comedian"), ("Stephen Fry", "presenter"),
    ("Rowan Atkinson", "comedian"), ("Conan O'Brien", "presenter"),
    ("Jimmy Fallon", "presenter"), ("John Oliver", "comedian"),
    ("James Corden", "presenter"), ("Graham Norton", "presenter"),
    ("Bill Burr", "comedian"), ("Dave Chappelle", "comedian"),
    ("Kevin Hart", "comedian"), ("Jerry Seinfeld", "comedian"),
    ("Billy Connolly", "comedian"), ("Eric Idle", "comedian"),
    ("John Cleese", "comedian"), ("Michael Palin", "presenter"),
    ("Seth Rogen", "comedian"), ("David Letterman", "presenter"),
    ("Trevor Noah", "comedian"), ("Hasan Minhaj", "comedian"),
    # athletes
    ("Michael Jordan", "basketball player"),
    ("LeBron James", "basketball player"), ("Kobe Bryant", "basketball player"),
    ("Shaquille O'Neal", "basketball player"),
    ("Stephen Curry", "basketball player"), ("Tiger Woods", "golfer"),
    ("Roger Federer", "tennis player"), ("Rafael Nadal", "tennis player"),
    ("Novak Djokovic", "tennis player"), ("Lionel Messi", "footballer"),
    ("Cristiano Ronaldo", "footballer"), ("Pelé", "footballer"),
    ("Diego Maradona", "footballer"), ("Kylian Mbappé", "footballer"),
    ("Usain Bolt", "sprinter"), ("Mo Farah", "runner"),
    ("Lewis Hamilton", "racing driver"), ("Max Verstappen", "racing driver"),
    ("Valentino Rossi", "motorcyclist"), ("Tom Brady", "quarterback"),
    ("Patrick Mahomes", "quarterback"), ("Wayne Gretzky", "ice hockey player"),
    ("Connor McDavid", "ice hockey player"), ("Mike Tyson", "boxer"),
    ("Muhammad Ali", "boxer"), ("Richie McCaw", "rugby player"),
    ("Sonny Bill Williams", "rugby player"), ("David Beckham", "footballer"),
    ("Thierry Henry", "footballer"), ("Ronaldinho", "footballer"),
    # beyond entertainment
    ("Barack Obama", "politician"), ("Elon Musk", "entrepreneur"),
    ("David Attenborough", "broadcaster"),
    ("Neil deGrasse Tyson", "astrophysicist"),
    ("Stephen Hawking", "physicist"), ("Albert Einstein", "physicist"),
    ("Gordon Ramsay", "chef"), ("Jamie Oliver", "chef"),
    ("Anthony Bourdain", "chef"), ("Bear Grylls", "adventurer"),
    ("Banksy", "artist"), ("Jeff Bezos", "entrepreneur"),
]

# Wikipedia categories to top the pool up from. Each one implies the job, which
# is why the job label is attached here rather than guessed per person.
CATEGORIES = [
    ("American film actresses", "female", "actress"),
    ("American television actresses", "female", "actress"),
    ("English film actresses", "female", "actress"),
    ("American women singers", "female", "singer"),
    ("American female models", "female", "model"),
    ("American women comedians", "female", "comedian"),
    ("American female tennis players", "female", "tennis player"),
    ("Australian actresses", "female", "actress"),
    ("American film actors", "male", "actor"),
    ("American television actors", "male", "actor"),
    ("English film actors", "male", "actor"),
    ("American male singers", "male", "singer"),
    ("American rappers", "male", "rapper"),
    ("American male models", "male", "model"),
    ("American male tennis players", "male", "tennis player"),
    ("Australian actors", "male", "actor"),
]


# ---- filtering ------------------------------------------------------------
# A category listing is not a list of people. "Category:American film
# actresses" contains "List of American film actresses", awards pages, and
# anything else that got categorised there. Posting one of those into chat as
# a person to shag, marry or kill is the failure mode this guards.

_NOT_A_PERSON = (
    "list of", "lists of", "timeline of", "outline of", "category:",
    "template:", "wikipedia:", "file:", "portal:", "index:", "help:",
    "award", "awards", "festival", "championship", "championships",
    "stadium", "arena", "university", "college", "school", "museum",
    "church", "cathedral", "station", "airport", "hotel", "theatre",
    "theater", "cinema", "studio", "magazine", "newspaper", "journal",
    "company", "records", "band", "tour", "album", "song", "single",
    "album", "film", "series", "episode", "game", "team", "league",
    "park", "street", "county", "city", "town", "village", "house",
    "building", "bridge", "monument", "statue", "library", "hospital",
    "discography", "filmography", "videography", "biography", "history",
)

_NAME_OK = re.compile(r"^[A-Z][A-Za-zÀ-ÿ.'\-]*( [A-Z][A-Za-zÀ-ÿ.'\-]*){0,4}$")


def person_name(title: str) -> str | None:
    """A category member title that is actually a person, cleaned, or None."""
    title = " ".join((title or "").split())
    if not title or len(title) < 3 or len(title) > 48:
        return None
    # A parenthetical is Wikipedia separating two people with the same name.
    # Picking one at random would name the wrong person, so skip both.
    if "(" in title or ")" in title:
        return None
    if any(ch.isdigit() for ch in title):
        return None
    lowered = title.lower()
    if any(bad in lowered for bad in _NOT_A_PERSON):
        return None
    # "The Godfather" is a film, not a person. Titles that open with "The "
    # are works, groups and events far more often than stage names, and the
    # few stage names lost this way are not worth the false positives.
    if lowered.startswith("the "):
        return None
    if not _NAME_OK.match(title):
        return None
    return title


# ---- the pool -------------------------------------------------------------
class NamePool:
    """Seed names plus a harvested cache, with a recency memory."""

    def __init__(self, path: str = NAMES_PATH, seed=None):
        self.path = path
        seed = seed if seed is not None else {}
        self._seed = {
            "female": list(seed.get("female") or SEED_FEMALE),
            "male": list(seed.get("male") or SEED_MALE),
        }
        self._harvested = {"female": [], "male": [], "any": []}
        # Reentrant: _recent_set() calls available() while the caller
        # already holds this, and a plain Lock deadlocked on draw.
        self._lock = threading.RLock()
        self._recent = []
        # (timestamp to resume, consecutive refusals) for the whole pool. A
        # 429 is about this client, not one category, so every fetch waits.
        # Each further refusal doubles the wait, so a persistently throttled
        # pool costs one request a day instead of one per cycle.
        self._cooldown = (0.0, 0)
        self.updated = None
        self._load()

    # -- persistence ---------------------------------------------------
    def _load(self) -> None:
        data = storage.load_json(self.path, None)
        if not isinstance(data, dict):
            return
        harvested = data.get("harvested")
        if isinstance(harvested, dict):
            with self._lock:
                for gender in self._harvested:
                    rows = harvested.get(gender)
                    if isinstance(rows, list):
                        # Each row is [name, job]; drop anything malformed.
                        self._harvested[gender] = [
                            [str(r[0]), str(r[1])] for r in rows
                            if isinstance(r, (list, tuple)) and len(r) >= 2
                            and r[0]
                        ]
        self.updated = data.get("updated")

    def _save(self) -> bool:
        with self._lock:
            payload = {"harvested": {g: [list(r) for r in rows]
                                     for g, rows in self._harvested.items()},
                       "updated": self.updated}
        return storage.save_json(self.path, payload)

    # -- reading -------------------------------------------------------
    def _known(self) -> set:
        with self._lock:
            known = {n.lower() for rows in self._seed.values()
                     for n, _ in rows}
            for rows in self._harvested.values():
                known.update(r[0].lower() for r in rows)
            return known

    def counts(self) -> dict:
        """How many names are available per gender. Handy for !help and tests."""
        with self._lock:
            return {
                "female": len(self._seed["female"])
                          + len(self._harvested["female"]),
                "male": len(self._seed["male"]) + len(self._harvested["male"]),
                "seed": len(self._seed["female"]) + len(self._seed["male"]),
                "harvested": sum(len(r) for r in self._harvested.values()),
            }

    def available(self, gender: str) -> list:
        """Every (name, job) pair for `gender`, seed first."""
        gender = gender if gender in self._harvested else "any"
        with self._lock:
            if gender == "any":
                out = list(self._seed["female"]) + list(self._seed["male"])
                for g in ("female", "male", "any"):
                    out += [tuple(r) for r in self._harvested[g]]
            else:
                out = list(self._seed[gender])
                out += [tuple(r) for r in self._harvested[gender]]
                out += [tuple(r) for r in self._harvested["any"]]
        return out

    # -- drawing -------------------------------------------------------
    def _recent_set(self, g: str, n: int) -> set:
        """The recency window, trimmed so it can never block a draw.

        A fixed window larger than the pool marks every name as recent once it
        fills up, and then there is nothing left to draw. Capping it at
        pool_size - n keeps it as long as it usefully can be.
        """
        total = len(self.available(g))
        allowed = max(0, min(RECENT_WINDOW, total - n))
        return set(self._recent[-allowed:]) if allowed else set()

    def _tiers(self, g: str, recent: set):
        """(seed, harvested) for a gender, minus anyone used recently."""
        if g == "any":
            seed = list(self._seed["female"]) + list(self._seed["male"])
            extra = [tuple(r) for rows in self._harvested.values()
                     for r in rows]
        else:
            seed = list(self._seed[g])
            extra = [tuple(r) for r in self._harvested[g]]
            extra += [tuple(r) for r in self._harvested["any"]]
        return ([p for p in seed if p[0].lower() not in recent],
                [p for p in extra if p[0].lower() not in recent])

    def draw(self, gender: str = "any", n: int = 3):
        """`n` distinct (name, job) pairs, avoiding recently used names.

        Each name is drawn mostly from the seed pool and sometimes from the
        harvested tail. Blending them matters: drawing seed-first with a
        recency window smaller than the seed pool means the harvested names
        are *never* reached, and the top-up does nothing at all. Weighting the
        choice keeps rounds recognisable while still putting new names into
        rotation, which is the whole reason there is a second tier.

        The recency filter is relaxed rather than returning nothing: "I ran
        out of names" is a worse answer than a repeat.

        Returns (picks, label) or None if there are fewer than `n` names.
        """
        g = {"f": "female", "female": "female", "women": "female",
             "woman": "female", "her": "female",
             "m": "male", "male": "male", "men": "male", "man": "male",
             "him": "male"}.get((gender or "any").strip().lower(), "any")

        with self._lock:
            seed, extra = self._tiers(g, self._recent_set(g, n))
        if len(seed) + len(extra) < n:
            # Recency filtered too hard for this gender, so ignore it rather
            # than tell chat the pool is empty when it plainly is not.
            with self._lock:
                seed, extra = self._tiers(g, set())
        if len(seed) + len(extra) < n:
            return None

        picks = []
        for _ in range(n):
            # `seed` and `extra` are local copies, so popping from them is
            # what keeps the three names in a round distinct.
            want_extra = extra and random.random() > SEED_WEIGHT
            tier = extra if (want_extra or not seed) else seed
            if not tier:
                tier = extra or seed
            if not tier:
                break
            picks.append(tier.pop(random.randrange(len(tier))))

        if len(picks) < n:
            # Recency ate the pool. Fall back to everyone, still distinct.
            with self._lock:
                everyone, _ = self._tiers(g, set())
            everyone = [p for p in everyone if p not in picks]
            random.shuffle(everyone)
            picks += everyone[:n - len(picks)]

        self.mark_used(name for name, _ in picks)
        return picks, g

    def mark_used(self, names) -> None:
        with self._lock:
            for name in names:
                low = name.lower()
                if low in self._recent:
                    self._recent.remove(low)
                self._recent.append(low)
            del self._recent[:-RECENT_WINDOW]

    def forget_recent(self) -> None:
        with self._lock:
            self._recent.clear()

    # -- topping up ----------------------------------------------------
    def _resumable(self) -> bool:
        """True unless the whole pool is still cooling off after a 429."""
        return time.time() >= self._cooldown[0]

    def _note_refusal(self, retry_after: float | None) -> float:
        """Record a refusal pool-wide and return how long every fetch waits.

        A 429 is about this *client*, not about the one category that happened
        to trip it - the next category would have been refused too. So the
        backoff is pool-wide: the first version cooled off only the category
        that failed, and the next cycle simply restarted at category 0 and
        burned the same budget again.

        Wikipedia's own Retry-After wins when it sends one, because the server
        knows its window and we do not. Otherwise the wait doubles each time,
        so a persistently throttled pool costs a fetch a day rather than one
        per cycle.
        """
        fails = self._cooldown[1] + 1
        wait = retry_after if retry_after else min(
            COOLDOWN_SECONDS * (2 ** (fails - 1)), COOLDOWN_MAX)
        self._cooldown = (time.time() + wait, fails)
        return wait

    def _note_success(self) -> None:
        """Clear the doubling once a fetch gets through again."""
        self._cooldown = (0.0, 0)

    def top_up(self, categories=None, delay: float = FETCH_DELAY,
               timeout: float = 8.0, limit: int | None = None) -> int:
        """Fetch category listings and add any new people. Returns how many.

        Runs from a keeper thread. Never raises: a Wikipedia outage must not
        take the bot down, and the seed pool keeps working regardless.

        Refusals are summarised in ONE line per cycle rather than one per
        category: six identical 429 lines every cycle buried the log and made
        a single rate limit look like six separate faults.
        """
        if not self._resumable():
            left = self._cooldown[0] - time.time()
            print(f"[names] still cooling off after a rate limit; "
                  f"next top-up in ~{max(left / 60, 1):.0f} min.", flush=True)
            return 0
        added = 0
        throttled = 0
        failed = []
        wait_total = 0.0
        for title, gender, job in (categories or CATEGORIES)[:limit]:
            try:
                rows = harvest_category(title, timeout=timeout)
            except RateLimited as exc:
                throttled += 1
                wait_total = self._note_refusal(exc.retry_after)
                # Everything after this in the cycle would be refused too, so
                # stop here and let the cooldown run down.
                break
            except Exception as exc:
                failed.append(f"{title} ({type(exc).__name__})")
                continue
            self._note_success()
            added += self.add(rows, gender, job)
            if delay:
                time.sleep(delay)
        if added:
            self.updated = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._save()
        if throttled:
            print(f"[names] Wikipedia rate-limited the top-up; resuming in "
                  f"~{max(wait_total / 60, 1):.0f} min.", flush=True)
        elif failed:
            print(f"[names] {len(failed)} category lookup(s) failed: "
                  + "; ".join(failed[:3])
                  + (" ..." if len(failed) > 3 else ""), flush=True)
        return added

    def add(self, names, gender: str, job: str) -> int:
        """Add (name) strings under `gender`/`job`. Returns how many were new."""
        gender = gender if gender in self._harvested else "any"
        known = self._known()
        fresh = []
        for raw in names:
            name = person_name(raw)
            if not name or name.lower() in known:
                continue
            known.add(name.lower())
            fresh.append([name, job])
        if not fresh:
            return 0
        with self._lock:
            rows = self._harvested[gender]
            room = MAX_HARVESTED - len(rows)
            rows.extend(fresh[:max(0, room)])
        return min(len(fresh), max(0, room))


class RateLimited(Exception):
    """Wikipedia answered 429. Carries its Retry-After if it sent one.

    Separate from a network failure because the two want opposite responses:
    a timeout says "try again soon", a 429 says "stop, and come back later".
    """

    def __init__(self, retry_after: float | None = None):
        self.retry_after = retry_after
        super().__init__(f"429 Too Many Requests"
                         + (f" (retry after {retry_after:.0f}s)"
                            if retry_after else ""))


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    """Seconds from a Retry-After header, which may be a count or a date."""
    raw = (exc.headers or {}).get("Retry-After") if exc.headers else None
    if not raw:
        return None
    raw = raw.strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        when = parsedate_to_datetime(raw)
        return max(0.0, (when - datetime.datetime.now(when.tzinfo)).total_seconds())
    except Exception:
        return None


def harvest_category(title: str, timeout: float = 8.0) -> list:
    """Page titles in a Wikipedia category. Raises on a network failure.

    Raises rather than returning []: an empty list and an outage look the same
    from the outside, and the caller needs to tell them apart. A 429 raises
    RateLimited so the caller can honour Retry-After instead of hammering.
    """
    qs = urllib.parse.urlencode({
        "action": "query", "list": "categorymembers",
        "cmtitle": "Category:" + title.replace(" ", "_"),
        "cmtype": "page", "cmlimit": str(CMLIMIT),
        "format": "json", "formatversion": "2",
    })
    req = urllib.request.Request(
        f"{API}?{qs}", headers={"User-Agent": USER_AGENT,
                                "Accept": "application/json",
                                "Accept-Encoding": "identity"})
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise RateLimited(_retry_after(exc)) from exc
        raise
    # Wikimedia asks callers to back off after any request that took more than
    # a second to serve. Cheap to honour, and it is the signal that precedes a
    # 429 rather than the 429 itself.
    if time.time() - started > 1.0:
        time.sleep(SLOW_REPLY_SECONDS)
    members = ((data or {}).get("query") or {}).get("categorymembers") or []
    return [m.get("title") or "" for m in members if isinstance(m, dict)]


# One shared pool, so the recency memory survives across commands.
pool = NamePool()


def get_smk(gender: str = "any", n: int = 3, name_pool: NamePool | None = None):
    """`n` (name, job) pairs for shag / marry / kill."""
    return (name_pool or pool).draw(gender, n)


def clear_cache(path: str = NAMES_PATH) -> None:
    """Forget the harvested names on disk. Used by the tests."""
    try:
        import os
        os.unlink(path)
    except OSError:
        pass
