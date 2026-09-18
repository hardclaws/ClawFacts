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
import moderation as moderation_mod
import ongoing as ongoing_mod
import names as names_mod
import trucker as trucker_mod
import beef as beef_mod
import beefstats as beefstats_mod
import beefllm
import shoutout as shoutout_mod
import customcmds as customcmds_mod
import adminpanel
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
# Moderation, on a moderator's word: !ban, !unban, !timeout. They work
# typed in the channel and whispered to the bot, and they answer while
# the bot is switched off - a ban is not a "bot feature" that pausing
# should take away.
MOD_COMMANDS = {"ban", "unban", "timeout", "untimeout"}
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
# The bot's voice: !persona show/list/set/custom/reset, moderators only.
PERSONALITY_COMMANDS = {"persona", "personality", "voice"}
# The sub goal: anyone can read it, moderators maintain it.
SUBGOAL_COMMANDS = {"subgoal", "subgoals"}

#: The USERNOTICE ids that announce a sub the whole channel can see.
#: 'submysterygift' is deliberately absent: the community-gift banner
#: is followed by one 'subgift' notice per recipient, and counting both
#: would count every gift twice.
_SUB_NOTICE_IDS = ("sub", "resub", "subgift", "anonsubgift")

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
    # addressed by name, and occasionally chimes in during light conversation.
    # OFF by default - flip chat_ai_enabled to let the bot hold its own in
    # chat. Every line it posts is one short cleaned line. Mention replies
    # wait out chat_ai_mention_cooldown; autonomous attempts win a chance roll,
    # wait out chat_ai_cooldown and stay under chat_ai_max_hour. Direct asks
    # are never hidden behind an autonomous cap or the !cb chatter switch.
    "chat_ai_enabled": False,
    # Chime-in cadence, retuned after field feedback ("timer bot"): the
    # conversation is carried by mention replies and !ask; chime-ins are
    # rare accents in LIGHT conversation, never a second voice in a flowing
    # room. 0.10 roll + a 10-minute cooldown + a 6/hour cap.
    "chat_ai_cooldown": 600,
    # The mention cooldown is PER VIEWER: each person gets one persona
    # reply per this many seconds; different people never block each
    # other. Live-fire, one channel-wide clock lost 6 of 10 direct
    # questions in six minutes with four people talking to the bot.
    # chat_ai_mention_pace is the channel-wide floor between any two
    # persona replies - just enough that the bot cannot be made to flood.
    "chat_ai_mention_cooldown": 60,
    # A question held by a rail is answered, but until then the room sees
    # nothing - and from chat, a rail and a crash look exactly the same,
    # which is why the same question gets asked three times. Acknowledge
    # the hold with the wait quoted. Below chat_ai_ack_min_seconds the
    # answer is close enough that a message about it would be noise.
    "chat_ai_ack_held": True,
    "chat_ai_ack_min_seconds": 5,
    "chat_ai_mention_pace": 8,
    # Memory distills (the model call that turns a viewer's lines into
    # remembered facts) run for a viewer only after they have said this
    # many new lines since their last distill, and at most once per this
    # many minutes. It was every reply; that was a third of the token
    # budget spent on 'NOTHING WORTH KEEPING'.
    "chat_ai_distill_lines": 4,
    "chat_ai_distill_minutes": 10,
    "chat_ai_chance": 0.10,
    "chat_ai_max_hour": 6,
    "chat_ai_min_chat": 5,
    # Autonomous chimes listen during light conversation but get out of the
    # way once chat is flowing. Activity is rolling, not the lifetime buffer.
    "chat_ai_context_seconds": 300,
    "chat_ai_busy_seconds": 30,
    "chat_ai_busy_messages": 4,
    # The quiet-room half: when nobody has spoken for chat_ai_quiet_seconds,
    # the bot opens the conversation itself (a question, a hook) rather than
    # waiting for a message to react to - at most once per
    # chat_ai_quiet_cooldown, inside the same hourly cap.
    "chat_ai_quiet_seconds": 300,
    "chat_ai_quiet_cooldown": 900,
    # Qwen3-family models "think" before answering, which on CPU turns a
    # one-line reply into a half-minute stall. true appends Qwen3's
    # documented /no_think soft switch to every prompt. No effect on
    # models that do not know the switch.
    "llm_no_think": False,
    # The second LLM provider: when the llm_* provider is rate-limited
    # (Groq's free tier 429s mid-stream), chat, sourced answers and fact
    # writing switch to this one until the window clears. Empty model = no
    # fallback (the default). A local
    # Ollama works here too: no key needed, and it is warmed at startup
    # like the primary.
    "llm_fallback_key": "",
    "llm_fallback_base_url": "",
    "llm_fallback_model": "",
    # ...or a whole ordered list of them, each with its own key, base and
    # model chain: Groq -> NVIDIA NIM -> Gemini -> OpenRouter. Every one
    # has its own rest window, so a dead key on one is skipped instead of
    # taking the next provider down with it. Entries look like
    # {"base_url": "...", "key": "...", "model": "a, b"} - or name a
    # "key_env" and keep the secret in bot.env. A key already in the
    # environment (NVIDIA_API_KEY, GEMINI_API_KEY) adds its provider on
    # its own, so an empty list still means "use whatever keys you have".
    "llm_fallback_providers": [],
    # Moderation on request: a moderator of THIS channel can ask the bot
    # to ban, time out or unban somebody - typed in the room or sent to
    # the bot privately. The bot itself has to be a moderator for Twitch
    # to accept the call at all, and the token needs the
    # moderator:manage:banned_users scope (run python3 bot.py --login once
    # after an upgrade). mod_logins is who may ask privately: a whisper
    # carries no channel badges, so the channel's own list is the
    # authority there. The broadcaster is always allowed.
    "mod_commands_enabled": True,
    "mod_logins": [],
    "mod_timeout_seconds": 600,
    "chat_ai_names": ["doc", "docbot"],
    "bot_personality": "",
    # The chat AI's memory: one SQLite file. Messages are pruned after
    # 90 days; distilled per-viewer facts stay until a mod runs
    # !forget <viewer>, which erases everything held about them.
    "memory_db_path": "chat_memory.db",
    # Runtime state files (like haul.json): the mod-chosen persona and
    # the sub goal survive restarts without touching config.json.
    "persona_state_path": "persona.json",
    "subgoal_state_path": "subgoal.json",
    # Subs the bot SEES in chat (sub, resub and gift announcements)
    # bump the sub goal on their own - Twitch lets only the
    # broadcaster's own token read the live count, so counting the
    # announcements is the honest automated half, and !subgoal count is
    # still the sync point for anything missed while offline.
    "subgoal_auto_count": True,
    # Keep the console log in a file too (e.g. "bot.log"): everything the
    # window shows, plus crash tracebacks, survives the scrollback.
    # Empty = console only.
    "log_file": "",
    # The admin panel: a password-protected web page served by the bot
    # itself (see adminpanel.py). Off by default. Create a login first with
    #   python3 bot.py --admin-user yourname
    # then set admin_panel_enabled true. It binds to loopback, so reach it
    # over an SSH tunnel (ssh -L 8477:127.0.0.1:8477 user@server) or set
    # admin_panel_bind to the VM's Tailscale address. A public bind
    # (0.0.0.0) is refused unless admin_panel_public is true as well.
    "admin_panel_enabled": False,
    "admin_panel_bind": "127.0.0.1",
    "admin_panel_port": 8477,
    "admin_panel_public": False,
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
    # Seconds between the lines when the chat AI performs - a song, a
    # poem, a story asked for by name ('docbot sing me a song'). Empty
    # means the same gap as beef_act_delay; 0 posts the piece at once.
    "chat_ai_perform_delay": "",
    # How long a mod's announcement ('docbot tell everyone Doc is on the
    # phone, radio silence') stands as the answer to 'your mic is muted' /
    # 'hello?' / 'is he afk?'. Each viewer is told once. 0 turns it off.
    "chat_ai_notice_minutes": 20,
    # The chat AI's working memory: the game it was asked to host ('lets
    # do a 3 round cycling quiz', 'round 2', 'whats the answer') and the
    # counts it was asked to keep ('keep count of dirty lepages'). Both
    # live in ongoing_state_path and survive a restart. A game nobody has
    # touched for chat_ai_activity_minutes is dropped; counts stay until
    # they are stopped. chat_ai_count_anyone lets any viewer bump a count
    # (off: mods, the broadcaster and whoever opened it - anyone can ASK).
    "ongoing_state_path": "ongoing.json",
    "chat_ai_activity_minutes": 45,
    "chat_ai_count_anyone": False,
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
    # weatherapi.com (free key at weatherapi.com/signup): when set, weather
    # questions are answered from it as one sentence to the asker -
    # "kvack, it is currently Clear in Wilkes-Barre, Pennsylvania. 63°F
    # (17°C). Feels like ..." - with Open-Meteo as the keyless fallback.
    "weatherapi_key": "",
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

    # Normalize fallback aliases and optional environment variables. The first
    # fallback implementation accepted only ``llm_fallback_key``; a natural
    # ``llm_fallback_api_key`` spelling was silently ignored and looked exactly
    # like failover never ran. Provider-specific environment keys are also a
    # valid source when the configured fallback URL names that provider.
    cfg["llm_fallback_model"] = str(
        cfg.get("llm_fallback_model")
        or cfg.get("llm_fallback_model_name")
        or os.environ.get("LLM_FALLBACK_MODEL", "")).strip()
    cfg["llm_fallback_base_url"] = str(
        cfg.get("llm_fallback_base_url")
        or cfg.get("llm_fallback_url")
        or os.environ.get("LLM_FALLBACK_BASE_URL", "")).strip()
    cfg["llm_fallback_key"] = str(
        cfg.get("llm_fallback_key")
        or cfg.get("llm_fallback_api_key")
        or os.environ.get("LLM_FALLBACK_API_KEY", "")
        or os.environ.get("LLM_FALLBACK_KEY", "")).strip()
    fbbase = (cfg.get("llm_fallback_base_url") or
              "https://openrouter.ai/api/v1").lower()
    if cfg.get("llm_fallback_model") and not cfg.get("llm_fallback_key"):
        if "openrouter" in fbbase:
            cfg["llm_fallback_key"] = os.environ.get(
                "OPENROUTER_API_KEY", "").strip()
        elif "groq" in fbbase:
            cfg["llm_fallback_key"] = os.environ.get(
                "GROQ_API_KEY", "").strip()

    # The ordered fallback list, normalized: a list of dicts, junk dropped
    # with a line instead of a silent no-op. A provider entry may carry its
    # key inline or name a "key_env" - the second keeps the secret in
    # bot.env, where the panel never writes it. llm.py adds any provider
    # whose key is already in the environment, so this list is only for the
    # order and the models you want.
    providers = cfg.get("llm_fallback_providers")
    if isinstance(providers, dict):
        providers = [providers]
    clean = []
    for item in (providers or []):
        if isinstance(item, dict) and (item.get("base_url") or item.get("base")):
            clean.append(item)
        else:
            print(f"[config] llm_fallback_providers entry ignored (needs a "
                  f"base_url): {item!r}", flush=True)
    cfg["llm_fallback_providers"] = clean

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
        # Moderation on request (see moderation.py). Shares the access
        # client, so a refreshed token reaches it without a restart.
        self._moderator = moderation_mod.Moderator(
            self._access.helix, nick=self.nick or "", log=self._log)
        # Private replies are rate-limited per sender: a whisper costs a
        # Helix call and Twitch allows 40 recipients a day.
        self._pm_last = {}
        self._last_probe = 0.0              # last follower-permission probe
        self._follows_start = None           # followers at startup
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
        self._chat_ai_last = 0.0                   # last unprompted attempt
        self._chat_ai_mention_last = 0.0           # last mention reply
        self._chat_ai_mention_by = {}              # {login: last reply t}
        # Memory distills are paced per viewer: {login: (last t, their
        # last line then)}. A distill costs a third of a mention's tokens and
        # used to run after EVERY reply - on a day Groq's budget was
        # gone before the stream began.
        self._distilled = {}
        self._chat_ai_times = []                   # lines posted, last hour
        self._chat_ai_own = []                  # its last lines: anti-echo
        # A standing notice: what a mod asked the bot to tell the room
        # ('@Doc is on the phone, radio silence'). While it stands, a
        # viewer wondering why the stream went quiet gets it repeated -
        # (text, posted-at, {nicks already told}).
        self._chat_ai_notice = None
        # The live-data fast lane skips the mention cooldown, so it paces
        # itself: one reading per viewer per 15s (mods exempt). {login: t}
        self._live_data_last = {}
        self._chat_human_times = []             # rolling flow/busy detector
        # One quiet opener per HUMAN conversation lull. It starts disarmed so
        # startup never becomes a timer talking into a room that said nothing.
        self._chat_ai_quiet_armed = False
        self._persona = self._load_json_state(
            self.cfg.get("persona_state_path", "persona.json"))
        self._subgoal = self._load_json_state(
            self.cfg.get("subgoal_state_path", "subgoal.json"))
        # Chat-side sub notices and mod !subgoal commands both write the
        # sub goal; the lock keeps the two writers honest.
        self._subgoal_lock = threading.Lock()
        self._chat_ai_pending = []              # mentions held by cooldown
        # login -> when we last promised this person an answer, so a held
        # question asked three times gets one acknowledgement, not three.
        self._ack_last = {}
        self._last_performance = None           # (kind, subject) for 'encore'
        # What the bot is in the middle of: the game it is hosting and the
        # counts it is keeping. The one thing every prompt used to lack.
        self._ongoing = ongoing_mod.Ongoing(
            cfg.get("ongoing_state_path") or ongoing_mod.STATE_PATH,
            cfg.get("chat_ai_activity_minutes", 45), log=self._log)
        self._ongoing_timer = None              # the ONE scheduled cadence step
        self._ongoing_busy = None               # ((kind, n), t): step just queued
        self._ongoing_pace = {}                 # {login: t} state answers, 15s
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
        self._opts = {                      # passed through to facts + chat AI
            "spice": cfg.get("spice", "clean"),
            "max_fact_chars": int(cfg.get("max_fact_chars", 200)),
            "fact_source": cfg.get("fact_source", "sources"),
            "answer_questions": cfg.get("answer_questions", True),
            "llm_api_key": cfg.get("llm_api_key", ""),
            "llm_base_url": cfg.get("llm_base_url", ""),
            "llm_model": cfg.get("llm_model", ""),
            # These used to stop at config.json: llm.chat_reply() received
            # _opts, not cfg, so a fully configured fallback was invisible.
            # The tell in a live log was that startup warmed only the primary.
            "llm_fallback_key": (cfg.get("llm_fallback_key")
                                 or cfg.get("llm_fallback_api_key", "")),
            "llm_fallback_base_url": (cfg.get("llm_fallback_base_url")
                                      or cfg.get("llm_fallback_url", "")),
            "llm_fallback_model": (cfg.get("llm_fallback_model")
                                   or cfg.get("llm_fallback_model_name", "")),
            # The ordered provider list travels with the options too - the
            # chat client only ever sees this dict, not cfg.
            "llm_fallback_providers": list(
                cfg.get("llm_fallback_providers") or []),
            "llm_no_think": bool(cfg.get("llm_no_think", False)),
            # Deliberately absent from DEFAULTS (llm.py chooses hosted/local
            # defaults); preserving a user override here does not create one.
            "chat_ai_timeout": cfg.get("chat_ai_timeout"),
            "google_api_key": cfg.get("google_api_key", ""),
            "google_cx": cfg.get("google_cx", ""),
            "serper_api_key": cfg.get("serper_api_key", ""),
            "tavily_api_key": cfg.get("tavily_api_key", "")
            or os.environ.get("TAVILY_API_KEY", ""),
            "weatherapi_key": cfg.get("weatherapi_key", "")
            or os.environ.get("WEATHERAPI_KEY", ""),
            # The day's top headlines are packed three to a message, so
            # the engine needs the message budget, and which country's
            # front page to read (US unless told otherwise).
            "max_message_chars": int(cfg.get("max_message_chars", 450)),
            "news_country": cfg.get("news_country", "US"),
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
        # USERSTATE also carries OUR OWN user id, which is the
        # moderator_id every Helix moderation call has to name. Free here;
        # an API round trip otherwise.
        self._moderator.set_own_id(tags.get("user-id", ""))
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
            # Pay the model's cold start NOW, in the background: a local
            # 8B model takes 10-25s to load into RAM, longer than any
            # chat timeout - the first line of chat must not pay it.
            warmer = threading.Thread(
                target=self._chat_ai_warmup, name="chat-ai-warmup",
                daemon=True
            )
            warmer.start()
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
        elif command == "WHISPER":
            # A private message. Twitch stopped letting bots SEND these
            # over IRC in Feb 2023 and no longer documents receiving them,
            # so this path may never fire - which is exactly why the same
            # commands also work in the channel. When it does fire, the
            # answer goes back out through Helix, never into the room.
            match = re.match(r":([^!]+)!", src)
            wlogin = (match.group(1) if match else "?").lower()
            wnick = tags.get("display-name") or wlogin
            wtext = trailing[1:] if trailing.startswith(":") else trailing
            self._on_private(wnick, wlogin, tags.get("badges", ""), wtext)
        elif command == "USERNOTICE":
            # Twitch sends this for subs, gift subs and - the one that matters
            # here - raids. Nothing handled it before, so raids were invisible.
            msg = tags.get("msg-id")
            if msg == "raid":
                self._on_raid(tags)
            elif msg in _SUB_NOTICE_IDS:
                self._on_sub_notice(tags)
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
        prefix = self.cfg.get("prefix", "!")
        # A message sent to the bot itself rather than to the room. It
        # must never be answered in the channel: '!ban somebody' over a
        # private message would post that name in public chat, and it must
        # not reach the chat AI's ear as room context either.
        if target and not target.startswith("#") \
                and target.lower() != (self.channel or "").lstrip("#").lower():
            self._on_private(nick, login or nick.lower(), badges, message)
            return
        human = (nick or "").lower() != (self.nick or "").lower()
        # Only HUMAN lines drive silence and flow. Counting the bot's own IRC
        # echo makes an autonomous timer influence its own activity detector.
        if human:
            now = time.time()
            self._last_chat = now
            # Every human line ends the previous lull. Only an ordinary safe
            # conversation line below may arm a new one-shot follow-up.
            self._chat_ai_quiet_armed = False
            with self._chat_lock:
                self._chat_human_times.append(now)
                self._chat_human_times = [
                    t for t in self._chat_human_times if now - t <= 600]
        # ...and anyone who says anything counts as present. Presence only
        # ever decides whether an @-tag would reach somebody - never who gets
        # named in a feud - and it is kept in memory, not in a file.
        self._beef_seen.note(nick)
        # The chat AI's ear. Runs before the command check: most of what
        # it should hear is plain chatter, which never starts with the
        # prefix. Cheap by design - a list append and, rarely, a job.
        if human:
            # _EXPLICIT/_TASTELESS are OUTPUT backstops. A viewer is allowed
            # to use rough language when directly asking the bot something,
            # but that line must not linger in the room context for the model
            # to parrot at somebody later. The direct request is still passed
            # separately to _chat_ai_line; only ambient context is filtered.
            unsafe_context = bool(funfacts._EXPLICIT.search(message or "")
                                  or funfacts._TASTELESS.search(message or ""))
            addressed = chatai.mention_kind(message, self._chat_ai_names)
            if not unsafe_context:
                with self._chat_lock:
                    self._chat_buf.append(
                        (nick, " ".join((message or "").split())[:200]))
                    if len(self._chat_buf) > 60:
                        del self._chat_buf[:len(self._chat_buf) - 60]
                # A quiet opener continues ordinary human conversation. A
                # command or direct ask already has its own answer and should
                # not arm a timer five minutes later.
                if not (message or "").startswith(prefix) and not addressed:
                    self._chat_ai_quiet_armed = True
            if self.cfg.get("chat_ai_enabled", False):
                self._memory.note(nick, login, message)
            kind = self._chat_ai_kind(nick, badges, message)
            if kind:
                self._maybe_chime(nick, login, kind, message, badges)
            elif self.cfg.get("chat_ai_enabled", False) \
                    and not (message or "").startswith(prefix) \
                    and self._ongoing.host():
                # The streamer's ordinary lines never chime - but while
                # HE is hosting a game, 'whats the answer' / 'round 3' /
                # 'game over' are the game's controls, name or no name.
                self._ongoing_control(nick, login, badges, message,
                                      addressed=False)
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

        # !ban / !unban / !timeout: moderators and lead mods only, and
        # deliberately reachable while the bot is switched off - pausing
        # the fun-fact bot must not take the room's moderation with it.
        if command in MOD_COMMANDS:
            self._mod_command(nick, login, badges, command, argument)
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

        # The sub goal: anyone can check the progress, moderators maintain
        # the numbers. Same shape as !haul - mutations answered even while
        # paused, the read open to everyone.
        if command in SUBGOAL_COMMANDS:
            if self._subgoal_mutation(nick, badges, argument):
                return
            if self.paused:
                return
            self._say_subgoal(nick)
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

        # !persona <show|list|set|custom|reset> manages the bot's voice.
        # A moderation command, reachable while the bot is switched off.
        if command in PERSONALITY_COMMANDS:
            self._persona_command(nick, badges, argument)
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

    # ---- private messages and moderation ------------------------------
    def _on_private(self, nick: str, login: str, badges: str,
                    message: str) -> None:
        """A message sent to the bot itself: moderation, or a pointer.

        Nothing here is ever answered in the channel. Twitch stopped
        letting bots send whispers over IRC in Feb 2023 and no longer
        documents delivering them, so a private ask may simply never
        arrive - the same command typed in the room is the reliable path,
        and says so when a private one is refused.
        """
        message = " ".join((message or "").split())
        self._log(f"private message from {nick or login}: {message[:100]!r}")
        prefix = self.cfg.get("prefix", "!")
        if not message.startswith(prefix):
            return
        body = message[len(prefix):].strip()
        command, _, argument = body.partition(" ")
        command = command.lower()
        if command in MOD_COMMANDS:
            self._mod_command(nick, login, badges, command, argument,
                              private=True)
            return
        self._reply_private(
            login, f"{prefix}{command} is a channel command - say it in "
            f"{self.channel}. For mods, {prefix}ban, {prefix}timeout and "
            f"{prefix}unban work here too.")

    def _reply_private(self, login: str, text: str) -> bool:
        """Answer a private message privately, through Helix.

        Rate-limited per sender: a whisper is an API call, Twitch allows
        40 recipients a day, and a bot that answers every message it is
        sent is a spam vector.
        """
        login = (login or "").strip().lower()
        now = time.time()
        if now - self._pm_last.get(login, 0.0) < 20.0:
            self._log(f"private reply to {login} skipped (too soon)")
            return False
        self._pm_last[login] = now
        return self._moderator.whisper(login, text)

    def _mod_authorised(self, login: str, badges: str,
                        private: bool = False) -> bool:
        """May THIS sender ask the bot to moderate the room?"""
        if not self.cfg.get("mod_commands_enabled", True):
            return False
        # In the channel the badges are the authority, and a lead
        # moderator counts: Twitch replaces the moderator badge with
        # lead_moderator/1, so a tool that looks only for "moderator"
        # silently ignores the role.
        if access.tier_from_badges(badges or "") in ("broadcaster",
                                                     "moderator"):
            return True
        if not private:
            return False
        # A whisper carries no channel badges - moderator is a channel
        # role - and a bot account cannot read another channel's
        # moderator list: Helix wants broadcaster_id to be the token's own
        # user id. So for a private ask the channel's own list decides.
        login = (login or "").strip().lower()
        return bool(login) and login in self._mod_logins()

    def _mod_logins(self) -> set:
        """Who may ask privately: mod_logins, the broadcaster, the bot."""
        names = {str(n).strip().lstrip("#@").lower()
                 for n in (self.cfg.get("mod_logins") or []) if str(n).strip()}
        names.add((self.channel or "").lstrip("#").lower())
        names.add((self.nick or "").lower())
        names.discard("")
        return names

    def _mod_blocked(self, target: str) -> str:
        """Who the bot will not touch, even on a moderator's word."""
        if not moderation_mod.LOGIN_RE.match(target or ""):
            return (f"{target!r} is not a Twitch username (letters, numbers "
                    f"and underscore only)")
        if target == (self.channel or "").lstrip("#").lower():
            return f"{target} is the broadcaster"
        if target == (self.nick or "").lower():
            return "I am not banning myself"
        if target in self._mod_logins():
            return (f"{target} is on mod_logins, and Twitch does not let me "
                    f"ban a moderator")
        return ""

    def _mod_reply(self, nick: str, login: str, private: bool,
                   text: str) -> None:
        """Answer where the ask came from - a whisper stays a whisper."""
        if private:
            if not self._reply_private(login, text):
                self._log(f"(private answer to {login} not delivered: {text})")
        else:
            self._say(f"@{nick} {text}")

    def _mod_command(self, nick: str, login: str, badges: str, command: str,
                     argument: str, private: bool = False) -> None:
        """!ban <name> [reason] | !timeout <name> [10m] [reason] | !unban."""
        pre = self.cfg.get("prefix", "!")
        if not self.cfg.get("mod_commands_enabled", True):
            self._mod_reply(nick, login, private,
                            f"moderation commands are switched off "
                            f"(mod_commands_enabled).")
            return
        if not self._mod_authorised(login, badges, private):
            self._log(f"{pre}{command} refused - {nick or login} is not a "
                      f"moderator of {self.channel}")
            if private:
                self._reply_private(
                    login, f"only moderators of {self.channel} can ask me to "
                    f"{command}.")
            return
        words = " ".join((argument or "").split()).split(" ")
        target = access.clean_login(words[0]).lower() if words[0] else ""
        if not target:
            usage = " [10m] [reason]" if command == "timeout" else " [reason]"
            self._mod_reply(nick, login, private,
                            f"usage: {pre}{command} <twitch name>{usage}")
            return
        blocked = self._mod_blocked(target)
        if blocked:
            self._mod_reply(nick, login, private, blocked)
            return
        duration, reason = None, " ".join(words[1:])
        if command == "timeout":
            try:
                default = int(self.cfg.get("mod_timeout_seconds")
                              or moderation_mod.DEFAULT_TIMEOUT)
            except (TypeError, ValueError):
                default = moderation_mod.DEFAULT_TIMEOUT
            duration = moderation_mod.parse_duration(
                words[1] if len(words) > 1 else "", default)
            if duration is None:
                self._mod_reply(nick, login, private,
                                f"{words[1]!r} is not a length I know - try "
                                f"30s, 10m, 2h or 1d")
                return
            reason = " ".join(words[2:])
        if not self._moderator.usable:
            self._mod_reply(nick, login, private,
                            "I cannot moderate yet: "
                            + (self._moderator.problem()
                               or "no Twitch login"))
            return
        self._log(f"{pre}{command} {target} asked by {nick or login}"
                  f"{' (private)' if private else ''}"
                  + (f" for {duration}s" if duration else "")
                  + (f" reason={reason[:60]!r}" if reason.strip() else ""))

        def _do():
            # Helix can take a few seconds; the IRC read loop must not
            # wait for it, or chat stalls behind a moderation call.
            try:
                if command in ("unban", "untimeout"):
                    res = self._moderator.unban(target)
                else:
                    res = self._moderator.ban(target, reason.strip(),
                                              duration=duration)
                self._mod_reply(nick, login, private,
                                res.note if res.ok
                                else f"could not {command}: {res.note}")
            except Exception as exc:
                self._log(f"[mod] {command} {target} failed: {exc!r}")
                self._mod_reply(nick, login, private,
                                f"could not {command} {target}: {exc!r}")

        threading.Thread(target=_do, name="mod-action", daemon=True).start()

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
        self._drip(body, delay, started=started)

    def _drip(self, lines: list, delay: float, head: str = "",
              started: float = None) -> None:
        """Post `lines` one message at a time, `delay` seconds apart.

        The shared pacing for anything the bot delivers in parts: a beef
        story's acts, a song's lines, a poem's verses. Each line still
        passes through the say queue, so Twitch's rate limit is respected
        and the reader thread never stops. `head` is prefixed to the
        FIRST line only (the @-tag of whoever asked); the rest stand
        alone. 0 sends everything at once.

        With no `started`, the first line goes out now and the rest
        follow at delay, 2*delay, ... after it. A caller that already
        posted an opening message (a beef headline) passes the moment it
        did as `started`, and every line here waits its turn from THAT
        moment - time spent waiting on a model never stretches the gaps.
        """
        lines = [ln for ln in lines if ln]
        if not lines:
            return
        lines = [head + lines[0]] + lines[1:]
        if delay <= 0.0:
            for line in lines:
                self._queue_say(line)
            return
        if started is None:
            started = time.time()
            self._queue_say(lines[0])
            lines = lines[1:]
        for i, line in enumerate(lines, 1):
            wait = max(0.05, i * delay - (time.time() - started))
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

    def _on_sub_notice(self, tags: dict) -> None:
        """A sub the whole channel just saw. Twitch will not let a bot
        read the live sub count - that number belongs to the
        broadcaster's own token alone - but every sub, resub and gift
        announces itself in chat, and counting what the room actually
        SEES keeps the goal moving without any API at all. The count
        itself is quick enough for the read loop; only the goal-crossing
        announcement is queued, because saying paces at over a second a
        line and the read loop must not wait for that."""
        if not self.cfg.get("subgoal_auto_count", True):
            return
        with self._subgoal_lock:
            g = self._subgoal or {}
            if not g.get("goal"):
                return          # nothing to count toward yet
            was = int(g.get("current", 0))
            g["current"] = was + 1
            self._save_json_state(self.cfg.get("subgoal_state_path",
                                               "subgoal.json"),
                                  self._subgoal)
            goal = int(g["goal"])
            cur = g["current"]
            crossed = was < goal <= cur
            line = self._subgoal_line() if crossed else ""
        who = self._unescape_tag(tags.get("display-name")
                                 or tags.get("login") or "someone")
        self._log(f"sub seen in chat ({who}): {cur}/{goal}")
        if crossed:
            self._jobs.put(("", "", "", "say", line))

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

    def _chat_ai_warmup(self) -> None:
        """Load the chat model at startup (see llm.warm_up). Own thread,
        because a cold local model can take half a minute - time the
        keeper and the read loop must not spend."""
        import llm as llm_mod
        try:
            # A config still naming a slug the provider shut down gets a
            # plain line here, with the replacement - not a 404 an hour
            # into the stream.
            llm_mod.check_models(self._opts)
            llm_mod.warm_up(self._opts)
        except Exception as exc:
            self._log(f"chat ai warm-up error: {exc!r}")

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
            if helix.self_test():
                # 'How many follows this stream?' needs the baseline; the
                # startup probe already fetches it, so keep it this time.
                if self._follows_start is None:
                    try:
                        self._follows_start = helix.follow_total()
                    except Exception:
                        pass
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

                # A step of the game the bot is hosting (ask round N,
                # reveal the answer, close it out). Composed here, off
                # the read loop; paced by the game, not by chat cooldowns.
                if command == "step":
                    self._do_step(nick, argument)
                    continue
                if command == "tally":
                    self._do_tally(nick, argument)
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
        m = chatai.mention_kind(text, self._chat_ai_names)
        if m:
            # Direct address wins over the ambient-input filter. Those regexes
            # are output rails, but this check used them as input censorship:
            # "Docbot ... Pussy or it's ok?" was silently ignored, then the
            # model answered that stale line when the viewer later asked
            # "you ok?". The reply still passes clean_line(), so the bot can
            # answer rough wording without repeating it into chat.
            return m
        if funfacts._EXPLICIT.search(text) \
                or funfacts._TASTELESS.search(text):
            return None
        if "broadcaster/1" in (badges or ""):
            # The streamer has the floor: the bot never butts into his
            # lines with a chime-in. A direct mention returned above.
            return None
        # A chime-in needs actual words to react to: an emoji wall has
        # characters but no conversation in it.
        return chatai.CHIME if chatai.chime_worthy(text) else None

    def _chat_activity(self, now: float = None) -> tuple[int, bool]:
        """(recent human lines, flowing-now) for autonomous speech gates."""
        now = time.time() if now is None else now
        with self._chat_lock:
            times = list(self._chat_human_times)
        try:
            context_seconds = float(self.cfg.get(
                "chat_ai_context_seconds", 300))
            busy_seconds = float(self.cfg.get("chat_ai_busy_seconds", 30))
            busy_messages = int(self.cfg.get("chat_ai_busy_messages", 4))
        except (TypeError, ValueError):
            context_seconds, busy_seconds, busy_messages = 300.0, 30.0, 4
        recent = chatai.recent_chat_count(times, now, context_seconds)
        busy = chatai.room_is_busy(times, now, busy_seconds, busy_messages)
        return recent, busy

    def _maybe_chime(self, nick: str, login: str, kind: str,
                     message: str, badges: str = "") -> None:
        """Gate the moment, then hand the composing to a worker.

        The LLM call takes seconds and must never block the read loop; a
        job does the waiting. Autonomous attempts mark their clock before
        enqueue, so a slow or declining model cannot be billed in a loop.
        """
        # A note ('docbot, take a mental note X') is a command, not
        # chatter: it is stored and acknowledged whatever the roll and
        # the cooldowns say - the streamer logs moments mid-conversation
        # ('2:49am, Sullivan MO truck stop') and quizzes the bot later.
        # Like everything the memory keeps, notes need chat_ai_enabled.
        if kind == chatai.MENTION \
                and self.cfg.get("chat_ai_enabled", False):
            note = chatai.note_request(message)
            if note:
                # A note ask is CONSUMED here whatever the answer is:
                # passed on, the model would happily pretend it noted
                # something it cannot store ('sure, noted!' as a lie).
                if access.tier_from_badges(badges) not in ("broadcaster",
                                                           "moderator"):
                    self._log(f"note from {nick} ignored - not a mod")
                    return
                subject, payload = note
                who = subject or nick
                if not self._memory.ok:
                    self._log("note not kept - memory db unavailable")
                    ack = "my memory file isn't available right now"
                else:
                    kept = self._memory.remember(who, [payload])
                    self._log(f"noted for {who} (kept {kept}): "
                              f"{payload[:60]!r}")
                    ack = "Noted." if kept else "Already had that one."
                self._jobs.put(("", "", "", "say", f"@{nick} {ack}"))
                return
            # 'keep count of dirty lepages' / 'spotted one' / 'lets do a
            # 3 round cycling quiz' / 'round 2' / 'whats the answer':
            # the bot's working memory. Counts are answered with no
            # model at all; a game step goes to the worker as a job the
            # model must PERFORM, framed as such - never as a chat line
            # that happens to mention a round.
            if self._ongoing_control(nick, login, badges, message):
                return
            # 'docbot tell everyone that Doc is on the phone' - the
            # persona still answers in character below; the plain
            # notice is kept for whoever asks 'why is it so quiet?'.
            announced = self._take_notice(nick, badges, message)
        else:
            announced = False
            # The host's own line while their game runs ('whats the
            # answer', 'round 3') is aimed at the game even without the
            # bot's name on it.
            if self.cfg.get("chat_ai_enabled", False) and self._ongoing_control(
                    nick, login, badges, message, addressed=False):
                return
        now = time.time()
        # A standing notice answers the room's confusion on the spot.
        # Live-fire: a mod had the bot announce 'Doc is on the phone,
        # radio silence'; two lines later a viewer said 'Your mic is
        # muted' / 'I assume because your codriver is sleeping' and the
        # bot said nothing - those lines were not addressed to it, so
        # they were ambient chimes: a 10% roll, five lines of recent
        # chat, and a ten-minute cooldown the bot's own announcement
        # had just started. The one thing the bot knew for certain, it
        # kept to itself. A viewer who is confused about something the
        # bot was ASKED to tell the room is not ambient chatter: they
        # get the notice, once each, no roll and no cooldown.
        # A direct 'docbot, is his mic muted?' gets it the same way -
        # the one certain answer, not a persona guess held by a cooldown.
        if not announced and self._say_notice(nick, message):
            return
        # THE FAST LANE. 'Docbot whats the weather currently in Brewster,
        # NY' is an API reading, no model in the loop - and live-fire it
        # got NOTHING. Two rails built for persona chatter were in front
        # of it: the 60s mention cooldown (the bot had answered a
        # different 'docbot ...' moments earlier, so the question was
        # 'held'), and the single worker queue, where a job that waits
        # behind someone else's slow model call finds the cooldown
        # re-armed when its turn comes and is dropped without a log
        # line. Neither rail exists for data: a weather or sunrise
        # question is answered now, on its own thread, and it does not
        # touch the mention clock - it is not chatter.
        if kind == chatai.MENTION and self.cfg.get(
                "chat_ai_enabled", False) and not self.paused \
                and chatai.live_data_question(message, self._chat_ai_names):
            # No mention cooldown here, so the lane paces itself: a
            # viewer gets one reading per 15s (a real ask is instant, a
            # loop of 'docbot weather in X?' is not six API calls). The
            # broadcaster and mods are exempt - they run the show.
            who = (login or nick or "").lower()
            if access.tier_from_badges(badges) not in ("broadcaster",
                                                       "moderator"):
                last = self._live_data_last.get(who, 0.0)
                if now - last < 15.0:
                    self._log(f"live data ask from {nick} paced - one "
                              f"reading per 15s")
                    return
                self._live_data_last[who] = now
                if len(self._live_data_last) > 200:
                    self._live_data_last = {
                        k: v for k, v in self._live_data_last.items()
                        if now - v < 60.0}
            threading.Thread(target=self._answer_live_data,
                             args=(nick, message), name="live-data",
                             daemon=True).start()
            return
        recent_chat, busy = self._chat_activity(now)
        # Flowing human chat already has a conversation. Autonomous Doc listens;
        # mentions still pass straight through this gate.
        if kind == chatai.CHIME and busy:
            return
        # should_speak's mention gate is fed the PER-VIEWER wait: the
        # last reply to this person and the channel pace, never another
        # viewer's clock.
        mention_wait = self._mention_wait(login or nick, now)
        if not chatai.should_speak(
                enabled=bool(self.cfg.get("chat_ai_enabled", False)),
                paused=self.paused,
                ambient_off=self._cb_ambient_off,
                kind=kind, roll=random.random(),
                chance=float(self.cfg.get("chat_ai_chance", 0.10)),
                now=now, last=self._chat_ai_last,
                mention_last=now - 1.0 if mention_wait > 0 else 0.0,
                mention_cd=1.5 if mention_wait > 0 else 0.0,
                chime_cd=float(self.cfg.get("chat_ai_cooldown", 600)),
                times=self._chat_ai_times,
                max_hour=int(self.cfg.get("chat_ai_max_hour", 6)),
                buffer_len=recent_chat,
                min_chat=int(self.cfg.get("chat_ai_min_chat", 5))):
            if kind == chatai.MENTION and self.cfg.get(
                    "chat_ai_enabled", False) and not self.paused:
                # A direct question never dangles. Held by the cooldown
                # (or the cap), it is answered to the RIGHT person the
                # moment the rail clears - the keeper tick picks it up.
                # Left dangling, a chime answers it to whoever spoke
                # next: live-fire, 'pick a number 1-100' was answered to
                # someone else entirely. A QUEUE, not a slot: several
                # people can ask inside one window, and each gets their
                # answer - oldest first. One entry per person (their
                # latest question replaces their earlier one, so a
                # repeat is not two answers), eight people deep; a
                # queue that overflows says who it dropped.
                who = (login or nick or "").lower()
                self._chat_ai_pending = [
                    p for p in self._chat_ai_pending
                    if (p[3] if len(p) > 3 else (p[0] or "").lower()) != who]
                # The entry carries its own expiry: at least the usual two
                # minutes, and always long enough to cover a wait we are
                # about to quote back to the person. A promise the queue
                # then throws away is worse than no promise at all.
                self._chat_ai_pending.append(
                    (nick, message, time.time(), who,
                     time.time() + max(120.0, mention_wait + 45.0)))
                if len(self._chat_ai_pending) > 8:
                    dropped = self._chat_ai_pending.pop(0)
                    self._log(f"held-question queue full - dropped "
                              f"{dropped[0]}'s {dropped[1][:40]!r}")
                    self._queue_say(
                        f"@{dropped[0]} I've lost the thread of your "
                        f"question - ask me again in a minute?")
                self._log(f"mention from {nick} held {mention_wait:.0f}s - "
                          f"{'their own cooldown' if mention_wait > 0 else 'the hourly cap or a busy worker'}"
                          f" (a rail, not a bug)")
                self._ack_held(nick, who, mention_wait)
            return
        if kind == chatai.CHIME:
            # Mark an autonomous ATTEMPT at enqueue, not after the model call.
            # Otherwise several fast messages all pass the same old timestamp,
            # and rejected chimes never start their advertised cooldown.
            self._chat_ai_last = now
        self._jobs.put((nick, login or (nick or "").lower(), "",
                        "chime", message))

    def _ack_held(self, nick: str, who: str, wait: float) -> None:
        """Tell a held asker the bot heard them, and roughly when to expect it.

        The number quoted is the rail's own remaining time, rounded up -
        the same figure the log prints. The keeper answers on its next
        tick, so the reply can land a few seconds after that; "about" is
        doing honest work there, and a padded number ("a minute" for a
        44-second wait) reads as the bot making things up.
        """
        if not self.cfg.get("chat_ai_ack_held", True):
            return
        try:
            floor = float(self.cfg.get("chat_ai_ack_min_seconds", 5))
        except (TypeError, ValueError):
            floor = 5.0
        if wait < floor:
            return                       # seconds away; a message is noise
        now = time.time()
        if now - self._ack_last.get(who or "", 0.0) < 45.0:
            # They were told once already. Answering the third copy of the
            # same question with a third "on it" is the spam it is meant
            # to prevent.
            return
        self._ack_last[who or ""] = now
        if len(self._ack_last) > 200:
            self._ack_last = {k: v for k, v in self._ack_last.items()
                              if now - v < 120.0}
        line = (f"@{nick} on it - give me about "
                f"{self._human_wait(wait)} to look that up.")
        # Posted on its own thread, NOT through the job queue: the queue is
        # exactly what may be stuck behind a slow model call, and an "on it"
        # that arrives after the answer is worth nothing. _say paces itself.
        threading.Thread(target=self._say, args=(line,), name="ack",
                         daemon=True).start()

    @staticmethod
    def _human_wait(seconds: float) -> str:
        """59 -> '60 seconds', 74 -> 'a minute', 130 -> '2 minutes'.

        Rounded UP to the next 5 seconds (and the next minute past that):
        the wait quoted is a promise, and a promise that runs short is
        what makes somebody ask again.
        """
        try:
            secs = int(seconds)
        except (TypeError, ValueError):
            return "a moment"
        secs = -((-secs) // 5) * 5       # round up to the next 5
        if secs < 20:
            return "a few seconds"
        if secs < 60:
            return f"{secs} seconds"
        if secs < 90:
            return "a minute"
        return f"{-((-secs) // 60)} minutes"

    def _answer_live_data(self, nick: str, message: str) -> None:
        """Answer a weather / sunrise question from the live feed, off
        the read loop. The engine owns the whole path: a reading, or an
        honest 'couldn't fetch' line - never silence and never a model.
        Failures are logged by the engine; a crash here is logged too,
        so a missing reply always has a line in bot.log."""
        try:
            question = chatai.strip_address(message, self._chat_ai_names)
            result = get_funfact(question, self._opts)
            if not result or not result.get("fact"):
                self._log(f"live data: engine had nothing for {question!r}")
                self._say(f"@{nick} I couldn't get that reading right now - "
                          f"try me again in a minute.")
                return
            self._reply(nick, question, result)
            self._log(f"live data answered for {nick}: {question[:60]!r}")
        except Exception as exc:
            self._log(f"live data failed for {nick}: {exc!r}")

    # ---- the working memory: the game being hosted, the counts kept ----
    def _ongoing_control(self, nick: str, login: str, badges: str,
                         message: str, addressed: bool = True) -> bool:
        """'keep count of dirty lepages' / 'spotted one' / 'lets do a 3
        round cycling quiz' / 'round 2' / 'whats the answer' - the bot's
        working memory, recognised here on the read loop. True when the
        line was taken (queued as a count job or a game step, answered
        from state, or refused) - it is then not persona chatter.

        Counts are pure arithmetic: applied and answered by the worker
        with no model. A game step is queued for the worker as a job
        the model must PERFORM, framed as such. Live-fire, both were
        handed to the persona as ordinary chat: it improvised a 'round
        2' that asked for the temperature in Rolla, MO, and an hour
        after 'keep count of Dirty Lepages' it had no idea what one was.

        `addressed` False is the host's own un-addressed line while
        their game runs ('whats the answer', 'round 3', 'game over' said
        to the room): the host is talking to the game, so it is taken -
        steps only, never a new game or a count."""
        if not self.cfg.get("chat_ai_enabled", False) or self.paused:
            return False
        who = (login or nick or "").lower()
        host = (self._ongoing.host() or "").lower()
        is_host = bool(host) and who == host
        if not addressed and not is_host:
            return False
        ctl = ongoing_mod.control(message, self._ongoing.snapshot(),
                                  self._chat_ai_names, strict=not addressed)
        if ctl is None:
            return False
        kind = ctl.kind
        if not addressed and kind not in ("round", "reveal", "end"):
            return False
        is_mod = access.tier_from_badges(badges) in ("broadcaster",
                                                     "moderator")
        now = time.time()

        def say(line):
            self._jobs.put(("", "", "", "say", self._fit(f"@{nick} ", line)))

        # Setting things up is for the mods and the streamer: a viewer
        # cannot make the bot host a game or open a count. Said plainly,
        # once - never handed to the model, which would pretend to.
        if kind in ongoing_mod.MOD_KINDS and not is_mod:
            self._log(f"{kind} from {nick} refused - not a mod: "
                      f"{message[:60]!r}")
            if self._ongoing_paced(who, now, is_mod):
                say(ongoing_mod.NOT_A_MOD_LINE)
            return True
        if kind == "status":
            # Anyone may ask where things stand: exact, from state, no
            # model - one line per person per 15s so it is not a toy.
            if self._ongoing_paced(who, now, is_mod):
                say(self._ongoing.status_line(ctl, now))
                self._log(f"game status answered for {nick}")
            else:
                self._log(f"game status from {nick} paced")
            return True
        if kind in ongoing_mod.COUNT_KINDS:
            owner = (self._ongoing.tally_owner(ctl.key) or "").lower()
            allowed = (is_mod or who == owner or (nick or "").lower() == owner
                       or bool(self.cfg.get("chat_ai_count_anyone", False)))
            if not allowed:
                # The count is the mods' to keep (chat_ai_count_anyone
                # opens it up). The viewer is told where it stands, not
                # ignored - and the number does not move.
                self._log(f"{kind} from {nick} not applied - not a mod or "
                          f"the count's owner")
                if self._ongoing_paced(who, now, is_mod):
                    say(f"{self._ongoing.tally_state(ctl.key)} - the mods "
                        f"keep that count.")
                return True
        if kind.startswith("tally_"):
            if kind == "tally_query" and not self._ongoing_paced(
                    who, now, is_mod):
                self._log(f"count query from {nick} paced")
                return True
            self._jobs.put((nick, who, badges, "tally", ctl))
            return True
        # A game step. The host and the mods call the rounds; a viewer
        # saying 'next' or 'whats the answer' is told to hang tight.
        if kind in ongoing_mod.HOST_KINDS and not is_mod and not is_host:
            self._log(f"{kind} from {nick} refused - not the host or a mod")
            if self._ongoing_paced(who, now, is_mod):
                say(ongoing_mod.NOT_THE_HOST_LINE)
            return True
        if kind == "start":
            self._cancel_step_timer()
        # The same step asked twice inside a few seconds (a double-typed
        # 'round 2') is one step, not two questions.
        busy = self._ongoing_busy
        if busy and busy[0] == (kind, ctl.n) and now - busy[1] < 20:
            self._log(f"{kind} {ctl.n or ''} from {nick} already queued")
            return True
        self._ongoing_busy = ((kind, ctl.n), now)
        self._jobs.put((nick, who, badges, "step", ctl))
        self._log(f"game step {kind}{' ' + str(ctl.n) if ctl.n else ''} "
                  f"queued for {nick}: {message[:60]!r}")
        return True

    def _ongoing_paced(self, who: str, now: float, is_mod: bool) -> bool:
        """One state answer per viewer per 15s (mods unpaced): these
        cost no model call, but they are still a line in chat."""
        if is_mod:
            return True
        last = self._ongoing_pace.get(who, 0.0)
        if now - last < 15.0:
            return False
        self._ongoing_pace[who] = now
        if len(self._ongoing_pace) > 200:
            self._ongoing_pace = {k: v for k, v in self._ongoing_pace.items()
                                  if now - v < 60.0}
        return True

    def _do_tally(self, nick: str, ctl) -> None:
        """A count changed or was asked for: arithmetic and one line,
        on the worker so the state file is never written on the read
        loop. No model anywhere near it."""
        if self.paused:
            return
        line = self._ongoing.apply_tally(ctl, nick, time.time())
        self._say(self._fit(f"@{nick} ", line))
        self._log(f"count {ctl.kind[6:]} by {nick}: {line[:70]!r}")

    def _lookup_blocked(self, nick: str, text: str) -> bool:
        """While a round of the bot's own quiz is open, the fact engine
        is not a back door to the answer: 'docbot what holds the pedals
        on a bike?' thirty seconds into round 1 is told to wait for the
        host. Anything that is not a lookup (a weather reading took the
        fast lane already; smalltalk; the game's own controls) passes."""
        n = self._ongoing.open_round()
        if not n:
            return False
        if not (chatai.factual_question(text, self._chat_ai_names)
                or chatai.knowledge_question(text, self._chat_ai_names)):
            return False
        self._say(self._fit(f"@{nick} ", f"no lookups while round {n} is "
                            f"open - the host calls time, then ask me."))
        self._log(f"lookup from {nick} held - round {n} is open: "
                  f"{text[:60]!r}")
        return True

    def _cancel_step_timer(self) -> None:
        t = getattr(self, "_ongoing_timer", None)
        if t is not None:
            try:
                t.cancel()
            except Exception:
                pass
        self._ongoing_timer = None

    def _do_step(self, nick: str, ctl) -> None:
        """One step of the game the bot is hosting, on the worker: ask
        round N, reveal the answer, read the standings, close it out.
        The model writes the question or the answer - it is the only
        part of the bot that can - but the prompt leads with the job,
        the line is checked against the job (a question must BE a
        question and not one already asked), and a model that cannot
        produce the step says so honestly instead of posting a stand-in.
        A cadence ('30 secs to answer, then round 3') schedules ONE next
        step from here; that step re-checks the game when it fires."""
        import llm as llm_mod
        self._ongoing_busy = None
        if self.paused:
            self._log(f"game step {ctl.kind} skipped - the bot is paused")
            return
        now = time.time()
        auto = bool(ctl.auto)
        step = self._ongoing.begin(ctl, nick, now)
        if step is None:
            if not auto:
                self._say(self._fit(f"@{nick} ",
                                    ongoing_mod.NOTHING_RUNNING_LINE))
            else:
                self._log(f"scheduled {ctl.kind} stood down - the game moved on")
            return
        if step.spoken:
            head = f"@{nick} " if (nick and step.kind != "end") else ""
            self._say(self._fit(head, step.cue))
            self._ongoing.record(step, step.cue, now)
            if step.kind == "end":
                self._cancel_step_timer()
            self._log(f"game {step.kind}: {step.cue[:70]!r}")
            return
        if not llm_mod.any_configured(self._opts):
            self._say(self._fit(f"@{nick} ", "I need a chat model to write "
                                "questions and I haven't got one right now."))
            self._ongoing.failed(step, now)
            return
        snapshot = self._chat_ai_snapshot()
        line = None
        for attempt in range(2):
            task = step.cue
            if attempt:
                task += (" Your previous attempt was not usable. Output the "
                         + ("question itself - one line ending in '?', "
                            "different from anything you asked before."
                            if step.kind == "ask" else
                            "line itself - plain text, in character."))
            line = self._chat_ai_line(snapshot, nick or "chat", "", task=task)
            if line == chatai.DIRECT_FAILURE_LINE:
                line = None
            if line and step.kind == "ask":
                if not ongoing_mod.looks_like_question(line):
                    self._log(f"round {step.round} line was not a question - "
                              f"{'retry' if not attempt else 'giving up'}: "
                              f"{line[:80]!r}")
                    line = None
                elif self._ongoing.repeats(line):
                    self._log(f"round {step.round} question repeated an "
                              f"earlier one - "
                              f"{'retry' if not attempt else 'giving up'}")
                    line = None
            if line:
                break
        if not line:
            self._ongoing.failed(step, now)
            self._say(self._fit(f"@{nick} " if nick else "",
                                ongoing_mod.step_failure_line(step)))
            self._log(f"game step {step.kind} {step.round} failed - the "
                      f"model gave nothing usable")
            return
        now = time.time()
        self._chat_ai_own = (self._chat_ai_own + [line])[-3:]
        a = self._ongoing.current(now) or {}
        of = f" of {a['rounds']}" if a.get("rounds") else ""
        if step.kind == "ask":
            self._say(self._fit(f"Round {step.round}{of}: ", line))
        elif step.kind == "reveal":
            self._say(self._fit(f"Round {step.round} answer: ", line))
        else:
            self._say(self._fit(f"@{nick} " if nick else "", line))
        self._ongoing.record(step, line, now)
        self._log(f"game step {step.kind} {step.round} posted")
        nxt = self._ongoing.next_auto(step)
        if nxt:
            secs, ctl2 = nxt
            self._cancel_step_timer()
            host = self._ongoing.host() or nick
            t = threading.Timer(
                secs, self._jobs.put,
                args=((host, (host or "").lower(), "", "step", ctl2),))
            t.daemon = True
            t.start()
            self._ongoing_timer = t
            self._log(f"next game step ({ctl2.kind} "
                      f"{ctl2.n or step.round}) in {secs:g}s")

    def _mention_wait(self, nick: str, now: float = None) -> float:
        """Seconds until THIS viewer may get a persona reply: 0 = now.

        Two clocks. The viewer's own (chat_ai_mention_cooldown, 60s):
        one reply per person per minute, so nobody can wind the bot up
        like a toy. And the channel pace (chat_ai_mention_pace, 8s): a
        floor between any two persona replies, so four people asking at
        once is four answers a few seconds apart - not one answer and
        three questions held for a minute each, which is what one shared
        60s clock did live (6 of 10 direct questions lost)."""
        now = time.time() if now is None else now
        try:
            own_cd = float(self.cfg.get("chat_ai_mention_cooldown", 60))
        except (TypeError, ValueError):
            own_cd = 60.0
        try:
            pace = float(self.cfg.get("chat_ai_mention_pace", 8))
        except (TypeError, ValueError):
            pace = 8.0
        who = (nick or "").lower()
        own = own_cd - (now - self._chat_ai_mention_by.get(who, 0.0))
        shared = pace - (now - self._chat_ai_mention_last)
        return max(0.0, own, shared)

    def _mark_mention_reply(self, nick: str, now: float = None) -> None:
        """A persona reply went to this viewer: start both clocks."""
        now = time.time() if now is None else now
        self._chat_ai_mention_last = now
        self._chat_ai_mention_by[(nick or "").lower()] = now
        if len(self._chat_ai_mention_by) > 500:
            self._chat_ai_mention_by = {
                k: v for k, v in self._chat_ai_mention_by.items()
                if now - v < 3600}

    def _notice_minutes(self) -> float:
        try:
            return max(0.0, float(self.cfg.get("chat_ai_notice_minutes", 20)))
        except (TypeError, ValueError):
            return 20.0

    def _take_notice(self, nick: str, badges: str, text: str) -> bool:
        """Keep what a mod asked the bot to tell the room as the standing
        notice. The reply itself still comes from the persona (it reads
        the room and says it in character); this is the plain fact the
        bot repeats to anyone confused later. Mods and the broadcaster
        only - a viewer cannot make the bot announce things."""
        if not self.cfg.get("chat_ai_enabled", False) \
                or self._notice_minutes() <= 0:
            return False
        if access.tier_from_badges(badges) not in ("broadcaster",
                                                   "moderator"):
            return False
        notice = chatai.announce_request(text, self._chat_ai_names)
        if not notice:
            return False
        if chatai.notice_clears(notice):
            # 'tell everyone doc is back' ends the quiet: drop the
            # notice rather than answer 'hello?' with it for 20 min.
            if self._chat_ai_notice:
                self._log(f"standing notice cleared by {nick}: "
                          f"{notice[:60]!r}")
            self._chat_ai_notice = None
            return True
        # Said in the bot's own voice: 'tell everyone that I am on the
        # phone' is the MOD on the phone, not the bot.
        notice = re.sub(r"\b[Ii]\s+am\b", f"{nick} is", notice)
        notice = re.sub(r"\b[Ii]'m\b", f"{nick} is", notice)
        notice = re.sub(r"\b(?:[Ii]|me)\b", nick, notice)
        notice = re.sub(r"\bmy\b", f"{nick}'s", notice, flags=re.IGNORECASE)
        self._chat_ai_notice = (notice, time.time(), set())
        self._log(f"standing notice from {nick} for the next "
                  f"{self._notice_minutes():g} min: {notice[:80]!r}")
        return True

    def _standing_notice(self) -> str | None:
        """The notice text while it stands, else None (and expired ones
        are dropped)."""
        held = self._chat_ai_notice
        if not held:
            return None
        if time.time() - held[1] > self._notice_minutes() * 60:
            self._chat_ai_notice = None
            return None
        return held[0]

    def _say_notice(self, nick: str, text: str) -> bool:
        """Repeat the standing notice to a viewer who sounds confused
        about the quiet stream. True when it posted. Each viewer is told
        once per notice; the notice expires after chat_ai_notice_minutes
        or when a mod hands the bot a new one."""
        if self.paused or self._standing_notice() is None:
            return False
        notice, _since, told = self._chat_ai_notice
        asked = chatai.strip_address(text, self._chat_ai_names)
        if (nick or "").lower() in told or not chatai.stream_confusion(asked):
            return False
        told.add((nick or "").lower())
        self._jobs.put(("", "", "", "say",
                        self._fit(f"@{nick} heads up: ", notice)))
        self._log(f"standing notice repeated to {nick} for {text[:50]!r}")
        return True

    def _chat_ai_snapshot(self) -> list:
        with self._chat_lock:
            return list(self._chat_buf)

    def _chat_ai_line(self, lines: list, nick: str, text: str,
                      quiet: bool = False, vary: bool = False,
                      overheard: bool = False, task: str = None):
        """Compose one cleaned line, or None. Shared by chime, !ask, the
        quiet-room opener and the game steps. `vary` re-asks after a
        too-similar reply (explicit commands get one redemption; ambient
        lines do not). `overheard` frames the message as one the bot is
        jumping in on, not one it was asked - the model holds a far
        higher bar. `task` is a step of the game the bot is hosting: the
        line must DO it (ask round 2, give the answer), and the prompt
        leads with the job rather than with the message."""
        import llm as llm_mod
        persona = self._persona_text()
        direct = not quiet and not overheard
        # The model gets human conversation, not commands, its own IRC output,
        # or older questions aimed at it. Those competing instructions caused
        # both stale direct answers and generic quiet-room monologues. A game
        # step keeps the lines aimed at the bot: the guesses ARE the material.
        prompt_lines = chatai.direct_context(
            lines, self._chat_ai_names, self.cfg.get("prefix", "!"), self.nick,
            keep_addressed=bool(task))
        # What the bot is in the middle of - the game, the counts. Every
        # prompt carries it while something is going on; it is what let a
        # host mid-quiz forget it was hosting.
        going_on = self._ongoing.prompt_lines(local=llm_mod._is_local(
            (self._opts.get("llm_base_url") or "").strip()))
        speakers = [n for n, _ in prompt_lines[-6:]] + [nick]
        # ...and whoever the line itself is about: a recall question
        # names its subject ('when did @TruckingWithDoc last stop'),
        # and the subject may not have spoken recently enough to sit in
        # the room buffer. The streamer is always a candidate - the
        # channel is about him. Case variants because memory keys are
        # the name as it was stored ('TruckingWithDoc' vs
        # 'truckingwithdoc').
        named = chatai.named_people(text)
        streamer = (self.cfg.get("channel") or "").lstrip("#")
        candidates = (speakers + named + [streamer]
                      + [n.lower() for n in named if n]
                      + [streamer.lower() if streamer else ""])
        memories = self._memory.recall(
            candidates) if self._memory.ok else []
        # A local model on CPU reads the whole prompt before writing a
        # word - that read, not the generation, is what blew a 20s
        # timeout on a warm model. Send it a smaller room and fewer
        # memories; hosted APIs keep the full context, it costs them
        # nothing.
        local = llm_mod._is_local(
            (self._opts.get("llm_base_url") or "").strip())
        system = chatai.system_prompt(persona)
        if vary:
            system += ("\nYour previous attempt repeated your own recent "
                       "lines. Write something COMPLETELY different: "
                       "different words, different angle, different mood.")
        # 'how long does it take to run 5k' is a question the model
        # KNOWS the answer to. Live-fire it went to the fact engine
        # twice (a Reddit thread title, then the race's distance); now
        # it comes here, and the prompt says what kind of ask it is.
        knowledge = direct and not task and chatai.knowledge_question(
            text, self._chat_ai_names)
        if knowledge:
            self._log("general-knowledge question - the model answers it, "
                      "not the fact engine")
        try:
            raw = llm_mod.chat_reply(
                system,
                chatai.user_prompt(prompt_lines, nick, text, memories,
                                   quiet=quiet,
                                   max_lines=8 if local else 15,
                                   max_memories=4 if local else 8,
                                   own=[] if task else list(self._chat_ai_own),
                                   overheard=overheard,
                                   notice=self._standing_notice(),
                                   knowledge=knowledge,
                                   ongoing=going_on, task=task),
                self._opts)
        except Exception as exc:
            self._log(f"chat ai error: {exc!r}")
            return None
        if not raw:
            self._log("chat model returned nothing - see the [llm] lines "
                      "above; if this repeats the model is still thinking "
                      "past its cap or erroring")
            return None
        if chatai.declined(raw):
            return None
        line = chatai.clean_line(raw)
        if line is None and not quiet and not overheard:
            # A DIRECT ask (a mention or !ask) never goes mute on an
            # unusable reply: one redemption re-ask, told what was
            # wrong. Live-fire: the zwift question's reply came back
            # unusable ('...a solid rhythm, not a') and the question
            # got SILENCE - then the next mention ('you ok?') was
            # answered with the stale zwift take.
            leaked = chatai.is_narration(raw)
            if leaked:
                # The model's THINKING came back as the content ('The
                # user is asking me who my favorite NFL team is. I need
                # to answer as the Commentator persona...'). Live-fire
                # that happened three times in a row on one question,
                # 44 s, no answer. Name it in the log and in the retry:
                # 'too long' is not what was wrong.
                self._log(f"model narrated its reasoning instead of "
                          f"answering - one retry: {raw[:120]!r}")
                llm_mod.note_narration(self._opts)
            else:
                self._log(f"chat line rejected by the cleaner - one retry: "
                          f"{raw[:120]!r}")
            try:
                raw = llm_mod.chat_reply(
                    system + ("\nYour previous reply described what you "
                              "were going to say ('The user is asking "
                              "me...', 'I need to answer as...') instead "
                              "of saying it. Do not narrate, plan or "
                              "explain. Output ONLY the line itself, "
                              "spoken in character, under 200 characters."
                              if leaked else
                              "\nYour previous reply was unusable - too "
                              "long, cut off mid-sentence, or not allowed. "
                              "Write ONE complete line of plain text, "
                              "under 200 characters."),
                    chatai.user_prompt(prompt_lines, nick, text, memories,
                                       quiet=quiet,
                                       max_lines=8 if local else 15,
                                       max_memories=4 if local else 8,
                                       own=[] if task else list(self._chat_ai_own),
                                       overheard=overheard,
                                       notice=self._standing_notice(),
                                       knowledge=knowledge,
                                       ongoing=going_on, task=task),
                    self._opts)
            except Exception as exc:
                self._log(f"chat ai error: {exc!r}")
                return None
            if not raw or chatai.declined(raw):
                return None
            line = chatai.clean_line(raw)
            if line is None and chatai.is_narration(raw):
                self._log(f"model narrated its reasoning AGAIN: "
                          f"{raw[:120]!r}")
        if line is None:
            # The rails stay the rails (no @, no links, no explicit output),
            # but an otherwise-safe paragraph can be fitted after both model
            # attempts ignored the length instruction. Direct asks get a
            # deterministic acknowledgement if even that is impossible;
            # ambient lines remain optional and may stay silent.
            recovered = (chatai.recover_direct_line(raw) if direct else None)
            if recovered:
                self._log("overlong direct reply recovered at a complete "
                          f"boundary: {recovered[:120]!r}")
                return recovered
            self._log(f"chat line rejected by the cleaner: {raw[:120]!r}")
            if direct:
                return chatai.DIRECT_FAILURE_LINE
        return line

    def _chat_ai_tick(self, now: float = None) -> bool:
        """The quiet-room half of the chat AI.

        This runs on the idle keeper's heartbeat, but it is not a repeating
        timer: one ordinary human conversation arms ONE possible follow-up.
        Once an opener is attempted, silence cannot produce another; a human
        has to restart the conversation first. The attempt is marked at
        enqueue, so a busy worker cannot double-fire it.
        """
        if not self.cfg.get("chat_ai_enabled", False) or self.paused:
            return False
        now = time.time() if now is None else now
        # Mentions held by the cooldown are answered here, to the people
        # who asked, oldest first - one per tick, so the cooldown's
        # pacing holds even when several people asked in one window.
        # Two minutes staleness each: after that the moment has passed
        # and answering would be the non-sequitur, not the fix.
        while self._chat_ai_pending:
            head = self._chat_ai_pending[0]
            # Entries made before the expiry field (and any a test builds by
            # hand) keep the old flat two minutes.
            expires = head[4] if len(head) > 4 else head[2] + 120
            if expires > now:
                break
            stale = self._chat_ai_pending.pop(0)   # the next may be live
            self._log(f"held question from {stale[0]} went stale - not "
                      f"answered")
            if (stale[3] if len(stale) > 3 else "") in self._ack_last:
                # We told them an answer was coming. Going quiet now is
                # exactly the silence that reads as being ignored.
                self._queue_say(
                    f"@{stale[0]} that one got away from me - ask me again "
                    f"and I'll get it.")
        # The first held question whose asker is clear to be answered -
        # not necessarily the oldest: with per-viewer clocks, Dani's
        # question does not wait for Yeyeboi's cooldown.
        for i, p in enumerate(self._chat_ai_pending):
            who = p[3] if len(p) > 3 else (p[0] or "").lower()
            if self._mention_wait(who, now) <= 0:
                # Direct questions are not ambient chatter and therefore are
                # never stranded behind the autonomous hourly cap.
                self._chat_ai_pending.pop(i)
                self._log(f"answering {p[0]}'s held message")
                self._jobs.put((p[0], who, "", "chime", p[1]))
                return True
        # !cb off controls only the bot's own initiative. Check it after the
        # direct queue so the autonomous switch cannot strand a real question.
        if self._cb_ambient_off:
            return False
        try:
            quiet_seconds = float(self.cfg.get("chat_ai_quiet_seconds", 300))
        except (TypeError, ValueError):
            quiet_seconds = 300.0
        # Zero is the intuitive way to turn quiet openers off entirely.
        if quiet_seconds <= 0 or not self._chat_ai_quiet_armed:
            return False
        if now - self._last_chat < quiet_seconds:
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
                "chat_ai_quiet_cooldown", 900)):
            return False
        if len([t for t in self._chat_ai_times if now - t < 3600]) >= int(
                self.cfg.get("chat_ai_max_hour", 6)):
            return False
        self._chat_ai_last = now
        self._chat_ai_quiet_armed = False
        self._jobs.put(("", "", "", "chime", ""))
        return True

    #: 'how many follows this stream', 'follower count' - channel stats
    #: the bot can answer from Helix, not trivia for the fact engine.
    _FOLLOWS_Q = re.compile(
        r"\bhow many (?:new )?follow(?:ers?|s)?\b"
        r"|\bfollow(?:er)?s? count\b", re.IGNORECASE)

    def _follows_question(self, text: str) -> bool:
        return bool(self._FOLLOWS_Q.search(text or ""))

    def _say_follows(self, nick: str) -> bool:
        """Answer a follower-count question from Helix. False lets the
        persona answer honestly (it has no number) - which is what
        happened before, except the bot DID have the number all along:
        the startup probe prints it and never kept it."""
        helix = self._access.helix
        if helix is None:
            return False
        try:
            total = helix.follow_total()
        except Exception as exc:
            self._log(f"follow count lookup failed: {exc!r}")
            return False
        if total is None:
            self._log("follow count lookup returned nothing")
            return False
        if self._follows_start is None:
            self._follows_start = total
            self._say(f"@{nick} {total:,} followers right now.")
            self._log(f"follow count answered: {total} (no baseline)")
            return True
        new = max(0, total - self._follows_start)
        if new:
            self._say(f"@{nick} {total:,} followers now - {new:,} new "
                      f"since I came online.")
        else:
            self._say(f"@{nick} {total:,} followers, none new since I "
                      f"came online.")
        self._log(f"follow count answered: {total} "
                  f"({new} new since baseline)")
        return True

    def _asks_about_someone(self, text: str) -> bool:
        """True when the question is about a PERSON, not trivia.

        Live-fire: 'when and where did @TruckingWithDoc last take a
        piss?' went to the fact engine and came back a weigh-station
        fact. Any @-mentioned name, the streamer's own name, or someone
        the bot holds memories of makes it the persona's question - the
        memories are injected into its prompt."""
        t = text or ""
        if re.search(r"@[A-Za-z0-9_]{3,}", t):
            return True
        streamer = (self.cfg.get("channel") or "").lstrip("#")
        if streamer:
            if re.search(r"(?<![a-z0-9])" + re.escape(streamer.lower())
                         + r"(?![a-z0-9])", t.lower()):
                return True
        if self._memory.ok:
            named = chatai.named_people(t)
            named = named + [n.lower() for n in named if n]
            if named and self._memory.recall(named):
                return True
        return False

    def _do_chime(self, nick: str, text: str) -> None:
        """A bot-initiated line of chat, composed off the read loop.

        An empty nick is the quiet-room opener: posted bare (nobody to
        @), and no memory distill (nobody talked).
        """
        quiet = not (nick or "").strip()
        now = time.time()
        addressed = (None if quiet else
                     chatai.mention_kind(text, self._chat_ai_names))
        # Re-check session switches at execution time: a queued autonomous job
        # may have waited behind slower work. !cb off silences that initiative,
        # but never a human who directly addressed the bot.
        if self.paused or (self._cb_ambient_off and not addressed):
            return
        if quiet:
            # A human may resume chat while this job waits behind a fact lookup.
            # Do not drop a timer line into a conversation that is alive again.
            try:
                quiet_seconds = float(self.cfg.get(
                    "chat_ai_quiet_seconds", 300))
            except (TypeError, ValueError):
                quiet_seconds = 300.0
            if quiet_seconds <= 0 or now - self._last_chat < quiet_seconds:
                return
        elif addressed:
            # Re-checked at execution time: this job may have waited
            # behind slower work. Only THIS viewer's clock and the short
            # channel pace count - and a drop is never silent (live-fire:
            # two held questions vanished here with no line at all).
            wait = self._mention_wait(nick, now)
            if wait > 0:
                if wait <= 20:
                    time.sleep(wait)    # the pace gap: wait it out
                else:
                    self._log(f"{nick}'s question dropped at the worker - "
                              f"answered them {wait:.0f}s inside their own "
                              f"cooldown already")
                    return
        else:
            # The room can accelerate after an ambient job is queued. Direct
            # asks continue; unsolicited work is canceled before paying the
            # model or interrupting flowing chat.
            _, busy = self._chat_activity(now)
            if busy:
                return
        # A factual question ADDRESSED to the bot ('doc, what is a bongo
        # twist?') is answered by the fact engine, not the persona - same
        # rule as !ask: grounded beats charming, and a guess is the
        # failure mode. Overheard questions do NOT get this: 'Where ya
        # cuttin thru with Illinois?' was asked of the room, and the
        # chime path answering it with a FunFact was the bot answering a
        # question nobody asked it. And a question about a PERSON skips
        # the engine too: 'when and where did @TruckingWithDoc last
        # take a piss?' is a recall question, and the encyclopedia
        # answered it with a weigh station (live-fire).
        # 'How many follows this stream?' is a channel-stats question:
        # the bot holds the number (the startup probe fetches it) and
        # must not hand it to the encyclopedia or shrug.
        if not quiet and self._follows_question(text) \
                and self._say_follows(nick):
            now = time.time()
            self._chat_ai_times = [t for t in self._chat_ai_times
                                   if now - t < 3600] + [now]
            self._mark_mention_reply(nick, now)
            return
        if not quiet and addressed and self._lookup_blocked(nick, text):
            self._mark_mention_reply(nick, time.time())
            return
        if not quiet and addressed \
                and chatai.factual_question(text, self._chat_ai_names) \
                and not self._asks_about_someone(text) \
                and self._answer_factual(nick, text):
            now = time.time()
            self._chat_ai_times = [t for t in self._chat_ai_times
                                   if now - t < 3600] + [now]
            self._mark_mention_reply(nick, now)
            return
        # 'Docbot sing me a song' / 'make me a poem' asks for a PIECE, not
        # a line. Through the one-line path the model wrote a sentence
        # about singing and stopped - it rambled and never did the thing.
        # A performance is written whole and delivered over several
        # messages, a few seconds apart. Only when addressed: the bot
        # never breaks into song because two viewers discussed karaoke.
        if not quiet and addressed and self._perform(nick, text):
            now = time.time()
            self._chat_ai_times = [t for t in self._chat_ai_times
                                   if now - t < 3600] + [now]
            self._mark_mention_reply(nick, now)
            self._distill(nick, self._chat_ai_snapshot())
            return
        snapshot = self._chat_ai_snapshot()
        line = self._chat_ai_line(snapshot, nick or "chat", text, quiet=quiet,
                                  overheard=not (quiet or addressed))
        # Failed attempts back off on THEIR OWN clock. The old code put every
        # non-quiet attempt on the mention clock: rejected ambient chimes never
        # started chat_ai_cooldown, yet they blocked actual questions. That is
        # why the log filled with chime declines while mentions were held.
        now = time.time()
        if quiet or not addressed:
            self._chat_ai_last = now
        else:
            self._mark_mention_reply(nick, now)
        if not line:
            # The model is down or declined. A general-knowledge question
            # ('how long does it take to run 5k') still gets the fact
            # engine's try - it posts only a figure of the kind asked for,
            # or nothing. A chatty direct address gets a canned Doc line -
            # the bot never goes fully mute on "doc, hows it going?" - but
            # any other factual question stays silent rather than risk a
            # made-up answer.
            if (addressed and not quiet
                    and chatai.knowledge_question(text, self._chat_ai_names)
                    and self._answer_factual(nick, text)):
                self._log("the model had nothing for a general-knowledge "
                          "question - the fact engine answered it")
                self._chat_ai_times = [t for t in self._chat_ai_times
                                       if now - t < 3600] + [now]
                return
            quip = chatai.smalltalk(text)
            if quip and not quiet:
                self._say(self._fit(f"@{nick} ", quip))
            return
        context = chatai.direct_context(
            snapshot, self._chat_ai_names, self.cfg.get("prefix", "!"),
            self.nick)
        similarity_source = (context[-1][1] if quiet and context else text)
        if chatai.too_similar(line, self._chat_ai_own,
                              source=similarity_source):
            if addressed:
                # A direct question is not optional chatter. Re-ask once with
                # the repetition named instead of silently discarding a valid
                # answer because the persona reused a motif.
                self._log("direct reply sounded recycled - one retry")
                line = self._chat_ai_line(snapshot, nick, text, vary=True)
                if not line or chatai.too_similar(
                        line, self._chat_ai_own, source=text):
                    self._say(self._fit(f"@{nick} ",
                                        chatai.DIRECT_FAILURE_LINE))
                    self._log("direct reply declined after repetition retry")
                    return
            else:
                self._log(f"chat line declined - too similar to its own "
                          f"recent lines: {line[:80]!r}")
                return
        if quiet:
            # A quiet opener resumes the actual last HUMAN topic or stays
            # quiet. This is the output-side guarantee against scheduled
            # cadence/coffee/trucking slogans unrelated to the room.
            if not similarity_source or not chatai.grounded(
                    line, similarity_source):
                self._log("quiet opener declined - not about the last human "
                          f"topic: {line[:80]!r}")
                return
            if chatai.parrots(line, similarity_source):
                self._log("quiet opener declined - it repeated the last human "
                          f"line: {line[:80]!r}")
                return
        if not quiet and not addressed:
            # A chime must be ABOUT the message it jumps on. Live-fire:
            # a supplement comment got 'The freezer rattles like wind
            # through pine trees, and I'm swapping frozen beans for a
            # steaming oat latte' - a persona poem at a person who said
            # nothing about freezers. It must share a content word with
            # what was said... and must not simply repeat chat back.
            # Mentions and !ask are not gated: they were addressed to
            # the bot, so relevance is already given.
            if not chatai.grounded(line, text):
                self._log(f"chime declined - not about what was said: "
                          f"{line[:80]!r}")
                return
            if chatai.parrots(line, text):
                self._log(f"chime declined - it repeated chat back: "
                          f"{line[:80]!r}")
                return
        self._chat_ai_times = [t for t in self._chat_ai_times
                               if now - t < 3600] + [now]
        self._chat_ai_own = (self._chat_ai_own + [line])[-3:]
        if quiet:
            self._say(self._fit("", line))
            self._log("chat ai opened the quiet room")
        else:
            self._say(self._fit(f"@{nick} ", line))
            self._log(f"chat ai replied to {nick}")
            self._distill(nick, self._chat_ai_snapshot())

    # ---- performances: a song, a poem, a story, over several messages ----
    def _perform_gap(self) -> float:
        """Seconds between the lines of a performance - the same knob as
        the beef acts unless chat_ai_perform_delay says otherwise."""
        for key in ("chat_ai_perform_delay", "beef_act_delay"):
            try:
                value = self.cfg.get(key)
                if value is not None and value != "":
                    return max(0.0, float(value))
            except (TypeError, ValueError):
                continue
        return 4.0

    def _perform(self, nick: str, text: str) -> bool:
        """Deliver a song / poem / story / rap / limerick / haiku / toast
        when `text` asks for one. True when it was handled - including
        an honest 'can't right now' - False when the message is not a
        performance request at all (the one-line path takes it).

        The piece is composed off the read loop (this runs on the
        worker), validated line by line against the same rails as any
        chat line, and then posted the way a person would deliver it:
        the first line at once, @-tagged to whoever asked, the rest a
        few seconds apart through the drip scheduler, so the worker is
        free again immediately and chat can react between lines. A model
        that pads with 'Sure, here's a song:' loses that line, not the
        piece; one that breaks a rail anywhere loses the piece and is
        asked once more with the miss named; a second miss gets a plain
        one-line decline rather than half a song.
        """
        import llm as llm_mod
        want = chatai.performance_request(text, self._chat_ai_names)
        if want is None:
            if chatai.encore_request(text, self._chat_ai_names) \
                    and self._last_performance:
                want = self._last_performance
            else:
                return False
        kind, subject = want
        if not llm_mod.any_configured(self._opts):
            # No model anywhere: say so instead of a Wikipedia fact about
            # the word 'song'. The line is deterministic on purpose.
            self._say(self._fit(f"@{nick} ",
                                chatai.performance_unavailable(kind)))
            return True
        self._last_performance = (kind, subject)
        snapshot = self._chat_ai_snapshot()
        room = chatai.direct_context(
            snapshot, self._chat_ai_names, self.cfg.get("prefix", "!"),
            self.nick)
        system = chatai.system_prompt(self._persona_text())
        prompt = chatai.performance_prompt(kind, subject, nick, room)
        piece = []
        for attempt in range(2):
            try:
                raw = llm_mod.chat_reply(
                    system + ("" if attempt == 0 else
                              "\nYour previous attempt was unusable - it "
                              "broke a rule above, or was not the piece "
                              "itself. Deliver ONLY the lines of the piece."),
                    prompt, self._opts,
                    max_tokens=llm_mod.PERFORMANCE_MAX_TOKENS)
            except Exception as exc:
                self._log(f"performance error: {exc!r}")
                raw = None
            piece = chatai.clean_performance(raw, kind)
            if piece:
                break
            self._log(f"{kind} for {nick} rejected by the cleaner"
                      f"{' - one retry' if attempt == 0 else ''}: "
                      f"{(raw or '')[:120]!r}")
        if not piece:
            self._say(self._fit(f"@{nick} ",
                                chatai.performance_unavailable(kind)))
            return True
        self._chat_ai_own = (self._chat_ai_own + [piece[-1]])[-3:]
        self._drip(piece, self._perform_gap(), head=f"@{nick} ")
        self._log(f"{kind} for {nick}"
                  + (f" about {subject!r}" if subject else "")
                  + f": {len(piece)} lines, {self._perform_gap():g}s apart")
        return True

    def _distill(self, nick: str, lines: list) -> None:
        """After talking, remember what is durable about the viewer.

        Runs on the worker thread, after the reply has posted, so the
        extra model call never delays a line of chat. Bounded for free:
        it only runs after a chime or an !ask, both already throttled.
        """
        import llm as llm_mod
        if not self.cfg.get("chat_ai_enabled", False):
            return              # !ask works with the AI off; memory does not
        if not self._memory.ok or not llm_mod.any_configured(self._opts):
            return
        theirs = [(n, t) for n, t in lines if (n or "").lower()
                  == (nick or "").lower()]
        if not theirs:
            return
        # Pace it. The old code distilled after every reply: ~500
        # tokens of bookkeeping per ~1,100-token exchange, a third of
        # the day's budget spent on a call that mostly returns NOTHING
        # WORTH KEEPING for a viewer who said 'lol' since last time.
        # Now a viewer is distilled again only when they have said at
        # least chat_ai_distill_lines NEW lines since their last
        # distill, and at most once per chat_ai_distill_minutes. More
        # material per call, far fewer calls, same memory.
        who = (nick or "").lower()
        now = time.time()
        try:
            min_lines = max(1, int(self.cfg.get("chat_ai_distill_lines", 4)))
        except (TypeError, ValueError):
            min_lines = 4
        try:
            min_gap = max(0.0, float(
                self.cfg.get("chat_ai_distill_minutes", 10))) * 60
        except (TypeError, ValueError):
            min_gap = 600.0
        # 'Seen' is the viewer's LAST line at the previous distill: the
        # room buffer rolls, so counting is done from that line forward,
        # not by comparing lengths.
        last_t, seen_last = self._distilled.get(who, (0.0, None))
        fresh = [t for _, t in theirs]
        if seen_last in fresh:
            new_lines = len(fresh) - fresh.index(seen_last) - 1
        else:
            new_lines = len(fresh)
        if last_t and (now - last_t < min_gap or new_lines < min_lines):
            return
        self._distilled[who] = (now, fresh[-1])
        if len(self._distilled) > 500:
            self._distilled = {k: v for k, v in self._distilled.items()
                               if now - v[0] < 7200}
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

    def _answer_factual(self, nick: str, question: str) -> bool:
        """A factual question gets the fact engine's grounded answer.

        The persona will happily guess on trivia it does not know
        ('sounds like a spin on a roadside snack') while the real answer
        sits in the engine - the streamer's own !funfact proved it. Used
        by !ask and by factual mentions; returns True when an answer
        posted (the caller bumps the clocks)."""
        import llm as llm_mod
        # 'doc, what is a bongo twist?' - the engine gets the question,
        # not the address; the FunFact header must not carry 'doc'.
        question = chatai.strip_address(question, self._chat_ai_names)
        opts = self._opts
        if llm_mod.chat_timed_out() and llm_mod._is_local(
                (self._opts.get("llm_base_url") or "").strip()):
            # The model is known-busy right now (a chat call just timed
            # out on it): the engine's question path would stack another
            # model call on the same busy model. Records only.
            opts = {**self._opts, "_skip_llm": True}
        result = get_funfact(question, opts)
        if not result or not result.get("fact"):
            return False
        self._reply(nick, question, result)
        self._distill(nick, self._chat_ai_snapshot())
        return True

    def _load_json_state(self, path: str) -> dict:
        """A small runtime state file (persona, sub goal). Missing or
        corrupt means empty state, never a crash - a mod can always
        re-set it."""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_json_state(self, path: str, data: dict) -> None:
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
        except OSError as exc:
            self._log(f"could not save {path}: {exc!r}")

    def _persona_text(self) -> str:
        """The persona the chat AI actually uses: the mod-chosen preset
        or custom text, else the config's bot_personality, else Doc."""
        p = self._persona or {}
        if p.get("name") == "custom" and (p.get("text") or "").strip():
            return p["text"].strip()
        text = chatai.persona(p.get("name") or "")
        if text:
            return text
        return self.cfg.get("bot_personality", "") or chatai.DEFAULT_PERSONA

    def _persona_command(self, nick: str, badges: str,
                         argument: str) -> None:
        """!persona - show, list, set, custom, reset. Moderators only;
        a viewer gets silence, like the other mod switches."""
        pre = self.cfg.get("prefix", "!")
        if access.tier_from_badges(badges) not in ("broadcaster",
                                                   "moderator"):
            self._log(f"!persona from {nick} ignored - not a mod")
            return
        args = (argument or "").strip()
        if not args:
            cur = (self._persona or {}).get("name") or "doc"
            self._say(f"@{nick} current voice: {cur}. {pre}persona list "
                      f"to see them all, {pre}persona set <name> to "
                      f"switch.")
            return
        verb, _, rest = args.partition(" ")
        verb = verb.lower()
        rest = rest.strip()
        if verb == "list":
            # One message per crew, through the queue like !help: the
            # whole library in one line is longer than Twitch allows,
            # and the read loop must not sit in _say's pacing four
            # times over.
            self._queue_fitted(
                f"@{nick} ", f"{len(chatai.PERSONAS)} voices - {pre}persona "
                f"set <name> to switch, {pre}persona custom <description> "
                f"for your own:")
            listed = set()
            for label, names in chatai.PERSONA_GROUPS:
                names = [n for n in names if n in chatai.PERSONAS]
                listed.update(names)
                self._queue_fitted(
                    f"{label}: ", ", ".join(
                        f"{n} ({chatai.PERSONA_BLURBS.get(n, '')})"
                        for n in names))
            stray = sorted(set(chatai.PERSONAS) - listed)
            if stray:
                self._queue_fitted("more: ", ", ".join(
                    f"{n} ({chatai.PERSONA_BLURBS.get(n, '')})"
                    for n in stray))
            return
        if verb == "set":
            name = chatai.persona_name(rest)
            if name:
                self._persona = {"name": name}
                self._save_json_state(self.cfg.get(
                    "persona_state_path", "persona.json"), self._persona)
                self._say(f"@{nick} voice set to {name}.")
                self._log(f"persona set to {name} by {nick}")
                return
            self._say(f"@{nick} no voice called '{rest}'. {pre}persona "
                      f"list shows them all.")
            return
        if verb == "custom":
            text = rest
            if not 12 <= len(text) <= 300:
                self._say(f"@{nick} describe the voice in 12-300 "
                          f"characters.")
                return
            if funfacts._EXPLICIT.search(text) \
                    or funfacts._TASTELESS.search(text):
                self._say(f"@{nick} not that kind of stream.")
                return
            self._persona = {"name": "custom", "text": text}
            self._save_json_state(self.cfg.get(
                "persona_state_path", "persona.json"), self._persona)
            self._say(f"@{nick} custom voice set.")
            self._log(f"persona set to custom by {nick}: {text[:60]!r}")
            return
        if verb in ("reset", "default", "doc"):
            self._persona = {"name": "doc"}
            self._save_json_state(self.cfg.get(
                "persona_state_path", "persona.json"), self._persona)
            self._say(f"@{nick} back to Doc.")
            return
        self._say(f"@{nick} {pre}persona [list|set <name>|custom <text>|"
                  f"reset]")

    def _subgoal_mutation(self, nick: str, badges: str,
                          argument: str) -> bool:
        """Handle sub-goal changes. True if this was a mutation."""
        verb, _, rest = (argument or "").strip().partition(" ")
        verb = verb.lower()
        if verb not in ("set", "count", "add", "sub", "clear"):
            return False
        if access.tier_from_badges(badges) not in ("broadcaster",
                                                   "moderator"):
            self._log(f"!subgoal {verb} from {nick} ignored - not a mod")
            return True          # recognised, but not theirs to change
        pre = self.cfg.get("prefix", "!")
        if verb == "clear":
            self._subgoal = {}
            self._save_json_state(self.cfg.get(
                "subgoal_state_path", "subgoal.json"), self._subgoal)
            self._say(f"@{nick} sub goal cleared.")
            return True
        if verb == "set":
            # !subgoal set 50 wear a clown costume for a whole shift
            number, _, label = rest.partition(" ")
            try:
                goal = int(number)
            except ValueError:
                goal = 0
            label = label.strip()
            if goal < 2 or not label:
                self._say(f"@{nick} usage: {pre}subgoal set <goal> <what "
                          f"happens when we hit it>")
                return True
            self._subgoal = {"goal": goal,
                             "current": int(self._subgoal.get("current",
                                                              0) or 0),
                             "label": label}
            self._save_json_state(self.cfg.get(
                "subgoal_state_path", "subgoal.json"), self._subgoal)
            self._say(self._fit(f"@{nick} sub goal set: ",
                                self._subgoal_line()))
            self._log(f"sub goal set by {nick}: {goal} - {label[:60]!r}")
            return True
        # count/add/sub all take one number
        try:
            n = int(rest.split()[0])
        except (ValueError, IndexError):
            n = None
        if n is None:
            self._say(f"@{nick} usage: {pre}subgoal count <N> | "
                      f"add <N> | sub <N>")
            return True
        if not self._subgoal.get("goal"):
            self._say(f"@{nick} no goal set yet - {pre}subgoal set <goal> "
                      f"<what happens when we hit it> first.")
            return True
        if verb == "count":
            self._subgoal["current"] = max(0, n)
        elif verb == "add":
            self._subgoal["current"] = self._subgoal.get("current", 0) + n
        elif verb == "sub":
            self._subgoal["current"] = max(
                0, self._subgoal.get("current", 0) - n)
        self._save_json_state(self.cfg.get(
            "subgoal_state_path", "subgoal.json"), self._subgoal)
        self._say(self._fit(f"@{nick} ", self._subgoal_line()))
        self._log(f"sub goal now {self._subgoal.get('current')}/"
                  f"{self._subgoal.get('goal')} (by {nick})")
        return True

    def _subgoal_line(self) -> str:
        g = self._subgoal or {}
        goal, current = g.get("goal") or 0, g.get("current") or 0
        label = g.get("label") or "the goal"
        if current >= goal:
            return (f"GOAL REACHED - {current}/{goal} subs, and that "
                    f"means: {label}. Pay up.")
        return (f"{current}/{goal} subs - {label} - {goal - current} "
                f"to go!")

    def _say_subgoal(self, nick: str) -> None:
        if not (self._subgoal or {}).get("goal"):
            self._say(f"@{nick} no sub goal set right now.")
            return
        self._say(self._fit(f"@{nick} ", self._subgoal_line()))

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
        if self._lookup_blocked(nick, q):
            return
        # Factual questions get the engine's grounded answer FIRST: the
        # persona guesses on trivia, the engine looks it up. If the engine
        # has nothing, the persona still gets its chance below.
        if chatai.factual_question(q, self._chat_ai_names) \
                and self._answer_factual(nick, q):
            return
        # '!ask sing me a song' is the same request as saying it to the
        # bot: a piece over several lines, never a one-line reply about it.
        if chatai.performance_request(q, self._chat_ai_names) \
                and self._perform(nick, q):
            self._distill(nick, self._chat_ai_snapshot())
            return
        if llm_mod.any_configured(self._opts):
            snapshot = self._chat_ai_snapshot()
            line = self._chat_ai_line(snapshot, nick, q)
            if line and chatai.too_similar(
                    line, self._chat_ai_own, source=q):
                # An explicit command gets one redemption: ask again with
                # the repetition named, then take whatever comes.
                line = self._chat_ai_line(snapshot, nick, q, vary=True)
            if line and not chatai.too_similar(
                    line, self._chat_ai_own, source=q):
                self._say(self._fit(f"@{nick} ", line))
                self._chat_ai_own = (self._chat_ai_own + [line])[-3:]
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
        # If the chat call just TIMED OUT on a local model, the model is
        # too busy to answer anything: the question path would stack a
        # second, bigger call (it carries the sources) on it and burn
        # another full timeout before the records answer anyway. Skip
        # straight to the keyless paths.
        if llm_mod.chat_timed_out() and llm_mod._is_local(
                (self._opts.get("llm_base_url") or "").strip()):
            self._log("chat model is too slow right now - answering "
                      "without it")
            self._reply(nick, q, get_funfact(
                q, {**self._opts, "_skip_llm": True}))
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
            f"{prefix}ban / {prefix}timeout / {prefix}unban <name> - "
            f"moderation (mods only)"
            if self.cfg.get("mod_commands_enabled", True) else None,
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
                f"@{nick} ", f"mods: {prefix}subgoal set <goal> <what "
                f"happens when we hit it>, {prefix}subgoal count/add/sub "
                f"<N> to keep it moving")
            self._queue_fitted(
                f"@{nick} ", f"mods: {prefix}persona set <name> changes "
                f"the bot's voice ({prefix}persona list, custom <text>, "
                f"reset)")
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
        limit = int(self.cfg.get("max_message_chars", 450))
        if result.get("sentence"):
            # Live data written as a sentence (weatherapi.com): it names
            # its own place, so it is said TO the asker rather than filed
            # under a 'Weather | place:' header.
            prefix = f"{nick}, "
            msg = prefix + trim_to_fit(fact, max(40, limit - len(prefix)))
            self._say(msg)
            self._log(f"replied for {argument!r}")
            return
        name = result.get('kind') \
            or self.cfg.get('fact_prefix', 'FunFact')
        if result.get("news"):
            # A headline is quoted, not filed: 'News | helicopter crash
            # California: NBC4 helicopter crashes in Chatsworth, killing
            # 3 (Los Angeles Times, 3h ago)' - the outlet and the age
            # are the answer's credentials, so they are never trimmed off.
            prefix = f"{name} | {place}: "
            self._say(prefix + trim_to_fit(fact, max(60, limit - len(prefix))))
            self._log(f"replied with a headline for {argument!r}")
            return
        prefix = f"{name} | {place}: "
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
    wkey = (cfg.get("weatherapi_key") or os.environ.get("WEATHERAPI_KEY", "")
            or "").strip()
    print("  weather          : " + (
        "weatherapi.com (one sentence to the asker), Open-Meteo fallback"
        if wkey else
        "Open-Meteo (no weatherapi_key - set one for the sentence format)"))

    try:
        import llm as llm_mod
    except Exception as exc:
        print(f"  llm module       : import failed - {exc!r}")
        return
    if not llm_mod.any_configured(cfg):
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
        llm_ok = llm_mod.any_configured(cfg)
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
    if not key and provider != "local Ollama":
        print(f"[llm] primary NOT configured — {provider}, model {model}, "
              "no key")
    else:
        print(f"[llm] using {provider} — model {model}, key {masked}")

    # Failover used to be silent when one field was misspelled or missing. The
    # resulting live log showed endless primary 429s but gave no clue that the
    # supposed fallback had never become an endpoint.
    try:
        import llm as llm_mod
        providers = llm_mod.fallback_providers(cfg)
        problem = llm_mod.fallback_problem(cfg)
    except Exception as exc:
        print(f"[llm] fallback status unavailable: {exc!r}")
        return
    if not providers:
        level = "MISCONFIGURED" if (cfg.get("llm_fallback_providers")
                                    or any((cfg.get(k) or "").strip() for k in (
                                        "llm_fallback_key", "llm_fallback_api_key",
                                        "llm_fallback_base_url", "llm_fallback_url",
                                        "llm_fallback_model",
                                        "llm_fallback_model_name"))) else "OFF"
        print(f"[llm] fallback {level} — {problem}")
        return
    # Every provider in the chain, in the order a line will be tried. One
    # line each: a chain that quietly stops at the first dead key is the
    # exact failure this list exists to prevent.
    for i, (fbase, fkey, fmodels) in enumerate(providers):
        fprovider = llm_mod.provider_name(fbase)
        fmasked = ("(no key)" if llm_mod._is_local(fbase) else
                   (f"{fkey[:4]}…{fkey[-4:]}" if len(fkey) > 10 else "(set)"))
        label = "fallback READY" if i == 0 else f"fallback #{i + 1} READY"
        print(f"[llm] {label} — {fprovider}, "
              f"model {', '.join(fmodels)}, key {fmasked}")


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
        "llm_fallback_key": cfg.get("llm_fallback_key", ""),
        "llm_fallback_base_url": cfg.get("llm_fallback_base_url", ""),
        "llm_fallback_model": cfg.get("llm_fallback_model", ""),
        "llm_fallback_providers": list(cfg.get("llm_fallback_providers") or []),
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
        llm_ok = llm_mod.any_configured(cfg)
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


