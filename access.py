"""Who may use !funfact, and how often.

Twitch chat tells us a user's *badges* for free on every message, so moderator,
VIP and subscriber status cost nothing to check. Follow status is not in IRC at
all — the only way to get it is the Helix API:

    GET /helix/channels/followers?broadcaster_id=<us>&user_id=<them>

which needs the `moderator:read:followers` scope and a *user* access token
belonging to the broadcaster or one of the channel's moderators. That is the
device-login token auth.py already stores, so the only extra step is the scope.

Lookups are cached hard: a user's id never changes, and follow status changes
at most once. Badged users (mod/VIP/sub) never trigger an API call at all.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

import auth

HELIX = "https://api.twitch.tv"

# Seconds a tier must wait between its own !funfact uses. Overridable in
# config.json under "tier_cooldowns".
DEFAULT_TIER_COOLDOWNS = {
    "broadcaster": 30.0,
    "moderator": 30.0,
    "vip": 60.0,
    "subscriber": 60.0,
    "follower": 300.0,
}

# A follower must have been following this long to use the command at all.
DEFAULT_MIN_FOLLOW_AGE = 86400.0  # 1 day

# Badges that count as a role, highest precedence first. "founder" and
# "sub-gifter" are subscriber badges; staff/admin/global_mod are Twitch staff
# and treated as moderators of any channel.
_BADGE_TIERS = (
    ("broadcaster", "broadcaster"),
    ("admin", "moderator"),
    ("global_mod", "moderator"),
    ("staff", "moderator"),
    ("moderator", "moderator"),
    ("vip", "vip"),
    ("founder", "subscriber"),
    ("subscriber", "subscriber"),
)

USER_AGENT = "ClawFacts/1.0 (Twitch fun-fact bot)"


class Decision:
    """The answer to 'may this user run the command now?'."""

    __slots__ = ("allowed", "tier", "cooldown", "reason", "wait")

    def __init__(self, allowed, tier, cooldown, reason, wait=0.0):
        self.allowed = allowed
        self.tier = tier
        self.cooldown = cooldown
        self.reason = reason
        self.wait = wait  # seconds left on the user's own cooldown

    def __repr__(self):
        return (f"<Decision allowed={self.allowed} tier={self.tier!r} "
                f"reason={self.reason!r} wait={self.wait:.0f}s>")


def parse_badges(tag: str) -> list:
    """'moderator/1,subscriber/12,vip/1' -> ['moderator','subscriber','vip']"""
    out = []
    for item in (tag or "").split(","):
        name = item.split("/", 1)[0].strip()
        if name:
            out.append(name)
    return out


def tier_from_badges(badges: str) -> str | None:
    """Highest role the user's badges prove, or None for a plain chatter.

    Badge order in the tag is not privilege order, so scan the tier table
    instead: a moderator who is also a subscriber is a moderator.
    """
    have = set(parse_badges(badges))
    for badge, tier in _BADGE_TIERS:
        if badge in have:
            return tier
    return None


def clean_login(text: str) -> str:
    """Strip the decoration chat puts around a Twitch login.

    Typing '@' in chat opens the mention picker, so '!twitch @name' is how
    people naturally ask about someone. '#' and a pasted twitch.tv URL are the
    other two. None of those characters can appear in a real login - Twitch
    allows only [a-z0-9_] - so removing them can never damage a name.

    Deliberately does NOT lowercase. Callers that need a canonical form
    lower-case themselves; the ones that echo the name back to chat should
    keep the spelling the viewer actually typed.
    """
    text = " ".join((text or "").split()).strip()
    if "/" in text:                       # a pasted twitch.tv/name URL
        text = text.rstrip("/").rsplit("/", 1)[-1]
    return text.lstrip("#@").strip()


def _get(url: str, params: dict, token: str, client_id: str, timeout: float = 5.0):
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v})
    req = urllib.request.Request(
        f"{url}?{qs}" if qs else url,
        headers={
            "Client-Id": client_id,
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


class Helix:
    """The two Helix reads access control needs, with caching."""

    def __init__(self, client_id: str, token: str, broadcaster_id: str = "",
                 cache_seconds: float = 21600.0, timeout: float = 5.0):
        self.client_id = (client_id or "").strip()
        self.token = (token or "").strip()
        self.broadcaster_id = (broadcaster_id or "").strip()
        self.cache_seconds = float(cache_seconds)
        self.timeout = float(timeout)
        self._ids = {}       # login -> user id (ids never change)
        self._follows = {}   # user id -> (expires_at, followed_at or None)
        self.errors = 0
        # None = not probed yet, True/False = whether this token is actually
        # allowed to read the follower list. See self_test().
        self.authorised = None

    @property
    def usable(self) -> bool:
        return bool(self.client_id and self.token and self.broadcaster_id)

    def set_token(self, token: str) -> None:
        """Adopt a freshly refreshed token.

        Refreshing an access token invalidates the previous one, so a client
        left holding the old string gets a 401 on every call afterwards -
        which access control reports to chat as 'could not verify your follow
        status'. Nothing ever recovered from that, because the token is only
        handed over once, at construction.
        """
        new = (token or "").replace("oauth:", "").strip()
        if not new or new == self.token:
            return
        self.token = new
        self.authorised = None      # force a fresh permission probe
        self._follows.clear()

    def describe_token(self):
        """The account and scopes behind this token, per Twitch itself.

        The follower gate has several failure modes that all end in the same
        chat message, so read the token back from Twitch and say which one it
        is instead of guessing. None if Twitch will not validate it.
        """
        if not self.token:
            return None
        try:
            return auth.validate_token(self.token)
        except Exception as exc:
            self.errors += 1
            print(f"[access] could not validate the oauth token: {exc!r}",
                  flush=True)
            return None

    def is_live(self, broadcaster_id: str = ""):
        """True if the channel is streaming, False if it is not, None if the
        question could not be settled.

        Get Streams needs no scope, so the token the bot already holds is
        enough - no extra --login just to find out whether anyone is home.
        """
        bid = (broadcaster_id or self.broadcaster_id or "").strip()
        if not (bid and self.client_id and self.token):
            return None
        try:
            data = _get(f"{HELIX}/helix/streams", {"user_id": bid, "first": 1},
                        self.token, self.client_id, self.timeout)
        except urllib.error.HTTPError as exc:
            self.errors += 1
            print(f"[access] streams lookup HTTP {exc.code}", flush=True)
            return None
        except (urllib.error.URLError, OSError, ValueError):
            self.errors += 1
            return None
        return bool(data.get("data"))

    def moderator_of(self, broadcaster_id: str, user_id: str,
                     has_scope: bool = False):
        """True if this token's user moderates the channel, False if provably
        not, None if the question could not be answered.

        Twitch returns 401 both for a token missing
        moderation:read:moderators and for a requester who is neither
        broadcaster nor moderator, so 401 only settles anything once the
        caller has confirmed the scope is present.
        """
        if not (broadcaster_id and user_id):
            return None
        try:
            data = _get(f"{HELIX}/helix/moderation/moderators",
                        {"broadcaster_id": broadcaster_id, "user_id": user_id},
                        self.token, self.client_id, self.timeout)
        except urllib.error.HTTPError as exc:
            self.errors += 1
            return False if (exc.code == 401 and has_scope) else None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self.errors += 1
            print(f"[access] moderator lookup failed: {exc!r}", flush=True)
            return None
        return bool(data.get("data"))

    def user_id(self, login: str):
        # clean_login, not a bare strip: callers hand this a channel name, and
        # "#truckingwithdoc" is what config carries. Sending the '#' to Helix
        # gets a 400, which reads as "that channel does not exist" and quietly
        # disables every follow check.
        login = clean_login(login).lower()
        if not login:
            return None
        if login in self._ids:
            return self._ids[login]
        try:
            data = _get(f"{HELIX}/helix/users", {"login": login},
                        self.token, self.client_id, self.timeout)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self.errors += 1
            print(f"[access] helix users lookup failed for {login!r}: {exc!r}",
                  flush=True)
            return None
        users = data.get("data") or []
        uid = (users[0].get("id") if users else None) or None
        if uid:
            self._ids[login] = uid
        return uid

    def channel_profile(self, login: str):
        """What Twitch says about a channel, or None if there is no such user.

        Both calls are scope-free. Get Users answers with any app or user
        token, and Get Channel Followers returns the follower *count* even for
        a channel this token is not allowed to moderate - it is only the
        per-user rows that need moderator:read:followers. So this works with
        the token the bot already has, no re-login.
        """
        login = clean_login(login).lower()
        if not login or not (self.client_id and self.token):
            return None
        try:
            data = _get(f"{HELIX}/helix/users", {"login": login},
                        self.token, self.client_id, self.timeout)
        except urllib.error.HTTPError as exc:
            self.errors += 1
            print(f"[access] helix users HTTP {exc.code} for {login!r}",
                  flush=True)
            return None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self.errors += 1
            print(f"[access] helix users lookup failed for {login!r}: {exc!r}",
                  flush=True)
            return None
        users = data.get("data") or []
        if not users:
            return None
        user = users[0]
        profile = {
            "id": str(user.get("id") or ""),
            "login": user.get("login") or login,
            "display_name": user.get("display_name") or login,
            "bio": " ".join((user.get("description") or "").split()),
            "broadcaster_type": (user.get("broadcaster_type") or "").lower(),
            "created_at": user.get("created_at") or "",
            "followers": None,
        }
        if profile["id"]:
            try:
                follows = _get(f"{HELIX}/helix/channels/followers",
                               {"broadcaster_id": profile["id"], "first": 1},
                               self.token, self.client_id, self.timeout)
                total = follows.get("total")
                profile["followers"] = int(total) if total is not None else None
            except (urllib.error.HTTPError, urllib.error.URLError, OSError,
                    ValueError, TypeError):
                # The count is a nicety. Never fail the whole lookup over it.
                pass
        return profile

    def followed_at(self, user_id: str):
        """ISO-8601 follow time, '' if not following, None if unknown."""
        if not user_id:
            return None
        hit = self._follows.get(user_id)
        if hit and hit[0] > time.time():
            return hit[1]
        try:
            data = _get(f"{HELIX}/helix/channels/followers",
                        {"broadcaster_id": self.broadcaster_id, "user_id": user_id},
                        self.token, self.client_id, self.timeout)
        except urllib.error.HTTPError as exc:
            self.errors += 1
            print(f"[access] helix followers HTTP {exc.code} — is the "
                  f"'moderator:read:followers' scope on the token? Run "
                  f"'python3 bot.py --login' to re-authorise.", flush=True)
            return None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self.errors += 1
            print(f"[access] helix followers lookup failed: {exc!r}", flush=True)
            return None
        rows = data.get("data") or []
        if rows:
            value = rows[0].get("followed_at") or ""
        elif self.authorised is not True:
            # Twitch returns the total count and an EMPTY data array when the
            # token is not the broadcaster or a moderator, or is missing
            # moderator:read:followers. Reading that as "does not follow" is
            # what made every real follower get turned away. An empty list is
            # only evidence of a non-follow once the permission probe has
            # actually succeeded, so an unfinished probe also means unknown.
            value = None
        else:
            value = ""
        if value is None:
            return None
        # A confirmed follow can be cached for good; a non-follow is cached
        # only briefly so a new follower isn't locked out for hours.
        ttl = self.cache_seconds if value else min(900.0, self.cache_seconds)
        self._follows[user_id] = (time.time() + ttl, value)
        return value

    def self_test(self):
        """Probe once whether this token may read the follower list, and say
        what is wrong if it may not. Without this the only symptom is every
        follower being told they don't follow the channel."""
        if not self.usable:
            self.authorised = False
            print("[access] follower check unavailable: missing client_id, "
                  "oauth token or broadcaster_id.", flush=True)
            return False
        try:
            data = _get(f"{HELIX}/helix/channels/followers",
                        {"broadcaster_id": self.broadcaster_id, "first": 1},
                        self.token, self.client_id, self.timeout)
        except urllib.error.HTTPError as exc:
            self.authorised = False
            self.errors += 1
            print(f"[access] follower check failed: HTTP {exc.code}.", flush=True)
            if exc.code == 401:
                print("[access]   The token cannot read this channel's followers. "
                      "Either it is missing the moderator:read:followers scope "
                      "(run 'python3 bot.py --login' to re-authorise) or the bot "
                      "account is not the broadcaster or a moderator of it.",
                      flush=True)
            return False
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self.authorised = None   # transient - do not conclude anything
            self.errors += 1
            print(f"[access] follower check could not reach Helix: {exc!r}",
                  flush=True)
            return False
        rows = data.get("data") or []
        total = data.get("total")
        if not rows:
            # 200 OK with an empty list but a real total = no permission.
            self.authorised = False
            print(f"[access] follower check returned no rows (total={total}). "
                  "Twitch only returns the list to the broadcaster or a "
                  "moderator holding moderator:read:followers.", flush=True)
            return False
        self.authorised = True
        print(f"[access] follower check OK - token may read this channel's "
              f"followers (total={total}).", flush=True)
        return True


