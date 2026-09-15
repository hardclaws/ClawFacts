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
           "persona_state_path": os.path.join(tempfile.mkdtemp(), "p.json"),
           "subgoal_state_path": os.path.join(tempfile.mkdtemp(), "sg.json"),
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


def _make_room_light(b, count=6):
    """Enough recent conversation for a chime, but no flowing-chat burst."""
    now = time.time()
    with b._chat_lock:
        b._chat_human_times = [now - 40 * (i + 1) for i in range(count)]


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
        # line carrying an @mention of its own is dropped whole. A direct ask
        # gets the bot's safe acknowledgement instead of disappearing.
        llm.chat_reply = lambda s, u, c: "@kvack you would not believe it"
        b._chat_ai_mention_last = 0.0
        b._on_message("kvack", "#t", "doc honestly", "kvack", "")
        _drain(b)
        assert b.said[-1] == "@kvack " + chatai.DIRECT_FAILURE_LINE, b.said
    finally:
        llm.chat_reply = orig
    print("[PASS] a mention gets one bounded reply, then quiet")


def test_chime_ins_are_gated():
    b = _bot(llm_api_key="k")
    orig_roll, orig_reply = bot_mod.random, llm.chat_reply
    # Grounded in the message it answers ('running', 'tonight') - a
    # chime that is not about what was said gets declined now.
    llm.chat_reply = lambda s, u, c: "Running I-80 tonight, keep the hammer down."
    try:
        bot_mod.random = _FixedRoll(0.9)      # the fillers roll high too
        for i in range(6):                    # a room worth joining
            b._on_message(f"viewer{i}", "#t",
                          "the weather out there is brutal today",
                          f"viewer{i}", "")
        # Even a winning roll stays silent while those rapid human lines make
        # the room busy. A direct mention would still pass.
        bot_mod.random = _FixedRoll(0.05)
        b._on_message("viewer9", "#t", "anyone else running I-80 tonight",
                      "viewer9", "")
        assert b._jobs.empty(), "autonomous chime interrupted flowing chat"
        # Age the same conversation into light chat: a losing roll stays quiet.
        _make_room_light(b)
        bot_mod.random = _FixedRoll(0.9)
        b._on_message("viewer9", "#t", "anyone else running I-80 tonight",
                      "viewer9", "")
        assert b._jobs.empty(), "chimed in on a losing roll"
        # In light chat a winning roll may speak, once.
        _make_room_light(b)
        bot_mod.random = _FixedRoll(0.05)
        b._on_message("viewer9", "#t", "anyone else running I-80 tonight",
                      "viewer9", "")
        _drain(b)
        assert b.said == ["@viewer9 Running I-80 tonight, keep the "
                          "hammer down."], b.said
        # The long cooldown holds even on a winning roll.
        b._chat_ai_last = time.time() - 30
        bot_mod.random = _FixedRoll(0.05)
        b._on_message("viewer8", "#t", "roads are rough near Joplin",
                      "viewer8", "")
        assert b._jobs.empty(), "chimed in inside the long cooldown"
        # The hourly cap is for autonomous chatter, never direct questions.
        b._chat_ai_last = 0.0
        b._chat_ai_times = [time.time() - 10] * bot_mod.DEFAULTS[
            "chat_ai_max_hour"]
        b._on_message("kvack", "#t", "doc tell them", "kvack", "")
        assert b._jobs.qsize() == 1, "hourly ambient cap blocked a direct ask"
        _drain(b)
        # !bot off pauses everything. !cb off controls only autonomous
        # chatter, so it must not turn a direct question into silence.
        b._chat_ai_times = []
        b._chat_ai_mention_last = 0.0
        b.paused = True
        b._on_message("kvack", "#t", "doc tell them", "kvack", "")
        assert b._jobs.empty(), "spoke while paused"
        b.paused = False
        b._cb_ambient_off = True
        before = len(b.said)
        b._on_message("kvack", "#t", "doc tell them", "kvack", "")
        assert b._jobs.qsize() == 1, "!cb off blocked a direct ask"
        _drain(b)
        assert len(b.said) == before + 1
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


