# AWS setup guide: running Saathi live (Bedrock + SES + SNS)

This takes Saathi from offline mode (rule-based router; emails, texts and dispatch records saved as files)
to live mode. In live mode:
- **Claude on Amazon Bedrock** runs the conversation, the emergency check, background reflection and
  update wording.
- **Amazon SES** delivers real email; **Amazon SNS** delivers real texts.
- **Nothing is deployed to AWS:** Saathi still runs on your machine and calls AWS over the network.
- **Emergency dispatch stays simulated** in live mode too.

**Time:** about 45–60 minutes, most of it waiting for verification emails and texts.
**Cost:** a full demo run is well under US $1 with Claude Haiku 4.5 (details in [Costs](#costs)).

```
your machine                                       AWS (one region, e.g. us-east-1)
┌─────────────────────────────┐  ConverseStream      ┌──────────────────────────────────┐
│ Saathi host (Strands agent) │ ───────────────────▶ │ Amazon Bedrock · Claude          │
│  · conversation             │  Converse (≤10 s)    │  (inference profile)             │
│  · emergency check          │ ───────────────────▶ │                                  │
│ Saathi MCP server           │  Converse            │                                  │
│  · reflection, wording      │ ───────────────────▶ │                                  │
│  · email                    │ ── SendEmail ──────▶ │ Amazon SES ─▶ Priya's inbox      │
│  · texts                    │ ── Publish ────────▶ │ Amazon SNS ─▶ Priya's phone      │
└─────────────────────────────┘                      └──────────────────────────────────┘
          credentials: an IAM user's access key, via `aws configure`
```

What switches each part on:

| Part | Needs |
|---|---|
| Claude conversation, emergency check, reflection, wording | `MOCK=0` and `BEDROCK_MODEL_ID` |
| Real email | `MOCK=0` and `SES_SENDER` |
| Real texts | `MOCK=0` and `SMS_ENABLED=1` (and a phone number for the contact) |

Each part can be live on its own. For example, Bedrock and SES live with texts still written to `outbox/`.

> Use a **personal** AWS account and **personal** email addresses for this, not a work account or work
> address.

---

## 1. Pick a region

Bedrock, SES and SNS are regional. Saathi uses `AWS_REGION` for all three; `SES_REGION` and `SNS_REGION`
override email and texts only.

| Option | Settings | Notes |
|---|---|---|
| **Recommended** | `AWS_REGION=us-east-1` | Widest Claude availability; SES available. Simplest. |
| From India | `AWS_REGION=ap-south-1` with a `global.` inference profile, and `SES_REGION=ap-south-1` | Claude is reached from Mumbai through global cross-Region inference. |

Use the **same SES region** for everything SES-related below. Identities verified in one region don't exist
in another.

## 2. Install the AWS CLI

You'll use it to sign in and to list models.

- **Windows:** `winget install Amazon.AWSCLI` (or the MSI from the AWS CLI page), then open a new terminal.
- **macOS:** `brew install awscli`. **Linux:** follow the AWS CLI v2 install page.

Check with `aws --version`; you need version 2.

## 3. Create an IAM user for Saathi

Don't use the root user's keys.

1. Open the AWS console, go to **IAM → Users → Create user**, and name it `saathi-dev`. It doesn't need
   console access.
2. On the permissions step choose **Attach policies directly → Create policy → JSON**, paste the policy
   below, name it `SaathiLive`, and attach it to the user.
3. Open the user, go to **Security credentials → Create access key → Command Line Interface (CLI)**, and
   copy the key ID and secret.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "InvokeClaude",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      "Resource": [
        "arn:aws:bedrock:*::foundation-model/anthropic.*",
        "arn:aws:bedrock:*:*:inference-profile/*anthropic.*"
      ]
    },
    {
      "Sid": "ListModels",
      "Effect": "Allow",
      "Action": ["bedrock:ListInferenceProfiles", "bedrock:ListFoundationModels"],
      "Resource": "*"
    },
    {
      "Sid": "FirstUseMarketplaceSubscription",
      "Effect": "Allow",
      "Action": ["aws-marketplace:Subscribe", "aws-marketplace:ViewSubscriptions"],
      "Resource": "*"
    },
    {
      "Sid": "SendAndCheckEmail",
      "Effect": "Allow",
      "Action": ["ses:SendEmail", "ses:GetAccount", "ses:GetEmailIdentity"],
      "Resource": "*"
    },
    {
      "Sid": "SendAndCheckTexts",
      "Effect": "Allow",
      "Action": ["sns:Publish", "sns:GetSMSSandboxAccountStatus", "sns:ListSMSSandboxPhoneNumbers"],
      "Resource": "*"
    }
  ]
}
```

Why each part is there:
- **InvokeClaude:** `InvokeModelWithResponseStream` is needed because Strands streams replies through
  ConverseStream. The foundation-model line covers every region a cross-Region profile may route to.
- **FirstUseMarketplaceSubscription:** some Bedrock models are sold through AWS Marketplace, and the
  subscription is created automatically on the first call. Without these two actions, that first call
  fails with AccessDenied.
- **SendAndCheckEmail:** `GetAccount` and `GetEmailIdentity` are only used by the preflight script.
- **SendAndCheckTexts:** `sns:Publish` sends SMS directly to a phone number (no topic needed). The two
  sandbox actions are only used by the preflight script.

## 4. Sign in from your machine

```bash
aws configure
#   AWS Access Key ID:     <from step 3>
#   AWS Secret Access Key: <from step 3>
#   Default region name:   us-east-1          (or your region from step 1)
#   Default output format: json

