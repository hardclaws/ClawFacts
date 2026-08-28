"""Tests for the ambient trucker chatter: trucker.ramble and bot._cb_chatter.

Run:  python3 mock_trucker_test.py

Nothing reaches the network. The generator is local, so the interesting
failures here are grammar, exhaustiveness and clock behaviour rather than I/O.
"""

import re

import bot as bot_mod
import trucker

# Terms that are genuine CB slang but not something to drop into a live
# channel unprompted. The bot posts on its own, so the bar is higher than
# for a command a viewer chose to run.
BANNED = ("lot lizard", "sleeper creeper", "male buffalo", "pickle park")

SLOT_RE = re.compile(r"\{(\w+)\}")
LOWERCASE_AFTER_STOP = re.compile(r"(?<=[.!?] )[a-z]")

IRC_CAP = 450
T = 1_000_000.0


def _bot(live=True, **cfg):
    base = dict(bot_mod.DEFAULTS, nick="bot", channel="#test", prefix="!",
                cb_chatter_minutes=25)
    base.update(cfg)
    b = bot_mod.TwitchBot(base)
    said = []
    b._say = said.append
    b._access.helix.is_live = lambda bid="": live
    return b, said


def _all_strings():
    """Every literal the generator can reach: templates and pool values."""
    seen = set()
    stack = [t for ts in trucker.REGISTERS.values() for t in ts]
    stack += [v for pool in trucker._POOLS.values() for v in pool]
    while stack:
        item = stack.pop()
        if item in seen:
            continue
        seen.add(item)
        for slot in SLOT_RE.findall(item):
            stack.extend(trucker._POOLS[slot])
    return seen


# --- generator ----------------------------------------------------------

def test_every_slot_has_a_pool():
    """No {slot} anywhere can be left unresolvable.

    This is a real bug that shipped once: {opener} and {ramble} were used in
    templates but missing from _POOLS, so every road and ramble line raised
    KeyError. It is caught here rather than in a live channel.
    """
    for text in _all_strings():
        for slot in SLOT_RE.findall(text):
            assert slot in trucker._POOLS, f"{slot!r} in {text!r} has no pool"
    print(f"[PASS] every slot in all {len(_all_strings())} literals has a pool")


def test_no_line_is_left_partially_filled():
    lines = [trucker.ramble().text for _ in range(20000)]
    broken = [ln for ln in lines if "{" in ln or "}" in ln]
    assert not broken, broken[:3]
    print("[PASS] 20000 lines, none left with an unfilled slot")


# Prepositions that may not sit directly in front of a slot whose values
# already open with one. "out past {where}" produced "out past just past the
# Wally World" because every WHERE entry is itself a prepositional phrase.
_PREP = ("at", "past", "off", "up", "back", "in", "on", "out")
_LEADING = ("at ", "just past ", "off ", "up at ", "back of ",
            "comin' up on ", "past ", "in ")
_SLOT_AFTER_WORD = re.compile(r"(\w[\w']*)\s+\{(\w+)\}")


def test_no_template_puts_a_preposition_before_a_preposition():
    """Checked structurally, on the templates rather than on samples.

    A sample-based test only fails if the bad draw happens; this fails the
    moment any template is written this way, for any value in the pool.
    """
    for name, templates in trucker.REGISTERS.items():
        for template in templates:
            for m in _SLOT_AFTER_WORD.finditer(template):
                prev, slot = m.group(1).lower(), m.group(2)
                if prev not in _PREP:
                    continue
                for value in trucker._POOLS[slot]:
                    assert not value.lower().startswith(_LEADING), (
                        f"{name}: '{prev} {{{slot}}}' in {template!r} reads "
                        f"'{prev} {value}'")
    print("[PASS] no template puts a preposition in front of one")


def test_no_lowercase_after_a_full_stop():
    """Pools are lowercase so they read mid-sentence; the filler must
    capitalise any that land at the start of one."""
    lines = [trucker.ramble().text for _ in range(20000)]
    bad = [ln for ln in lines if LOWERCASE_AFTER_STOP.search(ln)]
    assert not bad, bad[:3]
    print("[PASS] no line starts a sentence with a lowercase letter")


def test_lines_fit_one_irc_message():
    lines = [trucker.ramble().text for _ in range(20000)]
    longest = max(lines, key=len)
    assert len(longest) <= IRC_CAP, (len(longest), longest)
    print(f"[PASS] longest of 20000 is {len(longest)} chars (cap {IRC_CAP})")


