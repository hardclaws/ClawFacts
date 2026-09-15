"""Shoutouts: what the bot says when another channel raids in, and when a
moderator shouts someone out by hand.

Every fact is read off something real. The raider's display name and viewer
count come from the raid notice Twitch sends. Affiliate/partner status and
follower count come from Helix. The game is named only from an endpoint that
can actually see it, and the tense says which one that was.

Two rules the wording is built around:

* A manual shoutout must never say the person raided. `!so <name>` works on
  any channel, and most of them did not raid - claiming they did is a small
  lie posted in public about a real person.
* The praise has to be about *them*, not filler that would fit anyone. The
  ask lives in one call to action at the end, so no praise line needs to
  repeat "go give them a follow".

Messages are assembled from whole sentences rather than by filling slots inside
one sentence. Two bugs came from the other approach (a hardcoded article before
a slot whose values carried their own, and a slot used twice that read right
for mass nouns and wrong for count nouns), and five more came from clauses that
were each fine alone and collided when joined.
"""

import random
import re

__all__ = ["format_raid", "url_for", "theme_for_game", "LABEL", "THEMES",
           "OPENERS_RAID", "OPENERS_MANUAL", "PRAISE", "PRAISE_LIVE",
           "PRAISE_LAST", "RAID_PLURAL", "RAID_ONE", "CTA"]

LABEL = "SO"

#: The flavours available. "generic" is the fallback for anything the bot
#: cannot place - offline, a category it does not recognise, no Helix answer.
THEMES = ("trucking", "zwift", "fortnite", "generic")

#: Twitch category names land in a theme by substring, case-insensitive, so
#: "Euro Truck Simulator 2" and "American Truck Simulator" both reach trucking
#: without anyone maintaining a list of every skin of the name.
GAME_THEMES = (
    ("trucking", ("truck", "snowrunner", "mudrunner", "bus simulator")),
    ("zwift", ("zwift",)),
    ("fortnite", ("fortnite",)),
)

#: Openers for an actual raid: they describe somebody arriving.
OPENERS_RAID = {
    "trucking": (
        "🚛 {name} just pulled into the channel!",
        "📣 Breaker one-nine, {name} is on the road with us!",
        "🚚 {name} rolled in and brought the convoy!",
        "📣 Hammer lane for {name}, they just swung off the interstate!",
        "🙌 {name} caught us on the flip side!",
        "🚛 {name} just backed into the lot!",
        "📣 10-4 {name}, we see you rolling through!",
        "🚚 Good buddy {name} just turned in off the highway!",
    ),
    "zwift": (
        "🚴 {name} just rolled into the pack!",
        "🏃 {name} sprinted in!",
        "📣 {name} just joined the group ride!",
        "🚴 {name} dropped in and the peloton noticed!",
        "💨 {name} attacked off the front!",
        "🏃 {name} just came through the timing mat!",
        "🚴 {name} tucked in behind us!",
        "📣 {name} just rolled up to the front of the bunch!",
    ),
    "fortnite": (
        "🎮 {name} just dropped in!",
        "🪂 {name} landed hot!",
        "🎮 {name} rotated over with the squad!",
        "📣 {name} just claimed the lobby!",
        "🪂 {name} just glided in!",
        "🎮 {name} just joined the squad!",
        "📣 {name} crashed the lobby!",
        "🪂 {name} dropped straight in here!",
    ),
    "generic": (
        "🙌 {name} just came through!",
        "📣 {name} just showed up!",
        "🙌 Look who it is - {name}!",
        "📣 {name} just landed in here!",
        "🙌 {name} just swung by!",
        "📣 Everyone, {name} is here!",
    ),
}

