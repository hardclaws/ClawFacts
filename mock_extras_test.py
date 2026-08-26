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

    # Failure path: network error -> None (no crash)
    def boom(url, timeout=8):
        raise OSError("offline")
    extras._get_json = boom
    assert extras.get_joke() is None
    assert extras.get_random_fact() is None
    assert extras.get_riddle() is None
    assert extras.get_wyr() is None
    print("[PASS] all return None on network error")
    extras._get_json = orig

    print("ALL PASSED ✔" if ok else "SOME FAILED ✘")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
