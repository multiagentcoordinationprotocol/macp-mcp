"""The gate matches contract/gate/, the files shared byte-for-byte with the TS twin."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from macp_mcp import config
from macp_mcp.gate import _contract, cli, engine, proxy
from macp_mcp.gate.context import UnsafeIntegerError, context_id, preimage
from macp_mcp.gate.policy import DEFAULT_RULE_TTL_MS, MODES, parse_policy

GATE = Path(__file__).resolve().parents[2] / "contract" / "gate"


def load(name: str) -> Any:
    return json.loads((GATE / name).read_text(encoding="utf-8"))


VECTORS = load("context-id-vectors.json")
SESSION = load("session.json")
CLI = load("cli.json")
SCHEMA = load("gate.schema.json")


def test_contract_files_are_exactly_these() -> None:
    assert sorted(p.name for p in GATE.iterdir()) == [
        "cli.json",
        "context-id-vectors.json",
        "example.gate.json",
        "gate.schema.json",
        "session.json",
    ]


def test_vectors_file_is_generated() -> None:
    assert (GATE / "context-id-vectors.json").read_text(encoding="utf-8") == _contract.render(
        _contract.vectors()
    )


@pytest.mark.parametrize("vector", VECTORS["vectors"], ids=lambda v: v["name"])
def test_context_id_vector(vector: dict[str, Any]) -> None:
    inp = vector["input"]
    args = (inp["server"], inp["tool"], inp.get("arguments"))
    if "expected_error" in vector:
        with pytest.raises(UnsafeIntegerError) as exc:
            preimage(*args)
        assert str(exc.value) == vector["expected_error"]
        return
    assert preimage(*args).decode("utf-8") == vector["preimage"]
    assert context_id(*args) == vector["context_id"]


def test_vectors_cover_success_and_refusal() -> None:
    kinds = ["expected_error" in v for v in VECTORS["vectors"]]
    assert kinds.count(False) >= 3
    assert True in kinds


def test_schema_is_valid_2020_12_and_accepts_the_example() -> None:
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    example = load("example.gate.json")
    jsonschema.Draft202012Validator(SCHEMA).validate(example)
    policy = parse_policy(example, "gate")
    assert {r.mode for r in policy.rules} == set(MODES)
    assert SCHEMA["$defs"]["rule"]["properties"]["mode"]["enum"] == list(MODES)
    assert SCHEMA["$defs"]["rule"]["properties"]["ttl_ms"]["default"] == DEFAULT_RULE_TTL_MS


def test_session_contract_matches_the_engine() -> None:
    assert SESSION["session_start"]["intent"] == "Approve MCP tool call {server}/{tool}"
    assert SESSION["modes"]["macp.mode.decision.v1"]["first_message"]["proposal_id"] == (
        engine.DECISION_PROPOSAL_ID
    )
    assert SESSION["commitment"]["approve"] == {
        "action": engine.ACTION_APPROVE,
        "outcome_positive": True,
    }
    assert SESSION["commitment"]["deny"] == {
        "action": engine.ACTION_DENY,
        "outcome_positive": False,
    }
    assert SESSION["commitment"]["authority_scope"] == "tools/call:{server}/{tool}"
    assert SESSION["forwarded_result"]["meta_key"] == engine.META_KEY
    assert set(SESSION["forwarded_result"]["meta_value"]) == set(
        engine.Approved("s", "m", "h").meta()
    )
    assert SESSION["timing"]["expiry_grace_ms"] == engine.EXPIRY_GRACE_MS
    assert SESSION["refusals"]["templates"] == {
        "denied": engine.DENIED,
        "policy_denied": engine.POLICY_DENIED,
        "not_open": engine.NOT_OPEN,
        "unsafe_integer": UnsafeIntegerError.TEMPLATE,
    }


def test_cli_contract_matches_the_parser() -> None:
    options = {a.option_strings[0]: a for a in cli.parser()._actions if a.option_strings}
    for flag in CLI["flags"]:
        assert options[flag["name"]].required is flag["required"]
    assert set(options) - {"-h"} == {f["name"] for f in CLI["flags"]}
    assert CLI["environment"]["withheld_from_upstream"] == list(cli.WITHHELD_ENV)
    source = Path(config.__file__).read_text(encoding="utf-8")
    for name in CLI["environment"]["read"]:
        assert f'"{name}"' in source, name
    assert CLI["server"]["name"] == "macp-mcp-gate"
    assert CLI["server"]["instructions"] == proxy.INSTRUCTIONS
