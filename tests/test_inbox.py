"""The family member's phone: every email and text Saathi sent them, newest first."""

from __future__ import annotations

from saathi.core import clock, escalation, family, people
from saathi.host import inbox


def test_phone_shows_emails_and_texts(seeded):
    clock.set_time("14:00")
    family.send("meera", "Priya", extra_note="The roses are out")
    clock.set_time("18:30")
    escalation.escalate("raj", "medical_emergency", "My chest feels tight")

    priya = people.contact_by_token("priya-demo")
    messages = inbox.messages_for(priya)
    kinds = [(m["type"], m["kind"]) for m in messages]
    # newest first: the emergency (both contacts are the same Priya, so both show on her phone)
    assert kinds[0][1] == "medical_emergency" and kinds[-1][1] == "update"
    texts = [m for m in messages if m["type"] == "text"]
    emails = [m for m in messages if m["type"] == "email"]
    assert any('"The roses are out."' in t["body"] for t in texts)
    assert any(e["subject"] == "An update about Meera" for e in emails)
    urgent = [m for m in messages if m["urgent"]]
    assert {m["type"] for m in urgent} == {"text", "email"}
    assert all("Emergency services have been contacted" in m["body"] for m in urgent)
    assert not any("Sent by Saathi" in e["body"] or "automatic message" in e["body"] for e in emails)  # footers trimmed
    assert all("dispatch" not in m["id"] for m in messages)  # emergency-service records aren't family messages


def test_other_peoples_messages_are_not_shown(seeded):
    family.send("meera", "Priya")
    stranger = {"email": "someone@else.example", "phone": "+15555559999"}
    assert inbox.messages_for(stranger) == []
