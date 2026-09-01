"""!beef - a three-act feud between whoever typed the command and a rival.

Deliberately template-driven rather than a model call per command, for three
reasons the rest of this bot already learned:

* Latency. A model round trip is seconds; a chat command that makes people
  wait is a chat command they stop using. trucker.py produces 12 million
  distinct lines without one.
* Cost and quota. Every beef would spend OpenRouter credits, and an account
  with none returns 402 and disables the LLM for an hour - which would take
  the fun facts down with it.
* Control. A model writing fiction about two named people in someone's chat
  can produce something genuinely hurtful, and there is no filter for that
  downstream. Every line here is a line a person read first.

The winner is decided BEFORE the text is assembled, the same way the blueprint
proposed - so the story always lands on the right outcome instead of the model
being asked to retrofit one.

!revenge is a retelling of the same engine (feud(revenge=True)), not a second
one: a rematch headline, a rematch opener, a fresh 50/50 roll. Its window and
the leaderboard live in beefstats.py, which is as local as this file - the
game never waits on a model, spends a credit, or breaks when the LLM is down.
"""

import random
import re

__all__ = ["beef", "feud", "GENRES", "combination_count", "genre_for", "LABEL",
           "REMATCH_SPARKS"]

LABEL = "BEEF"

#: Named rivals. A feud needs a second party, and pulling a real viewer in
#: without them opting in is how a fun command becomes a harassment report -
#: so the default opponent is one of these.
RIVALS = {
    "trucking": ("Diesel Dan", "The Reefer Phantom", "Lot Lizard Larry",
                 "Scale-House Steve", "Fourteen-Wheel Wendy"),
    "zwift": ("The Watt-Bomber", "FTP Ferdinand", "Draft- dodger Dee",
             "Kom Kraken", "The Alpe Assassin"),
    "fortnite": ("Bush-Camper Barry", "The No-Skin Menace", "Crankin' Kevin",
                 "Storm Chaser Sal", "The Loot Goblin"),
    "baking": ("Buttercream Brenda", "The Soggy Bottom", "Proving-Time Pete",
              "Fondant Fiona", "The Showstopper"),
    "robots": ("Immolator 9000", "Hydraulic Hazel", "The Pit Crusher",
              "Spark-Gap Sidney", "Torque Wrench Tina"),
    "karaoke": ("Falsetto Phil", "The Key-Change Kid", "Ballad Brenda",
               "Mic-Drop Marcus", "The Autotune Menace"),
}

