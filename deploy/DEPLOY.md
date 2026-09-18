# Deploying the bot 24/7 in the cloud

This bot is a tiny standard-library Python script — it runs happily on the
smallest, cheapest cloud VM (256 MB RAM is plenty). This guide gets it running
24/7 under `systemd`, so it answers `!funfact` even when your PC is off.

## Step 0 — Pick a host

Any Linux VM works. What the bot needs from a host is small: **outbound**
network only (Twitch IRC over TLS, HTTPS to Wikipedia and the AI / weather /
news APIs — no inbound port, no web server), well under 256 MB of RAM, Python
3.8+, and a disk that **survives restarts** — `config.json`, `tokens.json`,
`persona.json`, `subgoal.json` and the memory database live next to `bot.py`.
That last point is what rules out most "free web app" platforms.

Free/cheap options that fit (checked September 2026):

| Host | Cost | Notes |
|---|---|---|
| **Oracle Cloud "Always Free"** | $0 forever | Best free fit. Up to 2 Arm OCPUs / 12 GB total on `VM.Standard.A1.Flex` (halved from 4 / 24 GB in June 2026) **or** two 1 GB AMD `VM.Standard.E2.1.Micro` VMs, 200 GB of storage, public IP included. Always Free only exists in the tenancy's **home region**, which is a permanent choice. Signup needs a card (not charged). Quirks: "Out of host capacity" is common for A1 — ask for 1 OCPU / 6 GB, or take an E2.1.Micro (1 GB is plenty for this bot); a free-only tenancy can have a VM that Oracle deems *idle* for 7 days **stopped** (restart it, or upgrade to Pay As You Go — still $0 inside the Always Free limits, and set a budget alert — which is Oracle's documented way to opt out of reclamation). Pick the **Ubuntu** image (or Oracle Linux 9+): Oracle Linux 8 ships `python3` 3.6, older than the bot's 3.8 minimum. |
| **Google Cloud `e2-micro`** | VM $0 forever; budget ≈ $3.65/mo for its IPv4 | One `e2-micro` (2 shared vCPU, 1 GB) + 30 GB **standard** persistent disk (`pd-standard`, not the default balanced disk) free forever, but **only** in `us-west1`, `us-central1` or `us-east1` — elsewhere ≈ $7/mo. In late 2025 Google dropped the "external IP is free with the e2-micro" clause; the network price list now gives one free hour a month and $0.005/h after that, and the bot needs a public address to reach Twitch, so check the first invoice. Card needed, and the 90-day trial must be upgraded to a paid billing account or the VM is deleted with it. |
| **Azure B1s** / **AWS credits** | $0 for 12 / 6 months, then paid | Azure free accounts get 750 h/mo of a B1s VM for 12 months; AWS accounts opened after 15 July 2025 get $100–200 of credits for up to 6 months, after which the account closes unless upgraded. Fine for a trial, not a permanent home. |
| **Hetzner / DigitalOcean / Vultr** | ~$4–6/mo | Simplest & most reliable if you'd rather not deal with free-tier quirks. |
| **A Raspberry Pi / old laptop at home** | $0 + power | Not cloud, but the bot is standard-library Python, so anything that runs Linux 24/7 does the job. |

**Free hosts that do *not* work for this bot:** Render's free tier only runs
web services, spins them down after 15 minutes without inbound traffic and
wipes local files on every restart (no free background workers or cron);
Fly.io, Railway, Heroku, Replit and Glitch no longer offer an always-on free
tier; PythonAnywhere free accounts cannot run always-on tasks and only reach
whitelisted sites; Google Cloud Shell and GitHub Actions are not meant for a
24/7 process; serverless free tiers (Cloud Run, Vercel, Cloudflare Workers) bill per
request and idle their containers in between, so they cannot hold an IRC
connection open for free.

The rest of this guide is identical on any of them (Ubuntu/Debian assumed).

## Step 1 — Put the code on GitHub (recommended)

This makes deploying *and* updating one command each:

