"""Offline tests for llm.py (Groq / OpenAI-compatible client) — no network.

Run:  python3 mock_llm_test.py
"""

import io
import json
import urllib.error

import llm

FAKE_BODY = {
    "id": "chatcmpl-1",
    "choices": [{"index": 0, "message": {"role": "assistant", "content":
        "1. Milford was founded in 1796 by a judge who clearly enjoyed being first.\n"
        "2. Its population is small enough that everyone knows the mayor's business.\n"
        "3. The first circuit judge in Pennsylvania set up shop right here."}}],
}

captured = []


def _fake_urlopen(req, timeout=60):
    captured.append({"url": req.full_url, "headers": req.headers,
                     "body": req.data.decode("utf-8"), "timeout": timeout})
    return io.BytesIO(json.dumps(FAKE_BODY).encode("utf-8"))


def main():
    ok = True
    cfg = {"llm_api_key": "gsk-test", "llm_base_url": "https://api.groq.com/openai/v1",
           "llm_model": "openai/gpt-oss-120b"}

    orig = llm.urllib.request.urlopen
    llm.urllib.request.urlopen = _fake_urlopen
    try:
        text = llm.rewrite_fact("Milford, Pennsylvania", "Milford, PA",
                                ["Milford was founded in 1796 by Judge John Biddis."], cfg)
        assert text and "1796" in text, text
        lines = [ln for ln in text.splitlines() if ln.strip()]
        assert len(lines) == 3, lines
        print("[PASS] llm rewrite returns 3 lines")

        req = captured[0]
        assert req["url"] == "https://api.groq.com/openai/v1/chat/completions", req["url"]
        assert req["headers"]["Authorization"] == "Bearer gsk-test"
        # Cloudflare 1010 fix: must send a browser User-Agent, never Python-urllib.
        ua = next((v for k, v in req["headers"].items() if k.lower() == "user-agent"), "")
        assert ua, req["headers"]
        assert "Python-urllib" not in ua, ua
        assert "Mozilla" in ua, ua
        body = json.loads(req["body"])
        assert body["model"] == "openai/gpt-oss-120b"
        # Reasoning model: max_completion_tokens, NO temperature.
        assert "max_completion_tokens" in body and "temperature" not in body, body
        assert "Milford" in body["messages"][1]["content"]
        print("[PASS] Groq URL + bearer auth + reasoning-model payload (no temperature)")

        # Non-reasoning model should get temperature + max_tokens.
        cfg2 = dict(cfg, llm_model="llama-3.3-70b-versatile")
        captured.clear()
        llm.rewrite_fact("X", "X", ["f"], cfg2)
        b2 = json.loads(captured[0]["body"])
        assert "temperature" in b2 and "max_tokens" in b2, b2
        assert "max_completion_tokens" not in b2, b2
        print("[PASS] non-reasoning model payload uses temperature + max_tokens")

        # HTTP error path -> None (no crash), and a 401 disables the LLM so we
        # don't hammer the API with the same bad key on every request.
        def boom(req, timeout=60):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"error":"bad key"}'))
        llm.urllib.request.urlopen = boom
        assert llm.rewrite_fact("X", "X", ["f"], cfg) is None
        assert llm._unavailable(), "401 must disable the LLM for the session"
        calls = len(captured)
        assert llm.rewrite_fact("X", "X", ["f"], cfg) is None
        assert len(captured) == calls, "disabled LLM must not make another HTTP request"
        assert llm.summarize("some long fact that needs shortening", 40, cfg) is None
        assert len(captured) == calls, "disabled summarize must not hit the API"
        llm.reset_disable_state()
        assert not llm._unavailable()
        print("[PASS] 401 disables the LLM (no further requests); reset restores it")
    except Exception as exc:
        ok = False
        print(f"[FAIL] {exc!r}")
    finally:
        llm.urllib.request.urlopen = orig

    # No key + hosted base -> None without any request.
    captured.clear()
    llm.urllib.request.urlopen = _fake_urlopen
    assert llm.rewrite_fact("X", "X", ["f"], {}) is None
    assert not captured
    print("[PASS] llm skipped when no key set")

    # is_configured(): key -> True; localhost no key -> True; hosted no key -> False.
    assert llm.is_configured({"llm_api_key": "gsk-x"}) is True
    assert llm.is_configured({"llm_base_url": "http://localhost:11434/v1"}) is True
    assert llm.is_configured({"llm_base_url": "https://api.groq.com/openai/v1"}) is False
    assert llm.is_configured({}) is False
    print("[PASS] is_configured detects key and local Ollama")

    # Local Ollama (no key): should make a request with no Authorization header.
    captured.clear()
    llm.urllib.request.urlopen = _fake_urlopen
    ocfg = {"llm_base_url": "http://localhost:11434/v1", "llm_model": "llama3.1:8b"}
    text3 = llm.rewrite_fact("X", "X", ["f"], ocfg)
    assert text3, "Ollama path should return text"
    req3 = captured[0]
    assert req3["url"] == "http://localhost:11434/v1/chat/completions", req3["url"]
    assert not any(k.lower() == "authorization" for k in req3["headers"]), req3["headers"]
    b3 = json.loads(req3["body"])
    assert b3["model"] == "llama3.1:8b"
    assert "temperature" in b3 and "max_tokens" in b3, b3
    print("[PASS] local Ollama request (no auth header, non-reasoning payload)")
    llm.urllib.request.urlopen = orig

    # summarize(): returns the shortened fact, or None when unconfigured.
    def fake_sum(req, timeout=60):
        captured.append({"url": req.full_url, "headers": req.headers, "body": req.data.decode("utf-8")})
        return io.BytesIO(json.dumps(
            {"choices": [{"message": {"content": "Eric Frein was caught after a 48-day manhunt."}}]}
        ).encode("utf-8"))
    llm.urllib.request.urlopen = fake_sum
    s = llm.summarize("Eric Matthew Frein was identified as the sole suspect of the ambush and was sought by federal and state authorities.", 60, cfg)
    assert s == "Eric Frein was caught after a 48-day manhunt.", s
    sb = json.loads(captured[-1]["body"])
    assert "Max characters" in sb["messages"][1]["content"]
    assert sb["messages"][0]["content"] != llm.SYSTEM_PROMPT  # summarise prompt, not the fact-writer
    llm.urllib.request.urlopen = orig
    assert llm.summarize("x" * 300, 60, {}) is None
    print("[PASS] llm summarize returns shortened fact; None when unconfigured")

    # Qwen3's /no_think switch: appended to every prompt when
    # llm_no_think is set, absent otherwise. On CPU a thinking Qwen3
    # turns a one-line chat reply into a half-minute stall.
    llm.urllib.request.urlopen = _fake_urlopen
    try:
        captured.clear()
        llm.chat_reply("system", "say something dry",
                       {"llm_api_key": "k", "llm_no_think": True})
        body = json.loads(captured[-1]["body"])
        assert body["messages"][-1]["content"].endswith("/no_think"), body
        captured.clear()
        llm.chat_reply("system", "say something dry",
                       {"llm_api_key": "k"})
        body = json.loads(captured[-1]["body"])
        assert "/no_think" not in body["messages"][-1]["content"], body
        captured.clear()
        llm.answer_question("q?", ["a source line"],
                            {"llm_api_key": "k", "llm_no_think": True})
        body = json.loads(captured[-1]["body"])
        assert body["messages"][-1]["content"].endswith("/no_think"), body
    finally:
        llm.urllib.request.urlopen = orig
    print("[PASS] llm_no_think appends Qwen3's switch to every prompt")

    # The warm-up: one tiny request at startup so the first line of chat
    # does not pay the model's cold start (10-25s for a local 8B model).
    llm.reset_disable_state()
    llm.urllib.request.urlopen = _fake_urlopen
    try:
        captured.clear()
        assert llm.warm_up({"llm_api_key": "k"}) is True
        body = json.loads(captured[-1]["body"])
        assert body["messages"][-1]["content"].endswith("OK"), body
        assert llm.warm_up({}) is False      # unconfigured: no request
        assert len(captured) == 1, captured
        # An empty reply is odd but must not be silent: a missing
        # warm-up line in the log reads as the feature being off.
        import contextlib
        import io as _io2
        orig_call2 = llm._call
        llm._call = lambda *a, **k: ""
        out = _io2.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                got = llm.warm_up({"llm_api_key": "k"})
        finally:
            llm._call = orig_call2
        assert got is False
        assert "empty reply" in out.getvalue(), out.getvalue()
    finally:
        llm.urllib.request.urlopen = orig
    print("[PASS] warm-up loads the model at startup; unconfigured skips")

    # The chat budget is provider-aware: 8s was tuned for hosted APIs and
    # kept timing out WARM local models - the chat prompt is the big one,
    # and reading it on CPU runs 10s+. Local self-defaults to 30s;
    # chat_ai_timeout still overrides both ways.
    llm.urllib.request.urlopen = _fake_urlopen
    try:
        captured.clear()
        llm.chat_reply("s", "u", {"llm_api_key": "k",
                                  "llm_base_url": "http://127.0.0.1:11434/v1"})
        assert captured[-1]["timeout"] == 30.0, captured[-1]
        body = json.loads(captured[-1]["body"])
        assert body["max_tokens"] == 120, body
        captured.clear()
        llm.chat_reply("s", "u", {"llm_api_key": "k",
                                  "llm_base_url": "https://api.groq.com/openai/v1"})
        assert captured[-1]["timeout"] == 8.0, captured[-1]
        captured.clear()
        llm.chat_reply("s", "u", {"llm_api_key": "k",
                                  "llm_base_url": "http://127.0.0.1:11434/v1",
                                  "chat_ai_timeout": 15})
        assert captured[-1]["timeout"] == 15.0, captured[-1]
        captured.clear()
        llm.chat_reply("s", "u", {"llm_api_key": "k",
                                  "llm_base_url": "http://127.0.0.1:11434/v1",
                                  "chat_ai_timeout": 99})
        assert captured[-1]["timeout"] == 30.0, captured[-1]   # the clamp
    finally:
        llm.urllib.request.urlopen = orig
    import bot as _b
    assert "chat_ai_timeout" not in _b.DEFAULTS   # absence reaches llm.py
    print("[PASS] local chat budget self-defaults to 30s; chat is capped "
          "at 120 tokens")

    # A timed-out model is not asked twice: the flag is set by a chat
    # timeout, cleared by the next success, and the question path reads
    # fewer sources with a local-sized budget.
    calls = []
    orig_call = llm._call
    try:
        def _boom(base, model, key, user, system=None, timeout=60.0,
                  max_tokens=None, hard_nothink=False):
            calls.append((user, timeout))
            raise TimeoutError("timed out")

        llm._call = _boom
        assert llm.chat_reply("s", "u", {"llm_api_key": "k",
                                         "llm_base_url":
                                         "http://127.0.0.1:11434/v1"}) is None
        assert llm.chat_timed_out() is True
        llm._call = (lambda base, model, key, user, system=None,
                     timeout=60.0, max_tokens=None, hard_nothink=False:
                     "fine and rolling again")
        assert llm.chat_reply("s", "u", {"llm_api_key": "k"}) == \
            "fine and rolling again"
        assert llm.chat_timed_out() is False
        # The question path: 5 sources and a 30s budget locally, 8 and 60
        # hosted.
        seen = []
        llm._call = (lambda base, model, key, user, system=None,
                     timeout=60.0, max_tokens=None, hard_nothink=False:
                     (seen.append((user, timeout)) or "a line."))
        llm.answer_question(
            "q?", ["source %d." % i for i in range(10)],
            {"llm_api_key": "k", "llm_base_url":
             "http://127.0.0.1:11434/v1"})
        assert seen[-1][1] == 30.0, seen[-1]
        assert seen[-1][0].count("- source") == 5, seen[-1][0]
        llm.answer_question(
            "q?", ["source %d." % i for i in range(10)],
            {"llm_api_key": "k"})
        assert seen[-1][1] == 60.0, seen[-1]
        assert seen[-1][0].count("- source") == 8, seen[-1][0]
    finally:
        llm._call = orig_call
        llm._set_chat_timeout(False)
    print("[PASS] a timed-out model is flagged; the question path is "
          "smaller locally")

    # qwen3 wraps answers in an empty <think></think> block even with
    # /no_think, and the token cap counts the stripped block: the
    # warm-up's tight cap cut the answer out entirely (live-fire: 'warm-up
    # got an empty reply from qwen3:4b'). Think blocks are stripped from
    # every reply, and the warm-up retries generously.
    import copy
    orig_body = copy.deepcopy(FAKE_BODY)
    llm.urllib.request.urlopen = _fake_urlopen
    try:
        FAKE_BODY["choices"][0]["message"]["content"] = \
            "<think>\n\n</think>\nOK"
        got = llm.chat_reply("s", "u", {"llm_api_key": "k"})
        assert got == "OK", got
        FAKE_BODY["choices"][0]["message"]["content"] = \
            "<think>\nthe user wants OK. I will say it."
        got = llm.chat_reply("s", "u", {"llm_api_key": "k"})
        assert not got, got         # cut inside the think: no answer
        # (an empty reply is retried once at a bigger budget now; the
        # retry hits the same cut think block and still has no answer)
    finally:
        FAKE_BODY.clear()
        FAKE_BODY.update(orig_body)
    orig_call = llm._call
    try:
        caps = []

        def _two(base, model, key, user, system=None, timeout=60.0,
                 max_tokens=None, hard_nothink=False):
            caps.append(max_tokens)
            return "OK" if len(caps) > 1 else ""

        llm._call = _two
        assert llm.warm_up({"llm_api_key": "k"}) is True
        assert caps == [24, 200], caps
    finally:
        llm._call = orig_call
    print("[PASS] think blocks are stripped; the warm-up retries with a "
          "generous cap")

    # The HARD switch: /no_think is only a soft request and qwen3:4b
    # ignored it, thinking anyway until every capped generation died
    # inside the think block. Local + llm_no_think now sends Ollama's
    # engine-level think:false; hosted providers never see the field.
    llm.urllib.request.urlopen = _fake_urlopen
    try:
        captured.clear()
        llm.chat_reply("s", "u", {"llm_api_key": "",
                                  "llm_base_url":
                                  "http://127.0.0.1:11434/v1",
                                  "llm_no_think": True})
        body = json.loads(captured[-1]["body"])
        assert body.get("think") is False, body
        assert body["messages"][-1]["content"].endswith("/no_think"), body
        captured.clear()
        llm.chat_reply("s", "u", {"llm_api_key": "",
                                  "llm_base_url":
                                  "http://127.0.0.1:11434/v1"})
        body = json.loads(captured[-1]["body"])
        assert "think" not in body, body
        captured.clear()
        llm.chat_reply("s", "u", {"llm_api_key": "k",
                                  "llm_no_think": True})
        body = json.loads(captured[-1]["body"])
        assert "think" not in body, body
        captured.clear()
        assert llm.warm_up({"llm_api_key": "",
                            "llm_base_url": "http://127.0.0.1:11434/v1",
                            "llm_no_think": True}) is True
        body = json.loads(captured[-1]["body"])
        assert body.get("think") is False, body
    finally:
        llm.urllib.request.urlopen = orig
    print("[PASS] local + llm_no_think sends Ollama's hard think:false; "
          "hosted never sees it")

    # A 400/5xx from the chat endpoint used to vanish without a log
    # line - the single worst way to debug a silent bot (an Ollama build
    # that rejects the think field would 400 here).
    import contextlib
    import urllib.error as _ue

    def _400(req, timeout=60):
        raise _ue.HTTPError(req.full_url, 400, "Bad Request", {},
                            io.BytesIO(b'{"error":"unknown field"}'))
    llm.urllib.request.urlopen = _400
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            got = llm.chat_reply("s", "u", {"llm_api_key": "k"})
    finally:
        llm.urllib.request.urlopen = orig
    assert got is None
    assert "HTTP 400" in out.getvalue(), out.getvalue()
    print("[PASS] an unexpected HTTP code on the chat call is logged")

    # The fallback provider: Groq's free tier 429s mid-stream and the
    # chat voice used to go dark for the two-minute breaker window. A
    # configured second provider (OpenRouter here) carries the line
    # instead - and when the primary's breaker is open, it is not even
    # asked again.
    def _groq_429_openrouter_ok(req, timeout=60):
        captured.append({"url": req.full_url, "headers": req.headers,
                         "body": req.data.decode("utf-8"),
                         "timeout": timeout})
        if "groq" in req.full_url:
            raise _ue.HTTPError(req.full_url, 429, "Too Many Requests",
                                {}, io.BytesIO(b'{"error":"rate limited"}'))
        return io.BytesIO(json.dumps(
            {"choices": [{"message": {"content": "Fallback line."}}]}
        ).encode("utf-8"))

    llm.urllib.request.urlopen = _groq_429_openrouter_ok
    fbcfg = {"llm_api_key": "gsk-test",
             "llm_base_url": "https://api.groq.com/openai/v1",
             "llm_model": "openai/gpt-oss-120b",
             "llm_fallback_key": "or-test",
             "llm_fallback_base_url": "https://openrouter.ai/api/v1",
             "llm_fallback_model": "mistralai/mistral-nemo"}
    # fallback_endpoint itself: off by default, needs a model, a local
    # base needs no key, and the primary wearing a different hat is not
    # a fallback.
    assert llm.fallback_endpoint({}) is None
    assert llm.fallback_endpoint({"llm_fallback_key": "k"}) is None
    assert llm.fallback_endpoint(
        {"llm_fallback_model": "llama3.1:8b",
         "llm_fallback_base_url": "http://localhost:11434/v1"}) is not None
    # The natural api_key spelling used in hand-edited configs must not be
    # silently ignored.
    assert llm.fallback_endpoint(
        {"llm_fallback_api_key": "alias-key",
         "llm_fallback_model": "fallback/model"}) == (
            "https://openrouter.ai/api/v1", "alias-key", "fallback/model")
    assert llm.is_configured(
        {"llm_fallback_api_key": "alias-key",
         "llm_fallback_model": "fallback/model"}) is False
    assert llm.any_configured(
        {"llm_fallback_api_key": "alias-key",
         "llm_fallback_model": "fallback/model"}) is True
    assert llm.fallback_endpoint(
        {"llm_api_key": "k", "llm_model": "m",
         "llm_base_url": "https://api.groq.com/openai/v1",
         "llm_fallback_key": "k",
         "llm_fallback_base_url": "https://api.groq.com/openai/v1",
         "llm_fallback_model": "m"}) is None
    try:
        llm.reset_disable_state()
        captured.clear()
        got = llm.chat_reply("s", "u" * 20, fbcfg)
        assert got == "Fallback line.", got
        assert [c["url"] for c in captured] == [
            "https://api.groq.com/openai/v1/chat/completions",
            "https://openrouter.ai/api/v1/chat/completions"], captured
        auth = captured[1]["headers"]["Authorization"]
        assert auth == "Bearer or-test", auth
        assert json.loads(captured[1]["body"])["model"] == \
            "mistralai/mistral-nemo"
        assert llm._unavailable(), "the 429 must open Groq's window"
        # Inside the window the primary is not asked again - the next
        # line goes straight to the fallback.
        captured.clear()
        got = llm.chat_reply("s", "u" * 20, fbcfg)
        assert got == "Fallback line.", got
        assert [c["url"] for c in captured] == [
            "https://openrouter.ai/api/v1/chat/completions"], captured
        # Warm-up warms the fallback too - and with the primary's
        # breaker open it warms ONLY the fallback.
        captured.clear()
        assert llm.warm_up(fbcfg) is True
        assert [c["url"] for c in captured] == [
            "https://openrouter.ai/api/v1/chat/completions"], captured
        llm.reset_disable_state()
    finally:
        llm.urllib.request.urlopen = orig
    print("[PASS] a rate-limited provider hands chat to the fallback")

    # Factual answers used a different helper and ignored the configured
    # provider fallback completely. The same 429 failover must cover every LLM
    # path, not just persona chat.
    llm.urllib.request.urlopen = _groq_429_openrouter_ok
    try:
        llm.reset_disable_state()
        captured.clear()
        got = llm.answer_question(
            "What time is sunrise?", ["Sunrise is at 6:38 AM."], fbcfg)
        assert got == "Fallback line.", got
        assert [c["url"] for c in captured] == [
            "https://api.groq.com/openai/v1/chat/completions",
            "https://openrouter.ai/api/v1/chat/completions"], captured
        # The primary breaker is now open; a fact rewrite goes straight to the
        # second provider instead of returning None before _complete can run.
        captured.clear()
        got = llm.rewrite_fact("Vandalia", "Vandalia, IL",
                               ["Vandalia was once the Illinois capital."],
                               fbcfg)
        assert got == "Fallback line.", got
        assert [c["url"] for c in captured] == [
            "https://openrouter.ai/api/v1/chat/completions"], captured
        llm.reset_disable_state()
    finally:
        llm.urllib.request.urlopen = orig
    print("[PASS] sourced answers and fact writing use provider failover too")

    # When BOTH providers are rate-limited, the bot goes quiet politely:
    # each breaker opens once, and a call with both windows open makes
    # no request at all.
    def _all_429(req, timeout=60):
        captured.append({"url": req.full_url, "headers": req.headers,
                         "body": req.data.decode("utf-8"),
                         "timeout": timeout})
        raise _ue.HTTPError(req.full_url, 429, "Too Many Requests", {},
                            io.BytesIO(b"{}"))

    llm.urllib.request.urlopen = _all_429
    try:
        llm.reset_disable_state()
        captured.clear()
        assert llm.chat_reply("s", "u" * 20, fbcfg) is None
        captured.clear()
        assert llm.chat_reply("s", "u" * 20, fbcfg) is None
        assert not captured, captured
        llm.reset_disable_state()
    finally:
        llm.urllib.request.urlopen = orig
    print("[PASS] both providers down: two breakers, no request storm")

    # An EMPTY reply (live-fire: a held mention 'answered' at 17:33:23
    # came back with nothing at 17:33:24 - the reasoning model thought
    # past its completion budget) gets ONE retry at a doubled thinking
    # budget, and only then hands the line to the fallback.
    def _empty_chain(groq_replies, fallback_reply):
        state = {"bodies": []}

        def _fake(req, timeout=60):
            captured.append({"url": req.full_url, "headers": req.headers,
                             "body": req.data.decode("utf-8"),
                             "timeout": timeout})
            if "groq" in req.full_url:
                state["bodies"].append(json.loads(req.data.decode()))
                r = groq_replies[len(state["bodies"]) - 1]
            else:
                r = fallback_reply
            return io.BytesIO(json.dumps(
                {"choices": [{"message": {"content": r}}]}
            ).encode("utf-8"))
        return _fake, state

    # Retry answers it: two Groq calls, the second at the doubled budget.
    _fake, state = _empty_chain(["", "Second try."], "FB.")
    llm.urllib.request.urlopen = _fake
    try:
        llm.reset_disable_state()
        captured.clear()
        got = llm.chat_reply("s", "u" * 20, fbcfg)
        assert got == "Second try.", got
        assert len(captured) == 2, [c["url"] for c in captured]
        budgets = [json.loads(c["body"]).get("max_completion_tokens")
                   for c in captured]
        assert budgets == [300, 600], budgets
        assert not llm._unavailable(), "an empty reply is not a breaker"
        llm.reset_disable_state()
    finally:
        llm.urllib.request.urlopen = orig
    # Two empties: the fallback carries the line.
    _fake, _ = _empty_chain(["", ""], "FB line.")
    llm.urllib.request.urlopen = _fake
    try:
        llm.reset_disable_state()
        captured.clear()
        got = llm.chat_reply("s", "u" * 20, fbcfg)
        assert got == "FB line.", got
        assert [c["url"] for c in captured] == [
            "https://api.groq.com/openai/v1/chat/completions",
            "https://api.groq.com/openai/v1/chat/completions",
            "https://openrouter.ai/api/v1/chat/completions"], captured
        llm.reset_disable_state()
    finally:
        llm.urllib.request.urlopen = orig
    # A fragment ('The', 'CyclingWith' - both live-fire, cut off before
    # the answer started and then rejected by the cleaner) is as good
    # as empty: same single retry at the doubled budget.
    _fake, _ = _empty_chain(["The", "A full line this time."], "FB.")
    llm.urllib.request.urlopen = _fake
    try:
        llm.reset_disable_state()
        captured.clear()
        got = llm.chat_reply("s", "u" * 20, fbcfg)
        assert got == "A full line this time.", got
        budgets = [json.loads(c["body"]).get("max_completion_tokens")
                   for c in captured]
        assert budgets == [300, 600], budgets
        llm.reset_disable_state()
    finally:
        llm.urllib.request.urlopen = orig
    # A cut-off that made it past the length floor (live-fire: 'If
    # they try to slash wages, I'll' - 33 characters, stopped only by
    # the downstream gates) gets the same retry; a complete line does
    # not pay for one.
    _fake, _ = _empty_chain(["If they try to slash wages, I\u2019ll",
                             "If they slash wages, I park the rig."],
                            "FB.")
    llm.urllib.request.urlopen = _fake
    try:
        llm.reset_disable_state()
        captured.clear()
        got = llm.chat_reply("s", "u" * 20, fbcfg)
        assert got == "If they slash wages, I park the rig.", got
        budgets = [json.loads(c["body"]).get("max_completion_tokens")
                   for c in captured]
        assert budgets == [300, 600], budgets
        llm.reset_disable_state()
    finally:
        llm.urllib.request.urlopen = orig
    _fake, _ = _empty_chain(["A complete line, no dangling tail."], "FB.")
    llm.urllib.request.urlopen = _fake
    try:
        llm.reset_disable_state()
        captured.clear()
        got = llm.chat_reply("s", "u" * 20, fbcfg)
        assert got == "A complete line, no dangling tail.", got
        assert len(captured) == 1, "a complete line must not pay a retry"
        llm.reset_disable_state()
    finally:
        llm.urllib.request.urlopen = orig
    # A failed warm-up SAYS so: the fallback's warm-up used to fail
    # silently (401 bad key, 404 dead slug, 429 - all printed nothing),
    # so a dead fallback was indistinguishable from none configured
    # (live-fire: one warm-up line at startup, no explanation).
    def _or_401(req, timeout=60):
        if "openrouter" in req.full_url:
            raise _ue.HTTPError(req.full_url, 401, "Unauthorized", {},
                                io.BytesIO(b'{"error":{"message":'
                                           b'"bad key"}}'))
        return io.BytesIO(json.dumps(
            {"choices": [{"message": {"content": "OK"}}]}
        ).encode("utf-8"))

    llm.urllib.request.urlopen = _or_401
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            llm.warm_up(fbcfg)
    finally:
        llm.urllib.request.urlopen = orig
    assert "warm-up OK - openai/gpt-oss-120b" in out.getvalue(), \
        out.getvalue()
    assert "warm-up of mistralai/mistral-nemo failed (HTTP 401)" \
        in out.getvalue(), out.getvalue()
    assert "fallback NOT READY" in out.getvalue(), out.getvalue()

    # OpenRouter documents a second error shape: an upstream can fail after
    # HTTP 200 is committed, leaving {"error": ...} and no choices. That was
    # the live KeyError('choices') and hid the actual code/message.
    def _or_200_error(req, timeout=60):
        if "openrouter" in req.full_url:
            return io.BytesIO(json.dumps({
                "error": {"code": 429,
                          "message": "free-model provider is rate limited"}
            }).encode("utf-8"))
        return io.BytesIO(json.dumps(
            {"choices": [{"message": {"content": "OK"}}]}
        ).encode("utf-8"))

    llm.reset_disable_state()
    llm.urllib.request.urlopen = _or_200_error
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            assert llm.warm_up(fbcfg) is True  # primary is still healthy
    finally:
        llm.urllib.request.urlopen = orig
    log = out.getvalue()
    assert "fallback rate-limited (HTTP 429)" in log, log
    assert "free-model provider is rate limited" in log, log
    assert "fallback NOT READY" in log, log
    assert "KeyError" not in log, log
    assert llm._fallback_unavailable(), "the fallback's own breaker must open"
    llm.reset_disable_state()
    print("[PASS] failed warm-ups and HTTP-200 error envelopes say why")

    print("[PASS] an empty chat reply is retried once at a doubled "
          "budget, then the fallback takes it")

    print("ALL PASSED ✔" if ok else "SOME FAILED ✘")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
