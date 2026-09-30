"""Offline tests for the admin panel - a real HTTP server on a random
loopback port, a real bot object, no Twitch, no network beyond 127.0.0.1.

Run:  python3 mock_adminpanel_test.py
"""

import http.cookiejar
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import adminpanel as ap
import bot as bot_mod
import customcmds
import haul
import reminders


ADMIN_PW = "correct horse battery"
MOD_PW = "another long password"


# ---------------------------------------------------------------------------
# scaffolding
# ---------------------------------------------------------------------------

def _bot(tmp, **over):
    cfg = {**bot_mod.DEFAULTS,
           "nick": "TruckingWithDocBot", "channel": "#t",
           "chat_ai_enabled": True, "llm_api_key": "gsk_secretsecret1234",
           "beef_state_path": os.path.join(tmp, "bs.json"),
           "memory_db_path": os.path.join(tmp, "mem.db"),
           "persona_state_path": os.path.join(tmp, "p.json"),
           "subgoal_state_path": os.path.join(tmp, "sg.json"),
           "ongoing_state_path": os.path.join(tmp, "og.json"),
           **over}
    b = bot_mod.TwitchBot(cfg)
    b.said = []
    b._say = b.said.append
    b._log = lambda *a, **k: None
    b._access.helix = None
    b._distill = lambda *a, **k: None
    b.reminders = reminders.ReminderSet(os.path.join(tmp, "r.json"))
    b.cargo = haul.Cargo(os.path.join(tmp, "h.json"))
    b.custom_cmds = customcmds.CommandSet(
        path=os.path.join(tmp, "cc.json"), reserved=bot_mod.RESERVED_COMMANDS)
    return b


def _drain(b):
    while not b._jobs.empty():
        nick, login, badges, command, argument = b._jobs.get()
        if command == "say":
            b._say(argument)


class Panel:
    """A running panel plus a cookie-carrying HTTP client."""

    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="clawfacts-panel-")
        self.config_path = os.path.join(self.tmp, "config.json")
        with open(self.config_path, "w", encoding="utf-8") as fh:
            json.dump({"nick": "TruckingWithDocBot", "channel": "#t",
                       "llm_api_key": "gsk_secretsecret1234",
                       "_note": "a comment key, kept",
                       "chat_ai_notice_minutes": 20, "spice": "clean"}, fh)
        self.bot = _bot(self.tmp)
        self.users_path = os.path.join(self.tmp, "users.json")
        self.users = ap.Users(self.users_path)
        assert self.users.set("hardclaws", ADMIN_PW, "admin")[0]
        assert self.users.set("modguy", MOD_PW, "mod")[0]
        self.ring = ap.LogRing()
        self.ring.write_text("[12:00:00] hello from the log\n[llm] warm-up OK\n")
        self.restarts = []
        self.control = ap.BotControl(
            bot=self.bot, cfg=self.bot.cfg, config_path=self.config_path,
            ring=self.ring, restart=lambda: self.restarts.append(1))
        self.server = ap.start_panel(
            {"admin_panel_enabled": True, "admin_panel_bind": "127.0.0.1",
             "admin_panel_port": 0}, self.control, self.users)
        assert self.server is not None, "panel did not start"
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.jar = http.cookiejar.CookieJar()

        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None

        self._follow = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        self._stay = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect())
        self.csrf = ""

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    # -- http -----------------------------------------------------------
    def get(self, path):
        """(status, final_url, body) following redirects."""
        try:
            r = self._follow.open(self.base + path, timeout=5)
            return r.status, r.geturl(), r.read().decode("utf-8")
        except urllib.error.HTTPError as ex:
            return ex.code, ex.geturl(), ex.read().decode("utf-8")

    def post(self, path, **data):
        """(status, redirect-location-with-message-decoded, body)."""
        if "csrf" not in data and self.csrf and path != "/login":
            data["csrf"] = self.csrf
        body = urllib.parse.urlencode(data).encode()
        try:
            r = self._stay.open(urllib.request.Request(self.base + path, data=body),
                                timeout=5)
            return r.status, urllib.parse.unquote(r.headers.get("Location") or ""), ""
        except urllib.error.HTTPError as ex:
            return (ex.code, urllib.parse.unquote(ex.headers.get("Location") or ""),
                    ex.read().decode("utf-8"))

    def login(self, user, pw):
        st, loc, _ = self.post("/login", user=user, password=pw)
        if loc == "/":
            _, _, body = self.get("/")
            self.csrf = re.search(r'name="csrf" value="([^"]+)"', body).group(1)
        return st, loc

    def logout(self):
        self.post("/logout")
        self.jar.clear()
        self.csrf = ""

    def config_on_disk(self):
        with open(self.config_path, encoding="utf-8") as fh:
            return json.load(fh)


