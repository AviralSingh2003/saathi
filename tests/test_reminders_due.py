"""Reminders, check-ins and due events (deterministic, clock-driven)."""

from __future__ import annotations

import pytest

from saathi.core import SaathiError, clock, due, family, memory, reminders


def test_spoken_time_and_second_person():
    assert reminders.spoken_time("14:00") == "2 in the afternoon"
    assert reminders.spoken_time("09:30") == "9:30 in the morning"
    assert reminders.spoken_time("12:00") == "12 noon"
    assert reminders.second_person("take my BP tablet") == "take your BP tablet"


def test_invalid_time_rejected(seeded):
    with pytest.raises(SaathiError, match="HH:MM"):
        reminders.create("meera", "walk", "2pm")


def test_one_off_reminder_fires_once(seeded):
    clock.set_time("09:05")
    rem = reminders.create("meera", "take my BP tablet", "14:00")
    assert "at 2 in the afternoon" in rem["spoken_confirmation"]
    assert due.due_events("meera", ["reminder"]) == []
    clock.set_time("14:00")
    events = due.due_events("meera", ["reminder"])
    assert [e["reminder_id"] for e in events] == [rem["reminder_id"]]
    assert due.due_events("meera", ["reminder"]) == []  # surfaced only once


def test_daily_reminder_fires_each_day_after_creation(seeded):
    clock.set_time("08:00")
    rem = reminders.create("meera", "water the roses", "08:30", recurrence="daily")
    clock.set_time("08:30")
    assert len(due.due_events("meera", ["reminder"])) == 1
    reminders.complete(rem["reminder_id"])
    assert reminders.get(rem["reminder_id"])["status"] == "active"
    clock.advance(24 * 60)
    assert len(due.due_events("meera", ["reminder"])) == 1


def test_completion_is_logged_and_listed(seeded):
    clock.set_time("09:05")
    rem = reminders.create("meera", "take my BP tablet", "14:00")
    clock.set_time("14:01")
    reminders.complete(rem["reminder_id"])
    listed = reminders.list_upcoming("meera")
    assert listed[0]["done_today"] is True
    with pytest.raises(SaathiError, match="already finished"):
        reminders.complete(rem["reminder_id"])


def test_checkin_carries_followups_and_note_count(seeded):
    family.leave_note("priya-demo", "See you Sunday")
    clock.set_time("09:00")
    events = due.due_events("meera")
    checkin = next(e for e in events if e["kind"] == "checkin")
    assert checkin["part_of_day"] == "morning"
    assert checkin["pending_notes"] == 1
    assert checkin["followups"][0]["fact"].startswith("My grandson Arjun")
    assert due.due_events("meera") == []  # not repeated


def test_answered_followup_is_not_asked_again(seeded):
    clock.set_time("09:00")
    followup = due.due_events("meera")[0]["followups"][0]
    memory.remember("meera", "Arjun's team won", "family", followup_of=followup["memory_id"])
    clock.set_time("18:00")
    checkin = due.due_events("meera")[0]
    assert checkin["followups"] == []


def test_notes_only_handed_over_on_request(seeded):
    family.leave_note("priya-demo", "Call you at 6")
    clock.set_time("09:00")
    assert all(e["kind"] != "note" for e in due.due_events("meera"))
    notes = due.due_events("meera", ["note"])
    assert notes[0]["text"] == "Call you at 6" and notes[0]["from_name"] == "Priya"
    assert due.due_events("meera", ["note"]) == []


def test_reply_requires_delivered_note(seeded):
    note = family.leave_note("priya-demo", "Call you at 6")
    with pytest.raises(SaathiError, match="hasn't been read"):
        family.reply(note["note_id"], "Okay")
    due.due_events("meera", ["note"])
    assert family.reply(note["note_id"], "Okay, I'll be ready")["sent_text"] == "Okay, I'll be ready"


def test_recall_by_keyword(seeded):
    found = memory.recall("meera", "cricket match")
    assert found and "cricket" in found[0]["fact"]
