"""Reflection after each turn: what to remember, how the person seemed, what Saathi is learning.

Live: one Claude call (Bedrock Converse). Its system prompt carries the guardrails; the
model decides what to remember (in its own words, updating old memories), how the
person came across (free text, its own vocabulary), a running read on today, and
long-term insights it revises over time, and whether family should hear about a
concern today (a short text). The server enforces only the JSON shape and lengths, a
small safety net against diagnostic or clinical language, and a rate limit on texts.

Offline (no model): verbatim fact capture (capture.py), a few rule-based cues and
the engagement index stand in, so the demo runs without credentials.
"""

from __future__ import annotations

import json
import logging
import re

from saathi import config
from saathi.core import capture, clock, engagement, impressions, memory, notify, people

log = logging.getLogger(__name__)

# Offline only: turns whose words were already consumed as structured content
# (a reminder, a reply to family, a self-reported feeling…) aren't mined for facts.
MEMORY_SKIP_TOOLS = frozenset({
    "remember", "set_reminder", "log_mood_selfreport", "reply_to_family_note",
    "send_family_update", "update_settings",
})
MAX_ITEMS = 3
OFFLINE_CONCERNS = ("seemed a little low", "seemed worried", "was quieter than usual")

SYSTEM_PROMPT = """You are the reflective memory of Saathi, a warm voice companion for an older person who \
lives alone. After each turn of conversation you update what Saathi knows about them. What you write is used \
to make Saathi more personal and empathetic, and it is shown to the person's family on their dashboard, in \
updates and in an evening summary. The person themselves doesn't see your impressions.

Return ONLY a JSON object:
{"memories": [{"fact": "...", "category": "family|upcoming|preference|general|health", "replaces": null}],
 "impressions": [{"text": "...", "aspect": "...", "confidence": 0.0}],
 "day_impression": "..." or null,
 "insights": [{"text": "...", "replaces": null}],
 "concern": {"flag": false, "note": ""}}

memories: what a caring friend would remember: the people in their life and what's happening with them, plans \
and upcoming events (category "upcoming"), likes, routines, stories they tell, things that matter to them. Write \
each briefly in your own words, in the first person as they would say it ("My granddaughter Diya starts college \
next week"). If it updates something already known, set "replaces" to that memory_id instead of adding a \
near-duplicate. Skip what's already known, small talk, requests to Saathi, and anything secret (passwords, money, \
bank details, addresses) or private about other people. Usually zero to two.

impressions: how they came across in this turn: mood, energy, engagement, warmth, worry, pride, nostalgia, \
restlessness, whatever fits. Use natural, specific, nuanced language ("seemed quietly proud of Arjun", "sounded a \
little flat this morning", "came across as restless, mentioning the time twice"). Always hedge with seemed, \
sounded or came across as. Base them on what they said, how they said it, how they engaged, and the engagement \
signals given (measured against their own usual). Don't over-read a single word; if the turn tells you little, \
return none. confidence is how sure you are (0-1). Keep self-reports separate: "said she feels tired" is theirs; \
"seemed tired" is yours.

day_impression: one or two sentences on how they've seemed today overall, starting with their name, written \
kindly for their family ("Meera seemed cheerful this morning, especially about the cricket, though a little \
tired by the afternoon."). Revise it as the day goes on; null if there's nothing to say yet.

insights: longer-term patterns you're fairly sure of across days ("Lights up whenever her grandchildren come \
up", "Tends to be quieter on Monday mornings"). Revise an existing insight with "replaces" rather than adding a \
similar one. Usually none; at most one per turn.

concern: set flag true only when their family would want to know today, not tomorrow: they seem low, \
worried, unusually withdrawn or not themselves, or something in their life seems wrong. It is NOT for \
emergencies (those are handled separately and instantly). note is one or two short sentences for a text \
message to family, starting with their name, kind and specific, ending with a gentle suggestion ("Raj seemed \
much quieter than usual this morning and didn't mention his walk. You might give him a call."). Most turns: \
{"flag": false, "note": ""}.

Guardrails: you are a companion, not a clinician. Never diagnose. Never use clinical, psychiatric or cognitive \
terms (depressed, dementia, cognitive decline, confused, disorder and the like), never speculate about memory or \
cognition, and never give medical advice. Be kind and fair, because family will read this; never alarmist, never \
mocking. Don't invent details."""


def _clean_turns(recent_turns: list | None) -> list[dict]:
    out = []
    for turn in (recent_turns or [])[-12:]:
        if isinstance(turn, dict) and isinstance(turn.get("text"), str) and turn["text"].strip():
            out.append({"role": str(turn.get("role", "person"))[:10], "text": turn["text"].strip()[:600]})
    return out


