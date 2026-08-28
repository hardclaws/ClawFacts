"""Test access.py against a fake Helix API (no real Twitch needed).

Run:  python3 mock_access_test.py
Covers: badge tiers, per-user cooldowns, the follower gate, caching, and what
happens when the follow check cannot run.
"""

import http.server
import json
import threading
import time

import access

STATE = {"calls": [], "unauthorised": False, "not_a_mod": False,
         "no_moderator_scope": False, "reject_token": ""}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        STATE["calls"].append(self.path)
        # Reject by token, not by path: a dead token 401s on everything.
        if STATE["reject_token"] and self.headers.get(
                "Authorization", "").endswith(STATE["reject_token"]):
            self._send(401, {"error": "Unauthorized", "status": 401})
            return
        if STATE["unauthorised"] and "/helix/channels/followers" in self.path:
            # Exactly what Twitch sends when the token is not the broadcaster
            # or a moderator: 200 OK, the real total, and no rows.
            self._send(200, {"total": 1234, "data": []})
            return
        if "/helix/users" in self.path:
            login = self.path.split("login=")[-1].split("&")[0]
            body = {"data": [{"id": "999" + login, "login": login}]}
        elif "/helix/channels/followers" in self.path:
            if "user_id=" not in self.path:
                # The self-test call: no user_id, just "may I read this list?"
                stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 999))
                body = {"total": 1234, "data": [{"followed_at": stamp}]}
            elif "user_id=999stranger" in self.path:
                body = {"total": 0, "data": []}
            elif "user_id=999fresh" in self.path:
                stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
                body = {"total": 1, "data": [{"followed_at": stamp}]}
            elif "user_id=999oldtimer" in self.path:
                stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 86400 * 40))
                body = {"total": 1, "data": [{"followed_at": stamp}]}
            else:
                body = {"error": "Unauthorized", "status": 401}
                self._send(401, body)
                return
        elif "/helix/moderation/moderators" in self.path:
            if STATE["not_a_mod"] or STATE["no_moderator_scope"]:
                # Twitch sends the same 401 for a missing scope and for a
                # requester who is neither broadcaster nor moderator.
                self._send(401, {"error": "Unauthorized", "status": 401})
                return
            if "user_id=999me" in self.path \
                    or "user_id=999truckingwithdocbot" in self.path:
                body = {"total": 1,
                        "data": [{"user_id": "999me", "user_login": "me"}]}
            else:
                body = {"total": 0, "data": []}
        elif "/oauth2/validate" in self.path:
            body = {"client_id": "cid", "login": "truckingwithdocbot",
                    "scope": ["chat:read", "chat:edit",
                              "moderation:read:moderators"],
                    "user_id": "999me", "expires_in": 1234}
        else:
            body = {"data": []}
        self._send(200, body)

    def _send(self, code, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def make_helix(**kw):
    return access.Helix("client", "token", "12345", **kw)


def test_user_id_sends_a_clean_login(port):
    """'#truckingwithdoc' must reach Helix as 'truckingwithdoc'.

    Sending the '#' gets HTTP 400, which reads as "that channel does not
    exist" and silently disables every follow check in the bot. user_id used
    to depend on its caller stripping the '#'; when that was removed the
    channel name went through verbatim. Assert what actually left the process,
    not what the helper returns - that is the only way this can be caught.
    """
    h = make_helix()
    for raw in ("#truckingwithdoc", "@truckingwithdoc", "truckingwithdoc"):
        STATE["calls"].clear()
        h._ids.clear()
        uid = h.user_id(raw)
        sent = [p for p in STATE["calls"] if "/helix/users" in p]
        assert sent, f"no Helix call for {raw!r}: {STATE['calls']}"
        assert "login=truckingwithdoc" in sent[0], (raw, sent[0])
        # Neither '#' nor '@' may survive in any encoding.
        assert "%23" not in sent[0] and "%40" not in sent[0], sent[0]
        assert "#" not in sent[0].split("login=")[-1], sent[0]
        assert uid, (raw, uid)
    print("[PASS] user_id sends a clean login for #name, @name and a bare name")


def test_badge_tiers(port):
    assert access.tier_from_badges("broadcaster/1") == "broadcaster"
    assert access.tier_from_badges("moderator/1") == "moderator"
    assert access.tier_from_badges("vip/1") == "vip"
    assert access.tier_from_badges("subscriber/12") == "subscriber"
    # Privilege order, not badge order: a mod who also subs is a mod.
    assert access.tier_from_badges("subscriber/24,moderator/1") == "moderator"
    assert access.tier_from_badges("vip/1,subscriber/06") == "vip"
    # Founder and sub-gifter style badges are subscriber badges.
    assert access.tier_from_badges("founder/0") == "subscriber"
    # Twitch staff moderate any channel.
    assert access.tier_from_badges("staff/1") == "moderator"
    assert access.tier_from_badges("") is None
    assert access.tier_from_badges("premium/1,turbo/1") is None
    print("[PASS] badge tiers resolve, privilege order respected")


def test_tier_cooldowns(port):
    """The exact schedule asked for: mods 30s, VIP/sub 60s, followers 5m."""
    cfg = {"min_follow_age_seconds": 86400}
    ac = access.AccessControl(cfg, make_helix())
    t = 1_000_000.0

    def use(login, badges, at):
        d = ac.check(login, badges, now=at)
        if d.allowed:
            ac.commit(login, now=at)
        return d

    cases = [("hardclaws", "broadcaster/1", 30.0),
             ("mod_mike", "moderator/1,subscriber/12", 30.0),
             ("vip_vic", "vip/1", 60.0),
             ("sub_sam", "subscriber/06", 60.0)]
    for login, badges, seconds in cases:
        assert use(login, badges, t).allowed, login
        # Still blocked just before the window closes...
        blocked = use(login, badges, t + seconds - 1)
        assert not blocked.allowed, (login, blocked)
        assert blocked.wait > 0
        # ...and through exactly at the boundary.
        assert use(login, badges, t + seconds).allowed, login
        print(f"[PASS] {login:12s} {badges:28s} -> {seconds:g}s")

    # Cooldowns are per user: a sub waiting does not block the next sub.
    assert use("sub_two", "subscriber/01", t + 1).allowed
    print("[PASS] cooldowns are per user, not per channel")


def test_follower_gate(port):
    h = make_helix()
    # The bot always probes before it gates anyone (_resolve_broadcaster ->
    # self_test), and the gate must be read against that result: an empty
    # follower list only means "not following" once the token is confirmed
    # allowed to see the list at all.
    assert h.self_test() is True
    ac = access.AccessControl({"min_follow_age_seconds": 86400}, h)
    t = 2_000_000.0

    stranger = ac.check("stranger", "", now=t)
    assert not stranger.allowed and stranger.tier == "non-follower", stranger
    print("[PASS] non-follower blocked entirely")

    fresh = ac.check("fresh", "", now=t)
    assert not fresh.allowed and fresh.tier == "new-follower", fresh
    print("[PASS] 1-hour-old follower blocked (needs 1 day)")

    old = ac.check("oldtimer", "", now=t)
    assert old.allowed and old.cooldown == 300.0, old
    ac.commit("oldtimer", now=t)
    soon = ac.check("oldtimer", "", now=t + 299)
    assert not soon.allowed and soon.wait > 0, soon
    assert ac.check("oldtimer", "", now=t + 300).allowed
    print("[PASS] 40-day follower allowed, 5-minute cooldown enforced")


def test_badged_users_skip_the_api(port):
    STATE["calls"].clear()
    ac = access.AccessControl({}, make_helix())
    assert ac.check("mod_mike", "moderator/1", now=1.0).allowed
    assert ac.check("vip_vic", "vip/1", now=1.0).allowed
    assert ac.check("sub_sam", "subscriber/06", now=1.0).allowed
    assert STATE["calls"] == [], STATE["calls"]
    print("[PASS] mods/VIPs/subs cost zero API calls")


def test_follower_cache(port):
    STATE["calls"].clear()
    ac = access.AccessControl({}, make_helix())
    ac.check("oldtimer", "", now=1.0)
    n = len(STATE["calls"])
    ac.check("oldtimer", "", now=2.0)
    ac.check("oldtimer", "", now=3.0)
    assert len(STATE["calls"]) == n, STATE["calls"]
    print(f"[PASS] repeat checks are cached ({n} call(s) total)")


def test_check_failure_is_configurable(port):
    # No broadcaster id -> the Helix client cannot run at all.
    h = access.Helix("client", "token", "")
    deny = access.AccessControl({"follower_check_failure": "deny"}, h)
    d = deny.check("oldtimer", "", now=1.0)
    assert not d.allowed and d.tier == "unknown", d
    allow = access.AccessControl({"follower_check_failure": "allow"}, h)
    assert allow.check("oldtimer", "", now=1.0).allowed
    # Badged users are unaffected either way.
    assert deny.check("mod_mike", "moderator/1", now=1.0).allowed
    print("[PASS] unverifiable follow status: deny by default, allow opt-in")


def test_disabled(port):
    ac = access.AccessControl({"access_control": False}, make_helix())
    assert ac.check("stranger", "", now=1.0).allowed
    print("[PASS] access_control=false lets everyone through")


def test_unauthorised_token_does_not_accuse_followers(port):
    """Twitch returns 200 OK with the total count and an EMPTY data array when
    the token is not the broadcaster or a moderator. Reading that as "does not
    follow" turned every real follower away with "only followers can use that"
    - which is what happened on the live channel. The honest answer is "could
    not verify", and it points at the actual fix."""
    STATE["unauthorised"] = True
    try:
        h = make_helix()
        assert h.self_test() is False
        assert h.authorised is False
        ac = access.AccessControl({"follower_check_failure": "deny"}, h)
        d = ac.check("oldtimer", "", now=1.0)
        assert not d.allowed, d
        assert d.tier == "unknown", d
        assert "verify" in d.reason, d
        assert "follower" not in d.reason.replace("follow status", ""), d
        # ...and mods/VIPs/subs are still unaffected.
        assert ac.check("mod_mike", "moderator/1", now=1.0).allowed
    finally:
        STATE["unauthorised"] = False
    print("[PASS] a token without permission reports 'unknown', not 'not following'")


def test_authorised_empty_data_means_not_following(port):
    """With a working token an empty data array really does mean the user is
    not a follower, and they must be turned away."""
    h = make_helix()
    assert h.self_test() is True
    assert h.authorised is True
    ac = access.AccessControl({}, h)
    d = ac.check("stranger", "", now=1.0)
    assert not d.allowed and d.tier == "non-follower", d
    assert ac.check("oldtimer", "", now=1.0).allowed
    print("[PASS] with permission, an empty list means genuinely not following")


def test_unprobed_token_is_unknown_not_not_following(port):
    """A probe that never completed must not be read as permission. An empty
    follower list is only evidence of a non-follow once self_test() succeeded;
    before that it is Twitch saying 'you may not see this'."""
    h = make_helix()
    assert h.authorised is None, "a fresh client has not probed yet"
    STATE["unauthorised"] = True
    try:
        assert h.followed_at("999oldtimer") is None
        ac = access.AccessControl({}, h)
        d = ac.check("oldtimer", "", now=1.0)
        assert not d.allowed and d.tier == "unknown", d
    finally:
        STATE["unauthorised"] = False
    print("[PASS] an unprobed token reports 'unknown', never 'not following'")


def test_set_token_recovers_after_a_refresh(port):
    """Refreshing an access token invalidates the previous one. The Helix
    client was handed its copy once at construction, so after a refresh it kept
    sending the dead token and every follow check 401'd with no recovery."""
    h = make_helix()
    assert h.self_test() is True and h.authorised is True
    h.followed_at("999oldtimer")          # warm the cache
    assert h._follows, "expected a cached follow"

    h.set_token("oauth:NEWTOKEN")
    assert h.token == "NEWTOKEN", h.token
    assert h.authorised is None, "a new token must be re-probed"
    assert not h._follows, "cached follows belong to the old token"

    h.set_token("NEWTOKEN")               # no-op, must not disturb the probe
    assert h.authorised is None
    assert h.self_test() is True
    ac = access.AccessControl({}, h)
    assert ac.check("oldtimer", "", now=1.0).allowed
    print("[PASS] a refreshed token reaches the Helix client and is re-probed")


def test_moderator_check(port):
    """The other half of the 401: is the bot actually a moderator? A 401 only
    settles that once the caller knows the scope is present."""
    h = make_helix()
    bid = h.user_id("hardclaws")
    me = h.user_id("me")

    STATE["no_moderator_scope"] = True
    assert h.moderator_of(bid, me, has_scope=False) is None, \
        "a 401 without the scope proves nothing"
    STATE["no_moderator_scope"] = False

    STATE["not_a_mod"] = True
    assert h.moderator_of(bid, me, has_scope=True) is False, \
        "a 401 with the scope present means not a moderator"
    STATE["not_a_mod"] = False

    assert h.moderator_of(bid, me) is True
    assert h.moderator_of(bid, "999someone_else") is False
    assert h.moderator_of("", me) is None
    print("[PASS] moderator status: yes, provably no, and honestly unknown")


def test_describe_token(port):
    """The startup diagnosis reads the token back from Twitch so the log names
    the cause instead of guessing."""
    import auth
    auth.VALIDATE_ENDPOINT = f"http://127.0.0.1:{port}/oauth2/validate"
    info = make_helix().describe_token()
    assert info["login"] == "truckingwithdocbot", info
    assert "moderator:read:followers" not in info["scope"], \
        "the fixture stands in for a token logged in before the scope existed"
    print("[PASS] describe_token reports the account and its actual scopes")


def test_startup_diagnosis(port):
    """The whole point: four causes share one chat message, so the log must
    name the cause. This drives bot._diagnose_access against the fake Helix
    with a token that is missing the scope and a bot that is not a moderator."""
    import bot as bot_mod
    import auth

    auth.VALIDATE_ENDPOINT = f"http://127.0.0.1:{port}/oauth2/validate"
    cfg = dict(bot_mod.DEFAULTS)
    cfg.update({"nick": "truckingwithdocbot", "channel": "#hardclaws",
                "client_id": "cid", "oauth_token": "oauth:T"})
    b = bot_mod.TwitchBot(cfg)
    b._access.helix = make_helix()
    logged = []
    b._log = logged.append
    STATE["not_a_mod"] = True
    try:
        b._diagnose_access()
    finally:
        STATE["not_a_mod"] = False
    text = "\n".join(logged)
    assert "PROBLEM" in text and "moderator:read:followers" in text, text
    assert "could not verify your follow status" in text, text
    # The startup probe must NOT claim to know moderator status. Get
    # Moderators only answers for the broadcaster's own token, so for a bot
    # account it 401s every time and proves nothing either way. Guessing here
    # is what put an invented scope name in auth.SCOPES and locked the bot out
    # of login entirely; the answer comes from USERSTATE instead.
    assert "is not a moderator" not in text, text
    assert "USERSTATE" in text, text
    # And the USERSTATE path does answer it.
    b._bot_is_mod = None
    b._note_own_state({"mod": "0", "badges": ""})
    assert b._bot_is_mod is False, b._bot_is_mod
    state_text = "\n".join(logged)
    assert "is NOT a moderator" in state_text, state_text
    assert "/mod truckingwithdocbot" in state_text, state_text
    print("---- what the bot now prints at startup when it is misconfigured ----")
    for line in logged:
        print("   ", line)
    print("[PASS] the startup diagnosis names the follow-scope cause, and "
          "moderator status comes from chat")


def test_startup_diagnosis_clean(port):
    """And the opposite case must say so plainly rather than staying silent."""
    import bot as bot_mod
    import auth

    auth.VALIDATE_ENDPOINT = f"http://127.0.0.1:{port}/oauth2/validate"
    auth.SCOPES = "chat:read chat:edit moderator:read:followers"
    cfg = dict(bot_mod.DEFAULTS)
    cfg.update({"nick": "truckingwithdocbot", "channel": "#hardclaws",
                "client_id": "cid", "oauth_token": "oauth:T"})
    b = bot_mod.TwitchBot(cfg)
    h = make_helix()
    b._access.helix = h
    logged = []
    b._log = logged.append
    h.describe_token = lambda: {"login": "truckingwithdocbot",
                                "scope": ["chat:read", "chat:edit",
                                          "moderator:read:followers",
                                          "moderation:read:moderators"]}
    b._diagnose_access()
    text = "\n".join(logged)
    assert "PROBLEM" not in text, text
    assert "follow checks are working" in text, text
    print("[PASS] a correctly configured bot says so")


def test_channel_profile(port):
    """What !whois shows for a streamer. Both calls are scope-free, and the
    follower *count* comes back even for a channel this token cannot
    moderate - only the per-user rows need moderator:read:followers."""
    h = make_helix()
    profile = h.channel_profile("hardclaws")
    assert profile is not None, "no profile for a known login"
    assert profile["id"] == "999hardclaws", profile
    assert profile["display_name"] == "hardclaws", profile
    assert profile["followers"] == 1234, profile
    assert h.channel_profile("") is None
    assert h.channel_profile("#hardclaws") is not None, "a leading # must work"
    # Without the pieces it needs the answer is None, never a blank profile.
    assert access.Helix("", "token", "1").channel_profile("hardclaws") is None
    assert access.Helix("cid", "", "1").channel_profile("hardclaws") is None
    print("[PASS] channel_profile: id, display name and follower count")


def test_401_refreshes_the_token_and_retries(port):
    """The defect: a 401 was terminal.

    user_id() caught it, logged it, returned None, and nothing asked for a new
    token - so every follow check stayed dead until the process restarted.
    Twitch's own guidance is to react to the 401, so the client now asks for a
    fresh token and retries once.
    """
    STATE["reject_token"] = "stale"
    try:
        h = access.Helix("client", "stale", "12345")
        refreshed = []

        def on_unauthorized():
            refreshed.append(1)
            h.set_token("fresh")

        h.on_unauthorized = on_unauthorized
        uid = h.user_id("truckingwithdoc")
        assert uid == "999truckingwithdoc", uid
        assert len(refreshed) == 1, refreshed
        assert h.unauthorized == 1, h.unauthorized
        print(f"[PASS] a 401 refreshed the token and the retry succeeded "
              f"({h.unauthorized} x 401)")
    finally:
        STATE["reject_token"] = ""


def test_a_400_is_not_treated_as_an_expired_token(port):
    """Only a 401 means the token died. Refreshing on a 400 would mask the
    real bug class - a malformed request, like the '#' that reached Helix."""
    STATE["reject_token"] = ""
    h = access.Helix("client", "token", "12345")
    calls = []
    h.on_unauthorized = lambda: calls.append(1)
    # A login Twitch will not accept: the fake server 400s on an empty login,
    # so send one that cannot resolve instead and check the path is taken.
    import urllib.error
    orig = access._get

    def boom(url, params, token, client_id, timeout=5.0):
        raise urllib.error.HTTPError(url, 400, "Bad Request", {}, None)
    access._get = boom
    try:
        assert h.user_id("truckingwithdoc") is None
        assert calls == [], "a 400 must not trigger a refresh"
        assert h.unauthorized == 0, h.unauthorized
    finally:
        access._get = orig
    print("[PASS] a 400 propagates without refreshing or counting as a 401")


def test_without_a_hook_the_behaviour_is_unchanged(port):
    """The retry is opt-in. With no callback a 401 must behave exactly as it
    did before - return None, never raise into the caller."""
    STATE["reject_token"] = "stale"
    try:
        h = access.Helix("client", "stale", "12345")
        assert h.on_unauthorized is None
        assert h.user_id("truckingwithdoc") is None
        assert h.unauthorized == 0, "no retry attempted, so no 401 counted"
    finally:
        STATE["reject_token"] = ""
    print("[PASS] with no refresh hook, a 401 returns None as it always did")


def test_the_denial_names_an_expired_token(port):
    """'Could not verify your follow status' is true but useless when the real
    cause is a dead token - it sends the viewer chasing their own follow."""
    import bot as bot_mod
    b = bot_mod.TwitchBot(dict(bot_mod.DEFAULTS, nick="bot", channel="#test",
                               prefix="!"))
    said = []
    b._say = said.append
    b._access.helix = access.Helix("client", "stale", "12345")
    b._access.helix.unauthorized = 3
    b._note_denial("smithkxx", "smithkxx",
                   "could not verify your follow status", 0.0)
    assert said and "expired" in said[0], said
    assert "moderator" in said[0], said
    # and the generic wording when it is NOT a token problem
    said.clear()
    b._last_denial_note.clear()
    b._access.helix.unauthorized = 0
    b._note_denial("smithkxx", "smithkxx",
                   "could not verify your follow status", 0.0)
    assert said and "expired" not in said[0], said
    print("[PASS] a dead token says so; anything else keeps the plain wording")


def main():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    access.HELIX = f"http://127.0.0.1:{port}"   # point the client at the fake

    print("==== access control tests ====")
    for fn in (test_badge_tiers, test_tier_cooldowns, test_follower_gate,
               test_badged_users_skip_the_api, test_follower_cache,
               test_check_failure_is_configurable,
               test_unauthorised_token_does_not_accuse_followers,
               test_authorised_empty_data_means_not_following,
               test_unprobed_token_is_unknown_not_not_following,
               test_set_token_recovers_after_a_refresh,
               test_moderator_check, test_describe_token,
               test_startup_diagnosis, test_startup_diagnosis_clean,
               test_channel_profile, test_disabled,
               test_user_id_sends_a_clean_login,
               test_401_refreshes_the_token_and_retries,
               test_a_400_is_not_treated_as_an_expired_token,
               test_without_a_hook_the_behaviour_is_unchanged,
               test_the_denial_names_an_expired_token):
        fn(port)
    server.shutdown()
    print("ALL PASSED ✔")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
