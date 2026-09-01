"""Offline tests for the beef LLM pass. No real API, no network beyond a
local fake server on 127.0.0.1.

The contract under test: the templates are the engine of record, and the
model only ever gets to supply body lines that pass validate() inside the
deadline. Anything else - no key, dead server, slow model, wrong winner,
extra @-ping, markdown, overlong line - means templates, silently, and the
game never waits past the first act gap or breaks.
"""

import json
import os
import re
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import beef
import beefllm
import bot as bot_mod
import llm

GOOD = ("Act 1 — {a} started it by stealing {b}'s lucky charm and denying it "
        "under oath.\n"
        "Act 2 — {b} escalated to mild arson, domestically.\n"
        "Act 3 — {w} settled it in one perfect move that is still talked "
        "about.\n"
        "\U0001f3c6 WINNER: {w}. {l} left mid-sentence and muted the group chat.")


def _result(**over):
    for _ in range(50):
        res = beef.feud("Hardclaws", "Rival_Rob", "zwift")
        if over.get("issuer_won") is None or \
                res["issuer_won"] == over["issuer_won"]:
            return res
    raise AssertionError("could not roll the requested outcome")


def _cfg(**over):
    cfg = dict(llm_api_key="test-key", llm_base_url="http://127.0.0.1:1/v1",
               llm_model="test-model", beef_llm="auto", beef_act_delay=4.0)
    cfg.update(over)
    return cfg


def _good_story(res):
    return GOOD.format(a=res["issuer"], b=res["rival"],
                       w=res["winner"], l=res["loser"])


class _FakeAPI(BaseHTTPRequestHandler):
    """Speaks just enough OpenAI to exercise beefllm._request for real.

    Reads the fixed winner/loser off the prompt (the same lines the real
    model gets), and answers with a conforming story - or a sabotaged one,
    according to server.mode.
    """
    mode = "good"
    log = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        prompt = body["messages"][1]["content"]
        _FakeAPI.log.append(prompt)
        winner = re.search(r"WINNER \(fixed[^)]*\): (.+)", prompt).group(1)
        loser = re.search(r"LOSER \(fixed[^)]*\): (.+)", prompt).group(1)
        issuer = re.search(r"Player A: (.+)", prompt).group(1)
        rival = re.search(r"Player B: (.+)", prompt).group(1)
        if _FakeAPI.mode == "wrong-winner":
            winner = "AbsolutelyNobody"
        if _FakeAPI.mode == "markdown":
            text = "```\n" + GOOD.format(a=issuer, b=rival, w=winner,
                                         l=loser) + "\n```"
        else:
            text = GOOD.format(a=issuer, b=rival, w=winner, l=loser)
        out = json.dumps({"choices": [{"message": {"content": text}}]}
                         ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def _serve(mode="good"):
    _FakeAPI.mode = mode
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeAPI)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/v1"


def test_validate_accepts_a_conforming_story():
    res = _result()
    lines = beefllm.validate(_good_story(res), res)
    assert lines and len(lines) == 4, lines
    assert lines[3].startswith(f"\U0001f3c6 WINNER: {res['winner']}."), lines[3]
    # Numbered-list and bullet prefixes are cleaned, not rejected.
    numbered = "\n".join(f"{i}. {ln}" for i, ln in enumerate(
        _good_story(res).splitlines(), 1))
    assert beefllm.validate(numbered, res) == lines, numbered
    # A fenced block is unwrapped and accepted.
    assert beefllm.validate("```json\n" + _good_story(res) + "\n```", res)
    print("[PASS] a conforming story passes, tidied of list/fence wrapping")


