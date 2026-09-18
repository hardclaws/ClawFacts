"""Offline tests for moderation.py and the bot's !ban path - no network.

Run:  python3 mock_moderation_test.py
"""

import io
import json
import sys
import threading
import time
import urllib.error

import access
import auth
import bot as bot_mod
import llm
import moderation

CALLS = []


class _Resp:
    def __init__(self, raw=b"{}", status=200):
        self._raw, self.status, self.headers = raw, status, {}

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _helix(client_id="cid", token="tok", broadcaster_id="111"):
    h = access.Helix(client_id, token, broadcaster_id)
    h._ids = {"spammer": "999", "leadmod": "555", "docbot": "777",
              "doc": "111"}
    return h


def _router(fail=None, users=True, body=None):
    """Record every Helix call; answer or fail as told."""
    def _fake(req, timeout=8):
        CALLS.append({"method": req.get_method(), "url": req.full_url,
                      "body": (req.data or b"").decode("utf-8"),
                      "auth": req.headers.get("Authorization"),
                      "client": req.headers.get("Client-id")})
        for code, match in (fail or {}).items():
            if match in req.full_url:
                raise urllib.error.HTTPError(
                    req.full_url, code, "nope", {},
                    io.BytesIO(json.dumps(body or {
                        "error": "Bad Request",
                        "message": "the user is already banned"}
                    ).encode("utf-8")))
        if "/helix/users" in req.full_url:
            login = req.full_url.split("login=")[-1]
            data = [{"id": "999", "login": login}] if users else []
            return _Resp(json.dumps({"data": data}).encode("utf-8"))
        if "moderation/bans" in req.full_url:
            return _Resp(json.dumps({"data": [{
                "broadcaster_id": "111", "moderator_id": "777",
                "user_id": "999", "created_at": "2026-09-18T00:00:00Z",
                "end_time": None}]}).encode("utf-8"))
        return _Resp(b"{}")
    return _fake


def _join_threads():
    for t in threading.enumerate():
        if t.name == "mod-action" and t is not threading.current_thread():
            t.join(5)


def _bot(**over):
    cfg = dict(bot_mod.DEFAULTS, nick="Docbot", channel="#doc",
               mod_logins=["Leadmod", "doc"],
               memory_db_path=":memory:")
    cfg.update(over)
    b = bot_mod.TwitchBot(cfg)
    b._distill = lambda *a, **k: None
    return b


