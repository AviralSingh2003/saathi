"""MCP Apps cards (`ui://saathi/...`).

Each card is a single self-contained HTML document. The view-side runtime
(`card-runtime.js` from the mcp-voice-sim repo) is inlined at registration, so
the card needs no network access inside the host's sandboxed iframe.
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.apps import Apps

from saathi.config import REPO_ROOT

REMINDER_CARD = "ui://saathi/reminder-card"
FAMILY_DASHBOARD_CARD = "ui://saathi/family-dashboard"

_CARD_DIR = Path(__file__).parent / "card_html"
_RUNTIME = REPO_ROOT / "web" / "vendor" / "card-runtime.js"
_PLACEHOLDER = "<!--CARD_RUNTIME-->"

_CARDS = {
    REMINDER_CARD: ("reminder-card.html", "Reminder", "The reminder that was just set."),
    FAMILY_DASHBOARD_CARD: ("family-dashboard.html", "Family dashboard",
                            "Alerts, how they've seemed, updates and the notes thread."),
}


def _runtime_script() -> str:
    if not _RUNTIME.exists():
        raise FileNotFoundError(
            f"{_RUNTIME} is missing. Build and vendor mcp-voice-sim: "
            "cd ../mcp-voice-sim && npm install && npm run build && npm run vendor -- ../saathi/web/vendor"
        )
    # Guard against a literal </script> inside the bundle ending the tag early.
    code = _RUNTIME.read_text(encoding="utf-8").replace("</script", "<\\/script")
    return f"<script>{code}</script>"


def render(uri: str) -> str:
    filename = _CARDS[uri][0]
    html = (_CARD_DIR / filename).read_text(encoding="utf-8")
    return html.replace(_PLACEHOLDER, _runtime_script())


def register(apps: Apps) -> None:
    for uri, (_, title, description) in _CARDS.items():
        apps.add_html_resource(uri, render(uri), title=title, description=description, prefers_border=False)
