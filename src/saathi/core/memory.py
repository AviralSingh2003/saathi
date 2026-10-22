"""Cross-session memory: small facts the person shares, recalled by keyword.

Deliberately simple (keyword match + recency). The remember/recall interface is
narrow so a hosted memory service could replace it without touching tools.

Each memory records its source: 'explicit' (a remember call) or 'auto'
(kept by the host's reflection step after a turn). 'insight' memories are
longer-term patterns about the person, revised as Saathi gets to know them.
"""

from __future__ import annotations

import re

from saathi.core import SaathiError, clock, db, people

CATEGORIES = ("general", "family", "health", "upcoming", "preference", "insight")
SOURCES = ("explicit", "auto")
_STOPWORDS = {"the", "and", "for", "with", "that", "this", "was", "has", "have", "about", "what", "did", "how", "her", "his", "my"}
_COLUMNS = "memory_id, ts, category, fact, source"


def remember(person_id: str, fact: str, category: str = "general", followup_of: int | None = None,
             source: str = "explicit", replaces: int | None = None) -> dict:
    """Store a fact. If it answers a follow-up (an 'upcoming' memory), pass that
    memory's id as followup_of so it isn't asked about again. `replaces` rewrites
    an existing memory instead (a fact that changed, or an insight that matured)."""
    fact = (fact or "").strip()
    if not fact or len(fact) > 300:
        raise SaathiError("A memory must be 1-300 characters.")
    if category not in CATEGORIES:
        category = "general"
    if source not in SOURCES:
        source = "explicit"
    people.get_person(person_id)
    with db.connect() as conn:
        if replaces is not None:
            cur = conn.execute(
                "UPDATE memory SET fact = ?, category = ?, ts = ?, source = ? WHERE memory_id = ? AND person_id = ?",
                (fact, category, clock.iso(clock.now()), source, replaces, person_id),
            )
            if cur.rowcount:
                return {"memory_id": int(replaces), "stored": True, "fact": fact}
        if followup_of is not None:
            conn.execute(
                "UPDATE memory SET category = 'past' WHERE memory_id = ? AND person_id = ? AND category = 'upcoming'",
                (followup_of, person_id),
            )
        existing = conn.execute(
            "SELECT memory_id FROM memory WHERE person_id = ? AND lower(fact) = lower(?)", (person_id, fact)
        ).fetchone()
        if existing:
            return {"memory_id": existing["memory_id"], "stored": False, "fact": fact}
        cur = conn.execute(
            "INSERT INTO memory (person_id, ts, category, fact, source) VALUES (?, ?, ?, ?, ?)",
            (person_id, clock.iso(clock.now()), category, fact, source),
        )
        return {"memory_id": int(cur.lastrowid), "stored": True, "fact": fact}


def _terms(query: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9']+", (query or "").lower()) if len(w) > 2 and w not in _STOPWORDS]


def recall(person_id: str, query: str = "", limit: int = 5) -> list[dict]:
    """Facts about the person (insights are kept separately: see insights())."""
    limit = max(1, min(int(limit or 5), 20))
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT {_COLUMNS} FROM memory WHERE person_id = ? AND category != 'insight' "
            "ORDER BY ts DESC, memory_id DESC",
            (person_id,),
        )]
    terms = _terms(query)
    if not terms:
        return rows[:limit]
    scored = []
    for row in rows:
        text = row["fact"].lower()
        score = sum(1 for t in terms if t in text)
        if score:
            scored.append((score, row))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [row for _, row in scored[:limit]]


def insights(person_id: str, limit: int = 8) -> list[dict]:
    """Long-term patterns about the person, newest first."""
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM memory WHERE person_id = ? AND category = 'insight' "
            "ORDER BY ts DESC, memory_id DESC LIMIT ?",
            (person_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def followups(person_id: str, limit: int = 2) -> list[dict]:
    """Things worth asking about at a check-in: 'upcoming' facts from an earlier
    day (a plan mentioned this afternoon hasn't happened by this evening)."""
    before = clock.iso(clock.start_of_day())
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM memory WHERE person_id = ? AND category = 'upcoming' AND ts < ? "
            "ORDER BY ts DESC, memory_id DESC LIMIT ?",
            (person_id, before, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def since(person_id: str, since_iso: str) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM memory WHERE person_id = ? AND ts >= ? AND category != 'insight' ORDER BY ts",
            (person_id, since_iso),
        ).fetchall()
    return [dict(r) for r in rows]
