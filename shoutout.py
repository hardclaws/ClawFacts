"""Shoutouts: what the bot says when another channel raids in.

Every fact in the message is read off something real. The raider's display name
and viewer count come from the raid notice Twitch sends. Affiliate/partner
status, follower count and what they are streaming right now come from Helix.
Nothing is invented about the person - a shoutout that makes something up is
worse than no shoutout, because a real channel will see it and their chat will
read it.

One limit worth stating plainly, because it shapes the wording: Twitch has no
"last seen playing" for an arbitrary channel. Get Streams answers for a channel
that is live *right now* and says nothing otherwise. So the game is only ever
mentioned as present tense, and only when Twitch confirmed it. A shoutout that
guessed at what someone was streaming would be a claim about a real person that
nobody can check.

Messages are assembled from whole sentences rather than by filling slots inside
one sentence. Two separate bugs came from the other approach: a template that
hardcoded "a" in front of a slot whose values carried their own article, and a
second occurrence of a slot that read correctly for mass nouns and wrongly for
count nouns. Independent clauses cannot produce either.
"""

import random
import re

__all__ = ["format_raid", "url_for", "theme_for_game", "LABEL", "THEMES",
           "OPENERS", "PRAISE", "PRAISE_LIVE", "RAID_PLURAL", "RAID_ONE"]

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

#: Openers: the greeting. Owns the raider's name, says nothing about the raid's
#: size so it works for 1 viewer or 400.
OPENERS = {
    "trucking": (
        "🙌 Huge shoutout to {name}!",
        "🚛 {name} just pulled into the channel!",
        "📣 Breaker one-nine, {name} is on the road with us!",
        "🚚 {name} rolled in and brought the convoy!",
        "🙌 Big shoutout to {name}!",
        "📣 Hammer lane for {name}, they just raided in!",
        "🚛 Shoutout to {name}, good to see them rolling with us!",
        "🙌 {name} caught us on the flip side!",
    ),
    "zwift": (
        "🙌 Huge shoutout to {name}!",
        "🚴 {name} just rolled into the pack!",
        "🏃 {name} sprinted in with a raid!",
        "📣 Shoutout to {name}, welcome to the group ride!",
        "🚴 {name} dropped in and the whole peloton noticed!",
        "💨 {name} attacked off the front with a raid!",
        "🏃 Big welcome to {name}!",
        "🙌 Shoutout to {name}, thanks for swinging by!",
    ),
    "fortnite": (
        "🙌 Huge shoutout to {name}!",
        "🎮 {name} just dropped in with a raid!",
        "🪂 Shoutout to {name}, they landed hot!",
        "🎮 {name} rotated over with the squad!",
        "📣 {name} just claimed the lobby with a raid!",
        "🙌 Big shoutout to {name}!",
        "🪂 Welcome in {name}, thanks for the drop!",
        "🙌 {name} just squad-joined with a raid!",
    ),
    "generic": (
        "🙌 Huge shoutout to {name}!",
        "📣 Shoutout to {name} for the raid!",
        "🙌 Big welcome to {name}!",
        "📣 {name} just raided in, welcome!",
        "🙌 Thanks for the raid, {name}!",
        "📣 {name} swung by with a raid!",
    ),
}

#: The raid itself. Split by shape so a count is never edited out of a
#: template by string surgery - that is how "dropped 1 viewers" happened.
# None of these says "raid" - half the openers already do, and two in a row
# read as a copy-paste error rather than a sentence.
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

