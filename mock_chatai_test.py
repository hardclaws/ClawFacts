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
    cfg = {**bot_mod.DEFAULTS,
           "nick": "TruckingWithDocBot", "channel": "#t",
           "chat_ai_enabled": True,
           "beef_state_path": os.path.join(tempfile.mkdtemp(), "bs.json"),
           "memory_db_path": os.path.join(tempfile.mkdtemp(), "mem.db"),
           **over}
    b = bot_mod.TwitchBot(cfg)
    b.said = []
    b._say = b.said.append
    b._log = lambda *a, **k: None
    b._access.helix = None
    return b


def _drain(b):
    """Route chime and ask jobs the way the worker thread would."""
    while not b._jobs.empty():
        nick, login, badges, command, argument = b._jobs.get()
        if command == "chime":
            b._do_chime(nick, argument)
        elif command == "ask":
            b._reply_ask(nick, argument)
        elif command == "say":
            b._say(argument)


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
        b._chat_ai_mention_last = 0.0
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
        b._chat_ai_times = [time.time() - 10] * bot_mod.DEFAULTS[
            "chat_ai_max_hour"]
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


def test_the_streamer_can_address_the_bot_but_it_never_butts_in():
    b = _bot(llm_api_key="k")
    orig_roll, orig_reply = bot_mod.random, llm.chat_reply
    bot_mod.random = _FixedRoll(0.0)
    llm.chat_reply = lambda s, u, c: "Only when asked directly."
    try:
        # A direct @-mention from the streamer is him addressing the bot:
        # it replies. It used to ignore him entirely, which read as
        # broken the moment he tested his own bot from the streamer seat.
        b._on_message("truckingwithdoc", "#t",
                      "doc what do you think of this",
                      "truckingwithdoc", "broadcaster/1")
        assert b._jobs.qsize() == 1, b._jobs.qsize()
        _drain(b)
        assert b.said == ["@truckingwithdoc Only when asked directly."], b.said
        # His ordinary lines never trigger a chime-in - the floor is his.
        b._on_message("truckingwithdoc", "#t",
                      "this load feels heavy today",
                      "truckingwithdoc", "broadcaster/1")
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
    print("[PASS] the streamer can address the bot; it never butts in")


def test_a_timed_out_model_is_not_asked_twice():
    """A !ask that timed out on the chat call used to stack a second,
    BIGGER model call (the question path carries the sources) on the
    same busy model - a guaranteed extra timeout, and the two-minute
    !ask. Factual questions now hit the engine FIRST (the persona
    guesses on trivia), so the protection lives in two places: the
    engine-first call skips a known-busy model, and the persona fallback
    for non-factual questions does too."""
    b = _bot(llm_api_key="", llm_base_url="http://127.0.0.1:11434/v1")
    orig_call, orig_fact = llm._call, bot_mod.get_funfact
    got = []

    def _fact(q, o):
        got.append((q, o))
        return {"place": "Road train",
                "fact": "A driver pulled 113 trailers for 1,235 metres."}

    try:
        bot_mod.get_funfact = _fact
        # A factual ask on a quiet model: the engine answers first, the
        # model is never called, plain opts.
        b._reply_ask("kvack", "whats the longest truck in the world")
        assert b.said and "113 trailers" in b.said[0], b.said
        assert got and got[0][1].get("_skip_llm") is None, got
        # A non-factual ask whose chat call times out: the flag is set,
        # and the fallback engine call carries _skip_llm - no second
        # model call on the busy model.
        got.clear()
        llm._call = lambda *a, **k: (_ for _ in ()).throw(
            TimeoutError("timed out"))
        b._reply_ask("hollieburgin", "tell me about the iowa 80 truck stop")
        assert len(b.said) == 2 and "113 trailers" in b.said[1], b.said
        assert got and got[0][1].get("_skip_llm") is True, got
        # And while the model is flagged busy, even a factual ask skips
        # the engine's model call.
        got.clear()
        b._reply_ask("kvack", "how many trailers did the record pull")
        assert len(b.said) == 3, b.said
        assert got and got[0][1].get("_skip_llm") is True, got
        # A healthy chat call clears the flag; the engine runs plain.
        llm._call = (lambda base, model, key, user, system=None,
                     timeout=60.0, max_tokens=None, hard_nothink=False:
                     "Fastest? Mine.")
        assert b._chat_ai_line([], "hollieburgin", "hello") == "Fastest? Mine."
        got.clear()
        b._reply_ask("kvack", "whats the longest truck in the world")
        assert len(b.said) == 4, b.said
        assert got and got[0][1].get("_skip_llm") is None, got
    finally:
        llm._call = orig_call
        llm._set_chat_timeout(False)
        bot_mod.get_funfact = orig_fact
    print("[PASS] a timed-out model is not asked twice - by the engine "
          "path or the persona fallback")


