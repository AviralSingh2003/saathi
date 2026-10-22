"""The 16 Saathi MCP tools.

Tools are thin: they validate input and delegate to `saathi.core`. Two tools
are MCP Apps tools and point at `ui://saathi/...` cards via `_meta.ui.resourceUri`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from mcp.server import MCPServer
from mcp.server.apps import Apps

from saathi.core import due, engagement, escalation, family, impressions, memory, people, reflect, reminders
from saathi.server import cards

INSTRUCTIONS = (
    "Saathi keeps an older person connected to their family. It stores memories and reminders, "
    "reports due events (reminders, check-ins, family notes), keeps impressions of how the person seems, "
    "keeps their family updated, and handles emergencies. Rules for any agent using these tools: never give "
    "medical advice or diagnose; when the person says how they feel, log it with log_mood_selfreport in their "
    "own words; read reminder times back before setting them; never tell the person what Saathi has "
    "concluded about their mood, and never lie to them if they ask what is shared."
)

FAMILY_ONLY_TOOLS = ("leave_family_note", "get_family_dashboard")
# Called by the host itself, never chosen by the conversational model.
HOST_ONLY_TOOLS = ("reflect_turn", "send_due_digests", "escalate_to_contact")
HIDDEN_FROM_AGENT = FAMILY_ONLY_TOOLS + HOST_ONLY_TOOLS


# ---- result models (structured output) ----

class PersonContext(BaseModel):
    person_id: str
    display_name: str
    checkin_times: list[str]
    contacts: list[dict]
    recent_memories: list[dict]
    todays_reminders: list[dict]
    pending_family_notes: int
    knowledge: dict = {}


class MemoryResult(BaseModel):
    memory_id: int
    stored: bool
    fact: str


class RecallResult(BaseModel):
    memories: list[dict]


class ReminderResult(BaseModel):
    reminder_id: int
    text: str
    time: str
    date: str | None
    recurrence: str
    when_text: str
    spoken_confirmation: str


class RemindersList(BaseModel):
    reminders: list[dict]


class ReminderDone(BaseModel):
    reminder_id: int
    status: str
    text: str
    done_at: str


class LogResult(BaseModel):
    log_id: int
    recorded_word: str


class DueEvents(BaseModel):
    now: str
    events: list[dict]


class DeliveryResult(BaseModel):
    status: str
    channels: list[str] = []
    contact_name: str | None = None
    sent_text: str | None = None
    phrasing: str | None = None
    errors: list[str] = []


class EscalationResult(BaseModel):
    status: str
    reason_code: str
    dispatched: bool
    reference: str | None = None
    provider: str | None = None
    number: str | None = None
    dispatched_at: str | None = None
    contact_name: str | None = None
    contact_names: list[str] = []
    delivered: int = 0
    errors: list[str] = []


class DigestResult(BaseModel):
    sent: list[dict]


class NoteResult(BaseModel):
    note_id: int
    status: str
    delivery: str


class Dashboard(BaseModel):
    person_name: str
    contact_name: str
    can_leave_notes: bool
    shared_with_you: str
    updates: list[dict]
    notes: list[dict]
    impressions: dict | None = None
    alerts: list[dict] = []


class ReflectResult(BaseModel):
    remembered: list[dict]
    impressions: list[dict]
    day_impression: str | None
    insights: list[dict]
    engagement: dict | None
    rejected: list[dict]
    method: str
    note: str | None = None
    concern_sent: str | None = None


class SettingResult(BaseModel):
    key: str
    value: bool | float | list[str]


def build_server() -> MCPServer:
    apps = Apps()
    cards.register(apps)

    # ---- MCP Apps tools (render cards) ----

    @apps.tool(
        resource_uri=cards.REMINDER_CARD,
        description="Set a reminder for the person. Read the time back to them and get a yes before calling. "
        "time is 24-hour HH:MM; recurrence is 'none' or 'daily'.",
    )
    def set_reminder(person_id: str, text: str, time: str, date: str | None = None,
                     recurrence: Literal["none", "daily"] = "none") -> ReminderResult:
        return ReminderResult(**reminders.create(person_id, text, time, date, recurrence))

    @apps.tool(
        resource_uri=cards.FAMILY_DASHBOARD_CARD,
        description="Family view: recent alerts, Saathi's impressions of how the person has seemed, the updates "
        "sent, and the notes thread. Requires the family member's access token.",
    )
    def get_family_dashboard(contact_token: str) -> Dashboard:
        return Dashboard(**family.dashboard(contact_token))

    mcp = MCPServer("saathi", instructions=INSTRUCTIONS, version="0.1.0", extensions=[apps])

    # ---- plain tools ----

    @mcp.tool(description="Session grounding: profile, contacts, recent memories, today's reminders, how many "
              "family notes are waiting, and what Saathi has learned (insights, memories relevant to `query`, how "
              "they've seemed today and lately). Use it to answer 'what do you share with <name>?'.")
    def get_person_context(person_id: str, query: str = "") -> PersonContext:
        person = people.get_person(person_id)
        contact_rows = people.contacts(person_id)
        knowledge = {
            "relevant_memories": memory.recall(person_id, query, 5) if query.strip() else [],
            "insights": [m["fact"] for m in memory.insights(person_id)],
            "today": {"day_impression": impressions.day_text(person_id),
                      "impressions": [i["text"] for i in impressions.on_day(person_id)][-6:],
                      "engagement": engagement.today(person_id)},
            "recent_days": impressions.recent_days(person_id, 3),
        }
        return PersonContext(
            knowledge=knowledge,
            person_id=person_id,
            display_name=person["display_name"],
            checkin_times=person["checkin_times"],
            contacts=[{"contact_id": c["contact_id"], "name": c["name"], "relationship": c["relationship"],
                       "receives_updates": c["consent_granted"],
                       "what_is_shared": people.share_summary(person, c)} for c in contact_rows],
            recent_memories=memory.recall(person_id, "", 5),
            todays_reminders=reminders.list_upcoming(person_id, 24),
            pending_family_notes=due.pending_notes_count(person_id),
        )

    @mcp.tool(description="Store a small fact the person shared, for later conversations. "
              "category: general | family | health | upcoming | preference. If the fact answers a check-in "
              "follow-up, pass that follow-up's memory_id as followup_of so it isn't asked about again.")
    def remember(person_id: str, fact: str, category: str = "general",
                 followup_of: int | None = None) -> MemoryResult:
        return MemoryResult(**memory.remember(person_id, fact, category, followup_of))

    @mcp.tool(description="Host use, after every turn: reflect on what the person just said. Saathi decides what to "
              "remember, how they seemed, a running impression of today and long-term insights, and measures "
              "engagement against their own usual; if something seems worrying, the family get a short text. "
              "Pass their exact words, Saathi's previous reply, the recent conversation and the tools already "
              "called this turn.")
    def reflect_turn(person_id: str, utterance: str, previous_reply: str | None = None,
                     recent_turns: list[dict] | None = None, tools_called: list[str] | None = None) -> ReflectResult:
        return ReflectResult(**reflect.reflect(person_id, utterance, previous_reply, recent_turns, tools_called))

    @mcp.tool(description="Recall stored facts about the person by keyword (empty query = most recent).")
    def recall(person_id: str, query: str = "", limit: int = 5) -> RecallResult:
        return RecallResult(memories=memory.recall(person_id, query, limit))

    @mcp.tool(description="List upcoming reminders in the next window_hours, including ones done today. "
              "Family callers must pass their contact_token.")
    def list_reminders(person_id: str, window_hours: int = 24, contact_token: str | None = None) -> RemindersList:
        if contact_token:
            contact = people.contact_by_token(contact_token)
            if contact["person_id"] != person_id:
                return RemindersList(reminders=[])
        return RemindersList(reminders=reminders.list_upcoming(person_id, window_hours))

    @mcp.tool(description="Mark a reminder as done when the person says they've done it.")
    def complete_reminder(reminder_id: int) -> ReminderDone:
        return ReminderDone(**reminders.complete(reminder_id))

    @mcp.tool(description="Record how the person says they feel, when they state it, using their own words "
              "(e.g. 'pretty good'). Impressions of how they seem are kept separately by the host.")
    def log_mood_selfreport(person_id: str, word: str) -> LogResult:
        log_id = people.log_mood_selfreport(person_id, word)
        return LogResult(log_id=log_id, recorded_word=word.strip().strip('"').strip())

    @mcp.tool(description="Everything due now: reminders and check-ins (default). Pass kinds=['note'] to hand over "
              "waiting family notes once the person wants to hear them. Each event is returned only once.")
    def get_due_events(person_id: str, kinds: list[Literal["reminder", "checkin", "note"]] | None = None) -> DueEvents:
        from saathi.core import clock

        events = due.due_events(person_id, list(kinds) if kinds else None)
        return DueEvents(now=clock.iso(clock.now()), events=events)

    @mcp.tool(description="Send a family member an update about the person's day now (email + text). Pass "
              "extra_note with anything the person asked you to tell them, in their own words. Don't read the "
              "update back to the person; just say it's been sent.")
    def send_family_update(person_id: str, contact: str, extra_note: str | None = None) -> DeliveryResult:
        return DeliveryResult(**family.send(person_id, contact, extra_note))

    @mcp.tool(description="Family view: leave a note for the person. Requires the family member's access token.")
    def leave_family_note(contact_token: str, text: str) -> NoteResult:
        return NoteResult(**family.leave_note(contact_token, text))

    @mcp.tool(description="Send the person's reply to a family note (email + text), in their exact words.")
    def reply_to_family_note(note_id: int, text: str) -> DeliveryResult:
        return DeliveryResult(**family.reply(note_id, text))

    @mcp.tool(description="Host use: send the evening summary (email + text) to each person's family once the "
              "digest time has passed today. Safe to call repeatedly; each day's digest is sent once.")
    def send_due_digests() -> DigestResult:
        return DigestResult(sent=family.send_due_digests())

    @mcp.tool(description="Change a setting. Keys for the person: checkin_times ('09:00,18:00') and speech_rate. "
              "A family member (with contact_token) may change only how they're notified: notify_email, notify_sms.")
    def update_settings(person_id: str, key: str, value: str, contact_token: str | None = None) -> SettingResult:
        if contact_token:
            contact = people.contact_by_token(contact_token)
            return SettingResult(**people.update_contact_setting(contact, key, value))
        result = people.update_setting(person_id, key, value)
        return SettingResult(**result)

    @mcp.tool(description="Host emergency path only. medical_emergency contacts emergency services first, then "
              "sends the family an urgent text + email saying help has been called. self_harm / asked_for_help: "
              "urgent text + email. checking / all_clear: Saathi is checking on them / they said they're okay. "
              "`said` is what the person said. Messages are fixed and never diagnose.")
    def escalate_to_contact(person_id: str,
                            reason_code: Literal["medical_emergency", "self_harm", "asked_for_help", "checking",
                                                 "all_clear"],
                            said: str = "") -> EscalationResult:
        result = escalation.escalate(person_id, reason_code, said)
        return EscalationResult(**{k: v for k, v in result.items() if k in EscalationResult.model_fields})

    return mcp
