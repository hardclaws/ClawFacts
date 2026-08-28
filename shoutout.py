"""Shoutouts: what the bot says when another channel raids in.

Every fact in the message is either read off the raid notice itself (the
raider's display name and viewer count, which Twitch supplies) or fetched from
Twitch (their affiliate/partner status and follower count). Nothing is invented
about the person - a shoutout that makes something up is worse than no shoutout
at all, because it is about a real channel who will see it and whose chat will
read it.

The Helix lookup is best-effort. A raid is the worst possible moment to be
waiting on a network call, and the token can be expired, so the plain
name-and-count shoutout is always available as the floor.
"""

import random
import re

__all__ = ["format_raid", "url_for", "LABEL", "BODIES_PLURAL", "BODIES_ONE",
           "BODIES_UNKNOWN"]

LABEL = "SO"

# Three separate pools rather than one pool with the count edited out of it.
# String surgery on a template only works for the templates that happen to
# contain the exact substring, so "just dropped {count} viewers" survived a
# replace(" with {count}", "") and produced "dropped 1 viewers".
BODIES_PLURAL = (
    "Shoutout to {name} for raiding in with {count}! Go say hello and give "
    "'em a follow.",
    "{name} just dropped {count} viewers on us. Welcome in!",
    "Welcome to the {count} of you who raided in with {name}!",
    "{name} brought {count} friends and a whole lot of noise. Shoutout!",
    "Raid from {name} with {count} viewers! Stick around and say hi.",
)

BODIES_ONE = (
    "Shoutout to {name} for raiding in! Go say hello and give 'em a follow.",
    "{name} just raided over. Welcome in!",
    "Raid from {name}! Stick around and say hi.",
)

#: The viewer count was missing or unparseable. Says nothing about how many.
BODIES_UNKNOWN = (
    "Shoutout to {name} for the raid! Go say hello and give 'em a follow.",
    "{name} just raided in. Welcome!",
    "Raid from {name}! Stick around and say hi.",
)


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
    It does not fetch the game they are streaming, so nothing here claims to
    know that. Returns '' when there is nothing trustworthy to add.
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
    # Its own sentence, not a dash-glued tail: every body ends in a full
    # stop, so a " - " prefix read as "give 'em a follow. - Twitch Affiliate".
    text = ", ".join(bits)
    # First letter only - str.capitalize() would also lowercase "Affiliate".
    return " " + text[:1].upper() + text[1:] + "." if bits else ""


def format_raid(display_name: str, viewer_count=None, login: str = "",
                profile: dict | None = None) -> str:
    """One shoutout line, or '' if there is nothing trustworthy to say.

    ``viewer_count`` may be an int or the raw string off the IRC tag. Anything
    unparseable falls through to the count-free wording rather than being
    printed as-is.

    The URL always comes last: it is the actionable part, and a link buried
    mid-sentence is easy to miss and awkward to click.
    """
    name = (display_name or "").strip()
    url = url_for(login or name)
    if not name or not url:
        return ""

    try:
        count = int(str(viewer_count).strip())
    except (TypeError, ValueError):
        count = None

    if count is None:
        body = random.choice(BODIES_UNKNOWN).format(name=name)
    elif count == 1:
        body = random.choice(BODIES_ONE).format(name=name)
    else:
        body = random.choice(BODIES_PLURAL).format(name=name,
                                                   count=f"{count:,}")

    return f"{body}{profile_tail(profile or {})} They're over at {url}"
