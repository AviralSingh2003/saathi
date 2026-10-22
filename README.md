# Saathi

**An ambient voice companion for Alexa+ that keeps isolated older people connected to their family, and gets
help fast when something is wrong.** It gets to know them over time, checks in, handles reminders, and
carries messages both ways. Behind the scenes it notices how they're doing, so family get an honest picture
("seemed cheerful today", "a bit quieter than usual"), a text if something seems worrying, and an evening
summary. In a medical emergency, emergency services are contacted first, then the family.

> **Simulated Alexa+ experience · self-hosted MCP server.** Self-hosted MCP servers can't be connected to a
> real Alexa+ device by hackathon participants (that tooling is partner-only), so Saathi ships a real MCP
> server (spec 2025-11-25, Streamable HTTP) plus a browser-based simulated Alexa+ host that talks to it as
> a genuine MCP client. **Emergency dispatch is simulated too**: no real emergency call is ever placed (see
> [Emergencies](#emergencies)).

## Judge quickstart (offline, no AWS credentials)

You need **Python 3.11+** only. This repo is self-contained. The browser kit it uses, **mcp-voice-sim**, is a
separate open-source repo, but its built bundles are already in `web/vendor/`, so you don't need to clone it
or install Node to run Saathi (see [Two repos](#two-repos-saathi-and-mcp-voice-sim)).

```bash
git clone https://github.com/AviralSingh2003/saathi.git
cd saathi
./demo.sh            # macOS / Linux / Git Bash
.\demo.ps1           # Windows PowerShell
```

First run creates `.venv` and installs dependencies. Then it seeds demo data, starts the MCP
server (`:8765/mcp`) and the host (`:8080`), and opens two tabs:

- **Elder view** — `http://127.0.0.1:8080/` (use **Chrome or Edge** for voice; typing works everywhere)
- **Family view** — `http://127.0.0.1:8080/family.html?token=priya-demo`
- **Priya's phone** — `http://127.0.0.1:8080/phone`: every text and email Saathi sends her, as she'd see them
  (not opened automatically)

The simulated clock starts at 08:59:50, so within a few seconds Saathi greets Meera on its own.
Press **D** on the elder view for the demo clock. Offline, every email and text to Priya is written to
`outbox/` (`*.eml`, `*.sms.txt`), and every simulated dispatch to `outbox/*-dispatch-*.txt`.

### Try this (type or say it)

| Time | You say | What happens |
|---|---|---|
| 09:00 | *(nothing — Saathi speaks first)* | "Good morning, Meera. Last time you mentioned that your grandson Arjun has a cricket match on Saturday. How did it go?" |
| | `They won by 20 runs!` | "That's wonderful!" Remembered; in the background Saathi notes she seemed cheerful |
| | `Remind me to take my BP tablet at 2` → `yes` | Time read back first; reminder card appears (MCP App) |
| | `My granddaughter Diya starts college next week.` | Remembered; a "Remembered: …" line appears |
| 09:30 | switch "Who's speaking?" to **Raj**, set the clock to 09:30; answer `Fine.` · `Not much.` · `I watched the news.` | His engagement falls well below his own usual. Priya gets a text: "Raj was quieter than usual this morning. You might want to check in." |
| 14:00 | *(Meera)* `I'm feeling pretty good` → `done` | Feeling logged in her words; reminder completed |
| | `Tell Priya the roses are out` | Sent at once by email and text: "Meera says: 'The roses are out.'" |
| family view | look, then leave a note | Recent alerts, **How Meera has seemed** (today, the last few days, insights), updates. Then "Mum, I'll call Sunday at 6. Love you." |
| 18:00 | `yes` → `tell her I'll be ready at 6` | Note read aloud; reply sent at once |
| 18:30 | *(Raj)* `My chest feels tight and my left arm has gone numb` | **Emergency services contacted first** (simulated), "Help is on the way", then Priya gets an urgent text + email saying help has been called |
| 19:30 | *(nothing)* | Evening summary to Priya for each of them (email + text), leading with the day's emergency |

Open the **MCP inspector** panel on either page to watch the live JSON-RPC traffic.
`python scripts/simulate_day.py` runs the same day headless and prints every message to Priya.
`python scripts/show_memory.py [--person meera]` lists what's stored (read-only, safe while the demo runs).

## What makes it work

- **Two real MCP clients, one server.** The host agent (Strands Agents SDK) is the "simulated Alexa+": it
  converses and calls Saathi's tools through `MCPClient` over Streamable HTTP. The family page is a direct
  browser MCP client (`initialize → tools/list → tools/call → resources/read`).
- **Reflection in the background after every turn.** The host calls `reflect_turn` without holding up the
  conversation. Claude reads the conversation along with what Saathi already knows, and decides:
  - what to remember, updating stale memories
  - how the person came across, in its own nuanced words
  - a running impression of the day
  - long-term insights
  - whether family should hear about a concern today

  Before every turn that knowledge goes back into the agent's system prompt, so Saathi gets more personal over time.
- **An engagement index, not a word count.** "Quieter than usual" is measured against the person's own
  14-day baseline. It looks at how much they say when asked, whether they bring things up, whether they
  share about their life, and whether they ask anything back. Only counts are stored.
- **Two-layer emergency detection, emergency services first.** See below.
- **16 MCP tools** — memory, reflection, reminders, due events, family updates and digests, two-way notes,
  settings, emergency escalation. See [docs/architecture.md](docs/architecture.md).
- **MCP Apps cards** — reminder and family dashboard (`ui://saathi/...`, `text/html;profile=mcp-app`),
  rendered in sandboxed iframes via the official `AppBridge`.
- **Agent Skill** — persona and guardrails in [`skills/saathi-companion/SKILL.md`](skills/saathi-companion/SKILL.md),
  loaded by Strands' native `AgentSkills` plugin and always applied via the system prompt.
- **AWS** — Claude on Amazon Bedrock (conversation, reflection, the emergency check, update phrasing),
  Amazon SES (email) and Amazon SNS (text messages), orchestrated with Strands. See
  [docs/aws-integration.md](docs/aws-integration.md).

## Emergencies

1. **Phrase check, instantly, before any model sees the words.** It covers heart attack and stroke signs,
   falls, breathing, collapse, overdose, self-harm and asking for help, plus a few Hindi phrases
   ([config/crisis_phrases.txt](config/crisis_phrases.txt)). Works offline.
2. **A Haiku check of every other message (live mode)**, in parallel with the conversation, with a
   **10-second limit** ([`host/emergency.py`](src/saathi/host/emergency.py)). It catches indirect wording ("my words
   are coming out wrong and my arm feels heavy"), context and other languages.
3. **If the check is unsure, too slow or fails**, Saathi asks "Are you okay? Do you need me to call for
   help?" and texts the family that it's checking. If they say yes, or don't answer within 30 seconds,
   emergency services are contacted.
4. **Medical emergency:** emergency services are contacted first. Then Saathi says help is on the way, and the
   family get an urgent text and email saying help has been called, with what was said and the time.
   **Self-harm:** crisis-line details, and an urgent text and email to the family. Urgent messages ignore the
   family's quiet settings.

**Dispatch is simulated.** Software can't safely dial real emergency lines: false calls are illegal in many
places, and telephony APIs don't support emergency calling. [`core/dispatch.py`](src/saathi/core/dispatch.py)
has a `SimulatedDispatcher` that records what would be sent (name, address, what they said, time) and is
labelled as simulated in every message. A real deployment would plug a professional monitored-response
service in behind the same interface, the way Alexa Emergency Assist connects to trained 24/7 agents.

## Companion, not clinician

- **Impressions, not assessments.** Impressions are hedged ("seemed", "sounded") and written kindly for
  family. A server-side safety net drops anything with clinical, psychiatric or cognitive language
  ("depressed", "confused", "cognitive decline"…), whatever the model wrote. The family page labels them "not
  a medical assessment".
- **The guardrails live in the prompts.** Claude's reflection prompt asks for natural language grounded in
  the conversation, a confidence level, and self-reports kept separate from inferences
  ([`core/reflect.py`](src/saathi/core/reflect.py)). There's no fixed vocabulary.
- **In the background, and honest.** The person isn't told Saathi's impressions; Saathi just responds with
  care. It never lies: asked "what do you share with Priya?", it says "I keep Priya up to date about your
  day: your reminders, what you tell me and how you've been."
- **Family choose how they're told** (email and/or text), never what's shared. Everything about a person can
  be deleted: `python scripts/wipe.py --person meera`.

## Live mode (Bedrock + SES + SNS)

Full walkthrough: **[docs/aws-setup-guide.md](docs/aws-setup-guide.md)**. It covers IAM, the Anthropic
use-case form, inference profile IDs, SES and SNS sandboxes, budgets, and troubleshooting. In short, copy
`.env.example` to `.env` and set:

```
MOCK=0
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0
SES_SENDER=<an SES-verified sender address>
PRIYA_EMAIL=<an SES-verified recipient (required while SES is in sandbox)>
SMS_ENABLED=1
PRIYA_PHONE=<an SNS-verified number in E.164, e.g. +9198…>
EMERGENCY_NUMBER=112            # or 911, 999…
```

Then run `python scripts/check_aws.py` (add `--send-test` to send one real email and one real text). If
Bedrock is unavailable mid-session, the host falls back to the deterministic router, and the instant phrase
check still protects every message.

**Email without SES.** Some AWS accounts (e.g. the new Free plan) can't use SES or SMS. Leave `SES_SENDER`
empty and set `SMTP_HOST`, `SMTP_USER` and `SMTP_PASSWORD`. For Gmail, that's `smtp.gmail.com` and an App
password. Saathi then emails through SMTP instead. The order is SES, then SMTP, then outbox copies only. A
copy of every email and text is always kept in `outbox/`, and `/phone` shows them.

## Tests

```bash
.venv/bin/python -m pytest          # (Windows: .venv\Scripts\python -m pytest) — all offline, about 90 s
cd e2e && npm install && npm test   # optional: real-browser run (installed Chrome/Edge), demo must be running
```

The Python suite covers:
- the protocol: 2025-11-25 negotiation, all 16 tools, card `_meta`, MCP Apps MIME type, Origin validation
- emergencies, with a stubbed Haiku verdict:
  - emergency services before family
  - indirect wording
  - timeout → ask → yes, fine, or no answer
  - rate limits
  - urgent alerts that ignore quiet settings
- texts through SNS (stubbed client) and the evening digest
- reflection: a stubbed Claude response is stored, clinical language is dropped, concern texts are rate-limited
- the engagement index, updates, notes and replies
- the full storyline end to end

## Repository layout

```
src/saathi/server/   MCP server: app (security, CORS), 16 tools, MCP Apps cards
src/saathi/core/     state + rules: memory, reflection, impressions, engagement, reminders, due events,
                     family loop, notify (SES + SNS), escalation, dispatch (simulated)
src/saathi/host/     simulated Alexa+: Strands agent, MockModel, emergency phrase list + Haiku check, WebSocket bridge
skills/              Agent Skill (SKILL.md + references)
web/                 elder + family pages; web/vendor = built bundles from mcp-voice-sim
scripts/             demo, seed, simulate_day, show_memory, wipe, check_aws
tests/, e2e/         pytest suite; optional browser run
docs/                architecture, AWS integration and setup, demo script, friction log, product feedback
```

## Two repos: Saathi and mcp-voice-sim

| Repo | What it is | Needs the other? |
|---|---|---|
| **saathi** (this one) | The product: MCP server, simulated Alexa+ host, web pages | **No.** `web/vendor/` holds mcp-voice-sim's built bundles, and `web/vendor/VERSION` records which commit they came from |
| [**mcp-voice-sim**](https://github.com/AviralSingh2003/mcp-voice-sim) | The browser kit Saathi's pages are built on: voice loop, MCP Apps host, Streamable HTTP client, inspector | **No.** It has its own example server, page and tests |

You only need both when you change the kit and want Saathi to use the new build. Clone them side by side:

```
workspace/
├── saathi/
└── mcp-voice-sim/
```

```bash
git clone https://github.com/AviralSingh2003/saathi.git
git clone https://github.com/AviralSingh2003/mcp-voice-sim.git

# 1. Saathi on its own (Python 3.11+)
cd saathi
./demo.sh                                   # Windows: .\demo.ps1

# 2. Rebuild the kit and copy it into Saathi (Node.js 22+), in a second terminal
cd ../mcp-voice-sim
npm install
npm run build                               # -> dist/mcp-voice-sim.js, dist/card-runtime.js
npm test
npm run vendor -- ../saathi/web/vendor      # copies both bundles and writes VERSION
```

Then reload the Saathi pages (Ctrl+Shift+R). No restart is needed: the host serves `web/` as static files.
Commit the updated `web/vendor/` files in Saathi so the repo stays self-contained.

## License

MIT — see [LICENSE](LICENSE).