# ---------------------------------------------------------------------------
# users and passwords
# ---------------------------------------------------------------------------

def test_passwords_are_hashed_and_checked():
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "u.json")
    users = ap.Users(path)
    ok, why = users.set("Hardclaws", ADMIN_PW, "admin")
    assert ok, why
    raw = open(path, encoding="utf-8").read()
    assert ADMIN_PW not in raw, "the password must never be stored"
    assert "pbkdf2_sha256$" in raw
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    assert users.verify("hardclaws", ADMIN_PW) == "admin"        # case-folded
    assert users.verify("hardclaws", "wrong") is None
    assert users.verify("nobody", ADMIN_PW) is None
    assert not users.set("x", "short", "mod")[0]
    assert not users.set("bad name!", ADMIN_PW, "mod")[0]
    assert not users.set("y", ADMIN_PW, "root")[0]
    # reload from disk
    again = ap.Users(path)
    assert again.verify("hardclaws", ADMIN_PW) == "admin"
    assert again.role("hardclaws") == "admin"
    print("  passwords hashed, case-folded, validated  ok")


def test_bind_safety():
    good = ("127.0.0.1", "10.0.0.5", "192.168.1.9", "172.20.0.2",
            "100.101.102.103", "::1", "fd7a:115c:a1e0::1")
    bad = ("0.0.0.0", "::", "203.0.113.9", "8.8.8.8", "banana", "")
    for b in good:
        assert ap._bind_is_safe(b)[0], b
    for b in bad:
        assert not ap._bind_is_safe(b)[0], b
    tmp = tempfile.mkdtemp()
    control = ap.BotControl()
    # no users -> refuses to start
    assert ap.start_panel({"admin_panel_enabled": True}, control,
                          os.path.join(tmp, "none.json")) is None
    users = ap.Users(os.path.join(tmp, "u.json"))
    users.set("a", ADMIN_PW, "admin")
    # public bind without the opt-in -> refuses
    assert ap.start_panel({"admin_panel_enabled": True,
                           "admin_panel_bind": "0.0.0.0"}, control,
                          users.path) is None
    # off -> nothing
    assert ap.start_panel({"admin_panel_enabled": False}, control, users.path) is None
    print("  loopback/private/tailscale binds only; no users = no panel  ok")


# ---------------------------------------------------------------------------
# the running panel
# ---------------------------------------------------------------------------

def test_anonymous_sees_only_the_login_page(p):
    st, url, body = p.get("/")
    assert st == 200 and url.endswith("/login") and "Sign in" in body
    st, url, _ = p.get("/log/tail?since=0")
    assert url.endswith("/login")
    st, url, _ = p.get("/config")
    assert url.endswith("/login")
    st, _, body = p.get("/healthz")
    assert st == 200 and body.strip() == "ok"
    # a POST without a session is bounced, not executed
    st, loc, _ = p.post("/act/pause", on="1", csrf="x")
    assert loc.startswith("/login") and p.bot.paused is False
    print("  anonymous -> login page only  ok")


def test_login_lockout_and_logging(p):
    t0 = time.time()
    st, loc = p.login("hardclaws", "wrong")
    assert "wrong user name or password" in loc
    assert time.time() - t0 >= 0.9, "a miss must cost a second"
    st, loc = p.login("hardclaws", ADMIN_PW)
    assert loc == "/", loc
    cookie = next(c for c in p.jar if c.name == "panel_session")
    # cookiejar keeps the extra attributes case-insensitively in _rest
    rest = {k.lower(): v for k, v in cookie._rest.items()}
    assert "httponly" in rest, cookie._rest
    assert rest.get("samesite") == "Strict", cookie._rest
    p.logout()
    # five misses lock the account even for the right password
    for _ in range(ap.LOCKOUT_AFTER):
        p.post("/login", user="modguy", password="nope")
    st, loc, _ = p.post("/login", user="modguy", password=MOD_PW)
    assert "too many failed logins" in loc, loc
    PanelHandlerSessions = ap.PanelHandler.sessions
    PanelHandlerSessions.clear_failures("modguy")
    print("  wrong password costs 1s, 5 misses lock 15 min, cookie HttpOnly+Strict  ok")


