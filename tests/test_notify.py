"""Texts (Amazon SNS) and the evening digest."""

from __future__ import annotations

import pytest

from saathi.core import SaathiError, clock, escalation, family, reminders, sms
from tests.conftest import sms_texts


def test_texts_are_written_to_the_outbox_offline(seeded, isolated_env):
    result = sms.send("+15555550100", "Priya", "Hello from Saathi", "test")
    assert result["channel"] == "sms-outbox"
    assert sms_texts(isolated_env, "test") == ["Hello from Saathi"]
    with pytest.raises(SaathiError, match="No valid phone number"):
        sms.send("0123", "Priya", "Hello", "test")


def test_live_texts_go_through_sns(seeded, monkeypatch):
    calls = []

    class FakeSNS:
        def publish(self, **kwargs):
            calls.append(kwargs)
            return {"MessageId": "m-1"}

    import boto3

    monkeypatch.setenv("MOCK", "0")
    monkeypatch.setenv("SMS_ENABLED", "1")
    monkeypatch.setenv("SNS_REGION", "us-east-1")
    monkeypatch.setattr(boto3, "client", lambda service, **kw: FakeSNS() if service == "sns" else None)
    result = sms.send("+15555550100", "Priya", "URGENT from Saathi: test", "medical_emergency")
    assert result == {"status": "delivered", "channel": "sns", "message_id": "m-1"}
    assert calls[0]["PhoneNumber"] == "+15555550100"
    assert calls[0]["MessageAttributes"]["AWS.SNS.SMS.SMSType"]["StringValue"] == "Transactional"


def test_digest_goes_once_a_day_after_digest_time(seeded, isolated_env):
    rem = reminders.create("meera", "take my BP tablet", "14:00")
    clock.set_time("14:00")
    reminders.complete(rem["reminder_id"])
    clock.set_time("19:00")
    assert family.send_due_digests() == []  # not yet
    clock.set_time("19:31")
    sent = family.send_due_digests()
    assert {s["person_id"] for s in sent} == {"meera", "raj"}
    assert family.send_due_digests() == []  # once a day
    texts = sms_texts(isolated_env, "digest")
    assert any(t.startswith("Saathi, Meera's day: Meera completed the reminder") for t in texts)
    assert any("A quiet day" in t for t in texts)  # Raj said nothing today


def test_digest_leads_with_the_days_emergency(seeded, isolated_env):
    clock.set_time("18:30")
    escalation.escalate("raj", "medical_emergency", "My chest feels tight")
    clock.set_time("19:31")
    family.send_due_digests()
    raj = [t for t in sms_texts(isolated_env, "digest") if "Raj's day" in t][0]
    assert raj.startswith("Saathi, Raj's day: Emergency services have been contacted for Raj at 18:30")


def test_dashboard_shows_recent_alerts(seeded):
    escalation.escalate("meera", "asked_for_help", "Can someone come over?")
    alerts = family.dashboard("priya-demo")["alerts"]
    assert alerts[0]["kind"] == "asked_for_help"
