#!/usr/bin/env python3
"""Twitch chat bot: "!funfact <place>".

Looks up a fun fact about a location on the internet and posts it to chat.

    python3 bot.py               # reads ./config.json, logs in if needed
    python3 bot.py --login       # force a fresh Twitch browser login
    python3 bot.py other.json    # reads a different config file

Configuration (config.json and/or environment variables):

    nick               bot account username             (env: TWITCH_NICK)
    oauth_token        chat token "oauth:..." (OPTIONAL — the bot can log in
                       for you and save it to tokens.json)  (env: TWITCH_OAUTH)
    client_id          Twitch app Client ID for auto-login  (env: TWITCH_CLIENT_ID)
    client_secret      Twitch app Client Secret (optional)  (env: TWITCH_CLIENT_SECRET)
    channel            channel to join, e.g. "#name"    (env: TWITCH_CHANNEL)
    fact_source        "sources" (default) or "llm"      (env: TWITCH_FACT_SOURCE)
    prefix             command prefix                   (default "!")
    cooldown_seconds   min seconds between lookups      (default 5)
    max_message_chars  hard cap for one chat message    (default 450)
    fact_prefix        text before the fact             (default "FunFact")
    respond_only_to    optional list of usernames; empty = everyone

No pip packages required — standard library only.
"""

from __future__ import annotations

import json
import os
import queue
import random
import re
import socket
import ssl
import sys
import threading
import time

import auth
import access
import extras
import reminders as reminders_mod
import haul as haul_mod
import whois
from funfacts import get_funfact, trim_to_fit

HOST = "irc.chat.twitch.tv"
PORT = 6697  # TLS

# Extra entertainment commands (enabled when config "fun_commands" is true).
# !funfacts is the same command as !funfact - the plural is the natural typo.
FUNFACT_ALIASES = {"funfact", "funfacts"}
EXTRAS_COMMANDS = {"joke", "randomfact", "riddle", "wouldyourather", "wyr", "smk"}
SMK_ALIASES = {"smk", "shagmarrykill", "marryshagkill"}
# !help is documentation, not a game: it stays reachable for everyone so a
# viewer can read what the bot does even if they may not run a command yet.
HELP_COMMANDS = {"help", "commands"}
# !whois <name> posts the lead of that person's Wikipedia article. It is a
# network call, so it goes through the same queue and rate limit as !funfact.
WHOIS_COMMANDS = {"whois", "who"}
# What gets posted into a quiet channel to get it going again. All five are
# local or keyless, so this never spends the fact engine's budget.
IDLE_COMMANDS = ("smk", "riddle", "joke", "randomfact", "wyr")
# Moderator-owned state. Both stay reachable while the bot is switched off,
# otherwise !bot off would strand a pending reminder or the cargo board.
REMINDER_COMMANDS = {"reminder", "reminders"}
# !haul is the command. "transporting"/"transport" stay accepted so anyone
# mid-habit is not broken by the rename; drop them from the set to retire them.
HAUL_COMMANDS = {"haul", "hauls", "transporting", "transport"}