def _iso_to_epoch(stamp: str):
    if not stamp:
        return None
    try:
        return time.mktime(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")) - \
            time.timezone
    except (ValueError, OverflowError):
        return None


class AccessControl:
    """Per-user rate limiting by role, with a follower gate for everyone else.

    `cooldown_seconds` in config stays in force as a *per-channel* floor: it
    protects the upstream sources (Wikipedia rate-limits by IP), and per-user
    limits alone would not stop twenty different viewers each firing once.
    """

    def __init__(self, cfg: dict, helix: Helix | None = None):
        tiers = dict(DEFAULT_TIER_COOLDOWNS)
        for k, v in (cfg.get("tier_cooldowns") or {}).items():
            try:
                tiers[str(k).lower()] = float(v)
            except (TypeError, ValueError):
                continue
        self.tiers = tiers
        self.min_follow_age = float(cfg.get("min_follow_age_seconds",
                                            DEFAULT_MIN_FOLLOW_AGE))
        self.on_failure = str(cfg.get("follower_check_failure", "deny")).lower()
        self.enabled = bool(cfg.get("access_control", True))
        self.helix = helix
        self._last = {}   # login -> timestamp of that user's last accepted use

    def tier_for(self, login: str, badges: str):
        """(tier, follow_age_seconds_or_None, checked: bool)."""
        tier = tier_from_badges(badges)
        if tier:
            return tier, None, False
        # Everything past here is an attempt to verify follow status. `checked`
        # is True whenever we tried and could not settle it — including when
        # Helix is not configured at all — so the caller can apply
        # "follower_check_failure" instead of wrongly labelling the user a
        # non-follower.
        if not self.helix or not self.helix.usable:
            return None, None, True
        uid = self.helix.user_id(login)
        if not uid:
            return None, None, True
        stamp = self.helix.followed_at(uid)
        if stamp is None:
            return None, None, True
        if not stamp:
            return "none", None, True
        started = _iso_to_epoch(stamp)
        age = (time.time() - started) if started else None
        return "follower", age, True

    def check(self, login: str, badges: str, now: float | None = None):
        now = time.time() if now is None else now
        if not self.enabled:
            return Decision(True, "anyone", 0.0, "access control disabled")

        tier, follow_age, checked = self.tier_for(login, badges)

        if tier is None and checked:
            # We tried to verify follow status and could not.
            if self.on_failure == "allow":
                return Decision(True, "unknown", self.tiers.get("follower", 300.0),
                                "follower check unavailable, allowing")
            return Decision(False, "unknown", 0.0,
                            "could not verify your follow status")

        if tier is None or tier == "none":
            return Decision(False, "non-follower", 0.0,
                            "only followers of the channel can use that")

        if tier == "follower" and (follow_age is None
                                   or follow_age < self.min_follow_age):
            return Decision(False, "new-follower", 0.0,
                            "you need to have been following for over a day")

        cooldown = self.tiers.get(tier, self.tiers.get("follower", 300.0))
        key = (login or "").lower()
        last = self._last.get(key)
        if last is not None and now - last < cooldown:
            return Decision(False, tier, cooldown, "still on cooldown",
                            wait=cooldown - (now - last))
        return Decision(True, tier, cooldown, "ok")

    def commit(self, login: str, now: float | None = None) -> None:
        """Record that this user's request was accepted."""
        self._last[(login or "").lower()] = time.time() if now is None else now