aws sts get-caller-identity               # should print ...:user/saathi-dev
```

This stores the key in `~/.aws/credentials`, where boto3 and Strands find it automatically. **Don't put
access keys in `.env`.** It's gitignored, but keeping keys out of the repo folder is safer.

## 5. Set up Claude on Bedrock

Serverless models are now enabled by default; the old "Model access" page is gone. Anthropic models
still need a **one-time use-case form** per account.

1. **Submit the form.** Open the Bedrock console in your region, go to **Model catalog**, choose
   **Claude Haiku 4.5**, and open it in the **playground**. The form appears the first time; fill it in and
   submit. Access is normally granted right away.
2. **Send one message in the playground** to confirm it answers.
3. **Pick the model ID.** Newer Claude models must be called through an **inference profile**; the bare
   `anthropic.…` model ID is rejected for on-demand use.

| Model | `BEDROCK_MODEL_ID` | When to use |
|---|---|---|
| **Claude Haiku 4.5 (recommended)** | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | Fast and cheap; voice turns need low latency |
| Claude Haiku 4.5, global routing | `global.anthropic.claude-haiku-4-5-20251001-v1:0` | From ap-south-1 or other regions without a `us.` profile |
| A larger Claude model | look it up (below) | Richer wording, slower, costs more |

To see every Claude inference profile available in your region:

```bash
aws bedrock list-inference-profiles --region us-east-1 \
  --query "inferenceProfileSummaries[?contains(inferenceProfileId, 'anthropic')].inferenceProfileId" --output table
