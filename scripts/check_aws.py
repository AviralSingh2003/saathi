"""Preflight check for live mode: credentials, Bedrock, SES, SNS.

    python scripts/check_aws.py               # read-only checks + one tiny Bedrock call
    python scripts/check_aws.py --send-test   # also send a test email to PRIYA_EMAIL and a text to PRIYA_PHONE
    python scripts/check_aws.py --no-bedrock  # skip the Bedrock call

Reads the same settings as Saathi (.env / environment). Each failure prints what
to do next. Exit code 0 only if everything needed for live mode is ready.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saathi import config  # noqa: E402

OK, WARN, FAIL = "  OK  ", " WARN ", " FAIL "


def line(status: str, text: str, hint: str | None = None) -> None:
    print(f"[{status}] {text}")
    if hint:
        for part in hint.splitlines():
            print(f"         -> {part}")


def bedrock_hint(code: str, message: str) -> str:
    text = f"{code} {message}".lower()
    if "on-demand throughput" in text or "inference profile" in text:
        return ("Use an inference profile ID, not the bare model ID, e.g.\n"
                "BEDROCK_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0")
    if "use case" in text or "first time" in text or "usage form" in text:
        return ("Submit Anthropic's one-time use-case form: Bedrock console -> Model catalog -> "
                "pick the Claude model -> open it in the playground; the form appears.")
    if "marketplace" in text:
        return ("Your IAM identity needs aws-marketplace:Subscribe and aws-marketplace:ViewSubscriptions "
                "for the first call (see docs/aws-setup-guide.md, step 3).")
    if code in ("AccessDeniedException",):
        return ("Check the IAM policy allows bedrock:InvokeModel and bedrock:InvokeModelWithResponseStream "
                "on the model and inference profile, then the Anthropic use-case form.")
    if code in ("ResourceNotFoundException", "ValidationException"):
        return ("Check BEDROCK_MODEL_ID and that the model is offered in AWS_REGION. List profiles with:\n"
                "aws bedrock list-inference-profiles --region <region>")
    if code == "ThrottlingException":
        return "You're being throttled; wait a minute and retry."
    return "See the troubleshooting table in docs/aws-setup-guide.md."


def smtp_hint(exc: Exception) -> str:
    text = str(exc).lower()
    if "application-specific password" in text or "534" in text:
        return "Gmail needs an App password: turn on 2-Step Verification, then create one at\nmyaccount.google.com/apppasswords and put it in SMTP_PASSWORD."
    if "535" in text or "username and password not accepted" in text or "authentication" in text:
        return "Login rejected. For Gmail use an App password (not your normal password) in SMTP_PASSWORD,\nand your full Gmail address in SMTP_USER."
    if "timed out" in text or "getaddrinfo" in text or "refused" in text:
        return "Couldn't reach the server. Check SMTP_HOST / SMTP_PORT (Gmail: smtp.gmail.com, 587) and your network."
    return "Check SMTP_HOST, SMTP_PORT, SMTP_USER and SMTP_PASSWORD in .env."


def ready_smtp(s, recipient: str, send_test: bool) -> bool:
    """Log in to the SMTP server (and optionally send one test email). Never prints the password."""
    import smtplib
    import ssl

    if not s.smtp_user or not s.smtp_password:
        line(FAIL, f"SMTP {s.smtp_host}: SMTP_USER or SMTP_PASSWORD is empty",
             "Put your Gmail address in SMTP_USER and a Gmail App password in SMTP_PASSWORD.")
        return False
    try:
        if s.smtp_security == "ssl":
            server = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=15, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15)
        with server:
            if s.smtp_security == "starttls":
                server.starttls(context=ssl.create_default_context())
            server.login(s.smtp_user, s.smtp_password)
            line(OK, f"SMTP {s.smtp_host}:{s.smtp_port} login as {s.smtp_user} succeeded")
            if send_test:
                from email.message import EmailMessage
                from email.utils import formataddr

                msg = EmailMessage()
                msg["From"] = formataddr(("Saathi", s.smtp_from))
                msg["To"] = recipient
                msg["Subject"] = "Saathi test email"
                msg.set_content("If you can read this, Saathi can email family alerts and updates.")
                server.send_message(msg)
                line(OK, f"Test email sent to {recipient} via {s.smtp_host}. Check spam too.")
    except (smtplib.SMTPException, OSError) as exc:
        line(FAIL, f"SMTP {s.smtp_host}: {type(exc).__name__}: {str(exc)[:160]}", smtp_hint(exc))
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--send-test", action="store_true", help="send a test email and a test text")
    parser.add_argument("--no-bedrock", action="store_true", help="skip the Bedrock call")
    args = parser.parse_args()

    import boto3
    from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

    s = config.get()
    recipient = os.environ.get("PRIYA_EMAIL", "priya@example.com")
    ready = True

    print("Saathi live-mode preflight\n")
    print(f"  MOCK={int(s.mock)}  AWS_REGION={s.aws_region}  SES_REGION={s.ses_region}")
    print(f"  BEDROCK_MODEL_ID={s.bedrock_model_id or '(not set)'}")
    print(f"  SES_SENDER={s.ses_sender or '(not set)'}  PRIYA_EMAIL={recipient}")
    print(f"  SMTP_HOST={s.smtp_host or '(not set)'}  SMTP_USER={s.smtp_user or '(not set)'}  "
          f"SMTP_PASSWORD={'(set)' if s.smtp_password else '(not set)'}\n")

    if s.mock:
        line(WARN, "MOCK=1 — Saathi will stay offline until you set MOCK=0 in .env")

    # 1. credentials
    try:
        who = boto3.client("sts", region_name=s.aws_region).get_caller_identity()
        line(OK, f"AWS credentials: {who['Arn']} (account {who['Account']})")
        if who["Arn"].endswith(":root"):
            line(WARN, "You're using the root user", "Create an IAM user for Saathi instead (step 3 of the guide).")
    except NoCredentialsError:
        line(FAIL, "No AWS credentials found", "Run `aws configure` (step 4 of docs/aws-setup-guide.md).")
        return 1
    except (ClientError, BotoCoreError) as exc:
        line(FAIL, f"Credentials rejected: {exc}", "Re-run `aws configure` with a valid access key, or refresh SSO.")
        return 1

    # 2. Bedrock
    if not s.bedrock_model_id:
        line(FAIL, "BEDROCK_MODEL_ID is not set",
             "Set BEDROCK_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0 (or another profile) in .env")
        ready = False
    elif s.bedrock_model_id.startswith("anthropic."):
        line(WARN, "BEDROCK_MODEL_ID looks like a bare model ID",
             "Newer Claude models need an inference profile: prefix with us. / eu. / apac. / global.")
    if s.bedrock_model_id and not args.no_bedrock:
        try:
            client = boto3.client("bedrock-runtime", region_name=s.aws_region)
            start = time.monotonic()
            response = client.converse(
                modelId=s.bedrock_model_id,
                messages=[{"role": "user", "content": [{"text": "Reply with the single word: ready"}]}],
                inferenceConfig={"maxTokens": 10, "temperature": 0},
            )
            ms = (time.monotonic() - start) * 1000
            text = " ".join(b.get("text", "") for b in response["output"]["message"]["content"]).strip()
            usage = response.get("usage", {})
            line(OK, f"Bedrock answered \"{text}\" in {ms:.0f} ms "
                     f"({usage.get('inputTokens', '?')} in / {usage.get('outputTokens', '?')} out tokens)")
        except ClientError as exc:
            err = exc.response.get("Error", {})
            line(FAIL, f"Bedrock: {err.get('Code')}: {err.get('Message')}",
                 bedrock_hint(err.get("Code", ""), err.get("Message", "")))
            ready = False
        except BotoCoreError as exc:
            line(FAIL, f"Bedrock: {exc}", "Check AWS_REGION and your network connection.")
            ready = False

    # 3. SES
    if not s.ses_sender and s.smtp_host:
        line(OK, f"SES_SENDER is not set: email goes through SMTP ({s.smtp_host}) instead")
    elif not s.ses_sender:
        line(WARN, "SES_SENDER is not set: emails go to outbox/*.eml instead of Amazon SES",
             "For real email, set SES_SENDER to an address you verified in SES (step 6),\n"
             "or SMTP_HOST / SMTP_USER / SMTP_PASSWORD for an SMTP server such as Gmail.")
    else:
        ses = boto3.client("sesv2", region_name=s.ses_region)
        try:
            account = ses.get_account()
            production = account.get("ProductionAccessEnabled", False)
            quota = account.get("SendQuota", {})
            per_day = int(quota.get("Max24HourSend", 0))
            per_sec = int(quota.get("MaxSendRate", 0))
            line(OK if production else WARN,
                 f"SES in {s.ses_region}: {'production' if production else 'sandbox'} "
                 f"(max {per_day}/day, {per_sec}/s)",
                 None if production else "Sandbox is fine for the demo, but every recipient must be verified too.")
            to_check = [("sender", s.ses_sender)] + ([] if production else [("recipient", recipient)])
            for role, address in to_check:
                try:
                    identity = ses.get_email_identity(EmailIdentity=address)
                    if identity.get("VerifiedForSendingStatus"):
                        line(OK, f"SES {role} {address} is verified")
                    else:
                        line(FAIL, f"SES {role} {address} is not verified yet",
                             "Click the link in the verification email AWS sent to that address.")
                        ready = False
                except ClientError as exc:
                    if exc.response.get("Error", {}).get("Code") == "NotFoundException":
                        domain = address.split("@")[-1]
                        try:
                            if ses.get_email_identity(EmailIdentity=domain).get("VerifiedForSendingStatus"):
                                line(OK, f"SES {role} {address} is covered by verified domain {domain}")
                                continue
                        except ClientError:
                            pass
                        line(FAIL, f"SES {role} {address} isn't an SES identity in {s.ses_region}",
                             f"SES console ({s.ses_region}) -> Identities -> Create identity -> Email address.")
                    else:
                        line(FAIL, f"SES lookup for {address}: {exc}", "Check the IAM policy allows ses:GetEmailIdentity.")
                    ready = False
            if s.ses_sender.split("@")[-1].lower() in {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "aol.com"}:
                line(WARN, f"Sender is a free-mail address ({s.ses_sender})",
                     "Mail sent through SES from a gmail/yahoo/outlook address fails DMARC checks, so it may land in\n"
                     "spam or be rejected. For the demo, check spam; for reliability verify a domain you own.")
        except ClientError as exc:
            line(FAIL, f"SES: {exc}", "Check the IAM policy allows ses:GetAccount and ses:SendEmail.")
            ready = False

        if args.send_test and ready:
            try:
                out = ses.send_email(
                    FromEmailAddress=s.ses_sender,
                    Destination={"ToAddresses": [recipient]},
                    Content={"Simple": {
                        "Subject": {"Data": "Saathi test email", "Charset": "UTF-8"},
                        "Body": {"Text": {"Data": "If you can read this, Saathi can deliver family updates.",
                                          "Charset": "UTF-8"}},
                    }},
                )
                line(OK, f"Test email sent to {recipient} (MessageId {out['MessageId']}). Check spam too.")
            except ClientError as exc:
                line(FAIL, f"Test email failed: {exc}", "See the SES rows of the troubleshooting table.")
                ready = False

    # 3b. SMTP (email without SES)
    if s.smtp_host and not s.ses_sender:
        if not ready_smtp(s, recipient, args.send_test):
            ready = False

    # 4. SNS (text messages)
    phone = os.environ.get("PRIYA_PHONE", "+15555550100")
    if not s.sms_enabled:
        line(WARN, "SMS_ENABLED is off: texts go to outbox/*.sms.txt instead of Amazon SNS",
             "Set SMS_ENABLED=1 and PRIYA_PHONE=+<country code><number> in .env to send real texts.")
    else:
        sns = boto3.client("sns", region_name=s.sns_region)
        try:
            in_sandbox = sns.get_sms_sandbox_account_status().get("IsInSandbox", True)
            line(OK if not in_sandbox else WARN,
                 f"SNS SMS in {s.sns_region}: {'sandbox' if in_sandbox else 'production'}",
                 "In the sandbox, texts only reach verified numbers (step 6a of the guide)." if in_sandbox else None)
            if in_sandbox:
                numbers = sns.list_sms_sandbox_phone_numbers().get("PhoneNumbers", [])
                status = {n["PhoneNumber"]: n["Status"] for n in numbers}
                if status.get(phone) == "Verified":
                    line(OK, f"SNS destination {phone} is verified")
                else:
                    line(FAIL, f"SNS destination {phone} is not verified ({status.get(phone, 'not added')})",
                         "SNS console -> Text messaging (SMS) -> Sandbox destination phone numbers -> Add.")
                    ready = False
        except ClientError as exc:
            line(FAIL, f"SNS: {exc}", "Check the IAM policy allows sns:Publish and sns:GetSMSSandboxAccountStatus.")
            ready = False
        if args.send_test and ready:
            try:
                out = sns.publish(PhoneNumber=phone, Message="Saathi test text: family alerts will arrive like this.",
                                  MessageAttributes={"AWS.SNS.SMS.SMSType": {"DataType": "String",
                                                                             "StringValue": "Transactional"}})
                line(OK, f"Test text sent to {phone} (MessageId {out['MessageId']}).")
            except ClientError as exc:
                line(FAIL, f"Test text failed: {exc}",
                     "US numbers need an origination number (toll-free or 10DLC); India needs DLT/sender-ID "
                     "registration. See step 6a of the guide.")
                ready = False

    print()
    if ready and not s.mock:
        print("Ready for live mode. Start Saathi with ./demo.sh or .\\demo.ps1")
    elif ready:
        print("AWS is ready. Set MOCK=0 in .env to switch Saathi to live mode.")
    else:
        print("Not ready yet — fix the FAIL lines above (details in docs/aws-setup-guide.md).")
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