def test_ambient_attempts_use_the_ambient_clock_only():
    """Live-fire: every non-quiet attempt updated the MENTION clock instead
    of the ambient clock. Rejected chimes could therefore run on every winning
    message while real questions were held. The clocks must stay separate and
    an attempt must start its cooldown before its model job runs."""
    b = _bot(llm_api_key="k", chat_ai_chance=1.0)
    _make_room_light(b)
    orig_reply, orig_roll = llm.chat_reply, bot_mod.random
    bot_mod.random = _FixedRoll(0.0)
    calls = []

    def _model(system, user, cfg):
        calls.append(user)
        if "MESSAGE TO ANSWER" in user:
            return "Wide awake. What do you need?"
        return "A freezer poem unrelated to anything in the room."

    llm.chat_reply = _model
    try:
        b._on_message("viewer", "#t", "the weather turned rough",
                      "viewer", "")
        assert b._jobs.qsize() == 1
        marked = b._chat_ai_last
        assert marked > 0, "ambient cooldown was not marked at enqueue"
        # The pending ambient job must not block an actual question.
        b._on_message("Hardclaws", "#t", "doc are you awake",
                      "hardclaws", "broadcaster/1")
        assert b._jobs.qsize() == 2, "ambient work blocked a direct mention"
        _drain(b)
        assert b.said == ["@Hardclaws Wide awake. What do you need?"], b.said
        assert b._chat_ai_last >= marked
        assert b._chat_ai_mention_last > 0
        # Another winning ambient moment inside 10 minutes pays no model call.
        before = len(calls)
        _make_room_light(b)
        b._on_message("viewer2", "#t", "the weather is still rough",
                      "viewer2", "")
        assert b._jobs.empty()
        assert len(calls) == before
    finally:
        llm.chat_reply = orig_reply
        bot_mod.random = orig_roll
    print("[PASS] ambient declines cool down themselves and never block asks")


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
        # ...but a real line may chime once the room is light, not flowing.
        _make_room_light(b)
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


def test_a_held_mention_is_answered_late_to_the_right_person():
    """Live-fire: 'pick a number 1-100 @truckingwithdocbot' arrived
    inside the mention cooldown, was silently held, and a later chime
    answered the number game TO SOMEONE ELSE ('@Etchedchampion Pick
    42...'). A direct question never dangles: it is held, and the keeper
    answers it to the right person the moment the cooldown clears - or
    drops it after two minutes, when answering would be the
    non-sequitur."""
    b = _bot(llm_api_key="k")
    logs = []
    b._log = logs.append
    orig = llm.chat_reply
    llm.chat_reply = lambda s, u, c: "Forty-two. Obviously."
    try:
        T0 = time.time()
        b._chat_ai_mention_last = T0 - 30      # a reply 30s ago: held
        b._on_message("Yeyeboi", "#t",
                      "pick a number 1-100 @truckingwithdocbot",
                      "yeyeboi", "")
        assert b._jobs.empty(), "should be held, not enqueued"
        assert len(b._chat_ai_pending) == 1
        assert any("held" in l for l in logs), logs
        # A second viewer asks inside the same window: QUEUED, not
        # overwritten - the single-slot version lost their question.
        b._on_message("DaniLikesDonuts", "#t", "doc pick one for me too",
                      "danilikesdonuts", "")
        assert len(b._chat_ai_pending) == 2
        # The cooldown clears: answered, oldest first, to Yeyeboi.
        assert b._chat_ai_tick(now=T0 + 40) is True
        # _do_chime re-checks the cooldown against real time; the tick
        # above ran at synthetic T0+40, so move the last-reply timestamp
        # with it, exactly as the wall clock would have.
        b._chat_ai_mention_last = T0 - 200
        _drain(b)
        assert b.said == ["@Yeyeboi Forty-two. Obviously."], b.said
        assert len(b._chat_ai_pending) == 1      # Dani still queued
    finally:
        llm.chat_reply = orig
    # A spammer cannot build a queue: the cap is three, and overflow
    # drops the OLDEST - the freshest questions are the ones still live.
    b4 = _bot(llm_api_key="k")
    T4 = time.time()
    b4._chat_ai_mention_last = T4 - 30
    for who in ("Yeyeboi", "DaniLikesDonuts", "kvack", "tayfta"):
        b4._on_message(who, "#t", "doc pick a number", who.lower(), "")
    assert len(b4._chat_ai_pending) == 3, b4._chat_ai_pending
    assert b4._chat_ai_pending[0][0] == "DaniLikesDonuts"
    # Stale pendings (over two minutes) are dropped, not answered.
    b2 = _bot(llm_api_key="k")
    b2._last_chat = time.time()            # quiet gate closed
    b2._chat_ai_pending = [("kvack", "doc hello there",
                            time.time() - 300)]
    assert b2._chat_ai_tick() is False
    assert b2._chat_ai_pending == []
    # The autonomous hourly cap never strands a direct pending question.
    b3 = _bot(llm_api_key="k")
    b3._last_chat = time.time()
    b3._chat_ai_pending = [("kvack", "doc hello there", time.time())]
    b3._chat_ai_times = [time.time()] * bot_mod.DEFAULTS["chat_ai_max_hour"]
    assert b3._chat_ai_tick() is True
    assert b3._chat_ai_pending == []
    print("[PASS] held mentions queue up and are answered late, each to "
          "the right person")


