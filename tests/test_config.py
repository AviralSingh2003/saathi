"""The minimal .env reader."""

from __future__ import annotations

from saathi.config import _parse_value


def test_values_and_comments():
    assert _parse_value("1   # offline") == "1"
    assert _parse_value("us-east-1") == "us-east-1"
    assert _parse_value('"has # inside"') == "has # inside"
    assert _parse_value("+15555550100   # E.164") == "+15555550100"


def test_an_empty_value_followed_by_a_comment_is_empty():
    # e.g. SES_SENDER=            # SES-verified sender  -> not configured (must not switch SES on)
    assert _parse_value("            # SES-verified sender, e.g. saathi@yourdomain.com") == ""
    assert _parse_value("") == ""
