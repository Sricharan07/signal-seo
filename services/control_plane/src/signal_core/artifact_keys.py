"""Read the dedicated brand-document AES key from a narrow OpenBao path."""

import base64
import binascii
import ssl
from dataclasses import dataclass, field

import httpx2

from signal_core.crawl_artifacts import ArtifactEncryptionKey
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


class ArtifactKeyUnavailable(RuntimeError):
    """No validated artifact key was available; no plaintext may be stored."""


@dataclass(frozen=True)
class OpenBaoBrandArtifactKey:
    base_url: str
    token: str = field(repr=False)
    mount: str = "signal-artifacts"

    def __post_init__(self) -> None:
        if (
            not valid_base_url(self.base_url)
            or not valid_token(self.token)
            or not valid_mount(self.mount)
        ):
            raise ValueError("A validated OpenBao artifact key reader is required.")

    async def read(
        self,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        verify: ssl.SSLContext | bool = True,
    ) -> ArtifactEncryptionKey:
        try:
            response = await request(
                base_url=self.base_url,
                token=self.token,
                method="GET",
                path=f"/{self.mount}/data/brand/default",
                transport=transport,
                verify=verify,
            )
            if response.status_code != 200:
                raise ArtifactKeyUnavailable("artifact_key_unavailable")
            document = json_document(response)
        except (OpenBaoTransportError, OpenBaoDocumentError, OpenBaoTlsConfigurationError):
            raise ArtifactKeyUnavailable("artifact_key_unavailable") from None
        envelope = document.get("data")
        data = envelope.get("data") if isinstance(envelope, dict) else None
        metadata = envelope.get("metadata") if isinstance(envelope, dict) else None
        if (
            not isinstance(data, dict)
            or set(data) != {"key_base64"}
            or not isinstance(metadata, dict)
        ):
            raise ArtifactKeyUnavailable("artifact_key_unavailable")
        encoded = data["key_base64"]
        if (
            not isinstance(encoded, str)
            or len(encoded) != 44
            or metadata.get("version") != 1
            or metadata.get("destroyed") is not False
            or metadata.get("deletion_time") not in {None, ""}
        ):
            raise ArtifactKeyUnavailable("artifact_key_unavailable")
        try:
            material = base64.b64decode(encoded, validate=True)
            return ArtifactEncryptionKey(f"openbao:{self.mount}:brand:v1", material)
        except (binascii.Error, ValueError):
            raise ArtifactKeyUnavailable("artifact_key_unavailable") from None
