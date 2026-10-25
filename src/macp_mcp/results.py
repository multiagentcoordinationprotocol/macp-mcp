"""Typed tool results.

These ``TypedDict`` shapes are what each tool returns. The MCP SDK derives each
tool's ``outputSchema`` from them, and returns the dict itself as
``structuredContent``. A plain ``dict[str, Any]`` would come back wrapped as
``{"result": ...}``. ``contract/tools.json`` pins the schemas, and the
TypeScript twin tests against the same file.
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

VerifyOutcome = Literal["match", "mismatch", "cannot_verify"]
VerifySource = Literal["runtime_history", "payload_b64"]


class AckResult(TypedDict):
    ok: bool
    duplicate: bool
    message_id: str
    session_id: str
    session_state: str


class StartSessionResult(AckResult):
    mode: str
    initiator: str
    participants: list[str]
    ttl_ms: int


class ProposalAckResult(AckResult):
    proposal_id: str
    sender: str


class VoteAckResult(ProposalAckResult):
    vote: str


class CommitAckResult(AckResult):
    sender: str


class ProposalView(TypedDict):
    proposal_id: str
    option: str
    rationale: str
    sender: str


class EvaluationView(TypedDict):
    proposal_id: str
    recommendation: str
    confidence: float
    reason: str
    sender: str


class ObjectionView(TypedDict):
    proposal_id: str
    reason: str
    severity: str
    sender: str


class VoteView(TypedDict):
    proposal_id: str
    vote: str
    reason: str
    sender: str


class HistoryEntry(TypedDict):
    message_type: str
    sender: str
    message_id: str


class SessionResult(TypedDict):
    session_id: str
    mode: str
    state: str
    initiator: str
    participants: list[str]
    mode_version: str
    configuration_version: str
    policy_version: str
    started_at_unix_ms: int
    expires_at_unix_ms: int
    you: str
    phase: str
    proposals: list[ProposalView]
    evaluations: list[EvaluationView]
    objections: list[ObjectionView]
    votes: list[VoteView]
    approve_totals: dict[str, int]
    majority_winner: str | None
    blocking_objection: bool
    committed: bool
    history: list[HistoryEntry]


class Supersedes(TypedDict):
    session_id: str
    commitment_hash: str


class CommitmentView(TypedDict):
    commitment_id: str
    action: str
    authority_scope: str
    reason: str
    outcome_positive: bool
    mode_version: str
    policy_version: str
    configuration_version: str
    supersedes: NotRequired[Supersedes]


class CommitmentResult(TypedDict):
    session_id: str
    state: str
    found: bool
    sender: NotRequired[str]
    message_id: NotRequired[str]
    commitment: NotRequired[CommitmentView]
    payload_b64: NotRequired[str]
    commitment_hash: NotRequired[str]
    hash_label: NotRequired[str]


class VerifyResult(TypedDict):
    outcome: VerifyOutcome
    claimed_hash: str
    recomputed_hash: str | None
    hash_label: str
    source: VerifySource
    session_id: NotRequired[str]
