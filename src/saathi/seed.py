"""Demo data: Meera (and Raj) with their daughter Priya.

Memories are things the person "said". There are also two weeks of history so
Saathi already knows them a little: engagement baselines (counts only), a few past
impressions and day summaries, and one insight.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timedelta

from saathi import config
from saathi.core import clock, db

DEMO_START = "08:59:50"  # the 09:00 check-in arrives a few seconds after the page opens


_TABLES = ("preview", "family_update", "note", "checkin", "memory", "activity", "reminder", "impression",
           "day_impression", "turn_stat", "alert", "digest", "contact", "person")

# A typical day's turns: (content_words, replied_to_open_q, initiated, disclosed, asked_q).
_USUAL_DAY = {
    "meera": [(9, 1, 0, 1, 0), (7, 0, 1, 1, 0), (11, 1, 0, 0, 1), (6, 1, 0, 0, 0), (8, 0, 1, 0, 0), (10, 1, 0, 1, 0)],
    "raj": [(8, 1, 0, 1, 0), (6, 1, 0, 0, 0), (9, 0, 1, 1, 1), (7, 1, 0, 0, 0), (5, 0, 1, 0, 0)],
}
# (days ago, person, impression or None, that day's summary)
_PAST_DAYS = [
    (1, "meera", "seemed cheerful", "Meera seemed cheerful, especially talking about her roses."),
    (2, "meera", None, "Meera was more talkative than usual, full of news about the neighbours."),
    (3, "meera", "seemed cheerful", "Meera seemed cheerful and looked forward to Priya's call."),
    (1, "raj", None, "Raj seemed relaxed, chatting about the morning news."),
    (2, "raj", "seemed cheerful", "Raj seemed cheerful after his evening walk."),
]
_INSIGHTS = [("meera", "Lights up when talking about her garden and her grandchildren")]


def _reset_db() -> None:
    """Empty every table. (Deleting the file fails on Windows while the server has it open.)"""
    db.init()
    with db.connect() as conn:
        for table in _TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM sqlite_sequence")
        conn.execute("UPDATE clock SET offset_seconds = 0 WHERE id = 1")


def demo_family_token() -> str:
    """Priya's access token in demo mode (overridable with PRIYA_TOKEN)."""
    return os.environ.get("PRIYA_TOKEN") or "priya-demo"


def seed(reset: bool = True, demo_clock: bool | None = None) -> dict:
    settings = config.get()
    if reset:
        _reset_db()
    else:
        db.init()
    demo = settings.demo_mode if demo_clock is None else demo_clock
    priya_email = os.environ.get("PRIYA_EMAIL", "priya@example.com")
    priya_phone = os.environ.get("PRIYA_PHONE", "+15555550100")  # fictional; set a verified number for live SMS
    meera_token = demo_family_token() if demo else (os.environ.get("PRIYA_TOKEN") or secrets.token_urlsafe(12))
    raj_token = (meera_token + "-raj") if demo else secrets.token_urlsafe(12)

    with db.connect() as conn:
        for person_id, name, display, times, address, emergency_contact in [
            ("meera", "Meera Sharma", "Meera", ["09:00", "18:00"], "Flat 4B, 12 Lake Road, Pune (demo address)", "priya"),
            ("raj", "Raj Sharma", "Raj", ["09:30", "19:00"], "House 7, Green Park, Pune (demo address)", "priya-raj"),
        ]:
            conn.execute(
                "INSERT OR REPLACE INTO person (person_id, name, display_name, speaker_key, checkin_times, language, "
                "address, emergency_contact_id) VALUES (?, ?, ?, ?, ?, 'en-IN', ?, ?)",
                (person_id, name, display, person_id, json.dumps(times), address, emergency_contact),
            )
        for contact_id, person_id, token in [("priya", "meera", meera_token), ("priya-raj", "raj", raj_token)]:
            conn.execute(
                "INSERT OR REPLACE INTO contact (contact_id, person_id, name, relationship, email, phone, "
                "consent_granted, can_leave_notes, notify_email, notify_sms, contact_token) "
                "VALUES (?, ?, 'Priya', 'daughter', ?, ?, 1, 1, 1, 1, ?)",
                (contact_id, person_id, priya_email, priya_phone, token),
            )
        yesterday = (datetime.now().replace(microsecond=0) - timedelta(days=1)).isoformat()
        for person_id, category, fact in [
            ("meera", "upcoming", "My grandson Arjun has a cricket match on Saturday"),
            ("meera", "preference", "I love tending the roses in my garden"),
            ("meera", "family", "Priya usually calls on Sunday evenings"),
            ("raj", "preference", "I enjoy the morning news on the radio"),
        ]:
            conn.execute(
                "INSERT INTO memory (person_id, ts, category, fact) VALUES (?, ?, ?, ?)",
                (person_id, yesterday, category, fact),
            )

    if demo:
        clock.set_offset(0)
        clock.set_time(DEMO_START)
    _seed_history()
    return {
        "family_token": meera_token,
        "family_url": f"http://{settings.ui_host}:{settings.ui_port}/family.html?token={meera_token}",
        # token-free link for opening a browser: the host redirects it (demo mode only)
        "family_short_url": f"http://{settings.ui_host}:{settings.ui_port}/family",
        "elder_url": f"http://{settings.ui_host}:{settings.ui_port}/",
        "now": clock.iso(clock.now()),
    }


def _seed_history() -> None:
    """Two weeks of history relative to the (demo) clock's today."""
    today = clock.start_of_day()
    with db.connect() as conn:
        for person_id, turns in _USUAL_DAY.items():
            for days_ago in range(1, 15):
                day = today - timedelta(days=days_ago)
                for i, (words, replied, initiated, disclosed, asked) in enumerate(turns):
                    ts = clock.iso(day.replace(hour=9, minute=15) + timedelta(minutes=7 * i))
                    conn.execute(
                        "INSERT INTO turn_stat (person_id, ts, content_words, replied_to_open_q, initiated, "
                        "disclosed, asked_q) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (person_id, ts, words, replied, initiated, disclosed, asked),
                    )
        for days_ago, person_id, impression, summary in _PAST_DAYS:
            day = today - timedelta(days=days_ago)
            if impression:
                conn.execute(
                    "INSERT INTO impression (person_id, ts, text, aspect, confidence, method) "
                    "VALUES (?, ?, ?, 'mood', 0.6, 'rules')",
                    (person_id, clock.iso(day.replace(hour=10)), impression),
                )
            conn.execute(
                "INSERT INTO day_impression (person_id, day, text, updated_at) VALUES (?, ?, ?, ?)",
                (person_id, day.date().isoformat(), summary, clock.iso(day.replace(hour=19))),
            )
        for person_id, text in _INSIGHTS:
            conn.execute(
                "INSERT INTO memory (person_id, ts, category, fact, source) VALUES (?, ?, 'insight', ?, 'auto')",
                (person_id, clock.iso(today - timedelta(days=2)), text),
            )


def main() -> None:
    info = seed()
    print("Seeded demo data.")
    for key, value in info.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
