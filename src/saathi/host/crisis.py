"""Emergency layer 1: an instant phrase list, run before any model sees the person's words.

A match produces a direct `escalate_to_contact` call (medical emergencies contact emergency
services first) and a fixed spoken reply. The conversational model is never consulted on
this path. Layer 2, the Haiku check for everything this list misses, is host/emergency.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from saathi import config
from saathi.config import REPO_ROOT

PHRASES_FILE = REPO_ROOT / "config" / "crisis_phrases.txt"
REASONS = ("medical_emergency", "self_harm", "asked_for_help")

SCRIPTS = {
    "medical_emergency": (
        "{lead}I've called emergency services, {name}. Help is on the way. If you can, stay where you are "
        "and keep the door unlocked. I'm letting {contact} know right now."
    ),
    "medical_not_dispatched": (
        "{lead}That sounds serious, {name}. Please call {number} right now if you can. "
        "I'm letting {contact} know straight away so they can reach you."
    ),
    "self_harm": (
        "I'm really glad you told me, {name}. You deserve support right now. "
        "Please call {crisis_line}, or {number} if you're in danger. "
        "I'm letting {contact} know so they can get in touch with you."
    ),
    "asked_for_help": "Okay, {name}. I'm asking {contact} to get in touch with you as soon as they can.",
}
ASK = "Are you okay, {name}? Do you need me to call for help?"
NO_ANSWER_LEAD = "I haven't heard back from you, so "


@dataclass(frozen=True)
class CrisisMatch:
    reason_code: str
    pattern: str


@lru_cache(maxsize=4)
def _load(path: Path) -> tuple[tuple[str, re.Pattern], ...]:
    rules = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        reason, _, pattern = line.partition(":")
        reason, pattern = reason.strip(), pattern.strip()
        if reason in REASONS and pattern:
            rules.append((reason, re.compile(pattern, re.IGNORECASE)))
    return tuple(rules)


def check(text: str, path: Path = PHRASES_FILE) -> CrisisMatch | None:
    normalized = (text or "").replace("’", "'")
    for reason, pattern in _load(path):
        if pattern.search(normalized):
            return CrisisMatch(reason, pattern.pattern)
    return None


def script(reason_code: str, name: str, contact: str | None, dispatched: bool = True, no_answer: bool = False) -> str:
    settings = config.get()
    key = reason_code
    if reason_code == "medical_emergency" and not dispatched:
        key = "medical_not_dispatched"
    lead = NO_ANSWER_LEAD if no_answer else ""
    text = SCRIPTS[key].format(name=name, contact=contact or "your family", number=settings.emergency_number,
                               crisis_line=settings.crisis_line_text, lead=lead)
    return text[0].upper() + text[1:]
