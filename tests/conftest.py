"""Shared fixtures. Every test runs in mock mode against a throwaway database."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    # Never read the developer's .env (it may point at a real AWS profile).
    monkeypatch.setattr("saathi.config._DOTENV_LOADED", True)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("SAATHI_DB", str(tmp_path / "saathi.db"))
    monkeypatch.setenv("SAATHI_OUTBOX", str(tmp_path / "outbox"))
    monkeypatch.setenv("MOCK", "1")
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    monkeypatch.delenv("SES_SENDER", raising=False)
    monkeypatch.delenv("MCP_URL", raising=False)
    monkeypatch.delenv("PRIYA_TOKEN", raising=False)
    for name in ("SMS_ENABLED", "PRIYA_PHONE", "PRIYA_EMAIL", "EMERGENCY_CHECK_TIMEOUT",
                 "EMERGENCY_CONFIRM_SECONDS", "DIGEST_TIME", "EMERGENCY_NUMBER", "SMTP_HOST", "SMTP_PORT",
                 "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_SECURITY"):
        monkeypatch.delenv(name, raising=False)
    yield tmp_path


@pytest.fixture
def seeded(isolated_env):
    from saathi.seed import seed

    return seed()


@pytest.fixture
def server(seeded, monkeypatch):
    """A real Saathi MCP server over Streamable HTTP on a free port."""
    from saathi.simulate import free_port, start_server

    port = free_port()
    monkeypatch.setenv("MCP_PORT", str(port))
    srv = start_server(port)
    yield f"http://127.0.0.1:{port}/mcp"
    srv.should_exit = True


@pytest.fixture
def host(server):
    from saathi.host.agent import Host

    h = Host()
    yield h
    h.close()


def outbox_files(tmp_path, kind: str | None = None):
    folder = tmp_path / "outbox"
    pattern = f"*-{kind}-*.eml" if kind else "*.eml"
    return sorted(folder.glob(pattern)) if folder.exists() else []


def sms_files(tmp_path, kind: str | None = None):
    folder = tmp_path / "outbox"
    pattern = f"*-{kind}-*.sms.txt" if kind else "*.sms.txt"
    return sorted(folder.glob(pattern)) if folder.exists() else []


def sms_texts(tmp_path, kind: str | None = None) -> list[str]:
    return [p.read_text(encoding="utf-8").split("\n\n", 1)[1].strip() for p in sms_files(tmp_path, kind)]


os.environ.setdefault("PYTHONIOENCODING", "utf-8")
