"""The MCP server over a real Streamable HTTP session."""

from __future__ import annotations

import httpx
import pytest

from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client

EXPECTED_TOOLS = {
    "get_person_context", "remember", "recall", "set_reminder", "list_reminders", "complete_reminder",
    "log_mood_selfreport", "get_due_events", "send_family_update", "send_due_digests",
    "leave_family_note", "reply_to_family_note", "get_family_dashboard", "update_settings",
    "escalate_to_contact", "reflect_turn",
}
CARD_TOOLS = {
    "set_reminder": "ui://saathi/reminder-card",
    "get_family_dashboard": "ui://saathi/family-dashboard",
}


async def test_initialize_negotiates_2025_11_25(server):
    async with Client(streamable_http_client(server), mode="legacy") as client:
        assert client.protocol_version == "2025-11-25"
        assert client.server_info.name == "saathi"


async def test_all_tools_listed_with_card_meta(server):
    async with Client(streamable_http_client(server), mode="legacy") as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == EXPECTED_TOOLS
    for name, uri in CARD_TOOLS.items():
        assert tools[name].meta["ui"]["resourceUri"] == uri
    assert tools["recall"].meta is None or "ui" not in (tools["recall"].meta or {})


async def test_card_resources_are_mcp_apps(server):
    async with Client(streamable_http_client(server), mode="legacy") as client:
        for uri in CARD_TOOLS.values():
            contents = (await client.read_resource(uri)).contents[0]
            assert contents.mime_type == "text/html;profile=mcp-app"
            assert "window.McpCard" in contents.text  # the inlined card runtime


async def test_tool_call_returns_structured_content(server):
    async with Client(streamable_http_client(server), mode="legacy") as client:
        result = await client.call_tool("get_person_context", {"person_id": "meera"})
    assert not result.is_error
    assert result.structured_content["display_name"] == "Meera"
    assert "insights" in result.structured_content["knowledge"]


async def test_user_errors_reach_the_caller(server):
    async with Client(streamable_http_client(server), mode="legacy") as client:
        result = await client.call_tool("send_family_update", {"person_id": "meera", "contact": "Nobody"})
    assert result.is_error
    assert "don't have a contact called 'Nobody'" in result.content[0].text


@pytest.mark.parametrize("origin,expected", [("http://evil.example", 403), ("http://127.0.0.1:8080", 200)])
def test_origin_is_validated(server, origin, expected):
    response = httpx.post(
        server,
        headers={"Origin": origin, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}},
    )
    assert response.status_code == expected
    if expected == 200:
        assert response.headers.get("access-control-expose-headers", "").lower().find("mcp-session-id") >= 0
        assert response.headers.get("mcp-session-id")
