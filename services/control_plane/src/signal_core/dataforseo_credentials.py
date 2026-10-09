"""Per-site, generation-fenced DataForSEO secrets held only by OpenBao KV v2."""

import base64
import re
from dataclasses import dataclass, field
from uuid import UUID

from signal_core.connector_secrets import kv_document
from signal_core.egress_profiles import DataForSeoCredentialScope
from signal_core.openbao_http import (
    OpenBaoDocumentError,
    OpenBaoTlsConfigurationError,
    OpenBaoTransportError,
    request,
    valid_base_url,
    valid_mount,
    valid_token,
)


class DataForSeoUnavailable(Exception):
    def __init__(self, code: str = "DATAFORSEO_UNAVAILABLE") -> None:
        self.code = code
        super().__init__(code)


def validate_credential(login: str, password: str) -> None:
    if (
        not isinstance(login, str)
        or re.fullmatch(r"[!-9;-~]{1,254}", login) is None
        or not isinstance(password, str)
        or re.fullmatch(r"[!-~]{8,512}", password) is None
    ):
        raise DataForSeoUnavailable("DATAFORSEO_CREDENTIAL_INVALID")


@dataclass(frozen=True, repr=False)
class OpenBaoDataForSeoCredentials:
    base_url: str
    token: str = field(repr=False)
    mount: str = "signal-dataforseo"

    def __post_init__(self) -> None:
        if (
            not valid_base_url(self.base_url)
            or not valid_token(self.token)
            or not valid_mount(self.mount)
        ):
            raise ValueError("Invalid OpenBao DataForSEO configuration.")

    async def _request(self, method, path, payload=None, **options):
        try:
            return await request(
                base_url=self.base_url,
                token=self.token,
                method=method,
                path=path,
                payload=payload,
                **options,
            )
        except (OpenBaoTlsConfigurationError, OpenBaoTransportError):
            raise DataForSeoUnavailable() from None

    def _path(self, tenant_id: UUID, site_id: UUID, generation: UUID, kind="data") -> str:
        if not all(isinstance(value, UUID) for value in (tenant_id, site_id, generation)):
            raise DataForSeoUnavailable()
        return f"/{self.mount}/{kind}/{tenant_id}/{site_id}/{generation}"

    async def put(self, tenant_id, site_id, generation, login, password, **options) -> None:
        validate_credential(login, password)
        response = await self._request(
            "POST",
            self._path(tenant_id, site_id, generation),
            {"options": {"cas": 0}, "data": {"login": login, "password": password}},
            **options,
        )
        if response.status_code != 200:
            raise DataForSeoUnavailable()

    async def remove(self, tenant_id, site_id, generation, **options) -> None:
        response = await self._request(
            "DELETE", self._path(tenant_id, site_id, generation, "metadata"), **options
        )
        if response.status_code not in {204, 404}:
            raise DataForSeoUnavailable("DATAFORSEO_SECRET_REMOVAL_UNKNOWN")

    async def scope(self, tenant_id, site_id, generation, **options) -> DataForSeoCredentialScope:
        response = await self._request("GET", self._path(tenant_id, site_id, generation), **options)
        try:
            if response.status_code != 200:
                raise ValueError
            data, _ = kv_document(response, exact_version=1)
            if set(data) != {"login", "password"}:
                raise ValueError
            validate_credential(data["login"], data["password"])
        except (OpenBaoDocumentError, KeyError, TypeError, ValueError):
            raise DataForSeoUnavailable() from None
        authorization = "Basic " + base64.b64encode(
            f"{data['login']}:{data['password']}".encode("ascii")
        ).decode("ascii")
        return DataForSeoCredentialScope(str(generation), authorization)