DEFAULTS = {
    "nick": "",
    "oauth_token": "",
    "client_id": "",
    "client_secret": "",
    "channel": "",
    "prefix": "!",
    "cooldown_seconds": 5,
    "spice": "clean",          # "clean" or "spicy" (adult-rated facts)
    "max_fact_chars": 200,     # max length of the fact itself
    "max_message_chars": 450,
    "fact_prefix": "FunFact",
    "fun_commands": True,      # enable !joke !randomfact !riddle !wouldyourather
    "respond_only_to": [],
    # Who may use !funfact, and how often (see access.py). Badges come free
    # from chat; follow status needs the Helix API and the
    # moderator:read:followers scope, so a fresh --login is needed once.
    "access_control": True,
    "tier_cooldowns": {"broadcaster": 30, "moderator": 30, "vip": 60,
                       "subscriber": 60, "follower": 300},
    "min_follow_age_seconds": 86400,   # followers must be 1 day old
    "riddle_answer_delay": 20,         # seconds before !riddle shows its answer
    # How much of the Wikipedia lead !whois posts. Trimmed on a sentence
    # boundary, so a lower number loses whole sentences, never half of one.
    "whois_max_chars": 400,
    # Post something into a quiet channel to get it going again. Only fires
    # while the channel is actually streaming, so it cannot chatter into an
    # offline room all night.
    "idle_chat_enabled": True,
    "idle_chat_minutes": 10,
    "idle_chat_commands": list(IDLE_COMMANDS),
    # If the follow check can't run (no token, API down, scope missing):
    # "deny" keeps the follower gate honest, "allow" falls back to badges only.
    "follower_check_failure": "deny",
    # Optional LLM writer (used in spicy mode when an LLM is configured).
    # Auto-detects GROQ_API_KEY / OPENROUTER_API_KEY / OLLAMA_MODEL env vars,
    # so if you already run another AI app (e.g. Dayforge) it just works.
    # For genuinely adult facts, prefer a LOCAL Ollama model — hosted models
    # (Groq etc.) are alignment-filtered and water down risqué output.
    "llm_api_key": "",
    "llm_base_url": "https://api.groq.com/openai/v1",
    "llm_model": "openai/gpt-oss-120b",
    # Optional Google Programmable Search (needs a key + search-engine id).
    "google_api_key": "",
    "google_cx": "",
    "serper_api_key": "",
    # Advanced / testing only — normally leave these alone.
    "host": HOST,
    "port": PORT,
    "use_tls": True,
}

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _fail_config_syntax(path: str, exc: json.JSONDecodeError) -> None:
    """A typo in config.json used to produce a Python traceback and then a
    restart loop that could never succeed. Say what is wrong instead."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        lines = []
    out = sys.stderr
    print(f"\n[ERROR] {path} is not valid JSON.", file=out)
    print(f"        {exc.msg} at line {exc.lineno}, column {exc.colno}.", file=out)
    prev = lines[exc.lineno - 2].rstrip() if exc.lineno >= 2 else ""
    bad = lines[exc.lineno - 1] if 0 < exc.lineno <= len(lines) else ""
    if bad:
        if prev:
            print(f"   {exc.lineno - 1:>4} | {prev}", file=out)
        print(f"   {exc.lineno:>4} | {bad}", file=out)
        print(f"        | {' ' * max(0, exc.colno - 1)}^", file=out)
    if prev and not prev.endswith((",", "{", "[", ":")):
        print("\n  The line above does not end with a comma. That is nearly",
              file=out)
        print("  always the cause of this error - add one and try again:", file=out)
        print(f"      {prev},", file=out)
    elif prev.endswith(",") and bad.lstrip().startswith(("}", "]")):
        print("\n  JSON does not allow a trailing comma before a closing",
              file=out)
        print("  bracket - remove the comma at the end of the line above.",
              file=out)
    print(f"\n  Check the whole file with:  python -m json.tool {path}\n",
          file=out)
    # Exit code 2 tells start-bot.bat this is not worth restarting for.
    raise SystemExit(2)


def load_config(path: str) -> dict:
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                cfg.update(json.load(fh))
        except json.JSONDecodeError as exc:
            _fail_config_syntax(path, exc)

    cfg["nick"] = os.environ.get("TWITCH_NICK", cfg.get("nick", "")).strip()
    cfg["oauth_token"] = os.environ.get(
        "TWITCH_OAUTH", cfg.get("oauth_token", "")
    ).strip()
    cfg["client_id"] = os.environ.get(
        "TWITCH_CLIENT_ID", cfg.get("client_id", "")
    ).strip()
    cfg["client_secret"] = os.environ.get(
        "TWITCH_CLIENT_SECRET", cfg.get("client_secret", "")
    ).strip()
    cfg["channel"] = os.environ.get(
        "TWITCH_CHANNEL", cfg.get("channel", "")
    ).strip()
    cfg["spice"] = os.environ.get("TWITCH_SPICE", cfg.get("spice", "clean")).strip()
    cfg["fact_source"] = os.environ.get(
        "TWITCH_FACT_SOURCE", cfg.get("fact_source", "sources")).strip()
    cfg["serper_api_key"] = os.environ.get(
        "SERPER_API_KEY", cfg.get("serper_api_key", "")
    ).strip()

    # Reuse any AI setup already present in the environment (e.g. from another
    # local app like Dayforge), so the bot works with zero extra config.
    # Priority: OLLAMA (local, unfiltered) > GROQ > OPENROUTER. An explicit
    # llm_api_key or a non-default llm_base_url in config.json always wins.
    _pinned = bool((cfg.get("llm_api_key") or "").strip()) or (
        (cfg.get("llm_base_url") or "").strip().rstrip("/")
        != DEFAULTS["llm_base_url"].rstrip("/")
    )
    if not _pinned:
        if os.environ.get("OLLAMA_MODEL", "").strip() or os.environ.get("OLLAMA_BASE_URL", "").strip():
            cfg["llm_api_key"] = ""
            cfg["llm_base_url"] = os.environ.get("OLLAMA_BASE_URL", "").strip() or "http://localhost:11434/v1"
            cfg["llm_model"] = os.environ.get("OLLAMA_MODEL", "").strip() or "llama3.1:8b"
        elif not (cfg.get("llm_api_key") or "").strip():
            for provider, key_env, base_env, model_env, base_default, model_default in (
                ("groq", "GROQ_API_KEY", "GROQ_API_BASE", "GROQ_MODEL",
                 "https://api.groq.com/openai/v1", "openai/gpt-oss-120b"),
                ("openrouter", "OPENROUTER_API_KEY", "OPENROUTER_API_BASE", "OPENROUTER_MODEL",
                 "https://openrouter.ai/api/v1", "nousresearch/hermes-4-70b"),
            ):
                key = os.environ.get(key_env, "").strip()
                if key:
                    cfg["llm_api_key"] = key
                    cfg["llm_base_url"] = os.environ.get(base_env, "").strip() or base_default
                    cfg["llm_model"] = os.environ.get(model_env, "").strip() or model_default
                    break

    # The OAuth token is optional here — it can come from the auto-login flow.
    missing = [k for k in ("nick", "channel") if not cfg.get(k)]
    if missing:
        raise SystemExit(
            f"Missing config for: {', '.join(missing)} "
            f"(edit the config file or set the env vars)"
        )

    if not cfg["channel"].startswith("#"):
        cfg["channel"] = "#" + cfg["channel"]

    cfg["channel"] = cfg["channel"].lower()
    cfg["nick"] = cfg["nick"].lower()
    return cfg


class TwitchBot:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.nick = cfg["nick"]
        self.channel = cfg["channel"]
        self.host = cfg.get("host", HOST)
        self.port = int(cfg.get("port", PORT))
        self.sock = None
        self.buf = b""
        self.running = True
        self._send_lock = threading.Lock()
        self._say_lock = threading.Lock()   # paces chat messages
        self._last_say = 0.0
        self._last_ping = 0.0               # keep-alive pacing
        self._jobs = queue.Queue()          # (nick, login, badges, cmd, arg)
        self._last_used = {}                # channel -> timestamp
        # Role-based rate limiting. broadcaster_id is resolved lazily on the
        # first command so a failed Helix call can never block startup.
        self._access = access.AccessControl(cfg, self._build_helix(cfg))
        self._broadcaster_id = ""
        self._last_probe = 0.0              # last follower-permission probe
        self.paused = False                 # !bot off (moderators only)
        # Mod-owned state, both persisted next to the code so they survive a
        # restart. A reminder set for tomorrow must not be lost to an update.
        self.reminders = reminders_mod.ReminderSet()
        self.cargo = haul_mod.Cargo()
        self._last_denial_note = {}         # login -> timestamp (spam guard)
        self._last_chat = time.time()       # last message seen in the channel
        self._opts = {                      # passed through to funfacts
            "spice": cfg.get("spice", "clean"),
            "max_fact_chars": int(cfg.get("max_fact_chars", 200)),
            "fact_source": cfg.get("fact_source", "sources"),
            "llm_api_key": cfg.get("llm_api_key", ""),
            "llm_base_url": cfg.get("llm_base_url", ""),
            "llm_model": cfg.get("llm_model", ""),
            "google_api_key": cfg.get("google_api_key", ""),
            "google_cx": cfg.get("google_cx", ""),
            "serper_api_key": cfg.get("serper_api_key", ""),
            "debug": bool(cfg.get("debug")),
        }

    def _log(self, msg: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    def _maybe_refresh_token(self) -> None:
        """Keep the OAuth token alive: reuse/refresh the saved login (no prompt)
        and update the token used for IRC PASS. Called before every connect and
        periodically by the keeper thread, so the bot never forces a re-login
        just because a token expired mid-stream."""
        try:
            new = auth.refresh_if_possible(self.cfg)
            if new and new != self.cfg.get("oauth_token"):
                self.cfg["oauth_token"] = new
                # IRC picks the new token up from cfg on the next PASS, but the
                # Helix client was handed its copy once, at construction. Left
                # alone it keeps sending the superseded - now invalid - token
                # and every follow check 401s from then on.
                if self._access.helix is not None:
                    self._access.helix.set_token(new)
                self._log("oauth token refreshed")
        except Exception as exc:
            self._log(f"token refresh error: {exc!r}")

    # ---- connection ---------------------------------------------------
    def _connect(self) -> None:
        self._log(f"connecting to {self.host}:{self.port} ...")
        raw = socket.create_connection((self.host, self.port), timeout=15)
        if self.cfg.get("use_tls", True):
            ctx = ssl.create_default_context()
            self.sock = ctx.wrap_socket(raw, server_hostname=self.host)
        else:
            self.sock = raw
        # Short read timeout so Ctrl+C / stop() responds within ~1s instead of
        # blocking on recv for up to a minute. PINGs are still paced (see
        # _read_loop), so this does not spam the server.
        self.sock.settimeout(1.0)
        self._last_ping = time.time()
        self._send("CAP REQ :twitch.tv/tags twitch.tv/commands")
        self._send(f"PASS {self.cfg['oauth_token']}")
        self._send(f"NICK {self.nick}")
        self._send(f"JOIN {self.channel}")
        self._log("authenticated, joining " + self.channel)

    def _close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
        self.buf = b""

    def _send(self, text: str) -> None:
        if self.sock is None:
            return
        try:
            with self._send_lock:
                self.sock.sendall((text + "\r\n").encode("utf-8"))
        except OSError as exc:
            self._log(f"send error: {exc}")

    def _say(self, text: str) -> None:
        # Pace chat messages by a small minimum gap so replies never bunch up
        # and trigger Twitch's "sending messages too quickly" rate limit.
        with self._say_lock:
            now = time.time()
            gap = now - self._last_say
            if gap < 1.3:
                time.sleep(1.3 - gap)
            self._send(f"PRIVMSG {self.channel} :{text}")
            self._last_say = time.time()

    # ---- startup diagnostics -------------------------------------------
    def _diagnose_access(self) -> None:
        """Print exactly why follow checks will or will not work.

        Four different things produce the identical chat message 'could not
        verify your follow status', and only two of them are fixable by the
        person running the bot. Reading the token and the moderator list back
        from Twitch at startup turns that guess into a stated cause.
        """
        if not self.cfg.get("access_control", True):
            self._log("[access] access control is OFF in config - anyone may "
                      "use the commands.")
            return
        helix = self._access.helix
        if helix is None or not (helix.client_id and helix.token):
            self._log("[access] follow checks unavailable: no client_id or "
                      "oauth token. Only badged users (mod/VIP/subscriber) can "
                      "use the commands; everyone else is refused.")
            return
        self._resolve_broadcaster(self.cfg.get("channel", ""))
        if not helix.broadcaster_id:
            self._log("[access] could not look up the channel's id, so follow "
                      "checks are unavailable.")
            return

        info = helix.describe_token()
        scopes, login = [], ""
        if info:
            scopes = list(info.get("scope") or info.get("scopes") or [])
            login = str(info.get("login") or "")
            self._log(f"[access] token account={login!r} "
                      f"scopes={','.join(scopes) or '(none)'}")
        else:
            self._log("[access] Twitch would not validate the oauth token - it "
                      "is probably expired. Run 'python3 bot.py --login'.")

        problems = []
        if info and "moderator:read:followers" not in scopes:
            problems.append("the token is missing the moderator:read:followers "
                            "scope - run 'python3 bot.py --login' to re-authorise")
        if login and login.lower() != self.nick.lower():
            problems.append(f"the token belongs to {login!r} but the bot "
                            f"connects as {self.nick!r} - /mod the token's "
                            f"account, not this one")

        me = helix.user_id(self.nick)
        is_mod = helix.moderator_of(helix.broadcaster_id, me,
                                    has_scope="moderation:read:moderators" in scopes)
        if is_mod is False:
            problems.append(f"{self.nick} is not a moderator of "
                            f"{self.cfg.get('channel', '')} - run "
                            f"/mod {self.nick} in that channel")
        elif is_mod is None:
            self._log("[access] could not confirm moderator status (needs the "
                      "moderation:read:moderators scope).")

        # The probe is the ground truth: it is the exact call the gate makes.
        if helix.authorised is True and not problems:
            self._log("[access] follow checks are working.")
            return
        for line in problems:
            self._log(f"[access] PROBLEM: {line}.")
        if helix.authorised is not True and is_mod is None and not problems:
            self._log("[access] PROBLEM: the follower probe failed and the "
                      "cause is not settled - the token needs "
                      "moderator:read:followers AND the bot must be the "
                      "broadcaster or a /mod of the channel.")
        self._log("[access] until the above is fixed, followers will be told "
                  "'could not verify your follow status'.")

    # ---- main loop ----------------------------------------------------
    def run(self) -> None:
        self._diagnose_access()
        worker = threading.Thread(
            target=self._worker, name="fact-worker", daemon=True
        )
        worker.start()
        keeper = threading.Thread(
            target=self._token_keeper, name="token-keeper", daemon=True
        )
        keeper.start()
        ticker = threading.Thread(
            target=self._reminder_keeper, name="reminder-keeper", daemon=True
        )
        ticker.start()
        chatter = threading.Thread(
            target=self._idle_chat_keeper, name="idle-chat", daemon=True
        )
        chatter.start()

        backoff = 2
        while self.running:
            try:
                self._maybe_refresh_token()
                self._connect()
                backoff = 2
                self._read_loop()
            except (OSError, ssl.SSLError) as exc:
                self._log(f"connection lost: {exc}")
                self._close()
                self._log(f"reconnecting in {backoff}s ...")
                # Sleep in 1s slices so a shutdown request is honoured quickly.
                for _ in range(backoff):
                    if not self.running:
                        return
                    time.sleep(1)
                backoff = min(backoff * 2, 60)

    def _token_keeper(self) -> None:
        """Refresh the OAuth token every 30 minutes so it never expires while
        the bot is connected (Twitch access tokens are short-lived; the refresh
        token in tokens.json keeps them alive indefinitely)."""
        while self.running:
            time.sleep(1800)
            if not self.running:
                return
            self._maybe_refresh_token()

    def _read_loop(self) -> None:
        while self.running and self.sock is not None:
            try:
                data = self.sock.recv(4096)
            except socket.timeout:
                # Idle — send a keep-alive PING only every ~4 minutes.
                if time.time() - self._last_ping > 240:
                    self._send("PING :tmi.twitch.tv")
                    self._last_ping = time.time()
                continue
            except (OSError, ssl.SSLError):
                raise
            if not data:
                raise OSError("server closed the connection")

            self.buf += data
            while b"\r\n" in self.buf:
                raw, self.buf = self.buf.split(b"\r\n", 1)
                try:
                    self._handle(raw.decode("utf-8", "replace"))
                except Exception as exc:  # never die on a malformed line
                    self._log(f"handle error: {exc!r}")

    # ---- line handling ------------------------------------------------
    def _handle(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        if line.startswith("PING"):
            self._send("PONG :tmi.twitch.tv")
            return

        tags = {}
        if line.startswith("@"):
            tag_part, _, line = line.partition(" ")
            for kv in tag_part[1:].split(";"):
                if "=" in kv:
                    k, _, v = kv.partition("=")
                    tags[k] = v

        parts = line.split(" ", 3)
        if len(parts) < 4:
            if " NOTICE " in line:
                self._log("NOTICE " + line)
            return

        src, command, target, trailing = parts
        if command == "PRIVMSG":
            match = re.match(r":([^!]+)!", src)
            nick = match.group(1) if match else "?"
            nick = tags.get("display-name") or nick
            message = trailing[1:] if trailing.startswith(":") else trailing
            login_match = re.match(r":([^!]+)!", src)
            login = (login_match.group(1) if login_match else nick).lower()
            self._on_message(nick, target, message, login,
                             tags.get("badges", ""))
        elif command == "NOTICE":
            self._log("NOTICE " + line)

    # ---- chat ---------------------------------------------------------
    def _on_message(self, nick: str, target: str, message: str,
                    login: str = "", badges: str = "") -> None:
        # Anything anyone says counts as chat being alive, not just commands.
        self._last_chat = time.time()
        prefix = self.cfg.get("prefix", "!")
        if not message.startswith(prefix):
            return
        body = message[len(prefix):].strip()
        if not body:
            return
        command, _, argument = body.partition(" ")
        command = command.lower()
        if command in FUNFACT_ALIASES:
            command = "funfact"

        # The moderator switch has to keep working while the bot is switched
        # off, otherwise switching it off is a one-way trip.
        if command == "bot":
            self._bot_switch(nick, badges, argument.strip().lower())
            return

        if command in REMINDER_COMMANDS:
            self._reminder_command(nick, badges, argument)
            return

        if command in HAUL_COMMANDS:
            # update/delete are moderator-only and answered even while paused.
            if self._haul_mutation(nick, badges, argument):
                return
            if self.paused:
                return
            # Reading the board costs nothing and answers no API call, so like
            # !help it stays open to everyone, badges or not.
            self._say_haul(nick)
            return

        if self.paused:
            return

        extras_enabled = bool(self.cfg.get("fun_commands", True))
        if command == "funfact" or command in HELP_COMMANDS \
                or command in WHOIS_COMMANDS:
            pass
        elif command in SMK_ALIASES:
            command = "smk"
        elif extras_enabled and command in EXTRAS_COMMANDS:
            pass
        else:
            return

        allowed = self.cfg.get("respond_only_to") or []
        if allowed and nick.lower() not in {a.lower() for a in allowed}:
            return

        if command in HELP_COMMANDS:
            self._say_help(nick, badges)
            return

        if command == "funfact" and not argument.strip():
            self._say(
                f"@{nick} usage: {prefix}funfact <place>  "
                f"(e.g. {prefix}funfact Milford, PA)"
            )
            return

        if command in WHOIS_COMMANDS and not argument.strip():
            self._say(
                f"@{nick} usage: {prefix}whois <name>  "
                f"(e.g. {prefix}whois Aubrey Plaza)"
            )
            return

        now = time.time()
        key = target.lower()
        cooldown = float(self.cfg.get("cooldown_seconds", 5))
        if key in self._last_used and now - self._last_used[key] < cooldown:
            return
        self._last_used[key] = now

        argument = argument.strip()[:80]
        self._log(f"{command} request from {nick}: {argument!r}")
        self._jobs.put((nick, login or nick.lower(), badges, command, argument))

    def _bot_switch(self, nick: str, badges: str, argument: str) -> None:
        """!bot on | !bot off | !bot status - a moderator kill switch."""
        if access.tier_from_badges(badges) not in ("broadcaster", "moderator"):
            # Staying silent for everyone else keeps the switch itself from
            # becoming a spam vector for viewers who find it by accident.
            return
        if argument in ("off", "disable", "pause", "stop"):
            self.paused = True
            self._say(f"@{nick} commands are now OFF - I'll stay quiet until a "
                      f"moderator runs {self.cfg.get('prefix', '!')}bot on.")
        elif argument in ("on", "enable", "resume", "start", "unpause"):
            self.paused = False
            self._say(f"@{nick} commands are back ON.")
        elif argument in ("", "status"):
            state = "OFF" if self.paused else "ON"
            pre = self.cfg.get("prefix", "!")
            self._say(f"@{nick} commands are {state}. {pre}bot off to pause, "
                      f"{pre}bot on to resume.")
        else:
            pre = self.cfg.get("prefix", "!")
            self._say(f"@{nick} usage: {pre}bot on | {pre}bot off | "
                      f"{pre}bot status")
        self._log(f"!bot {argument or 'status'} from {nick} -> paused={self.paused}")

    # ---- reminders and the cargo board --------------------------------
    def _is_mod(self, badges: str) -> bool:
        return access.tier_from_badges(badges) in ("broadcaster", "moderator")

    def _fit(self, head: str, body: str, tail: str = "") -> str:
        """Keep head + body + tail inside Twitch's 500-character limit."""
        budget = max(60, min(500, int(self.cfg.get("max_message_chars", 450)))
                     - len(head) - len(tail))
        return head + trim_to_fit(body, budget) + tail

    def _reminder_command(self, nick: str, badges: str, argument: str) -> None:
        """!reminder <when> <message> | list | cancel <n>|all  (mods only)."""
        if not self._is_mod(badges):
            return                      # silent: no spam vector for viewers
        prefix = self.cfg.get("prefix", "!")
        spec, _, message = (argument or "").strip().partition(" ")
        word = spec.lower()

        if word in ("", "help", "?"):
            self._say(f"@{nick} usage: {prefix}reminder 60mins <message> or "
                      f"{prefix}reminder 01:30PDT <message> | "
                      f"{prefix}reminder list | {prefix}reminder cancel <n>|all")
            return
        if word in ("list", "ls", "pending"):
            self._say_reminder_list(nick)
            return
        if word in ("cancel", "delete", "remove", "rm"):
            count, why = self.reminders.cancel(message)
            if why:
                self._say(f"@{nick} {why}.")
            elif count > 1:
                self._say(f"@{nick} cancelled all {count} reminders.")
            else:
                self._say(f"@{nick} reminder #{message.strip().lstrip('#')} cancelled.")
            return

        item, why = self.reminders.add(spec, message, nick)
        if item is None:
            self._say(self._fit(f"@{nick} ", why or "could not set that reminder"))
            return
        self._say(self._fit(
            f"@{nick} reminder #{item.id} set for {self._when_local(item.due)} "
            f"({item.label}) - ", item.message))
        self._log(f"reminder #{item.id} from {nick}: {item.label} - "
                  f"{item.message[:60]!r}")

    def _say_reminder_list(self, nick: str) -> None:
        pending = self.reminders.pending()
        if not pending:
            self._say(f"@{nick} no reminders pending.")
            return
        shown, rest = pending[:6], len(pending) - 6
        parts = [f"#{r.id} {r.when()} - " + (r.message[:40] +
                 ("..." if len(r.message) > 40 else "")) for r in shown]
        body = " | ".join(parts) + (f" | +{rest} more" if rest > 0 else "")
        self._say(self._fit(f"@{nick} reminders: ", body))

    def _fire_reminder(self, item) -> None:
        tail = f'  (set by @{item.creator})' if item.creator else ""
        self._say(self._fit("Reminder | ", item.message, tail))
        self._log(f"reminder #{item.id} fired: {item.message[:60]!r}")

    @staticmethod
    def _when_local(due: float) -> str:
        """'21:05 UTC today' - the machine's own clock, so a reminder set in
        one timezone can be read by whoever is watching the console."""
        due_lt = time.localtime(due)
        zone = time.tzname[0] or "local"
        clock = time.strftime("%H:%M", due_lt)
        if due_lt[:3] == time.localtime()[:3]:
            return f"{clock} {zone} today"
        if due_lt[:3] == time.localtime(time.time() + 86400)[:3]:
            return f"{clock} {zone} tomorrow"
        return f"{clock} {zone} on " + time.strftime("%a %d %b", due_lt)

    def _reply_whois(self, nick: str, query: str) -> None:
        """Post what Twitch and Wikipedia say - not a model's take."""
        try:
            result = whois.lookup(
                query, int(self.cfg.get("whois_max_chars",
                                        whois.DEFAULT_MAX_CHARS)),
                helix=self._access.helix)
        except whois.WhoisError as exc:
            self._log(f"whois {query!r} failed: {exc}")
            self._say(f"@{nick} I couldn't reach Wikipedia just now - "
                      f"try again in a moment.")
            return
        if not result.get("found"):
            self._say(self._fit(f"@{nick} ", result.get("reason")
                                or "I couldn't find that."))
            return

        twitch, wiki = result.get("twitch"), result.get("wiki")
        # A caller that found someone must always be answered. Older callers
        # put title/description/text at the top level rather than under
        # "wiki", and a silent no-op is the worst possible reply.
        if wiki is None and result.get("text"):
            wiki = {k: result.get(k) for k in ("title", "description",
                                               "text")}
        if twitch:
            self._say(self._fit(
                f"WhoIs | {twitch.get('display_name') or query} | ",
                whois.format_twitch(twitch)))
        if wiki:
            head = f"WhoIs | {wiki.get('title') or query}"
            description = (wiki.get("description") or "").strip()
            if description:
                head += f" ({description})"
            self._say(self._fit(head + ": ", wiki.get("text") or ""))
        sources = "+".join(n for n, have in (("twitch", twitch),
                                             ("wikipedia", wiki)) if have)
        self._log(f"whois {query!r} -> {result.get('title')} ({sources})")

    @staticmethod
    def _mention(nick: str) -> str:
        return f"@{nick} " if nick else ""

    def _idle_chat_tick(self, now: float | None = None):
        """One pass of the idle-chat clock. Returns the command it posted.

        Fires when nobody has said anything in the channel for
        `idle_chat_minutes`, and only while the channel is actually streaming -
        a bot that posts jokes into an offline room every ten minutes is not a
        feature. Where the live check cannot be settled it posts anyway: the
        requested behaviour beats a guess, and a missed check must not turn the
        whole feature off silently.
        """
        now = time.time() if now is None else now
        if not self.cfg.get("idle_chat_enabled", True):
            return None
        if self.paused or not self.cfg.get("fun_commands", True):
            return None
        window = float(self.cfg.get("idle_chat_minutes", 10)) * 60.0
        if window <= 0:
            return None
        idle_for = now - self._last_chat
        if idle_for < window:
            return None

        helix = self._access.helix
        if helix is not None and helix.is_live() is False:
            # Offline. Hold the clock so this is not re-checked every tick.
            self._last_chat = now
            return None

        # An explicitly empty list means "post nothing". `or IDLE_COMMANDS`
        # would read that as "not configured" and post everything instead.
        configured = self.cfg.get("idle_chat_commands")
        if configured is None:
            configured = IDLE_COMMANDS
        pool = [c for c in configured if c in IDLE_COMMANDS]
        if not pool:
            return None
        command = random.choice(pool)
        argument = random.choice(("female", "male", "any")) \
            if command == "smk" else ""
        self._last_chat = now      # the next one is another window away
        self._log(f"chat idle for {int(idle_for)}s - posting !{command}")
        self._reply_extra("", command, argument)
        return command

    def _idle_chat_keeper(self) -> None:
        while self.running:
            time.sleep(15.0)
            if not self.running:
                return
            try:
                self._idle_chat_tick()
            except Exception as exc:
                self._log(f"idle-chat error: {exc!r}")

    def _tick_reminders(self) -> int:
        """One pass of the reminder clock. Returns how many it posted."""
        if not self.running or self.paused:
            # Held, not dropped: a moderator who paused the bot did not cancel
            # its reminders, and firing one into a silenced channel is wrong.
            return 0
        posted = 0
        for item in self.reminders.pop_due():
            self._fire_reminder(item)
            posted += 1
        return posted

    def _reminder_keeper(self) -> None:
        while self.running:
            time.sleep(1.0)
            try:
                self._tick_reminders()
            except Exception as exc:      # a bad reminder must not kill the bot
                self._log(f"reminder error: {exc!r}")

    # ---- the cargo board ---------------------------------------------------
    def _haul_mutation(self, nick: str, badges: str, argument: str) -> bool:
        """Handle update/delete. True if this was a mutation (handled)."""
        verb, _, cargo = (argument or "").strip().partition(" ")
        verb = verb.lower()
        if verb in ("update", "set", "u"):
            if not self._is_mod(badges):
                return True             # recognised, but not yours to change
            ok, why = self.cargo.update(cargo)
            if not ok:
                self._say(f"@{nick} {why}.")
                return True
            self.cargo.set_by = nick
            self.cargo.save()
            self._say(self._fit(f"@{nick} haul updated: ", self.cargo.text))
            self._log(f"haul updated by {nick}: {self.cargo.text[:60]!r}")
            return True
        if verb in ("delete", "clear", "remove", "d"):
            if not self._is_mod(badges):
                return True
            ok, why = self.cargo.delete()
            self._say(f"@{nick} {why}." if not ok else f"@{nick} haul cleared.")
            return True
        return False

    def _say_haul(self, nick: str) -> None:
        if not self.cargo.is_set:
            prefix = self.cfg.get("prefix", "!")
            self._say(f"@{nick} nothing is logged as the haul right now - a "
                      f"moderator can set it with {prefix}haul update "
                      f"<what we're hauling>")
            return
        age = self.cargo.age()
        tail = f"  (set by @{self.cargo.set_by}" + (f", {age}" if age else "") + ")"
        self._say(self._fit(f"@{nick} we are transporting ",
                            self.cargo.text, "." + tail))

    def _build_helix(self, cfg: dict):
        """Helix client for follow checks. The broadcaster's id is the channel
        we are joined to, not necessarily the account the bot logged in as."""
        token = (cfg.get("oauth_token") or "").replace("oauth:", "").strip()
        return access.Helix(cfg.get("client_id", ""), token)

    def _resolve_broadcaster(self, channel: str) -> str:
        helix = self._access.helix
        if not helix or not (helix.client_id and helix.token):
            return ""
        if not self._broadcaster_id:
            uid = helix.user_id(channel.lstrip("#"))
            if not uid:
                return ""
            self._broadcaster_id = uid
            helix.broadcaster_id = uid
            self._log(f"access control: broadcaster_id={uid}")
        # Re-probe while the probe is failing. Granting the bot /mod mid-stream,
        # or re-logging in, must take effect without a restart - otherwise the
        # only feedback is a chat message that never changes.
        if helix.authorised is not True and time.time() - self._last_probe >= 300.0:
            self._last_probe = time.time()
            helix.self_test()
        return self._broadcaster_id

    def _note_denial(self, nick: str, login: str, reason: str, wait: float) -> None:
        """Tell a rejected user why, but never more than once every 2 minutes
        each - otherwise refusing them becomes its own spam vector."""
        now = time.time()
        if now - self._last_denial_note.get(login, 0.0) < 120.0:
            return
        self._last_denial_note[login] = now
        if wait:
            self._say(f"@{nick} that's on cooldown - {int(wait)}s to go.")
        elif "follow" in reason:
            self._say(f"@{nick} {reason} to use that command.")
        else:
            self._say(f"@{nick} {reason}.")

    def _worker(self) -> None:
        while True:
            nick, login, badges, command, argument = self._jobs.get()
            try:
                # Every command shares the one per-user schedule: !joke and
                # !funfact draw on the same budget, so the cheap commands
                # cannot be used to flood either.
                self._resolve_broadcaster(self.cfg.get("channel", ""))
                verdict = self._access.check(login, badges)
                if not verdict.allowed:
                    self._log(f"{command} denied for {nick}: {verdict.reason} "
                              f"(tier={verdict.tier})")
                    self._note_denial(nick, login, verdict.reason, verdict.wait)
                    continue
                self._access.commit(login)
                if command == "funfact":
                    result = get_funfact(argument, self._opts)
                    self._reply(nick, argument, result)
                elif command in WHOIS_COMMANDS:
                    self._reply_whois(nick, argument)
                else:
                    self._reply_extra(nick, command, argument)
            except Exception as exc:
                self._log(f"{command} failed: {exc!r}")
            finally:
                self._jobs.task_done()

    def _say_help(self, nick: str, badges: str = "") -> None:
        prefix = self.cfg.get("prefix", "!")
        lines = [
            f"{prefix}funfact <place> - a real fun fact about a town "
            f"({prefix}funfacts works too)",
            f"{prefix}smk female|male|any - shag, marry or kill three names",
            f"{prefix}joke - a joke",
            f"{prefix}randomfact - a random fact",
            f"{prefix}riddle - a riddle; the answer follows shortly",
            f"{prefix}wyr - a would-you-rather",
            f"{prefix}haul - what the truck is hauling right now",
            f"{prefix}whois <name> - who that person is",
        ]
        self._say(f"@{nick} commands: " + " | ".join(lines))
        self._say(f"@{nick} who can use them: broadcaster/mod every 30s, "
                  f"VIP and subscribers every 60s, followers of over a day "
                  f"every 5 minutes.")
        if self._is_mod(badges):
            # Only worth advertising to the people allowed to use it.
            self._say(f"@{nick} mods: {prefix}reminder 60mins <message> or "
                      f"{prefix}reminder 01:30PDT <message>, then "
                      f"{prefix}reminder list / cancel <n>|all")
            self._say(f"@{nick} mods: {prefix}haul update <cargo> / "
                      f"delete, and {prefix}bot off / on / status")

    def _reply_extra(self, nick: str, command: str, argument: str = "") -> None:
        limit = int(self.cfg.get("max_message_chars", 450))
        text = None
        try:
            if command == "joke":
                text = extras.get_joke()
                label = "Joke"
            elif command == "randomfact":
                text = extras.get_random_fact()
                label = "RandomFact"
            elif command in ("wouldyourather", "wyr"):
                text = extras.get_wyr()
                label = "WouldYouRather"
            elif command == "smk":
                picked = extras.get_smk(argument)
                if not picked:
                    self._say(f"{self._mention(nick)}couldn't build a round "
                              f"right now \U0001F615")
                    return
                picks, label = picked
                # No "you're up": naming the person who typed the command
                # turns a game the whole chat can play into a solo turn. Each
                # name carries what they are known for, so a round is playable
                # by people who do not recognise every face in the pool.
                self._say(_CONTROL.sub(
                    "", f"ShagMarryKill [{label}] | "
                        f"{extras.format_smk(picks)} - shag one, marry one, "
                        f"kill one.")[:limit])
                return
            elif command == "riddle":
                pair = extras.get_riddle()
                if pair:
                    riddle, answer = pair
                    self._say(_CONTROL.sub("", f"Riddle | {riddle}")[:limit])
                    t = threading.Timer(
                        float(self.cfg.get("riddle_answer_delay", 20)),
                        self._say,
                        args=(_CONTROL.sub("", f"Answer | {answer}")[:limit],),
                    )
                    t.daemon = True
                    t.start()
                else:
                    self._say(f"{self._mention(nick)}couldn't fetch a riddle "
                              f"right now 😕")
                return
            else:
                return
        except Exception as exc:
            self._log(f"extra command {command} error: {exc!r}")

        if text:
            self._say(_CONTROL.sub("", f"{label} | {text}")[:limit])
        else:
            self._say(f"{self._mention(nick)}couldn't fetch that right now "
                      f"😕")

    def _reply(self, nick: str, argument: str, result) -> None:
        if not result:
            self._say(f'@{nick} couldn\'t find any fun facts for "{argument}" 😕')
            return
        if result.get("busy"):
            self._say(f'@{nick} fact sources are rate-limited right now — '
                      f'try again in a few seconds 🙂')
            return
        if not result.get("fact"):
            self._say(f'@{nick} couldn\'t find any fun facts for "{argument}" 😕')
            return
        place = result.get("place") or argument
        fact = _CONTROL.sub("", " ".join(result["fact"].split()))
        prefix = f"{self.cfg.get('fact_prefix', 'FunFact')} | {place}: "
        limit = int(self.cfg.get("max_message_chars", 450))
        # Fit the fact to what is left of the message budget, ending on a
        # sentence boundary rather than chopping one in half.
        fact = trim_to_fit(fact, max(40, limit - len(prefix)))
        msg = prefix + fact
        self._say(msg)
        self._log(f"replied for {argument!r}")