def _context_text(person: dict, utterance: str, turns: list[dict], signals: dict | None,
                  eng: dict, tools: set[str]) -> str:
    pid, name = person["person_id"], person["display_name"]
    now = clock.now()
    known = {m["memory_id"]: m for m in memory.recall(pid, utterance, 6) + memory.recall(pid, "", 6)}
    lines = [f"Person: {name}", f"Now: {now.strftime('%A %d %B, %H:%M')}"]
    lines.append("Known memories (memory_id: fact):")
    lines += [f"- {m['memory_id']}: {m['fact']}" for m in known.values()] or ["- none yet"]
    lines.append("Insights so far (memory_id: text):")
    lines += [f"- {m['memory_id']}: {m['fact']}" for m in memory.insights(pid)] or ["- none yet"]
    today = impressions.on_day(pid)
    if today:
        lines.append("Impressions earlier today: " + "; ".join(i["text"] for i in today[-6:]))
    if (day := impressions.day_text(pid)):
        lines.append(f"Today so far: {day}")
    for past in impressions.recent_days(pid, 3):
        lines.append(f"On {past['day']}: {past['text']}")
    if eng.get("index") is not None:
        parts = ", ".join(f"{k} {v['ratio']}" for k, v in eng["signals"].items())
        lines.append(f"Engagement today vs their own usual (1.0 = usual): index {eng['index']} ({parts}); "
                     f"{eng['turns']} turns so far.")
    else:
        lines.append(f"Engagement today: {eng.get('turns', 0)} turns so far (not enough to compare yet).")
    if signals:
        lines.append(
            "This turn: " + ", ".join(filter(None, [
                f"{signals['content_words']} content words",
                "answered an open question" if signals["replied_to_open_q"] else None,
                "brought something up themselves" if signals["initiated"] else None,
                "asked Saathi something" if signals["asked_q"] else None,
            ])))
    if tools:
        lines.append("Saathi already used these tools this turn: " + ", ".join(sorted(tools)))
    if turns:
        lines.append("Conversation so far (most recent last):")
        lines += [f"{'Saathi' if t['role'] == 'saathi' else name}: {t['text']}" for t in turns]
    lines.append(f"\nLatest message from {name}:\n{utterance}")
    return "\n".join(lines)


def _bedrock_reflect(context: str) -> dict:
    import boto3

    settings = config.get()
    client = boto3.client("bedrock-runtime", region_name=settings.aws_region)
    response = client.converse(
        modelId=settings.bedrock_model_id,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": context}]}],
        inferenceConfig={"maxTokens": 700, "temperature": 0.3},
    )
    raw = " ".join(b["text"] for b in response["output"]["message"]["content"] if "text" in b).strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    if not isinstance(data, dict):
        raise ValueError("reflection is not a JSON object")
    return data


