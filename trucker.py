"""Ambient trucker chatter for chat.

There is no API for this. What exists is static quote listicles - a few dozen
lines, almost all attributed "Unknown" - and CB slang glossaries, which are
reference dictionaries rather than generators. The glossaries are the better
raw material: they give an authentic vocabulary, and a slot generator built
from real terms produces far more variety than any fixed list of sentences.

Every term in the pools below is genuine CB usage. Nothing here is invented
lore dressed up as trucker talk.

Three registers, chosen at random so chat cannot learn the tone either:

* ``road``     - dry channel-19 traffic report. What CB actually sounds like.
* ``grizzled`` - an old-timer grumbling to himself. Personal, world-weary.
* ``ramble``   - unhinged stream of consciousness. The weird one.

The bot posts unprompted into a live channel, so the slang is held to "crude
but never explicit": the grittier truck-stop terms are fine, the four that
refer to sex work (``lot lizard``, ``sleeper creeper``, ``male buffalo``,
``pickle park``) are deliberately absent, and nothing here can get a channel
timed out.
"""

import random
import re
from typing import NamedTuple

__all__ = ["ramble", "combination_count", "registers", "REGISTERS", "LABELS",
           "Post"]

# --------------------------------------------------------------------------
# Vocabulary. Each entry is real CB/trucker slang.
# --------------------------------------------------------------------------

#: Handles the bot answers to on the radio. Rotated so it has a bit of a
#: personality without pretending to be a person.
HANDLES = (
    "Night Owl", "Chrome Dog", "Gravel Road", "Long Haul", "Dust Devil",
    "Red Ball", "Piston Pete", "Weigh Skipper", "Back Forty", "Granny Gear",
)

OPENERS = (
    "Breaker one-nine", "Breaker 1-9", "Hey driver", "C'mon, driver",
    "Back door", "Radio check", "Somethin' else", "Hey now",
    "10-4, driver", "Easy on it",
)

#: What the bot is reporting. All noun phrases, so they drop into any
#: template slot that wants a subject.
SIGHTINGS = (
    "a bear in the bushes", "Kojak with a Kodak", "a bear in the air",
    "an alligator in the right lane", "a chicken coop all locked up",
    "a smokin' scooter on the shoulder", "a cheese wagon", "a stagecoach",
    "bear bait up ahead", "a salt shaker", "a parking lot haulin' empty",
    "a skateboard with a hot load", "an Evel Knievel", "a mama-bear",
    "a baby bear learnin' the job", "three four-wheelers racin'",
    "a lumper arguin' over two boxes", "a CB Rambo on Sesame Street",
)

#: Prepositional phrases, so they follow a sighting cleanly.
WHERE = (
    "at the {yardstick} yardstick", "just past the Wally World",
    "off the {route}", "up at the split", "back of the grain elevator",
    "at the cash register", "comin' up on the Double Nickel",
    "at the {yardstick} mile marker", "past the nap trap",
    "in the back row at the truck stop",
)

#: Road conditions. Full clauses, always usable as a standalone statement.
CONDITIONS = (
    "road's greasy", "visibility's nothin'", "wind's pushin' the trailer",
    "they're checkin' ground pressure", "everybody's smokin' the brakes",
    "it's throwin' iron weather", "the granny lane is triple digits",
    "the coop's shut but they're movin' trucks anyway",
    "fog's sittin' in the cut", "the scale house is wide open",
    "that grade's a brake-eater", "four-wheelers are blowin' my doors off",
)

ADVICE = (
    "Ease off the loud pedal.", "Give it room, driver.",
    "Keep the shiny side up.", "Don't be a hero on that grade.",
    "Slow is smooth, smooth is fast.", "Keep the rubber side down.",
    "Stand on it and stay legal.", "Mind your donkey.",
    "Leave it in the granny lane and live.", "Watch your mirror.",
)

