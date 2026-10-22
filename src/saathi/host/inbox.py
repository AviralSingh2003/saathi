"""What a family member received: emails and texts, read from the outbox copies.

Every email and text Saathi sends keeps a copy in outbox/ (whichever channel actually
delivered it: SES, SMTP, SNS, or none in offline mode). This turns those copies into a
phone-style inbox for one family member. Dispatch records aren't messages to family
and are never shown.
"""

from __future__ import annotations

import re
from datetime import datetime
from email import message_from_bytes
from email.utils import parseaddr

from saathi import config

URGENT_KINDS = ("medical_emergency", "self_harm", "asked_for_help")
_FOOTERS = re.compile(r"\n\n(?:— Sent by Saathi|This is an automatic message from Saathi|— Saathi$).*", re.DOTALL)


def _when(name: str) -> str:
    try:
        return datetime.strptime(name[:15], "%Y%m%d-%H%M%S").isoformat(timespec="seconds")
    except ValueError:
        return ""


def _email(path, address: str) -> dict | None:
    msg = message_from_bytes(path.read_bytes())
    if parseaddr(msg["To"] or "")[1].lower() != address.lower():
        return None
    body = msg.get_payload(decode=True).decode("utf-8", errors="replace")
    body = _FOOTERS.sub("", body.replace("\r\n", "\n")).strip()
    kind = msg["X-Saathi-Kind"] or ""
    return {"type": "email", "id": path.name, "ts": _when(path.name), "kind": kind, "urgent": kind in URGENT_KINDS,
            "subject": msg["Subject"] or "", "body": body, "channel": msg["X-Saathi-Channel"] or "outbox"}


def _text(path, phone: str) -> dict | None:
    head, _, text = path.read_text(encoding="utf-8").partition("\n\n")
    fields = dict(line.split(": ", 1) for line in head.splitlines() if ": " in line)
    if f"<{phone}>" not in fields.get("To", ""):
        return None
    kind = fields.get("Kind", "")
    return {"type": "text", "id": path.name, "ts": _when(path.name), "kind": kind, "urgent": kind in URGENT_KINDS,
            "body": text.strip(), "channel": fields.get("Channel", "outbox")}


def messages_for(contact: dict) -> list[dict]:
    """Everything sent to this family member, newest first."""
    folder = config.get().outbox_dir
    if not folder.exists():
        return []
    out = []
    for path in folder.iterdir():
        if "-dispatch-" in path.name:
            continue
        item = None
        if path.suffix == ".eml" and contact.get("email"):
            item = _email(path, contact["email"])
        elif path.name.endswith(".sms.txt") and contact.get("phone"):
            item = _text(path, contact["phone"])
        if item:
            out.append(item)
    return sorted(out, key=lambda m: m["id"], reverse=True)
