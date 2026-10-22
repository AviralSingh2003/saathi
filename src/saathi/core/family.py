"""The family loop: updates out (email + text), notes in, replies back, and the evening digest.

Rules enforced here (and covered by tests):
- Updates go only to family members who signed up for them (consent_granted), by
  the channels they chose (notify_email, notify_sms). Emergencies are separate
  (core/escalation.py) and ignore those preferences.
- Notes are accepted only from contacts who can leave notes (can_leave_notes).
- Family access needs the contact's token.
"""

from __future__ import annotations

import re
from datetime import datetime

from saathi import config
from saathi.core import SaathiError, clock, db, impressions, notify, people, summary

FOOTER = "\n\n— Sent by Saathi for {name}.\nSaathi is a companion, not a medical service."
SMS_LIMIT = 300


def _email_body(person: dict, contact: dict, body: str) -> str:
    return f"Hi {contact['name']},\n\n{body}" + FOOTER.format(name=person["display_name"])


def _sms(prefix: str, body: str) -> str:
    """A text message: whole sentences up to the limit, then a pointer to the email."""
    text = f"{prefix} {body}"
    if len(text) <= SMS_LIMIT:
        return text
    kept = prefix
    for sentence in re.split(r"(?<=[.!?\"])\s+", body):
        if len(kept) + len(sentence) + 20 > SMS_LIMIT:
            break
        kept += " " + sentence
    return kept + " More in your email."


def _record(person: dict, contact: dict, body: str, delivery: dict, kind: str) -> None:
    channels = ",".join(d["channel"] for d in delivery["deliveries"]) or "dashboard"
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO family_update (person_id, contact_id, text, sent_at, channel, status) VALUES (?, ?, ?, ?, ?, ?)",
            (person["person_id"], contact["contact_id"], body, clock.iso(clock.now()), channels,
             "delivered" if delivery["deliveries"] or not delivery["errors"] else "failed"),
        )
    people.log(person["person_id"], f"{kind}_sent", detail=f"to {contact['name']}")


def send(person_id: str, contact: str, extra_note: str | None = None) -> dict:
    """Send an update about today straight away (email + text)."""
    person = people.get_person(person_id)
    target = people.resolve_contact(person_id, contact)
    if not target["consent_granted"]:
        raise SaathiError(f"{target['name']} hasn't signed up for updates yet, so I can't send one.")
    extra_note = (extra_note or "").strip() or None
    if extra_note and len(extra_note) > 500:
        raise SaathiError("The message is too long (500 characters max).")
    facts = summary.collect_facts(person, target)
    body, method = summary.compose(person, target, facts, extra_note)
    name = person["display_name"]
    # The text leads with their own message, if there is one; the email has the whole day.
    sms_text = (_sms(f"Saathi: {name} says:", f"\"{extra_note.rstrip('.')}.\" Today's update is in your email.")
                if extra_note else _sms("Saathi:", body))
    delivery = notify.deliver_to(target, "update", f"An update about {name}",
                                 _email_body(person, target, body), sms_text)
    if delivery["errors"] and not delivery["deliveries"]:
        raise SaathiError("The update couldn't be sent: " + "; ".join(delivery["errors"]))
    _record(person, target, body, delivery, "update")
    return {"status": "delivered", "channels": [d["channel"] for d in delivery["deliveries"]],
            "contact_name": target["name"], "sent_text": body, "phrasing": method, "errors": delivery["errors"]}


def leave_note(contact_token: str, text: str) -> dict:
    contact = people.contact_by_token(contact_token)
    person = people.get_person(contact["person_id"])
    text = (text or "").strip()
    if not text or len(text) > 500:
        raise SaathiError("A note must be 1-500 characters.")
    if not contact["can_leave_notes"]:
        raise SaathiError(f"Notes from you aren't set up for {person['display_name']}.")
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO note (person_id, contact_id, direction, text, status, created_at) "
            "VALUES (?, ?, 'to_elder', ?, 'pending', ?)",
            (person["person_id"], contact["contact_id"], text, clock.iso(clock.now())),
        )
    return {
        "note_id": int(cur.lastrowid),
        "status": "pending",
        "delivery": f"{person['display_name']} will hear it at the next check-in, or sooner if they ask for messages.",
    }


