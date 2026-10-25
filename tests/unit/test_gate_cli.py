"""``macp-mcp gate``: argument parsing, upstream env, configuration errors."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from macp_mcp import __main__ as entry
from macp_mcp.gate import cli

POLICY = {
    "version": 1,
    "server": "fs",
    "rules": [{"tool": "write_file", "mode": "macp.mode.quorum.v1", "participants": ["bob"]}],
}


@pytest.fixture
def policy_file(tmp_path: Path) -> Path:
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(POLICY), encoding="utf-8")
    return path


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MACP_AGENT_ID", "gate")
    monkeypatch.setenv("MACP_ALLOW_INSECURE", "1")


def test_upstream_takes_every_remaining_argument() -> None:
    args = cli.parser().parse_args(
        ["--policy", "g.json", "--upstream", "npx", "-y", "server", "--policy", "x"]
    )
    assert args.policy == "g.json"
    assert args.upstream == ["npx", "-y", "server", "--policy", "x"]


def test_upstream_env_withholds_the_auth_token() -> None:
    params = cli.upstream_params(
        ["srv", "--flag"], {"MACP_AUTH_TOKEN": "secret", "MACP_AGENT_ID": "gate", "PATH": "/bin"}
    )
    assert params.command == "srv"
    assert params.args == ["--flag"]
    assert params.env == {"MACP_AGENT_ID": "gate", "PATH": "/bin"}


@pytest.mark.usefixtures("env")
def test_empty_upstream_exits_2(policy_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--policy", str(policy_file), "--upstream"])
    assert exc.value.code == 2
    assert "--upstream needs a command" in capsys.readouterr().err


@pytest.mark.usefixtures("env")
def test_bad_policy_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "gate.json"
    bad.write_text(json.dumps({**POLICY, "version": 9}), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cli.main(["--policy", str(bad), "--upstream", "srv"])
    assert exc.value.code == 2
    assert "version must be 1" in capsys.readouterr().err


def test_gate_as_approver_exits_2(
    policy_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MACP_AGENT_ID", "bob")
    monkeypatch.setenv("MACP_ALLOW_INSECURE", "1")
    with pytest.raises(SystemExit) as exc:
        cli.main(["--policy", str(policy_file), "--upstream", "srv"])
    assert exc.value.code == 2
    assert "gate's own agent id 'bob'" in capsys.readouterr().err


@pytest.mark.usefixtures("env")
def test_dispatch_from_main_serves_stdio(
    policy_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    served: list[Any] = []
    monkeypatch.setattr(cli.anyio, "run", lambda fn: served.append(fn))
    monkeypatch.setattr(
        sys, "argv", ["macp-mcp", "gate", "--policy", str(policy_file), "--upstream", "srv"]
    )
    entry.main()
    assert len(served) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "server=fs rules=1 upstream=srv" in captured.err
