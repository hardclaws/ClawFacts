"""The bot's admin panel: a small password-protected web page next to bot.py.

    python3 bot.py --admin-user me          # set (or reset) a login, once
    "admin_panel_enabled": true             # in config.json, then start the bot

Standard library only, like the rest of the bot: ``http.server`` in a daemon
thread inside the bot process, so it can read the bot's live state directly
(connected or not, what the chat AI is resting, the standing notice) and
flip the same switches a moderator flips in chat.

Why it exists: once the bot lives on a cloud VM there is no console window.
Restarting it, reading the log, pausing it during a phone call, editing
config.json - every one of those became an SSH session. The panel is that
console window, reachable from a phone.

Security model - the parts that matter:

* **Bound to loopback by default** (``admin_panel_bind: "127.0.0.1"``).
  Nothing on the public internet can reach it. Reach it over an SSH tunnel
  (``ssh -L 8477:127.0.0.1:8477 user@vm``) or set the bind to the VM's
  Tailscale address (``100.x.y.z``) so only devices on your tailnet see it.
  Binding to ``0.0.0.0`` is refused unless ``admin_panel_public: true`` is
  set explicitly, and then the panel says so loudly in the log.
* **Logins live in admin_users.json** as salted PBKDF2-SHA256 hashes,
  never as passwords. There is no default account: ``--admin-user`` must be
  run once on the server (it prompts for the password, or reads
  ``ADMIN_PANEL_PASSWORD`` for scripted setups).
* **Sessions** are random 256-bit cookies (``HttpOnly``, ``SameSite=Strict``,
  ``Secure`` when the panel is reached over HTTPS), expire after 12 idle
  hours, and are dropped by Logout and by any restart.
* **Failed logins lock the account** for 15 minutes after 5 misses, and
  every miss costs a second so a script cannot guess quickly even before
  the lock. Every login success or failure is one line in the bot's log.
* **Every state change is a POST with a per-session CSRF token**; GETs never
  change anything, so a link in a chat message cannot pause the bot.
* **Two roles.** ``admin`` sees and does everything. ``mod`` gets the
  operational switches - pause, notices, reminders, custom commands, haul,
  sub goal, speak-as-bot - but never the config editor, secrets, the
  restart button, the Twitch login, or viewer memory.
* **Secrets are masked** on the config page (``gsk_…ab12``) and a masked
  value posted back leaves the stored one untouched, so a saved form
  never blanks a key by accident.

The HTML is deliberately plain: one page per tab, forms, no JavaScript
framework, a few lines of script for the live log tail. It works in a phone
browser over a tunnel.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import html
import http.server
import ipaddress
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
import urllib.parse

import storage

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

DEFAULT_PORT = 8477
DEFAULT_BIND = "127.0.0.1"
USERS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "admin_users.json")

SESSION_IDLE_SECONDS = 12 * 3600
LOCKOUT_AFTER = 5
LOCKOUT_SECONDS = 15 * 60
PBKDF2_ROUNDS = 600_000
MIN_PASSWORD = 10

ROLES = ("admin", "mod")

#: config.json keys whose values are secrets: shown masked, and a masked
#: value posted back means "leave it alone".
SECRET_KEYS = frozenset({
    "oauth_token", "client_secret", "llm_api_key", "llm_fallback_key",
    "llm_fallback_api_key", "google_api_key", "serper_api_key",
    "tavily_api_key", "weatherapi_key",
})

#: config.json keys the panel never edits, because they are bound at
#: process start (a change would be a lie until the next restart is
#: obvious enough that the form hides them) or are the panel's own gate.
LOCKED_KEYS = frozenset({
    "admin_panel_enabled", "admin_panel_bind", "admin_panel_port",
    "admin_panel_public",
})

LOG_KEEP = 2000          # lines held in memory for the log tab
_STAMPED = re.compile(r"^\[\d{2}:\d{2}:\d{2}\] ")
MASK = "\u2022\u2022\u2022\u2022"


# ---------------------------------------------------------------------------
# the log ring: everything the bot prints, kept for the panel
# ---------------------------------------------------------------------------

class LogRing:
    """A bounded, thread-safe list of the bot's console lines.

    Installed as a tee on sys.stdout/stderr (alongside the existing log-file
    tee) so the panel shows exactly what the console shows, with a sequence
    number per line so the browser can ask for "everything after N".
    """

    def __init__(self, keep: int = LOG_KEEP):
        self.keep = keep
        self._lines: list = []          # [(seq, ts, text)]
        self._seq = 0
        self._lock = threading.Lock()
        self._partial = ""

    def write_text(self, data: str) -> None:
        with self._lock:
            data = self._partial + data
            parts = data.split("\n")
            self._partial = parts.pop()
            now = time.time()
            for line in parts:
                if not line.strip():
                    continue
                # The bot's own lines already start with [HH:MM:SS]; the
                # panel adds its own stamp, so drop theirs rather than
                # showing two clocks.
                if _STAMPED.match(line):
                    line = line[11:]
                self._seq += 1
                self._lines.append((self._seq, now, line))
            if len(self._lines) > self.keep:
                del self._lines[:len(self._lines) - self.keep]

    def since(self, seq: int, limit: int = 400) -> list:
        with self._lock:
            out = [row for row in self._lines if row[0] > seq]
        return out[-limit:]

    def tail(self, n: int = 200) -> list:
        with self._lock:
            return list(self._lines[-n:])

    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._seq


class _RingTee:
    """Wraps a stream so writes also land in the LogRing."""

    def __init__(self, stream, ring: LogRing):
        self._stream = stream
        self._ring = ring

    def write(self, data) -> int:
        self._stream.write(data)
        try:
            self._ring.write_text(data if isinstance(data, str) else str(data))
        except Exception:
            pass
        return len(data)

    def flush(self) -> None:
        self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


def install_log_ring(ring: LogRing) -> None:
    sys.stdout = _RingTee(sys.stdout, ring)
    sys.stderr = _RingTee(sys.stderr, ring)


# ---------------------------------------------------------------------------
# users and passwords
# ---------------------------------------------------------------------------

def _hash_password(password: str, salt: bytes | None = None,
                   rounds: int = PBKDF2_ROUNDS) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                                 rounds)
    return "pbkdf2_sha256$%d$%s$%s" % (
        rounds, base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"))


def _check_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_b64, digest_b64 = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        want = base64.b64decode(digest_b64)
        got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                                  int(rounds))
        return hmac.compare_digest(want, got)
    except (ValueError, TypeError):
        return False


class Users:
    """The admin_users.json file: {"users": {name: {hash, role, created}}}."""

    def __init__(self, path: str = USERS_PATH):
        self.path = path
        self._lock = threading.Lock()
        self._users = {}
        self._load()

    def _load(self) -> None:
        data = storage.load_json(self.path, default=None)
        users = (data or {}).get("users") if isinstance(data, dict) else None
        self._users = {
            str(name).lower(): row for name, row in (users or {}).items()
            if isinstance(row, dict) and row.get("hash")
        }

    def save(self) -> bool:
        ok = storage.save_json(self.path, {"version": 1, "users": self._users})
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return ok

    def __len__(self) -> int:
        return len(self._users)

    def names(self) -> list:
        return sorted(self._users)

    def role(self, name: str) -> str | None:
        row = self._users.get((name or "").lower())
        return row.get("role") if row else None

    def set(self, name: str, password: str, role: str = "admin") -> tuple:
        name = (name or "").strip().lower()
        if not name or not name.replace("_", "").replace("-", "").isalnum():
            return False, "the user name must be letters, digits, _ or -"
        if len(password or "") < MIN_PASSWORD:
            return False, f"the password must be at least {MIN_PASSWORD} characters"
        if role not in ROLES:
            return False, f"the role must be one of {', '.join(ROLES)}"
        with self._lock:
            old = self._users.get(name) or {}
            self._users[name] = {
                "hash": _hash_password(password),
                "role": role,
                "created": old.get("created") or time.time(),
                "changed": time.time(),
            }
            saved = self.save()
        return saved, ("saved" if saved else "could not write admin_users.json")

    def delete(self, name: str) -> bool:
        with self._lock:
            if (name or "").lower() not in self._users:
                return False
            del self._users[name.lower()]
            return self.save()

    def verify(self, name: str, password: str) -> str | None:
        """The user's role if the password matches, else None."""
        row = self._users.get((name or "").lower())
        if not row:
            # Burn the same time as a real check so a missing user cannot be
            # told from a wrong password by the clock.
            _check_password(password or "", _hash_password("x"))
            return None
        if _check_password(password or "", row.get("hash", "")):
            return row.get("role") or "admin"
        return None


def set_user_interactive(name: str, role: str = "admin",
                         path: str = USERS_PATH) -> int:
    """`bot.py --admin-user NAME [--role mod]`: prompt for a password
    twice (or read ADMIN_PANEL_PASSWORD) and store it."""
    users = Users(path)
    pw = os.environ.get("ADMIN_PANEL_PASSWORD", "")
    if not pw:
        try:
            pw = getpass.getpass(f"Password for panel user '{name}': ")
            again = getpass.getpass("Again: ")
        except (EOFError, KeyboardInterrupt):
            print("\n[admin] cancelled")
            return 1
        if pw != again:
            print("[admin] the passwords do not match; nothing saved")
            return 1
    ok, why = users.set(name, pw, role)
    if not ok:
        print(f"[admin] {why}")
        return 1
    print(f"[admin] {role} '{name.lower()}' saved to {path} "
          f"({len(users)} user{'s' if len(users) != 1 else ''} total).")
    print("[admin] Enable the panel with \"admin_panel_enabled\": true in "
          "config.json and restart the bot.")
    return 0


