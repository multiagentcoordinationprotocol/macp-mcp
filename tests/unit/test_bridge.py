from __future__ import annotations

import asyncio
import base64
from typing import Any

import pytest
from macp.v1 import envelope_pb2
from macp_sdk import MacpAckError

from macp_mcp.bridge import MODE_DECISION, MacpBridge, default_client_factory
from macp_mcp.config import Settings

from .fakes import FakeClient


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def make_bridge(agent: str = "coordinator", client: FakeClient | None = None) -> MacpBridge:
    fake = client or FakeClient()
    return MacpBridge(Settings(agent_id=agent), client_factory=fake.factory)  # type: ignore[arg-type]


async def resolved_session(bridge: MacpBridge) -> str:
    started = await bridge.start_session("pick a plan", ["alice"])
    sid = started["session_id"]
    await bridge.propose(sid, "p1", "canary", rationale="safe")
    await bridge.vote(sid, "p1", "approve")
    await bridge.commit(sid, "deployment.approved", "release", "p1 won")
    return sid


def test_start_session_adds_self_and_dedupes() -> None:
    bridge = make_bridge()
    out = run(bridge.start_session("decide", [" alice ", "alice", "", "bob"], ttl_ms=5000))
    assert out["participants"] == ["coordinator", "alice", "bob"]
    assert out["ok"] is True
    assert out["session_state"] == "OPEN"
    assert out["mode"] == MODE_DECISION
    assert out["initiator"] == "coordinator"
    assert out["ttl_ms"] == 5000
    assert out["session_id"]


def test_start_session_keeps_self_position_and_default_ttl() -> None:
    bridge = make_bridge()
    out = run(bridge.start_session("decide", ["alice", "coordinator"]))
    assert out["participants"] == ["alice", "coordinator"]
    assert out["ttl_ms"] == Settings().default_ttl_ms


def test_actions_echo_sender_and_normalise_vote() -> None:
    fake = FakeClient()
    bridge = make_bridge(client=fake)

    async def go() -> list[dict[str, Any]]:
        sid = (await bridge.start_session("decide", ["alice"]))["session_id"]
        return [
            await bridge.propose(sid, "p1", "canary"),
            await bridge.evaluate(sid, "p1", "approve", 0.8, reason="ok"),
            await bridge.vote(sid, "p1", "approve"),
        ]

    proposed, evaluated, voted = run(go())
    assert proposed["sender"] == evaluated["sender"] == voted["sender"] == "coordinator"
    assert voted["vote"] == "APPROVE"
    assert [e.message_type for e in fake.sent] == [
        "SessionStart",
        "Proposal",
        "Evaluation",
        "Vote",
    ]


def test_commit_binds_session_versions() -> None:
    fake = FakeClient()
    bridge = make_bridge(client=fake)
    sid = run(resolved_session(bridge))
    commit = fake.sent[-1]
    assert commit.message_type == "Commitment"
    meta = fake.metadata[sid]
    out = run(bridge.get_commitment(sid))
    assert out["commitment"]["mode_version"] == meta.mode_version
    assert out["commitment"]["policy_version"] == meta.policy_version
    assert out["commitment"]["configuration_version"] == meta.configuration_version


def test_get_session_projects_full_history() -> None:
    bridge = make_bridge()

    async def go() -> dict[str, Any]:
        sid = (await bridge.start_session("decide", ["alice"]))["session_id"]
        await bridge.propose(sid, "p1", "canary", rationale="safe")
        await bridge.evaluate(sid, "p1", "APPROVE", 0.9)
        await bridge.vote(sid, "p1", "APPROVE", reason="go")
        return await bridge.get_session(sid)

    out = run(go())
    assert out["state"] == "OPEN"
    assert out["you"] == "coordinator"
    assert out["participants"] == ["coordinator", "alice"]
    assert out["proposals"][0]["option"] == "canary"
    assert out["evaluations"][0]["confidence"] == pytest.approx(0.9)
    assert out["votes"] == [
        {"proposal_id": "p1", "vote": "APPROVE", "reason": "go", "sender": "coordinator"}
    ]
    assert out["majority_winner"] == "p1"
    assert out["approve_totals"] == {"p1": 1}
    assert out["blocking_objection"] is False
    assert out["committed"] is False
    assert [h["message_type"] for h in out["history"]] == [
        "SessionStart",
        "Proposal",
        "Evaluation",
        "Vote",
    ]


def test_get_commitment_before_resolution() -> None:
    bridge = make_bridge()

    async def go() -> dict[str, Any]:
        sid = (await bridge.start_session("decide", ["alice"]))["session_id"]
        return await bridge.get_commitment(sid)

    out = run(go())
    assert out == {"session_id": out["session_id"], "state": "OPEN", "found": False}