GENRES = {
    "trucking": {
        "setting": "the interstate at 3am, somewhere outside Kearney",
        "sparks": (
            "{b} cut {a} off at the scale house and did not even wave.",
            "{a} found {b} parked in the only shower slot at the Petro.",
            "{b} claimed {a}'s parking spot and left a note saying 'thanks, buddy'.",
            "{a} reported {b} for a logbook that was clearly written in crayon.",
            "{b} hogged the CB channel with a forty-minute podcast.",
            "{a} took the last pork sandwich at the rolling store.",
        ),
        "escalations": (
            "{a} upgraded to an industrial-grade roof fairing and started drafting semis for fun.",
            "{b} retuned the Jake brake to play the opening of 'Convoy' at 60 decibels.",
            "{a} started running doubles and bragging about it on the CB.",
            "{b} filed a complaint with the DOT, then filed another one about the first one.",
            "{a} relocated the entire rig into a Walmart lot to win on technicalities.",
            "{b} filled the reefer with nothing but glitter and parked it downhill.",
        ),
        "climaxes": (
            "It ended at the truck stop at dawn, with {w} holding the last cup of coffee and {l} out of quarters.",
            "{w} won the convoy vote 41 to 3. {l} was not informed until the group chat went quiet.",
            "A scale-house official settled it with a tape measure. {w} won by a mudflap.",
            "{w} took it on the final leg. {l} is still arguing about it in the group chat.",
            "It came down to who could parallel-park a 53-footer in one attempt. {w} could. {l} hit a bollard.",
        ),
    },
    "zwift": {
        "setting": "the virtual mountains of Watopia",
        "sparks": (
            "{b} drafted past {a} on the Alpe and spammed the 'Close the Gap' button.",
            "{a} accused {b} of riding a smart trainer calibrated with a car battery.",
            "{b} squirted a digital water bottle at {a}'s avatar and rode off laughing.",
            "{a} reported {b} for a 900-watt FTP that no human has ever held.",
            "{b} took {a}'s KOM and renamed the segment something unrepeatable.",
            "{a} caught {b} riding in a full skinsuit in a 30-degree living room.",
        ),
        "escalations": (
            "{a} moved the entire indoor setup into a walk-in freezer for crisp cold oxygen.",
            "{b} hacked the weather server to make it rain exclusively on {a}'s avatar.",
            "{a} started lubing the chain with something that is definitely not chain lube.",
            "{b} bought a second smart trainer just so they could ride two at once.",
            "{a} recruited a paceline of six strangers and refused to name them.",
            "{b} increased virtual rolling resistance by 40% and called it 'character building'.",
        ),
        "climaxes": (
            "It came down to a sprint up the volcano. {w} took it by a front tyre-width while {l} cracked a pedal clean off.",
            "{w} won on the final climb. {l} disconnected ten metres from the line.",
            "The league settled it on time trial. {w} by four seconds. {l} has since changed their username.",
            "{w} crossed first and held the broken pedal aloft. {l} rage-quit the server.",
            "It ended at the Watopia arch with {w} still pedalling and {l} arguing with the results screen.",
        ),
    },
    "fortnite": {
        "setting": "a lobby with far too many people in it",
        "sparks": (
            "{b} stole {a}'s legendary chest and did a dance on top of it.",
            "{a} caught {b} bush-camping in the same bush for eleven minutes.",
            "{b} took the last medkit while {a} was down and knocked.",
            "{a} accused {b} of cranking 90s purely to be annoying about it.",
            "{b} emoted directly onto {a}'s elimination card.",
            "{a} reported {b} for using a controller while claiming keyboard and mouse.",
        ),
        "escalations": (
            "{a} started carrying nothing but a pump shotgun and spite.",
            "{b} built a tower so tall the render distance gave up.",
            "{a} began landing at the same spot as {b} every single match.",
            "{b} recruited the entire squad to ignore {a} on purpose.",
            "{a} played eleven hours straight and refused to admit it.",
            "{b} switched to zero-build mode just to make it personal.",
        ),
        "climaxes": (
            "It ended in a 1v1 at Tilted. {w} took the Victory Royale with one health left. {l} threw their controller.",
            "{w} won the final circle. {l} was still building and had not noticed the storm.",
            "The lobby voted. {w} by a landslide. {l} has been muted by four people since.",
            "{w} closed it out with a pump shotgun from across the map. {l} is contesting the result.",
            "It came down to last mats. {w} had two. {l} had none and a plan.",
        ),
    },
    "baking": {
        "setting": "a high-stakes tent with one oven and no air conditioning",
        "sparks": (
            "{b} used the last of the vanilla and put the empty bottle back.",
            "{a} found {b}'s showstopper in the fridge, labelled 'DO NOT TOUCH', and touched it.",
            "{b} claimed {a}'s buttercream was bought in a tub.",
            "{a} reported {b} for a soggy bottom two weeks running.",
            "{b} set the proving drawer to a temperature no yeast has ever survived.",
            "{a} ate {b}'s technical challenge entry and blamed the judges.",
        ),
        "escalations": (
            "{a} started laminating dough at 5am to get a head start and a grudge.",
            "{b} replaced {a}'s caster sugar with something crystalline and unhelpful.",
            "{a} brought a second oven in a wheelbarrow.",
            "{b} entered three showstoppers and dared the judges to pick.",
            "{a} piped an entire cathedral out of royal icing, purely to make a point.",
            "{b} proved their dough in a car on a hot day and called it technique.",
        ),
        "climaxes": (
            "The judges deliberated for nine minutes. {w} took the star baker apron; {l} went home with a collapsed sponge.",
            "{w} won on the showstopper. {l}'s ganache split in front of everyone.",
            "It came down to the technical. {w} ranked first. {l} ranked last and blamed the oven.",
            "{w} took the tent. {l} left carrying a cake that had leaned visibly to the left.",
            "The final was decided by a single macaron foot. {w} had one. {l} did not.",
        ),
    },
    "robots": {
        "setting": "an underground arena with questionable insurance",
        "sparks": (
            "{b} reversed into {a}'s robot in the pit and called it a calibration test.",
            "{a} accused {b} of entering the same robot under three different names.",
            "{b} welded something onto {a}'s chassis while they were getting a sandwich.",
            "{a} reported {b}'s weapon as illegal in two separate weight classes.",
            "{b} drained {a}'s battery and left a note that said 'charge it'.",
            "{a} caught {b} testing in the arena during someone else's match.",
        ),
        "escalations": (
            "{a} rebuilt the drive train overnight using parts of uncertain provenance.",
            "{b} added a second spinner, mounted sideways, for no tactical reason.",
            "{a} started armour-plating everything, including the remote.",
            "{b} wired the hydraulics to a car battery and stopped answering questions about it.",
            "{a} entered a heavyweight with a lightweight's paperwork.",
            "{b} replaced the tyres with something that was definitely not a tyre.",
        ),
        "climaxes": (
            "The final ran to a judges' decision. {w} by a clear margin. {l} was on fire, literally.",
            "{w} won by knockout in eleven seconds. {l}'s weapon arm is still in the ceiling.",
            "It came down to the pit. {w} rolled out. {l} was carried out.",
            "{w} took the belt. {l} has appealed to a committee that does not exist.",
            "The arena settled it on a count-out. {w} was mobile. {l} was a pile.",
        ),
    },
    "karaoke": {
        "setting": "a pub back room with one microphone and no forgiveness",
        "sparks": (
            "{b} took {a}'s song and sang it an octave higher on purpose.",
            "{a} reported {b} for using the autotune setting and denying it.",
            "{b} talked over {a}'s ballad to order a drink.",
            "{a} changed {b}'s key mid-song from the sound desk.",
            "{b} dedicated a breakup song to {a}, in front of {a}.",
            "{a} booked the last three slots of the night and sang all of them.",
        ),
        "escalations": (
            "{a} brought a backing vocalist, a tambourine, and a grudge.",
            "{b} learned the entire second verse, which nobody does.",
            "{a} started doing key changes on songs that do not have them.",
            "{b} arranged a four-part harmony with three strangers from the bar.",
            "{a} performed the whole set in character and refused to break.",
            "{b} requested the longest song in the book, twice.",
        ),
        "climaxes": (
            "The room voted by noise. {w} won by a margin you could feel in the floor. {l} left during the applause.",
            "{w} took the night on a final falsetto. {l} cracked on the high note and knows it.",
            "The landlord settled it by buying {w} a drink. {l} got the microphone cord tangled.",
            "{w} won the encore. {l} was cut off mid-bridge, which the landlord later admitted was deliberate.",
            "It ended with {w} holding the mic and {l} holding a coaster, arguing about pitch.",
        ),
    },
}

