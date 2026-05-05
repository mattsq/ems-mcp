"""SSL/TLS verification helper for the EMS HTTP clients.

Corporate proxies (e.g. Zscaler) intercept TLS and present their own
certificate. The fix is to point httpx at a combined CA bundle that
includes the corporate root CA, *not* to disable verification.

Resolution order for the bundle path:
    1. EMS_CA_BUNDLE - project-specific override.
    2. SSL_CERT_FILE - the conventional Python/certifi/httpx env var.
    3. REQUESTS_CA_BUNDLE - widely used by other tooling on the same host.

If none are set, returns ``True`` so httpx falls back to its default
trust store (certifi). Returning ``True`` preserves verification.
"""

from __future__ import annotations

import os
import ssl
from pathlib import Path


def get_verify_setting() -> ssl.SSLContext | bool:
    """Resolve the value to pass to ``httpx.AsyncClient(verify=...)``.

    Returns:
        An ``SSLContext`` loaded from the resolved CA bundle file when one
        of the env vars points at an existing file, otherwise ``True`` to
        keep the default trust store (certifi) active. Verification is
        never disabled.
    """
    for env_var in ("EMS_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        value = os.environ.get(env_var)
        if value and Path(value).is_file():
            return ssl.create_default_context(cafile=value)
    return True