def warn_config(cfg: dict) -> None:
    """Loud, clear warnings so a misconfiguration can't silently downgrade
    the bot to boring plain facts."""
    spice = str(cfg.get("spice", "clean")).strip().lower()
    spicy = spice in ("spicy", "adult", "r", "on", "true", "1", "yes")
    try:
        import llm as llm_mod
        llm_ok = llm_mod.is_configured(cfg)
    except Exception:
        llm_ok = bool((cfg.get("llm_api_key") or "").strip())
    if spicy and not llm_ok:
        print("[warn] spice='spicy' but no LLM configured — facts will be PLAIN, not adult.")
        print("       Options: run a local Ollama model (set OLLAMA_MODEL, no key needed),")
        print("       set GROQ_API_KEY / OPENROUTER_API_KEY, or put llm_api_key in config.json.")
        print("       Note: hosted models (Groq/OpenRouter) are filtered and water down")
        print("       adult output — a local Ollama model is the way to get real spice.")
    elif not spicy:
        print(f"[info] spice='{spice or 'clean'}' — clean mode (no adult facts). "
              f'Set "spice": "spicy" for adult mode.')

    if llm_ok:
        _log_llm_provider(cfg)
    _log_search_sources(cfg)


def _log_search_sources(cfg: dict) -> None:
    """Print which fact sources are live, so a key that never gets used (or was
    never loaded) is obvious at startup instead of in a chat log at 10:13."""
    live = ["wikipedia"]
    if (cfg.get("serper_api_key") or "").strip():
        live.append("serper")
    if ((cfg.get("google_api_key") or "").strip()
            and (cfg.get("google_cx") or "").strip()):
        live.append("google")
    print(f"[info] fact sources: {' -> '.join(live)} -> duckduckgo (fallback)")
    if not any(s in live for s in ("serper", "google")):
        print("       no web-search key set - towns with a thin Wikipedia stub")
        print('       will fall back to DuckDuckGo. Set "serper_api_key" in')
        print("       config.json (or SERPER_API_KEY) for Google-quality results.")