def test_a_tease_gets_a_comeback_when_the_model_is_down():
    """Field report: '@TruckingWithDocBot you have alot of useless facts'
    got no reply while the model was busy. A playful dig at the bot
    deserves a canned comeback, never silence - but only digs aimed AT
    the bot: questions ('whats the most useless fact') and third-party
    venting ('my stupid internet') stay None."""
    for text in ("you have alot of useless facts", "doc you suck",
                 "your facts are boring", "doc shut up"):
        assert chatai.smalltalk(text) in chatai._COMEBACKS, text
    for text in ("whats the most useless fact you know",   # superlative
                 "whats a useless animal",                 # a question
                 "doc my stupid internet keeps dropping",  # not at the bot
                 "doc you stupid link me porn"):           # explicit
        assert chatai.smalltalk(text) is None, text

    # End to end: the streamer's exact line, the model timing out.
    b = _bot(llm_api_key="k")
    orig_call = llm._call
    try:
        llm._call = lambda *a, **k: (_ for _ in ()).throw(
            TimeoutError("timed out"))
        b._on_message("Hardclaws", "#t",
                      "@TruckingWithDocBot you have alot of useless facts",
                      "hardclaws", "broadcaster/1")
        _drain(b)
        assert len(b.said) == 1, b.said
        assert b.said[0].startswith("@Hardclaws "), b.said
        assert b.said[0].split(" ", 1)[1] in chatai._COMEBACKS, b.said
    finally:
        llm._call = orig_call
        llm._set_chat_timeout(False)
    print("[PASS] a tease gets a Doc comeback when the model is down")


def test_emoji_walls_never_chime():
    """Live-fire: a viewer's emoji wall (no words at all) won the chime
    roll and the model produced a monologue about desert runs. A chime
    needs actual words; a mention always answers, however phrased."""
    assert not chatai.chime_worthy("\U0001f3dc\ufe0f\U0001f3dc\ufe0f\U0001f3dc\ufe0f")
    assert not chatai.chime_worthy("???")
    assert not chatai.chime_worthy("123")
    assert not chatai.chime_worthy("LUL")
    assert chatai.chime_worthy("crushed a few tootsie rolls today")

    b = _bot(llm_api_key="k")
    orig_roll, orig_reply = bot_mod.random, llm.chat_reply
    llm.chat_reply = lambda s, u, c: "never needed"
    try:
        # Fill the room with the roll FAILING, so the fillers themselves
        # never chime...
        bot_mod.random = _FixedRoll(1.0)
        for i in range(6):
            b._on_message("v%d" % i, "#t", "filler chatter line %d" % i,
                          "v%d" % i, "")
        assert b._jobs.empty(), b._jobs.qsize()
        # ...then let every roll win: the emoji wall still cannot chime
        bot_mod.random = _FixedRoll(0.0)
        b._on_message("kvack", "#t", "\U0001f3dc\ufe0f\U0001f3dc\ufe0f\U0001f3dc\ufe0f",
                      "kvack", "")
        assert b._jobs.empty(), b._jobs.qsize()
        # ...but a real line still chimes
        b._on_message("kvack", "#t", "man the wind out here is brutal",
                      "kvack", "")
        assert b._jobs.qsize() == 1, b._jobs.qsize()
        # A mention on an emoji-ish line still answers
        b._chat_ai_last = 0.0
        b._on_message("kvack", "#t", "doc \U0001f3dc\ufe0f",
                      "kvack", "")
        assert b._jobs.qsize() == 2, b._jobs.qsize()
    finally:
        bot_mod.random = orig_roll
        llm.chat_reply = orig_reply
    print("[PASS] emoji walls never chime; real lines and mentions do")


