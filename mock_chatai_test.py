"""Tests for the chat AI: persona replies, bounded chime-ins, !ask.

Run:  python3 mock_chatai_test.py

Nothing reaches the network: the LLM call is stubbed, and the fact-engine
fallback is monkeypatched at bot.py's own imported name. Everything is
deterministic - the chime-in roll is injected, not drawn.
"""

import os
import tempfile
import time

import bot as bot_mod
import chatai
import llm


class _FixedRoll:
    """Stands in for random.random() so chime-in rolls are testable."""

    def __init__(self, value):
        self.value = value

    def random(self):
        return self.value


def _bot(**over):
    cfg = dict(bot_mod.DEFAULTS,
               nick="TruckingWithDocBot", channel="#t",
               chat_ai_enabled=True,
               beef_state_path=os.path.join(tempfile.mkdtemp(), "bs.json"),
               **over)
    b = bot_mod.TwitchBot(cfg)
    b.said = []
    b._say = b.said.append
    b._log = lambda *a, **k: None
    b._access.helix = None
    return b


def _drain(b):
    """Route chime jobs the way the worker thread would."""
    while not b._jobs.empty():
        nick, login, badges, command, argument = b._jobs.get()
        if command == "chime":
            b._do_chime(nick, argument)


def test_a_mention_gets_one_bounded_reply():
    b = _bot(llm_api_key="k")
    orig = llm.chat_reply
    llm.chat_reply = lambda s, u, c: "Graphics are free with the job."
    try:
        b._on_message("kvack", "#t", "hey doc these graphics kind of suck",
                      "kvack", "")
        _drain(b)
        assert b.said == ["@kvack Graphics are free with the job."], b.said
        # A second mention inside the cooldown stays quiet - the bot
        # cannot be wound up like a toy.
        b._on_message("kvack", "#t", "doc you there?", "kvack", "")
        _drain(b)
        assert len(b.said) == 1, b.said
        # The reply is prefixed by the bot, never by the model: a model
        # line carrying an @mention of its own is dropped whole.
        llm.chat_reply = lambda s, u, c: "@kvack you would not believe it"
        b._chat_ai_last = 0.0
        b._on_message("kvack", "#t", "doc honestly", "kvack", "")
        _drain(b)
        assert len(b.said) == 1, b.said
    finally:
        llm.chat_reply = orig
    print("[PASS] a mention gets one bounded reply, then quiet")


def test_chime_ins_are_gated():
    b = _bot(llm_api_key="k")
    orig_roll, orig_reply = bot_mod.random, llm.chat_reply
    llm.chat_reply = lambda s, u, c: "Ten-four on that."
    try:
        bot_mod.random = _FixedRoll(0.9)      # the fillers roll high too
        for i in range(6):                    # a room worth joining
            b._on_message(f"viewer{i}", "#t",
                          "the weather out there is brutal today",
                          f"viewer{i}", "")
        # Roll too high: the bot keeps its own counsel.
        b._on_message("viewer9", "#t", "anyone else running I-80 tonight",
                      "viewer9", "")
        assert b._jobs.empty(), "chimed in on a losing roll"
        # Roll wins: it speaks, once.
        bot_mod.random = _FixedRoll(0.05)
        b._on_message("viewer9", "#t", "anyone else running I-80 tonight",
                      "viewer9", "")
        _drain(b)
        assert b.said == ["@viewer9 Ten-four on that."], b.said
        # The long cooldown holds even on a winning roll.
        b._chat_ai_last = time.time() - 30
        bot_mod.random = _FixedRoll(0.05)
        b._on_message("viewer8", "#t", "roads are rough near Joplin",
                      "viewer8", "")
        assert b._jobs.empty(), "chimed in inside the long cooldown"
        # The hourly cap is absolute - mentions included.
        b._chat_ai_last = 0.0
        b._chat_ai_times = [time.time() - 10] * 6
        b._on_message("kvack", "#t", "doc tell them", "kvack", "")
        assert b._jobs.empty(), "spoke past the hourly cap"
        # Paused (!bot off) and !cb off both silence it.
        b._chat_ai_times = []
        b.paused = True
        b._on_message("kvack", "#t", "doc tell them", "kvack", "")
        assert b._jobs.empty(), "spoke while paused"
        b.paused = False
        b._cb_ambient_off = True
        b._on_message("kvack", "#t", "doc tell them", "kvack", "")
        assert b._jobs.empty(), "spoke after !cb off"
        b._cb_ambient_off = False
        # A quiet room is not worth joining: too little chatter, no chime.
        b2 = _bot(llm_api_key="k")
        bot_mod.random = _FixedRoll(0.05)
        b2._on_message("loner", "#t", "hello anybody", "loner", "")
        assert b2._jobs.empty(), "chimed into an empty room"
    finally:
        bot_mod.random = orig_roll
        llm.chat_reply = orig_reply
    print("[PASS] chime-ins are gated by roll, room, cooldown and cap")


