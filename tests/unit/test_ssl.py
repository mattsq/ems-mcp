"""Tests for SSL/TLS verification helper (ems_mcp.api._ssl)."""

from __future__ import annotations

import logging
import ssl
from pathlib import Path

import pytest

from ems_mcp.api._ssl import get_verify_setting


class TestGetVerifySetting:
    """Tests for get_verify_setting()."""

    def test_returns_true_when_no_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should return True (default trust store) when no CA env vars are set."""
        monkeypatch.delenv("EMS_CA_BUNDLE", raising=False)
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)

        result = get_verify_setting()
        assert result is True

    def test_returns_ssl_context_when_ems_ca_bundle_exists(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        """Should return SSLContext when EMS_CA_BUNDLE points to a real file."""
        # Create a dummy CA file (doesn't need to be a real cert for this test
        # since we mock the ssl.create_default_context call indirectly).
        ca_file = tmp_path / "ca-bundle.crt"
        ca_file.write_text("dummy")

        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
        monkeypatch.setenv("EMS_CA_BUNDLE", str(ca_file))

        # Patch ssl.create_default_context to avoid needing real certs
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        monkeypatch.setattr(ssl, "create_default_context", lambda cafile: ctx)

        result = get_verify_setting()
        assert result is ctx

    def test_raises_when_ems_ca_bundle_missing(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should raise FileNotFoundError when EMS_CA_BUNDLE path doesn't exist."""
        monkeypatch.setenv("EMS_CA_BUNDLE", "/nonexistent/ca-bundle.crt")
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)

        with pytest.raises(FileNotFoundError, match="EMS_CA_BUNDLE"):
            get_verify_setting()

    def test_warns_and_skips_when_ssl_cert_file_missing(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Should log warning and fall back when SSL_CERT_FILE doesn't exist."""
        monkeypatch.delenv("EMS_CA_BUNDLE", raising=False)
        monkeypatch.setenv("SSL_CERT_FILE", "/nonexistent/cert.pem")
        monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)

        with caplog.at_level(logging.WARNING, logger="ems_mcp.api._ssl"):
            result = get_verify_setting()

        assert result is True
        assert "SSL_CERT_FILE" in caplog.text
        assert "does not exist" in caplog.text

    def test_warns_and_skips_when_requests_ca_bundle_missing(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Should log warning and fall back when REQUESTS_CA_BUNDLE doesn't exist."""
        monkeypatch.delenv("EMS_CA_BUNDLE", raising=False)
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/nonexistent/bundle.pem")

        with caplog.at_level(logging.WARNING, logger="ems_mcp.api._ssl"):
            result = get_verify_setting()

        assert result is True
        assert "REQUESTS_CA_BUNDLE" in caplog.text

    def test_falls_through_to_valid_var(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Should skip missing SSL_CERT_FILE and use valid REQUESTS_CA_BUNDLE."""
        ca_file = tmp_path / "bundle.crt"
        ca_file.write_text("dummy")

        monkeypatch.delenv("EMS_CA_BUNDLE", raising=False)
        monkeypatch.setenv("SSL_CERT_FILE", "/nonexistent/cert.pem")
        monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(ca_file))

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        monkeypatch.setattr(ssl, "create_default_context", lambda cafile: ctx)

        with caplog.at_level(logging.WARNING, logger="ems_mcp.api._ssl"):
            result = get_verify_setting()

        assert result is ctx
        assert "SSL_CERT_FILE" in caplog.text

    def test_ems_ca_bundle_takes_priority(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        """EMS_CA_BUNDLE should be checked before other env vars."""
        ems_ca = tmp_path / "ems.crt"
        ems_ca.write_text("dummy")
        other_ca = tmp_path / "other.crt"
        other_ca.write_text("dummy")

        monkeypatch.setenv("EMS_CA_BUNDLE", str(ems_ca))
        monkeypatch.setenv("SSL_CERT_FILE", str(other_ca))

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        calls: list[str] = []

        def mock_create(cafile: str) -> ssl.SSLContext:
            calls.append(cafile)
            return ctx

        monkeypatch.setattr(ssl, "create_default_context", mock_create)

        result = get_verify_setting()
        assert result is ctx
        assert calls == [str(ems_ca)]