def test_the_space_is_actually_large():
    """The user's standing rule: a finite pool is the defect. Assert both the
    arithmetic and, separately, that draws really do vary - a count that is
    big but unreachable would pass the first and fail the second."""
    count = trucker.combination_count()
    assert count > 1_000_000, count
    draws = [trucker.ramble().text for _ in range(20000)]
    distinct = len(set(draws))
    # The arithmetic alone can lie: a total of 10M means nothing if most of it
    # sits in two templates. 20000 draws must therefore land almost entirely
    # on different lines, which only happens if the space is spread out.
    assert distinct > 14000, distinct
    print(f"[PASS] {count:,} distinct lines; {distinct:,} unique in 20000 "
          f"draws")


def test_no_single_template_is_a_fixed_list():
    """A big total can hide one near-static template, and that is the one a
    viewer keeps hearing. Every template must vary on its own.

    One template produced 110 distinct lines out of a 10M space because both
    of its halves were fixed sentences; the pool it drew from had grown but
    the template had not.
    """
    weakest = None
    for name, templates in trucker.REGISTERS.items():
        for template in templates:
            distinct = len({trucker._fill(template) for _ in range(3000)})
            if weakest is None or distinct < weakest[0]:
                weakest = (distinct, name, template)
    assert weakest[0] >= 400, weakest
    print(f"[PASS] weakest of {sum(len(t) for t in trucker.REGISTERS.values())}"
          f" templates still yields {weakest[0]} distinct lines "
          f"({weakest[1]})")


def test_no_banned_term_can_be_produced():
    for text in _all_strings():
        low = text.lower()
        for term in BANNED:
            assert term not in low, (term, text)
    print(f"[PASS] none of {BANNED} appears anywhere in the generator")


def test_pools_are_non_empty():
    for name, pool in trucker._POOLS.items():
        assert pool, f"{name} is empty"
        assert all(v.strip() for v in pool), f"{name} has a blank entry"
    print(f"[PASS] all {len(trucker._POOLS)} pools non-empty, no blank entries")


def test_three_registers_and_invalid_rejected():
    assert set(trucker.registers()) == {"road", "grizzled", "ramble",
                                        "yell"}, trucker.registers()
    for name in trucker.registers():
        assert trucker.ramble(name).text, name
    try:
        trucker.ramble("nope")
        raise AssertionError("invalid register did not raise")
    except ValueError:
        pass
    print("[PASS] three registers; an unknown one raises ValueError")


def test_it_does_not_repeat_itself_back_to_back():
    lines = [trucker.ramble().text for _ in range(2000)]
    repeats = sum(a == b for a, b in zip(lines, lines[1:]))
    assert repeats == 0, repeats
    print("[PASS] 2000 consecutive lines, no immediate repeat")


def test_on_my_donkey_only_takes_things_that_can_be_behind_you():
    """'Donkey' means behind you. Most sightings cannot be back there, so the
    template draws from a narrower BEHIND pool."""
    assert "a lumper arguin' over two boxes" not in trucker.BEHIND
    assert "a bear" in trucker.BEHIND
    bad = [ln for ln in (trucker.ramble().text for _ in range(20000))
           if "arguin" in ln and "on my donkey" in ln]
    assert not bad, bad[:2]
    print("[PASS] 'on my donkey' never takes a noun that cannot be behind you")


# --- the clock ----------------------------------------------------------

def test_jitter_is_bounded_and_never_repeats():
    """Random, not periodic - but bounded, so it can never spam."""
    b, _ = _bot(cb_chatter_minutes=25)
    base = 25 * 60.0
    vals = [b._cb_next_delay() for _ in range(20000)]
    assert all(0.4 * base <= v <= 2.0 * base for v in vals), \
        (min(vals), max(vals))
    assert len(set(vals)) == len(vals), len(set(vals))
    print(f"[PASS] 20000 rolls all distinct, within "
          f"{0.4*base/60:.0f}-{2.0*base/60:.0f} min of a 25 min average")


def test_zero_or_negative_average_disables_it():
    b, _ = _bot(cb_chatter_minutes=0)
    assert b._cb_next_delay() == 0.0
    b, _ = _bot(cb_chatter_minutes=-5)
    assert b._cb_next_delay() == 0.0
    print("[PASS] a zero or negative average yields no delay, not a crash")


def test_disabled_and_paused_post_nothing():
    T = 1_000_000.0
    b, said = _bot(cb_chatter_enabled=False)
    b._cb_next = 0.0
    assert b._cb_chatter_tick(now=T) is None
    assert not said
    b, said = _bot()
    b.paused = True
    b._cb_next = 0.0
    assert b._cb_chatter_tick(now=T) is None
    assert not said
    print("[PASS] disabled or paused, nothing is posted")