def test_csrf_is_required(p):
    p.login("hardclaws", ADMIN_PW)
    st, _, body = p.post("/act/pause", on="1", csrf="not-the-token")
    assert st == 400 and "Stale form" in body
    assert p.bot.paused is False
    st, loc, _ = p.post("/act/pause", on="1")
    assert st == 303 and p.bot.paused is True
    p.post("/act/pause", on="0")
    assert p.bot.paused is False
    p.logout()
    print("  every POST needs the session's CSRF token  ok")


def test_dashboard_and_switches(p):
    p.login("hardclaws", ADMIN_PW)
    st, _, body = p.get("/")
    assert st == 200
    assert "NOT connected" in body           # no socket in the test
    assert "gsk_" not in body, "no secret on the dashboard"
    assert "Pause bot" in body and "Clear AI rests" in body
    for which, attr in (("so", "_so_off"), ("cb", "_cb_ambient_off"),
                        ("beef", "_beef_off")):
        p.post("/act/switch", which=which, off="1")
        assert getattr(p.bot, attr) is True, which
        p.post("/act/switch", which=which, off="0")
        assert getattr(p.bot, attr) is False, which
    st, _, body = p.get("/status.json")
    data = json.loads(body)
    assert data["paused"] is False and data["llm"]["provider"] == "Groq"
    p.logout()
    print("  dashboard: status, switches, no secrets  ok")


def test_speak_notice_reminders_commands(p):
    p.login("hardclaws", ADMIN_PW)
    st, loc, _ = p.post("/act/say", text="hello chat")
    assert "queued" in loc
    _drain(p.bot)
    assert p.bot.said[-1] == "hello chat"
    st, loc, _ = p.post("/act/say", text="/ban someone")
    assert "not allowed" in loc
    st, loc, _ = p.post("/act/notice", text="Doc is on the phone")
    assert p.bot._chat_ai_notice[0] == "Doc is on the phone"
    st, _, body = p.get("/")
    assert "Doc is on the phone" in body
    st, loc, _ = p.post("/act/notice", text="")
    assert p.bot._chat_ai_notice is None
    # The working memory shows on the dashboard and the Chat tab, and a
    # mod can end the game / drop a count from there.
    import ongoing as _og
    now = time.time()
    p.bot._ongoing.start(_og.Control("start", topic="cycling", rounds=3,
                                     what="a 3-round cycling quiz"), "Hardclaws", now)
    p.bot._ongoing.apply_tally(_og.Control("tally_start", key="dirty lepage",
                                           label="Dirty Lepages"), "Hardclaws", now)
    st, _, body = p.get("/")
    assert "3-round cycling quiz" in body and "Dirty Lepages" in body, body[-1500:]
    st, _, body = p.get("/chat")
    assert "/act/game/end" in body and 'name="key" value="dirty lepage"' in body
    st, loc, _ = p.post("/act/game/end")
    assert "game ended" in loc and p.bot._ongoing.current() is None
    st, loc, _ = p.post("/act/game/end")
    assert "no game" in loc
    st, loc, _ = p.post("/act/count/drop", key="dirty lepage")
    assert "count dropped" in loc and not p.bot._ongoing.tallies
    st, _, body = p.get("/")
    assert "No game being hosted" in body and "No counts being kept" in body
    st, loc, _ = p.post("/act/reminder/add", when="60mins", message="stretch")
    assert "reminder #1 set in 1 hour" in loc, loc
    assert [r.message for r in p.bot.reminders.pending()] == ["stretch"]
    st, loc, _ = p.post("/act/reminder/add", when="banana", message="x")
    assert "neither a delay" in loc
    st, loc, _ = p.post("/act/reminder/cancel", which="1")
    assert len(p.bot.reminders) == 0
    st, loc, _ = p.post("/act/command/set", name="discord", message="join {user}")
    assert "!discord created" in loc and p.bot.custom_cmds.get("discord") == "join {user}"
    st, loc, _ = p.post("/act/command/set", name="discord", message="join here")
    assert "!discord updated" in loc
    st, loc, _ = p.post("/act/command/set", name="funfact", message="x")
    assert "built-in" in loc
    # persisted for the bot's own reload
    fresh = customcmds.CommandSet(path=p.bot.custom_cmds.path,
                                  reserved=bot_mod.RESERVED_COMMANDS)
    assert fresh.get("discord") == "join here"
    st, loc, _ = p.post("/act/command/delete", name="discord")
    assert "deleted" in loc and "discord" not in p.bot.custom_cmds
    p.logout()
    print("  speak / notice / reminders / custom commands  ok")