#: Genre aliases, so 'zwifting' and 'fortnite br' both land somewhere sensible.
_ALIASES = {
    "trucking": ("truck", "trucker", "ets2", "ats", "convoy", "lorry", "semis"),
    "zwift": ("zwifting", "cycling", "bike", "riding", "indoor cycling", "ftp"),
    "fortnite": ("fort", "br", "battle royale", "lobby"),
    "baking": ("bake", "bakeoff", "bake off", "cake", "pastry", "bake-off"),
    "robots": ("robot", "robot fighting", "battlebots", "bots"),
    "karaoke": ("singing", "song", "mic", "open mic"),
}

#: Rematch openers - Act 1 of a !revenge story. Genre-agnostic on purpose: a
#: slammed table is a slammed table in any setting, and per-genre rematch
#: pools would double the text for no new joke. Acts 2 and 3 stay native to
#: the genre the original feud was fought in.
REMATCH_SPARKS = (
    "{a} demanded a rematch on the spot, and the room went quiet.",
    "{b} said 'best of three' before {a} even finished asking.",
    "{a} flipped the table, set it back up, and demanded it again.",
    "{b} agreed to run it back, purely to watch {a} lose twice.",
    "{a} queued the rematch with shaking hands and total confidence.",
    "The rematch was scheduled for right now. {b} did not object, which worried everyone.",
)

_NAME_OK = re.compile(r"^[A-Za-z0-9_]{2,25}$")


def genre_for(text: str) -> str:
    """Map a typed genre to one we have, defaulting to a random one."""
    t = " ".join((text or "").split()).lower().strip()
    if not t:
        return random.choice(sorted(GENRES))
    if t in GENRES:
        return t
    for genre, words in _ALIASES.items():
        if t in words or any(w in t for w in words):
            return genre
    return random.choice(sorted(GENRES))


def _poss(name: str) -> str:
    """Possessive form, without producing 'Hardclaws's'."""
    n = (name or "").strip()
    if not n:
        return n
    return n + "'" if n.lower().endswith("s") else n + "'s"


