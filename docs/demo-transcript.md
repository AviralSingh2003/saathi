# Demo transcript

Produced by `python scripts/simulate_day.py` (offline mode: deterministic router, real MCP calls through the
Strands agent). Live mode follows the same flow with Claude's own wording and impressions.

- `[remembered: …]` and `[impression: …]` come from `reflect_turn`, which runs in the background after each
  turn. Only "Remembered" lines appear on the elder's screen; impressions stay on the family side.
- `-> Priya` lines are the texts (Amazon SNS) and emails (Amazon SES) sent to her phone. Offline they are
  files in `outbox/`.
- `-> EMERGENCY` is the simulated dispatch record.

```
--- 09:00 ---
 saathi: Good morning, Meera. Last time you mentioned that your grandson Arjun has a cricket match on Saturday. How did it go?
  meera: They won by 20 runs!
 saathi: That's wonderful! I'll remember that.   [impression: seemed cheerful]
  meera: Remind me to take my BP tablet at 2
 saathi: Just to check: remind you to take your BP tablet at 2 in the afternoon?
  meera: yes
 saathi: Okay. I'll remind you to take your BP tablet at 2 in the afternoon.   [card: set_reminder]
  meera: My granddaughter Diya starts college next week.
 saathi: Thank you for telling me, Meera.   [remembered: "My granddaughter Diya starts college next week"]

--- 09:30 ---
 saathi: Good morning, Raj. How are you feeling today?
    raj: Fine.
 saathi: Thank you for telling me.
    raj: Not much.
 saathi: That's alright, Raj. I'm here if you'd like to chat, or I can set a reminder or send Priya an update.
    raj: I watched the news.
 saathi: Thank you for telling me, Raj.   [impression: was quieter than usual]
         -> Priya (email): A note about Raj
         -> Priya (SMS): Saathi: Raj was quieter than usual this morning. You might want to check in.

--- 14:00 ---
 saathi: It's 2 in the afternoon. Time to take your BP tablet. Tell me when it's done.
  meera: I'm feeling pretty good
 saathi: Thank you for telling me.
  meera: done
 saathi: Got it, I've noted that's done. Well done.
  meera: Tell Priya the roses are out
 saathi: Done. I've passed that on to Priya.
         -> Priya (email): An update about Meera
         -> Priya (SMS): Saathi: Meera says: "The roses are out." Today's update is in your email.
 family: Mum, I'll call Sunday at 6. Love you.

--- 18:00 ---
 saathi: Good evening, Meera. Priya left you a message. Would you like to hear it?
  meera: yes
 saathi: Priya says: "Mum, I'll call Sunday at 6. Love you." Would you like to reply?
  meera: tell her I'll be ready at 6
 saathi: Sent. Priya will get your reply.
         -> Priya (email): Meera replied to your message
         -> Priya (SMS): Saathi: Meera replied: "I'll be ready at 6"
  meera: what do you share with Priya?
 saathi: I keep Priya up to date about your day: your reminders, what you tell me and how you've been. If you ever need help urgently, I'll get help and let Priya know straight away.
  meera: what do you remember about me?
 saathi: Here's what I remember. Your granddaughter Diya starts college next week. Your grandson Arjun has a cricket match on Saturday. Later you said: "They won by 20 runs!" Priya usually calls on Sunday evenings. You love tending the roses in your garden.

--- 18:30 ---
    raj: My chest feels tight and my left arm has gone numb
 saathi: I've called emergency services, Raj. Help is on the way. If you can, stay where you are and keep the door unlocked. I'm letting Priya know right now.
         -> EMERGENCY: SIMULATED EMERGENCY DISPATCH (no real call was placed), Reference: SIM-6247, Reason: medical_emergency
         -> Priya (email): URGENT: emergency services contacted for Raj
         -> Priya (SMS): URGENT from Saathi: Emergency services have been contacted for Raj at 18:30 (911, ref SIM-6247, simulated in this demo). Raj said: "My chest feels tight and my left arm has gone numb". Please try to reach Raj now.

--- 19:30 ---
         -> Priya (email): Meera's day (Thu 22 Oct)
         -> Priya (SMS): Saathi, Meera's day: Meera completed the reminder "take my BP tablet" at 2 in the afternoon. Meera described feeling "pretty good". Asked about "My grandson Arjun has a cricket match on Saturday", Meera said: "They won by 20 runs!" More in your email.
         -> Priya (email): Raj's day (Thu 22 Oct)
         -> Priya (SMS): Saathi, Raj's day: Emergency services have been contacted for Raj at 18:30 (911, ref SIM-6247, simulated in this demo). Raj described feeling "fine". Raj was quieter than usual today.
```

**Why Raj "was quieter than usual".** Each of his turns was counted: three short answers, nothing he
brought up himself, no mention of people or plans, no questions back. His engagement index came out well
below his own two-week baseline, so Priya got a gentle text. Raj wasn't told.

**The emergency.** The phrase list caught "chest feels tight" and "arm has gone numb" instantly, before any
model. Emergency services were contacted first, then Priya. Wording the list doesn't know ("my words are
coming out wrong and my arm feels heavy") is caught in live mode by the Haiku check, or by "Are you okay?"
if Haiku is slow.

The simulated dispatch record (`outbox/*-dispatch-*.txt`):

```
SIMULATED EMERGENCY DISPATCH (no real call was placed)
Reference: SIM-6247
Time: 2026-10-22T18:30:00
Service: 911
Person: Raj Sharma (Raj)
Address: House 7, Green Park, Pune (demo address)
Reason: medical_emergency
They said: "My chest feels tight and my left arm has gone numb"
```

The urgent email to Priya:

```
Subject: URGENT: emergency services contacted for Raj

Hi Priya,

Emergency services have been contacted for Raj at 18:30 (911, ref SIM-6247, simulated in this demo).

Raj said: "My chest feels tight and my left arm has gone numb"
Address on file: House 7, Green Park, Pune (demo address)

Please try to reach Raj now.

This is an automatic message from Saathi. It is not a medical assessment.
```

Meera's evening summary:

```
Subject: Meera's day (Thu 22 Oct)

Hi Priya,

Meera completed the reminder "take my BP tablet" at 2 in the afternoon. Meera described feeling "pretty good". Asked about "My grandson Arjun has a cricket match on Saturday", Meera said: "They won by 20 runs!" Meera said: "My granddaughter Diya starts college next week." Meera seemed cheerful today.

— Sent by Saathi for Meera.
Saathi is a companion, not a medical service.
```
