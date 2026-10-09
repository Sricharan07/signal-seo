"""OpenBao-only storage for Search Console OAuth client, verifier, and refresh token."""

import re
from dataclasses import dataclass, field
from typing import ClassVar

from signal_core.connector_secrets import OpenBaoOAuthSecrets, reference_id

_CLIENT_ID = re.compile(r"[A-Za-z0-9._-]{16,256}\.apps\.googleusercontent\.com")
_CLIENT_SECRET = re.compile(r"[!-~]{16,512}")
_OAUTH_SECRET = re.compile(r"[!-~]{20,4096}")
_VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")


class GscSecretError(Exception):
    """Fixed error that never includes provider or secret material."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class GscClientCredentials:
    client_id: str
    client_secret: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.client_id, str)
            or _CLIENT_ID.fullmatch(self.client_id) is None
            or not isinstance(self.client_secret, str)
            or _CLIENT_SECRET.fullmatch(self.client_secret) is None
        ):
            raise ValueError("Invalid Google OAuth client credentials.")


@dataclass(frozen=True, repr=False)
class OpenBaoGscSecrets(OpenBaoOAuthSecrets):
    mount: str = "signal-gsc"
    provider: ClassVar[str] = "gsc"
    prefix: ClassVar[str] = "GSC"
    error: ClassVar[type] = GscSecretError
    credentials_type: ClassVar[type] = GscClientCredentials
    configuration_label: ClassVar[str] = "GSC"


def _parse_reference(value, provider="gsc"):
    return reference_id(value, provider, GscSecretError("GSC_REFRESH_TOKEN_UNAVAILABLE"))