SIGNOFFS = (
    "Catch you on the flip flop.", "Stay loaded.", "I'm gone.", "10-4.",
    "Threes and eights.", "Keep the bugs off your glass.",
    "Shiny side up, driver.", "Hammer down.", "I'm 10 and on the side.",
    "Keep the dirty side down.", "Catch ya on the flip side.",
)

#: Grizzled-register grievances. Complete sentences, no slots needed for
#: most; the ones with slots are filled from the small numeric pools.
GRUMBLES = (
    "I'm 10-1 and gettin' worse, and this hundred-mile coffee ain't doin' a "
    "thing.",
    "Heater's a-glowin', manners are showin', and I still can't get the "
    "travel agent on the box.",
    "Been haulin' since go-go juice was a dollar a gallon.",
    "A lumper charged me {money} to touch two boxes.",
    "Payin' the water bill in a nap trap off the {route} at {hour} in the "
    "mornin' and callin' it a career.",
    "I got {miles} miles of nothin' but sky and a reefer that won't stop "
    "hummin'.",
    "Dispatcher wants it there yesterday. Yesterday already shipped.",
    "Somebody put a weigh station at the {yardstick} and called it progress.",
    "Radio's my co-pilot now and it don't argue about the thermostat.",
    "I been drawin' lines since before the logbook had a screen.",
    "The shiny side's up, the dirty side's down, and I still ain't home.",
    "Fuel's up, coffee's burnt, and the road off the {route} is the only "
    "boss I answer to.",
    "{miles} miles and the only thing that waved at me was a salt shaker.",
    "Got a bear bite last month and a drivin' award the month before. My "
    "wall's a scrapbook.",
    "Somebody at the {yardstick} yardstick asked me if I was tired. I have "
    "not been not tired since the {route} was a two-lane.",
    "I got {miles} miles left, a thermos that gave up an hour ago, and "
    "opinions about everybody's drivin'.",
)

#: The weird register. Deliberately odd images, still built from real slang.
RAMBLES = (
    "I got {sight1} cuttin' three lanes with no signal, {sight2} blowin' my "
    "doors off, and a bambi starin' at me from the treeline like it owes me "
    "money.",
    "Sesame Street's quiet, the road's greasy, and I'm throwin' iron in a "
    "parking lot at {hour}am askin' myself questions.",
    "Somebody tell that four-wheeler in the granny lane off the {route} that "
    "triple digits in a minivan ain't a personality.",
    "The reefer's hummin' in the key of G, the coffee's gone cold, and I'm "
    "pretty sure that lollipop's been watchin' me since the {yardstick}.",
    "I have decided the road is a long grey question and my truck, {miles} "
    "miles in, is a bad answer.",
    "Every yardstick out here looks like a yardstick that knows somethin', "
    "but the {yardstick} in particular has got an attitude.",
    "Got a chicken coop, a cash register and a bear den in {miles} miles and "
    "not one of 'em sells a decent sandwich.",
    "The bear in the bushes at the {yardstick} and me have an understandin'. "
    "He stays in the bushes.",
    # {freight} once, not twice: FREIGHT mixes mass nouns ("swingin' meat")
    # with count nouns that need an article ("a skateboard load"), so a second
    # "the {freight}" read as "the a skateboard load".
    "I'm haulin' {freight} and thinkin' about the load I hauled last week. "
    "That's the whole job.",
    "My handle's {handle} and I answer to nobody, including the dispatcher, "
    "especially the dispatcher.",
    "Radio check. I hear static, I hear a stagecoach, I hear {miles} miles "
    "of my own choices.",
    "Somethin' else - I just got passed by a cheese wagon at the {yardstick} "
    "and I have never felt more seen.",
    # No article of its own: SIGHTINGS entries already carry one ("a bear in
    # the bushes", "an alligator", "three four-wheelers"), so "a {sighting}"
    # produced "There is a a parking lot haulin' empty".
    "There's {sighting} out here somewhere and I have decided it is "
    "followin' me. We have been through {miles} miles together.",
    "I been on the {route} so long the white lines started lookin' like "
    "somethin' I was supposed to read.",
)