def test_it_waits_for_its_own_clock():
    T = 1_000_000.0
    b, said = _bot()
    b._cb_next = T + 500.0
    b._last_chat = T - 3600.0
    assert b._cb_chatter_tick(now=T) is None
    assert not said
    assert b._cb_next == T + 500.0, "not due must not reschedule"
    print("[PASS] before its roll comes due it stays silent and holds it")


def test_offline_pushes_the_schedule_out():
    T = 1_000_000.0
    b, said = _bot(live=False)
    b._cb_next = 0.0
    b._last_chat = T - 3600.0
    assert b._cb_chatter_tick(now=T) is None
    assert not said
    assert b._cb_next > T, "offline must push the schedule out, not leave 0"
    print("[PASS] offline: nothing posted, schedule pushed out")


def test_it_does_not_talk_over_an_active_conversation():
    T = 1_000_000.0
    b, said = _bot()
    b._cb_next = 0.0
    b._last_chat = T - 10.0          # someone spoke 10s ago
    assert b._cb_chatter_tick(now=T) is None
    assert not said
    assert b._cb_next == T + 45.0, b._cb_next
    print("[PASS] chat active in the last 60s: defers 45s instead of posting")


def test_it_posts_when_due_and_reschedules():
    T = 1_000_000.0
    b, said = _bot()
    b._cb_next = 0.0
    b._last_chat = T - 3600.0
    post = b._cb_chatter_tick(now=T)
    assert post.text and said == [f"{post.label} | {post.text}"], (post, said)
    assert T < b._cb_next <= T + 2.0 * 25 * 60.0, b._cb_next
    assert not said[0].startswith("@"), "ambient chatter must not @mention"
    # The whole point of the label: chat can tell the modes apart.
    assert said[0].split(" | ", 1)[0] in ("CB", "WINDOW"), said[0]
    print(f"[PASS] when due it posts labelled and reschedules: {said[0][:46]}...")


def test_command_reply_posts_without_a_mention():
    b, said = _bot()
    b._reply_cb("viewer19")
    assert len(said) == 1 and said[0]
    assert not said[0].startswith("@"), said[0]
    assert 0 < len(said[0]) <= IRC_CAP
    print(f"[PASS] !cb answers without a mention: {said[0][:50]}...")


def test_help_advertises_the_command():
    b, said = _bot()
    b._say_help("viewer19")
    joined = " ".join(said)
    assert "!cb - the bot talks on the radio" in joined, joined[:200]
    print("[PASS] !help advertises !cb")


# --- the three switches ---------------------------------------------------

MOD = "moderator/1"
BC = "broadcaster/1"
VIEWER = ""
SUB = "subscriber/12"


def test_command_can_be_switched_off_while_ambient_survives():
    """The point of a separate switch: random chatter keeps going."""
    b, said = _bot(cb_command_enabled=False)
    b._reply_cb("viewer19", SUB)
    assert said == [], said
    b._cb_next = 0.0
    b._last_chat = T - 3600.0
    assert b._cb_chatter_tick(now=T), "ambient must survive"
    assert len(said) == 1
    print("[PASS] cb_command_enabled=false silences !cb but not the chatter")


def test_access_can_be_limited_to_moderators():
    b, _ = _bot(cb_command_access="moderator")
    assert b._cb_allowed(VIEWER) is False
    assert b._cb_allowed(SUB) is False
    assert b._cb_allowed(MOD) is True
    assert b._cb_allowed(BC) is True
    b, said = _bot(cb_command_access="moderator")
    b._reply_cb("viewer19", SUB)
    assert said == [], "a subscriber must be ignored"
    b._reply_cb("amod", MOD)
    assert len(said) == 1, said
    print("[PASS] cb_command_access=moderator ignores viewers and subs")


def test_access_can_be_limited_to_the_broadcaster():
    b, _ = _bot(cb_command_access="broadcaster")
    assert b._cb_allowed(MOD) is False
    assert b._cb_allowed(BC) is True
    print("[PASS] cb_command_access=broadcaster excludes even moderators")


def test_a_misspelt_access_setting_fails_closed():
    """A typo must not silently open the command to everyone."""
    for bad in ("moderatr", "every one", "", "nobody"):
        b, _ = _bot(cb_command_access=bad)
        assert b._cb_allowed(VIEWER) is False, bad
        assert b._cb_allowed(MOD) is True, bad
    print("[PASS] an unrecognised cb_command_access fails closed to moderator")


