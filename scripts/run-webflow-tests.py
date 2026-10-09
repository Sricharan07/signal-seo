#!/usr/bin/env python3
"""Local-only Webflow doubles, PostgreSQL journals, and TLS OpenBao qualification."""

import hashlib
import json
import signal
import ssl
import subprocess
import sys
from datetime import UTC, datetime

import httpx2
import psycopg
from authority_journal_lab import _journal_credentials
from database_lab import (
    ROOT,
    LabError,
    isolated_postgres,
    provision,
    source_hashes,
    summarize_junit,
)
from lab_runtime import runtime_root
from openbao_lab import _create_scoped_token, _expect, isolated_openbao


def webflow_credentials(server):
    with httpx2.Client(
        base_url=server.base_url,
        timeout=5,
        follow_redirects=False,
        trust_env=False,
        verify=server.tls_context,
        headers={"X-Vault-Token": server.root_token},
    ) as root:
        _expect(
            root.post(
                "/v1/sys/mounts/signal-webflow", json={"type": "kv", "options": {"version": "2"}}
            ),
            204,
            "Webflow mount unavailable.",
        )
        _expect(
            root.post("/v1/signal-webflow/config", json={"max_versions": 1, "cas_required": True}),
            204,
            "Webflow KV configuration unavailable.",
        )
        _expect(
            root.post(
                "/v1/signal-webflow/data/client",
                json={
                    "options": {"cas": 0},
                    "data": {
                        "client_id": "synthetic-webflow-client-id-0098",
                        "client_secret": "synthetic-webflow-client-secret-0098",
                    },
                },
            ),
            200,
            "Webflow client unavailable.",
        )
        policy = (
            'path "signal-webflow/data/client" { capabilities = ["read"] }\n'
            'path "signal-webflow/data/tokens/*" { capabilities = ["create", "read"] }\n'
            'path "signal-webflow/metadata/tokens/*" { capabilities = ["delete"] }\n'
        )
        _expect(
            root.put("/v1/sys/policies/acl/signal-webflow-connector", json={"policy": policy}),
            204,
            "Webflow ACL unavailable.",
        )
        token = _create_scoped_token(root, "signal-webflow-connector")
    return {
        "SIGNAL_TEST_WEBFLOW_BAO_URL": server.base_url,
        "SIGNAL_TEST_WEBFLOW_BAO_TOKEN": token,
        "SIGNAL_TEST_WEBFLOW_BAO_CA": ssl.DER_cert_to_PEM_cert(
            server.tls_context.get_ca_certs(binary_form=True)[0]
        ),
    }


def hashes():
    result = source_hashes()
    path = ROOT / "scripts/run-webflow-tests.py"
    result[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    for path in (ROOT / "tests/webflow").glob("*.py"):
        result[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def main():
    directory = runtime_root(ROOT) / "webflow"
    directory.mkdir(parents=True, exist_ok=True)
    before = hashes()
    with (
        isolated_postgres() as (primary_dsn, common),
        isolated_postgres() as (journal_dsn, journal_common),
        isolated_openbao() as bao,
    ):
        env, _ = provision(primary_dsn, common)
        writer, _ = _journal_credentials(journal_dsn, journal_common)
        env.update(SIGNAL_TEST_WRITE_JOURNAL_DSN=writer, SIGNAL_WEBFLOW_LAB="1")
        env.update(webflow_credentials(bao))
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            cwd=ROOT,
            env=env,
            check=True,
            timeout=180,
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/webflow",
                "-v",
                f"--junitxml={directory / 'latest.xml'}",
            ],
            cwd=ROOT,
            env=env,
            timeout=600,
        )
    if before != hashes():
        raise LabError("Webflow source changed during qualification; rerun.")
    report = {
        "schema_version": 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        "source_sha256": before,
        "production_authority": False,
        "live_webflow": "NOT_EXECUTED",
        "cleanup": "completed",
        **summarize_junit(directory / "latest.xml"),
    }
    (directory / "latest.json").write_text(json.dumps(report, indent=2) + "\n")
    return result.returncode or int(report["passed"] != len(report["tests"]))


if __name__ == "__main__":

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Webflow lab interrupted; owned cleanup attempted.", file=sys.stderr)
        raise SystemExit(130) from None
    except (LabError, OSError, subprocess.SubprocessError, psycopg.Error):
        print("Webflow lab unavailable; no production resources used.", file=sys.stderr)
        raise SystemExit(1) from None
