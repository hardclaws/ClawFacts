"""Offline tests for extras.py (parse the canned API responses correctly).

Run:  python3 mock_extras_test.py
"""

import extras

JOKE = {"type": "general", "setup": "Why did the truck stop?",
        "punchline": "Because it needed a break.", "id": 1}
FACT = {"id": "x", "text": "A fun fact about something.", "source": "s", "source_url": "u", "language": "en"}
RIDDLE = {"riddle": "What has four wheels and flies?", "answer": "A garbage truck"}
WYR = {"question": "Would you rather drive at night or in the rain?"}


def main():
    ok = True
    orig = extras._get_json
    try:
        extras._get_json = lambda url, timeout=8: JOKE
        j = extras.get_joke()
        assert j == "Why did the truck stop? Because it needed a break.", j
        print(f"[PASS] joke -> {j}")

        extras._get_json = lambda url, timeout=8: FACT
        f = extras.get_random_fact()
        assert f == "A fun fact about something.", f
        print(f"[PASS] randomfact -> {f}")

        extras._get_json = lambda url, timeout=8: RIDDLE
        r = extras.get_riddle()
        assert r == ("What has four wheels and flies?", "A garbage truck"), r
        print(f"[PASS] riddle -> {r}")

        extras._get_json = lambda url, timeout=8: WYR
        w = extras.get_wyr()
        assert w == "Would you rather drive at night or in the rain?", w
        print(f"[PASS] wyr -> {w}")
    except Exception as exc:
        ok = False
        print(f"[FAIL] {exc!r}")
    finally:
        extras._get_json = orig

    # Failure path: a dead API falls back to the local pool instead of
    # returning None. Returning None here used to mean chat got "couldn't
    # fetch that right now" - every single time, for as long as the endpoint
    # stayed down. A dead API should cost variety, not the game.
    def boom(url, timeout=8):
        raise OSError("offline")
    extras._get_json = boom
    extras._fallback_used.clear()
    extras._fallback_logged.clear()

    j = extras.get_joke()
    assert j in extras.JOKES, j
    f = extras.get_random_fact()
    assert f in extras.FACTS, f
    r = extras.get_riddle()
    assert r in extras.RIDDLES, r
    w = extras.get_wyr()
    assert w in extras.WOULD_YOU_RATHER, w
    print("[PASS] a dead API falls back to the local pool, never None")

    # And the pool is big enough to cycle: no repeats until it is exhausted.
    # Reset first - the calls above already consumed one slot, and starting
    # mid-round makes a full-length draw wrap and collide.
    extras._fallback_used.clear()
    jokes = {extras.get_joke() for _ in range(len(extras.JOKES))}
    assert len(jokes) == len(extras.JOKES), (len(jokes), len(extras.JOKES))
    # One more draw must reuse something, which proves the reset really is what
    # made the previous 30 distinct rather than luck.
    extras._fallback_used.clear()
    after = [extras.get_joke() for _ in range(len(extras.JOKES))]
    assert len(set(after)) == len(extras.JOKES), len(set(after))
    print(f"[PASS] {len(jokes)} distinct fallback jokes before anything "
          f"repeats")

    # The pools must never be empty, or the fallback is a lie.
    for name, pool in (("JOKES", extras.JOKES), ("FACTS", extras.FACTS),
                       ("RIDDLES", extras.RIDDLES),
                       ("WOULD_YOU_RATHER", extras.WOULD_YOU_RATHER)):
        assert len(pool) >= 20, (name, len(pool))
        assert all(pool), f"{name} has an empty entry"
    print("[PASS] every fallback pool has at least 20 non-empty entries")
    extras._get_json = orig

    print("ALL PASSED ✔" if ok else "SOME FAILED ✘")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
