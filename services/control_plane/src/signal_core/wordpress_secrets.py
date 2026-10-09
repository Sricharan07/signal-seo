"""Read-only application-password custody in an operator-provisioned OpenBao path."""

from dataclasses import dataclass, field
from uuid import UUID

from signal_core.connector_secrets import kv_document
from signal_core.openbao_http import request, valid_base_url, valid_token
from signal_core.wordpress_protocol import WordPressCredential, WordPressUnavailable


@dataclass(frozen=True, repr=False)
class OpenBaoWordPressSecrets:
    base_url: str
    token: str = field(repr=False)

    def __post_init__(self):
        if not valid_base_url(self.base_url) or not valid_token(self.token):
            raise ValueError("Invalid WordPress secret configuration.")

    async def credential(
        self, binding_id: UUID, *, tenant_id: UUID, site_id: UUID, origin: str, **options
    ):
        try:
            if not all(isinstance(v, UUID) for v in (binding_id, tenant_id, site_id)):
                raise ValueError
            response = await request(
                base_url=self.base_url,
                token=self.token,
                method="GET",
                path=f"/signal-wordpress/data/bindings/{binding_id}",
                **options,
            )
            data, _ = kv_document(response, minimum_version=1, require_deletion=True)
            if (
                response.status_code != 200
                or set(data) != {"username", "password", "tenant_id", "site_id", "origin"}
                or data["tenant_id"] != str(tenant_id)
                or data["site_id"] != str(site_id)
                or data["origin"] != origin
            ):
                raise ValueError
            return WordPressCredential(data["username"], data["password"])
        except Exception:
            raise WordPressUnavailable("WORDPRESS_CREDENTIAL_UNAVAILABLE") from None
