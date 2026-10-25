# Security Policy

## Supported versions

Security fixes are released for the latest minor version only.

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |

## Reporting a vulnerability

Please **do not** open a public issue for a suspected vulnerability.

Report it privately through GitHub Security Advisories: go to this repository's
**Security** tab and click **Report a vulnerability**
(<https://github.com/multiagentcoordinationprotocol/macp-mcp/security/advisories/new>).
The report is visible only to you and the maintainers.

Please include the affected version, a description of the issue, and steps or a
minimal snippet to reproduce it. We will acknowledge the report, keep you updated
as we investigate, and credit you in the advisory unless you prefer otherwise.

## Deployment notes

- `macp-mcp` acts as exactly one MACP agent: the one named by `MACP_AGENT_ID`
  and authenticated by `MACP_AUTH_TOKEN`. Anything that can drive the MCP
  client (including the model) can act as that agent within what the runtime
  allows. Give it a token scoped to that agent only.
- `MACP_ALLOW_INSECURE=1` (plaintext gRPC, dev-agent auth) is for a local
  development runtime only. Never point it at a shared runtime.
- The token is read from the environment and never returned in tool results or
  written to logs.
