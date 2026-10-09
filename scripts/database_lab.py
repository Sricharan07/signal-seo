"""Disposable PostgreSQL lifecycle and reproducible integration-test evidence."""

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parents[1]
IMAGE = (
    "postgres:17.11-alpine@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73"
)
LABEL = "io.signal.lab.run"


class LabError(RuntimeError):
    """Failure safe to print without subprocess arguments or secrets."""


def require_free_space(directory: Path, minimum: int = 3 * 1024**3) -> None:
    if shutil.disk_usage(directory).free < minimum:
        raise LabError("At least 3 GiB free is required before starting the database lab.")


def docker(*args: str, timeout: int = 30, env: dict | None = None) -> str:
    try:
        result = subprocess.run(
            ["docker", *args], env=env, check=True, text=True, capture_output=True, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise LabError(f"Docker {args[0]} failed ({type(error).__name__}).") from None
    return result.stdout.strip()


def cleanup(name: str) -> None:
    """Attempt both cleanups even if creation acknowledgement or the first cleanup was lost."""
    failures = []
    for kind, template in [("container", "{{.Names}}"), ("network", "{{.Name}}")]:
        try:
            args = [kind, "ls"] + (["--all"] if kind == "container" else [])
            names = docker(
                *args, "--filter", f"label={LABEL}={name}", "--format", template
            ).splitlines()
            if name in names:
                options = ["--force", "--volumes"] if kind == "container" else []
                docker(kind, "rm", *options, name)
        except LabError:
            failures.append(kind)
    if failures:
        raise LabError(
            f"Cleanup unconfirmed for {name}: {', '.join(failures)}. Inspect this run only."
        )


@contextmanager
def isolated_postgres(*, minimum_free: int = 3 * 1024**3):
    require_free_space(ROOT, minimum=minimum_free)
    docker("info", "--format", "{{.ServerVersion}}", timeout=10)
    name = f"signal-db-tests-{secrets.token_hex(6)}"
    password = secrets.token_hex(32)
    print(f"Starting isolated project {name}.", flush=True)
    try:
        # Docker's internal networks do not publish ports on this supported engine.
        # Use a run-private bridge and require loopback-only publication below.
        docker("network", "create", "--label", f"{LABEL}={name}", name)
        docker(
            "container",
            "create",
            "--name",
            name,
            "--label",
            f"{LABEL}={name}",
            "--network",
            name,
            "--publish",
            "127.0.0.1::5432",
            "--memory",
            "384m",
            "--cpus",
            "1",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/var/lib/postgresql/data",
            "--env",
            "POSTGRES_PASSWORD",
            "--env",
            "POSTGRES_DB=signal_test",
            IMAGE,
            env=dict(os.environ, POSTGRES_PASSWORD=password),
            timeout=180,
        )
        docker("container", "start", name)
        description = json.loads(docker("inspect", name))[0]
        bindings = description["NetworkSettings"]["Ports"].get("5432/tcp")
        if not bindings or len(bindings) != 1 or bindings[0]["HostIp"] != "127.0.0.1":
            state = description.get("State", {})
            raise LabError(
                "Test database must publish only an ephemeral loopback port. "
                f"Observed bindings={bindings}, state={state.get('Status')}, "
                f"exit={state.get('ExitCode')}."
            )
        common = {
            "host": "127.0.0.1",
            "port": bindings[0]["HostPort"],
            "dbname": "signal_test",
            "connect_timeout": 2,
        }
        admin_dsn = make_conninfo(**common, user="postgres", password=password)
        deadline = time.monotonic() + 60
        while True:
            try:
                with psycopg.connect(admin_dsn, autocommit=True) as connection:
                    connection.execute("SELECT 1")
                break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise LabError("Disposable PostgreSQL did not become ready.") from None
                time.sleep(0.2)
        yield admin_dsn, common
    finally:
        cleanup(name)


def provision(admin_dsn: str, common: dict) -> tuple[dict, str]:
    env = dict(os.environ, SIGNAL_TEST_ADMIN_DSN=admin_dsn, SIGNAL_DATABASE_LAB="1")
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        version = connection.execute("SHOW server_version").fetchone()[0]
        if not version.startswith("17.11"):
            raise LabError("Unexpected PostgreSQL version for the pinned test profile.")
        connection.execute((ROOT / "database/bootstrap.sql").read_text())
        connection.execute("REVOKE ALL ON DATABASE signal_test FROM PUBLIC")
        for role, variable in {
            "signal_migrator": "SIGNAL_MIGRATION_DSN",
            "signal_identity": "SIGNAL_TEST_IDENTITY_DSN",
            "signal_api": "SIGNAL_TEST_API_DSN",
            "signal_bootstrap": "SIGNAL_TEST_BOOTSTRAP_DSN",
            "signal_scheduler": "SIGNAL_TEST_SCHEDULER_DSN",
            "signal_authority_dispatcher": "SIGNAL_TEST_AUTHORITY_DISPATCHER_DSN",
            "signal_release_manager": "SIGNAL_TEST_RELEASE_MANAGER_DSN",
            "signal_workflow": "SIGNAL_TEST_WORKFLOW_DSN",
            "signal_crawl_admission": "SIGNAL_TEST_CRAWL_ADMISSION_DSN",
            "signal_crawl_ingest": "SIGNAL_TEST_CRAWL_INGEST_DSN",
        }.items():
            password = secrets.token_hex(32)
            connection.execute(
                sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )
            connection.execute(
                sql.SQL("GRANT CONNECT ON DATABASE signal_test TO {}").format(sql.Identifier(role))
            )
            env[variable] = make_conninfo(**common, user=role, password=password)
    return env, version


def source_hashes() -> dict:
    files = [
        ROOT / path
        for path in [
            "requirements.txt",
            "pyproject.toml",
            "scripts/database_lab.py",
            "scripts/run-database-tests.py",
            "scripts/database_shards.py",
            "scripts/test_shards.py",
            "scripts/lab_runtime.py",
            "tests/conftest.py",
        ]
    ]
    for directory in ["database", "services/control_plane/src", "tests/control_plane"]:
        files.extend(
            path
            for path in (ROOT / directory).rglob("*")
            if path.suffix in {".py", ".sql", ".ini", ".json"}
        )
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(files)
    }


def summarize_junit(filename: Path) -> dict:
    cases = ET.parse(filename).getroot().findall(".//testcase")
    if not cases:
        raise LabError("Empty integration-test report.")
    tests = []
    for case in cases:
        failed = any(case.find(tag) is not None for tag in ["failure", "error"])
        status = "FAIL" if failed else "PASS"
        if case.find("skipped") is not None:
            status = "SKIP"
        tests.append({"name": case.attrib["name"], "status": status})
    return {"tests": tests, "passed": sum(test["status"] == "PASS" for test in tests)}


def main() -> int:
    from database_shards import main as run_shards

    return run_shards()
