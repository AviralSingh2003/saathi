# Devpost submission (ready to paste; fill in the links)

**Project name:** Saathi
**Tagline:** An ambient voice companion that keeps isolated older people connected to their family, and gets help fast when something's wrong.
**Primary track:** Alexa+ · **Mini-challenges:** AWS Builder, Open Source

## Description

Millions of older people live alone, and their families live far away and worry. Saathi is an ambient voice
companion for Alexa+ that keeps them connected. It gets to know them over time, checks in, handles
reminders, and carries messages both ways. Behind the scenes it notices how they're doing, so family get an
honest picture, a note when something seems off, and an evening summary. In a medical emergency it contacts
emergency services first, then the family.

**How it works.** Saathi is a self-hosted MCP server (spec 2025-11-25, Streamable HTTP) with 16 tools for
memory and reflection, reminders, due events, family updates and digests, two-way family notes and
emergency escalation. Because participants can't connect self-hosted servers to real Alexa+ devices, we
built a simulated Alexa+ host: a Strands Agents SDK agent running Claude on Amazon Bedrock, connected to the
server as a real MCP client, with a browser voice interface. A family web page is a second, direct MCP
client. Emergency dispatch is simulated (no real emergency call is placed), behind a plug-in interface for
a professional response service.

**What's different**
- *A companion that gets to know them.* After every turn, in the background, Claude reflects on the
  conversation. It decides what to remember, how the person came across, what it's learning over days, and
  whether family should hear about something today. Saathi brings that knowledge into every conversation.
  "Quieter than usual" comes from an engagement index measured against the person's own baseline, not a
  word count.
- *Emergency services first.* An instant phrase check catches the obvious. A Claude Haiku check runs
  alongside every other message, with a 10-second limit, and catches indirect wording and other languages.
  If it's unsure or too slow, Saathi asks "Are you okay?" and escalates if there's no answer. Family get an
  urgent email and text saying that help has already been called.
- *Two-way.* Family leave notes, Saathi delivers them at the next check-in, and replies go straight back.
- *Companion, not clinician.* Impressions are hedged and never diagnostic: a server-side safety net drops
  clinical language. They're labelled "not a medical assessment" on the family page. Saathi never lies to
  the person about what it shares.
- *MCP Apps + Agent Skill.* Reminder and family-dashboard cards render as MCP Apps; persona and guardrails
  ship as an Agent Skill.

**Built with:** Model Context Protocol (Python SDK, TypeScript SDK, MCP Apps), Agent Skills, Strands Agents
SDK, Amazon Bedrock (Claude), Amazon SES and Amazon SNS (implemented; see the note below), Python, FastAPI,
Web Speech API.

**Note on email and texts in the demo.** Saathi sends email with Amazon SES and texts with Amazon SNS. Our
AWS account is on the Free plan, which blocks both (an organization policy denies SES, and SMS isn't
available). So in the video, emails go through Gmail SMTP, the built-in fallback, and texts are shown on
the "phone" page exactly as they would be sent. Emergency dispatch is simulated.

## Links
- Repo: https://github.com/AviralSingh2003/saathi (public, MIT)
- Video: TODO (YouTube/Vimeo, public, < 3:00)

## AWS Builder
Strands orchestrates a Claude-on-Bedrock agent that calls the MCP server, with its system prompt refreshed
every turn from Saathi's long-term memory. Bedrock is also used for:
- a time-limited Haiku emergency check on every message, running in parallel with the conversation
- a background reflection after every turn (memories, impressions, insights, concerns)
- update phrasing, checked, with a template fallback

Family email goes out through Amazon SES and texts through Amazon SNS, including urgent emergency alerts.
Both are implemented and covered by tests with stubbed AWS calls, but our Free-plan account blocks them, so the demo
emails through the Gmail SMTP fallback. Details in `docs/aws-integration.md`.

## Open Source
A new, additional open-source project (new repo, MIT), created during the hackathon window.

- Contribution URL: https://github.com/AviralSingh2003/mcp-voice-sim
- Project repository URL: https://github.com/AviralSingh2003/mcp-voice-sim. The project itself; Saathi's repo
  is the main submission's repo link.
- GitHub username: AviralSingh2003
- **What I did:** built and released mcp-voice-sim, a framework-free browser kit for simulated voice-assistant
  experiences on MCP. It provides a Streamable HTTP client that records every JSON-RPC frame, a Web Speech
  loop with typed fallback, a minimal MCP Apps host, a live protocol inspector, and a runtime for
  single-file MCP App cards. Saathi's elder and family pages are built on it.
- **How it works:** ES modules on the official MCP TypeScript SDK (`@modelcontextprotocol/client`) and the
  MCP Apps SDK (`@modelcontextprotocol/ext-apps`). Cards are fetched as `ui://` resources and rendered in
  sandboxed iframes through the official `AppBridge`, which is connected before the card loads so the
  handshake is never missed. Bundled with esbuild into one import with no framework; tested with Vitest;
  includes a minimal example MCP server and page.
- **Why it matters:** the official Alexa+ add-on tooling is partner-only, so anyone who builds an MCP server
  for an Alexa+-style experience has to build their own host to demo it. This kit gives them voice, MCP
  Apps cards and a visible, genuine MCP session in one import, reusable beyond this hackathon.

## Product feedback
See `docs/product-feedback.md`.

## Friction log
See `docs/friction-log.md`.
