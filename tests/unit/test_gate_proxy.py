"""The gate server: an in-process stub upstream, the in-memory runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from macp.v1 import core_pb2
from macp_sdk import commitment_hash
from mcp import Client
from mcp.types import CallToolResult

from macp_mcp.config import Settings
from macp_mcp.gate.context import UnsafeIntegerError
from macp_mcp.gate.engine import META_KEY, GateEngine
from macp_mcp.gate.policy import MODE_QUORUM, parse_policy
from macp_mcp.gate.proxy import INSTRUCTIONS, build_gate_server
from tests import stub_upstream

from .gate_fakes import GateFakeClient, sent_payloads

POLICY = parse_policy(
    {
        "version": 1,
        "server": "fs",
        "rules": [{"tool": "write_*", "mode": MODE_QUORUM, "participants": ["alice", "bob"]}],
    },
    "gate",
)
WRITE = {"path": "/a", "content": "hi"}


@pytest.fixture(autouse=True)
def _fresh_calls() -> None:
    stub_upstream.CALLS.clear()


def run(fake: GateFakeClient, body: Callable[[Client], Awaitable[Any]]) -> Any:
    engine = GateEngine(
        Settings(agent_id="gate"),
        client_factory=fake.factory,  # type: ignore[arg-type]
        poll_seconds=0.0,
    )
    server = build_gate_server(POLICY, engine, lambda: Client(stub_upstream.build()))

    async def go() -> Any:
        async with Client(server) as client:
            assert client.instructions == INSTRUCTIONS
            return await body(client)

    return asyncio.run(go())


def text(result: CallToolResult) -> str:
    return result.content[0].text  # type: ignore[union-attr]


def test_tools_pass_through_verbatim() -> None:
    async def body(client: Client) -> None:
        listed = (await client.list_tools()).tools
        async with Client(stub_upstream.build()) as direct:
            assert listed == (await direct.list_tools()).tools

    run(GateFakeClient(), body)


def test_ungated_call_is_forwarded_without_a_session() -> None:
    fake = GateFakeClient()

    async def body(client: Client) -> CallToolResult:
        return await client.call_tool("read_file", {"path": "/a"})

    result = run(fake, body)
    assert text(result) == "contents of /a"
    assert not result.meta or META_KEY not in result.meta
    assert fake.sent == []
    assert stub_upstream.CALLS == [("read_file", {"path": "/a"})]


def test_approved_call_is_forwarded_with_commitment_meta() -> None:
    fake = GateFakeClient([("alice", "Approve"), ("bob", "Approve")])

    async def body(client: Client) -> CallToolResult:
        return await client.call_tool("write_file", WRITE)

    result = run(fake, body)
    assert not result.is_error
    assert text(result) == "wrote 2 bytes to /a"
    assert stub_upstream.CALLS == [("write_file", WRITE)]
    assert result.meta is not None
    # The upstream's own _meta survives next to the gate's.
    for key, value in stub_upstream.UPSTREAM_META.items():
        assert result.meta[key] == value
    meta = result.meta[META_KEY]
    (start,) = sent_payloads(fake, "SessionStart")
    assert meta["session_id"] == start.session_id
    assert meta["mode"] == MODE_QUORUM
    assert meta["outcome"] == "approve"
    (commit,) = sent_payloads(fake, "Commitment")
    payload = core_pb2.CommitmentPayload()
    payload.ParseFromString(commit.payload)
    assert meta["commitment_hash"] == commitment_hash(payload)


def test_denied_call_never_reaches_the_upstream() -> None:
    fake = GateFakeClient([("alice", "Reject")])

    async def body(client: Client) -> CallToolResult:
        return await client.call_tool("write_file", WRITE)

    result = run(fake, body)
    assert result.is_error
    (start,) = sent_payloads(fake, "SessionStart")
    assert (
        text(result) == f"POLICY_DENIED: tool call write_file denied by session {start.session_id}"
    )
    assert stub_upstream.CALLS == []


def test_unsafe_integer_is_refused_before_a_session() -> None:
    fake = GateFakeClient()

    async def body(client: Client) -> CallToolResult:
        return await client.call_tool("write_file", {"path": "/a", "content": "x", "n": 2**60})

    result = run(fake, body)
    assert result.is_error
    assert text(result) == UnsafeIntegerError.TEMPLATE.format(tool="write_file")
    assert fake.sent == []
    assert stub_upstream.CALLS == []


def test_runtime_failure_is_a_coded_tool_error() -> None:
    fake = GateFakeClient()
    fake.reject_next = "UNAUTHENTICATED"

    async def body(client: Client) -> CallToolResult:
        return await client.call_tool("write_file", WRITE)

    result = run(fake, body)
    assert result.is_error
    assert text(result) == "UNAUTHENTICATED: rejected"
    assert stub_upstream.CALLS == []
    assert fake.closed  # the engine's client closes with the server
