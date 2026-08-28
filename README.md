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
| `!reminder 60mins …`  | Post a message later. **Moderators only.**                    |
| `!help`               | Lists the commands and who may use them.                      |
| `!bot off` / `!bot on`| Moderator kill switch for every command.                      |

Places can be given as `City, ST`, `City, Country`, a landmark, etc. —
whatever you'd type into a search box. The extra commands come from free,
keyless APIs and can be disabled with `"fun_commands": false`.

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
   random one on repeat calls. **This is what makes spicy mode actually spicy**
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

Ten minutes with nobody saying anything, and the bot posts one of the five
entertainment commands at random to give chat something to react to:

```
[04:12:31] chat idle for 600s - posting !smk
ShagMarryKill [male] | Harrison Ford (actor), Viggo Mortensen (actor), Pedro Pascal (actor) - shag one, marry one, kill one.
```

```json
"idle_chat_enabled": true,
"idle_chat_minutes": 10,
"idle_chat_commands": ["smk", "riddle", "joke", "randomfact", "wyr"]
```

Anything **anyone** says resets the clock — not just commands, and not just
messages the bot acts on. The clock also resets after each post, so it is one
per quiet window rather than one every time the checker wakes up.

**It only fires while the channel is actually streaming.** A bot posting jokes
into an offline room every ten minutes is not a feature; it is a channel that
looks broken when the streamer comes back. The live check uses
`GET /helix/streams`, which needs no scope, so the token the bot already holds
is enough — no extra `--login`.

Where the check cannot be settled (no token, no `client_id`, Twitch
unreachable) it posts anyway. A missed check must not silently switch the
feature off, and "unknown" is not the same as "offline".

`!bot off` silences it, as does `"fun_commands": false`. An `!riddle` posted
this way still reveals its answer on the timer.

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


## Truck talk on the radio

The bot can mutter to itself on the CB while you stream. Three voices, picked
at random each time so chat cannot learn the tone either:

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
!cb off      - silence the random chatter (mods and broadcaster only)
!cb on       - bring it back
!cb status   - which state it is in
```

`!cb off` lasts until the next restart; `cb_chatter_enabled` in config.json is
the permanent setting. That is the same split `!bot off` uses. A viewer who
types `!cb off` gets no answer at all, so the switch cannot become a spam
vector — and unlike `!bot off`, it does not silence anything else.

**The timing is deliberately not a fixed period.** `cb_chatter_minutes` is an
*average*: the gap before each post is re-rolled between 40% and 200% of it, so
a 25-minute setting produces gaps anywhere from 10 to 50 minutes and chat
cannot settle into a rhythm. The lower bound also means it can never fire twice
in quick succession.

Three things hold it back:

* **The channel has to be streaming.** Same live check as the idle poster.
* **It will not talk over an active conversation.** If anyone has spoken in
  the last 60 seconds it waits and tries again shortly.
* **`!bot off` silences it**, along with everything else.

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
| `funfacts.py`        | Fact lookup + ranking, spice, rotation, caching.|
| `extras.py`          | Extra commands (joke/randomfact/riddle/wyr).    |
| `reminders.py`       | `!reminder` parsing, scheduling, persistence.   |
| `haul.py`            | The `!haul` board.                              |
| `whois.py`           | `!whois` (Wikipedia) and `!twitch` (Helix).     |
| `names.py`           | The `!smk` name pool + Wikipedia top-up.        |
| `storage.py`         | Atomic JSON writes for the bot's state files.   |
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
| `mock_idle_test.py`  | Offline idle-chat tests.                        |
| `tokens.json`        | Created on first login; holds the saved login.  |
| `reminders.json`     | Pending reminders; written at runtime.          |
| `haul.json`          | The current haul; written at runtime.           |
| `names.json`         | Harvested `!smk` names; written at runtime.     |
