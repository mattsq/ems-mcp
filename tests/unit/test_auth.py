"""Unit tests for authentication and token management."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from ems_mcp.api.auth import AuthenticationError, TokenManager
from ems_mcp.api.models import CachedToken
from ems_mcp.config import EMSSettings


class TestCachedToken:
    """Tests for CachedToken model."""

    def test_is_valid_with_fresh_token(self) -> None:
        """Fresh token should be valid."""
        token = CachedToken(
            access_token="test",
            token_type="bearer",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            base_url="https://example.com",
        )
        assert token.is_valid()

    def test_is_valid_respects_buffer(self) -> None:
        """Token expiring within buffer should be invalid."""
        # Token expires in 30 seconds, buffer is 60 seconds
        token = CachedToken(
            access_token="test",
            token_type="bearer",
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
            base_url="https://example.com",
        )
        assert not token.is_valid(buffer_seconds=60)

    def test_is_valid_with_custom_buffer(self) -> None:
        """Token should respect custom buffer value."""
        # Token expires in 30 seconds
        token = CachedToken(
            access_token="test",
            token_type="bearer",
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
            base_url="https://example.com",
        )
        # Should be valid with 10 second buffer
        assert token.is_valid(buffer_seconds=10)
        # Should be invalid with 60 second buffer
        assert not token.is_valid(buffer_seconds=60)

    def test_is_valid_with_expired_token(self) -> None:
        """Expired token should be invalid."""
        token = CachedToken(
            access_token="test",
            token_type="bearer",
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
            base_url="https://example.com",
        )
        assert not token.is_valid()


class TestTokenManager:
    """Tests for TokenManager class."""

    @pytest.fixture
    def settings(self) -> EMSSettings:
        """Create test settings."""
        return EMSSettings(
            base_url="https://test-ems.example.com",
            username="testuser",
            password="testpass",
        )

    @pytest.fixture
    def token_manager(self, settings: EMSSettings) -> TokenManager:
        """Create a TokenManager for testing."""
        TokenManager.reset_instance()
        return TokenManager(settings=settings)

    @pytest.mark.asyncio
    async def test_get_instance_returns_singleton(
        self, settings: EMSSettings
    ) -> None:
        """get_instance should return the same instance."""
        TokenManager.reset_instance()
        # Pre-create instance with settings to avoid env var lookup
        TokenManager._instance = TokenManager(settings=settings)
        instance1 = await TokenManager.get_instance()
        instance2 = await TokenManager.get_instance()
        assert instance1 is instance2
        TokenManager.reset_instance()

    @pytest.mark.asyncio
    async def test_reset_instance_clears_singleton(
        self, settings: EMSSettings
    ) -> None:
        """reset_instance should clear the singleton."""
        TokenManager.reset_instance()
        TokenManager._instance = TokenManager(settings=settings)
        instance1 = await TokenManager.get_instance()
        TokenManager.reset_instance()
        TokenManager._instance = TokenManager(settings=settings)
        instance2 = await TokenManager.get_instance()
        assert instance1 is not instance2
        TokenManager.reset_instance()

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_token_requests_new_token(
        self, token_manager: TokenManager
    ) -> None:
        """get_token should request a new token when cache is empty."""
        respx.post("https://test-ems.example.com/api/token").mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "new_token_123",
                    "token_type": "bearer",
                    "expires_in": 1799,
                },
            )
        )

        token = await token_manager.get_token()
        assert token == "new_token_123"

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_token_uses_cached_token(
        self, token_manager: TokenManager
    ) -> None:
        """get_token should return cached token if valid."""
        # First request gets a new token
        respx.post("https://test-ems.example.com/api/token").mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "cached_token",
                    "token_type": "bearer",
                    "expires_in": 1799,
                },
            )
        )

        token1 = await token_manager.get_token()
        token2 = await token_manager.get_token()

        assert token1 == token2 == "cached_token"
        # Should only have made one request
        assert len(respx.calls) == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_token_refreshes_expired_token(
        self, token_manager: TokenManager
    ) -> None:
        """get_token should refresh an expired token."""
        # Manually set an expired token
        token_manager._token = CachedToken(
            access_token="expired_token",
            token_type="bearer",
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
            base_url="https://test-ems.example.com",
        )

        respx.post("https://test-ems.example.com/api/token").mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "new_token",
                    "token_type": "bearer",
                    "expires_in": 1799,
                },
            )
        )

        token = await token_manager.get_token()
        assert token == "new_token"

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_token_handles_invalid_credentials(
        self, token_manager: TokenManager
    ) -> None:
        """get_token should raise AuthenticationError on invalid credentials."""
        respx.post("https://test-ems.example.com/api/token").mock(
            return_value=httpx.Response(
                400,
                json={
                    "error": "invalid_grant",
                    "error_description": "The user name or password is incorrect.",
                },
            )
        )

        with pytest.raises(AuthenticationError) as exc_info:
            await token_manager.get_token()

        assert "incorrect" in str(exc_info.value).lower()
        assert exc_info.value.error_code == "invalid_grant"

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_token_handles_401_with_oauth_body(
        self, token_manager: TokenManager
    ) -> None:
        """A 401 from the token endpoint with an OAuth error body should
        raise AuthenticationError carrying the parsed message and code,
        not fall through to the generic "Unexpected response" path."""
        respx.post("https://test-ems.example.com/api/token").mock(
            return_value=httpx.Response(
                401,
                json={
                    "error": "invalid_client",
                    "error_description": "Client authentication failed.",
                },
            )
        )

        with pytest.raises(AuthenticationError) as exc_info:
            await token_manager.get_token()

        assert "client authentication failed" in str(exc_info.value).lower()
        assert exc_info.value.error_code == "invalid_client"

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_token_handles_401_without_oauth_body(
        self, token_manager: TokenManager
    ) -> None:
        """A 401 with a non-OAuth body still raises AuthenticationError and
        includes the status code in the message so deployments returning
        plain text get a useful error."""
        respx.post("https://test-ems.example.com/api/token").mock(
            return_value=httpx.Response(401, text="Unauthorized")
        )

        with pytest.raises(AuthenticationError) as exc_info:
            await token_manager.get_token()

        msg = str(exc_info.value)
        assert "401" in msg
        assert "Unauthorized" in msg

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_token_handles_network_error(
        self, token_manager: TokenManager
    ) -> None:
        """get_token should raise AuthenticationError on network failure."""
        respx.post("https://test-ems.example.com/api/token").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        with pytest.raises(AuthenticationError) as exc_info:
            await token_manager.get_token()

        assert "network error" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_clear_token(self, token_manager: TokenManager) -> None:
        """clear_token should remove cached token."""
        token_manager._token = CachedToken(
            access_token="test",
            token_type="bearer",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            base_url="https://test-ems.example.com",
        )

        await token_manager.clear_token()
        assert token_manager._token is None

    @pytest.mark.asyncio
    async def test_clear_token_skips_when_replaced(
        self, token_manager: TokenManager
    ) -> None:
        """clear_token(expected_access_token=...) is a no-op when the cached
        token was already replaced.

        Prevents the race where a concurrent request 401s on stale token T1
        while another request has already refreshed the cache to T2 -- we
        must not wipe T2 just because T1 expired.
        """
        token_manager._token = CachedToken(
            access_token="T2-fresh",
            token_type="bearer",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            base_url="https://test-ems.example.com",
        )

        await token_manager.clear_token(expected_access_token="T1-stale")
        assert token_manager._token is not None
        assert token_manager._token.access_token == "T2-fresh"

        # When the expected token matches the cached token, clear proceeds.
        await token_manager.clear_token(expected_access_token="T2-fresh")
        assert token_manager._token is None

    @pytest.mark.asyncio
    async def test_clear_token_serialised_with_refresh(
        self, token_manager: TokenManager
    ) -> None:
        """clear_token must take the same lock as get_token so an in-flight
        refresh cannot overwrite the cache after the compare-and-clear's
        equality check succeeds.
        """
        # Pre-load a stale token so clear_token would otherwise wipe it.
        stale = CachedToken(
            access_token="T1-stale",
            token_type="bearer",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            base_url="https://test-ems.example.com",
        )
        token_manager._token = stale

        # Hold the token lock to simulate get_token() awaiting _request_token.
        # clear_token must block on this lock instead of racing past it.
        async with token_manager._token_lock:
            cleared_task = asyncio.create_task(
                token_manager.clear_token(expected_access_token="T1-stale")
            )
            # Give the task a chance to run; it must be parked on the lock.
            await asyncio.sleep(0)
            assert not cleared_task.done()
            assert token_manager._token is stale
        # Lock released -- clear can now proceed.
        await cleared_task
        assert token_manager._token is None

    def test_get_auth_headers(self, token_manager: TokenManager) -> None:
        """get_auth_headers should return standard headers."""
        headers = token_manager.get_auth_headers()

        assert "X-Adi-Application-Name" in headers
        assert headers["X-Adi-Application-Name"] == "ems-mcp"
        assert "User-Agent" in headers
        assert "ems-api-sdk" in headers["User-Agent"]
        assert "Accept" in headers
        assert headers["Accept"] == "application/json"

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_token_refreshes_on_base_url_change(
        self, settings: EMSSettings
    ) -> None:
        """Token should be refreshed if base URL changes."""
        token_manager = TokenManager(settings=settings)

        # Set a token for different base URL
        token_manager._token = CachedToken(
            access_token="old_token",
            token_type="bearer",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            base_url="https://different-ems.example.com",
        )

        respx.post("https://test-ems.example.com/api/token").mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "new_token",
                    "token_type": "bearer",
                    "expires_in": 1799,
                },
            )
        )

        token = await token_manager.get_token()
        assert token == "new_token"
