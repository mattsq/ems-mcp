"""Unit tests for EMS API HTTP client."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from ems_mcp.api.auth import AuthenticationError, TokenManager
from ems_mcp.api.client import (
    EMSAPIError,
    EMSAuthorizationError,
    EMSClient,
    EMSNotFoundError,
    EMSRateLimitError,
    EMSServerError,
)
from ems_mcp.api.models import CachedToken, RetryConfig
from ems_mcp.config import EMSSettings


class TestRetryConfig:
    """Tests for RetryConfig model."""

    def test_default_values(self) -> None:
        """RetryConfig should have sensible defaults."""
        config = RetryConfig()
        assert config.max_retries == 3
        assert config.base_delay == 1.0
        assert config.max_delay == 30.0
        assert config.exponential_base == 2.0
        assert config.jitter is True

    def test_get_delay_exponential(self) -> None:
        """get_delay should implement exponential backoff."""
        config = RetryConfig(jitter=False)  # Disable jitter for predictable tests

        assert config.get_delay(0) == 1.0  # base_delay * 2^0 = 1.0
        assert config.get_delay(1) == 2.0  # base_delay * 2^1 = 2.0
        assert config.get_delay(2) == 4.0  # base_delay * 2^2 = 4.0

    def test_get_delay_respects_max(self) -> None:
        """get_delay should cap at max_delay."""
        config = RetryConfig(max_delay=5.0, jitter=False)

        assert config.get_delay(0) == 1.0
        assert config.get_delay(10) == 5.0  # Capped at max_delay

    def test_get_delay_with_jitter(self) -> None:
        """get_delay with jitter should return values in expected range."""
        config = RetryConfig(jitter=True)

        # With jitter, delay should be between 0.5x and 1.5x the base delay
        delays = [config.get_delay(0) for _ in range(100)]
        assert all(0.5 <= d <= 1.5 for d in delays)


class TestEMSClient:
    """Tests for EMSClient class."""

    @pytest.fixture
    def settings(self) -> EMSSettings:
        """Create test settings."""
        return EMSSettings(
            base_url="https://test-ems.example.com",
            username="testuser",
            password="testpass",
            request_timeout=30,
            max_retries=3,
        )

    @pytest.fixture
    def mock_token_manager(self) -> AsyncMock:
        """Create a mock token manager."""
        manager = AsyncMock(spec=TokenManager)
        manager.get_token = AsyncMock(return_value="mock_token")
        manager.get_auth_headers = MagicMock(
            return_value={
                "X-Adi-Application-Name": "ems-mcp",
                "User-Agent": "test",
                "Accept": "application/json",
            }
        )
        manager.clear_token = MagicMock()
        return manager

    @pytest.mark.asyncio
    async def test_create_context_manager(self, settings: EMSSettings) -> None:
        """EMSClient.create() should work as async context manager."""
        with patch.object(TokenManager, "get_instance", new_callable=AsyncMock) as mock:
            mock.return_value = MagicMock(spec=TokenManager)

            async with EMSClient.create(settings=settings) as client:
                assert client is not None
                assert client._http_client is not None

            # Client should be cleaned up after context
            assert client._http_client is None

    def test_get_instance_raises_without_set(self) -> None:
        """get_instance should raise if not set."""
        EMSClient.clear_instance()
        with pytest.raises(RuntimeError, match="instance not set"):
            EMSClient.get_instance()

    def test_set_and_get_instance(self, settings: EMSSettings) -> None:
        """set_instance and get_instance should work together."""
        EMSClient.clear_instance()
        client = EMSClient(settings=settings)
        EMSClient.set_instance(client)

        assert EMSClient.get_instance() is client
        EMSClient.clear_instance()

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_request_success(
        self, settings: EMSSettings, mock_token_manager: AsyncMock
    ) -> None:
        """GET request should return parsed JSON on success."""
        respx.get("https://test-ems.example.com/api/v2/ems-systems").mock(
            return_value=httpx.Response(
                200, json=[{"id": 1, "name": "Test System"}]
            )
        )

        client = EMSClient(settings=settings, token_manager=mock_token_manager)
        client._http_client = httpx.AsyncClient()

        try:
            result = await client.get("/api/v2/ems-systems")
            assert result == [{"id": 1, "name": "Test System"}]
        finally:
            await client._http_client.aclose()

    @pytest.mark.asyncio
    @respx.mock
    async def test_post_request_success(
        self, settings: EMSSettings, mock_token_manager: AsyncMock
    ) -> None:
        """POST request should send JSON and return parsed response."""
        respx.post("https://test-ems.example.com/api/v2/query").mock(
            return_value=httpx.Response(200, json={"rows": []})
        )

        client = EMSClient(settings=settings, token_manager=mock_token_manager)
        client._http_client = httpx.AsyncClient()

        try:
            result = await client.post("/api/v2/query", json={"select": []})
            assert result == {"rows": []}
        finally:
            await client._http_client.aclose()

    @pytest.mark.asyncio
    @respx.mock
    async def test_handles_401_with_retry(
        self, settings: EMSSettings, mock_token_manager: AsyncMock
    ) -> None:
        """Client should clear token and retry once on 401."""
        call_count = 0

        def response_callback(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(401, json={"message": "Unauthorized"})
            return httpx.Response(200, json={"data": "success"})

        respx.get("https://test-ems.example.com/api/test").mock(
            side_effect=response_callback
        )

        client = EMSClient(settings=settings, token_manager=mock_token_manager)
        client._http_client = httpx.AsyncClient()

        try:
            result = await client.get("/api/test")
            assert result == {"data": "success"}
            mock_token_manager.clear_token.assert_called_once()
        finally:
            await client._http_client.aclose()

    @pytest.mark.asyncio
    @respx.mock
    async def test_raises_auth_error_on_repeated_401(
        self, settings: EMSSettings, mock_token_manager: AsyncMock
    ) -> None:
        """Client should raise AuthenticationError if 401 persists after retry."""
        respx.get("https://test-ems.example.com/api/test").mock(
            return_value=httpx.Response(401, json={"message": "Unauthorized"})
        )

        client = EMSClient(settings=settings, token_manager=mock_token_manager)
        client._http_client = httpx.AsyncClient()

        try:
            with pytest.raises(AuthenticationError):
                await client.get("/api/test")
        finally:
            await client._http_client.aclose()

    @pytest.mark.asyncio
    @respx.mock
    async def test_raises_not_found_on_404(
        self, settings: EMSSettings, mock_token_manager: AsyncMock
    ) -> None:
        """Client should raise EMSNotFoundError on 404."""
        respx.get("https://test-ems.example.com/api/missing").mock(
            return_value=httpx.Response(
                404, json={"message": "Resource not found"}
            )
        )

        client = EMSClient(settings=settings, token_manager=mock_token_manager)
        client._http_client = httpx.AsyncClient()

        try:
            with pytest.raises(EMSNotFoundError) as exc_info:
                await client.get("/api/missing")
            assert exc_info.value.status_code == 404
        finally:
            await client._http_client.aclose()

    @pytest.mark.asyncio
    @respx.mock
    async def test_raises_authorization_error_on_403(
        self, settings: EMSSettings, mock_token_manager: AsyncMock
    ) -> None:
        """Client should raise EMSAuthorizationError on 403."""
        respx.get("https://test-ems.example.com/api/forbidden").mock(
            return_value=httpx.Response(403, json={"message": "Access denied"})
        )

        client = EMSClient(settings=settings, token_manager=mock_token_manager)
        client._http_client = httpx.AsyncClient()

        try:
            with pytest.raises(EMSAuthorizationError) as exc_info:
                await client.get("/api/forbidden")
            assert exc_info.value.status_code == 403
        finally:
            await client._http_client.aclose()

    @pytest.mark.asyncio
    @respx.mock
    async def test_retries_on_429_with_retry_after(
        self, settings: EMSSettings, mock_token_manager: AsyncMock
    ) -> None:
        """Client should retry on 429 respecting Retry-After header."""
        call_count = 0

        def response_callback(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(
                    429,
                    json={"message": "Rate limited"},
                    headers={"Retry-After": "1"},
                )
            return httpx.Response(200, json={"data": "success"})

        respx.get("https://test-ems.example.com/api/test").mock(
            side_effect=response_callback
        )

        # Use minimal retry config for faster tests
        retry_config = RetryConfig(max_retries=3, base_delay=0.1, jitter=False)
        client = EMSClient(
            settings=settings,
            token_manager=mock_token_manager,
            retry_config=retry_config,
        )
        client._http_client = httpx.AsyncClient()

        try:
            result = await client.get("/api/test")
            assert result == {"data": "success"}
            assert call_count == 2
        finally:
            await client._http_client.aclose()

    @pytest.mark.asyncio
    @respx.mock
    async def test_retries_on_500_error(
        self, settings: EMSSettings, mock_token_manager: AsyncMock
    ) -> None:
        """Client should retry on 5xx server errors."""
        call_count = 0

        def response_callback(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return httpx.Response(
                    500, json={"message": "Server error", "unexpected": True}
                )
            return httpx.Response(200, json={"data": "success"})

        respx.get("https://test-ems.example.com/api/test").mock(
            side_effect=response_callback
        )

        retry_config = RetryConfig(max_retries=3, base_delay=0.01, jitter=False)
        client = EMSClient(
            settings=settings,
            token_manager=mock_token_manager,
            retry_config=retry_config,
        )
        client._http_client = httpx.AsyncClient()

        try:
            result = await client.get("/api/test")
            assert result == {"data": "success"}
            assert call_count == 3
        finally:
            await client._http_client.aclose()

    @pytest.mark.asyncio
    @respx.mock
    async def test_raises_after_max_retries(
        self, settings: EMSSettings, mock_token_manager: AsyncMock
    ) -> None:
        """Client should raise error after exhausting retries."""
        respx.get("https://test-ems.example.com/api/test").mock(
            return_value=httpx.Response(500, json={"message": "Server error"})
        )

        retry_config = RetryConfig(max_retries=2, base_delay=0.01, jitter=False)
        client = EMSClient(
            settings=settings,
            token_manager=mock_token_manager,
            retry_config=retry_config,
        )
        client._http_client = httpx.AsyncClient()

        try:
            with pytest.raises(EMSServerError):
                await client.get("/api/test")
        finally:
            await client._http_client.aclose()

    @pytest.mark.asyncio
    @respx.mock
    async def test_handles_empty_response(
        self, settings: EMSSettings, mock_token_manager: AsyncMock
    ) -> None:
        """Client should handle empty response body."""
        respx.delete("https://test-ems.example.com/api/resource").mock(
            return_value=httpx.Response(204, content=b"")
        )

        client = EMSClient(settings=settings, token_manager=mock_token_manager)
        client._http_client = httpx.AsyncClient()

        try:
            result = await client._request("DELETE", "/api/resource")
            assert result is None
        finally:
            await client._http_client.aclose()

    @pytest.mark.asyncio
    async def test_raises_without_initialization(
        self, settings: EMSSettings
    ) -> None:
        """Client should raise if used without initialization."""
        client = EMSClient(settings=settings)

        with pytest.raises(RuntimeError, match="not initialized"):
            await client.get("/api/test")


class TestExceptionHierarchy:
    """Tests for exception classes."""

    def test_ems_api_error_base(self) -> None:
        """EMSAPIError should be the base exception."""
        error = EMSAPIError("Test error", status_code=400)
        assert error.message == "Test error"
        assert error.status_code == 400
        assert str(error) == "Test error"

    def test_not_found_error(self) -> None:
        """EMSNotFoundError should inherit from EMSAPIError."""
        error = EMSNotFoundError("Resource not found", status_code=404)
        assert isinstance(error, EMSAPIError)
        assert error.status_code == 404

    def test_authorization_error(self) -> None:
        """EMSAuthorizationError should inherit from EMSAPIError."""
        error = EMSAuthorizationError("Access denied", status_code=403)
        assert isinstance(error, EMSAPIError)
        assert error.status_code == 403

    def test_rate_limit_error(self) -> None:
        """EMSRateLimitError should have retry_after attribute."""
        error = EMSRateLimitError("Rate limited", retry_after=60)
        assert isinstance(error, EMSAPIError)
        assert error.status_code == 429
        assert error.retry_after == 60

    def test_server_error(self) -> None:
        """EMSServerError should inherit from EMSAPIError."""
        error = EMSServerError("Internal error", status_code=500)
        assert isinstance(error, EMSAPIError)
        assert error.status_code == 500
