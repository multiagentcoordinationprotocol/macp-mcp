"""``macp-mcp gate`` against a real runtime: passthrough, approve, reject, expire.

Runs ``examples/gated_tool_call.py``: the gate as a stdio subprocess in front
of the stub upstream, with the approvers voting through the MACP SDK. The
upstream's own call log (``STUB_UPSTREAM_LOG``) proves which calls reached it.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from macp_mcp.gate import META_KEY

from .conftest import RUNTIME_TARGET

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
SESSION = json.loads((ROOT / "contract" / "gate" / "session.json").read_text(encoding="utf-8"))
UPSTREAM_META_KEY = "example.com/upstream"


def _load_example() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "gated_tool_call", ROOT / "examples" / "gated_tool_call.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through it
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def transcript() -> Any:
    return asyncio.run(_load_example().run(RUNTIME_TARGET))


def test_lists_upstream_tools(transcript: Any) -> None:
    assert transcript.tools == ["delete_file", "env_names", "read_file", "write_file"]


def test_ungated_call_passes_through(transcript: Any) -> None:
    step = transcript.steps[0]
    assert (step.tool, step.is_error, step.context_id) == ("read_file", False, None)
    assert step.text == "contents of /srv/reports/q3.md"


def test_approved_call_is_forwarded_with_commitment(transcript: Any) -> None:
    step = transcript.steps[1]
    assert not step.is_error
    assert step.text.startswith("wrote 17 bytes")
    commitment = step.meta[META_KEY]
    assert commitment["session_id"] == step.session["session_id"]
    assert commitment["mode"] == "macp.mode.quorum.v1"
    assert commitment["outcome"] == "approve"
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", commitment["commitment_hash"])
    # The upstream's own _meta survives next to the gate's.
    assert step.meta[UPSTREAM_META_KEY] == {"trace": "t-1"}
    assert step.session["state"] == "RESOLVED"
    assert step.session["initiator"] == "gate"
    assert step.session["context_id"] == step.context_id


def test_rejected_call_is_refused(transcript: Any) -> None:
    step = transcript.steps[2]
    assert step.is_error
    assert step.text == SESSION["refusals"]["templates"]["denied"].format(
        tool="write_file", session_id=step.session["session_id"]
    )
    assert META_KEY not in (step.meta or {})
    assert step.session["state"] == "RESOLVED"


def test_expired_call_is_refused(transcript: Any) -> None:
    step = transcript.steps[3]
    assert step.is_error
    state = step.session["state"]
    assert state in ("EXPIRED", "CANCELLED")
    assert step.text == SESSION["refusals"]["templates"]["not_open"].format(
        session_id=step.session["session_id"], state=state
    )
    assert step.session["mode"] == "macp.mode.decision.v1"


def test_refused_calls_never_reach_upstream(transcript: Any) -> None:
    assert [c["tool"] for c in transcript.upstream_calls] == ["read_file", "write_file"]
    assert transcript.upstream_calls[1]["arguments"]["path"] == "/srv/reports/q3.md"