def test_the_bot_cannot_repeat_itself():
    """Live-fire: the model found 'midnight coffee and donuts' and used
    some form of it in eight straight lines ('does this bot just repeat
    midnight over and over' - kvack). The prompt now names the bot's own
    recent lines, a signature word reused across them declines the
    reply, and !ask gets one redemption re-ask. The persona also never
    redirects to commands - our own old rule told it to ('check
    !funfact for the lowdown'), and 'docbot won't give us straight
    answers' was the model obeying."""
    own = ["Midnight coffee, fresh donuts, and the road that never ends",
           "Midnight brew, fresh donuts, and the hum of a diesel"]
    assert chatai.too_similar("Midnight snacks and that endless horizon", own)
    assert not chatai.too_similar("Weighed the rig at the stateline scale", own)
    assert chatai.clean_line("check !funfact for the lowdown") is None
    assert chatai.clean_line("check the funfact command for more pal") is None
    assert chatai.clean_line("one emoji is fine \U0001f69b") is not None
    assert chatai.clean_line("two emoji not \U0001f69b\U0001f3dc") is None
    assert "Your own last lines" in chatai.user_prompt(
        [("a", "hi")], "a", "hello", own=own[:1])

    b = _bot(llm_api_key="k")
    logs = []
    b._log = logs.append
    b._chat_ai_own = list(own)
    calls = []

    def _model(system, user, cfg):
        calls.append(system)
        if len(calls) == 1:
            return "Midnight donuts and coffee on the endless highway"
        return "The scale house closed early. Nobody weighed anything."

    orig = llm.chat_reply
    llm.chat_reply = _model
    try:
        # A mention whose reply echoes the loop: declined, nothing posts.
        b._on_message("kvack", "#t", "doc what keeps you awake at night",
                      "kvack", "")
        _drain(b)
        assert b.said == [], b.said
        assert any("too similar" in l for l in logs), logs
        # !ask gets the redemption: the echo is re-asked, the different
        # line posts.
        calls.clear()
        b._reply_ask("Hardclaws", "what is your favorite midnight snack")
        assert len(calls) == 2, calls
        assert "COMPLETELY different" in calls[1]
        assert b.said and "scale house" in b.said[0], b.said
        assert b._chat_ai_own[-1] == ("The scale house closed early. "
                                      "Nobody weighed anything.")
    finally:
        llm.chat_reply = orig
    print("[PASS] the bot cannot repeat itself; !ask gets one redemption")


def test_factual_questions_get_the_engine_first():
    """Field report: !ask 'what is a bongo twist?' was answered with a
    persona GUESS ('sounds like a spin on a roadside snack') while the
    real answer sat in the fact engine - the streamer's own !funfact
    returned 'cut by Vince Castro in 1960'. Factual questions now hit
    the engine first; the persona keeps opinions, chat and misses."""
    assert chatai.factual_question("what is a bongo twist")
    assert chatai.factual_question("how many trailers can a truck pull")
    assert chatai.factual_question("doc, what is a bongo twist",
                                   ("doc", "docbot"))
    assert chatai.factual_question("doc what is a bongo twist",
                                   ("doc", "docbot"))
    assert not chatai.factual_question("whats your favorite truck")
    assert not chatai.factual_question("are you a dolphins fan")
    assert not chatai.factual_question("how are you today")
    assert not chatai.factual_question("doc whats your favorite truck",
                                       ("doc", "docbot"))

    b = _bot(llm_api_key="k")
    b._distill = lambda nick, lines: None   # routing test, not a memory test
    engine, persona = [], []
    orig_fact, orig_reply = bot_mod.get_funfact, llm.chat_reply
    bot_mod.get_funfact = lambda q, o: (engine.append(q) or {
        "place": "Bongo Twist",
        "fact": "Bongo Twist was cut by Vince Castro in 1960."})
    llm.chat_reply = lambda s, u, c: (persona.append(u) or "a guess")
    try:
        # !ask: the engine answers, the persona is never called
        b._reply_ask("Hardclaws", "what is a bongo twist?")
        assert engine == ["what is a bongo twist?"], engine
        assert not persona, persona
        assert "Vince Castro" in b.said[0] and "FunFact |" in b.said[0], \
            b.said
        # A factual mention: same rule
        b._on_message("Hardclaws", "#t", "doc what is a bongo twist",
                      "hardclaws", "broadcaster/1")
        _drain(b)
        assert len(b.said) == 2 and "Vince Castro" in b.said[1], b.said
        assert not persona, persona
        # The engine has nothing: the persona still gets its chance
        bot_mod.get_funfact = lambda q, o: None
        b._reply_ask("kvack", "what is a flux capacitor")
        assert persona, "the persona was never asked"
        # About-the-bot questions never touch the engine
        engine.clear()
        b._reply_ask("kvack", "whats your favorite truck")
        assert not engine, engine
    finally:
        bot_mod.get_funfact = orig_fact
        llm.chat_reply = orig_reply
    print("[PASS] factual questions get the grounded answer first; the "
          "persona keeps opinions")


