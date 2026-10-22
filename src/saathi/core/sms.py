"""SMS delivery: Amazon SNS (live) or .sms.txt files in outbox/ only. A copy is always kept in outbox/."""

from __future__ import annotations

import re

from saathi import config
from saathi.core import SaathiError, clock

MAX_LENGTH = 600  # SNS splits long texts into segments; alerts stay short anyway


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:30] or "message"


def valid_number(phone: str) -> bool:
    return bool(re.fullmatch(r"\+[1-9]\d{6,14}", phone or ""))


def send(phone: str, to_name: str, text: str, kind: str) -> dict:
    if not valid_number(phone):
        raise SaathiError(f"No valid phone number for {to_name} (E.164, e.g. +15555550100).")
    text = text.strip()[:MAX_LENGTH]
    settings = config.get()
    if settings.live_sns:
        result = _send_sns(settings, phone, text)
        _write_outbox(settings, phone, to_name, text, kind, "sns")  # a copy for the phone page
        return result
    return _write_outbox(settings, phone, to_name, text, kind, "outbox")


def _write_outbox(settings, phone, to_name, text, kind, channel: str = "outbox") -> dict:
    settings.outbox_dir.mkdir(parents=True, exist_ok=True)
    stamp = clock.now().strftime("%Y%m%d-%H%M%S")
    path = settings.outbox_dir / f"{stamp}-{kind}-{_slug(to_name)}.sms.txt"
    counter = 1
    while path.exists():
        path = settings.outbox_dir / f"{stamp}-{kind}-{_slug(to_name)}-{counter}.sms.txt"
        counter += 1
    path.write_text(f"To: {to_name} <{phone}>\nKind: {kind}\nChannel: {channel}\n\n{text}\n", encoding="utf-8")
    return {"status": "delivered", "channel": "sms-outbox", "path": str(path)}


def _send_sns(settings, phone, text) -> dict:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    client = boto3.client("sns", region_name=settings.sns_region)
    try:
        response = client.publish(
            PhoneNumber=phone,
            Message=text,
            MessageAttributes={"AWS.SNS.SMS.SMSType": {"DataType": "String", "StringValue": "Transactional"}},
        )
    except (ClientError, BotoCoreError) as exc:
        raise SaathiError(f"Text message could not be sent ({exc}).") from exc
    return {"status": "delivered", "channel": "sns", "message_id": response.get("MessageId", "")}
