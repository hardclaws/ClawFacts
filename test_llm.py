"""Standalone check that your LLM writes spicy facts.

Run this BEFORE debugging the bot — it isolates the AI from everything else.

    python3 test_llm.py                     # key/endpoint from env or config.json
    python3 test_llm.py "Las Vegas, NV"     # try a different town
    GROQ_API_KEY=gsk_xxx python3 test_llm.py
    python3 test_llm.py --key gsk_xxx
    python3 test_llm.py --ollama            # force local Ollama (no key needed)
"""

import json
import os
import sys

import llm


def _resolve() -> tuple:
    """Return (key, base_url, model, source) or (None, None, None, reason)."""
    args = sys.argv[1:]
    if "--ollama" in args:
        return ("", "http://localhost:11434/v1", "llama3.1:8b", "--ollama")
    for i, a in enumerate(args):
        if a == "--key" and i + 1 < len(args):
            key = args[i + 1]
            if key.startswith("gsk_"):
                return key, "https://api.groq.com/openai/v1", "openai/gpt-oss-120b", "--key"
            return key, "https://openrouter.ai/api/v1", "nousresearch/hermes-4-70b", "--key"

    # Local Ollama wins — it's the unfiltered engine for adult facts.
    if os.environ.get("OLLAMA_MODEL", "").strip() or os.environ.get("OLLAMA_BASE_URL", "").strip():
        return ("",
                os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1").strip(),
                os.environ.get("OLLAMA_MODEL", "llama3.1:8b").strip(),
                "OLLAMA env")
    if os.environ.get("GROQ_API_KEY", "").strip():
        return (os.environ["GROQ_API_KEY"].strip(),
                os.environ.get("GROQ_API_BASE", "https://api.groq.com/openai/v1").strip(),
                os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b").strip(),
                "GROQ_API_KEY env")
    if os.environ.get("OPENROUTER_API_KEY", "").strip():
        return (os.environ["OPENROUTER_API_KEY"].strip(),
                os.environ.get("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1").strip(),
                os.environ.get("OPENROUTER_MODEL", "nousresearch/hermes-4-70b").strip(),
                "OPENROUTER_API_KEY env")

    try:
        with open("config.json", "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        cfg = {}
    if llm.is_configured(cfg):
        return ((cfg.get("llm_api_key") or "").strip(),
                (cfg.get("llm_base_url") or "https://api.groq.com/openai/v1").strip(),
                (cfg.get("llm_model") or "openai/gpt-oss-120b").strip(),
                "config.json")
    return None, None, None, "no LLM configured"


def main() -> int:
    key, base, model, source = _resolve()
    print("=" * 60)
    print("  LLM TEST")
    print("=" * 60)
    if not llm.is_configured({"llm_api_key": key, "llm_base_url": base}):
        print("[FAIL] No LLM configured anywhere.")
        print("       * Local Ollama (recommended for adult facts):  install Ollama,")
        print("         run `ollama pull llama3.1:8b`, then:")
        print("           python3 test_llm.py --ollama")
        print("         or set OLLAMA_MODEL=llama3.1:8b in the environment.")
        print("       * Hosted: set GROQ_API_KEY / OPENROUTER_API_KEY, or put")
        print("         llm_api_key in config.json.")
        return 1

    if key:
        masked = key[:6] + "…" + key[-4:] if len(key) > 12 else "***"
        print(f"[info] using key from {source}: {masked}")
    else:
        print(f"[info] local Ollama (no key) from {source}")
    print(f"[info] endpoint: {base}   model: {model}\n")

    town = "Milford, PA"
    try:
        out = llm.rewrite_fact("Milford, Pennsylvania", town,
                               ["Milford was founded in 1796 by Judge John Biddis, one of Pennsylvania's first four circuit judges."],
                               {"llm_api_key": key, "llm_base_url": base, "llm_model": model})
    except Exception as exc:
        print(f"[FAIL] exception: {exc!r}")
        return 1

    if not out:
        print("[FAIL] The LLM returned nothing. Check the error lines above.")
        if not key:
            print("       Is Ollama running?  Try:  ollama list   and  ollama run llama3.1:8b")
        else:
            print("       Common causes: bad key, no credits, or model name not on your provider.")
        return 1

    print(f"[ ok ] Sample spicy facts for {town}:\n")
    for ln in out.splitlines():
        ln = ln.strip()
        if ln:
            print(f"       {ln}")
    print("\nLLM TEST PASSED ✔  — now restart the bot and it will use this LLM.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