def test_overheard_questions_never_get_funfacts():
    """Live-fire: 'Where ya cuttin thru with Illinois?' was asked of the
    ROOM - and the chime path answered it with a FunFact about traffic
    lights. The fact engine answers only questions ADDRESSED to the bot
    (mentions, !ask); overheard ones get the persona's judgment, under a
    prompt that says the message was NOT to the bot."""
    b = _bot(llm_api_key="k")
    engine, prompts = [], []
    orig_fact, orig_reply = bot_mod.get_funfact, llm.chat_reply
    orig_roll = bot_mod.random
    bot_mod.get_funfact = lambda q, o: (engine.append(q) or {
        "place": "Illinois",
        "fact": "You may not cut through private property."})
    llm.chat_reply = lambda s, u, c: (prompts.append(u) or
                                      "Illinois? I-80 to Joliet. Skip "
                                      "the Circle.")
    try:
        bot_mod.random = _FixedRoll(1.0)     # fillers never chime
        for i in range(6):
            b._on_message("v%d" % i, "#t", "chatter line %d" % i,
                          "v%d" % i, "")
        _make_room_light(b)
        bot_mod.random = _FixedRoll(0.0)     # the overheard question wins
        b._on_message("reverendscottherapy", "#t",
                      "Where ya cuttin thru with Illinois ?",
                      "reverendscottherapy", "")
        _drain(b)
        assert engine == [], engine
        assert any("NOT to you" in p for p in prompts), prompts
        assert b.said and "I-80" in b.said[0], b.said
        # A factual question ADDRESSED to the bot still gets the engine,
        # with the address stripped from the query and the header.
        b._chat_ai_mention_last = 0.0
        b._on_message("kvack", "#t", "doc what is a bongo twist",
                      "kvack", "")
        _drain(b)
        assert engine == ["what is a bongo twist"], engine
        assert "doc what" not in b.said[-1], b.said
    finally:
        bot_mod.get_funfact = orig_fact
        llm.chat_reply = orig_reply
        bot_mod.random = orig_roll
    print("[PASS] overheard questions never get FunFacts; addressed ones do")


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
    cadence_own = ["Cadence is the whole game on this climb",
                   "Smooth cadence beats stomping the pedals"]
    assert chatai.too_similar("Cadence keeps the effort honest", cadence_own)
    assert not chatai.too_similar(
        "Cadence keeps the effort honest", cadence_own,
        source="doc what cadence should I ride at")
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
        # A direct mention whose first reply echoes the loop gets one retry;
        # direct questions are no longer silently discarded by anti-repeat.
        b._on_message("kvack", "#t", "doc what keeps you awake at night",
                      "kvack", "")
        _drain(b)
        assert len(calls) >= 2 and "COMPLETELY different" in calls[1], calls
        assert b.said and "scale house" in b.said[0], b.said
        assert any("recycled" in l for l in logs), logs
        # !ask gets the same redemption from the same starting history.
        calls.clear()
        b._chat_ai_own = list(own)
        b._reply_ask("Hardclaws", "what is your favorite midnight snack")
        assert len(calls) == 2, calls
        assert "COMPLETELY different" in calls[1]
        assert len(b.said) == 2 and "scale house" in b.said[1], b.said
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
        # Exact live-fire clock question: preserve the data source's label and
        # concrete time; never post it as a FunFact or ask the persona to guess.
        bot_mod.get_funfact = lambda q, o: {
            "place": "Vandalia, Illinois", "kind": "Sunrise",
            "fact": "Sunrise is expected around 6:38 AM local time today."}
        b._chat_ai_mention_last = 0.0
        b._on_message("Hardclaws", "#t",
                      "Docbot what time we expecting sunrise today in "
                      "Vandalia, IL ?", "hardclaws", "broadcaster/1")
        _drain(b)
        assert b.said[-1] == ("Sunrise | Vandalia, Illinois: Sunrise is "
                              "expected around 6:38 AM local time today."), b.said
        assert not persona, persona
        # Exact weather field report: current measured conditions keep their
        # Weather label; an archive-page snippet can never impersonate them.
        bot_mod.get_funfact = lambda q, o: {
            "place": "Marshall, Illinois", "kind": "Weather",
            "fact": ("Currently 68°F with partly cloudy skies; feels like "
                     "66°F; humidity 59%; wind WSW at 12 mph.")}
        b._chat_ai_mention_last = 0.0
        b._on_message("Hardclaws", "#t",
                      "Docbot what is the weather in Marshall, IL",
                      "hardclaws", "broadcaster/1")
        _drain(b)
        assert b.said[-1] == (
            "Weather | Marshall, Illinois: Currently 68°F with partly cloudy "
            "skies; feels like 66°F; humidity 59%; wind WSW at 12 mph."), b.said
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