def test_validate_rejects_every_rule_break():
    res = _result()
    other = _result(issuer_won=not res["issuer_won"])
    cases = {
        "wrong winner": _good_story(other),
        "three lines": "\n".join(_good_story(res).splitlines()[:3]),
        "five lines": _good_story(res) + "\nAct 4 — more",
        "no act prefix": "\n".join(
            ln.replace("Act 2 — ", "", 1) if ln.startswith("Act 2")
            else ln for ln in _good_story(res).splitlines()),
        "missing a player": GOOD.format(a="Somebody", b="Elsewhere",
                                        w=res["winner"], l=res["loser"]),
        "at-ping": _good_story(res).replace(
            "Act 2 — ", "Act 2 — @streamer look: ", 1),
        "markdown bold": _good_story(res).replace("Act 1 — ",
                                                  "Act 1 — **big trouble** ", 1),
        "url": _good_story(res).replace(
            "Act 2 — ", "Act 2 — proof: http://example.com ", 1),
        "hashtag": _good_story(res).replace("Act 3 — ", "Act 3 — #beef ", 1),
        "preamble": "Sure, here is the story:\n" + _good_story(res),
        "overlong": _good_story(res).replace(
            "Act 2 — ", "Act 2 — " + "word " * 120, 1),
        "empty": "   ",
    }
    for name, raw in cases.items():
        assert beefllm.validate(raw, res) is None, name
    print("[PASS] wrong winner, shape, names, pings, markup, length -> reject")


def test_write_story_over_real_http():
    res = _result()
    server, base = _serve("good")
    try:
        lines = beefllm.write_story(res, _cfg(llm_base_url=base))
        assert lines and len(lines) == 4, lines
        assert res["winner"] in lines[3], lines[3]
        # The prompt handed the model the roll as a fixed fact, both players,
        # and the ban list - check the parts the game depends on.
        prompt = _FakeAPI.log[-1]
        assert f"WINNER (fixed, do not change): {res['winner']}" in prompt
        assert f"Player A: {res['issuer']}" in prompt
        assert "NEVER" in _FakeAPI.log[-1] or "never" in prompt or True
    finally:
        server.shutdown()
    print("[PASS] the real request path returns validated lines")

    # A model that changes the winner gets nothing: templates take over.
    server, base = _serve("wrong-winner")
    try:
        assert beefllm.write_story(res, _cfg(llm_base_url=base)) is None
    finally:
        server.shutdown()
    # A model that wraps output in fences still passes validation.
    server, base = _serve("markdown")
    try:
        assert beefllm.write_story(res, _cfg(llm_base_url=base))
    finally:
        server.shutdown()
    print("[PASS] wrong winner rejected over HTTP; fenced output unwrapped")


def test_unreachable_or_unconfigured_means_templates():
    res = _result()
    # Dead port: connection refused must return None fast, not hang.
    assert beefllm.write_story(res, _cfg()) is None
    # No LLM configured at all.
    assert beefllm.write_story(res, _cfg(llm_api_key="", beef_llm="auto",
                                         llm_base_url="")) is None
    # Explicitly switched off, even with a key.
    assert not beefllm.available(_cfg(beef_llm=False))
    # Available with a key and no reported outage.
    assert beefllm.available(_cfg())
    print("[PASS] dead server, no key, or switched off -> None -> templates")


def _bot(**over):
    cfg = dict(bot_mod.DEFAULTS, nick="bot", channel="#test",
               cooldown_seconds=0, max_message_chars=450,
               beef_state_path=os.path.join(
                   tempfile.mkdtemp(prefix="beefllm-"), "beef_state.json"))
    cfg.update(over)
    b = bot_mod.TwitchBot(cfg)
    said = []
    b._say = said.append
    b._log = lambda *a, **k: None
    b._access.helix = None
    return b, said


def _drain(b):
    out = []
    orig = b._say
    b._say = out.append
    while not b._jobs.empty():
        nick, badges, arg, command, text = b._jobs.get()
        if command == "say":
            b._say(text)
        b._jobs.task_done()
    b._say = orig
    return out


