"""Engagement index: richer than word count, measured against the person's own usual."""

from __future__ import annotations

from saathi.core import clock, engagement

OPEN_Q = "Good morning, Raj. How are you feeling today?"


def test_confirmations_and_commands_do_not_count():
    for text in ["yes", "No thanks.", "okay", "Remind me to call Sunil at 5", "tell her I'll be ready", "done"]:
        assert engagement.turn_signals(text, "Shall I send it?") is None, text


def test_signals_for_a_turn():
    answer = engagement.turn_signals("We went to the temple and then had lunch with my sister.", OPEN_Q)
    assert answer["replied_to_open_q"] and not answer["initiated"]
    assert answer["content_words"] == 4 and answer["disclosed"]  # went, temple, lunch, sister

    offer = engagement.turn_signals("It was a quiet one really.", "Would you like me to tell Priya?")
    assert not offer["replied_to_open_q"]  # an offer isn't an open question

    raised = engagement.turn_signals("This morning Diya called about her exams.", "Okay. I've set that.")
    assert raised["initiated"] and raised["disclosed"]  # brought it up themselves, mentions someone

    asked = engagement.turn_signals("How is the weather looking for Sunday?", "Okay.")
    assert asked["asked_q"]


def _turns(person_id, texts, first_reply=OPEN_Q):
    previous = first_reply
    for text in texts:
        signals = engagement.turn_signals(text, previous)
        if signals:
            engagement.record(person_id, signals)
        previous = "Thank you for telling me."


def test_short_answers_read_as_quieter_than_their_usual(seeded):
    clock.set_time("09:30")
    _turns("raj", ["Fine.", "Not much.", "I watched the news."])
    today = engagement.today("raj")
    assert today["turns"] == 3 and today["baseline_days"] == 14
    assert today["index"] < engagement.QUIETER
    assert today["label"] == "was quieter than usual"


def test_a_chatty_day_reads_as_more_talkative(seeded):
    clock.set_time("09:30")
    _turns("raj", [
        "Wonderful, actually. Sunil came over and we walked to the market and bought mangoes for everyone.",
        "Then my neighbour Mr Iyer told me all about his grandson's wedding plans in Chennai.",
        "Do you think it will rain on Sunday? I want to take Priya's children to the park.",
        "And I finally fixed the radio, so the morning news is back on every day.",
    ])
    today = engagement.today("raj")
    assert today["index"] > engagement.CHATTIER
    assert today["label"] == "was more talkative than usual"


def test_too_few_turns_to_judge(seeded):
    clock.set_time("09:30")
    _turns("raj", ["Fine."])
    assert engagement.today("raj")["index"] is None
