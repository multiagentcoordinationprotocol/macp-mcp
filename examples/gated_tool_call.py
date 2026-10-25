"""Gate an upstream MCP server's tool calls behind MACP approval.

Spawns ``macp-mcp gate`` over stdio, exactly as an MCP client such as Claude
Desktop would, in front of a stub upstream (``tests/stub_upstream.py``). The
gate runs as MACP agent ``gate``; the approvers ``alice``, ``bob`` and
``carol`` are plain MACP SDK clients that find the gate's session by its
``context_id`` and cast their ballots. Four calls:

1. ``read_file`` matches no rule and passes straight through.
2. ``write_file`` (Quorum, 2 of 3): alice and bob approve, so the call is
   forwarded and its result carries the Commitment in ``_meta``.
3. ``write_file`` again, other arguments: alice and bob reject, so the call
   is refused and never reaches the upstream.
4. ``delete_file`` (Decision, short TTL): nobody votes, the session ends
   without a commitment, and the call is refused.

Needs a runtime::

    docker run -p 50051:50051 -e MACP_ALLOW_INSECURE=1 -e MACP_MEMORY_ONLY=1 \\
        ghcr.io/multiagentcoordinationprotocol/macp-runtime:0.9.0

Then::

    python examples/gated_tool_call.py                  # print the transcript
    python examples/gated_tool_call.py --out examples/gated-tool-call.md

The integration test (tests/integration/test_gate.py) runs the same ``run()``
and asserts on every step.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

from macp.v1 import envelope_pb2
from macp_sdk import (
    AsyncDecisionSession,
    AsyncMacpClient,
    AsyncQuorumSession,
    AuthConfig,
    MacpAckError,
)
from mcp import Client, StdioServerParameters

from macp_mcp.gate import context_id

ROOT = Path(__file__).resolve().parents[1]
SERVER = "filesystem"
DELETE_TTL_MS = 2_000
#: Set by the MCP server library on every result; not the gate's.
SERVER_INFO = "io.modelcontextprotocol/serverInfo"

POLICY = {
    "version": 1,
    "server": SERVER,
    "rules": [
        {
            "tool": "write_file",
            "mode": "macp.mode.quorum.v1",
            "participants": ["alice", "bob", "carol"],
            "threshold": 2,
            "ttl_ms": 60_000,
            "description": "Two of three reviewers approve every write.",
        },
        {
            "tool": "delete_file",
            "mode": "macp.mode.decision.v1",
            "participants": ["alice", "bob"],
            "threshold": 2,
            "ttl_ms": DELETE_TTL_MS,
            "description": "Deletes need both owners, quickly.",
        },
    ],
}


@dataclass
class Step:
    tool: str
    arguments: dict[str, Any]
    context_id: str | None
    ballots: list[str]
    is_error: bool
    text: str
    meta: dict[str, Any] | None
    session: dict[str, Any] | None = None


@dataclass
class Transcript:
    target: str
    tools: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    upstream_calls: list[dict[str, Any]] = field(default_factory=list)


def _gate(target: str, policy: Path, log: Path) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "macp_mcp",
            "gate",
            "--policy",
            str(policy),
            "--upstream",
            sys.executable,
            str(ROOT / "tests" / "stub_upstream.py"),
        ],
        env={
            "MACP_RUNTIME_TARGET": target,
            "MACP_AGENT_ID": "gate",
            "MACP_ALLOW_INSECURE": "1",
            "STUB_UPSTREAM_LOG": str(log),
            "PATH": os.environ.get("PATH", ""),
        },
    )


async def _find_session(
    client: AsyncMacpClient, auth: AuthConfig, cid: str, *, open_only: bool = False
) -> Any:
    """The newest session for this call, found by its context_id.

    The same call made twice has the same context_id, so an earlier run's
    sessions on a long-lived runtime match too; the newest is this call's.
    """
    for _ in range(100):
        matches = [
            m
            for m in await client.list_sessions(auth=auth)
            if m.context_id == cid and (not open_only or m.state == envelope_pb2.SESSION_STATE_OPEN)
        ]
        if matches:
            return max(matches, key=lambda m: m.started_at_unix_ms)
        await asyncio.sleep(0.1)
    raise RuntimeError(f"no session with context_id {cid}")


async def _retrying(send: Callable[[], Awaitable[Any]]) -> None:
    # The session can be visible a moment before the gate's ApprovalRequest or
    # Proposal lands; a ballot sent in that gap is refused, so try again.
    for attempt in range(50):
        try:
            await send()
            return
        except MacpAckError:
            if attempt == 49:
                raise
            await asyncio.sleep(0.1)


async def _ballots(
    client: AsyncMacpClient, mode: str, cid: str, votes: list[tuple[str, bool]]
) -> list[str]:
    first = AuthConfig.for_dev_agent(votes[0][0])
    meta = await _find_session(client, first, cid, open_only=True)
    cast = []
    for agent, approve in votes:
        auth = AuthConfig.for_dev_agent(agent)
        reason = f"{agent} reviewed the call"
        if mode == "macp.mode.quorum.v1":
            quorum = AsyncQuorumSession(client, session_id=meta.session_id, auth=auth)
            ballot = quorum.approve if approve else quorum.reject
            await _retrying(partial(ballot, cid, reason=reason, sender=agent, auth=auth))
            cast.append(f"{agent}: {'Approve' if approve else 'Reject'}")
        else:
            decision = AsyncDecisionSession(client, session_id=meta.session_id, auth=auth)
            vote = "APPROVE" if approve else "REJECT"
            await _retrying(
                partial(decision.vote, "call", vote, reason=reason, sender=agent, auth=auth)
            )
            cast.append(f"{agent}: {vote}")
    return cast


async def _session(client: AsyncMacpClient, cid: str) -> dict[str, Any]:
    meta = await _find_session(client, AuthConfig.for_dev_agent("alice"), cid)
    return {
        "session_id": meta.session_id,
        "mode": meta.mode,
        "state": envelope_pb2.SessionState.Name(meta.state).removeprefix("SESSION_STATE_"),
        "initiator": meta.initiator,
        "participants": list(meta.participants),
        "context_id": meta.context_id,
    }


async def _call(
    t: Transcript,
    gate: Client,
    sdk: AsyncMacpClient,
    tool: str,
    arguments: dict[str, Any],
    votes: list[tuple[str, bool]] | None = None,
) -> Step:
    rule = next((r for r in POLICY["rules"] if r["tool"] == tool), None)
    cid = context_id(SERVER, tool, arguments) if rule else None
    pending = asyncio.ensure_future(gate.call_tool(tool, arguments))
    cast: list[str] = []
    if rule and cid and votes:
        cast = await _ballots(sdk, str(rule["mode"]), cid, votes)
    result = await pending
    text = "".join(getattr(c, "text", "") for c in result.content)
    step = Step(tool, arguments, cid, cast, bool(result.is_error), text, result.meta)
    if cid:
        step.session = await _session(sdk, cid)
    t.steps.append(step)
    return step


async def run(target: str = "127.0.0.1:50051") -> Transcript:
    t = Transcript(target=target)
    with tempfile.TemporaryDirectory() as tmp:
        policy = Path(tmp) / "gate.json"
        policy.write_text(json.dumps(POLICY, indent=2), encoding="utf-8")
        log = Path(tmp) / "upstream.jsonl"
        sdk = AsyncMacpClient(
            target=target, allow_insecure=True, auth=AuthConfig.for_dev_agent("alice")
        )
        try:
            async with Client(_gate(target, policy, log)) as gate:
                t.tools = sorted(tool.name for tool in (await gate.list_tools()).tools)
                await _call(t, gate, sdk, "read_file", {"path": "/srv/reports/q3.md"})
                await _call(
                    t,
                    gate,
                    sdk,
                    "write_file",
                    {"path": "/srv/reports/q3.md", "content": "Q3 revenue: up 4%"},
                    [("alice", True), ("bob", True)],
                )
                await _call(
                    t,
                    gate,
                    sdk,
                    "write_file",
                    {"path": "/etc/passwd", "content": "root::0:0::/:/bin/sh"},
                    [("alice", False), ("bob", False)],
                )
                await _call(t, gate, sdk, "delete_file", {"path": "/srv/reports"})
        finally:
            await sdk.close()
        if log.exists():
            t.upstream_calls = [json.loads(line) for line in log.read_text().splitlines()]
    return t


def render_markdown(t: Transcript) -> str:
    lines = [
        "# A tool call gated by MACP",
        "",
        "Recorded output of `python examples/gated_tool_call.py`, the same driver the CI",
        "`integration` job runs (`tests/integration/test_gate.py`).",
        "",
        "An MCP client spawned `macp-mcp gate` over **stdio** in front of a stub upstream",
        "MCP server, against `ghcr.io/multiagentcoordinationprotocol/macp-runtime:0.9.0`.",
        "The gate is MACP agent `gate`; `alice`, `bob` and `carol` are approvers using the",
        "MACP SDK directly. Ids and hashes differ on every run.",
        "",
        "The policy (`gate.json`):",
        "",
        "```json",
        json.dumps(POLICY, indent=2),
        "```",
        "",
        f"Tools listed through the gate (the upstream's, verbatim): "
        f"{', '.join(f'`{n}`' for n in t.tools)}.",
        "",
    ]
    for i, s in enumerate(t.steps, 1):
        lines += [
            f"## {i}. `tools/call` `{s.tool}`",
            "",
            "```json",
            json.dumps(s.arguments),
            "```",
            "",
        ]
        if s.context_id is None:
            lines += ["No rule matches: forwarded to the upstream untouched.", ""]
        else:
            lines += [f"Gated. `context_id` = `{s.context_id}`", ""]
            if s.ballots:
                lines += ["Ballots: " + "; ".join(s.ballots) + ".", ""]
            else:
                lines += ["No ballots are cast.", ""]
        lines += ["Error:" if s.is_error else "Result:", "", "```", s.text, "```", ""]
        meta = {k: v for k, v in (s.meta or {}).items() if k != SERVER_INFO}
        if meta:
            lines += ["`_meta`:", "", "```json", json.dumps(meta, indent=2), "```", ""]
        if s.session:
            lines += [
                "The MACP session afterwards:",
                "",
                "```json",
                json.dumps(s.session, indent=2),
                "```",
                "",
            ]
    lines += [
        "## What reached the upstream",
        "",
        "```json",
        json.dumps(t.upstream_calls, indent=2),
        "```",
        "",
        "Only the passthrough read and the approved write. The rejected write and the",
        "expired delete never left the gate.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--target", default=os.environ.get("MACP_RUNTIME_TARGET", "127.0.0.1:50051")
    )
    parser.add_argument("--out", help="write the Markdown transcript here")
    args = parser.parse_args()
    md = render_markdown(asyncio.run(run(args.target)))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(md)
    else:
        print(md)


if __name__ == "__main__":
    main()
