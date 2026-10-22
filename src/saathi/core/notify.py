"""Telling the family: one fan-out for email (Amazon SES) and SMS (Amazon SNS), plus an alert log.

Every message to family goes through here. Urgent messages (emergencies) ignore the
family member's own notification preferences; everything else respects them.
"""

from __future__ import annotations

from datetime import timedelta

from saathi.core import SaathiError, clock, db, mailer, people, sms

CONCERN_COOLDOWN = timedelta(hours=6)
URGENT_KINDS = ("medical_emergency", "self_harm", "asked_for_help", "checking", "all_clear")


def deliver_to(contact: dict, kind: str, subject: str, body: str | None, sms_text: str | None,
               urgent: bool = False) -> dict:
    """Email and/or text one family member. Returns what went where (and what failed)."""
    deliveries, errors = [], []
    if body and contact.get("email") and (urgent or contact["notify_email"]):
        try:
            deliveries.append(mailer.send(contact["email"], contact["name"], subject, body, kind=kind))
        except SaathiError as exc:
            errors.append(str(exc))
    if sms_text and contact.get("phone") and (urgent or contact["notify_sms"]):
        try:
            deliveries.append(sms.send(contact["phone"], contact["name"], sms_text, kind=kind))
        except SaathiError as exc:
            errors.append(str(exc))
    return {"contact_name": contact["name"], "deliveries": deliveries, "errors": errors}


def notify(person_id: str, kind: str, subject: str, body_for, sms_for, urgent: bool = False,
           detail: str = "") -> dict:
    """Message every family member who signed up for updates. body_for / sms_for take the contact
    (so messages can greet them by name) and return text or None."""
    results = []
    for contact in people.contacts(person_id):
        if contact["consent_granted"]:
            results.append(deliver_to(contact, kind, subject, body_for(contact), sms_for(contact), urgent))
    log_alert(person_id, kind, detail or subject)
    return {"kind": kind, "results": results,
            "delivered": sum(len(r["deliveries"]) for r in results),
            "errors": [e for r in results for e in r["errors"]]}


def log_alert(person_id: str, kind: str, detail: str) -> int:
    with db.connect() as conn:
        cur = conn.execute("INSERT INTO alert (person_id, ts, kind, detail) VALUES (?, ?, ?, ?)",
                           (person_id, clock.iso(clock.now()), kind, detail))
        return int(cur.lastrowid)


def recent_alerts(person_id: str, limit: int = 5, kinds: tuple[str, ...] | None = None) -> list[dict]:
    kinds = kinds or (*URGENT_KINDS, "concern")
    marks = ",".join("?" * len(kinds))
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT ts, kind, detail FROM alert WHERE person_id = ? AND kind IN ({marks}) "
            "ORDER BY ts DESC, alert_id DESC LIMIT ?",
            (person_id, *kinds, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def _last(person_id: str, kind: str) -> str | None:
    with db.connect() as conn:
        row = conn.execute("SELECT ts FROM alert WHERE person_id = ? AND kind = ? ORDER BY ts DESC LIMIT 1",
                           (person_id, kind)).fetchone()
    return row["ts"] if row else None


def concern(person_id: str, message: str) -> dict | None:
    """A short text to family when something seems worrying (at most one per person every 6 hours)."""
    last = _last(person_id, "concern")
    if last and clock.now() - clock.parse(last) < CONCERN_COOLDOWN:
        return None
    person = people.get_person(person_id)
    text = f"Saathi: {message.strip()}"
    return notify(person_id, "concern", f"A note about {person['display_name']}",
                  lambda c: f"Hi {c['name']},\n\n{message.strip()}\n\n— Saathi", lambda c: text, detail=message)
