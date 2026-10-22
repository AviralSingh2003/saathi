# Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ BROWSER (served by the host on :8080)                                          │
│  Elder view (Echo-Show style)              Family view (/family.html)          │
│  • Web Speech in/out + typed fallback      • Direct MCP client (TS SDK v2,     │
│  • Own MCP session → resources/read →        Streamable HTTP): initialize →    │
│    renders ui:// cards (MCP Apps host)       tools/list → tools/call →         │
│  • JSON-RPC inspector                        resources/read                    │
└───────────────┬───────────────────────────────────────┬──────────────────────┘
                │ WebSocket (utterances, replies,        │ JSON-RPC over
                │ cards, host tool-call frames)          │ Streamable HTTP
                ▼                                        │
┌──────────────────────────────────────┐                 │
│ HOST = "simulated Alexa+"  (FastAPI)  │                 │
│ • Strands Agent per person            │                 │
│   - Claude on Amazon Bedrock (live)   │                 │
│   - MockModel (offline): same agent   │                 │
│     loop, real MCP tool calls         │                 │
│ • Agent Skill (SKILL.md) + guardrails │                 │
│ • Emergency layer 1: phrase list,     │                 │
│   BEFORE any model                    │                 │
│ • Emergency layer 2: Haiku check in   │                 │
│   parallel, 10 s limit (live)         │                 │
│ • Polls get_due_events → speaks first │                 │
│ • Each turn: context in; reflection   │                 │
│   runs in the background afterwards   │                 │
│ • Evening digest loop                 │                 │
│ • Strands MCPClient ──────────────────┼──────┐          │
└──────────────────────────────────────┘      │          │
                                              ▼          ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ SAATHI MCP SERVER (:8765/mcp — official MCP Python SDK, Streamable HTTP,       │
│ protocol 2025-11-25; Origin/Host validation; CORS exposes Mcp-Session-Id)      │
│  16 tools · 2 ui:// MCP Apps resources                                          │
│  core/: memory · reflection · impressions · engagement · reminders · due events │
│         family loop · notify · escalation · dispatch (simulated)               │
│  Bedrock Converse: reflection (memory, impressions, concern) and update text   │
└───────────────────────────────────────────────────┬──────────────────────────┘
                     ┌──────────────────────────────┼──────────────────────────┐
                     ▼                              ▼                          ▼
     Emergency dispatch (SIMULATED;     Amazon SES email (live)      Amazon SNS SMS (live)
     outbox/*-dispatch-*.txt)           / outbox/*.eml (offline)     / outbox/*.sms.txt (offline)
```

## Why this shape

On a real Alexa+ device, Alexa is the conversational model and an add-on supplies tools and state. Saathi
mirrors that: the host agent plays Alexa+, and the MCP server never runs a "chat" tool of its own. State,
delivery and every safety rule live in the server.

## Who decides what

| Decision | Who makes it |
|---|---|
| Which tool to call for an utterance | Host model (Claude or MockModel); the server validates every parameter |
| Which person is speaking | The host (a hook overwrites `person_id`; the model can't choose it) |
| Whether a reminder / check-in / note / digest is due | Server, from the DB and the clock |
| Whether it's an emergency | Host: phrase list first (instant), then Claude Haiku (10 s limit); unsure/slow → ask the person |
| What happens in a medical emergency | Fixed: emergency services first (simulated dispatcher), then fixed urgent text + email to family, then a fixed spoken script. The conversational model can't trigger or word it |
| What gets remembered | **Claude** (reflection), in its own words, updating stale memories. Offline: verbatim rules |
| How the person seemed | **Claude** (reflection), free text, guardrails in its prompt; the server drops clinical/diagnostic language. Offline: a few cues |
| Whether family get a concern text today | **Claude** (reflection's `concern`); server: clinical net, at most one per 6 h |
| "Quieter / chattier than usual" | Server arithmetic: engagement index vs the person's own 14-day baseline, given to Claude as signals |
| Update wording | Bedrock over today's facts → no unbacked mood words, no clinical words → template fallback |
| How family are told | The family member: `notify_email`, `notify_sms` (urgent alerts ignore them) |

## A turn

```
utterance ─► answer to "Are you okay?" pending?  ─ yes/help ─► dispatch ─► family ─► "Help is on the way"
          │                                     └ fine ─────► family: all clear
          ├► phrase list match? ──────────────────────────────► (same emergency path; no model)
          ├► live: Haiku check  ║  conversation turn (parallel)
          │     dispatch / self-harm / asked for help ─► emergency path (conversation reply dropped)
          │     unsure, >10 s or error ─► "Are you okay? Do you need me to call for help?" + text to family
          │                                └ no answer in 30 s ─► (next poll) dispatch ─► family
          │     none ─► the conversation reply is spoken
          └► afterwards, in the background: reflect_turn
                ├─ engagement signals → turn_stat (counts only)
                └─ Claude: memories · impressions · day impression · insights · concern ─► concern text
evening: send_due_digests ─► email + text per person (the day's emergencies first)
```

## Tools

| Tool | Caller | Notes |
|---|---|---|
| `get_person_context` | host (before every turn) | profile, contacts (+ truthful "what is shared"), recent memories, today's reminders, waiting notes, and `knowledge`: insights, memories relevant to `query`, how they've seemed |
| `remember` / `recall` | host model | small facts; `followup_of` retires an answered follow-up |
| `reflect_turn` | host itself, in the background after every turn | Claude: memories (can `replace`), impressions, day impression, insights, concern → text; engagement index. Hidden from the model |
| `set_reminder` | host model | **MCP App**: reminder card; read back before calling |
| `list_reminders` / `complete_reminder` | host model (family with token: list) | |
| `log_mood_selfreport` | host model | the person's own words |
| `get_due_events` | host | reminders + check-ins; `kinds=["note"]` hands over family notes |
| `send_family_update` | host model | sends today's update at once (email + text); `extra_note` carries their words |
| `send_due_digests` | host itself (loop) | evening summary per person, once a day after `DIGEST_TIME`. Hidden from the model |
| `leave_family_note` | family (token) | hidden from the model |
| `reply_to_family_note` | host model | exact words, sent at once (email + text) |
| `get_family_dashboard` | family (token) | **MCP App**: alerts, how they've seemed, updates, notes |
| `update_settings` | host (check-in times, speech rate) / family (own `notify_email`, `notify_sms`) | |
| `escalate_to_contact` | host emergency path only | `medical_emergency` (dispatch first), `self_harm`, `asked_for_help`, `checking`, `all_clear`. Hidden from the model |

User-facing errors are raised as the SDK's `ToolError` (via `SaathiError`), so the message reaches the caller;
any other exception is reported as a crash without leaking details.

## Demo clock

`core/clock.py` keeps an offset in the database, shared by server, host and scripts.
`POST /dev/clock {"set": "14:00"}` (DEMO_MODE only) jumps to the next 14:00; the elder page's **D** panel
uses it. Emergency confirmation deadlines and the digest use the same clock.

## Versions

`mcp==2.1.1` is pinned because `strands-agents==1.60.0` requires `mcp<2.2`. 2.1.1 negotiates 2025-11-25 on
`initialize` and includes the MCP Apps extension (`mcp.server.apps`). The browser uses
`@modelcontextprotocol/client` 2.3 and `@modelcontextprotocol/ext-apps` 2.0 via the mcp-voice-sim bundle.
