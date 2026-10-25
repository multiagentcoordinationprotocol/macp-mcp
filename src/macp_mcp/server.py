"""MCP server exposing a MACP Decision session as tools.

Built on the official ``mcp`` Python SDK's ``MCPServer`` (the 2.x name for
FastMCP). Each tool is a thin wrapper over :class:`macp_mcp.bridge.MacpBridge`,
which does the MACP work with the SDK's ``AsyncMacpClient``.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal, TypeVar, cast

import grpc
from macp_sdk import MacpAckError, MacpSdkError, MacpTransportError, macp_error_code
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from . import __version__
from .bridge import ClientFactory, MacpBridge, default_client_factory
from .config import Settings, load_settings
from .results import (
    CommitAckResult,
    CommitmentResult,
    ProposalAckResult,
    SessionResult,
    StartSessionResult,
    VerifyResult,
    VoteAckResult,
)

T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., Awaitable[Any]])

INSTRUCTIONS = """\
These tools let you run or join a MACP Decision session (macp.mode.decision.v1):
a bounded, auditable vote among named agents that ends in one binding Commitment.
You act as the MACP agent named in the `you` / `sender` fields of tool results.

Typical loop:
1. start_session (initiator) -> share the returned session_id with the other participants.
2. propose an option; participants evaluate (optional) and vote on it.
3. get_session to see proposals, votes and the majority winner.
4. commit (the initiator) to resolve the session with the outcome.
5. get_commitment returns the binding outcome and its RFC-MACP-0013 commitment hash;
   verify recomputes that hash from the payload bytes the runtime recorded.