def test_mods_can_switch_the_bots_voice():
    """!persona: show, list, set, custom, reset - moderators only. The
    chosen voice is what the model actually receives (pinned by
    capturing the system prompt), and it survives a restart."""
    assert len(chatai.PERSONAS) >= 13
    assert len(set(chatai.PERSONAS.values())) == len(chatai.PERSONAS)
    assert chatai.persona("SARGE") == chatai.PERSONAS["sarge"]
    assert chatai.persona("nope") is None
    assert chatai.PERSONAS["doc"] == chatai.DEFAULT_PERSONA
    # The streamer's own crew (the unit medic, the CB, the drop
    # partner, the coach, the trail hand) plus the roadhouse floor
    # (the waitress, the commentator, the detective). Every voice -
    # including a custom one - also receives his story, so a voice
    # that lands on a run night or a Warzone night knows what room it
    # is in.
    for v in ("medic", "cb", "squaddie", "coach", "cowboy",
              "flo", "commentator", "noir"):
        assert chatai.persona(v), v
    assert set(chatai.PERSONA_BLURBS) == set(chatai.PERSONAS)
    bio = chatai.system_prompt()
    assert "airborne" in bio and "Afghanistan" in bio, bio
    assert "truck-stop 5Ks" in bio and "Red Dead Redemption 2" in bio
    assert "airborne" in chatai.system_prompt("you are a pirate"), bio

    b = _bot(llm_api_key="k")
    pre = bot_mod.DEFAULTS["prefix"]
    # A viewer gets silence.
    b._on_message("kvack", "#t", "!persona set sarge", "kvack", "")
    assert b._jobs.empty()
    b._persona_command("kvack", "", "set sarge")
    assert b.said == [], b.said
    # A mod switches to Sarge; the model's system prompt is Sarge's.
    systems = []
    orig = llm.chat_reply
    llm.chat_reply = lambda s, u, c: (systems.append(s) or "Copy that.")
    try:
        b._persona_command("amod", "moderator/1", "set sarge")
        assert any("voice set to sarge" in s for s in b.said), b.said
        assert b._persona_text() == chatai.PERSONAS["sarge"]
        b._on_message("Hardclaws", "#t", "doc what do you think",
                      "hardclaws", "broadcaster/1")
        _drain(b)
        assert systems and chatai.PERSONAS["sarge"] in systems[0]
        # ...and so does the streamer's story - Sarge knows whose
        # channel he is barking in.
        assert "airborne" in systems[0], systems[0]
        # Unknown name, list, custom validation, reset.
        b._persona_command("amod", "moderator/1", "set nobody")
        assert any("no voice called" in s for s in b.said), b.said
        b._persona_command("amod", "moderator/1", "list")
        assert any("sarge" in s and "rookie" in s for s in b.said), b.said
        b._persona_command("amod", "moderator/1", "custom too short")
        assert any("12-300" in s for s in b.said), b.said
        b._persona_command("amod", "moderator/1",
                           "custom You are a pirate captain who "
                           "delivers freight by sea and complains about it")
        assert any("custom voice set" in s for s in b.said), b.said
        assert "pirate" in b._persona_text()
        b._persona_command("amod", "moderator/1", "reset")
        assert any("back to Doc" in s for s in b.said), b.said
        assert b._persona_text() == chatai.DEFAULT_PERSONA
    finally:
        llm.chat_reply = orig
    # It survives a restart: a fresh bot on the same state file loads it.
    b._persona_command("amod", "moderator/1", "set rookie")
    b2 = _bot(persona_state_path=b.cfg["persona_state_path"])
    assert b2._persona_text() == chatai.PERSONAS["rookie"]
    print("[PASS] mods can switch the bot's voice; it reaches the model "
          "and survives restarts")


