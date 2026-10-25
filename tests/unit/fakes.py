"""An in-memory stand-in for ``AsyncMacpClient``.

It records every accepted envelope per session and replays them on a
subscribe frame, which is all the bridge needs from the runtime.
"""

from __future__ import annotations

from typing import Any

from macp.v1 import core_pb2, envelope_pb2
from macp_sdk import MacpAckError, MacpTimeoutError
from macp_sdk.errors import AckFailure

OPEN = envelope_pb2.SESSION_STATE_OPEN
RESOLVED = envelope_pb2.SESSION_STATE_RESOLVED


class FakeStream:
    def __init__(self, client: FakeClient) -> None:
        self._client = client
        self._queue: list[envelope_pb2.Envelope] = []
        self.end_with_none = client.stream_ends_with_none

    async def __aenter__(self) -> FakeStream:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def send_subscribe(self, session_id: str, after_sequence: int = 0) -> None:
        self._queue = list(self._client.history.get(session_id, []))[after_sequence:]
        self._queue += self._client.extra_stream_envelopes

    async def read(self, timeout: float | None = None) -> envelope_pb2.Envelope | None:
        if self._queue:
            return self._queue.pop(0)
        if self.end_with_none:
            return None
        raise MacpTimeoutError("idle")


class FakeClient:
    def __init__(self) -> None:
        self.history: dict[str, list[envelope_pb2.Envelope]] = {}
        self.metadata: dict[str, core_pb2.SessionMetadata] = {}
        self.sent: list[envelope_pb2.Envelope] = []
        self.reject_next: str | None = None
        self.extra_stream_envelopes: list[envelope_pb2.Envelope] = []
        self.stream_ends_with_none = False
        self.closed = False
        self.auth = None

    async def send(
        self, envelope: envelope_pb2.Envelope, *, auth: Any = None, **_: Any
    ) -> envelope_pb2.Ack:
        if self.reject_next:
            code, self.reject_next = self.reject_next, None
            raise MacpAckError(AckFailure(code=code, message="rejected"))
        # Like the runtime, bind the sender to the authenticated identity.
        stamped = envelope_pb2.Envelope()
        stamped.CopyFrom(envelope)
        auth = auth or self.auth
        stamped.sender = auth.expected_sender if auth else envelope.sender
        envelope = stamped
        self.sent.append(envelope)
        sid = envelope.session_id
        if envelope.message_type == "SessionStart":
            start = core_pb2.SessionStartPayload()
            start.ParseFromString(envelope.payload)
            self.metadata[sid] = core_pb2.SessionMetadata(
                session_id=sid,
                mode=envelope.mode,
                state=OPEN,
                initiator=envelope.sender,
                participants=list(start.participants),
                mode_version=start.mode_version,
                configuration_version=start.configuration_version,
                policy_version=start.policy_version,
            )
        self.history.setdefault(sid, []).append(envelope)
        if envelope.message_type == "Commitment":
            self.metadata[sid].state = RESOLVED
        return envelope_pb2.Ack(
            ok=True,
            message_id=envelope.message_id,
            session_id=sid,
            session_state=self.metadata[sid].state,
        )

    def factory(self, settings: Any) -> FakeClient:
        """A ``ClientFactory``: adopt the server's auth, as AsyncMacpClient does."""
        self.auth = settings.auth
        return self

    async def get_session(self, session_id: str, **_: Any) -> core_pb2.GetSessionResponse:
        return core_pb2.GetSessionResponse(metadata=self.metadata[session_id])

    def open_stream(self, **_: Any) -> FakeStream:
        return FakeStream(self)

    async def close(self) -> None:
        self.closed = True