def main():
    ok = True
    orig = moderation.urllib.request.urlopen

    # ---- the request Twitch actually gets -----------------------------
    mod = moderation.Moderator(_helix(), nick="docbot")
    mod.moderator_id = "777"
    logs = []
    mod.log = logs.append
    moderation.urllib.request.urlopen = _router()
    try:
        res = mod.ban("Spammer", "spam in chat")
        assert res.ok, res
        assert res.action == "ban" and res.target == "spammer", res
        call = CALLS[-1]
        assert call["method"] == "POST", call
        assert call["url"] == ("https://api.twitch.tv/helix/moderation/bans"
                               "?broadcaster_id=111&moderator_id=777"), call
        body = json.loads(call["body"])
        assert body == {"data": {"user_id": "999",
                                 "reason": "spam in chat"}}, body
        assert call["auth"] == "Bearer tok", call
        # The IRC form of the same token must not reach the header.
        mod.helix.token = "oauth:tok"
        assert mod._token() == "tok", mod._token()
        assert call["client"] == "cid", call
        assert "banned" in res.note and "spam in chat" in res.note, res

        # A timeout carries duration; a ban must not.
        CALLS.clear()
        res = mod.ban("spammer", "", duration=600)
        body = json.loads(CALLS[-1]["body"])
        assert body == {"data": {"user_id": "999", "duration": 600}}, body
        assert res.action == "timeout" and "10 min" in res.note, res

        # Unban is a DELETE naming the user.
        CALLS.clear()
        res = mod.unban("spammer")
        assert res.ok and CALLS[-1]["method"] == "DELETE", CALLS
        assert "user_id=999" in CALLS[-1]["url"], CALLS

        # A name Twitch does not know never reaches the ban endpoint.
        moderation.urllib.request.urlopen = _router(users=False)
        CALLS.clear()
        res = mod.ban("nosuchuser123")
        assert not res.ok and "no account named" in res.note, res
        assert not [c for c in CALLS if "moderation/bans" in c["url"]], CALLS
        moderation.urllib.request.urlopen = _router()

        # Not a login: refused before any call.
        CALLS.clear()
        assert not mod.ban("some body!").ok
        assert not mod.ban("").ok
        assert CALLS == [], CALLS

        # Already banned reads as words, not as a bare 400.
        moderation.urllib.request.urlopen = _router(fail={400: "moderation/bans"})
        res = mod.ban("spammer")
        assert not res.ok and "already banned" in res.note, res
        moderation.urllib.request.urlopen = _router()

        # A token without the scope says which one to go and get, and does
        # not burn a refresh on it: a missing scope is not a dead token.
        refreshed = []
        mod.helix.on_unauthorized = lambda: refreshed.append(1)
        moderation.urllib.request.urlopen = _router(
            fail={401: "moderation/bans"},
            body={"error": "Unauthorized",
                  "message": "Missing scope moderator:manage:banned_users"})
        res = mod.ban("spammer")
        assert not res.ok and "moderator:manage:banned_users" in res.note \
            and "--login" in res.note, res
        assert refreshed == [], refreshed
        mod.helix.on_unauthorized = None
        # Not a moderator: 403 names the fix.
        moderation.urllib.request.urlopen = _router(
            fail={403: "moderation/bans"})
        res = mod.ban("spammer")
        assert not res.ok and "not a moderator" in res.note and \
            "/mod docbot" in res.note, res
    finally:
        moderation.urllib.request.urlopen = orig
        CALLS.clear()
    print("[PASS] a ban is a Helix call: broadcaster + moderator id, reason, "
          "duration only for a timeout, and Twitch's errors in plain words")

    # ---- the bot's gate: mods and lead mods, in chat and privately -----
    moderation.urllib.request.urlopen = _router()
    try:
        b = _bot()
        b._moderator = moderation.Moderator(_helix(), nick="docbot")
        b._moderator.moderator_id = "777"
        b._log = lambda line: None
        b.said = []
        b._say = lambda text: b.said.append(text)

        # A lead moderator in the channel: allowed (Twitch replaces the
        # moderator badge with lead_moderator/1).
        assert b._mod_authorised("leadmod", "lead_moderator/1") is True
        assert b._mod_authorised("modguy", "moderator/1,subscriber/12") is True
        assert b._mod_authorised("doc", "broadcaster/1") is True
        assert b._mod_authorised("viewer", "subscriber/3") is False
        assert b._mod_authorised("viewer", "") is False
        # A whisper carries no channel badges, so the channel's own list
        # decides: mod_logins, the broadcaster, the bot.
        assert b._mod_authorised("leadmod", "", private=True) is True
        assert b._mod_authorised("doc", "", private=True) is True
        assert b._mod_authorised("stranger", "", private=True) is False
        assert b._mod_authorised("MODERATOR/1", "", private=True) is False

        # Nobody the bot will touch, even on a mod's word.
        assert "broadcaster" in b._mod_blocked("doc")
        assert "myself" in b._mod_blocked("docbot")
        assert "moderator" in b._mod_blocked("leadmod")
        assert b._mod_blocked("spammer") == ""
        assert "not a Twitch username" in b._mod_blocked("@some body")

        # A viewer typing !ban in chat gets nothing at all.
        CALLS.clear()
        b.said.clear()
        b._on_message("Viewer", "#doc", "!ban spammer", "viewer",
                      "subscriber/3")
        _join_threads()
        assert b.said == [] and CALLS == [], (b.said, CALLS)

        # A lead mod's !ban in chat reaches Twitch and is answered in chat.
        CALLS.clear()
        b.said.clear()
        b._on_message("Leadmod", "#doc", "!ban Spammer trolling", "leadmod",
                      "lead_moderator/1")
        _join_threads()
        assert [c["url"] for c in CALLS if "moderation/bans" in c["url"]] == [
            "https://api.twitch.tv/helix/moderation/bans"
            "?broadcaster_id=111&moderator_id=777"], CALLS
        body = json.loads([c for c in CALLS
                           if "moderation/bans" in c["url"]][0]["body"])
        assert body["data"]["reason"] == "trolling", body
        assert any("banned" in line for line in b.said), b.said

        # !timeout takes a length; junk is refused without a call.
        CALLS.clear()
        b.said.clear()
        b._on_message("Leadmod", "#doc", "!timeout spammer 10m spam",
                      "leadmod", "lead_moderator/1")
        _join_threads()
        body = json.loads([c for c in CALLS
                           if "moderation/bans" in c["url"]][0]["body"])
        assert body["data"]["duration"] == 600, body
        assert body["data"]["reason"] == "spam", body
        CALLS.clear()
        b.said.clear()
        b._on_message("Leadmod", "#doc", "!timeout spammer banana", "leadmod",
                      "lead_moderator/1")
        _join_threads()
        assert CALLS == [] and any("not a length" in l for l in b.said), \
            (CALLS, b.said)

        # !unban is a DELETE.
        CALLS.clear()
        b._on_message("doc", "#doc", "!unban spammer", "doc", "broadcaster/1")
        _join_threads()
        assert [c["method"] for c in CALLS
                if "moderation/bans" in c["url"]] == ["DELETE"], CALLS

        # A private message: answered privately, never in the room, and it
        # must not reach the chat AI's ear as room context.
        whispers = []
        b._moderator.whisper = lambda login, text: (
            whispers.append((login, text)), True)[1]
        CALLS.clear()
        b.said.clear()
        with b._chat_lock:
            b._chat_buf.clear()
        b._on_message("Leadmod", "docbot", "!ban spammer ad spam", "leadmod",
                      "")
        _join_threads()
        assert b.said == [], b.said
        assert whispers and whispers[0][0] == "leadmod" \
            and "banned" in whispers[0][1], whispers
        assert [c["url"] for c in CALLS if "moderation/bans" in c["url"]], CALLS
        with b._chat_lock:
            assert not b._chat_buf, b._chat_buf

        # A stranger's private !ban is refused privately and calls nothing.
        whispers.clear()
        CALLS.clear()
        b._pm_last.clear()
        b._on_message("Stranger", "docbot", "!ban docbot", "stranger", "")
        _join_threads()
        assert CALLS == [], CALLS
        assert whispers and "only moderators" in whispers[0][1], whispers

        # A whisper line from IRC itself takes the same path.
        whispers.clear()
        CALLS.clear()
        b._pm_last.clear()
        b._handle("@badges=;display-name=Leadmod;user-id=555 "
                  ":leadmod!leadmod@leadmod.tmi.twitch.tv WHISPER docbot "
                  ":!unban spammer")
        _join_threads()
        assert [c["method"] for c in CALLS
                if "moderation/bans" in c["url"]] == ["DELETE"], CALLS
        assert whispers and "unbanned" in whispers[0][1], whispers

        # Switched off, nobody moderates - not even the broadcaster.
        b.cfg["mod_commands_enabled"] = False
        CALLS.clear()
        whispers.clear()
        b.said.clear()
        b._pm_last.clear()
        b._on_message("doc", "#doc", "!ban spammer", "doc", "broadcaster/1")
        _join_threads()
        assert CALLS == [] and any("switched off" in l for l in b.said), \
            (CALLS, b.said)
        b._on_message("doc", "docbot", "!ban spammer", "doc", "broadcaster/1")
        _join_threads()
        assert CALLS == [], CALLS
    finally:
        moderation.urllib.request.urlopen = orig
        CALLS.clear()
    print("[PASS] only this channel's mods and lead mods can ask - in chat by "
          "badge, privately by mod_logins - and a private ask stays private")

    # ---- the scopes the login has to carry ----------------------------
    for scope in ("moderator:manage:banned_users", "user:manage:whispers"):
        assert scope in auth.SCOPES, auth.SCOPES
    assert moderation.parse_duration("10m") == 600
    assert moderation.parse_duration("90s") == 90
    assert moderation.parse_duration("2h") == 7200
    assert moderation.parse_duration("") == moderation.DEFAULT_TIMEOUT
    assert moderation.parse_duration("banana") is None
    assert moderation.human_duration(600) == "10 min"
    print("[PASS] the ban scopes are in the login, and lengths parse")

    print("ALL PASSED \u2714" if ok else "SOME FAILED \u2718")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.argv = sys.argv[:1]
    raise SystemExit(main())