def test_chimes_answer_what_was_said():
    """Live-fire: 'yeah I hipped 1athlete to that supplement' got
    '@PiMPleff The freezer rattles like wind through pine trees, and
    I'm swapping frozen beans for a steaming oat latte' - a persona
    poem at a person who said nothing about freezers. A chime now has
    to share a content word with the message it jumps on, must not
    repeat chat back, and the bot's own consecutive lines may not
    share even one template word."""
    src1 = "yeah I hipped 1athlete to that supplement"
    bad1 = ("The freezer rattles like wind through pine trees, and I'm "
            "swapping frozen beans for a steaming oat latte while the "
            "highway whispers secrets")
    src2 = ("goodmorning indeed, this is my other account, its Martin "
            "the farmer")
    bad2 = ("The road's amber glow kisses the chrome, and I'm swapping "
            "stale jerky for a warm cup of caramel macchiato as the "
            "night drifts like a soft vinyl record")
    # The two real stream lines are not grounded in their messages.
    assert not chatai.grounded(bad1, src1), bad1
    assert not chatai.grounded(bad2, src2), bad2
    assert chatai.grounded("That supplement hipped him up nicely", src1)
    assert chatai.grounded("Farmer by day, chatter by morning", src2)
    # The template the old rule missed: only 'swapping' is shared, but
    # it was the immediately previous line.
    assert chatai.too_similar(bad2, [bad1])
    # Repeating chat back is not chiming in.
    assert chatai.parrots("goodmorning indeed this is my other "
                          "account Martin", src2)

    # Live-fire: the model returns the freezer line for the supplement
    # message; the bot declines and the room stays clean.
    b = _bot(llm_api_key="k")
    orig_reply, orig_roll = llm.chat_reply, bot_mod.random
    llm.chat_reply = lambda s, u, c: bad1
    try:
        bot_mod.random = _FixedRoll(1.0)     # fillers never chime
        for i in range(6):
            b._on_message("v%d" % i, "#t", "chatter line %d" % i,
                          "v%d" % i, "")
        _make_room_light(b)
        bot_mod.random = _FixedRoll(0.0)     # the chime moment wins
        b._on_message("PiMPleff", "#t", src1, "pimpleff", "")
        _drain(b)
        assert b.said == [], b.said
        # A grounded reply goes out, @-tagged to the speaker. Move past the
        # autonomous attempt cooldown for this independent case.
        b._chat_ai_last = 0.0
        _make_room_light(b)
        llm.chat_reply = lambda s, u, c: "That supplement hipped him up"
        b._on_message("PiMPleff", "#t", "that supplement really works",
                      "pimpleff", "")
        _drain(b)
        assert b.said and "@PiMPleff" in b.said[0], b.said
        assert "supplement" in b.said[0], b.said
        # Parroting the message back is not an answer either.
        b._chat_ai_last = 0.0
        _make_room_light(b)
        llm.chat_reply = lambda s, u, c: "that supplement really works"
        b._on_message("PiMPleff", "#t", "that supplement really works",
                      "pimpleff", "")
        _drain(b)
        assert len(b.said) == 1, b.said
    finally:
        llm.chat_reply = orig_reply
        bot_mod.random = orig_roll
    print("[PASS] chimes answer what was said, or the bot stays quiet")


def test_the_follower_count_is_one_question_away():
    """Live-fire: 'docbot do you know how many follows we have received
    this stream?' got 'I don't have the current follower count on hand
    right now' - while the startup probe had printed total=1368 and
    thrown it away. The bot keeps the baseline now and answers from
    Helix: total now, new since it came online."""
    from types import SimpleNamespace
    b = _bot(llm_api_key="k")
    b._access.helix = SimpleNamespace(follow_total=lambda: 1372)
    b._follows_start = 1368
    engine = []
    orig_fact, orig_reply = bot_mod.get_funfact, llm.chat_reply
    bot_mod.get_funfact = lambda q, o: (engine.append(q) or None)
    llm.chat_reply = lambda s, u, c: "I have no idea, honestly."
    try:
        b._on_message("Hardclaws", "#t",
                      "docbot do you know how many follows we have "
                      "received this stream?", "hardclaws",
                      "broadcaster/1")
        _drain(b)
        assert b.said and "1,372 followers" in b.said[0], b.said
        assert "4 new since I came online" in b.said[0], b.said
        assert engine == [], "the fact engine was asked for follows"
        # No baseline yet: still answers the total.
        b2 = _bot(llm_api_key="k")
        b2._access.helix = SimpleNamespace(follow_total=lambda: 1368)
        b2._on_message("kvack", "#t", "doc whats the follower count",
                       "kvack", "")
        _drain(b2)
        assert b2.said and "1,368 followers" in b2.said[0], b2.said
        assert b2._follows_start == 1368, "first ask sets the baseline"
        # No Helix at all: falls through to the persona, honestly.
        b3 = _bot(llm_api_key="k")
        b3._access.helix = None
        b3._on_message("kvack", "#t", "doc how many followers do we have",
                       "kvack", "")
        _drain(b3)
        assert b3.said and "no idea" in b3.said[0], b3.said
    finally:
        bot_mod.get_funfact = orig_fact
        llm.chat_reply = orig_reply
    print("[PASS] the follower count is one question away "
          "(total + new since baseline)")


