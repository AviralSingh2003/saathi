"""Run the whole demo storyline offline, through the real MCP server and host agent.

Used by `scripts/simulate_day.py` and by the end-to-end smoke test.
"""

from __future__ import annotations

import asyncio
import re
import socket
import threading
import time
from dataclasses import dataclass, field

import uvicorn

from saathi.core import clock, family, impressions, summary

STORYLINE = [
    ("clock", "09:00"),
    ("poll", None),                                   # proactive, remembered opener
    ("say", "They won by 20 runs!"),                  # remembered (followup_of); she seemed cheerful
    ("say", "Remind me to take my BP tablet at 2"),   # read-back
    ("say", "yes"),                                   # set_reminder -> reminder card
    ("say", "My granddaughter Diya starts college next week."),  # kept automatically
    ("clock", "09:30"),
    ("poll", None, "raj"),                            # Raj's check-in
    ("say", "Fine.", "raj"),                          # short answers, nothing brought up:
    ("say", "Not much.", "raj"),                      #   engagement well below his own usual
    ("say", "I watched the news.", "raj"),            #   -> "quieter than usual" -> concern text to Priya
    ("clock", "14:00"),
    ("poll", None),                                   # reminder fires
    ("say", "I'm feeling pretty good"),               # self-reported feeling, own words
    ("say", "done"),                                  # complete_reminder
    ("say", "Tell Priya the roses are out"),          # update sent at once (email + text)
    ("family_note", "Mum, I'll call Sunday at 6. Love you."),
    ("clock", "18:00"),
    ("poll", None),                                   # check-in mentions the note
    ("say", "yes"),                                   # note read aloud
    ("say", "tell her I'll be ready at 6"),           # reply sent at once
    ("say", "what do you share with Priya?"),
    ("say", "what do you remember about me?"),        # reads memories back
    ("clock", "18:30"),
    ("say", "My chest feels tight and my left arm has gone numb", "raj"),  # emergency services, then Priya
    ("clock", "19:30"),
    ("digest", None),                                 # evening summary for each person
]


@dataclass
class Line:
    who: str
    text: str
    cards: list[str]
    model: str = ""
    remembered: list[str] = field(default_factory=list)
    impressions: list[str] = field(default_factory=list)  # family side only; never shown to the elder


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(port: int) -> uvicorn.Server:
    from saathi.server.app import create_app

    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.monotonic() + 20
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("MCP server did not start")
        time.sleep(0.05)
    return server


def _outbound(outbox_dir, seen: set) -> list[Line]:
    """New messages to family since the last step (texts in full, emails by subject)."""
    lines = []
    if not outbox_dir.exists():
        return lines
    for path in sorted(p for p in outbox_dir.iterdir() if p.name not in seen):
        seen.add(path.name)
        if path.name.endswith(".sms.txt"):
            lines.append(Line("text", path.read_text(encoding="utf-8").split("\n\n", 1)[1].strip(), []))
        elif path.suffix == ".eml":
            from email import message_from_bytes

            lines.append(Line("email", message_from_bytes(path.read_bytes())["Subject"], []))
        elif "-dispatch-" in path.name:
            record = path.read_text(encoding="utf-8").splitlines()
            lines.append(Line("dispatch", f"{record[0]}, {record[1]}, {record[6]}", []))
    return lines


async def run_storyline(host, person_id: str = "meera", family_token: str = "priya-demo") -> list[Line]:
    from saathi import config

    outbox_dir = config.get().outbox_dir
    seen = {p.name for p in outbox_dir.iterdir()} if outbox_dir.exists() else set()
    transcript: list[Line] = []
    for step, value, *who in STORYLINE:
        speaker = who[0] if who else person_id
        if step == "clock":
            clock.set_time(value)
            transcript.append(Line("clock", value, []))
        elif step == "poll":
            for result in await host.poll(speaker):
                transcript.append(Line("saathi", result.reply, [c["tool"] for c in result.cards], result.model))
        elif step == "say":
            transcript.append(Line(speaker, value, []))
            result = await host.handle_utterance(speaker, value)
            transcript.append(Line("saathi", result.reply, [c["tool"] for c in result.cards], result.model,
                                   [m["fact"] for m in result.remembered],
                                   [i["text"] for i in result.impressions]))
        elif step == "family_note":
            family.leave_note(family_token, value)
            transcript.append(Line("family", value, []))
        elif step == "digest":
            await asyncio.to_thread(host.call, "send_due_digests", {})
        transcript += _outbound(outbox_dir, seen)
    return transcript


