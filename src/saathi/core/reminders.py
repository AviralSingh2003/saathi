"""Reminders. Firing and completion are entirely deterministic."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from saathi.core import SaathiError, clock, db, people

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
RECURRENCES = ("none", "daily")


def validate_time(value: str) -> str:
    match = _TIME_RE.match((value or "").strip())
    if not match:
        raise SaathiError(f"Time must be 24-hour HH:MM (for example 14:00), got '{value}'.")
    return f"{int(match.group(1)):02d}:{match.group(2)}"


_SECOND_PERSON = {"my": "your", "me": "you", "i": "you", "myself": "yourself", "mine": "yours", "i'm": "you're"}


def second_person(text: str) -> str:
    """'take my BP tablet' -> 'take your BP tablet' (for speech only; storage keeps the person's words)."""
    return re.sub(
        r"\b(my|me|i|myself|mine|i'm)\b",
        lambda m: _SECOND_PERSON[m.group(1).lower()],
        text,
        flags=re.IGNORECASE,
    )


def spoken_time(hhmm: str) -> str:
    """'14:00' -> '2 in the afternoon'; '09:30' -> '9:30 in the morning'."""
    hour, minute = (int(x) for x in hhmm.split(":"))
    if hour == 12 and minute == 0:
        return "12 noon"
    if hour == 0 and minute == 0:
        return "midnight"
    h12 = hour % 12 or 12
    base = f"{h12}" if minute == 0 else f"{h12}:{minute:02d}"
    if 5 <= hour < 12:
        period = "in the morning"
    elif 12 <= hour < 17:
        period = "in the afternoon"
    elif 17 <= hour < 21:
        period = "in the evening"
    else:
        period = "at night"
    return f"{base} {period}"


def _occurrence(rem: dict, day: date) -> datetime:
    hour, minute = (int(x) for x in rem["time"].split(":"))
    return datetime(day.year, day.month, day.day, hour, minute)


def current_occurrence(rem: dict, now: datetime) -> datetime | None:
    """The occurrence that is (or most recently was) due, or None."""
    if rem["status"] != "active":
        return None
    if rem["recurrence"] == "daily":
        occ = _occurrence(rem, now.date())
        created = clock.parse(rem["created_at"])
        if occ < created.replace(second=0):
            return None
        return occ if occ <= now else None
    occ = _occurrence(rem, date.fromisoformat(rem["date"]))
    return occ if occ <= now else None


def next_occurrence(rem: dict, now: datetime) -> datetime | None:
    if rem["status"] != "active":
        return None
    if rem["recurrence"] == "daily":
        occ = _occurrence(rem, now.date())
        return occ if occ > now else occ + timedelta(days=1)
    occ = _occurrence(rem, date.fromisoformat(rem["date"]))
    return occ if occ > now else None


def create(person_id: str, text: str, time: str, date_: str | None = None, recurrence: str = "none") -> dict:
    text = (text or "").strip().rstrip(".")
    if not text or len(text) > 200:
        raise SaathiError("Reminder text must be 1-200 characters.")
    time = validate_time(time)
    if recurrence not in RECURRENCES:
        raise SaathiError(f"Recurrence must be one of {', '.join(RECURRENCES)}.")
    people.get_person(person_id)
    now = clock.now()
    if recurrence == "none":
        if date_:
            try:
                day = date.fromisoformat(date_)
            except ValueError as exc:
                raise SaathiError("Date must be YYYY-MM-DD.") from exc
        else:
            today_occ = _occurrence({"time": time}, now.date())
            day = now.date() if today_occ > now else now.date() + timedelta(days=1)
        date_value = day.isoformat()
    else:
        date_value = None
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO reminder (person_id, text, time, date, recurrence, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'active', ?)",
            (person_id, text, time, date_value, recurrence, clock.iso(now)),
        )
        reminder_id = int(cur.lastrowid)
    when = spoken_time(time)
    if recurrence == "daily":
        when_text = f"every day at {when}"
    elif date_value == now.date().isoformat():
        when_text = f"at {when}"
    elif date_value == (now.date() + timedelta(days=1)).isoformat():
        when_text = f"tomorrow at {when}"
    else:
        when_text = f"on {date_value} at {when}"
    return {
        "reminder_id": reminder_id,
        "text": text,
        "time": time,
        "date": date_value,
        "recurrence": recurrence,
        "when_text": when_text,
        "spoken_confirmation": f"Okay. I'll remind you to {second_person(text)} {when_text}.",
    }


def get(reminder_id: int) -> dict:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM reminder WHERE reminder_id = ?", (reminder_id,)).fetchone()
    if row is None:
        raise SaathiError(f"Unknown reminder {reminder_id}.")
    return dict(row)


def list_upcoming(person_id: str, window_hours: int = 24) -> list[dict]:
    now = clock.now()
    horizon = now + timedelta(hours=max(1, min(int(window_hours or 24), 168)))
    today = now.date()
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM reminder WHERE person_id = ? AND status IN ('active', 'done') ORDER BY time",
            (person_id,),
        )]
    out = []
    for rem in rows:
        nxt = next_occurrence(rem, now)
        done_today = bool(rem["last_done_at"]) and clock.parse(rem["last_done_at"]).date() == today
        if rem["status"] == "done" and not done_today:
            continue
        if nxt is not None and nxt > horizon and not done_today:
            continue
        out.append({
            "reminder_id": rem["reminder_id"],
            "text": rem["text"],
            "time": rem["time"],
            "spoken_time": spoken_time(rem["time"]),
            "recurrence": rem["recurrence"],
            "next_at": clock.iso(nxt) if nxt else None,
            "done_today": done_today,
        })
    return out


def complete(reminder_id: int) -> dict:
    rem = get(reminder_id)
    if rem["status"] != "active":
        raise SaathiError("That reminder is already finished.")
    now = clock.now()
    with db.connect() as conn:
        if rem["recurrence"] == "daily":
            conn.execute("UPDATE reminder SET last_done_at = ? WHERE reminder_id = ?", (clock.iso(now), reminder_id))
        else:
            conn.execute(
                "UPDATE reminder SET status = 'done', last_done_at = ? WHERE reminder_id = ?",
                (clock.iso(now), reminder_id),
            )
    people.log(rem["person_id"], "reminder_done", detail=f"{rem['text']}|{now.strftime('%H:%M')}")
    return {"reminder_id": reminder_id, "status": "done", "text": rem["text"], "done_at": clock.iso(now)}


def mark_fired(reminder_id: int, at: datetime) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE reminder SET last_fired_at = ? WHERE reminder_id = ?", (clock.iso(at), reminder_id))


def active_for(person_id: str) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM reminder WHERE person_id = ? AND status = 'active'", (person_id,)
        ).fetchall()
    return [dict(r) for r in rows]