#: Openers for a moderator shouting someone out by hand. None of these claim
#: the person arrived, because most of the time they did not.
OPENERS_MANUAL = {
    "trucking": (
        "📣 Chat, go meet {name} - worth the stop.",
        "🚛 Everybody go say hi to {name}.",
        "🚛 Go find {name} - good driver, better company.",
        "📣 {name} is the kind of channel you leave running all shift.",
        "🚛 Give {name} a look next time you're parked up.",
        "🙌 Put {name} on the list, driver to driver.",
        "🙌 Shoutout to {name} - 10-10 on the side.",
    ),
    "zwift": (
        "📣 Chat, go meet {name} - worth every kilometre.",
        "🚴 Everybody go say hi to {name}.",
        "🚴 Go find {name} - good legs, better company.",
        "📣 {name} is the kind of channel you leave running all ride.",
        "🏃 Give {name} a look next time you're on the trainer.",
        "🙌 Put {name} on the list, rider to rider.",
        "🙌 Shoutout to {name} - worth every watt.",
    ),
    "fortnite": (
        "📣 Chat, go meet {name} - worth the drop.",
        "🎮 Everybody go say hi to {name}.",
        "🪂 Go find {name} - good aim, better company.",
        "📣 {name} is the kind of channel you leave running all lobby.",
        "🎮 Give {name} a look next time you're short a squad.",
        "🙌 Put {name} on the list, squad to squad.",
        "🙌 Shoutout to {name} - they'll carry you.",
    ),
    "generic": (
        "📣 Chat, go meet {name} - worth a look.",
        "🙌 Everybody go say hi to {name}.",
        "📣 Go find {name} - good stream, better company.",
        "📣 {name} is the kind of channel you leave running all evening.",
        "🙌 Give {name} a look when you've got a minute.",
        "🙌 Put {name} on the list while you're thinking about it.",
        "🙌 Shoutout to {name}.",
    ),
}

#: The size of the raid. Split by shape so a count is never edited out of a
#: template by string surgery - that is how "dropped 1 viewers" happened.
#: Used only for real raids; a manual shoutout has no count to report.
RAID_PLURAL = (
    "They brought {count} viewers over with them.",
    "{count} of you came across.",
    "That is {count} more people in here.",
)
RAID_ONE = (
    "They came across on their own.",
    "Just the one of them, and that is one more than we had.",
    "They made the trip solo.",
)
#: The count was missing or unparseable, so nothing is said about it.
RAID_UNKNOWN: tuple[str, ...] = ()

#: Praise. Deliberately carries no call to action - the ask is the CTA at the
#: end, and a line that also says "go give them a follow" makes the message
#: ask twice and reads like boilerplate. These describe the person instead.
PRAISE = {
    "trucking": (
        "Clean logbook, clean chat, and no drama at the chicken coop.",
        "A thousand miles of good stories and a playlist to match.",
        "The kind of driver who pulls over to help and never mentions it.",
        "Ten-ten on the side and worth every minute of it.",
        "They'll run all night and never once let it get dull.",
        "Good rig, good humour, and they don't brag about the miles.",
        "Proper road company, the sort you'd share a hundred-mile coffee with.",
        "They know every truck stop worth stopping at and they'll tell you.",
    ),
    "zwift": (
        "The sort of rider who waits at the top for the rest of the group.",
        "They'll talk you up the climb and never let you bonk alone.",
        "Proper watts and none of the ego that usually comes with them.",
        "They'll sit on your wheel all day and still ask how you're going.",
        "Good legs, good chat, and no sprint-finish nonsense.",
        "The kind of company that makes two hours feel like forty minutes.",
        "They've got the suffering sorted and the jokes sorted too.",
        "Happy to ride at your pace, which is rarer than it should be.",
    ),
    "fortnite": (
        "Crankin' 90s and never once toxic about it.",
        "Shares their mats and doesn't rage at the storm.",
        "They'll carry the lobby and still let you take the win.",
        "Good aim, better humour, zero bush-camping nonsense.",
        "They play like it's actually fun, which is rarer than it sounds.",
        "The kind of channel where the chat is half the entertainment.",
        "Squad-worthy, and they'll tell you straight when you earned it.",
        "No tilted nonsense, just someone who's good and knows it's a game.",
    ),
    "generic": (
        "They put more into an hour of stream than most put into a week.",
        "Genuinely lovely channel and a genuinely lovely person.",
        "The kind of stream you open for twenty minutes and close three hours "
        "later.",
        "They've got the rare trick of making a chat feel like a room full of "
        "mates.",
        "Consistently good, which is harder than it sounds.",
        "Funny without trying too hard, and kind without making a thing of it.",
        "They're the reason the follow button exists.",
        "Someone who streams because they like it, and you can tell.",
    ),
}

