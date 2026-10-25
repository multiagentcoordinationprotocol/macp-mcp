"""The MCP surface, exercised in-process through the official MCP client."""

from __future__ import annotations

import asyncio
from typing import Any

import grpc
import pytest
from macp_sdk import MacpAckError, MacpSessionError, MacpTransportError
from macp_sdk.errors import AckFailure
from mcp import Client
from mcp.types import CallToolResult

from macp_mcp.config import Settings
from macp_mcp.server import INSTRUCTIONS, _guarded, build_server

from .fakes import FakeClient

EXPECTED_TOOLS = {
    "start_session",
    "propose",
    "evaluate",
    "vote",
    "commit",
    "get_session",
    "get_commitment",
    "verify",
}
READ_ONLY = {"get_session", "get_commitment", "verify"}


def _body(result: Any) -> dict[str, Any]:
    body = result.structured_content
    assert isinstance(body, dict)
    assert set(body) != {"result"}  # results are returned unwrapped
    return body


def _server(fake: FakeClient, agent: str = "coordinator") -> Any:
    return build_server(Settings(agent_id=agent), client_factory=fake.factory)  # type: ignore[arg-type]


def test_lists_tools_with_annotations() -> None:
    async def go() -> Any:
        async with Client(_server(FakeClient())) as client:
            return (await client.list_tools()).tools, client

    tools, _ = asyncio.run(go())
    assert {t.name for t in tools} == EXPECTED_TOOLS
    for tool in tools:
        assert tool.description
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is (tool.name in READ_ONLY)


def test_full_loop_over_mcp() -> None:
    fake = FakeClient()

    async def go() -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        async with Client(_server(fake)) as client:

            async def call(tool: str, **args: Any) -> dict[str, Any]:
                result = await client.call_tool(tool, args)
                assert not result.is_error, result.content
                out[tool] = _body(result)
                return out[tool]

            sid = (await call("start_session", intent="decide", participants=["alice"]))[
                "session_id"
            ]
            await call("propose", session_id=sid, proposal_id="p1", option="canary")
            await call(
                "evaluate",
                session_id=sid,
                proposal_id="p1",
                recommendation="APPROVE",
                confidence=0.7,
            )
            await call("vote", session_id=sid, proposal_id="p1", vote="APPROVE")
            await call(
                "commit",
                session_id=sid,
                action="deployment.approved",
                authority_scope="release",
                reason="p1 won",
            )
            await call("get_session", session_id=sid)
            got = await call("get_commitment", session_id=sid)
            await call("verify", commitment_hash=got["commitment_hash"], session_id=sid)
        return out

    out = asyncio.run(go())
    assert out["commit"]["session_state"] == "RESOLVED"
    assert out["get_session"]["state"] == "RESOLVED"
    assert out["get_session"]["committed"] is True
    assert out["verify"]["outcome"] == "match"


def test_errors_surface_as_tool_errors() -> None:
    async def go() -> Any:
        async with Client(_server(FakeClient())) as client:
            return await client.call_tool("verify", {"commitment_hash": "sha256:" + "0" * 64})

    result = asyncio.run(go())
    assert result.is_error
    # Exactly the bridge's message: no SDK "Error executing tool" prefix.
    assert [c.text for c in result.content] == [  # type: ignore[union-attr]
        "pass exactly one of session_id or payload_b64"
    ]


def test_argument_validation() -> None:
    async def go() -> Any:
        async with Client(_server(FakeClient())) as client:
            return await client.call_tool(
                "evaluate",
                {
                    "session_id": "s",
                    "proposal_id": "p1",
                    "recommendation": "APPROVE",
                    "confidence": 2.0,
                },
            )

    assert asyncio.run(go()).is_error


def test_server_metadata() -> None:
    async def go() -> Any:
        async with Client(_server(FakeClient())) as client:
            return client.server_info, client.instructions

    info, instructions = asyncio.run(go())
    assert info.name == "macp-mcp"
    assert instructions == INSTRUCTIONS


def test_build_server_loads_settings_from_env(monkeypatch) -> None:
    monkeypatch.setenv("MACP_AGENT_ID", "env-agent")
    server = build_server(client_factory=lambda _s: FakeClient())  # type: ignore[arg-type,return-value]
    assert server.name == "macp-mcp"


class _FakeRpcError(grpc.RpcError):
    def __init__(self, macp_code: str | None) -> None:
        self._macp_code = macp_code

    def code(self) -> grpc.StatusCode:
        return grpc.StatusCode.NOT_FOUND

    def details(self) -> str:
        return "session not found"

    def trailing_metadata(self) -> Any:
        return (("macp-error-code", self._macp_code),) if self._macp_code else ()


async def _raise(exc: BaseException) -> dict[str, Any]:
    raise exc


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (
            MacpAckError(AckFailure(code="SESSION_NOT_OPEN", message="closed")),
            "SESSION_NOT_OPEN: closed",
        ),
        (
            MacpTransportError("stream lagged", macp_code="STREAM_LAGGED"),
            "STREAM_LAGGED: stream lagged",
        ),
        (MacpTransportError("deadline", code="DEADLINE_EXCEEDED"), "DEADLINE_EXCEEDED: deadline"),
        (MacpTransportError("connection refused"), "connection refused"),
        (MacpSessionError("session not started"), "session not started"),
        (_FakeRpcError("SESSION_NOT_FOUND"), "SESSION_NOT_FOUND: session not found"),
        (_FakeRpcError(None), "NOT_FOUND: session not found"),
        (ValueError("bad input"), "bad input"),
    ],
)
def test_guarded_maps_anticipated_errors(exc: BaseException, expected: str) -> None:
    result = asyncio.run(_guarded(_raise(exc)))
    assert isinstance(result, CallToolResult)
    assert result.is_error
    assert [c.text for c in result.content] == [expected]  # type: ignore[union-attr]


def test_guarded_lets_crashes_through() -> None:
    with pytest.raises(KeyError):
        asyncio.run(_guarded(_raise(KeyError("boom"))))
