"""The cross-language tool contract (``contract/tools.json``).

macp-mcp has a TypeScript twin with the identical tool surface. Both packages
test against one manifest: the tool names, descriptions, annotations, input
schemas and output schemas, plus the server instructions. Schema generators
differ, so schemas are compared after :func:`normalize_schema`, which
inlines ``$ref``, drops ``title``, and writes nullable types as a type array.
What is left is the part a client actually validates against. The manifest
is fully inlined and uses plain JSON Schema keywords only. The TypeScript
twin serves it verbatim and validates with ajv.

Regenerate after an intentional surface change with ``make contract`` and
review the diff. ``make verify-contract`` (CI) fails on drift.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from mcp import Client

from .config import Settings
from .server import INSTRUCTIONS, build_server

#: Bump on any breaking change to the manifest.
CONTRACT_VERSION = "1"

#: Dialect of every schema in the manifest (stated once, not per schema).
JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

_DROPPED_KEYS = {"title", "$defs"}


def _as_nullable(schema: dict[str, Any]) -> dict[str, Any] | None:
    """``{"anyOf": [{"type": T, ...}, {"type": "null"}]}`` → ``{"type": [T, "null"], ...}``.

    pydantic spells ``str | None`` with ``anyOf``. The manifest uses the type
    array, which every validator (ajv included) handles the same way.
    """
    branches = schema.get("anyOf")
    if not isinstance(branches, list) or len(branches) != 2 or {"type": "null"} not in branches:
        return None
    other = next(b for b in branches if b != {"type": "null"})
    if not isinstance(other, dict) or not isinstance(other.get("type"), str):
        return None
    rest = {k: v for k, v in schema.items() if k != "anyOf"}
    return {**other, **rest, "type": [other["type"], "null"]}


def normalize_schema(schema: Any, defs: dict[str, Any] | None = None) -> Any:
    """Inline local ``$ref``s, drop ``title`` / ``$defs``, sort keys and ``required``."""
    if isinstance(schema, dict):
        if defs is None:
            defs = schema.get("$defs", {})
        ref = schema.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            target = dict(defs[ref.removeprefix("#/$defs/")])
            siblings = {k: v for k, v in schema.items() if k != "$ref"}
            return normalize_schema({**target, **siblings}, defs)
        nullable = _as_nullable(schema)
        if nullable is not None:
            return normalize_schema(nullable, defs)
        out: dict[str, Any] = {}
        for key, value in sorted(schema.items()):
            if key in _DROPPED_KEYS:
                continue
            if key == "properties" and isinstance(value, dict):
                # Property names are data, not keywords: a property called
                # "title" must survive.
                out[key] = {p: normalize_schema(v, defs) for p, v in sorted(value.items())}
            elif key == "required" and isinstance(value, list):
                out[key] = sorted(value)
            else:
                out[key] = normalize_schema(value, defs)
        return out
    if isinstance(schema, list):
        return [normalize_schema(v, defs) for v in schema]
    return schema


async def listed_contract() -> dict[str, Any]:
    """The contract as this server actually advertises it over MCP."""
    async with Client(build_server(Settings())) as client:
        tools = (await client.list_tools()).tools
        instructions = client.instructions
    return {
        "contract_version": CONTRACT_VERSION,
        "json_schema_dialect": JSON_SCHEMA_DIALECT,
        "server_name": "macp-mcp",
        "instructions": instructions if instructions is not None else INSTRUCTIONS,
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "annotations": tool.annotations.model_dump(by_alias=True, exclude_none=True)
                if tool.annotations
                else {},
                "inputSchema": normalize_schema(tool.input_schema),
                "outputSchema": normalize_schema(tool.output_schema),
            }
            for tool in sorted(tools, key=lambda t: t.name)
        ],
    }


def render(contract: dict[str, Any]) -> str:
    return json.dumps(contract, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    sys.stdout.write(render(asyncio.run(listed_contract())))


if __name__ == "__main__":
    main()
