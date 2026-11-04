"""Unit tests for OS credential store integration."""

import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from ems_mcp.config import EMSSettings
from ems_mcp.credentials import (
    delete_credentials,
    load_credentials,
    store_credentials,
)


class TestCredentialStore:
    """Tests for credential store helpers."""

    def test_store_and_load(self) -> None:
        store_credentials("user", "secret")
        assert load_credentials() == ("user", "secret")

    def test_load_missing_returns_none(self) -> None:
        assert load_credentials() is None

    def test_load_by_username(self) -> None:
        store_credentials("user", "secret")
        assert load_credentials(username="user") == ("user", "secret")
        assert load_credentials(username="other") is None

    def test_store_replaces_existing(self, fake_keyring) -> None:  # type: ignore[no-untyped-def]
        store_credentials("old", "pw1")
        store_credentials("new", "pw2")
        assert load_credentials() == ("new", "pw2")
        assert len(fake_keyring.store) == 1

    def test_custom_service(self) -> None:
        store_credentials("user", "secret", service="ems-other")
        assert load_credentials() is None
        assert load_credentials("ems-other") == ("user", "secret")

    def test_delete(self) -> None:
        store_credentials("user", "secret")
        assert delete_credentials() is True
        assert load_credentials() is None
        assert delete_credentials() is False


class TestSettingsFromCredentialStore:
    """Tests for loading EMSSettings credentials from the credential store."""

    def test_loads_credentials_from_store(self) -> None:
        store_credentials("stored_user", "stored_pw")
        with patch.dict(os.environ, {"EMS_BASE_URL": "https://ems.example.com"}, clear=True):
            settings = EMSSettings()  # type: ignore[call-arg]
        assert settings.username == "stored_user"
        assert settings.password.get_secret_value() == "stored_pw"

    def test_env_vars_take_precedence(self) -> None:
        store_credentials("stored_user", "stored_pw")
        env = {
            "EMS_BASE_URL": "https://ems.example.com",
            "EMS_USERNAME": "env_user",
            "EMS_PASSWORD": "env_pw",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = EMSSettings()  # type: ignore[call-arg]
        assert settings.username == "env_user"
        assert settings.password.get_secret_value() == "env_pw"

    def test_env_username_uses_matching_stored_password(self) -> None:
        store_credentials("stored_user", "stored_pw")
        env = {"EMS_BASE_URL": "https://ems.example.com", "EMS_USERNAME": "stored_user"}
        with patch.dict(os.environ, env, clear=True):
            settings = EMSSettings()  # type: ignore[call-arg]
        assert settings.password.get_secret_value() == "stored_pw"

    def test_env_username_does_not_borrow_other_users_password(self) -> None:
        store_credentials("stored_user", "stored_pw")
        env = {"EMS_BASE_URL": "https://ems.example.com", "EMS_USERNAME": "someone_else"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValidationError) as exc_info:
                EMSSettings()  # type: ignore[call-arg]
        assert {e["loc"][0] for e in exc_info.value.errors()} == {"password"}

    def test_keyring_service_from_env(self) -> None:
        store_credentials("user", "pw", service="ems-test-system")
        env = {
            "EMS_BASE_URL": "https://ems.example.com",
            "EMS_KEYRING_SERVICE": "ems-test-system",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = EMSSettings()  # type: ignore[call-arg]
        assert settings.username == "user"
        assert settings.keyring_service == "ems-test-system"
