"""The family loop: updates go straight out (email + text), notes, replies, and the family's own settings."""

from __future__ import annotations

import pytest

from saathi.core import SaathiError, clock, db, family, people, reminders
from tests.conftest import outbox_files, sms_texts


def _complete_a_reminder():
    rem = reminders.create("meera", "take my BP tablet", "14:00")
    clock.set_time("14:00")
    reminders.complete(rem["reminder_id"])


def test_update_is_sent_at_once_by_email_and_text(seeded, isolated_env):
    _complete_a_reminder()
    people.log_mood_selfreport("meera", "pretty good")
    sent = family.send("meera", "Priya", extra_note="The roses are out")
    assert sent["status"] == "delivered" and set(sent["channels"]) == {"outbox", "sms-outbox"}
    assert 'Meera asked me to tell you: "The roses are out."' in sent["sent_text"]
    email = outbox_files(isolated_env, "update")[0].read_text(encoding="utf-8")
    assert "The roses are out." in email.replace("=\n", "")
    assert sms_texts(isolated_env, "update") == ['Saathi: Meera says: "The roses are out." Today\'s update is in your email.']


def test_everything_from_today_is_included(seeded):
    from saathi.core import memory

    _complete_a_reminder()
    people.log_mood_selfreport("meera", "a bit tired")
    memory.remember("meera", "My neighbours brought round some mangoes", "general")
    text = family.send("meera", "Priya")["sent_text"]
    assert 'completed the reminder "take my BP tablet"' in text
    assert 'described feeling "a bit tired"' in text
    assert "mangoes" in text


def test_no_update_to_a_contact_who_has_not_signed_up(seeded):
    with db.connect() as conn:
        conn.execute("UPDATE contact SET consent_granted = 0 WHERE contact_id = 'priya'")
    with pytest.raises(SaathiError, match="hasn't signed up"):
        family.send("meera", "Priya")


def test_family_choose_how_they_are_told(seeded, isolated_env):
    contact = people.contact_by_token("priya-demo")
    people.update_contact_setting(contact, "notify_sms", "no")
    sent = family.send("meera", "Priya")
    assert sent["channels"] == ["outbox"] and sms_texts(isolated_env) == []
    with pytest.raises(SaathiError, match="only change how they're notified"):
        people.update_contact_setting(contact, "share_topics", "yes")


def test_notes_only_from_contacts_who_can_leave_them(seeded):
    with db.connect() as conn:
        conn.execute("UPDATE contact SET can_leave_notes = 0 WHERE contact_id = 'priya'")
    with pytest.raises(SaathiError, match="aren't set up"):
        family.leave_note("priya-demo", "Hello Mum")


def test_reply_goes_straight_back_by_email_and_text(seeded, isolated_env):
    from saathi.core import due

    note = family.leave_note("priya-demo", "Mum, I'll call Sunday at 6.")
    due.due_events("meera", ["note"])  # read to her
    sent = family.reply(note["note_id"], "I'll be ready at 6")
    assert sent["sent_text"] == "I'll be ready at 6"
    assert sms_texts(isolated_env, "reply") == ['Saathi: Meera replied: "I\'ll be ready at 6"']


def test_what_is_shared_answer_is_truthful(seeded):
    person = people.get_person("meera")
    answer = people.share_summary(person, people.contacts("meera")[0])
    assert "how you've been" in answer and "I'll get help" in answer


def test_unknown_family_token_rejected(seeded):
    with pytest.raises(SaathiError, match="isn't valid"):
        family.dashboard("not-a-token")


def test_wipe_person_removes_everything(seeded):
    _complete_a_reminder()
    family.send("meera", "Priya")
    result = people.wipe_person("meera")
    assert result["deleted"]["reminder"] == 1 and result["deleted"]["family_update"] == 1
    with pytest.raises(SaathiError):
        people.get_person("meera")
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM reminder WHERE person_id='meera'").fetchone()[0] == 0
