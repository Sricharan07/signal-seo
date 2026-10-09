"""Read the current independently stored recovery generation from OpenBao."""

import re
import ssl
from dataclasses import dataclass, field

import httpx2

from signal_core.openbao_http import (
    OpenBaoDocumentError,
    OpenBaoTlsConfigurationError,
    OpenBaoTransportError,
    json_document,
    request,
    valid_base_url,
    valid_mount,
    valid_token,
)

_GENERATION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


class RecoveryAuthorityError(Exception):
    """The external recovery generation could not be established safely."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class RecoveryGeneration:
    value: str
    version: int

    def __post_init__(self) -> None:
        if _GENERATION.fullmatch(self.value) is None:
            raise ValueError("Recovery generation is invalid.")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("Recovery generation version is invalid.")


@dataclass(frozen=True)
class OpenBaoRecoveryAuthority:
    base_url: str
    token: str = field(repr=False)
    mount: str = "signal-authority"

    def __post_init__(self) -> None:
        if not valid_base_url(self.base_url):
            raise ValueError("OpenBao base URL must be an exact HTTPS origin.")
        if not valid_token(self.token):
            raise ValueError("OpenBao token is invalid.")
        if not valid_mount(self.mount):
            raise ValueError("OpenBao KV mount name is invalid.")

    async def current_generation(
        self,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        verify: ssl.SSLContext | bool = True,
    ) -> RecoveryGeneration:
        """Read and validate the latest externally anchored generation."""
        try:
            response = await request(
                base_url=self.base_url,
                token=self.token,
                method="GET",
                path=f"/{self.mount}/data/recovery/current",
                transport=transport,
                verify=verify,
            )
        except OpenBaoTlsConfigurationError:
            raise RecoveryAuthorityError("RECOVERY_AUTHORITY_TLS_REJECTED") from None
        except OpenBaoTransportError:
            raise RecoveryAuthorityError("RECOVERY_AUTHORITY_UNAVAILABLE") from None
        if response.status_code != 200:
            raise RecoveryAuthorityError("RECOVERY_AUTHORITY_UNAVAILABLE")
        try:
            document = json_document(response)
        except OpenBaoDocumentError:
            raise RecoveryAuthorityError("RECOVERY_AUTHORITY_UNAVAILABLE") from None
        envelope = document.get("data")
        if not isinstance(envelope, dict):
            raise RecoveryAuthorityError("RECOVERY_AUTHORITY_UNAVAILABLE")
        data = envelope.get("data")
        metadata = envelope.get("metadata")
        if (
            not isinstance(data, dict)
            or set(data) != {"generation"}
            or not isinstance(metadata, dict)
            or metadata.get("destroyed") is not False
            or metadata.get("deletion_time") not in {None, ""}
        ):
            raise RecoveryAuthorityError("RECOVERY_AUTHORITY_UNAVAILABLE")
        try:
            return RecoveryGeneration(
                value=data["generation"],
                version=metadata["version"],
            )
        except (KeyError, TypeError, ValueError):
            raise RecoveryAuthorityError("RECOVERY_AUTHORITY_UNAVAILABLE") from None