def _valid(name: str) -> bool:
    """A display name we can safely put in a sentence and a mention."""
    return bool(_NAME_OK.match((name or "").strip()))


def combination_count() -> int:
    """Distinct stories, before names go in.

    Act 1 is drawn from the genre's sparks or, for a rematch, the shared
    rematch openers - both pools count, so revenge doubles the openers rather
    than reusing them.
    """
    total = 0
    for genre, pools in GENRES.items():
        total += ((len(pools["sparks"]) + len(REMATCH_SPARKS))
                  * len(pools["escalations"])
                  * len(pools["climaxes"]) * len(RIVALS[genre]) * 2)
    return total


def feud(issuer: str, rival: str = "", genre: str = "", revenge: bool = False,
         tag: bool = False) -> dict | None:
    """Tell one feud and report what happened, or None if names are unusable.

    This is the engine behind beef(): same templates, same winner-first roll,
    but the caller gets the outcome too - the leaderboard and the !revenge
    window need to know who won, and parsing it back out of the English was
    how the last self-inflicted bug happened.

    revenge=True retells it as a rematch: a REMATCH headline and a rematch
    opener in Act 1, everything else native to the genre. The roll stays
    50/50 - a rematch you were guaranteed to win would not be worth points.

    tag=True renders the rival as @name in the headline only. The caller
    decides who deserves a ping; this module just formats it.
    """
    issuer = (issuer or "").strip()
    if not _valid(issuer):
        return None

    genre_key = genre_for(genre)
    pools = GENRES[genre_key]

    rival = (rival or "").strip()
    # "random" is a keyword, not somebody's display name. _reply_beef strips
    # it before calling, but this is the public entry point and a rival
    # literally called "random" is not what anyone meant.
    if rival.lower() in ("random", "rand", "?"):
        rival = ""
    if not _valid(rival) or rival.lower() == issuer.lower():
        # No rival given, or they asked to beef with themselves. Either way the
        # opponent is one of the named characters - not another viewer, who did
        # not opt in, and not the issuer, whose escalation lines would read as
        # one person doing both halves.
        others = [r for r in RIVALS[genre_key] if r.lower() != issuer.lower()]
        rival = random.choice(others)

    # Winner first, story second - otherwise the ending has to be retrofitted
    # onto whatever the templates happened to produce.
    issuer_wins = random.random() < 0.5
    winner, loser = (issuer, rival) if issuer_wins else (rival, issuer)

    def fill(text):
        # Possessives first, so a name ending in s does not become
        # "Hardclaws's buttercream".
        text = (text.replace("{a}'s", _poss(issuer))
                    .replace("{b}'s", _poss(rival))
                    .replace("{w}'s", _poss(winner))
                    .replace("{l}'s", _poss(loser)))
        return text.format(a=issuer, b=rival, w=winner, l=loser)

    label = "REMATCH" if revenge else "BEEF"
    head_rival = f"@{rival}" if tag else rival
    head = (f"\U0001f525 {label}: {issuer} vs. {head_rival} "
            f"\u2014 {pools['setting']} \U0001f525")
    opener = random.choice(REMATCH_SPARKS if revenge else pools["sparks"])
    spark = fill(opener)
    escalation = fill(random.choice(pools["escalations"]))
    climax = fill(random.choice(pools["climaxes"]))
    verdict = f"\U0001f3c6 WINNER: {winner}. {loser} has left the building."

    return {
        "lines": [
            head,
            f"Act 1 \u2014 {spark}",
            f"Act 2 \u2014 {escalation}",
            f"Act 3 \u2014 {climax} {verdict}",
        ],
        "issuer": issuer,
        "rival": rival,
        "genre": genre_key,
        "winner": winner,
        "loser": loser,
        "issuer_won": issuer_wins,
    }


def beef(issuer: str, rival: str = "", genre: str = "") -> list:
    """Three chat lines telling a feud, or [] if the names are unusable.

    The issuer is whoever typed the command - that is the whole point of the
    game, and it is a person choosing to put themselves in. The rival defaults
    to one of the named characters rather than another viewer, because pulling
    a stranger into a public feud they did not ask for is not a joke to them.

    Returns a list of lines rather than one string: the story is 600+
    characters and a Twitch message is not. The caller queues them.

    Callers that need the outcome (stats, !revenge) call feud() instead; this
    stays for every caller that only wants the story.
    """
    result = feud(issuer, rival, genre)
    return result["lines"] if result else []
