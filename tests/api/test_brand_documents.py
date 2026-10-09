import base64
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx2 as httpx
import pytest
from signal_api.browser_security import CSRF_HEADER_NAME, SESSION_COOKIE_NAME, BrowserSecurity
from signal_api.config import ApiSettings
from signal_api.main import create_app
from signal_core.brand_documents import (
    BrandDocument,
    BrandDocumentUnavailable,
    UntrustedDocumentText,
)

TOKEN = "t" * 43
ORIGIN = "https://dashboard.example.test"
SITE = uuid4()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@dataclass
class Documents:
    inputs: list[tuple] = field(default_factory=list)
    document: BrandDocument = field(
        default_factory=lambda: BrandDocument(
            uuid4(), "brand.txt", "text/plain", datetime.now(UTC), None, False, False, False, True
        )
    )

    async def upload_brand_document(
        self,
        *,
        session_token: str,
        site_id: UUID,
        filename: str,
        body: bytes,
        supersedes_id: UUID | None,
    ) -> BrandDocument:
        self.inputs.append((session_token, site_id, filename, body, supersedes_id))
        return self.document

    async def list_brand_documents(self, *, session_token: str, site_id: UUID):
        self.inputs.append((session_token, site_id))
        return (self.document,)

    async def delete_brand_document(
        self,
        *,
        session_token: str,
        site_id: UUID,
        document_id: UUID,
    ) -> str:
        self.inputs.append((session_token, site_id, document_id))
        return "deleted_retained"

    async def read_brand_document(
        self,
        *,
        session_token: str,
        site_id: UUID,
        document_id: UUID,
    ) -> UntrustedDocumentText:
        self.inputs.append((session_token, site_id, document_id))
        return UntrustedDocumentText(document_id, uuid4(), site_id, "Brand voice.", "a" * 64, False)


def headers(security: BrowserSecurity) -> dict[str, str]:
    return {
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}",
        CSRF_HEADER_NAME: security.issue_csrf_token(TOKEN),
    }


@pytest.mark.anyio
async def test_document_upload_list_read_and_delete_are_owner_gateway_scoped():
    security = BrowserSecurity(b"brand-documents-test-key" * 2, frozenset({ORIGIN}))
    gateway = Documents()
    app = create_app(
        settings=ApiSettings(environment="test"),
        browser_security=security,
        browser_documents=gateway,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        upload = await client.post(
            f"/v1/sites/{SITE}/brand-documents",
            headers=headers(security),
            json={
                "schema_version": 1,
                "filename": "brand.txt",
                "content_base64": base64.b64encode(b"Brand voice.").decode(),
                "supersedes_id": None,
            },
        )
        assert upload.status_code == 201
        assert upload.json()["document_id"] == str(gateway.document.document_id)
        listed = await client.get(
            f"/v1/sites/{SITE}/brand-documents",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}"},
        )
        assert listed.status_code == 200
        assert listed.json()["documents"][0]["display_name"] == "brand.txt"
        viewed = await client.get(
            f"/v1/sites/{SITE}/brand-documents/{gateway.document.document_id}",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}"},
        )
        assert viewed.status_code == 200
        assert viewed.json()["trust_label"] == "owner_upload_untrusted_data"
        removed = await client.delete(
            f"/v1/sites/{SITE}/brand-documents/{gateway.document.document_id}",
            headers=headers(security),
        )
        assert removed.status_code == 200
        assert removed.json()["retained_for_evidence"] is True
    assert gateway.inputs[0] == (TOKEN, SITE, "brand.txt", b"Brand voice.", None)


@pytest.mark.anyio
async def test_document_mutation_rejects_csrf_schema_and_oversize_before_gateway():
    security = BrowserSecurity(b"brand-documents-test-key" * 2, frozenset({ORIGIN}))
    gateway = Documents()
    app = create_app(
        settings=ApiSettings(environment="test"),
        browser_security=security,
        browser_documents=gateway,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        missing_csrf = await client.post(
            f"/v1/sites/{SITE}/brand-documents",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}"},
            json={},
        )
        assert missing_csrf.status_code == 403
        extra = await client.post(
            f"/v1/sites/{SITE}/brand-documents",
            headers=headers(security),
            json={
                "schema_version": 1,
                "filename": "brand.txt",
                "content_base64": "eA==",
                "supersedes_id": None,
                "extra": 1,
            },
        )
        assert extra.status_code == 422
        oversized = await client.post(
            f"/v1/sites/{SITE}/brand-documents",
            headers=headers(security),
            json={
                "schema_version": 1,
                "filename": "brand.txt",
                "content_base64": base64.b64encode(b"x" * (2 * 1024 * 1024 + 1)).decode(),
                "supersedes_id": None,
            },
        )
        assert oversized.status_code == 422
    assert gateway.inputs == []


@pytest.mark.anyio
async def test_document_unavailable_is_explicit():
    security = BrowserSecurity(b"brand-documents-test-key" * 2, frozenset({ORIGIN}))

    class Denied(Documents):
        async def list_brand_documents(self, *, session_token: str, site_id: UUID):
            raise BrandDocumentUnavailable("owner_access_denied")

    app = create_app(
        settings=ApiSettings(environment="test"),
        browser_security=security,
        browser_documents=Denied(),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        response = await client.get(
            f"/v1/sites/{SITE}/brand-documents",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}"},
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "DOCUMENT_ACCESS_DENIED"