#: Things that can actually be *behind* you, for "on my donkey". Narrower
#: than SIGHTINGS on purpose: "a lumper arguin' over two boxes on my donkey"
#: is not a thing anybody has ever said on a radio.
BEHIND = (
    "a bear", "a mama-bear", "a baby bear learnin' the job", "an Evel Knievel",
    "a smokin' scooter", "a cheese wagon", "a stagecoach", "a salt shaker",
    "Kojak with a Kodak", "three four-wheelers racin'", "a bear in the air",
)

#: Opening bellow. Short and all-caps so the line reads as shouting without
#: the whole message being caps - Twitch automod treats walls of caps badly.
SHOUTS = (
    "HEY", "OI", "SOMEBODY", "EXCUSE ME", "PAL", "CHIEF", "BUDDY", "HEY NOW",
    "OI OI", "SAY",
)

#: What he is yelling at. Truckers call cars four-wheelers.
VEHICLES = (
    "that minivan", "that sedan", "that hatchback", "that crossover",
    "that convertible", "that pickup with nothin' in the bed",
    "that silver sedan", "that blue hatchback", "that little red car",
    "that SUV with the roof box", "that four-wheeler", "that estate",
)

#: What they did. Always mid-sentence, always lowercase: it follows "just"
#: or a subject in every template.
OFFENCES = (
    "cut across three lanes with no signal",
    "merged like the mirrors were decorative",
    "braked for absolutely no reason",
    "sat in the fast lane at 55",
    "sped up while I was passin' him",
    "turned left out of the right lane",
    "drifted over the line twice",
    "rolled through that stop sign like it was a suggestion",
    "pulled out in front of eighty thousand pounds",
    "hit the brakes on a green light",
    "took the exit from the middle lane",
    "texted all the way up the on-ramp",
    "waved me on and then went himself",
    "found the middle of the road and stayed there",
)

#: The follow-up. Exasperated, never a threat - the joke is that he is
#: furious and completely powerless to do anything about it.
QUIPS = (
    "The signal lever is on the left. It does somethin'.",
    "I got eighty thousand pounds and you got a latte. Pick one.",
    "Two little red lights. That's all I'm askin' for.",
    "I have seen better lane discipline from a shopping trolley.",
    "Mirrors are not decoration, pal.",
    "Somewhere a drivin' instructor is sittin' down.",
    "That's a whole personality right there.",
    "The granny lane is free. It is empty. It is right there.",
    "I don't get paid for this and I don't get paid at all.",
    "Somebody out here is havin' a worse day than me.",
    "My reefer drives straighter than that.",
    "Who taught you that? Was it a shopping car park?",
)

ORDINALS = ("second", "third", "fourth", "fifth", "sixth")

FREIGHT = (
    "swingin' meat", "a skateboard load", "chicken lights and hope",
    "nothin' but air and a deadline", "doubles that won't track straight",
    "paper towels, all of them", "somebody's entire house",
)

#: Small numeric pools. Kept separate so templates can share them and so the
#: combination count stays honest.
YARDSTICKS = tuple(str(n) for n in (
    12, 19, 27, 42, 55, 63, 78, 84, 96, 104, 111, 128, 143, 160, 187, 202,
))
ROUTES = tuple(str(n) for n in (
    10, 40, 55, 65, 70, 75, 80, 81, 90, 94, 95, 285, 295, 395,
))
MONEY = tuple(f"${n}" for n in (
    20, 30, 40, 50, 60, 75, 80, 100, 120, 150,
))
MILES = tuple(str(n) for n in (
    40, 65, 90, 120, 180, 240, 310, 400, 475, 550,
))
HOURS = tuple(str(n) for n in (1, 2, 3, 4, 5))