def test_stream_tab(p):
    p.login("hardclaws", ADMIN_PW)
    st, loc, _ = p.post("/act/haul", text="Produce to Denver")
    assert p.bot.cargo.text == "Produce to Denver"
    assert p.bot.cargo.set_by == "panel:hardclaws"
    st, loc, _ = p.post("/act/haul", text="")
    assert p.bot.cargo.text == ""
    st, loc, _ = p.post("/act/subgoal", goal="50", current="12", label="clown suit")
    assert p.bot._subgoal == {"goal": 50, "current": 12, "label": "clown suit"}
    assert json.load(open(p.bot.cfg["subgoal_state_path"]))["goal"] == 50
    st, loc, _ = p.post("/act/subgoal", goal="1", current="0", label="x")
    assert "2 or more" in loc
    st, loc, _ = p.post("/act/subgoal", goal="", current="", label="")
    assert p.bot._subgoal == {}
    st, loc, _ = p.post("/act/persona", name="noir", custom="")
    assert p.bot._persona == {"name": "noir"}
    assert p.bot._persona_text().strip()          # a real persona resolves
    st, loc, _ = p.post("/act/persona", name="custom", custom="short")
    assert "12-300" in loc
    st, loc, _ = p.post("/act/persona", name="custom",
                        custom="a weary night-shift dispatcher who has seen it all")
    assert p.bot._persona["name"] == "custom"
    st, loc, _ = p.post("/act/persona", name="nope", custom="")
    assert "no voice" in loc
    p.post("/act/persona", name="doc", custom="")
    st, _, body = p.get("/stream")
    assert st == 200 and "Beef leaderboard" in body
    p.logout()
    print("  haul / sub goal / voice  ok")


def test_memory_tab(p):
    p.login("hardclaws", ADMIN_PW)
    p.bot._memory.remember("kvack", ["likes pancakes", "drives a Volvo"])
    st, _, body = p.get("/memory")
    assert st == 200 and "kvack" in body
    st, _, body = p.get("/memory?nick=kvack")
    assert "pancakes" in body and "Volvo" in body
    st, loc, _ = p.post("/act/forget", nick="kvack")
    assert "forgot kvack: 2 facts" in loc
    assert p.bot._memory.recall(["kvack"]) == []
    p.logout()
    print("  viewer memory: list, detail, forget  ok")


def test_config_editor(p):
    p.login("hardclaws", ADMIN_PW)
    st, _, body = p.get("/config")
    assert st == 200
    assert "gsk_secretsecret1234" not in body and ("gsk_" + ap.MASK) in body
    assert "_note" not in body and "admin_panel_bind" not in body
    # ordinary values save to disk AND apply live; the comment key survives
    st, loc, _ = p.post("/act/config", spice="spicy", chat_ai_notice_minutes="30",
                        llm_api_key="", chat_ai_enabled="true")
    assert "saved spice, chat_ai_notice_minutes" in loc, loc
    disk = p.config_on_disk()
    assert disk["spice"] == "spicy" and disk["chat_ai_notice_minutes"] == 30
    assert disk["llm_api_key"] == "gsk_secretsecret1234", "blank secret = unchanged"
    assert disk["_note"] == "a comment key, kept"
    assert p.bot.cfg["spice"] == "spicy" and p.bot._opts["spice"] == "spicy"
    assert p.bot._notice_minutes() == 30.0
    # a masked value posted straight back changes nothing
    st, loc, _ = p.post("/act/config", llm_api_key="gsk_" + ap.MASK + "1234")
    assert "nothing changed" in loc
    # a new secret is taken, and flagged as needing a restart
    st, loc, _ = p.post("/act/config", llm_api_key="gsk_newkey_9999")
    assert "restart the bot for llm_api_key" in loc
    assert p.config_on_disk()["llm_api_key"] == "gsk_newkey_9999"
    assert p.bot._opts["llm_api_key"] == "gsk_newkey_9999"
    # tick "clear" to blank it
    st, loc, _ = p.post("/act/config", llm_api_key="", clear_llm_api_key="1")
    assert p.config_on_disk()["llm_api_key"] == ""
    # lists are JSON; bad JSON keeps the old value
    st, loc, _ = p.post("/act/config", chat_ai_names="not json")
    assert p.config_on_disk()["chat_ai_names"] == ["doc", "docbot"]
    st, loc, _ = p.post("/act/config", chat_ai_names='["doc", "clawbot"]')
    assert p.config_on_disk()["chat_ai_names"] == ["doc", "clawbot"]
    # the CSRF token and the panel's own keys never land in the file
    st, loc, _ = p.post("/act/config", admin_panel_bind="0.0.0.0",
                        admin_panel_public="true")
    assert "nothing changed" in loc
    disk = p.config_on_disk()
    assert "csrf" not in disk and "admin_panel_bind" not in disk
    p.logout()
    print("  config: masked secrets, live apply, restart flags, atomic write  ok")