#: The raider is live this minute, per Get Streams, whose game_name is "the
#: category or game being streamed". Present tense only.
PRAISE_LIVE = {
    "trucking": (
        "They're out on {game} right now, so go catch them before it ends.",
        "Currently on {game}, mid-run.",
        "They're on {game} this minute.",
        "Live on {game} as we speak, engine still warm.",
    ),
    "zwift": (
        "They're out on {game} right now, so go catch them before the ride "
        "ends.",
        "Currently on {game}, mid-effort.",
        "They're grinding out {game} right this minute.",
        "Live on {game}, wheels turning.",
    ),
    "fortnite": (
        "They're live on {game} right now, so go catch them before the match "
        "ends.",
        "Currently on {game}, mid-lobby.",
        "They're playing {game} this minute.",
        "Live on {game} and the squad's already stacked.",
    ),
    "generic": (
        "They're live on {game} right now, so go catch them while they're on.",
        "Currently on {game}.",
        "On {game} right now if you fancy it.",
        "Live on {game} at this very moment.",
    ),
}

#: The raider is offline, but Get Channel Information reported a category.
#: Twitch documents that field as "the game that the broadcaster is playing or
#: last played", so "last seen" is sourced here and not a guess. Used only when
#: Get Streams said they are not live, which is what makes the past tense
#: correct rather than stale.
PRAISE_LAST = {
    "trucking": (
        "Last seen out on {game}, so go catch them when they're back on the "
        "road.",
        "Last time we looked, they were on {game}.",
        "They were on {game} when we last checked.",
        "Last spotted out on {game}.",
    ),
    "zwift": (
        "Last seen out on {game}, so go catch them when they're back in the "
        "saddle.",
        "Their last session was {game}, as far as we can tell.",
        "They were on {game} the last time anyone looked.",
        "Off the bike at the moment - {game} was their last one.",
    ),
    "fortnite": (
        "Last seen playing {game}, so go catch them when they're back in the "
        "lobby.",
        "Their last lobby was {game}.",
        "They were on {game} before they logged off.",
        "{game} was the last thing they queued.",
    ),
    "generic": (
        "Last seen playing {game}, so go catch them when they're back on.",
        "Their last stream was {game}.",
        "They were on {game} before they signed off.",
        "{game} was the last thing they were playing.",
    ),
}

CTA = "Go show them some love at {url}"


def theme_for_game(category: str | None) -> str:
    """Map a Twitch category to a shoutout flavour.

    Unknown, empty or missing categories all land in "generic" rather than
    raising: a shoutout should never fail because of a game name.
    """
    text = (category or "").strip().lower()
    if not text:
        return "generic"
    for theme, needles in GAME_THEMES:
        if any(n in text for n in needles):
            return theme
    return "generic"


#: Twitch logins are 4-25 characters of [a-z0-9_]. Anything else cannot be a
#: channel, so linking to it would post a dead URL into chat.
_LOGIN_RE = re.compile(r"^[a-z0-9_]{4,25}$")


