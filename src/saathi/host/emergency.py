"""Emergency layer 2: a fast Claude (Haiku) check on every turn the phrase list didn't catch.

Runs in parallel with the conversation, with a hard time limit (EMERGENCY_CHECK_TIMEOUT,
10 s by default). It only classifies; it never writes messages or decides what to say.
The host acts on the result (host/agent.py):
- medical emergency, confident  -> emergency services first, then family
- self-harm / asked for help    -> urgent text + email to family
- medical but unsure            -> Saathi asks "Are you okay?", then escalates
- timeout or error              -> the same "ask, then escalate" path
"""

from __future__ import annotations

import json
import re

from saathi import config

TYPES = ("medical_emergency", "self_harm", "asked_for_help", "none")
DISPATCH_AT = 0.6     # medical_emergency at or above this: contact emergency services now
ASK_AT = 0.35         # medical_emergency between ASK_AT and DISPATCH_AT: ask first
NOTIFY_AT = 0.5       # self_harm / asked_for_help at or above this: urgent family alert

SYSTEM_PROMPT = """You are the emergency check inside Saathi, a voice companion for an older person who \
lives alone. You see their latest words (and a little context). Decide ONLY whether they need urgent help now.

Return ONLY JSON: {"type": "medical_emergency|self_harm|asked_for_help|none", "confidence": 0.0-1.0, \
"summary": "<= 12 words"}

medical_emergency: anything that may need an ambulance now. Heart attack signs (chest pain, tightness, \
pressure, pain spreading to arm/jaw/back, cold sweat with breathlessness); stroke signs (face drooping, arm or \
leg weakness or numbness, slurred or muddled speech, sudden severe headache, sudden loss of vision or balance); \
can't breathe; a fall with injury or being unable to get up; collapse, fainting, unresponsive person nearby; \
heavy bleeding; overdose or taking the wrong or too many medicines; severe allergic reaction; seizures.
self_harm: thoughts of ending their life or harming themselves, even indirect ("everyone would be better off").
asked_for_help: they ask for help or for someone to come, without a clear medical emergency.
none: everything else, including ordinary aches ("my knee hurts"), past events that are over and dealt with, \
talking about someone else's past illness, figures of speech ("I nearly died laughing"), and "I fell asleep".

Older people often understate ("I feel a bit funny, my arm's gone heavy"); treat that seriously. Understand any \
language or mix of languages (e.g. Hindi: "seene mein dard ho raha hai"). When unsure about a possible medical \
emergency, use medical_emergency with a moderate confidence rather than none."""


def assess(utterance: str, recent_turns: list[dict] | None = None) -> dict:
    """Classify one utterance with Claude on Bedrock. Raises on any failure (the caller decides)."""
    import boto3
    from botocore.config import Config

    settings = config.get()
    timeout = max(1, int(settings.emergency_check_timeout))
    client = boto3.client("bedrock-runtime", region_name=settings.aws_region,
                          config=Config(connect_timeout=timeout, read_timeout=timeout, retries={"max_attempts": 1}))
    context = "\n".join(f"{'Saathi' if t['role'] == 'saathi' else 'Them'}: {t['text']}"
                        for t in (recent_turns or [])[-4:])
    message = (f"Recent conversation:\n{context}\n\n" if context else "") + f"Latest words:\n{utterance}"
    response = client.converse(
        modelId=settings.bedrock_model_id,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": message}]}],
        inferenceConfig={"maxTokens": 80, "temperature": 0},
    )
    raw = " ".join(b["text"] for b in response["output"]["message"]["content"] if "text" in b)
    return parse(raw)


def parse(raw: str) -> dict:
    data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    kind = data.get("type") if data.get("type") in TYPES else "none"
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0.0
    summary = re.sub(r"\s+", " ", str(data.get("summary", "")))[:120]
    return {"type": kind, "confidence": confidence, "summary": summary}


def decide(verdict: dict | None) -> str:
    """What the host should do: 'dispatch' | 'self_harm' | 'asked_for_help' | 'ask' | 'none'.
    None (timeout or error) means 'ask'."""
    if verdict is None:
        return "ask"
    kind, confidence = verdict["type"], verdict["confidence"]
    if kind == "medical_emergency":
        if confidence >= DISPATCH_AT:
            return "dispatch"
        return "ask" if confidence >= ASK_AT else "none"
    if kind in ("self_harm", "asked_for_help") and confidence >= NOTIFY_AT:
        return kind
    return "none"
