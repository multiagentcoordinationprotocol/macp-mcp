# A tool call gated by MACP

Recorded output of `python examples/gated_tool_call.py`, the same driver the CI
`integration` job runs (`tests/integration/test_gate.py`).

An MCP client spawned `macp-mcp gate` over **stdio** in front of a stub upstream
MCP server, against `ghcr.io/multiagentcoordinationprotocol/macp-runtime:0.9.0`.
The gate is MACP agent `gate`; `alice`, `bob` and `carol` are approvers using the
MACP SDK directly. Ids and hashes differ on every run.

The policy (`gate.json`):

```json
{
  "version": 1,
  "server": "filesystem",
  "rules": [
    {
      "tool": "write_file",
      "mode": "macp.mode.quorum.v1",
      "participants": [
        "alice",
        "bob",
        "carol"
      ],
      "threshold": 2,
      "ttl_ms": 60000,
      "description": "Two of three reviewers approve every write."
    },
    {
      "tool": "delete_file",
      "mode": "macp.mode.decision.v1",
      "participants": [
        "alice",
        "bob"
      ],
      "threshold": 2,
      "ttl_ms": 2000,
      "description": "Deletes need both owners, quickly."
    }
  ]
}
```

Tools listed through the gate (the upstream's, verbatim): `delete_file`, `env_names`, `read_file`, `write_file`.

## 1. `tools/call` `read_file`

```json
{"path": "/srv/reports/q3.md"}
```

No rule matches: forwarded to the upstream untouched.

Result:

```
contents of /srv/reports/q3.md
```

## 2. `tools/call` `write_file`

```json
{"path": "/srv/reports/q3.md", "content": "Q3 revenue: up 4%"}
```

Gated. `context_id` = `sha256:330ccb8df9d72d67b7d4835a591e313b5882bf79f69726d9fbfd06d896d77196`

Ballots: alice: Approve; bob: Approve.

Result:

```
wrote 17 bytes to /srv/reports/q3.md
```

`_meta`:

```json
{
  "example.com/upstream": {
    "trace": "t-1"
  },
  "io.multiagentcoordinationprotocol/commitment": {
    "session_id": "359adcd6-15ce-4837-a5d3-fb3b8b7b985e",
    "mode": "macp.mode.quorum.v1",
    "outcome": "approve",
    "commitment_hash": "sha256:3a5c44bee9e04af9ff72ac9707d79605bfa1da287a51ea1a7cf41622d1064bce"
  }
}
```

The MACP session afterwards:

```json
{
  "session_id": "359adcd6-15ce-4837-a5d3-fb3b8b7b985e",
  "mode": "macp.mode.quorum.v1",
  "state": "RESOLVED",
  "initiator": "gate",
  "participants": [
    "alice",
    "bob",
    "carol"
  ],
  "context_id": "sha256:330ccb8df9d72d67b7d4835a591e313b5882bf79f69726d9fbfd06d896d77196"
}
```

## 3. `tools/call` `write_file`

```json
{"path": "/etc/passwd", "content": "root::0:0::/:/bin/sh"}
```

Gated. `context_id` = `sha256:0dfa26f410b84d3aa866a84888cf4f7e2d647973a39738cb7d528a4da1da626e`

Ballots: alice: Reject; bob: Reject.

Error:

```
POLICY_DENIED: tool call write_file denied by session 43058832-b858-471e-8496-cfd596b1e60b
```

The MACP session afterwards:

```json
{
  "session_id": "43058832-b858-471e-8496-cfd596b1e60b",
  "mode": "macp.mode.quorum.v1",
  "state": "RESOLVED",
  "initiator": "gate",
  "participants": [
    "alice",
    "bob",
    "carol"
  ],
  "context_id": "sha256:0dfa26f410b84d3aa866a84888cf4f7e2d647973a39738cb7d528a4da1da626e"
}
```

## 4. `tools/call` `delete_file`

```json
{"path": "/srv/reports"}
```

Gated. `context_id` = `sha256:4899c05dae5e203c273dc8e929e04b9c31ddc7345729d03296836e9ea08b3468`

No ballots are cast.

Error:

```
SESSION_NOT_OPEN: session 9d2ef294-adbd-4117-8885-1304846d9daf ended EXPIRED without a commitment
```

The MACP session afterwards:

```json
{
  "session_id": "9d2ef294-adbd-4117-8885-1304846d9daf",
  "mode": "macp.mode.decision.v1",
  "state": "EXPIRED",
  "initiator": "gate",
  "participants": [
    "gate",
    "alice",
    "bob"
  ],
  "context_id": "sha256:4899c05dae5e203c273dc8e929e04b9c31ddc7345729d03296836e9ea08b3468"
}
```

## What reached the upstream

```json
[
  {
    "tool": "read_file",
    "arguments": {
      "path": "/srv/reports/q3.md"
    }
  },
  {
    "tool": "write_file",
    "arguments": {
      "path": "/srv/reports/q3.md",
      "content": "Q3 revenue: up 4%"
    }
  }
]
```

Only the passthrough read and the approved write. The rejected write and the
expired delete never left the gate.
