"""Pydantic models for EMS API types.

This module defines the data structures used for authentication tokens,
error responses, and retry configuration.
"""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class TokenResponse(BaseModel):
    """OAuth token response from /api/token endpoint.

    Example response:
        {
            "access_token": "JW74p1oRZHbXH2-clkSiZYlHUb2Iz3gWqlAPwAq7K...",
            "token_type": "bearer",
            "expires_in": 1799
        }
    """

    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Token lifetime in seconds")


class CachedToken(BaseModel):
    """Internal representation of a cached OAuth token.

    Includes the computed expiry time and utility methods for validity checking.
    """

    access_token: str
    token_type: str
    expires_at: datetime
    base_url: str = Field(description="The base URL this token was issued for")

    def is_valid(self, buffer_seconds: int = 60) -> bool:
        """Check if token is still valid with a buffer for safety.

        Args:
            buffer_seconds: Number of seconds before actual expiry to consider
                            token expired. Default 60 seconds prevents mid-request
                            expiry.

        Returns:
            True if token is valid and won't expire within the buffer period.
        """
        now = datetime.now(UTC)
        expiry_with_buffer = self.expires_at.timestamp() - buffer_seconds
        return now.timestamp() < expiry_with_buffer


class EMSErrorResponse(BaseModel):
    """Standard EMS API error response format.

    Example response:
        {
            "message": "High-level error message",
            "messageDetail": "Detailed explanation",
            "unexpected": true
        }
    """

    message: str
    message_detail: str | None = Field(default=None, alias="messageDetail")
    unexpected: bool = False


class OAuthErrorResponse(BaseModel):
    """OAuth-specific error response format.

    Example response:
        {
            "error": "invalid_grant",
            "error_description": "The user name or password is incorrect."
        }
    """

    error: str
    error_description: str | None = None


class RetryConfig(BaseModel):
    """Configuration for HTTP retry behavior.

    Implements exponential backoff with jitter for resilient API calls.
    """

    max_retries: int = Field(default=3, description="Maximum number of retry attempts")
    base_delay: float = Field(default=1.0, description="Base delay in seconds")
    max_delay: float = Field(default=30.0, description="Maximum delay in seconds")
    exponential_base: float = Field(default=2.0, description="Exponential backoff multiplier")
    jitter: bool = Field(default=True, description="Add random jitter to delays")

    def get_delay(self, attempt: int) -> float:
        """Calculate delay for a given retry attempt.

        Args:
            attempt: The retry attempt number (0-indexed).

        Returns:
            Delay in seconds, with optional jitter.
        """
        import random

        delay = min(
            self.base_delay * (self.exponential_base**attempt),
            self.max_delay,
        )
        if self.jitter:
            delay = delay * (0.5 + random.random())
        return delay


class EMSSystem(BaseModel):
    """EMS system information."""

    id: int
    name: str
    description: str | None = None


class PingResponse(BaseModel):
    """Response from EMS system ping endpoint."""

    timestamp: datetime | None = None
    server_time: str | None = Field(default=None, alias="serverTime")
    status: str = "ok"
    extra: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}
