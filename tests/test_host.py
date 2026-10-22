"""Host agent: the emergency phrase list, MockModel through Strands, and the full storyline."""

from __future__ import annotations

from saathi.host import crisis
from tests.conftest import outbox_files, sms_texts


def test_phrase_list():
    assert crisis.check("I've fallen and I can't get up").reason_code == "medical_emergency"
    assert crisis.check("I don’t want to live anymore").reason_code == "self_harm"
    assert crisis.check("please call Priya now").reason_code == "asked_for_help"
    assert crisis.check("Help me set a reminder") is None
    assert crisis.check("I fell asleep early") is None
    assert crisis.check("I fell in the bathroom").reason_code == "medical_emergency"
    assert crisis.check("Remind me to take my tablet at 2") is None
    # the wider list: heart, stroke signs, collapse, overdose, a little Hindi
    for text in ["I think I'm having a heart attack", "my chest feels tight", "my left arm has gone numb",
                 "I can't feel my face", "my husband has collapsed", "I took all my tablets",
                 "seene mein dard ho raha hai"]:
        assert crisis.check(text).reason_code == "medical_emergency", text
    assert crisis.check("madad karo").reason_code == "asked_for_help"
    assert crisis.check("my knee aches a bit today") is None


async def test_emergency_contacts_services_before_family_and_skips_the_model(host, isolated_env, monkeypatch):
    session = host.session("meera")
    calls = []

    async def spy(*args, **kwargs):
        calls.append(args)
        raise AssertionError("model must not run on the emergency path")

    monkeypatch.setattr(session.agent, "invoke_async", spy)
    result = await host.handle_utterance("meera", "I think I've fallen, I can't get up")
    assert result.escalated and result.model == "emergency"
    assert result.reply.startswith("I've called emergency services, Meera. Help is on the way.")
    assert calls == []
    dispatch = sorted((isolated_env / "outbox").glob("*-dispatch-*.txt"))
    assert dispatch and "SIMULATED EMERGENCY DISPATCH" in dispatch[0].read_text(encoding="utf-8")
    texts = sms_texts(isolated_env, "medical_emergency")
    assert texts and "Emergency services have been contacted for Meera" in texts[0]
    assert outbox_files(isolated_env, "medical_emergency")  # and an email


async def test_person_id_is_fixed_by_the_host(host):
    # The mock passes person_id itself, but the hook overwrites it with the session's speaker.
    result = await host.handle_utterance("raj", "what do you share with Priya?")
    assert result.reply.startswith("I keep Priya up to date about your day")


async def test_reminder_flow_relays_card(host):
    first = await host.handle_utterance("meera", "Remind me to take my BP tablet at 2")
    assert first.reply.startswith("Just to check")
    assert first.cards == []
    second = await host.handle_utterance("meera", "yes")
    assert "I'll remind you to take your BP tablet" in second.reply
    assert second.cards[0]["uri"] == "ui://saathi/reminder-card"
    assert second.cards[0]["result"]["structuredContent"]["time"] == "14:00"
    assert any(f["message"].get("method") == "tools/call" for f in second.frames)


def test_live_agent_builds_with_skill(host, monkeypatch):
    """Live mode wiring (no network until a turn runs): BedrockModel + the native AgentSkills plugin."""
    from strands.models import BedrockModel

    from saathi import config
    from saathi.host import skill

    session = host.session("meera")
    monkeypatch.setenv("MOCK", "0")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-model-id")
    host.settings = config.get()
    agent = session._build_agent(mock=False)
    assert isinstance(agent.model, BedrockModel)
    assert agent.model.get_config()["model_id"] == "test-model-id"
    assert "Companion, not clinician" in agent.system_prompt
    assert "person_id is 'meera'" in agent.system_prompt
    assert skill.skill_body().startswith("# Saathi companion")
    assert "skills" in agent.tool_names  # the native AgentSkills tool is registered

    # The skill passes strict Agent Skills validation and is discovered for an agent.
    from strands import Agent, AgentSkills

    plugin = AgentSkills(skills=str(skill.SKILLS_DIR), strict=True)
    probe = Agent(model=BedrockModel(model_id="x", region_name="us-east-1"), plugins=[plugin], callback_handler=None)
    assert [s.name for s in plugin.get_available_skills(probe)] == ["saathi-companion"]


async def test_full_storyline_offline(host, isolated_env):
    from saathi.simulate import check_outbox, run_storyline

    transcript = await run_storyline(host)
    replies = [line.text for line in transcript if line.who == "saathi"]
    texts = [line.text for line in transcript if line.who == "text"]
    assert replies[0].startswith("Good morning, Meera. Last time you mentioned")
    assert "Done. I've passed that on to Priya." in replies  # no read-back, no approval step
    assert any(r.startswith('Priya says: "Mum') for r in replies)
    assert "Sent. Priya will get your reply." in replies
    # Impressions stay off the elder's side: Saathi never says how they "seemed"
    assert not any("seemed" in r for r in replies)
    seen = [text for line in transcript for text in line.impressions]
    assert seen == ["seemed cheerful", "was quieter than usual"]
    # …but Priya hears: a concern text, Meera's message, the reply, the emergency, the evening summaries
    assert texts[0] == "Saathi: Raj was quieter than usual this morning. You might want to check in."
    assert 'Saathi: Meera says: "The roses are out."' in texts[1]
    assert any(t.startswith("URGENT from Saathi: Emergency services have been contacted for Raj") for t in texts)
    assert any(t.startswith("Saathi, Raj's day: Emergency services have been contacted for Raj") for t in texts)
    assert any(t.startswith("Saathi, Meera's day:") for t in texts)
    dispatched_at = next(i for i, line in enumerate(transcript) if line.who == "dispatch")
    urgent_at = next(i for i, line in enumerate(transcript) if line.text.startswith("URGENT from Saathi"))
    assert dispatched_at < urgent_at  # emergency services first, then Priya
    emergency = next(r for r in replies if r.startswith("I've called emergency services, Raj"))
    assert "Help is on the way" in emergency
    kept = [fact for line in transcript for fact in line.remembered]
    assert kept == ["My granddaughter Diya starts college next week"]
    readback = next(r for r in replies if r.startswith("Here's what I remember."))
    assert 'Later you said: "They won by 20 runs!"' in readback
    assert all(line.model in ("mock", "emergency") for line in transcript if line.who == "saathi")
    assert check_outbox(isolated_env / "outbox") == []