```

## 6. Set up Amazon SES

New accounts start in the **SES sandbox**: you can only send to verified addresses, up to 200 messages a
day. That's enough for the demo; you don't need production access.

1. Open **SES console (same region as `SES_REGION`/`AWS_REGION`) → Configuration → Identities → Create
   identity → Email address**.
2. Add the **sender**, the address Saathi sends from. Open the verification email and click the link.
3. Add **Priya's address**, the recipient in the demo (for example a second personal inbox you can open on
   your phone). Verify it too; in the sandbox, recipients must be verified as well.
4. Both identities should show **Verified** in the console.

> **Deliverability:** if the sender is a Gmail, Yahoo or Outlook address, mail sent through SES fails
> those providers' DMARC checks, so it may land in **spam** or be rejected. For the demo, check the spam
> folder and mark the message "not spam" once. For reliable delivery, verify a **domain you own** in SES
> (Create identity → Domain, then add the Easy DKIM DNS records) and send from an address on that domain.

## 6a. Set up Amazon SNS text messages

Family alerts (emergencies, concerns, updates, the evening digest) also go out as texts. New accounts start
in the **SMS sandbox**: texts only reach phone numbers you've verified.

1. Open **SNS console (your `SNS_REGION`, defaults to `AWS_REGION`) → Mobile → Text messaging (SMS)**.
2. Under **Sandbox destination phone numbers**, choose **Add phone number**, enter Priya's phone (for the demo,
   your own mobile) in E.164 form (`+91…`, `+1…`), and enter the code it texts you.
3. Under **Account information**, set the **default message type to Transactional**. Saathi also sets it on
   every message, so alerts get delivery priority.
4. **Where the texts come from depends on the destination country:**
   - **United States:** carriers require an origination number. Request a **toll-free number** (quickest
     for a demo; it needs registration) or a 10DLC number in **AWS End User Messaging SMS → Phone numbers**.
     Without one, US texts don't arrive.
   - **India:** commercial SMS needs **DLT registration** (entity and template), and delivery to Indian
     numbers without it is unreliable. For the demo, the sandbox plus a verified number is usually enough
     to show a text arriving. If it doesn't, the email alert still arrives, and the `.sms.txt` file in
     offline mode shows exactly what would be sent.
   - **Other countries** have their own rules; the SNS "Supported countries" page lists them.
5. Set a **monthly SMS spending limit** (Text messaging → Account information). The default $1 is enough
   for many demo runs.

## 6b. Email without SES (Gmail SMTP)

Use this if SES is blocked. On AWS's **Free plan**, an organization policy denies SES and SMS isn't
available; `check_aws.py` reports `explicit deny in a service control policy`. Saathi can send the same
emails through any SMTP server. It uses SMTP only while `SES_SENDER` is empty.

1. **Turn on 2-Step Verification** for the Gmail account that will send (Google Account → Security).
2. **Create an App password:** open **myaccount.google.com/apppasswords**, name it "Saathi", and copy the
   16 characters.
3. **In `.env`:**
   ```ini
   SES_SENDER=
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=you@gmail.com
   SMTP_PASSWORD=abcdefghijklmnop     # the App password, never your normal password
   ```
4. **Test it:** run `check_aws.py --send-test`. You want `SMTP smtp.gmail.com:587 login … succeeded` and
   `Test email sent`. Check the recipient's spam folder once.

For a local test inbox that sends nothing, run **Mailpit** (`docker run -p 8025:8025 -p 1025:1025
axllent/mailpit`) with `SMTP_HOST=127.0.0.1`, `SMTP_PORT=1025` and `SMTP_SECURITY=none`, then open
`http://localhost:8025`.

Texts can't be sent without SNS. Their copies (`outbox/*.sms.txt`) appear on **`/phone`**, "Priya's phone".

## 7. Set a budget and check your credits

1. Go to **Billing → Budgets → Create budget → Monthly cost budget**, set **$20**, and add your email for
   alerts.
2. Go to **Billing → Credits** and note the $150 credit's **expiry date** and **eligible services**.
3. **After your first live run**, open **Billing → Bills** and see where the Claude charges are listed.
   - Under **Amazon Bedrock**, the credit normally applies.
   - Under **AWS Marketplace**, promotional credits often *don't* cover them. The amounts are small, but
     if you want them covered, ask AWS Support to confirm.

## 8. Configure Saathi

In the `saathi` folder, copy `.env.example` to `.env` and set:

