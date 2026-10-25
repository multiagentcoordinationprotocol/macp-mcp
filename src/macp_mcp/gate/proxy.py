"""``macp-mcp gate``: an MCP server that proxies one upstream MCP server.

Tool listings pass through verbatim. A ``tools/call`` whose tool matches a
``gate.json`` rule first runs a MACP session (:class:`GateEngine`) and only
reaches the upstream if that session commits APPROVE; the upstream's result
then carries the Commitment under ``_meta[META_KEY]``, next to any ``_meta``
the upstream set. A call that matches no rule is forwarded untouched.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import grpc
from macp_sdk import MacpSdkError
from mcp import Client
from mcp.server.lowlevel import Server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
)

from .. import __version__
from ..server import _tool_error, error_text
from .engine import META_KEY, Approved, GateEngine
from .policy import GatePolicy

log = logging.getLogger("macp_mcp.gate")

#: Opens a connected client to the upstream (``mcp.Client(...)`` in production).
UpstreamFactory = Callable[[], AbstractAsyncContextManager[Client]]

INSTRUCTIONS = (
    "Some tools on this server are gated by MACP: a call to one opens a MACP session and "
    "runs only after the named approvers approve it. A refused call returns an error "
    "starting POLICY_DENIED or SESSION_NOT_OPEN; do not retry it unchanged."
)


def build_gate_server(
    policy: GatePolicy,
    engine: GateEngine,
    upstream: UpstreamFactory,
) -> Server[Client]:
    """The gate's MCP server. The upstream connection lives as long as the server."""

    @asynccontextmanager
    async def lifespan(_: Server[Client]) -> AsyncIterator[Client]:
        try:
            async with upstream() as client:
                yield client
        finally:
            await engine.aclose()

    async def list_tools(ctx: Any, params: PaginatedRequestParams | None) -> ListToolsResult:
        client: Client = ctx.lifespan_context
        # Bypass the client-side cache: the gate's listing is the upstream's, live.
        return await client.list_tools(
            cursor=params.cursor if params else None, cache_mode="bypass"
        )

    async def call_tool(ctx: Any, params: CallToolRequestParams) -> CallToolResult:
        client: Client = ctx.lifespan_context
        rule = policy.rule_for(params.name)
        if rule is None:
            return await client.call_tool(params.name, params.arguments)
        try:
            verdict = await engine.decide(rule, policy.server, params.name, params.arguments)
        except (MacpSdkError, grpc.RpcError, ValueError) as exc:
            return _tool_error(error_text(exc))
        if not isinstance(verdict, Approved):
            log.info("gate: refused %s: %s", params.name, verdict.text)
            return _tool_error(verdict.text)
        result = await client.call_tool(params.name, params.arguments)
        meta = dict(result.meta or {})
        meta[META_KEY] = verdict.meta()
        return result.model_copy(update={"meta": meta})

    return Server(
        "macp-mcp-gate",
        version=__version__,
        title="MACP tool-call gate",
        instructions=INSTRUCTIONS,
        website_url="https://multiagentcoordinationprotocol.io",
        lifespan=lifespan,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
