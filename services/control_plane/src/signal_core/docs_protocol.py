"""Selected-only Google Drive metadata and plain-text export via shared egress."""

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from signal_core.egress_profiles import DRIVE_METADATA_FIELDS, DrivePickedScope, EgressProfile
from signal_core.gsc_oauth import DRIVE_FILE_SCOPE, GscAuthorization, new_gsc_authorization
from signal_core.shared_egress import ProviderEgressUnavailable, SharedEgressProvider

NOTION_UNAVAILABLE_REASON = (
    "Notion read-only capability cannot be verified from provider documentation; "
    "pending live qualification"
)


class DocsRejected(Exception):
    def __init__(self, code: str = "DOCS_UNAVAILABLE") -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class DocsMetadata:
    file_id: str
    version: str
    modified_time: datetime
    trashed: bool


def docs_authorization(*, client_id: str, redirect_uri: str) -> GscAuthorization:
    authorization = new_gsc_authorization(
        client_id=client_id, redirect_uri=redirect_uri, scope=DRIVE_FILE_SCOPE
    )
    url = urlsplit(authorization.url)
    return replace(
        authorization,
        url=urlunsplit(
            (
                url.scheme,
                url.netloc,
                url.path,
                urlencode(
                    [
                        *parse_qsl(url.query),
                        ("trigger_onepick", "true"),
                        ("allow_multiple", "true"),
                        ("mimetypes", "application/vnd.google-apps.document"),
                    ]
                ),
                "",
            )
        ),
    )


def picked_files(value: str) -> tuple[str, ...]:
    if not isinstance(value, str) or len(value) > 4020:
        raise DocsRejected("DOCS_PICKER_REJECTED")
    files = tuple(value.split(","))
    try:
        DrivePickedScope(files, "synthetic-validation-token")
    except ValueError:
        raise DocsRejected("DOCS_PICKER_REJECTED") from None
    return files


def _read(
    egress: SharedEgressProvider,
    scope: DrivePickedScope,
    file_id: str,
    operation_id: UUID,
    *,
    export: bool,
):
    if not isinstance(egress, SharedEgressProvider) or egress.purpose != "connector":
        raise DocsRejected("DOCS_EGRESS_REQUIRED")
    profile = EgressProfile.DRIVE_EXPORT if export else EgressProfile.DRIVE_METADATA
    suffix = "/export" if export else ""
    query = {"mimeType": "text/plain"} if export else {"fields": DRIVE_METADATA_FIELDS}
    try:
        response = egress.request_json(
            method="GET",
            url=f"https://www.googleapis.com/drive/v3/files/{file_id}{suffix}?{urlencode(query)}",
            profile=profile,
            drive_picked_scope=scope,
            authorization=f"Bearer {scope.access_token}",
            operation_id=operation_id,
            timeout_seconds=10,
            max_response_bytes=512 * 1024 if export else 16384,
        )
    except ProviderEgressUnavailable:
        raise DocsRejected("DOCS_PROVIDER_UNAVAILABLE") from None
    if response.status_code == 403:
        try:
            error = json.loads(response.body)["error"]
            reasons = {x["reason"] for x in error.get("errors", [])}
        except (ValueError, TypeError, KeyError, AttributeError):
            reasons = set()
        if not reasons or not reasons.issubset(
            {"insufficientFilePermissions", "appNotAuthorizedToFile"}
        ):
            raise DocsRejected("DOCS_PROVIDER_UNAVAILABLE")
    if response.status_code in {403, 404}:
        raise DocsRejected("DOCS_PROVIDER_UNSHARED")
    if response.status_code == 401:
        raise DocsRejected("DOCS_REAUTH_REQUIRED")
    if response.status_code != 200:
        raise DocsRejected("DOCS_PROVIDER_UNAVAILABLE")
    if response.media_type != ("text/plain" if export else "application/json"):
        raise DocsRejected("DOCS_RESPONSE_REJECTED")
    return response.body


def read_metadata(
    egress: SharedEgressProvider, scope: DrivePickedScope, file_id: str, operation_id: UUID
) -> DocsMetadata:
    body = _read(egress, scope, file_id, operation_id, export=False)
    try:
        data = json.loads(body)
        if (
            not isinstance(data, dict)
            or set(data)
            != {"id", "mimeType", "modifiedTime", "version", "trashed", "isAppAuthorized"}
            or data["id"] != file_id
            or data["mimeType"] != "application/vnd.google-apps.document"
            or (
                data["isAppAuthorized"] is not True
                or type(data["trashed"]) is not bool
                or not isinstance(data["version"], str)
                or re.fullmatch(r"[0-9]{1,100}", data["version"]) is None
            )
        ):
            raise ValueError
        modified = datetime.fromisoformat(data["modifiedTime"].replace("Z", "+00:00"))
        if modified.tzinfo is None:
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        raise DocsRejected("DOCS_RESPONSE_REJECTED") from None
    return DocsMetadata(file_id, data["version"], modified, data["trashed"])


def export_text(
    egress: SharedEgressProvider, scope: DrivePickedScope, file_id: str, operation_id: UUID
) -> bytes:
    body = _read(egress, scope, file_id, operation_id, export=True)
    try:
        if not 1 <= len(body) <= 512 * 1024 or b"\x00" in body:
            raise ValueError
        body.decode("utf-8", errors="strict")
    except (ValueError, UnicodeError):
        raise DocsRejected("DOCS_RESPONSE_REJECTED") from None
    return body
