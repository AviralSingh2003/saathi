"""Emergency escalation, called by the host's emergency path, never decided by the conversational model.

medical_emergency: emergency services are contacted FIRST (core/dispatch.py), then the
family get an urgent text and email saying help has already been called.
self_harm / asked_for_help: an urgent text and email to the family.
checking / all_clear: Saathi couldn't confirm she's safe and is checking with her; then
the outcome if she says she's fine.

Messages are fixed templates (no model writes them) and never diagnose.
"""

from __future__ import annotations

from saathi import config
from saathi.core import SaathiError, clock, dispatch, notify, people

REASONS = ("medical_emergency", "self_harm", "asked_for_help", "checking", "all_clear")
DISCLAIMER = "\n\nThis is an automatic message from Saathi. It is not a medical assessment."


def _short(said: str, limit: int = 120) -> str:
    said = " ".join((said or "").split())
    return said if len(said) <= limit else said[: limit - 1] + "…"


def escalate(person_id: str, reason_code: str, said: str = "") -> dict:
    if reason_code not in REASONS:
        raise SaathiError(f"reason_code must be one of: {', '.join(REASONS)}.")
    person = people.get_person(person_id)
    name = person["display_name"]
    when = clock.now().strftime("%H:%M")
    quote = _short(said)
    result = {"status": "notified", "reason_code": reason_code, "dispatched": False, "reference": None}

    if reason_code == "medical_emergency":
        try:
            dispatched = dispatch.get_dispatcher().dispatch(person, reason_code, said)
            result.update(dispatched=True, reference=dispatched["reference"], provider=dispatched["provider"],
                          number=dispatched["number"], dispatched_at=dispatched["ts"])
        except Exception as exc:  # never let a dispatch failure stop the family alert
            result["dispatch_error"] = str(exc)
        number = config.get().emergency_number
        if result["dispatched"]:
            simulated = ", simulated in this demo" if result.get("provider") == "simulated" else ""
            headline = (f"Emergency services have been contacted for {name} at {when} ({number}, ref "
                        f"{result['reference']}{simulated}).")
        else:
            headline = f"Saathi could NOT reach emergency services for {name}. Please call {number} for them now."
        subject = f"URGENT: emergency services contacted for {name}" if result["dispatched"] else f"URGENT: call {number} for {name}"
        sms_text = f"URGENT from Saathi: {headline} {name} said: \"{quote}\". Please try to reach {name} now."
        body = (f"{headline}\n\n{name} said: \"{said}\"\nAddress on file: {person.get('address') or 'not on file'}\n\n"
                f"Please try to reach {name} now.")
    elif reason_code == "self_harm":
        subject = f"URGENT: please check on {name} now"
        sms_text = (f"URGENT from Saathi: at {when} {name} said something that sounded like they may need support "
                    f"right now. Please reach {name} as soon as you can.")
        body = (f"At {when}, {name} said something that sounded like they may need support right now:\n\n"
                f"\"{said}\"\n\nPlease reach {name} as soon as you can. Saathi gave them crisis line details.")
    elif reason_code == "asked_for_help":
        subject = f"{name} asked for help"
        sms_text = f"Saathi: at {when} {name} asked for help and would like you to get in touch as soon as you can."
        body = f"At {when}, {name} asked Saathi for help:\n\n\"{said}\"\n\nPlease get in touch as soon as you can."
    elif reason_code == "checking":
        subject = f"Saathi is checking on {name}"
        sms_text = (f"Saathi: at {when} {name} said something that might be worrying. Saathi is checking with "
                    f"{name} now and will contact emergency services if {name} needs help or doesn't answer.")
        body = (f"At {when}, {name} said: \"{said}\"\n\nSaathi couldn't be sure {name} is safe, so it's asking "
                f"{name} now. If {name} says they need help, or doesn't answer within "
                f"{config.get().emergency_confirm_seconds} seconds, Saathi will contact emergency services and "
                "let you know.")
    else:  # all_clear
        subject = f"{name} says they're okay"
        sms_text = f"Saathi: {name} says they're okay. No emergency services were contacted."
        body = f"{name} told Saathi they're okay. No emergency services were contacted."

    sent = notify.notify(person_id, reason_code, subject, lambda c: f"Hi {c['name']},\n\n{body}{DISCLAIMER}",
                         lambda c: sms_text, urgent=True, detail=sms_text)
    people.log(person_id, "escalation", detail=f"{reason_code} -> {sent['delivered']} deliveries")
    names = [r["contact_name"] for r in sent["results"]]
    result.update(contact_name=names[0] if names else None, contact_names=names,
                  delivered=sent["delivered"], errors=sent["errors"])
    if not names:
        result["status"] = "no_contact"
    return result