def test_a_direct_ask_gets_one_redemption():
    """Live-fire: the zwift question's reply came back unusable (cut
    off mid-sentence, over the rail) and a direct question from the
    streamer got SILENCE - the next mention ('you ok?') was then
    answered with the stale zwift take. A direct ask now gets ONE
    redemption re-ask when the reply fails the cleaner; a chime does
    not - chimes are optional talk."""
    b = _bot(llm_api_key="k")
    calls = []
    orig, orig_fact = llm.chat_reply, bot_mod.get_funfact
    # The zwift question is a WHAT question: stub the fact engine out so
    # the test is hermetic - it has nothing, and the persona answers.
    bot_mod.get_funfact = lambda q, o: None
    bad = "Hey @kvack, check this thing out right now please buddy"
    good = "0% is fine by me - keep the cadence and crush the miles"
    try:
        llm.chat_reply = lambda s, u, c: (calls.append(u) or
                                          (bad if len(calls) == 1
                                           else good))
        b._chat_ai_mention_last = 0.0
        b._on_message("Hardclaws", "#t",
                      "docbot what do we think of people who ride zwift "
                      "with 0% trainer difficulty", "hardclaws",
                      "broadcaster/1")
        _drain(b)
        # Three model calls: the unusable reply, the redemption, and the
        # post-reply memory distill. Two would mean no redemption.
        assert len(calls) == 3, calls
        assert b.said and "0% is fine" in b.said[0], b.said
        # A chime (overheard) gets no redemption: one call, no post.
        c = _bot(llm_api_key="k")
        calls2 = []
        llm.chat_reply = lambda s, u, cfg: (calls2.append(u) or bad)
        bot_mod.random = _FixedRoll(1.0)     # fillers never chime
        for i in range(6):
            c._on_message("v%d" % i, "#t", "chatter line %d" % i,
                          "v%d" % i, "")
        _make_room_light(c)
        bot_mod.random = _FixedRoll(0.0)     # the chime moment wins
        c._on_message("kvack", "#t", "anyone else running I-80 tonight",
                      "kvack", "")
        _drain(c)
        assert len(calls2) == 1, calls2
        assert c.said == [], c.said
    finally:
        llm.chat_reply = orig
        bot_mod.get_funfact = orig_fact
    print("[PASS] a direct ask gets one redemption; a chime does not")


def test_rough_direct_ask_is_answered_and_cannot_go_stale():
    """Exact live-fire regression: an output-only profanity regex was also
    censoring viewer input. The Zwift ask never became a job, but stayed in the
    room buffer, so a later "you ok?" finally triggered the model and received
    the stale Zwift answer. Direct asks must win, unsafe ambient context must
    not linger, and configured fallback settings must actually reach llm.py.
    """
    b = _bot(
        llm_api_key="groq-test",
        llm_fallback_key="or-test",
        llm_fallback_base_url="https://openrouter.ai/api/v1",
        llm_fallback_model="fallback/test",
    )
    assert b._opts["llm_fallback_key"] == "or-test", b._opts
    assert b._opts["llm_fallback_model"] == "fallback/test", b._opts
    assert llm.fallback_endpoint(b._opts) == (
        "https://openrouter.ai/api/v1", "or-test", "fallback/test")
    alias_b = _bot(llm_api_key="groq-test",
                   llm_fallback_api_key="alias-key",
                   llm_fallback_model="fallback/alias")
    assert llm.fallback_endpoint(alias_b._opts) == (
        "https://openrouter.ai/api/v1", "alias-key", "fallback/alias")

    rough = ("Docbot what do we think of people who ride zwift with 0% "
             "trainer difficulty? Pussy or its ok?")
    assert not chatai.factual_question(rough, b._chat_ai_names), \
        "an opinion request was routed to encyclopedia search"
    prompts = []
    orig = llm.chat_reply

    def answer(system, user, cfg):
        prompts.append(user)
        if "you ok?" in user.lower():
            return "Running fine. The coffee is the part making strange noises."
        return "0% is okay. Trainer difficulty is a preference, not a character test."

    # Keep the regression about reply routing; memory distillation is covered
    # separately and would add unrelated model calls to prompts.
    b._distill = lambda *a, **k: None
    llm.chat_reply = answer
    try:
        b._on_message("Hardclaws", "#t", rough, "hardclaws",
                      "broadcaster/1")
        assert not b._jobs.empty(), "rough wording silently killed a direct ask"
        _drain(b)
        assert b.said and "0% is okay" in b.said[-1], b.said
        assert all(rough not in text for _, text in b._chat_ai_snapshot()), \
            b._chat_ai_snapshot()

        # Also simulate an older, otherwise-safe addressed question in the
        # room. direct_context must remove it from the next prompt.
        with b._chat_lock:
            b._chat_buf.append(("Hardclaws", "docbot old zwift question"))
        b._chat_ai_mention_last = 0.0
        b._on_message("Hardclaws", "#t", "Docbot you ok?", "hardclaws",
                      "broadcaster/1")
        _drain(b)
        assert "Running fine" in b.said[-1], b.said
        assert "old zwift question" not in prompts[-1], prompts[-1]
        assert "MESSAGE TO ANSWER" in prompts[-1]
        assert chatai.smalltalk("Docbot you ok?") in chatai._SMALLTALK_LINES
    finally:
        llm.chat_reply = orig
    print("[PASS] rough direct asks answer now; old asks cannot hijack later replies")


