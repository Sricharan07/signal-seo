"""OpenBao KV-v2 storage for Bing OAuth credentials and refresh tokens."""

import re
from dataclasses import dataclass, field
from typing import ClassVar

from signal_core.connector_secrets import OpenBaoOAuthSecrets, reference_id

_SECRET = re.compile(r"[!-~]{16,4096}")


class BingSecretError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class BingClientCredentials:
    client_id: str
    client_secret: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.client_id, str)
            or not 16 <= len(self.client_id) <= 256
            or not self.client_id.isascii()
            or not isinstance(self.client_secret, str)
            or _SECRET.fullmatch(self.client_secret) is None
        ):
            raise ValueError("Invalid Bing OAuth credentials.")


@dataclass(frozen=True, repr=False)
class OpenBaoBingSecrets(OpenBaoOAuthSecrets):
    mount: str = "signal-bing"
    provider: ClassVar[str] = "bing"
    prefix: ClassVar[str] = "BING"
    error: ClassVar[type] = BingSecretError
    credentials_type: ClassVar[type] = BingClientCredentials
    configuration_label: ClassVar[str] = "Bing"
    refresh_label: ClassVar[str] = "REFRESH"
    refresh_pattern: ClassVar[re.Pattern] = _SECRET


def _reference(value):
    return reference_id(value, "bing", BingSecretError("BING_REFRESH_UNAVAILABLE"))
