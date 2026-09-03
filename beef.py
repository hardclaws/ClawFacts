"""!beef - a feud between whoever typed the command and a rival, told in
five clean lines.

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

A story is five messages, not four: headline, three acts, then the verdict on
its own line behind the longest pause. The caller spaces them out (see
TwitchBot._schedule_beef) - five messages in one burst is a wall, and a wall
gets scrolled past, not read.

The acts are two beats, not one: an incident and a quote, an escalation and a
crowd reaction. A single bare sentence per act is why the first cut read
"boring and always the same" - narration alone has no voice in it, and with
six lines per pool the repeats came fast. Quotes, crowd lines, stakes,
fates and act tags are all separate pools now, and _pick() refuses to re-draw
a line it used in the last few stories, so back-to-back beefs stop echoing
each other.

!revenge is a retelling of the same engine (feud(revenge=True)), not a second
one: a rematch headline, a rematch opener, a fresh 50/50 roll. Its window and
the leaderboard live in beefstats.py, which is as local as this file - the
game never waits on a model, spends a credit, or breaks when the LLM is down.
"""

import random
import re
from collections import deque

__all__ = ["beef", "feud", "GENRES", "combination_count", "genre_for",
           "VERDICTS",
           "match_genre", "LABEL", "REMATCH_SPARKS", "FATES", "STAKES"]

LABEL = "BEEF"

