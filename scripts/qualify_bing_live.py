"""One-shot read-only Bing qualification through a prepared shared-egress run."""

import getpass
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/control_plane/src"))

from qualify_jev_provider import QualificationConfigurationError, _read_context, _run  # noqa: E402
from signal_core.bing_protocol import (  # noqa: E402
    BingProtocolError,
    discover_bing_sites,
    import_bing_link_counts,
    import_bing_performance,
    import_bing_url_links,
    refresh_bing_token,
)
from signal_core.bing_secrets import BingClientCredentials  # noqa: E402
from signal_core.crawl_admission import OriginAdmissionPolicy  # noqa: E402
from signal_core.crawl_artifacts import EncryptedLocalArtifactStore  # noqa: E402
from signal_core.crawl_http import BoundedSystemResolver, PinnedHttpFetcher  # noqa: E402
from signal_core.crawl_urls import CrawlScopePolicy  # noqa: E402
from signal_core.shared_egress import SharedEgressProvider  # noqa: E402

BING_ORIGIN = ("https://www.bing.com",)


class QualificationUnavailable(RuntimeError):
    pass


@contextmanager
def configured_egress():
    context_path = os.environ.get("SIGNAL_BING_EGRESS_CONTEXT")
    admission_dsn = os.environ.get("SIGNAL_BING_EGRESS_ADMISSION_DSN")
    ingest_dsn = os.environ.get("SIGNAL_BING_EGRESS_INGEST_DSN")
    if not context_path or not admission_dsn or not ingest_dsn:
        raise QualificationUnavailable("BING_EGRESS_UNCONFIGURED")
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
        if policy.allowed_origins != BING_ORIGIN:
            raise ValueError
        store = EncryptedLocalArtifactStore(Path(document["artifact_root"]))
    except (KeyError, TypeError, ValueError, QualificationConfigurationError):
        raise QualificationUnavailable("BING_EGRESS_CONTEXT_INVALID") from None
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
            "worker.bing-qualification",
            OriginAdmissionPolicy(),
            None,
            purpose="connector",
        )
    except psycopg.Error:
        raise QualificationUnavailable("BING_EGRESS_STATE_UNAVAILABLE") from None
    finally:
        if admission is not None:
            admission.close()
        if ingest is not None:
            ingest.close()


def main() -> int:
    origin = os.environ.get("SIGNAL_BING_QUALIFY_ORIGIN")
    site_url = os.environ.get("SIGNAL_BING_QUALIFY_SITE_URL")
    if (
        not origin
        or not site_url
        or not all(
            os.environ.get(name)
            for name in (
                "SIGNAL_BING_EGRESS_CONTEXT",
                "SIGNAL_BING_EGRESS_ADMISSION_DSN",
                "SIGNAL_BING_EGRESS_INGEST_DSN",
            )
        )
    ):
        print("BING_QUALIFICATION_UNCONFIGURED", file=sys.stderr)
        return 2
    client_id = getpass.getpass("Bing OAuth client ID: ")
    client_secret = getpass.getpass("Bing OAuth client secret: ")
    refresh_token = getpass.getpass("Bing refresh token: ")
    try:
        credentials = BingClientCredentials(client_id, client_secret)
        with configured_egress() as egress:
            tokens = refresh_bing_token(
                egress,
                credentials.client_id,
                credentials.client_secret,
                refresh_token,
                uuid4(),
            )
            sites = discover_bing_sites(egress, tokens.access_token, origin, uuid4())
            if not any(site.url == site_url and site.eligible for site in sites):
                raise QualificationUnavailable("BING_SITE_NOT_ELIGIBLE")
            performance = import_bing_performance(egress, tokens.access_token, site_url, uuid4())
            links = import_bing_link_counts(egress, tokens.access_token, site_url, uuid4())
            detail_rows = 0
            if links.rows:
                details = import_bing_url_links(
                    egress,
                    tokens.access_token,
                    site_url,
                    links.rows[0]["target_url"],
                    uuid4(),
                )
                detail_rows = len(details.rows)
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "eligible_site": True,
                    "performance_rows": len(performance.rows),
                    "link_count_rows": len(links.rows),
                    "inbound_link_detail_rows": detail_rows,
                    "coverage_complete": False,
                    "credential_recorded": False,
                }
            )
        )
        return 0
    except (BingProtocolError, QualificationUnavailable, ValueError) as error:
        print(
            error.code if hasattr(error, "code") else "BING_QUALIFICATION_UNAVAILABLE",
            file=sys.stderr,
        )
        return 1
    finally:
        client_id = client_secret = refresh_token = ""


if __name__ == "__main__":
    raise SystemExit(main())
