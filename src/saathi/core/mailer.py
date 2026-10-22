"""Email delivery: Amazon SES, else an SMTP server (e.g. Gmail), else .eml files in outbox/ only.

A copy of every email is always kept in outbox/, whichever channel sent it, so the
"Priya's phone" page can show exactly what the family received.
"""

from __future__ import annotations

import re
from email.message import EmailMessage
from email.utils import formataddr, formatdate

from saathi import config
from saathi.core import SaathiError, clock

MOCK_SENDER = "saathi@saathi.invalid"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:30] or "message"


def _message(sender: str, to_email: str, to_name: str, subject: str, body: str, kind: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr(("Saathi", sender))
    msg["To"] = formataddr((to_name, to_email))
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["X-Saathi-Kind"] = kind
    msg.set_content(body)
    return msg


def send(to_email: str, to_name: str, subject: str, body: str, kind: str) -> dict:
    settings = config.get()
    if settings.live_ses:
        result = _send_ses(settings, to_email, subject, body)
    elif settings.live_smtp:
        result = _send_smtp(settings, _message(settings.smtp_from, to_email, to_name, subject, body, kind))
    else:
        return _write_outbox(settings, to_email, to_name, subject, body, kind, "outbox")
    _write_outbox(settings, to_email, to_name, subject, body, kind, result["channel"])  # a copy for the phone page
    return result


def _write_outbox(settings, to_email, to_name, subject, body, kind, channel: str) -> dict:
    settings.outbox_dir.mkdir(parents=True, exist_ok=True)
    msg = _message(MOCK_SENDER, to_email, to_name, subject, body, kind)
    msg["X-Saathi-Channel"] = channel
    stamp = clock.now().strftime("%Y%m%d-%H%M%S")
    path = settings.outbox_dir / f"{stamp}-{kind}-{_slug(to_name)}.eml"
    counter = 1
    while path.exists():
        path = settings.outbox_dir / f"{stamp}-{kind}-{_slug(to_name)}-{counter}.eml"
        counter += 1
    path.write_bytes(bytes(msg))
    return {"status": "delivered", "channel": "outbox", "path": str(path)}


def _send_smtp(settings, msg: EmailMessage) -> dict:
    import smtplib
    import ssl

    try:
        if settings.smtp_security == "ssl":
            server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15,
                                      context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15)
        with server:
            if settings.smtp_security == "starttls":
                server.starttls(context=ssl.create_default_context())
            if settings.smtp_user and settings.smtp_password:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        # Surface the failure (never the password): nothing is marked as sent.
        raise SaathiError(f"Email could not be sent through {settings.smtp_host} ({type(exc).__name__}: "
                          f"{str(exc)[:160]}).") from exc
    return {"status": "delivered", "channel": "smtp"}


def _send_ses(settings, to_email, subject, body) -> dict:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    client = boto3.client("sesv2", region_name=settings.ses_region)
    try:
        response = client.send_email(
            FromEmailAddress=settings.ses_sender,
            Destination={"ToAddresses": [to_email]},
            Content={"Simple": {
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
            }},
        )
    except (ClientError, BotoCoreError) as exc:
        # Surface the failure: nothing is marked as sent, so the person can retry.
        raise SaathiError(f"Email could not be sent ({exc}). Nothing was sent.") from exc
    return {"status": "delivered", "channel": "ses", "message_id": response.get("MessageId", "")}
