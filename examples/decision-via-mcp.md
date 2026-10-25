# A MACP Decision session driven through MCP

Recorded output of `python examples/decision_via_mcp.py`, the same driver the CI
`integration` job runs (`tests/integration/test_decision_via_mcp.py`).

Two `macp-mcp` servers were spawned over **stdio**, one per MACP agent, against
`ghcr.io/multiagentcoordinationprotocol/macp-runtime:0.9.0`. Every step below is
one MCP `tools/call`. The JSON shown is the tool's structured result, unedited;
ids and hashes differ on every run.

Step 8 is the point of the exercise. `alice`'s MCP client has a `commit` tool and
calls it, but she is not the session initiator, so the MACP runtime refuses the
action. The refusal reaches the model as a tool error. The MCP host decides
which tools exist; MACP decides who may use them, and when.

| MCP client | MACP agent | Negotiated MCP protocol |
|---|---|---|
| coordinator | `coordinator` | `2025-11-25` |
| alice | `alice` | `2026-07-28` |

Tools listed by the server: `commit`, `evaluate`, `get_commitment`, `get_session`, `propose`, `start_session`, `verify`, `vote`.

Outcome: session **RESOLVED**, commitment hash verified (`match`), tampered hash rejected (`mismatch`).

## 1. `coordinator` calls `start_session`

```json
{
  "intent": "Pick the deployment plan for release 2.1",
  "participants": [
    "coordinator",
    "alice"
  ],
  "ttl_ms": 120000
}
```

Result:

```json
{
  "ok": true,
  "duplicate": false,
  "message_id": "af7a8229-b04a-4e85-b272-af81aac94b61",
  "session_id": "d1128be9-29a1-4295-a17e-fe9066dfb263",
  "session_state": "OPEN",
  "mode": "macp.mode.decision.v1",
  "initiator": "coordinator",
  "participants": [
    "coordinator",
    "alice"
  ],
  "ttl_ms": 120000
}
```

## 2. `coordinator` calls `propose`

```json
{
  "session_id": "d1128be9-29a1-4295-a17e-fe9066dfb263",
  "proposal_id": "p1",
  "option": "Canary rollout of v2.1 to 5% of traffic",
  "rationale": "All tests green; canary limits blast radius"
}
```

Result:

```json
{
  "ok": true,
  "duplicate": false,
  "message_id": "b745ee8d-dc04-4dd1-8232-ec7a1f89eabc",
  "session_id": "d1128be9-29a1-4295-a17e-fe9066dfb263",
  "session_state": "OPEN",
  "proposal_id": "p1",
  "sender": "coordinator"
}
```

## 3. `alice` calls `get_session`

```json
{
  "session_id": "d1128be9-29a1-4295-a17e-fe9066dfb263"
}
```

Result:

```json
{
  "session_id": "d1128be9-29a1-4295-a17e-fe9066dfb263",
  "mode": "macp.mode.decision.v1",
  "state": "OPEN",
  "initiator": "coordinator",
  "participants": [
    "coordinator",
    "alice"
  ],
  "mode_version": "1.0.0",
  "configuration_version": "config.default",
  "policy_version": "policy.default",
  "started_at_unix_ms": 1792888119437,
  "expires_at_unix_ms": 1792888239434,
  "you": "alice",
  "phase": "Evaluation",
  "proposals": [
    {
      "proposal_id": "p1",
      "option": "Canary rollout of v2.1 to 5% of traffic",
      "rationale": "All tests green; canary limits blast radius",
      "sender": "coordinator"
    }
  ],
  "evaluations": [],
  "objections": [],
  "votes": [],
  "approve_totals": {},
  "majority_winner": null,
  "blocking_objection": false,
  "committed": false,
  "history": [
    {
      "message_type": "SessionStart",
      "sender": "coordinator",
      "message_id": "af7a8229-b04a-4e85-b272-af81aac94b61"
    },
    {
      "message_type": "Proposal",
      "sender": "coordinator",
      "message_id": "b745ee8d-dc04-4dd1-8232-ec7a1f89eabc"
    }
  ]
}
```

## 4. `alice` calls `evaluate`

```json
{
  "session_id": "d1128be9-29a1-4295-a17e-fe9066dfb263",
  "proposal_id": "p1",
  "recommendation": "APPROVE",
  "confidence": 0.9,
  "reason": "Canary plan has a rollback path"
}
```

Result:

```json
{
  "ok": true,
  "duplicate": false,
  "message_id": "caca228f-ab18-41a0-8081-a255ea40a4c7",
  "session_id": "d1128be9-29a1-4295-a17e-fe9066dfb263",
  "session_state": "OPEN",
  "proposal_id": "p1",
  "sender": "alice"
}
```

## 5. `alice` calls `vote`

```json
{
  "session_id": "d1128be9-29a1-4295-a17e-fe9066dfb263",
  "proposal_id": "p1",
  "vote": "APPROVE",
  "reason": "Ship the canary"
}
```

Result:

