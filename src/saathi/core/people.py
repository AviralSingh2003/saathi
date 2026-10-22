"""People, family contacts, the activity log, and session context."""

from __future__ import annotations

import json

from saathi.core import SaathiError, clock, db

def _person_from_row(row) -> dict:
    person = dict(row)
    person["checkin_times"] = db.loads(person["checkin_times"], [])
    return person


def get_person(person_id: str) -> dict:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM person WHERE person_id = ?", (person_id,)).fetchone()
    if row is None:
        raise SaathiError(f"Unknown person '{person_id}'.")
    return _person_from_row(row)


def list_people() -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM person ORDER BY name").fetchall()
    return [_person_from_row(r) for r in rows]


def _contact_from_row(row) -> dict:
    contact = dict(row)
    for key in ("consent_granted", "can_leave_notes", "notify_email", "notify_sms"):
        contact[key] = bool(contact[key])
    return contact


def contacts(person_id: str) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM contact WHERE person_id = ? ORDER BY name", (person_id,)).fetchall()
    return [_contact_from_row(r) for r in rows]


def get_contact(contact_id: str) -> dict:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM contact WHERE contact_id = ?", (contact_id,)).fetchone()
    if row is None:
        raise SaathiError(f"Unknown contact '{contact_id}'.")
    return _contact_from_row(row)


def contact_by_token(token: str) -> dict:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM contact WHERE contact_token = ?", (token or "",)).fetchone()
    if row is None:
        raise SaathiError("That family access link isn't valid.")
    return _contact_from_row(row)


def resolve_contact(person_id: str, contact: str) -> dict:
    """Find one of the person's contacts by id or (case-insensitive) name."""
    wanted = (contact or "").strip().lower()
    for c in contacts(person_id):
        if wanted in (c["contact_id"].lower(), c["name"].lower()):
            return c
    names = ", ".join(c["name"] for c in contacts(person_id)) or "none"
    raise SaathiError(f"I don't have a contact called '{contact}'. Known contacts: {names}.")


def log(person_id: str, type_: str, detail: str = "", mood_word: str | None = None) -> int:
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO activity (person_id, ts, type, detail, mood_word) VALUES (?, ?, ?, ?, ?)",
            (person_id, clock.iso(clock.now()), type_, detail, mood_word),
        )
        return int(cur.lastrowid)


def activity_since(person_id: str, since_iso: str) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM activity WHERE person_id = ? AND ts >= ? ORDER BY ts",
            (person_id, since_iso),
        ).fetchall()
    return [dict(r) for r in rows]


def log_mood_selfreport(person_id: str, word: str) -> int:
    word = (word or "").strip().strip('"').strip()
    if not word or len(word) > 40:
        raise SaathiError("Record the feeling in the person's own short words (1-40 characters).")
    get_person(person_id)
    return log(person_id, "mood_selfreport", detail=f'said they feel "{word}"', mood_word=word)


def share_summary(person: dict, contact: dict) -> str:
    """What Saathi says if the person asks what it shares: short, general and true."""
    if not contact["consent_granted"]:
        return f"{contact['name']} hasn't signed up for updates, so I don't send them anything."
    return (f"I keep {contact['name']} up to date about your day: your reminders, what you tell me and how "
            f"you've been. If you ever need help urgently, I'll get help and let {contact['name']} know straight away.")


def family_share_summary(person: dict) -> str:
    """What the family page explains about the updates it shows."""
    name = person["display_name"]
    return (f"Saathi keeps you up to date about {name}'s day: reminders done, what {name} says and Saathi's "
            "impressions of how they've seemed. You'll get an evening summary, a text if something seems "
            "worrying, and an urgent alert in an emergency.")


def update_setting(person_id: str, key: str, value: str) -> dict:
    get_person(person_id)
    if key == "checkin_times":
        times = [t.strip() for t in str(value).split(",") if t.strip()]
        from saathi.core.reminders import validate_time

        for t in times:
            validate_time(t)
        with db.connect() as conn:
            conn.execute("UPDATE person SET checkin_times = ? WHERE person_id = ?", (json.dumps(sorted(times)), person_id))
        return {"key": key, "value": sorted(times)}
    if key == "speech_rate":
        rate = float(value)
        if not 0.5 <= rate <= 1.5:
            raise SaathiError("speech_rate must be between 0.5 and 1.5.")
        with db.connect() as conn:
            conn.execute("UPDATE person SET speech_rate = ? WHERE person_id = ?", (rate, person_id))
        return {"key": key, "value": rate}
    raise SaathiError(f"Unknown setting '{key}'.")


CONTACT_KEYS = ("notify_email", "notify_sms")


def update_contact_setting(contact: dict, key: str, value: str) -> dict:
    if key not in CONTACT_KEYS:
        raise SaathiError("Family members can only change how they're notified (notify_email, notify_sms).")
    flag = _parse_bool(value)
    with db.connect() as conn:
        conn.execute(f"UPDATE contact SET {key} = ? WHERE contact_id = ?", (int(flag), contact["contact_id"]))
    return {"key": key, "value": flag}


def wipe_person(person_id: str) -> dict:
    """Delete everything stored about a person (contacts, reminders, memories, notes, logs)."""
    get_person(person_id)
    with db.connect() as conn:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table} WHERE person_id = ?", (person_id,)).fetchone()[0]
            for table in ("contact", "reminder", "activity", "memory", "note", "preview", "family_update", "checkin",
                          "impression", "day_impression", "turn_stat", "alert", "digest")
        }
        conn.execute("DELETE FROM person WHERE person_id = ?", (person_id,))  # ON DELETE CASCADE does the rest
    return {"person_id": person_id, "deleted": counts}


def _parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enable", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disable", "disabled"}:
        return False
    raise SaathiError(f"Expected yes/no, got '{value}'.")
