"""Entry point: ``macp-mcp`` / ``python -m macp_mcp`` (stdio transport).

``macp-mcp gate ...`` runs the tool-call gate instead (:mod:`macp_mcp.gate.cli`).
"""

from __future__ import annotations

import logging
import sys

from .config import ConfigError, load_settings


def main() -> None:
    # stdout carries the MCP protocol; every log line must go to stderr.
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    if sys.argv[1:2] == ["gate"]:
        from .gate.cli import main as gate_main

        gate_main(sys.argv[2:])
        return
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"macp-mcp: {exc}", file=sys.stderr)
        raise SystemExit(2) from None

    from .server import build_server

    print(
        f"macp-mcp: agent={settings.agent_id} runtime={settings.target} "
        f"auth={settings.auth_mode} tls={'off' if settings.allow_insecure else 'on'}",
        file=sys.stderr,
    )
    build_server(settings).run("stdio")


if __name__ == "__main__":
    main()