def test_everyone_is_the_default():
    b, _ = _bot()
    assert b.cfg["cb_command_access"] == "everyone"
    assert b._cb_allowed(VIEWER) is True
    print("[PASS] by default anyone may ask for one")


def test_a_bare_cb_is_not_mistaken_for_a_switch():
    b, said = _bot()
    assert b._cb_switch("amod", MOD, "") is False
    assert b._cb_switch("amod", MOD, "hardclaws") is False
    assert said == [], said
    print("[PASS] !cb with no verb still rambles instead of switching")


def test_a_viewer_cannot_touch_the_switch():
    b, said = _bot()
    assert b._cb_switch("viewer19", SUB, "off") is True   # recognised
    assert said == [], f"a viewer must get no answer: {said}"
    assert b._cb_ambient_off is False, "and it must not take effect"
    print("[PASS] !cb off from a viewer is ignored, and stays silent")


def test_a_moderator_can_switch_the_random_chatter():
    T2 = T
    b, said = _bot()
    assert b._cb_switch("amod", MOD, "off") is True
    assert any("OFF" in m for m in said), said
    b._cb_next = 0.0
    b._last_chat = T2 - 3600.0
    said.clear()
    assert b._cb_chatter_tick(now=T2) is None
    assert said == [], "off must actually stop it"
    assert b._cb_next > T2, "and must push the clock, not leave it due"

    assert b._cb_switch("amod", MOD, "on") is True
    b._cb_next = 0.0
    assert b._cb_chatter_tick(now=T2), "on must bring it back"
    print("[PASS] !cb off stops the random chatter, !cb on restores it")


def test_status_reports_the_real_state():
    b, said = _bot()
    b._cb_switch("amod", MOD, "status")
    assert "ON" in said[0], said
    b._cb_switch("amod", MOD, "off")
    b._cb_switch("amod", MOD, "status")
    assert "OFF" in said[-1], said
    print("[PASS] !cb status reports the state it is actually in")


def test_on_names_the_config_setting_when_config_has_it_off():
    """Promising 'back on' while the keeper thread was never started is a
    lie, so it names the setting that is really holding it."""
    b, said = _bot(cb_chatter_enabled=False)
    assert b._cb_switch("amod", MOD, "on") is True
    assert "cb_chatter_enabled" in said[0], said
    assert "back ON" not in said[0], said
    said.clear()
    b._cb_switch("amod", MOD, "status")
    assert "config.json" in said[0], said
    print("[PASS] with cb_chatter_enabled=false it names the config key")


def test_help_omits_a_command_that_is_switched_off():
    b, said = _bot(cb_command_enabled=False)
    b._say_help("viewer19")
    assert not any("!cb" in m for m in said), said
    b, said = _bot()
    b._say_help("viewer19")
    assert any("!cb" in m for m in said)
    print("[PASS] !help only advertises !cb when the command is on")


# --- labels and the WINDOW voice -----------------------------------------

def test_every_line_carries_the_label_for_its_voice():
    """The label is derived inside ramble(), from the same draw that produced
    the text - so a WINDOW line can never go out under a CB label."""
    seen = {}
    for _ in range(20000):
        post = trucker.ramble()
        seen.setdefault(post.label, 0)
        seen[post.label] += 1
    assert set(seen) == {"CB", "WINDOW"}, seen
    for name in trucker.registers():
        want = trucker.LABELS[name]
        for _ in range(200):
            assert trucker.ramble(name).label == want, (name, want)
    print(f"[PASS] both labels appear, and each voice keeps its own "
          f"({ {k: v for k, v in sorted(seen.items())} })")


def test_the_window_voice_targets_cars_not_people():
    """Yelling out the window, not at a named person: every template names a
    vehicle, so nothing can read as aimed at a real viewer."""
    for template in trucker.YELL_TEMPLATES:
        assert "{vehicle}" in template, template
    for vehicle in trucker.VEHICLES:
        assert vehicle.startswith("that "), vehicle
    print("[PASS] every yell names a vehicle, and always as 'that <vehicle>'")


def test_the_yelling_is_never_a_threat():
    """Exasperated and powerless is funny; anything implying violence is a
    moderation problem in a live channel."""
    banned = ("ram", "kill", "crash", "wreck", "punch", "shoot", "die",
              "brake check", "run you", "hit you", "road rage", "fight")
    pats = [re.compile(r"\b" + re.escape(w) + r"\b") for w in banned]
    for text in _all_strings():
        low = text.lower()
        for word, pat in zip(banned, pats):
            assert not pat.search(low), (word, text)
    print(f"[PASS] none of {len(banned)} violent terms appears in the generator")


