"""Generates ``contract/gate/context-id-vectors.json``.

The inputs are chosen to hit each RFC 8785 trap (number formatting, unsafe
integers, string escapes, UTF-16 key order); the outputs are computed by
:mod:`macp_mcp.gate.context`. The TypeScript twin checks the same file
against its own canonicalizer, so a disagreement surfaces in one of the two
CI runs. Regenerate with ``make contract``; ``make verify-contract`` fails on
drift.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from .context import UnsafeIntegerError, context_id_of, preimage

CONTRACT_VERSION = "1"

DESCRIPTION = (
    'macp-mcp gate context_id vectors. context_id = "sha256:" + lowercase hex SHA-256 of the '
    "RFC 8785 (JCS) canonical JSON (UTF-8) of "
    '{"arguments": input.arguments or {}, "server": input.server, "tool": input.tool}. '
    "preimage is that canonical JSON. Numbers are IEEE-754 doubles, as JSON.parse reads them. "
    "A vector with expected_error instead of preimage and context_id is refused with exactly "
    "that text, before any session is opened."
)

#: (name, note, input): each has a context_id.
INPUTS: list[tuple[str, str, dict[str, Any]]] = [
    (
        "arguments-absent",
        "Absent arguments hash as {}.",
        {"server": "filesystem", "tool": "list_allowed_directories"},
    ),
    (
        "simple",
        "Flat string arguments; keys sorted.",
        {
            "server": "filesystem",
            "tool": "write_file",
            "arguments": {"path": "/srv/reports/q3.md", "content": "Q3 revenue: up 4%"},
        },
    ),
    (
        "nested",
        "Nested objects and arrays, booleans and null; array order is kept.",
        {
            "server": "github",
            "tool": "create_issue",
            "arguments": {
                "repo": "acme/api",
                "labels": ["bug", "p1"],
                "assignee": None,
                "draft": False,
                "body": {"steps": [3, 1, 2], "env": {"os": "linux", "arch": "arm64"}},
            },
        },
    ),
    (
        "numbers",
        "ECMAScript number formatting: 1.0 -> 1, -0.0 -> 0, small exponents, shortest "
        "round-trip, the largest safe integer.",
        {
            "server": "payments",
            "tool": "transfer",
            "arguments": {
                "amount": 1250.5,
                "fee": 0.1,
                "whole": 1.0,
                "negzero": -0.0,
                "small": 1.5e-7,
                "tiny": 1e-7,
                "edge": 1e-6,
                "thirds": 333333333.33333329,
                "max_safe": 9007199254740991,
                "neg": -42,
            },
        },
    ),
    (
        "strings-escapes",
        "Escapes: quote, backslash, \\b \\f \\n \\r \\t, other controls as \\u00XX; "
        "DEL, U+2028, U+2029, '/' and non-ASCII stay literal.",
        {
            "server": "mail",
            "tool": "send",
            "arguments": {
                "subject": 'Re: "Q3" \\ final',
                "body": "line1\nline2\ttab\u0007bell\u0001soh\b\f\r\u007fdel\u2028ls\u2029ps",
                "url": "https://example.com/a/b",
                "to": "zoë@example.com",
                "greeting": "שלום \U0001f600",
            },
        },
    ),
    (
        "key-order-utf16",
        "Keys sort by UTF-16 code units, not code points: "
        "U+20AC < U+1F600 (D83D) < U+FB01 < U+FB33.",
        {
            "server": "kv",
            "tool": "put",
            "arguments": {
                "דּ": 4,
                "ﬁ": 3,
                "\U0001f600": 2,
                "€": 1,
                "ö": 0,
                "\u0080": 0,
                "b": 0,
                "B": 0,
                "_": 0,
                "1": 0,
                "\r": 0,
                "": 0,
            },
        },
    ),
]

#: (name, note, input): each is refused before a session opens.
ERROR_INPUTS: list[tuple[str, str, dict[str, Any]]] = [
    (
        "unsafe-integer",
        "An integer outside ±(2^53\u22121) has no canonical form: JSON.parse may already "
        "have rounded it. The call is refused with expected_error; no session is opened.",
        {"server": "ledger", "tool": "post_entry", "arguments": {"entry_id": 9007199254740993}},
    ),
    (
        "unsafe-integer-exponent",
        "The same holds for an integral value written with an exponent (1e21), "
        "here nested in an array.",
        {"server": "ledger", "tool": "post_entry", "arguments": {"ids": [1, 1e21]}},
    ),
]


def vectors() -> dict[str, Any]:
    out: list[dict[str, Any]] = []
    for name, note, inp in INPUTS:
        canonical = preimage(inp["server"], inp["tool"], inp.get("arguments"))
        out.append(
            {
                "name": name,
                "note": note,
                "input": inp,
                "preimage": canonical.decode("utf-8"),
                "context_id": context_id_of(canonical),
            }
        )
    for name, note, inp in ERROR_INPUTS:
        try:
            preimage(inp["server"], inp["tool"], inp.get("arguments"))
        except UnsafeIntegerError as exc:
            out.append({"name": name, "note": note, "input": inp, "expected_error": str(exc)})
        else:  # pragma: no cover - the generator itself would be wrong
            raise AssertionError(f"{name} canonicalized")
    return {"contract_version": CONTRACT_VERSION, "description": DESCRIPTION, "vectors": out}


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    sys.stdout.write(render(vectors()))


if __name__ == "__main__":
    main()