def test_log_tab(p):
    p.login("hardclaws", ADMIN_PW)
    st, _, body = p.get("/log")
    assert st == 200 and "hello from the log" in body
    st, _, body = p.get("/log/tail?since=0")
    lines = json.loads(body)["lines"]
    assert lines and lines[0]["s"] == "hello from the log"   # stamp stripped
    last = lines[-1]["n"]
    p.ring.write_text("[llm] something new\n")
    st, _, body = p.get("/log/tail?since=%d" % last)
    new = json.loads(body)["lines"]
    assert [x["s"] for x in new] == ["[llm] something new"]
    p.logout()
    print("  log: tail, incremental poll, stamps  ok")


def test_system_tab_users_and_restart(p):
    p.login("hardclaws", ADMIN_PW)
    st, _, body = p.get("/system")
    assert st == 200 and "Restart the bot" in body and "modguy" in body
    st, loc, _ = p.post("/act/user", name="newmod", password="a mod password!", role="mod")
    assert "saved" in loc and p.users.role("newmod") == "mod"
    st, loc, _ = p.post("/act/user", name="hardclaws", delete="1")
    assert "cannot delete yourself" in loc
    st, loc, _ = p.post("/act/user", name="newmod", delete="1")
    assert "deleted" in loc and p.users.role("newmod") is None
    st, loc, _ = p.post("/act/restart")
    assert "restarting" in loc
    time.sleep(0.8)
    assert p.restarts == [1]
    assert any("RESTART" in t for _, _, t in p.control.actions)
    p.logout()
    print("  system: users, restart hook, audit trail  ok")


def test_mod_role_is_fenced(p):
    st, loc = p.login("modguy", MOD_PW)
    assert loc == "/"
    st, _, body = p.get("/")
    assert ">Config<" not in body and "Clear AI rests" not in body
    for path in ("/config", "/memory", "/system"):
        st, _, _ = p.get(path)
        assert st == 403, path
    for path, data in (("/act/restart", {}), ("/act/config", {"spice": "clean"}),
                       ("/act/forget", {"nick": "x"}), ("/act/llm/reset", {}),
                       ("/act/user", {"name": "evil", "password": "evil password!", "role": "admin"}),
                       ("/act/beef/reset", {})):
        st, _, _ = p.post(path, **data)
        assert st == 403, path
    assert p.restarts == [1], "the mod's restart must not have fired"
    assert p.users.role("evil") is None
    assert p.config_on_disk()["spice"] == "spicy"
    # ...but the operational switches work
    st, loc, _ = p.post("/act/pause", on="1")
    assert st == 303 and p.bot.paused is True
    p.post("/act/pause", on="0")
    st, loc, _ = p.post("/act/say", text="mods can talk")
    assert "queued" in loc
    p.logout()
    print("  mod role: switches yes, config/memory/system/restart no  ok")


def test_admin_user_cli(monkeypatch=None):
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "u.json")
    os.environ["ADMIN_PANEL_PASSWORD"] = "set from the environment"
    try:
        assert ap.set_user_interactive("Doc", "admin", path) == 0
        assert ap.set_user_interactive("helper", "mod", path) == 0
        assert ap.set_user_interactive("x", "nope", path) == 1
        os.environ["ADMIN_PANEL_PASSWORD"] = "short"
        assert ap.set_user_interactive("y", "mod", path) == 1
    finally:
        del os.environ["ADMIN_PANEL_PASSWORD"]
    users = ap.Users(path)
    assert users.role("doc") == "admin" and users.role("helper") == "mod"
    assert users.verify("doc", "set from the environment") == "admin"
    print("  --admin-user CLI (env password, roles, refusals)  ok")


def main() -> int:
    print("adminpanel tests")
    test_passwords_are_hashed_and_checked()
    test_bind_safety()
    test_admin_user_cli()
    p = Panel()
    try:
        test_anonymous_sees_only_the_login_page(p)
        test_login_lockout_and_logging(p)
        test_csrf_is_required(p)
        test_dashboard_and_switches(p)
        test_speak_notice_reminders_commands(p)
        test_stream_tab(p)
        test_memory_tab(p)
        test_config_editor(p)
        test_log_tab(p)
        test_system_tab_users_and_restart(p)
        test_mod_role_is_fenced(p)
    finally:
        p.close()
    print("\nALL PASSED \u2714")
    return 0


if __name__ == "__main__":
    sys.exit(main())
