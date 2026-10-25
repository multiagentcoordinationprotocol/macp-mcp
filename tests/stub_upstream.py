"""A stub upstream MCP server for the gate tests.

In-process: ``build()`` returns the server, and ``CALLS`` records each call.
Over stdio: ``python tests/stub_upstream.py``; each call is also appended as
one JSON line to ``$STUB_UPSTREAM_LOG`` when that is set, so a test in
another process can prove the upstream was (or was not) called.
"""

from __future__ import annotations

import json
import os
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent

UPSTREAM_META = {"example.com/upstream": {"trace": "t-1"}}

CALLS: list[tuple[str, dict[str, Any]]] = []


def _record(tool: str, arguments: dict[str, Any]) -> None:
    CALLS.append((tool, arguments))
    log = os.environ.get("STUB_UPSTREAM_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"tool": tool, "arguments": arguments}) + "\n")


def build() -> MCPServer:
    server = MCPServer("stub-upstream")

    @server.tool(description="Write a file (stub).")
    def write_file(path: str, content: str) -> CallToolResult:
        _record("write_file", {"path": path, "content": content})
        return CallToolResult(
            content=[TextContent(type="text", text=f"wrote {len(content)} bytes to {path}")],
            _meta=UPSTREAM_META,
        )

    @server.tool(description="Read a file (stub).")
    def read_file(path: str) -> str:
        _record("read_file", {"path": path})
        return f"contents of {path}"

    @server.tool(description="Delete a file (stub).")
    def delete_file(path: str) -> str:
        _record("delete_file", {"path": path})
        return f"deleted {path}"

    @server.tool(description="Leak the environment variable names (stub).")
    def env_names() -> str:
        return json.dumps(sorted(os.environ))

    return server


if __name__ == "__main__":
    build().run("stdio")
