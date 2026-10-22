"""Due events, computed on demand from the DB and the clock.

There is no background scheduler: the host polls `get_due_events`, and this
module decides deterministically what is due right now. Surfacing an event
marks it so it is never returned twice.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from saathi.core import clock, db, impressions, memory, people, reminders

CHECKIN_WINDOW = timedelta(hours=3)
EVENT_KINDS = ("reminder", "checkin", "note")


def _part_of_day(hhmm: str) -> str:
    hour = int(hhmm.split(":")[0])
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"


def pending_notes_count(person_id: str) -> int:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM note WHERE person_id = ? AND direction = 'to_elder' AND status = 'pending'",
            (person_id,),
        ).fetchone()
    return int(row["n"])


def _due_reminders(person_id: str, now: datetime) -> list[dict]:
    events = []
    for rem in reminders.active_for(person_id):
        occ = reminders.current_occurrence(rem, now)
        if occ is None:
            continue
        if rem["last_fired_at"] and clock.parse(rem["last_fired_at"]) >= occ:
            continue
        reminders.mark_fired(rem["reminder_id"], now)
        events.append({
            "kind": "reminder",
            "event_id": f"reminder:{rem['reminder_id']}:{clock.iso(occ)}",
            "reminder_id": rem["reminder_id"],
            "text": rem["text"],
            "time": rem["time"],
            "spoken_time": reminders.spoken_time(rem["time"]),
        })
    return events


def _due_checkin(person: dict, now: datetime) -> list[dict]:
    today = now.date().isoformat()
    with db.connect() as conn:
        done = {r["slot"] for r in conn.execute(
            "SELECT slot FROM checkin WHERE person_id = ? AND date = ?", (person["person_id"], today)
        )}
    candidates = []
    for slot in sorted(person["checkin_times"]):
        hour, minute = (int(x) for x in slot.split(":"))
        slot_dt = now.replace(hour=hour, minute=minute, second=0)
        if slot not in done and slot_dt <= now:
            candidates.append((slot, slot_dt))
    if not candidates:
        return []
    with db.connect() as conn:
        for slot, _ in candidates:
            conn.execute(
                "INSERT OR IGNORE INTO checkin (person_id, date, slot, surfaced_at) VALUES (?, ?, ?, ?)",
                (person["person_id"], today, slot, clock.iso(now)),
            )
    slot, slot_dt = candidates[-1]  # only the latest; older missed ones are skipped
    if now - slot_dt > CHECKIN_WINDOW:
        return []
    yesterday = now.date() - timedelta(days=1)
    return [{
        "kind": "checkin",
        "event_id": f"checkin:{person['person_id']}:{today}:{slot}",
        "slot": slot,
        "part_of_day": _part_of_day(slot),
        "display_name": person["display_name"],
        "pending_notes": pending_notes_count(person["person_id"]),
        "followups": [{"memory_id": m["memory_id"], "fact": m["fact"]} for m in memory.followups(person["person_id"])],
        # How they seemed yesterday, so the greeting can pick up where things left off.
        "recent_impressions": [i["text"] for i in impressions.on_day(person["person_id"], yesterday)][-4:],
        "yesterday": impressions.day_text(person["person_id"], yesterday),
    }]


def _pending_notes(person_id: str, now: datetime) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT n.*, c.name AS contact_name FROM note n JOIN contact c ON c.contact_id = n.contact_id "
            "WHERE n.person_id = ? AND n.direction = 'to_elder' AND n.status = 'pending' ORDER BY n.created_at",
            (person_id,),
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE note SET status = 'delivered', delivered_at = ? WHERE note_id = ?",
                (clock.iso(now), row["note_id"]),
            )
    events = []
    for row in rows:
        people.log(person_id, "note_delivered", detail=f"from {row['contact_name']}")
        events.append({
            "kind": "note",
            "event_id": f"note:{row['note_id']}",
            "note_id": row["note_id"],
            "from_name": row["contact_name"],
            "from_contact_id": row["contact_id"],
            "text": row["text"],
        })
    return events


def due_events(person_id: str, kinds: list[str] | None = None) -> list[dict]:
    """Return (and mark surfaced) everything due now.

    By default reminders and check-ins. Family notes are only handed over when
    asked for explicitly (kinds=["note"]), e.g. after the person says they'd like
    to hear a message; a check-in event reports how many are waiting.
    """
    person = people.get_person(person_id)
    wanted = set(kinds or ("reminder", "checkin")) & set(EVENT_KINDS)
    now = clock.now()
    events: list[dict] = []
    if "reminder" in wanted:
        events += _due_reminders(person_id, now)
    if "checkin" in wanted:
        events += _due_checkin(person, now)
    if "note" in wanted:
        events += _pending_notes(person_id, now)
    return events
