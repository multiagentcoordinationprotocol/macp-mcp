"""The MACP side of the server: Decision-mode actions over ``AsyncMacpClient``.

The bridge is deliberately stateless. It keeps no per-session objects
between tool calls; every action is built from the session id plus what the
runtime reports (``GetSession`` metadata and the replayed, append-only
session history). That has two consequences worth knowing:

- A server that restarts mid-session loses nothing, and a server that never
  started a session can still join it: knowing the ``session_id`` is enough.
- ``get_session`` and ``get_commitment`` reflect every participant's
  messages, not just this agent's, because they read the runtime's history
  rather than a local projection.
"""

from __future__ import annotations

import base64
import binascii
import time
from collections.abc import Callable
from typing import Protocol

from macp.v1 import core_pb2, envelope_pb2
from macp_sdk import (
    AsyncDecisionSession,
    AsyncMacpClient,
    MacpTimeoutError,
    commitment_hash,
    verify_commitment_hash,
)
from macp_sdk.commitment_hash import LABEL as COMMITMENT_HASH_LABEL
from macp_sdk.projections import DecisionProjection

from .config import Settings
from .results import (
    AckResult,
    CommitAckResult,
    CommitmentResult,
    CommitmentView,
    ProposalAckResult,
    SessionResult,
    StartSessionResult,
    VerifyResult,
    VerifySource,
    VoteAckResult,
)

MODE_DECISION = "macp.mode.decision.v1"

#: Cap on a single replay, so a busy open session cannot pin a tool call.
REPLAY_MAX_SECONDS = 15.0


class _Stream(Protocol):
    async def send_subscribe(self, session_id: str, after_sequence: int = 0) -> None: ...

    async def read(self, timeout: float | None = None) -> envelope_pb2.Envelope | None: ...

    async def __aenter__(self) -> _Stream: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


ClientFactory = Callable[[Settings], AsyncMacpClient]


def default_client_factory(settings: Settings) -> AsyncMacpClient:
    """Build the SDK's asyncio client from settings (call inside the event loop)."""
    return AsyncMacpClient(
        target=settings.target,
        allow_insecure=settings.allow_insecure,
        auth=settings.auth,
        root_certificates=settings.root_certificates,
        client_name="macp-mcp",
    )


def _state_name(state: int) -> str:
    name = envelope_pb2.SessionState.Name(state)
    return name.removeprefix("SESSION_STATE_")


def _ack_dict(ack: envelope_pb2.Ack) -> AckResult:
    return {
        "ok": ack.ok,
        "duplicate": ack.duplicate,
        "message_id": ack.message_id,
        "session_id": ack.session_id,
        "session_state": _state_name(ack.session_state),
    }


def _commitment_dict(payload: core_pb2.CommitmentPayload) -> CommitmentView:
    out: CommitmentView = {
        "commitment_id": payload.commitment_id,
        "action": payload.action,
        "authority_scope": payload.authority_scope,
        "reason": payload.reason,
        "outcome_positive": payload.outcome_positive,
        "mode_version": payload.mode_version,
        "policy_version": payload.policy_version,
        "configuration_version": payload.configuration_version,
    }
    if payload.HasField("supersedes"):
        out["supersedes"] = {
            "session_id": payload.supersedes.session_id,
            "commitment_hash": payload.supersedes.commitment_hash,
        }
    return out


