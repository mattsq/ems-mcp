"""Configuration management for EMS MCP server.

Uses pydantic-settings for environment variable loading with validation.
"""

from functools import lru_cache
from typing import Annotated

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from ems_mcp.credentials import DEFAULT_SERVICE_NAME, KeyringSettingsSource


class EMSSettings(BaseSettings):
    """EMS MCP server settings loaded from environment variables.

    Credentials (username/password) are read from Windows Credential Manager
    (or the platform's OS credential store) by default. Store them with
    ``ems-mcp credentials set``. EMS_USERNAME/EMS_PASSWORD environment
    variables, if set, take precedence over the credential store.

    Required environment variables:
        EMS_BASE_URL: Base URL of EMS API server (e.g., https://ems.example.com)

    Optional environment variables:
        EMS_USERNAME: Username override (if set without EMS_PASSWORD, the
            stored password for this username is used)
        EMS_PASSWORD: Password override
        EMS_KEYRING_SERVICE: Credential store service name (default: ems-mcp)
        EMS_DEFAULT_SYSTEM: Default EMS system ID
        EMS_CACHE_TTL: Cache time-to-live in seconds (default: 3600)
        EMS_REQUEST_TIMEOUT: Request timeout in seconds (default: 120)
        EMS_LOG_LEVEL: Logging level (default: INFO)
        EMS_MAX_RETRIES: Maximum retry attempts (default: 3)
    """

    model_config = SettingsConfigDict(
        env_prefix="EMS_",
        case_sensitive=False,
    )

    # Required settings
    base_url: str
    username: str
    password: SecretStr

    # Optional settings with defaults
    default_system: int | None = None
    cache_ttl: Annotated[int, "Cache TTL in seconds"] = 3600
    request_timeout: Annotated[int, "Request timeout in seconds"] = 120
    log_level: str = "INFO"
    max_retries: Annotated[int, "Maximum retry attempts"] = 3
    keyring_service: str = DEFAULT_SERVICE_NAME

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Add the OS credential store as the lowest-priority source."""
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
            KeyringSettingsSource(settings_cls),
        )

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, v: str) -> str:
        """Normalize base URL: remove trailing slash, upgrade HTTP to HTTPS.
        Also removes a trailing '/api' if present, as the client and token manager
        add it themselves.
        """
        url = v.rstrip("/")
        if url.lower().endswith("/api"):
            url = url[:-4]
        if url.startswith("http://"):
            url = "https://" + url[7:]
        return url

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Validate log level is a valid Python logging level."""
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid_levels:
            raise ValueError(f"Invalid log level: {v}. Must be one of {valid_levels}")
        return upper


@lru_cache
def get_settings() -> EMSSettings:
    """Get singleton settings instance.

    Returns:
        EMSSettings instance loaded from environment variables.

    Raises:
        ValidationError: If required environment variables are missing or invalid.
    """
    return EMSSettings()  # type: ignore[call-arg]