def _log_llm_provider(cfg: dict) -> None:
    """Print which LLM provider/key/model is active so a wrong/stale key is
    obvious at startup instead of surfacing as per-request 401 errors."""
    base = (cfg.get("llm_base_url") or "").lower()
    key = (cfg.get("llm_api_key") or "").strip()
    model = cfg.get("llm_model") or "default"
    if "openrouter" in base:
        provider = "OpenRouter"
    elif "groq" in base:
        provider = "Groq"
    elif "localhost" in base or "127.0.0.1" in base or "11434" in base or "ollama" in base:
        provider = "local Ollama"
    else:
        provider = base or "custom LLM"
    if provider == "local Ollama":
        masked = "(no key)"
    elif key:
        masked = f"{key[:4]}…{key[-4:]}" if len(key) > 10 else "(set)"
    else:
        masked = "(no key)"
    print(f"[llm] using {provider} — model {model}, key {masked}")


def run_selftest(cfg: dict) -> int:
    """Run a few end-to-end checks without connecting to chat.

    Verifies (1) the Twitch login resolves, and (2) the fact engine returns
    results in both the configured spice mode. Used via `python3 bot.py --selftest`.
    """
    print("=" * 60)
    print("  SELF-TEST  (no chat connection needed)")
    print("=" * 60)

    # 1. Login check (optional — skipped gracefully if no valid login).
    try:
        token = auth.resolve_token(cfg)
        print(f"  [ ok ] Twitch login -> token acquired ({len(token)} chars)")
    except auth.OAuthError as exc:
        print(f"  [warn] login not tested: {exc}")

    # 2. Fact engine checks.
    opts = {
        "spice": cfg.get("spice", "clean"),
        "max_fact_chars": int(cfg.get("max_fact_chars", 200)),
        "fact_source": cfg.get("fact_source", "sources"),
        "llm_api_key": cfg.get("llm_api_key", ""),
        "llm_base_url": cfg.get("llm_base_url", ""),
        "llm_model": cfg.get("llm_model", ""),
        "google_api_key": cfg.get("google_api_key", ""),
        "google_cx": cfg.get("google_cx", ""),
        "serper_api_key": cfg.get("serper_api_key", ""),
        "debug": bool(cfg.get("debug")),
    }
    spice = str(opts["spice"]).lower()
    print(f"\n  -- fact engine (mode: {opts['spice']}, max {opts['max_fact_chars']} chars) --")

    # A standard town, a spicy-db town (spicy mode only), and a remote spot
    # that exercises the geocoder fallback.
    samples = ["Milford, PA", "Kulgera, NT"]
    spicy = spice in ("spicy", "adult", "r", "on", "true", "1", "yes")
    if spicy:
        samples.insert(1, "Las Vegas, NV")

    ok = True
    for q in samples:
        try:
            r = get_funfact(q, opts)
        except Exception as exc:
            print(f"  [FAIL] {q}: {exc!r}")
            ok = False
            continue
        if r:
            print(f"  [ ok ] {q} -> {r['place']}")
            print(f"         {r['fact']}")
        else:
            print(f"  [miss] {q}: no result (check network)")
            ok = False

    # 3. LLM writer check — this is what makes spicy mode adult.
    try:
        import llm as llm_mod
        llm_ok = llm_mod.is_configured(cfg)
    except Exception as exc:
        llm_ok = False
        print(f"\n  [FAIL] could not import llm.py: {exc!r}")
        ok = False
    if llm_ok:
        model = cfg.get("llm_model") or llm_mod.DEFAULT_MODEL
        print(f"\n  -- llm writer (model: {model}) --")
        try:
            out = llm_mod.rewrite_fact(
                "Testville, USA", "Testville, USA",
                ["Testville is a small town known for its rowdy saloons and an infamous 1887 gunfight."],
                cfg)
        except Exception as exc:
            out = None
            print(f"  [FAIL] llm call error: {exc!r}")
            ok = False
        if out:
            for ln in out.splitlines()[:3]:
                print(f"  [ ok ] {ln.strip()}")
        else:
            print("  [FAIL] llm returned nothing — check key/model/credits (or that Ollama is running).")
            ok = False
    elif spicy:
        print("\n  [warn] spice='spicy' but no LLM configured — adult facts unavailable.")
    else:
        print("\n  [info] clean mode — the LLM is not used.")

    print("\n" + ("SELF-TEST PASSED ✔" if ok else "SELF-TEST ISSUES ✘ (see above)"))
    return 0 if ok else 1


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force_login = "--login" in sys.argv
    do_selftest = "--selftest" in sys.argv
    path = args[0] if args else "config.json"

    cfg = load_config(path)

    # --debug (or TWITCH_DEBUG=1 / "debug": true) prints the exact prompts and
    # responses sent to the LLM, so you can see precisely what the AI receives.
    cfg["debug"] = (
        "--debug" in sys.argv
        or bool(cfg.get("debug"))
        or os.environ.get("TWITCH_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
    )
    if cfg["debug"]:
        print("[debug] verbose LLM logging enabled — full prompts/responses will be shown.")
        # Also trace retrieval: which source answered, and the exact seed pool
        # handed to the LLM. Without this a wrong fact can only be guessed at.
        import funfacts
        funfacts.DEBUG = True

    if not do_selftest:
        warn_config(cfg)

    if do_selftest:
        raise SystemExit(run_selftest(cfg))

    try:
        cfg["oauth_token"] = auth.resolve_token(cfg, force_login=force_login)
    except auth.OAuthError as exc:
        raise SystemExit(f"\n[auth] {exc}\n")

    bot = TwitchBot(cfg)
    try:
        bot.run()
    except KeyboardInterrupt:
        bot.running = False
        bot._close()
        print("\nshutting down ...")


if __name__ == "__main__":
    main()
