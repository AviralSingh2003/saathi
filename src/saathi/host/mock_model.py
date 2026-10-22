"""MockModel: a deterministic stand-in for the LLM, used in offline mode.

It is a real Strands model provider: it emits the same streaming events a
Bedrock model would (including tool-use blocks), so in mock mode the Strands
agent loop still runs and every action is a genuine MCP tool call. It just
picks tools with simple rules instead of reasoning.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterable
from typing import Any

from strands.models import Model

from saathi.core.capture import offline_candidates
from saathi.core.impressions import CONCERN, offline_cues
from saathi.core.reminders import second_person, spoken_time

_YES = re.compile(r"^(yes|yeah|yep|yup|sure|ok|okay|please do|please|go ahead|send it|do it|that's right|correct|right|haan|ha+n?)\b")
_NO = re.compile(r"^(no|nope|not now|don't|do not|cancel|never mind|leave it|nahi|na)\b")
_REMIND = re.compile(
    r"remind me (?:to )?(?P<text>.+?)\s+(?P<daily>every day |daily )?(?:at|by)\s+"
    r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ampm>a\.?m\.?|p\.?m\.?)?(?P<daily2> every day| daily)?$"
)
_DONE = re.compile(r"^(?:i'?ve |i have )?(?:done|finished|taken it|taken|all done|did it)\b|^i (?:took|did) it\b|^it'?s done\b")
_MOOD = re.compile(r"\bi(?:'m| am) feeling (?P<w>[^.!?,]+)|\bi feel (?P<w2>[^.!?,]+)")
_UPDATE = re.compile(
    r"\b(?:let|tell|update|message|send)\s+(?P<name>[a-z]+)\s+(?:know|how|an update|a quick update)"
    r"|\bsend (?:an |a )?update(?: to (?P<name2>[a-z]+))?"
)
_MESSAGES = re.compile(r"\b(?:any|my|new) messages?\b|\bdid (?:anyone|[a-z]+) (?:write|message|leave)")
_SHARE = re.compile(r"\bwhat do you (?:share|tell|send)\b")
_LIST = re.compile(r"\bwhat'?s on (?:today|for today)\b|\bmy reminders\b|\bwhat reminders\b")
_REMEMBER = re.compile(r"^(?:please )?remember (?:that )?(?P<fact>.+)")
_WHAT_REMEMBERED = re.compile(r"\bwhat do you (?:remember|know) about me\b|\bwhat have you remembered\b")
_GREETING = re.compile(r"^(?:hi|hello|hey|namaste|good (?:morning|afternoon|evening))\b[\s\w,!.]{0,12}$")
# "Tell Priya the roses are out" -> an update carrying her words
_TELL = re.compile(r"^(?:please )?(?:tell|let) (?P<name>[a-z]+)(?: know)? (?:that )?(?P<note>.+)$")
# Spoken when a cue suggests how they're doing (offline stand-in for Claude's empathy).
_EMPATHY = {
    "seemed a bit tired": "That sounds tiring, {name}. Take it gently today.",
    "seemed a little low": "I'm sorry it's been hard, {name}. I'm here with you.",
    "seemed worried": "That sounds worrying, {name}. I'm here if you'd like to talk it through.",
    "seemed excited": "How exciting, {name}!",
    "seemed cheerful": "That's lovely to hear, {name}!",
}
_ERROR_PREFIX = re.compile(r"^Error executing tool \w+:\s*")
_ANSWER_START = re.compile(r"^(?:it|it's|they|we|he|she|yes|yeah|no|not|good|great|fine|well|very|so|oh|ah|the)\b")
_TOPIC_STOPWORDS = {"have", "with", "that", "this", "about", "what", "when", "were", "been", "there", "their",
                    "they", "from", "will", "just", "very", "really"}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("’", "'")).strip()


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def _spoken_memory(fact: str) -> str:
    """'My son lives in Pune. Later I said: "I visit him"' -> 'Your son lives in Pune. Later you said:
    "I visit him".' Words in quotes are the person's own and stay as they are."""
    parts = re.split(r'("[^"]*")', fact.strip())
    spoken = "".join(part if i % 2 else second_person(part) for i, part in enumerate(parts))
    spoken = spoken[:1].upper() + spoken[1:]
    return spoken if spoken.endswith((".", "!", "?", '"')) else spoken + "."


def _topic_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']{4,}", text.lower()) if w not in _TOPIC_STOPWORDS}


def _answers_followup(text: str, fact: str) -> bool:
    """After "how did <fact> go?": is this the answer, or something new the person is telling us?
    New = a fact about a person, plan or like that shares no words with the question."""
    if _ANSWER_START.match(text.lower()) or not offline_candidates(text):
        return True
    return bool(_topic_words(text) & _topic_words(fact))


def _empathy(text: str, name: str) -> str | None:
    for cue in offline_cues(text):
        if cue["text"] in _EMPATHY:
            return _EMPATHY[cue["text"]].format(name=name)
    return None


def _hhmm(h: int, m: int, ampm: str | None) -> str:
    if ampm:
        pm = ampm.startswith("p")
        h = h % 12 + (12 if pm else 0)
    elif 1 <= h <= 7:
        h += 12  # "at 2" almost always means the afternoon
    return f"{h:02d}:{m:02d}"


class MockModel(Model):
    """Rule-based router with a small conversation state machine."""

    def __init__(self, person_id: str, display_name: str, contact_names: list[str]):
        self.person_id = person_id
        self.name = display_name
        self.contacts = contact_names or ["your family"]
        self.default_contact = self.contacts[0]
        self.pending: dict | None = None
        self.queued_offer: dict | None = None
        self.last_tool: str | None = None
        self.last_args: dict = {}
        self.last_reminder: dict | None = None
        self.last_text = ""
        self.config: dict[str, Any] = {"model_id": "saathi-mock-router"}

    # ---- Strands Model interface ----

    def update_config(self, **model_config: Any) -> None:
        self.config.update(model_config)

    def get_config(self) -> dict[str, Any]:
        return self.config

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):  # pragma: no cover
        raise NotImplementedError("MockModel does not support structured output")
        yield  # make this an async generator

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs) -> AsyncIterable[dict]:
        last = messages[-1] if messages else {"content": []}
        results = [b["toolResult"] for b in last.get("content", []) if "toolResult" in b]
        if results:
            action = self._after_tool(results[-1])
        else:
            text = " ".join(b.get("text", "") for b in last.get("content", []) if "text" in b)
            action = self._route(_clean(text))
        for event in self._emit(action):
            yield event

    # ---- event emission ----

    def _emit(self, action: tuple) -> list[dict]:
        events: list[dict] = [{"messageStart": {"role": "assistant"}}]
        if action[0] == "tool":
            _, name, args = action
            self.last_tool, self.last_args = name, args
            events += [
                {"contentBlockStart": {"start": {"toolUse": {"name": name, "toolUseId": f"mock-{uuid.uuid4().hex[:10]}"}}}},
                {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(args)}}}},
                {"contentBlockStop": {}},
                {"messageStop": {"stopReason": "tool_use"}},
            ]
        else:
            events += [
                {"contentBlockDelta": {"delta": {"text": action[1]}}},
                {"contentBlockStop": {}},
                {"messageStop": {"stopReason": "end_turn"}},
            ]
        return events

    @staticmethod
    def _say(text: str) -> tuple:
        return ("say", text)

    def _tool(self, name: str, **args) -> tuple:
        return ("tool", name, args)

    # ---- routing ----

    # Pending states that expect free text: the person's words win over keyword
    # commands (so "tell her I'm feeling better" is a reply, not a mood log),
    # except for unambiguous commands like "remind me…" or "done".
    _FREE_TEXT = frozenset({"offer_reply", "followup"})

    def _route(self, text: str) -> tuple:
        if text.startswith("[event]"):
            return self._on_event(text[len("[event]"):].strip())
        low = text.lower()
        self.last_text = text

        if (self.pending and self.pending["type"] in self._FREE_TEXT
                and not (_REMIND.search(low) or _DONE.search(low))):
            handled = self._on_pending(low, text)
            if handled is not None:
                return handled

        saved, self.pending = self.pending, None  # a new command starts fresh
        command = self._command(low, text)
        if command is not None:
            return command
        self.pending = saved
        if self.pending:
            handled = self._on_pending(low, text)
            if handled is not None:
                return handled
        if _YES.match(low) or _NO.match(low):
            return self._say("Okay.")
        question = low.rstrip().endswith("?")
        if _GREETING.match(low):
            return self._say(f"Hello, {self.name}! You can ask me to set a reminder, send {self.default_contact} "
                             "an update, or check for messages.")
        if len(low.split()) >= 4 and not question:
            # Something the person is telling us (the host reflects on it afterwards).
            return self._say(_empathy(text, self.name) or f"Thank you for telling me, {self.name}.")
        if not question:
            return self._say(_empathy(text, self.name) or (
                f"That's alright, {self.name}. I'm here if you'd like to chat, or I can set a reminder "
                f"or send {self.default_contact} an update."))
        return self._say(
            f"I'm here, {self.name}. You can ask me to set a reminder, send {self.default_contact} an update, "
            "or check for messages."
        )

    def _command(self, low: str, text: str) -> tuple | None:
        if m := _REMIND.search(low):
            hhmm = _hhmm(int(m["h"]), int(m["m"] or 0), m["ampm"])
            daily = bool(m["daily"] or m["daily2"])
            reminder_text = text[m.start("text"):m.end("text")].strip()
            self.pending = {"type": "confirm_reminder", "text": reminder_text, "time": hhmm,
                            "recurrence": "daily" if daily else "none"}
            every = ", every day" if daily else ""
            return self._say(f"Just to check: remind you to {second_person(reminder_text)} "
                             f"at {spoken_time(hhmm)}{every}?")
        if _DONE.search(low):
            if self.last_reminder:
                return self._tool("complete_reminder", reminder_id=self.last_reminder["reminder_id"])
            return self._say("Which reminder have you finished? I don't have one waiting right now.")
        if (m := _TELL.match(low)) and self._known(m["name"]) and not m["note"].startswith(("how ", "an update")):
            note = text[m.start("note"):].strip().rstrip(".")
            return self._tool("send_family_update", person_id=self.person_id, contact=self._contact(m["name"]),
                              extra_note=note[:1].upper() + note[1:])
        if m := _MOOD.search(low):
            word = (m["w"] or m["w2"]).strip()[:40]
            return self._tool("log_mood_selfreport", person_id=self.person_id, word=word)
        if m := _UPDATE.search(low):
            name = (m["name"] or m["name2"] or "").strip()
            return self._tool("send_family_update", person_id=self.person_id, contact=self._contact(name))
        if _MESSAGES.search(low):
            return self._tool("get_due_events", person_id=self.person_id, kinds=["note"])
        if _SHARE.search(low):
            return self._tool("get_person_context", person_id=self.person_id)
        if _LIST.search(low):
            return self._tool("list_reminders", person_id=self.person_id, window_hours=24)
        if _WHAT_REMEMBERED.search(low):
            return self._tool("recall", person_id=self.person_id, query="", limit=5)
        if m := _REMEMBER.match(low):
            fact = text[m.start("fact"):].strip().rstrip(".")
            return self._tool("remember", person_id=self.person_id, fact=fact, category="general")
        return None

    def _contact(self, name: str) -> str:
        for candidate in self.contacts:
            if candidate.lower() == name.lower():
                return candidate
        return self.default_contact

    def _known(self, name: str) -> bool:
        return name.lower() in {c.lower() for c in self.contacts}

    def _on_pending(self, low: str, text: str) -> tuple | None:
        p = self.pending
        kind = p["type"]
        yes, no = bool(_YES.match(low)), bool(_NO.match(low))

        if kind == "confirm_reminder":
            self.pending = None
            if yes:
                return self._tool("set_reminder", person_id=self.person_id, text=p["text"],
                                  time=p["time"], recurrence=p["recurrence"])
            if no:
                return self._say("Okay, I won't set it.")
            return None

        if kind == "offer_note":
            self.pending = None
            if yes:
                return self._tool("get_due_events", person_id=self.person_id, kinds=["note"])
            if no:
                return self._say("Alright, I'll keep it for later. Just ask me for messages any time.")
            return None

        if kind == "offer_reply":
            if no:
                self.pending = None
                return self._say("Okay.")
            reply = re.sub(r"^(?:yes,?\s*)?(?:tell (?:her|him|them|[a-z]+) |say )?(?:that )?", "", text, flags=re.I).strip()
            if not reply or yes and len(reply.split()) <= 2:
                return self._say(f"What would you like to say to {p['contact']}?")
            self.pending = None
            return self._tool("reply_to_family_note", note_id=p["note_id"], text=reply)

        if kind == "followup":
            self.pending = None
            if _MOOD.search(low):
                return None  # a feeling: logged in their words by the mood command, never kept as a memory
            if not _answers_followup(text, p["fact"]):
                # Not an answer: the question stays for another check-in, and the host's
                # automatic memory keeps the new fact.
                return self._with_queued_offer(f"Thank you for telling me, {self.name}.")
            return self._tool("remember", person_id=self.person_id,
                              fact=f"{p['fact'].rstrip('.')}. Later I said: \"{text.strip()}\"",
                              category="family", followup_of=p["memory_id"])

        if kind == "mood_ask":
            self.pending = None
            if no:
                return self._say("That's alright.")
            word = re.sub(r"^(?:i'?m|i am|feeling|i feel)\s+", "", low).strip(" .!")
            word = " ".join(word.split()[:4])[:40]
            if not word:
                return None
            return self._tool("log_mood_selfreport", person_id=self.person_id, word=word)
        return None

    # ---- scheduler events ----

    def _on_event(self, raw: str) -> tuple:
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            return self._say("")
        if event.get("kind") == "reminder":
            self.last_reminder = event
            return self._say(f"It's {event['spoken_time']}. Time to {second_person(event['text'])}. "
                             "Tell me when it's done.")
        if event.get("kind") == "checkin":
            greeting = f"Good {event['part_of_day']}, {self.name}."
            notes = int(event.get("pending_notes") or 0)
            note_offer = {"type": "offer_note"} if notes else None
            followups = event.get("followups") or []
            if followups:
                f = followups[0]
                self.pending = {"type": "followup", "memory_id": f["memory_id"], "fact": f["fact"]}
                self.queued_offer = note_offer
                fact = second_person(_lower_first(f["fact"]))  # "my granddaughter…" -> "your granddaughter…"
                return self._say(f"{greeting} Last time you mentioned that {fact}. How did it go?")
            concern = next((t for t in event.get("recent_impressions") or [] if CONCERN.search(t)), None)
            if concern:
                # Extra care after a hard day, without telling them what was noticed.
                self.pending = {"type": "mood_ask"}
                self.queued_offer = note_offer
                return self._say(f"{greeting} I hope you got some good rest. How are you feeling today?")
            if notes:
                self.pending = note_offer
                return self._say(f"{greeting} {self.default_contact} left you a message. Would you like to hear it?")
            self.pending = {"type": "mood_ask"}
            return self._say(f"{greeting} How are you feeling today?")
        return self._say("")

    # ---- after a tool result ----

    def _after_tool(self, result: dict) -> tuple:
        name = self.last_tool
        data = _result_data(result)
        if result.get("status") == "error":
            message = _ERROR_PREFIX.sub("", _result_text(result)) or "Sorry, something went wrong."
            return self._say(message)

        if name == "set_reminder":
            return self._say(data.get("spoken_confirmation", "Done."))
        if name == "complete_reminder":
            self.last_reminder = None
            return self._say("Got it, I've noted that's done. Well done.")
        if name == "log_mood_selfreport":
            comfort = {"seemed a bit tired": " Take it gently today.", "seemed a little low": " I'm here with you."}
            extra = next((comfort[c["text"]] for c in offline_cues(self.last_text) if c["text"] in comfort), "")
            return self._with_queued_offer(f"Thank you for telling me.{extra}")
        if name == "send_family_update":
            who = data.get("contact_name", self.default_contact)
            if self.last_args.get("extra_note"):
                return self._say(f"Done. I've passed that on to {who}.")
            return self._say(f"Done. I've sent {who} an update about your day.")
        if name == "get_due_events":
            notes = [e for e in data.get("events", []) if e.get("kind") == "note"]
            if not notes:
                return self._say("There are no new messages right now.")
            spoken = " ".join(f"{n['from_name']} says: \"{n['text']}\"" for n in notes)
            last = notes[-1]
            self.pending = {"type": "offer_reply", "note_id": last["note_id"], "contact": last["from_name"]}
            return self._say(f"{spoken} Would you like to reply?")
        if name == "reply_to_family_note":
            return self._say(f"Sent. {data.get('contact_name', self.default_contact)} will get your reply.")
        if name == "remember":
            happy = any(c["text"] in ("seemed cheerful", "seemed excited") for c in offline_cues(self.last_text))
            return self._with_queued_offer(("That's wonderful!" if happy else "Thank you for telling me.")
                                           + " I'll remember that.")
        if name == "recall":
            # 'past' = a plan whose follow-up answer is its own memory; reading both repeats it
            facts = [_spoken_memory(m["fact"]) for m in data.get("memories", []) if m.get("category") != "past"]
            if not facts:
                return self._say("I don't have anything remembered about you yet.")
            return self._say("Here's what I remember. " + " ".join(facts))
        if name == "get_person_context":
            contacts = data.get("contacts", [])
            if contacts:
                return self._say(" ".join(c["what_is_shared"] for c in contacts))
            return self._say("I don't share anything with anyone right now.")
        if name == "list_reminders":
            items = data.get("reminders", [])
            if not items:
                return self._say("You don't have any reminders coming up.")
            parts = [f"{second_person(r['text'])} at {r['spoken_time']}" + (" (done)" if r["done_today"] else "")
                     for r in items]
            return self._say("Here's what's coming up: " + "; ".join(parts) + ".")
        return self._say("Done.")

    def _with_queued_offer(self, text: str) -> tuple:
        if self.queued_offer:
            self.pending, self.queued_offer = self.queued_offer, None
            return self._say(f"{text} Also, {self.default_contact} left you a message. Would you like to hear it?")
        return self._say(text)


def _result_text(result: dict) -> str:
    return " ".join(c.get("text", "") for c in result.get("content", []) if isinstance(c, dict)).strip()


def _result_data(result: dict) -> dict:
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    text = _result_text(result)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}
