from __future__ import annotations

import pytest

from macp_mcp.config import (
    DEFAULT_AGENT_ID,
    DEFAULT_TARGET,
    DEFAULT_TTL_MS,
    ConfigError,
    Settings,
    load_settings,
)


def test_defaults_from_empty_env() -> None:
    s = load_settings({})
    assert s.target == DEFAULT_TARGET
    assert s.agent_id == DEFAULT_AGENT_ID
    assert s.token is None
    assert s.allow_insecure is False
    assert s.root_certificates is None
    assert s.default_ttl_ms == DEFAULT_TTL_MS
    assert s.auth_mode == "dev-agent"


def test_reads_every_variable(tmp_path) -> None:
    ca = tmp_path / "ca.pem"
    ca.write_bytes(b"PEM")
    s = load_settings(
        {
            "MACP_RUNTIME_TARGET": " runtime.example:443 ",
            "MACP_AGENT_ID": "alice",
            "MACP_AUTH_TOKEN": "tok-123",
            "MACP_TLS_CA_FILE": str(ca),
            "MACP_DEFAULT_TTL_MS": "60000",
            "MACP_REPLAY_IDLE_MS": "250",
        }
    )
    assert s.target == "runtime.example:443"
    assert s.agent_id == "alice"
    assert s.token == "tok-123"
    assert s.root_certificates == b"PEM"
    assert s.default_ttl_ms == 60_000
    assert s.replay_idle_ms == 250
    assert s.auth_mode == "bearer"


@pytest.mark.parametrize("raw", ["1", "true", "YES", "on"])
def test_allow_insecure_truthy(raw: str) -> None:
    assert load_settings({"MACP_ALLOW_INSECURE": raw}).allow_insecure is True


def test_allow_insecure_other_values_are_false() -> None:
    assert load_settings({"MACP_ALLOW_INSECURE": "0"}).allow_insecure is False


def test_dev_agent_auth_uses_agent_id() -> None:
    auth = Settings(agent_id="bob").auth
    assert auth.bearer_token == "bob"
    assert auth.expected_sender == "bob"


def test_bearer_auth_binds_expected_sender() -> None:
    auth = Settings(agent_id="bob", token="secret").auth
    assert auth.bearer_token == "secret"
    assert auth.expected_sender == "bob"


@pytest.mark.parametrize("raw", ["abc", "1.5"])
def test_non_integer_ttl_rejected(raw: str) -> None:
    with pytest.raises(ConfigError, match="MACP_DEFAULT_TTL_MS must be an integer"):
        load_settings({"MACP_DEFAULT_TTL_MS": raw})


@pytest.mark.parametrize("raw", ["0", "-5"])
def test_non_positive_ttl_rejected(raw: str) -> None:
    with pytest.raises(ConfigError, match="must be positive"):
        load_settings({"MACP_DEFAULT_TTL_MS": raw})


def test_ca_file_and_insecure_are_exclusive(tmp_path) -> None:
    ca = tmp_path / "ca.pem"
    ca.write_bytes(b"PEM")
    with pytest.raises(ConfigError, match="mutually exclusive"):
        load_settings({"MACP_TLS_CA_FILE": str(ca), "MACP_ALLOW_INSECURE": "1"})


def test_unreadable_ca_file(tmp_path) -> None:
    with pytest.raises(ConfigError, match="cannot read MACP_TLS_CA_FILE"):
        load_settings({"MACP_TLS_CA_FILE": str(tmp_path / "missing.pem")})


def test_defaults_to_os_environ(monkeypatch) -> None:
    monkeypatch.setenv("MACP_AGENT_ID", "from-env")
    assert load_settings().agent_id == "from-env"
