"""One-shot read-only Google qualification through a prepared shared-egress run."""

import getpass
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/control_plane/src"))

from qualify_jev_provider import (  # noqa: E402
    QualificationConfigurationError,
    _read_context,
    _run,
)
from signal_core.crawl_admission import OriginAdmissionPolicy  # noqa: E402
from signal_core.crawl_artifacts import EncryptedLocalArtifactStore  # noqa: E402
from signal_core.crawl_http import BoundedSystemResolver, PinnedHttpFetcher  # noqa: E402
from signal_core.crawl_urls import CrawlScopePolicy  # noqa: E402
from signal_core.gsc_oauth import (  # noqa: E402
    GscOAuthError,
    query_gsc_analytics,
    refresh_gsc_access_token,
)
from signal_core.gsc_properties import (  # noqa: E402
    GscProtocolError,
    discover_gsc_properties_via_egress,
)
from signal_core.gsc_secrets import GscClientCredentials  # noqa: E402
from signal_core.shared_egress import SharedEgressProvider  # noqa: E402

GOOGLE_ORIGINS = ("https://oauth2.googleapis.com", "https://www.googleapis.com")


class QualificationUnavailable(RuntimeError):
    """No safe live provider qualification was possible."""


@contextmanager
def configured_egress():
    context_path = os.environ.get("SIGNAL_GSC_EGRESS_CONTEXT")
    admission_dsn = os.environ.get("SIGNAL_GSC_EGRESS_ADMISSION_DSN")
    ingest_dsn = os.environ.get("SIGNAL_GSC_EGRESS_INGEST_DSN")
    if not context_path or not admission_dsn or not ingest_dsn:
        raise QualificationUnavailable("GSC_EGRESS_UNCONFIGURED")
    try:
        document = _read_context(Path(context_path))
        run = _run(document["run"])
        values = document["policy"]
        if not isinstance(values, dict) or set(values) != {
            "schema_version",
            "allowed_origins",
            "user_agent",
            "max_redirects",
            "max_body_bytes",
            "request_timeout_seconds",
            "total_timeout_seconds",
        }:
            raise ValueError
        policy = CrawlScopePolicy(
            schema_version=values["schema_version"],
            allowed_origins=tuple(values["allowed_origins"]),
            user_agent=values["user_agent"],
            max_redirects=values["max_redirects"],
            max_body_bytes=values["max_body_bytes"],
            request_timeout_seconds=values["request_timeout_seconds"],
            total_timeout_seconds=values["total_timeout_seconds"],
        )
        if policy.allowed_origins != GOOGLE_ORIGINS:
            raise ValueError
        store = EncryptedLocalArtifactStore(Path(document["artifact_root"]))
    except (KeyError, TypeError, ValueError, QualificationConfigurationError):
        raise QualificationUnavailable("GSC_EGRESS_CONTEXT_INVALID") from None
    admission = ingest = None
    try:
        admission = psycopg.connect(admission_dsn, autocommit=True, connect_timeout=5)
        ingest = psycopg.connect(ingest_dsn, autocommit=True, connect_timeout=5)
        yield SharedEgressProvider(
            admission,
            ingest,
            store,
            run,
            policy,
            PinnedHttpFetcher(BoundedSystemResolver()),
            "worker.gsc-qualification",
            OriginAdmissionPolicy(),
            None,
            purpose="connector",
        )
    except psycopg.Error:
        raise QualificationUnavailable("GSC_EGRESS_STATE_UNAVAILABLE") from None
    finally:
        if admission is not None:
            admission.close()
        if ingest is not None:
            ingest.close()


def main() -> int:
    verified_origin = os.environ.get("SIGNAL_GSC_QUALIFY_ORIGIN")
    property_name = os.environ.get("SIGNAL_GSC_QUALIFY_PROPERTY")
    if (
        not verified_origin
        or not property_name
        or not all(
            os.environ.get(name)
            for name in (
                "SIGNAL_GSC_EGRESS_CONTEXT",
                "SIGNAL_GSC_EGRESS_ADMISSION_DSN",
                "SIGNAL_GSC_EGRESS_INGEST_DSN",
            )
        )
    ):
        print("GSC_QUALIFICATION_UNCONFIGURED", file=sys.stderr)
        return 2
    client_id = getpass.getpass("Google OAuth client ID: ")
    client_secret = getpass.getpass("Google OAuth client secret: ")
    refresh_token = getpass.getpass("Google refresh token: ")
    try:
        credentials = GscClientCredentials(client_id, client_secret)
        with configured_egress() as egress:
            tokens = refresh_gsc_access_token(
                egress=egress,
                credentials=credentials,
                refresh_token=refresh_token,
                operation_id=uuid4(),
            )
            properties = discover_gsc_properties_via_egress(
                access_token=tokens.access_token,
                verified_origin=verified_origin,
                egress=egress,
                operation_id=uuid4(),
            )
            if not any(
                item.resource_name == property_name and item.eligible for item in properties
            ):
                raise QualificationUnavailable("GSC_PROPERTY_NOT_ELIGIBLE")
            end = datetime.now(ZoneInfo("America/Los_Angeles")).date() - timedelta(days=3)
            observation = query_gsc_analytics(
                egress=egress,
                access_token=tokens.access_token,
                property_resource_name=property_name,
                start_date=end - timedelta(days=6),
                end_date=end,
                dimensions=("date",),
                operation_id=uuid4(),
                data_state="final",
            )
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "eligible_property": True,
                    "returned_rows": len(observation.rows),
                    "coverage_complete": observation.coverage["complete"],
                    "credential_recorded": False,
                }
            )
        )
        return 0
    except (GscOAuthError, GscProtocolError, QualificationUnavailable, ValueError) as error:
        print(
            error.code if hasattr(error, "code") else "GSC_QUALIFICATION_UNAVAILABLE",
            file=sys.stderr,
        )
        return 1
    finally:
        client_id = client_secret = refresh_token = ""


if __name__ == "__main__":
    raise SystemExit(main())
