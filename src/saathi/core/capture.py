"""Automatic memory: keep concrete facts from what the person says.

The host calls this after every turn with the person's exact words, so memory
doesn't depend on the conversational model choosing to call `remember`.

Rules (the same in live and offline mode):
- **Select, never write.** A fact is kept only if it is copied verbatim from the
  person's words. In live mode Claude picks the spans; offline, rules do. Either
  way the server checks every candidate against the utterance.
- **Never feelings, never health.** Mood words and health/medical details are
  never auto-saved (feelings are recorded only via log_mood_selfreport, when the
  person states one).
- At most three facts per turn; exact duplicates are ignored.
"""

from __future__ import annotations

import json
import logging
import re

from saathi import config
from saathi.core import memory, people, summary

log = logging.getLogger(__name__)

MAX_FACTS = 3
MIN_WORDS = 4

_RELATION = re.compile(
    r"\b(son|daughter|grandson|granddaughter|grandchild(?:ren)?|grandkids?|husband|wife|sister|brother|"
    r"friend|neighbou?r|niece|nephew|cousin|mother|father|mum|mom|dad|son-in-law|daughter-in-law)s?\b"
)
_DAYS = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
_FUTURE = re.compile(
    rf"\b(tomorrow|tonight|this (?:evening|weekend|week)|next (?:week|month|year|{_DAYS})|"
    rf"on {_DAYS}|this {_DAYS}|in (?:a|two|three|\d+) (?:days?|weeks?|months?)|"
    r"is coming|are coming|will visit|is visiting|are visiting|starts|is starting)\b"
)
_PREFERENCE = re.compile(r"\bi (?:really |do )?(?:love|like|enjoy|prefer|hate|dislike|adore)\b|\bmy favou?rite\b")
_HEALTH = re.compile(
    r"\b(pain|painful|ache|aches|aching|hurts?|sore|doctor|hospital|clinic|medicine|medication|tablets?|pills?|"
    r"bp|blood pressure|sugar|diabetes|diabetic|insulin|surgery|operation|injury|injured|fell|fall|dizzy|sick|ill|"
    r"fever|cough|cold|heart|chest|breath|breathing|cancer|stroke|dementia|memory loss|prescription|therapy)\b"
)
_COMMAND = re.compile(
    r"^(?:yes|yeah|yep|no|nope|okay|ok|sure|please|remind|tell|send|let|what|any|done|remember|stop|start|"
    r"cancel|thanks|thank you|hi|hello|good (?:morning|afternoon|evening|night))\b"
)


def _normalise(text: str) -> str:
    text = text.replace("’", "'").replace("“", '"').replace("”", '"').lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .,!?;:\"'")


def is_request(text: str) -> bool:
    """A command or confirmation to Saathi ("remind me…", "yes", "tell her…"), not something about their life."""
    return bool(_COMMAND.match(_normalise(text)))


def mentions_life(text: str) -> bool:
    """Mentions people in their life or plans (used by the engagement index)."""
    low = _normalise(text)
    return bool(_RELATION.search(low) or _FUTURE.search(low))


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def disqualified(fact: str) -> str | None:
    """Why a candidate fact must not be stored (None = allowed)."""
    low = _normalise(fact)
    words = low.split()
    if len(words) < MIN_WORDS:
        return "too short"
    if fact.strip().endswith("?"):
        return "a question"
    if _COMMAND.match(low):
        return "a request, not a fact"
    if _HEALTH.search(low):
        return "health details are never auto-saved"
    mood = set(re.findall(r"[a-z']+", low)) & summary.BLOCKLIST
    if mood or re.search(r"\bi(?:'m| am) feeling\b|\bi feel\b", low):
        return "feelings are never auto-saved"
    return None


def _category(fact: str) -> str:
    low = _normalise(fact)
    if _FUTURE.search(low):
        return "upcoming"
    if _RELATION.search(low):
        return "family"
    if _PREFERENCE.search(low):
        return "preference"
    return "general"


def offline_candidates(utterance: str) -> list[dict]:
    """Rule-based selection: sentences about people, plans or likes."""
    out = []
    for sentence in _sentences(utterance):
        low = _normalise(sentence)
        if _RELATION.search(low) or _FUTURE.search(low) or _PREFERENCE.search(low):
            out.append({"text": sentence, "category": _category(sentence)})
    return out


_SYSTEM = (
    "You select facts worth remembering from what an older person just said to their companion. "
    "Return only JSON: {\"facts\": [{\"text\": \"...\", \"category\": \"family|upcoming|preference|general\"}]}. "
    "Each text MUST be copied exactly, word for word, from the message — never rephrase, summarise or combine. "
    "Keep only lasting, concrete facts a caring friend would remember: people in their life, plans and "
    "upcoming events, hobbies and things they like or dislike. Never select feelings or moods, health or "
    "medical details, money, addresses, passwords, or private matters about other people. "
    "Questions and requests are not facts. If nothing qualifies, return {\"facts\": []}. At most 3 facts."
)


def _bedrock_candidates(utterance: str) -> list[dict]:
    import boto3

    settings = config.get()
    client = boto3.client("bedrock-runtime", region_name=settings.aws_region)
    response = client.converse(
        modelId=settings.bedrock_model_id,
        system=[{"text": _SYSTEM}],
        messages=[{"role": "user", "content": [{"text": f"Message:\n{utterance}"}]}],
        inferenceConfig={"maxTokens": 300, "temperature": 0},
    )
    raw = " ".join(b["text"] for b in response["output"]["message"]["content"] if "text" in b).strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    facts = data.get("facts", []) if isinstance(data, dict) else []
    return [f for f in facts if isinstance(f, dict) and isinstance(f.get("text"), str)]


def capture(person_id: str, utterance: str) -> dict:
    """Store verbatim facts from the person's words. Returns what was stored and why others weren't."""
    people.get_person(person_id)
    utterance = (utterance or "").strip()
    if not utterance or utterance.startswith("[event]"):
        return {"stored": [], "rejected": [], "method": "none", "note": "nothing to capture"}

    method = "rules"
    settings = config.get()
    if settings.live_bedrock:
        try:
            candidates, method = _bedrock_candidates(utterance), "bedrock"
        except Exception as exc:  # network/credentials/parse errors fall back safely
            log.warning("Bedrock fact selection failed (%s); using rules", exc)
            candidates, method = offline_candidates(utterance), "rules (bedrock unavailable)"
    else:
        candidates = offline_candidates(utterance)

    source = _normalise(utterance)
    stored, rejected = [], []
    for candidate in candidates[: MAX_FACTS * 2]:
        text = candidate["text"].strip()
        if _normalise(text) not in source:
            rejected.append({"text": text, "reason": "not word-for-word from what the person said"})
            continue
        reason = disqualified(text)
        if reason:
            rejected.append({"text": text, "reason": reason})
            continue
        if len(stored) >= MAX_FACTS:
            break
        category = candidate.get("category") if candidate.get("category") in memory.CATEGORIES else _category(text)
        if category == "health":
            rejected.append({"text": text, "reason": "health details are never auto-saved"})
            continue
        result = memory.remember(person_id, text.rstrip("."), category, source="auto")
        if result["stored"]:
            stored.append({"memory_id": result["memory_id"], "fact": result["fact"], "category": category})
    return {"stored": stored, "rejected": rejected, "method": method, "note": None}
