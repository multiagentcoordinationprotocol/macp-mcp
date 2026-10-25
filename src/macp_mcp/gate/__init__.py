"""``macp-mcp gate``: quorum-gated tool calls for an upstream MCP server."""

from .context import context_id, preimage
from .engine import META_KEY, Approved, GateEngine, Refused
from .policy import GatePolicy, GateRule, PolicyError, load_policy, parse_policy
from .proxy import build_gate_server

__all__ = [
    "META_KEY",
    "Approved",
    "GateEngine",
    "GatePolicy",
    "GateRule",
    "PolicyError",
    "Refused",
    "build_gate_server",
    "context_id",
    "load_policy",
    "parse_policy",
    "preimage",
]