#: A line that already carries its own [HH:MM:SS] stamp - the bot's own
#: log lines do, and double-stamping them made the file noisy.
_STAMPED = re.compile(r"^\[\d{2}:\d{2}:\d{2}\] ")


class _Tee:
    """Writes to the console AND a log file, so the log survives the
    window scrolling away. Enabled by setting "log_file" in config.json
    (e.g. "bot.log"); empty means console only, the default.

    The console output is untouched; the FILE gets a [HH:MM:SS] stamp on
    every line. Half the log lines already carry timestamps and half do
    not ([llm], [access]), which made 'what happened at 12:47' a chore
    to answer from a file."""
    _TS = "[%H:%M:%S] "

    def __init__(self, stream, path: str):
        self._stream = stream
        self._fh = open(path, "a", encoding="utf-8", errors="replace")
        self._at_line_start = True

    def write(self, data) -> int:
        self._stream.write(data)
        data = data if isinstance(data, str) else str(data)
        parts = data.split("\n")
        for i, line in enumerate(parts):
            last = i == len(parts) - 1
            # Never double-stamp: many lines already carry [HH:MM:SS].
            if (self._at_line_start and (line or not last)
                    and not _STAMPED.match(line)):
                self._fh.write(time.strftime(self._TS))
            self._fh.write(line)
            if not last:
                self._fh.write("\n")
        self._at_line_start = parts[-1] == ""
        return len(data)

    def flush(self) -> None:
        self._stream.flush()
        self._fh.flush()


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force_login = "--login" in sys.argv
    do_selftest = "--selftest" in sys.argv
    do_doctor = "--doctor" in sys.argv

    # --admin-user NAME [--role mod]: create or reset a panel login, then
    # exit. Done before the config is read so it works on a fresh server
    # that has no config.json yet.
    if "--admin-user" in sys.argv:
        i = sys.argv.index("--admin-user")
        name = sys.argv[i + 1] if i + 1 < len(sys.argv) else ""
        if not name or name.startswith("--"):
            raise SystemExit("usage: python3 bot.py --admin-user NAME [--role admin|mod]")
        role = "admin"
        if "--role" in sys.argv:
            j = sys.argv.index("--role")
            role = sys.argv[j + 1] if j + 1 < len(sys.argv) else "admin"
        raise SystemExit(adminpanel.set_user_interactive(name, role))
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

    # Keep the log: everything the console shows also lands in a file,
    # including crash tracebacks (stderr is teed too). The console window
    # scrolls away and dies with the window; the file does not.
    log_path = (cfg.get("log_file") or "").strip()
    if log_path:
        sys.stdout = _Tee(sys.stdout, log_path)
        sys.stderr = _Tee(sys.stderr, log_path)
        print(f"[log] also writing everything to {log_path}")

    # The admin panel's log tab shows what the console shows: keep the last
    # few thousand lines in memory from here on (cheap, and off when the
    # panel is off - the ring only exists so the panel has something to
    # show from before it was opened).
    log_ring = None
    if cfg.get("admin_panel_enabled"):
        log_ring = adminpanel.LogRing()
        adminpanel.install_log_ring(log_ring)

    # Which build is this? A pasted log should never require guessing
    # whether a fix is actually running. Silent if git is unavailable.
    try:
        import subprocess
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        if commit:
            print(f"[bot] build {commit}")
        else:
            print("[bot] build unknown - no commit available; if this "
                  "folder has no .git, updates were copied in by hand")
    except Exception:
        print("[bot] build unknown - git is unavailable here")

    # ...and because hand-copied folders have no git at all, the fix
    # count IS the build number: 149 is a different build than 143,
    # whatever the folder's history says. A pasted log can then never
    # leave us guessing which fixes are actually running.
    try:
        import subprocess
        _here = os.path.dirname(os.path.abspath(__file__))
        _checker = os.path.join(_here, "check_fixes.py")
        _run = subprocess.run(
            [sys.executable, _checker],
            capture_output=True, text=True, timeout=120, cwd=_here,
            encoding="utf-8", errors="replace",
        )
        _out = _run.stdout or ""
        _m = re.search(r"(\d+)/(\d+) present", _out)
        if _m:
            print(f"[bot] fixes self-check: {_m.group(1)}/{_m.group(2)} "
                  f"present - that is the build you are running")
            # A count like 155/157 proves something is missing but used to hide
            # WHAT, forcing another round trip for the full checker output.
            for _missing in re.findall(
                    r"^\s*\[ \]\s*(.*?)(?:\s{2,})?$", _out, re.MULTILINE):
                print(f"[bot] MISSING FIX: {_missing.strip()}")
        elif not os.path.exists(_checker):
            print("[bot] fixes self-check: could not read check_fixes.py "
                  "- is it next to bot.py?")
        else:
            # The file is there; the checker itself died before it could
            # print a count (a check raised, an import failed). That used to
            # be reported as "could not read", which sent people looking for
            # a missing file when the traceback held the actual answer.
            print(f"[bot] fixes self-check: check_fixes.py failed "
                  f"(exit code {_run.returncode}) before printing a count")
            _err = (_run.stderr or "").strip()
            for _line in (_err or "(no error output)").splitlines()[-25:]:
                print(f"[bot] fixes self-check | {_line}")
    except Exception as exc:
        print(f"[bot] fixes self-check: could not run check_fixes.py - "
              f"{type(exc).__name__}: {exc}")

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
    if cfg.get("admin_panel_enabled"):
        adminpanel.start_panel(cfg, adminpanel.BotControl(
            bot=bot, cfg=cfg, config_path=path, ring=log_ring,
            restart=adminpanel.restart_process))
    try:
        bot.run()
    except KeyboardInterrupt:
        bot.running = False
        bot._close()
        print("\nshutting down ...")


if __name__ == "__main__":
    main()
