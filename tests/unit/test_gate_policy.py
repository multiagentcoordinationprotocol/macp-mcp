"""gate.json: the hand validator agrees with gate.schema.json, plus loader-only rules."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from macp_mcp.gate.policy import (
    MODE_DECISION,
    MODE_QUORUM,
    GateRule,
    PolicyError,
    default_threshold,
    load_policy,
    parse_policy,
)

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "contract" / "gate" / "gate.schema.json").read_text(
        encoding="utf-8"
    )
)
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)

RULE = {"tool": "write_file", "mode": MODE_QUORUM, "participants": ["alice", "bob"]}
BASE = {"version": 1, "server": "fs", "rules": [RULE]}


def with_rule(**changes: Any) -> dict[str, Any]:
    doc = copy.deepcopy(BASE)
    for k, v in changes.items():
        if v is ...:
            doc["rules"][0].pop(k)
        else:
            doc["rules"][0][k] = v
    return doc


def with_top(**changes: Any) -> dict[str, Any]:
    doc = copy.deepcopy(BASE)
    for k, v in changes.items():
        if v is ...:
            doc.pop(k)
        else:
            doc[k] = v
    return doc


VALID = [
    BASE,
    with_top(rules=[]),
    with_rule(mode=MODE_DECISION, threshold=2, ttl_ms=1, policy_version="p", description="d"),
    with_rule(tool="*"),
]

# Rejected by both the schema and the loader.
INVALID = [
    [],
    with_top(version=2),
    with_top(version=True),
    with_top(server=""),
    with_top(server=...),
    with_top(rules=...),
    with_top(rules={}),
    with_top(extra=1),
    {**BASE, "rules": ["x"]},
    with_rule(tool=""),
    with_rule(tool=...),
    with_rule(mode="macp.mode.task.v1"),
    with_rule(mode=...),
    with_rule(participants=[]),
    with_rule(participants="alice"),
    with_rule(participants=["alice", "alice"]),
    with_rule(participants=["alice", 3]),
    with_rule(participants=...),
    with_rule(threshold=0),
    with_rule(threshold=True),
    with_rule(threshold=1.5),
    with_rule(ttl_ms=0),
    with_rule(policy_version=""),
    with_rule(description=""),
    with_rule(extra=1),
]


@pytest.mark.parametrize("doc", VALID)
def test_valid(doc: Any) -> None:
    VALIDATOR.validate(doc)
    parse_policy(doc, "gate")


@pytest.mark.parametrize("doc", INVALID)
def test_invalid(doc: Any) -> None:
    assert not VALIDATOR.is_valid(doc)
    with pytest.raises(PolicyError):
        parse_policy(doc, "gate")


@pytest.mark.parametrize(
    ("doc", "message"),
    [
        (with_rule(threshold=3), "exceeds its 2 participants"),
        (with_rule(participants=["gate", "bob"]), "gate's own agent id 'gate'"),
    ],
)
def test_loader_only_rules(doc: Any, message: str) -> None:
    VALIDATOR.validate(doc)  # the schema cannot express these
    with pytest.raises(PolicyError, match=message):
        parse_policy(doc, "gate")


def test_defaults() -> None:
    (rule,) = parse_policy(with_rule(participants=["a", "b", "c", "d"]), "gate").rules
    assert rule.threshold == default_threshold(4) == 3
    assert rule.ttl_ms == 300_000
    assert rule.policy_version is None
    assert [default_threshold(n) for n in (1, 2, 3, 5)] == [1, 2, 2, 3]


@pytest.mark.parametrize(
    ("pattern", "name", "match"),
    [
        ("write_file", "write_file", True),
        ("write_file", "write_files", False),
        ("write_*", "write_", True),
        ("write_*", "write_file", True),
        ("*_file", "read_file", True),
        ("?ead", "read", True),
        ("?ead", "rread", False),
        ("Write", "write", False),
        ("a[b]", "a[b]", True),
        ("a[b]", "ab", False),
        ("a.b", "axb", False),
        ("*", "multi\nline", True),
    ],
)
def test_glob(pattern: str, name: str, match: bool) -> None:
    rule = GateRule(tool=pattern, mode=MODE_QUORUM, participants=("a",), threshold=1)
    assert rule.matches(name) is match


def test_first_matching_rule_wins() -> None:
    doc = with_top(
        rules=[
            {**RULE, "tool": "write_*", "description": "first"},
            {**RULE, "tool": "*", "description": "second"},
        ]
    )
    policy = parse_policy(doc, "gate")
    assert policy.rule_for("write_file").description == "first"  # type: ignore[union-attr]
    assert policy.rule_for("read_file").description == "second"  # type: ignore[union-attr]
    assert parse_policy(BASE, "gate").rule_for("read_file") is None


def test_load_policy(tmp_path: Path) -> None:
    good = tmp_path / "gate.json"
    good.write_text(json.dumps(BASE), encoding="utf-8")
    assert load_policy(good, "gate").server == "fs"
    with pytest.raises(PolicyError, match="cannot read"):
        load_policy(tmp_path / "missing.json", "gate")
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(PolicyError, match="not valid JSON"):
        load_policy(bad, "gate")
