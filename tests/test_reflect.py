"""Reflection: Claude decides what to remember and how the person seemed; the server keeps the guardrails."""

from __future__ import annotations

from saathi.core import clock, db, family, impressions, memory, people, reflect
from tests.conftest import sms_texts


def _live(monkeypatch, response):
    """Live mode with a stubbed Claude reply; returns the prompts Claude was given."""
    monkeypatch.setenv("MOCK", "0")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-model")
    prompts = []

    def fake(context):
        prompts.append(context)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(reflect, "_bedrock_reflect", fake)
    return prompts


# ---------- live: the model decides ----------

def test_live_reflection_stores_what_claude_decides(monkeypatch, seeded):
    prompts = _live(monkeypatch, {
        "memories": [{"fact": "My granddaughter Diya is starting college in Pune next week", "category": "upcoming"}],
        "impressions": [{"text": "seemed quietly proud talking about Diya", "aspect": "pride", "confidence": 0.8}],
        "day_impression": "Meera seemed proud and chatty this morning, full of news about Diya.",
        "insights": [{"text": "Her grandchildren's milestones mean a great deal to her"}],
    })
    result = reflect.reflect("meera", "Diya got into the college in Pune! She starts next week.",
                             previous_reply="How have you been?", recent_turns=[{"role": "saathi", "text": "How have you been?"}])
    assert result["method"] == "bedrock"
    # Claude's own wording is kept (no verbatim requirement in live mode)
    assert [m["fact"] for m in result["remembered"]] == ["My granddaughter Diya is starting college in Pune next week"]
    assert [i["text"] for i in result["impressions"]] == ["seemed quietly proud talking about Diya"]
    assert result["impressions"][0]["aspect"] == "pride"  # no fixed label set
    assert impressions.day_text("meera") == "Meera seemed proud and chatty this morning, full of news about Diya."
    assert any("milestones" in m["fact"] for m in memory.insights("meera"))
    # Claude was given the long-term memory, the conversation and the engagement signals
    assert "Lights up when talking about her garden" in prompts[0]
    assert "Saathi: How have you been?" in prompts[0]
    assert "This turn:" in prompts[0] and "answered an open question" in prompts[0]


def test_live_memory_can_replace_an_old_one(monkeypatch, seeded):
    old = next(m for m in memory.recall("meera", "Priya calls") if "Priya" in m["fact"])
    _live(monkeypatch, {"memories": [{"fact": "Priya calls on Saturday mornings now", "category": "family",
                                      "replaces": old["memory_id"]}]})
    reflect.reflect("meera", "Priya calls on Saturday mornings now instead of Sundays.")
    facts = [m["fact"] for m in memory.recall("meera", "", 20)]
    assert "Priya calls on Saturday mornings now" in facts
    assert "Priya usually calls on Sunday evenings" not in facts


def test_clinical_language_is_never_stored(monkeypatch, seeded):
    _live(monkeypatch, {
        "impressions": [{"text": "seemed confused about the day", "aspect": "cognition"},
                        {"text": "sounded a little flat", "aspect": "mood", "confidence": 0.6}],
        "day_impression": "Meera may be showing signs of cognitive decline.",
        "insights": [{"text": "Possibly depressed in the evenings"}],
    })
    result = reflect.reflect("meera", "What day is it again? I thought it was Sunday.")
    assert [i["text"] for i in result["impressions"]] == ["sounded a little flat"]
    assert impressions.day_text("meera") is None
    assert all("depressed" not in m["fact"] for m in memory.insights("meera"))
    assert len(result["rejected"]) == 3


def test_model_failure_falls_back_to_offline_rules(monkeypatch, seeded):
    _live(monkeypatch, ValueError("not JSON"))
    result = reflect.reflect("meera", "I'm so tired, I hardly slept.")
    assert result["method"] == "rules (bedrock unavailable)"
    assert [i["text"] for i in result["impressions"]] == ["seemed a bit tired"]


def test_claude_decides_when_family_should_hear_today(monkeypatch, seeded, isolated_env):
    note = "Meera sounded lonely and mentioned missing her late husband twice. You might give her a call tonight."
    _live(monkeypatch, {"impressions": [{"text": "sounded lonely, missing her late husband"}],
                        "concern": {"flag": True, "note": note}})
    result = reflect.reflect("meera", "The house is so quiet. I keep thinking about him.")
    assert result["concern_sent"] == note
    assert sms_texts(isolated_env, "concern") == [f"Saathi: {note}"]
    # at most one concern text every 6 hours
    reflect.reflect("meera", "The house is so quiet.")
    assert len(sms_texts(isolated_env, "concern")) == 1


