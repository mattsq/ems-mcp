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

If ``EMS_CA_BUNDLE`` is set but the file does not exist, a
``FileNotFoundError`` is raised immediately so TLS misconfigurations
are caught early rather than producing opaque handshake failures.
For the generic vars (``SSL_CERT_FILE``, ``REQUESTS_CA_BUNDLE``),
a warning is logged but the search continues because those vars may
have been set by unrelated tooling.
"""

from __future__ import annotations

import logging
import os
import ssl
from pathlib import Path

logger = logging.getLogger(__name__)


def get_verify_setting() -> ssl.SSLContext | bool:
    """Resolve the value to pass to ``httpx.AsyncClient(verify=...)``.

    Returns:
        An ``SSLContext`` loaded from the resolved CA bundle file when one
        of the env vars points at an existing file, otherwise ``True`` to
        keep the default trust store (certifi) active. Verification is
        never disabled.

    Raises:
        FileNotFoundError: If ``EMS_CA_BUNDLE`` is set but the path does
            not exist or is not a file.
    """
    for env_var in ("EMS_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        value = os.environ.get(env_var)
        if not value:
            continue
        path = Path(value)
        if path.is_file():
            ctx = ssl.create_default_context(cafile=value)
            # Corporate proxy bundles (e.g. Zscaler) often contain a CA whose
            # basicConstraints extension is not marked critical. Python 3.10+
            # enables VERIFY_X509_STRICT by default, which rejects such certs
            # with "Basic Constraints of CA cert not marked critical". Relax
            # only when a custom bundle is in use; the system trust store
            # path below keeps strict validation.
            ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
            return ctx
        # Path is set but doesn't exist.
        if env_var == "EMS_CA_BUNDLE":
            raise FileNotFoundError(
                f"EMS_CA_BUNDLE is set to '{value}' but the file does not "
                "exist. Fix the path or unset the variable."
            )
        logger.warning(
            "%s is set to '%s' but the file does not exist; skipping.",
            env_var, value,
        )
    return True
