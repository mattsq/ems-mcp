"""Credential storage for EMS MCP server.

EMS credentials are stored in the OS credential store via the ``keyring``
package. On Windows this is Windows Credential Manager (a "Generic Credential"
whose target name is the service name, ``ems-mcp`` by default).

Credentials can be managed from the command line:

    ems-mcp credentials set      # prompt for username/password and store them
    ems-mcp credentials show     # show the stored username (never the password)
    ems-mcp credentials delete   # remove the stored credential
"""

import logging
from typing import Any

import keyring
from keyring.errors import KeyringError, PasswordDeleteError
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

logger = logging.getLogger(__name__)

DEFAULT_SERVICE_NAME = "ems-mcp"


class CredentialStoreError(Exception):
    """Raised when the OS credential store cannot be accessed."""


def load_credentials(
    service: str = DEFAULT_SERVICE_NAME, username: str | None = None
) -> tuple[str, str] | None:
    """Load EMS credentials from the OS credential store.

    Args:
        service: Credential store service (target) name.
        username: If given, look up the password for this specific username.
            Otherwise return whichever credential is stored for the service.

    Returns:
        (username, password) tuple, or None if no credential is stored or the
        credential store is unavailable.
    """
    try:
        if username:
            password = keyring.get_password(service, username)
            return (username, password) if password is not None else None
        credential = keyring.get_credential(service, None)
    except KeyringError as e:
        logger.warning("Could not read credentials from credential store: %s", e)
        return None
    if credential is None or not credential.username:
        return None
    return credential.username, credential.password


def store_credentials(
    username: str, password: str, service: str = DEFAULT_SERVICE_NAME
) -> None:
    """Store EMS credentials in the OS credential store.

    Any existing credential for the service is replaced, so only one
    username is stored per service name.

    Args:
        username: EMS username.
        password: EMS password.
        service: Credential store service (target) name.

    Raises:
        CredentialStoreError: If the credential could not be written.
    """
    try:
        delete_credentials(service)
        keyring.set_password(service, username, password)
    except KeyringError as e:
        raise CredentialStoreError(f"Could not store credentials: {e}") from e


def delete_credentials(service: str = DEFAULT_SERVICE_NAME) -> bool:
    """Delete the stored EMS credential for a service.

    Args:
        service: Credential store service (target) name.

    Returns:
        True if a credential was deleted, False if none was stored.

    Raises:
        CredentialStoreError: If the credential store could not be accessed.
    """
    existing = load_credentials(service)
    if existing is None:
        return False
    try:
        keyring.delete_password(service, existing[0])
    except PasswordDeleteError:
        return False
    except KeyringError as e:
        raise CredentialStoreError(f"Could not delete credentials: {e}") from e
    return True


class KeyringSettingsSource(PydanticBaseSettingsSource):
    """Settings source that supplies username/password from the credential store.

    Runs after the environment variable sources, so EMS_USERNAME/EMS_PASSWORD
    still take precedence when set. If only EMS_USERNAME is set, the password
    stored for that username is used.
    """

    def __init__(self, settings_cls: type[BaseSettings]):
        super().__init__(settings_cls)

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        # Values are produced in bulk by __call__.
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        state = self.current_state
        if state.get("username") and state.get("password"):
            return {}

        service = state.get("keyring_service") or DEFAULT_SERVICE_NAME
        credentials = load_credentials(service, username=state.get("username") or None)
        if credentials is None:
            return {}

        username, password = credentials
        logger.debug("Loaded EMS credentials for %s from credential store", username)
        return {"username": username, "password": password}
