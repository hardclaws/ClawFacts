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

STATE = {"calls": [], "unauthorised": False}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        STATE["calls"].append(self.path)
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
    ac = access.AccessControl({"min_follow_age_seconds": 86400}, make_helix())
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
               test_disabled):
        fn(port)
    server.shutdown()
    print("ALL PASSED ✔")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