def test_a_shout_is_capped_but_the_line_is_not():
    """Twitch automod treats a wall of capitals as spam. The bellow is caps,
    the rest is not."""
    acronyms = {"SUV", "CB", "DOT", "GPS"}
    for _ in range(20000):
        post = trucker.ramble("yell")
        caps = [w.strip(".,!?") for w in post.text.split()]
        caps = [w for w in caps
                if w.isupper() and len(w) > 1 and w not in acronyms]
        assert post.text != post.text.upper(), post.text
        assert len(caps) <= 2, (caps, post.text)
    print("[PASS] at most the bellow is in capitals, never the whole line")


def test_a_voice_can_be_dropped_without_losing_the_others():
    b, _ = _bot(cb_yell_enabled=False)
    assert b._cb_excluded() == ("yell",)
    labels = {trucker.ramble(exclude=b._cb_excluded()).label
              for _ in range(3000)}
    assert labels == {"CB"}, labels
    b, _ = _bot()
    assert b._cb_excluded() == ()
    labels = {trucker.ramble(exclude=()).label for _ in range(3000)}
    assert labels == {"CB", "WINDOW"}, labels
    print("[PASS] cb_yell_enabled=false drops WINDOW and keeps all three CB "
          "voices")


def test_excluding_everything_is_an_error_not_silence():
    try:
        trucker.ramble(exclude=tuple(trucker.registers()))
        raise AssertionError("excluding everything did not raise")
    except ValueError:
        pass
    print("[PASS] excluding every voice raises rather than posting nothing")


def test_the_yelling_is_grammatical():
    """Two real defects this catches: a past-tense offence after 'to', and a
    'that'-prefixed vehicle dropped in after an ordinal."""
    import re
    ordinal_that = re.compile(
        r"the (second|third|fourth|fifth|sixth) that")
    bad = []
    for _ in range(30000):
        text = trucker.ramble("yell").text
        if ordinal_that.search(text):
            bad.append(text)
        if any(f" to {offence}" in text for offence in trucker.OFFENCES):
            bad.append(text)
    assert not bad, bad[:2]
    print("[PASS] 30000 yells, no 'to <past tense>' and no ordinal before a "
          "vehicle")


def main():
    tests = [
        test_every_slot_has_a_pool,
        test_no_line_is_left_partially_filled,
        test_no_template_puts_a_preposition_before_a_preposition,
        test_no_lowercase_after_a_full_stop,
        test_lines_fit_one_irc_message,
        test_the_space_is_actually_large,
        test_no_single_template_is_a_fixed_list,
        test_no_banned_term_can_be_produced,
        test_pools_are_non_empty,
        test_three_registers_and_invalid_rejected,
        test_it_does_not_repeat_itself_back_to_back,
        test_on_my_donkey_only_takes_things_that_can_be_behind_you,
        test_jitter_is_bounded_and_never_repeats,
        test_zero_or_negative_average_disables_it,
        test_disabled_and_paused_post_nothing,
        test_it_waits_for_its_own_clock,
        test_offline_pushes_the_schedule_out,
        test_it_does_not_talk_over_an_active_conversation,
        test_it_posts_when_due_and_reschedules,
        test_command_reply_posts_without_a_mention,
        test_help_advertises_the_command,
        test_command_can_be_switched_off_while_ambient_survives,
        test_access_can_be_limited_to_moderators,
        test_access_can_be_limited_to_the_broadcaster,
        test_a_misspelt_access_setting_fails_closed,
        test_everyone_is_the_default,
        test_a_bare_cb_is_not_mistaken_for_a_switch,
        test_a_viewer_cannot_touch_the_switch,
        test_a_moderator_can_switch_the_random_chatter,
        test_status_reports_the_real_state,
        test_on_names_the_config_setting_when_config_has_it_off,
        test_help_omits_a_command_that_is_switched_off,
        test_every_line_carries_the_label_for_its_voice,
        test_the_window_voice_targets_cars_not_people,
        test_the_yelling_is_never_a_threat,
        test_a_shout_is_capped_but_the_line_is_not,
        test_a_voice_can_be_dropped_without_losing_the_others,
        test_excluding_everything_is_an_error_not_silence,
        test_the_yelling_is_grammatical,
    ]
    failed = 0
    for test in tests:
        trucker._HISTORY.clear()
        try:
            test()
        except Exception as exc:
            failed += 1
            print(f"[FAIL] {test.__name__}: {exc!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
