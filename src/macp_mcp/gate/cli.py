"""``macp-mcp gate --policy gate.json --upstream <command> [args...]``.

Everything after ``--upstream`` is the upstream's stdio command line. The
upstream inherits the gate's environment minus ``MACP_AUTH_TOKEN`` (the
gate's own MACP credential is not the upstream's business). MACP settings
come from the same ``MACP_*`` variables as the base server.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence

import anyio
from mcp import Client, StdioServerParameters
from mcp.server.stdio import stdio_server

from ..config import ConfigError, load_settings
from .engine import GateEngine
from .policy import PolicyError, load_policy
from .proxy import build_gate_server

#: Never forwarded to the upstream process.
WITHHELD_ENV = ("MACP_AUTH_TOKEN",)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="macp-mcp gate",
        description="Proxy an upstream MCP server; gated tool calls need a MACP approval.",
    )
    p.add_argument("--policy", required=True, metavar="PATH", help="the gate.json policy file")
    p.add_argument(
        "--upstream",
        required=True,
        nargs=argparse.REMAINDER,
        metavar="COMMAND",
        help="the upstream's stdio command line (must be the last option)",
    )
    return p


def upstream_params(command: Sequence[str], env: Mapping[str, str]) -> StdioServerParameters:
    if not command:
        raise ValueError("--upstream needs a command")
    return StdioServerParameters(
        command=command[0],
        args=list(command[1:]),
        env={k: v for k, v in env.items() if k not in WITHHELD_ENV},
    )


def main(argv: Sequence[str]) -> None:
    args = parser().parse_args(argv)
    try:
        settings = load_settings()
        policy = load_policy(args.policy, settings.agent_id)
        params = upstream_params(args.upstream, os.environ)
    except (ConfigError, PolicyError, ValueError) as exc:
        print(f"macp-mcp gate: {exc}", file=sys.stderr)
        raise SystemExit(2) from None

    print(
        f"macp-mcp gate: agent={settings.agent_id} runtime={settings.target} "
        f"server={policy.server} rules={len(policy.rules)} upstream={params.command}",
        file=sys.stderr,
    )
    server = build_gate_server(policy, GateEngine(settings), lambda: Client(params))

    async def serve() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(serve)
