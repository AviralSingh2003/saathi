"""The single source of "now". Stored as an offset in the DB so the server,
the host and scripts agree, and so the demo can fast-forward time."""

from __future__ import annotations

from datetime import datetime, timedelta

from saathi.core import db


def _offset() -> float:
    with db.connect() as conn:
        row = conn.execute("SELECT offset_seconds FROM clock WHERE id = 1").fetchone()
    return float(row["offset_seconds"]) if row else 0.0


def now() -> datetime:
    """Local, naive datetime (seconds precision)."""
    return (datetime.now() + timedelta(seconds=_offset())).replace(microsecond=0)


def set_offset(seconds: float) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO clock (id, offset_seconds) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET offset_seconds = excluded.offset_seconds",
            (seconds,),
        )


def advance(minutes: float) -> datetime:
    set_offset(_offset() + minutes * 60)
    return now()


def set_time(hhmm: str) -> datetime:
    """Jump to the next HH:MM[:SS] — today's, or tomorrow's if that time has already passed."""
    parts = [int(x) for x in hhmm.split(":")]
    hour, minute, second = (parts + [0, 0])[:3]
    current = now()
    target = current.replace(hour=hour, minute=minute, second=second)
    if target < current:
        target += timedelta(days=1)
    real = datetime.now().replace(microsecond=0)
    set_offset((target - real).total_seconds())
    return now()


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def parse(raw: str) -> datetime:
    return datetime.fromisoformat(raw)


def start_of_day(dt: datetime | None = None) -> datetime:
    dt = dt or now()
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)