# ---------------------------------------------------------------------------
# sessions, lockouts, CSRF
# ---------------------------------------------------------------------------

class Sessions:
    def __init__(self, idle: float = SESSION_IDLE_SECONDS):
        self.idle = idle
        self._lock = threading.Lock()
        self._live = {}       # token -> {user, role, csrf, seen}
        self._fails = {}      # user -> [timestamps]

    def open(self, user: str, role: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._live[token] = {"user": user, "role": role,
                                 "csrf": secrets.token_urlsafe(24),
                                 "seen": time.time()}
        return token

    def get(self, token: str) -> dict | None:
        if not token:
            return None
        with self._lock:
            row = self._live.get(token)
            if not row:
                return None
            if time.time() - row["seen"] > self.idle:
                del self._live[token]
                return None
            row["seen"] = time.time()
            return dict(row)

    def close(self, token: str) -> None:
        with self._lock:
            self._live.pop(token, None)

    def close_user(self, user: str) -> None:
        with self._lock:
            for tok in [t for t, r in self._live.items() if r["user"] == user]:
                del self._live[tok]

    def count(self) -> int:
        with self._lock:
            return len(self._live)

    # -- lockout ----------------------------------------------------------
    def locked_for(self, user: str) -> float:
        with self._lock:
            now = time.time()
            hits = [t for t in self._fails.get(user, []) if now - t < LOCKOUT_SECONDS]
            self._fails[user] = hits
            if len(hits) >= LOCKOUT_AFTER:
                return LOCKOUT_SECONDS - (now - hits[-LOCKOUT_AFTER])
            return 0.0

    def note_failure(self, user: str) -> int:
        with self._lock:
            self._fails.setdefault(user, []).append(time.time())
            return len(self._fails[user])

    def clear_failures(self, user: str) -> None:
        with self._lock:
            self._fails.pop(user, None)


# ---------------------------------------------------------------------------
# what the panel can see and do: one object between HTTP and the bot
# ---------------------------------------------------------------------------

class BotControl:
    """Every read and every switch the panel offers, in one place, so the
    HTTP layer has no idea how the bot is built and the tests can drive the
    panel against a stub.

    `bot` is the running TwitchBot (or None before it exists); `cfg` is the
    live config dict; `config_path` is the file the config tab edits;
    `restart` is a callable that ends the process (systemd brings it back).
    """

    def __init__(self, bot=None, cfg: dict | None = None,
                 config_path: str = "config.json", ring: LogRing | None = None,
                 restart=None, started: float | None = None):
        self.bot = bot
        self.cfg = cfg if cfg is not None else {}
        self.config_path = config_path
        self.ring = ring or LogRing()
        self._restart = restart
        self.started = started or time.time()
        self.actions: list = []      # [(ts, user, text)] the audit trail

    # -- helpers ----------------------------------------------------------
    def audit(self, user: str, text: str) -> None:
        self.actions.append((time.time(), user, text))
        del self.actions[:-200]
        print(f"[admin] {user}: {text}", flush=True)

    def _log(self, text: str) -> None:
        bot = self.bot
        if bot is not None and hasattr(bot, "_log"):
            bot._log(text)
        else:
            print(text, flush=True)

    # -- status -----------------------------------------------------------
    def status(self) -> dict:
        bot = self.bot
        cfg = self.cfg
        out = {
            "channel": cfg.get("channel", ""),
            "nick": cfg.get("nick", ""),
            "uptime": time.time() - self.started,
            "connected": bool(bot is not None and getattr(bot, "sock", None)),
            "paused": bool(getattr(bot, "paused", False)),
            "is_mod": getattr(bot, "_bot_is_mod", None),
            "chat_ai": bool(cfg.get("chat_ai_enabled", False)),
            "so_off": bool(getattr(bot, "_so_off", False)),
            "cb_off": bool(getattr(bot, "_cb_ambient_off", False)),
            "beef_off": bool(getattr(bot, "_beef_off", False)),
            "notice": None,
            "notice_minutes": float(cfg.get("chat_ai_notice_minutes", 20) or 0),
            "pending": 0,
            "reminders": 0,
            "custom_cmds": 0,
            "spice": cfg.get("spice", "clean"),
            "build": _build_id(),
        }
        held = getattr(bot, "_chat_ai_notice", None)
        if held:
            text, since, told = held
            age = time.time() - since
            if age < out["notice_minutes"] * 60:
                out["notice"] = {"text": text, "age": age, "told": len(told)}
        # The working memory: the game being hosted and the counts kept.
        out["ongoing"] = {"activity": None, "tallies": []}
        going = getattr(bot, "_ongoing", None)
        if going is not None:
            try:
                out["ongoing"] = going.summary()
            except Exception:
                pass
        try:
            out["pending"] = bot._jobs.qsize()
        except Exception:
            pass
        try:
            out["reminders"] = len(bot.reminders)
        except Exception:
            pass
        try:
            out["custom_cmds"] = len(bot.custom_cmds)
        except Exception:
            pass
        out["llm"] = self.llm_status()
        out["login"] = self.login_status()
        return out

    def llm_status(self) -> dict:
        cfg = self.cfg
        base = (cfg.get("llm_base_url") or "").strip()
        low = base.lower()
        provider = ("OpenRouter" if "openrouter" in low else
                    "Groq" if "groq" in low else
                    "local Ollama" if ("localhost" in low or "11434" in low
                                       or "127.0.0.1" in low or "ollama" in low)
                    else (low or "not configured"))
        out = {"provider": provider, "model": cfg.get("llm_model") or "",
               "fallback_model": cfg.get("llm_fallback_model") or "",
               "fallback_base": cfg.get("llm_fallback_base_url") or "",
               # The ordered chain, one entry per provider: who would take
               # a line next, and whether their own breaker is open.
               "fallbacks": [],
               "configured": bool((cfg.get("llm_api_key") or "").strip()
                                  or provider == "local Ollama"),
               "resting": [], "breaker": 0.0, "fallback_breaker": 0.0,
               "on_fallback": False}
        try:
            import llm as llm_mod
            now = time.time()
            if base:
                out["provider"] = llm_mod.provider_name(base)
            for fbase, _fkey, fmodels in llm_mod.fallback_providers(cfg):
                left = max(0.0, llm_mod._FALLBACK_DISABLED_BY_BASE.get(
                    fbase, 0.0) - now)
                out["fallbacks"].append({
                    "provider": llm_mod.provider_name(fbase),
                    "base": fbase,
                    "model": ", ".join(fmodels),
                    "resting": left,
                })
            for (b, m), until in sorted(llm_mod._MODEL_DISABLED_UNTIL.items()):
                if until > now:
                    out["resting"].append({"model": m, "base": b,
                                           "left": until - now})
            out["breaker"] = max(0.0, llm_mod._DISABLED_UNTIL - now)
            out["fallback_breaker"] = max(0.0, llm_mod._FALLBACK_DISABLED_UNTIL - now)
            out["on_fallback"] = bool(llm_mod._ON_FALLBACK)
        except Exception:
            pass
        return out

    def login_status(self) -> dict:
        out = {"present": False, "login": "", "expires_in": None,
               "client_id_matches": None, "refresh": False}
        try:
            import auth
            tokens = auth.load_tokens()
        except Exception:
            tokens = None
        if not tokens:
            return out
        out["present"] = True
        out["login"] = tokens.get("login") or ""
        out["refresh"] = bool(tokens.get("refresh_token"))
        exp = float(tokens.get("expires_at") or 0)
        out["expires_in"] = (exp - time.time()) if exp else None
        cid = (self.cfg.get("client_id") or "").strip()
        out["client_id_matches"] = (tokens.get("client_id") == cid) if cid else None
        return out

    def reset_llm_rests(self, user: str) -> str:
        try:
            import llm as llm_mod
            llm_mod.reset_disable_state()
        except Exception as exc:
            return f"could not reset: {exc!r}"
        self.audit(user, "cleared the AI rate-limit rests and breakers")
        return "AI rests cleared - the next line tries every model again"

    # -- switches ---------------------------------------------------------
    def set_paused(self, on: bool, user: str) -> str:
        if self.bot is None:
            return "the bot is not running yet"
        self.bot.paused = bool(on)
        self.audit(user, f"bot {'PAUSED' if on else 'resumed'} (!bot {'off' if on else 'on'})")
        return f"bot is {'OFF' if on else 'ON'}"

    def set_switch(self, which: str, off: bool, user: str) -> str:
        if self.bot is None:
            return "the bot is not running yet"
        attr = {"so": "_so_off", "cb": "_cb_ambient_off",
                "beef": "_beef_off"}.get(which)
        if not attr:
            return "unknown switch"
        setattr(self.bot, attr, bool(off))
        label = {"so": "raid shoutouts", "cb": "doc's own chatter",
                 "beef": "!beef"}[which]
        self.audit(user, f"{label} {'OFF' if off else 'ON'}")
        return f"{label} {'OFF' if off else 'ON'}"

    # -- speaking ---------------------------------------------------------
    def say(self, text: str, user: str) -> str:
        text = " ".join((text or "").split())
        if not text:
            return "nothing to say"
        if len(text) > 450:
            return "keep it under 450 characters (Twitch's limit)"
        if self.bot is None:
            return "the bot is not running yet"
        if text.startswith("/") or text.startswith("."):
            return "slash commands are not allowed from here"
        self.bot._jobs.put(("", "", "", "say", text))
        self.audit(user, f"said: {text[:80]!r}")
        return "queued"

    def set_notice(self, text: str, user: str) -> str:
        if self.bot is None:
            return "the bot is not running yet"
        text = " ".join((text or "").split())
        if not text:
            self.bot._chat_ai_notice = None
            self.audit(user, "cleared the standing notice")
            return "notice cleared"
        if len(text) > 300:
            return "keep the notice under 300 characters"
        self.bot._chat_ai_notice = (text, time.time(), set())
        self.audit(user, f"standing notice: {text[:80]!r}")
        return "notice set"

    def end_game(self, user: str) -> str:
        if self.bot is None:
            return "the bot is not running yet"
        going = getattr(self.bot, "_ongoing", None)
        if going is None or not going.end():
            return "no game was running"
        try:
            self.bot._cancel_step_timer()
        except Exception:
            pass
        self.audit(user, "ended the game the bot was hosting")
        return "game ended"

    def drop_count(self, key: str, user: str) -> str:
        if self.bot is None:
            return "the bot is not running yet"
        going = getattr(self.bot, "_ongoing", None)
        if going is None or not going.drop_tally((key or "").strip()):
            return "no such count"
        self.audit(user, f"dropped the count {key!r}")
        return "count dropped"

    # -- reminders --------------------------------------------------------
    def reminders(self) -> list:
        try:
            return [r.as_dict() for r in self.bot.reminders.pending()]
        except Exception:
            return []

    def add_reminder(self, when: str, message: str, user: str) -> str:
        if self.bot is None:
            return "the bot is not running yet"
        item, why = self.bot.reminders.add(when, message, f"panel:{user}")
        if item is None:
            return why or "could not add"
        self.audit(user, f"reminder #{item.id} {item.label}: {item.message[:60]!r}")
        return f"reminder #{item.id} set {item.label}"

    def cancel_reminder(self, which: str, user: str) -> str:
        if self.bot is None:
            return "the bot is not running yet"
        n, why = self.bot.reminders.cancel(which)
        if why:
            return why
        self.audit(user, f"cancelled reminder {which}")
        return f"cancelled {n}"

    # -- custom commands --------------------------------------------------
    def custom_commands(self) -> list:
        try:
            cc = self.bot.custom_cmds
            return [(n, cc.get(n)) for n in cc.names()]
        except Exception:
            return []

    def set_custom(self, name: str, message: str, user: str) -> str:
        if self.bot is None:
            return "the bot is not running yet"
        cc = self.bot.custom_cmds
        name = (name or "").strip().lstrip("!").lower()
        if name in cc:
            ok, why = cc.edit(name, message)
        else:
            ok, why = cc.add(name, message)
        if ok and not cc.save():
            return "saved in memory but custom_commands.json could not be written"
        if ok:
            self.audit(user, f"custom command !{name}: {message[:60]!r}")
        return why

    def delete_custom(self, name: str, user: str) -> str:
        if self.bot is None:
            return "the bot is not running yet"
        cc = self.bot.custom_cmds
        ok, why = cc.delete((name or "").strip().lstrip("!"))
        if ok:
            cc.save()
            self.audit(user, f"deleted custom command !{name}")
        return why

    # -- haul / sub goal / persona ---------------------------------------
    def haul(self) -> dict:
        c = getattr(self.bot, "cargo", None)
        if c is None:
            return {"text": "", "set_by": "", "age": ""}
        return {"text": c.text, "set_by": c.set_by,
                "age": c.age() if c.text else ""}

    def set_haul(self, text: str, user: str) -> str:
        if self.bot is None:
            return "the bot is not running yet"
        c = self.bot.cargo
        if not (text or "").strip():
            ok, why = c.delete()
            if ok:
                self.audit(user, "cleared the haul")
            return "haul cleared" if ok else why
        ok, why = c.update(text)
        if not ok:
            return why
        c.set_by = f"panel:{user}"
        c.save()
        self.audit(user, f"haul: {c.text[:60]!r}")
        return "haul updated"

    def subgoal(self) -> dict:
        return dict(getattr(self.bot, "_subgoal", None) or {})

    def set_subgoal(self, goal: str, current: str, label: str, user: str) -> str:
        if self.bot is None:
            return "the bot is not running yet"
        bot = self.bot
        path = self.cfg.get("subgoal_state_path", "subgoal.json")
        label = " ".join((label or "").split())
        if not (goal or "").strip() and not label:
            with bot._subgoal_lock:
                bot._subgoal = {}
                bot._save_json_state(path, bot._subgoal)
            self.audit(user, "cleared the sub goal")
            return "sub goal cleared"
        try:
            g = int(goal)
            c = int(current or 0)
        except ValueError:
            return "goal and count must be whole numbers"
        if g < 2 or not label:
            return "the goal needs a number of 2 or more and a payoff"
        with bot._subgoal_lock:
            bot._subgoal = {"goal": g, "current": max(0, c), "label": label}
            bot._save_json_state(path, bot._subgoal)
        self.audit(user, f"sub goal {c}/{g}: {label[:60]!r}")
        return "sub goal saved"

    def persona(self) -> dict:
        p = dict(getattr(self.bot, "_persona", None) or {})
        try:
            import chatai
            p["choices"] = [(n, chatai.PERSONA_BLURBS.get(n, ""))
                            for n in sorted(chatai.PERSONAS)]
        except Exception:
            p["choices"] = []
        return p

    def set_persona(self, name: str, custom: str, user: str) -> str:
        if self.bot is None:
            return "the bot is not running yet"
        bot = self.bot
        path = self.cfg.get("persona_state_path", "persona.json")
        custom = " ".join((custom or "").split())
        if name == "custom":
            if not 12 <= len(custom) <= 300:
                return "describe the custom voice in 12-300 characters"
            try:
                import funfacts
                if funfacts._EXPLICIT.search(custom) or funfacts._TASTELESS.search(custom):
                    return "not that kind of stream"
            except Exception:
                pass
            bot._persona = {"name": "custom", "text": custom}
        else:
            try:
                import chatai
                canon = chatai.persona_name(name)
            except Exception:
                canon = "doc" if name == "doc" else None
            if not canon:
                return f"no voice called {name!r}"
            bot._persona = {"name": canon}
        bot._save_json_state(path, bot._persona)
        self.audit(user, f"voice set to {bot._persona.get('name')}")
        return f"voice set to {bot._persona.get('name')}"

    # -- viewer memory (admin) --------------------------------------------
    def memory_overview(self, limit: int = 200) -> list:
        mem = getattr(self.bot, "_memory", None)
        db = getattr(mem, "_db", None)
        if db is None:
            return []
        try:
            with mem._lock:
                rows = db.execute(
                    "SELECT nick, COUNT(*), MAX(ts) FROM memories GROUP BY nick"
                    " ORDER BY MAX(ts) DESC LIMIT ?", (limit,)).fetchall()
            return [{"nick": r[0], "count": r[1], "last": r[2]} for r in rows]
        except Exception:
            return []

    def memory_for(self, nick: str) -> list:
        mem = getattr(self.bot, "_memory", None)
        db = getattr(mem, "_db", None)
        if db is None or not nick:
            return []
        try:
            with mem._lock:
                rows = db.execute(
                    "SELECT ts, fact FROM memories WHERE nick = ? ORDER BY ts DESC",
                    (nick,)).fetchall()
            return [{"ts": r[0], "fact": r[1]} for r in rows]
        except Exception:
            return []

    def forget(self, nick: str, user: str) -> str:
        mem = getattr(self.bot, "_memory", None)
        if mem is None:
            return "memory is not available"
        dropped = mem.purge(nick)
        self.audit(user, f"forgot {nick} ({dropped} facts)")
        return f"forgot {nick}: {dropped} facts and all their lines"

    # -- beef -------------------------------------------------------------
    def beef_board(self) -> list:
        try:
            return [(n, dict(r)) for n, r in self.bot.beef_state.leaderboard(50)]
        except Exception:
            return []

    def reset_beef(self, user: str) -> str:
        st = getattr(self.bot, "beef_state", None)
        if st is None:
            return "beef state not available"
        st.players.clear()
        st.windows.clear()
        st.save()
        self.audit(user, "reset the beef leaderboard")
        return "beef leaderboard reset"

    # -- config (admin) ---------------------------------------------------
    def config_view(self) -> list:
        """[(key, shown_value, is_secret, kind)] for the editor; the file's
        own keys plus DEFAULTS the file omits, minus the `_comment` keys."""
        try:
            import bot as bot_mod
            defaults = bot_mod.DEFAULTS
        except Exception:
            defaults = {}
        on_disk = storage.load_json(self.config_path, default={}) or {}
        keys = [k for k in on_disk if not k.startswith("_")]
        keys += [k for k in defaults if k not in on_disk and not k.startswith("_")]
        rows = []
        for k in keys:
            if k in LOCKED_KEYS:
                continue
            v = on_disk.get(k, defaults.get(k))
            secret = k in SECRET_KEYS
            if secret:
                shown = _mask(v)
            elif k == "llm_fallback_providers":
                # A provider list can carry keys inline. Show it as JSON
                # like any other list, but with every key masked - and a
                # masked key posted back means "keep the stored one".
                shown = json.dumps(_mask_providers(v), ensure_ascii=False)
            elif isinstance(v, (dict, list)):
                shown = json.dumps(v, ensure_ascii=False)
            elif isinstance(v, bool):
                shown = "true" if v else "false"
            elif v is None:
                shown = ""
            else:
                shown = str(v)
            kind = ("bool" if isinstance(v, bool) else
                    "num" if isinstance(v, (int, float)) else
                    "json" if isinstance(v, (dict, list)) else "str")
            rows.append((k, shown, secret, kind, k in on_disk))
        return rows

    def save_config(self, form: dict, user: str) -> str:
        """Write the posted values back to config.json - atomically, keeping
        the comment keys, skipping masked secrets - and apply the runtime
        ones to the live cfg. Returns a message."""
        on_disk = storage.load_json(self.config_path, default=None)
        if not isinstance(on_disk, dict):
            on_disk = {}
        try:
            import bot as bot_mod
            defaults = bot_mod.DEFAULTS
        except Exception:
            defaults = {}
        changed = []
        known = set(defaults) | {k for k in on_disk if not k.startswith("_")}
        for key, raw in form.items():
            # Only real config keys: not the CSRF token, not the clear_*
            # checkboxes, not a comment key, never the panel's own gate.
            if key not in known or key in LOCKED_KEYS:
                continue
            raw = raw if isinstance(raw, str) else str(raw)
            if key in SECRET_KEYS:
                if form.get("clear_" + key) == "1":
                    raw = ""
                elif raw.strip() == "" or MASK in raw:
                    continue        # blank or masked = leave the stored one
            old = on_disk.get(key, defaults.get(key))
            new = _coerce(raw, old)
            if key == "llm_fallback_providers" and isinstance(new, list):
                new = _unmask_providers(new, old)
            if new == old and key in on_disk:
                continue
            on_disk[key] = new
            changed.append(key)
        if not changed:
            return "nothing changed"
        if not storage.save_json(self.config_path, on_disk):
            return "could not write config.json"
        opts = getattr(self.bot, "_opts", None)
        for key in changed:
            self.cfg[key] = on_disk[key]
            # The fact engine and chat AI read a snapshot of cfg taken at
            # construction (bot._opts); keep it in step so spice, the
            # message budget and the API keys change without a restart.
            if isinstance(opts, dict) and key in opts:
                opts[key] = on_disk[key]
        self.audit(user, "config saved: " + ", ".join(
            f"{k}={'(secret)' if k in SECRET_KEYS else on_disk[k]!r}"[:60]
            for k in changed))
        needs = [k for k in changed if k in RESTART_KEYS]
        note = (f" - restart the bot for {', '.join(needs)} to take effect"
                if needs else " - live")
        return f"saved {', '.join(changed)}{note}"

    def restart(self, user: str) -> str:
        self.audit(user, "RESTART requested from the panel")
        if self._restart is None:
            return "no restart hook - stop and start the bot yourself"
        threading.Timer(0.5, self._restart).start()
        return "restarting - the service manager brings it back in a few seconds"

    # -- tests / doctor ---------------------------------------------------
    def run_check(self, which: str) -> str:
        import subprocess
        here = os.path.dirname(os.path.abspath(__file__))
        if which == "fixes":
            cmd = [sys.executable, os.path.join(here, "check_fixes.py")]
        elif which == "doctor":
            cmd = [sys.executable, os.path.join(here, "bot.py"), "--doctor",
                   self.config_path]
        else:
            return "unknown check"
        try:
            run = subprocess.run(cmd, capture_output=True, text=True,
                                 timeout=180, cwd=here, encoding="utf-8",
                                 errors="replace")
            return (run.stdout or "") + (("\n" + run.stderr) if run.stderr else "")
        except Exception as exc:
            return f"could not run: {exc!r}"

    # -- twitch login from the panel (admin) ------------------------------
    def start_twitch_login(self, user: str) -> dict:
        """Begin a device-code login; the panel shows the code + URL and
        polls in the background until Twitch answers."""
        import auth
        cid = (self.cfg.get("client_id") or "").strip()
        if not cid:
            return {"error": "no client_id in config.json"}
        try:
            device = auth.start_device_flow(cid)
        except Exception as exc:
            return {"error": f"Twitch refused to start a login: {exc}"}
        state = {"code": device.get("user_code"),
                 "url": device.get("verification_uri") or auth.ACTIVATE_URL,
                 "done": False, "error": None, "login": None}
        self._login_state = state

        def poll():
            try:
                data = auth.poll_device_token(
                    cid, device.get("device_code"),
                    interval=int(device.get("interval") or 5),
                    timeout=int(device.get("expires_in") or 1800))
                token = auth._store_tokens(data, cid)
                state["login"] = (auth.load_tokens() or {}).get("login")
                bot = self.bot
                if bot is not None:
                    bot.cfg["oauth_token"] = token
                    self.cfg["oauth_token"] = token
                    try:
                        if bot._access.helix is not None:
                            bot._access.helix.set_token(token)
                    except Exception:
                        pass
                    # A new login only takes on the next connect.
                    try:
                        bot._close()
                    except Exception:
                        pass
                self.audit(user, f"Twitch login renewed as {state['login']}")
            except Exception as exc:
                state["error"] = str(exc)
            state["done"] = True

        threading.Thread(target=poll, name="panel-twitch-login",
                         daemon=True).start()
        self.audit(user, "started a Twitch device login")
        return dict(state)

    def twitch_login_state(self) -> dict | None:
        st = getattr(self, "_login_state", None)
        return dict(st) if st else None


#: Config keys read once at startup; a change is saved but needs a restart.
RESTART_KEYS = frozenset({
    "nick", "channel", "client_id", "client_secret", "host", "port",
    "use_tls", "memory_db_path", "persona_state_path", "subgoal_state_path",
    "beef_state_path", "log_file", "chat_ai_enabled", "chat_ai_names",
    "llm_api_key", "llm_base_url", "llm_model", "llm_fallback_key",
    "llm_fallback_base_url", "llm_fallback_model",
    "llm_fallback_providers", "llm_no_think",
    "tier_cooldowns", "access_control", "min_follow_age_seconds",
    "follower_check_failure", "prefix", "names_topup_enabled",
    "names_topup_hours",
})


def _mask_providers(value):
    """The provider list with any inline API key masked."""
    if not isinstance(value, list):
        return value
    out = []
    for entry in value:
        if isinstance(entry, dict) and entry.get("key"):
            entry = dict(entry)
            entry["key"] = _mask(entry.get("key"))
        out.append(entry)
    return out


def _unmask_providers(new, old):
    """A masked key posted back means 'keep the one already stored'.

    The editor shows every inline key masked, so saving an unrelated
    field would otherwise overwrite them with the mask itself.
    """
    stored = [e for e in (old if isinstance(old, list) else [])
              if isinstance(e, dict)]
    out = []
    for i, entry in enumerate(new):
        if isinstance(entry, dict) and MASK in str(entry.get("key") or ""):
            entry = dict(entry)
            entry["key"] = (stored[i].get("key", "")
                            if i < len(stored) else "")
        out.append(entry)
    return out


def _mask(value) -> str:
    v = str(value or "")
    if not v:
        return ""
    if len(v) <= 8:
        return MASK
    return v[:4] + MASK + v[-4:]


def _coerce(raw: str, old):
    """A form field is text; give it the type the old value had."""
    s = raw.strip()
    if isinstance(old, bool):
        return s.lower() in ("1", "true", "yes", "on")
    if isinstance(old, int) and not isinstance(old, bool):
        try:
            return int(s)
        except ValueError:
            try:
                return float(s)
            except ValueError:
                return old
    if isinstance(old, float):
        try:
            return float(s)
        except ValueError:
            return old
    if isinstance(old, (dict, list)):
        try:
            v = json.loads(s) if s else type(old)()
            return v if isinstance(v, type(old)) else old
        except ValueError:
            return old
    if old is None and s.lower() in ("true", "false"):
        return s.lower() == "true"
    return raw


def _build_id() -> str:
    try:
        import subprocess
        here = os.path.dirname(os.path.abspath(__file__))
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=3,
                              cwd=here).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def human(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    seconds = int(seconds)
    if seconds < 0:
        return "expired " + human(-seconds) + " ago"
    d, r = divmod(seconds, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def e(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


_CSS = """
:root{--bg:#0f1115;--card:#171a21;--ink:#e6e6e6;--mut:#9aa3ad;--acc:#5aa9ff;
--ok:#3ddc84;--bad:#ff5c5c;--warn:#ffb020;--line:#262b35}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 system-ui,Segoe UI,Roboto,
sans-serif;background:var(--bg);color:var(--ink)}a{color:var(--acc)}
header{display:flex;flex-wrap:wrap;gap:.5rem 1rem;align-items:center;padding:.7rem 1rem;
background:var(--card);border-bottom:1px solid var(--line);position:sticky;top:0}
header b{font-size:1.05rem}nav a{margin-right:.9rem;text-decoration:none;color:var(--mut)}
nav a.on{color:var(--ink);border-bottom:2px solid var(--acc)}
main{padding:1rem;max-width:1100px;margin:0 auto}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:1rem;margin:0 0 1rem}h2{margin:.2rem 0 .8rem;font-size:1.05rem}
.grid{display:grid;gap:1rem;grid-template-columns:repeat(auto-fit,minmax(280px,1fr))}
dl{display:grid;grid-template-columns:max-content 1fr;gap:.25rem .9rem;margin:0}
dt{color:var(--mut)}dd{margin:0;word-break:break-word}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}.mut{color:var(--mut)}
button,input[type=submit]{background:var(--acc);border:0;color:#04121f;font-weight:600;
padding:.5rem .9rem;border-radius:7px;cursor:pointer}button.danger{background:var(--bad);color:#fff}
button.quiet{background:#2a3140;color:var(--ink)}
input[type=text],input[type=password],input[type=number],textarea,select{width:100%;
background:#0c0e12;color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:.5rem}
textarea{min-height:4.5rem;font:inherit}label{display:block;margin:.5rem 0 .2rem;color:var(--mut)}
form.inline{display:inline}.row{display:flex;gap:.6rem;flex-wrap:wrap;align-items:center}
table{width:100%;border-collapse:collapse}td,th{padding:.35rem .5rem;border-bottom:1px solid var(--line);
text-align:left;vertical-align:top}th{color:var(--mut);font-weight:500}
pre{background:#0c0e12;border:1px solid var(--line);border-radius:8px;padding:.7rem;
overflow:auto;max-height:70vh;font:12.5px/1.4 ui-monospace,Consolas,monospace;white-space:pre-wrap}
.flash{background:#1d2a3a;border:1px solid #2d4a6d;padding:.6rem .8rem;border-radius:8px;margin-bottom:1rem}
.pill{display:inline-block;padding:.05rem .5rem;border-radius:99px;font-size:.8rem;background:#2a3140}
small{color:var(--mut)}.login{max-width:380px;margin:12vh auto}
"""

TABS = [("/", "Dashboard", "mod"), ("/log", "Log", "mod"),
        ("/chat", "Chat", "mod"), ("/reminders", "Reminders", "mod"),
        ("/commands", "Commands", "mod"), ("/stream", "Stream", "mod"),
        ("/memory", "Memory", "admin"), ("/config", "Config", "admin"),
        ("/system", "System", "admin")]


def page(title: str, body: str, session: dict | None, path: str,
         flash: str = "") -> str:
    nav = ""
    if session:
        role = session.get("role", "mod")
        nav = "".join(
            f'<a href="{p}" class="{"on" if p == path else ""}">{n}</a>'
            for p, n, need in TABS if role == "admin" or need == "mod")
        who = (f'<span class="mut" style="margin-left:auto">{e(session["user"])} '
               f'<span class="pill">{e(role)}</span></span>'
               f'<form class="inline" method="post" action="/logout">'
               f'<input type="hidden" name="csrf" value="{e(session["csrf"])}">'
               f'<button class="quiet">Logout</button></form>')
    else:
        who = ""
    fl = f'<div class="flash">{e(flash)}</div>' if flash else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{e(title)} - bot panel</title><style>{_CSS}</style></head>'
            f'<body><header><b>Bot panel</b><nav>{nav}</nav>{who}</header>'
            f'<main>{fl}{body}</main></body></html>')


def _csrf(session: dict) -> str:
    return f'<input type="hidden" name="csrf" value="{e(session["csrf"])}">'


def _ongoing_html(og: dict) -> str:
    """The game being hosted and the counts kept, as a short block."""
    a = og.get("activity")
    parts = []
    if a:
        parts.append(f'<p>Hosting <b>{e(a["what"])}</b> for {e(a["asked_by"])} '
                     f'<small>(since {human(time.time() - a["since"])} ago)</small><br>'
                     f'<small>{e(a["status"])}</small></p>')
    else:
        parts.append('<p class="mut">No game being hosted.</p>')
    tallies = og.get("tallies") or []
    if tallies:
        parts.append('<p>Counts: ' + "; ".join(
            f'<b>{e(t["label"])}</b>: {t["count"]}' for t in tallies) + '</p>')
    else:
        parts.append('<p class="mut">No counts being kept.</p>')
    return "".join(parts)


def _btn_form(session: dict, action: str, label: str, fields: dict | None = None,
              danger: bool = False, confirm: str = "") -> str:
    hidden = "".join(f'<input type="hidden" name="{e(k)}" value="{e(v)}">'
                     for k, v in (fields or {}).items())
    onsub = f' onsubmit="return confirm({json.dumps(confirm)})"' if confirm else ""
    cls = ' class="danger"' if danger else ""
    return (f'<form class="inline" method="post" action="{action}"{onsub}>'
            f'{_csrf(session)}{hidden}<button{cls}>{e(label)}</button></form>')


# ---------------------------------------------------------------------------
# the HTTP handler
# ---------------------------------------------------------------------------

class PanelHandler(http.server.BaseHTTPRequestHandler):
    server_version = "BotPanel/1"
    sys_version = ""
    # Set by PanelServer:
    control: BotControl = None
    users: Users = None
    sessions: Sessions = None

    # -- plumbing -----------------------------------------------------------
    def log_message(self, fmt, *args):      # quiet: the bot's log is enough
        pass

    def _cookie(self) -> str:
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == "panel_session":
                return v
        return ""

    def _secure(self) -> bool:
        return (self.headers.get("X-Forwarded-Proto", "").lower() == "https")

    def _send(self, body: str, status: int = 200, ctype: str = "text/html; charset=utf-8",
              extra: dict | None = None) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; style-src 'unsafe-inline'; "
                         "script-src 'unsafe-inline'; connect-src 'self'; "
                         "form-action 'self'; frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _redirect(self, where: str, flash: str = "", extra: dict | None = None) -> None:
        if flash:
            where += ("&" if "?" in where else "?") + "m=" + urllib.parse.quote(flash)
        self.send_response(303)
        self.send_header("Location", where)
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _form(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 256 * 1024:
            return {}
        raw = self.rfile.read(length).decode("utf-8", "replace")
        return {k: v[-1] for k, v in urllib.parse.parse_qs(
            raw, keep_blank_values=True).items()}

    def _session(self) -> dict | None:
        return self.sessions.get(self._cookie())

    def _set_cookie(self, token: str, clear: bool = False) -> dict:
        attrs = "; Path=/; HttpOnly; SameSite=Strict"
        if self._secure():
            attrs += "; Secure"
        if clear:
            return {"Set-Cookie": f"panel_session=; Max-Age=0{attrs}"}
        return {"Set-Cookie": f"panel_session={token}; Max-Age={SESSION_IDLE_SECONDS}{attrs}"}

    # -- GET ------------------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        path, q = url.path, urllib.parse.parse_qs(url.query)
        flash = (q.get("m") or [""])[0][:300]
        if path == "/healthz":
            return self._send("ok\n", ctype="text/plain")
        session = self._session()
        if path == "/login":
            return self._send(self._login_page(flash))
        if session is None:
            return self._redirect("/login")
        role = session["role"]
        admin_only = {p for p, _, need in TABS if need == "admin"}
        if path in admin_only and role != "admin":
            return self._send(page("Forbidden", "<p>That tab is for admins.</p>",
                                   session, path), 403)
        if path == "/log/tail":
            since = int((q.get("since") or ["0"])[0] or 0)
            rows = self.control.ring.since(since)
            return self._send(json.dumps({"lines": [
                {"n": n, "t": time.strftime("%H:%M:%S", time.localtime(ts)),
                 "s": txt} for n, ts, txt in rows]}),
                ctype="application/json")
        if path == "/status.json":
            return self._send(json.dumps(self.control.status(), default=str),
                              ctype="application/json")
        if path == "/system/login-state" and role == "admin":
            return self._send(json.dumps(self.control.twitch_login_state() or {}),
                              ctype="application/json")
        view = {
            "/": self._dashboard, "/log": self._log, "/chat": self._chat,
            "/reminders": self._reminders, "/commands": self._commands,
            "/stream": self._stream, "/memory": self._memory,
            "/config": self._config, "/system": self._system,
        }.get(path)
        if view is None:
            return self._send(page("Not found", "<p>No such page.</p>", session, path), 404)
        return self._send(view(session, flash, q))

    # -- POST -----------------------------------------------------------------
    def do_POST(self):
        url = urllib.parse.urlsplit(self.path)
        path = url.path
        form = self._form()
        client = self.client_address[0] if self.client_address else "?"
        if path == "/login":
            user = (form.get("user") or "").strip().lower()[:40]
            pw = form.get("password") or ""
            locked = self.sessions.locked_for(user)
            if locked > 0:
                print(f"[admin] login for {user!r} from {client} refused: "
                      f"locked for {human(locked)}", flush=True)
                return self._redirect("/login", f"too many failed logins - try again in {human(locked)}")
            role = self.users.verify(user, pw)
            if role is None:
                n = self.sessions.note_failure(user)
                print(f"[admin] login FAILED for {user!r} from {client} "
                      f"({n} miss{'es' if n != 1 else ''})", flush=True)
                time.sleep(1.0)
                return self._redirect("/login", "wrong user name or password")
            self.sessions.clear_failures(user)
            token = self.sessions.open(user, role)
            print(f"[admin] {user} logged in from {client} as {role}", flush=True)
            return self._redirect("/", "", self._set_cookie(token))

        session = self._session()
        if session is None:
            return self._redirect("/login", "please log in")
        if not hmac.compare_digest(form.get("csrf", ""), session["csrf"]):
            return self._send(page("Refused", "<p>Stale form - go back and try again.</p>",
                                   session, path), 400)
        user, role = session["user"], session["role"]
        c = self.control

        if path == "/logout":
            self.sessions.close(self._cookie())
            print(f"[admin] {user} logged out", flush=True)
            return self._redirect("/login", "logged out", self._set_cookie("", clear=True))

        # -- mod-level actions
        if path == "/act/pause":
            return self._redirect("/", c.set_paused(form.get("on") == "1", user))
        if path == "/act/switch":
            return self._redirect("/", c.set_switch(form.get("which", ""),
                                                    form.get("off") == "1", user))
        if path == "/act/say":
            return self._redirect("/chat", c.say(form.get("text", ""), user))
        if path == "/act/notice":
            return self._redirect("/chat", c.set_notice(form.get("text", ""), user))
        if path == "/act/game/end":
            return self._redirect("/chat", c.end_game(user))
        if path == "/act/count/drop":
            return self._redirect("/chat", c.drop_count(form.get("key", ""), user))
        if path == "/act/reminder/add":
            return self._redirect("/reminders", c.add_reminder(
                form.get("when", ""), form.get("message", ""), user))
        if path == "/act/reminder/cancel":
            return self._redirect("/reminders", c.cancel_reminder(form.get("which", ""), user))
        if path == "/act/command/set":
            return self._redirect("/commands", c.set_custom(
                form.get("name", ""), form.get("message", ""), user))
        if path == "/act/command/delete":
            return self._redirect("/commands", c.delete_custom(form.get("name", ""), user))
        if path == "/act/haul":
            return self._redirect("/stream", c.set_haul(form.get("text", ""), user))
        if path == "/act/subgoal":
            return self._redirect("/stream", c.set_subgoal(
                form.get("goal", ""), form.get("current", ""), form.get("label", ""), user))
        if path == "/act/persona":
            return self._redirect("/stream", c.set_persona(
                form.get("name", ""), form.get("custom", ""), user))

        # -- admin-only actions
        if role != "admin":
            return self._send(page("Forbidden", "<p>Admins only.</p>", session, path), 403)
        if path == "/act/llm/reset":
            return self._redirect("/", c.reset_llm_rests(user))
        if path == "/act/forget":
            return self._redirect("/memory", c.forget(form.get("nick", ""), user))
        if path == "/act/beef/reset":
            return self._redirect("/stream", c.reset_beef(user))
        if path == "/act/config":
            return self._redirect("/config", c.save_config(form, user))
        if path == "/act/restart":
            return self._redirect("/system", c.restart(user))
        if path == "/act/twitch-login":
            st = c.start_twitch_login(user)
            return self._redirect("/system", st.get("error") or
                                  f"open {st['url']} and enter code {st['code']}")
        if path == "/act/user":
            name = form.get("name", "")
            if form.get("delete") == "1":
                if name.lower() == user:
                    return self._redirect("/system", "you cannot delete yourself")
                ok = self.users.delete(name)
                if ok:
                    self.sessions.close_user(name.lower())
                    c.audit(user, f"deleted panel user {name}")
                return self._redirect("/system", "deleted" if ok else "no such user")
            ok, why = self.users.set(name, form.get("password", ""),
                                     form.get("role", "mod"))
            if ok:
                c.audit(user, f"saved panel user {name.lower()} ({form.get('role', 'mod')})")
                self.sessions.close_user(name.lower())
            return self._redirect("/system", why)
        return self._send(page("Not found", "<p>No such action.</p>", session, path), 404)

    # -- pages ------------------------------------------------------------------
    def _login_page(self, flash: str) -> str:
        body = (
            '<div class="card login"><h2>Sign in</h2>'
            '<form method="post" action="/login">'
            '<label>User</label><input type="text" name="user" autocomplete="username" required autofocus>'
            '<label>Password</label><input type="password" name="password" autocomplete="current-password" required>'
            '<p><button>Sign in</button></p></form>'
            + ('' if len(self.users) else
               '<p class="warn">No panel users yet. On the server run: '
               '<code>python3 bot.py --admin-user yourname</code></p>')
            + '</div>')
        return page("Sign in", body, None, "/login", flash)

    def _dashboard(self, session, flash, q) -> str:
        s = self.control.status()
        L = s["llm"]
        T = s["login"]

        def yn(v, good="yes", bad="no"):
            return f'<span class="ok">{good}</span>' if v else f'<span class="bad">{bad}</span>'

        conn = (f'<dl><dt>Channel</dt><dd>{e(s["channel"])} as {e(s["nick"])}</dd>'
                f'<dt>Connected</dt><dd>{yn(s["connected"], "connected", "NOT connected")}</dd>'
                f'<dt>Uptime</dt><dd>{human(s["uptime"])} <small>build {e(s["build"])}</small></dd>'
                f'<dt>Moderator</dt><dd>{"unknown yet" if s["is_mod"] is None else yn(s["is_mod"], "yes", "NO - run /mod")}</dd>'
                f'<dt>Commands</dt><dd>{yn(not s["paused"], "ON", "PAUSED (!bot off)")}</dd>'
                f'<dt>Queue</dt><dd>{s["pending"]} job(s)</dd>'
                f'<dt>Spice</dt><dd>{e(s["spice"])}</dd></dl>')
        sw = ('<div class="row" style="margin-top:.8rem">'
              + (_btn_form(session, "/act/pause", "Resume bot", {"on": "0"}) if s["paused"]
                 else _btn_form(session, "/act/pause", "Pause bot (!bot off)", {"on": "1"}, danger=True))
              + (_btn_form(session, "/act/switch", "Shoutouts: OFF - turn on", {"which": "so", "off": "0"}) if s["so_off"]
                 else _btn_form(session, "/act/switch", "Shoutouts: ON - turn off", {"which": "so", "off": "1"}))
              + (_btn_form(session, "/act/switch", "Doc chatter: OFF - turn on", {"which": "cb", "off": "0"}) if s["cb_off"]
                 else _btn_form(session, "/act/switch", "Doc chatter: ON - turn off", {"which": "cb", "off": "1"}))
              + (_btn_form(session, "/act/switch", "!beef: OFF - turn on", {"which": "beef", "off": "0"}) if s["beef_off"]
                 else _btn_form(session, "/act/switch", "!beef: ON - turn off", {"which": "beef", "off": "1"}))
              + '</div><p><small>These are the same switches as !bot / !so / !cb / !beef in chat; they last until the next restart.</small></p>')

        rests = "".join(f'<li>{e(r["model"])} <small>resting {human(r["left"])} more</small></li>'
                        for r in L["resting"]) or "<li class='mut'>none</li>"
        chain = "".join(
            f'<li>{e(f["provider"])} <small>{e(f["model"])}</small>'
            + (f' <span class="warn">resting {human(f["resting"])}</span>'
               if f["resting"] else '') + '</li>'
            for f in L.get("fallbacks") or []) or "<li class='mut'>none</li>"
        ai = (f'<dl><dt>Provider</dt><dd>{e(L["provider"])} {yn(L["configured"], "", "(no key)")}</dd>'
              f'<dt>Model</dt><dd>{e(L["model"])}</dd>'
              f'<dt>Fallback chain</dt><dd><ol style="margin:0;padding-left:1.2rem">'
              f'{chain}</ol></dd>'
              f'<dt>Chat AI</dt><dd>{yn(s["chat_ai"], "enabled", "off")}'
              + (' <span class="warn">on fallback</span>' if L["on_fallback"] else '') + '</dd>'
              f'<dt>Breaker</dt><dd>{("primary down " + human(L["breaker"])) if L["breaker"] else "clear"}'
              + (f'; fallback down {human(L["fallback_breaker"])}' if L["fallback_breaker"] else '') + '</dd>'
              f'<dt>Resting</dt><dd><ul style="margin:0;padding-left:1rem">{rests}</ul></dd></dl>')
        if session["role"] == "admin":
            ai += '<p>' + _btn_form(session, "/act/llm/reset", "Clear AI rests") + '</p>'

        exp = T["expires_in"]
        tw = (f'<dl><dt>tokens.json</dt><dd>{yn(T["present"], "present", "MISSING")}</dd>'
              f'<dt>Logged in as</dt><dd>{e(T["login"] or "-")}</dd>'
              f'<dt>Access token</dt><dd>{"-" if exp is None else ("valid " + human(exp)) if exp > 0 else "<span class=bad>expired</span>"}</dd>'
              f'<dt>Refresh token</dt><dd>{yn(T["refresh"], "present", "MISSING")}</dd>'
              f'<dt>client_id</dt><dd>{"-" if T["client_id_matches"] is None else yn(T["client_id_matches"], "matches config", "MISMATCH")}</dd></dl>')

        n = s["notice"]
        notice = (f'<p>Standing notice: <b>{e(n["text"])}</b> <small>({human(n["age"])} ago, '
                  f'{n["told"]} told)</small></p>' if n else '<p class="mut">No standing notice.</p>')
        notice += _ongoing_html(s.get("ongoing") or {})

        body = (f'<div class="grid"><div class="card"><h2>Connection</h2>{conn}{sw}</div>'
                f'<div class="card"><h2>AI</h2>{ai}</div>'
                f'<div class="card"><h2>Twitch login</h2>{tw}</div>'
                f'<div class="card"><h2>Right now</h2>{notice}'
                f'<p>{s["reminders"]} reminder(s) pending, {s["custom_cmds"]} custom command(s).</p>'
                f'<p><a href="/log">Open the log</a></p></div></div>')
        return page("Dashboard", body, session, "/", flash)

    def _log(self, session, flash, q) -> str:
        rows = self.control.ring.tail(300)
        pre = "".join(f'{e(time.strftime("%H:%M:%S", time.localtime(ts)))} {e(txt)}\n'
                      for _, ts, txt in rows)
        last = rows[-1][0] if rows else 0
        body = (
            '<div class="card"><h2>Live log</h2>'
            '<div class="row"><input type="text" id="f" placeholder="filter (e.g. [llm], !funfact, error)" style="max-width:360px">'
            '<label style="margin:0"><input type="checkbox" id="follow" checked> follow</label></div>'
            f'<pre id="log">{pre}</pre></div>'
            '<script>'
            f'var last={last};var pre=document.getElementById("log");var f=document.getElementById("f");'
            'var all=pre.textContent.split("\\n").filter(Boolean);'
            'function render(){var t=f.value.toLowerCase();pre.textContent=all.filter(function(l){return !t||l.toLowerCase().indexOf(t)>=0}).join("\\n")+"\\n";'
            'if(document.getElementById("follow").checked)pre.scrollTop=pre.scrollHeight}'
            'f.addEventListener("input",render);'
            'function poll(){fetch("/log/tail?since="+last,{credentials:"same-origin"}).then(function(r){return r.json()}).then(function(d){'
            'if(d.lines.length){d.lines.forEach(function(x){all.push(x.t+" "+x.s);last=x.n});if(all.length>2000)all.splice(0,all.length-2000);render()}'
            '}).catch(function(){}).then(function(){setTimeout(poll,2000)})}'
            'render();setTimeout(poll,2000);</script>')
        return page("Log", body, session, "/log", flash)

    def _chat(self, session, flash, q) -> str:
        s = self.control.status()
        n = s["notice"]
        og = s.get("ongoing") or {}
        a = og.get("activity")
        going = '<div class="card"><h2>Game and counts</h2>' + _ongoing_html(og)
        if a:
            going += '<p>' + _btn_form(session, "/act/game/end", "End the game",
                                        confirm="End the game the bot is hosting?") + '</p>'
        for t in og.get("tallies") or []:
            going += ('<p>' + _btn_form(session, "/act/count/drop", f'Drop "{t["label"]}"',
                                        {"key": t["key"]}) + '</p>')
        going += ('<small>What the chat AI is in the middle of: the quiz a mod asked it to host '
                  '("docbot lets do a 3 round cycling quiz", "round 2", "whats the answer") and the '
                  'counts it was asked to keep ("keep count of dirty lepages", "+1", "how many so far?"). '
                  'Saved in ongoing.json, so a restart mid-game is not amnesia.</small></div>')
        body = (
            '<div class="grid"><div class="card"><h2>Speak as the bot</h2>'
            f'<form method="post" action="/act/say">{_csrf(session)}'
            '<textarea name="text" maxlength="450" placeholder="What the bot should post in chat" required></textarea>'
            '<p><button>Post to chat</button></p></form>'
            '<small>Goes through the same queue and pacing as everything else the bot says.</small></div>'
            '<div class="card"><h2>Standing notice</h2>'
            f'<p>{("Current: <b>" + e(n["text"]) + "</b> <small>(" + human(n["age"]) + " ago)</small>") if n else "<span class=mut>None standing.</span>"}</p>'
            f'<form method="post" action="/act/notice">{_csrf(session)}'
            '<textarea name="text" maxlength="300" placeholder="e.g. Doc is on the phone, back in 10"></textarea>'
            '<p><button>Set notice</button> <button class="quiet" name="text" value="">Clear</button></p></form>'
            f'<small>Anyone who asks why the stream went quiet is told this once, for {s["notice_minutes"]:g} minutes '
            '(chat_ai_notice_minutes). Same as a mod saying "docbot tell everyone ...".</small></div>'
            + going + '</div>')
        return page("Chat", body, session, "/chat", flash)

    def _reminders(self, session, flash, q) -> str:
        rows = self.control.reminders()
        now = time.time()
        table = "".join(
            f'<tr><td>#{r["id"]}</td><td>{e(r["message"])}</td>'
            f'<td>{e(time.strftime("%Y-%m-%d %H:%M", time.localtime(r["due"])))} '
            f'<small>({human(r["due"] - now)})</small></td><td>{e(r["creator"])}</td>'
            f'<td>{_btn_form(session, "/act/reminder/cancel", "Cancel", {"which": str(r["id"])}, danger=True)}</td></tr>'
            for r in rows) or '<tr><td colspan="5" class="mut">none pending</td></tr>'
        body = (
            '<div class="card"><h2>Pending reminders</h2><table><tr><th>#</th><th>Message</th>'
            f'<th>Due (server time)</th><th>Set by</th><th></th></tr>{table}</table>'
            + (('<p>' + _btn_form(session, "/act/reminder/cancel", "Cancel all", {"which": "all"}, danger=True,
                                  confirm="Cancel every pending reminder?") + '</p>') if rows else '')
            + '</div><div class="card"><h2>New reminder</h2>'
            f'<form method="post" action="/act/reminder/add">{_csrf(session)}'
            '<label>When</label><input type="text" name="when" placeholder="60mins, 1h30m, or 21:30EST" required>'
            '<label>Message</label><input type="text" name="message" maxlength="300" required>'
            '<p><button>Add</button></p></form>'
            '<small>Same syntax as !reminder. Reminders are held while the bot is paused.</small></div>')
        return page("Reminders", body, session, "/reminders", flash)

    def _commands(self, session, flash, q) -> str:
        rows = self.control.custom_commands()
        table = "".join(
            f'<tr><td>!{e(n)}</td><td>{e(m)}</td><td class="row">'
            f'<form class="inline" method="post" action="/act/command/set">{_csrf(session)}'
            f'<input type="hidden" name="name" value="{e(n)}">'
            f'<input type="text" name="message" value="{e(m)}" maxlength="400" style="width:min(60vw,420px)"> '
            f'<button class="quiet">Save</button></form>'
            f'{_btn_form(session, "/act/command/delete", "Delete", {"name": n}, danger=True, confirm="Delete !" + n + "?")}'
            f'</td></tr>' for n, m in rows) or '<tr><td colspan="3" class="mut">no custom commands</td></tr>'
        body = (
            f'<div class="card"><h2>Custom commands</h2><table><tr><th>Command</th><th>Says</th><th></th></tr>{table}</table></div>'
            '<div class="card"><h2>Add one</h2>'
            f'<form method="post" action="/act/command/set">{_csrf(session)}'
            '<label>Name</label><input type="text" name="name" placeholder="discord" required>'
            '<label>Message</label><input type="text" name="message" maxlength="400" placeholder="Join us: https://discord.gg/..." required>'
            '<p><button>Create</button></p></form>'
            '<small>Same rules as !cmd add: {user} is replaced by whoever ran it; built-in names are refused.</small></div>')
        return page("Commands", body, session, "/commands", flash)

    def _stream(self, session, flash, q) -> str:
        h = self.control.haul()
        g = self.control.subgoal()
        p = self.control.persona()
        cur = p.get("name") or "doc"
        opts = "".join(
            f'<option value="{e(n)}"{" selected" if n == cur else ""}>{e(n)} - {e(b)}</option>'
            for n, b in p.get("choices", [])) + f'<option value="custom"{" selected" if cur == "custom" else ""}>custom - your own description</option>'
        board = "".join(
            f'<tr><td>{i}</td><td>{e(n)}</td><td>{r.get("points", 0)}</td>'
            f'<td>{r.get("wins", 0)}-{r.get("losses", 0)}</td><td>{r.get("streak", 0)}</td></tr>'
            for i, (n, r) in enumerate(self.control.beef_board(), 1)) or '<tr><td colspan="5" class="mut">nobody has played</td></tr>'
        body = (
            '<div class="grid"><div class="card"><h2>Haul board</h2>'
            f'<p>{("<b>" + e(h["text"]) + "</b> <small>set by " + e(h["set_by"]) + (", " + e(h["age"]) if h["age"] else "") + "</small>") if h["text"] else "<span class=mut>nothing logged</span>"}</p>'
            f'<form method="post" action="/act/haul">{_csrf(session)}'
            '<input type="text" name="text" maxlength="200" placeholder="Produce to Denver">'
            '<p><button>Update</button> <button class="quiet" name="text" value="">Clear</button></p></form></div>'
            '<div class="card"><h2>Sub goal</h2>'
            f'<form method="post" action="/act/subgoal">{_csrf(session)}'
            f'<label>Goal</label><input type="number" name="goal" min="2" value="{e(g.get("goal", ""))}">'
            f'<label>Current</label><input type="number" name="current" min="0" value="{e(g.get("current", 0))}">'
            f'<label>What happens when we hit it</label><input type="text" name="label" maxlength="200" value="{e(g.get("label", ""))}">'
            '<p><button>Save</button></p></form>'
            '<small>Leave goal and payoff empty and save to clear it.</small></div>'
            '<div class="card"><h2>Voice</h2>'
            f'<form method="post" action="/act/persona">{_csrf(session)}'
            f'<select name="name">{opts}</select>'
            f'<label>Custom description (only for "custom")</label>'
            f'<textarea name="custom" maxlength="300">{e(p.get("text", ""))}</textarea>'
            '<p><button>Set voice</button></p></form></div>'
            f'<div class="card"><h2>Beef leaderboard</h2><table><tr><th>#</th><th>Player</th><th>Pts</th><th>W-L</th><th>Streak</th></tr>{board}</table>'
            + (('<p>' + _btn_form(session, "/act/beef/reset", "Reset leaderboard", danger=True,
                                  confirm="Wipe every beef score?") + '</p>') if session["role"] == "admin" else '')
            + '</div></div>')
        return page("Stream", body, session, "/stream", flash)

    def _memory(self, session, flash, q) -> str:
        who = (q.get("nick") or [""])[0][:60]
        if who:
            facts = self.control.memory_for(who)
            rows = "".join(
                f'<tr><td><small>{e(time.strftime("%Y-%m-%d", time.localtime(f["ts"])))}</small></td><td>{e(f["fact"])}</td></tr>'
                for f in facts) or '<tr><td colspan="2" class="mut">nothing remembered</td></tr>'
            body = (f'<div class="card"><h2>What the bot remembers about {e(who)}</h2>'
                    f'<table>{rows}</table><p><a href="/memory">Back</a> '
                    + _btn_form(session, "/act/forget", f"Forget {who}", {"nick": who}, danger=True,
                                confirm=f"Erase everything about {who}?") + '</p></div>')
        else:
            over = self.control.memory_overview()
            rows = "".join(
                f'<tr><td><a href="/memory?nick={urllib.parse.quote(r["nick"])}">{e(r["nick"])}</a></td>'
                f'<td>{r["count"]}</td><td><small>{e(time.strftime("%Y-%m-%d", time.localtime(r["last"])))}</small></td>'
                f'<td>{_btn_form(session, "/act/forget", "Forget", {"nick": r["nick"]}, danger=True, confirm="Erase everything about " + r["nick"] + "?")}</td></tr>'
                for r in over) or '<tr><td colspan="4" class="mut">no memories yet</td></tr>'
            body = (f'<div class="card"><h2>Viewer memory</h2><table><tr><th>Viewer</th><th>Facts</th><th>Last</th><th></th></tr>{rows}</table>'
                    '<p><small>Forget = the same as !forget in chat: their distilled facts and their logged lines, gone.</small></p></div>')
        return page("Memory", body, session, "/memory", flash)

    def _config(self, session, flash, q) -> str:
        rows = self.control.config_view()
        fields = []
        for key, shown, secret, kind, on_disk in rows:
            if kind == "bool":
                inp = (f'<select name="{e(key)}"><option value="true"{" selected" if shown == "true" else ""}>true</option>'
                       f'<option value="false"{" selected" if shown == "false" else ""}>false</option></select>')
            elif secret:
                inp = (f'<input type="text" name="{e(key)}" value="" placeholder="{e(shown) or "(not set)"}" autocomplete="off"> '
                       f'<small><label style="display:inline"><input type="checkbox" name="clear_{e(key)}" value="1"> clear</label></small>')
            elif kind == "json" or len(shown) > 70:
                inp = f'<textarea name="{e(key)}">{e(shown)}</textarea>'
            else:
                inp = f'<input type="text" name="{e(key)}" value="{e(shown)}">'
            tag = (' <span class="pill">restart</span>' if key in RESTART_KEYS else '') + \
                  ('' if on_disk else ' <span class="pill mut">default</span>')
            fields.append(f'<tr><td><code>{e(key)}</code>{tag}</td><td>{inp}</td></tr>')
        body = (
            f'<div class="card"><h2>config.json <small>{e(self.control.config_path)}</small></h2>'
            f'<form method="post" action="/act/config">{_csrf(session)}<table>{"".join(fields)}</table>'
            '<p><button>Save</button></p></form>'
            '<p><small>Secrets show masked and stay as they are unless you type a new value or tick clear. '
            'Keys marked <span class="pill">restart</span> are read at startup - save, then restart from the System tab; '
            'the rest take effect at once. The file is rewritten atomically and comment keys (<code>_like_this</code>) are kept. '
            'Anything set in <code>bot.env</code> (TWITCH_SPICE, GROQ_API_KEY, ...) still wins over this file at the next start.</small></p></div>')
        return page("Config", body, session, "/config", flash)

    def _system(self, session, flash, q) -> str:
        out = ""
        which = (q.get("run") or [""])[0]
        if which in ("fixes", "doctor"):
            out = f'<div class="card"><h2>{e(which)} output</h2><pre>{e(self.control.run_check(which))}</pre></div>'
        st = self.control.twitch_login_state()
        login = ""
        if st:
            if st.get("done"):
                login = (f'<p class="ok">Logged in as {e(st.get("login"))}.</p>' if not st.get("error")
                         else f'<p class="bad">Login failed: {e(st["error"])}</p>')
            else:
                login = (f'<p>Open <a href="{e(st["url"])}" target="_blank" rel="noopener">{e(st["url"])}</a> '
                         f'on any device, log in as the <b>bot</b> account and enter code <b style="font-size:1.4rem">{e(st["code"])}</b>. '
                         f'Waiting for Twitch...</p><script>setTimeout(function(){{location.replace("/system")}},5000)</script>')
        users = "".join(
            f'<tr><td>{e(n)}</td><td>{e(self.users.role(n))}</td><td>'
            + ('' if n == session["user"] else _btn_form(session, "/act/user", "Delete", {"name": n, "delete": "1"}, danger=True,
                                                          confirm="Delete panel user " + n + "?"))
            + '</td></tr>' for n in self.users.names())
        acts = "".join(f'<tr><td><small>{e(time.strftime("%m-%d %H:%M", time.localtime(ts)))}</small></td>'
                       f'<td>{e(u)}</td><td>{e(t)}</td></tr>' for ts, u, t in reversed(self.control.actions[-50:])) \
            or '<tr><td colspan="3" class="mut">nothing yet</td></tr>'
        body = (
            f'{out}<div class="grid"><div class="card"><h2>Process</h2>'
            f'<p>Build {e(_build_id())}, up {human(time.time() - self.control.started)}, '
            f'{self.sessions.count()} panel session(s).</p><p>'
            + _btn_form(session, "/act/restart", "Restart the bot", danger=True,
                        confirm="Restart now? Under systemd it is back in ~5 seconds.")
            + '</p><p><a href="/system?run=fixes">Run check_fixes</a> - <a href="/system?run=doctor">Run --doctor</a></p></div>'
            '<div class="card"><h2>Twitch login</h2>'
            f'{login}<p>' + _btn_form(session, "/act/twitch-login", "Log in to Twitch again")
            + '</p><small>Starts the same device-code login as <code>bot.py --login</code>; the new tokens.json '
            'is picked up on the next reconnect (which happens right away).</small></div>'
            f'<div class="card"><h2>Panel users</h2><table><tr><th>User</th><th>Role</th><th></th></tr>{users}</table>'
            f'<form method="post" action="/act/user">{_csrf(session)}'
            '<label>Name</label><input type="text" name="name" required>'
            f'<label>Password (min {MIN_PASSWORD})</label><input type="password" name="password" autocomplete="new-password" required>'
            '<label>Role</label><select name="role"><option value="mod">mod - switches, chat, reminders, commands, stream</option>'
            '<option value="admin">admin - everything</option></select>'
            '<p><button>Add / reset</button></p></form></div>'
            f'<div class="card"><h2>Recent panel actions</h2><table>{acts}</table></div></div>')
        return page("System", body, session, "/system", flash)


# ---------------------------------------------------------------------------
# the server
# ---------------------------------------------------------------------------

class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 16


#: Address ranges the panel binds to without the explicit public opt-in:
#: loopback, RFC 1918 private, Tailscale's CGNAT range and its IPv6 ULA.
#: An allowlist rather than ipaddress.is_private, which also says "private"
#: for documentation and other reserved ranges that are not yours.
_SAFE_NETS = tuple(ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "100.64.0.0/10", "::1/128", "fd00::/8", "fe80::/10"))


def _bind_is_safe(bind: str) -> tuple:
    """(ok, why). Loopback, RFC 1918 and Tailscale (100.64/10, fd7a::/…)
    are fine; anything else - and the wildcard - needs the explicit
    opt-in."""
    try:
        ip = ipaddress.ip_address(bind)
    except ValueError:
        return False, f"{bind!r} is not an IP address"
    if ip.is_unspecified:
        return False, "0.0.0.0 / :: would expose the panel to the whole internet"
    if any(ip in net for net in _SAFE_NETS):
        return True, ""
    return False, f"{bind} is a public address"


def start_panel(cfg: dict, control: BotControl, users=None):
    """Start the panel thread if config asks for it. Returns the server (or
    None) so tests can shut it down. `users` is a Users object or a path to
    the users file (default: admin_users.json next to bot.py)."""
    if not cfg.get("admin_panel_enabled"):
        return None
    if not isinstance(users, Users):
        users = Users(users or USERS_PATH)
    if len(users) == 0:
        print("[admin] panel enabled but admin_users.json has no users - run: "
              "python3 bot.py --admin-user yourname   (the panel will not start)",
              flush=True)
        return None
    bind = str(cfg.get("admin_panel_bind") or DEFAULT_BIND).strip()
    port = cfg.get("admin_panel_port")
    port = DEFAULT_PORT if port in (None, "") else int(port)   # 0 = any free port
    ok, why = _bind_is_safe(bind)
    if not ok and not cfg.get("admin_panel_public"):
        print(f"[admin] refusing to bind the panel to {bind}: {why}. Keep "
              f"admin_panel_bind on 127.0.0.1 (reach it over an SSH tunnel) or "
              f"a Tailscale 100.x address, or set admin_panel_public: true if "
              f"you really mean it and have HTTPS in front.", flush=True)
        return None
    try:
        server = _Server((bind, port), PanelHandler)
    except OSError as exc:
        print(f"[admin] could not listen on {bind}:{port}: {exc}", flush=True)
        return None
    PanelHandler.control = control
    PanelHandler.users = users
    PanelHandler.sessions = Sessions()
    thread = threading.Thread(target=server.serve_forever, name="admin-panel",
                              daemon=True)
    thread.start()
    how = ("ssh -L {p}:127.0.0.1:{p} user@server, then http://localhost:{p}".format(p=port)
           if ipaddress.ip_address(bind).is_loopback else f"http://{bind}:{port}")
    print(f"[admin] panel on http://{bind}:{port}  ({len(users)} user(s); {how})"
          + ("  ** PUBLIC BIND - make sure HTTPS and a firewall are in front **"
             if not ok else ""), flush=True)
    return server


def restart_process() -> None:
    """End the process with a distinct exit code; systemd's Restart=always
    (or start-bot.bat's loop) starts a fresh one. Files are flushed first."""
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(75)          # EX_TEMPFAIL: "try again", distinct from a crash
