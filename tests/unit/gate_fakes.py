"""``FakeClient`` plus what the gate needs: approvers who answer, cancel, time."""

from __future__ import annotations

from typing import Any

from macp.modes.decision.v1 import decision_pb2
from macp.modes.quorum.v1 import quorum_pb2
from macp.v1 import envelope_pb2
from macp_sdk import MacpAckError, MacpTransportError
from macp_sdk.envelope import build_envelope
from macp_sdk.errors import AckFailure

from .fakes import FakeClient, FakeStream

CANCELLED = envelope_pb2.SESSION_STATE_CANCELLED
EXPIRED = envelope_pb2.SESSION_STATE_EXPIRED

_QUORUM = {
    "Approve": quorum_pb2.ApprovePayload,
    "Reject": quorum_pb2.RejectPayload,
    "Abstain": quorum_pb2.AbstainPayload,
}


def ballot(mode: str, sid: str, sender: str, choice: str, ref: str) -> envelope_pb2.Envelope:
    """A Quorum ballot (``choice`` = Approve/Reject/Abstain) or a Decision Vote."""
    if mode.endswith("quorum.v1"):
        payload = _QUORUM[choice](request_id=ref, reason="r").SerializeToString()
        message_type = choice
    else:
        payload = decision_pb2.VotePayload(proposal_id=ref, vote=choice).SerializeToString()
        message_type = "Vote"
    return build_envelope(
        mode=mode, message_type=message_type, session_id=sid, payload=payload, sender=sender
    )


class GateFakeClient(FakeClient):
    def __init__(self, ballots: list[tuple[str, str]] | None = None) -> None:
        super().__init__()
        #: (approver, choice) answered as soon as the gate's request lands.
        self.ballots = ballots or []
        self.ballot_ref: str | None = None  # override request/proposal id
        self.reject_commit: str | None = None
        self.on_request_state: int | None = None  # e.g. EXPIRED
        self.cancelled: list[tuple[str, str]] = []
        self.stream_errors = 0

    async def send(self, envelope: envelope_pb2.Envelope, **kw: Any) -> envelope_pb2.Ack:
        if envelope.message_type == "Commitment" and self.reject_commit:
            raise MacpAckError(AckFailure(code=self.reject_commit, message="policy says no"))
        ack = await super().send(envelope, **kw)
        sid = envelope.session_id
        if envelope.message_type in ("ApprovalRequest", "Proposal"):
            if envelope.message_type == "ApprovalRequest":
                req = quorum_pb2.ApprovalRequestPayload()
                req.ParseFromString(envelope.payload)
                ref = req.request_id
            else:
                prop = decision_pb2.ProposalPayload()
                prop.ParseFromString(envelope.payload)
                ref = prop.proposal_id
            for sender, choice in self.ballots:
                self.history[sid].append(
                    ballot(envelope.mode, sid, sender, choice, self.ballot_ref or ref)
                )
            if self.on_request_state is not None:
                self.metadata[sid].state = self.on_request_state  # type: ignore[assignment]
        return ack

    async def cancel_session(self, session_id: str, *, reason: str = "", **_: Any) -> Any:
        self.cancelled.append((session_id, reason))
        if self.metadata[session_id].state == envelope_pb2.SESSION_STATE_OPEN:
            self.metadata[session_id].state = CANCELLED
        return envelope_pb2.Ack(ok=True, session_id=session_id)

    def open_stream(self, **_: Any) -> FakeStream:
        return _ErroringStream(self) if self.stream_errors else FakeStream(self)


class _ErroringStream(FakeStream):
    async def read(self, timeout: float | None = None) -> envelope_pb2.Envelope | None:
        client: GateFakeClient = self._client  # type: ignore[assignment]
        client.stream_errors -= 1
        raise MacpTransportError("lagged", code="UNAVAILABLE", macp_code="STREAM_LAGGED")


def sent_payloads(client: FakeClient, message_type: str) -> list[envelope_pb2.Envelope]:
    return [e for e in client.sent if e.message_type == message_type]
