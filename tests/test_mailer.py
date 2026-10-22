"""Email delivery order: Amazon SES, else SMTP (e.g. Gmail), else outbox only; a copy is always kept."""

from __future__ import annotations

import smtplib
from email import message_from_bytes

import pytest

from saathi.core import SaathiError, mailer, notify, people
from tests.conftest import outbox_files


class FakeSMTP:
    sent: list = []
    fail = False

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        if FakeSMTP.fail:
            raise smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")
        self.calls.append(("login", user, password))

    def send_message(self, msg):
        FakeSMTP.sent.append((self.host, self.port, self.calls, msg))


@pytest.fixture
def gmail(monkeypatch):
    FakeSMTP.sent, FakeSMTP.fail = [], False
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("MOCK", "0")
    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("SMTP_USER", "saathi.demo@gmail.com")
    monkeypatch.setenv("SMTP_PASSWORD", "abcd efgh ijkl mnop")
    return FakeSMTP


def test_smtp_sends_and_keeps_a_copy(seeded, isolated_env, gmail):
    result = mailer.send("priya@example.com", "Priya", "An update about Meera", "Hi Priya,\n\nAll well.", "update")
    assert result["channel"] == "smtp"
    host, port, calls, msg = gmail.sent[0]
    assert (host, port) == ("smtp.gmail.com", 587)
    assert calls == ["starttls", ("login", "saathi.demo@gmail.com", "abcdefghijklmnop")]  # spaces removed
    assert "saathi.demo@gmail.com" in msg["From"] and msg["To"] == "Priya <priya@example.com>"
    copy = message_from_bytes(outbox_files(isolated_env, "update")[0].read_bytes())
    assert copy["X-Saathi-Channel"] == "smtp" and copy["Subject"] == "An update about Meera"


def test_smtp_failure_is_reported_not_raised_to_family_flow(seeded, gmail):
    gmail.fail = True
    with pytest.raises(SaathiError, match="smtp.gmail.com"):
        mailer.send("priya@example.com", "Priya", "Hi", "Body", "update")
    contact = people.contacts("meera")[0]
    result = notify.deliver_to(contact, "update", "Hi", "Body", "Text body")
    assert result["errors"] and "abcd" not in " ".join(result["errors"])  # the password never leaks
    assert result["deliveries"]  # the text copy still went


def test_ses_wins_when_configured(seeded, gmail, monkeypatch):
    monkeypatch.setenv("SES_SENDER", "saathi@example.com")
    monkeypatch.setattr(mailer, "_send_ses", lambda *a: {"status": "delivered", "channel": "ses", "message_id": "m"})
    assert mailer.send("priya@example.com", "Priya", "Hi", "Body", "update")["channel"] == "ses"
    assert gmail.sent == []


def test_without_a_password_smtp_stays_off(seeded, gmail, monkeypatch):
    monkeypatch.setenv("SMTP_PASSWORD", "")  # not pasted in yet
    assert mailer.send("priya@example.com", "Priya", "Hi", "Body", "update")["channel"] == "outbox"
    assert gmail.sent == []


def test_offline_never_touches_smtp(seeded, gmail, monkeypatch, isolated_env):
    monkeypatch.setenv("MOCK", "1")
    assert mailer.send("priya@example.com", "Priya", "Hi", "Body", "update")["channel"] == "outbox"
    assert gmail.sent == []
    assert message_from_bytes(outbox_files(isolated_env, "update")[0].read_bytes())["X-Saathi-Channel"] == "outbox"
