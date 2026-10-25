"""Environment-driven configuration for the macp-mcp server.

One server process is one MACP agent. Every setting comes from the
environment, because MCP clients (Claude Desktop, Claude Code, Cursor)
launch the server as a subprocess and pass configuration through an ``env``
block, not flags.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from macp_sdk import AuthConfig

DEFAULT_TARGET = "127.0.0.1:50051"
DEFAULT_AGENT_ID = "mcp-agent"
DEFAULT_TTL_MS = 300_000
DEFAULT_REPLAY_IDLE_MS = 1_000

_TRUE = {"1", "true", "yes", "on"}


class ConfigError(ValueError):
    """Raised when the environment holds an unusable configuration."""


@dataclass(frozen=True)
class Settings:
    """Resolved server settings.

    :param target: runtime gRPC address (``host:port``).
    :param agent_id: the MACP identity this server acts as. With dev-agent
        auth it is also the bearer token; with a real token it is the
        identity the runtime binds that token to.
    :param token: bearer token issued by the runtime, or ``None`` for
        dev-agent auth (local runtimes started with ``MACP_ALLOW_INSECURE=1``).
    :param allow_insecure: use a plaintext channel instead of TLS.
    :param root_certificates: PEM bundle for a private CA, or ``None`` for the
        system trust store.
    :param default_ttl_ms: TTL used by ``start_session`` when the caller
        does not pass one.
    :param replay_idle_ms: how long a history replay waits for the next
        envelope before treating the replay as complete.
    """

    target: str = DEFAULT_TARGET
    agent_id: str = DEFAULT_AGENT_ID
    token: str | None = None
    allow_insecure: bool = False
    root_certificates: bytes | None = None
    default_ttl_ms: int = DEFAULT_TTL_MS
    replay_idle_ms: int = DEFAULT_REPLAY_IDLE_MS

    @property
    def auth(self) -> AuthConfig:
        """The SDK auth config for this agent.

        A token selects ``AuthConfig.for_bearer`` bound to ``agent_id``;
        no token selects ``AuthConfig.for_dev_agent``, whose bearer value is
        the agent id itself (the runtime's dev fallback binds it verbatim).
        """
        if self.token:
            return AuthConfig.for_bearer(self.token, expected_sender=self.agent_id)
        return AuthConfig.for_dev_agent(self.agent_id)

    @property
    def auth_mode(self) -> str:
        return "bearer" if self.token else "dev-agent"


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from None
    if value <= 0:
        raise ConfigError(f"{name} must be positive, got {value}")
    return value


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build :class:`Settings` from ``env`` (defaults to ``os.environ``).

    Recognised variables:

    - ``MACP_RUNTIME_TARGET``: runtime address (default ``127.0.0.1:50051``).
    - ``MACP_AGENT_ID``: this agent's identity (default ``mcp-agent``).
    - ``MACP_AUTH_TOKEN``: bearer token; unset means dev-agent auth.
    - ``MACP_ALLOW_INSECURE``: ``1`` for a plaintext channel (local dev only).
    - ``MACP_TLS_CA_FILE``: PEM file for a private CA.
    - ``MACP_DEFAULT_TTL_MS``: default session TTL (default 300000).
    - ``MACP_REPLAY_IDLE_MS``: replay idle cutoff (default 1000).
    """
    env = os.environ if env is None else env
    target = env.get("MACP_RUNTIME_TARGET", "").strip() or DEFAULT_TARGET
    agent_id = env.get("MACP_AGENT_ID", "").strip() or DEFAULT_AGENT_ID
    token = env.get("MACP_AUTH_TOKEN", "").strip() or None
    allow_insecure = env.get("MACP_ALLOW_INSECURE", "").strip().lower() in _TRUE

    root_certificates: bytes | None = None
    ca_file = env.get("MACP_TLS_CA_FILE", "").strip()
    if ca_file:
        if allow_insecure:
            raise ConfigError("MACP_TLS_CA_FILE and MACP_ALLOW_INSECURE are mutually exclusive")
        try:
            with open(ca_file, "rb") as fh:
                root_certificates = fh.read()
        except OSError as exc:
            raise ConfigError(f"cannot read MACP_TLS_CA_FILE {ca_file!r}: {exc}") from None

    return Settings(
        target=target,
        agent_id=agent_id,
        token=token,
        allow_insecure=allow_insecure,
        root_certificates=root_certificates,
        default_ttl_ms=_int(env, "MACP_DEFAULT_TTL_MS", DEFAULT_TTL_MS),
        replay_idle_ms=_int(env, "MACP_REPLAY_IDLE_MS", DEFAULT_REPLAY_IDLE_MS),
    )
