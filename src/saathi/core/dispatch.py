"""Contacting emergency services.

Software can't safely dial real emergency lines (false calls are illegal in many places,
and telephony APIs don't support emergency calling), so this build ships a clearly
labelled SimulatedDispatcher. A real deployment would plug in a professional monitored
response service here (the way Alexa Emergency Assist connects to 24/7 trained agents
who dispatch help), behind the same interface.
"""

from __future__ import annotations

import secrets
from typing import Protocol

from saathi import config
from saathi.core import clock, people


class Dispatcher(Protocol):
    name: str

    def dispatch(self, person: dict, reason: str, said: str) -> dict: ...


class SimulatedDispatcher:
    name = "simulated"

    def dispatch(self, person: dict, reason: str, said: str) -> dict:
        settings = config.get()
        reference = f"SIM-{secrets.randbelow(9000) + 1000}"
        now = clock.now()
        record = (
            f"SIMULATED EMERGENCY DISPATCH (no real call was placed)\n"
            f"Reference: {reference}\nTime: {clock.iso(now)}\nService: {settings.emergency_number}\n"
            f"Person: {person['name']} ({person['display_name']})\nAddress: {person.get('address') or 'not on file'}\n"
            f"Reason: {reason}\nThey said: \"{said}\"\n"
        )
        settings.outbox_dir.mkdir(parents=True, exist_ok=True)
        path = settings.outbox_dir / f"{now.strftime('%Y%m%d-%H%M%S')}-dispatch-{reference.lower()}.txt"
        path.write_text(record, encoding="utf-8")
        people.log(person["person_id"], "emergency_dispatch", detail=f"{reference} {reason}")
        return {"status": "dispatched", "reference": reference, "provider": self.name,
                "number": settings.emergency_number, "ts": clock.iso(now), "path": str(path)}


def get_dispatcher() -> Dispatcher:
    return SimulatedDispatcher()
