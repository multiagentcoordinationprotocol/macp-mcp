"""One gated tool call, end to end: open a MACP session, count, commit.

The gate is the session's initiator and the only party that commits (the
default ``initiator_only`` commitment authority). It never votes:

- **Quorum** (``macp.mode.quorum.v1``): the gate is not a participant; the
  rule's participants are the voters. It sends one ``ApprovalRequest``.
- **Decision** (``macp.mode.decision.v1``): the runtime only accepts a
  ``Proposal`` from a declared participant, so the gate is added as the first
  participant, sends one ``Proposal`` (id ``call``), and is left out of the
  count.

Approve once ``threshold`` approvers have approved; deny as soon as the
approvals plus the approvers yet to vote fall short of it. The gate then
commits that outcome, and the Commitment the runtime records is the one
reported (``contract/gate/session.json`` pins every value used here).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from macp.modes.decision.v1 import decision_pb2
from macp.modes.quorum.v1 import quorum_pb2
from macp.v1 import core_pb2, envelope_pb2
from macp_sdk import (
    DEFAULT_POLICY_VERSION,
    AsyncDecisionSession,
    AsyncMacpClient,
    AsyncQuorumSession,
    MacpAckError,
    MacpTimeoutError,
    MacpTransportError,
    commitment_hash,
)

from ..bridge import ClientFactory, default_client_factory
from ..config import Settings
from .context import context_id_of, preimage
from .policy import MODE_DECISION, MODE_QUORUM, GateRule

log = logging.getLogger("macp_mcp.gate")

META_KEY = "io.multiagentcoordinationprotocol/commitment"
DECISION_PROPOSAL_ID = "call"
ACTION_APPROVE = "macp.gate.approve"
ACTION_DENY = "macp.gate.deny"
#: How long past the session's TTL the gate waits for the runtime to end it.
EXPIRY_GRACE_MS = 5_000

#: Refusal texts (``contract/gate/session.json`` ``refusals.templates``).
DENIED = "POLICY_DENIED: tool call {tool} denied by session {session_id}"
POLICY_DENIED = "POLICY_DENIED: {runtime_message}"
NOT_OPEN = "SESSION_NOT_OPEN: session {session_id} ended {state} without a commitment"

_BALLOTS: Mapping[str, type[Any]] = {
    "Approve": quorum_pb2.ApprovePayload,
    "Reject": quorum_pb2.RejectPayload,
    "Abstain": quorum_pb2.AbstainPayload,
}


@dataclass(frozen=True)
class Approved:
    """The session committed APPROVE: forward the call, attach this as ``_meta``."""

    session_id: str
    mode: str
    commitment_hash: str

    def meta(self) -> dict[str, str]:
        return {
            "session_id": self.session_id,
            "mode": self.mode,
            "outcome": "approve",
            "commitment_hash": self.commitment_hash,
        }


@dataclass(frozen=True)
class Refused:
    """The call must not reach the upstream. ``text`` is the tool-error text."""

    text: str
    session_id: str | None = None


Verdict = Approved | Refused


def _state_name(state: int) -> str:
    return envelope_pb2.SessionState.Name(state).removeprefix("SESSION_STATE_")


class _Tally:
    """First ballot per approver, read from the session's envelopes."""

    def __init__(self, rule: GateRule, request_id: str) -> None:
        self._rule = rule
        self._request_id = request_id
        self._approvers = set(rule.participants)
        self.ballots: dict[str, bool] = {}  # approver -> approved?

    def apply(self, envelope: envelope_pb2.Envelope) -> None:
        sender = envelope.sender
        if sender not in self._approvers or sender in self.ballots:
            return
        if self._rule.mode == MODE_QUORUM:
            payload_type = _BALLOTS.get(envelope.message_type)
            if payload_type is None:
                return
            ballot = payload_type()
            ballot.ParseFromString(envelope.payload)
            if ballot.request_id == self._request_id:
                self.ballots[sender] = envelope.message_type == "Approve"
        elif envelope.message_type == "Vote":
            vote = decision_pb2.VotePayload()
            vote.ParseFromString(envelope.payload)
            if vote.proposal_id == self._request_id:
                self.ballots[sender] = vote.vote.strip().upper() == "APPROVE"

    @property
    def approvals(self) -> int:
        return sum(self.ballots.values())

    def outcome(self) -> bool | None:
        """``True`` approve, ``False`` deny, ``None`` still open."""
        if self.approvals >= self._rule.threshold:
            return True
        pending = len(self._approvers) - len(self.ballots)
        if self.approvals + pending < self._rule.threshold:
            return False
        return None