#: Named rivals. A feud needs a second party, and pulling a real viewer in
#: without them opting in is how a fun command becomes a harassment report -
#: so the default opponent is one of these.
RIVALS = {
    "trucking": ("Diesel Dan", "The Reefer Phantom", "Lot Lizard Larry",
                 "Scale-House Steve", "Fourteen-Wheel Wendy"),
    "zwift": ("The Watt-Bomber", "FTP Ferdinand", "Draft-Dodger Dee",
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

#: How the verdict names the winner - spoken, not labelled. "WINNER:" in
#: caps was the last piece of form-speak left in the story.
VERDICTS = (
    "takes it",
    "wins it",
    "takes the W",
    "walks away with this one",
    "settles it",
)

#: What the loser gets. "Has left the building" every single time was part of
#: why the stories felt stamped out - the exit is a punchline slot, and it
#: was always the same punchline.
FATES = (
    "{l} has left the building.",
    "{l} has changed their username.",
    "{l} is demanding a recount from the car park.",
    "{l} walked into the sunset backwards, maintaining eye contact.",
    "{l} has gone to find themselves. They checked the fridge first.",
    "{l} is blaming the equipment.",
    "{l} left mid-sentence and muted the group chat.",
    "{l} says the result is 'under review'.",
    "{l} has taken a break from this channel, and from greatness.",
)

#: Headline stakes - the tail of line one, when it carries one.
STAKES = (
    "Winner keeps the parking spot.",
    "Loser mutes the group chat.",
    "No apologies will be issued.",
    "Two enter, one leaves.",
    "The rubber meets the rumble strip.",
    "Somebody's deleting their account tonight.",
    "Winner gets the last word. Loser gets a footnote.",
    "This is sanctioned. Barely.",
)

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
    "{a} walked back in with the same shirt and a new plan. The plan did not survive.",
    "Both of them said 'run it back' at the same second. The room gasped.",
)

GENRES = {
    "trucking": {
        "settings": (
            "the interstate at 3am, somewhere outside Kearney",
            "the Flying J in North Platte, idling room only",
            "a scale-house lot with one exit and no witnesses",
        ),
        "sparks": (
            "{b} blocked {a}'s dock exit for forty minutes and called it 'safety'.",
            "{a} caught {b} siphoning DEF into a coffee thermos like it was normal.",
            "{b} keyed 'LEARN TO PARK' into {a}'s trailer door backwards, so {a} could read it in the mirror.",
            "{a} reported {b}'s logbook, which was filled out in three pens and one crayon.",
            "{b} took the last open pump, then went inside for a half-hour shower.",
            "{a}'s freight arrived smelling like {b}'s live-haul chickens. The chickens said nothing.",
            "{b} rode {a}'s bumper down Donner Pass with the high beams on strobe.",
            "{a} found {b} asleep in their cab. Wearing their jacket. Using their pillow.",
            "{b} called {a} 'dispatch's little angel' on channel 19, twice, slowly.",
            "{a} watched {b} drop a trailer on the scale and blame a gust of wind nobody else felt.",
        ),
        "quotes": (
            "{a} told the fuel desk, loud enough for both rows: \"{b} parks like a drunk crane.\"",
            "\"{b} drives like brakes are optional and the schedule is a rumor,\" {a} told the counter.",
            "{a} keyed up on 19: \"Breaker one-nine, anybody seen where {b} left their dignity? Over.\"",
            "\"I've seen smoother backing from a shopping cart,\" {b} said, about {a}, to a witness.",
            "{a}, directly to {b}: \"Your truck sounds like a blender full of wrenches.\"",
            "\"Fourteen hours driving and {b} still can't find the fuel tank,\" {a} told the line at the register.",
        ),
        "escalations": (
            "{a} started routing every load past {b}'s favourite stop at 2am, slowly, Jake brake singing.",
            "{b} filed a DOT complaint, then a second complaint about how the first one was handled.",
            "{a} relocated the entire rig into a Walmart lot to win on technicalities.",
            "{b} filled {a}'s DEF tank with glitter. The engine now runs mostly on spite.",
            "{a} wired up a linear amp and narrated {b}'s mistakes to four states.",
            "{b} printed actual paper flyers of {a}'s worst backing job. Title: EXHIBIT A.",
            "{a} hired a flag man whose only job was to hold a coffee and shake his head at {b}.",
            "{b} started a podcast about the feud. Episode one is forty minutes and names names.",
            "{a} practised parallel parking between shopping carts at 3am. There are witnesses.",
            "{b} got the scale house to weigh {a} twice, and the second time said 'for fun'.",
        ),
        "crowd": (
            "The fuel island is picking sides.",
            "Channel 19 has never been this alive.",
            "Somebody's selling tickets by the diesel pump.",
            "Two regulars have already made shirts.",
            "The night dispatcher has taken bets and a side.",
        ),
        "climaxes": (
            "They settled it at dawn in the lot: one backing shot each at the tightest dock. {w} dropped it clean first try; {l} is still circling the row with a spotlight on.",
            "The scale house called it: engines off, one weigh-in. {w} came in legal by forty pounds; {l} came in four thousand over and philosophical.",
            "A fuel-off on the frontage road, last rig rolling wins. {l}'s engine quit at the two-mile flag; {w} never even downshifted.",
            "{w} took it on the logbook audit: clean sheets, no gaps. {l} had a nine-hour gap labelled 'traffic (emotional)'.",
            "Convoy vote at the J: 41 to 3 for {w}. {l} is demanding a recount from a moving vehicle.",
            "It ended on the last open pump of a Friday night. {w} slid in clean; {l} waited forty minutes and grew as a person, slightly.",
        ),
    },
    "zwift": {
        "settings": (
            "the virtual mountains of Watopia",
            "a garage in Ohio with one fan and zero mercy",
            "the Alpe du Zwift, all twenty-one hairpins of it",
        ),
        "sparks": (
            "{b} squirted a digital water bottle at {a}'s avatar and rode off laughing.",
            "{a} reported {b} for a 900-watt FTP that no human has ever held.",
            "{b} took {a}'s KOM and renamed the segment '{a} Is Like This At Home Too'.",
            "{a} caught {b} racing D category in a skinsuit worth more than a car.",
            "{b} brake-checked {a} on the Alpe descent. In a video game. On purpose.",
            "{a}'s garage door opened mid-race and {b} clipped the laundry for posterity.",
            "{b} unfollowed {a}, then refollowed, purely to thumbs-down the ride.",
            "{a} watched {b}'s watts jump two hundred in one second and said nothing, loudly.",
            "{b} joined {a}'s recovery group ride and attacked it at kilometre zero.",
            "{a} accused {b} of weighing the bike in grams of soul it does not have.",
        ),
        "quotes": (
            "{a} in the race chat: \"Some of us train. Some of us buy watts.\"",
            "\"{b} drafts like a wedding chauffeur,\" {a} told the group ride.",
            "{b}, on stream, about {a}: \"That FTP has more padding than the saddle.\"",
            "\"I've seen better bike handling on a shopping trolley,\" said {b}, mid-crash.",
            "{a} typed 'gg', waited forty minutes, then typed 'actually no'.",
            "{b} called {a} \"the only rider who warm-downs their ego\".",
        ),
        "escalations": (
            "{a} moved the whole setup into a walk-in freezer for crisp cold oxygen.",
            "{b} hacked the weather server so it rains only on {a}'s avatar.",
            "{a} started lubing the chain with something that is definitely not chain lube.",
            "{b} bought a second smart trainer to ride two bikes at once. It worked. That's the worst part.",
            "{a} recruited a paceline of six strangers and refuses to name them.",
            "{b} quietly raised {a}'s trainer difficulty to 40 percent and called it character building.",
            "{a} hung fourteen kilos of bottles on the bike chasing a weight bonus that does not exist.",
            "{b} started a training block titled BEAT {a} OR DIE. Week one is just anger.",
            "{a} practised the finish-line bike throw until the avatar developed a shoulder problem.",
            "{b} reported {a}'s sprint to the UCI, Zwift support, and one very confused parish council.",
        ),
        "crowd": (
            "The discord is in flames.",
            "The race chat is ninety percent popcorn.",
            "Three admins have muted this channel.",
            "Somebody clipped it. Everybody clipped it.",
            "The segment leaderboard is taking sides.",
        ),
        "climaxes": (
            "The league settled it one-up-the-Alpe, winner keeps the KOM. {w} by eleven seconds; {l} unclipped at the top and sat on the virtual tarmac.",
            "It came down to a sprint on the volcano. {w} took it by half a wheel while {l} threw the bike, the bottle, and a prayer.",
            "Last corner of the crit: {w} took an inside line that does not exist; {l} is still in the barriers thinking about that line.",
            "{w} dropped {l} on the fourteen-percent pitch and waited at the top. Just waited. {l} arrived four minutes later, to applause.",
            "Most vertical in one hour: {w} climbed Everesting minus a bit; {l} climbed a mild hill, twice, slowly.",
            "The category police checked both licences. {w} races exactly where they belong; {l} has been gently returned to the beginner ride, with a flag.",
        ),
    },
    "fortnite": {
        "settings": (
            "a lobby with far too many people in it",
            "Tilted Towers, prime drop, no survivors",
            "the final circle, both of them camping",
        ),
        "sparks": (
            "{b} stole {a}'s legendary chest and danced on the lid.",
            "{a} caught {b} bush-camping the same bush for eleven straight minutes.",
            "{b} took the last medkit while {a} bled out and typed 'skills issue'.",
            "{a} accused {b} of cranking 90s purely to be annoying about it.",
            "{b} emoted directly onto {a}'s elimination card. Slow version.",
            "{a} reported {b} for playing keyboard and mouse while married to a controller.",
            "{b} left {a} hanging on a team reset and watched them fall out of the sky.",
            "{a}'s trap got a squad wipe and {b} told the lobby it was 'a team effort'.",
            "{b} hot-dropped onto {a}'s marker out of pure spite, and lived.",
            "{a} heard {b}'s mic breathing for the whole bus ride and said nothing. Yet.",
        ),
        "quotes": (
            "{a} in party chat: \"I've carried you since chapter two. Watch this.\"",
            "\"{b} aims like the mouse is on fire,\" {a} told the lobby.",
            "{b}, calmly: \"{a} builds like the storm is a rumour.\"",
            "\"Touch grass,\" said {a}, who had not touched grass since the season started.",
            "{b} muted {a} mid-match and told the squad, \"I'm in a good place now.\"",
            "{a} typed '1v1 me' from inside a bush, at full health, hiding.",
        ),
        "escalations": (
            "{a} started dropping on {b}'s head every match, win or lose, no looting, just war.",
            "{b} built a tower so tall the render distance gave up, and so did the squad.",
            "{a} began running nothing but pump shotgun and spite.",
            "{b} recruited the whole lobby to ignore {a} on purpose, in writing.",
            "{a} played eleven hours straight and refused to admit it to their own reflection.",
            "{b} switched to zero build to make it personal. It worked; it's more personal.",
            "{a} learned {b}'s drop spot by heart and started leaving taunts in the locker.",
            "{b} changed their skin to {a}'s and played badly in it, publicly.",
            "{a} watched four hours of {b}'s streams 'for research' and took actual notes.",
            "{b} sold {a}'s loadout trick to the enemy team for one v-buck.",
        ),
        "crowd": (
            "The squad chat is a war crimes tribunal.",
            "Two spectators have picked gladiators.",
            "The kill feed is taking notes.",
            "Someone's little brother has chosen violence.",
            "The lobby has stopped playing to watch this.",
        ),
        "climaxes": (
            "1v1, Tilted, one gun each: {w} took it on one health with a pump; {l} threw a controller that is now load-bearing.",
            "Final circle, both on zero materials: {w} boxed up smart; {l} panic-ramped into the storm and left the match in a huff.",
            "The lobby voted, and {w} won by a landslide measured in emotes; {l} has been muted by four separate people.",
            "{w} ended it with a snipe across the map and apologised to nobody; {l} is contesting the result with a screenshot of a wall.",
            "Box fight, first blood: {w} read {l}'s edit like a book with large print.",
            "It came down to the last mats. {w} had two wood; {l} had a plan, which was worse.",
        ),
    },
    "baking": {
        "settings": (
            "a high-stakes tent with one oven and no air conditioning",
            "the county fair, butter in the air",
            "a shared kitchen with one stand mixer and two grudges",
        ),
        "sparks": (
            "{b} used the last of the vanilla and put the empty bottle back on {a}'s station.",
            "{a} found {b}'s showstopper in the fridge labelled DO NOT TOUCH, and touched it.",
            "{b} told the judges {a}'s buttercream came out of a tub, in a whisper, twice.",
            "{a} reported {b} for a soggy bottom two weeks running.",
            "{b} set the proving drawer to a temperature no yeast has ever survived.",
            "{a} ate {b}'s technical challenge entry and blamed the lighting.",
            "{b} borrowed {a}'s piping bag and returned it 'cleaned', in quotes.",
            "{a}'s meringue cracked the moment {b} walked past humming.",
            "{b} swapped {a}'s caster sugar for salt in front of a camera {b} knew was on.",
            "{a} caught {b} measuring {a}'s proving bowl with their own ruler.",
        ),
        "quotes": (
            "{a} to the tent: \"{b} bakes like the timer is advisory.\"",
            "\"{b}'s ganache is just chocolate that gave up,\" {a} told the cooling rack.",
            "{b}, softly: \"{a} proves dough in a car and calls it technique.\"",
            "\"Some of us cream butter,\" said {a}, \"and some of us just... hope.\"",
            "{b} asked the judges whether {a}'s oven was 'also emotional'.",
            "{a} said 'store bought' once, in a whisper, and {b} has lived on it for weeks.",
        ),
        "escalations": (
            "{a} started laminating at 5am to get a head start and a grudge.",
            "{b} replaced {a}'s caster sugar with something crystalline and unhelpful.",
            "{a} brought a second oven. In a wheelbarrow. Through the front door.",
            "{b} entered three showstoppers and dared the judges to pick one.",
            "{a} piped an entire cathedral in royal icing purely to make a point about detail.",
            "{b} proved dough on a car bonnet in July and called it technique.",
            "{a} bought the last eight blocks of butter in town. All of them. On purpose.",
            "{b} started a rumour that {a}'s sourdough starter is 'store bought, spiritually'.",
            "{a} practised a mirror glaze so many times the counter has a shine to it, permanently.",
            "{b} iced a cake in {a}'s colours and then dropped it, theatrically, outside.",
        ),
        "crowd": (
            "The tent has chosen violence.",
            "The judges are pretending not to watch.",
            "Somebody's nan has picked a side and it's personal.",
            "The cooling rack is a crime scene now.",
            "Two viewers have started a bracket.",
        ),
        "climaxes": (
            "Nine minutes of deliberation: {w} took the star baker apron; {l} went home with a collapsed sponge and a theory.",
            "It came down to the technical: {w} ranked first; {l} ranked last and blamed the oven they both shared.",
            "The judges split one macaron two ways. {w}'s had the foot; {l}'s had ambitions.",
            "{w} took it on the showstopper, clean; {l}'s ganache split in front of everyone, including the camera.",
            "The tiebreak was a signature bake in thirty minutes: {w} produced a tart; {l} produced smoke and an apology.",
            "{w} won by a single crumb coat; {l} left the tent carrying a cake leaning visibly to the left, like their argument.",
        ),
    },
    "robots": {
        "settings": (
            "an underground arena with questionable insurance",
            "the pits, where the angle grinder is a love language",
            "an event hall that bans fireworks but not this",
        ),
        "sparks": (
            "{b} reversed into {a}'s robot in the pit and called it a calibration test.",
            "{a} accused {b} of entering the same robot under three names and two colours.",
            "{b} welded something onto {a}'s chassis while {a} was getting a sandwich.",
            "{a} reported {b}'s weapon as illegal in two weight classes at once.",
            "{b} drained {a}'s battery and left a note that said 'charge it'.",
            "{a} caught {b} testing in the arena during somebody else's match.",
            "{b} borrowed {a}'s transmitter and returned it 'paired to something else'.",
            "{a}'s spare speed controller vanished, then appeared in {b}'s spares box, painted.",
            "{b} weighed {a}'s robot 'helpfully' and announced the number loudly, twice.",
            "{a} saw {b}'s new spinner up close and described it, accurately, as 'a threat'.",
        ),
        "quotes": (
            "{a} at the safety check: \"{b}'s bot is a weapon with a robot attached.\"",
            "\"{b} builds like the rules are a suggestion,\" {a} told the inspector.",
            "{b}, proudly: \"{a}'s armour is decorative and their drivetrain is hope.\"",
            "{a} said '{b} has never won a push fight in their life' on a hot mic.",
            "\"My grandmother solders better,\" said {b}, whose grandmother does not solder.",
            "{a} asked the ref whether cowardice was a weight class, looking straight at {b}.",
        ),
        "escalations": (
            "{a} rebuilt the drivetrain overnight using parts of uncertain provenance and one prayer.",
            "{b} added a second spinner, mounted sideways, for no tactical reason whatsoever.",
            "{a} started armour-plating everything, including the remote and one shoe.",
            "{b} wired the hydraulics to a car battery and stopped answering questions about it.",
            "{a} entered a heavyweight with a lightweight's paperwork and a heavyweight's grin.",
            "{b} replaced the tyres with something that was definitely not a tyre.",
            "{a} bought the last four speed controllers at the venue. All four. From every stall.",
            "{b} studied {a}'s old matches and built a counter-bot, which flattered {a} enormously.",
            "{a} upgraded to titanium and told everyone it was 'just how the shop cut it'.",
            "{b} painted their bot in {a}'s colours so the crowd would boo correctly.",
        ),
        "crowd": (
            "The pits have picked sides.",
            "Safety inspection has requested a witness.",
            "Two teams have started a pool.",
            "The announcer is already drafting the eulogy.",
            "Somebody in row three brought a helmet. For a robot.",
        ),
        "climaxes": (
            "Judges' decision, three-nil: {w} by a clear margin; {l} was on fire, literally, in a way the crowd enjoyed.",
            "Knockout in eleven seconds: {w} by spinner; {l}'s weapon arm is still up in the rafters, waving.",
            "It came down to the pit: {w} rolled out; {l} was carried out, in a bin, by friends.",
            "{w} took the belt on a count-out; {l}'s bot was mobile in spirit only.",
            "Push fight at the arena edge: {w} shoved {l} straight down the pit like a bank shot.",
            "They ran the final twice because nobody believed the first one. {w} won both; {l} has appealed to a committee that does not exist.",
        ),
    },
    "karaoke": {
        "settings": (
            "a pub back room with one microphone and no forgiveness",
            "open mic night at a bar that has seen things",
            "the function room, 11pm, book closing",
        ),
        "sparks": (
            "{b} took {a}'s song and sang it an octave higher. On purpose.",
            "{a} reported {b} for using autotune and then denying it under oath.",
            "{b} talked straight through {a}'s ballad to order a pint and a taxi.",
            "{a} changed {b}'s key mid-song from the sound desk, like a sniper.",
            "{b} dedicated a breakup song to {a}, in front of {a}, with eye contact.",
            "{a} booked the last three slots of the night and sang all of them, back to back.",
            "{b} clapped for {a} in the specific way that means the opposite.",
            "{a}'s mic cut out for exactly one line, and {b} was standing nearest the desk.",
            "{b} put the longest song in the book into {a}'s slot. Twice.",
            "{a} heard {b} warming up in the car park and called it 'practising at people'.",
        ),
        "quotes": (
            "{a} to the bar: \"{b} sings like the mic owes them money.\"",
            "\"{b} does key changes to songs that don't have keys,\" {a} told the landlord.",
            "{b}, magnificently: \"{a} performs at people. There is a difference.\"",
            "{a} said 'some of us warm up' while holding a warm drink, technically.",
            "\"I've heard more pitch from a doorbell,\" said {b}, who then sang flat.",
            "{a} requested a moment of silence for {b}'s last performance.",
        ),
        "escalations": (
            "{a} brought a backing vocalist, a tambourine, and a grudge.",
            "{b} learned the entire second verse, which nobody does, out of spite.",
            "{a} started doing key changes on songs that do not contain any.",
            "{b} arranged a four-part harmony with three strangers from the smoking area.",
            "{a} performed the whole set in character and refused to break, even at the bar.",
            "{b} put their name down for every slot left, under four different spellings.",
            "{a} tipped the DJ to never play {b}'s song again. The DJ took the money.",
            "{b} started arriving two hours early to 'get the room's energy right'.",
            "{a} brought a printed set list, laminated, with {b}'s slot crossed out.",
            "{b} answered all of it with one raised eyebrow and a ballad.",
        ),
        "crowd": (
            "The pub has chosen a side, loudly.",
            "The landlord is allowing this, which says everything.",
            "Table four has started a chant.",
            "Somebody is recording, vertically, unfortunately.",
            "The regulars have seen wars start over less.",
        ),
        "climaxes": (
            "The room voted by noise: {w} won by a margin you could feel in the floorboards; {l} left during their own applause.",
            "{w} took the night on a final falsetto that made the landlord look up; {l} cracked on the high note and knows exactly which one.",
            "The landlord settled it by buying {w} a pint and calling {l} a taxi.",
            "{w} won the encore; {l} was cut off mid-bridge, which the landlord later admitted was deliberate.",
            "Sudden death, one verse each of the same song: {w} chose dynamics, {l} chose volume, and the room chose {w}.",
            "{w} closed the night holding the mic like a trophy; {l} closed it holding a coaster and a theory about pitch.",
        ),
    },
}

#: Theme-generic story lines. When the player types a freeform theme
#: ("poledancing", "Eating Tacos") and the LLM pass cannot write the story,
#: the fallback used to borrow a random genre's lines under the themed
#: headline - a poledancing feud told with robot batteries. These instead
#: carry the theme's own words into every line: universal beats that are
#: funny about ANY activity, because they are about the people.
THEME_SPARKS = (
    "It started, as these things do, with both of them claiming to be the founder of \"{topic}\".",
    "{b} was caught running a secret \"{topic}\" training camp in a storage unit.",
    "{a} accused {b} of doing \"{topic}\" with the door closed. There are rules.",
    "{b} brought custom equipment for \"{topic}\" and acted like that was normal.",
    "Someone filmed {a} attempting \"{topic}\". The footage is sealed, thankfully.",
    "It began as friendly \"{topic}\" and escalated within minutes, witnesses say.",
    "{a} called {b}'s form at \"{topic}\" 'a safety incident'.",
    "{b} rated {a}'s \"{topic}\" a four out of ten, out loud, in front of everyone.",
    "{a} and {b} were paired for \"{topic}\" at a retreat. Neither has been the same since.",
    "{b} allegedly hid the good \"{topic}\" supplies where only they could find them.",
    "It started when {b} suggested \"{topic}\" 'just for fun', which nobody believed.",
    "{a} found {b}'s \"{topic}\" diary. It has CHAPTERS.",
    "{b} has been telling everyone they invented \"{topic}\". There are records. There are witnesses.",
    "{a} asked one question about \"{topic}\" and {b} answered for forty minutes.",
    "The \"{topic}\" league had two slots left and exactly one brain cell between them.",
    "{b} practised \"{topic}\" in front of a mirror, and the mirror filed a complaint.",
    "{a} bumped the thermostat during \"{topic}\" and called it 'strategy'.",
    "It was \"{topic}\" for charity until {b} made it personal.",
    "{b} brought a laminated rulebook for \"{topic}\" and read it aloud, twice.",
    "{a} swears {b} cheated at \"{topic}\". {b} swears nothing, on legal advice.",
)

THEME_ESCALATIONS = (
    "{a} started training for \"{topic}\" at dawn, in secret, like a person with a plan.",
    "{b} studied fourteen hours of \"{topic}\" footage 'for research'.",
    "{a} hired a coach. For \"{topic}\". Money changed hands.",
    "{b} prepared a forty-slide presentation about \"{topic}\". Nobody asked for it.",
    "The \"{topic}\" authorities were notified, and then ignored.",
    "{a} challenged {b} to public \"{topic}\", winner picks the playlist forever.",
    "{b} annexed the only good spot for \"{topic}\" and declared it sovereign.",
    "Group-chat polls were held about the \"{topic}\" dispute. Two friendships ended.",
    "{a} got sponsors. For \"{topic}\". The sponsors have questions now.",
    "{b} started a podcast purely to discuss \"{topic}\" grievances. Episode one is four hours.",
    "Neutral parties were invited to mediate the \"{topic}\" situation. All of them declined.",
    "{a} planted a mole in {b}'s \"{topic}\" crew. The mole is doing great.",
    "{b} got \"{topic}\" insured, which raises more questions than it answers.",
    "The \"{topic}\" dispute went to arbitration. The arbitrator quit.",
    "{a} released a diss track about \"{topic}\". It's... not bad, actually.",
    "{b} printed T-shirts declaring themselves the \"{topic}\" champion. Premature.",
    "{a} studied {b}'s old \"{topic}\" tapes and took notes in a small, angry notebook.",
    "Somebody leaked {b}'s \"{topic}\" practice schedule. Analysts called it 'concerning'.",
    "{b} lobbied to have the \"{topic}\" rules changed. The rules said no.",
    "It escalated when {a} brought a lawyer to casual \"{topic}\". The lawyer stayed for the drama.",
)

THEME_CLIMAXES = (
    "They settled it head-to-head at \"{topic}\", in front of everyone. {w} by a mile; {l} is appealing.",
    "One round of \"{topic}\" each, judged by the room. {w} took every vote but {l}'s.",
    "{w} won at \"{topic}\" so convincingly that {l} demanded a drug test.",
    "It ended in sudden-death \"{topic}\": {w} advanced; {l} blamed the lighting.",
    "The final was \"{topic}\", best of three. {w} took it in two; {l} took it personally.",
    "{w} settled it with one perfect \"{topic}\" run that got slow-clapped. {l} left during the applause.",
    "The \"{topic}\" title match went long. {w} endured; {l} asked if they could start over.",
    "Judges' decision, unanimous: {w}. {l} submitted a counter-argument with diagrams. The diagrams were wrong.",
    "{w} clutched the \"{topic}\" final with a move nobody has attempted since. {l} saw it coming and could do nothing.",
    "It came down to the last attempt at \"{topic}\". {w} delivered; {l} delivered slightly less.",
    "The \"{topic}\" showdown ended in a score so lopsided the scoreboard asked for confirmation. {w} confirmed. {l} left.",
    "{w} took the \"{topic}\" crown on a tiebreaker nobody knew existed, least of all {l}.",
)

#: Second beats for theme stories - trash talk and crowd weather, the same
#: two-beat structure the genre pools always had. A bare sentence per line
#: was half of why fallback beefs read stamped-out.
THEME_QUOTES = (
    "{a} told the room that {b} treats \"{topic}\" like a whole personality.",
    "{b} was overheard calling {a} 'a tourist' at \"{topic}\".",
    "{a} said {b}'s \"{topic}\" reputation is 'mostly background noise'.",
    "{b} described {a}'s \"{topic}\" style as 'legally distinct from talent'.",
    "{a} asked whether \"{topic}\" has a mercy rule. It does not.",
    "{b} told a journalist, unprompted, that \"{topic}\" is 'a war of attrition'.",
    "{a} now refers to {b} only as 'the situation'.",
    "{b} said 'we'll see' in the tone of someone who had already seen.",
    "{a} promised the crowd a masterclass in \"{topic}\". The crowd is still waiting.",
    "{b} called {a} 'an ideas man' at \"{topic}\". It was not a compliment.",
    "{a} demanded a rematch before the first one had even finished.",
    "{b} thanked the fans, the family, and nobody else involved in \"{topic}\".",
)

THEME_CROWD = (
    "The group chat is drafting constitutions.",
    "Two neutral parties have chosen flags.",
    "Someone started a bracket. It's already corrupt.",
    "The spectators have opinions and no decorum.",
    "A poll was held. The poll made it worse.",
    "The whole room has stopped pretending to work.",
    "Somebody's nan weighed in. She was brutal.",
    "The chant started spontaneously and refuses to stop.",
    "Merch is being made in someone's basement.",
    "The \"{topic}\" thread made two local Facebook groups. Both locked it.",
)

#: The topic is QUOTED in every story line, never bare: a gerund theme
#: ("using webcams with Zwift") pasted into "attempting {topic}" is how the
#: bot once said 'attempting using webcams'. Quoted, any string reads as the
#: activity's name: attempting "using webcams with Zwift" is a sentence.

#: Genre aliases, so 'zwifting' and 'fortnite br' both land somewhere sensible.
_ALIASES = {
    "trucking": ("truck", "trucker", "ets2", "ats", "convoy", "lorry", "semis"),
    "zwift": ("zwifting", "cycling", "bike", "riding", "indoor cycling", "ftp"),
    "fortnite": ("fort", "br", "battle royale", "lobby"),
    "baking": ("bake", "bakeoff", "bake off", "cake", "pastry", "bake-off"),
    "robots": ("robot", "robot fighting", "battlebots", "bots"),
    "karaoke": ("singing", "song", "mic", "open mic"),
}

_NAME_OK = re.compile(r"^[A-Za-z0-9_]{2,25}$")

#: The last few lines drawn from each pool, so consecutive stories stop
#: echoing each other. "Always way the same" was half pool size and half
#: this: random choice from a small pool re-draws the same line constantly.
_RECENT: dict = {}
_RECENT_SPAN = 3      # won't re-draw a line used in the last 3 stories


def _pick(pool, pool_id: str):
    """Draw from `pool`, avoiding anything drawn in the last few stories.

    Falls back to the whole pool when everything is burnt (a pool smaller
    than the span must still work). Per-bot-process memory only; a restart
    forgetting what was repetitive is fine.
    """
    recent = _RECENT.setdefault(pool_id, deque(maxlen=_RECENT_SPAN))
    fresh = [t for t in pool if t not in recent]
    choice = random.choice(fresh or pool)
    recent.append(choice)
    return choice


def match_genre(text: str):
    """The genre `text` names, or None when it names nothing we have.

    The distinction matters: "!beef @friend Eating Tacos" is not a genre
    mismatch to be silently randomised away - it is a freeform theme the
    player typed on purpose, and the caller keeps it (see feud(theme=...)).
    """
    t = " ".join((text or "").split()).lower().strip()
    if not t:
        return None
    if t in GENRES:
        return t
    for genre, words in _ALIASES.items():
        if t in words or any(w in t for w in words):
            return genre
    return None


def genre_for(text: str) -> str:
    """Map a typed genre to one we have, defaulting to a random one."""
    return match_genre(text) or random.choice(sorted(GENRES))


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

    A conservative floor: sparks x escalations x climaxes x rivals x either
    winner x fates. Quotes, crowd lines, stakes and act tags multiply it
    further, but they are optional beats - this counts the spine.
    """
    all_rivals = [r for g in RIVALS.values() for r in g]
    total = ((len(THEME_SPARKS) + len(REMATCH_SPARKS))
             * len(THEME_ESCALATIONS) * len(THEME_CLIMAXES)
             * len(all_rivals) * 2 * len(FATES))
    for genre, pools in GENRES.items():
        total += ((len(pools["sparks"]) + len(REMATCH_SPARKS))
                  * len(pools["escalations"])
                  * len(pools["climaxes"]) * len(RIVALS[genre])
                  * 2 * len(FATES))
    return total


def feud(issuer: str, rival: str = "", genre: str = "", revenge: bool = False,
         tag: bool = False, theme: str = "") -> dict | None:
    """Tell one feud and report what happened, or None if names are unusable.

    This is the engine behind beef(): same templates, same winner-first roll,
    but the caller gets the outcome too - the leaderboard and the !revenge
    window need to know who won, and parsing it back out of the English was
    how the last self-inflicted bug happened.

    The story is five clean lines - headline, three story lines, verdict -
    with no "Act 1" labels: the chrome read like a form and took away from
    the story. Each line is up to two beats (incident + quote, escalation +
    crowd reaction) because a bare sentence is a summary, not a story. The
    verdict stands alone so the caller can put the biggest pause before it.

    revenge=True retells it as a rematch: a REMATCH headline and a rematch
    opener in Act 1, everything else native to the genre. The roll stays
    50/50 - a rematch you were guaranteed to win would not be worth points.

    tag=True renders the rival as @name in the headline only. The caller
    decides who deserves a ping; this module just formats it.

    theme is freeform words the player typed after the rival ("Eating
    Tacos"). It is not a genre and must not be discarded: it headlines the
    story as typed, travels to the LLM pass so the acts are written inside
    it, and is remembered in the result so a !revenge rematch stays on
    theme. The LLM writes inside the theme when it can; the template
    fallback draws from theme-generic pools that carry the player's words
    into every line - never from an unrelated genre's pools.
    """
    issuer = (issuer or "").strip()
    if not _valid(issuer):
        return None
    theme = " ".join((theme or "").split())

    genre_key = genre_for(genre)
    pools = GENRES[genre_key]

    # Viewers type "@name" out of habit; treating that as an invalid name
    # silently swapped the rival for a random character, which read as the
    # bot ignoring the person they named. Strip the ping, keep the feud.
    rival = (rival or "").strip().lstrip("@").strip()
    # "random" is a keyword, not somebody's display name.
    if rival.lower() in ("random", "rand", "?"):
        rival = ""
    if not _valid(rival) or rival.lower() == issuer.lower():
        # No rival given, or they asked to beef with themselves. Either way the
        # opponent is one of the named characters - not another viewer, who did
        # not opt in, and not the issuer, whose escalation lines would read as
        # one person doing both halves.
        pool = ([r for g in RIVALS.values() for r in g] if theme.strip()
                else RIVALS[genre_key])
        others = [r for r in pool if r.lower() != issuer.lower()]
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
        return text.format(a=issuer, b=rival, w=winner, l=loser,
                           topic=theme)

    # Only a rematch needs the word - a normal beef is self-evident, and
    # labels on every line read like a form.
    label = "REMATCH: " if revenge else ""
    key = "rematch" if revenge else genre_key
    head_rival = f"@{rival}" if tag else rival

    if theme:
        setting = theme            # the player's own words, not discarded
    else:
        setting = _pick(pools["settings"], ("setting", key))

    head = (f"\U0001f525 {label}{issuer} vs. {head_rival} \u2014 "
            f"{setting} \U0001f525")
    if random.random() < 0.55:
        head += f" {fill(_pick(STAKES, ('stakes',)))}"

    # A freeform theme tells its own story lines - borrowing a genre's lines
    # under a themed headline is how a poledancing beef came back full of
    # robot batteries. Only a rematch opener ignores the theme, and only
    # because "demanded a rematch" is true in every world.
    if theme:
        story = {"sparks": THEME_SPARKS, "escalations": THEME_ESCALATIONS,
                 "climaxes": THEME_CLIMAXES}
        tagpools = ("theme",)
    else:
        story = pools
        tagpools = (key,)

    if theme:
        quotes, crowd = THEME_QUOTES, THEME_CROWD
    else:
        quotes, crowd = pools["quotes"], pools["crowd"]

    act1 = fill(_pick(REMATCH_SPARKS if revenge else story["sparks"],
                      ("spark",) + tagpools))
    if not revenge and random.random() < 0.65:
        act1 += " " + fill(_pick(quotes, ("quote",) + tagpools))

    act2 = fill(_pick(story["escalations"], ("esc",) + tagpools))
    if random.random() < 0.65:
        act2 += " " + fill(_pick(crowd, ("crowd",) + tagpools))

    act3 = fill(_pick(story["climaxes"], ("climax",) + tagpools))

    verdict = (f"\U0001f3c6 {winner} "
               f"{_pick(VERDICTS, ('verb',))}. "
               f"{fill(_pick(FATES, ('fate',)))}")

    return {
        "lines": [head, act1, act2, act3, verdict],
        "issuer": issuer,
        "rival": rival,
        "genre": genre_key,
        "theme": theme,
        "winner": winner,
        "loser": loser,
        "issuer_won": issuer_wins,
    }


def beef(issuer: str, rival: str = "", genre: str = "") -> list:
    """The story lines of a feud, or [] if the names are unusable.

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
