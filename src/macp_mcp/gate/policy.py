"""``gate.json``: which upstream tool calls need a MACP session, and how.

``contract/gate/gate.schema.json`` is the normative shape, shared byte-for-byte
with the TypeScript twin. :func:`parse_policy` enforces the same rules by
hand (the server does not ship a JSON Schema validator), plus the checks a
schema cannot express: ``threshold`` against the participant count, and the
gate's own identity never being an approver. The unit tests run every sample
through both, so the two cannot drift.
"""

from __future__ import annotations

import fnmatch
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MODE_QUORUM = "macp.mode.quorum.v1"
MODE_DECISION = "macp.mode.decision.v1"
MODES = (MODE_QUORUM, MODE_DECISION)

POLICY_VERSION = 1
DEFAULT_RULE_TTL_MS = 300_000

_RULE_KEYS = {
    "tool",
    "mode",
    "participants",
    "threshold",
    "ttl_ms",
    "policy_version",
    "description",
}
_TOP_KEYS = {"version", "server", "rules"}


class PolicyError(ValueError):
    """``gate.json`` is unreadable or breaks the gate contract."""


@dataclass(frozen=True)
class GateRule:
    """One rule. ``threshold`` is the number of APPROVE ballots/votes needed."""

    tool: str
    mode: str
    participants: tuple[str, ...]
    threshold: int
    ttl_ms: int = DEFAULT_RULE_TTL_MS
    policy_version: str | None = None
    description: str | None = None

    def matches(self, tool_name: str) -> bool:
        return _glob(self.tool).fullmatch(tool_name) is not None


@dataclass(frozen=True)
class GatePolicy:
    server: str
    rules: tuple[GateRule, ...]

    def rule_for(self, tool_name: str) -> GateRule | None:
        """The first matching rule, or ``None``: the call passes straight through."""
        return next((r for r in self.rules if r.matches(tool_name)), None)


def _glob(pattern: str) -> re.Pattern[str]:
    # Only "*" and "?" are wildcards; fnmatch's "[...]" classes are escaped
    # so a literal bracket in a tool name matches itself.
    escaped = pattern.replace("[", "[[]")
    return re.compile(fnmatch.translate(escaped), re.DOTALL)


def default_threshold(approvers: int) -> int:
    """A strict majority of the approvers."""
    return approvers // 2 + 1


def _string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise PolicyError(f"{where} must be a non-empty string")
    return value


def _positive_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PolicyError(f"{where} must be an integer >= 1")
    return value


def _rule(raw: Any, index: int, agent_id: str) -> GateRule:
    where = f"rules[{index}]"
    if not isinstance(raw, Mapping):
        raise PolicyError(f"{where} must be an object")
    unknown = sorted(set(raw) - _RULE_KEYS)
    if unknown:
        raise PolicyError(f"{where} has unknown keys: {', '.join(unknown)}")
    for key in ("tool", "mode", "participants"):
        if key not in raw:
            raise PolicyError(f"{where}.{key} is required")
    tool = _string(raw["tool"], f"{where}.tool")
    mode = raw["mode"]
    if mode not in MODES:
        raise PolicyError(f"{where}.mode must be one of {', '.join(MODES)}")
    participants = raw["participants"]
    if not isinstance(participants, list) or not participants:
        raise PolicyError(f"{where}.participants must be a non-empty array")
    approvers = tuple(_string(p, f"{where}.participants[]") for p in participants)
    if len(set(approvers)) != len(approvers):
        raise PolicyError(f"{where}.participants must not repeat an id")
    if agent_id in approvers:
        raise PolicyError(
            f"{where}.participants must not include the gate's own agent id {agent_id!r}"
        )
    if "threshold" in raw:
        threshold = _positive_int(raw["threshold"], f"{where}.threshold")
        if threshold > len(approvers):
            raise PolicyError(
                f"{where}.threshold {threshold} exceeds its {len(approvers)} participants"
            )
    else:
        threshold = default_threshold(len(approvers))
    ttl_ms = (
        _positive_int(raw["ttl_ms"], f"{where}.ttl_ms") if "ttl_ms" in raw else DEFAULT_RULE_TTL_MS
    )
    policy_version = (
        _string(raw["policy_version"], f"{where}.policy_version")
        if "policy_version" in raw
        else None
    )
    description = (
        _string(raw["description"], f"{where}.description") if "description" in raw else None
    )
    return GateRule(
        tool=tool,
        mode=mode,
        participants=approvers,
        threshold=threshold,
        ttl_ms=ttl_ms,
        policy_version=policy_version,
        description=description,
    )


def parse_policy(raw: Any, agent_id: str) -> GatePolicy:
    """Validate a decoded ``gate.json`` for the gate acting as ``agent_id``."""
    if not isinstance(raw, Mapping):
        raise PolicyError("gate policy must be a JSON object")
    unknown = sorted(set(raw) - _TOP_KEYS)
    if unknown:
        raise PolicyError(f"gate policy has unknown keys: {', '.join(unknown)}")
    for key in ("version", "server", "rules"):
        if key not in raw:
            raise PolicyError(f"{key} is required")
    if isinstance(raw["version"], bool) or raw["version"] != POLICY_VERSION:
        raise PolicyError(f"version must be {POLICY_VERSION}")
    server = _string(raw["server"], "server")
    rules = raw["rules"]
    if not isinstance(rules, list):
        raise PolicyError("rules must be an array")
    return GatePolicy(
        server=server, rules=tuple(_rule(r, i, agent_id) for i, r in enumerate(rules))
    )


def load_policy(path: str | Path, agent_id: str) -> GatePolicy:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise PolicyError(f"cannot read gate policy {str(path)!r}: {exc.strerror}") from None
    except json.JSONDecodeError as exc:
        raise PolicyError(f"gate policy {str(path)!r} is not valid JSON: {exc}") from None
    return parse_policy(raw, agent_id)
