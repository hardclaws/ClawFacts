# Deploying the bot 24/7 in the cloud

This bot is a tiny standard-library Python script — it runs happily on the
smallest, cheapest cloud VM (256 MB RAM is plenty). This guide gets it running
24/7 under `systemd`, so it answers `!funfact` even when your PC is off.

## Step 0 — Pick a host

Any Linux VM works. Free/cheap options:

| Host | Cost | Notes |
|---|---|---|
| **Oracle Cloud "Always Free"** | $0 forever | 4-core ARM VM, great deal; signup needs a card (not charged); capacity can be flaky. |
| **Google Cloud `e2-micro`** | $0 forever | Free tier VM in us-west1/us-central1/us-east1; needs a card (not charged). |
| **Hetzner / DigitalOcean / Vultr** | ~$4–6/mo | Simplest & most reliable if you'd rather not deal with free-tier quirks. |

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
