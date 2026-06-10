"""Unit tests for the hosted Postgres connection policy (cortex#472).

No driver required: these cover URL validation, the failure taxonomy, and
the policy kwargs without importing psycopg.
"""

from __future__ import annotations

import sys

import pytest

from cortex.hosted.db import (
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_STATEMENT_TIMEOUT_MS,
    HOSTED_APPLICATION_NAME,
    ConnectionSettings,
    HostedDbError,
    classify_connection_failure,
    connect,
    redacted_url,
)

RAILWAY_URL = "postgresql://cortex:s3cret@compass.railway.internal:5432/railway?sslmode=require"


def test_railway_url_is_held_verbatim_with_sslmode_honored() -> None:
    """Invariant: the policy never rewrites the URL, so sslmode cannot be lost."""

    settings = ConnectionSettings(RAILWAY_URL)
    assert settings.conninfo == RAILWAY_URL
    assert settings.sslmode == "require"


def test_connect_kwargs_carry_the_policy_and_never_override_sslmode() -> None:
    settings = ConnectionSettings(RAILWAY_URL)
    kwargs = settings.connect_kwargs()
    assert kwargs == {
        "connect_timeout": DEFAULT_CONNECT_TIMEOUT_SECONDS,
        "application_name": HOSTED_APPLICATION_NAME,
        "options": f"-c statement_timeout={DEFAULT_STATEMENT_TIMEOUT_MS}",
    }
    assert "sslmode" not in kwargs


def test_timeout_overrides_flow_into_kwargs() -> None:
    settings = ConnectionSettings(
        RAILWAY_URL, connect_timeout_seconds=3, statement_timeout_ms=1500
    )
    kwargs = settings.connect_kwargs()
    assert kwargs["connect_timeout"] == 3
    assert kwargs["options"] == "-c statement_timeout=1500"


@pytest.mark.parametrize("connect_timeout_seconds", [0, -1])
def test_non_positive_connect_timeout_is_rejected(connect_timeout_seconds: int) -> None:
    with pytest.raises(HostedDbError, match="connect_timeout_seconds"):
        ConnectionSettings(RAILWAY_URL, connect_timeout_seconds=connect_timeout_seconds)


@pytest.mark.parametrize("statement_timeout_ms", [0, -250])
def test_non_positive_statement_timeout_is_rejected(statement_timeout_ms: int) -> None:
    with pytest.raises(HostedDbError, match="statement_timeout_ms"):
        ConnectionSettings(RAILWAY_URL, statement_timeout_ms=statement_timeout_ms)


@pytest.mark.parametrize("url", ["", "   "])
def test_empty_url_is_rejected(url: str) -> None:
    with pytest.raises(HostedDbError, match="must not be empty"):
        ConnectionSettings(url)


def test_non_postgres_scheme_is_rejected() -> None:
    with pytest.raises(HostedDbError, match="scheme"):
        ConnectionSettings("mysql://user:pass@host:3306/db")


def test_url_without_host_is_rejected() -> None:
    with pytest.raises(HostedDbError, match="host"):
        ConnectionSettings("postgresql:///railway")


def test_unparseable_port_is_rejected() -> None:
    with pytest.raises(HostedDbError, match="invalid hosted database URL"):
        ConnectionSettings("postgresql://user:pass@host:notaport/db")


def test_empty_sslmode_is_rejected_not_silently_dropped() -> None:
    with pytest.raises(HostedDbError, match="sslmode is declared but empty"):
        ConnectionSettings("postgresql://user:pass@host:5432/db?sslmode=")


def test_unknown_sslmode_value_is_rejected() -> None:
    with pytest.raises(HostedDbError, match="unknown sslmode 'requre'"):
        ConnectionSettings("postgresql://user:pass@host:5432/db?sslmode=requre")


def test_last_sslmode_occurrence_wins_matching_libpq() -> None:
    settings = ConnectionSettings(
        "postgresql://user:pass@host:5432/db?sslmode=disable&sslmode=require"
    )
    assert settings.sslmode == "require"


def test_sslmode_is_none_when_url_does_not_declare_it() -> None:
    settings = ConnectionSettings("postgresql://user:pass@host:5432/db")
    assert settings.sslmode is None


def test_application_name_must_not_be_blank() -> None:
    with pytest.raises(HostedDbError, match="application_name"):
        ConnectionSettings(RAILWAY_URL, application_name="   ")


def test_redacted_url_hides_the_password_and_keeps_everything_else() -> None:
    redacted = redacted_url(RAILWAY_URL)
    assert "s3cret" not in redacted
    assert ":***@" in redacted
    assert "compass.railway.internal:5432" in redacted
    assert "sslmode=require" in redacted


def test_redacted_url_without_credentials_is_unchanged() -> None:
    url = "postgresql://host:5432/db"
    assert redacted_url(url) == url


def test_redacted_url_refuses_to_echo_unparseable_urls() -> None:
    assert redacted_url("postgresql://user:pass@host:notaport/db") == (
        "<unparseable database URL>"
    )


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ('FATAL:  password authentication failed for user "cortex"', "auth"),
        ("fe_sendauth: no password supplied", "auth"),
        ('FATAL: pg_hba.conf rejects connection for host "1.2.3.4"', "auth"),
        ("connection timed out", "unreachable"),
        ('could not translate host name "gone.railway.internal" to address', "unreachable"),
        ("connection refused", "unreachable"),
        ('FATAL:  database "missing" does not exist', "rejected"),
    ],
)
def test_connection_failures_classify_into_visible_reasons(message: str, expected: str) -> None:
    assert classify_connection_failure(message) == expected


def test_connect_without_driver_names_the_hosted_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing driver is a visible HostedDbError, never a bare ImportError."""

    # Setting the sys.modules entry to None forces `import psycopg` to raise
    # ImportError deterministically, whether or not the extra is installed.
    monkeypatch.setitem(sys.modules, "psycopg", None)
    with pytest.raises(HostedDbError, match=r"cortex\[hosted\]"):
        connect(RAILWAY_URL)


def test_connect_validates_the_url_before_touching_the_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bad URLs fail as bad URLs even when the driver is absent."""

    monkeypatch.setitem(sys.modules, "psycopg", None)
    with pytest.raises(HostedDbError, match="scheme"):
        connect("mysql://user:pass@host:3306/db")