_POOLS = {
    "opener": OPENERS,
    "handle": HANDLES,
    "ramble": RAMBLES,
    "sighting": SIGHTINGS,
    "where": WHERE,
    "condition": CONDITIONS,
    "advice": ADVICE,
    "signoff": SIGNOFFS,
    "yardstick": YARDSTICKS,
    "route": ROUTES,
    "money": MONEY,
    "miles": MILES,
    "hour": HOURS,
    "freight": FREIGHT,
    "sight1": SIGHTINGS,
    "sight2": SIGHTINGS,
    "grumble": GRUMBLES,
    "behind": BEHIND,
    "shout": SHOUTS,
    "vehicle": VEHICLES,
    "offence": OFFENCES,
    "quip": QUIPS,
    "ordinal": ORDINALS,
}

# --------------------------------------------------------------------------
# Templates. Each is a complete sentence pattern; slots are type-matched to
# the pool above so the result always reads as grammar.
# --------------------------------------------------------------------------

ROAD_TEMPLATES = (
    "{opener}, we got {sighting} {where}. {advice}",
    "{opener} - {condition} {where}, and {sighting}. {signoff}",
    "{opener}, {condition}. {advice} {signoff}",
    "{opener}, {sighting} {where}. {advice} {signoff}",
    "{opener}: {condition}, {sighting} {where}. {signoff}",
    "{opener}, this is {handle}. {sighting} {where} - {advice}",
    "{opener}, {condition} and {sighting} {where}. Stand by. {signoff}",
    "{opener}, I got {behind} on my donkey. {advice}",
    "{opener} - {condition}. {signoff}",
    "{opener}, {sighting} {where} and they're checkin' ground pressure. "
    "{advice}",
)

GRIZZLED_TEMPLATES = (
    "{grumble} {signoff}",
    "{grumble} {advice}",
    "{handle} here. {grumble}",
    "{grumble} And that's the job. {signoff}",
    "{grumble} {condition}, too. {signoff}",
    "Radio check, this is {handle}. {grumble}",
    "{grumble} {sighting} {where}, of course. {signoff}",
    "{grumble} Somebody put on the hundred-mile coffee - I'm "
    "{miles} miles from anywhere and the pot's been on since the "
    "{yardstick}.",
    "{grumble} I'll be at the {yardstick} yardstick if anybody needs me.",
    "{grumble} {signoff}",
)

RAMBLE_TEMPLATES = (
    "{ramble} {signoff}",
    "Radio check. {ramble}",
    "{handle} on the box - {ramble}",
    "Somethin' else. {ramble} {signoff}",
    "{ramble} {advice}",
    "I'm 10 and on the side. {ramble}",
    "Breaker one-nine. {ramble} {signoff}",
    "{ramble} {condition}. {signoff}",
)

YELL_TEMPLATES = (
    "{shout} - {vehicle} just {offence}. {quip}",
    "{shout}! {vehicle} {offence} like the road was his driveway. {quip}",
    "{shout} - {vehicle} {offence}. {quip}",
    "I got {miles} miles left and {vehicle} just {offence}. {quip}",
    "{shout}, {vehicle} - {quip}",
    # VEHICLES entries begin with "that" and OFFENCES are past tense, so this
    # reads "{vehicle} {offence}. That's the third one..." rather than
    # "the third that minivan to cut across", which is neither.
    "{shout}! {vehicle} {offence}. That's the {ordinal} one since the "
    "{yardstick}. {quip}",
    "{shout} - {vehicle} {offence}, and I'm a very patient man. {quip}",
    "{shout}! {vehicle} {offence}. {quip} {signoff}",
)

#: The four voices, keyed by the names `ramble(register=...)` accepts.
REGISTERS = {"road": ROAD_TEMPLATES,
             "grizzled": GRIZZLED_TEMPLATES,
             "ramble": RAMBLE_TEMPLATES,
             "yell": YELL_TEMPLATES}

