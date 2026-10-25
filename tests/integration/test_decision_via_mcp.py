"""An MCP client drives a MACP Decision session to RESOLVED over stdio.

Runs ``examples/decision_via_mcp.py``: two real ``macp-mcp`` subprocesses
(one per MACP agent) spoken to through the official MCP client's stdio
transport, against a real runtime. Every result is checked against the shared
contract (contract/tools.json).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import jsonschema
import pytest

from .conftest import RUNTIME_TARGET

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
TOOLS = {
    t["name"]: t
    for t in json.loads((ROOT / "contract" / "tools.json").read_text(encoding="utf-8"))["tools"]
}


def _load_example() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "decision_via_mcp", ROOT / "examples" / "decision_via_mcp.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through it
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def run() -> Any:
    example = _load_example()
    transcript = asyncio.run(example.run(RUNTIME_TARGET))
    return example, transcript


def test_drives_session_to_resolved(run: Any) -> None:
    _, t = run
    calls = [(s.agent, s.tool) for s in t.steps]
    assert calls == [
        ("coordinator", "start_session"),
        ("coordinator", "propose"),
        ("alice", "get_session"),
        ("alice", "evaluate"),
        ("alice", "vote"),
        ("coordinator", "vote"),
        ("coordinator", "get_session"),
        ("alice", "commit"),
        ("coordinator", "commit"),
        ("alice", "get_commitment"),
        ("alice", "verify"),
        ("alice", "verify"),
        ("coordinator", "get_session"),
    ]
    by = dict(zip(range(len(t.steps)), t.steps, strict=True))
    assert by[0].result["session_state"] == "OPEN"
    assert by[6].result["majority_winner"] == "p1"
    assert by[6].result["approve_totals"] == {"p1": 2}
    assert by[8].result["session_state"] == "RESOLVED"
    final = by[12].result
    assert final["state"] == "RESOLVED"
    assert final["committed"] is True
    assert final["initiator"] == "coordinator"
    assert {v["sender"] for v in final["votes"]} == {"coordinator", "alice"}


def test_joining_agent_sees_initiator_messages(run: Any) -> None:
    _, t = run
    joined = t.steps[2].result
    assert joined["you"] == "alice"
    assert [p["sender"] for p in joined["proposals"]] == ["coordinator"]


def test_runtime_refuses_non_initiator_commit(run: Any) -> None:
    _, t = run
    refused = t.steps[7]
    assert refused.is_error
    # "<MACP_CODE>: <message>" with no SDK prefix (contract wire rule).
    code, _, _ = refused.result["text"].partition(": ")
    assert code.isupper() and code.replace("_", "").isalpha(), refused.result


def test_commitment_hash_verifies(run: Any) -> None:
    _, t = run
    commitment, ok, tampered = t.steps[9].result, t.steps[10].result, t.steps[11].result
    assert commitment["found"] is True
    assert commitment["sender"] == "coordinator"
    assert commitment["commitment"]["action"] == "deployment.approved"
    assert commitment["hash_label"] == "macp-commitment-hash/1"
    assert ok["outcome"] == "match"
    assert ok["source"] == "runtime_history"
    assert ok["recomputed_hash"] == commitment["commitment_hash"]
    assert tampered["outcome"] == "mismatch"


def test_results_match_contract(run: Any) -> None:
    _, t = run
    assert sorted(TOOLS) == t.tools
    for step in t.steps:
        jsonschema.Draft202012Validator(TOOLS[step.tool]["inputSchema"]).validate(step.arguments)
        if not step.is_error:
            jsonschema.Draft202012Validator(TOOLS[step.tool]["outputSchema"]).validate(step.result)


def test_negotiates_both_mcp_handshakes(run: Any) -> None:
    _, t = run
    assert t.protocol_versions["coordinator"] != t.protocol_versions["alice"]


def test_transcript_renders(run: Any) -> None:
    example, t = run
    md = example.render_markdown(t)
    assert "session **RESOLVED**" in md
    assert "(`match`)" in md and "(`mismatch`)" in md
