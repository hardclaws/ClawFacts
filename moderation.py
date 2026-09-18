"""Moderation on request: a moderator asks, the bot does it.

Two Twitch facts decide the shape of this module, and both are dates:

* On 18 February 2023 Twitch switched off every chat command sent over
  IRC. ``PRIVMSG #chan :/ban somebody`` is accepted by the server and
  does nothing at all - no error, no ban. The only way left is Helix:
  ``POST /helix/moderation/bans`` (a permanent ban when the body has no
  ``duration``, a timeout when it has one) and ``DELETE`` on the same
  path to undo it.
* The same change took whispers away from IRC, in both directions. A
  reply to a moderator's private message therefore goes out through
  ``POST /helix/whispers``, and arriving at all is Twitch's business,
  not ours - so every command here also works typed in the channel.

Both calls need a *user* access token - the bot's own - and both need
``moderator_id`` to be the bot's user id, because Twitch checks that the
token and the moderator are the same account. That is why the bot has to
be a moderator of the channel: Helix returns 403 for anyone else. The
scopes are ``moderator:manage:banned_users`` (and
``user:manage:whispers`` to answer privately); adding a scope means the
stored token no longer carries it, so ``python3 bot.py --login`` has to
be run once after upgrading.

Who is allowed to ask is decided by bot.py, not here: badges in the
channel, the channel's own ``mod_logins`` list for a private message.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

HELIX = "https://api.twitch.tv"
USER_AGENT = "ClawFacts/1.0 (Twitch fun-fact bot)"

#: The scopes this module needs. auth.SCOPES carries them; they are named
#: here too so a 401 can say exactly which one to go and get.
BAN_SCOPE = "moderator:manage:banned_users"
#: Send Whisper wants user:MANAGE:whispers, not user:write:whispers - the
#: reference page names it in both the Authorization section and the 401.
#: The sender also has to have a verified phone number, or Twitch 401s.
WHISPER_SCOPE = "user:manage:whispers"

#: A Twitch login is [a-z0-9_], 4-25 characters. Checked before anything
#: is sent: a stray '@' or a pasted sentence must never reach the API as
#: a name to ban.
LOGIN_RE = re.compile(r"^[A-Za-z0-9_]{2,25}$")

MAX_REASON = 500            # Twitch's limit on the reason text
MAX_TIMEOUT = 1209600       # 14 days - the longest timeout Twitch allows
DEFAULT_TIMEOUT = 600

_DURATION = re.compile(r"^(\d+(?:\.\d+)?)\s*([smhd]?)$", re.IGNORECASE)
_UNIT = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text, default: int = DEFAULT_TIMEOUT):
    """'600', '10m', '2h', '1d' -> seconds; '' -> default; junk -> None."""
    text = (text or "").strip()
    if not text:
        return default
    m = _DURATION.match(text)
    if not m:
        return None
    try:
        secs = int(float(m.group(1)) * _UNIT[m.group(2).lower()])
    except (ValueError, KeyError):
        return None
    if secs < 1:
        return None
    return min(secs, MAX_TIMEOUT)


def human_duration(secs) -> str:
    """600 -> '10 min'; 90 -> '1 min'; 7200 -> '2 h'."""
    try:
        secs = int(secs)
    except (TypeError, ValueError):
        return "?"
    if secs >= 86400:
        return f"{secs // 86400} day" + ("s" if secs >= 172800 else "")
    if secs >= 3600:
        return f"{secs // 3600} h"
    mins = max(1, round(secs / 60))
    return f"{mins} min"


class Result:
    """What one moderation action did, in words a moderator can read."""

    __slots__ = ("ok", "note", "code", "action", "target")

    def __init__(self, ok, note, code=0, action="", target=""):
        self.ok = bool(ok)
        self.note = note
        self.code = code
        self.action = action
        self.target = target

    def __repr__(self):
        return (f"<Result ok={self.ok} action={self.action!r} "
                f"target={self.target!r} code={self.code} note={self.note!r}>")


class Moderator:
    """The bot's hands in the channel: ban, timeout, unban, whisper."""

    def __init__(self, helix, nick: str = "", log=None, timeout: float = 8.0):
        # access.Helix carries the client id, the live token, the
        # broadcaster's id, the login -> id cache and the token refresher.
        # Reusing it means a refreshed token reaches this class too.
        self.helix = helix
        self.nick = (nick or "").strip()
        self.log = log or (lambda line: print(line, flush=True))
        self.timeout = timeout
        self.errors = 0
        #: The bot's own user id, i.e. Helix's moderator_id. Set by the bot
        #: from the USERSTATE line Twitch sends on join - no API call - and
        #: resolved through Helix if it was never seen.
        self.moderator_id = ""
        #: Recent actions, newest first, for the console and the panel.
        self.actions = []
        self.last_403 = 0.0

    # ---- readiness ----------------------------------------------------
    @property
    def usable(self) -> bool:
        h = self.helix
        return bool(h is not None and h.client_id and h.token
                    and h.broadcaster_id)

    def problem(self) -> str:
        """Why an action could not be attempted, or '' when it could."""
        h = self.helix
        if h is None:
            return "no Twitch login"
        if not h.client_id:
            return "client_id is empty"
        if not self._token():
            return "the bot is not logged in (run python3 bot.py --login)"
        if not h.broadcaster_id:
            return "the channel's user id is not resolved yet"
        return ""

    def _token(self) -> str:
        """The bearer token, without the ``oauth:`` prefix IRC needs.

        access.Helix keeps it bare already; stripping again costs nothing
        and stops a caller who handed over the IRC form from getting a 401
        on every moderation call.
        """
        return (getattr(self.helix, "token", "") or "").replace(
            "oauth:", "").strip()

    def own_id(self) -> str:
        """The bot's own user id - Helix's moderator_id."""
        if self.moderator_id:
            return self.moderator_id
        if self.helix is None or not self.nick:
            return ""
        try:
            self.moderator_id = self.helix.user_id(self.nick) or ""
        except Exception:
            self.moderator_id = ""
        return self.moderator_id

    def set_own_id(self, user_id: str) -> None:
        user_id = str(user_id or "").strip()
        if user_id:
            self.moderator_id = user_id

    # ---- the HTTP half ------------------------------------------------
    def _request(self, method: str, path: str, params: dict = None,
                 body: dict = None):
        """One Helix call, retrying once after a token refresh on a 401.

        Returns (status, payload). Raises urllib.error.HTTPError with the
        provider's own message attached as ``.note`` so a 400 can say
        'the user is already banned' instead of a bare number.
        """
        h = self.helix
        qs = urllib.parse.urlencode({k: v for k, v in (params or {}).items()
                                     if v not in (None, "")})
        url = HELIX + path + ("?" + qs if qs else "")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        last = None
        for attempt in (0, 1):
            req = urllib.request.Request(
                url, data=data, method=method,
                headers={
                    "Client-Id": h.client_id,
                    "Authorization": f"Bearer {self._token()}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": USER_AGENT,
                })
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    status = getattr(resp, "status", None) \
                        or getattr(resp, "code", 200)
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace").strip()
                except Exception:
                    pass
                exc.note = detail[:400]
                last = exc
                # A 401 is usually a token that died early; ask for a new
                # one and try once more, exactly as access.py does.
                if exc.code == 401 and attempt == 0 \
                        and getattr(h, "on_unauthorized", None) is not None \
                        and "scope" not in detail.lower():
                    try:
                        h.on_unauthorized()
                        continue
                    except Exception:
                        pass
                raise
            except (urllib.error.URLError, OSError) as exc:
                self.errors += 1
                raise
            try:
                payload = json.loads(raw.decode("utf-8", "replace")) \
                    if raw else {}
            except ValueError:
                payload = {}
            return status, payload
        raise last                                  # pragma: no cover

    @staticmethod
    def _message(exc) -> str:
        detail = getattr(exc, "note", "") or ""
        try:
            data = json.loads(detail) if detail else {}
        except ValueError:
            data = {}
        # "message" is the specific one ("...is already banned"); "error"
        # is only the HTTP phrase ("Bad Request"), so it is the fallback.
        err = data.get("message") or data.get("error") or ""
        if isinstance(err, str) and err:
            return err[:200]
        return (detail[:200] or f"HTTP {exc.code}")

    def _note(self, action: str, target: str, ok: bool, text: str,
              code: int = 0) -> Result:
        res = Result(ok, text, code=code, action=action, target=target)
        self.actions.insert(0, (time.time(), action, target, ok, text))
        del self.actions[50:]
        self.log(f"[mod] {action} {target or '?'}: {text}")
        return res

    # ---- the actions --------------------------------------------------
    def ban(self, login: str, reason: str = "", duration=None) -> Result:
        """Ban (no duration) or time out (duration in seconds) one viewer."""
        login = (login or "").strip().lower()
        action = "timeout" if duration else "ban"
        if not LOGIN_RE.match(login):
            return self._note(action, login, False,
                              "that is not a Twitch username")
        problem = self.problem()
        if problem:
            return self._note(action, login, False, problem)
        uid = self.helix.user_id(login)
        if not uid:
            return self._note(action, login, False,
                              f"Twitch has no account named {login}")
        own = self.own_id()
        if not own:
            return self._note(
                action, login, False,
                "I could not work out my own Twitch id - check client_id "
                "and that I am logged in")
        data = {"user_id": str(uid)}
        if duration:
            data["duration"] = max(1, min(int(duration), MAX_TIMEOUT))
        reason = (reason or "").strip()[:MAX_REASON]
        if reason:
            data["reason"] = reason
        try:
            status, payload = self._request(
                "POST", "/helix/moderation/bans",
                {"broadcaster_id": self.helix.broadcaster_id,
                 "moderator_id": own}, {"data": data})
        except urllib.error.HTTPError as exc:
            return self._note(action, login, False,
                              self._explain(exc, login), exc.code)
        except Exception as exc:
            self.errors += 1
            return self._note(action, login, False,
                              f"could not reach Twitch: {exc!r}")
        rows = payload.get("data") or []
        end = (rows[0].get("end_time") if rows else None) or None
        if duration:
            what = (f"timed out for {human_duration(duration)}"
                    if not end else f"timed out until {end}")
        else:
            what = "banned"
        return self._note(action, login, True,
                          f"{login} {what}"
                          + (f" ({reason})" if reason else ""), status)

    def unban(self, login: str) -> Result:
        """Lift a ban, or end a timeout early."""
        login = (login or "").strip().lower()
        if not LOGIN_RE.match(login):
            return self._note("unban", login, False,
                              "that is not a Twitch username")
        problem = self.problem()
        if problem:
            return self._note("unban", login, False, problem)
        uid = self.helix.user_id(login)
        if not uid:
            return self._note("unban", login, False,
                              f"Twitch has no account named {login}")
        own = self.own_id()
        if not own:
            return self._note("unban", login, False,
                              "I could not work out my own Twitch id")
        try:
            status, _payload = self._request(
                "DELETE", "/helix/moderation/bans",
                {"broadcaster_id": self.helix.broadcaster_id,
                 "moderator_id": own, "user_id": uid})
        except urllib.error.HTTPError as exc:
            return self._note("unban", login, False,
                              self._explain(exc, login), exc.code)
        except Exception as exc:
            self.errors += 1
            return self._note("unban", login, False,
                              f"could not reach Twitch: {exc!r}")
        return self._note("unban", login, True, f"{login} unbanned", status)

    def whisper(self, login: str, text: str) -> bool:
        """Answer a moderator privately, through Helix.

        Whisper-over-IRC was switched off with the rest of the chat
        commands, so this is the only way to reply to a private ask - and
        it needs user:manage:whispers plus a recipient whose privacy
        settings allow it. False (with a console line) when it did not
        arrive; the caller decides whether to say it in the room instead.
        """
        login = (login or "").strip().lower()
        if not (LOGIN_RE.match(login) and text):
            return False
        problem = self.problem()
        if problem:
            self.log(f"[mod] cannot whisper {login}: {problem}")
            return False
        to = self.helix.user_id(login)
        own = self.own_id()
        if not (to and own):
            self.log(f"[mod] cannot whisper {login}: unknown account")
            return False
        try:
            _status, _payload = self._request(
                "POST", "/helix/whispers",
                {"from_user_id": own, "to_user_id": to},
                {"message": text[:500]})
        except urllib.error.HTTPError as exc:
            self.log(f"[mod] whisper to {login} failed (HTTP {exc.code}): "
                     f"{self._message(exc)}")
            return False
        except Exception as exc:
            self.log(f"[mod] whisper to {login} failed: {exc!r}")
            return False
        return True

    # ---- error translation -------------------------------------------
    def _explain(self, exc, login: str) -> str:
        """Twitch's code for a moderation failure, in plain words."""
        detail = self._message(exc)
        low = detail.lower()
        if exc.code in (401, 403) and ("scope" in low or "unauthorized" in low
                                       or exc.code == 401):
            if "scope" in low:
                return (f"my login is missing the {BAN_SCOPE} scope - run "
                        f"python3 bot.py --login once, then restart")
            self.last_403 = time.time()
            return ("Twitch says I am not a moderator of this channel - "
                    f"ask the broadcaster to /mod {self.nick or 'the bot'}")
        if exc.code == 403:
            self.last_403 = time.time()
            return ("Twitch says I am not a moderator of this channel - "
                    f"ask the broadcaster to /mod {self.nick or 'the bot'}")
        if exc.code == 404:
            return f"Twitch has no account named {login}"
        if exc.code == 409:
            return "Twitch is already updating that ban - try again in a moment"
        if exc.code == 429:
            return "Twitch rate-limited the moderation API - wait a moment"
        if exc.code == 400:
            if "already banned" in low:
                return f"{login} is already banned"
            if "may not be banned" in low or "may not be put in a timeout" in low:
                return (f"Twitch will not let me do that to {login} - "
                        "moderators and the broadcaster cannot be banned")
            if "reason" in low:
                return "Twitch rejected the reason text (500 characters max)"
        return f"Twitch refused (HTTP {exc.code}): {detail[:120]}"
