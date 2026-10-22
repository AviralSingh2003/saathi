"""Automatic memory, offline path: verbatim facts only, never feelings or health, person in control.

(Live, Claude decides what to remember through reflect_turn; see test_reflect.py.)
"""

from __future__ import annotations

from saathi.core import capture, clock, due, family, memory, people, summary


def _stored(result):
    return [m["fact"] for m in result["stored"]]


# ---------- offline selection (rules) ----------

def test_keeps_people_plans_and_likes_verbatim(seeded):
    result = capture.capture(
        "meera",
        "My granddaughter Diya starts college next week. I really love the jasmine in my garden. It rained a lot.",
    )
    assert _stored(result) == ["My granddaughter Diya starts college next week", "I really love the jasmine in my garden"]
    categories = {m["fact"]: m["category"] for m in result["stored"]}
    assert categories["My granddaughter Diya starts college next week"] == "upcoming"
    assert categories["I really love the jasmine in my garden"] == "preference"
    assert all(m["source"] == "auto" for m in memory.recall("meera", "diya jasmine"))


def test_never_keeps_feelings(seeded):
    result = capture.capture("meera", "I'm feeling lonely since my son moved away. My son seemed busy.")
    assert result["stored"] == []
    assert {r["reason"] for r in result["rejected"]} == {"feelings are never auto-saved"}


def test_never_keeps_health_details(seeded):
    result = capture.capture("meera", "My knee hurts after walking with my friend Kamala. My doctor is Dr Rao.")
    assert result["stored"] == []
    assert all(r["reason"].startswith("health") for r in result["rejected"])


def test_ignores_questions_requests_and_short_bits(seeded):
    for text in ["Is my grandson coming tomorrow?", "tell her my grandson is coming tomorrow",
                 "Remind me to call my sister tomorrow at 5", "yes", "my son"]:
        assert capture.capture("meera", text)["stored"] == [], text


def test_duplicates_are_not_stored_twice(seeded):
    assert _stored(capture.capture("meera", "My brother Sunil lives in Pune with his family."))
    assert capture.capture("meera", "My brother Sunil lives in Pune with his family.")["stored"] == []


# ---------- live selection (Bedrock picks, server verifies) ----------

def _live(monkeypatch, picks):
    monkeypatch.setenv("MOCK", "0")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-model")
    monkeypatch.setattr(capture, "_bedrock_candidates", lambda utterance: picks)


def test_live_paraphrase_is_rejected(monkeypatch, seeded):
    _live(monkeypatch, [{"text": "Meera's granddaughter is starting college", "category": "upcoming"}])
    result = capture.capture("meera", "My granddaughter Diya starts college next week.")
    assert result["method"] == "bedrock"
    assert result["stored"] == []
    assert result["rejected"][0]["reason"] == "not word-for-word from what the person said"


def test_live_verbatim_span_is_kept_but_health_and_mood_are_not(monkeypatch, seeded):
    _live(monkeypatch, [
        {"text": "my granddaughter Diya starts college next week", "category": "upcoming"},
        {"text": "my knee has been sore", "category": "general"},
        {"text": "I am so happy about it", "category": "general"},
    ])
    result = capture.capture("meera", "Oh, my granddaughter Diya starts college next week! My knee has been sore. "
                                      "I am so happy about it.")
    assert _stored(result) == ["my granddaughter Diya starts college next week"]
    assert len(result["rejected"]) == 2


def test_live_failure_falls_back_to_rules(monkeypatch, seeded):
    monkeypatch.setenv("MOCK", "0")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-model")

    def boom(_):
        raise RuntimeError("no credentials")

    monkeypatch.setattr(capture, "_bedrock_candidates", boom)
    result = capture.capture("meera", "My grandson Arjun is visiting on Sunday.")
    assert result["method"].startswith("rules")
    assert _stored(result) == ["My grandson Arjun is visiting on Sunday"]


# ---------- how captured facts are used ----------

def test_a_plan_is_asked_about_on_a_later_day_not_the_same_day(seeded):
    clock.set_time("10:00")
    capture.capture("meera", "My granddaughter Diya starts college next week.")
    clock.set_time("18:00")
    same_day = next(e for e in due.due_events("meera") if e["kind"] == "checkin")
    assert all("Diya" not in f["fact"] for f in same_day["followups"])
    clock.set_time("09:00")  # next morning
    next_day = next(e for e in due.due_events("meera") if e["kind"] == "checkin")
    assert next_day["followups"][0]["fact"] == "My granddaughter Diya starts college next week"


def test_updates_quote_the_persons_words(seeded):
    capture.capture("meera", "My granddaughter Diya starts college next week.")
    sent = family.send("meera", "Priya")
    assert 'Meera said: "My granddaughter Diya starts college next week."' in sent["sent_text"]
    assert summary.blocklist_violations(sent["sent_text"], summary.collect_facts(
        people.get_person("meera"), people.contacts("meera")[0])) == []


# ---------- through the host ----------

async def test_host_captures_after_a_turn_and_reports_it(host):
    result = await host.handle_utterance("meera", "My granddaughter Diya starts college next week.")
    assert result.reply == "Thank you for telling me, Meera."
    assert [m["fact"] for m in result.remembered] == ["My granddaughter Diya starts college next week"]
    assert "reflect_turn" in result.tools_called()


async def test_host_skips_capture_when_the_turn_already_stored_it(host):
    clock.set_time("09:00")
    checkin = await host.poll("meera")  # the check-in asks about Arjun's match
    assert checkin and "Arjun" in checkin[0].reply
    result = await host.handle_utterance("meera", "They won by 20 runs and my grandson Arjun scored ten!")
    assert "remember" in result.tools_called()
    assert "reflect_turn" in result.tools_called()
    assert result.remembered == []  # offline, the follow-up answer isn't mined a second time


async def test_new_fact_at_a_checkin_is_kept_and_the_question_waits(host):
    clock.set_time("09:00")
    await host.poll("meera")  # "Last time you mentioned … Arjun … How did it go?"
    result = await host.handle_utterance("meera", "My granddaughter Diya starts college next week.")
    assert result.reply == "Thank you for telling me, Meera."
    assert "remember" not in result.tools_called()  # not taken as the answer about Arjun
    assert [m["fact"] for m in result.remembered] == ["My granddaughter Diya starts college next week"]
    assert any("Arjun" in m["fact"] for m in memory.followups("meera"))  # still to be asked about


async def test_a_feeling_at_a_checkin_is_logged_not_remembered(host):
    clock.set_time("09:00")
    await host.poll("meera")
    result = await host.handle_utterance("meera", "I'm feeling a bit tired")
    assert result.tools_called() == {"log_mood_selfreport", "reflect_turn"}
    assert all("tired" not in m["fact"] for m in memory.recall("meera", limit=20))
    assert [i["text"] for i in result.impressions] == ["seemed a bit tired"]  # an impression, not a fact


async def test_reflection_is_hidden_from_the_model(host):
    assert "reflect_turn" not in {t.tool_name for t in host.tools}


async def test_read_back(host):
    kept = await host.handle_utterance("meera", "My brother Sunil lives in Pune with his family.")
    assert kept.remembered
    readback = await host.handle_utterance("meera", "What do you remember about me?")
    assert "Your brother Sunil lives in Pune with his family." in readback.reply


async def test_crisis_turn_is_never_mined(host):
    result = await host.handle_utterance("meera", "I've fallen near my daughter's photo and I can't get up")
    assert result.escalated
    assert "reflect_turn" not in result.tools_called()
