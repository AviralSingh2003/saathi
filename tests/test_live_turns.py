"""How real Claude turns behave (a scripted Strands model stands in for Bedrock)."""

from __future__ import annotations

import json

from strands.models import Model

from saathi.core import clock, db


class Scripted(Model):
    """Replays model messages: each step may speak, call one tool, or both (like Claude does)."""

    def __init__(self, steps):
        self.steps = list(steps)

    def update_config(self, **_):
        pass

    def get_config(self):
        return {}

    async def structured_output(self, *_, **__):  # pragma: no cover
        raise NotImplementedError
        yield

    async def stream(self, messages, tool_specs=None, system_prompt=None, **_):
        step = self.steps.pop(0) if self.steps else {}
        yield {"messageStart": {"role": "assistant"}}
        if step.get("text"):
            yield {"contentBlockDelta": {"delta": {"text": step["text"]}}}
            yield {"contentBlockStop": {}}
        if step.get("tool"):
            yield {"contentBlockStart": {"start": {"toolUse": {"name": step["tool"], "toolUseId": "t-1"}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(step["args"])}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"messageStop": {"stopReason": "end_turn"}}


async def test_words_spoken_before_a_tool_call_are_the_reply(host):
    session = host.session("meera")
    session.agent.model = Scripted([
        {"text": "That's wonderful, Meera! Let me remember that.",
         "tool": "remember", "args": {"person_id": "meera", "fact": "Arjun's team won by 20 runs", "category": "family"}},
        {},  # Claude often ends with an empty message after the tool result
    ])
    result = await session.turn("They won by 20 runs!")
    assert result.reply == "That's wonderful, Meera! Let me remember that."
    assert "remember" in result.tools_called()


async def test_no_feeling_is_logged_on_a_check_in(host):
    clock.set_time("09:00")
    session = host.session("meera")
    session.agent.model = Scripted([
        {"tool": "log_mood_selfreport", "args": {"person_id": "meera", "word": "checking in"}},
        {"text": "Good morning, Meera! How are you feeling today?"},
    ])
    result = await session.turn('[event] {"kind": "checkin"}', proactive=True)
    assert result.reply == "Good morning, Meera! How are you feeling today?"
    with db.connect() as conn:
        logged = conn.execute("SELECT COUNT(*) FROM activity WHERE type = 'mood_selfreport'").fetchone()[0]
    assert logged == 0