#: What chat sees in front of the line, so it is never ambiguous whether the
#: bot is on the radio or hanging out of the window at a car.
LABELS = {"road": "CB", "grizzled": "CB", "ramble": "CB", "yell": "WINDOW"}

_SLOT_RE = re.compile(r"\{(\w+)\}")
_SENTENCE_START = re.compile(r"(?<=[.!?] )[a-z]")

class Post(NamedTuple):
    """One line plus the label that says what kind of line it is.

    Returned together so a caller cannot post a WINDOW line under a CB label
    by re-deriving the register and getting a different draw.
    """

    label: str
    text: str


# Recent-line memory, so an ambient post never repeats itself back to back.
_HISTORY: list[str] = []
_HISTORY_MAX = 40


def registers() -> tuple[str, ...]:
    """The three registers, in the order they are documented."""
    return tuple(REGISTERS)


def _fill(text: str) -> str:
    """Replace every ``{slot}`` with a random draw from its pool.

    Repeated slots in one template resolve to the same value, which is what
    makes ``{sight1}``/``{sight2}`` distinct pools rather than one pool drawn
    twice.
    """
    seen: dict[str, str] = {}

    class _Dict(dict):
        def __missing__(self, key: str) -> str:
            if key not in seen:
                seen[key] = random.choice(_POOLS[key])
            return seen[key]

    # Pools nest: a WHERE entry is "at the {yardstick} yardstick", an OPENER
    # is "Breaker one-nine, this is {handle}", and a GRUMBLE carries {money}.
    # format_map does not recurse, so loop until nothing is left. Guarded so a
    # malformed pool cannot spin forever.
    for _ in range(6):
        if not _SLOT_RE.search(text):
            break
        text = text.format_map(_Dict())
    # Pools are deliberately lowercase so they read correctly mid-sentence
    # ("we got a bear in the bushes"). Capitalise any that ended up after a
    # full stop. Done here rather than in the pools, which are shared between
    # sentence-start and mid-sentence slots.
    return _SENTENCE_START.sub(lambda m: m.group(0).upper(), text)


def _ways(text: str) -> int:
    """Distinct results for one template, counting slots inside slot values.

    Counting ``len(pool)`` would understate it: ``{where}`` has 10 entries but
    each expands again through ``{yardstick}``/``{route}``.
    """
    slots = set(_SLOT_RE.findall(text))
    if not slots:
        return 1
    ways = 1
    for slot in slots:
        ways *= sum(_ways(v) for v in _POOLS[slot])
    return ways


def combination_count() -> int:
    """Exact count of distinct lines the generator can produce.

    Multiplicative across slots within a template, summed over templates and
    registers. Reported in the log so "never the same line twice" is a number
    rather than a claim.
    """
    return sum(_ways(t) for ts in REGISTERS.values() for t in ts)


def ramble(register: str | None = None,
           exclude: tuple[str, ...] = ()) -> Post:
    """One line of trucker chatter, plus the label it should be posted under.

    ``register`` picks a specific voice. Without it the voice is chosen at
    random, so chat cannot predict the tone any more than the timing.
    ``exclude`` drops voices the current config does not want - the car
    yelling has its own switch, independent of the CB chatter.
    """
    if register is not None:
        if register not in REGISTERS:
            raise ValueError(f"unknown register {register!r}")
        names = [register]
    else:
        names = [n for n in REGISTERS if n not in exclude]
        if not names:
            raise ValueError("every register was excluded")
    for _ in range(12):
        key = random.choice(names)
        line = _fill(random.choice(REGISTERS[key])).strip()
        if line not in _HISTORY:
            break
    _HISTORY.append(line)
    if len(_HISTORY) > _HISTORY_MAX:
        del _HISTORY[0]
    return Post(LABELS[key], line)