#: Praise, split in two: lines that stand alone, and lines that carry the
#: raider's current game. Only the second set mentions a game, and only when
#: Helix confirmed they are live this minute.
PRAISE = {
    "trucking": (
        "They are an absolute gem of a creator, go give them a follow.",
        "Proper road company. Stick around and say hello.",
        "They run a great channel and are well worth a follow.",
        "One of the good ones. Go say hi.",
        "Solid channel and a solid human.",
        "Good people to have in the convoy.",
    ),
    "zwift": (
        "They are an absolute gem of a creator, go give them a follow.",
        "Great company on a long ride. Go say hello.",
        "They run a brilliant channel and are well worth a follow.",
        "Good legs and good vibes.",
        "One to keep an eye on. Stick around and say hi.",
        "Exactly the sort of channel this community should follow.",
    ),
    "fortnite": (
        "They are an absolute gem of a creator, go give them a follow.",
        "Cracked channel and good people. Go say hello.",
        "They run a great stream and are well worth a follow.",
        "One of the good ones. Stick around and say hi.",
        "Solid squad material.",
        "Well worth a follow if you like a good lobby.",
    ),
    "generic": (
        "They are an absolute gem of a creator, go give them a follow.",
        "Great channel and great people. Go say hello.",
        "Well worth a follow, and good company besides.",
        "One of the good ones. Stick around and say hi.",
        "They run a great channel and are well worth a follow.",
        "Good people. Go say hello.",
    ),
}

#: Same praise, but the raider is live right now and Twitch said what on.
#: Present tense only: "last seen playing" would be a claim nobody can check.
PRAISE_LIVE = {
    "trucking": (
        "They are an absolute gem of a creator and are live right now on "
        "{game}.",
        "Proper road company, and currently out on {game}.",
        "They are on {game} right now, so go catch them after this.",
    ),
    "zwift": (
        "They are an absolute gem of a creator and are live right now on "
        "{game}.",
        "Great company on a long ride, and currently out on {game}.",
        "They are on {game} right now, so go catch them after this.",
    ),
    "fortnite": (
        "They are an absolute gem of a creator and are live right now playing "
        "{game}.",
        "Cracked channel, and they are on {game} right now.",
        "They are playing {game} right now, so go catch them after this.",
    ),
    "generic": (
        "They are an absolute gem of a creator and are live right now on "
        "{game}.",
        "They are live right now playing {game}.",
        "Currently on {game}, so go catch them after this.",
    ),
}

CTA = "Go show them some love at {url}"


def theme_for_game(category: str | None) -> str:
    """Map a Twitch category to a shoutout flavour.

    Unknown, empty or missing categories all land in "generic" rather than
    raising: a raid should never fail because of a game name.
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
                raider_stream: dict | None = None) -> str:
    """One shoutout line, or '' if there is nothing trustworthy to say.

    ``viewer_count`` may be an int or the raw string off the IRC tag. Anything
    unparseable falls through to the count-free wording rather than printed
    as-is.

    ``theme`` picks the flavour (see THEMES); an unknown value falls back to
    generic rather than raising. ``raider_stream`` is the raider's live Helix
    stream row, or None when they are not live or the lookup failed - the game
    is mentioned only when it is present.

    The URL always comes last: it is the actionable part, and a link buried
    mid-sentence is easy to miss and awkward to click.
    """
    name = (display_name or "").strip()
    url = url_for(login or name)
    if not name or not url:
        return ""

    theme = theme if theme in THEMES else "generic"

    try:
        count = int(str(viewer_count).strip())
    except (TypeError, ValueError):
        count = None

    if count is None:
        raid = random.choice(RAID_UNKNOWN) if RAID_UNKNOWN else ""
    elif count == 1:
        raid = random.choice(RAID_ONE)
    else:
        raid = random.choice(RAID_PLURAL).format(count=f"{count:,}")

    game = _raider_game(raider_stream)
    if game and PRAISE_LIVE.get(theme):
        praise = random.choice(PRAISE_LIVE[theme]).format(game=game)
    else:
        praise = random.choice(PRAISE[theme])

    parts = [
        random.choice(OPENERS[theme]).format(name=name),
        raid,
        praise,
        profile_tail(profile or {}),
        CTA.format(url=url),
    ]
    # Joined whole sentences, so no clause can inherit another's grammar.
    return " ".join(p for p in parts if p)
