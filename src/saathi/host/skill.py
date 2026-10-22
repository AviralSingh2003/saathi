"""Load the Saathi Agent Skill and build the host's system prompt from it.

Strands can load skills natively (AgentSkills plugin), but a skill is only
activated when the model chooses to. Guardrails must always apply, so the
SKILL.md body is also used as the system prompt.
"""

from __future__ import annotations

from saathi.config import REPO_ROOT

SKILLS_DIR = REPO_ROOT / "skills"
SKILL_FILE = SKILLS_DIR / "saathi-companion" / "SKILL.md"


def skill_body() -> str:
    text = SKILL_FILE.read_text(encoding="utf-8")
    if text.startswith("---"):
        _, _, rest = text.partition("---")
        _, _, text = rest.partition("---")
    return text.strip()


def knowledge_block(context: dict) -> str:
    """What Saathi has learned about the person: refreshed before every turn, so it grows over time."""
    name = context["display_name"]
    knowledge = context.get("knowledge") or {}
    seen, facts = set(), []
    for m in (knowledge.get("relevant_memories") or []) + (context.get("recent_memories") or []):
        if m["memory_id"] not in seen and m.get("category") != "past":
            seen.add(m["memory_id"])
            facts.append(m["fact"])
    lines = [f"## What you know about {name}"]
    if knowledge.get("insights"):
        lines.append("Insights you've built up:")
        lines += [f"- {text}" for text in knowledge["insights"]]
    if facts:
        lines.append("Things they've told you (most relevant first):")
        lines += [f"- {fact}" for fact in facts[:8]]
    today = knowledge.get("today") or {}
    if today.get("day_impression") or today.get("impressions"):
        lines.append("How they've seemed today: " + (today.get("day_impression") or "; ".join(today["impressions"])))
    engagement = today.get("engagement") or {}
    if engagement.get("index") is not None:
        lines.append(f"Engagement today vs their usual (1.0 = usual): {engagement['index']}.")
    for past in knowledge.get("recent_days") or []:
        lines.append(f"On {past['day']}: {past['text']}")
    if len(lines) == 1:
        lines.append("Nothing yet; you're just getting to know them.")
    lines.append("Use this to be warm and personal. Bring things up naturally; never recite this list.")
    return "\n".join(lines)


def system_prompt(context: dict) -> str:
    contacts = context.get("contacts", [])
    contact_lines = "\n".join(
        f"- {c['name']} ({c['relationship']}), contact id '{c['contact_id']}'"
        + ("" if c["receives_updates"] else " — has not opted in to updates")
        for c in contacts
    ) or "- none"
    return (
        f"{skill_body()}\n\n"
        "## This session\n"
        f"You are speaking with {context['display_name']}. Their person_id is '{context['person_id']}'; "
        "use it for every tool that takes person_id.\n"
        f"Family contacts:\n{contact_lines}\n"
        "You are running in a simulated Alexa+ experience; the person hears your replies spoken aloud.\n\n"
        f"{knowledge_block(context)}"
    )