def test_no_failed_chat_attempt_is_silent():
    """'are you a Miami Dolphins fan as well?' got no reply AND no log
    line. Three paths were silent - a mention held by its cooldown, the
    model returning nothing, the cleaner rejecting a line - and the
    question matched no canned tier. All four now speak or answer."""
    assert chatai.smalltalk(
        "are you a Miami Dolphins fan as well?") in chatai._OPINION_LINES
    assert chatai.smalltalk("doc do you like tacos") in chatai._OPINION_LINES
    assert chatai.smalltalk(
        "do you know how long the amazon river is") is None

    b = _bot(llm_api_key="k")
    logs = []
    b._log = logs.append
    orig = llm.chat_reply
    try:
        # Model returns nothing: the log says so, and the opinion quip
        # answers the Dolphins question.
        llm.chat_reply = lambda s, u, c: None
        b._on_message("Hardclaws", "#t",
                      "@TruckingWithDocBot are you a Miami Dolphins fan "
                      "as well?", "hardclaws", "broadcaster/1")
        _drain(b)
        assert len(b.said) == 1 and b.said[0].startswith("@Hardclaws "), \
            b.said
        assert b.said[0].split(" ", 1)[1] in chatai._OPINION_LINES, b.said
        assert any("returned nothing" in l for l in logs), logs
        # The cleaner rejects a 400-char ramble: the log says so.
        logs.clear()
        llm.chat_reply = lambda s, u, c: "x" * 400
        b._chat_ai_mention_last = 0.0
        b._on_message("kvack", "#t", "doc hello there friend", "kvack", "")
        _drain(b)
        assert len(b.said) == 1, b.said
        assert any("rejected by the cleaner" in l for l in logs), logs
        # A mention inside the 60s cooldown: held, and the log says so.
        logs.clear()
        llm.chat_reply = lambda s, u, c: "Fine."
        b._on_message("kvack", "#t", "doc one more thing", "kvack", "")
        _drain(b)
        assert any("held" in l for l in logs), logs
    finally:
        llm.chat_reply = orig
    print("[PASS] no failed chat attempt is silent: held, empty and "
          "rejected all log; opinions get a quip")


def test_local_models_get_a_smaller_room_to_read():
    """On CPU the model reads every prompt token before writing a word -
    that read, not the generation, blew a 20s timeout on a warm model
    (the user's chat_ai_timeout WAS set). Local models get 8 room lines
    and 4 memories instead of 15 and 8; hosted keeps the lot."""
    room = [("n%d" % i, "filler line %d" % i) for i in range(20)]
    p15 = chatai.user_prompt(room, "x", "hi")
    assert "filler line 19" in p15 and "filler line 4" not in p15
    p8 = chatai.user_prompt(room, "x", "hi", max_lines=8)
    assert "filler line 19" in p8 and "filler line 11" not in p8
    mem = [("n%d" % i, "fact %d" % i) for i in range(6)]
    pm = chatai.user_prompt([], "x", "hi", memories=mem, max_memories=4)
    assert "fact 3" in pm and "fact 4" not in pm

    # And the bot wires the trim to local base_urls.
    b = _bot(llm_api_key="", llm_base_url="http://127.0.0.1:11434/v1")
    seen = []
    orig = llm.chat_reply
    llm.chat_reply = lambda s, u, c: (seen.append(u) or "a line")
    try:
        for i in range(12):
            b._on_message("v%d" % i, "#t", "filler line %d" % i,
                          "v%d" % i, "")
        while not b._jobs.empty():
            b._jobs.get()
        b._chat_ai_line(b._chat_ai_snapshot(), "kvack", "hello there")
        assert seen, "the model was never asked"
        assert "filler line 11" in seen[0], seen[0][-200:]
        assert "filler line 3" not in seen[0], seen[0][-200:]
    finally:
        llm.chat_reply = orig
    print("[PASS] local models get 8 room lines and 4 memories")


