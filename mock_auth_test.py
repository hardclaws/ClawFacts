"""Test auth.py against a fake Twitch OAuth server (no real Twitch needed).

Run:  python3 mock_auth_test.py
Covers: device login, token reuse, auto-refresh, and --login (force re-login).
"""

import http.server
import json
import os
import tempfile
import threading
import time
import urllib.parse

import auth

STATE = {"polls": 0, "refresh_calls": 0}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def _json(self, code, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
        path = urllib.parse.urlparse(self.path).path

        if path == "/oauth2/device":
            self._json(200, {
                "device_code": "DEVCODE1",
                "user_code": "ABCD-EFGH",
                "verification_uri": "http://127.0.0.1/activate?device-code=ABCD-EFGH",
                "expires_in": 1800,
                "interval": 1,
            })
        elif path == "/oauth2/token":
            grant = (body.get("grant_type") or [""])[0]
            if grant == "urn:ietf:params:oauth:grant-type:device_code":
                STATE["polls"] += 1
                if STATE["polls"] < 3:
                    self._json(400, {"status": 400, "message": "authorization_pending"})
                else:
                    self._json(200, {
                        "access_token": "fake_access",
                        "refresh_token": "fake_refresh",
                        "expires_in": 14400,
                        "scope": ["chat:read", "chat:edit"],
                    })
            elif grant == "refresh_token":
                STATE["refresh_calls"] += 1
                self._json(200, {
                    "access_token": "refreshed_access",
                    "refresh_token": "fake_refresh2",
                    "expires_in": 14400,
                    "scope": ["chat:read", "chat:edit"],
                })
            else:
                self._json(400, {"message": "invalid_request"})
        else:
            self._json(404, {"message": "not found"})

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/oauth2/validate":
            token = self.headers.get("Authorization", "").replace("OAuth ", "")
            if token in ("fake_access", "refreshed_access"):
                self._json(200, {
                    "client_id": "testclient",
                    "login": "testbot",
                    "user_id": "1",
                    "scopes": ["chat:read", "chat:edit"],
                    "expires_in": 14400,
                })
            else:
                self._json(401, {"status": 401, "message": "invalid access token"})
        else:
            self._json(404, {"message": "not found"})


# Every scope this bot asks for, checked against Twitch's published scope list
# (https://dev.twitch.tv/docs/authentication/scopes/). A name that is not in
# here does not exist, and Twitch rejects the ENTIRE device flow over one bad
# entry - not just that entry. That is what "invalid scope requested:
# 'moderation:read:moderators'" did: the bot could not log in at all.
KNOWN_GOOD_SCOPES = {
    "chat:read", "chat:edit", "chat:manage:moderated_messages",
    "moderator:read:followers", "moderator:read:chatters",
    "moderator:manage:banned_users", "moderation:read",
    "channel:moderate", "channel:manage:moderators",
    "user:read:moderated_channels", "user:read:email",
    "whispers:read", "whispers:edit",
}


def test_scopes_all_exist():
    """A single invented scope name locks the bot out of login entirely."""
    import auth
    requested = auth.SCOPES.split()
    bogus = [s for s in requested if s not in KNOWN_GOOD_SCOPES]
    assert not bogus, (
        f"these are not Twitch scopes and will abort the device flow: {bogus}")
    # The specific mistake that shipped once.
    assert "moderation:read:moderators" not in requested, (
        "that scope does not exist; the real one is 'moderation:read', and it "
        "is useless to a bot account anyway")
    # moderator:read:followers is load-bearing: without it the follower gate
    # cannot work at all.
    assert "moderator:read:followers" in requested, requested
    print(f"[PASS] all {len(requested)} requested scopes exist: "
          f"{' '.join(requested)}")


def test_confidential_refresh_needs_secret():
    """Their live log said exactly this, and it means the login dies in 4h.

    Twitch only lets a *Public* client refresh without a secret; the Dev
    Console defaults to Confidential. Device-flow access tokens last about
    4 hours, so a bot with no client_secret silently needs re-authorising
    four times a day - and the old message just said "a fresh login is
    needed", which sends you re-running --login forever without fixing it.
    """
    import auth

    calls = {}

    def fake_post(url, data, timeout=15.0):
        calls.update(data)
        raise auth.OAuthError("missing client secret")

    real = auth._post
    auth._post = fake_post
    try:
        got = auth.refresh_if_possible({"client_id": "cid"})
    finally:
        auth._post = real
    assert got is None, got
    assert "client_secret" not in calls, calls
    print("[PASS] a refresh without client_secret is reported as the cause, "
          "not as a generic failure")


def test_the_two_silent_renewal_failures_now_say_why():
    """The reason a 401 can look random even with client_secret set.

    refresh_if_possible returned None with no message at all when tokens.json
    was missing, or when it had been saved for a different client_id. Either
    makes renewal fail permanently while the bot keeps running, so it works
    for four hours and then every Helix call 401s - with nothing in the log
    to explain it.
    """
    orig = (auth.load_tokens, auth.TOKENS_PATH)
    try:
        # --- missing tokens.json -------------------------------------
        auth.load_tokens = lambda: None
        auth._WARNED.clear()
        assert auth.refresh_if_possible({"client_id": "c",
                                         "client_secret": "s"}) is None
        assert "no-tokens" in auth._WARNED, auth._WARNED

        # --- tokens saved for a different app ------------------------
        auth.load_tokens = lambda: {"access_token": "t", "refresh_token": "r",
                                    "expires_at": 0.0,
                                    "client_id": "OTHER-APP"}
        auth._WARNED.clear()
        assert auth.refresh_if_possible({"client_id": "MINE",
                                         "client_secret": "s"}) is None
        assert "client-id-mismatch" in auth._WARNED, auth._WARNED

        # --- and describe_login states it as a fact ------------------
        lines = "\n".join(auth.describe_login({"client_id": "MINE",
                                                "client_secret": "s"}))
        assert "MISMATCH" in lines, lines
        assert "OTHER-APP" in lines, lines
        # the secret is never printed, only whether one exists
        assert "somesecret" not in lines and "s" != lines
        assert "client_secret  : present" in lines, lines
    finally:
        auth.load_tokens, auth.TOKENS_PATH = orig
        auth._WARNED.clear()
    print("[PASS] a missing tokens.json and a client_id mismatch both name "
          "themselves")


def test_a_warning_is_printed_once_not_every_wake_up():
    """refresh_if_possible runs every 30 minutes for as long as the bot is up,
    so an ungated print would scroll the same line past all night."""
    import io as _io
    import contextlib
    orig = auth.load_tokens
    try:
        auth.load_tokens = lambda: None
        auth._WARNED.clear()
        buf = _io.StringIO()
        with contextlib.redirect_stderr(buf):
            for _ in range(5):
                auth.refresh_if_possible({"client_id": "c",
                                          "client_secret": "s"})
        assert buf.getvalue().count("no saved login") == 1, buf.getvalue()
    finally:
        auth.load_tokens = orig
        auth._WARNED.clear()
    print("[PASS] the diagnostic prints once, not on every 30-minute wake-up")


def test_the_refresh_margin_outlives_the_keeper_interval():
    """The bug behind the intermittent 401s.

    The token keeper wakes every 30 minutes (bot._token_keeper), but the
    refresh only fired within 120s of expiry. So the keeper kept finding a
    token that was still good for another half hour, reusing it, and never
    refreshing at all - the token was renewed only after it had already died,
    and Helix answered 401 for up to 30 minutes every cycle.
    """
    assert auth.REFRESH_MARGIN > 1800, auth.REFRESH_MARGIN

    calls = {"validate": 0, "refresh": 0}
    state = {"access": "old", "expires_at": time.time() + 1800}
    orig = (auth.load_tokens, auth.validate_token, auth.refresh_access_token,
            auth._store_tokens)
    try:
        auth.load_tokens = lambda: {
            "access_token": state["access"], "refresh_token": "rt",
            "expires_at": state["expires_at"], "client_id": "cid"}

        def validate(token):
            calls["validate"] += 1
        auth.validate_token = validate

        def refresh(cid, rt, secret):
            calls["refresh"] += 1
            return {"access_token": "new", "refresh_token": "rt",
                    "expires_in": 14400}
        auth.refresh_access_token = refresh

        def store(tok, cid):
            state["access"] = tok["access_token"]
            state["expires_at"] = time.time() + 14400
            return "oauth:" + tok["access_token"]
        auth._store_tokens = store

        # 30 minutes left: it would already be expired by the next wake-up,
        # so it must be renewed now rather than reused.
        auth.refresh_if_possible({"client_id": "cid", "client_secret": "s"})
        assert calls["refresh"] == 1, calls
        assert calls["validate"] == 0, "must not have reused the old token"

        # Plenty of life left: still reused, not churned on every wake-up.
        calls["refresh"] = calls["validate"] = 0
        state["expires_at"] = time.time() + 7200
        auth.refresh_if_possible({"client_id": "cid", "client_secret": "s"})
        assert calls["refresh"] == 0, calls
        assert calls["validate"] == 1, calls
    finally:
        (auth.load_tokens, auth.validate_token, auth.refresh_access_token,
         auth._store_tokens) = orig
    print(f"[PASS] refresh margin {auth.REFRESH_MARGIN:.0f}s exceeds the "
          f"1800s keeper interval, so renewal happens before expiry")


def main():
    test_scopes_all_exist()
    test_confidential_refresh_needs_secret()
    test_the_refresh_margin_outlives_the_keeper_interval()
    test_the_two_silent_renewal_failures_now_say_why()
    test_a_warning_is_printed_once_not_every_wake_up()
    tmp = tempfile.mkdtemp()
    tokens_path = os.path.join(tmp, "tokens.json")

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    base = f"http://127.0.0.1:{port}"
    auth.DEVICE_ENDPOINT = base + "/oauth2/device"
    auth.TOKEN_ENDPOINT = base + "/oauth2/token"
    auth.VALIDATE_ENDPOINT = base + "/oauth2/validate"
    auth.TOKENS_PATH = tokens_path
    auth.webbrowser.open = lambda *a, **k: None  # don't pop a browser in tests

    cfg = {"client_id": "testclient"}
    results = []

    # 1. Fresh login via device flow
    token = auth.resolve_token(cfg)
    results.append(("device login", token == "oauth:fake_access", token))
    saved = auth.load_tokens()
    results.append(("tokens.json written", bool(saved and saved.get("login") == "testbot"), saved))

    # 2. Reuse — should validate and NOT poll again
    polls_before = STATE["polls"]
    token = auth.resolve_token(cfg)
    results.append(("reuse without re-login", token == "oauth:fake_access" and STATE["polls"] == polls_before, token))

    # 3. Auto-refresh when the token is expired
    auth.save_tokens({
        "access_token": "expired_access",
        "refresh_token": "fake_refresh",
        "expires_at": time.time() - 100,
        "client_id": "testclient",
    })
    token = auth.resolve_token(cfg)
    results.append(("auto-refresh", token == "oauth:refreshed_access" and STATE["refresh_calls"] == 1, token))

    # 4. --login forces a fresh device flow even with saved tokens
    polls_before = STATE["polls"]
    token = auth.resolve_token(cfg, force_login=True)
    results.append(("force re-login", token == "oauth:fake_access" and STATE["polls"] > polls_before, token))

    # 5. No saved tokens AND no client_id -> helpful error
    os.remove(tokens_path)  # clear the saved login
    try:
        auth.resolve_token({"oauth_token": ""})
        results.append(("missing client_id error", False, "no error raised"))
    except auth.OAuthError as exc:
        results.append(("missing client_id error", "dev.twitch.tv/console" in str(exc), str(exc)[:80]))

    # 6. refresh_if_possible is non-interactive: never opens the device flow,
    #    returns None when nothing is saved, reuses a valid token otherwise.
    if os.path.exists(tokens_path):
        os.remove(tokens_path)
    t = auth.refresh_if_possible(cfg)
    results.append(("refresh_if_possible no tokens -> None", t is None, t))
    auth.save_tokens({
        "access_token": "fake_access",
        "refresh_token": "fake_refresh",
        "expires_at": time.time() + 99999,
        "client_id": "testclient",
    })
    polls_before = STATE["polls"]
    t = auth.refresh_if_possible(cfg)
    results.append(("refresh_if_possible reuses valid token",
                    t == "oauth:fake_access" and STATE["polls"] == polls_before, t))

    server.shutdown()
    print("\n==== auth flow test results ====")
    ok = True
    for name, passed, detail in results:
        mark = "PASS" if passed else "FAIL"
        ok = ok and passed
        print(f"  [{mark}] {name}: {detail}")
    print("ALL PASSED ✔" if ok else "SOME FAILED ✘")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