```bash
# On your PC (E:\funfact-bot), once:
git init
git add .
git commit -m "funfact bot"
git branch -M main
git remote add origin https://github.com/YOURNAME/funfact-bot.git
git push -u origin main
```

> Make sure `tokens.json` is NOT committed (it's a secret). Add a `.gitignore`:
> ```
> config.json
> tokens.json
> bot.env
> __pycache__/
> ```

## Step 2 — Get it onto the server

```bash
ssh root@YOUR-SERVER-IP        # or your cloud console's SSH button

# Create the bot user and folder
useradd -r -m funfact
mkdir -p /opt/funfact-bot && chown funfact:funfact /opt/funfact-bot

# Clone the repo (preferred) …
sudo -u funfact git clone https://github.com/YOURNAME/funfact-bot.git /opt/funfact-bot
# …or, if you didn't use GitHub, copy the zip up and unzip it instead:
#   scp funfact-bot-latest.zip root@YOUR-SERVER-IP:/tmp/
#   unzip /tmp/funfact-bot-latest.zip -d /opt/funfact-bot
```

Python 3 is usually already installed (`python3 --version`). If not:
`sudo apt update && sudo apt install -y python3 git`.

## Step 3 — Config + secrets

```bash
cd /opt/funfact-bot

# 1) config.json — copy the shape from config.example.json
sudo -u funfact cp config.example.json config.json
sudo -u funfact nano config.json
#    Fill in at least:  "nick", "channel", "client_id", "spice".
#    Leave oauth_token empty — the device login fills tokens.json.

# 2) bot.env — LLM key + spice mode (kept out of config.json)
sudo -u funfact cp deploy/bot.env.example bot.env
sudo -u funfact nano bot.env   # put your OPENROUTER_API_KEY etc. here
```

## Step 4 — Log in once (works over SSH, no browser on the server)

```bash
cd /opt/funfact-bot
sudo -u funfact python3 bot.py --login
```

It prints a short code and an activation URL. Open that URL **on your own PC**,
log in as the bot account, click **Authorize**. The server saves the login to
`tokens.json` and — from now on — refreshes it automatically every 30 minutes
and before every reconnect, so you never have to log in again.

> **Upgrading an existing install?** The bot now also asks for
> `moderator:manage:banned_users` and `user:manage:whispers` — the scopes
> behind `!ban` / `!timeout` / `!unban` (Twitch switched the IRC `/ban`
> commands off in Feb 2023, so moderation is a Helix call, and the bot has
> to be a moderator of the channel). A token issued before them will get a
> 401, so run `python3 bot.py --login` once after the upgrade. Check what
> the stored token actually has with `sudo -u funfact python3 bot.py --doctor`;
> it names any missing scope and says the same thing.

> Already logged in on your PC? You can skip the login by copying the saved
> tokens over instead:
> `scp E:\funfact-bot\tokens.json funfact@YOUR-SERVER-IP:/opt/funfact-bot/`
> (then `sudo chown funfact:funfact /opt/funfact-bot/tokens.json`).

## Step 5 — Install as a service (auto-start + auto-restart)

```bash
sudo cp /opt/funfact-bot/deploy/funfact-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now funfact-bot
sudo systemctl status funfact-bot     # should show "active (running)"
```

## Step 6 — Watch the logs / debug

```bash
sudo journalctl -u funfact-bot -f          # follow live
sudo journalctl -u funfact-bot -n 200      # last 200 lines
```

To see exactly what's being sent to the AI, set `TWITCH_DEBUG=1` in `bot.env`,
then `sudo systemctl restart funfact-bot`.

## Step 7 — The admin panel (the console window, from your phone)

The bot can serve a small password-protected web page: dashboard (connected,
AI provider and which models are resting, Twitch login health), the live log,
pause/resume and the `!so` / `!cb` / `!beef` switches, speak as the bot, the
standing notice, reminders, custom commands, haul, sub goal, voice, viewer
memory, a `config.json` editor with masked secrets, panel users, a Twitch
re-login and a **Restart** button. Details and the security model are at the
top of `adminpanel.py`.

```bash
cd /opt/funfact-bot
sudo -u funfact python3 bot.py --admin-user yourname          # prompts for a password (10+ chars)
sudo -u funfact python3 bot.py --admin-user modname --role mod  # optional: a moderator login
sudo -u funfact nano config.json        # "admin_panel_enabled": true
sudo systemctl restart funfact-bot
sudo journalctl -u funfact-bot | grep '\[admin\]'   # "[admin] panel on http://127.0.0.1:8477 ..."
```

A **mod** login gets the operational switches, chat, reminders, commands and
the stream tab; only an **admin** sees config, secrets, viewer memory, the
system tab and the Restart button.

### Reaching it — pick one

The panel listens on `127.0.0.1` by default, so nothing on the internet can
see it. That is deliberate: a login form on a public IP gets hammered by bots
within the hour. Two good ways to reach it, both free:

**A. Tailscale (recommended — works from your phone).** Tailscale is a free
private network between your own devices; the VM gets a `100.x.y.z` address
only your devices can reach, and Oracle's public firewall never sees the
panel at all.

```bash
# on the VM
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up                       # prints a login URL - open it once
tailscale ip -4                         # e.g. 100.101.102.103
sudo -u funfact nano config.json        # "admin_panel_bind": "100.101.102.103"
sudo systemctl restart funfact-bot
```

Install the Tailscale app on your PC/phone, log in with the same account, and
open `http://100.101.102.103:8477` (or `http://<vm-name>:8477` with MagicDNS).
Nothing to open in Oracle's security list, no domain, no certificate. If you
want HTTPS on top, `sudo tailscale serve --bg 8477` gives you a
`https://<vm-name>.<tailnet>.ts.net` URL with a real certificate, still
private to your tailnet.

**B. SSH tunnel (nothing to install on the VM).** Leave the bind on
`127.0.0.1` and, on your PC:

```bash
ssh -L 8477:127.0.0.1:8477 ubuntu@YOUR-SERVER-IP
```

then open `http://localhost:8477` while that SSH session is open. Works from
any laptop with an SSH client (Windows 10+ has one built in); awkward from a
phone.

**Not recommended: a public bind.** The panel refuses `0.0.0.0` / a public IP
unless `admin_panel_public` is also `true`. If you really want it on the open
internet, put a reverse proxy with HTTPS (Caddy is a two-line config) and a
firewall rule in front of it - the panel itself speaks plain HTTP and its
login cookie is only marked `Secure` when a proxy tells it the request came
in over HTTPS (`X-Forwarded-Proto: https`).

## Updating later

```bash
sudo -u funfact git -C /opt/funfact-bot pull
sudo systemctl restart funfact-bot
```

## Troubleshooting

- **Bot starts, then "couldn't find any fun facts"?** The server's IP may be
  rate-limited by Wikipedia if something else on it queries a lot; it clears
  itself in ~45s. Persistent? A different region/host usually fixes it.
- **Browser keeps asking to authorize every run?** `tokens.json` is missing from
  `/opt/funfact-bot/` — redo Step 4 and make sure the file persists.
- **Spicy facts come out plain?** The LLM isn't configured — check `bot.env` was
  copied to `/opt/funfact-bot/bot.env` and `systemctl status` shows the env
  file loaded (no "EnvironmentFile" warnings).
- **Panel says "could not write config.json" / state files never save?** The
  unit's `ProtectSystem=strict` makes the disk read-only except for
  `ReadWritePaths=/opt/funfact-bot` — if you installed to another folder,
  change that line too (`sudo systemctl daemon-reload` afterwards).
- **`[admin] panel enabled but admin_users.json has no users`?** Run
  `python3 bot.py --admin-user yourname` as the `funfact` user, in
  `/opt/funfact-bot`, so the file lands next to `bot.py`.
- **Locked out of the panel?** Wait 15 minutes, or reset the password with
  the same `--admin-user` command (it overwrites), then restart the service.