class MacpBridge:
    """Decision-mode actions for one MACP agent identity."""

    def __init__(
        self,
        settings: Settings,
        client_factory: ClientFactory = default_client_factory,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self._client_factory = client_factory
        self._client: AsyncMacpClient | None = None
        self._clock = clock

    @property
    def client(self) -> AsyncMacpClient:
        # Created lazily so it binds to the server's running event loop
        # (grpc.aio channels bind to the loop current at creation).
        if self._client is None:
            self._client = self._client_factory(self.settings)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    def _session(
        self, session_id: str, metadata: core_pb2.SessionMetadata | None = None
    ) -> AsyncDecisionSession:
        if metadata is None:
            return AsyncDecisionSession(self.client, session_id=session_id)
        return AsyncDecisionSession(
            self.client,
            session_id=session_id,
            mode_version=metadata.mode_version,
            configuration_version=metadata.configuration_version,
            policy_version=metadata.policy_version,
        )

    async def _metadata(self, session_id: str) -> core_pb2.SessionMetadata:
        return (await self.client.get_session(session_id)).metadata

    # -- history ----------------------------------------------------------

    async def replay(self, session_id: str) -> list[envelope_pb2.Envelope]:
        """Return the session's accepted history, oldest first.

        Opens a ``StreamSession`` and sends a subscribe frame with
        ``after_sequence=0`` (RFC-MACP-0006-A1), which replays every accepted
        envelope and then switches to live delivery. The replay is treated
        as complete once a ``Commitment`` arrives (nothing follows a terminal
        message) or no envelope arrives for ``replay_idle_ms``.
        """
        idle = self.settings.replay_idle_ms / 1000.0
        deadline = self._clock() + REPLAY_MAX_SECONDS
        history: list[envelope_pb2.Envelope] = []
        seen: set[str] = set()
        stream: _Stream = self.client.open_stream()
        async with stream:
            await stream.send_subscribe(session_id, after_sequence=0)
            while self._clock() < deadline:
                try:
                    envelope = await stream.read(timeout=idle)
                except MacpTimeoutError:
                    break
                if envelope is None:
                    break
                if envelope.session_id != session_id or envelope.message_id in seen:
                    continue
                seen.add(envelope.message_id)
                history.append(envelope)
                if envelope.message_type == "Commitment":
                    break
        return history

    @staticmethod
    def _find_commitment(history: list[envelope_pb2.Envelope]) -> envelope_pb2.Envelope | None:
        for envelope in history:
            if envelope.message_type == "Commitment":
                return envelope
        return None

    # -- tools ------------------------------------------------------------

    async def start_session(
        self,
        intent: str,
        participants: list[str],
        ttl_ms: int = 0,
        context_id: str = "",
    ) -> StartSessionResult:
        members = list(dict.fromkeys(p.strip() for p in participants if p.strip()))
        if self.settings.agent_id not in members:
            members.insert(0, self.settings.agent_id)
        session = self._session("")
        ack = await session.start(
            intent=intent,
            participants=members,
            ttl_ms=ttl_ms or self.settings.default_ttl_ms,
            context_id=context_id,
        )
        return {
            **_ack_dict(ack),
            "session_id": session.session_id,
            "mode": MODE_DECISION,
            "initiator": self.settings.agent_id,
            "participants": members,
            "ttl_ms": ttl_ms or self.settings.default_ttl_ms,
        }

    async def propose(
        self, session_id: str, proposal_id: str, option: str, rationale: str = ""
    ) -> ProposalAckResult:
        ack = await self._session(session_id).propose(proposal_id, option, rationale=rationale)
        return {**_ack_dict(ack), "proposal_id": proposal_id, "sender": self.settings.agent_id}

    async def evaluate(
        self,
        session_id: str,
        proposal_id: str,
        recommendation: str,
        confidence: float,
        reason: str = "",
    ) -> ProposalAckResult:
        ack = await self._session(session_id).evaluate(
            proposal_id, recommendation, confidence=confidence, reason=reason
        )
        return {**_ack_dict(ack), "proposal_id": proposal_id, "sender": self.settings.agent_id}

    async def vote(
        self, session_id: str, proposal_id: str, vote: str, reason: str = ""
    ) -> VoteAckResult:
        ack = await self._session(session_id).vote(proposal_id, vote, reason=reason)
        return {
            **_ack_dict(ack),
            "proposal_id": proposal_id,
            "vote": vote.upper(),
            "sender": self.settings.agent_id,
        }

    async def commit(
        self,
        session_id: str,
        action: str,
        authority_scope: str,
        reason: str,
        outcome_positive: bool = True,
    ) -> CommitAckResult:
        # Bind the commitment to the versions the session was started with,
        # so a joining committer cannot drift from the initiator's binding.
        metadata = await self._metadata(session_id)
        ack = await self._session(session_id, metadata).commit(
            action=action,
            authority_scope=authority_scope,
            reason=reason,
            outcome_positive=outcome_positive,
        )
        return {**_ack_dict(ack), "sender": self.settings.agent_id}

    async def get_session(self, session_id: str) -> SessionResult:
        metadata = await self._metadata(session_id)
        history = await self.replay(session_id)
        projection = DecisionProjection()
        for envelope in history:
            projection.apply_envelope(envelope)
        winner = projection.majority_winner()
        return {
            "session_id": metadata.session_id,
            "mode": metadata.mode,
            "state": _state_name(metadata.state),
            "initiator": metadata.initiator,
            "participants": list(metadata.participants),
            "mode_version": metadata.mode_version,
            "configuration_version": metadata.configuration_version,
            "policy_version": metadata.policy_version,
            "started_at_unix_ms": metadata.started_at_unix_ms,
            "expires_at_unix_ms": metadata.expires_at_unix_ms,
            "you": self.settings.agent_id,
            "phase": projection.phase,
            "proposals": [
                {
                    "proposal_id": p.proposal_id,
                    "option": p.option,
                    "rationale": p.rationale,
                    "sender": p.sender,
                }
                for p in projection.proposals.values()
            ],
            "evaluations": [
                {
                    "proposal_id": e.proposal_id,
                    "recommendation": e.recommendation,
                    "confidence": e.confidence,
                    "reason": e.reason,
                    "sender": e.sender,
                }
                for e in projection.evaluations
            ],
            "objections": [
                {
                    "proposal_id": o.proposal_id,
                    "reason": o.reason,
                    "severity": o.severity,
                    "sender": o.sender,
                }
                for o in projection.objections
            ],
            "votes": [
                {"proposal_id": pid, "vote": v.vote, "reason": v.reason, "sender": v.sender}
                for pid, by_sender in projection.votes.items()
                for v in by_sender.values()
            ],
            "approve_totals": projection.vote_totals(),
            "majority_winner": winner,
            "blocking_objection": projection.has_blocking_objection(winner),
            "committed": projection.is_committed,
            "history": [
                {"message_type": e.message_type, "sender": e.sender, "message_id": e.message_id}
                for e in history
            ],
        }

    async def get_commitment(self, session_id: str) -> CommitmentResult:
        metadata = await self._metadata(session_id)
        envelope = self._find_commitment(await self.replay(session_id))
        if envelope is None:
            return {
                "session_id": session_id,
                "state": _state_name(metadata.state),
                "found": False,
            }
        payload = core_pb2.CommitmentPayload()
        payload.ParseFromString(envelope.payload)
        return {
            "session_id": session_id,
            "state": _state_name(metadata.state),
            "found": True,
            "sender": envelope.sender,
            "message_id": envelope.message_id,
            "commitment": _commitment_dict(payload),
            "payload_b64": base64.b64encode(envelope.payload).decode("ascii"),
            "commitment_hash": commitment_hash(payload),
            "hash_label": COMMITMENT_HASH_LABEL,
        }

    async def verify(
        self,
        commitment_hash_claim: str,
        session_id: str = "",
        payload_b64: str = "",
    ) -> VerifyResult:
        """Recompute the RFC-MACP-0013 hash and compare it with a claim.

        Exactly one source of payload bytes is used: ``payload_b64`` (bytes
        the caller already holds) or ``session_id`` (bytes re-fetched from
        the runtime's history, independent of any earlier tool result).
        """
        if bool(session_id) == bool(payload_b64):
            raise ValueError("pass exactly one of session_id or payload_b64")
        if payload_b64:
            try:
                wire = base64.b64decode(payload_b64, validate=True)
            except (binascii.Error, ValueError):
                raise ValueError("payload_b64 is not valid base64") from None
            source: VerifySource = "payload_b64"
        else:
            envelope = self._find_commitment(await self.replay(session_id))
            if envelope is None:
                raise ValueError(f"session {session_id} has no Commitment in its history")
            wire = envelope.payload
            source = "runtime_history"
        outcome = verify_commitment_hash(wire, commitment_hash_claim)
        recomputed: str | None = None
        if outcome != "cannot_verify":
            payload = core_pb2.CommitmentPayload()
            payload.ParseFromString(wire)
            recomputed = commitment_hash(payload)
        result: VerifyResult = {
            "outcome": outcome,
            "claimed_hash": commitment_hash_claim,
            "recomputed_hash": recomputed,
            "hash_label": COMMITMENT_HASH_LABEL,
            "source": source,
        }
        if session_id:
            result["session_id"] = session_id
        return result
