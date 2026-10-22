"""Emergencies: phrase list, then a time-limited Haiku check, then simulated dispatch before family.

The Haiku check is stubbed (live mode needs AWS); everything around it is real.
"""

from __future__ import annotations

import time

import pytest

from saathi import config
from saathi.core import clock, people
from saathi.host import emergency
from tests.conftest import outbox_files, sms_texts


def _dispatches(env):
    return sorted((env / "outbox").glob("*-dispatch-*.txt")) if (env / "outbox").exists() else []


@pytest.fixture
def live_check(host, monkeypatch):
    """Turn on layer 2 with a stubbed Haiku verdict (set .verdict, or .delay to simulate a slow call)."""
    class Stub:
        verdict = {"type": "none", "confidence": 0.0, "summary": ""}
        delay = 0.0
        calls = []

    def fake(utterance, recent_turns=None):
        Stub.calls.append(utterance)
        if Stub.delay:
            time.sleep(Stub.delay)
        return Stub.verdict

    monkeypatch.setattr(emergency, "assess", fake)
    monkeypatch.setenv("EMERGENCY_CHECK_TIMEOUT", "0.3")
    host.settings = config.get()
    host.emergency_check_enabled = True
    return Stub


# ---------- deciding ----------

def test_thresholds():
    assert emergency.decide(None) == "ask"  # timeout or error
    assert emergency.decide({"type": "medical_emergency", "confidence": 0.8}) == "dispatch"
    assert emergency.decide({"type": "medical_emergency", "confidence": 0.45}) == "ask"
    assert emergency.decide({"type": "medical_emergency", "confidence": 0.2}) == "none"
    assert emergency.decide({"type": "self_harm", "confidence": 0.7}) == "self_harm"
    assert emergency.decide({"type": "none", "confidence": 0.99}) == "none"
    assert emergency.parse('{"type": "earthquake", "confidence": "high"}') == {
        "type": "none", "confidence": 0.0, "summary": ""}


# ---------- Haiku catches what the phrase list misses ----------

async def test_indirect_wording_contacts_emergency_services_first(host, live_check, isolated_env):
    live_check.verdict = {"type": "medical_emergency", "confidence": 0.9, "summary": "possible stroke"}
    text = "I feel a bit funny, my words are coming out all wrong and my arm is heavy"
    result = await host.handle_utterance("meera", text)
    assert result.escalated
    assert result.reply.startswith("I've called emergency services, Meera. Help is on the way.")
    dispatch = _dispatches(isolated_env)
    urgent = sms_texts(isolated_env, "medical_emergency")
    assert dispatch and urgent and "Emergency services have been contacted for Meera" in urgent[0]
    assert text in dispatch[0].read_text(encoding="utf-8")
    assert any(f["message"].get("method") == "emergency_check" for f in result.frames)


async def test_self_harm_alerts_family_and_gives_crisis_line(host, live_check, isolated_env):
    live_check.verdict = {"type": "self_harm", "confidence": 0.8, "summary": "feels a burden"}
    result = await host.handle_utterance("meera", "Everyone would be fine without me around")
    assert "crisis line" in result.reply
    assert sms_texts(isolated_env, "self_harm") and not _dispatches(isolated_env)


async def test_ordinary_turns_get_the_normal_reply(host, live_check, isolated_env):
    result = await host.handle_utterance("meera", "My knee aches a bit after the walk, nothing new.")
    assert not result.escalated and not result.checking
    assert sms_texts(isolated_env) == [] and not _dispatches(isolated_env)
    assert live_check.calls  # the check did run


async def test_the_phrase_list_answers_before_haiku_is_asked(host, live_check, isolated_env):
    await host.handle_utterance("meera", "I think I'm having a heart attack")
    assert live_check.calls == [] and _dispatches(isolated_env)


# ---------- too slow or unsure: ask, then escalate ----------

async def test_timeout_asks_then_yes_dispatches(host, live_check, isolated_env):
    live_check.delay = 2
    asked = await host.handle_utterance("meera", "I don't feel right at all")
    assert asked.checking and asked.reply == "Are you okay, Meera? Do you need me to call for help?"
    assert "Saathi is checking with Meera now" in sms_texts(isolated_env, "checking")[0]
    result = await host.handle_utterance("meera", "Yes, please call someone")
    assert result.escalated and _dispatches(isolated_env)


async def test_unsure_then_fine_tells_family_all_clear(host, live_check, isolated_env):
    live_check.verdict = {"type": "medical_emergency", "confidence": 0.45, "summary": "dizzy"}
    asked = await host.handle_utterance("meera", "I went a bit dizzy just now")
    assert asked.checking
    result = await host.handle_utterance("meera", "No, I'm fine, I just stood up too fast")
    assert result.reply.startswith("I'm glad you're okay, Meera.")
    assert sms_texts(isolated_env, "all_clear") == ["Saathi: Meera says they're okay. No emergency services were contacted."]
    assert not _dispatches(isolated_env)


async def test_no_answer_dispatches_after_the_wait(host, live_check, isolated_env):
    live_check.delay = 2
    await host.handle_utterance("meera", "Oh dear, oh dear")
    assert await host.poll("meera") == [] or not _dispatches(isolated_env)  # still waiting
    clock.advance(1)  # 60 s later, no answer
    result = (await host.poll("meera"))[0]
    assert result.escalated and result.proactive
    assert result.reply.startswith("I haven't heard back from you, so I've called emergency services")
    assert _dispatches(isolated_env)


async def test_slow_checks_ask_at_most_once_every_ten_minutes(host, live_check, isolated_env):
    live_check.delay = 2
    first = await host.handle_utterance("meera", "Hmm")
    await host.handle_utterance("meera", "I'm fine")
    second = await host.handle_utterance("meera", "What a slow day")
    assert first.checking and not second.checking


# ---------- delivery ----------

async def test_urgent_alerts_ignore_notification_preferences(host, isolated_env):
    contact = people.contact_by_token("priya-demo")
    people.update_contact_setting(contact, "notify_sms", "no")
    people.update_contact_setting(contact, "notify_email", "no")
    await host.handle_utterance("meera", "I've fallen and I can't get up")
    assert sms_texts(isolated_env, "medical_emergency") and outbox_files(isolated_env, "medical_emergency")


async def test_offline_has_no_haiku_check(host, monkeypatch):
    def boom(*_):
        raise AssertionError("no model offline")

    monkeypatch.setattr(emergency, "assess", boom)
    result = await host.handle_utterance("meera", "I feel a bit funny")
    assert not result.escalated and not result.checking