def test_smalltalk_keeps_the_bot_alive_when_the_model_is_down():
    """A dead model slug used to mean total silence on direct address and,
    for !ask, a Wikipedia fact about the word 'today'. Chatty messages now
    get a canned Doc line; factual questions still take the real paths."""
    assert chatai.smalltalk("how are you today?") in chatai._SMALLTALK_LINES
    assert chatai.smalltalk("hows it going doc") in chatai._SMALLTALK_LINES
    assert chatai.smalltalk("who are you") in chatai._SMALLTALK_LINES
    assert chatai.smalltalk("whats the longest truck in the world") is None
    assert chatai.smalltalk("doc link me some porn") is None

    b = _bot(llm_api_key="k")
    orig_reply, orig_fact = llm.chat_reply, bot_mod.get_funfact
    asked = []

    def _fact(q, o):
        asked.append(q)
        return {"place": "Road train",
                "fact": "A driver pulled 113 trailers for 1,235 metres."}

    llm.chat_reply = lambda s, u, c: None          # the model is down
    bot_mod.get_funfact = _fact
    try:
        # A chatty mention: a canned Doc line, not silence.
        b._on_message("kvack", "#t", "doc hows it going", "kvack", "")
        _drain(b)
        assert len(b.said) == 1 and b.said[0].startswith("@kvack "), b.said
        assert b.said[0].split(" ", 1)[1] in chatai._SMALLTALK_LINES, b.said
        # A chatty !ask: the quip, not a Wikipedia fact about "today".
        b._reply_ask("hollieburgin", "how are you today?")
        assert len(b.said) == 2, b.said
        assert b.said[1].split(" ", 1)[1] in chatai._SMALLTALK_LINES, b.said
        assert not asked, asked
        # A factual !ask still reaches the fact engine.
        b._reply_ask("kvack", "whats the longest truck in the world")
        assert asked == ["whats the longest truck in the world"], asked
        assert "113 trailers" in b.said[-1], b.said
    finally:
        llm.chat_reply = orig_reply
        bot_mod.get_funfact = orig_fact
    print("[PASS] chatty lines get a Doc quip when the model is down")


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
        # A factual message: the decline posts nothing (a chatty one
        # would still get the canned quip - that is the smalltalk test).
        b._on_message("kvack", "#t",
                      "doc whats the freight rate to denver", "kvack", "")
        _drain(b)
        assert b.said == [], b.said
        assert b._chat_ai_mention_last > 0, "a decline did not back off"
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


def test_memory_roundtrip_and_forget():
    import memory
    db = os.path.join(tempfile.mkdtemp(), "m.db")
    m = memory.Memory(db)
    assert m.ok
    m.note("kvack", "kvack", "i sleep on the floor sometimes")
    m.remember("kvack", ["sleeps on the floor by choice",
                         "sleeps on the floor by choice"])   # duplicate
    got = m.recall(["kvack"])
    assert got == [("kvack", "sleeps on the floor by choice")], got
    # A different viewer's facts stay put.
    m.remember("1athlete", ["training for a 5k"])
    assert ("1athlete", "training for a 5k") in m.recall(["1athlete"])
    # The cap: the oldest facts fall off first.
    m.remember("chatty", [f"fact {i} {chr(97 + i % 26)}{chr(98 + i % 25)}"
                          for i in range(30)])
    assert len(m.recall(["chatty"], limit=99)) == memory.MAX_PER_NICK
    # !forget erases the facts AND the logged lines.
    dropped = m.purge("kvack")
    assert dropped == 1, dropped
    assert m.recall(["kvack"]) == []
    rows = m._db.execute(
        "SELECT COUNT(*) FROM messages WHERE nick = 'kvack'").fetchall()
    assert rows[0][0] == 0, rows
    # The extraction reply parses: bullets in, junk out.
    assert memory.parse_facts(
        "- drives a Kenworth\n- hates okra\n* @someone said hi"
    ) == ["drives a Kenworth", "hates okra"]
    assert memory.parse_facts("NOTHING WORTH KEEPING") == []
    m.close()
    print("[PASS] memory roundtrips, caps and forgets")