```ini
MOCK=0
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0
SES_SENDER=you@yourdomain.com        # verified in step 6
PRIYA_EMAIL=priya-inbox@example.com  # verified in step 6
# SES_REGION=ap-south-1              # only if SES is in a different region from Bedrock
SMS_ENABLED=1                        # send texts with SNS (step 6a)
PRIYA_PHONE=+919800000000            # verified in step 6a, E.164
# SNS_REGION=ap-south-1              # only if SMS is set up in a different region
EMERGENCY_NUMBER=112                 # named in messages: 112 India/EU, 911 US, 999 UK
CRISIS_LINE_TEXT=Tele MANAS on 14416 # spoken if someone may harm themselves; set per country
```

Emergency dispatch stays **simulated** in live mode too: no real emergency call is placed (see the README).

`PRIYA_EMAIL` and `PRIYA_PHONE` are written into the demo data at startup, and `demo.ps1` / `demo.sh` re-seed
every run, so a restart picks up changes.

Optional tuning (defaults shown): `EMERGENCY_CHECK_TIMEOUT=10`, `EMERGENCY_CONFIRM_SECONDS=30`,
`DIGEST_TIME=19:30`.

## 9. Run the preflight check

```bash
.venv\Scripts\python scripts\check_aws.py               # Windows
.venv/bin/python scripts/check_aws.py                   # macOS / Linux
.venv\Scripts\python scripts\check_aws.py --send-test   # also sends one real test email and one real text
```

It checks, in order:
1. **Credentials**, and warns if you're using the root user.
2. **One tiny Bedrock call**, reporting latency and tokens. Under 2 s is comfortable for the 10-second
   emergency-check limit.
3. **SES:** whether the account is in the sandbox or production, and whether the sender and recipient are
   verified.
4. **Sender domain:** warns about free-mail sender addresses.
5. **SNS:** whether SMS is in the sandbox, and whether `PRIYA_PHONE` is a verified destination.

Every FAIL line says what to do next. Keep going until it ends with **"Ready for live mode."**

## 10. Run Saathi live

```bash
.\demo.ps1        # or ./demo.sh
```

- The console should say **`LIVE (Bedrock)`**.
- The elder page briefly shows **"Live mode: Claude on Amazon Bedrock"**.

Then follow the offline test workflow. In live mode you can phrase things naturally; Claude handles it.
Try an emergency the phrase list doesn't know, e.g. "my words are coming out all wrong and my arm feels
heavy". The Haiku check should catch it.

How to confirm each AWS service is really being used:

| Service | Where to see it |
|---|---|
| Bedrock (conversation) | Replies are no longer the fixed offline sentences. The host log has no "falling back to MockModel" line. |
| Bedrock (emergency check) | Inspector: an `emergency_check` entry on each turn with Haiku's verdict. A "timed out" warning in the host log means the ask-then-escalate path ran. |
| Bedrock (reflection) | Inspector → the `reflect_turn` result shows `"method": "bedrock"`, with Claude's own wording for impressions. |
| Bedrock (update wording) | The `send_family_update` result shows `"phrasing": "bedrock"`. (`template (bedrock output rejected)` means the guard caught an unbacked mood word or clinical term: working as designed.) |
| SES / SNS | Emails and texts arrive on Priya's phone. `outbox/` only gets the dispatch records in live mode. |
| Usage | Bedrock console → **Model invocations** / CloudWatch metrics; SES **Account dashboard**; SNS **Text messaging → Delivery statistics**. |

If a Bedrock call fails mid-conversation, the host switches that session to the offline router and keeps
going, and the safety rules still apply. Fix the cause and restart to return to live mode.

## 11. Recording-day checklist

- [ ] `check_aws.py --send-test` passes. The test email reaches Priya's inbox (not spam) and the test text
      arrives on the phone.
- [ ] Priya's inbox and messages are open on the phone you'll film.
- [ ] Rehearse the emergency once in live mode. Check the urgent text arrives and the dispatch record says
      SIMULATED.
