# Video transcript (target under 3:00)

Two parts: the story you've already filmed (about 1:27) and the product demo (about 1:28 after editing),
which the demo driver plays for you (`cd e2e && npm run demo`). Voice-over is in plain text; *italics* are
what's on screen.

Keep **"Simulated Alexa+ experience · self-hosted MCP server"** visible during the demo (the stage shows it),
and the caption **"Emergency dispatch is simulated in this prototype"** during the emergency scene.

---

## Part 1: the story (your footage, about 1:27)

Your narration, with four accuracy fixes: the product is **Saathi** (built for Alexa+, not Alexa's own
feature), emergency services are **contacted (simulated)** rather than "911 called", it reacts to what she
**says** rather than detecting falls, and the alert quotes the real message. Check the two statistics against
your source and show it as a small caption, or use the softer wording in brackets.

> Every morning begins in quiet routine.
> A cup of tea… warm sunlight… and long hours of silence.
> Miles away, the work never stops…
> but Priya's thoughts never leave home. Wondering if Ma ate… or if she feels alone.
> Behind closed doors, one in four older adults lives with chronic loneliness, doubling the risk of depression. *[caption: source]*
> Yet daily social connection reduces that risk by up to thirty percent. *[caption: source, or: "Yet staying connected every day makes a real difference."]*
> That's where **Saathi** steps in, a voice companion built for Alexa+. It notices when Ma seems low or quieter than usual, and gently lets her daughter know before worry takes root.
> More than a companion, it acts when it matters most. If she mentions a fall, chest pain or the signs of a stroke, Saathi **contacts emergency services first**, then sends an urgent alert: "Emergency services have been contacted. Please try to reach her now." *[caption: emergency dispatch simulated in this prototype]*
> With this safety net, the weight of constant worry lifts.
> Checking in is as easy as saying hello across the room.
> Two distant worlds meeting in the middle.
> No complicated apps, just the quiet reassurance that someone is always looking out for her.
> For Priya, the worry turns into peace.
> Knowing her mother is never truly on her own.

*(Move "Because care isn't about being in the same room…" and the tagline to the very end of the video.)*

---

## Part 2: the demo (about 1:28 after editing)

The driver's scenes, in order. Saathi's exact wording varies a little in live mode (Claude writes it);
the lines below are what it said in a test run.

| Time | Scene | On screen | Voice-over |
|---|---|---|---|
| 0:00–0:16 | **1. Saathi speaks first, and remembers** | *Saathi: "Good morning, Meera… last time you mentioned Arjun's cricket match. How did it go?"* · Meera types *"They won, and Arjun hit the winning shot!"* · *Saathi replies warmly* | "Saathi speaks first, and remembers what matters to her." |
| 0:16–0:24 | **2. A reminder** | *"Remind me to take my BP tablet at 2"* → *read-back* → *"Yes, please"* → *the reminder card appears* | *(no voice-over)* |
| 0:24–0:42 | **3. A quiet morning** | *Raj, 9:30. Saathi checks in. Raj: "Fine." "Not much." "I watched the news."* · *Priya's phone: "Raj was quieter than usual this morning. You might want to check in."* | "Raj usually loves to chat. Today he's quiet, compared with his own usual, so Saathi gently lets Priya know." |
| 0:42–0:52 | **4. A message for Priya** | *Meera: "Tell Priya the roses are out"* · *Priya's phone: "Meera says: 'The roses are out.'"* (+ the real email on your phone) | "And Meera's own words reach her daughter instantly." |
| 0:52–1:14 | **5. The emergency** | *Raj: "My words are coming out all wrong and my arm feels heavy"* · *Saathi: "I've called emergency services, Raj. Help is on the way…"* · *Priya's phone, red: "URGENT: Emergency services have been contacted for Raj…"* · *caption: dispatch simulated* | "No keywords, but Claude recognises the signs of a stroke within seconds. Emergency services first, then Priya." |
| 1:14–1:24 | **6. Under the hood** | *Priya's family page: "How Meera has seemed", insights, updates* · *the MCP inspector: tools/call · reflect_turn · emergency_check · escalate_to_contact* | "Built on a real MCP server, with Strands and Claude on Amazon Bedrock." |
| 1:24–1:28 | **End card** | *Saathi · Closer than distance.* | "Because care isn't about being in the same room. It's about staying close at heart. Saathi. Closer than distance." |

### Editing to fit
- The driver run takes about 2:30–3:30 with spoken replies and Claude's thinking time. Cut the waits:
  speed up typing and Claude's ~2-second pauses (2–3×), and cut straight to each phone notification.
- Keep the emergency scene at full length; it's the one judges remember. Trim scene 2 first if you're over.
- If live Claude doesn't send the "quiet morning" note in one take, run again (each run is fresh) or use
  the offline take for that scene (`MOCK=1`), where it's deterministic.
- Offline, scene 5 uses "My chest feels tight and my left arm has gone numb" (the instant phrase check);
  live, it uses the no-keyword stroke line so the Haiku check is what catches it.
