"""The server matches contract/tools.json, the manifest shared with the TS twin."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from mcp import Client

from macp_mcp import _contract
from macp_mcp._contract import listed_contract, normalize_schema, render
from macp_mcp.config import Settings
from macp_mcp.server import build_server

from .fakes import FakeClient

MANIFEST_PATH = Path(__file__).resolve().parents[2] / "contract" / "tools.json"
MANIFEST: dict[str, Any] = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
TOOLS = {t["name"]: t for t in MANIFEST["tools"]}


def test_listed_tools_match_manifest() -> None:
    listed = asyncio.run(listed_contract())
    # Compare per tool first for a readable failure, then the whole document.
    for tool in listed["tools"]:
        assert tool == TOOLS[tool["name"]], f"{tool['name']} drifted from contract/tools.json"
    assert listed == MANIFEST


def test_manifest_file_is_canonically_rendered() -> None:
    assert MANIFEST_PATH.read_text(encoding="utf-8") == render(MANIFEST)


def test_manifest_header() -> None:
    assert MANIFEST["contract_version"] == _contract.CONTRACT_VERSION
    assert MANIFEST["server_name"] == "macp-mcp"
    assert MANIFEST["json_schema_dialect"].endswith("/draft/2020-12/schema")


def test_manifest_is_inlined_plain_json_schema() -> None:
    text = MANIFEST_PATH.read_text(encoding="utf-8")
    for keyword in ('"$ref"', '"$defs"', '"anyOf"', '"title"', '"$schema"'):
        assert keyword not in text
    for tool in MANIFEST["tools"]:
        jsonschema.Draft202012Validator.check_schema(tool["inputSchema"])
        jsonschema.Draft202012Validator.check_schema(tool["outputSchema"])


def _check_output(tool: str, body: dict[str, Any]) -> None:
    jsonschema.Draft202012Validator(TOOLS[tool]["outputSchema"]).validate(body)


def test_every_tool_result_validates_against_manifest() -> None:
    fake = FakeClient()
    server = build_server(Settings(agent_id="coordinator"), client_factory=fake.factory)  # type: ignore[arg-type]
    seen: set[str] = set()

    async def go() -> None:
        async with Client(server) as client:

            async def call(tool: str, **args: Any) -> dict[str, Any]:
                jsonschema.Draft202012Validator(TOOLS[tool]["inputSchema"]).validate(args)
                result = await client.call_tool(tool, args)
                assert not result.is_error, result.content
                body = result.structured_content
                assert isinstance(body, dict)
                _check_output(tool, body)
                # Text content carries the same JSON object.
                assert json.loads(result.content[0].text) == body  # type: ignore[union-attr]
                seen.add(tool)
                return body

            sid = (await call("start_session", intent="decide", participants=["alice"]))[
                "session_id"
            ]
            await call("get_commitment", session_id=sid)  # found: false shape
            await call("propose", session_id=sid, proposal_id="p1", option="canary")
            await call(
                "evaluate",
                session_id=sid,
                proposal_id="p1",
                recommendation="APPROVE",
                confidence=0.5,
            )
            await call("vote", session_id=sid, proposal_id="p1", vote="APPROVE")
            await call(
                "commit",
                session_id=sid,
                action="a",
                authority_scope="s",
                reason="r",
            )
            await call("get_session", session_id=sid)
            got = await call("get_commitment", session_id=sid)
            await call("verify", commitment_hash=got["commitment_hash"], session_id=sid)
            await call("verify", commitment_hash=got["commitment_hash"], payload_b64="/w==")

    asyncio.run(go())
    assert seen == set(TOOLS)


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        (
            {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
            {"default": None, "type": ["string", "null"]},
        ),
        # Not a simple nullable: left alone.
        (
            {"anyOf": [{"type": "string"}, {"type": "integer"}]},
            {"anyOf": [{"type": "string"}, {"type": "integer"}]},
        ),
        (
            {"anyOf": [{"enum": ["a"]}, {"type": "null"}]},
            {"anyOf": [{"enum": ["a"]}, {"type": "null"}]},
        ),
        (
            {
                "$defs": {"X": {"title": "X", "type": "object", "required": ["b", "a"]}},
                "properties": {"title": {"$ref": "#/$defs/X"}},
            },
            {"properties": {"title": {"required": ["a", "b"], "type": "object"}}},
        ),
    ],
)
def test_normalize_schema(schema: dict[str, Any], expected: dict[str, Any]) -> None:
    assert normalize_schema(schema) == expected


def test_contract_main_prints_manifest(capsys) -> None:
    _contract.main()
    assert json.loads(capsys.readouterr().out) == MANIFEST