- [ ] Do one full live rehearsal, then restart `demo.ps1` for fresh data before recording.
- [ ] Keep `.env`, keys, phone numbers and account IDs off screen.
- [ ] After recording, fill in the Bedrock, SES and SNS sections of `docs/product-feedback.md` and add live-run
      entries to `docs/friction-log.md`.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `NoCredentialsError` / "Unable to locate credentials" | No credentials on this machine | `aws configure` (step 4) |
| `UnrecognizedClientException`, `InvalidClientTokenId`, `ExpiredToken` | Wrong, deleted or expired key | Create a new access key; re-run `aws configure` |
| `ValidationException … on-demand throughput isn't supported` | Bare model ID used | Use an inference profile ID (`us.` / `global.` prefix) |
| `AccessDeniedException` mentioning use case / first-time use | Anthropic form not submitted | Step 5.1 |
| `AccessDeniedException` mentioning `aws-marketplace:Subscribe` | Missing Marketplace permissions | Add the `FirstUseMarketplaceSubscription` statement (step 3) |
| `AccessDeniedException … bedrock:InvokeModelWithResponseStream` | Policy only allows `InvokeModel` | Add both actions (step 3) |
| `ResourceNotFoundException` / model not found | Model or profile not offered in this region | List profiles (step 5.3) or switch region / `global.` profile |
| `ThrottlingException` | Too many requests too quickly | Wait and retry; the demo makes few calls |
| `MessageRejected: Email address is not verified` | Sender or recipient not verified **in `SES_REGION`** | Verify both in that region (step 6) |
| Email "sent" but never arrives | DMARC on a free-mail sender; spam folder | Check spam; use a domain you own (step 6 note) |
| Console says `MOCK (offline…)` despite `.env` | `MOCK=0` not set, or `BEDROCK_MODEL_ID` empty | Both are needed for live conversation |
| Replies feel slow | Larger model, or a distant region | Use Haiku 4.5; pick the closest region with a `global.` profile |
| Saathi keeps asking "Are you okay?" | The emergency check is timing out or failing (credentials, region, model ID) | Check the host log; run `check_aws.py`. It asks at most once per 10 minutes on failures |
| Text "sent" (MessageId returned) but nothing arrives | SMS sandbox (number not verified), no US origination number, India DLT, or spending limit reached | Step 6a; SNS → Text messaging → Delivery statistics shows the failure reason |
| `AuthorizationError` from SNS | Missing `sns:Publish` | Add the `SendAndCheckTexts` statement (step 3) |
| `… explicit deny in a service control policy` (SES) / "not available on free tier" (SMS) | The account is on AWS's Free plan | Upgrade to the Paid plan, or email via SMTP (step 6b) and show texts on `/phone` |
| SMTP `535` / "Username and Password not accepted" | Normal Gmail password used | Use an App password (step 6b) |
| SMTP "Application-specific password required" | 2-Step Verification is off | Turn it on, then create an App password |

## Costs

| Item | Rough cost |
|---|---|
| Claude Haiku 4.5 on Bedrock | about $1 per million input tokens and $5 per million output tokens at the time of writing (check the Bedrock pricing page). A full demo day is roughly 80 model calls: conversation, plus one emergency check and one reflection
per turn, mostly a few thousand tokens each. That's **around $0.50–1 per run**. Larger models cost several times more. |
| Amazon SES | $0.10 per 1,000 emails; a demo day sends about 8. |
| Amazon SNS SMS | Per message, by country (fractions of a cent to a few cents); a demo day sends about 8. Toll-free number registration in the US has a monthly fee. |
| Everything else | Nothing is deployed, so there's no hosting cost. |

## After the hackathon

- **Revoke the key:** IAM → Users → `saathi-dev` → Security credentials → **Deactivate / Delete** the
  access key, or delete the user.
- **Optional:** delete the SES identities and the SNS sandbox phone numbers; release any SMS origination
  number you rented.
- **Switch back to offline mode:** set `MOCK=1` in `.env`.
