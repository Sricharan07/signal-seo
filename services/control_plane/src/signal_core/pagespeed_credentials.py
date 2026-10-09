"""Optional operator/owner-provisioned PSI key, read only from OpenBao KV v2."""

import re
from dataclasses import dataclass, field

from signal_core.openbao_http import (
    OpenBaoDocumentError,
    OpenBaoTlsConfigurationError,
    OpenBaoTransportError,
    json_document,
    request,
    valid_base_url,
    valid_token,
)


class PageSpeedCredentialUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("PSI_CREDENTIAL_UNAVAILABLE")


@dataclass(frozen=True, repr=False)
class OpenBaoPageSpeedCredentials:
    base_url: str
    token: str = field(repr=False)
    secret_path: str | None = None

    def __post_init__(self):
        if not valid_base_url(self.base_url) or not valid_token(self.token):
            raise ValueError("Invalid OpenBao PageSpeed configuration.")
        if (
            self.secret_path is not None
            and re.fullmatch(
                r"[a-z][a-z0-9_-]{0,63}/data/pagespeed/[a-zA-Z0-9_-]{1,128}", self.secret_path
            )
            is None
        ):
            raise ValueError("An exact PageSpeed KV v2 path is required.")

    async def api_key(self, *, transport=None, verify=True) -> str | None:
        if self.secret_path is None:
            return None
        try:
            response = await request(
                base_url=self.base_url,
                token=self.token,
                method="GET",
                path="/" + self.secret_path,
                transport=transport,
                verify=verify,
            )
            if response.status_code != 200:
                raise ValueError()
            envelope = json_document(response)["data"]
            data, metadata = envelope["data"], envelope["metadata"]
            if (
                set(data) != {"api_key"}
                or type(metadata.get("version")) is not int
                or metadata["version"] < 1
                or metadata.get("destroyed") is not False
                or metadata.get("deletion_time") not in {None, ""}
                or not isinstance(data["api_key"], str)
                or re.fullmatch(r"[A-Za-z0-9_-]{16,256}", data["api_key"]) is None
            ):
                raise ValueError()
            return data["api_key"]
        except (
            OpenBaoDocumentError,
            OpenBaoTransportError,
            OpenBaoTlsConfigurationError,
            KeyError,
            TypeError,
            ValueError,
            AttributeError,
        ):
            raise PageSpeedCredentialUnavailable() from None
