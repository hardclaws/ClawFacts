"""Extra free, keyless fun commands — inspired by free API lists used by other
Twitch chatbots (e.g. abdullahmorrison/twitch-chatbot).

Every endpoint below was tested and is currently live. All return plain text
(no keys, no auth).

Each one also has a local pool it falls back to. That matters more than it
sounds: these are hobby endpoints run by strangers, and any of them can vanish
overnight. Without a fallback the bot's answer to that is "couldn't fetch that
right now" — repeated every time anyone types the command, forever. A dead
API should cost variety, not the game.

So each getter tries the network first and drops to its own pool on any
failure, and says which it did. The pools are drawn without repeating
themselves until they have been all the way round.

    !joke            official-joke-api.appspot.com
    !randomfact      uselessfacts.jsph.pl
    !riddle          riddles-api.vercel.app  (answer revealed by the bot later)
    !wouldyourather  api.truthordarebot.xyz/v1/wyr
"""

from __future__ import annotations

import json
import urllib.error
import random
import urllib.request

USER_AGENT = "TwitchFunFactBot/1.0 (hobby Twitch chat bot)"


def _get_json(url: str, timeout: float = 8.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _clean(s: str) -> str:
    return " ".join((s or "").split())


JOKES = [
    "I told my wife she was drawing her eyebrows too high. She looked surprised.",
    "I'm reading a book about anti-gravity. It's impossible to put down.",
    "I used to hate facial hair, but then it grew on me.",
    "Parallel lines have so much in common. It's a shame they'll never meet.",
    "I told my doctor I broke my arm in two places. He told me to stop going to those places.",
    "My wife told me to stop impersonating a flamingo. I had to put my foot down.",
    "I would tell you a construction joke, but I'm still working on it.",
    "I invented a new word today: plagiarism.",
    "I'm on a seafood diet. I see food and I eat it.",
    "Why don't skeletons fight each other? They don't have the guts.",
    "I asked my dog what's on top of the house. He said, 'Roof!'",
    "What do you call a fake noodle? An impasta.",
    "I couldn't figure out how to put my seatbelt on. Then it clicked.",
    "My boss told me to have a good day. So I went home.",
    "I only know 25 letters of the alphabet. I don't know y.",
    "What do you call a bear with no teeth? A gummy bear.",
    "I used to be a banker, but I lost interest.",
    "Why did the scarecrow win an award? He was outstanding in his field.",
    "I'm terrible at telling jokes. I always forget the punchline.",
    "What's brown and sticky? A stick.",
    "I told my computer I needed a break. Now it won't stop sending me KitKat ads.",
    "Why don't eggs tell jokes? They'd crack each other up.",
    "I stayed up all night wondering where the sun went. Then it dawned on me.",
    "What do you call a fish without eyes? A fsh.",
    "I have a joke about time travel, but you didn't like it.",
    "My friend says I'm addicted to brake fluid. I said I can stop any time.",
    "What did the ocean say to the shore? Nothing, it just waved.",
    "Why did the bicycle fall over? It was two tired.",
    "I cut my finger chopping cheese. I think I may have bigger problems.",
    "What do you call a sleeping dinosaur? A dino-snore.",
]

FACTS = [
    "Honey found in ancient Egyptian tombs is still edible; its low moisture and acidity keep bacteria out.",
    "Octopuses have three hearts: two pump blood to the gills, one to the rest of the body.",
    "A day on Venus lasts longer than its year - it rotates once every 243 Earth days but orbits in 225.",
    "Bananas are berries, but strawberries are not.",
    "Wombat droppings are cube-shaped, which stops them rolling away.",
    "The Eiffel Tower grows about 15 cm taller in summer as the iron expands.",
    "Scotland's national animal is the unicorn.",
    "Sharks existed before trees did, by roughly 50 million years.",
    "There are more possible games of chess than atoms in the observable universe.",
    "A group of flamingos is called a flamboyance.",
    "Oxford University is older than the Aztec Empire.",
    "The heart of a blue whale can weigh around 180 kg.",
    "Hot water can freeze faster than cold water under some conditions - the Mpemba effect.",
    "Cows have best friends and get stressed when separated from them.",
    "The Moon drifts about 3.8 cm further from Earth every year.",
    "A cloud can weigh more than a million kilograms and still float.",
    "Slugs have four noses.",
    "The shortest war in history lasted 38 minutes, between Britain and Zanzibar in 1896.",
    "Your body contains enough carbon to fill about 9,000 pencils.",
    "Sea otters hold hands while they sleep so they don't drift apart.",
    "Lightning strikes the Earth about 100 times every second.",
    "A jiffy is a real unit of time: one hundredth of a second.",
    "Butterflies taste with their feet.",
    "The Great Barrier Reef is the largest structure on Earth built by living organisms.",
    "There are more trees on Earth than stars in the Milky Way.",
    "Crows can remember human faces for years, and hold grudges.",
    "Humans share about 60% of their DNA with bananas.",
    "A snail can sleep for up to three years.",
    "The inventor of the Frisbee was turned into a Frisbee after he died.",
    "Antarctica is technically the world's largest desert.",
]

RIDDLES = [
    ("What has keys but can't open locks?", "A piano."),
    ("The more you take, the more you leave behind. What are they?", "Footsteps."),
    ("What has a head and a tail but no body?", "A coin."),
    ("I speak without a mouth and hear without ears. I have no body, but I come alive with wind. What am I?", "An echo."),
    ("What gets wetter the more it dries?", "A towel."),
    ("What can travel around the world while staying in a corner?", "A stamp."),
    ("What has hands but cannot clap?", "A clock."),
    ("What has many teeth but cannot bite?", "A comb."),
    ("What goes up but never comes down?", "Your age."),
    ("I'm tall when I'm young and short when I'm old. What am I?", "A candle."),
    ("What can fill a room but takes up no space?", "Light."),
    ("What has a neck but no head?", "A bottle."),
    ("The more of this there is, the less you see. What is it?", "Darkness."),
    ("What has one eye but cannot see?", "A needle."),
    ("What breaks once you say its name?", "Silence."),
    ("What comes down but never goes up?", "Rain."),
    ("I have cities but no houses, forests but no trees, and water but no fish. What am I?", "A map."),
    ("What can you hold in your left hand but not your right?", "Your right elbow."),
    ("What gets bigger the more you take away from it?", "A hole."),
    ("What runs but never walks, has a mouth but never talks?", "A river."),
    ("What has a thumb and four fingers but is not alive?", "A glove."),
    ("What is full of holes but still holds water?", "A sponge."),
    ("Forward I am heavy, backward I am not. What am I?", "The word 'ton'."),
    ("What is always in front of you but can't be seen?", "The future."),
    ("What has to be broken before you can use it?", "An egg."),
]

WOULD_YOU_RATHER = [
    "Would you rather always be ten minutes late or always twenty minutes early?",
    "Would you rather be able to talk to animals or speak every human language?",
    "Would you rather give up your phone for a month or your car for a month?",
    "Would you rather know the date of your death or the cause of it?",
    "Would you rather have unlimited free fuel for life or unlimited free flights?",
    "Would you rather live without music or without film and television?",
    "Would you rather be able to fly at walking speed or run at 200 km/h?",
    "Would you rather always know when someone is lying or always get away with lying?",
    "Would you rather restart your life at age ten keeping what you know, or jump ahead ten years with a million dollars?",
    "Would you rather never need to sleep or never need to eat?",
    "Would you rather be famous for something silly or unknown for something remarkable?",
    "Would you rather have a personal chef or a personal driver?",
    "Would you rather lose your sense of taste or your sense of smell?",
    "Would you rather be able to pause time or rewind it by ten seconds?",
    "Would you rather live somewhere always too hot or always too cold?",
    "Would you rather have a perfect memory or perfect aim?",
    "Would you rather work four ten-hour days or five eight-hour days?",
    "Would you rather be the funniest person in the room or the smartest?",
    "Would you rather give up coffee or give up sugar?",
    "Would you rather be able to breathe underwater or survive in space for an hour?",
    "Would you rather always say everything you think or never be able to speak again?",
    "Would you rather have a house with a workshop or a house with a big kitchen?",
    "Would you rather be able to drive any vehicle perfectly or ride any animal safely?",
    "Would you rather know how the world ends or when it ends?",
    "Would you rather never hit a red light again or never wait in a queue again?",
]


def get_joke() -> str | None:
    """A random one-liner-style joke (setup + punchline).

    Falls back to the local pool if the API is unreachable or returns
    something unusable, so a dead endpoint costs variety rather than the game.
    """
    try:
        d = _get_json("https://official-joke-api.appspot.com/random_joke")
        setup = _clean(d.get("setup"))
        punchline = _clean(d.get("punchline"))
        if setup and punchline:
            return f"{setup} {punchline}"
    except (OSError, ValueError, KeyError) as exc:
        _fell_back("joke", exc)
    return _draw("joke", JOKES)[0]


# ---- local fallback pools --------------------------------------------------
# Small next to a live API, but they are the difference between "here's a joke"
# and "couldn't fetch that" when an endpoint dies. Drawn in shuffled batches so
# the same three do not come round every night.

_fallback_used: dict[str, list] = {}


def _draw(name: str, pool: list, n: int = 1):
    """Take `n` items from `pool`, not repeating until it is exhausted."""
    used = _fallback_used.setdefault(name, [])
    room = [i for i in range(len(pool)) if i not in used]
    if len(room) < n:                        # been all the way round
        used.clear()
        room = list(range(len(pool)))
    picks = random.sample(room, n)
    used.extend(picks)
    return [pool[i] for i in picks]


_fallback_logged: set[str] = set()


def _fell_back(kind: str, exc: Exception | None = None):
    """Note the fallback once per pool.

    Not once per call: a dead endpoint would otherwise print a line on every
    command for as long as the bot runs, burying the log that is supposed to
    be telling you something is wrong.
    """
    if kind in _fallback_logged:
        return
    _fallback_logged.add(kind)
    why = f" ({exc!r})" if exc else ""
    print(f"[extras] {kind}: using the local pool until the API comes back"
          f"{why}", flush=True)


def get_random_fact() -> str | None:
    """A random (useless) fact — good filler for long stretches of road."""
    try:
        d = _get_json("https://uselessfacts.jsph.pl/api/v2/facts/random?language=en")
        text = _clean(d.get("text"))
        if text:
            return text
    except (OSError, ValueError, KeyError) as exc:
        _fell_back("randomfact", exc)
    return _draw("fact", FACTS)[0]


def get_riddle() -> tuple[str, str] | None:
    """Returns (riddle, answer). The bot shows the answer after a delay."""
    try:
        d = _get_json("https://riddles-api.vercel.app/random")
        riddle, answer = _clean(d.get("riddle")), _clean(d.get("answer"))
        if riddle and answer:
            return riddle, answer
    except (OSError, ValueError, KeyError) as exc:
        _fell_back("riddle", exc)
    return _draw("riddle", RIDDLES)[0]


def get_wyr() -> str | None:
    """A 'would you rather' question."""
    try:
        d = _get_json("https://api.truthordarebot.xyz/v1/wyr")
        question = _clean(d.get("question"))
        if question:
            return question
    except (OSError, ValueError, KeyError) as exc:
        _fell_back("wyr", exc)
    return _draw("wyr", WOULD_YOU_RATHER)[0]

# ---- shag / marry / kill ---------------------------------------------------
# The names live in names.py: a hand-picked seed pool plus Wikipedia category
# listings topped up in the background. This module just formats the round.

import names


def format_smk(picks) -> str:
    """[(name, job), ...] -> 'Rihanna (singer), Zendaya (actress), ...'"""
    return ", ".join(f"{name} ({job})" if job else name for name, job in picks)


def get_smk(gender: str = "any"):
    """Three (name, job) pairs for shag / marry / kill.

    `gender` is "female", "male" or "any" (mixed). Returns None only if the
    pool genuinely has fewer than three names, which the seed pool alone makes
    impossible - this is here so a caller never has to trust that.
    """
    return names.get_smk(gender)
