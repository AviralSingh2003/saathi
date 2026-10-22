# AWS integration (AWS Builder mini-challenge)

Saathi is a multi-service AWS pipeline. **Strands Agents SDK** orchestrates a **Claude on Amazon Bedrock**
agent that calls the Saathi MCP server. Bedrock is used three more times:
- a fast **Haiku emergency check** on every message, with a hard time limit
- a background **reflection** after every turn (memory, impressions, concerns)
- **update phrasing**

Family are reached by **Amazon SES** (email) and **Amazon SNS** (text messages).

| Service | Where in the code | What it does | Offline equivalent |
|---|---|---|---|
| Strands Agents SDK | `src/saathi/host/agent.py` | One `Agent` per person; `MCPClient` over Streamable HTTP with `tool_filters`; `AgentSkills` plugin; `HookProvider` for tool-call relay and `person_id` injection; system prompt refreshed every turn with what Saathi knows | Same agent loop with `MockModel` (a custom `strands.models.Model`) |
| Amazon Bedrock — conversation | `host/agent.py` (`BedrockModel`) | Converses, picks tools, follows the Agent Skill | `MockModel` rule-based router |
| Amazon Bedrock — emergency check | `src/saathi/host/emergency.py` (`converse`, temperature 0, 80 tokens, botocore timeouts) | Runs alongside the conversation on every message the phrase list didn't catch. Classifies medical emergency / self-harm / asked for help / none, with a confidence. 10-second limit, then "ask, then escalate" | Phrase list only |
| Amazon Bedrock — reflection | `src/saathi/core/reflect.py` (`converse`) | In the background after every turn: memories, impressions, day impression, insights, and whether family should get a concern text | Verbatim fact rules, a few cues, the engagement index |
| Amazon Bedrock — update phrasing | `src/saathi/core/summary.py` (`converse`) | Turns today's facts into 1–2 plain sentences; can't add moods that aren't in the facts, or any clinical language | Template |
| Amazon SES (v2) | `src/saathi/core/mailer.py` (`sesv2.send_email`) | Updates, replies, concern notes, evening digests, emergency alerts | `.eml` files in `outbox/` |
| Amazon SNS | `src/saathi/core/sms.py` (`sns.publish`, `SMSType=Transactional`) | The same messages as short texts; urgent emergency texts ignore quiet settings | `.sms.txt` files in `outbox/` |

## Turning it on

Step-by-step instructions are in [aws-setup-guide.md](aws-setup-guide.md). In summary:
1. **An IAM user** with:
   - `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream`
   - `aws-marketplace:Subscribe` and `aws-marketplace:ViewSubscriptions` (first-call model subscription)
   - `ses:SendEmail`
   - `sns:Publish` and `sns:GetSMSSandboxAccountStatus`
2. **Anthropic's one-time use-case form**, submitted in the Bedrock console.
3. **A Claude inference profile ID**, e.g. `us.anthropic.claude-haiku-4-5-20251001-v1:0`.
4. **SES:** sender and recipient verified (sandbox).
5. **SNS:** destination number verified (SMS sandbox); for US numbers, an origination number.
6. **`.env`** with `MOCK=0`, `BEDROCK_MODEL_ID`, `SES_SENDER`, `PRIYA_EMAIL`, `SMS_ENABLED=1`, `PRIYA_PHONE`,
   `EMERGENCY_NUMBER`. Then run `python scripts/check_aws.py --send-test`.
7. **`./demo.sh`**: the elder page shows "Live mode: Claude on Amazon Bedrock".

## Failure behaviour

- **The conversation model fails:** the session switches to `MockModel` and keeps going.
- **The emergency check times out (10 s) or fails:** Saathi asks "Are you okay? Do you need me to call for help?" and
  texts the family. A yes, or no answer within 30 s, contacts emergency services. At most once per 10
  minutes per person, so a Bedrock outage doesn't make Saathi ask after every sentence. The phrase list keeps
  working.
- **Reflection fails** (network, credentials, malformed JSON): that turn uses the offline rules; the `method`
  field says which ran.
- **Update phrasing fails, or adds an unbacked mood word or clinical language:** the template is used.
- **SES or SNS rejects a send:** the other channel still goes, and the error is reported. If neither channel
  delivers an update, the tool returns an error.
- **SES not configured or not allowed** (e.g. on AWS's Free plan): email goes through an SMTP server instead,
  if `SMTP_HOST` is set. The order is SES, then SMTP, then outbox copies only. A copy of every email and text
  is always kept, and the `/phone` page shows what the family received.

## Cost

Develop in mock mode; use live mode for validation and recording. The live demo day is roughly 20
conversational turns. Each turn adds two small Haiku calls: the emergency check (about 300 tokens) and
reflection (about 1–2k input tokens). On top of that, a handful of emails and about 8 texts. SNS SMS is
priced per message and varies by country; India and the US are cents each.
