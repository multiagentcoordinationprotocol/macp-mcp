"""The gated call's ``context_id``: the SHA-256 of its RFC 8785 preimage.

``context_id`` binds a MACP session to one exact tool call. It is
``"sha256:"`` plus the lowercase hex SHA-256 of the RFC 8785 (JCS) canonical
JSON of::

    {"arguments": <the call's arguments, {} if absent>,
     "server": <the upstream's configured name>,
     "tool": <the tool name>}

The preimage itself travels in the session (the Quorum ``ApprovalRequest``
``details`` / the Decision ``Proposal`` ``rationale``), so an approver can
recompute the id and see exactly what it is approving.
``contract/gate/context-id-vectors.json`` pins the result; the TypeScript twin
runs the same vectors through its own canonicalizer.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from typing import Any, cast

import rfc8785

PREFIX = "sha256:"

#: Integral values beyond this magnitude may already have been rounded.
_SAFE_INTEGER = 2**53 - 1


class UnsafeIntegerError(ValueError):
    """The arguments hold an integral number outside ±(2^53-1).

    Such a number has no reliable canonical form: a JavaScript gate has
    already rounded it to a double by the time it sees it, and a Python gate
    would forward the exact value while the approvers saw the rounded one.
    Either way the approval would not bind the call that runs, so the gate
    refuses the call before opening a session.
    """

    TEMPLATE = (
        "INVALID_ARGUMENT: tool call {tool} arguments contain an integer outside "
        "\u00b1(2^53\u22121), which has no canonical JSON form"
    )

    def __init__(self, tool: str) -> None:
        super().__init__(self.TEMPLATE.format(tool=tool))


def _has_unsafe_integer(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return abs(value) > _SAFE_INTEGER
    if isinstance(value, float):
        return math.isfinite(value) and value.is_integer() and abs(value) > _SAFE_INTEGER
    if isinstance(value, Mapping):
        return any(_has_unsafe_integer(v) for v in value.values())
    if isinstance(value, list | tuple):
        return any(_has_unsafe_integer(v) for v in value)
    return False


def preimage(server: str, tool: str, arguments: Mapping[str, Any] | None) -> bytes:
    """The RFC 8785 canonical bytes ``context_id`` hashes.

    :raises UnsafeIntegerError: an integral number outside ±(2^53-1).
    :raises ValueError: anything else JSON cannot represent (a non-finite
        number, which JSON input cannot carry).
    """
    body = {"arguments": dict(arguments or {}), "server": server, "tool": tool}
    if _has_unsafe_integer(body["arguments"]):
        raise UnsafeIntegerError(tool)
    try:
        return rfc8785.dumps(cast("Any", body))
    except rfc8785.CanonicalizationError as exc:
        raise ValueError(f"arguments cannot be canonicalized (RFC 8785): {exc}") from None


def context_id_of(canonical: bytes) -> str:
    return PREFIX + hashlib.sha256(canonical).hexdigest()


def context_id(server: str, tool: str, arguments: Mapping[str, Any] | None) -> str:
    return context_id_of(preimage(server, tool, arguments))