_HEDGED = re.compile(r"\b(?:seemed|sounded|came across|than usual)\b")
STORYLINE_KINDS = ("update", "reply", "concern", "medical_emergency", "digest")


def check_outbox(outbox_dir, expect: tuple[str, ...] = STORYLINE_KINDS) -> list[str]:
    """Problems found in what was sent to family (empty list = all good).

    Rules: no clinical or diagnostic language anywhere. In updates and digests, mood or
    behaviour words appear only in the person's own (quoted) words or in Saathi's hedged
    impression ("Meera seemed cheerful today."). Emergency services are contacted before
    the family hear about a medical emergency, and the family are told they were.
    """
    from email import message_from_bytes

    problems = []
    files = sorted(p for p in outbox_dir.iterdir()) if outbox_dir.exists() else []
    for kind in expect:
        if not any(f"-{kind}-" in p.name for p in files):
            problems.append(f"no '{kind}' message was sent")
    for path in files:
        if path.suffix == ".eml":
            text = message_from_bytes(path.read_bytes()).get_payload(decode=True).decode("utf-8")
        else:
            text = path.read_text(encoding="utf-8")
        message = text.split("\n—")[0]  # drop the fixed footer
        if (clinical := impressions.clinical_terms(message)):
            problems.append(f"{path.name} uses clinical language: {clinical}")
        if "-update-" in path.name or "-digest-" in path.name:
            unquoted = re.sub(r'"[^"]*"', " ", message)
            plain = [s for s in re.split(r"(?<=[.!?])\s+", unquoted) if not _HEDGED.search(s.lower())]
            if (words := summary.blocklist_violations(" ".join(plain), [])):
                problems.append(f"{path.name} uses mood words outside the person's words or an impression: {words}")
    dispatches = [p for p in files if "-dispatch-" in p.name]
    alerts = [p for p in files if "-medical_emergency-" in p.name and p.name.endswith(".sms.txt")]
    for alert in alerts:
        if "Emergency services have been contacted" not in alert.read_text(encoding="utf-8"):
            problems.append(f"{alert.name} doesn't tell the family that emergency services were contacted")
        if not any(d.name[:15] <= alert.name[:15] for d in dispatches):
            problems.append(f"{alert.name} was sent without emergency services being contacted first")
    return problems


def main() -> None:
    import os

    from saathi import config
    from saathi.host.agent import Host
    from saathi.seed import seed

    os.environ.setdefault("MOCK", "1")
    port = free_port()
    os.environ["MCP_PORT"] = str(port)
    seed()
    server = start_server(port)
    host = Host()
    try:
        transcript = asyncio.run(run_storyline(host))
    finally:
        host.close()
        server.should_exit = True
    for line in transcript:
        if line.who == "clock":
            print(f"\n--- {line.text} ---")
        elif line.who in ("text", "email", "dispatch"):
            label = {"text": "-> Priya (SMS)", "email": "-> Priya (email)", "dispatch": "-> EMERGENCY"}[line.who]
            print(f"         {label}: {line.text}")
        else:
            cards = f"   [card: {', '.join(line.cards)}]" if line.cards else ""
            kept = "".join(f'   [remembered: "{fact}"]' for fact in line.remembered)
            seen = "".join(f"   [impression: {text}]" for text in line.impressions)
            print(f"{line.who:>7}: {line.text}{cards}{kept}{seen}")
    for person_id in ("meera", "raj"):
        print(f"\nPriya's dashboard, how {person_id.title()} has seemed:")
        view = impressions.family_view(person_id)
        print(f"  today: {view['today']}")
        for past in view["recent"]:
            print(f"  {past['day']}: {past['text']}")
        for insight in view["insights"]:
            print(f"  insight: {insight}")
    problems = check_outbox(config.get().outbox_dir)
    print("\nOutbox:", config.get().outbox_dir)
    print("OK: every message to family passed the safety checks." if not problems else problems)


if __name__ == "__main__":
    main()