def test_get_commitment_and_verify_roundtrip() -> None:
    bridge = make_bridge()

    async def go() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        sid = await resolved_session(bridge)
        got = await bridge.get_commitment(sid)
        claimed = got["commitment_hash"]
        by_session = await bridge.verify(claimed, session_id=sid)
        by_bytes = await bridge.verify(claimed, payload_b64=got["payload_b64"])
        tampered = claimed[:-1] + ("0" if claimed[-1] != "0" else "1")
        bad = await bridge.verify(tampered, session_id=sid)
        return got, by_session, by_bytes, bad

    got, by_session, by_bytes, bad = run(go())
    assert got["found"] is True
    assert got["state"] == "RESOLVED"
    assert got["sender"] == "coordinator"
    assert got["commitment"]["action"] == "deployment.approved"
    assert got["commitment_hash"].startswith("sha256:")
    assert got["hash_label"] == "macp-commitment-hash/1"
    assert by_session["outcome"] == "match"
    assert by_session["source"] == "runtime_history"
    assert by_session["session_id"] == got["session_id"]
    assert by_bytes["outcome"] == "match"
    assert by_bytes["source"] == "payload_b64"
    assert "session_id" not in by_bytes
    assert bad["outcome"] == "mismatch"
    assert bad["recomputed_hash"] == got["commitment_hash"]


def test_verify_cannot_verify_non_commitment_bytes() -> None:
    bridge = make_bridge()
    junk = base64.b64encode(b"\xff\xff\xff").decode()
    out = run(bridge.verify("sha256:" + "0" * 64, payload_b64=junk))
    assert out["outcome"] == "cannot_verify"
    assert out["recomputed_hash"] is None


@pytest.mark.parametrize(
    ("session_id", "payload_b64"),
    [("", ""), ("s1", "AAAA")],
)
def test_verify_requires_exactly_one_source(session_id: str, payload_b64: str) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        run(make_bridge().verify("sha256:" + "0" * 64, session_id, payload_b64))


def test_verify_rejects_bad_base64() -> None:
    with pytest.raises(ValueError, match="not valid base64"):
        run(make_bridge().verify("sha256:" + "0" * 64, payload_b64="not base64!"))


def test_verify_open_session_has_no_commitment() -> None:
    bridge = make_bridge()

    async def go() -> None:
        sid = (await bridge.start_session("decide", ["alice"]))["session_id"]
        await bridge.verify("sha256:" + "0" * 64, session_id=sid)

    with pytest.raises(ValueError, match="has no Commitment"):
        run(go())


def test_replay_skips_foreign_and_duplicate_envelopes() -> None:
    fake = FakeClient()
    bridge = make_bridge(client=fake)

    async def go() -> list[envelope_pb2.Envelope]:
        sid = (await bridge.start_session("decide", ["alice"]))["session_id"]
        first = fake.history[sid][0]
        fake.extra_stream_envelopes = [
            envelope_pb2.Envelope(session_id="other", message_id="x", message_type="Proposal"),
            first,
        ]
        fake.stream_ends_with_none = True
        return await bridge.replay(sid)

    history = run(go())
    assert [e.message_type for e in history] == ["SessionStart"]


def test_replay_stops_at_deadline() -> None:
    fake = FakeClient()
    ticks = iter([0.0, 100.0])
    bridge = MacpBridge(
        Settings(),
        client_factory=fake.factory,
        clock=lambda: next(ticks),  # type: ignore[arg-type,return-value]
    )

    async def go() -> list[envelope_pb2.Envelope]:
        sid = (await bridge.start_session("decide", ["alice"]))["session_id"]
        return await bridge.replay(sid)

    assert run(go()) == []


def test_runtime_rejection_propagates() -> None:
    fake = FakeClient()
    bridge = make_bridge(client=fake)

    async def go() -> None:
        sid = (await bridge.start_session("decide", ["alice"]))["session_id"]
        fake.reject_next = "SESSION_NOT_OPEN"
        await bridge.vote(sid, "p1", "APPROVE")

    with pytest.raises(MacpAckError, match="SESSION_NOT_OPEN"):
        run(go())


def test_client_is_lazy_and_closable() -> None:
    fake = FakeClient()
    calls: list[Settings] = []

    def factory(s: Settings) -> FakeClient:
        calls.append(s)
        return fake

    bridge = MacpBridge(Settings(), client_factory=factory)  # type: ignore[arg-type]
    assert calls == []
    assert bridge.client is bridge.client
    assert len(calls) == 1
    run(bridge.aclose())
    assert fake.closed is True
    run(bridge.aclose())  # idempotent once closed


def test_default_client_factory_builds_async_client() -> None:
    async def go() -> None:
        client = default_client_factory(Settings(allow_insecure=True, agent_id="a"))
        try:
            assert client.__class__.__name__ == "AsyncMacpClient"
        finally:
            await client.close()

    run(go())
