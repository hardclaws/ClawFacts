# 🚚 Twitch FunFact Bot

A lightweight Twitch chat bot for truck-driving (or any IRL) streams. Viewers type:

```
!funfact Milford, PA
```

…and the bot searches the internet and replies with a fun fact about that place:

```
FunFact | Milford, Pennsylvania: Milford was founded in 1796 by Judge John Biddis, one of Pennsylvania's first four circuit judges.
```

## How it works

1. The bot connects to Twitch IRC (`irc.chat.twitch.tv`) with a bot account.
2. When a viewer types `!funfact <place>`, it looks the place up online and posts the result to chat.
3. Fact sources — and how it handles **any** town, even remote ones:
   - **Wikipedia** — full-text search → article lead + History, then the most "interesting" sentences are ranked. Wikipedia covers an enormous number of tiny places (hamlets, unincorporated communities, roadhouses).
   - **DuckDuckGo Instant Answers** — fallback if Wikipedia fails or rate-limits.
   - **Geocoder + coordinate search** — if text search finds nothing (very remote spots), the bot geocodes the name with OpenStreetMap's Nominatim (free, no key, covers even tiny villages), retries with the canonical "Name, State", then uses Wikipedia's coordinate search for the nearest notable place.
   - **Open-Meteo live clock data** — sunrise and sunset questions use the requested place's coordinates and timezone, not search-result snippets or an LLM.
   - **weatherapi.com (optional key) / Open-Meteo** — weather questions are live readings, never snippets; see [Weather](#weather).
   - **`spicy_facts.json`** — a built-in database of verified adult-rated facts (used only when spice="spicy").
   - **Google (optional)** — with your own API key + search-engine ID, searches
     the web with `safe=off` for adult-rated local stories.
   - **Optional LLM** — a local Ollama model or your own API key, to rewrite the real facts into punchy adult-humor one-liners.
4. Results are cached for a while, so repeat questions don't spam the APIs.
5. **Multiple facts per place** — asking `!funfact` for the same town a few times rotates through different facts, so you don't get the same answer twice in a row.
6. **Length cap** — facts are cut to `max_fact_chars` (default 200) on a word boundary.

No pip packages required — Python standard library only.

## Requirements

- Python 3.8 or newer
- A Twitch account for the bot (your own or a fresh one)
- A free Twitch "app" Client ID — **one-time setup**, needed only because Twitch
  requires an app registration for every OAuth login (see step 2).

## Setup

### 1. Get the code

```
cd funfact-bot
```

### 2. Get a Client ID (one time, ~2 minutes)

Twitch requires every app to register before it can let someone log in. This is
a free one-time step — after it, the bot logs itself in and you'll never touch it again.

1. Go to <https://dev.twitch.tv/console/apps>
2. Log in with your normal Twitch account, then **Register Your Application**
3. Name: anything, e.g. `FunFact Bot`
4. **OAuth Redirect URLs**: `http://localhost` (not actually used by this flow,
   but the form needs a value)
5. Category: *Chat Bot*
6. **Create**, then copy the **Client ID** (and optionally the Client Secret —
   only needed if you ever want to revoke the bot's login from the dashboard).

### 3. Configure

```
cp config.example.json config.json
```

Edit `config.json`:

```json
{
  "nick": "your_bot_account_name",
  "oauth_token": "",
  "client_id": "paste_your_client_id_here",
  "client_secret": "optional",
  "channel": "#your_channel",
  "prefix": "!",
  "cooldown_seconds": 5,
  "spice": "spicy",
  "max_fact_chars": 200,
  "max_message_chars": 450,
  "fact_prefix": "FunFact",
  "respond_only_to": [],
  "llm_api_key": "sk-or-v1-paste_your_openrouter_key_here",
  "llm_base_url": "https://openrouter.ai/api/v1",
  "llm_model": "nousresearch/hermes-4-70b"
}
```

> **Which LLM provider?** The three `llm_*` fields above are set for
> **OpenRouter** (recommended for adult-aligned facts). The same three fields
> are all you change to switch provider:
>
> **OpenRouter** — low-refusal models, fractions of a cent per fact:
> `"llm_api_key": "sk-or-v1-..."`, `"llm_base_url": "https://openrouter.ai/api/v1"`,
> `"llm_model": "nousresearch/hermes-4-70b"` (or `sao10k/l3.3-euryale-70b`)
>
> **Groq** — free but filtered (tame for spicy mode):
> `"llm_api_key": "gsk_..."`, `"llm_base_url": "https://api.groq.com/openai/v1"`,
> `"llm_model": "openai/gpt-oss-120b"`
>
> **NVIDIA NIM** — free developer key, 100+ models, ~40 req/min:
> `"llm_api_key": "nvapi-..."`, `"llm_base_url": "https://integrate.api.nvidia.com/v1"`,
> `"llm_model": "openai/gpt-oss-120b"`
>
> **Google AI Studio (Gemini)** — free per-project quota, strongest free
> models: `"llm_api_key": "..."`,
> `"llm_base_url": "https://generativelanguage.googleapis.com/v1beta/openai"`,
> `"llm_model": "gemini-3.8-flash"`
>
> **Local Ollama** — unfiltered, no key needed:
> `"llm_api_key": ""`, `"llm_base_url": "http://localhost:11434/v1"`,
> `"llm_model": "llama3.1:8b"`
>
> See the *LLM writer* section below for setup.

| Key                | Meaning                                                        |
| ------------------ | -------------------------------------------------------------- |
| `nick`             | The bot account's username (lowercase).                        |
| `oauth_token`      | Leave blank — the bot fills this in automatically after login. |
| `client_id`        | Client ID from step 2.                                         |
| `client_secret`    | **Needed to stay logged in** unless your app is a Public client. |
| `channel`          | Channel to join, e.g. `#your_channel`.                         |
| `prefix`           | Command prefix (default `!`).                                  |
| `cooldown_seconds` | Minimum seconds between lookups (default 5).                   |
| `spice`            | `"clean"` or `"spicy"` — adult-rated facts (default `clean`).  |
| `max_fact_chars`   | Max length of the fact itself (default 200).                   |
| `fact_source`      | `"sources"` (default) or `"llm"` — see [Where facts come from](#where-facts-come-from). |
| `llm_api_key`      | Groq/OpenRouter/OpenAI-compatible API key (leave empty for local Ollama). |
| `llm_base_url`     | LLM API base URL (default `https://api.groq.com/openai/v1`; Ollama = `http://localhost:11434/v1`). |
| `llm_model`        | LLM model (default `openai/gpt-oss-120b`; Ollama e.g. `llama3.1:8b`). |
| `serper_api_key` | **Recommended.** Serper web-search key (free tier 2,500 queries). |
| `weatherapi_key`   | Optional [weatherapi.com](https://www.weatherapi.com/) key (free tier 1M calls/month). Set it and weather questions are answered as one sentence to the asker — see [Weather](#weather). Empty = the keyless Open-Meteo reply. |
| `google_api_key`   | Legacy Google Custom Search key (closed to new customers).     |
| `google_cx`        | Optional Google search-engine ID (from programmablesearchengine.google.com). |
| `max_message_chars`| Hard cap for a chat message (Twitch caps ~500; default 450).   |
| `fact_prefix`      | Text before the fact, e.g. `FunFact` or `FUN FACT`.            |
| `fun_commands`     | Enable extra entertainment commands (`true`/`false`).          |
| `respond_only_to`  | Optional list of usernames allowed to use the command. Empty = everyone. |

You can also use environment variables instead of the file:

```
TWITCH_NICK=bot_name TWITCH_CLIENT_ID=your_id TWITCH_CHANNEL=#channel python3 bot.py
```

### 4. Run it (and log in)

```bash
python3 bot.py          # macOS / Linux
python bot.py           # Windows (or just double-click start-bot.bat)
```

**On Windows** you can double-click **`start-bot.bat`** — it finds Python, runs
from the bot's own folder, and restarts the bot automatically if it ever
crashes (press `N` at the prompt, or Ctrl+C, to stop). For verbose LLM logging
use **`start-bot-debug.bat`** (same as `python bot.py --debug`).

On first run (and whenever you change the Client ID), the bot walks you through
a login:

```
================================================================
  TWITCH LOGIN
================================================================
  A browser window should open. If it doesn't, open this URL:
    https://www.twitch.tv/activate?device-code=ABCD-EFGH

  Code: ABCD-EFGH   (expires in 30 min)

  1. Log in to the account the BOT should use
  2. Click 'Authorize'
================================================================
  Waiting for authorization.....
```

Just click the link (it opens automatically), make sure you're logged in as the
account the bot should use, and hit **Authorize**. Done — the bot saves the
login to `tokens.json` and joins your chat.

After that, every run reuses the saved login and refreshes it automatically, so
you never have to log in again. Use `python3 bot.py --login` to switch accounts.

While the bot is running it also keeps the token alive on its own: a keeper
thread refreshes the OAuth token every 30 minutes and before every reconnect,
so the login never expires mid-stream (Twitch access tokens are short-lived;
the saved refresh token renews them silently).

That's it. The bot joins chat and starts answering `!funfact <place>`.

### Keeping the log

The console window IS the bot's log — but it scrolls away and dies with
the window. Set `"log_file": "bot.log"` in `config.json` and everything
the console shows (including crash tracebacks) is also appended to that
file. Watch it live in PowerShell:

```
Get-Content .\bot.log -Wait
```

## Running it 24/7

The bot is a tiny standard-library Python script — the easiest way to keep it
always-on (so it answers chat even when your PC is off) is a small free cloud VM
running it under `systemd`. A complete step-by-step guide (host options,
GitHub setup, device login over SSH, systemd unit, logs) is in
**[`deploy/DEPLOY.md`](deploy/DEPLOY.md)**, with ready-made
`deploy/funfact-bot.service` and `deploy/bot.env.example` files. Short
version: Oracle Cloud's Always Free VM is the free host that fits (the bot
needs a disk that survives restarts and a process that never sleeps, which
rules out the "free web app" platforms); Google Cloud's `e2-micro` is the
runner-up; the guide's Step 0 table has the 2026 fine print.

### The admin panel

Once the bot lives on a server there is no console window, so the bot can
serve one: a small password-protected web page (`adminpanel.py`, standard
library only, running inside the bot process) with a dashboard (connected or
not, uptime and build, whether the bot is a mod, the AI provider and which
models are resting, Twitch token health), the live log with a filter, the
`!bot` / `!so` / `!cb` / `!beef` switches, speak-as-the-bot, the standing
notice, reminders, custom commands, haul, sub goal and voice, viewer memory
(view and `!forget`), a `config.json` editor with masked secrets that applies
live where it can and says "restart" where it cannot, panel users, a Twitch
re-login (the same device code flow, shown in the page) and a Restart button.

```bash
python3 bot.py --admin-user yourname               # set a password (10+ characters)
python3 bot.py --admin-user modname --role mod     # optional moderator login
# config.json: "admin_panel_enabled": true   ->  restart  ->  http://localhost:8477
```

Two roles: **admin** does everything; **mod** gets the switches, chat,
reminders, commands and the stream tab but never config, secrets, memory,
users or restart. Logins are salted PBKDF2 hashes in `admin_users.json`
(gitignored), sessions are `HttpOnly; SameSite=Strict` cookies that expire
after 12 idle hours, five wrong passwords lock a name for 15 minutes with a
one-second cost per miss, every state change is a POST with a per-session
CSRF token, and every action lands in the bot's log as `[admin] user: …`.

The panel listens on `127.0.0.1` by default and **refuses** `0.0.0.0` or a
public address unless `admin_panel_public` is set as well. On a server, reach
it over Tailscale (bind it to the VM's `100.x` address) or an SSH tunnel -
`deploy/DEPLOY.md` Step 7 walks through both.

Quick-and-dirty local alternatives:

- **`nohup`** — `nohup python3 bot.py >> bot.log 2>&1 &`
- **`screen` / `tmux`** — so you can reattach to it later.

  Then `sudo systemctl enable --now funfact-bot`.

## Chat commands

| Command               | Result                                                        |
| --------------------- | ------------------------------------------------------------- |
| `!funfact Milford, PA`| Posts a fun fact about the location. `!funfacts` also works.  |
| `!funfact` (no arg)   | Posts usage instructions.                                     |
| `!joke`               | A random joke (free API).                                     |
| `!randomfact`         | A random useless fact (free API).                             |
| `!riddle`             | A riddle; the answer is posted ~20s later.                    |
| `!wouldyourather`     | A "would you rather" question (also `!wyr`).                  |
| `!smk female`         | Shag, marry or kill — also `male` or `any` (default).         |
| `!haul`               | What the truck is hauling right now.                          |
| `!whois Aubrey Plaza` | Who that person is, in Wikipedia's own words.                 |
| `!twitch hardclaws`   | Who that Twitch channel is.                                   |
| `!cb`                 | The bot talks on the radio, or yells at a car (also `!radio`).|
| `!so <name>`          | Shout a channel out. **Moderators only.**                     |
| `!so off` / `!so on`  | Moderator switch for raid shoutouts.                          |
| `!cmd add <n> <msg>`  | Create your own command. **Moderators only.**                 |
| `!cmd list`           | The commands this channel has defined. Anyone may read it.    |
| `!reminder 60mins …`  | Post a message later. **Moderators only.**                    |
| `!help`               | Lists the commands and who may use them.                      |
| `!bot off` / `!bot on`| Moderator kill switch for every command.                      |
| `!ask anything`       | The bot answers in its own voice (see the chat AI below).     |
| `!subgoal`            | The sub goal and how many subs to go (mods maintain it).      |
| `!ban <name> [reason]`| Bans somebody. **Moderators and lead mods only.**             |
| `!timeout <name> [10m] [reason]` | Times somebody out (default 10 min). **Mods only.** |
| `!unban <name>`       | Lifts a ban, or ends a timeout early. **Mods only.**          |

Places can be given as `City, ST`, `City, Country`, a landmark, etc. —
whatever you'd type into a search box. The extra commands come from free,
keyless APIs and can be disabled with `"fun_commands": false`.

## Moderation on request — !ban, !timeout, !unban

The bot is a moderator of the channel, so a moderator can simply tell it
what to do. It works typed in chat, and a private message to the bot works
too when Twitch delivers one:

```
Leadmod: !ban spambot123 posting links
Docbot:  @Leadmod spambot123 banned (posting links)
Leadmod: !timeout chatty 10m caps
Leadmod: !unban spambot123
```

**Who may ask.** In the channel the badges decide, and Twitch's *lead
moderator* role counts — that role replaces the `moderator/1` badge with
`lead_moderator/1`, so a bot that only looks for `moderator` silently
ignores it. A private message carries no channel badges at all
(moderator is a channel role), and a bot account cannot read another
channel's moderator list — Helix requires `broadcaster_id` to be the
token's own user id — so for a private ask the channel's own list is the
authority: the broadcaster, the bot, and whoever is named in
`"mod_logins"`. Anyone else is refused, and refused privately.

**What Twitch needs from you.** Chat moderation commands went away from
IRC on 18 February 2023 — `PRIVMSG #chan :/ban somebody` is accepted and
does nothing — so this is Helix: `POST /helix/moderation/bans`, with
`moderator_id` set to the bot's own user id, which is why the bot has to
be a moderator. That needs the `moderator:manage:banned_users` scope, and
`user:manage:whispers` to answer a private ask privately. **Both are new
scopes, so run `python3 bot.py --login` once after upgrading** — a token
issued before them will get a 401, and the bot says exactly that instead
of failing quietly. `python3 bot.py --doctor` lists the scopes the stored
token actually has.

**A private message is best-effort, and here is the honest reason.**
Twitch delivers whispers to bots through EventSub (a webhook endpoint),
not through the IRC connection this bot uses, and it stopped letting bots
*send* whispers over IRC on the same day the chat commands went. So the
bot accepts a `WHISPER` line and a `PRIVMSG` addressed to itself if one
ever arrives, answers through the Helix whisper API, and never posts the
answer in the room — but if nothing arrives, nothing happens. The
in-channel command is the reliable path; the whisper is a convenience.
Sending whispers also needs a **verified phone number** on the bot's
account, and Twitch allows 40 unique recipients a day.

**Guard rails.** The bot will not ban the broadcaster, itself, or anyone
on `mod_logins` (Twitch refuses those anyway, and says so in words rather
than a bare `400`). A name has to look like a Twitch login
(`[a-z0-9_]`) before anything is sent, so a stray sentence never reaches
the API as a name. Lengths are `30s`, `10m`, `2h`, `1d` or a bare number
of seconds, capped at Twitch's 14 days. Every action — and every refusal
— is logged with who asked, and moderation still works while `!bot off`
has the rest of the commands paused. Turn the whole thing off with
`"mod_commands_enabled": false`.

## The chat AI: !ask, replies and chime-ins

`!ask` itself is a core command like `!funfact` and `!whois`: always
available, whatever the config says. The autonomous half — replies,
chime-ins, quiet-room openers, the memory — is off by default; flip
`"chat_ai_enabled": true` in `config.json` to turn it on. The `llm_*`
fields (any provider, including local Ollama) supply the personality;
everything else works without a key.

**A second provider for chat and the fact engine.** Groq's free tier rate-limits
mid-stream (HTTP 429), and both the chat voice and sourced question
answers used to go quiet for the two-minute breaker window. Fill the
`llm_fallback_*` fields in `config.json` and a second provider carries
chat replies, sourced answers, fact rewrites and summaries until the
window clears. The console announces the switch once per outage, both
providers are warmed at startup, and each has its own breaker so a dead
fallback key never takes the primary down. Startup also prints
`fallback READY`, `OFF`, or `MISCONFIGURED`; a misspelled or missing
fallback field can no longer fail invisibly. `READY` at this first line means
the three config fields form an endpoint; the background probe then prints a
second `fallback READY` only after a real answer, or `fallback NOT READY` with
the provider's actual code and message. The common
`llm_fallback_api_key` spelling is accepted as an alias for
`llm_fallback_key`.

For zero-cost OpenRouter fallback, use a key from openrouter.ai/keys and a
specific current model slug ending in `:free`. The bot uses exactly that model:
it does **not** substitute `openrouter/free`, whose random selection can mix
reasoning and non-reasoning models with incompatible response behaviour, and
it never adds a paid model to the configured second-provider path. Free slugs
rotate and shared capacity can fill, so recheck openrouter.ai/models when the
warm-up reports `fallback NOT READY`. Account-wide free-tier limits still
apply. A local Ollama fallback is another zero-cost option with no hosted
quota. An empty chat reply — or one
cut off mid-sentence ("If they try to slash wages, I'll") — is retried
once at a doubled thinking budget before the second provider takes the
line.

**Rate limits are per model, and the chain walks.** Groq's free tier
meters each model separately (`openai/gpt-oss-120b` and
`openai/gpt-oss-20b` each get their own 8k tokens a minute and 200k a
day), and a `tokens per day (TPD)` 429 means *hours*, not two minutes.
Live-fire the day's gpt-oss budget was gone before the stream started,
the whole provider was parked for two minutes at a time, and every line
went to a slow free model on OpenRouter while a fresh Groq bucket sat
unused. Now a 429 rests **that model** for as long as the error says (a
TPD message until the quoted reset, capped at six hours; a per-minute
limit for two minutes) and the line moves on at once: first to the same
provider's spare (`openai/gpt-oss-20b` on Groq — same key, faster,
separate budget), then down the fallback chain.

**Retired slugs are retired for the session.** Groq shut down
`llama-3.3-70b-versatile` and `llama-3.1-8b-instant` for free/developer
keys on 16 Aug 2026 (`qwen/qwen3-32b` and Llama 4 Scout a month earlier).
Live-fire the bot's spare was still the 70B: after every gpt-oss 429 it was
tried again, 404'd again, fired the *"check your llm_model slug"* hint for
a model the config never named, and only then went to the 30-second free
fallback. Now a 404 — or Groq's `400 model_decommissioned` — on any
primary-chain model rests it for six hours with one line naming the
replacement (`… does not exist on this provider (HTTP 404) - Groq retired
it; its replacement is openai/gpt-oss-120b. Skipping it for the rest of
the session`), the `llm_model` hint fires only when the *configured* model
is the dead one, and a `config.json` still naming a retired slug is told
so at startup, with the replacement. `llm_fallback_model` accepts a
**comma-separated list**, tried in order; each model rests on its own
clock and a resting model is skipped, not waited for. Only when every
model of a provider is resting does that provider's breaker open. The
log says which model rested and why (`gpt-oss-120b rate-limited (HTTP
429): … tokens per day … - resting it for 127 min; other models carry
on`) and which one took over (`trying nex-agi/nex-n2.5-pro:free instead`).

A recommended free chain, as of September 2026:

```json
"llm_fallback_key": "sk-or-...",
"llm_fallback_model": "nvidia/nemotron-3-super-120b-a12b:free, nex-agi/nex-n2.5-pro:free, cohere/north-mini-code:free"
```

**A whole chain of providers, not just one.** One second provider is
still one more key that can be dead, out of credits or rate-limited at
the exact wrong moment, so `llm_fallback_providers` takes an *ordered
list* — each entry its own base, its own key, its own model chain and
its own rest window. A line is offered to them in order until one
answers; a provider whose key is rejected is skipped for the session
and the next one is asked instead, so no single dead key can mute the
room.

```json
"llm_fallback_providers": [
  {"base_url": "https://integrate.api.nvidia.com/v1",
   "key_env": "NVIDIA_API_KEY",
   "model": "openai/gpt-oss-120b, nvidia/nemotron-3-super-120b-a12b"},
  {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
   "key_env": "GEMINI_API_KEY",
   "model": "gemini-3.8-flash, gemini-3.5-flash-lite"},
  {"base_url": "https://openrouter.ai/api/v1",
   "key_env": "OPENROUTER_API_KEY",
   "model": "nvidia/nemotron-3-super-120b-a12b:free, cohere/north-mini-code:free"}
]
```

`key_env` names the environment variable that carries the key, so the
secret stays in `bot.env` and is never written into `config.json` (an
inline `"key"` works too, and the admin panel shows it masked either
way). The old `llm_fallback_key` / `_base_url` / `_model` trio still
works unchanged and is always tried first, so nothing about an existing
setup has to move.

| Provider | Base URL | Key | Free tier (Sept 2026) |
| --- | --- | --- | --- |
| Groq | `https://api.groq.com/openai/v1` | `gsk_...` | 30 RPM, 1K req/day, 8K tokens/min **per model** — fast, but the minute budget is the weak spot |
| NVIDIA NIM | `https://integrate.api.nvidia.com/v1` | `nvapi-...` (phone, no card) | ~40 RPM shared across every model, no daily token cap; hosts the same `openai/gpt-oss-*` models |
| Google AI Studio | `https://generativelanguage.googleapis.com/v1beta/openai` | from `aistudio.google.com/apikey` | free per-project quota; Gemini 3.x Flash ≈ 10 RPM, Flash-Lite ≈ 15 RPM, resets midnight Pacific |
| OpenRouter | `https://openrouter.ai/api/v1` | `sk-or-v1-...` | `:free` slugs at 20 RPM; 50 req/day, or 1,000/day after a one-time $10 purchase |

Two of these providers need their own request shape, which the bot now
handles per endpoint: NVIDIA NIM validates a body against each model's
schema, so it gets `max_tokens` (the newer `max_completion_tokens` is a
422 there) and its 202 "still working" answer counts as a miss rather
than a reply. Gemini 3.x always thinks before it answers —
`reasoning_effort: "none"` only switches thinking off on the 2.5
family — so the bot sends it the reasoning budget and low effort, the
same way it does for `gpt-oss` and Nemotron, or the thinking eats the
answer. A key in the environment is enough on its own: `NVIDIA_API_KEY`
or `GEMINI_API_KEY` in `bot.env` adds that provider with these
defaults, no config edit.

`nemotron-3-super` answers in under a second where the 550b `ultra`
takes 20–30 s a line; `nex-n2.5-pro` and `north-mini-code` are
non-reasoning and fast. Nemotron is a hybrid reasoning family, so the
bot now sends it the same low-effort thinking budget as gpt-oss instead
of letting it narrate its reasoning at the room. Two caveats worth
knowing: OpenRouter allows only **50 free requests a day** on an account
that has never bought credits (a one-time $10 purchase lifts that to
1,000 a day, permanently, and the credit also covers cheap paid models
like `nousresearch/hermes-4-70b` as a non-free last resort), and the
`:free` roster rotates monthly — recheck openrouter.ai/models when
warm-up says `fallback NOT READY`. Groq's other free buckets
(`qwen/qwen3-32b`, `meta-llama/llama-4-scout-17b-16e-instruct`,
`moonshotai/kimi-k2-instruct`) are also separate daily budgets on the
same key: put them in `llm_model` as a list (`"openai/gpt-oss-120b,
qwen/qwen3-32b"`) and the primary walks them the same way.

- **`!ask anything`** — factual questions ("what is a bongo twist",
  "how many trailers can a truck pull") are answered by the fact engine
  FIRST — the persona will guess on trivia it doesn't know, and a
  grounded answer beats a charming guess. Live data gets its own header
  because it is not trivia. Sunrise/sunset questions are geocoded and
  answered directly from Open-Meteo — for example,
  `Sunrise | Vandalia, Illinois: Sunrise is expected around 6:38 AM local time today.`
  — instead of accepting a search snippet that merely says times are
  local. Weather questions take the same direct-data route — one sentence
  from weatherapi.com when `weatherapi_key` is set, otherwise Open-Meteo's
  `Weather | Marshall, Illinois: Currently 68°F with partly cloudy skies;
  feels like 66°F; humidity 59%; wind WSW at 12 mph.` (see
  [Weather](#weather)). An archive-page snippet such as “weather reports
  from the last weeks” can never become a current-weather answer. When the
  records miner backs a
  superlative question, the article it digs through must actually be
  about the subject (a US freight-lane question once came back with
  Ivory Coast's GDP — the search loved "coat"~"Côte" and "west
  coast"). The persona takes over when
  the engine has nothing, and owns opinions and about-the-bot questions
  ("whats your favorite truck") outright. No LLM key configured? The
  ask falls through to the fact engine's question path, which answers
  real questions from Wikipedia alone. It is not gated on
  `chat_ai_enabled` — it is a command, not the chatter — but while the
  chat AI is off its answers record nothing to memory. And when the
  model is configured but *unreachable* (a retired model slug, a stopped
  Ollama), the question path degrades instead of dying: superlative
  questions still get the concrete record answer from the records
  miner, chatty ones ("how are you today?") get a canned Doc line
  instead of a Wikipedia fact about the word "today", and playful digs
  at the bot ("you have alot of useless facts") get a canned comeback,
  and asked-at-the-bot opinion questions ("are you a Miami Dolphins
  fan?") get a deflection — never silence. Factual questions and
  third-party venting never get a canned line. And every failed
  attempt leaves a line in the log — a mention held by its cooldown, a
  model that returned nothing, a reply the cleaner rejected, an
  unexpected HTTP code — so `bot.log` always shows which one it was.
- **Mention replies** — someone says "doc, ..." (see `chat_ai_names`) and
  the bot answers (a factual question in a mention gets the fact
  engine's grounded answer, same as `!ask`), at most once **per viewer**
  per `chat_ai_mention_cooldown` seconds (60), with a short channel-wide
  floor of `chat_ai_mention_pace` seconds (8) between any two persona
  replies. Viewer wording is not run through
  the bot's *output* profanity filter: a directly addressed question with
  rough language is still answered, while the generated reply still has to
  pass every output rail. Unsafe viewer lines are never retained as ambient
  model context, so they cannot be parroted into a later reply. The cooldown
  keeps the bot from being wound up like a toy — but it is *each person's
  own* minute. Live-fire, one shared 60-second clock meant four people
  asking in the same minute got one answer and three holds, and six of ten
  direct questions in seven minutes were lost; with per-viewer clocks the
  same seven minutes answer nine of the ten (the tenth was the same person
  asking again 13 seconds after her reply). A mention that arrives inside
  the asker's own cooldown is *held*, not dropped — the bot answers it to
  the right person the moment their clock clears (within two minutes; after
  that the moment has passed and answering would be the non-sequitur). The
  held queue keeps one question per person (a repeat replaces the earlier
  one, so nobody gets two answers) for up to eight people, and whichever
  asker is clear first is answered first. Nothing leaves the queue quietly:
  a hold, a full-queue drop and a worker-side drop each write a log line
  naming the person (`held-question queue full - dropped kvack's …`). And a
  reply that comes back unusable — cut off mid-sentence, too long — is
  re-asked once; a safe overlong answer is then fitted at a complete boundary,
  or the bot posts an honest retry acknowledgement. A direct question is
  never left dangling, and a late answer always answers the message that was
  actually sent.
  Channel-stats questions are answered straight from Helix: "docbot,
  how many follows this stream?" gets the live follower total plus how
  many are new since the bot came online (the baseline the startup
  probe fetches). And a question about a *person* — anyone @-mentioned
  or anyone the bot holds memories of — is answered from those
  memories, never routed at the encyclopedia.
  Several people asking in one window are queued (up to three) and
  answered in order, each to their own asker.
  The streamer's own lines never
  trigger chime-ins — he has the floor — but directly addressing the bot
  by name does get a reply.
- **Contextual chime-ins** — these are for *light* conversation, never a
  busy room. A moment must win a `chat_ai_chance` roll (default 0.10),
  the room must have at least `chat_ai_min_chat` recent human messages,
  and `chat_ai_cooldown` seconds (default 600) must have passed since the
  last unprompted attempt. If `chat_ai_busy_messages` human lines arrive
  inside `chat_ai_busy_seconds` (defaults: 4 in 30 seconds), chat has the
  floor: the bot stays silent unless someone asks or addresses it. Bot
  echoes, commands, unsafe text and emoji walls do not create an excuse
  to chime.

  The model is told that the message was NOT to it and may speak only if
  it has something specific to add. An overheard question is never
  answered with a FunFact — the fact engine answers only questions
  *addressed* to the bot. Every chime must be **about the human message
  it jumps on**: the result has to carry that message's subject, and is
  declined otherwise. Generic cadence, coffee, trucking, motivation or
  workout filler does not count just because it matches Doc's persona.
  A declined attempt still starts the cooldown, so a poor model cannot
  hammer the room or the API. Chime-ins are accents (default autonomous
  cap 6 an hour), not a second voice in the conversation.
- **One contextual follow-up per lull** — a safe ordinary human
  conversation can arm one follow-up. After
  `chat_ai_quiet_seconds` (default 300) of silence, the bot may continue
  that latest topic; it may not invent a generic icebreaker. The attempt
  consumes the latch whether it speaks or declines. Continued silence
  therefore never becomes a repeating timer: a new ordinary human
  conversation must happen before another lull can be armed. Startup
  silence is not armed, resumed chat cancels queued quiet work, and
  `chat_ai_quiet_cooldown` (default 900) remains an additional backstop.
- `chat_ai_max_hour` limits autonomous chime-ins and quiet follow-ups,
  not directly addressed questions. Mentions use their own clock and
  remain reliable even when the room is flowing or the autonomous cap
  is full. `!cb off` still silences all autonomous chat AI.
- **`!cb off`** silences it for the session, like the other chatter.

What it will never do, by prompt *and* by output filter: tease people
(only topics), post insults, threats or anything creepy, joke about
illness or grief, state facts it is not sure of (it answers general
knowledge it knows — *how long does a 5K take* — and lookups on named things
and live data are routed to the fact engine first), guess anything personal
about a viewer, or post links,
@mentions or more than one emoji, no command syntax (the persona never
tells viewers to go use `!funfact` — it answers itself or says nothing).
It also cannot repeat itself: its own recent lines are named in the
prompt, and an answer that recycles a phrase, distinctive signature or
most of a recent line is declined. "One emoji" is counted the way a
person sees it: a shrug with a skin tone and a gender sign
(`🤷🏻‍♂️`, four code points) or a flag is *one* — live-fire the cleaner
counted the code points, called a good line a two-emoji violation and
threw it away. Ordinary topic words are exempt when
the viewer just used them — mentioning `cadence` again while answering a
cadence question is not repetition. A directly addressed answer gets one
"say something completely different" retry before a deterministic
failure line; autonomous chatter simply declines. That blocks actual
repeated templates without making real questions silently disappear.
A model with nothing worth saying replies `NOTHING TO SAY` and the bot
stays quiet. The streamer's own messages never trigger autonomous chat:
he already has the floor, though directly addressing the bot still gets
an answer.

The voice is `bot_personality` in `config.json` — your words, your
rules — and the built-in default is Doc: a dry-witted old trucker who
has been everywhere twice.

### "Docbot sing me a song" — performances over several messages

Some asks are for a *piece*, not a line. `Docbot sing me a song`,
`doc make me a poem about kvack`, `docbot tell us a story`, `doc rap for
us`, `give us a limerick about the load`, `do a haiku on coffee`, `make a
toast to hollie` — pushed through the one-line reply path, the model wrote
a sentence *about* singing ("Sure, here's a little ditty about…") and
stopped. It rambled and never did the thing.

Now a performance is recognised as one, written whole (the model gets a
bigger completion budget for it), checked **line by line** against the same
rails as every other chat line, and posted the way a person would deliver
it: the first line at once, `@`-tagged to whoever asked, the rest a few
seconds apart, so chat can react between lines and it reads like a genuine
exchange rather than a wall. `!ask sing me a song` is the same request.

```
kvack: docbot sing me a song about the night shift
Bot:   @kvack Rolling down the I-80 line,
Bot:   Coffee's cold but the load's on time,           (4 s later)
Bot:   Oh the night shift hums, the night shift glows, (4 s later)
Bot:   Where the diesel goes, nobody knows.            (4 s later)
```

- **What counts.** A song (tune, ditty, ballad, shanty, lullaby…), poem
  (verse, sonnet, rhyme), rap (bars, freestyle), limerick, haiku, story,
  or toast — asked for with `sing / write / make / do / give / tell / drop /
  spit / recite …`, a bare `can you sing` / `rap for us`, or just `one more
  song` / `another poem`. A subject after `about / on / for / to` is used
  (`SUBJECT: the night shift` in the prompt); with none, the model is told to
  draw on what is on screen or in chat — never on itself. `doc encore!`
  repeats the last piece for whoever asked.
- **What does not.** Questions about real pieces (`who sang that song`,
  `what's the story with the lights`, `tell me the story of Route 66`) keep
  their grounded paths; someone narrating their own day (`I wrote a song
  yesterday`, `we're gonna sing later`) is not a request. And the bot performs
  **only when addressed** — two viewers discussing karaoke never make it
  break into song.
- **The rails hold per line.** No `@`, no links, no hashtags, no command
  syntax, nothing explicit, at most one emoji in the whole piece. Padding
  (`Sure! Here's a song:`, `Verse 1:`, `[Chorus]`, `Hope you liked it!`,
  numbering, quotes, code fences) is stripped; a broken rail anywhere is a
  refused piece, retried once with the miss named, and then an honest line in
  character (`Voice is shot tonight - the singing will have to wait.`) — never
  half a song. No model configured gets the same honest line, not a Wikipedia
  fact about the word "song".
- **Pacing.** `chat_ai_perform_delay` in `config.json` is the gap in seconds
  between lines. Empty (the default) means the same gap as `beef_act_delay`;
  `0` posts the whole piece at once. A performance counts as the mention reply,
  so the usual `chat_ai_mention_cooldown` applies afterwards.

### Weather

"Docbot whats the weather in wilkes barre, pa" is a live reading, never a
search snippet and never a persona guess. Any phrasing with a place reaches
the data — `docbot weather in scranton?`, `doc hows the weather in paris`,
`!ask weather in miami` — while a remark like "the weather in texas is
crazy" stays ordinary conversation.

- **With `weatherapi_key`** (free at [weatherapi.com](https://www.weatherapi.com/)),
  the answer is one sentence addressed to whoever asked, in the shape the
  channel asked for:

  ```
  Hardclaws, it is currently Clear in Wilkes-Barre, Pennsylvania. 63°F (17°C). Feels like 61°F (16°C). Wind is blowing from the SW at 4 mph (7 km/h). 61% humidity. Visibility: 6 miles (10 km). Precipitation: 0.0 in (0.0 mm).
  ```

  Condition, place as weatherapi.com resolved it (the country is added outside
  the US), temperature, feels-like, wind direction and speed, humidity,
  visibility and precipitation — imperial first, metric in brackets. The
  sentence is never trimmed by `max_fact_chars` (it is data, not prose) and is
  cached for five minutes per place. `WEATHERAPI_KEY` in the environment works
  too.
- **Without a key** — or when the key is rejected, the place is unknown to
  weatherapi.com, or the service is down — the keyless Open-Meteo path answers
  exactly as before: `Weather | Wilkes-Barre, Pennsylvania: Currently 63°F with
  clear skies; feels like 61°F; humidity 61%; wind SW at 4 mph.` Every fallback
  is logged with the reason (`weatherapi.com HTTP 401: API key is invalid -
  falling back to Open-Meteo`). Weather never goes quiet over a key problem.
- **It is instant, and it skips the chat AI's rails.** Live-fire, "Docbot
  whats the weather currently in Brewster, NY" got *nothing*: the bot had
  answered a different "docbot …" 40 seconds earlier, so the 60-second
  `chat_ai_mention_cooldown` held the question — and a held question that
  waits behind someone else's slow model call in the single worker queue
  could be dropped without a log line when its turn came. Those rails exist
  for persona chatter; an API reading has no model in the loop and no reason
  to wait behind one. A weather or sunrise/sunset question addressed to the
  bot (`docbot …`, or the click-to-mention `@TruckingWithDocBot …`) now takes
  a **fast lane**: answered immediately on its own thread, never touching the
  mention clock (so the next persona question is still answered on time),
  paced at one reading per viewer per 15 seconds (mods and the broadcaster
  exempt). Every outcome is a line in the log — `live data answered for …`,
  `live data ask from … paced`, or the engine's own failure reason.

### "Sunrise in Hintok, ok" — the town he meant, not a trail in Thailand

Live-fire: *"Docbot what time is sunrise in Hintok, ok?"* → `Sunrise |
ไทรโยค: Sunrise is expected around 6:13 AM local time today.` Three faults
in one line. The geocoder (Nominatim) has no town called Hintok, so its best
string match was **"Hintok Cut" — a footpath at Hellfire Pass, Thailand** —
and the `, ok` was ignored; it named the place **in Thai** (Sai Yok
district); and nothing asked whether the hit was a *place* at all. The
viewer meant **Hinton, Oklahoma**.

Geocoding now works the way a person reads the question:

- **The region typed with the place is a hard constraint.** `Hintok, ok`,
  `Hintok ok`, `Cuba Missouri`, `Banff, AB`, `Yorkshire united kingdom` — a
  US state or Canadian province also pins the *country*, so a hit on another
  continent is not a near miss, it is a different place and is discarded
  (logged: *matches for 'Hintok, ok' were all outside oklahoma*).
- **A road, shop or trail is not a settlement.** Only a city/town/village/
  hamlet (or an admin area, park or landmark *in the right region*) can
  answer for a place name.
- **Labels come back in English** (`accept-language=en`), and a province
  counts as a region, so the header is `Hinton, Oklahoma` — or `Sai Yok,
  Kanchanaburi Province` if someone really asks for Thailand.
- **A spelling that finds nothing gets a fuzzy match on places** in that
  region, from Photon (komoot's keyless OSM geocoder): `Hintok` in Oklahoma
  → Hinton, `Terra Haute` → Terre Haute, `Scrantin` → Scranton. Only a
  plausible misspelling is accepted (`Red Rock` is *not* "Red Rock Canyon
  State Park", `Hilton` is not `Hintok`), and every correction is logged:
  *geocoder read 'Hintok, ok' as 'Hinton, Oklahoma, United States' (closest
  place by that name)*.
- An exact, real place still costs **one** geocoder call; the fuzzy step
  only runs when the exact spelling finds no place in the region. Photon
  down and nothing found is an honest "couldn't fetch", never the trail.

The same geocoder serves weather (keyless Open-Meteo path), sunrise/sunset,
and the fact engine's tiny-town fallback, so all three inherit this.

### News — "who got into a helicopter crash today in California"

Live-fire, that exact question got `FunFact | who got into a helicopter
crash today 15th September 2026…: The Interstate Aviation Committee (MAK)
investigation found out that the Certificate of Airworthiness of the
aircraft had expired in 2012.` — a Wikipedia sentence about a different
crash on a different continent, fourteen years earlier. The fact engine's
sources are an encyclopedia and a search engine's instant-answer box;
neither knows what happened this morning, and the grounded-answer path did
exactly what it is built to do with the only "helicopter crash" text it
could find.

What *happened* is news, and it now has its own path. A question with a
recency marker (today, tonight, yesterday, this week, latest, breaking, or a
date like "15th September 2026") **and** an event word (crash, died,
arrested, won, earthquake, happened, "going on"…) is a **news question**:

- It is answered from a **headline feed**, not the encyclopedia and not a
  model: Google News' keyless RSS search (seconds fresh, no key, no quota to
  speak of), or Tavily's `topic: news` first when `tavily_api_key` is set.
- The answer is the **headline itself**, verbatim, with its outlet and age:
  `News | helicopter crash California: NBC4 helicopter crashes in Chatsworth,
  killing 3, after deadly Metro bus crash (Los Angeles Times, 3h ago)`. A
  repeat of the question rotates to the next headline (BBC, CBS…), like any
  fact pool; headlines are cached ten minutes, not an hour.
- It takes the **weather fast lane**: answered at once on its own thread,
  no model, no mention clock. `!ask` uses the same path.
- The window follows the question: "today"/"latest" is one day, a named date
  or "yesterday" two days, "this week" seven.
- An empty feed is an honest **`Nothing in the headlines about that in the
  last 2 days.`** — never a 2012 fact dressed as an answer — and a feed
  outage says so and retries in a minute.

Weather and sunrise keep their own paths ("whats the weather today" is
weather, not news), opinions aimed at the bot ("who's the best QB today")
stay with the persona, and plain trivia ("who won the 1998 World Cup") stays
with the encyclopedia.

**"Whats the leading headlines for today"** — live-fire, that got `News |
leading headlines: top news of the day september 16 2026 (thehindu.com,
17h ago)`. Two things went wrong. The words *leading headlines* were
treated as the **subject** and searched, and the best match for a search
about headlines is a roundup page — a headline *about* headlines. Then the
follow-up, *"that is not a headline where the news"*, had no "today" in
it, so it was not a news question at all and went to the persona, which
made a joke about not being a headline.

Now a headline ask with **no subject** — *whats the news, headlines?, top
stories, whats the leading headlines for today, whats going on in the
world, what happened in the news today, where the news* — is answered
from Google News' **top-stories feed** (the front page as RSS, no query,
no key), or Tavily's news topic when the feed is down:

- **Three real stories per message**, each with outlet and age, packed to
  the message budget: `News | top headlines: Fed holds rates steady as
  inflation cools (Reuters, 2h ago) | Hurricane Otis makes landfall near
  Acapulco as Category 4 storm (AP News, 3h ago) | Senate passes stopgap
  funding bill, averting shutdown (CNN, 4h ago)`. Asking again rotates to
  the next three; every phrasing shares one ten-minute cache.
- **Roundup page titles are never quoted** — *Top news of the day…*,
  *Today's top stories*, *Morning briefing: what to know today*, *5 things
  to know today*, *Live updates* — whatever feed they came from. A feed of
  nothing but roundups is an honest "the top-stories feed came back empty
  just now", not a page title.
- The ask needs **no recency word**: "the headlines" are today's by
  definition. Sharing news ("I got news, my truck is fixed"), the bot's
  news ("whats your news source") and "the news said it would rain" are
  not asks.
- Asks **with** a subject — *news on the LA bus crash*, *whats the news in
  Australia today*, *any news on the Big Bend fire* — still search for
  that subject as before. A capitalised *Big*, *Top* or *World* mid-question
  is a name and stays in the search; lowercase ask-words are dropped.
- `news_country` (default `US`) picks whose front page it reads — `GB`,
  `AU`, `CA`…

### "How long does it take to run 5k" — the model's question, not the encyclopedia's

Live-fire, two rounds:

- *"Docbot whats the avg time for someone to run 5k"* → `FunFact | …: Whats
  a good average time to do 5K? : r/C25K.` — a Reddit thread title. The same
  question, asked back.
- *"Docbot how long it take to run 5k home boy?"* → `FunFact | …: The 5K
  run is a long-distance road running competition over a distance of five
  kilometres (3.107 mi).` — a how-long question answered with a distance.

The root cause was the **routing**: both were sent to the fact engine, and
the engine *looks things up* — there is no article to look up for a typical
5K time. The chat model knows the answer and was never asked.

**Routing now.** A general-knowledge question — a duration, rate, typical
value, size, weight, price, temperature, a how-to or a why (*how long does
it take to run 5k, whats the avg time for a 5k, how much does a gallon of
diesel weigh, how often should you change oil, why is the sky blue, how do
air brakes work*) — goes to the **chat model**, whose prompt says what kind
of ask it is: *give the real answer first — the figure, the range or the
reason — in your own voice; if you genuinely do not know, say so; never
invent a number.* The persona rule that used to say "factual questions are
answered elsewhere" now says to answer general knowledge it is sure of. The
fact engine keeps what it is good at: **named things** with an article
(*what is a bongo twist, when was the eiffel tower built, how tall is Mount
Everest* — a capitalised name past the first word is the tell), **records**
(*how many trailers can a truck pull, whats the longest truck*), and **live
data** (weather, sunrise, headlines). Questions about the bot still go to
the persona as before. If the model declines a knowledge question, the
engine still gets its try, under the rules below.

**The engine's own rails**, for when it does answer:

1. **A question is never a source and never an answer**, wherever its `?`
   sits. Forum furniture (`r/C25K`, `| Reddit`, `Posted by u/…`, Quora) is
   dropped before the engine's model ever sees it.
2. **The answer must be the kind of figure the question asked for.** A
   how-long question wants a **duration** ("30 to 40 minutes", "13:10");
   *how far* a **distance**; *how much does it cost* a **price**; *how hot /
   how cold* a **temperature**; *how heavy* a **weight**. A line of the
   wrong kind is rejected on every path — the engine's model answer, the
   Wikipedia records miner and the article-facts path — judged on the
   trimmed line that would actually post. With no such figure in any source
   the engine returns **nothing** (never a shrug that would stand in front
   of the chat model, never the wrong-kind fact). "How long *is* the bridge"
   and "how long *ago*" are not duration questions; "what temperature does
   condensation stop" keeps its standing exemption ("the dew point").

### A thinking model narrating instead of answering

Live-fire 15:30:09–15:30:53: "docbot who is your favorite NFL team" got,
three times in a row, *"The user is asking me (Docbot) who my favorite NFL
team is. I need to answer as the Commentator persona — a British sp…"* —
the model's reasoning delivered as its reply. The cleaner threw it out, but
for **length**; the length recovery nearly posted a trimmed slice of it as
the answer; the retry was told "too long", which was not what was wrong;
and after 44 seconds the viewer got nothing.

Narration is now recognised as narration (`chatai.is_narration`): "The user
X is asking…", "I need to answer as the … persona", "Let me craft a reply…",
"We need to keep it under 200 characters…". Such a reply is named in the log
(`model narrated its reasoning instead of answering - one retry`), the
retry is told *not to narrate* rather than to be shorter, the length recovery
refuses it outright, and a second leak posts the honest failure line instead
of a fragment of reasoning. The persona rules now say it in advance ("Output
ONLY the line itself, spoken in character. Never narrate, plan or explain").
Three leaks from one model in an hour earn a single console line naming the
model — because the durable fix is a config change: put a non-reasoning
model ahead of it in `llm_model` / `llm_fallback_model` (the nemotron
family is the usual culprit; `nex-agi/nex-n2.5-pro:free` on OpenRouter
does not think out loud, and `qwen/qwen3.8-27b` on Groq can be told not to).

### "Your mic is muted" — a mod's announcement stands as a notice

Live-fire: a mod asked "Docbot can you tell every one that @TruckingWithDoc
is currently on the phone so we are in radio silence", the bot answered in
character — and two lines later a viewer said "Your mic is muted" / "I
assume because your codriver is sleeping" and the bot said nothing. Those
lines were not addressed to it, so they were ambient chime-ins: a 10% roll,
five lines of recent chat, and the ten-minute `chat_ai_cooldown` the bot's
own announcement had just started. The one thing it knew for certain, it
kept to itself.

Now an announcement a **mod or the broadcaster** hands the bot — "docbot tell
everyone that ...", "doc let chat know ...", "docbot remind the folks ..." —
is still answered in character, and is also kept as a **standing notice** for
`chat_ai_notice_minutes` (default 20). While it stands:

- anyone who sounds lost about the quiet stream — "your mic is muted",
  "hello?", "can't hear you", "no audio", "is he afk?", "why so quiet" —
  gets `@name heads up: TruckingWithDoc is currently on the phone so we are
  in radio silence`, whether or not they addressed the bot, with **no chime
  roll and no cooldown**. Each viewer is told once per notice, and the
  relayed text drops the `@` so the man on the phone is not pinged every
  time;
- the persona sees the notice in every prompt, so "docbot hows your night"
  is answered by someone who knows the stream is quiet and why;
- "docbot tell everyone doc is back" (or "mic is back on", "unmuted") clears
  it early, so "hello?" is ordinary chatter again.

A plain viewer cannot plant a notice, a story request ("tell everyone about
the time you drove to Alaska") is not one, and `chat_ai_notice_minutes: 0`
turns the feature off. Every notice kept, repeated, or cleared is a line in
the log.

### "Lets do a 3 round cycling quiz" — it hosts, and remembers it is hosting

Live-fire: "Docbot lets do a test run. Topic would be Cycling and lets make
it 3 rounds" → the bot posed round one (the crankset), and then for "lets
get Round 2 going" it asked chat *what the current temperature in Rolla,
Missouri was*, explained that it was "just the on-stream info bot", and
went "mangled in the gears" twice. Separately, "keep count of Dirty Lepages
and we will tell the bot when we spot one" got the count to 1 — and an hour
later the bot had no idea what a Dirty Lepage was.

Both were the same missing thing. Every prompt was built from the last few
*human* lines with the bot's own lines and every earlier ask stripped out
(on purpose — old questions were hijacking new answers), the long-term
memory keeps only what a viewer says about *themselves*, and a mod note
needs the words "take a mental note". Nothing anywhere held **what we are
doing right now**. That is `ongoing.py`, the chat AI's working memory:

- **A game.** A mod or the broadcaster sets one up in plain chat — "docbot
  lets do a test run, topic cycling, 3 rounds", "quiz us on world capitals",
  "trivia time! topic: 80s music, 5 questions". The bot acknowledges it
  (no model) and waits for "start the first round" / "round 1" / "go". A
  round is a **job**, not a chat line: the model is told *your line IS the
  round-2 question*, and what comes back must actually be a question and
  not one it already asked, or it is asked again with the miss named — and
  if it still cannot, the bot says so ("my round 2 question got stuck in the
  gears — say 'round 2' again") instead of posting a stand-in. The question
  is posted as `Round 2 of 3: …`. While a round is open, "docbot what part
  of a bike holds the pedals?" is told to wait for the host rather than
  looked up, and the persona's replies to everything else carry the game
  state and the open question, so "is it the crankset?" gets "the host
  calls time" and never a denial that it is hosting. The host (or a mod)
  calls "whats the answer" / "times up" / "who got it right" — by name or,
  from the host, without — and the model gives the answer as
  `Round 1 answer: …`, naming only people who actually appear in chat. A
  timing ask — "give 30 secs to answer then start round 3 30 secs after
  that" — becomes a cadence the bot runs by itself, one scheduled step at a
  time; "game over" / "cancel the game" ends it; "what round are we on?" /
  "repeat the question" is answered from state by anyone; "another round"
  after the last one extends it. A viewer cannot start a game or call its
  rounds — they are told so, once, in plain words.
- **Counts.** "keep count of dirty lepages" (mods / broadcaster) opens a
  named counter; "dirty lepage!", "spotted one", "+1", "another one", "+2"
  bump it; "scratch that", "false alarm", "-1" take one back; "set the count
  to 5" corrects it; "how many dirty lepages so far?", "whats the count?"
  read it back with when it started and when the last one was; "stop
  counting dirty lepages" closes it with the final number. No model is
  anywhere near a count — it is arithmetic, and the model is the one part
  of the bot that cannot be trusted with it — so it costs nothing and is
  exact an hour later. Bumping is for the mods, the broadcaster and whoever
  opened it unless `chat_ai_count_anyone` is true; asking is for anyone.
  Every open count sits in the persona's prompt, quoted exactly.

Both live in `ongoing.json` (`ongoing_state_path`), so a restart mid-game
is not amnesia. A game nobody has touched for `chat_ai_activity_minutes`
(45) is dropped; counts stay until they are stopped. The admin panel shows
the game and the counts on the dashboard and lets a mod end the game or
drop a count from the Chat tab. Every step, refusal and count change is a
line in the log.

### Changing the voice

Mods can switch the persona at runtime — it reaches the model
immediately and survives restarts (`!persona` is mod-only):

```
!persona            - which voice is active
!persona list       - the voices, one message per crew
!persona set sarge  - switch
!persona set gollum - names are forgiving: "Lot Lizard", "gollum", "Sam", "spin class" all land
!persona custom <12-300 characters describing the voice>
!persona reset      - back to Doc
```

There are twenty-eight built-in voices in four crews (`!persona list`
reads them out one crew per message — the whole library no longer fits
in one Twitch line). From the streamer's own world:
**medic** (airborne combat medic from his army days — calm, clipped,
counting everyone's water bottles like ammo), **cb** (1970s Citizens
Band radio, breaker one-nine, hands out handles and calls the streamer
Driver), **squaddie** (his longtime Warzone/Fortnite drop partner —
callouts, hype comms, zero tilt), **coach** (track-and-trail hype man
for the indoor runs, cycling rides and truck-stop 5Ks), and **cowboy**
(an old-west drift for Red Dead nights — laconic, every line lands).
The roadhouse originals: **doc** (the default long-haul dry wit),
**sarge** (barking dispatcher, loud but never cruel), **rookie** (three
weeks on the job, terrified of geese), **rusty** (shop mechanic, duct
tape and blasphemy), **nightshift** (smooth 3am AM-radio voice). The
roadhouse floor: **flo** (truck-stop diner waitress, forty years of
coffee refills, calls everybody "hon"), **commentator** (a posh British
play-by-play voice treating a treadmill mile and a Warzone drop with
equal gravity — disasters are "regrettable"), and **noir** (a
hardboiled private eye narrating the stream like a case file — short,
hard sentences), and **lotlizard** — an *actual lizard*, a leathery old
iguana who has lived under the fuel-island dumpster for eleven years,
knows every rig by its brakes and sells warm rocks as prime real estate.
The name is the whole joke and it is played dead straight; the prompt
says "you are a reptile and only a reptile — nothing flirty, ever", and
like every voice it sits under the output rails, so it cannot be turned
into the other thing.

The big top — voices that are pure fun: **clown** (Bumper, a honk-honk
birthday-party clown, groan-worthy puns and balloon animals — the
birthday kind, never the sewer kind), **spin** (a maxed-out indoor-cycling
instructor who treats chat like a 6am class — ADD A TURN, every task is
a hill and every hill is yours, capitals in bursts but never a whole
line), **infomercial** (a late-night pitchman who cannot stop selling —
"Tired of MERGING?", but wait, there's more; never a price, a link or a
real brand), **pirate** (a skipper who has taken the semi for a ship and
the interstate for the sea), **butler** (an unflappable English
gentleman's gentleman — a bad drop is "perhaps not our finest hour,
sir"), **grandma** (everyone's grandmother, proud of all of you, worried
whether you have eaten, sharper than she lets on) and **painter** (the
soft-spoken public-TV landscape painter — no mistakes, only happy little
accidents).

Middle-earth and beyond: **yoda** (object first, verb last, nine hundred
years of truck-stop wisdom — do, or do not), **smeagol** (Smeagol *and*
Gollum, arguing inside the same line about the precious — "we helps
them, yes… no! nasty chatses!" — silly and pitiable, never menacing;
`!persona set gollum` works too), **gandalf** (the grey wizard riding
shotgun — counsel, proverbs, and YOU SHALL NOT PASS on the right),
**gimli** (axe, ale, no indoor voice, a running tally against the
elves — "that still only counts as one"), **samwise** (the loyal
gardener, po-ta-toes, "Mister" and "Miss", carrying the snacks),
**legolas** (sees everything first and says so — "they are taking the
hobbits to the weigh station") and **treebeard** (the Ent, hoom hom,
never hasty, halfway through a very long thought and deeply frustrated
by a one-line limit).

Every persona sits under the same hard rules — a voice changes the
flavour, never the rails — and the same 240-character, one-line, one-emoji
output gate; a voice that shouts (spin, gimli) still cannot post a wall
of capitals. The admin panel's Voice drop-down lists all twenty-eight
with the same short tags.

Whichever voice is active — including a custom one — it also always
receives the streamer's story: army veteran, airborne combat medic,
two tours of Afghanistan, driving semi trucks cross-country, truck-stop
5Ks, indoor runs, cycling rides, Fortnite/Warzone/Red Dead nights.
That is what keeps the chimes aimed at what is actually on screen, not
generic chatter.

### The sub goal

`!subgoal` is open to everyone: it shows the current count, the goal,
what happens when it's reached, and how many to go. Mods maintain it:

```
!subgoal set 50 wear a clown costume for a full driving shift
!subgoal add 1     - a new sub
!subgoal sub 1     - a sub lapsed
!subgoal count 37  - sync the number from the dashboard
!subgoal clear
```

The automated half: **every sub, resub and gifted sub the bot sees
announced in chat bumps the count on its own** (`subgoal_auto_count`,
on by default), and the moment the goal is crossed the bot posts the
payoff line — someone gifts the 50th sub and "GOAL REACHED … Pay up."
lands immediately. Community-gift banners are skipped (each recipient
gets their own gift notice, so counting both would double every gift).
It still can't know the true total: Twitch only lets the
*broadcaster's own* token read live sub counts — the bot's token can't,
by API design, moderator or not — and anything it misses while offline
goes unsaid. `!subgoal count` remains the honest sync point;
`add`/`sub` keep it moving between syncs.

### It remembers its viewers

While the chat AI is enabled, one small SQLite file (`memory_db_path`,
default `chat_memory.db`) holds two things: the recent chat log (pruned
after 90 days — raw material, never fed to the model wholesale) and
distilled per-viewer facts. After the bot talks with someone, the model
quietly extracts the durable stuff — work, vehicles, pets, hobbies,
plans, strong preferences — and those facts are injected into its
prompts from then on. That extraction is **paced**: a viewer is
distilled on first contact, and after that only when both
`chat_ai_distill_minutes` (10) have passed *and* they have said
`chat_ai_distill_lines` (4) new lines since. It used to run after every
single reply — a second model call re-reading the same twenty lines,
roughly 40% of the day's token budget spent to learn that someone said
"lol" — which is a large part of how Groq's daily allowance ran out before
the stream began. Same memory, a fraction of the calls. That is what "remembers conversations from any
point in time" actually looks like at channel scale: not recall of every
line, but the handful of facts that make a reply feel personal. Facts
stay attached to their person: the prompt lists them by name with the
instruction that one viewer's fact is never quoted at somebody else —
and a chime has to be grounded in the message it answers regardless of
what anyone's facts inspired.

**Taking a note on request:** "docbot, take a mental note…" or
"remember this…" (moderators; needs `chat_ai_enabled`) stores the rest
of the message as a memory under the person it's about — the
@-mentioned name if there is one, else the speaker — and the bot
confirms with "Noted.". Asking about a person afterwards ("when and
where did @TruckingWithDoc last take a piss?") is answered from those
memories, never routed to the fact engine: a question about a person in
the room is a recall question, not trivia.

Deliberate limits: only public chat is recorded, and only while
`chat_ai_enabled` is true — the feature owns its data. Health details,
politics, religion, finances and anything intimate are never kept
(that rule is in the extraction prompt). Every viewer is capped at 25
facts, oldest first off the end. And `!forget <viewer>` (moderators,
works while the bot is off) erases every memory **and** every logged
line for that viewer, then says how much it dropped.

### Running it on a local Ollama

The whole chat AI runs on a local model — no key, no rate limits, no
per-message cost, nothing leaving the box. If the mini PC already runs
Ollama for something else, the bot shares that instance: point
`llm_base_url` at it and use a model that's already pulled. On a 32GB
mini PC with no discrete GPU (e.g. a UM560 XT), an 8B model at Q4
quantisation answers in a few seconds — fine for a few lines an hour:

```json
"llm_api_key": "",
"llm_base_url": "http://127.0.0.1:11434/v1",
"llm_model": "qwen3:8b",
"llm_no_think": true
```

(`chat_ai_timeout` is optional now: local models self-default to the
full 30s — 8s was tuned for hosted APIs and kept timing out warm local
models, because the chat prompt is the big one and reading it on CPU
runs 10s+. Set the key only to override. Chat replies are also capped
at 120 generated tokens — one line never needs more, and a rambling
model on CPU would otherwise burn the whole budget.)

Three box-specific notes:

- **No double-billing a busy model.** When the chat call times out
  (usually another generation holding the CPU — the webpage sharing the
  Ollama), `!ask` does not stack a second, *bigger* model call on top:
  the question path carries the sources, so it would just burn another
  full timeout before the records answered anyway. It skips straight to
  the keyless paths, and the question path itself reads fewer sources
  (5 instead of 8) with a 30s budget when local.
- **The hard switch.** `/no_think` in the prompt is only a soft
  request — qwen3:4b ignored it outright and thought anyway, and every
  capped generation died inside the think block (stripped to an empty
  reply, warm-up included). With `llm_no_think: true` on a local
  Ollama, the bot also sends the engine-level `think: false`, which the
  model cannot overrule. Hosted providers never see the field.
- **The empty think block.** Qwen3 opens its reply with an empty
  `<think></think>` even with `/no_think`, and a generation cap counts
  the stripped block's tokens — a tight cap can cut the answer out
  entirely (that's the `warm-up got an empty reply` line). Think blocks
  are stripped from every reply on every provider, and the warm-up
  retries with a generous cap before reporting failure.
- **Prompt size.** On CPU the model reads every token of the prompt
  before writing a word — that read, not the generation, is what blows
  past a 20s timeout on a *warm* model. For local models the bot sends
  a smaller room (8 chat lines instead of 15) and fewer memories (4
  instead of 8); hosted APIs keep the full context, it costs them
  nothing.

- **`llm_no_think: true`** matters with Qwen3-family models. They
  "think" before answering, and on CPU that turns a one-line reply into
  a half-minute stall — every timeout goes off and the bot goes quiet.
  The switch makes them answer directly. No effect on other models.
- **Warm-up.** The bot sends one tiny request at startup (in the
  background) purely to load the model: a cold 8B load takes 10–25s,
  longer than any chat timeout, and without this the first line of chat
  after every restart dies with a timeout. Watch for
  `[llm] warm-up OK - qwen3:8b …` in the log after the join.
- **Keep-alive.** Ollama unloads an idle model after a few minutes, and
  the bot asks maybe six times an hour — so it would often arrive to a
  cold 10–20s load. Raise the service default so the model stays
  resident (RAM is cheap here): `systemctl edit ollama` →
  `Environment=OLLAMA_KEEP_ALIVE=30m`. If another app on the box passes
  its own short keep-alive per request, the service default still
  governs everyone else.
- **Sharing with another app.** Concurrent requests queue inside
  Ollama (parallel is enabled by default on 0.33+), so a long
  generation for the other app can add a little wait — the timeouts
  absorb it. `qwen3:4b` is the fast alternative if lines ever feel
  sluggish, at the cost of a second model resident in RAM.

The architecture is provider-agnostic — swap back to a hosted key any
time by editing the same three fields.

## Where facts come from

`"fact_source"` picks one of two engines:

**`"sources"` (default)** — find real facts first, then rewrite them. Wikipedia
(and optionally DuckDuckGo / Google / Serper) supply sentences about the place;
the LLM only *rewords* what was found. Every line is checked before it reaches
chat: no invented names or dates, no invented claims, no county story handed to
the town. Downside: a small town's article is often thin, so the answer can be
plainer than you'd like.

For the place's own article the bot reads the **whole article**, not the lead.
MediaWiki's TextExtracts API caps `exchars` at 1200 and silently clamps anything
larger, so asking for 4000 returned the first 1200 characters — the lead and a
little of the history. Almost everything a town is actually known for sits below
that line: Cuba, Missouri's *World's Largest Rocking Chair*, its Bette Davis and
Amelia Earhart visits and the Wagon Wheel Motel are all further down. A
single-title request with no `exchars` returns the entire article, which the bot
then trims to `_EXTRACT_CHAR_CAP` (20,000 chars) and ranks.

**`"llm"`** — skip retrieval entirely and ask the model for interesting facts
from its own knowledge, capped at `max_fact_chars`. One prompt, no sources, no
ranking — much simpler, and for well-documented places it often produces a
better fact than a stubby Wikipedia article.

The trade-off is that in `"llm"` mode **nothing is verified**. The grounded
filter works by comparing a line against the facts that were found, and here
nothing was found, so there is nothing to compare against — only the
explicit/taste filters and the character limit still apply. The prompt demands
documented-only facts and tells the model to answer `NOTHING RELIABLE` rather
than guess (the bot then falls back to the real sources), but that is a request,
not a guarantee: a model with latitude is what produced *"the only place in
Trumbull County where a hanging party went down"* for Girard, OH. Use `"llm"`
when you'd rather have a livelier fact and accept the risk; keep `"sources"`
when being right matters more.

## Spicy (adult) mode

Set `"spice": "spicy"` in `config.json` (or `TWITCH_SPICE=spicy`) for adult-rated
fun facts. Three layers, in order:

1. **`spicy_facts.json`** — hand-verified adult facts for famous spots (Las
   Vegas, Reno, Pahrump, New Orleans, Butte, Wallace ID, Tombstone, Deadwood,
   Milford PA, …). These are a *boost, not a shortcut* — the bot still searches
   the web and adds discovered facts below them, so nothing gets hidden.
2. **"Spicy dig"** — searches `<place> brothel / scandal / murder / …` and mines
   *other* Wikipedia articles that mention the town, surfacing racy history the
   town's own article omits.
3. **Region dig** — when a town's own article is dry, the bot mines its
   **county and state** (`<county> honeymoon / resort / legend / …`), so a tiny
   town still gets its region's claim to fame (e.g. the Poconos honeymoon
   resorts for a Pike County hamlet).
4. **LLM writer (recommended)** — the real facts found above are handed to a
   large language model with one prompt: *"Write up to 10 **interesting** true
   facts about this place: what it is known for, its history, its oddities, and
   any rowdy stories the supplied facts actually contain, each ≤ 200 chars."*
   The model rewrites **only the supplied facts** (never invents its own),
   returns up to 10 one-liners, and the bot serves the top one first, then a
   random one on repeat calls. A line cut off mid-quote is repaired to its last
   complete clause or dropped before it can reach chat. **This is what makes
   spicy mode actually spicy**
   — but see the note below: for real adult output you want a **local Ollama
   model**, because hosted models are filtered.

**Ask for interesting, not for crime.** The web sources used to append
`"history crime scandal"` to every spicy query. For Girard, OH that returned a
truncated page title (*"The Only Man Ever Hanged in Trumbull County: A True
…"*), an SEO line from a crime-stats site (*"Explore crime rates for Girard, OH
including murder, assault, and property crime statistics."*) and one recent
police story — thin, dark grist that the model then padded into invented dark
history. Spicy mode now searches `<place> history facts famous landmark
record`, and two kinds of search noise are never treated as facts: truncated
titles/snippets (anything ending in `...`) and SEO boilerplate (crime-rate,
"cost of living", real-estate, "is it safe" pages). Tone still comes from the
model; the *subject matter* has to be real.

**Fallback ladder** — for every lookup the bot tries, in order: ① **spicy**
facts (brothels, crime, gambling, scandal…), then ② **weird/bizarre** facts
(oddities, mysteries, legends, hoaxes, world records), then ③ the town's
**most popular** fun facts. So a sleepy town with no racy history still gets a
genuinely interesting answer instead of a made-up scandal.

**Related-article harvest** — a town's own Wikipedia page is often a stub whose
only sentence is census filler. The bot also harvests the *related* articles the
search returns (e.g. tiny Lakemont, PA gets its real claim to fame from
**Lakemont Park** — Leap-The-Dips, the world's oldest roller coaster), and it
hard-drops census/population/"it is located…" boilerplate so a dry stub never
turns into a boring reply. Same-named towns in other states are ignored.

Without any LLM, the bot posts the plain (real) facts it found — it never
tacks on fake joke comments.

`"spice": "clean"` switches back to family-friendly facts.

⚠️ **Keep it legal — and keep it TRUE**: "spicy" means **adult-aligned, not
sexual** — R-rated *topics* (crime, vice, gambling, scandal, dark history) told
with barstool wit and salty language, grounded in real history. It is *not*
explicit sexual content, porn/XXX, slurs, or hate speech — Twitch will ban a
channel for those. Hard output filters enforce this:

- **explicit** — any LLM line with explicit sexual words is dropped.
- **taste** — a real killing, execution or lynching is never framed as a party
  or a good time ("a hanging party went down" is dropped even when the
  underlying fact is true). A literal murder-*mystery* dinner is fine.
- **grounding** — every LLM line must be traceable to the facts that were
  actually found, in three parts: no invented **names or dates**; no invented
  **claims** (a crime, vice, disaster, record or "only / first / largest" boast
  that no source fact makes — these are written in lowercase, so a name-and-date
  check alone cannot see them); and no **borrowed boasts** — a "the only … /
  the first …" claim that is pinned on the town *and* scoped to a region ("home
  to Trumbull County's one and only …") must be backed by a fact that names the
  town. Facts dug out of the county or state are also prefixed `In the area:`,
  in chat as well as for the filter. Telling a regional story *as* a regional
  story is fine; handing it to the town is not.

If a line fails any of these it is dropped, and if every line fails the bot
posts the plain real facts instead. Spice comes from the curated database and
the search sources — never from the model's imagination.

Worked example — `!funfact girard, OH`. Girard's whole Wikipedia article yields
one interesting sentence (settled 1800, grew after the Ohio and Erie Canal), so
a low-refusal model asked for "rowdy" facts made up *"the only place in
Trumbull County where a hanging party went down"*, *"so fast and loose with
drugs, one local broke records as a mule"* and, later, *"home to Trumbull
County's one and only hanging … they really dropped the axe on this one guy"*.
None of it is true: the only hanging in Trumbull County was Ira West Gardner's
1830s execution in Warren for the murder of his stepdaughter Maria Buel, and no
drug-mule record exists for Girard. All three are now dropped — no source fact
mentions a hanging in Girard, drugs, a mule or a record, and a county-wide
"only" cannot be pinned on the town — and the bot posts the real facts instead
(Girard is named for Stephen Girard, the Philadelphia philanthropist). Telling
the Gardner hanging *as* a Trumbull County story is still allowed.

### Commands

| Command | What it does |
|---|---|
| `!funfact <place>` | A real fun fact about a town. `!funfacts` is the same command |
| `!smk female\|male\|any` | Shag, marry or kill — three names, each with their occupation |
| `!joke` | A joke |
| `!randomfact` | A random fact |
| `!riddle` | A riddle; the answer follows after `riddle_answer_delay` seconds |
| `!wyr` | A would-you-rather |
| `!help` | Lists all of the above, plus who may use them |
| `!bot off` / `!bot on` / `!bot status` | **Moderators only** — switch every command off and on again |
| `!haul` | What the truck is hauling — anyone in chat can ask |
| `!whois <name>` | Who that person is, from Twitch and Wikipedia |
| `!haul update <cargo>` / `delete` | **Moderators only** — set or clear it |
| `!reminder <when> <message>` | **Moderators only** — post into chat later |
| `!reminder list` / `cancel <n>` / `cancel all` | **Moderators only** — see and clear them |

`!smk` is also accepted as `!shagmarrykill` or `!marryshagkill`, and `f`/`m`
work as shorthands for `female`/`male`. Its names come from a local pool of
public figures — no API call, and nobody in chat gets named by accident.

```
ShagMarryKill [female] | Dolly Parton (singer), Kate McKinnon (comedian), Rihanna (singer) - shag one, marry one, kill one.
```

The round is posted on its own, with no one nominated to answer it — anyone in
chat can play, not just whoever typed the command. Each name carries what that
person is known for, so a round is playable by people who do not recognise
every face in the pool.

`!help` is answered before the rate limiter, so a viewer can read what the bot
does even if they aren't allowed to run a command yet. Everything else is
gated.

`!bot off` silences the bot completely — every command, including `!funfact`,
is ignored rather than refused, so it does not become a spam vector. It is
restricted to the broadcaster and moderators, and it deliberately keeps
answering while the bot is off; otherwise switching it off would be a one-way
trip. The state lives in memory, so a restart brings the commands back on.

### Reminders

A moderator can leave a note that posts into chat later. Two ways to say when:

```
!reminder 60mins Make sure you check the lights are working
!reminder 01:30PDT Check your lights
```

```
@amod reminder #1 set for 03:50 UTC today (in 1 hour) - Make sure you check the lights are working
@amod reminder #2 set for 08:30 UTC today (at 08:30 PDT tomorrow) - Check your lights
```

and an hour later, unprompted:

```
Reminder | Make sure you check the lights are working  (set by @amod)
```

**Durations** — `60mins`, `60 mins`, `90s`, `1h30m`, `2d`, `1.5h`, `1 week`.
A bare number means minutes, so `!reminder 5 do the thing` is five minutes.
Between 10 seconds and 14 days, up to 20 pending at once.

**Clock times** — `01:30PDT`, `1:30pm PDT`, `13:30`, `01:30 America/Los_Angeles`,
`01:30 UTC-7`. An abbreviation is a *fixed* offset, so `PDT` means UTC−7
whatever the season; an IANA name such as `America/Los_Angeles` follows DST.
With no timezone at all it uses the machine the bot runs on. A time that has
already passed today rolls to tomorrow, which is what "remind me at 1:30"
means at 4pm.

The confirmation always shows **both** the machine's own clock and the zone you
asked for, so a misread timezone is obvious at a glance and can be cancelled.

Managing them:

```
!reminder list          @amod reminders: #3 1 minute from now - short one | #1 1 hour from now - ...
!reminder cancel 3      @amod reminder #3 cancelled.
!reminder cancel all    @amod cancelled all 3 reminders.
```

`!reminders` is accepted as an alias for `!reminder`.

**Reminders survive a restart.** They are written to `reminders.json` as they
are created, so one set for tomorrow morning is not lost to an update or a
crash overnight. One that came due while the bot was down still fires when it
comes back up; one more than an hour overdue is dropped rather than posted out
of the blue. While the bot is switched off with `!bot off` they are *held*, not
dropped, and fire when it resumes.

### The haul board

One line of state so viewers can ask what the truck is carrying:

```
!haul update Produce        (moderator)
@viewer2 we are transporting Produce.  (set by @amod, 4 minutes ago)

!haul delete                (moderator)
@viewer2 nothing is logged as the haul right now - a moderator can set it
with !haul update <what we're hauling>
```

Reading it is open to everyone — it costs no API call and answers no fact
engine — and it stays open even for viewers the fact commands are gated
against. Only moderators can change or clear it, and a viewer who tries gets
no reply at all. `!hauls` is an alias, and `!transporting` / `!transport`
still work from before the rename. It is stored in `haul.json`, so what the
truck is hauling does not reset when the bot is updated; a `transporting.json`
left over from the older name is carried over automatically.

Both state files are gitignored, like `tokens.json` and `config.json`. Keep
them when you update the bot's files if you want reminders and the haul board
to carry over.

### !whois

Two commands, because they answer two different questions and guessing
between them produces an answer about the wrong person:

```
!whois Aubrey Plaza
WhoIs | Aubrey Plaza (American actress): Aubrey Christina Plaza (born June 26,
1984) is an American actress, comedian, producer, and writer. She gained
recognition for playing April Ludgate on the NBC sitcom Parks and Recreation.

!twitch hardclaws
Twitch | Hardclaws | Twitch Partner, 45,231 followers, joined Mar 2019. "Truck driver streaming from the cab."
```

**Nothing here is written by a model.** `!whois` posts the lead of the
person's Wikipedia article, cut to `whois_max_chars` (default 400) on a
sentence boundary with `[1]`-style citation markers stripped. `!twitch`
posts the channel's own bio, partner/affiliate status, follower count and join
date. A blurb about a real person has to be something you can check.

These were one command for a while. That meant `!whois Aubrey Plaza` had to
decide whether you meant the actress or whoever owns the login `aubreyplaza` —
and when it picked the login, it titled the answer after a stranger wearing a
real person's name. Two commands, no guessing.

`!twitch` uses `GET /helix/users` and the follower **count** from
`GET /helix/channels/followers`. Neither needs a scope, and the count comes
back even for a channel the bot cannot moderate — only the per-user rows need
`moderator:read:followers`. So this works with the token you already have, no
re-login. A Helix failure is reported as "I couldn't reach Twitch", never as
"there is no such channel".

**Names are cleaned the way chat types them.** All of these work, because
Twitch's mention picker inserts the `@` and people paste URLs:

```
!twitch NOTTaitch
!twitch @NOTTaitch
!twitch #hardclaws
!twitch https://twitch.tv/nottaitch
```

None of `@`, `#` or a URL can appear in a real login — Twitch allows only
`[a-z0-9_]` — so stripping them can never damage a name, and the spelling the
viewer typed is what gets echoed back. This is one helper,
`access.clean_login`, shared by every site that normalises a login. It used to
be two separate `lstrip("#")` calls, which is how `@name` slipped through: the
bot answered *"there is no Twitch channel called @NOTTaitch"* for a channel
that plainly exists. A query it cannot parse must never be reported as a
missing channel.

`!whois` costs two calls at most — the article, and Wikipedia's search when the
name as typed is not a page title. Results are cached for six hours because
Wikipedia rate-limits by IP.

`!who` is an alias for `!whois`. `!twitch` was previously `!whotwitch`, and
the old spellings `!whotwitch`, `!whotw` and `!twitchwho` all still work -
only `!twitch` is advertised in `!help`, so nobody mid-conversation breaks.
Both go through the same per-user rate limit as `!funfact`, since each makes a
network call.

### !smk — and why the name pool has two layers

```
!smk female
ShagMarryKill [female] | Dolly Parton (singer), Kate McKinnon (comedian), Rihanna (singer) - shag one, marry one, kill one.
```

The command never names the person who asked — the round belongs to the whole
chat, not to whoever typed it — and every name carries its occupation so the
round is playable by people who do not recognise every face.

**The pool used to be 44 names.** Draw three of those, several times a night,
and chat sees the same faces repeatedly no matter how well it is shuffled: the
list is simply shorter than the number of rounds people play. So:

1. **A seed pool of 389 hand-picked names** (`names.py`, 185 female / 204
   male), each with an occupation — famous enough that a round still works for
   someone who does not follow that field. Always available, no network.

2. **Wikipedia category listings, topped up in the background.** A keeper
   thread fetches `Category:American film actresses` and 15 siblings and caches
   whatever people it finds to `names.json`. Thousands more names, growing
   every 12 hours (`names_topup_hours`).

3. **A recency memory** of the last 240 names used, so a big pool behaves like
   one. Without it, a 4,000-name pool still hands out the same three faces.

Two things worth knowing:

**The tiers are blended, not stacked.** Drawing seed-first with a recency
window smaller than the seed pool means the harvested names are *never*
reached, and the whole top-up does nothing. Each name in a round is drawn
~75% from the seed pool and ~25% from the harvested tail, so new names
actually reach chat while rounds stay recognisable.

**A category listing is not a list of people.** `Category:American film
actresses` contains `List of American film actresses`, awards pages and
namesake pages. `person_name()` rejects those — posting one into chat as
someone to marry is the failure mode this guards. Namesakes
(`Jane Doe (actress)`) are skipped rather than coin-flipped, because picking
one at random names the wrong person.

The top-up is a nicety, never a dependency. Set `"names_topup_enabled": false`
and the seed pool carries the game on its own. A Wikipedia outage, a corrupt
`names.json` or a dead category all fall back to the seed without the command
noticing.

#### Being a polite guest on Wikipedia

Wikimedia sorts API clients into two rate-limit tiers **by User-Agent**: an
identifiable one carrying a contactable URL or email is allowed
**200 requests per minute**; anything else is treated as unidentified and
capped at **10**.

The bot fetches 16 categories at 1.1s apart — about **55 requests per minute**.
Against a 10/min cap that meant the first ten categories of every cycle
succeeded and the **last six came back `429 Too Many Requests`**, every single
cycle, forever:

```
[names] English film actors: <HTTPError 429: 'Too Many Requests'>
[names] American male singers: <HTTPError 429: 'Too Many Requests'>
...
```

Those are always the same six because they are always the last six. Three
things fix it:

- **A contactable User-Agent** — `ClawFacts/1.0 (+https://github.com/hardclaws/ClawFacts;
  hobby Twitch chat bot, name pools)`. That alone moves the bot from the
  10/min tier to the 200/min tier, where 55/min is unremarkable.
- **`Retry-After` is honoured.** A 429 raises `RateLimited` carrying the
  server's own header, and the pool waits that long. The server knows its
  window and we do not, so its number always beats ours.
- **The backoff is pool-wide and doubles.** A 429 is about *this client*, not
  the one category that happened to trip it — the next category would have
  been refused too. So the refusal ends the cycle immediately and every fetch
  waits. With no `Retry-After` the wait doubles each time, to a day at most.

The six lines become one:

```
[names] Wikipedia rate-limited the top-up; resuming in ~5 min.
```

Wikipedia also asks callers to back off after any request that took more than
a second to serve; `harvest_category` sleeps five seconds when that happens,
which is the signal that usually precedes a 429 rather than the 429 itself.

### !beef — a drip-fed feud

```
!beef Hardclaws zwift
!beef random
!beef zwift            (a lone genre word is the setting, not the rival)
!revenge               (rematch the rival who just beat you, within 60s)
!beef stats            (the leaderboard; !beef stats <name> for one player)
```

Whoever types it stars in it, against the name they give. The story is five
messages — headline, three story lines, verdict — because a feud is 600+
characters and a Twitch message is not. They are **clean** — no `BEEF |` prefix, no
`Act 1` labels, no `WINNER:` in caps — the verdict speaks English ("🏆
SpeedyDave takes it."); the chrome read like a form and took away from the story,
so the lines stand alone. And they are **spaced out, not fired as one
burst**: the headline lands immediately, then each part follows
`beef_act_delay` seconds apart — the same gap every time, so the knob means
exactly what it reads — and the winner is announced last. Five messages at
once is a wall; drip-fed, it's a bit of theatre chat can react to between
the lines (`OHHH`, `no he didn't`). With the default
`beef_act_delay: 4` the whole story takes ~16 seconds. **Config is read at
startup — restart the bot after editing config.json**, and `!beef status`
shows the live value so you never have to guess whether it took.

```
🔥 SpeedyDave vs. @TruckingWithDoc — Eating Tacos 🔥 Two enter, one leaves.
        (+ beef_act_delay)
SpeedyDave caught TruckingWithDoc double-dipping the communal salsa like a
man with no witnesses.
        (+ beef_act_delay)
TruckingWithDoc retaliated with a taco so structurally ambitious it required
a permit. The salsa bar is picking sides.
        (+ beef_act_delay)
They settled it at the table, three tacos each, judged by the whole room.
TruckingWithDoc's collapsed on the second bite.
        (+ beef_act_delay)
🏆 SpeedyDave takes it. TruckingWithDoc says the result is 'under review'.
```

**Template-driven, with an optional LLM pass that has to earn its slot.**
The templates are the engine of record: the winner is rolled before any text
exists, and every template line is one a person read before it shipped. When
an LLM is configured (`beef_llm: "auto"`), `beefllm.py` asks the model for
the three acts and the verdict while chat is still reading the headline —
the pacing work is what makes this safe, because the first act's gap is the
model's deadline. Deliver in time and pass `validate()` — exactly four lines,
the unlabelled shape, **the pre-rolled winner** right after the trophy,
both players in the story, length
limits, no `@`-pings, no URLs or markup — and chat gets the model's version.
Anything else (no key, 402, timeout, wrong winner, creative formatting)
falls back to the template lines silently. The headline is always the
template one: it carries the @-tag decision, and that is never the model's
to make. The leaderboard and `!revenge` are scored off the pre-rolled winner
either way, so the model cannot tilt the game. Six genres × 18 openers × 10
escalations × 6 climaxes × 5 rivals × either winner × 9 exit lines is
**1,000,000+ template stories on the spine before names go in**, so the
fallback is never a consolation prize. Neither `beef.py` nor `beefstats.py`
imports the LLM, the network, or a key — a dead Ollama or a 402 on the fun
facts cannot take the feuds down with them — and `python3 check_fixes.py`
refuses a copy where that ever changes, or where the LLM pass stops
validating its output.

**The winner is rolled before the text is written**, so the story lands on the
right outcome instead of the model being asked to retrofit one — and the
verdict is its own message, so the pause before it does the work a drumroll
would.

**The acts are two beats, not one.** An incident plus a trash-talk quote, an
escalation plus a crowd reaction ("The race chat is ninety percent popcorn").
A single bare sentence per act was a summary, not a story — no voice in it,
and with six lines per pool the repeats came fast. The pools are ~10 lines
deep per beat now, headlines carry stakes ("Loser mutes the group chat."),
the loser's exit is drawn from nine fates instead of "has left the building"
every time, and `_pick()` refuses to re-draw any line used in the last three
stories, so back-to-back beefs stop echoing each other.

**`!beef random` randomises the genre, not the opponent.** Pulling a bystander
out of chat into a public feud they never asked for is how a joke command
becomes a harassment report, and their chat reads it too. The rival is a named
character — The Watt-Bomber, Lot Lizard Larry, Buttercream Brenda — unless the
issuer typed a real name.

**Freeform themes.** `!beef @W_E_S_T_Y Eating Tacos` doesn't have to name a
genre. The words after the rival become the story's setting line verbatim —
*🔥 Hardclaws vs. W_E_S_T_Y — Eating Tacos 🔥* — and with the LLM pass
on, the story is written inside that theme (a taco-eating feud, salsa
politics and all). With templates only, the lines come from a random genre
under your words: arbitrary themes are the one thing a fixed pool honestly
cannot do, which is the model's job. Losing a themed beef arms `!revenge`
with the theme attached, so the rematch is still about the tacos.

| control | effect |
| ------- | ------ |
| `!beef off` / `on` / `status` | moderators only, silent for viewers, works while the bot is off |
| `"beef_enabled": false` | off for everyone; the bot names the key |
| `"beef_act_delay": 4` | **exact** seconds between each part (0 = the whole story at once; restart after editing) |
| `"beef_llm": "auto"` | LLM writes the story when a model is configured; `false` = templates only. Failures fall back silently |
| `"beef_llm_timeout": 4` | seconds the model gets — never more than the first gap |

Stories coming out as templates while a model *is* configured means the model
missed the deadline — the console logs `!beef LLM pass failed or missed the
deadline`. A local 8B can need 8–10s: raise `beef_llm_timeout` (default 4) (it is capped
at the first gap, and the pacing is measured from the headline, so waiting
longer never stretches the gaps). `!beef status` says whether the LLM pass is
live at all.
| `!revenge` | the player who just lost may rematch the same rival within 60s |
| `!beef stats [name]` | the top five, or one player's card; readable even while the game is off |

`!beef` and `!revenge` are both reserved, so a moderator cannot shadow either
with a custom command. A rival typed as `@Name` works — the `@` is stripped,
not treated as an invalid name that silently swaps in a random character.

#### !revenge — the rematch

Whoever just **lost** a beef has 60 seconds to run it back against the same
rival, same genre — and same theme, so a taco loss gets avenged as a taco
rematch:

```
🔥 REMATCH: SpeedyDave vs. Hardclaws — the virtual mountains of Watopia 🔥
SpeedyDave demanded a rematch on the spot, and the room went quiet.
...
```

The window is a timestamp in `beef_state.json`, not a timer on a thread — the
worker that ran the original beef is long gone by the time the player types
`!revenge`, and a restart inside the window must not eat the rematch. The
rematch is a fresh 50/50 roll: an unloseable rematch would not be worth the
extra point. Winning one is the hard win of the game.

#### !beef stats — the leaderboard

```
!beef stats              -> the top five
!beef stats Hardclaws    -> one player's card
```

Points: a win is **+2**, a loss **−1**, an avenged loss **+3**. Streaks are
consecutive wins; titles run from *Fresh Meat* through *Feud Professional* to
*Legendary Grudge*. Only the person who typed the command is scored — a named
rival who never played has not opted into having a record. The state lives in
`beef_state.json` next to the code, written atomically; losing it loses the
board, not the bot. The scoring knobs (`WIN_POINTS`, `REVENGE_WINDOW`, …) are
all at the top of `beefstats.py` for tuning after a stream.

#### Tagging is earned, not assumed

The headline @-tags the rival only when that rival **has played the game
themselves** (typed `!beef` or `!revenge` at least once) **and was seen in
chat recently** — or is the channel's broadcaster, the one presence the bot
can assume. A bystander the issuer named still gets *named*; naming is the
issuer's choice. But they are never *pinged* into a feud they never touched.
Presence is kept in memory only, capped, and never written to a file.

### The other games never run dry either

`!joke`, `!randomfact`, `!riddle` and `!wyr` fetch live from their own
keyless APIs, so they are not limited by a list — but those endpoints are
run by strangers and any of them can vanish overnight. When one does, the bot
used to answer `couldn't fetch that right now 😕` **every single time the
command was typed**, for as long as the endpoint stayed down.

Each one now falls back to a local pool — 30 jokes, 30 facts, 25 riddles, 25
would-you-rathers — drawn without repeating until the pool has been all the
way round. A dead API costs variety, not the game. The fallback is logged
**once per pool**, not once per call, so a dead endpoint does not bury the log
that is supposed to be telling you something is wrong.

So every game has a live source and a floor:

| Command        | Live source                     | Floor                    |
| -------------- | ------------------------------- | ------------------------ |
| `!smk`         | 16 Wikipedia categories         | 389 seed names           |
| `!joke`        | official-joke-api               | 30 jokes                 |
| `!randomfact`  | uselessfacts.jsph.pl            | 30 facts                 |
| `!riddle`      | riddles-api.vercel.app          | 25 riddles               |
| `!wyr`         | api.truthordarebot.xyz          | 25 would-you-rathers     |
| `!whois`       | Wikipedia                       | — (a miss is the answer) |
| `!twitch`      | Twitch Helix                    | — (a miss is the answer) |

### Waking up a quiet channel

The old idle poster (ten minutes of silence, then a random `!smk` or
`!joke`) is gone. The chat AI owns the quiet moments now: after
`chat_ai_quiet_seconds` of silence the active voice opens the
conversation itself — a question for chat, a hook from its own life. See
[The chat AI](#the-chat-ai-ask-replies-and-chime-ins). Like the old
poster, openers only fire while the channel is actually streaming
(scope-free `GET /helix/streams`); where the check cannot be settled
they fire anyway — unknown is not offline.

### Rewrites cannot add character

The LLM rewrite path is only allowed to re-word the facts the sources
returned. It used to be checked for invented names, dates, claims and praise
clichés — all of which this line cleared:

```
Aubrey Plaza: actress, comedian, producer, writer - and deadpan delivered with extra deadpan.
```

Nothing there is a name, a date, a place, an event or a claim. What it invented
was an opinion. Rewrites are now also checked for clauses built mostly out of
words the source never used, which drops that while still allowing a genuine
paraphrase — synonyms and abbreviations like "Philly" for Philadelphia
survive, because they scatter through the sentence rather than piling into a
trailing clause.

Facts are trimmed to `max_fact_chars` / `max_message_chars` on a **sentence
boundary** where one fits, so a long fact loses its trailing sentence instead
of ending mid-phrase:

```
before: ...contributing buildings that…
after:  ...is listed on the National Register of Historic Places.
```

### Who can use !funfact, and how often

Chat abuse is easy to stop for mods, VIPs and subs — Twitch sends their badges
on every message, so those checks are free and instant. **Follow status is not
in IRC at all.** The only way to get it is the Helix API:

```
GET /helix/channels/followers?broadcaster_id=<channel>&user_id=<viewer>
```

which needs the `moderator:read:followers` scope and a user token belonging to
the broadcaster or one of the channel's moderators — the device-login token
`auth.py` already stores.

**Two things must both be true, or every follower gets turned away:**

1. **The bot account must be the broadcaster or a moderator of the channel.**
   If it is a separate account, promote it once in chat:
   ```
   /mod TruckingWithDocBot
   ```
   This is Twitch's rule, not the bot's: *"The ID in the broadcaster_id query
   parameter must match the user ID in the access token or the user ID in the
   access token must be a moderator for the specified broadcaster."*

2. **Re-authorise once** so the token carries the new scope:
   ```
   python3 bot.py --login
   ```

Only three scopes are requested: `chat:read`, `chat:edit` and
`moderator:read:followers`. Nothing else, because Twitch **rejects the entire
device flow over one scope name it does not recognise** — the bot would print
`invalid scope requested` and exit, unable to log in at all.

> This shipped broken once. The list carried `moderation:read:moderators`,
> which is not a Twitch scope (the real one is `moderation:read`), and login
> failed outright. The scope was pointless even under its correct name: Get
> Moderators requires `broadcaster_id` to equal the token's own user id, so a
> bot account that is not the broadcaster can never read another channel's
> moderator list. Asking for it bought nothing and cost the ability to log in.

#### The bot says why, at startup

Four different faults all produce the same chat message — *"could not verify
your follow status"* — and only two of them are yours to fix. So the bot reads
its own token back from Twitch on the way up and prints the cause rather than
leaving you to guess:

```
[access] token account='truckingwithdocbot' scopes=chat:read,chat:edit
[access] moderator status is read from chat on join (USERSTATE), not from the API.
[access] PROBLEM: the token is missing the moderator:read:followers scope -
         run 'python3 bot.py --login' to re-authorise.
[access] until the above is fixed, followers will be told 'could not verify
         your follow status'.
```

**Moderator status comes from chat, not the API.** When the bot joins, Twitch
sends a `USERSTATE` line carrying the bot's *own* badges — free, no scope,
authoritative. The bot reads it and tells you what it found:

```
[access] TruckingWithDocBot is a moderator of #hardclaws.
```

or, if it is not:

```
[access] PROBLEM: TruckingWithDocBot is NOT a moderator of #hardclaws. Run
         /mod TruckingWithDocBot in that channel - until then !bot and
         !reminder will refuse it.
```

That also re-reports itself if you `/mod` or `/unmod` the bot mid-stream,
since Twitch resends `USERSTATE` after every message the bot sends.

With the scope in place it says so plainly:

```
[access] token account='truckingwithdocbot' scopes=chat:read,chat:edit,moderator:read:followers
[access] follower check OK - token may read this channel's followers (total=812).
[access] follow checks are working.
```

Read the `scopes=` line first. If `moderator:read:followers` is not in it, no
amount of `/mod` will help — the token simply cannot ask the question, and the
only fix is `python3 bot.py --login`.

The probe also re-runs every five minutes while it is failing, so granting the
bot `/mod` mid-stream takes effect without a restart.

> Twitch answers an unauthorised request with **200 OK, the real follower
> total, and an empty list** — the same shape as "this user does not follow".
> Treating those as the same thing is what made every real follower get told
> they don't follow the channel. The bot now reports *"could not verify your
> follow status"* in that case rather than accusing the viewer.

Default schedule (`tier_cooldowns` in `config.json`):

| Who | Cooldown | How it's known |
|---|---|---|
| Broadcaster | 30s | `broadcaster` badge |
| Moderator | 30s | `moderator` badge (also `staff`/`admin`/`global_mod`) |
| VIP | 60s | `vip` badge |
| Subscriber | 60s | `subscriber` badge (`founder` counts) |
| Follower, following > 1 day | 5 min | Helix API, cached |
| Follower, less than 1 day | blocked | Helix API |
| Not following | blocked | Helix API |

Notes:

- Cooldowns are **per user**, so one person waiting never blocks the next. The
  old `cooldown_seconds` stays as a *per-channel* floor — that is what protects
  Wikipedia's per-IP rate limit, and per-user limits alone would not stop twenty
  different viewers each firing once.
- Mods, VIPs and subs never trigger an API call. Follower lookups are cached:
  a confirmed follow for 6 hours, a non-follow for 15 minutes.
- `min_follow_age_seconds` (default 86400) is the 1-day rule.
- If the follow check cannot run — no token, missing scope, API down —
  `follower_check_failure` decides. The default `"deny"` keeps the gate honest;
  `"allow"` falls back to badges only.
- **The gate covers every command**, not just `!funfact`. `!joke`,
  `!randomfact`, `!riddle` and `!wouldyourather` all draw on the same per-user
  budget, so the free commands cannot be used to flood the channel either. A
  sub who fires `!joke` waits the same 60s before `!funfact`.
- Rejections are explained in chat, but at most once every 2 minutes per user —
  otherwise refusing people becomes its own spam vector.
- `"access_control": false` turns the whole thing off.

`!riddle` reveals its answer after `riddle_answer_delay` seconds — **20** by
default (it was a hardcoded 45).

### Updating the bot's files without losing your login

`auth.py` saves your Twitch login to **`tokens.json`** (chmod 600) in the bot's
own folder, and silently reuses or refreshes it on every start — you should only
ever run the device login once.

`tokens.json` and `config.json` are **not in git** (they hold your OAuth token
and API keys) and they are listed in `.gitignore`. So if you update by
downloading the repo or a zip and replacing the whole folder, you delete your
saved login and the bot asks you to authorise again. That is the usual reason a
restart demands a fresh login — it is not the token expiring.

To update safely, copy the new `.py` files, `spicy_facts.json`, `README.md` and
`fixtures/` over the old ones and **leave `tokens.json` and `config.json`
alone**. If you do lose the login, `python3 bot.py --login` re-runs the device
flow once.

After every restart the bot prints its own build level — no git needed:

```
[bot] fixes self-check: 149/149 present - that is the build you are running
```

The count is the honest build number: it rises with every fix that ships, so a
pasted log can never leave anyone guessing which fixes are actually running
(`[bot] build …` names the git commit when there is one; the self-check works
even in a hand-copied folder).

One more thing that looks identical: `refresh_if_possible()` refuses saved
tokens whose `client_id` differs from the one in `config.json`. Replacing
`config.json` with `config.example.json` therefore also forces a re-login.

### Source order — read this if a search key never seems to be used

`_try_sources()` is a ladder that stops at the **first** source that returns
anything usable:

```
wikipedia  ->  serper  ->  google  ->  duckduckgo
```

Wikipedia is first because it is free and usually sufficient, so a paid key is
only spent on towns it cannot serve. DuckDuckGo is last because its Instant
Answer is a single Wikipedia-style blurb.

> It used to run `wikipedia -> duckduckgo -> google -> serper`. That was a bug:
> one dull DuckDuckGo sentence ended the ladder, so a configured key was never
> consulted. For `!funfact Jerome, Missouri` DDG returned "It is located on the
> Gasconade River near Interstate 44" and the bot posted that instead of
> searching. A configured key is now tried before DuckDuckGo.

Startup prints the active ladder so you can see it immediately:

```
[info] fact sources: wikipedia -> serper -> duckduckgo (fallback)
```

### Serper search (recommended — this is the one to set up)

Wikipedia throttles anonymous API use **per IP**, and plenty of towns have a
stub article with nothing worth posting (Jerome, Missouri is 116 words). The
cure is a **second, independent search source**. [Serper](https://serper.dev) is
the simplest: sign up (free, no card), copy the one API key, and add:

```json
"serper_api_key": "abc123..."
```

(or set the `SERPER_API_KEY` environment variable.) Free tier is **2,500
queries**; beyond that it's ~$0.50 per 1,000. The bot asks for *interesting*
facts — `{town} history facts famous landmark record` — and mines the ranked
snippets, which reach local-history sites (route-66 associations, state
historical societies) that no encyclopedia covers.

Under `--debug` a missing key says so instead of failing silently:

```
[funfacts] serper source not configured (no serper_api_key)
```

### Optional Google search (legacy — closed to new customers)

Google's **Custom Search JSON API** needs *two* values: an API key
(`google_api_key`) *and* a Programmable Search engine ID (`google_cx`). Missing
either one disables the source entirely:

```
[funfacts] google source not configured (needs BOTH google_api_key and google_cx)
```

```json
"google_api_key": "AIza...",
"google_cx": "abc123..."
```

**Google has closed this API to new customers** and is sunsetting it
(1 January 2027); the free tier was 100 queries/day. Unless you already have a
`google_cx`, use Serper — it is one key, no engine to configure, and it is what
this bot is tested against.

> Both search sources return short snippets, not prose — so their facts are
> usually weaker than Wikipedia's. Their real value is coverage of places with
> no usable Wikipedia article.

> The bot also needs a real *model*: `openrouter/free` is OpenRouter's
> auto-router — it picks a random free model per call (often a reasoning model
> whose "thinking" leaks into the reply, and often rate-limited). Set
> `OPENROUTER_MODEL` to a specific model such as `nousresearch/hermes-4-70b`.

### Optional LLM writer (the recommended way to get spicy facts)

The LLM rewrites the bot's real facts into punchy adult-humor one-liners for
*any* town. There are two ways to run it:

#### A) Local Ollama model — the one that's actually adult ✅

Hosted models (Groq, OpenRouter, OpenAI…) are **alignment-filtered**: ask them
for risqué output and they refuse, or quietly rewrite it into bland puns.
That's why `!funfact` kept coming back tame.

The fix is a **local, unfiltered model** via [Ollama](https://ollama.com) —
it runs on your own machine (the MiniPC that already runs Dayforge is perfect),
costs nothing, and has no content filter. Setup:

1. Install Ollama — Windows: `winget install Ollama.Ollama` (or download from
   ollama.com). Linux: `curl -fsSL https://ollama.com/install.sh | sh`.
2. Pull a model:
   ```
   ollama pull llama3.1:8b          # solid default
   ollama pull dolphin-llama3:8b    # "dolphin" tunes have less refusal training
   ```
   Smaller/faster if the box is slow: `llama3.2:3b` or `dolphin-phi:2.7b`.
   (An 8B model wants ~8 GB RAM; on CPU a fact takes a few seconds.)
3. Tell the bot to use it — either in `config.json`:
   ```json
   "llm_api_key": "",
   "llm_base_url": "http://localhost:11434/v1",
   "llm_model": "llama3.1:8b"
   ```
   …or via env vars (these beat any `GROQ_API_KEY` that's also set):
   ```
   OLLAMA_MODEL=llama3.1:8b python3 bot.py
   ```
4. Check it:
   ```
   python3 test_llm.py --ollama
   ```

The prompt still enforces the safety line (true facts, no slurs/hate,
adult-aligned topics yes / explicit sexual content no) — a local model just
stops *over*-censoring, and a hard filter drops any line that drifts into
explicit territory.

#### B) Hosted Groq / OpenRouter (fine for clean mode, tame for spicy)

**Groq** is free but filtered (see above) — use it for clean mode or when you
don't mind the tamer tone:

```json
"llm_api_key": "gsk_...",
"llm_base_url": "https://api.groq.com/openai/v1",
"llm_model": "openai/gpt-oss-120b"
```

- Get a Groq key at <https://console.groq.com/keys> (free).
- The default model `openai/gpt-oss-120b` is free on Groq; the bot
  automatically falls back to `openai/gpt-oss-20b` (same key, its own
  rate-limit bucket) when it's rate-limited. Groq retired the Llama 3.x
  slugs in Aug 2026 — a config still naming one is told at startup.
  The bot also handles the model's reasoning-mode quirks for you
  (`max_completion_tokens`, no `temperature`).
- **Already run another AI app with these vars?** Just start the bot in that
  same environment — it reads `GROQ_API_KEY`, `GROQ_API_BASE`, `GROQ_MODEL`
  (and `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` / `OPENROUTER_API_BASE`).
  To find your key: `grep GROQ .env` in that app's folder.

**OpenRouter** hosts the unfiltered models (Hermes, Euryale, Dolphin…) that
Groq doesn't. Note: the *free* tier's uncensored models were deprecated in
July 2026, so the uncensored ones are paid now — but they cost **fractions of
a cent per fact** (~200 tokens out; a fact ≈ $0.00005–$0.0002). One $5
top-up is effectively tens of thousands of facts.

```json
"llm_api_key": "sk-or-v1-...",
"llm_base_url": "https://openrouter.ai/api/v1",
"llm_model": "nousresearch/hermes-4-70b"
```

Unfiltered / low-refusal models to pick from (all current as of Aug 2026):

| Model | ~$/1M out | Notes |
| ----- | --------- | ----- |
| `nousresearch/hermes-4-70b` | $0.40 | Default. Cheap, low-refusal, good instruction-following. |
| `sao10k/l3.3-euryale-70b` | $0.75 | Unfiltered, edgiest prose — fine here because the no-sex filter catches drift. |
| `cognitivecomputations/dolphin-mistral-24b-venice-edition` | $0.90 | Literally labeled "Uncensored". |
| `thedrummer/unslopnemo-12b` | $0.40 | Abliterated (uncensored) Mistral Nemo, light and cheap. |
| `mistralai/mistral-nemo` | $0.03 | Dirt cheap and quite permissive (not fully uncensored). |

The bot hard-drops any LLM line containing explicit sexual words, so even the
spicier models stay **adult-aligned** (crime, vice, gambling, scandal, dark
history) rather than XXX.

Setup: <https://openrouter.ai/settings/keys> → create a key → in the Privacy
tab set "Model training on prompts" to **Off**. The bot sends the
`HTTP-Referer`/`X-Title` headers OpenRouter wants, so no extra config is
needed.

- Without any LLM the bot just posts the plain real facts — no fake jokes.
- The LLM is only used in spicy mode; clean mode stays factual.


## Truck talk on the radio

`!cb` makes the bot talk on the radio, on demand. Three voices, picked at
random each time so chat cannot learn the tone either:

Every line is prefixed with which mode the bot is in, so it is never ambiguous
whether this is radio traffic or somebody hanging out of the window at a car:

```
[21:04:12] CB | Breaker one-nine, we got a bear in the bushes at the 42 yardstick. Ease off the loud pedal.
[21:37:55] CB | Heater's a-glowin', manners are showin', and I still can't get the travel agent on the box. Catch you on the flip flop.
[22:05:41] WINDOW | HEY! That minivan took the exit from the middle lane. Two little red lights. That's all I'm askin' for.
[22:19:03] WINDOW | OI - that crossover texted all the way up the on-ramp, and I'm a very patient man.
```

Four voices, picked at random per post:

| Label | Voice | Sounds like |
| --- | --- | --- |
| `CB \|` | **road** | Dry channel-19 traffic report. What CB actually sounds like. |
| `CB \|` | **grizzled** | An old-timer grumbling to himself. |
| `CB \|` | **ramble** | The weird one. |
| `WINDOW \|` | **yell** | Yelling out the window at a four-wheeler that just did something wrong. |

The label comes out of the same draw as the text, so a `WINDOW` line can never
go out under a `CB` label by being re-rolled on the way to the socket.

**The yelling is exasperated, never threatening.** Every template names a
vehicle (`that minivan`, `that crossover`) rather than a person, so nothing can
read as aimed at a real viewer, and a test fails if any of a dozen violent
terms is ever added to a pool. The bellow is in capitals — `HEY!`, `OI OI!` —
but the rest of the line is not, because Twitch automod reads a wall of caps
as spam.

`!cb` (or `!radio`, `!breaker`) asks for one on demand. It is a local
generator, so it costs nothing and never waits on the network, and it answers
without an `@` mention — the bot is on the radio, not replying to a question.

```json
"cb_chatter_enabled": true,
"cb_chatter_minutes": 25,
"cb_command_enabled": true,
"cb_command_access": "everyone",
"cb_yell_enabled": true
```

### Turning parts of it off

The random chatter and the command are separate switches, so you can keep one
without the other:

| You want | Set this |
| --- | --- |
| Only the random chatter, nobody can ask for one | `"cb_command_enabled": false` |
| Only on demand, never unprompted | `"cb_chatter_enabled": false` |
| Only mods (and the broadcaster) may ask for one | `"cb_command_access": "moderator"` |
| Only the broadcaster may ask for one | `"cb_command_access": "broadcaster"` |
| CB chatter only, no yelling at cars | `"cb_yell_enabled": false` |

A misspelt `cb_command_access` is treated as `"moderator"`, not `"everyone"` —
an access setting should fail closed. When the command is switched off it also
disappears from `!help`.

**A moderator can also switch the random chatter from chat**, without editing a
file:

```
!cb off      - silence the bot's own chatter (mods and broadcaster only)
!cb on       - bring it back
!cb status   - which state it is in
```

`!cb off` lasts until the next restart. It silences the chat AI's own
chatter — the chime-ins and the quiet-room openers — while `!cb` itself
keeps working on demand. That is the same split `!bot off` uses. A viewer
who types `!cb off` gets no answer at all, so the switch cannot become a
spam vector — and unlike `!bot off`, it does not silence anything else.

The bot no longer posts radio lines unprompted: the ambient CB chatter and
the idle-chat poster are gone from the build, and the chat AI owns the
quiet moments.

### Why there is no API for this

There isn't one. What exists is static quote listicles — a few dozen lines,
nearly all attributed "Unknown" — and CB slang glossaries, which are reference
dictionaries rather than generators. The glossaries turned out to be the better
raw material: they give an authentic vocabulary, and a slot generator built
from real terms produces far more than any fixed list.

Every term in the pools is genuine CB usage — *bear in the bushes*, *Kojak with
a Kodak*, *chicken coop*, *alligator*, *hundred-mile coffee*, *yardstick*,
*stay loaded*. Nothing is invented lore dressed up as trucker talk.

That currently comes to **over 10 million distinct lines**, and the weakest of
the 28 templates still produces 400+ on its own, so there is no line chat keeps
hearing. `python3 mock_trucker_test.py` checks both numbers rather than
asserting them.

**The slang is held to "crude but never explicit."** The bot posts unprompted
into a live channel, so the bar is higher than for a command a viewer chose to
run. Four genuine CB terms that refer to sex work — *lot lizard*, *sleeper
creeper*, *male buffalo*, *pickle park* — are deliberately absent, and a test
fails if any of them is ever added to a pool.

## Shoutouts on a raid

When the stream gets raided the bot posts a shoutout on its own, with nothing
to configure:

```
SO | 📣 Hammer lane for DaniLikesDonuts, they just swung off the interstate!
42 of you came across. Live on Just Chatting as we speak, engine still warm.
Twitch Affiliate, 1,284 followers. Go show them some love at
twitch.tv/danilikesdonuts
```

The wording follows whatever this channel is streaming, so a truck night gets
trucker talk and a Zwift night gets cycling talk:

```
SO | 🚴 NOTTaitch dropped in and the peloton noticed! They made the trip solo.
Proper watts and none of the ego that usually comes with them. Go show them
some love at twitch.tv/nottaitch

SO | 🪂 Trader landed hot! They brought 7 viewers over with them. Their last
lobby was Fortnite. Go show them some love at twitch.tv/trader
```

**A raid and a hand-picked shoutout are worded differently.** `!so <name>`
works on any channel, and most of them did not raid — so a manual shoutout
never says the person arrived, and never reports a viewer count, because the
size of a raid belongs to the raid and not to the person:

```
SO | 🙌 Put CadenceKate on the list, rider to rider. They were on Zwift the
last time anyone looked. Twitch Partner, 31,412 followers. Go show them some
love at twitch.tv/cadencekate

SO | 🎮 Give LlamaLover a look next time you're short a squad. Squad-worthy,
and they'll tell you straight when you earned it. Go show them some love at
twitch.tv/llamalover
```

There are 30 raid openers, 28 manual ones, 32 praise lines, and 16 each of the
present-tense and past-tense game lines — 122 distinct pieces of copy and
1,888 combinations before names and counts go in. The praise lines carry no
call to action on purpose: the ask is the one line at the end, and a message
that asks twice reads like boilerplate.

`shoutout_theme` picks the flavour: `"auto"` (default) reads the channel's
current category from Helix, or force one of `"trucking"`, `"zwift"`,
`"fortnite"`, `"generic"`. Categories are matched by substring, so *Euro Truck
Simulator 2*, *American Truck Simulator*, *SnowRunner* and *MudRunner* all
reach trucking. Anything unrecognised, and any moment the channel is offline,
falls back to generic — never to a guess.

Every part of that line is read off something real. The name and the viewer
count come from the raid notice Twitch itself sends (`USERNOTICE` with
`msg-id=raid`). The affiliate/partner line, the follower count and the raider's
current game come from the Helix API. The link is built from the raider's
**login**, never their display name — display names carry capitals, spaces and
unicode that a URL does not. If the login cannot be a Twitch login (4-25
characters of `a-z`, `0-9` and `_`) then no link is posted at all, because a
dead link in a shoutout is worse than no link. If nothing trustworthy can be
said, the bot says nothing.

**How the game is known, and why the tense changes.** Two endpoints, two
different facts, and the wording tells you which one it came from:

| They are | Source | Twitch's own wording for the field | The shoutout says |
| -------- | ------ | ---------------------------------- | ----------------- |
| live now | `GET /helix/streams` | `game_name` is "the category or game **being streamed**" | "live right now playing Fortnite" |
| offline | `GET /helix/channels` | `game_name` is "the game that the broadcaster **is playing or last played**" | "last seen playing Fortnite" |
| neither | — | — | no game named at all |

Get Streams is the wrong tool for a channel that is not live: it returns
`{"data": [], "pagination": {}}` for anyone offline, so it can never say what
they were on before. Get Channel Information is the only Helix endpoint that
publishes a *last* category, and it needs no scope, so the token the bot
already holds can read another channel's.

```
SO | 🎮 {name} joined the squad! ... They're live on Fortnite right now, so go
catch them before the match ends.

SO | 🪂 Trader landed hot! ... Their last lobby was Fortnite.
```

`mock_shoutout_test.py` checks the two pools against each other rather than
looking for one phrase: no past-tense line may contain "right now",
"currently", "this minute", "as we speak" or "live on", and no present-tense
line may contain "last", "were on", "logged off" or "signed off". Rewording
the copy cannot quietly break that.

Being live wins when both are available: the present tense is the stronger
fact, and the stale category from Get Channel Information is dropped rather
than printed alongside it. Neither endpoint answering, or no category ever
set, means no game is named.

**How the message is built.** Each part is a whole sentence — opener, the size
of the raid, the praise, the Twitch facts, then the link — and the sentences are
joined rather than slots being filled inside one sentence. That is deliberate.
Two separate bugs came from the other approach: a template that hardcoded "a"
in front of a slot whose values carried their own article, and a slot used
twice that read correctly for mass nouns and wrongly for count nouns. Five more
showed up in the first draft of these themed pools, all of them collisions
between clauses that were each fine on their own: *"they just raided in! They
raided in with 7 in tow"*, and a praise line ending "show them some love"
immediately before the call to action that says it again. `mock_shoutout_test.py`
now sweeps 6,000 mixed combinations and fails on any of them.

The Helix lookup is best-effort. A raid is the worst possible moment to be
waiting on a network call, and the token can be expired, so the name-and-count
shoutout is always available as the floor. The bot also never claims to know
what the raider is streaming, because the profile lookup does not fetch that.

A raid is answered without checking whether the raider follows the channel.
They usually do not, and they carry no badges in this message — running a raid
through the normal access gate would refuse the one thing that should always be
answered.

| Switch | Effect |
| ------ | ------ |
| `!so off` / `!so on` / `!so status` | Moderator switch, lasts until the bot restarts. Silent for viewers. |
| `!so <name>` | Shout a channel out by hand. **Moderators only.** |
| `!so` (no argument) | Shows usage rather than guessing. |
| `"shoutout_enabled": false` | Turns both paths off in `config.json`. |
| `"shoutout_theme"` | `"auto"` (follows the current category) or `trucking` / `zwift` / `fortnite` / `generic`. |

## Commands your mods write themselves

A moderator can add a command in chat, with no restart and no editing files:

```
!cmd add discord Join us on Discord - discord.gg/example
!cmd list
!cmd edit discord New link: discord.gg/example
!cmd delete discord
```

`!discord` then answers for anyone who passes the normal access gate. `{user}`
is replaced with whoever asked:

```
!cmd add hi G'day {user}, welcome aboard
```

What is *not* up to the moderator:

* **A custom command cannot shadow a built-in.** `!cmd add help ...` is
  refused. If it were allowed, `!help` would still look like it worked while
  meaning something else.
* **The message is capped at 380 characters**, and an over-long one is refused
  with its length named rather than cut off mid-sentence when posted.
* **Names are 2-25 characters** of `a-z`, `0-9` and `_`, starting with a
  letter. Upper case is folded, not refused.
* **They still go through the access gate and the channel cooldown** — a custom
  command is not a way around the rate limit.
* **`!bot off` silences them**, though a moderator can still define one while
  the bot is paused, so switching the bot off is not a one-way trip.

They live in `custom_commands.json` next to the code, written atomically, so
they survive a restart. An unreadable or wrong-shaped file is reported and
ignored rather than stopping the bot, and a hand-edited file still cannot
hijack a built-in name. There is a ceiling of 100 commands.

`!cmd list` is readable by anyone — the commands are used in public chat, so
there is nothing to hide. Everything else is moderator-only and **silent** for
viewers, so it cannot be used to make the bot spam. An unknown placeholder such
as `{usr}` is left visible rather than blanked, so a typo shows up instead of
quietly eating text.

`"custom_commands_enabled": false` in `config.json` stops them running without
deleting them, and `!cmd list` names that as the reason.

## Testing without Twitch

- **Full self-test** (recommended): runs the login check + sample fact lookups
  with no chat connection at all:
  ```
  python3 bot.py --selftest
  ```
  Works whether or not your channel is live — Twitch *chat* is available 24/7
  regardless of stream status, but this tests everything from the terminal.
- Test just the fact lookup:
  ```
  python3 funfacts.py "Milford, PA"
  python3 funfacts.py --spicy "Las Vegas, NV"   # adult mode
  ```
- **Check which fixes the copy you are running actually has** — after
  downloading a zip or applying a patch, this removes all guesswork:
  ```
  python3 check_fixes.py
  ```
  It prints one line per fix (`[x]` present, `[ ]` missing) and exits non-zero
  if anything is absent.
- **Trace retrieval, not just the LLM**: `--debug` (or `TWITCH_DEBUG=1`) also
  prints which source answered and the exact seed pool handed to the model,
  so a wrong fact can be traced to its source instead of guessed at:
  ```
  [funfacts] source=duckduckgo place='Girard, Ohio' facts=3
  [funfacts]   seed 1: It is believed that Girard takes its name from Stephen Girard…
  ```
- **Replay a real lookup with no network** (for CI, or any machine that can't
  reach Wikipedia):
  ```
  python3 mock_live_test.py
  ```
  `fixtures/wiki_girard.json` holds the MediaWiki responses recorded for
  `!funfact girard, OH`. The harness swaps out `_http_get_json` only, so the
  real `_wiki_search_extracts → _wikipedia → _ranked_facts → get_funfact` chain
  runs end to end — including the two bugs that made Girard answer with
  invented crime: plain-text extracts are capped at **one page per request**
  (the API returns a `continue` token, which must be followed), and
  `"United States"` in a lead sentence is *not* another region (matching it
  discarded the town's own article and pushed the lookup onto web search).
- **See exactly what's sent to the AI**: run with `--debug` (or set
  `TWITCH_DEBUG=1` / `"debug": true` in config.json). Every LLM call prints
  the full system prompt, user prompt (including the real facts found), model,
  URL, and the raw response:
  ```
  python3 bot.py --debug
  python3 funfacts.py --debug --spicy "Blooming Grove, PA"
  ```
- Test the login flow against a fake Twitch OAuth server (no account needed):
  ```
  python3 mock_auth_test.py
  ```
- Test the full bot against a fake IRC server (no account needed):
  ```
  python3 mock_test.py
  ```
- Test the extra commands (joke/randomfact/riddle/wyr):
  ```
  python3 mock_extras_test.py
  ```

## Troubleshooting: `HTTPError 401` in the access log

```
[access] helix users lookup failed for 'someone': <HTTPError 401: 'Unauthorized'>
[access] funfact denied for someone: could not verify your follow status (tier=unknown)
```

A 401 means **Twitch rejected the bot's access token**. It is not about the
viewer, and it is not about the name in the log line. It also explains why it
seems intermittent: the token is valid for about four hours, so everything
works after a login or a restart and then starts failing.

Two independent causes, and the first is the common one:

**Run this first:**

```
python3 bot.py --doctor
```

It reads your actual `config.json` and `tokens.json` and prints the reason,
including the two that leave no trace in the log — a `tokens.json` saved for a
different `client_id`, and a missing refresh token. It never prints a secret,
only whether one is present. It also attempts a real renewal so you can see
whether one can happen at all.

**1. There is no `client_secret` in config.json.** A Confidential app (the Dev
Console default) cannot renew a token without one, so when the four hours run
out the login simply dies and stays dead. The bot says so at startup:

```
[auth] no client_secret in config.json - if your app is a Confidential client
the login expires in about 4 hours and will need 'python3 bot.py --login'
again. Set the app's client type to Public, or add the secret.
```

Fix it once: Dev Console → your app → **New Secret**, put it in config.json as
`"client_secret": "..."`, then run `python3 bot.py --login`. Alternatively set
the app's **client type to Public**, which needs no secret. Either way the bot
then renews itself indefinitely.

**2. The token expired before the keeper got to it.** Fixed in
`auth.REFRESH_MARGIN` (one hour). The token keeper wakes every 30 minutes but
the old code only renewed within 120 seconds of expiry — so the keeper kept
finding a token that was still good for another half hour, reusing it, and
never refreshing at all. The token was only renewed *after* it had died, which
produced up to 30 minutes of 401s every four hours **even with a valid
secret**.

**3. `tokens.json` was saved for a different app.** If `client_id` in
config.json is not the one in `tokens.json`, the saved login can never be
renewed. This used to fail completely silently — the bot ran fine for four
hours and then every Helix call 401s, with nothing in the log to say why. It
now says so once, at the first failure:

```
[auth] tokens.json was saved for client_id 'OTHER-APP' but config.json has
'abc123'. They must match, or the saved login can never be renewed.
```

`tokens.json` lives next to `bot.py`, not in the working directory.

Either way, a 401 now recovers on its own: the client asks for a fresh token
and retries the request once. Before that, a single 401 was terminal — it was
logged and every follow check stayed dead until the process was restarted.

If it still fails, the log now names the cause rather than leaving you to
guess, and chat is told the bot's login expired rather than being accused of
not following.

## Troubleshooting: "the facts aren't adult"

If `!funfact` returns plain, dry facts, the LLM writer isn't running. Check:

1. `"spice": "spicy"` is set in `config.json` (or `TWITCH_SPICE=spicy`).
2. An LLM is configured: `llm_api_key` (or `GROQ_API_KEY` /
   `OPENROUTER_API_KEY`), **or** `llm_base_url` points at a local Ollama
   (`http://localhost:11434/v1`, or `OLLAMA_MODEL` is set).
3. You restarted the bot after changing the config.

Then run `python3 bot.py --selftest` — it prints a **LLM writer** section with
a live test and a sample adult fact, plus loud warnings when nothing is
configured or spice is off.

If the facts come back *reworded but still not adult*, that's the **hosted
model's content filter** — Groq/OpenRouter will pun but won't do real risqué.
Switch to a local Ollama model (see above); that's the only reliable way to get
genuinely adult output.

Without any LLM the bot intentionally posts plain (real) facts — it never
appends fake joke comments.

## Notes & limits

- **Twitch chat limits**: normal accounts are capped (~20 msgs / 30 s, ~100 per 30 s for mods).
  The 5-second cooldown keeps this safe.
- **Smart place matching**: the bot checks that the article it finds actually matches
  the place asked for — a typo like `!funfact Mount Cobb, py` still finds
  Mount Cobb, Pennsylvania instead of a random person named Cobb, and a state/country
  (`!funfact Springfield, MO`) picks the right Springfield.
- **Wikipedia rate limits**: anonymous Wikipedia API use is capped per IP, so
  the bot is frugal: lookups use *batched* requests (one search + one bulk
  text fetch for all hits, instead of a request per article) and are paced to
  ~4/sec. If Wikipedia still returns a `429`, the bot backs off for 45 seconds
  and tells chat the sources are busy (retry in a moment) rather than a slow
  retry storm — and never claims a town has "no facts" just because the source
  was throttled. On a shared cellular/hotspot IP the limit is easier to hit;
  adding a non-Wikipedia source (Google CSE, Serper, Brave — see the LLM/Google
  sections) makes the bot immune to it.
- **Message pacing**: chat replies are spaced ~1.3s apart so answers never bunch
  up and trip Twitch's "sending messages too quickly" limit.
- The bot is designed to never crash chat on a bad fact — every lookup is wrapped,
  messages are sanitised and truncated to the configured limit.
- **Login**: `tokens.json` holds the bot's login and is created with restrictive
  file permissions (`chmod 600`). Never share it or paste it in chat. To revoke,
  delete `tokens.json` (and, if you set a Client Secret, disconnect the app in the
  Twitch Developer Console).
- Want smarter/funnier facts? The lookup lives in `funfacts.py` — you can add a
  Google Custom Search or an LLM API as another source by writing a function that
  returns `{"place": str, "fact": str}` and calling it from `get_funfact()`.

## Files

| File                 | Purpose                                        |
| -------------------- | ---------------------------------------------- |
| `bot.py`             | Twitch IRC bot (connection, chat, commands).    |
| `auth.py`            | Twitch login (device-code flow) + auto-refresh. |
| `adminpanel.py`      | The password-protected web admin panel.         |
| `funfacts.py`        | Fact lookup + ranking, spice, rotation, caching.|
| `extras.py`          | Extra commands (joke/randomfact/riddle/wyr).    |
| `reminders.py`       | `!reminder` parsing, scheduling, persistence.   |
| `haul.py`            | The `!haul` board.                              |
| `customcmds.py`      | `!cmd` - moderator-defined commands.            |
| `shoutout.py`        | The raid shoutout wording.                      |
| `trucker.py`         | The `!cb` radio chatter generator.              |
| `beefstats.py`       | Beef-game state: leaderboard, !revenge window, tagging gate. |
| `beefllm.py`         | Optional LLM pass for beef acts — validates or falls back.   |
| `access.py`          | Who may use a command, and how often.           |
| `whois.py`           | `!whois` (Wikipedia) and `!twitch` (Helix).     |
| `names.py`           | The `!smk` name pool + Wikipedia top-up.        |
| `storage.py`         | Atomic JSON writes for the bot's state files.   |
| `ongoing.py`         | The chat AI's working memory: the quiz it hosts, the counts it keeps. |
| `spicy_facts.json`   | Curated adult-rated facts (editable).           |
| `llm.py`             | LLM writer for spicy facts (Ollama / Groq / OpenRouter). |
| `config.example.json`| Sample configuration.                           |
| `mock_test.py`       | Offline end-to-end bot test.                    |
| `mock_auth_test.py`  | Offline login-flow test.                        |
| `mock_facts_test.py` | Offline fact-logic tests.                       |
| `mock_live_test.py`  | End-to-end lookup replayed from recorded API responses. |
| `fixtures/`          | Recorded MediaWiki responses for the replay.    |
| `check_fixes.py`     | Prints which fixes are present in the copy you're running. |
| `mock_extras_test.py`| Offline extra-command tests.                    |
| `mock_reminders_test.py` | Offline reminder and haul tests.          |
| `mock_whois_test.py` | Offline `!whois` / `!twitch` tests.             |
| `mock_names_test.py` | Offline `!smk` name-pool tests.                 |
| `mock_subgoal_test.py` | Offline !subgoal tests.                        |
| `mock_trucker_test.py` | Offline `!cb` chatter tests.                  |
| `mock_adminpanel_test.py` | Offline admin-panel tests (real HTTP on loopback). |
| `mock_beef_test.py`  | Offline `!beef` story tests.                    |
| `mock_beefstats_test.py` | Offline leaderboard / `!revenge` / tagging tests. |
| `mock_beefllm_test.py` | Offline LLM-pass tests, incl. a fake OpenAI server. |
| `mock_shoutout_test.py` | Offline raid-shoutout tests.                 |
| `mock_customcmds_test.py` | Offline `!cmd` tests.                      |
| `tokens.json`        | Created on first login; holds the saved login.  |
| `reminders.json`     | Pending reminders; written at runtime.          |
| `haul.json`          | The current haul; written at runtime.           |
| `beef_state.json`    | The beef leaderboard and !revenge windows.      |
| `ongoing.json`       | The game being hosted and the counts kept; written at runtime. |
| `custom_commands.json` | Mod-defined commands; written at runtime.     |
| `names.json`         | Harvested `!smk` names; written at runtime.     |
s.                        |
| `mock_trucker_test.py` | Offline `!cb` chatter tests.                  |
| `mock_beef_test.py`  | Offline `!beef` story tests.                    |
| `mock_beefstats_test.py` | Offline leaderboard / `!revenge` / tagging tests. |
| `mock_beefllm_test.py` | Offline LLM-pass tests, incl. a fake OpenAI server. |
| `mock_shoutout_test.py` | Offline raid-shoutout tests.                 |
| `mock_customcmds_test.py` | Offline `!cmd` tests.                      |
| `tokens.json`        | Created on first login; holds the saved login.  |
| `reminders.json`     | Pending reminders; written at runtime.          |
| `haul.json`          | The current haul; written at runtime.           |
| `beef_state.json`    | The beef leaderboard and !revenge windows.      |
| `custom_commands.json` | Mod-defined commands; written at runtime.     |
| `names.json`         | Harvested `!smk` names; written at runtime.     |
