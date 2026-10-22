"""Turn consented facts into the text of a family update.

Rule: the update states only the facts it's given: completed reminders, feelings
in the person's own words, topics, and Saathi's impression of how they seemed
today (when they share impressions). LLM phrasing (live mode) may not add mood or
behaviour words that aren't in those facts, and never clinical language; on any
violation we fall back to the template.
"""

from __future__ import annotations

import logging
import re

from saathi import config
from saathi.core import clock, impressions, memory, people, reminders

log = logging.getLogger(__name__)

# Words that describe mood, emotion, health or behaviour. Allowed only when they
# come from the facts (a self-report, or today's impression).
BLOCKLIST = frozenset({
    "happy", "happier", "cheerful", "cheery", "upbeat", "joyful", "content", "chatty", "talkative",
    "sad", "unhappy", "down", "low", "lonely", "alone", "isolated", "depressed", "depression",
    "anxious", "anxiety", "worried", "nervous", "stressed", "upset", "angry", "irritable",
    "confused", "confusion", "forgetful", "tired", "exhausted", "sleepy", "quiet", "withdrawn",
    "agitated", "distressed", "fine", "well", "unwell", "sick", "ill", "healthy", "struggling",
    "mood", "spirits", "energetic", "frail", "weak", "sounded", "seemed", "seems",
})


def collect_facts(person: dict, contact: dict) -> list[dict]:
    """Today's facts: reminders done, what they said they feel, what they talked about, how they seemed."""
    since = clock.iso(clock.start_of_day())
    facts: list[dict] = []
    activity = people.activity_since(person["person_id"], since)
    for entry in activity:
        if entry["type"] == "reminder_done":
            text, _, hhmm = entry["detail"].partition("|")
            when = reminders.spoken_time(hhmm) if hhmm else "earlier"
            facts.append({"kind": "reminder_done", "text": text, "time": hhmm, "spoken_time": when})
    moods = [e for e in activity if e["type"] == "mood_selfreport" and e["mood_word"]]
    if moods:
        facts.append({"kind": "mood_selfreport", "word": moods[-1]["mood_word"]})
    for item in memory.since(person["person_id"], since):
        if item["category"] != "past":
            facts.append({"kind": "topic", "text": item["fact"], "source": item.get("source", "explicit")})
    today = impressions.day_text(person["person_id"])
    if today:
        facts.append({"kind": "impression", "text": today})
    return facts


def template_body(person: dict, facts: list[dict]) -> str:
    name = person["display_name"]
    sentences = []
    for fact in facts:
        if fact["kind"] == "reminder_done":
            sentences.append(f'{name} completed the reminder "{fact["text"]}" at {fact["spoken_time"]}.')
        elif fact["kind"] == "mood_selfreport":
            sentences.append(f'{name} described feeling "{fact["word"]}".')
        elif fact["kind"] == "topic":
            asked, _, answer = fact["text"].partition('. Later I said: "')
            if answer:  # a check-in follow-up and what they said about it
                sentences.append(f'Asked about "{asked}", {name} said: "{answer.rstrip(chr(34))}"')
            else:  # memories are kept in the first person, so quote them
                sentences.append(f'{name} said: "{fact["text"].rstrip(".")}."')
        elif fact["kind"] == "impression":
            text = fact["text"].strip()
            sentences.append(text if text[-1:] in ".!?" else text + ".")
    if not sentences:
        sentences.append(f"Nothing new from {name} yet today.")
    return " ".join(sentences)


def allowed_words(facts: list[dict]) -> set[str]:
    words: set[str] = set()
    for fact in facts:
        for value in (fact.get("word"), fact.get("text")):
            if value:
                words |= set(re.findall(r"[a-z']+", value.lower()))
    return words


def blocklist_violations(text: str, facts: list[dict]) -> list[str]:
    """Mood/behaviour words not backed by a fact, plus any clinical language (never allowed)."""
    tokens = set(re.findall(r"[a-z']+", text.lower()))
    return sorted(((tokens & BLOCKLIST) - allowed_words(facts)) | set(impressions.clinical_terms(text)))


def _bedrock_body(person: dict, contact: dict, facts: list[dict]) -> str:
    import boto3

    settings = config.get()
    fact_lines = "\n".join(f"- {_fact_line(f)}" for f in facts)
    system = (
        f"You write one or two plain sentences for {contact['name']} about {person['display_name']}'s day. "
        "Use ONLY the facts provided. If a fact contains a feeling word in quotes, repeat it exactly, in quotes. "
        "If there is an impression of how they seemed today, include it, keeping its hedged wording (seemed, "
        "sounded). Don't add any other description of mood, health, energy or behaviour, and never use clinical "
        "language. No greetings, no sign-off, no advice. "
        f"Refer to the person as {person['display_name']}."
    )
    client = boto3.client("bedrock-runtime", region_name=settings.aws_region)
    response = client.converse(
        modelId=settings.bedrock_model_id,
        system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": f"Facts:\n{fact_lines}"}]}],
        inferenceConfig={"maxTokens": 200, "temperature": 0.2},
    )
    blocks = response["output"]["message"]["content"]
    return " ".join(b["text"] for b in blocks if "text" in b).strip()


def _fact_line(fact: dict) -> str:
    if fact["kind"] == "reminder_done":
        return f'completed reminder "{fact["text"]}" at {fact["spoken_time"]}'
    if fact["kind"] == "mood_selfreport":
        return f'said they are feeling "{fact["word"]}"'
    if fact["kind"] == "impression":
        return f"Saathi's impression of how they seemed today: {fact['text']}"
    return f"talked about: {fact['text']}"


def compose(person: dict, contact: dict, facts: list[dict], extra_note: str | None) -> tuple[str, str]:
    """Return (body, method). The person's own added words are appended verbatim."""
    method = "template"
    body = template_body(person, facts)
    if facts and config.get().live_bedrock:
        try:
            candidate = _bedrock_body(person, contact, facts)
            violations = blocklist_violations(candidate, facts)
            if candidate and not violations:
                body, method = candidate, "bedrock"
            else:
                log.warning("Bedrock summary rejected (blocklist: %s); using template", violations)
                method = "template (bedrock output rejected)"
        except Exception as exc:  # network/credentials/model errors fall back safely
            log.warning("Bedrock summary failed (%s); using template", exc)
            method = "template (bedrock unavailable)"
    if extra_note:
        note = extra_note.strip()
        if note[-1] not in ".!?":
            note += "."
        body = f'{body} {person["display_name"]} asked me to tell you: "{note}"'
    return body, method