def test_mention_notes_are_remembered_and_recalled():
    """Live-fire: 'Docbot take a mental note its 2:49am ... and
    @TruckingWithDoc just took a piss in Sullivan,MO Truck stop' had
    nowhere to go, and the later quiz got persona mush. Notes are
    stored now (mods only, under the person they are about), and a
    question about a person is answered from memory - never routed at
    the fact engine, which answered this one with a weigh station."""
    # The parser: subject from the @mention, filler stripped, and the
    # streamer's real typo ('mental not') parses like 'mental note'.
    for line in ("docbot take a mental not its 2:49am and "
                 "@TruckingWithDoc took a piss in Sullivan,MO truck stop",
                 "docbot take a mental note its 2:49am and "
                 "@TruckingWithDoc took a piss in Sullivan,MO truck stop"):
        subj, payload = chatai.note_request(line)
        assert subj == "TruckingWithDoc", (subj, payload)
        assert "2:49am" in payload and "Sullivan" in payload, payload
    assert chatai.note_request("docbot hows it going") is None
    assert chatai.note_request("docbot take a mental note") is None
    subj, payload = chatai.note_request("doc remember that kvack runs 5ks")
    assert subj is None and payload == "kvack runs 5ks", (subj, payload)

    b = _bot(llm_api_key="k")
    # The streamer takes the note; the ack is queued like any line.
    b._on_message("Hardclaws", "#t",
                  "docbot take a mental note its 2:49am and "
                  "@TruckingWithDoc took a piss in Sullivan,MO truck stop",
                  "hardclaws", "broadcaster/1")
    _drain(b)
    assert any("Noted" in s for s in b.said), b.said
    got = b._memory.recall(["TruckingWithDoc"])
    assert got and "Sullivan" in got[0][1], got
    # A viewer cannot plant notes about people.
    b._on_message("kvack", "#t",
                  "docbot take a mental note kvack is the coolest",
                  "kvack", "")
    assert not b._memory.recall(["kvack"]), "a viewer note was stored"
    # The quiz: the fact engine is never asked, the stored note is in
    # the prompt, and the answer carries it.
    prompts, engine = [], []
    orig_reply, orig_fact = llm.chat_reply, bot_mod.get_funfact
    llm.chat_reply = lambda s, u, c: (prompts.append(u) or
                                      "2:49am, Sullivan MO truck stop")
    bot_mod.get_funfact = lambda q, o: (engine.append(q) or None)
    try:
        b._chat_ai_mention_last = 0.0
        b._on_message("Hardclaws", "#t",
                      "docbot when and where did @TruckingWithDoc last "
                      "take a piss?", "hardclaws", "broadcaster/1")
        _drain(b)
        assert engine == [], engine
        # The quiz prompt carries the stored note (the last prompt may
        # be the post-reply distill pass - check them all).
        assert any("Sullivan" in p for p in prompts), prompts
        # The viewer's denied note was never answered - no prompt was
        # ever framed as a reply to kvack (the room buffer still shows
        # the line, which is correct: it was said in chat).
        assert not any("kvack just said" in p for p in prompts), prompts
        assert b.said and "Sullivan" in b.said[-1], b.said
    finally:
        llm.chat_reply = orig_reply
        bot_mod.get_funfact = orig_fact
    print("[PASS] notes are stored on request and answered from memory")


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
        # The cleaner rejects a 400-char token wall: the log says so and the
        # direct asker gets a safe acknowledgement, never the rejected text.
        logs.clear()
        llm.chat_reply = lambda s, u, c: "x" * 400
        b._chat_ai_mention_last = 0.0
        b._on_message("kvack", "#t", "doc hello there friend", "kvack", "")
        _drain(b)
        assert b.said[-1] == "@kvack " + chatai.DIRECT_FAILURE_LINE, b.said
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
    paragraph = ("Zero percent is fine; trainer difficulty is a preference, "
                 "not a character test. " + "Cadence is still cadence. " * 15)
    recovered = chatai.recover_direct_line(paragraph)
    assert recovered and len(recovered) <= 240 and "Zero percent" in recovered, \
        recovered
    assert chatai.recover_direct_line("x" * 400) is None
    assert chatai.recover_direct_line(
        "A safe opening sentence. " + "pussy " * 60) is None
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
    """A quiet opener is one contextual follow-up per human conversation
    lull, not a timer. It never starts from an empty startup room, never repeats
    into unanswered silence, and remains bounded by the ambient rails."""
    b = _bot(llm_api_key="k")
    seen = []
    orig = llm.chat_reply

    def _model(s, u, c):
        seen.append(u)
        return ("Anyone else ever lose a whole day to a weigh station "
                "line?" if len(seen) == 1 else
                "Does the navigating cat charge by the mile?")

    llm.chat_reply = _model
    try:
        now = time.time()
        # Startup with no human conversation never opens, however old the
        # timestamp gets. Ordinary safe chat arms one contextual follow-up.
        b._last_chat = now - 500
        assert not b._chat_ai_tick(now=now)
        with b._chat_lock:
            b._chat_buf.append(
                ("viewer", "that weigh station line took all afternoon"))
        b._chat_ai_quiet_armed = True
        # Chat alive: no opener.
        b._last_chat = now - 10
        assert not b._chat_ai_tick(now=now)
        # Quiet: one opener, posted bare, and grounded in the last human topic.
        b._last_chat = now - 500
        assert b._chat_ai_tick(now=now)
        _drain(b)
        assert b.said and not b.said[0].startswith("@"), b.said
        assert "weigh station" in b.said[0].lower(), b.said
        assert any("gone quiet" in u for u in seen), "model not told"
        # No human replied, so even after every cooldown expires it cannot
        # repeat into silence.
        b._last_chat = now - 900
        assert not b._chat_ai_tick(now=now + 30)
        assert not b._chat_ai_tick(now=now + 2000)
        # ...but a mention still works; the bot just spoke, so the
        # mention cooldown governs, not the quiet one.
        b._on_message("kvack", "#t", "doc good one", "kvack", "")
        _drain(b)
        assert len(b.said) == 2, b.said
        # Paused and !cb off both stop an armed opener.
        b._last_chat = now - 900
        b._chat_ai_last = 0.0
        b._chat_ai_quiet_armed = True
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