```json
{
  "ok": true,
  "duplicate": false,
  "message_id": "8ff1a534-90c8-4bea-84ad-a34af9a7f250",
  "session_id": "d1128be9-29a1-4295-a17e-fe9066dfb263",
  "session_state": "OPEN",
  "proposal_id": "p1",
  "sender": "alice",
  "vote": "APPROVE"
}
```

## 6. `coordinator` calls `vote`

```json
{
  "session_id": "d1128be9-29a1-4295-a17e-fe9066dfb263",
  "proposal_id": "p1",
  "vote": "APPROVE",
  "reason": "Agree"
}
```

Result:

```json
{
  "ok": true,
  "duplicate": false,
  "message_id": "ae482757-fd85-4bbe-8134-9f352901f7da",
  "session_id": "d1128be9-29a1-4295-a17e-fe9066dfb263",
  "session_state": "OPEN",
  "proposal_id": "p1",
  "sender": "coordinator",
  "vote": "APPROVE"
}
```

## 7. `coordinator` calls `get_session`

```json
{
  "session_id": "d1128be9-29a1-4295-a17e-fe9066dfb263"
}
```

Result:

```json
{
  "session_id": "d1128be9-29a1-4295-a17e-fe9066dfb263",
  "mode": "macp.mode.decision.v1",
  "state": "OPEN",
  "initiator": "coordinator",
  "participants": [
    "coordinator",
    "alice"
  ],
  "mode_version": "1.0.0",
  "configuration_version": "config.default",
  "policy_version": "policy.default",
  "started_at_unix_ms": 1792888119437,
  "expires_at_unix_ms": 1792888239434,
  "you": "coordinator",
  "phase": "Voting",
  "proposals": [
    {
      "proposal_id": "p1",
      "option": "Canary rollout of v2.1 to 5% of traffic",
      "rationale": "All tests green; canary limits blast radius",
      "sender": "coordinator"
    }
  ],
  "evaluations": [
    {
      "proposal_id": "p1",
      "recommendation": "APPROVE",
      "confidence": 0.9,
      "reason": "Canary plan has a rollback path",
      "sender": "alice"
    }
  ],
  "objections": [],
  "votes": [
    {
      "proposal_id": "p1",
      "vote": "APPROVE",
      "reason": "Ship the canary",
      "sender": "alice"
    },
    {
      "proposal_id": "p1",
      "vote": "APPROVE",
      "reason": "Agree",
      "sender": "coordinator"
    }
  ],
  "approve_totals": {
    "p1": 2
  },
  "majority_winner": "p1",
  "blocking_objection": false,
  "committed": false,
  "history": [
    {
      "message_type": "SessionStart",
      "sender": "coordinator",
      "message_id": "af7a8229-b04a-4e85-b272-af81aac94b61"
    },
    {
      "message_type": "Proposal",
      "sender": "coordinator",
      "message_id": "b745ee8d-dc04-4dd1-8232-ec7a1f89eabc"
    },
    {
      "message_type": "Evaluation",
      "sender": "alice",
      "message_id": "caca228f-ab18-41a0-8081-a255ea40a4c7"
    },
    {
      "message_type": "Vote",
      "sender": "alice",
      "message_id": "8ff1a534-90c8-4bea-84ad-a34af9a7f250"
    },
    {
      "message_type": "Vote",
      "sender": "coordinator",
      "message_id": "ae482757-fd85-4bbe-8134-9f352901f7da"
    }
  ]
}
```

## 8. `alice` calls `commit`

```json
{
  "session_id": "d1128be9-29a1-4295-a17e-fe9066dfb263",
  "action": "deployment.approved",
  "authority_scope": "release-management",
  "reason": "alice tries to resolve the session herself"
}
```

Error:

```json
{
  "text": "FORBIDDEN: Forbidden"
}
```

## 9. `coordinator` calls `commit`

```json
{
  "session_id": "d1128be9-29a1-4295-a17e-fe9066dfb263",
  "action": "deployment.approved",
  "authority_scope": "release-management",
  "reason": "majority winner p1: canary rollout"
}
```

Result:

```json
{
  "ok": true,
  "duplicate": false,
  "message_id": "f96d4b69-2b95-4476-9b51-9f9ebbfe4cfe",
  "session_id": "d1128be9-29a1-4295-a17e-fe9066dfb263",
  "session_state": "RESOLVED",
  "sender": "coordinator"
}
```

## 10. `alice` calls `get_commitment`

```json
{
  "session_id": "d1128be9-29a1-4295-a17e-fe9066dfb263"
}
```

Result:

