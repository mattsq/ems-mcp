"""OAuth token management for EMS API authentication.

Handles token acquisition, caching, and automatic refresh using the
OAuth 2.0 password grant flow.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import httpx

from ems_mcp.api.models import CachedToken, OAuthErrorResponse, TokenResponse
from ems_mcp.config import EMSSettings, get_settings

logger = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """Raised when authentication fails."""

    def __init__(self, message: str, error_code: str | None = None):
        super().__init__(message)
        self.message = message
        self.error_code = error_code


class TokenManager:
    """Manages OAuth tokens for EMS API authentication.

    Handles token acquisition, caching, and automatic refresh. Uses
    double-checked locking for thread-safe singleton access.

    The token is refreshed 60 seconds before expiry to prevent mid-request
    failures when a token expires during a long-running request.
    """

    _instance: "TokenManager | None" = None
    _lock: asyncio.Lock | None = None

    # Custom headers for API identification
    APPLICATION_NAME = "ems-mcp"
    USER_AGENT = "ems-api-sdk python ems-mcp/0.1.0"

    def __init__(self, settings: EMSSettings | None = None):
        """Initialize TokenManager.

        Args:
            settings: Optional settings override. If not provided, loads from
                      environment variables.
        """
        self._settings = settings or get_settings()
        self._token: CachedToken | None = None
        self._token_lock = asyncio.Lock()

    @classmethod
    async def get_instance(cls) -> "TokenManager":
        """Get singleton TokenManager instance with double-checked locking.

        Returns:
            The singleton TokenManager instance.
        """
        if cls._instance is None:
            if cls._lock is None:
                cls._lock = asyncio.Lock()
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance. Primarily for testing."""
        cls._instance = None

    async def get_token(self) -> str:
        """Get a valid access token, refreshing if necessary.

        Returns:
            A valid access token string.

        Raises:
            AuthenticationError: If token acquisition fails.
        """
        async with self._token_lock:
            # Check if we have a valid token for the current base URL
            if self._token is not None:
                if self._token.is_valid() and self._token.base_url == self._settings.base_url:
                    logger.debug("Using cached token")
                    return self._token.access_token
                else:
                    logger.debug("Token expired or base URL changed, refreshing")

            # Request a new token
            self._token = await self._request_token()
            return self._token.access_token

    async def _request_token(self) -> CachedToken:
        """Request a new OAuth token from the EMS API.

        Returns:
            CachedToken with the new access token and computed expiry.

        Raises:
            AuthenticationError: If the token request fails.
        """
        token_url = f"{self._settings.base_url}/api/token"

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Adi-Application-Name": self.APPLICATION_NAME,
            "User-Agent": self.USER_AGENT,
        }

        data = {
            "grant_type": "password",
            "username": self._settings.username,
            "password": self._settings.password.get_secret_value(),
        }

        logger.debug("Requesting new token from %s", token_url)

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    token_url,
                    headers=headers,
                    data=data,
                    timeout=30.0,
                )

                if response.status_code == 200:
                    token_data = TokenResponse.model_validate(response.json())
                    expires_at = datetime.now(UTC) + timedelta(seconds=token_data.expires_in)
                    cached = CachedToken(
                        access_token=token_data.access_token,
                        token_type=token_data.token_type,
                        expires_at=expires_at,
                        base_url=self._settings.base_url,
                    )
                    logger.info(
                        "Authentication successful, token expires at %s",
                        expires_at.isoformat(),
                    )
                    return cached

                elif response.status_code == 400:
                    try:
                        error_data = OAuthErrorResponse.model_validate(response.json())
                        raise AuthenticationError(
                            error_data.error_description or error_data.error,
                            error_code=error_data.error,
                        )
                    except (ValueError, KeyError):
                        raise AuthenticationError(
                            f"Authentication failed: {response.text}"
                        ) from None

                else:
                    raise AuthenticationError(
                        f"Unexpected response from token endpoint ({token_url}): {response.status_code} {response.text}"
                    )

        except httpx.RequestError as e:
            raise AuthenticationError(f"Network error during authentication: {e}") from e

    def clear_token(self) -> None:
        """Clear the cached token, forcing re-authentication on next request."""
        self._token = None
        logger.debug("Token cache cleared")

    def get_auth_headers(self) -> dict[str, str]:
        """Get standard headers for authenticated API requests.

        Note: This does NOT include the Authorization header. Use get_token()
        to get the token and add it separately after awaiting.

        Returns:
            Dict of standard headers for EMS API requests.
        """
        return {
            "X-Adi-Application-Name": self.APPLICATION_NAME,
            "User-Agent": self.USER_AGENT,
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        }
