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
    captured.append({"url": req.full_url, "headers": req.headers, "body": req.data.decode("utf-8")})
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

    print("ALL PASSED ✔" if ok else "SOME FAILED ✘")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
