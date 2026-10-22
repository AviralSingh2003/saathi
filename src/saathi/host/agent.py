"""The host agent ("simulated Alexa+").

- One Strands Agent per person, connected to the Saathi MCP server via MCPClient
  (Streamable HTTP). Family-only tools are filtered out.
- Live: Claude on Amazon Bedrock. Offline: MockModel (same agent loop, real MCP calls).
- The crisis pre-filter runs before any model sees the person's words.
- A hook relays tool calls to the browser (inspector) and MCP Apps cards.
- Before every turn the system prompt is refreshed with what Saathi knows about the
  person; after every turn the host calls reflect_turn (memory, impressions, insights).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from strands import Agent, AgentSkills
from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider, HookRegistry
from strands.tools.mcp import MCPClient

from saathi import config
from saathi.core import clock
from saathi.host import crisis, emergency, skill
from saathi.host.mock_model import MockModel
from saathi.server.tools import HIDDEN_FROM_AGENT

log = logging.getLogger(__name__)

ASK_COOLDOWN = timedelta(minutes=10)
# Answers to "Are you okay? Do you need me to call for help?" (checked in this order)
_NO_HELP_NEEDED = re.compile(r"\b(?:don'?t|do not|no) need (?:any )?(?:help|an ambulance|anyone)\b|\bdon'?t call\b")
_NEEDS_HELP = re.compile(r"^(?:yes|yeah|yep|haan|please)\b|\b(?:help|call|ambulance|hurry|hurts?|pain|bachao)\b"
                         r"|\bnot (?:okay|ok|fine|alright|all right)\b")
_SAYS_FINE = re.compile(r"^(?:no|nope|nahi)\b|\bi'?m (?:fine|ok|okay|alright|all right)\b|\bi am (?:fine|ok|okay)\b"
                        r"|\ball good\b|\bit'?s (?:fine|ok|okay|nothing)\b")


@dataclass
class TurnResult:
    reply: str
    cards: list[dict] = field(default_factory=list)
    frames: list[dict] = field(default_factory=list)
    escalated: bool = False
    checking: bool = False  # Saathi asked "Are you okay?" and is waiting for an answer
    proactive: bool = False
    model: str = ""
    remembered: list[dict] = field(default_factory=list)
    impressions: list[dict] = field(default_factory=list)  # for the family side only; never shown to the elder
    previous_reply: str | None = None  # what Saathi said just before this turn

    def tools_called(self) -> set[str]:
        return {f["message"]["params"]["name"] for f in self.frames
                if f.get("direction") == "out" and f["message"].get("method") == "tools/call"}


def tool_result_data(result: dict | None) -> Any:
    """Structured content of an MCP tool result (dict), falling back to parsed text."""
    if not result:
        return None
    structured = result.get("structuredContent")
    if structured is not None:
        return structured
    text = " ".join(c.get("text", "") for c in result.get("content", []) if isinstance(c, dict))
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


def _resource_uri(tool: Any) -> str | None:
    mcp_tool = getattr(tool, "mcp_tool", None)
    meta = getattr(mcp_tool, "meta", None) or {}
    return (meta.get("ui") or {}).get("resourceUri")


class ToolRelay(HookProvider):
    """Injects person_id, records tool calls as inspector frames, and relays cards."""

    def __init__(self, session: "Session"):
        self.session = session

    def register_hooks(self, registry: HookRegistry, **_: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before)
        registry.add_callback(AfterToolCallEvent, self.after)

    def before(self, event: BeforeToolCallEvent) -> None:
        tool_use = event.tool_use
        if tool_use.get("name") == "log_mood_selfreport" and self.session.current_text.startswith("[event]"):
            # A feeling is only logged when the person has just said it, never on a scheduled turn.
            event.cancel_tool = ("Not logged: the person hasn't said how they feel yet. Ask them, and log it "
                                 "only if they tell you, in their own words.")
            return
        spec = getattr(event.selected_tool, "tool_spec", None) or {}
        props = (spec.get("inputSchema", {}).get("json", {}) or {}).get("properties", {})
        args = tool_use.get("input")
        if isinstance(args, dict) and "person_id" in props:
            args["person_id"] = self.session.person_id  # the speaker is fixed by the host, not the model
        self.session.record_frame("out", {
            "jsonrpc": "2.0", "id": tool_use.get("toolUseId"), "method": "tools/call",
            "params": {"name": tool_use.get("name"), "arguments": args},
        })

    def after(self, event: AfterToolCallEvent) -> None:
        name = event.tool_use.get("name")
        result = event.result or {}
        data = tool_result_data(result)
        self.session.record_frame("in", {
            "jsonrpc": "2.0", "id": event.tool_use.get("toolUseId"),
            "result": {"tool": name, "isError": result.get("status") == "error", "structuredContent": data},
        })
        uri = self.session.host.card_tools.get(name)
        if uri and result.get("status") != "error":
            self.session.turn_cards.append({
                "tool": name, "uri": uri,
                "input": event.tool_use.get("input") or {},
                "result": {"content": [{"type": "text", "text": json.dumps(data)}], "structuredContent": data},
            })


class Session:
    def __init__(self, host: "Host", person_id: str):
        self.host = host
        self.person_id = person_id
        self.lock = asyncio.Lock()
        self.turn_frames: list[dict] = []
        self.turn_cards: list[dict] = []
        self._frames_lock = threading.Lock()
        self.context = host.call("get_person_context", {"person_id": person_id})
        self.last_reply: str | None = None
        self.current_text = ""  # what this turn is responding to (the person's words, or an [event])
        self.display_name = self.context["display_name"]
        self.contact_names = [c["name"] for c in self.context.get("contacts", []) if c.get("receives_updates")]
        self.using_mock = host.settings.mock or not host.settings.bedrock_model_id
        self.agent = self._build_agent(mock=self.using_mock)

    def _build_agent(self, mock: bool) -> Agent:
        if mock:
            model = MockModel(self.person_id, self.display_name, self.contact_names)
            plugins = []
        else:
            from strands.models import BedrockModel

            model = BedrockModel(
                model_id=self.host.settings.bedrock_model_id,
                region_name=self.host.settings.aws_region,
                temperature=0.3,
            )
            plugins = [AgentSkills(skills=str(skill.SKILLS_DIR))]
        return Agent(
            model=model,
            tools=self.host.tools,
            system_prompt=skill.system_prompt(self.context),
            hooks=[ToolRelay(self)],
            plugins=plugins,
            callback_handler=None,
            name=f"saathi-host-{self.person_id}",
        )

    def record_frame(self, direction: str, message: dict) -> None:
        with self._frames_lock:
            self.turn_frames.append({"direction": direction, "message": message})

    def _take(self) -> tuple[list[dict], list[dict]]:
        with self._frames_lock:
            frames, cards = self.turn_frames, self.turn_cards
            self.turn_frames, self.turn_cards = [], []
        return frames, cards

    def refresh_prompt(self, text: str) -> None:
        """Ground the next turn in what Saathi knows now (memories relevant to these words, insights,
        how they've seemed). Strands' AgentSkills plugin re-adds its skill list on every invocation."""
        query = "" if text.startswith("[event]") else text
        try:
            self.context = self.host.call("get_person_context", {"person_id": self.person_id, "query": query})
        except Exception as exc:  # keep the previous context; never block the conversation
            log.warning("Context refresh failed: %s", exc)
        self.agent.system_prompt = skill.system_prompt(self.context)

    def recent_turns(self, limit: int = 12) -> list[dict]:
        """The conversation so far as plain text (no tool traffic), for reflection."""
        turns = []
        for message in self.agent.messages:
            text = " ".join(b["text"] for b in message.get("content", []) if "text" in b).strip()
            if not text:
                continue
            if message["role"] == "user" and text.startswith("[event]"):
                text = "(Saathi started this part of the conversation)"
            turns.append({"role": "saathi" if message["role"] == "assistant" else "person", "text": text})
        return turns[-limit:]

    def _reply_since(self, start: int, result: Any) -> str:
        """Everything Saathi said during this turn. Claude often speaks *before* a tool call ("That's
        wonderful! Let me remember that.") and then ends with an empty message; Strands' result
        text only holds that last message, so collect the assistant text from the whole turn."""
        spoken = [block["text"].strip()
                  for message in self.agent.messages[start:] if message.get("role") == "assistant"
                  for block in message.get("content", []) if isinstance(block, dict) and block.get("text")]
        spoken = [s for s in spoken if s]
        return " ".join(spoken) if spoken else str(result).strip()

    async def turn(self, text: str, proactive: bool = False) -> TurnResult:
        async with self.lock:
            previous = self.last_reply
            self.current_text = text
            await asyncio.to_thread(self.refresh_prompt, text)
            try:
                start = len(self.agent.messages)
                result = await self.agent.invoke_async(text)
                reply = self._reply_since(start, result)
            except Exception as exc:
                if self.using_mock:
                    raise
                # Bedrock unavailable: fall back to the deterministic router for this session.
                log.warning("Live model failed (%s); falling back to MockModel", exc)
                self.using_mock = True
                self.agent = self._build_agent(mock=True)
                start = len(self.agent.messages)
                result = await self.agent.invoke_async(text)
                reply = self._reply_since(start, result)
            frames, cards = self._take()
            self.last_reply = reply
            return TurnResult(reply=reply, cards=cards, frames=frames, proactive=proactive,
                              model="mock" if self.using_mock else "bedrock", previous_reply=previous)


class Host:
    def __init__(self, settings: config.Settings | None = None):
        self.settings = settings or config.get()
        self.mcp = MCPClient(
            url=self.settings.mcp_url,
            tool_filters={"rejected": list(HIDDEN_FROM_AGENT)},
            startup_timeout=30,
            application_name="saathi-host",
            application_version="0.1.0",
        )
        self.mcp.start()
        self.tools = self.mcp.list_tools_sync()
        self.card_tools = {t.tool_name: uri for t in self.tools if (uri := _resource_uri(t))}
        self.sessions: dict[str, Session] = {}
        self._sessions_lock = threading.Lock()
        # Layer 2 of emergency detection needs a model, so it runs in live mode only.
        self.emergency_check_enabled = self.settings.live_bedrock
        self.pending_checks: dict[str, dict] = {}  # person_id -> {"deadline", "said"} while asking "Are you okay?"
        self.last_ask: dict[str, Any] = {}

    def close(self) -> None:
        try:
            self.mcp.stop(None, None, None)
        except Exception:  # pragma: no cover - best effort on shutdown
            pass

    def call(self, name: str, arguments: dict) -> Any:
        """Direct (non-LLM) tool call, used for grounding, polling and the crisis path."""
        result = self.mcp.call_tool_sync(f"host-{uuid.uuid4().hex[:10]}", name, arguments)
        if result.get("status") == "error":
            text = " ".join(c.get("text", "") for c in result.get("content", []) if isinstance(c, dict))
            raise RuntimeError(text or f"{name} failed")
        return tool_result_data(result)

    def session(self, person_id: str) -> Session:
        with self._sessions_lock:
            if person_id not in self.sessions:
                self.sessions[person_id] = Session(self, person_id)
            return self.sessions[person_id]

    async def handle_utterance(self, person_id: str, text: str, reflect: bool = True) -> TurnResult:
        """One conversational turn. With reflect=True, the turn is reflected on before returning;
        the web host passes reflect=False and runs reflect_after() in the background.

        Emergency handling comes first:
        1. an answer to "Are you okay?" (if Saathi is checking on them)
        2. the instant phrase list (offline too)
        3. live: the Haiku check, in parallel with the conversation, under a hard time limit
        """
        session = await asyncio.to_thread(self.session, person_id)
        answered = await self._answer_to_check(session, text)
        if answered:
            return answered
        match = crisis.check(text)
        if match:
            return await self._crisis(session, match.reason_code, said=text)  # never reflected on
        if self.emergency_check_enabled and not text.startswith("[event]"):
            result = await self._turn_with_emergency_check(session, text)
            if result.escalated or result.checking:
                return result
        else:
            result = await session.turn(text)
        if reflect:
            frames, outcome = await self.reflect_after(person_id, text, result)
            result.frames += frames
            result.remembered = outcome.get("remembered", [])
            result.impressions = outcome.get("impressions", [])
        return result

    async def reflect_after(self, person_id: str, text: str, turn: TurnResult) -> tuple[list[dict], dict]:
        """After a turn the host itself (not the conversational model) calls reflect_turn."""
        if turn.escalated:
            return [], {}
        session = self.sessions.get(person_id)
        args = {
            "person_id": person_id,
            "utterance": text,
            "previous_reply": turn.previous_reply,
            "recent_turns": session.recent_turns() if session else [],
            "tools_called": sorted(turn.tools_called()),
        }
        call_id = f"reflect-{uuid.uuid4().hex[:8]}"
        frames = [{"direction": "out", "message": {"jsonrpc": "2.0", "id": call_id, "method": "tools/call",
                                                   "params": {"name": "reflect_turn", "arguments": args}}}]
        try:
            outcome = await asyncio.to_thread(self.call, "reflect_turn", args)
        except Exception as exc:  # reflection is best-effort; never break the conversation
            log.warning("reflect_turn failed: %s", exc)
            return [], {}
        frames.append({"direction": "in", "message": {"jsonrpc": "2.0", "id": call_id, "result": {
            "tool": "reflect_turn", "structuredContent": outcome}}})
        return frames, outcome or {}

    # ---- emergencies ----

    async def _turn_with_emergency_check(self, session: Session, text: str) -> TurnResult:
        """Run the Haiku emergency check alongside the conversation. An emergency replaces the reply."""
        timeout = self.settings.emergency_check_timeout
        check = asyncio.create_task(asyncio.wait_for(
            asyncio.to_thread(emergency.assess, text, session.recent_turns(4)), timeout))
        conversation = asyncio.create_task(session.turn(text))
        try:
            verdict = await check
        except Exception as exc:  # timeout, network, credentials, unparseable
            log.warning("Emergency check failed or timed out (%s)", type(exc).__name__)
            verdict = None
        action = emergency.decide(verdict)
        frame = self._check_frame(text, verdict)
        if action == "dispatch":
            result = await self._crisis(session, "medical_emergency", said=text)
        elif action in ("self_harm", "asked_for_help"):
            result = await self._crisis(session, action, said=text)
        elif action == "ask" and (verdict is not None or self._may_ask(session.person_id)):
            result = await self._ask_if_okay(session, text)
        else:
            return await conversation
        result.frames[:0] = frame
        # The conversation reply (if it ever arrives) is dropped: the emergency comes first.
        conversation.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        return result

    @staticmethod
    def _check_frame(text: str, verdict: dict | None) -> list[dict]:
        call_id = f"check-{uuid.uuid4().hex[:8]}"
        return [
            {"direction": "out", "message": {"jsonrpc": "2.0", "id": call_id, "method": "emergency_check",
                                             "params": {"model": "haiku", "utterance": text}}},
            {"direction": "in", "message": {"jsonrpc": "2.0", "id": call_id,
                                            "result": verdict or {"type": "timeout_or_error"}}},
        ]

    def _may_ask(self, person_id: str) -> bool:
        """Ask-then-escalate after a timeout/error at most once per 10 minutes per person."""
        last = self.last_ask.get(person_id)
        now = clock.now()
        if last and now - last < ASK_COOLDOWN:
            return False
        self.last_ask[person_id] = now
        return True

    async def _ask_if_okay(self, session: Session, said: str) -> TurnResult:
        """Couldn't be sure: ask them, tell family we're checking, and escalate unless they say they're fine."""
        frames = await self._escalate_frames(session.person_id, "checking", said)
        deadline = clock.now() + timedelta(seconds=self.settings.emergency_confirm_seconds)
        self.pending_checks[session.person_id] = {"deadline": deadline, "said": said}
        reply = crisis.ASK.format(name=session.display_name)
        session.last_reply = reply
        return TurnResult(reply=reply, frames=frames, checking=True, model="emergency-check")

    async def _answer_to_check(self, session: Session, text: str) -> TurnResult | None:
        pending = self.pending_checks.get(session.person_id)
        if not pending:
            return None
        low = text.lower().replace("’", "'").strip()
        if not _NO_HELP_NEEDED.search(low) and _NEEDS_HELP.search(low):
            self.pending_checks.pop(session.person_id, None)
            return await self._crisis(session, "medical_emergency", said=f"{pending['said']} / then: {text}")
        if _NO_HELP_NEEDED.search(low) or _SAYS_FINE.search(low):
            self.pending_checks.pop(session.person_id, None)
            frames = await self._escalate_frames(session.person_id, "all_clear", text)
            reply = f"I'm glad you're okay, {session.display_name}. I've let your family know."
            session.last_reply = reply
            return TurnResult(reply=reply, frames=frames, model="emergency-check")
        return None  # something else: carry on; the deadline still stands

    async def _escalate_frames(self, person_id: str, reason_code: str, said: str) -> list[dict]:
        frames, _ = await self._escalate(person_id, reason_code, said)
        return frames

    async def _escalate(self, person_id: str, reason_code: str, said: str) -> tuple[list[dict], dict]:
        args = {"person_id": person_id, "reason_code": reason_code, "said": said}
        call_id = f"crisis-{uuid.uuid4().hex[:8]}"
        frames = [{"direction": "out", "message": {"jsonrpc": "2.0", "id": call_id, "method": "tools/call",
                                                   "params": {"name": "escalate_to_contact", "arguments": args}}}]
        try:
            outcome = await asyncio.to_thread(self.call, "escalate_to_contact", args)
            frames.append({"direction": "in", "message": {"jsonrpc": "2.0", "id": call_id, "result": {
                "tool": "escalate_to_contact", "structuredContent": outcome}}})
        except Exception as exc:  # still speak the safety message
            log.error("Escalation failed: %s", exc)
            outcome = {}
        return frames, outcome if isinstance(outcome, dict) else {}

    async def _crisis(self, session: Session, reason_code: str, said: str, no_answer: bool = False,
                      proactive: bool = False) -> TurnResult:
        """Emergency services first (medical), then family; then the fixed spoken script."""
        self.pending_checks.pop(session.person_id, None)
        frames, outcome = await self._escalate(session.person_id, reason_code, said)
        reply = crisis.script(reason_code, session.display_name, outcome.get("contact_name"),
                              dispatched=bool(outcome.get("dispatched")), no_answer=no_answer)
        session.last_reply = reply
        return TurnResult(reply=reply, frames=frames, escalated=True, proactive=proactive, model="emergency")

    async def poll(self, person_id: str) -> list[TurnResult]:
        """Fetch due events and let the agent speak first for each one."""
        session = await asyncio.to_thread(self.session, person_id)
        pending = self.pending_checks.get(person_id)
        if pending and clock.now() >= pending["deadline"]:
            # No answer to "Are you okay?": contact emergency services.
            return [await self._crisis(session, "medical_emergency", said=f"{pending['said']} (no answer)",
                                       no_answer=True, proactive=True)]
        if session.lock.locked():
            return []  # a turn is in progress; try again on the next tick
        args = {"person_id": person_id}
        data = await asyncio.to_thread(self.call, "get_due_events", args)
        events = (data or {}).get("events", [])
        results = []
        for event in events:
            results.append(await session.turn(f"[event] {json.dumps(event)}", proactive=True))
        if results:  # show the poll in the inspector only when it surfaced something
            call_id = f"poll-{uuid.uuid4().hex[:8]}"
            results[0].frames[:0] = [
                {"direction": "out", "message": {"jsonrpc": "2.0", "id": call_id, "method": "tools/call",
                                                 "params": {"name": "get_due_events", "arguments": args}}},
                {"direction": "in", "message": {"jsonrpc": "2.0", "id": call_id, "result": {
                    "tool": "get_due_events", "structuredContent": data}}},
            ]
        return results