def reply(note_id: int, text: str) -> dict:
    text = (text or "").strip()
    if not text or len(text) > 500:
        raise SaathiError("A reply must be 1-500 characters.")
    with db.connect() as conn:
        original = conn.execute("SELECT * FROM note WHERE note_id = ?", (note_id,)).fetchone()
    if original is None or original["direction"] != "to_elder":
        raise SaathiError(f"There's no family message {note_id} to reply to.")
    if original["status"] == "pending":
        raise SaathiError("That message hasn't been read to the person yet.")
    person = people.get_person(original["person_id"])
    contact = people.get_contact(original["contact_id"])
    name = person["display_name"]
    delivery = {"deliveries": [], "errors": []}
    if contact["consent_granted"]:
        delivery = notify.deliver_to(
            contact, "reply", f"{name} replied to your message",
            f"Hi {contact['name']},\n\n{name} replied to your message (\"{original['text']}\"):\n\n\"{text}\""
            + FOOTER.format(name=name),
            _sms(f"Saathi: {name} replied:", f"\"{text}\""),
        )
    now = clock.iso(clock.now())
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO note (person_id, contact_id, direction, text, status, created_at, delivered_at, reply_to) "
            "VALUES (?, ?, 'to_family', ?, 'delivered', ?, ?, ?)",
            (person["person_id"], contact["contact_id"], text, now, now, note_id),
        )
        conn.execute("UPDATE note SET status = 'replied' WHERE note_id = ?", (note_id,))
    people.log(person["person_id"], "note_replied", detail=f"to {contact['name']}")
    return {"status": "delivered", "channels": [d["channel"] for d in delivery["deliveries"]] or ["dashboard"],
            "reply_id": int(cur.lastrowid), "contact_name": contact["name"], "sent_text": text,
            "errors": delivery["errors"]}


def send_due_digests() -> list[dict]:
    """Evening summary for each person, once a day after DIGEST_TIME (demo clock)."""
    settings = config.get()
    now = clock.now()
    hour, minute = (int(x) for x in settings.digest_time.split(":"))
    if now < now.replace(hour=hour, minute=minute, second=0):
        return []
    today = now.date().isoformat()
    sent = []
    for person in people.list_people():
        with db.connect() as conn:
            done = conn.execute("SELECT 1 FROM digest WHERE person_id = ? AND date = ?",
                                (person["person_id"], today)).fetchone()
            if done:
                continue
            conn.execute("INSERT INTO digest (person_id, date, sent_at) VALUES (?, ?, ?)",
                         (person["person_id"], today, clock.iso(now)))
        sent += _digest_for(person, now)
    return sent


def _digest_for(person: dict, now: datetime) -> list[dict]:
    name = person["display_name"]
    sent = []
    for contact in people.contacts(person["person_id"]):
        if not contact["consent_granted"]:
            continue
        facts = summary.collect_facts(person, contact)
        body, method = summary.compose(person, contact, facts, None)
        if not facts:
            body = f"A quiet day: {name} didn't talk with Saathi much today."
        urgent = [a for a in notify.recent_alerts(person["person_id"], 10, notify.URGENT_KINDS[:3])
                  if a["ts"] >= clock.iso(clock.start_of_day(now))]
        if urgent:  # the day's emergencies come first
            body = " ".join(_alert_line(a) for a in reversed(urgent)) + " " + body  # each carries its time
        delivery = notify.deliver_to(contact, "digest", f"{name}'s day ({now.strftime('%a %d %b')})",
                                     _email_body(person, contact, body), _sms(f"Saathi, {name}'s day:", body))
        _record(person, contact, body, delivery, "digest")
        sent.append({"person_id": person["person_id"], "contact_name": contact["name"], "text": body,
                     "channels": [d["channel"] for d in delivery["deliveries"]], "phrasing": method})
    if sent:
        notify.log_alert(person["person_id"], "digest", sent[0]["text"])
    return sent


def _alert_line(alert: dict) -> str:
    """'Emergency services have been contacted for Raj (…).' from the urgent text that was sent."""
    text = re.sub(r"^URGENT from Saathi:\s*|^Saathi:\s*", "", alert["detail"])
    return re.split(r"(?<=[.!?])\s+", text)[0]


def dashboard(contact_token: str) -> dict:
    contact = people.contact_by_token(contact_token)
    person = people.get_person(contact["person_id"])
    with db.connect() as conn:
        updates = [dict(r) for r in conn.execute(
            "SELECT text, sent_at, channel FROM family_update WHERE contact_id = ? ORDER BY sent_at DESC LIMIT 10",
            (contact["contact_id"],),
        )]
        notes = [dict(r) for r in conn.execute(
            "SELECT note_id, direction, text, status, created_at, reply_to FROM note "
            "WHERE contact_id = ? ORDER BY created_at DESC, note_id DESC LIMIT 20",
            (contact["contact_id"],),
        )]
    return {
        "person_name": person["display_name"],
        "contact_name": contact["name"],
        "can_leave_notes": contact["can_leave_notes"],
        "shared_with_you": people.family_share_summary(person),
        "updates": updates,
        "notes": notes,
        "impressions": impressions.family_view(person["person_id"]),
        "alerts": notify.recent_alerts(person["person_id"], 5),
    }