def test_concern_with_clinical_language_is_not_sent(monkeypatch, seeded, isolated_env):
    _live(monkeypatch, {"concern": {"flag": True, "note": "Meera may be depressed."}})
    result = reflect.reflect("meera", "I don't feel like doing anything.")
    assert result["concern_sent"] is None and sms_texts(isolated_env, "concern") == []


def test_offline_low_mood_sends_a_concern_text(seeded, isolated_env):
    clock.set_time("10:00")
    reflect.reflect("meera", "I'm not great today, I miss him so much.")
    assert sms_texts(isolated_env, "concern") == [
        "Saathi: Meera seemed a little low this morning. You might want to check in."]


def test_scheduler_events_are_not_reflected_on(seeded):
    result = reflect.reflect("meera", '[event] {"kind": "checkin"}')
    assert result["method"] == "none"


# ---------- offline stand-in ----------

def test_offline_cues_and_day_summary(seeded):
    clock.set_time("10:00")
    result = reflect.reflect("meera", "They won by 20 runs! I was thrilled.")
    assert [i["text"] for i in result["impressions"]] == ["seemed cheerful"]
    assert result["day_impression"] == "Meera seemed cheerful today."


def test_repeated_impressions_become_an_insight(seeded):
    # Seed: cheerful on two earlier days; a third day makes it a pattern Saathi has learned.
    clock.set_time("10:00")
    reflect.reflect("meera", "What a lovely morning, the roses are out!")
    assert any(m["fact"].startswith("Has often seemed cheerful lately") for m in memory.insights("meera"))


# ---------- family side ----------

def test_dashboard_shows_impressions_live(seeded):
    clock.set_time("10:00")
    reflect.reflect("meera", "They won by 20 runs! I was thrilled.")
    view = family.dashboard("priya-demo")["impressions"]
    assert view["today"] == "Meera seemed cheerful today."
    assert view["recent"] and view["insights"]
    assert "not a medical assessment" in view["note"]


def test_updates_include_todays_impression(seeded):
    clock.set_time("10:00")
    reflect.reflect("meera", "They won by 20 runs! I was thrilled.")
    assert "Meera seemed cheerful today." in family.send("meera", "Priya")["sent_text"]


def test_wipe_removes_impressions_and_stats(seeded):
    reflect.reflect("meera", "They won by 20 runs! I was thrilled.")
    deleted = people.wipe_person("meera")["deleted"]
    assert deleted["impression"] >= 1 and deleted["turn_stat"] >= 1 and deleted["day_impression"] >= 1
    with db.connect() as conn:
        for table in ("impression", "day_impression", "turn_stat"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE person_id='meera'").fetchone()[0] == 0


# ---------- empathy through the host ----------

async def test_next_day_greeting_picks_up_how_they_seemed(host):
    clock.set_time("10:00")
    await host.handle_utterance("raj", "I'm exhausted, I didn't sleep at all.")
    clock.set_time("09:30")  # the next morning
    greeting = (await host.poll("raj"))[0].reply
    # extra care, without telling him what Saathi noticed
    assert greeting == "Good morning, Raj. I hope you got some good rest. How are you feeling today?"


async def test_what_saathi_knows_is_in_every_prompt(host):
    clock.set_time("10:00")
    await host.handle_utterance("meera", "They won by 20 runs! I was thrilled.")
    await host.handle_utterance("meera", "The roses are blooming in the garden.")
    prompt = host.sessions["meera"].agent.system_prompt
    assert "## What you know about Meera" in prompt
    assert "Lights up when talking about her garden" in prompt
    assert "How they've seemed today: Meera seemed cheerful today." in prompt
    assert "I love tending the roses in my garden" in prompt  # recalled as relevant to "roses"


async def test_impressions_stay_off_the_elder_screen(host):
    result = await host.handle_utterance("meera", "I'm so tired today, it's been a long day.")
    assert result.impressions  # kept for the family side…
    assert "seemed" not in result.reply  # …but Saathi just responds with care
    assert result.reply == "That sounds tiring, Meera. Take it gently today."
