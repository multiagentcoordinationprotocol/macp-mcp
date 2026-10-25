# Changelog

## 0.1.0

Initial release.

- MCP server (stdio) that lets any MCP client run or join a MACP Decision
  session (`macp.mode.decision.v1`) on a MACP runtime.
- Tools: `start_session`, `propose`, `evaluate`, `vote`, `commit`,
  `get_session`, `get_commitment`, `verify` (RFC-MACP-0013 commitment hash).
- `contract/tools.json`: the tool contract shared with the TypeScript twin.
- Built on `macp-sdk-python` 0.18 (`AsyncMacpClient`) and the official `mcp`
  Python SDK.
