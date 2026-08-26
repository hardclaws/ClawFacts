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
   - **`spicy_facts.json`** — a built-in database of verified adult-rated facts (used only in spicy mode).
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
| `client_secret`    | Optional; only needed to revoke the login later.               |
| `channel`          | Channel to join, e.g. `#your_channel`.                         |
| `prefix`           | Command prefix (default `!`).                                  |
| `cooldown_seconds` | Minimum seconds between lookups (default 5).                   |
| `spice`            | `"clean"` or `"spicy"` — adult-rated facts (default `clean`).  |
| `max_fact_chars`   | Max length of the fact itself (default 200).                   |
| `llm_api_key`      | Groq/OpenRouter/OpenAI-compatible API key (leave empty for local Ollama). |
| `llm_base_url`     | LLM API base URL (default `https://api.groq.com/openai/v1`; Ollama = `http://localhost:11434/v1`). |
| `llm_model`        | LLM model (default `openai/gpt-oss-120b`; Ollama e.g. `llama3.1:8b`). |
| `google_api_key`   | Optional Google Custom Search API key.                         |
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

## Running it 24/7

The bot is a tiny standard-library Python script — the easiest way to keep it
always-on (so it answers chat even when your PC is off) is a small free cloud VM
running it under `systemd`. A complete step-by-step guide (host options,
GitHub setup, device login over SSH, systemd unit, logs) is in
**[`deploy/DEPLOY.md`](deploy/DEPLOY.md)**, with ready-made
`deploy/funfact-bot.service` and `deploy/bot.env.example` files.

Quick-and-dirty local alternatives:

- **`nohup`** — `nohup python3 bot.py >> bot.log 2>&1 &`
- **`screen` / `tmux`** — so you can reattach to it later.

  Then `sudo systemctl enable --now funfact-bot`.

## Chat commands

| Command               | Result                                                        |
| --------------------- | ------------------------------------------------------------- |
| `!funfact Milford, PA`| Posts a fun fact about the location.                          |
| `!funfact` (no arg)   | Posts usage instructions.                                     |
| `!joke`               | A random joke (free API).                                     |
| `!randomfact`         | A random useless fact (free API).                             |
| `!riddle`             | A riddle; the answer is posted ~45s later.                    |
| `!wouldyourather`     | A "would you rather" question (also `!wyr`).                  |

Places can be given as `City, ST`, `City, Country`, a landmark, etc. —
whatever you'd type into a search box. The extra commands come from free,
keyless APIs and can be disabled with `"fun_commands": false`.

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
   large language model with one prompt: *"Write up to 10 fun facts that are
   crime, funny, bizarre, or the place's most popular claims to fame, each ≤
   200 chars."* The model rewrites **only the supplied facts** (never invents
   its own), returns up to 10 one-liners, and the bot serves the top one first,
   then a random one on repeat calls. **This is what makes spicy mode actually
   spicy** — but see the note below: for real adult output you want a **local
   Ollama model**, because hosted models are filtered.

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
channel for those. Two hard output filters enforce this: any LLM line with
explicit sexual words is dropped, and any LLM line that invents a name or date
not in the real facts is dropped (a low-refusal model will otherwise make up a
"Devil Jack Schramm"-style story for boring towns). Spice comes from the
curated database and the search sources — never from the model's imagination.

### Optional Google search (surfaces racier local news Wikipedia skips)

If you want the bot to also search Google (the sanctioned way — Google blocks
scraping `google.com`, but offers a free API), set up **Programmable Search**:

1. Create a search engine: <https://programmablesearchengine.google.com/>
   → name it anything → tick **"Search the entire web"** → Create → copy the
   **Search engine ID** (that's `google_cx`).
2. Get an API key: <https://console.cloud.google.com/apis/credentials> →
   create an API key, and enable the **Custom Search JSON API**.
3. Put both in `config.json`:

```json
"google_api_key": "AIza...",
"google_cx": "abc123..."
```

The bot then queries Google with `safe=off`, which lets through the adult-rated
local news and scandal stories that Wikipedia omits, and mines those snippets
for facts. **Free tier = 100 queries/day**; beyond that Google charges.

> Note: Google returns short search snippets, not prose — so its facts are
> usually weaker than Wikipedia's. Its real value is (a) the `safe=off` adult
> content and (b) coverage of places with no Wikipedia article.

### Optional Serper search (easiest fix for "wikipedia rate-limited")

Wikipedia throttles anonymous API use **per IP** — on cellular/hotspot or a
shared home connection the limit is often already half-used before the bot
asks. The bot is frugal (batched requests, ~4/sec pacing), but the real cure
is a **second, independent search source**. [Serper](https://serper.dev) is the
simplest: sign up (free, no card), copy the one API key, and add:

```json
"serper_api_key": "abc123..."
```

(or set the `SERPER_API_KEY` environment variable.) Free tier is **2,500
queries**; beyond that it's ~$0.50 per 1,000. When Wikipedia is down, the bot
then answers from Serper's Google-quality results instead of giving up.

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
  automatically falls back to `llama-3.3-70b-versatile` if it's unavailable.
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
| `funfacts.py`        | Fact lookup + ranking, spice, rotation, caching.|
| `extras.py`          | Extra commands (joke/randomfact/riddle/wyr).    |
| `spicy_facts.json`   | Curated adult-rated facts (editable).           |
| `llm.py`             | LLM writer for spicy facts (Ollama / Groq / OpenRouter). |
| `config.example.json`| Sample configuration.                           |
| `mock_test.py`       | Offline end-to-end bot test.                    |
| `mock_auth_test.py`  | Offline login-flow test.                        |
| `mock_facts_test.py` | Offline fact-logic tests.                       |
| `mock_extras_test.py`| Offline extra-command tests.                    |
| `tokens.json`        | Created on first login; holds the saved login.  |