def url_for(login: str) -> str:
    """The channel URL, or '' if this cannot be a login.

    Built from the login, never from the display name: display names carry
    capitals, spaces and unicode that a URL does not. "Big Al" lowercased is
    "big al", which is not a channel - and a dead link in a shoutout is worse
    than no link at all.
    """
    login = (login or "").strip().lstrip("#@").lower()
    return f"twitch.tv/{login}" if _LOGIN_RE.match(login) else ""


def profile_tail(profile: dict) -> str:
    """Only what Twitch actually told us about this channel.

    Deliberately limited to the fields `access.Helix.channel_profile` returns.
    Returns '' when there is nothing trustworthy to add.
    """
    if not profile:
        return ""
    bits = []
    kind = str(profile.get("broadcaster_type") or "").strip().lower()
    if kind in ("affiliate", "partner"):
        bits.append("Twitch " + kind.capitalize())
    followers = profile.get("followers")
    if isinstance(followers, int) and followers >= 0:
        bits.append(f"{followers:,} followers" if followers != 1
                    else "1 follower")
    text = ", ".join(bits)
    # First letter only - str.capitalize() would also lowercase "Affiliate".
    return text[:1].upper() + text[1:] + "." if bits else ""


def _raider_game(stream: dict | None) -> str:
    """The category the raider is live on, or '' if they are not live.

    A blank category on a live stream is real (plenty of channels stream with
    no category set) and is reported as '' rather than guessed at.
    """
    if not stream:
        return ""
    return " ".join(str(stream.get("game_name") or "").split())


def format_raid(display_name: str, viewer_count=None, login: str = "",
                profile: dict | None = None, theme: str = "generic",
                raider_stream: dict | None = None, last_game: str = "",
                is_raid: bool = True) -> str:
    """One shoutout line, or '' if there is nothing trustworthy to say.

    ``is_raid`` decides whether the message may describe someone arriving. A
    moderator's `!so <name>` passes False, because most channels shouted out by
    hand did not raid and saying they did would be a small public lie. It also
    suppresses the viewer count, which belongs to the raid and not the person.

    ``viewer_count`` may be an int or the raw string off the IRC tag. Anything
    unparseable falls through to the count-free wording rather than printed
    as-is.

    ``theme`` picks the flavour (see THEMES); an unknown value falls back to
    generic rather than raising.

    Two different game claims, two different sources, and the tense is what
    tells them apart:

    * ``raider_stream`` - a Get Streams row, which only exists for a channel
      live right now, so the wording is present tense.
    * ``last_game`` - Get Channel Information's game_name, documented as the
      game they "are playing or last played". Used only when they are NOT
      live, which is what makes "last seen playing" true rather than a guess.

    With neither, no game is named at all.

    The URL always comes last: it is the actionable part, and a link buried
    mid-sentence is easy to miss and awkward to click.
    """
    name = (display_name or "").strip()
    url = url_for(login or name)
    if not name or not url:
        return ""

    theme = theme if theme in THEMES else "generic"

    raid = ""
    if is_raid:
        try:
            count = int(str(viewer_count).strip())
        except (TypeError, ValueError):
            count = None
        if count == 1:
            raid = random.choice(RAID_ONE)
        elif count is not None:
            raid = random.choice(RAID_PLURAL).format(count=f"{count:,}")

    game = _raider_game(raider_stream)
    last = " ".join(str(last_game or "").split())
    if game:
        # Live this minute, per Get Streams. Present tense.
        praise = random.choice(PRAISE_LIVE[theme]).format(game=game)
    elif last:
        # Offline, but Twitch says what they were last on. Past tense.
        praise = random.choice(PRAISE_LAST[theme]).format(game=last)
    else:
        praise = random.choice(PRAISE[theme])

    openers = OPENERS_RAID if is_raid else OPENERS_MANUAL
    parts = [
        random.choice(openers[theme]).format(name=name),
        raid,
        praise,
        profile_tail(profile or {}),
        CTA.format(url=url),
    ]
    # Joined whole sentences, so no clause can inherit another's grammar.
    return " ".join(p for p in parts if p)
