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
import chatai
import haul as haul_mod
import memory as memory_mod
import names as names_mod
import trucker as trucker_mod
import beef as beef_mod
import beefstats as beefstats_mod
import beefllm
import shoutout as shoutout_mod
import customcmds as customcmds_mod
import whois
import funfacts
from funfacts import get_funfact, trim_to_fit

HOST = "irc.chat.twitch.tv"
PORT = 6697  # TLS

# Extra entertainment commands (enabled when config "fun_commands" is true).
# !funfacts is the same command as !funfact - the plural is the natural typo.
FUNFACT_ALIASES = {"funfact", "funfacts"}
EXTRAS_COMMANDS = {"joke", "randomfact", "riddle", "wouldyourather", "wyr",
                   "smk"}
SMK_ALIASES = {"smk", "shagmarrykill", "marryshagkill"}
# !help is documentation, not a game: it stays reachable for everyone so a
# viewer can read what the bot does even if they may not run a command yet.
HELP_COMMANDS = {"help", "commands"}
# !whois <name> posts the lead of that person's Wikipedia article. It is a
# network call, so it goes through the same queue and rate limit as !funfact.
WHOIS_COMMANDS = {"whois", "who"}
# Deliberately separate: !whois is Wikipedia, !twitch is Twitch. One
# command that guesses which you meant answers about the wrong person.
# "twitch" is the command; the three older spellings still work so nobody's
# muscle memory breaks, but only !twitch is advertised in !help.
TWITCH_COMMANDS = {"twitch", "whotwitch", "whotw", "twitchwho"}
# What gets posted into a quiet channel to get it going again. All six are
# local or keyless, so this never spends the fact engine's budget.
# Moderator-owned state. Both stay reachable while the bot is switched off,
# otherwise !bot off would strand a pending reminder or the cargo board.
REMINDER_COMMANDS = {"reminder", "reminders"}
# !haul is the command. "transporting"/"transport" stay accepted so anyone
# mid-habit is not broken by the rename; drop them from the set to retire them.
HAUL_COMMANDS = {"haul", "hauls", "transporting", "transport"}
# !cb asks the bot to talk on the radio. No argument, no lookup - it is a
# local generator, so it costs nothing and never waits on the network.
CB_COMMANDS = {"cb", "radio", "breaker"}
# !so <name> shouts a channel out. It also fires on its own when the stream
# gets raided, so the switch moderates both paths.
SO_COMMANDS = {"so", "shoutout"}
BEEF_COMMANDS = {"beef"}
# !revenge is its own command rather than a !beef subcommand: the player who
# just lost types it in a hurry, and '!beef revenge' would collide with a
# rival actually called Revenge.
REVENGE_COMMANDS = {"revenge"}
# "!beef stats" and friends. Parsed before the rival, so nobody ends up in a
# feud against somebody called Stats - the same bug class as '!beef random'
# once being accepted as a display name.
BEEF_STATS_WORDS = {"stats", "stat", "score", "scoreboard", "leaderboard",
                    "top", "lb"}
# !cmd lets a moderator define new commands in chat, with no restart.
CMD_COMMANDS = {"cmd", "customcmd"}
# !forget <viewer> - wipe the chat AI's memory of that viewer (mods).
FORGET_COMMANDS = {"forget"}

# Everything a moderator must not be able to redefine. Without this, "!help"
# typed by a moderator would silently stop meaning help.
RESERVED_COMMANDS = (
    {"beef", "revenge"} | FUNFACT_ALIASES | {"funfact"} | EXTRAS_COMMANDS
    | SMK_ALIASES
    | HELP_COMMANDS | WHOIS_COMMANDS | TWITCH_COMMANDS | REMINDER_COMMANDS
    | HAUL_COMMANDS | CB_COMMANDS | SO_COMMANDS | CMD_COMMANDS | {"bot"}
)

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
    # The !cb command: the bot talks on the radio on demand. The old
    # unprompted posters (idle-chat jokes, ambient CB rambles) are gone -
    # the chat AI owns the quiet moments now.
    "cb_command_enabled": True,
    # Who may ask for one on demand: "everyone", "moderator" (mods and the
    # broadcaster) or "broadcaster". Anything unrecognised is treated as
    # "moderator" - an access setting should fail closed, not open.
    "cb_command_access": "everyone",
    # The car yelling is the loudest thing the bot does, so it gets its own
    # switch. False leaves the three CB voices and drops the WINDOW one.
    "cb_yell_enabled": True,
    # The chat AI: an optional persona that answers !ask, replies when
    # addressed by name, and occasionally chimes in on a busy channel.
    # OFF by default - flip chat_ai_enabled to let the bot hold its own in
    # chat. Every line it posts is one short cleaned line, rate-limited:
    # mention replies wait out chat_ai_mention_cooldown, unprompted
    # chime-ins must win a chat_ai_chance roll and wait out
    # chat_ai_cooldown, and nothing exceeds chat_ai_max_hour lines an hour.
    # !cb off silences it for the session, like the other chatter.
    "chat_ai_enabled": False,
    "chat_ai_cooldown": 120,
    "chat_ai_mention_cooldown": 60,
    "chat_ai_chance": 0.4,
    "chat_ai_max_hour": 20,
    "chat_ai_min_chat": 5,
    "chat_ai_timeout": 8.0,
    # The quiet-room half: when nobody has spoken for chat_ai_quiet_seconds,
    # the bot opens the conversation itself (a question, a hook) rather than
    # waiting for a message to react to - at most once per
    # chat_ai_quiet_cooldown, inside the same hourly cap.
    "chat_ai_quiet_seconds": 90,
    "chat_ai_quiet_cooldown": 150,
    # Qwen3-family models "think" before answering, which on CPU turns a
    # one-line reply into a half-minute stall. true appends Qwen3's
    # documented /no_think soft switch to every prompt. No effect on
    # models that do not know the switch.
    "llm_no_think": False,
    "chat_ai_names": ["doc", "docbot"],
    "bot_personality": "",
    # The chat AI's memory: one SQLite file. Messages are pruned after
    # 90 days; distilled per-viewer facts stay until a mod runs
    # !forget <viewer>, which erases everything held about them.
    "memory_db_path": "chat_memory.db",
    # Shout out whoever raids in, and let a moderator trigger one with !so.
    # Nothing in the message is invented: the name and viewer count come off
    # the raid notice, and the affiliate/follower line comes from Helix.
    "shoutout_enabled": True,
    "beef_enabled": True,
    # Optional LLM pass for beef stories: "auto" uses the model whenever one
    # is configured, false always uses the templates. The model gets the
    # pre-rolled winner as a fact, its output must pass the same shape checks
    # the templates guarantee, and any miss (or timeout, or missing key)
    # falls back to templates silently - the game never waits or breaks.
    "beef_llm": "auto",
    "beef_llm_timeout": 4.0,
    # Seconds between the parts of a beef story - the literal gap, exactly,
    # so the knob means what it reads. 0 sends the whole story at once,
    # which is a wall nobody reads.
    "beef_act_delay": 4.0,
    # Where the beef leaderboard and the !revenge windows live. Local file,
    # local logic: the game runs on what the bot already has even with no LLM
    # anywhere, so there is nothing else to configure.
    "beef_state_path": "beef_state.json",
    # "auto" follows whatever this channel is streaming; or force one of
    # trucking / zwift / fortnite / generic.
    "shoutout_theme": "auto",
    "custom_commands_enabled": True,
    # !smk draws from a seed pool plus Wikipedia category listings fetched in
    # the background. Turn this off and the seed pool (a few hundred names)
    # carries the game on its own - it never depends on the network.
    "names_topup_enabled": True,
    "names_topup_hours": 12,
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