To join a session someone else started, you only need its session_id and to be a participant.
The runtime enforces membership, ordering and identity; a rejected action returns an error."""

SessionId = Annotated[str, Field(description="The MACP session id (from start_session).")]
ProposalId = Annotated[
    str, Field(description="Short identifier for the proposal, unique within the session.")
]

_READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)
_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)


def _coded(code: str | None, message: str) -> str:
    return f"{code}: {message}" if code else message


def _tool_error(text: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)


async def _guarded(call: Awaitable[T]) -> T:
    """Await a bridge call, turning anticipated failures into an error result.

    The MCP SDK shows the model only "Error executing tool <name>" for an
    unexpected exception, and prefixes a raised ``ToolError`` with the same
    words. A runtime rejection (wrong participant, session not open, policy
    denial) or a bad argument is something the model can act on, so it comes
    back as an ``isError`` result whose text is exactly
    ``"<CODE>: <message>"``, byte-identical to the TypeScript twin.

    Code precedence (shared with the twin): the Ack failure code, then the
    ``macp-error-code`` trailer, then the gRPC status name.

    The error result is returned in place of the tool's typed result (the SDK
    passes a ``CallToolResult`` through untouched), hence the cast.
    """
    try:
        return await call
    except (MacpSdkError, grpc.RpcError, ValueError) as exc:
        return cast(T, _tool_error(error_text(exc)))


def error_text(exc: MacpSdkError | grpc.RpcError | ValueError) -> str:
    """The ``"<CODE>: <message>"`` text for an anticipated failure (see :func:`_guarded`)."""
    if isinstance(exc, MacpAckError):
        return _coded(exc.failure.code or exc.macp_code, exc.failure.message)
    if isinstance(exc, MacpTransportError):
        return _coded(exc.macp_code or exc.code, str(exc))
    if isinstance(exc, grpc.RpcError):
        code = macp_error_code(exc) or exc.code().name  # type: ignore[attr-defined]
        return _coded(code, exc.details())  # type: ignore[attr-defined]
    return str(exc)


def build_server(
    settings: Settings | None = None,
    client_factory: ClientFactory = default_client_factory,
) -> MCPServer:
    """Build the MCP server for one MACP agent identity."""
    settings = settings or load_settings()
    bridge = MacpBridge(settings, client_factory=client_factory)
    server = MCPServer(
        name="macp-mcp",
        title="MACP Decision sessions",
        version=__version__,
        instructions=INSTRUCTIONS,
        website_url="https://multiagentcoordinationprotocol.io",
    )

    def tool(annotations: ToolAnnotations) -> Callable[[F], F]:
        # The docstring is the tool description, dedented so the wire text is
        # identical to the TypeScript twin's (contract/tools.json).
        def register(fn: F) -> F:
            return server.tool(
                description=inspect.cleandoc(fn.__doc__ or ""), annotations=annotations
            )(fn)

        return register

    @tool(_WRITE)
    async def start_session(
        intent: Annotated[str, Field(description="What the group is deciding, in one sentence.")],
        participants: Annotated[
            list[str],
            Field(
                description=(
                    "MACP agent ids allowed to act in the session. This agent is added "
                    "automatically if missing."
                )
            ),
        ],
        ttl_ms: Annotated[
            int,
            Field(
                description="Session time-to-live in milliseconds; 0 uses the server default.",
                ge=0,
            ),
        ] = 0,
        context_id: Annotated[
            str, Field(description="Optional opaque reference to external context.")
        ] = "",
    ) -> StartSessionResult:
        """Start a new MACP Decision session with this agent as initiator.

        Returns the new session_id; share it with the other participants so they can join.
        """
        return await _guarded(bridge.start_session(intent, participants, ttl_ms, context_id))

    @tool(_WRITE)
    async def propose(
        session_id: SessionId,
        proposal_id: ProposalId,
        option: Annotated[str, Field(description="The option being proposed.")],
        rationale: Annotated[str, Field(description="Why this option.")] = "",
    ) -> ProposalAckResult:
        """Put an option up for decision in a session."""
        return await _guarded(bridge.propose(session_id, proposal_id, option, rationale))

    @tool(_WRITE)
    async def evaluate(
        session_id: SessionId,
        proposal_id: ProposalId,
        recommendation: Annotated[
            Literal["APPROVE", "REVIEW", "BLOCK", "REJECT"],
            Field(description="Your assessment of the proposal."),
        ],
        confidence: Annotated[float, Field(description="Confidence from 0.0 to 1.0.", ge=0, le=1)],
        reason: Annotated[str, Field(description="Why.")] = "",
    ) -> ProposalAckResult:
        """Record a scored assessment of a proposal before voting (optional step)."""
        return await _guarded(
            bridge.evaluate(session_id, proposal_id, recommendation, confidence, reason)
        )

    @tool(_WRITE)
    async def vote(
        session_id: SessionId,
        proposal_id: ProposalId,
        vote: Annotated[Literal["APPROVE", "REJECT", "ABSTAIN"], Field(description="Your vote.")],
        reason: Annotated[str, Field(description="Why.")] = "",
    ) -> VoteAckResult:
        """Cast this agent's vote on a proposal. One vote per agent per proposal."""
        return await _guarded(bridge.vote(session_id, proposal_id, vote, reason))

    @tool(_WRITE)
    async def commit(
        session_id: SessionId,
        action: Annotated[
            str, Field(description="The decided action, e.g. 'deployment.approved'.")
        ],
        authority_scope: Annotated[
            str, Field(description="The scope of authority the decision binds, e.g. 'release'.")
        ],
        reason: Annotated[str, Field(description="Why, e.g. which proposal won the vote.")],
        outcome_positive: Annotated[
            bool, Field(description="False when the decision is a rejection.")
        ] = True,
    ) -> CommitAckResult:
        """Resolve the session with a binding Commitment (normally the initiator).

        After this the session is RESOLVED and accepts no further messages.
        """
        return await _guarded(
            bridge.commit(session_id, action, authority_scope, reason, outcome_positive)
        )

    @tool(_READ_ONLY)
    async def get_session(session_id: SessionId) -> SessionResult:
        """Show a session's state, participants, proposals, evaluations, votes and winner.

        Reads the runtime's session history, so it includes every participant's messages.
        """
        return await _guarded(bridge.get_session(session_id))

    @tool(_READ_ONLY)
    async def get_commitment(session_id: SessionId) -> CommitmentResult:
        """Return a resolved session's binding Commitment and its RFC-MACP-0013 hash.

        `found` is false while the session is still open. `payload_b64` is the exact
        Commitment payload bytes the runtime recorded; `commitment_hash` is computed over them.
        """
        return await _guarded(bridge.get_commitment(session_id))

    @tool(_READ_ONLY)
    async def verify(
        commitment_hash: Annotated[
            str, Field(description="The claimed hash, 'sha256:' followed by 64 hex digits.")
        ],
        session_id: Annotated[
            str,
            Field(description="Re-fetch the Commitment from this session's runtime history."),
        ] = "",
        payload_b64: Annotated[
            str, Field(description="Or: base64 Commitment payload bytes you already hold.")
        ] = "",
    ) -> VerifyResult:
        """Check a commitment hash by recomputing it (RFC-MACP-0013).

        Pass exactly one of session_id or payload_b64. `outcome` is `match`, `mismatch`, or
        `cannot_verify` (the payload itself is not hashable under the label).
        """
        return await _guarded(bridge.verify(commitment_hash, session_id, payload_b64))

    return server
