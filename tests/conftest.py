"""Pytest fixtures for EMS MCP Server tests."""

import os
from datetime import datetime, timedelta, timezone
from typing import Generator
from unittest.mock import patch

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.credentials import SimpleCredential
from keyring.errors import PasswordDeleteError

from ems_mcp.api.auth import TokenManager
from ems_mcp.api.client import EMSClient
from ems_mcp.api.models import CachedToken
from ems_mcp.config import EMSSettings


class InMemoryKeyring(KeyringBackend):
    """Keyring backend that keeps credentials in a dict, for tests."""

    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if self.store.pop((service, username), None) is None:
            raise PasswordDeleteError("not found")

    def get_credential(self, service: str, username: str | None) -> SimpleCredential | None:
        for (svc, user), password in self.store.items():
            if svc == service and (username is None or user == username):
                return SimpleCredential(user, password)
        return None


@pytest.fixture(autouse=True)
def fake_keyring() -> Generator[InMemoryKeyring, None, None]:
    """Replace the OS credential store so tests never touch real credentials."""
    original = keyring.get_keyring()
    backend = InMemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(original)


@pytest.fixture
def mock_env_vars() -> Generator[dict[str, str], None, None]:
    """Set up mock environment variables for testing."""
    env_vars = {
        "EMS_BASE_URL": "https://test-ems.example.com",
        "EMS_USERNAME": "testuser",
        "EMS_PASSWORD": "testpass",
        "EMS_LOG_LEVEL": "DEBUG",
    }
    with patch.dict(os.environ, env_vars, clear=False):
        yield env_vars


@pytest.fixture
def mock_settings(mock_env_vars: dict[str, str]) -> EMSSettings:
    """Create mock settings for testing."""
    # Clear the lru_cache before creating new settings
    from ems_mcp.config import get_settings

    get_settings.cache_clear()

    return EMSSettings(
        base_url=mock_env_vars["EMS_BASE_URL"],
        username=mock_env_vars["EMS_USERNAME"],
        password=mock_env_vars["EMS_PASSWORD"],
        log_level=mock_env_vars["EMS_LOG_LEVEL"],
    )


@pytest.fixture
def clear_singletons() -> Generator[None, None, None]:
    """Clear singleton instances before and after tests."""
    # Clear before test
    TokenManager.reset_instance()
    EMSClient.clear_instance()

    yield

    # Clear after test
    TokenManager.reset_instance()
    EMSClient.clear_instance()

    # Also clear settings cache
    from ems_mcp.config import get_settings

    get_settings.cache_clear()


@pytest.fixture
def mock_token() -> CachedToken:
    """Create a mock cached token for testing."""
    return CachedToken(
        access_token="mock_access_token_12345",
        token_type="bearer",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        base_url="https://test-ems.example.com",
    )


@pytest.fixture
def expired_token() -> CachedToken:
    """Create an expired mock token for testing."""
    return CachedToken(
        access_token="expired_token",
        token_type="bearer",
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        base_url="https://test-ems.example.com",
    )


@pytest.fixture
def token_manager(mock_settings: EMSSettings) -> TokenManager:
    """Create a TokenManager instance for testing."""
    return TokenManager(settings=mock_settings)


@pytest.fixture
def ems_client(mock_settings: EMSSettings) -> EMSClient:
    """Create an EMSClient instance for testing."""
    return EMSClient(settings=mock_settings)