def load_config(path: str, require: bool = True) -> dict:
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
    # `require` is False for --doctor: a half-finished config is exactly what
    # you are diagnosing, so refusing to load it would hide the answer.
    missing = [k for k in ("nick", "channel") if not cfg.get(k)]
    if require and missing:
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
        # Whether *this* bot is a moderator of the channel. Learned from the
        # USERSTATE line Twitch sends on join, never from the API - see
        # _note_own_state. None means "not known yet".
        self._bot_is_mod = None
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
        self.custom_cmds = customcmds_mod.CommandSet(reserved=RESERVED_COMMANDS)
        self._last_denial_note = {}         # login -> timestamp (spam guard)
        self._last_chat = time.time()       # last message seen in the channel
        self._refresh_lock = threading.Lock()   # one refresh at a time
        self._warned_401 = False
        self._cb_ambient_off = False        # !cb off, until the next restart
        self._so_off = False                # !so off, until the next restart
        self._beef_off = False
        # The beef game's persistent state: points, titles, the leaderboard,
        # the !revenge window. A file plus pure-local logic - if any part of
        # the game could fail because a model or an API was down, it would be
        # a dependency of the fun facts, not a game.
        self.beef_state = beefstats_mod.BeefState(
            path=str(cfg.get("beef_state_path") or beefstats_mod.STATE_PATH))
        self._beef_seen = beefstats_mod.RecentChatters()
        self._so_lock = threading.Lock()    # one shoutout per raid
        self._so_theme_cache = ("generic", 0.0)   # (theme, valid until)
        # The chat AI's ear and throttle. The buffer is what the room is
        # saying (commands and the bot's own lines aside); the timestamps
        # keep it from ever being the loudest voice in chat.
        self._chat_buf = []                        # [(nick, text), ...]
        self._chat_lock = threading.Lock()
        self._chat_ai_last = 0.0                   # last unprompted line
        self._chat_ai_mention_last = 0.0           # last mention reply
        self._chat_ai_times = []                   # lines posted, last hour
        self._chat_ai_names = set(
            str(n).lower() for n in
            (cfg.get("chat_ai_names") or ["doc", "docbot"]))
        self._chat_ai_names.add((self.nick or "").lower())
        # Long-term memory for the chat AI. The object exists even when
        # the feature is off (!forget still works on old data), but
        # nothing is ever RECORDED unless chat_ai_enabled is true: the
        # feature owns its data.
        self._memory = memory_mod.Memory(
            cfg.get("memory_db_path") or memory_mod.DB_PATH)
        self._memory.prune()
        self._opts = {                      # passed through to funfacts
            "spice": cfg.get("spice", "clean"),
            "max_fact_chars": int(cfg.get("max_fact_chars", 200)),
            "fact_source": cfg.get("fact_source", "sources"),
        "answer_questions": cfg.get("answer_questions", True),
            "llm_api_key": cfg.get("llm_api_key", ""),
            "llm_base_url": cfg.get("llm_base_url", ""),
            "llm_model": cfg.get("llm_model", ""),
            "google_api_key": cfg.get("google_api_key", ""),
            "google_cx": cfg.get("google_cx", ""),
            "serper_api_key": cfg.get("serper_api_key", ""),
        "tavily_api_key": cfg.get("tavily_api_key", "")
        or os.environ.get("TAVILY_API_KEY", ""),
            "tavily_api_key": cfg.get("tavily_api_key", "")
            or os.environ.get("TAVILY_API_KEY", ""),
            "debug": bool(cfg.get("debug")),
        }

    def _log(self, msg: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    def _maybe_refresh_token(self) -> None:
        """Keep the OAuth token alive: reuse/refresh the saved login (no prompt)
        and update the token used for IRC PASS. Called before every connect and
        periodically by the keeper thread, so the bot never forces a re-login
        just because a token expired mid-stream."""
        with self._refresh_lock:
            self._refresh_locked()

    def _refresh_locked(self) -> None:
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
            # The chat AI hears the bot's own lines, so it can tell what
            # it has already said and does not repeat itself.
            with self._chat_lock:
                self._chat_buf.append((self.nick or "bot",
                                       " ".join(text.split())[:200]))
                if len(self._chat_buf) > 60:
                    del self._chat_buf[:len(self._chat_buf) - 60]

    # ---- startup diagnostics -------------------------------------------
    def _note_own_state(self, tags: dict) -> None:
        """Record and report this bot's own standing in the channel.

        Driven by USERSTATE, so it runs on join and after every message we
        send. Logs only when the answer changes: without that, a bot that
        replies to every command would reprint the same line constantly.
        """
        mod, badges = tags.get("mod"), tags.get("badges", "")
        if mod is None and not badges:
            return                          # nothing to learn from this line
        # lead_moderator/1 too: the role replaces the moderator badge, so
        # without this a lead-mod bot would be told it is not a moderator.
        is_mod = (mod == "1") or ("moderator/1" in badges) \
            or ("lead_moderator/1" in badges) or ("broadcaster/1" in badges)
        if is_mod == self._bot_is_mod:
            return
        self._bot_is_mod = is_mod
        if is_mod:
            self._log(f"[access] {self.nick} is a moderator of "
                      f"{self.channel}.")
        else:
            self._log(f"[access] PROBLEM: {self.nick} is NOT a moderator of "
                      f"{self.channel}. Run /mod {self.nick} in that channel - "
                      f"until then {self.cfg.get('prefix', '!')}bot and "
                      f"{self.cfg.get('prefix', '!')}reminder will refuse it.")

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
        if not (self.cfg.get("client_secret") or "").strip():
            # Device-flow access tokens last about 4 hours, and a
            # Confidential app (the Dev Console default) cannot renew one
            # without its secret. Say so now rather than letting the login
            # quietly die mid-stream and then blaming something else.
            self._log("[auth] no client_secret in config.json - if your app is "
                      "a Confidential client the login expires in about 4 "
                      "hours and will need 'python3 bot.py --login' again. "
                      "Set the app's client type to Public, or add the "
                      "secret.")
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

        # Moderator status is deliberately NOT probed here. Get Moderators
        # only answers for the broadcaster's own token, so for a bot account
        # it 401s every time and proves nothing either way - which is how an
        # invented scope name ended up in auth.SCOPES and locked the bot out
        # of login entirely. The truth arrives on the USERSTATE line when we
        # join, and _note_own_state reports it.
        self._log("[access] moderator status is read from chat on join "
                  "(USERSTATE), not from the API.")

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
        if self.cfg.get("chat_ai_enabled", False):
            # The chat AI's heartbeat: quiet-room openers, offline-gated.
            talker = threading.Thread(
                target=self._chat_ai_keeper, name="chat-ai", daemon=True
            )
            talker.start()
        librarian = threading.Thread(
            target=self._names_keeper, name="names-topup", daemon=True
        )
        librarian.start()

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

        # Only three fields are guaranteed. PRIVMSG carries a fourth (the
        # text), but USERSTATE and ROOMSTATE stop at the target:
        #   :tmi.twitch.tv USERSTATE #channel
        # Requiring four here made every USERSTATE line fall through, which
        # is why the bot never learned its own moderator status.
        parts = line.split(" ", 3)
        if len(parts) < 3:
            if " NOTICE " in line:
                self._log("NOTICE " + line)
            return

        src, command, target = parts[0], parts[1], parts[2]
        trailing = parts[3] if len(parts) > 3 else ""
        if command == "PRIVMSG":
            match = re.match(r":([^!]+)!", src)
            nick = match.group(1) if match else "?"
            nick = tags.get("display-name") or nick
            message = trailing[1:] if trailing.startswith(":") else trailing
            login_match = re.match(r":([^!]+)!", src)
            login = (login_match.group(1) if login_match else nick).lower()
            self._on_message(nick, target, message, login,
                             tags.get("badges", ""))
        elif command == "USERNOTICE":
            # Twitch sends this for subs, gift subs and - the one that matters
            # here - raids. Nothing handled it before, so raids were invisible.
            if tags.get("msg-id") == "raid":
                self._on_raid(tags)
        elif command == "USERSTATE":
            # Twitch sends this when we join a channel and again after every
            # PRIVMSG we send, and it carries OUR OWN badges. That is the only
            # scope-free way to learn whether this bot is a moderator here.
            self._note_own_state(tags)
        elif command == "NOTICE":
            self._log("NOTICE " + line)

    # ---- chat ---------------------------------------------------------
    def _on_message(self, nick: str, target: str, message: str,
                    login: str = "", badges: str = "") -> None:
        # Anything anyone says counts as chat being alive, not just commands.
        self._last_chat = time.time()
        # ...and anyone who says anything counts as present. Presence only
        # ever decides whether an @-tag would reach somebody - never who gets
        # named in a feud - and it is kept in memory, not in a file.
        self._beef_seen.note(nick)
        # The chat AI's ear. Runs before the command check: most of what
        # it should hear is plain chatter, which never starts with the
        # prefix. Cheap by design - a list append and, rarely, a job.
        if (nick or "").lower() != (self.nick or "").lower():
            with self._chat_lock:
                self._chat_buf.append(
                    (nick, " ".join((message or "").split())[:200]))
                if len(self._chat_buf) > 60:
                    del self._chat_buf[:len(self._chat_buf) - 60]
            if self.cfg.get("chat_ai_enabled", False):
                self._memory.note(nick, login, message)
            kind = self._chat_ai_kind(nick, badges, message)
            if kind:
                self._maybe_chime(nick, login, kind, message)
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

        # !cb off/on/status moderates the random chatter and must stay
        # reachable while the bot is switched off, or a moderator could not
        # re-enable it. A bare !cb still falls through to the normal gate.
        if command in CB_COMMANDS and self._cb_switch(nick, badges, argument):
            return

        # !so off/on/status must stay reachable while the bot is switched off,
        # or a moderator could not turn shoutouts back on.
        if command in SO_COMMANDS and self._so_switch(nick, badges, argument):
            return

        # !beef off/on/status must stay reachable while the bot is switched
        # off, for the same reason !so and !cb do.
        if command in BEEF_COMMANDS and self._beef_switch(nick, badges,
                                                          argument):
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

        # Defining a command is moderation rather than chatter, so like
        # !haul update it stays reachable while the bot is switched off.
        if command in CMD_COMMANDS:
            self._cmd_command(nick, badges, argument)
            return

        # !forget <viewer> erases the chat AI's memory of someone. A
        # moderation command, reachable while the bot is switched off.
        if command in FORGET_COMMANDS:
            self._forget_command(nick, badges, argument)
            return

        if self.paused:
            return

        extras_enabled = bool(self.cfg.get("fun_commands", True))
        # !ask is a core command like !funfact and !whois: always available,
        # whatever fun_commands or chat_ai_enabled are set to. Without an LLM
        # it answers from the fact engine, so it is never a paid-only command.
        if command in ("funfact", "ask") or command in HELP_COMMANDS \
                or command in WHOIS_COMMANDS \
                or command in TWITCH_COMMANDS:
            pass
        elif command in SMK_ALIASES:
            command = "smk"
        elif extras_enabled and (command in EXTRAS_COMMANDS
                                 or command in CB_COMMANDS
                                 or command in SO_COMMANDS
                                 or command in BEEF_COMMANDS
                                 or command in REVENGE_COMMANDS):
            pass
        elif command in self.custom_cmds:
            # A command a moderator defined themselves. Deliberately outside
            # fun_commands: they created it, so they expect it to answer.
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

        if command in TWITCH_COMMANDS and not argument.strip():
            self._say(
                f"@{nick} usage: {prefix}twitch <twitch name>  "
                f"(e.g. {prefix}twitch hardclaws)"
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

    @staticmethod
    def _chunks(parts, budget: int, sep: str = " | ") -> list[str]:
        """Pack `parts` into groups that each fit inside `budget` characters.

        A single part longer than the budget is kept whole and passed through;
        cutting a command name in half would advertise one that does not exist.
        """
        out, current = [], ""
        for part in parts:
            if not part:
                continue
            candidate = current + sep + part if current else part
            if current and len(candidate) > budget:
                out.append(current)
                current = part
            else:
                current = candidate
        if current:
            out.append(current)
        return out

    def _queue_say(self, text: str) -> None:
        """Post `text` from the worker thread instead of this one.

        _say sleeps to pace messages, and while it sleeps the reader thread is
        not reading. A multi-line !help used to sit there for seconds; the
        commands that arrived during the freeze were then read in a burst, and
        the channel-wide cooldown dropped them. Multi-line output goes through
        the queue so reading never stops.
        """
        self._jobs.put(("", "", "", "say", text))

    def _msg_limit(self) -> int:
        return max(60, min(500, int(self.cfg.get("max_message_chars", 450))))

    def _queue_fitted(self, head: str, text: str) -> None:
        """Queue head + text, splitting on word boundaries if it will not fit.

        Splitting rather than trimming: a help line cut mid-word would tell
        someone to type half a command.
        """
        limit = self._msg_limit()
        if len(head) + len(text) <= limit:
            self._queue_say(head + text)
            return
        for chunk in self._chunks(text.split(), limit - len(head), sep=" "):
            self._queue_say(head + chunk)
            head = ""           # continuation lines carry no repeated prefix

    # Relative gaps between a story's five lines: headline, act 1, act 2,
    # act 3, verdict. Growing, because a story should tighten as it goes, and
    # the pause before the winner is announced is the whole point.
    # Seconds between one part of a beef story and the next - a plain,
    # predictable interval. It used to grow (0.75x..1.5x of the config),
    # which made "beef_act_delay: 10" produce a 7.5s first gap and read as
    # broken on stream; the knob now means exactly what it says.
    def _beef_gap(self) -> float:
        try:
            return max(0.0, float(self.cfg.get("beef_act_delay", 4.0) or 0.0))
        except (TypeError, ValueError):
            return 4.0

    def _tell_beef(self, result: dict) -> None:
        """Post a feud's story: the headline NOW, the body drip-fed.

        The headline is always the template one - it carries the @-tag
        decision, and that is never the model's to make. It is queued before
        the LLM is even asked, so the command always answers instantly.

        Then the body lines: the model's, if beefllm delivered four lines
        that passed the shape checks inside the deadline; the templates',
        otherwise. Scoring (in the caller) uses `result` either way - the
        winner was rolled before any text existed, and a story that
        disagrees with the roll is a validation failure, not a story.
        """
        lines = result["lines"]
        # No "BEEF |" prefix: the labels read like a form and took away from
        # the story. The lines stand alone; the pacing does the structuring.
        self._queue_say(lines[0])
        body = lines[1:]
        delay = self._beef_gap()
        started = time.time()      # gaps are measured from the headline
        # Burst mode (delay 0) has no gap to write in - the template lines
        # are queued immediately, so there is nothing for the model to fill.
        if delay > 0 and beefllm.available(self.cfg):
            got = beefllm.write_story(result, self.cfg)
            if got:
                body = got
                self._log(f"!beef acts written by the LLM "
                          f"({self.cfg.get('llm_model') or 'default model'})")
            else:
                self._log("!beef LLM pass failed or missed the deadline - "
                          "templates used")
        self._schedule_beef_rest(body, delay, started)

    def _schedule_beef_rest(self, body: list, delay: float,
                            started: float = None) -> None:
        """Drip the body of a beef story out, `delay` seconds apart.

        Five messages at once is a wall - chat reads the ending before the
        middle, and there is nowhere for anyone to react. The acts go out on
        timers (the riddle answer already uses this pattern) so the worker
        thread stays free for other commands while the story breathes, and
        every send still passes through the queue's pacing.

        `delay` is beef_act_delay seconds - the same gap every time, so the
        config knob behaves exactly as it reads. 0 sends the rest at once
        (the tests rely on that for determinism).
        """
        if delay <= 0.0:
            for line in body:
                self._queue_say(line)
            return
        # Gaps are measured from the headline, not from whenever the LLM
        # finished thinking - waiting on the model must not stretch the
        # pacing the config promised.
        base = started if started is not None else time.time()
        for i, line in enumerate(body, 1):
            wait = max(0.05, i * delay - (time.time() - base))
            t = threading.Timer(wait, self._queue_say, args=(line,))
            t.daemon = True
            t.start()

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
        """Post Wikipedia's own words about a person - not a model's take."""
        try:
            result = whois.lookup(
                query, int(self.cfg.get("whois_max_chars",
                                        whois.DEFAULT_MAX_CHARS)))
        except whois.WhoisError as exc:
            self._log(f"whois {query!r} failed: {exc}")
            self._say(f"@{nick} I couldn't reach Wikipedia just now - "
                      f"try again in a moment.")
            return
        if not result.get("found"):
            self._say(self._fit(f"@{nick} ", result.get("reason")
                                or "I couldn't find that."))
            return
        head = f"WhoIs | {result.get('title') or query}"
        description = (result.get("description") or "").strip()
        if description:
            head += f" ({description})"
        self._say(self._fit(head + ": ", result.get("text") or ""))
        self._log(f"whois {query!r} -> {result.get('title')}")

    def _reply_twitch(self, nick: str, query: str) -> None:
        """Post the Twitch profile for a login - the streamer, not the celeb."""
        result = whois.twitch_lookup(query, self._access.helix)
        if not result.get("found"):
            self._say(self._fit(f"@{nick} ", result.get("reason")
                                or "I couldn't find that channel."))
            return
        name = result.get("display_name") or query
        self._say(self._fit(f"Twitch | {name} | ",
                            whois.format_twitch(result.get("profile") or {})))
        self._log(f"twitch {query!r} -> {name}")

    @staticmethod
    def _mention(nick: str) -> str:
        return f"@{nick} " if nick else ""

    def _cb_excluded(self) -> tuple:
        """Voices the current config does not want.

        Passed to the generator rather than filtered afterwards, so a dropped
        voice is never drawn in the first place.
        """
        return () if self.cfg.get("cb_yell_enabled", True) else ("yell",)

    def _cb_allowed(self, badges: str) -> bool:
        """Whether these badges may run !cb on demand.

        Separate from the rate limit, which still applies on top: this decides
        *whether* a request is honoured at all, not how often.
        """
        want = str(self.cfg.get("cb_command_access", "everyone")).lower()
        if want == "everyone":
            return True
        tier = access.tier_from_badges(badges)
        if want == "broadcaster":
            return tier == "broadcaster"
        # "moderator", and anything misspelt, means mod or broadcaster.
        return tier in ("broadcaster", "moderator")

    def _cb_switch(self, nick: str, badges: str, argument: str) -> bool:
        """!cb off | !cb on | !cb status - moderates the bot's OWN chatter.

        That is the chat AI's chime-ins and quiet-room openers; !cb itself
        keeps working on demand. Returns True when the argument was a switch
        verb, so the caller does not also post a radio line on the same
        line. "Off" lasts until the next restart - the same split `!bot
        off` uses, so a moderator can quiet the bot mid-stream without
        editing a file.
        """
        verb = (argument or "").strip().lower()
        if verb not in ("off", "on", "status", "disable", "enable",
                        "pause", "resume"):
            return False
        pre = self.cfg.get("prefix", "!")
        if access.tier_from_badges(badges) not in ("broadcaster", "moderator"):
            # Silent for everyone else, as with !bot: answering would make the
            # switch itself a spam vector for viewers who find it by accident.
            self._log(f"!cb {verb} from {nick} ignored - not a moderator")
            return True
        if verb in ("off", "disable", "pause"):
            self._cb_ambient_off = True
            self._say(f"@{nick} doc's own chatter is OFF - {pre}cb on "
                      f"brings it back. {pre}cb on its own still works.")
        elif verb in ("on", "enable", "resume"):
            self._cb_ambient_off = False
            self._say(f"@{nick} doc's own chatter is back ON.")
        else:
            state = "OFF" if self._cb_ambient_off else "ON"
            self._say(f"@{nick} doc's own chatter is {state}. {pre}cb off "
                      f"to silence it, {pre}cb on to resume.")
        self._log(f"!cb {verb} from {nick} -> "
                  f"ambient={not self._cb_ambient_off}")
        return True

    @staticmethod
    def _unescape_tag(value: str) -> str:
        """Undo IRC tag escaping. Twitch sends a space as \\s."""
        return (value or "").replace("\\s", " ").replace("\\:", ";")

    def _on_raid(self, tags: dict) -> None:
        """Another channel just raided in. Shout them out.

        Runs off the worker thread: the Helix lookup is best-effort and must
        never hold up the read loop.
        """
        if not self.cfg.get("shoutout_enabled", True) or self._so_off:
            return
        name = self._unescape_tag(tags.get("msg-param-displayName", ""))
        login = self._unescape_tag(tags.get("msg-param-login", ""))
        count = self._unescape_tag(tags.get("msg-param-viewerCount", ""))
        if not (name or login):
            self._log("[so] raid notice carried no raider name; skipped")
            return
        self._jobs.put((name, login or name.lower(), "", "raid", count))

    def _say_shoutout(self, name: str, login: str, count,
                      is_raid: bool = True) -> None:
        """Post one shoutout, looking the channel up if we can.

        The lookup is optional. A raid is the worst moment to be waiting on a
        network call, and the token can be expired, so the name-and-count
        shoutout goes out regardless.
        """
        with self._so_lock:
            profile = None
            stream = None
            last_game = ""
            helix = self._access.helix
            if helix is not None:
                try:
                    profile = helix.channel_profile(login or name)
                except Exception as exc:
                    self._log(f"[so] profile lookup failed: {exc!r}")
                try:
                    # Their live row, for the game. None if they are offline.
                    stream = helix.stream_info(user_login=login or name)
                except Exception as exc:
                    self._log(f"[so] raider stream lookup failed: {exc!r}")
            if profile and profile.get("display_name"):
                name = profile["display_name"]
                login = profile.get("login") or login
            if not stream and profile and profile.get("id") and helix:
                # Offline, so ask the one endpoint that knows what they were
                # last on. Skipped entirely when they are live: Get Streams
                # already gave the current category.
                try:
                    info = helix.channel_info(profile["id"])
                    last_game = (info or {}).get("game_name") or ""
                except Exception as exc:
                    self._log(f"[so] last category lookup failed: {exc!r}")
            line = shoutout_mod.format_raid(
                name, count, login, profile, theme=self._so_theme(helix),
                raider_stream=stream, last_game=last_game, is_raid=is_raid)
            if not line:
                self._log("[so] nothing trustworthy to say; skipped")
                return
            self._say(self._fit(f"{shoutout_mod.LABEL} | ", line))
            self._log(f"[so] {name} ({count} viewers)")

    def _so_theme(self, helix) -> str:
        """Which shoutout flavour to use: what this channel is streaming.

        Cached for ten minutes rather than asked on every raid, because a
        category changes a handful of times a stream at most and a raid should
        not wait on a lookup for its own wording. An unknown or unresolvable
        category falls back to generic - never to a guess.
        """
        forced = str(self.cfg.get("shoutout_theme", "auto") or "auto").lower()
        if forced != "auto":
            return forced if forced in shoutout_mod.THEMES else "generic"
        now = time.time()
        cached, until = self._so_theme_cache
        if until > now:
            return cached
        theme = "generic"
        if helix is not None:
            try:
                self._resolve_broadcaster(self.cfg.get("channel", ""))
                info = helix.stream_info(user_id=self._broadcaster_id)
                theme = shoutout_mod.theme_for_game(
                    (info or {}).get("game_name"))
            except Exception as exc:
                self._log(f"[so] category lookup failed: {exc!r}")
        self._so_theme_cache = (theme, now + 600.0)
        return theme

    def _say_custom(self, nick: str, command: str) -> None:
        """Post a command a moderator defined."""
        if not self.cfg.get("custom_commands_enabled", True):
            return
        message = self.custom_cmds.get(command)
        if not message:
            # Deleted between being queued and being handled. Say nothing
            # rather than answer a command that no longer exists.
            return
        self._say(f"@{nick} "
                  f"{customcmds_mod.CommandSet.render(message, nick)}")

    def _cmd_command(self, nick: str, badges: str, argument: str) -> bool:
        """!cmd add | edit | delete | list - moderators define chat commands.

        Handled inline, not queued: it reads and writes one local file and
        makes no network call, so there is nothing to pace and no reason to
        make a moderator wait behind the rate limiter to fix a typo.
        """
        pre = self.cfg.get("prefix", "!")
        sub, _, rest = argument.partition(" ")
        sub, rest = sub.strip().lower(), rest.strip()

        if sub == "list":
            # Read-only and useful to everyone, so viewers get it too.
            if not self.cfg.get("custom_commands_enabled", True):
                self._say(f"@{nick} custom commands are switched off in "
                          f"the config (custom_commands_enabled).")
                return True
            names = self.custom_cmds.names()
            if not names:
                self._say(f"@{nick} no custom commands yet. Mods: {pre}cmd add "
                          f"<name> <what it should say>")
                return True
            shown = " ".join(f"{pre}{n}" for n in names)
            self._say(f"@{nick} {len(names)} custom command"
                      f"{'s' if len(names) != 1 else ''}: {shown}")
            return True

        if access.tier_from_badges(badges) not in ("broadcaster", "moderator"):
            # Silent for viewers, like !so off: no reply to build a flood from.
            self._log(f"!cmd {sub or '?'} from {nick} ignored - not a mod")
            return True

        if sub in ("", "help"):
            self._say(f"@{nick} {pre}cmd add <name> <message> | {pre}cmd edit "
                      f"<name> <message> | {pre}cmd delete <name> | "
                      f"{pre}cmd list")
            return True
        if sub not in ("add", "edit", "delete"):
            self._say(f"@{nick} I don't do '{pre}cmd {sub}' - try add, edit, "
                      f"delete or list.")
            return True

        if sub == "delete":
            name = rest
            if not name:
                self._say(f"@{nick} {pre}cmd delete needs the command name.")
                return True
            ok, why = self.custom_cmds.delete(name)
        else:
            name, _, message = rest.partition(" ")
            if not name or not message.strip():
                self._say(f"@{nick} {pre}cmd {sub} <name> <what it should say>")
                return True
            if sub == "add":
                ok, why = self.custom_cmds.add(name, message)
            else:
                ok, why = self.custom_cmds.edit(name, message)

        if ok and not self.custom_cmds.save():
            why += " - but I could not save it, so it will not survive a restart"
        self._say(f"@{nick} {why}")
        self._log(f"!cmd {sub} {name} from {nick} -> ok={ok}")
        return True

    def _beef_switch(self, nick: str, badges: str, argument: str) -> bool:
        """!beef off | on | status - moderators only, silent for viewers."""
        sub = " ".join((argument or "").split()).lower()
        if sub not in ("off", "on", "status", "enable", "disable"):
            return False
        if access.tier_from_badges(badges) not in ("broadcaster", "moderator"):
            self._log(f"!beef {sub} from {nick} ignored - not a moderator")
            return True
        if sub in ("off", "disable"):
            self._beef_off = True
        elif sub in ("on", "enable"):
            self._beef_off = False
        msg = f"@{nick} !beef is {'OFF' if self._beef_off else 'on'}."
        if sub == "status":
            src = ("LLM when the model delivers, templates otherwise"
                   if beefllm.available(self.cfg)
                   else "templates (no LLM in use)")
            # Pacing too, so "did my config take effect?" is one command
            # instead of a guess. Config is read at startup - a config.json
            # edit does nothing until the bot restarts.
            msg += (f" Stories: {src}. "
                    f"Lines every {self._beef_gap():g}s "
                    f"(beef_act_delay; config edits need a restart).")
        self._say(msg)
        return True

    def _reply_beef(self, nick: str, argument: str) -> None:
        """!beef <rival> [topic] - a feud in five clean lines, queued not
        said inline.

        The issuer is whoever typed it: that is the point of the game, and it
        is a person choosing to put themselves in.

        `!beef random` randomises the genre, NOT the opponent. Pulling a
        bystander out of chat into a public feud they never asked for is the
        fastest way to turn a joke command into a harassment report, and their
        chat reads it too.
        """
        args = " ".join((argument or "").split())

        # The scoreboard is read-only, so it answers even while the game is
        # switched off: turning the feuds off should not hide the standings,
        # and "!beef stats" must never start a feud with somebody called
        # Stats. This runs before the off gates on purpose.
        first, _, rest = args.partition(" ")
        if first.lower() in BEEF_STATS_WORDS:
            self._say_beef_stats(nick, rest.strip())
            return

        if not self.cfg.get("beef_enabled", True) or self._beef_off:
            self._say(f"@{nick} !beef is {self._beef_off_reason()}.")
            return

        if not args:
            # A bare !beef is ambiguous - against whom? Everything else
            # plays: an unusable rival name falls back to one of the named
            # characters rather than dead-ending the joke.
            self._say(f"@{nick} usage: !beef <name> [topic] - e.g. !beef "
                      f"Hardclaws zwift, !beef Hardclaws Eating Tacos, or "
                      f"!beef random")
            return
        rival, _, rest = args.partition(" ")
        rival = self._beef_canonical_rival(rival)
        if not rest.strip() and rival.lower() in beef_mod.GENRES:
            # "!beef zwift" means a beef set in Zwift, not a feud against
            # somebody called zwift.
            rival, rest = "", rival
        genre, theme = "", ""
        # Extra @names are spectators, not words: "!beef @a @truckingwithdoc
        # beer alpe" once matched the trucking GENRE, because the leftover
        # @truckingwithdoc contains "truck" - and the actual theme ("beer
        # alpe") was never seen again. Drop @tokens before parsing.
        rest = " ".join(t for t in rest.split() if not t.startswith("@"))
        if rest:
            genre = beef_mod.match_genre(rest)
            if not genre:
                # Freeform theme: "!beef @W_E_S_T_Y Eating Tacos" is a taco
                # feud. The words headline the story as typed, go to the LLM
                # pass, and back the template fallback's theme pools - the
                # player's words are never swapped for a random genre.
                theme = rest
        result = beef_mod.feud(nick, rival, genre, theme=theme,
                               tag=bool(self._beef_tag_target(rival)))
        if not result:
            self._say(f"@{nick} I could not build a beef from that - try "
                      f"!beef <name> [genre]")
            return
        self._tell_beef(result)
        # Scored after the lines are queued, so a scoring hiccup can never
        # cost chat the story - and because the story is the point.
        self.beef_state.record(nick, result["rival"], result["genre"],
                               result["issuer_won"],
                               theme=result.get("theme", ""))
        self._log(f"!beef {nick} vs {result['rival']} "
                  f"({result['theme'] or result['genre']}) - "
                  f"winner {result['winner']}")

    def _reply_revenge(self, nick: str) -> None:
        """!revenge - rematch the rival who just beat you, inside the window.

        The window is a timestamp in the state file, not a Timer on a thread:
        the worker that ran the original beef is long gone by the time the
        player types this, and a restart inside the window must not eat the
        rematch. A fresh 50/50 roll - an unloseable rematch would not be
        worth the extra point.
        """
        if not self.cfg.get("beef_enabled", True) or self._beef_off:
            self._say(f"@{nick} !beef is {self._beef_off_reason()}.")
            return
        window = self.beef_state.window_for(nick)
        if not window:
            prefix = self.cfg.get("prefix", "!")
            self._say(f"@{nick} nothing to avenge - start one: {prefix}beef "
                      f"<name> [genre], and if you lose you get 60s to "
                      f"{prefix}revenge it.")
            return
        result = beef_mod.feud(nick, window["rival"], window["genre"],
                               revenge=True, theme=window.get("theme", ""),
                               tag=bool(self._beef_tag_target(window["rival"])))
        if not result:
            self._say(f"@{nick} that rematch fell apart - try !beef "
                      f"{window['rival']} {window['genre']}")
            return
        self._tell_beef(result)
        self.beef_state.record(nick, result["rival"], result["genre"],
                               result["issuer_won"], revenge=True,
                               theme=result.get("theme", ""))
        self._log(f"!revenge {nick} vs {result['rival']} "
                  f"({result['genre']}) - winner {result['winner']}")

    def _beef_off_reason(self) -> str:
        """Why the beef game is off - the config key when config did it,
        the way back when a moderator did. Named either way: a switch that
        just says 'off' makes the user guess which lever to pull."""
        if not self.cfg.get("beef_enabled", True):
            return "switched off in the config (beef_enabled)"
        return "switched off - a moderator can turn it back on with !beef on"

    def _beef_canonical_rival(self, rival: str) -> str:
        """The properly-cased display name for a typed rival, when we know it.

        '!beef @truckingwithdoc poledancing' used to print 'truckingwithdoc'
        through all five messages, lowercase because that is how it was
        typed. If that person has played the game or been in chat, their
        real display name is on file - use it.
        """
        name = (rival or "").strip().lstrip("@").strip()
        if not name:
            return rival
        row = self.beef_state.players.get(name.lower())
        if row and row.get("name"):
            return row["name"]
        known = self._beef_seen.display(name)
        if known:
            return known
        # The one source that always knows: Twitch itself. The broadcaster
        # never plays the game and talks on mic, not in chat - but his
        # display name is one scope-free Helix call away, cached afterwards.
        helix = getattr(self._access, "helix", None)
        try:
            return (helix.display_name(name) if helix else None) or rival
        except Exception:
            return rival

    def _beef_tag_target(self, rival: str) -> str:
        """The rival name if it may be @-tagged in the headline, else "".

        Dynamic, and deliberately strict: a tag pings a person, so it has to
        be aimed at somebody who is actually there and has actually joined
        the game themselves. A bystander the issuer named still gets named -
        naming is the issuer's choice - but they are never pinged into a
        feud they never touched. The channel's broadcaster is the one
        presence this bot can assume without asking.
        """
        name = (rival or "").strip().lstrip("@").strip()
        if not name or not beef_mod._valid(name):
            return ""
        owner = (self.cfg.get("channel") or "").lstrip("#@ ").lower()
        if owner and name.lower() == owner:
            return name
        if self.beef_state.is_player(name) and self._beef_seen.seen(name):
            return name
        return ""

    def _say_beef_stats(self, nick: str, name: str) -> None:
        """!beef stats - the leaderboard, or one player's card."""
        if name:
            card = self.beef_state.card(name)
            if card:
                self._say(self._fit(f"@{nick} ", card))
            else:
                self._say(f"@{nick} no beefs on file for {name} - they "
                          f"haven't started one yet.")
            return
        line = self.beef_state.leader_line()
        if not line:
            prefix = self.cfg.get("prefix", "!")
            self._say(f"@{nick} nobody has started a beef yet - be the "
                      f"first: {prefix}beef <name>")
            return
        self._say(self._fit(f"@{nick} beef leaderboard: ", line))

    def _so_switch(self, nick: str, badges: str, argument: str) -> bool:
        """!so off | !so on | !so status - moderators only.

        Returns True when the argument was a switch verb, so the caller does
        not also treat it as a channel name to shout out.
        """
        verb = (argument or "").strip().lower()
        if verb not in ("off", "on", "status", "disable", "enable"):
            return False
        pre = self.cfg.get("prefix", "!")
        if access.tier_from_badges(badges) not in ("broadcaster", "moderator"):
            self._log(f"!so {verb} from {nick} ignored - not a moderator")
            return True
        if verb in ("off", "disable"):
            self._so_off = True
            self._say(f"@{nick} shoutouts are OFF - raids will not be "
                      f"announced. {pre}so on to resume.")
        elif verb in ("on", "enable"):
            if not self.cfg.get("shoutout_enabled", True):
                self._say(f"@{nick} shoutouts are switched off in the "
                          f"config (shoutout_enabled).")
                return True
            self._so_off = False
            self._say(f"@{nick} shoutouts are back ON - raids will be "
                      f"announced again.")
        else:
            state = ("OFF in the config"
                     if not self.cfg.get("shoutout_enabled", True)
                     else ("OFF" if self._so_off else "ON"))
            self._say(f"@{nick} shoutouts are {state}. {pre}so off to silence "
                      f"them, {pre}so on to resume.")
        self._log(f"!so {verb} from {nick} -> on={not self._so_off}")
        return True

    def _reply_so(self, nick: str, badges: str, argument: str) -> None:
        """!so <name> - a moderator triggers a shoutout by hand."""
        query = (argument or "").strip()
        if not query:
            pre = self.cfg.get("prefix", "!")
            self._say(f"@{nick} usage: {pre}so <twitch name>  (e.g. "
                      f"{pre}so hardclaws)")
            return
        if access.tier_from_badges(badges) not in ("broadcaster", "moderator"):
            # Silent, as with !bot and !cb: it posts a link into chat, so
            # answering every viewer who finds it becomes a spam vector.
            self._log(f"!so from {nick} ignored - not a moderator")
            return
        # One cleaning, used for both. The login has to be clean or the URL is
        # dead; the name has to be clean too, or the sentence reads "shoutout
        # to twitch.tv/hardclaws" because the moderator pasted a link.
        login = access.clean_login(query)
        # is_raid=False: they almost certainly did not raid, and a message
        # saying they did would be a small lie posted about a real person.
        self._say_shoutout(login or query, login, "", is_raid=False)

    def _reply_cb(self, nick: str, badges: str = "") -> None:
        """One line of CB chatter, on demand.

        Deliberately no `@nick` prefix: the bot is talking on the radio, not
        answering a question, and the mention breaks the voice. The log names
        whoever asked so a moderator can still trace it.
        """
        if not self.cfg.get("cb_command_enabled", True):
            self._log(f"!cb from {nick or 'chat'} ignored - cb_command_enabled "
                      f"is false")
            return
        if not self._cb_allowed(badges):
            self._log(f"!cb from {nick or 'chat'} ignored - "
                      f"cb_command_access is "
                      f"{self.cfg.get('cb_command_access', 'everyone')!r}")
            return
        post = trucker_mod.ramble(exclude=self._cb_excluded())
        self._say(self._fit(f"{post.label} | ", post.text))
        self._log(f"{post.label} ramble for {nick or 'chat'}: "
                  f"{post.text[:60]}")

    def _names_tick(self) -> int:
        """Top the !smk name pool up from Wikipedia. Returns names added.

        Runs off-thread: this makes one API call per category at about a
        second apart, and a chat command must never wait on it.
        """
        if not self.cfg.get("names_topup_enabled", True):
            return 0
        added = names_mod.pool.top_up()
        if added:
            counts = names_mod.pool.counts()
            self._log(f"names: +{added} harvested "
                      f"({counts['harvested']} cached, "
                      f"{counts['female']}f/{counts['male']}m available)")
        return added

    def _names_keeper(self) -> None:
        # A short first wait so a restart does not stall the join, then the
        # configured interval. Harvesting is a nicety, never a dependency.
        time.sleep(30.0)
        while self.running:
            try:
                self._names_tick()
            except Exception as exc:
                self._log(f"names top-up error: {exc!r}")
            for _ in range(int(float(self.cfg.get("names_topup_hours", 12))
                               * 3600)):
                if not self.running:
                    return
                time.sleep(1.0)

    def _chat_ai_keeper(self) -> None:
        """The chat AI's heartbeat: 15s ticks that watch for a quiet room.

        The idle-chat poster used to own this loop; the chat AI inherited
        the heartbeat when the poster was removed.
        """
        while self.running:
            time.sleep(15.0)
            if not self.running:
                return
            try:
                self._chat_ai_tick()
            except Exception as exc:
                self._log(f"chat-ai error: {exc!r}")

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
        helix = access.Helix(cfg.get("client_id", ""), token)
        # A 401 means the token died, and it can die between keeper wake-ups.
        # Let the client ask for a new one and retry, rather than logging the
        # 401 and leaving every follow check dead until a restart.
        helix.on_unauthorized = self._maybe_refresh_token
        return helix

    def _resolve_broadcaster(self, channel: str) -> str:
        helix = self._access.helix
        if not helix or not (helix.client_id and helix.token):
            return ""
        if not self._broadcaster_id:
            uid = helix.user_id(channel)   # user_id cleans '#' and '@' itself
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
            import math as _math
            self._say(f"@{nick} that's on cooldown - "
                      f"{_math.ceil(wait)}s to go.")
        elif "follow" in reason:
            helix = self._access.helix
            if helix is not None and helix.unauthorized:
                # The token is dead; the viewer's follow status was never the
                # problem. The generic line sends them chasing the wrong thing.
                self._say(f"@{nick} {reason} - the bot's Twitch login has "
                          f"expired, so it cannot check. A moderator will "
                          f"need to renew it.")
                if not self._warned_401:
                    self._warned_401 = True
                    self._log("[access] Twitch rejected the oauth token "
                              f"({helix.unauthorized} x 401). Run "
                              "'python3 bot.py --login', and add "
                              '"client_secret" to config.json so it can renew '
                              "itself instead of expiring every 4 hours.")
            else:
                self._say(f"@{nick} {reason} to use that command.")
        else:
            self._say(f"@{nick} {reason}.")

    def _worker(self) -> None:
        while True:
            nick, login, badges, command, argument = self._jobs.get()
            try:
                # A raid is not a command from a viewer. The raider is often
                # not a follower and carries no badges here, so running it
                # through the access gate would refuse the one thing that
                # should always be answered.
                if command == "raid":
                    self._say_shoutout(nick, login, argument)
                    continue

                # A literal line to post, queued by _queue_say.
                if command == "say":
                    self._say(argument)
                    continue

                # A bot-initiated chat-AI line: not a viewer command, so
                # the access gate (per-user cooldowns) does not apply - it
                # has its own cooldowns, set in _maybe_chime/_do_chime.
                if command == "chime":
                    self._do_chime(nick, argument)
                    continue

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
                elif command == "ask":
                    self._reply_ask(nick, argument)
                elif command in WHOIS_COMMANDS:
                    self._reply_whois(nick, argument)
                elif command in TWITCH_COMMANDS:
                    self._reply_twitch(nick, argument)
                elif command in CB_COMMANDS:
                    self._reply_cb(nick, badges)
                elif command in SO_COMMANDS:
                    self._reply_so(nick, badges, argument)
                elif command in BEEF_COMMANDS:
                    self._reply_beef(nick, argument)
                elif command in REVENGE_COMMANDS:
                    self._reply_revenge(nick)
                elif command in self.custom_cmds:
                    self._say_custom(nick, command)
                else:
                    self._reply_extra(nick, command, argument)
            except Exception as exc:
                self._log(f"{command} failed: {exc!r}")
            finally:
                self._jobs.task_done()

    # ---- the chat AI --------------------------------------------------
    def _chat_ai_kind(self, nick: str, badges: str, message: str) -> str | None:
        """What kind of chat-AI moment is this, if any?

        None for everything the bot should let pass: commands, the
        streamer's own lines (he has the floor), anything explicit, and
        text too short to be worth a line.
        """
        text = (message or "").strip()
        if len(text) < 3 or text.startswith(self.cfg.get("prefix", "!")):
            return None
        if funfacts._EXPLICIT.search(text) \
                or funfacts._TASTELESS.search(text):
            return None
        m = chatai.mention_kind(text, self._chat_ai_names)
        if "broadcaster/1" in (badges or ""):
            # The streamer has the floor: the bot never butts into his
            # lines with a chime-in. But a direct @-mention is him
            # addressing the bot, and ignoring it reads as broken - so
            # mentions reply, chime-ins stay off for him.
            return m
        return m or chatai.CHIME

    def _maybe_chime(self, nick: str, login: str, kind: str,
                     message: str) -> None:
        """Gate the moment, then hand the composing to a worker.

        The LLM call takes seconds and must never block the read loop; a
        job does the waiting. Failed attempts bump _chat_ai_last too (in
        _do_chime), so a declining model cannot be billed in a loop.
        """
        with self._chat_lock:
            buffer_len = len(self._chat_buf)
        if not chatai.should_speak(
                enabled=bool(self.cfg.get("chat_ai_enabled", False)),
                paused=self.paused,
                ambient_off=self._cb_ambient_off,
                kind=kind, roll=random.random(),
                chance=float(self.cfg.get("chat_ai_chance", 0.4)),
                now=time.time(), last=self._chat_ai_last,
                mention_last=self._chat_ai_mention_last,
                mention_cd=float(self.cfg.get(
                    "chat_ai_mention_cooldown", 60)),
                chime_cd=float(self.cfg.get("chat_ai_cooldown", 120)),
                times=self._chat_ai_times,
                max_hour=int(self.cfg.get("chat_ai_max_hour", 20)),
                buffer_len=buffer_len,
                min_chat=int(self.cfg.get("chat_ai_min_chat", 5))):
            return
        self._jobs.put((nick, login or (nick or "").lower(), "",
                        "chime", message))

    def _chat_ai_snapshot(self) -> list:
        with self._chat_lock:
            return list(self._chat_buf)

    def _chat_ai_line(self, lines: list, nick: str, text: str,
                      quiet: bool = False):
        """Compose one cleaned line, or None. Shared by chime, !ask and
        the quiet-room opener."""
        import llm as llm_mod
        persona = self.cfg.get("bot_personality", "") or \
            chatai.DEFAULT_PERSONA
        speakers = [n for n, _ in lines[-6:]] + [nick]
        memories = self._memory.recall(speakers) if self._memory.ok else []
        try:
            raw = llm_mod.chat_reply(
                chatai.system_prompt(persona),
                chatai.user_prompt(lines, nick, text, memories,
                                   quiet=quiet),
                self._opts)
        except Exception as exc:
            self._log(f"chat ai error: {exc!r}")
            return None
        if not raw or chatai.declined(raw):
            return None
        return chatai.clean_line(raw)

    def _chat_ai_tick(self, now: float = None) -> bool:
        """The quiet-room half of the chat AI.

        A chime-in can only trigger off someone's message - which is
        impossible when the room has gone silent, exactly when the bot
        should be doing the talking. This runs on the idle keeper's
        heartbeat: after chat_ai_quiet_seconds of silence, it queues one
        conversation opener, at most once per chat_ai_quiet_cooldown and
        inside the same hourly cap. The attempt is marked AT ENQUEUE, so
        a busy worker can never double-fire it.
        """
        if not self.cfg.get("chat_ai_enabled", False):
            return False
        if self.paused or self._cb_ambient_off:
            return False
        now = time.time() if now is None else now
        if now - self._last_chat < float(self.cfg.get(
                "chat_ai_quiet_seconds", 90)):
            return False                # chat is alive; the message path rules
        # An offline channel is not quiet, it is empty: without this gate
        # the bot would open conversations to nobody all night (same live
        # check the old idle poster used - scope-free GET /helix/streams).
        # Where the check cannot be settled it fires anyway: unknown is not
        # offline.
        helix = self._access.helix
        if helix is not None and helix.is_live() is False:
            return False
        if now - self._chat_ai_last < float(self.cfg.get(
                "chat_ai_quiet_cooldown", 150)):
            return False
        if len([t for t in self._chat_ai_times if now - t < 3600]) >= int(
                self.cfg.get("chat_ai_max_hour", 20)):
            return False
        self._chat_ai_last = now
        self._jobs.put(("", "", "", "chime", ""))
        return True

    def _do_chime(self, nick: str, text: str) -> None:
        """A bot-initiated line of chat, composed off the read loop.

        An empty nick is the quiet-room opener: posted bare (nobody to
        @), and no memory distill (nobody talked).
        """
        quiet = not (nick or "").strip()
        if not quiet:
            now = time.time()
            if now - self._chat_ai_mention_last < float(self.cfg.get(
                    "chat_ai_mention_cooldown", 60)):
                return                  # the room moved on while we queued
        line = self._chat_ai_line(self._chat_ai_snapshot(),
                                  nick or "chat", text, quiet=quiet)
        # Failed attempts back off too, or every following message would
        # pay for another model call. Mentions back off mentions; openers
        # were already marked at enqueue.
        now = time.time()
        if quiet:
            self._chat_ai_last = now
        else:
            self._chat_ai_mention_last = now
        if not line:
            # The model is down or declined. A chatty direct address still
            # gets a canned Doc line - the bot never goes fully mute on
            # "doc, hows it going?" - but a factual question stays silent
            # rather than risk a made-up answer.
            quip = chatai.smalltalk(text)
            if quip and not quiet:
                self._say(self._fit(f"@{nick} ", quip))
            return
        self._chat_ai_times = [t for t in self._chat_ai_times
                               if now - t < 3600] + [now]
        if quiet:
            self._say(self._fit("", line))
            self._log("chat ai opened the quiet room")
        else:
            self._say(self._fit(f"@{nick} ", line))
            self._log(f"chat ai replied to {nick}")
            self._distill(nick, self._chat_ai_snapshot())

    def _distill(self, nick: str, lines: list) -> None:
        """After talking, remember what is durable about the viewer.

        Runs on the worker thread, after the reply has posted, so the
        extra model call never delays a line of chat. Bounded for free:
        it only runs after a chime or an !ask, both already throttled.
        """
        import llm as llm_mod
        if not self.cfg.get("chat_ai_enabled", False):
            return              # !ask works with the AI off; memory does not
        if not self._memory.ok or not llm_mod.is_configured(self._opts):
            return
        theirs = [(n, t) for n, t in lines if (n or "").lower()
                  == (nick or "").lower()]
        if not theirs:
            return
        try:
            raw = llm_mod.chat_reply(
                memory_mod.DISTILL_RULES,
                memory_mod.distill_prompt(nick, theirs), self._opts)
        except Exception as exc:
            self._log(f"memory distill error: {exc!r}")
            return
        facts = memory_mod.parse_facts(raw)
        if facts:
            self._memory.remember(nick, facts)

    def _reply_ask(self, nick: str, argument: str) -> None:
        """!ask <anything> - the persona answers, falling back to facts.

        Without an LLM (or when the persona declines) the fact engine's
        question path answers instead - it works from Wikipedia alone, so
        !ask never becomes a paid-only command.
        """
        import llm as llm_mod
        q = (argument or "").strip()
        if not q:
            self._say(f"@{nick} ask me anything - a question or a topic.")
            return
        if llm_mod.is_configured(self._opts):
            snapshot = self._chat_ai_snapshot()
            line = self._chat_ai_line(snapshot, nick, q)
            if line:
                self._say(self._fit(f"@{nick} ", line))
                self._log(f"chat ai answered {nick}")
                self._distill(nick, snapshot)
                return
        # The persona is down or declined. Chatty questions still get a
        # canned Doc line - the fact engine would otherwise answer "how
        # are you today?" with the history of the word "today".
        quip = chatai.smalltalk(q)
        if quip:
            self._say(self._fit(f"@{nick} ", quip))
            return
        result = get_funfact(q, self._opts)
        self._reply(nick, q, result)

    def _forget_command(self, nick: str, badges: str, argument: str) -> None:
        """!forget <viewer> - erase every memory and logged message held
        about one viewer. Moderators only, silent for viewers."""
        who = (argument or "").strip().lstrip("@")
        if access.tier_from_badges(badges) not in ("broadcaster", "moderator"):
            self._log(f"!forget {who!r} from {nick} ignored - not a mod")
            return
        if not who:
            self._say(f"@{nick} forget who? !forget <viewer>")
            return
        dropped = self._memory.purge(who)
        self._say(f"@{nick} done - nothing remembered about {who} "
                  f"({dropped} fact{'s' if dropped != 1 else ''} and all "
                  f"their logged lines, gone).")
        self._log(f"memory purged for {who} by {nick}")

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
            f"{prefix}ask anything - the bot answers, in its own voice "
            "(or with a real fact)",
            f"{prefix}whois <name> - who that person is",
            f"{prefix}twitch <name> - who that Twitch channel is",
            f"{prefix}cb - the bot talks on the radio, or yells at a car"
            if self.cfg.get("cb_command_enabled", True) else None,
            f"{prefix}so <name> - shout a channel out (mods only)"
            if self.cfg.get("shoutout_enabled", True) else None,
            f"{prefix}beef <name> [topic] - start a feud ({prefix}beef "
            f"stats for the standings, {prefix}revenge after a loss)"
            if self.cfg.get("beef_enabled", True) else None,
        ]
        # Packed into as few messages as fit, rather than one "|" line.
        # Joining them all means every command added pushes the same message
        # closer to Twitch's limit until it goes over, and help silently stops
        # being deliverable. Chunking makes that impossible.
        limit = self._msg_limit()
        head, more = f"@{nick} commands: ", f"@{nick} more: "
        for i, chunk in enumerate(self._chunks(lines, limit - len(head))):
            self._queue_say((head if i == 0 else more) + chunk)
        self._queue_fitted(
            f"@{nick} ", "who can use them: broadcaster/mod every 30s, VIP "
            "and subscribers every 60s, followers of over a day every 5 "
            "minutes.")
        if self._is_mod(badges):
            # Only worth advertising to the people allowed to use it.
            self._queue_fitted(
                f"@{nick} ", f"mods: {prefix}reminder 60mins <message> or "
                f"{prefix}reminder 01:30PDT <message>, then {prefix}reminder "
                f"list / cancel <n>|all")
            self._queue_fitted(
                f"@{nick} ", f"mods: {prefix}haul update <cargo> / delete, "
                f"and {prefix}bot off / on / status")
            self._queue_fitted(
                f"@{nick} ", f"mods: {prefix}cmd add <name> <message> to "
                f"create your own command, {prefix}cmd list / {prefix}cmd "
                f"delete <name> to manage them")

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


def _doctor_questions(cfg: dict) -> None:
    """Report - and actually exercise - the free-form question path.

    --doctor printed a healthy login and a list of fact sources while saying
    nothing about whether questions can be answered at all. That left a
    working-looking doctor in front of a feature that had never run, and the
    only way to find out was to ask in chat and read the log afterwards.

    This makes one real call. A key that is present but rejected (401), or an
    OpenRouter account with no credits (402), fails here in two seconds instead
    of silently posting "couldn't find any fun facts" all evening.
    """
    print("\nClawFacts question answering\n")
    on = cfg.get("answer_questions", True)
    print(f"  answer_questions : {'on' if on else 'OFF'}")

    tkey = (cfg.get("tavily_api_key") or os.environ.get("TAVILY_API_KEY", "")
            or "").strip()
    skey = (cfg.get("serper_api_key") or "").strip()
    bits = []
    if tkey:
        bits.append("tavily")
    if skey:
        bits.append("serper")
    line = ", ".join(bits) if bits else (
        "none - only DuckDuckGo, which returns nothing for most "
        "free-form questions")
    print(f"  search for answers: {line}")

    try:
        import llm as llm_mod
    except Exception as exc:
        print(f"  llm module       : import failed - {exc!r}")
        return
    if not llm_mod.is_configured(cfg):
        print("  llm              : NOT configured - questions cannot be "
              "answered. Set llm_api_key (or GROQ_API_KEY / "
              "OPENROUTER_API_KEY), or point llm_base_url at a local Ollama.")
        return

    print("  llm              : configured - making one real call ...")
    llm_mod.reset_disable_state()
    try:
        got = llm_mod.answer_question(
            "What is the dew point?",
            ["The dew point is the temperature to which air must be cooled to "
             "become saturated with water vapour."],
            cfg,
        )
    except Exception as exc:
        print(f"  call raised      : {exc!r}")
        return
    if not got:
        print("  call             : FAILED - no answer came back. A rejected "
              "key (401/403) or no credits (402) prints its own line above.")
        return
    text = " ".join(str(got).split())
    print(f"  call             : OK - {text[:90]}"
          f"{'...' if len(text) > 90 else ''}")


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
        "answer_questions": cfg.get("answer_questions", True),
        "llm_api_key": cfg.get("llm_api_key", ""),
        "llm_base_url": cfg.get("llm_base_url", ""),
        "llm_model": cfg.get("llm_model", ""),
        "google_api_key": cfg.get("google_api_key", ""),
        "google_cx": cfg.get("google_cx", ""),
        "serper_api_key": cfg.get("serper_api_key", ""),
        "tavily_api_key": cfg.get("tavily_api_key", "")
        or os.environ.get("TAVILY_API_KEY", ""),
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


def run_doctor(cfg: dict) -> int:
    """Print exactly why the login does or does not stay alive.

    The 401 in the access log says the token was rejected but not why. Two of
    the reasons are invisible from chat - a tokens.json saved for a different
    client_id, and a missing refresh token - and both make renewal fail
    silently, so the bot works for four hours and then breaks.
    """
    print("ClawFacts login check\n")
    for line in auth.describe_login(cfg):
        print("  " + line)

    _doctor_questions(cfg)

    print("\n  attempting a real renewal ...")
    auth._WARNED.clear()      # so this run always shows the reason
    try:
        new = auth.refresh_if_possible(cfg)
    except Exception as exc:
        print(f"  renewal raised: {exc!r}")
        return 1
    if new:
        print("  renewal: OK - the bot can keep itself logged in.")
        return 0
    print("  renewal: did not happen. The lines above say why.")
    return 1


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force_login = "--login" in sys.argv
    do_selftest = "--selftest" in sys.argv
    do_doctor = "--doctor" in sys.argv
    path = args[0] if args else "config.json"

    cfg = load_config(path, require=not do_doctor)

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

    if do_doctor:
        raise SystemExit(run_doctor(cfg))

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
