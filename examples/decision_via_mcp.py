"""Drive a MACP Decision session to RESOLVED entirely through MCP tool calls.

Spawns two ``macp-mcp`` servers over stdio, one per MACP agent
(``coordinator`` and ``alice``), exactly as an MCP client such as Claude
Desktop would, and plays both sides of a Decision session through their
tools. Finishes by recomputing the RFC-MACP-0013 commitment hash.

Needs a runtime::

    docker run -p 50051:50051 -e MACP_ALLOW_INSECURE=1 -e MACP_MEMORY_ONLY=1 \\
        ghcr.io/multiagentcoordinationprotocol/macp-runtime:0.9.0

Then::

    python examples/decision_via_mcp.py                 # print the transcript
    python examples/decision_via_mcp.py --out examples/decision-via-mcp.md

The integration test (tests/integration/test_decision_via_mcp.py) runs the
same ``run()`` and asserts on every step.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any

from mcp import Client, StdioServerParameters


@dataclass
class Step:
    agent: str
    tool: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    is_error: bool = False


@dataclass
class Transcript:
    target: str
    protocol_versions: dict[str, str] = field(default_factory=dict)
    tools: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)

    def last(self, tool: str) -> dict[str, Any]:
        return next(s.result for s in reversed(self.steps) if s.tool == tool)


def _server(agent: str, target: str) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "macp_mcp"],
        env={
            "MACP_RUNTIME_TARGET": target,
            "MACP_AGENT_ID": agent,
            "MACP_ALLOW_INSECURE": "1",
            "PATH": os.environ.get("PATH", ""),
        },
    )


async def _call(
    transcript: Transcript,
    client: Client,
    agent: str,
    tool: str,
    *,
    expect_error: bool = False,
    **arguments: Any,
) -> dict[str, Any]:
    result = await client.call_tool(tool, arguments)
    if result.structured_content is not None:
        body = result.structured_content
    else:
        body = {"text": "".join(getattr(c, "text", "") for c in result.content)}
    transcript.steps.append(Step(agent, tool, arguments, body, result.is_error))
    if result.is_error != expect_error:
        raise RuntimeError(f"{agent} {tool}: expected is_error={expect_error}, got {body}")
    return body


async def run(target: str = "127.0.0.1:50051") -> Transcript:
    transcript = Transcript(target=target)
    # The coordinator uses the pre-2026 initialize handshake (what most
    # desktop MCP clients speak today); alice negotiates the default way.
    async with (
        Client(_server("coordinator", target), mode="legacy") as coordinator,
        Client(_server("alice", target)) as alice,
    ):
        transcript.protocol_versions = {
            "coordinator": coordinator.protocol_version,
            "alice": alice.protocol_version,
        }
        transcript.tools = sorted(t.name for t in (await coordinator.list_tools()).tools)

        started = await _call(
            transcript,
            coordinator,
            "coordinator",
            "start_session",
            intent="Pick the deployment plan for release 2.1",
            participants=["coordinator", "alice"],
            ttl_ms=120_000,
        )
        session_id = started["session_id"]

        await _call(
            transcript,
            coordinator,
            "coordinator",
            "propose",
            session_id=session_id,
            proposal_id="p1",
            option="Canary rollout of v2.1 to 5% of traffic",
            rationale="All tests green; canary limits blast radius",
        )
        # alice joins with nothing but the session id.
        await _call(transcript, alice, "alice", "get_session", session_id=session_id)
        await _call(
            transcript,
            alice,
            "alice",
            "evaluate",
            session_id=session_id,
            proposal_id="p1",
            recommendation="APPROVE",
            confidence=0.9,
            reason="Canary plan has a rollback path",
        )
        await _call(
            transcript,
            alice,
            "alice",
            "vote",
            session_id=session_id,
            proposal_id="p1",
            vote="APPROVE",
            reason="Ship the canary",
        )
        await _call(
            transcript,
            coordinator,
            "coordinator",
            "vote",
            session_id=session_id,
            proposal_id="p1",
            vote="APPROVE",
            reason="Agree",
        )
        tally = await _call(
            transcript, coordinator, "coordinator", "get_session", session_id=session_id
        )
        winner = tally["majority_winner"]
        # The governance point: alice's MCP client can call `commit`, but she
        # is not the initiator, so the runtime refuses to let her bind the
        # outcome. The refusal reaches the model as a tool error.
        await _call(
            transcript,
            alice,
            "alice",
            "commit",
            expect_error=True,
            session_id=session_id,
            action="deployment.approved",
            authority_scope="release-management",
            reason="alice tries to resolve the session herself",
        )
        await _call(
            transcript,
            coordinator,
            "coordinator",
            "commit",
            session_id=session_id,
            action="deployment.approved",
            authority_scope="release-management",
            reason=f"majority winner {winner}: canary rollout",
        )
        commitment = await _call(
            transcript, alice, "alice", "get_commitment", session_id=session_id
        )
        claimed = commitment["commitment_hash"]
        await _call(
            transcript,
            alice,
            "alice",
            "verify",
            commitment_hash=claimed,
            session_id=session_id,
        )
        tampered = claimed[:-1] + ("0" if claimed[-1] != "0" else "1")
        await _call(
            transcript,
            alice,
            "alice",
            "verify",
            commitment_hash=tampered,
            session_id=session_id,
        )
        await _call(transcript, coordinator, "coordinator", "get_session", session_id=session_id)
    return transcript


def render_markdown(t: Transcript) -> str:
    final = t.last("get_session")
    lines = [
        "# A MACP Decision session driven through MCP",
        "",
        "Recorded output of `python examples/decision_via_mcp.py`, the same driver the CI",
        "`integration` job runs (`tests/integration/test_decision_via_mcp.py`).",
        "",
        "Two `macp-mcp` servers were spawned over **stdio**, one per MACP agent, against",
        "`ghcr.io/multiagentcoordinationprotocol/macp-runtime:0.9.0`. Every step below is",
        "one MCP `tools/call`. The JSON shown is the tool's structured result, unedited;",
        "ids and hashes differ on every run.",
        "",
        "Step 8 is the point of the exercise. `alice`'s MCP client has a `commit` tool and",
        "calls it, but she is not the session initiator, so the MACP runtime refuses the",
        "action. The refusal reaches the model as a tool error. The MCP host decides",
        "which tools exist; MACP decides who may use them, and when.",
        "",
        "| MCP client | MACP agent | Negotiated MCP protocol |",
        "|---|---|---|",
        *(f"| {a} | `{a}` | `{v}` |" for a, v in t.protocol_versions.items()),
        "",
        f"Tools listed by the server: {', '.join(f'`{n}`' for n in t.tools)}.",
        "",
        f"Outcome: session **{final['state']}**, commitment hash verified "
        f"(`{t.steps[-3].result['outcome']}`), tampered hash rejected "
        f"(`{t.steps[-2].result['outcome']}`).",
        "",
    ]
    for i, step in enumerate(t.steps, 1):
        lines += [
            f"## {i}. `{step.agent}` calls `{step.tool}`",
            "",
            "```json",
            json.dumps(step.arguments, indent=2),
            "```",
            "",
            "Result:" if not step.is_error else "Error:",
            "",
            "```json",
            json.dumps(step.result, indent=2),
            "```",
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
