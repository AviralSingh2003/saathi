"""Impressions: how the person seemed in conversation.

Live, Claude writes these in its own words (see reflect.py). Offline, a handful
of rule-based cues and the engagement index stand in so the demo runs without a
model. Either way they're hedged ("seemed…"), and anything that reads like a
diagnosis is never stored: Saathi is a companion, not a clinician.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, time, timedelta

from saathi.core import clock, db

# Diagnostic, clinical or cognitive language: never stored, never shown to family.
CLINICAL = frozenset({
    "depressed", "depression", "depressive", "dementia", "alzheimer", "alzheimer's", "alzheimers", "cognitive",
    "cognition", "decline", "declining", "confused", "confusion", "disoriented", "delirium", "delirious",
    "forgetful", "disorder", "diagnosis", "diagnosed", "symptom", "symptoms", "suicidal", "psychotic",
    "paranoid", "bipolar", "unwell", "illness", "frail", "clinically", "psychiatric",
})
CLINICAL_PHRASES = ("memory loss", "mental health", "losing her memory", "losing his memory")

# Offline stand-ins for what Claude infers live: (aspect, phrase, cue).
_CUES = (
    ("energy", "seemed a bit tired",
     re.compile(r"\b(tired|exhausted|worn out|sleepy|didn'?t sleep|couldn'?t sleep|hardly slept|long day)\b")),
    ("mood", "seemed a little low",
     re.compile(r"\b(feeling down|bit down|not great|not so good|miss(?:ing)? (?:him|her|them)|lonely|sad)\b")),
    ("mood", "seemed worried",
     re.compile(r"\b(worried|worrying|anxious about|nervous about|concerned about)\b")),
    ("mood", "seemed excited", re.compile(r"\b(excited|can'?t wait|looking forward)\b")),
    ("mood", "seemed cheerful",
     re.compile(r"\b(won|wonderful|lovely|great news|amazing|brilliant|fantastic|delighted|thrilled|so happy|"
                r"very happy|proud|haha)\b")),
)
CONCERN = re.compile(r"\b(tired|low|worried|quieter|flat|down|weary|subdued)\b")


def clinical_terms(text: str) -> list[str]:
    low = (text or "").lower()
    found = sorted(set(re.findall(r"[a-z']+", low)) & CLINICAL)
    return found + [p for p in CLINICAL_PHRASES if p in low]


def offline_cues(utterance: str) -> list[dict]:
    """At most one mood and one energy impression from simple cues."""
    low = (utterance or "").lower().replace("’", "'")
    found, aspects = [], set()
    for aspect, phrase, cue in _CUES:
        if aspect not in aspects and cue.search(low):
            found.append({"aspect": aspect, "text": phrase})
            aspects.add(aspect)
    return found


def add(person_id: str, text: str, aspect: str = "mood", confidence: float = 0.5, method: str = "rules") -> dict:
    ts = clock.iso(clock.now())
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO impression (person_id, ts, text, aspect, confidence, method) VALUES (?, ?, ?, ?, ?, ?)",
            (person_id, ts, text, aspect, confidence, method),
        )
    return {"impression_id": int(cur.lastrowid), "ts": ts, "text": text, "aspect": aspect,
            "confidence": confidence, "method": method}


def on_day(person_id: str, day: date | None = None) -> list[dict]:
    start = clock.start_of_day() if day is None else datetime.combine(day, time())
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT impression_id, ts, text, aspect, confidence, method FROM impression "
            "WHERE person_id = ? AND ts >= ? AND ts < ? ORDER BY ts, impression_id",
            (person_id, clock.iso(start), clock.iso(start + timedelta(days=1))),
        ).fetchall()
    return [dict(r) for r in rows]


def set_engagement(person_id: str, phrase: str | None) -> None:
    """Today's measured engagement (offline): one row, replaced as the day goes on."""
    with db.connect() as conn:
        conn.execute(
            "DELETE FROM impression WHERE person_id = ? AND aspect = 'engagement' AND method = 'metric' AND ts >= ?",
            (person_id, clock.iso(clock.start_of_day())),
        )
    if phrase:
        add(person_id, phrase, "engagement", 0.7, "metric")


def set_day(person_id: str, text: str) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO day_impression (person_id, day, text, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(person_id, day) DO UPDATE SET text = excluded.text, updated_at = excluded.updated_at",
            (person_id, clock.now().date().isoformat(), text, clock.iso(clock.now())),
        )


def day_text(person_id: str, day: date | None = None) -> str | None:
    key = (day or clock.now().date()).isoformat()
    with db.connect() as conn:
        row = conn.execute("SELECT text FROM day_impression WHERE person_id = ? AND day = ?",
                           (person_id, key)).fetchone()
    return row["text"] if row else None


def recent_days(person_id: str, days: int = 7) -> list[dict]:
    """Earlier days' impressions, newest first (today is separate)."""
    today = clock.now().date()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT day, text FROM day_impression WHERE person_id = ? AND day < ? AND day >= ? ORDER BY day DESC",
            (person_id, today.isoformat(), (today - timedelta(days=days)).isoformat()),
        ).fetchall()
    return [dict(r) for r in rows]


def offline_day_text(name: str, todays: list[dict]) -> str | None:
    """'Meera seemed cheerful today.' from today's offline impressions."""
    moods = Counter(i["text"] for i in todays if i["aspect"] == "mood")
    latest = {i["text"]: n for n, i in enumerate(todays)}
    parts = []
    if moods:
        parts.append(max(moods, key=lambda t: (moods[t], latest[t])))
    parts += [i["text"] for i in reversed(todays) if i["aspect"] == "energy"][:1]
    parts += [i["text"] for i in reversed(todays) if i["aspect"] == "engagement"][:1]
    if not parts:
        return None
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"{name} {joined} today."


def patterns(person_id: str, days: int = 14, min_days: int = 3) -> list[str]:
    """Offline long-term patterns: the same impression on several different days."""
    since = clock.start_of_day() - timedelta(days=days - 1)
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT text, substr(ts, 1, 10) AS day FROM impression WHERE person_id = ? AND ts >= ?",
            (person_id, clock.iso(since)),
        ).fetchall()
    per_text = Counter(r["text"] for r in rows)
    out = []
    for text, n in per_text.most_common():
        if n >= min_days:
            phrase = re.sub(r"^was ", "been ", text)
            out.append(f"Has often {phrase} lately ({n} of the last {days} days)")
    return out


def family_view(person_id: str) -> dict:
    from saathi.core import memory

    return {
        "today": day_text(person_id),
        "recent": recent_days(person_id, 7),
        "insights": [m["fact"] for m in memory.insights(person_id, 4)],
        "note": "Saathi's impressions from conversation, not a medical assessment.",
    }
