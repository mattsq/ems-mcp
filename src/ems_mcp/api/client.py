"""HTTP client for EMS API with retry logic and error handling.

Provides a high-level async client for making requests to the EMS API
with automatic authentication, retries, and error handling.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from ems_mcp.api.auth import AuthenticationError, TokenManager
from ems_mcp.api.models import EMSErrorResponse, RetryConfig
from ems_mcp.config import EMSSettings, get_settings

logger = logging.getLogger(__name__)


# Exception hierarchy
class EMSAPIError(Exception):
    """Base exception for EMS API errors."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class EMSNotFoundError(EMSAPIError):
    """Raised when a requested resource is not found (404)."""

    pass


class EMSAuthorizationError(EMSAPIError):
    """Raised when access is denied (403)."""

    pass


class EMSRateLimitError(EMSAPIError):
    """Raised when rate limited (429)."""

    def __init__(self, message: str, retry_after: int | None = None):
        super().__init__(message, status_code=429)
        self.retry_after = retry_after


class EMSServerError(EMSAPIError):
    """Raised for server errors (5xx)."""

    pass


class EMSClient:
    """Async HTTP client for the EMS API.

    Provides methods for making authenticated requests with automatic
    token management, retries with exponential backoff, and error handling.

    Usage:
        async with EMSClient.create() as client:
            systems = await client.get("/api/v2/ems-systems")

        # Or for lifespan management:
        EMSClient.set_instance(client)
        client = EMSClient.get_instance()
    """

    _instance: "EMSClient | None" = None

    def __init__(
        self,
        settings: EMSSettings | None = None,
        token_manager: TokenManager | None = None,
        retry_config: RetryConfig | None = None,
    ):
        """Initialize EMSClient.

        Args:
            settings: Optional settings override.
            token_manager: Optional TokenManager override.
            retry_config: Optional retry configuration override.
        """
        self._settings = settings or get_settings()
        self._token_manager = token_manager
        self._retry_config = retry_config or RetryConfig(max_retries=self._settings.max_retries)
        self._http_client: httpx.AsyncClient | None = None

    @classmethod
    @asynccontextmanager
    async def create(
        cls,
        settings: EMSSettings | None = None,
        retry_config: RetryConfig | None = None,
    ) -> AsyncIterator["EMSClient"]:
        """Create an EMSClient as an async context manager.

        Args:
            settings: Optional settings override.
            retry_config: Optional retry configuration.

        Yields:
            Initialized EMSClient ready for use.
        """
        client = cls(settings=settings, retry_config=retry_config)
        await client._initialize()
        try:
            yield client
        finally:
            await client._cleanup()

    async def _initialize(self) -> None:
        """Initialize the HTTP client and token manager."""
        self._token_manager = await TokenManager.get_instance()
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._settings.request_timeout),
            follow_redirects=True,
        )
        logger.debug("EMSClient initialized")

    async def _cleanup(self) -> None:
        """Clean up resources."""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None
        logger.debug("EMSClient cleaned up")

    @classmethod
    def get_instance(cls) -> "EMSClient":
        """Get the singleton client instance.

        Returns:
            The singleton EMSClient instance.

        Raises:
            RuntimeError: If no instance has been set.
        """
        if cls._instance is None:
            raise RuntimeError(
                "EMSClient instance not set. Call set_instance() first or use create() context manager."
            )
        return cls._instance

    @classmethod
    def set_instance(cls, client: "EMSClient") -> None:
        """Set the singleton client instance.

        Args:
            client: The EMSClient instance to use as singleton.
        """
        cls._instance = client

    @classmethod
    def clear_instance(cls) -> None:
        """Clear the singleton instance. Primarily for testing."""
        cls._instance = None

    async def get(self, path: str, **kwargs: Any) -> Any:
        """Make an authenticated GET request.

        Args:
            path: API path (relative to base URL).
            **kwargs: Additional arguments passed to httpx.

        Returns:
            Parsed JSON response.

        Raises:
            EMSAPIError: On API errors.
            AuthenticationError: On authentication failures.
        """
        return await self._request("GET", path, **kwargs)

    async def post(self, path: str, json: Any = None, **kwargs: Any) -> Any:
        """Make an authenticated POST request.

        Args:
            path: API path (relative to base URL).
            json: JSON body to send.
            **kwargs: Additional arguments passed to httpx.

        Returns:
            Parsed JSON response.

        Raises:
            EMSAPIError: On API errors.
            AuthenticationError: On authentication failures.
        """
        return await self._request("POST", path, json=json, **kwargs)

    async def _request(
        self,
        method: str,
        path: str,
        retry_count: int = 0,
        **kwargs: Any,
    ) -> Any:
        """Make an authenticated HTTP request with retry logic.

        Args:
            method: HTTP method.
            path: API path (relative to base URL).
            retry_count: Current retry attempt (internal use).
            **kwargs: Additional arguments passed to httpx.

        Returns:
            Parsed JSON response.

        Raises:
            EMSAPIError: On API errors.
            AuthenticationError: On authentication failures.
        """
        if self._http_client is None or self._token_manager is None:
            raise RuntimeError("Client not initialized. Use create() context manager.")

        url = f"{self._settings.base_url}{path}"

        # Get auth headers with token
        token = await self._token_manager.get_token()
        headers = self._token_manager.get_auth_headers()
        headers["Authorization"] = f"Bearer {token}"

        # Merge with any provided headers
        if "headers" in kwargs:
            headers.update(kwargs.pop("headers"))

        logger.debug("%s %s (attempt %d)", method, path, retry_count + 1)

        try:
            response = await self._http_client.request(method, url, headers=headers, **kwargs)
            return await self._handle_response(response, method, path, retry_count, kwargs)

        except httpx.TimeoutException as e:
            logger.warning("Request timeout for %s %s: %s", method, path, e)
            return await self._handle_retry(
                EMSAPIError(f"Request timeout: {e}"),
                method,
                path,
                retry_count,
                kwargs,
            )

        except httpx.RequestError as e:
            logger.warning("Network error for %s %s: %s", method, path, e)
            return await self._handle_retry(
                EMSAPIError(f"Network error: {e}"),
                method,
                path,
                retry_count,
                kwargs,
            )

    async def _handle_response(
        self,
        response: httpx.Response,
        method: str,
        path: str,
        retry_count: int,
        kwargs: dict[str, Any],
    ) -> Any:
        """Handle HTTP response, including error cases and retries."""
        status = response.status_code

        # Success
        if 200 <= status < 300:
            if not response.content:
                return None
            return response.json()

        # Handle specific error codes
        if status == 401:
            # Token expired or invalid - clear and retry once
            if retry_count == 0 and self._token_manager:
                logger.info("Got 401, clearing token and retrying")
                self._token_manager.clear_token()
                return await self._request(method, path, retry_count=1, **kwargs)
            raise AuthenticationError("Authentication failed after retry")

        if status == 403:
            raise EMSAuthorizationError(
                self._extract_error_message(response, "Access denied"),
                status_code=403,
            )

        if status == 404:
            raise EMSNotFoundError(
                self._extract_error_message(response, "Resource not found"),
                status_code=404,
            )

        if status == 429:
            retry_after = response.headers.get("Retry-After")
            retry_seconds = int(retry_after) if retry_after else None
            rate_limit_error = EMSRateLimitError(
                "Rate limit exceeded",
                retry_after=retry_seconds,
            )
            return await self._handle_retry(rate_limit_error, method, path, retry_count, kwargs)

        if status >= 500:
            server_error = EMSServerError(
                self._extract_error_message(response, f"Server error: {status}"),
                status_code=status,
            )
            return await self._handle_retry(server_error, method, path, retry_count, kwargs)

        # Other client errors - no retry
        raise EMSAPIError(
            self._extract_error_message(response, f"API error: {status}"),
            status_code=status,
        )

    def _extract_error_message(self, response: httpx.Response, default: str) -> str:
        """Extract error message from response body."""
        try:
            data = response.json()
            error = EMSErrorResponse.model_validate(data)
            if error.message_detail:
                return f"{error.message}: {error.message_detail}"
            return error.message
        except Exception:
            return default

    async def _handle_retry(
        self,
        error: EMSAPIError,
        method: str,
        path: str,
        retry_count: int,
        kwargs: dict[str, Any],
    ) -> Any:
        """Handle retry logic with exponential backoff."""
        if retry_count >= self._retry_config.max_retries:
            logger.error(
                "Max retries exceeded for %s %s: %s",
                method,
                path,
                error.message,
            )
            raise error

        # Calculate delay
        delay: float
        if isinstance(error, EMSRateLimitError) and error.retry_after:
            delay = float(error.retry_after)
        else:
            delay = self._retry_config.get_delay(retry_count)

        logger.info(
            "Retrying %s %s in %.1f seconds (attempt %d/%d)",
            method,
            path,
            delay,
            retry_count + 1,
            self._retry_config.max_retries,
        )

        await asyncio.sleep(delay)
        return await self._request(method, path, retry_count=retry_count + 1, **kwargs)
