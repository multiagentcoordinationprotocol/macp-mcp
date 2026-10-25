# macp-mcp

An MCP server that lets any MCP client (Claude Desktop, Claude Code, Cursor and
others) run or join a [MACP](https://multiagentcoordinationprotocol.io) Decision
session.

**The first proof that MACP governs MCP tool use.** MCP decides which tools a
model can call. MACP decides who may use them and when, and records what was
decided. The model drives every step through ordinary MCP tool calls. The MACP
runtime enforces who may propose, vote and commit, ends the session in one
binding Commitment, and gives that outcome an RFC-MACP-0013 hash that anyone
can recompute.
[`examples/decision-via-mcp.md`](examples/decision-via-mcp.md) is a recorded
run: two MCP clients drive a session to `RESOLVED`. The runtime refuses the
non-initiator's `commit` call, and the commitment hash verifies.

```
MCP client (model) ──stdio──▶ macp-mcp ──gRPC──▶ MACP runtime
   tool calls                 one MACP agent     enforces membership, order,
                              (macp-sdk-python)  identity; records the Commitment
```

## Install

```bash
pip install macp-mcp        # or: uvx macp-mcp
```

Python 3.11 or newer. It needs a MACP runtime to talk to. For local
development:

```bash
docker run -p 50051:50051 -e MACP_ALLOW_INSECURE=1 -e MACP_MEMORY_ONLY=1 \
  ghcr.io/multiagentcoordinationprotocol/macp-runtime:0.9.0
```

One `macp-mcp` process is one MACP agent. To have several agents take part in
a session, run one server per agent (for example, one per MCP client), each
with its own `MACP_AGENT_ID`.

## Configure your MCP client

### Claude Desktop

Add the server to `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "macp": {
      "command": "uvx",
      "args": ["macp-mcp"],
      "env": {
        "MACP_RUNTIME_TARGET": "127.0.0.1:50051",
        "MACP_AGENT_ID": "coordinator",
        "MACP_ALLOW_INSECURE": "1"
      }
    }
  }
}
```

If you installed with pip, use `"command": "macp-mcp"` and drop `args`.

### Claude Code

```bash
claude mcp add macp \
  --env MACP_RUNTIME_TARGET=127.0.0.1:50051 \
  --env MACP_AGENT_ID=coordinator \
  --env MACP_ALLOW_INSECURE=1 \
  -- uvx macp-mcp
```

Or check it into a project's `.mcp.json`:

```json
{
  "mcpServers": {
    "macp": {
      "command": "uvx",
      "args": ["macp-mcp"],
      "env": {
        "MACP_RUNTIME_TARGET": "127.0.0.1:50051",
        "MACP_AGENT_ID": "alice",
        "MACP_ALLOW_INSECURE": "1"
      }
    }
  }
}
```

Then ask the model something like: *"Start a MACP decision with alice on
which deployment plan to use, propose a canary rollout, and vote for it."*

### Against a production runtime

Drop `MACP_ALLOW_INSECURE` and pass the bearer token the runtime issued for
this agent:

```json
"env": {
  "MACP_RUNTIME_TARGET": "macp.example.com:443",
  "MACP_AGENT_ID": "coordinator",
  "MACP_AUTH_TOKEN": "<token issued for coordinator>"
}
```

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `MACP_RUNTIME_TARGET` | `127.0.0.1:50051` | Runtime gRPC address (`host:port`). |
| `MACP_AGENT_ID` | `mcp-agent` | The MACP agent this server acts as. With dev-agent auth it is also the bearer token. With `MACP_AUTH_TOKEN` it is the identity the runtime must bind the token to. |
| `MACP_AUTH_TOKEN` | unset | Bearer token issued by the runtime. When unset, dev-agent auth is used, which needs a runtime started with `MACP_ALLOW_INSECURE=1`. |
| `MACP_ALLOW_INSECURE` | off | `1` uses plaintext gRPC. For a local runtime only. |
| `MACP_TLS_CA_FILE` | system trust | PEM bundle for a private CA. Cannot be combined with `MACP_ALLOW_INSECURE`. |
| `MACP_DEFAULT_TTL_MS` | `300000` | Session TTL used when `start_session` is called without `ttl_ms`. |
| `MACP_REPLAY_IDLE_MS` | `1000` | How long a history read waits for the next envelope before treating the replay as complete. |

The server logs to stderr only, because stdout carries the MCP protocol. A
bad configuration exits with status 2 and a one-line message.

## Tools

| Tool | Kind | What it does |
|---|---|---|
| `start_session` | write | Start a Decision session with this agent as initiator. Takes `intent`, `participants` and optional `ttl_ms` and `context_id`. Returns the `session_id` to share. |
| `propose` | write | Put an option (`proposal_id`, `option`, `rationale`) up for decision. |
| `vote` | write | Cast this agent's `APPROVE` / `REJECT` / `ABSTAIN` vote on a proposal. |
| `get_commitment` | read | The resolved session's binding Commitment, the exact payload bytes (`payload_b64`) and its RFC-MACP-0013 `commitment_hash`. |
| `verify` | read | Recompute a commitment hash with the SDK verifier, either from the runtime's recorded history (`session_id`) or from bytes you hold (`payload_b64`). Returns `match`, `mismatch` or `cannot_verify`. |
| `evaluate` | write | Record a scored assessment (`APPROVE` / `REVIEW` / `BLOCK` / `REJECT`, confidence 0..1) before voting. |
| `commit` | write | Resolve the session with a binding Commitment (`action`, `authority_scope`, `reason`). Under the default policy only the initiator can commit. |
| `get_session` | read | The session's state, participants, proposals, evaluations, votes, approve totals and majority winner. |

The first five are the core loop: start, propose, vote, read the outcome and
check it. The other three are the minimum needed to make that loop usable
from MCP:

- **`commit`**: a Decision session only resolves, and only produces a
  Commitment for `get_commitment` and `verify` to read, when someone commits.
  Without this tool an MCP client could never finish a session it started.
- **`get_session`**: MCP is request/response, and the runtime's `GetSession`
  returns metadata only. Without this tool a model joining a session has no
  way to see what was proposed or how the votes stand. It rebuilds the
  session view from the runtime's replayable history.
- **`evaluate`**: the Decision mode's assessment step. It lets an agent record
  a reasoned, scored opinion (including a `BLOCK`) that other participants and
  later auditors can see, separately from the bare vote.

Objections, cancellation, signals and other modes are deliberately left out.
They belong to orchestrators built on
[macp-sdk-python](https://github.com/multiagentcoordinationprotocol/macp-sdk-python).

Errors the model can act on, such as a runtime rejection or a bad argument,
come back as tool errors with the text `"<CODE>: <message>"`, for example
`FORBIDDEN: Forbidden` when a non-initiator tries to commit. The code is the
MACP error code (the Ack failure code, then the `macp-error-code` trailer,
then the gRPC status name).

The server is stateless. Every read replays the session from the runtime, so a
restart loses nothing, and several servers (agents) can share one session.

## Gate an MCP server's tool calls (`macp-mcp gate`)

`macp-mcp gate` is an MCP proxy. Put it in front of any stdio MCP server, and
the tool calls you name in a policy file run only after a MACP session
approves them:

```bash
macp-mcp gate --policy gate.json --upstream npx -y @modelcontextprotocol/server-filesystem /srv
```

Everything after `--upstream` is the upstream's command line. Your MCP client
spawns the gate instead of the upstream. The gate lists the upstream's tools
unchanged.

```json
{
  "version": 1,
  "server": "filesystem",
  "rules": [
    { "tool": "write_file", "mode": "macp.mode.quorum.v1",
      "participants": ["alice", "bob", "carol"], "threshold": 2, "ttl_ms": 120000 },
    { "tool": "move_*", "mode": "macp.mode.decision.v1",
      "participants": ["alice", "bob"], "ttl_ms": 60000 }
  ]
}
```

A `tools/call` that matches no rule is forwarded as-is. The first rule that
matches (`*` and `?` are wildcards) makes the gate, acting as MACP agent
`MACP_AGENT_ID`, do this:

1. Open a Quorum or Decision session with the rule's `participants` as
   approvers. Its `context_id` is `sha256:` plus the hex SHA-256 of the RFC 8785
   canonical JSON of `{arguments, server, tool}`, which binds the session to this
   exact call. The approvers find it by that `context_id` (`ListSessions` /
   `WatchSessions`) and vote with any MACP client.
2. Commit as soon as the outcome is settled: approve once `threshold` approvers
   (default: a strict majority) have approved, deny once that can no longer
   happen. Each approver's first ballot counts.
3. **Approved:** forward the call. The upstream's result comes back with the
   Commitment attached, next to any `_meta` the upstream set:

   ```json
   "_meta": { "io.multiagentcoordinationprotocol/commitment": {
     "session_id": "…", "mode": "macp.mode.quorum.v1", "outcome": "approve",
     "commitment_hash": "sha256:…" } }
   ```

   `commitment_hash` is the RFC-MACP-0013 hash. Check it with the `verify` tool
   or any MACP SDK.
4. **Refused:** return a tool error and never call the upstream. The error
   starts with `POLICY_DENIED` (the approvers denied it, or a runtime policy
   refused the commitment) or `SESSION_NOT_OPEN` (the session expired or was
   cancelled first). An argument holding an integer outside ±(2^53−1) is
   refused with `INVALID_ARGUMENT` before any session opens, because it has no
   canonical form to bind.

The gate passes its environment on to the upstream, minus `MACP_AUTH_TOKEN`.
[`examples/gated-tool-call.md`](examples/gated-tool-call.md) is a recorded run
against the pinned runtime: one passthrough, one approved write, one rejected
write and one expired delete.
[`contract/gate/`](contract/gate) pins the policy schema, the CLI, the session
shape, the refusal texts and the `context_id` vectors. The TypeScript twin
uses a byte-identical copy.

## Tool contract

[`contract/tools.json`](contract/tools.json) pins the tool names,
descriptions, annotations, input schemas and output schemas. It is fully
inlined JSON Schema 2020-12. The TypeScript twin
([macp-mcp-typescript](https://github.com/multiagentcoordinationprotocol/macp-mcp-typescript))
exposes the identical surface and tests against a byte-identical copy of this
file. Any change to the tool surface changes the manifest (`make contract`)
and needs the matching change in the TypeScript twin.

## Development

```bash
make setup                    # pip install -e ".[dev]"
make lint typecheck test      # ruff, mypy strict, unit tests + 85% coverage gate
make verify-contract          # advertised tools == contract/tools.json; gate contract checks
make test-integration-docker  # CI's integration gate: pinned runtime image + stdio MCP clients
make transcript               # re-record examples/*.md transcripts (runtime on :50051)
```

Releases follow macp-sdk-python: release-please opens the release PR, and
merging it tags `vX.Y.Z`, which publishes to PyPI through trusted publishing.

## License

Apache-2.0
