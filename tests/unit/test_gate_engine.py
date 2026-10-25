"""GateEngine: the MACP session behind one gated call, against an in-memory runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from macp.modes.decision.v1 import decision_pb2
from macp.modes.quorum.v1 import quorum_pb2
from macp.v1 import core_pb2
from macp_sdk import MacpAckError, commitment_hash

from macp_mcp.config import Settings
from macp_mcp.gate.context import context_id, preimage
from macp_mcp.gate.engine import (
    ACTION_APPROVE,
    ACTION_DENY,
    META_KEY,
    Approved,
    GateEngine,
    Refused,
)
from macp_mcp.gate.policy import MODE_DECISION, MODE_QUORUM, GateRule

from .gate_fakes import EXPIRED, GateFakeClient, ballot, sent_payloads

ARGS = {"path": "/srv/a.md", "content": "hi"}
CID = context_id("fs", "write_file", ARGS)


def rule(mode: str = MODE_QUORUM, n: int = 3, threshold: int = 2, **kw: Any) -> GateRule:
    return GateRule(
        tool="write_file",
        mode=mode,
        participants=tuple(["alice", "bob", "carol"][:n]),
        threshold=threshold,
        **kw,
    )


def engine(fake: GateFakeClient, clock: Callable[[], float] | None = None) -> GateEngine:
    return GateEngine(
        Settings(agent_id="gate"),
        client_factory=fake.factory,  # type: ignore[arg-type]
        clock=clock or (lambda: 0.0),
        poll_seconds=0.0,
    )


def decide(fake: GateFakeClient, r: GateRule, **kw: Any) -> Approved | Refused:
    return asyncio.run(engine(fake, **kw).decide(r, "fs", "write_file", ARGS))


def committed(fake: GateFakeClient) -> core_pb2.CommitmentPayload:
    (env,) = sent_payloads(fake, "Commitment")
    payload = core_pb2.CommitmentPayload()
    payload.ParseFromString(env.payload)
    return payload


def test_quorum_approve_commits_and_reports_the_recorded_commitment() -> None:
    fake = GateFakeClient([("alice", "Approve"), ("bob", "Approve")])
    verdict = decide(fake, rule(policy_version="policy.packs.two-agent-rule"))

    (start_env,) = sent_payloads(fake, "SessionStart")
    start = core_pb2.SessionStartPayload()
    start.ParseFromString(start_env.payload)
    assert start_env.mode == MODE_QUORUM
    assert start.intent == "Approve MCP tool call fs/write_file"
    assert list(start.participants) == ["alice", "bob", "carol"]
    assert start.context_id == CID
    assert start.policy_version == "policy.packs.two-agent-rule"
    assert start.ttl_ms == 300_000

    (req_env,) = sent_payloads(fake, "ApprovalRequest")
    req = quorum_pb2.ApprovalRequestPayload()
    req.ParseFromString(req_env.payload)
    assert req.request_id == CID
    assert req.action == "tools/call:write_file"
    assert req.summary == "fs/write_file"
    assert req.details == preimage("fs", "write_file", ARGS)
    assert req.required_approvals == 2

    payload = committed(fake)
    assert payload.action == ACTION_APPROVE
    assert payload.authority_scope == "tools/call:fs/write_file"
    assert payload.outcome_positive is True

    assert isinstance(verdict, Approved)
    assert verdict.session_id == start_env.session_id
    assert verdict.commitment_hash == commitment_hash(payload)
    assert verdict.meta() == {
        "session_id": start_env.session_id,
        "mode": MODE_QUORUM,
        "outcome": "approve",
        "commitment_hash": commitment_hash(payload),
    }
    assert META_KEY == "io.multiagentcoordinationprotocol/commitment"
    assert fake.cancelled == []


def test_quorum_deny_once_threshold_is_unreachable() -> None:
    fake = GateFakeClient([("alice", "Reject"), ("bob", "Abstain")])
    verdict = decide(fake, rule())
    assert committed(fake).action == ACTION_DENY
    assert committed(fake).outcome_positive is False
    assert isinstance(verdict, Refused)
    assert (
        verdict.text
        == f"POLICY_DENIED: tool call write_file denied by session {verdict.session_id}"
    )


def test_only_first_ballot_of_each_approver_counts() -> None:
    # alice approves twice, mallory is no approver: 1 of 2 needed, then carol rejects.
    fake = GateFakeClient(
        [
            ("alice", "Approve"),
            ("alice", "Approve"),
            ("mallory", "Approve"),
            ("bob", "Reject"),
            ("carol", "Reject"),
        ]
    )
    verdict = decide(fake, rule())
    assert isinstance(verdict, Refused)
    assert committed(fake).outcome_positive is False


def test_ballots_for_another_request_are_ignored() -> None:
    fake = GateFakeClient([("alice", "Approve"), ("bob", "Approve")])
    fake.ballot_ref = "other"
    clock = iter([0.0] + [1e9] * 100)
    verdict = decide(fake, rule(), clock=lambda: next(clock))
    assert isinstance(verdict, Refused)
    assert verdict.text.startswith("SESSION_NOT_OPEN: ")
    assert sent_payloads(fake, "Commitment") == []


def test_decision_gate_proposes_as_participant_and_is_not_counted() -> None:
    fake = GateFakeClient([("alice", "approve"), ("bob", "APPROVE")])
    verdict = decide(fake, rule(MODE_DECISION, n=2, threshold=2))

    (start_env,) = sent_payloads(fake, "SessionStart")
    start = core_pb2.SessionStartPayload()
    start.ParseFromString(start_env.payload)
    assert start_env.mode == MODE_DECISION
    assert list(start.participants) == ["gate", "alice", "bob"]

    (prop_env,) = sent_payloads(fake, "Proposal")
    prop = decision_pb2.ProposalPayload()
    prop.ParseFromString(prop_env.payload)
    assert prop.proposal_id == "call"
    assert prop.option == "tools/call:write_file"
    assert prop.rationale == preimage("fs", "write_file", ARGS).decode()

    assert isinstance(verdict, Approved)
    assert verdict.mode == MODE_DECISION


def test_decision_abstain_is_not_approval() -> None:
    fake = GateFakeClient([("alice", "APPROVE"), ("bob", "ABSTAIN")])
    verdict = decide(fake, rule(MODE_DECISION, n=2, threshold=2))
    assert isinstance(verdict, Refused)
    assert committed(fake).action == ACTION_DENY


def test_open_past_ttl_plus_grace_is_cancelled_and_refused() -> None:
    fake = GateFakeClient([("alice", "Approve")])
    now = [0.0]

    def clock() -> float:
        now[0] += 1.0
        return now[0]

    r = rule(ttl_ms=3_000)  # deadline = start + 8 s
    verdict = decide(fake, r, clock=clock)
    assert isinstance(verdict, Refused)
    sid = verdict.session_id
    assert verdict.text == f"SESSION_NOT_OPEN: session {sid} ended CANCELLED without a commitment"
    assert fake.cancelled == [(sid, "session outlived its ttl")]
    assert now[0] >= 8.0


def test_runtime_expiry_is_refused_without_cancel() -> None:
    fake = GateFakeClient()
    fake.on_request_state = EXPIRED
    verdict = decide(fake, rule())
    assert isinstance(verdict, Refused)
    assert verdict.text == (
        f"SESSION_NOT_OPEN: session {verdict.session_id} ended EXPIRED without a commitment"
    )
    assert fake.cancelled == []


def test_policy_denied_commit_cancels_and_refuses() -> None:
    fake = GateFakeClient([("alice", "Approve"), ("bob", "Approve")])
    fake.reject_commit = "POLICY_DENIED"
    verdict = decide(fake, rule())
    assert isinstance(verdict, Refused)
    assert verdict.text == "POLICY_DENIED: policy says no"
    assert fake.cancelled == [(verdict.session_id, "policy denied the gate's commitment")]


def test_other_commit_failure_propagates_and_cancels() -> None:
    fake = GateFakeClient([("alice", "Approve"), ("bob", "Approve")])
    fake.reject_commit = "FORBIDDEN"
    with pytest.raises(MacpAckError):
        decide(fake, rule())
    assert [r for _, r in fake.cancelled] == ["gate error"]


def test_stream_error_resubscribes() -> None:
    fake = GateFakeClient([("alice", "Approve"), ("bob", "Approve")])
    fake.stream_errors = 2
    assert isinstance(decide(fake, rule()), Approved)


def test_stream_end_resubscribes_until_commitment_arrives() -> None:
    fake = GateFakeClient([("alice", "Approve"), ("bob", "Approve")])
    fake.stream_ends_with_none = True
    verdict = decide(fake, rule())
    assert isinstance(verdict, Approved)
    assert len(sent_payloads(fake, "Commitment")) == 1


def test_commitment_already_on_stream_is_reported() -> None:
    # A Commitment some other committer recorded (e.g. after a gate restart).
    fake = GateFakeClient()
    payload = core_pb2.CommitmentPayload(action="x", outcome_positive=True)
    fake.extra_stream_envelopes = []

    async def go() -> Approved | Refused:
        eng = engine(fake)
        original = fake.send

        async def send(envelope: Any, **kw: Any) -> Any:
            ack = await original(envelope, **kw)
            if envelope.message_type == "ApprovalRequest":
                env = ballot(MODE_QUORUM, envelope.session_id, "alice", "Approve", CID)
                env.message_type = "Commitment"
                env.payload = payload.SerializeToString()
                fake.history[envelope.session_id].append(env)
            return ack

        fake.send = send  # type: ignore[method-assign]
        return await eng.decide(rule(), "fs", "write_file", ARGS)

    verdict = asyncio.run(go())
    assert isinstance(verdict, Approved)
    assert verdict.commitment_hash == commitment_hash(payload)
    assert sent_payloads(fake, "Commitment") == []


def test_aclose_closes_the_client_once() -> None:
    fake = GateFakeClient()
    eng = engine(fake)

    async def go() -> None:
        await eng.aclose()  # never opened: no-op
        assert eng.client is fake
        await eng.aclose()

    asyncio.run(go())
    assert fake.closed