def test_the_streamer_commands_and_own_lines_never_trigger():
    b = _bot(llm_api_key="k")
    orig_roll, orig_reply = bot_mod.random, llm.chat_reply
    bot_mod.random = _FixedRoll(0.0)
    llm.chat_reply = lambda s, u, c: "Should never be needed."
    try:
        # The streamer already has the floor.
        b._on_message("Hardclaws", "#t", "doc what do you think of this",
                      "hardclaws", "broadcaster/1")
        # Commands are not chatter.
        b._on_message("kvack", "#t", "!doc hello", "kvack", "")
        # The bot never reacts to its own lines.
        b._on_message("TruckingWithDocBot", "#t",
                      "I already said this once", "truckingwithdocbot", "")
        # Nor to explicit text.
        b._on_message("kvack", "#t", "doc link me some porn", "kvack", "")
        assert b._jobs.empty(), b._jobs.qsize()
    finally:
        bot_mod.random = orig_roll
        llm.chat_reply = orig_reply
    print("[PASS] streamer lines, commands, own lines and explicit text "
          "never trigger")


def test_unsafe_or_lazy_lines_never_post():
    b = _bot(llm_api_key="k")
    assert chatai.clean_line("@kvack you would not believe it") is None
    assert chatai.clean_line("check config.json for details") is None
    assert chatai.clean_line("x" * 300) is None
    assert chatai.clean_line("ok then") is None             # under 12 chars
    assert chatai.clean_line(
        "one \U0001f600 two \U0001f601 three \U0001f602 four") is None
    assert chatai.clean_line("Graphics are free with the job.") is not None
    assert chatai.declined("NOTHING TO SAY")
    assert not chatai.declined("Ten-four on that.")
    # A declined or uncleanable reply backs off instead of hammering: the
    # attempt still counts, so the next mention waits out the cooldown.
    orig = llm.chat_reply
    llm.chat_reply = lambda s, u, c: "NOTHING TO SAY"
    try:
        b._on_message("kvack", "#t", "doc say something", "kvack", "")
        _drain(b)
        assert b.said == [], b.said
        assert b._chat_ai_last > 0, "a decline did not back off"
        b._on_message("kvack", "#t", "doc try again", "kvack", "")
        assert b._jobs.empty(), "re-asked the model inside the cooldown"
    finally:
        llm.chat_reply = orig
    print("[PASS] unsafe or lazy lines never post, and declines back off")


def test_ask_answers_with_persona_then_facts():
    b = _bot(llm_api_key="k")
    orig_reply, orig_fact = llm.chat_reply, bot_mod.get_funfact
    try:
        llm.chat_reply = lambda s, u, c: (
            "Once, hauling lobsters out of Portland. Terrible radio.")
        b._reply_ask("hollieburgin", "doc have you ever been to maine")
        assert b.said and b.said[0].startswith("@hollieburgin Once, "
                                               "hauling"), b.said
        # The persona declines: the fact engine answers instead - patched
        # at bot.py's own imported name, where the fallback really calls.
        llm.chat_reply = lambda s, u, c: "NOTHING TO SAY"
        bot_mod.get_funfact = lambda q, o: {
            "place": "Road train",
            "fact": "A driver pulled 113 trailers for 1,235 metres."}
        b._reply_ask("Hardclaws", "whats the longest truck in the world")
        assert len(b.said) == 2 and "113 trailers" in b.said[1], b.said
        # And with no LLM at all, !ask is still a question command.
        b2 = _bot()
        assert not llm.is_configured(b2._opts)
        b2._reply_ask("kvack", "whats the longest truck in the world")
        assert b2.said and "113 trailers" in b2.said[0], b2.said
        # The system prompt the model sees carries the hard rules.
        sysprompt = chatai.system_prompt("")
        assert "NOTHING TO SAY" in sysprompt
        assert "never people" in sysprompt or "never state a fact" \
            in sysprompt
    finally:
        llm.chat_reply = orig_reply
        bot_mod.get_funfact = orig_fact
    print("[PASS] !ask answers with the persona, then with facts")


def main():
    test_a_mention_gets_one_bounded_reply()
    test_chime_ins_are_gated()
    test_the_streamer_commands_and_own_lines_never_trigger()
    test_unsafe_or_lazy_lines_never_post()
    test_ask_answers_with_persona_then_facts()
    print("\nALL PASSED \u2714")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
