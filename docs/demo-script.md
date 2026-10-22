# Demo video script (target 2:55, English, no third-party music)

> **Current plan:** [video-transcript.md](video-transcript.md) has the final voice-over and shot list, and
> `cd e2e && npm run demo` plays the demo half automatically for recording. This page is the earlier outline.

Pitch shape: problem → working product → who it's for → how it uses Alexa+ (MCP) and AWS.
Keep the label **"Simulated Alexa+ experience · self-hosted MCP server"** on screen throughout (it's in the
elder view's header), and say once that emergency dispatch is simulated.

**Setup before recording:**
- `.env` in live mode (Bedrock + SES + SNS).
- Priya's email and phone verified, and the phone on camera.
- Reset with `python scripts/seed.py`, then start `./demo.sh`.
- The lines below are what the offline router understands too, so a mock-mode take works as a fallback.
- **If texts can't be sent** (SNS unavailable): film **`/phone`** ("Priya's phone") in a browser window beside
  the elder view. It shows every text and email as they arrive. Emails can still reach a real inbox through
  Gmail SMTP (setup guide, step 6b).

| Time | Shot | Lines |
|---|---|---|
| 0:00–0:12 | Still of an empty kitchen, then the elder view | "Millions of older people live alone. Their families live far away, and worry." |
| 0:12–0:40 | Elder view; Saathi speaks first at 09:00 | Saathi: "Good morning, Meera. Last time you mentioned Arjun's cricket match on Saturday. How did it go?" — Meera: "They won by 20 runs!" — "That's wonderful!" Meera: "Remind me to take my BP tablet at 2." Read-back, "Yes", reminder card. |
| 0:40–1:05 | Switch to Raj, clock 09:30; then Priya's phone | Raj: "Fine." "Not much." "I watched the news." Voice-over: "Raj usually chats about the morning news. Saathi notices, measured against his own usual, and quietly tells his daughter." **Priya's phone buzzes:** "Raj was quieter than usual this morning. You might want to check in." |
| 1:05–1:25 | Meera at 14:00 | Reminder fires; "I'm feeling pretty good"; "Done." Meera: "Tell Priya the roses are out." → Priya's phone: "Meera says: 'The roses are out.'" |
| 1:25–1:50 | Family view | **Recent alerts**, **How Meera has seemed** (today, the last few days, "Lights up when talking about her garden and her grandchildren", labelled *not a medical assessment*). Priya leaves "Mum, I'll call Sunday at 6. Love you." 18:00: Meera hears it and replies; the reply reaches Priya. |
| 1:50–2:30 | **The emergency** (Raj, 18:30) | Raj: "My chest feels tight and my left arm has gone numb." Saathi, instantly: "I've called emergency services, Raj. Help is on the way…" Cut to the dispatch record ("SIMULATED EMERGENCY DISPATCH"), then Priya's phone: "URGENT: Emergency services have been contacted for Raj at 18:30…". Voice-over: "Emergency services first, family second. An instant check catches the obvious; Claude Haiku catches the rest, in any language, and if it can't answer within ten seconds, Saathi asks him directly and escalates if he doesn't reply." |
| 2:30–2:55 | Architecture slide (8 s), then close | "Saathi gets to know Mum and Dad, and makes sure help is never far away." Tools: self-hosted MCP server, Strands Agents, Claude on Amazon Bedrock (conversation, reflection, emergency check), Amazon SES and SNS. "Companion, not clinician: impressions, never diagnoses." |

**Don't show:** music, smart-home control, any real emergency number being dialled (dispatch is simulated).
**Do show:** the "simulated" label on the dispatch record, and "not a medical assessment" on Priya's page.