def _as_id(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _confidence(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.5


def _apply_live(person: dict, data: dict) -> dict:
    pid = person["person_id"]
    remembered, observed, insights, rejected = [], [], [], []
    day = None
    for item in (data.get("memories") or [])[:MAX_ITEMS]:
        fact = str((item or {}).get("fact", "")).strip() if isinstance(item, dict) else ""
        if not 1 <= len(fact) <= 300:
            rejected.append({"text": fact[:80], "reason": "empty or too long"})
            continue
        category = item.get("category") if item.get("category") in memory.CATEGORIES else "general"
        if category == "insight":
            category = "general"
        result = memory.remember(pid, fact.rstrip("."), category, source="auto",
                                 replaces=_as_id(item.get("replaces")))
        if result["stored"]:
            remembered.append({"memory_id": result["memory_id"], "fact": result["fact"], "category": category})
    for item in (data.get("impressions") or [])[:MAX_ITEMS]:
        text = str((item or {}).get("text", "")).strip() if isinstance(item, dict) else ""
        if not 1 <= len(text) <= 200:
            rejected.append({"text": text[:80], "reason": "empty or too long"})
            continue
        if (bad := impressions.clinical_terms(text)):
            rejected.append({"text": text, "reason": f"clinical language ({', '.join(bad)})"})
            continue
        aspect = str(item.get("aspect") or "mood").strip()[:30] or "mood"
        observed.append(impressions.add(pid, text, aspect, _confidence(item.get("confidence")), "bedrock"))
    candidate = data.get("day_impression")
    if isinstance(candidate, str) and candidate.strip():
        candidate = candidate.strip()[:400]
        if (bad := impressions.clinical_terms(candidate)):
            rejected.append({"text": candidate, "reason": f"clinical language ({', '.join(bad)})"})
        else:
            impressions.set_day(pid, candidate)
            day = candidate
    for item in (data.get("insights") or [])[:1]:
        text = str((item or {}).get("text", "")).strip() if isinstance(item, dict) else ""
        if not 1 <= len(text) <= 200 or impressions.clinical_terms(text):
            rejected.append({"text": text[:80], "reason": "insight not kept (length or clinical language)"})
            continue
        result = memory.remember(pid, text.rstrip("."), "insight", source="auto",
                                 replaces=_as_id(item.get("replaces")))
        if result["stored"]:
            insights.append({"memory_id": result["memory_id"], "text": result["fact"]})
    concern = data.get("concern") if isinstance(data.get("concern"), dict) else {}
    note = str(concern.get("note") or "").strip()[:240]
    if concern.get("flag") and note:
        if impressions.clinical_terms(note):
            rejected.append({"text": note, "reason": "concern note not sent (clinical language)"})
            note = ""
    else:
        note = ""
    return {"remembered": remembered, "impressions": observed, "day_impression": day,
            "insights": insights, "rejected": rejected, "concern": note or None}


def _apply_offline(person: dict, utterance: str, tools: set[str]) -> dict:
    pid = person["person_id"]
    remembered, rejected = [], []
    if not tools & MEMORY_SKIP_TOOLS:
        captured = capture.capture(pid, utterance)
        remembered, rejected = captured["stored"], captured["rejected"]
    observed = [impressions.add(pid, cue["text"], cue["aspect"], 0.6, "rules")
                for cue in impressions.offline_cues(utterance)]
    return {"remembered": remembered, "impressions": observed, "day_impression": None,
            "insights": [], "rejected": rejected, "concern": None}


def _offline_concern(person: dict, observed: list[dict]) -> str | None:
    """Offline stand-in for Claude's concern flag: a low/worried/quiet impression this turn."""
    for item in observed:
        if item["text"] in OFFLINE_CONCERNS:
            part = clock.now().hour
            when = "this morning" if part < 12 else "this afternoon" if part < 17 else "this evening"
            return f"{person['display_name']} {item['text']} {when}. You might want to check in."
    return None


def _offline_after_engagement(person: dict, eng: dict, result: dict) -> None:
    """Offline: today's measured engagement, today's summary line and long-term patterns."""
    pid = person["person_id"]
    before = [i["text"] for i in impressions.on_day(pid) if i["aspect"] == "engagement"]
    impressions.set_engagement(pid, eng.get("label"))
    if eng.get("label") and before[-1:] != [eng["label"]]:
        result["impressions"].append({"text": eng["label"], "aspect": "engagement", "method": "metric"})
    day = impressions.offline_day_text(person["display_name"], impressions.on_day(pid))
    if day:
        impressions.set_day(pid, day)
        result["day_impression"] = day
    result["concern"] = _offline_concern(person, result["impressions"])
    existing = memory.insights(pid, 20)
    for pattern in impressions.patterns(pid):
        prefix = pattern.split(" lately")[0]
        match = next((m for m in existing if m["fact"].startswith(prefix)), None)
        if match and match["fact"] == pattern:
            continue
        stored = memory.remember(pid, pattern, "insight", source="auto",
                                 replaces=match["memory_id"] if match else None)
        result["insights"].append({"memory_id": stored["memory_id"], "text": stored["fact"]})


def reflect(person_id: str, utterance: str, previous_reply: str | None = None,
            recent_turns: list | None = None, tools_called: list[str] | None = None) -> dict:
    person = people.get_person(person_id)
    utterance = (utterance or "").strip()
    if not utterance or utterance.startswith("[event]"):
        return {"remembered": [], "impressions": [], "day_impression": impressions.day_text(person_id),
                "insights": [], "engagement": None, "rejected": [], "method": "none",
                "note": "nothing to reflect on"}
    tools = set(tools_called or ())
    signals = engagement.turn_signals(utterance, previous_reply)
    stat_id = engagement.record(person_id, signals) if signals else None
    eng = engagement.today(person_id)

    method, result = "rules", None
    if config.get().live_bedrock:
        try:
            context = _context_text(person, utterance, _clean_turns(recent_turns), signals, eng, tools)
            result, method = _apply_live(person, _bedrock_reflect(context)), "bedrock"
        except Exception as exc:  # network/credentials/parse errors fall back safely
            log.warning("Bedrock reflection failed (%s); using rules", exc)
            method = "rules (bedrock unavailable)"
    if result is None:
        result = _apply_offline(person, utterance, tools)

    if stat_id and result["remembered"]:
        engagement.mark_disclosed(stat_id)
        eng = engagement.today(person_id)
    if not method.startswith("bedrock"):
        _offline_after_engagement(person, eng, result)
    concern = result.pop("concern", None)
    sent = notify.concern(person_id, concern) if concern else None
    result.update({"engagement": eng, "method": method, "note": None,
                   "concern_sent": concern if sent else None})
    if result["day_impression"] is None:
        result["day_impression"] = impressions.day_text(person_id)
    return result
