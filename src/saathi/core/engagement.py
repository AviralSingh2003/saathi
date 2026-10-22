"""Engagement: a better signal than word count for "quieter / more talkative than usual".

Raw word counts mostly measure which questions Saathi happened to ask. Instead each
turn (confirmations and commands don't count) gives four signals:

- elaboration: content words when replying to one of Saathi's open questions
- initiative: brought something up themselves (not a reply to a question, 3+ content words)
- disclosure: shared something about their life (people, plans, names, or a memory was kept)
- reciprocity: asked Saathi something

Each day's value per signal is compared with the person's own median over the previous
14 days (ratio clipped to 0-2). The engagement index is the mean of those ratios.
Only counts are stored, never the words.

Live, the index and its parts are signals for Claude's impression; offline, the index
alone decides "quieter / more talkative than usual".
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from datetime import timedelta

from saathi.core import capture, clock, db

BASELINE_DAYS = 14
MIN_TURNS = 3
QUIETER, CHATTIER = 0.6, 1.4
SIGNALS = ("elaboration", "initiative", "disclosure", "reciprocity")

_STOPWORDS = frozenset(
    "a an the and or but so of to in on at by for with from about as is are was were be been am i me my mine "
    "you your he him his she her they them their we us our it its this that these those there here then than "
    "do does did done have has had will would can could should just very really quite not no yes much many "
    "some any all also too oh ah um uh well ok okay".split()
)
_CONFIRMATION = re.compile(
    r"^(?:yes|yeah|yep|yup|sure|ok|okay|no|nope|not now|please|thanks|thank you|haan|nahi|right|correct)"
    r"(?:[\s,]+(?:please|thanks|thank you|go ahead|do it))?[\s.!,]*$"
)
_OFFER = re.compile(r"\b(?:shall i|should i|would you like|do you want|is that right|just to check)\b")
_ASKS = re.compile(r"^(?:what|when|where|who|why|how|which|do|does|did|is|are|can|could|will|would|have|has)\b")
_NAME = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-z]{2,}")


def content_words(text: str) -> int:
    return sum(1 for t in re.findall(r"[a-z0-9']+", text.lower()) if t not in _STOPWORDS)


def _open_question(reply: str | None) -> bool:
    """Saathi's last line was an open question (not a yes/no offer or read-back)."""
    if not reply or not reply.strip().endswith("?"):
        return False
    last = re.split(r"(?<=[.!?])\s+", reply.strip())[-1].lower()
    return not _OFFER.search(last)


def turn_signals(utterance: str, previous_reply: str | None = None) -> dict | None:
    """Signals for one turn, or None if the turn doesn't count (a confirmation or a command)."""
    text = (utterance or "").strip()
    low = text.lower()
    if not text or text.startswith("[event]") or _CONFIRMATION.match(low) or capture.is_request(text):
        return None
    words = content_words(text)
    after_question = bool(previous_reply and previous_reply.strip().endswith("?"))
    return {
        "content_words": words,
        "replied_to_open_q": _open_question(previous_reply),
        "initiated": not after_question and words >= 3,
        "disclosed": capture.mentions_life(text) or bool(_NAME.search(text)),
        "asked_q": text.endswith("?") or bool(_ASKS.match(low)),
    }


def record(person_id: str, signals: dict) -> int:
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO turn_stat (person_id, ts, content_words, replied_to_open_q, initiated, disclosed, asked_q) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (person_id, clock.iso(clock.now()), signals["content_words"], int(signals["replied_to_open_q"]),
             int(signals["initiated"]), int(signals["disclosed"]), int(signals["asked_q"])),
        )
        return int(cur.lastrowid)


def mark_disclosed(stat_id: int) -> None:
    """A memory was kept from this turn, so it shared something about their life."""
    with db.connect() as conn:
        conn.execute("UPDATE turn_stat SET disclosed = 1 WHERE stat_id = ?", (stat_id,))


def _day_values(rows: list) -> dict:
    replies = [r["content_words"] for r in rows if r["replied_to_open_q"]]
    return {
        "elaboration": statistics.median(replies) if replies else None,
        "initiative": sum(r["initiated"] for r in rows) / len(rows),
        "disclosure": sum(r["disclosed"] for r in rows) / len(rows),
        "reciprocity": sum(r["asked_q"] for r in rows) / len(rows),
    }


def today(person_id: str) -> dict:
    """Today's engagement against the person's own baseline."""
    start = clock.start_of_day()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM turn_stat WHERE person_id = ? AND ts >= ? ORDER BY ts",
            (person_id, clock.iso(start - timedelta(days=BASELINE_DAYS))),
        ).fetchall()
    cutoff = clock.iso(start)
    todays = [r for r in rows if r["ts"] >= cutoff]
    history: dict[str, list] = defaultdict(list)
    for r in rows:
        if r["ts"] < cutoff:
            history[r["ts"][:10]].append(r)
    result = {"turns": len(todays), "baseline_days": len(history), "index": None, "label": None, "signals": {}}
    if len(todays) < MIN_TURNS or not history:
        return result

    now_values = _day_values(todays)
    past = [_day_values(day_rows) for day_rows in history.values()]
    ratios = []
    for name in SIGNALS:
        usual_values = [p[name] for p in past if p[name] is not None]
        value = now_values[name]
        if value is None or not usual_values:
            continue
        usual = statistics.median(usual_values)
        if usual == 0 and value == 0:
            continue  # nothing to compare
        ratio = 2.0 if usual == 0 else min(2.0, value / usual)
        ratios.append(ratio)
        result["signals"][name] = {"today": round(value, 2), "usual": round(usual, 2), "ratio": round(ratio, 2)}
    if ratios:
        index = sum(ratios) / len(ratios)
        result["index"] = round(index, 2)
        if index < QUIETER:
            result["label"] = "was quieter than usual"
        elif index > CHATTIER:
            result["label"] = "was more talkative than usual"
    return result
