"""Gate rules bound to an RFC-MACP-0012 policy stricter than the rule itself.

Each rule names, through ``policy_version``, a policy registered on the
runtime that needs two approvals, while the rule's own ``threshold`` is one.
One approval satisfies the gate's tally, so the gate commits ``approve`` and
the runtime refuses it. Whatever the refusal, the call must be refused, the
gate must cancel the session (so it does not linger OPEN until its TTL), and
the upstream must never be called.

- **Decision**: the policy's vote quorum denies the Commitment with
  ``POLICY_DENIED``, which the gate renders with the ``policy_denied``
  template (contract/gate/session.json).
- **Quorum**: the runtime folds a policy ``threshold`` into the mode's own
  commitment-readiness check, so the Commitment is refused as
  ``INVALID_ENVELOPE`` before policy evaluation; the gate surfaces that code
  as-is (the contract's ``other`` case).

These are the only paths where the gate's own ``CancelSession`` decides the
session's terminal state, so they also pin that cancellation works against
the runtime (0.14.0 forbids SessionCancel submitted through Send; the SDK uses
the CancelSession RPC).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import tempfile
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from macp.v1 import policy_pb2
from macp_sdk import AsyncMacpClient, AuthConfig
from mcp import Client

from macp_mcp.gate import context_id

from .conftest import RUNTIME_TARGET

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
SESSION = json.loads((ROOT / "contract" / "gate" / "session.json").read_text(encoding="utf-8"))
MODE_DECISION = "macp.mode.decision.v1"
MODE_QUORUM = "macp.mode.quorum.v1"

#: Runtime policy rules needing two approvals, per mode.
STRICT_RULES = {
    MODE_DECISION: {
        "voting": {"algorithm": "majority", "quorum": {"type": "count", "value": 2}},
        "commitment": {"require_vote_quorum": True},
    },
    MODE_QUORUM: {"threshold": {"type": "n_of_m", "value": 2}},
}


def _load_example() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "gated_tool_call", ROOT / "examples" / "gated_tool_call.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through it
    spec.loader.exec_module(module)
    return module


async def _run(target: str, mode: str) -> dict[str, Any]:
    example = _load_example()
    # Unique per run: a long-lived runtime keeps policies, and the context_id
    # (hence the session lookup) depends on the arguments.
    run = uuid.uuid4().hex[:12]
    policy_id = f"policy.macp-mcp.it.gate-strict.{mode.split('.')[2]}.{run}"
    arguments = {"path": f"/srv/reports/{run}.md", "content": "draft"}
    gate_policy = {
        "version": 1,
        "server": example.SERVER,
        "rules": [
            {
                "tool": "write_file",
                "mode": mode,
                "participants": ["alice", "bob"],
                "threshold": 1,
                "ttl_ms": 60_000,
                "policy_version": policy_id,
            }
        ],
    }
    sdk = AsyncMacpClient(
        target=target, allow_insecure=True, auth=AuthConfig.for_dev_agent("alice")
    )
    with tempfile.TemporaryDirectory() as tmp:
        policy_path = Path(tmp) / "gate.json"
        policy_path.write_text(json.dumps(gate_policy), encoding="utf-8")
        log = Path(tmp) / "upstream.jsonl"
        try:
            registered = await sdk.register_policy(
                policy_pb2.PolicyDescriptor(
                    policy_id=policy_id,
                    mode=mode,
                    description="Two approvals, stricter than the gate rule's one.",
                    rules=json.dumps(STRICT_RULES[mode]),
                    schema_version=1,
                )
            )
            assert registered.ok, registered.error
            cid = context_id(example.SERVER, "write_file", arguments)
            async with Client(example._gate(target, policy_path, log)) as gate:
                pending = asyncio.ensure_future(gate.call_tool("write_file", arguments))
                await example._ballots(sdk, mode, cid, [("alice", True)])
                result = await pending
            session = await example._session(sdk, cid)
        finally:
            await sdk.close()
        calls = log.read_text().splitlines() if log.exists() else []
    return {
        "is_error": bool(result.is_error),
        "text": "".join(getattr(c, "text", "") for c in result.content),
        "session": session,
        "upstream_calls": calls,
    }


@pytest.fixture(scope="module", params=[MODE_DECISION, MODE_QUORUM], ids=["decision", "quorum"])
def outcome(request: pytest.FixtureRequest) -> tuple[str, dict[str, Any]]:
    mode = request.param
    return mode, asyncio.run(_run(RUNTIME_TARGET, mode))


def test_refused_with_the_runtime_code(outcome: tuple[str, dict[str, Any]]) -> None:
    mode, o = outcome
    assert o["is_error"]
    if mode == MODE_DECISION:
        prefix = SESSION["refusals"]["templates"]["policy_denied"].split("{")[0]
        assert o["text"].startswith(prefix), o["text"]
        # The runtime's denial, not the gate's own "denied by session" refusal.
        assert "denied by session" not in o["text"]
    else:
        assert o["text"].startswith("INVALID_ENVELOPE: "), o["text"]


def test_gate_cancels_the_refused_session(outcome: tuple[str, dict[str, Any]]) -> None:
    mode, o = outcome
    assert o["session"]["mode"] == mode
    assert o["session"]["initiator"] == "gate"
    assert o["session"]["state"] == "CANCELLED"


def test_refused_call_never_reaches_upstream(outcome: tuple[str, dict[str, Any]]) -> None:
    _, o = outcome
    assert o["upstream_calls"] == []