class GateEngine:
    """Runs the MACP session that decides one gated call."""

    def __init__(
        self,
        settings: Settings,
        client_factory: ClientFactory = default_client_factory,
        clock: Callable[[], float] = time.monotonic,
        poll_seconds: float = 1.0,
    ) -> None:
        self.settings = settings
        self._client_factory = client_factory
        self._client: AsyncMacpClient | None = None
        self._clock = clock
        self._poll = poll_seconds

    @property
    def client(self) -> AsyncMacpClient:
        if self._client is None:
            self._client = self._client_factory(self.settings)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def decide(
        self, rule: GateRule, server: str, tool: str, arguments: Mapping[str, Any] | None
    ) -> Verdict:
        canonical = preimage(server, tool, arguments)
        cid = context_id_of(canonical)
        policy_version = rule.policy_version or DEFAULT_POLICY_VERSION
        session: AsyncQuorumSession | AsyncDecisionSession
        if rule.mode == MODE_QUORUM:
            session = AsyncQuorumSession(self.client, policy_version=policy_version)
            participants = list(rule.participants)
            request_id = cid
        else:
            session = AsyncDecisionSession(self.client, policy_version=policy_version)
            participants = [self.settings.agent_id, *rule.participants]
            request_id = DECISION_PROPOSAL_ID
        await session.start(
            intent=f"Approve MCP tool call {server}/{tool}",
            participants=participants,
            ttl_ms=rule.ttl_ms,
            context_id=cid,
        )
        sid = session.session_id
        log.info("gate: %s/%s -> session %s (%s)", server, tool, sid, rule.mode)
        try:
            if isinstance(session, AsyncQuorumSession):
                await session.request_approval(
                    request_id,
                    f"tools/call:{tool}",
                    summary=f"{server}/{tool}",
                    details=canonical,
                    required_approvals=rule.threshold,
                )
            else:
                await session.propose(
                    request_id, f"tools/call:{tool}", rationale=canonical.decode("utf-8")
                )
            return await self._await_outcome(session, rule, server, tool, request_id)
        except BaseException:
            await self._cancel(session, "gate error")
            raise

    async def _await_outcome(
        self,
        session: AsyncQuorumSession | AsyncDecisionSession,
        rule: GateRule,
        server: str,
        tool: str,
        request_id: str,
    ) -> Verdict:
        sid = session.session_id
        tally = _Tally(rule, request_id)
        deadline = self._clock() + (rule.ttl_ms + EXPIRY_GRACE_MS) / 1000.0
        committed = False
        seen: set[str] = set()
        while True:
            async with self.client.open_stream() as stream:
                await stream.send_subscribe(sid, after_sequence=0)
                while True:
                    try:
                        envelope = await stream.read(timeout=self._poll)
                    except MacpTimeoutError:
                        envelope = None
                        idle = True
                    except MacpTransportError as exc:
                        # STREAM_LAGGED or a dropped stream: resubscribe from
                        # the start; envelopes already counted are skipped.
                        log.warning("gate: session %s stream error (%s); resubscribing", sid, exc)
                        break
                    else:
                        idle = False
                        if envelope is None:
                            break
                    if envelope is not None:
                        if envelope.session_id != sid or envelope.message_id in seen:
                            continue
                        seen.add(envelope.message_id)
                        if envelope.message_type == "Commitment":
                            return self._from_commitment(envelope, rule.mode, tool)
                        tally.apply(envelope)
                    if not committed:
                        outcome = tally.outcome()
                        if outcome is not None:
                            refusal = await self._commit(session, rule, server, tool, outcome)
                            if refusal is not None:
                                return refusal
                            committed = True
                            continue
                    if idle:
                        state, refusal = await self._check_open(session, deadline)
                        if refusal is not None:
                            return refusal
                        if state == "RESOLVED":
                            # Committed, but this stream has not delivered the
                            # Commitment: resubscribe and replay it.
                            break
            # The stream ended or failed: make sure the session is still worth
            # watching before resubscribing (a closed session would loop).
            _, refusal = await self._check_open(session, deadline)
            if refusal is not None:
                return refusal

    async def _commit(
        self,
        session: AsyncQuorumSession | AsyncDecisionSession,
        rule: GateRule,
        server: str,
        tool: str,
        approve: bool,
    ) -> Refused | None:
        try:
            await session.commit(
                action=ACTION_APPROVE if approve else ACTION_DENY,
                authority_scope=f"tools/call:{server}/{tool}",
                reason=f"threshold {rule.threshold} of {len(rule.participants)} approvers",
                outcome_positive=approve,
            )
        except MacpAckError as exc:
            if exc.failure.code != "POLICY_DENIED":
                raise
            # A bound RFC-0012 policy refused the outcome: the call is denied
            # and the session must not linger open.
            await self._cancel(session, "policy denied the gate's commitment")
            return Refused(
                POLICY_DENIED.format(runtime_message=exc.failure.message), session.session_id
            )
        return None

    async def _check_open(
        self, session: AsyncQuorumSession | AsyncDecisionSession, deadline: float
    ) -> tuple[str, Refused | None]:
        """The session's state, and the refusal if it ended without a Commitment."""
        sid = session.session_id
        state = _state_name((await self.client.get_session(sid)).metadata.state)
        if state == "OPEN" and self._clock() >= deadline:
            await self._cancel(session, "session outlived its ttl")
            state = _state_name((await self.client.get_session(sid)).metadata.state)
        if state in ("OPEN", "RESOLVED"):
            return state, None
        return state, Refused(NOT_OPEN.format(session_id=sid, state=state), sid)

    @staticmethod
    def _from_commitment(envelope: envelope_pb2.Envelope, mode: str, tool: str) -> Verdict:
        payload = core_pb2.CommitmentPayload()
        payload.ParseFromString(envelope.payload)
        sid = envelope.session_id
        if payload.outcome_positive:
            return Approved(session_id=sid, mode=mode, commitment_hash=commitment_hash(payload))
        return Refused(DENIED.format(tool=tool, session_id=sid), sid)

    @staticmethod
    async def _cancel(session: AsyncQuorumSession | AsyncDecisionSession, reason: str) -> None:
        with contextlib.suppress(Exception):
            await asyncio.shield(session.cancel(reason=reason))


__all__ = [
    "MODE_DECISION",
    "MODE_QUORUM",
    "Approved",
    "GateEngine",
    "Refused",
    "Verdict",
]
