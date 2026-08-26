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


def main():
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
