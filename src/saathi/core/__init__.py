"""Core logic: all state and deterministic rules. No LLM decisions live here."""

from mcp.server.mcpserver.exceptions import ToolError


class SaathiError(ToolError):
    """A user-facing error.

    Subclassing the SDK's ToolError means the MCP server passes the message to the
    caller (other exceptions are deliberately hidden as crashes).
    """