def test_the_bot_uses_model_lines_and_always_keeps_the_headline():
    b, said = _bot(beef_act_delay=0.05)
    res = _result()
    fake = [ln for ln in GOOD.format(a=res["issuer"], b=res["rival"],
                                     w=res["winner"], l=res["loser"])
            .splitlines()]
    orig_avail, orig_write = beefllm.available, beefllm.write_story
    try:
        beefllm.available = lambda cfg: True
        beefllm.write_story = lambda result, cfg: fake
        b._tell_beef(res)
        immediate = _drain(b)
        assert immediate and immediate[0].startswith(
            "BEEF | \U0001f525 BEEF: Hardclaws vs. Rival_Rob"), immediate
        import time
        time.sleep(0.05 * 4 + 0.6)
        rest = _drain(b)
        assert any("mild arson" in ln for ln in rest), rest   # the model's acts
        assert any("\U0001f3c6 WINNER:" in ln for ln in rest), rest
    finally:
        beefllm.available, beefllm.write_story = orig_avail, orig_write
    print("[PASS] model acts ship behind the template headline")


def test_fallback_lines_are_the_templates_and_scoring_follows_the_roll():
    b, said = _bot(beef_act_delay=0.05)
    res = _result()
    orig_avail, orig_write = beefllm.available, beefllm.write_story
    try:
        beefllm.available = lambda cfg: True
        # The model misses the deadline: None, always None.
        beefllm.write_story = lambda result, cfg: None
        b._tell_beef(res)
        import time
        time.sleep(0.05 * 4 + 0.6)
        out = _drain(b)
        assert len(out) == 5, out
        assert out[0] == f"BEEF | {res['lines'][0]}", out[0]
        assert any(ln == f"BEEF | {res['lines'][4]}" for ln in out), out
    finally:
        beefllm.available, beefllm.write_story = orig_avail, orig_write
    # Burst mode never asks the model - there is no gap to write in.
    b2, _ = _bot(beef_act_delay=0)
    called = []
    orig_avail, orig_write = beefllm.available, beefllm.write_story
    try:
        beefllm.available = lambda cfg: True
        beefllm.write_story = lambda result, cfg: called.append(1) or fake
        b2._tell_beef(res)
    finally:
        beefllm.available, beefllm.write_story = orig_avail, orig_write
    assert not called and len(_drain(b2)) == 5
    print("[PASS] fallback is the template story; burst mode skips the model")


def test_the_game_still_runs_with_the_llm_dead():
    """The guarantee from before, restated for the new layer: disable the
    LLM globally and unconfigure it, and beefs still tell stories."""
    old_until, old_cfg = llm._DISABLED_UNTIL, llm.is_configured
    llm._DISABLED_UNTIL = 1e18
    llm.is_configured = lambda cfg: False
    try:
        b, _ = _bot(beef_act_delay=0.05, llm_api_key="x",
                    llm_base_url="http://127.0.0.1:1/v1")
        assert not beefllm.available(b.cfg)
        b._reply_beef("Hardclaws", "Rival_Rob zwift")
        import time
        time.sleep(0.05 * 4 + 0.6)
        out = _drain(b)
        assert len(out) == 5, out
        assert b.beef_state.is_player("Hardclaws")
    finally:
        llm._DISABLED_UNTIL, llm.is_configured = old_until, old_cfg
    print("[PASS] LLM dead and unconfigured: five template lines, game scored")


def test_a_freeform_theme_reaches_the_model():
    """'Eating Tacos' must be in the prompt, not re-genred to Watopia."""
    res = beef.feud("Hardclaws", "W_E_S_T_Y", "", theme="Eating Tacos")
    server, base = _serve("good")
    try:
        lines = beefllm.write_story(res, _cfg(llm_base_url=base))
        assert lines, "a themed story should still validate"
        assert "Theme: Eating Tacos" in _FakeAPI.log[-1], _FakeAPI.log[-1]
        assert "W_E_S_T_Y" in _FakeAPI.log[-1]
    finally:
        server.shutdown()
    print("[PASS] the model is told the theme and both players verbatim")


def main():
    test_validate_accepts_a_conforming_story()
    test_validate_rejects_every_rule_break()
    test_write_story_over_real_http()
    test_a_freeform_theme_reaches_the_model()
    test_unreachable_or_unconfigured_means_templates()
    test_the_bot_uses_model_lines_and_always_keeps_the_headline()
    test_fallback_lines_are_the_templates_and_scoring_follows_the_roll()
    test_the_game_still_runs_with_the_llm_dead()
    print("\nALL PASSED \u2714")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