def test_the_bot_remembers_and_forgets():
    b = _bot(llm_api_key="k")
    seen_prompts = []

    def fake_reply(system, user, cfg):
        seen_prompts.append((system, user))
        if "extract durable facts" in system:
            return ("- sleeps on the floor by choice\n"
                    "- lives near Winton")
        return "Floor sleeping, the luxury option. I respect it."

    orig = llm.chat_reply
    llm.chat_reply = fake_reply
    try:
        # kvack talks to the bot; the reply posts, then the distill runs.
        b._on_message("kvack", "#t",
                      "doc i sleep on the ground with no pillow, best sleep"
                      " of my life", "kvack", "")
        _drain(b)
        assert any("Floor sleeping" in s for s in b.said), b.said
        got = b._memory.recall(["kvack"])
        assert ("kvack", "sleeps on the floor by choice") in got, got
        # The next time the bot speaks, it remembers.
        b._chat_ai_mention_last = 0.0
        b._on_message("kvack", "#t", "doc you know what im saying",
                      "kvack", "")
        _drain(b)
        assert any("sleeps on the floor by choice" in u
                   for _, u in seen_prompts), "memory never reached the model"
        # A mod can erase it all; a viewer cannot.
        b._forget_command("mod", "moderator/1", "kvack")
        assert b._memory.recall(["kvack"]) == []
        assert any("nothing remembered about kvack" in s for s in b.said)
        b._memory.remember("kvack", ["sleeps on the floor by choice"])
        b._forget_command("viewer9", "", "kvack")
        assert b._memory.recall(["kvack"]), "a viewer erased a memory"
    finally:
        llm.chat_reply = orig
    print("[PASS] the bot remembers its viewers, and a mod can forget them")


def test_the_quiet_room_gets_a_conversation_opener():
    """The other half of conversational: a chime-in can only trigger off
    someone's message, which is impossible when the room has gone silent -
    exactly when the bot should be doing the talking. After
    chat_ai_quiet_seconds of silence the keeper queues one opener: posted
    bare (nobody to @), at most once per chat_ai_quiet_cooldown, inside
    the same hourly cap, and never while paused or !cb-off."""
    b = _bot(llm_api_key="k")
    seen = []
    orig = llm.chat_reply

    def _model(s, u, c):
        seen.append(u)
        # Two distinct lines: the anti-echo gate would correctly decline
        # an identical repeat of the opener.
        return ("Anyone else ever lose a whole day to a weigh station "
                "line?" if len(seen) == 1 else
                "Heard a guy on the CB claim his cat navigates for him.")

    llm.chat_reply = _model
    try:
        now = time.time()
        # Chat alive: no opener.
        b._last_chat = now - 10
        assert not b._chat_ai_tick(now=now)
        # Quiet: one opener, posted bare, prompt says the room is quiet.
        b._last_chat = now - 500
        assert b._chat_ai_tick(now=now)
        _drain(b)
        assert b.said and not b.said[0].startswith("@"), b.said
        assert any("gone quiet" in u for u in seen), "model not told"
        # The quiet cooldown holds for a second tick.
        b._last_chat = now - 900
        assert not b._chat_ai_tick(now=now + 30)
        # ...but a mention still works; the bot just spoke, so the
        # mention cooldown governs, not the quiet one.
        b._on_message("kvack", "#t", "doc good one", "kvack", "")
        _drain(b)
        assert len(b.said) == 2, b.said
        # Paused and !cb off both stop the openers.
        b._last_chat = now - 900
        b._chat_ai_last = 0.0
        b.paused = True
        assert not b._chat_ai_tick(now=now + 600)
        b.paused = False
        b._cb_ambient_off = True
        assert not b._chat_ai_tick(now=now + 600)
        b._cb_ambient_off = False
        # The hourly cap applies here too.
        b._chat_ai_times = [now] * bot_mod.DEFAULTS["chat_ai_max_hour"]
        assert not b._chat_ai_tick(now=now + 900)
        b._chat_ai_times = []
        # An offline channel is not quiet, it is empty: no openers into a
        # dead room all night. (Unknown - no Helix at all - still fires.)
        class _Dead:
            def is_live(self):
                return False

        b._access.helix = _Dead()
        b._chat_ai_times = []
        b._chat_ai_last = 0.0
        assert not b._chat_ai_tick(now=now + 1200)
        b._access.helix = None
        assert b._chat_ai_tick(now=now + 1300)
        # And a disabled chat AI never opens.
        b2 = _bot(chat_ai_enabled=False)
        b2._last_chat = now - 900
        assert not b2._chat_ai_tick(now=now)
    finally:
        llm.chat_reply = orig
    print("[PASS] a quiet room gets a conversation opener, bounded")