```json
{
  "session_id": "d1128be9-29a1-4295-a17e-fe9066dfb263",
  "state": "RESOLVED",
  "found": true,
  "sender": "coordinator",
  "message_id": "f96d4b69-2b95-4476-9b51-9f9ebbfe4cfe",
  "commitment": {
    "commitment_id": "b98f2f7b-62ad-4946-b727-e4e84459ffe2",
    "action": "deployment.approved",
    "authority_scope": "release-management",
    "reason": "majority winner p1: canary rollout",
    "outcome_positive": true,
    "mode_version": "1.0.0",
    "policy_version": "policy.default",
    "configuration_version": "config.default"
  },
  "payload_b64": "CiRiOThmMmY3Yi02MmFkLTQ5NDYtYjcyNy1lNGU4NDQ1OWZmZTISE2RlcGxveW1lbnQuYXBwcm92ZWQaEnJlbGVhc2UtbWFuYWdlbWVudCIibWFqb3JpdHkgd2lubmVyIHAxOiBjYW5hcnkgcm9sbG91dCoFMS4wLjAyDnBvbGljeS5kZWZhdWx0Og5jb25maWcuZGVmYXVsdEAB",
  "commitment_hash": "sha256:0b19de07baac73ed198360d0fb6ac33f7c1dba0d01750748ead47e3c0004b304",
  "hash_label": "macp-commitment-hash/1"
}
```

## 11. `alice` calls `verify`

```json
{
  "commitment_hash": "sha256:0b19de07baac73ed198360d0fb6ac33f7c1dba0d01750748ead47e3c0004b304",
  "session_id": "d1128be9-29a1-4295-a17e-fe9066dfb263"
}
```

Result:

```json
{
  "outcome": "match",
  "claimed_hash": "sha256:0b19de07baac73ed198360d0fb6ac33f7c1dba0d01750748ead47e3c0004b304",
  "recomputed_hash": "sha256:0b19de07baac73ed198360d0fb6ac33f7c1dba0d01750748ead47e3c0004b304",
  "hash_label": "macp-commitment-hash/1",
  "source": "runtime_history",
  "session_id": "d1128be9-29a1-4295-a17e-fe9066dfb263"
}
```

## 12. `alice` calls `verify`

```json
{
  "commitment_hash": "sha256:0b19de07baac73ed198360d0fb6ac33f7c1dba0d01750748ead47e3c0004b300",
  "session_id": "d1128be9-29a1-4295-a17e-fe9066dfb263"
}
```

Result:

```json
{
  "outcome": "mismatch",
  "claimed_hash": "sha256:0b19de07baac73ed198360d0fb6ac33f7c1dba0d01750748ead47e3c0004b300",
  "recomputed_hash": "sha256:0b19de07baac73ed198360d0fb6ac33f7c1dba0d01750748ead47e3c0004b304",
  "hash_label": "macp-commitment-hash/1",
  "source": "runtime_history",
  "session_id": "d1128be9-29a1-4295-a17e-fe9066dfb263"
}
```

## 13. `coordinator` calls `get_session`

```json
{
  "session_id": "d1128be9-29a1-4295-a17e-fe9066dfb263"
}
```

Result:

```json
{
  "session_id": "d1128be9-29a1-4295-a17e-fe9066dfb263",
  "mode": "macp.mode.decision.v1",
  "state": "RESOLVED",
  "initiator": "coordinator",
  "participants": [
    "coordinator",
    "alice"
  ],
  "mode_version": "1.0.0",
  "configuration_version": "config.default",
  "policy_version": "policy.default",
  "started_at_unix_ms": 1792888119437,
  "expires_at_unix_ms": 1792888239434,
  "you": "coordinator",
  "phase": "Committed",
  "proposals": [
    {
      "proposal_id": "p1",
      "option": "Canary rollout of v2.1 to 5% of traffic",
      "rationale": "All tests green; canary limits blast radius",
      "sender": "coordinator"
    }
  ],
  "evaluations": [
    {
      "proposal_id": "p1",
      "recommendation": "APPROVE",
      "confidence": 0.9,
      "reason": "Canary plan has a rollback path",
      "sender": "alice"
    }
  ],
  "objections": [],
  "votes": [
    {
      "proposal_id": "p1",
      "vote": "APPROVE",
      "reason": "Ship the canary",
      "sender": "alice"
    },
    {
      "proposal_id": "p1",
      "vote": "APPROVE",
      "reason": "Agree",
      "sender": "coordinator"
    }
  ],
  "approve_totals": {
    "p1": 2
  },
  "majority_winner": "p1",
  "blocking_objection": false,
  "committed": true,
  "history": [
    {
      "message_type": "SessionStart",
      "sender": "coordinator",
      "message_id": "af7a8229-b04a-4e85-b272-af81aac94b61"
    },
    {
      "message_type": "Proposal",
      "sender": "coordinator",
      "message_id": "b745ee8d-dc04-4dd1-8232-ec7a1f89eabc"
    },
    {
      "message_type": "Evaluation",
      "sender": "alice",
      "message_id": "caca228f-ab18-41a0-8081-a255ea40a4c7"
    },
    {
      "message_type": "Vote",
      "sender": "alice",
      "message_id": "8ff1a534-90c8-4bea-84ad-a34af9a7f250"
    },
    {
      "message_type": "Vote",
      "sender": "coordinator",
      "message_id": "ae482757-fd85-4bbe-8134-9f352901f7da"
    },
    {
      "message_type": "Commitment",
      "sender": "coordinator",
      "message_id": "f96d4b69-2b95-4476-9b51-9f9ebbfe4cfe"
    }
  ]
}
```
