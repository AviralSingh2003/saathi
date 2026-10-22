"""Settings, read from the environment (and an optional .env file) on every call.

Reading on every call keeps tests simple: they set environment variables with
monkeypatch and every module sees the change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_DOTENV_LOADED = False


def _load_dotenv() -> None:
    """Minimal .env loader: KEY=VALUE lines, no overriding of real env vars."""
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    path = REPO_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), _parse_value(value))


def _parse_value(raw: str) -> str:
    """Quoted values are taken literally; unquoted values drop an inline ' # comment'."""
    raw = raw.strip()
    if raw.startswith("#"):  # "KEY=      # comment" means an empty value
        return ""
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    for marker in (" #", "\t#"):
        if marker in raw:
            raw = raw.split(marker, 1)[0]
    return raw.strip()


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    mock: bool
    demo_mode: bool
    db_path: Path
    outbox_dir: Path
    mcp_host: str
    mcp_port: int
    mcp_path: str
    ui_host: str
    ui_port: int
    poll_seconds: float
    aws_region: str
    ses_region: str
    bedrock_model_id: str
    ses_sender: str
    emergency_number: str
    crisis_line_text: str
    emergency_check_timeout: float
    emergency_confirm_seconds: int
    sms_enabled: bool
    sns_region: str
    digest_time: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    smtp_security: str

    @property
    def mcp_url(self) -> str:
        return os.environ.get("MCP_URL") or f"http://{self.mcp_host}:{self.mcp_port}{self.mcp_path}"

    @property
    def ui_origins(self) -> list[str]:
        explicit = os.environ.get("UI_ORIGIN")
        if explicit:
            return [o.strip() for o in explicit.split(",") if o.strip()]
        return [f"http://127.0.0.1:{self.ui_port}", f"http://localhost:{self.ui_port}"]

    @property
    def live_bedrock(self) -> bool:
        return not self.mock and bool(self.bedrock_model_id)

    @property
    def live_ses(self) -> bool:
        return not self.mock and bool(self.ses_sender)

    @property
    def live_sns(self) -> bool:
        return not self.mock and self.sms_enabled

    @property
    def live_smtp(self) -> bool:
        """Email through an SMTP server (e.g. Gmail) when SES isn't configured. Needs a password unless
        it's an unauthenticated local test server (SMTP_SECURITY=none, e.g. Mailpit)."""
        can_send = bool(self.smtp_password) or self.smtp_security == "none"
        return not self.mock and bool(self.smtp_host) and can_send and not self.live_ses


def get() -> Settings:
    _load_dotenv()
    env = os.environ
    return Settings(
        mock=_bool("MOCK", True),
        demo_mode=_bool("DEMO_MODE", True),
        db_path=Path(env.get("SAATHI_DB", REPO_ROOT / "data" / "saathi.db")),
        outbox_dir=Path(env.get("SAATHI_OUTBOX", REPO_ROOT / "outbox")),
        mcp_host=env.get("MCP_HOST", "127.0.0.1"),
        mcp_port=int(env.get("MCP_PORT", "8765")),
        mcp_path=env.get("MCP_PATH", "/mcp"),
        ui_host=env.get("UI_HOST", "127.0.0.1"),
        ui_port=int(env.get("UI_PORT", "8080")),
        poll_seconds=float(env.get("POLL_SECONDS", "5")),
        aws_region=env.get("AWS_REGION", "us-east-1"),
        ses_region=env.get("SES_REGION") or env.get("AWS_REGION", "us-east-1"),
        bedrock_model_id=env.get("BEDROCK_MODEL_ID", ""),
        ses_sender=env.get("SES_SENDER", ""),
        emergency_number=env.get("EMERGENCY_NUMBER", "911"),
        crisis_line_text=env.get("CRISIS_LINE_TEXT", "a crisis line such as 988"),
        emergency_check_timeout=float(env.get("EMERGENCY_CHECK_TIMEOUT", "10")),
        emergency_confirm_seconds=int(env.get("EMERGENCY_CONFIRM_SECONDS", "30")),
        sms_enabled=_bool("SMS_ENABLED", False),
        sns_region=env.get("SNS_REGION") or env.get("AWS_REGION", "us-east-1"),
        digest_time=env.get("DIGEST_TIME", "19:30"),
        smtp_host=env.get("SMTP_HOST", ""),
        smtp_port=int(env.get("SMTP_PORT") or "587"),
        smtp_user=env.get("SMTP_USER", ""),
        smtp_password=env.get("SMTP_PASSWORD", "").replace(" ", ""),  # Gmail shows app passwords in groups of 4
        smtp_from=env.get("SMTP_FROM") or env.get("SMTP_USER", ""),
        smtp_security=(env.get("SMTP_SECURITY") or "starttls").lower(),
    )
