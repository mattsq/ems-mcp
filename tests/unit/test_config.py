"""Unit tests for configuration management."""

import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from ems_mcp.config import EMSSettings, get_settings


class TestEMSSettings:
    """Tests for EMSSettings configuration class."""

    def test_loads_from_env_vars(self) -> None:
        """Settings should load from environment variables."""
        env = {
            "EMS_BASE_URL": "https://ems.example.com",
            "EMS_USERNAME": "user",
            "EMS_PASSWORD": "pass",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = EMSSettings()  # type: ignore[call-arg]
            assert settings.base_url == "https://ems.example.com"
            assert settings.username == "user"
            assert settings.password.get_secret_value() == "pass"

    def test_required_fields_missing(self) -> None:
        """Should raise error when required fields are missing."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValidationError) as exc_info:
                EMSSettings()  # type: ignore[call-arg]
            errors = exc_info.value.errors()
            # Should have errors for base_url, username, password
            missing_fields = {e["loc"][0] for e in errors}
            assert "base_url" in missing_fields
            assert "username" in missing_fields
            assert "password" in missing_fields

    def test_default_values(self) -> None:
        """Optional settings should have correct defaults."""
        env = {
            "EMS_BASE_URL": "https://ems.example.com",
            "EMS_USERNAME": "user",
            "EMS_PASSWORD": "pass",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = EMSSettings()  # type: ignore[call-arg]
            assert settings.default_system is None
            assert settings.cache_ttl == 3600
            assert settings.request_timeout == 120
            assert settings.log_level == "INFO"
            assert settings.max_retries == 3

    def test_base_url_removes_trailing_slash(self) -> None:
        """Base URL should have trailing slash removed."""
        env = {
            "EMS_BASE_URL": "https://ems.example.com/",
            "EMS_USERNAME": "user",
            "EMS_PASSWORD": "pass",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = EMSSettings()  # type: ignore[call-arg]
            assert settings.base_url == "https://ems.example.com"

    def test_base_url_upgrades_http_to_https(self) -> None:
        """Base URL should upgrade HTTP to HTTPS."""
        env = {
            "EMS_BASE_URL": "http://ems.example.com",
            "EMS_USERNAME": "user",
            "EMS_PASSWORD": "pass",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = EMSSettings()  # type: ignore[call-arg]
            assert settings.base_url == "https://ems.example.com"

    def test_base_url_normalization_combined(self) -> None:
        """Base URL normalization should strip /api suffix, trailing slash, and upgrade HTTP."""
        env = {
            "EMS_BASE_URL": "http://ems.example.com/api/",
            "EMS_USERNAME": "user",
            "EMS_PASSWORD": "pass",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = EMSSettings()  # type: ignore[call-arg]
            assert settings.base_url == "https://ems.example.com"

    def test_log_level_validation(self) -> None:
        """Log level should be validated against known levels."""
        env = {
            "EMS_BASE_URL": "https://ems.example.com",
            "EMS_USERNAME": "user",
            "EMS_PASSWORD": "pass",
            "EMS_LOG_LEVEL": "INVALID",
        }
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValidationError) as exc_info:
                EMSSettings()  # type: ignore[call-arg]
            assert "log_level" in str(exc_info.value)

    def test_log_level_case_insensitive(self) -> None:
        """Log level should be case-insensitive."""
        env = {
            "EMS_BASE_URL": "https://ems.example.com",
            "EMS_USERNAME": "user",
            "EMS_PASSWORD": "pass",
            "EMS_LOG_LEVEL": "debug",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = EMSSettings()  # type: ignore[call-arg]
            assert settings.log_level == "DEBUG"

    def test_optional_settings_override(self) -> None:
        """Optional settings should be overridable via env vars."""
        env = {
            "EMS_BASE_URL": "https://ems.example.com",
            "EMS_USERNAME": "user",
            "EMS_PASSWORD": "pass",
            "EMS_DEFAULT_SYSTEM": "5",
            "EMS_CACHE_TTL": "7200",
            "EMS_REQUEST_TIMEOUT": "60",
            "EMS_MAX_RETRIES": "5",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = EMSSettings()  # type: ignore[call-arg]
            assert settings.default_system == 5
            assert settings.cache_ttl == 7200
            assert settings.request_timeout == 60
            assert settings.max_retries == 5

    def test_password_is_secret(self) -> None:
        """Password should be a SecretStr for security."""
        env = {
            "EMS_BASE_URL": "https://ems.example.com",
            "EMS_USERNAME": "user",
            "EMS_PASSWORD": "secret123",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = EMSSettings()  # type: ignore[call-arg]
            # String representation should not reveal password
            assert "secret123" not in str(settings.password)
            # But we can still get the actual value when needed
            assert settings.password.get_secret_value() == "secret123"


class TestGetSettings:
    """Tests for get_settings() singleton function."""

    def test_returns_same_instance(self) -> None:
        """get_settings should return the same instance (cached)."""
        env = {
            "EMS_BASE_URL": "https://ems.example.com",
            "EMS_USERNAME": "user",
            "EMS_PASSWORD": "pass",
        }
        with patch.dict(os.environ, env, clear=True):
            get_settings.cache_clear()  # Clear any existing cache
            settings1 = get_settings()
            settings2 = get_settings()
            assert settings1 is settings2

    def test_cache_can_be_cleared(self) -> None:
        """get_settings cache should be clearable for testing."""
        env1 = {
            "EMS_BASE_URL": "https://ems1.example.com",
            "EMS_USERNAME": "user1",
            "EMS_PASSWORD": "pass1",
        }
        env2 = {
            "EMS_BASE_URL": "https://ems2.example.com",
            "EMS_USERNAME": "user2",
            "EMS_PASSWORD": "pass2",
        }

        with patch.dict(os.environ, env1, clear=True):
            get_settings.cache_clear()
            settings1 = get_settings()
            assert settings1.base_url == "https://ems1.example.com"

        with patch.dict(os.environ, env2, clear=True):
            get_settings.cache_clear()
            settings2 = get_settings()
            assert settings2.base_url == "https://ems2.example.com"