def test_quiet_followups_track_real_human_lulls():
    """Ordinary human chat arms the latch through the production message path.
    If another human speaks while the quiet job is queued, that stale opener is
    canceled and the new conversation owns the next (and only) opportunity."""
    b = _bot(llm_api_key="k", chat_ai_chance=0.0)
    orig = llm.chat_reply
    llm.chat_reply = lambda s, u, c: "Did those cats ever give the map back?"
    try:
        b._on_message("viewer", "#t", "the cats are navigating today",
                      "viewer", "")
        assert b._chat_ai_quiet_armed, "ordinary conversation did not arm lull"
        now = time.time()
        b._last_chat = now - 500
        b._chat_ai_last = 0.0
        assert b._chat_ai_tick(now=now)
        assert not b._chat_ai_quiet_armed, "attempt did not consume old lull"

        # The worker has not run yet. New human chat invalidates that job and
        # starts a fresh human-driven lull instead.
        b._on_message("viewer2", "#t", "the cats stole the map again",
                      "viewer2", "")
        assert b._chat_ai_quiet_armed
        _drain(b)
        assert b.said == [], "stale quiet job interrupted resumed chat"
        assert b._chat_ai_quiet_armed, "canceling stale work lost new lull"

        # Age the new lull and the autonomous backstop: exactly one contextual
        # follow-up can now post.
        b._last_chat = time.time() - 500
        b._chat_ai_last = 0.0
        assert b._chat_ai_tick()
        _drain(b)
        assert b.said == ["Did those cats ever give the map back?"], b.said
        assert not b._chat_ai_quiet_armed
        b._last_chat = time.time() - 5000
        b._chat_ai_last = 0.0
        assert not b._chat_ai_tick(), "same lull repeated after speaking"
    finally:
        llm.chat_reply = orig
    print("[PASS] human chat arms one lull; resumed chat cancels stale work")


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
    test_ambient_attempts_use_the_ambient_clock_only()
    test_the_streamer_can_address_the_bot_but_it_never_butts_in()
    test_unsafe_or_lazy_lines_never_post()
    test_ask_answers_with_persona_then_facts()
    test_a_timed_out_model_is_not_asked_twice()
    test_a_tease_gets_a_comeback_when_the_model_is_down()
    test_emoji_walls_never_chime()
    test_a_held_mention_is_answered_late_to_the_right_person()
    test_overheard_questions_never_get_funfacts()
    test_chimes_answer_what_was_said()
    test_mention_notes_are_remembered_and_recalled()
    test_a_direct_ask_gets_one_redemption()
    test_rough_direct_ask_is_answered_and_cannot_go_stale()
    test_the_follower_count_is_one_question_away()
    test_the_bot_cannot_repeat_itself()
    test_factual_questions_get_the_engine_first()
    test_mods_can_switch_the_bots_voice()
    test_no_failed_chat_attempt_is_silent()
    test_local_models_get_a_smaller_room_to_read()
    test_smalltalk_keeps_the_bot_alive_when_the_model_is_down()
    test_memory_roundtrip_and_forget()
    test_the_bot_remembers_and_forgets()
    test_the_quiet_room_gets_a_conversation_opener()
    test_quiet_followups_track_real_human_lulls()
    test_ask_is_a_command_not_a_feature()
    test_nothing_is_recorded_while_the_feature_is_off()
    print("\nALL PASSED \u2714")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
