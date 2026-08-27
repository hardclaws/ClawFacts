"""Twitch OAuth "just log in" helper.

Uses Twitch's Device Code Grant flow so the bot can log itself in:

    1. Ask Twitch for a device code   (POST /oauth2/device)
    2. Print a short code + an activation URL (the code is pre-filled in the URL)
    3. Poll /oauth2/token until the user clicks "Authorize"
    4. Save the access + refresh token to tokens.json (chmod 600)

After the first login, the saved token is reused and refreshed automatically,
so you only ever log in once.

The one thing Twitch still requires is an app Client ID (free, one-time
registration at https://dev.twitch.tv/console) — that is a platform
requirement for every OAuth login and can't be skipped.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

ID_HOST = "https://id.twitch.tv"
DEVICE_ENDPOINT = ID_HOST + "/oauth2/device"
TOKEN_ENDPOINT = ID_HOST + "/oauth2/token"
VALIDATE_ENDPOINT = ID_HOST + "/oauth2/validate"
ACTIVATE_URL = "https://www.twitch.tv/activate"

# moderator:read:followers is what lets the bot check whether a chatter
# follows the channel (access.py). It is not in the IRC tags, so there is no
# other way to enforce "followers only". Adding a scope means the stored
# token no longer has it - run `python3 bot.py --login` once after updating.
#
# Only scopes that actually exist belong in this list. It once carried
# "moderation:read:moderators", which is not a Twitch scope at all - the real
# one is "moderation:read" - and Twitch refused the whole device flow with
# "invalid scope requested", so the bot could not log in at all. The scope was
# pointless even under its correct name: Get Moderators requires broadcaster_id
# to equal the token's own user id, so a bot account that is not the
# broadcaster can never read another channel's moderator list. Whether *this*
# bot is a moderator comes from the USERSTATE line Twitch sends on join, which
# is free and needs no scope - see _note_own_state in bot.py.
SCOPES = "chat:read chat:edit moderator:read:followers"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"

TOKENS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tokens.json")


class OAuthError(Exception):
    """Raised when Twitch refuses a request or the flow fails."""


def _post(url: str, data: dict, timeout: float = 15.0) -> dict:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8", "replace"))
        except Exception:
            payload = {}
        raise OAuthError(payload.get("message") or f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise OAuthError(f"network error: {exc.reason}") from exc


def _get(url: str, headers: dict, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8", "replace"))
        except Exception:
            payload = {}
        raise OAuthError(payload.get("message") or f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise OAuthError(f"network error: {exc.reason}") from exc


def start_device_flow(client_id: str, scopes: str = SCOPES) -> dict:
    return _post(DEVICE_ENDPOINT, {"client_id": client_id, "scopes": scopes})


def poll_device_token(client_id: str, device_code: str, scope: str = SCOPES,
                      interval: int = 5, timeout: int = 1800, on_wait=None) -> dict:
    deadline = time.time() + timeout
    while True:
        try:
            return _post(TOKEN_ENDPOINT, {
                "client_id": client_id,
                "scope": scope,
                "device_code": device_code,
                "grant_type": DEVICE_GRANT,
            })
        except OAuthError as exc:
            msg = str(exc).lower()
            if "authorization_pending" in msg:
                if time.time() >= deadline:
                    raise OAuthError("Timed out waiting for authorization.") from exc
                if on_wait:
                    on_wait()
                time.sleep(interval)
                continue
            if "slow_down" in msg:  # Twitch asked us to poll less often
                interval = min(interval + 5, 30)
                if on_wait:
                    on_wait()
                time.sleep(interval)
                continue
            raise


def refresh_access_token(client_id: str, refresh_token: str,
                         client_secret: str | None = None) -> dict:
    data = {
        "client_id": client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    if client_secret:
        data["client_secret"] = client_secret
    return _post(TOKEN_ENDPOINT, data)


def validate_token(token: str) -> dict:
    return _get(VALIDATE_ENDPOINT, {"Authorization": f"OAuth {token}"})


# ---- token storage -----------------------------------------------------

def load_tokens() -> dict | None:
    try:
        with open(TOKENS_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def save_tokens(data: dict) -> None:
    try:
        with open(TOKENS_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.chmod(TOKENS_PATH, 0o600)
    except OSError as exc:
        print(f"[auth] could not save tokens.json: {exc}", file=sys.stderr)


def _normalize(token: str) -> str:
    token = (token or "").strip()
    if not token:
        raise OAuthError("Twitch returned an empty access token.")
    return token if token.lower().startswith("oauth:") else "oauth:" + token


def _store_tokens(data: dict, client_id: str) -> str:
    access = data.get("access_token")
    token = _normalize(access)
    expires_in = int(data.get("expires_in") or 0)
    record = {
        "access_token": access,
        "refresh_token": data.get("refresh_token"),
        "expires_at": (time.time() + expires_in) if expires_in else 0,
        "expires_in": expires_in,
        "scope": data.get("scope"),
        "client_id": client_id,
        "saved_at": time.time(),
    }
    try:
        info = validate_token(access)
        record["login"] = info.get("login")
        record["user_id"] = info.get("user_id")
    except OAuthError:
        pass
    save_tokens(record)
    return token


# ---- the interactive login ---------------------------------------------

def run_device_login(client_id: str) -> str:
    device = start_device_flow(client_id)
    user_code = device.get("user_code")
    verification_uri = device.get("verification_uri") or ACTIVATE_URL
    interval = int(device.get("interval") or 5)
    expires = int(device.get("expires_in") or 1800)

    print()
    print("=" * 64)
    print("  TWITCH LOGIN")
    print("=" * 64)
    print("  A browser window should open. If it doesn't, open this URL:")
    print(f"    {verification_uri}")
    print()
    print(f"  Code: {user_code}   (expires in {expires // 60} min)")
    print()
    print("  1. Log in to the account the BOT should use")
    print("  2. Click 'Authorize'")
    print("=" * 64)
    print("  Waiting for authorization", end="", flush=True)

    try:
        webbrowser.open(verification_uri)
    except Exception:
        pass

    def tick() -> None:
        print(".", end="", flush=True)

    try:
        data = poll_device_token(client_id, device.get("device_code"),
                                 interval=interval, timeout=expires, on_wait=tick)
    except OAuthError:
        print()
        raise
    print(" done!")
    return _store_tokens(data, client_id)


# ---- high-level entry points used by bot.py -----------------------------

def refresh_if_possible(cfg: dict) -> str | None:
    """Reuse or refresh a saved login WITHOUT any interactive step.

    Returns a working 'oauth:...' token, or None if only a fresh device login
    would help (the caller keeps whatever token it already has).

    Safe to call repeatedly while the bot runs: it validates the saved access
    token and, when that is close to expiring, silently swaps in a fresh one
    using the refresh token. After the first device login the bot stays logged
    in indefinitely.
    """
    client_id = (cfg.get("client_id") or "").strip()
    client_secret = (cfg.get("client_secret") or "").strip() or None

    tokens = load_tokens()
    if not tokens:
        return None
    if client_id and tokens.get("client_id") != client_id:
        return None  # tokens belong to a different app — don't touch them

    access = tokens.get("access_token")
    refresh = tokens.get("refresh_token")
    expires_at = float(tokens.get("expires_at") or 0)

    if access and expires_at > time.time() + 120:
        try:
            validate_token(access)
            return _normalize(access)
        except OAuthError:
            pass  # invalid despite expiry estimate — try refresh below

    if refresh and client_id:
        try:
            new = refresh_access_token(client_id, refresh, client_secret)
            print("[auth] refreshed access token.")
            return _store_tokens(new, client_id)
        except OAuthError as exc:
            print(f"[auth] refresh failed ({exc}); a fresh login is needed.",
                  file=sys.stderr)
    return None


def resolve_token(cfg: dict, force_login: bool = False) -> str:
    """Return a working 'oauth:...' token for the bot.

    Priority:
      1. oauth_token from config (unless --login forces a fresh login)
      2. tokens.json — reuse if valid, refresh if expired (no prompt)
      3. interactive device-code login (needs client_id)
    """
    oauth = (cfg.get("oauth_token") or "").strip()
    if oauth and not force_login:
        return _normalize(oauth)

    client_id = (cfg.get("client_id") or "").strip()

    if not force_login:
        token = refresh_if_possible(cfg)
        if token:
            return token

    if not client_id:
        raise OAuthError(
            "No saved login and no client_id in config.json. Register a free "
            "app at https://dev.twitch.tv/console to get a Client ID, put it "
            "in config.json, then run: python3 bot.py --login"
        )

    return run_device_login(client_id)