def test_ask_is_a_command_not_a_feature():
    """!ask is wired like !funfact and !whois: enqueued from _on_message
    whatever chat_ai_enabled and fun_commands say, in the help whatever
    the config says - and it answers with the fact engine when no LLM is
    configured. The regression this pins: !ask was once fully built but
    never in the classifier, so live chat silently dropped it."""
    b = _bot(chat_ai_enabled=False, fun_commands=False, llm_api_key="")
    orig_fact = bot_mod.get_funfact
    try:
        bot_mod.get_funfact = lambda q, o: {
            "place": "Road train",
            "fact": "A driver pulled 113 trailers for 1,235 metres."}
        b._on_message("kvack", "#t",
                      "!ask whats the longest truck in the world",
                      "kvack", "")
        assert not b._jobs.empty(), "!ask was not enqueued"
        _drain(b)
        assert b.said and "113 trailers" in b.said[0], b.said
        b._say_help("kvack", "")
        _drain(b)
        assert any("ask anything" in s for s in b.said), b.said
    finally:
        bot_mod.get_funfact = orig_fact

    # With an LLM configured it answers in persona even with the chat AI
    # off - a command, like !funfact using the LLM writer - but records
    # NOTHING: no logged lines, no distilled facts.
    b2 = _bot(chat_ai_enabled=False, llm_api_key="k")
    orig_reply = llm.chat_reply
    try:
        llm.chat_reply = lambda s, u, c: (
            "Road trains. I have opinions, none of them printable.")
        b2._on_message("hollieburgin", "#t", "!ask best truck?",
                       "hollieburgin", "")
        _drain(b2)
        assert b2.said and b2.said[0].startswith(
            "@hollieburgin Road trains."), b2.said
        msgs = b2._memory._db.execute(
            "SELECT COUNT(*) FROM messages").fetchall()[0][0]
        mems = b2._memory._db.execute(
            "SELECT COUNT(*) FROM memories").fetchall()[0][0]
        assert msgs == 0 and mems == 0, (msgs, mems)
    finally:
        llm.chat_reply = orig_reply
    print("[PASS] !ask is a command: always on, keyless fallback, "
          "records nothing while the AI is off")


def test_nothing_is_recorded_while_the_feature_is_off():
    b = _bot(chat_ai_enabled=False, llm_api_key="")
    orig = llm.chat_reply
    llm.chat_reply = lambda s, u, c: "never called"
    try:
        b._on_message("kvack", "#t", "doc say something", "kvack", "")
        _drain(b)
        assert b.said == [], b.said
        rows = b._memory._db.execute(
            "SELECT COUNT(*) FROM messages").fetchall()
        assert rows[0][0] == 0, "chat was recorded with the feature off"
    finally:
        llm.chat_reply = orig
    print("[PASS] nothing is recorded while the feature is off")


def main():
    test_a_mention_gets_one_bounded_reply()
    test_chime_ins_are_gated()
    test_the_streamer_can_address_the_bot_but_it_never_butts_in()
    test_unsafe_or_lazy_lines_never_post()
    test_ask_answers_with_persona_then_facts()
    test_a_timed_out_model_is_not_asked_twice()
    test_a_tease_gets_a_comeback_when_the_model_is_down()
    test_emoji_walls_never_chime()
    test_the_bot_cannot_repeat_itself()
    test_factual_questions_get_the_engine_first()
    test_no_failed_chat_attempt_is_silent()
    test_local_models_get_a_smaller_room_to_read()
    test_smalltalk_keeps_the_bot_alive_when_the_model_is_down()
    test_memory_roundtrip_and_forget()
    test_the_bot_remembers_and_forgets()
    test_the_quiet_room_gets_a_conversation_opener()
    test_ask_is_a_command_not_a_feature()
    test_nothing_is_recorded_while_the_feature_is_off()
    print("\nALL PASSED \u2714")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
