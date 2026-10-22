# Emergencies (reference)

The Saathi host handles emergencies itself, before and alongside you:

1. **Phrase check, before any model sees the words** (`config/crisis_phrases.txt`). Instant, and works offline.
2. **A fast separate Claude check of every other message**, in parallel with you, limited to 10 seconds.
   It understands indirect wording, context and other languages.
3. **If the check is unsure or too slow**, Saathi asks "Are you okay? Do you need me to call for help?"
   and texts their family. A yes, or no answer within 30 seconds, means emergency services are contacted.

| Situation | What happens |
|---|---|
| Medical emergency (heart attack or stroke signs, a fall, can't breathe, collapse, overdose…) | Emergency services are contacted **first** (simulated in this build). Then Saathi says help is on the way, and the family get an urgent text and email saying services have been called. |
| Self-harm | Saathi gives crisis-line details and the family get an urgent text and email. |
| Asked for help | The family get a text and email asking them to get in touch. |

Messages to family are fixed and never diagnose. Your reply is replaced by the fixed emergency script.

If something worrying reaches you anyway:
- Respond with care in one or two sentences.
- Encourage them to get help now: their family, or the emergency number.
- Never counsel, diagnose, or promise outcomes.
