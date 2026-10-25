from __future__ import annotations

from typing import Any

import pytest

from macp_mcp import __main__ as entry


def test_bad_config_exits_2(monkeypatch, capsys) -> None:
    monkeypatch.setenv("MACP_DEFAULT_TTL_MS", "nope")
    with pytest.raises(SystemExit) as exc:
        entry.main()
    assert exc.value.code == 2
    assert "MACP_DEFAULT_TTL_MS" in capsys.readouterr().err


def test_runs_stdio_server_and_logs_to_stderr(monkeypatch, capsys) -> None:
    ran: list[str] = []

    class FakeServer:
        def run(self, transport: str) -> None:
            ran.append(transport)

    def fake_build(settings: Any) -> FakeServer:
        return FakeServer()

    monkeypatch.setenv("MACP_AGENT_ID", "alice")
    monkeypatch.setenv("MACP_ALLOW_INSECURE", "1")
    monkeypatch.setattr("macp_mcp.server.build_server", fake_build)
    entry.main()
    assert ran == ["stdio"]
    captured = capsys.readouterr()
    assert captured.out == ""  # stdout is reserved for the MCP protocol
    assert "agent=alice" in captured.err
    assert "tls=off" in captured.err
