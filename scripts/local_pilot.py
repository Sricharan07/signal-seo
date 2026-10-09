"""Run Signal's disposable, loopback-only owner journey on real dependencies."""

import asyncio
import hashlib
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack, contextmanager, nullcontext
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx2
import psycopg
import uvicorn
from anyio import to_thread
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api/src"))
sys.path.insert(0, str(ROOT / "services/control_plane/src"))
sys.path.insert(0, str(ROOT / "scripts"))

import database_lab  # noqa: E402
import keycloak_lab  # noqa: E402
import openbao_lab  # noqa: E402
from signal_api.authentication import ComposedBrowserLogin  # noqa: E402
from signal_api.browser_security import BrowserSecurity  # noqa: E402
from signal_api.config import ApiSettings  # noqa: E402
from signal_api.main import create_app  # noqa: E402
from signal_core.crawl_artifacts import (  # noqa: E402
    ArtifactEncryptionKey,
    EncryptedLocalArtifactStore,
)
from signal_core.crawl_http import BoundedSystemResolver, PinnedHttpFetcher  # noqa: E402
from signal_core.crawl_workflow import CrawlSiteWorkflow  # noqa: E402
from signal_core.crawl_workflow_activities import (  # noqa: E402
    CrawlSiteActivities,
    PostgresTerminalStore,
    WorkflowTerminalActivities,
)
from signal_core.full_site_crawl import FullSiteCrawlExecutor  # noqa: E402
from signal_core.model_reasoning import OpenAIResponsesAdapter  # noqa: E402
from signal_core.oidc_login import OidcClientRegistration  # noqa: E402
from signal_core.oidc_protocol import discover_keycloak  # noqa: E402
from signal_core.outbox_worker import (  # noqa: E402
    AsyncDeliveryWorker,
    DeliveryWorkerConfig,
    PostgresOutboxStore,
)
from signal_core.workflow_consumer import (  # noqa: E402
    PostgresWorkflowStore,
    WorkflowEventPublisher,
)
from signal_core.workflow_contracts import CrawlManifestReference  # noqa: E402
from signal_core.workflow_start import TemporalWorkflowStarter  # noqa: E402

PILOT_REALM_PATH = ROOT / "deploy/local-pilot/keycloak-realm.json"
PILOT_REALM = "signal-local-pilot"
PILOT_CLIENT_ID = "signal-local-pilot-dashboard"
PILOT_DASHBOARD_PORTS = (3000, 3001)
PILOT_REDIRECT_URIS = tuple(
    f"http://localhost:{port}/auth/callback" for port in PILOT_DASHBOARD_PORTS
)
PILOT_USERNAME = "signal-local-owner"
PILOT_PASSWORD = "signal-local-only-password"
PILOT_SUBJECT = "11111111-1111-4111-8111-111111111111"
PILOT_USER_ID = UUID("22222222-2222-4222-8222-222222222222")
PILOT_TENANT_ID = UUID("33333333-3333-4333-8333-333333333333")
PILOT_MEMBERSHIP_ID = UUID("44444444-4444-4444-8444-444444444444")
API_ORIGIN = "http://127.0.0.1:8000"
TEMPORAL_DOWNLOAD = ROOT / ".runtime" / "local-pilot" / "temporal"


class LocalPilotError(RuntimeError):
    """A local pilot dependency failed without exposing credential material."""


def build_pilot_origin_fetcher() -> PinnedHttpFetcher:
    """Build the pilot's real, bounded public-origin network boundary."""
    return PinnedHttpFetcher(BoundedSystemResolver())


class LocalPilotCookieTransport:
    """Adapt secure host cookies only across the loopback pilot HTTP boundary."""

    def __init__(self, app, *, generation: str) -> None:
        self.app = app
        self.cookie_names = pilot_cookie_names(generation)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        adapted_scope = dict(scope)
        adapted_scope["headers"] = [
            (key, self._to_secure_cookie_header(value) if key.lower() == b"cookie" else value)
            for key, value in scope["headers"]
        ]

        async def send_adapted(message) -> None:
            if message["type"] != "http.response.start":
                await send(message)
                return
            adapted = dict(message)
            adapted["headers"] = [
                (
                    key,
                    self._to_local_set_cookie(value) if key.lower() == b"set-cookie" else value,
                )
                for key, value in message["headers"]
            ]
            await send(adapted)

        await self.app(adapted_scope, receive, send_adapted)

    def _to_secure_cookie_header(self, value: bytes) -> bytes:
        text = value.decode("latin-1")
        cookies: list[str] = []
        local_to_secure = {local: secure for secure, local in self.cookie_names.items()}
        for fragment in text.split(";"):
            stripped = fragment.lstrip()
            leading = fragment[: len(fragment) - len(stripped)]
            name, separator, cookie_value = stripped.partition("=")
            if separator:
                name = local_to_secure.get(name, name)
                fragment = f"{leading}{name}={cookie_value}"
            cookies.append(fragment)
        return ";".join(cookies).encode("latin-1")

    def _to_local_set_cookie(self, value: bytes) -> bytes:
        text = value.decode("latin-1")
        for secure_name, local_name in self.cookie_names.items():
            if text.startswith(f"{secure_name}="):
                text = f"{local_name}={text.removeprefix(f'{secure_name}=')}"
                text = text.replace("; Secure", "")
                break
        return text.encode("latin-1")


def pilot_cookie_names(generation: str) -> dict[str, str]:
    if re.fullmatch(r"[0-9a-f]{8}", generation) is None:
        raise LocalPilotError("Invalid local pilot cookie generation.")
    return {
        "__Host-signal_identity": f"signal_local_{generation}_identity",
        "__Host-signal_session": f"signal_local_{generation}_session",
        "__Host-signal_oidc_binding": f"signal_local_{generation}_oidc_binding",
        "__Host-signal_invitation_identity": (f"signal_local_{generation}_invitation_identity"),
    }


class LocalPilotCrawlExecutor:
    """Complete one visibly durable walkthrough without contacting a customer site."""

    async def execute(self, command, *, heartbeat) -> CrawlManifestReference:
        del heartbeat
        await asyncio.sleep(0.75)
        manifest_id = uuid5(NAMESPACE_URL, f"signal:local-pilot:{command.command_id}")
        digest = hashlib.sha256(command.command_id.encode("ascii")).hexdigest()
        return CrawlManifestReference(
            schema_version=1,
            manifest_id=str(manifest_id),
            manifest_sha256=digest,
            coverage="complete",
            discovered_count=1,
            terminal_count=1,
            scope_version=command.scope_version,
            crawl_policy_version=command.crawl_policy_version,
        )


def build_pilot_crawl_executor(
    *,
    verified_crawl: bool,
    crawl_admission_dsn: str,
    crawl_ingest_dsn: str,
    artifact_directory: str | None,
    artifact_key: ArtifactEncryptionKey | None,
) -> LocalPilotCrawlExecutor | FullSiteCrawlExecutor:
    if not verified_crawl:
        return LocalPilotCrawlExecutor()
    if artifact_directory is None or artifact_key is None:
        raise LocalPilotError("Verified crawl artifacts are unavailable.")
    return FullSiteCrawlExecutor(
        admission_connection_factory=connection_factory(crawl_admission_dsn),
        ingest_connection_factory=connection_factory(crawl_ingest_dsn),
        store=EncryptedLocalArtifactStore(Path(artifact_directory).resolve(strict=True)),
        artifact_key=artifact_key,
        fetcher=build_pilot_origin_fetcher(),
        network_profile_sha256=hashlib.sha256(b"signal-local-pilot-pinned-crawl-v1").hexdigest(),
        worker_key="local.pilot.crawl",
    )


@contextmanager
def isolated_pilot_keycloak():
    """Start one real Keycloak instance on an ephemeral loopback HTTP port."""
    keycloak_lab.require_free_space(ROOT)
    keycloak_lab.docker("info", "--format", "{{.ServerVersion}}", timeout=10)
    keycloak_lab.docker("image", "pull", keycloak_lab.IMAGE, timeout=300)
    name = f"signal-keycloak-tests-{secrets.token_hex(6)}"
    try:
        keycloak_lab.docker(
            "container",
            "create",
            "--name",
            name,
            "--label",
            f"{keycloak_lab.LABEL}={name}",
            "--user",
            keycloak_lab.keycloak_container_user(),
            "--publish",
            "127.0.0.1::8080",
            "--memory",
            "768m",
            "--cpus",
            "1",
            "--pids-limit",
            "256",
            "--security-opt",
            "no-new-privileges:true",
            "--mount",
            f"type=bind,src={PILOT_REALM_PATH},dst=/opt/keycloak/data/import/signal-local-pilot.json,ro",
            keycloak_lab.IMAGE,
            "start-dev",
            "--import-realm",
            "--http-enabled=true",
            "--http-port=8080",
            "--hostname-strict=false",
            timeout=180,
        )
        keycloak_lab.docker("container", "start", name)
        description = json.loads(keycloak_lab.docker("inspect", name))[0]
        bindings = description["NetworkSettings"]["Ports"].get("8080/tcp")
        if not bindings or len(bindings) != 1 or bindings[0]["HostIp"] != "127.0.0.1":
            raise LocalPilotError("Local pilot Keycloak must use one loopback-only port.")
        base_url = f"http://127.0.0.1:{bindings[0]['HostPort']}"
        discovery = f"{base_url}/realms/{PILOT_REALM}/.well-known/openid-configuration"
        with httpx2.Client(
            timeout=2,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            keycloak_lab.wait_for_keycloak(client, discovery)
        version_output = keycloak_lab.docker(
            "container", "exec", name, "/opt/keycloak/bin/kc.sh", "--version"
        )
        if version_output.splitlines()[0] != keycloak_lab.EXPECTED_VERSION:
            raise LocalPilotError("Unexpected Keycloak version for the local pilot.")
        yield base_url
    finally:
        keycloak_lab.cleanup(name)


def migrate_database(environment: dict[str, str]) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "database/alembic.ini",
            "upgrade",
            "head",
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=180,
    )
    if result.returncode != 0:
        raise LocalPilotError("Local pilot database migration failed.")


def seed_pilot_owner(admin_dsn: str, issuer: str) -> None:
    """Create only the synthetic owner and empty organization needed for login."""
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        connection.execute(
            "INSERT INTO app.tenants (tenant_id, name, home_region) "
            "VALUES (%s, 'Local pilot workspace (synthetic identity)', 'local')",
            (PILOT_TENANT_ID,),
        )
        connection.execute(
            "INSERT INTO control.tenant_directory "
            "(tenant_id, lifecycle, provisioning_generation) "
            "VALUES (%s, 'active', 1)",
            (PILOT_TENANT_ID,),
        )
        connection.execute(
            "INSERT INTO control.users "
            "(id, oidc_issuer, oidc_subject, display_name, contact_email) "
            "VALUES (%s, %s, %s, 'Local Owner', 'signal-local-owner@example.invalid')",
            (PILOT_USER_ID, issuer, PILOT_SUBJECT),
        )
        connection.execute(
            "INSERT INTO app.memberships "
            "(tenant_id, id, user_id, role_key, state, authorization_epoch) "
            "VALUES (%s, %s, %s, 'owner', 'active', 1)",
            (PILOT_TENANT_ID, PILOT_MEMBERSHIP_ID, PILOT_USER_ID),
        )


def connection_factory(dsn: str):
    def connect():
        return psycopg.connect(dsn, autocommit=True)

    return connect


@contextmanager
def managed_connection(dsn: str):
    with psycopg.connect(dsn, autocommit=True) as connection:
        yield connection


def require_available_ports() -> int:
    configured = os.environ.get("SIGNAL_LOCAL_PILOT_DASHBOARD_PORT")
    if configured is not None and configured not in {str(port) for port in PILOT_DASHBOARD_PORTS}:
        raise LocalPilotError("Local pilot dashboard port must be 3000 or 3001.")
    candidates = (int(configured),) if configured is not None else PILOT_DASHBOARD_PORTS
    unavailable: set[int] = set()
    for port in (8000, *candidates):
        try:
            with socket.create_connection(("localhost", port), timeout=0.1):
                unavailable.add(port)
                continue
        except OSError:
            pass
        try:
            with socket.create_server(("127.0.0.1", port)):
                pass
        except OSError:
            unavailable.add(port)
    if 8000 in unavailable:
        raise LocalPilotError("Loopback API port 8000 is already in use.")
    for port in candidates:
        if port not in unavailable:
            return port
    if configured is not None:
        raise LocalPilotError(f"Loopback dashboard port {configured} is already in use.")
    raise LocalPilotError("Loopback dashboard ports 3000 and 3001 are already in use.")


async def wait_for_url(url: str, process: subprocess.Popen | None = None) -> None:
    deadline = time.monotonic() + 90
    async with httpx2.AsyncClient(timeout=2, follow_redirects=False, trust_env=False) as client:
        while True:
            if process is not None and process.poll() is not None:
                raise LocalPilotError("The local pilot dashboard stopped during startup.")
            try:
                response = await client.get(url)
                if response.status_code == 200:
                    return
            except httpx2.HTTPError:
                pass
            if time.monotonic() >= deadline:
                raise LocalPilotError("The local pilot did not become reachable in time.")
            await asyncio.sleep(0.2)


async def serve_local_pilot(
    app,
    provider_origin: str,
    *,
    cookie_generation: str,
    dashboard_origin: str,
    dashboard_port: int,
    scheduler_dsn: str,
    workflow_dsn: str,
    verified_crawl: bool,
    crawl_admission_dsn: str,
    crawl_ingest_dsn: str,
    artifact_directory: str | None,
    artifact_key: ArtifactEncryptionKey | None,
) -> None:
    TEMPORAL_DOWNLOAD.mkdir(parents=True, exist_ok=True)
    temporal = await WorkflowEnvironment.start_local(
        ip="127.0.0.1",
        ui=False,
        download_dest_dir=str(TEMPORAL_DOWNLOAD),
        dev_server_download_version="default",
        dev_server_log_level="error",
    )
    api = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=8000,
            access_log=False,
            log_level="warning",
        )
    )
    api.capture_signals = nullcontext
    api_task = asyncio.create_task(api.serve())
    dashboard: subprocess.Popen | None = None
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    handled_signals = (signal.SIGINT, signal.SIGTERM)
    for handled_signal in handled_signals:
        loop.add_signal_handler(handled_signal, stop_event.set)
    outbox_worker = AsyncDeliveryWorker(
        store=PostgresOutboxStore(lambda: managed_connection(scheduler_dsn)),
        publisher=WorkflowEventPublisher(
            store=PostgresWorkflowStore(lambda: managed_connection(workflow_dsn)),
            starter=TemporalWorkflowStarter(temporal.client),
        ),
        config=DeliveryWorkerConfig(
            worker_key="local.pilot",
            outbox_batch_size=1,
            lease_seconds=5,
            retry_delay_seconds=2,
            idle_delay_seconds=0.1,
            active_delay_seconds=0.01,
        ),
    )
    delivery_task: asyncio.Task | None = None
    crawl_executor = build_pilot_crawl_executor(
        verified_crawl=verified_crawl,
        crawl_admission_dsn=crawl_admission_dsn,
        crawl_ingest_dsn=crawl_ingest_dsn,
        artifact_directory=artifact_directory,
        artifact_key=artifact_key,
    )
    try:
        async with Worker(
            temporal.client,
            task_queue="signal.crawl.v1",
            workflows=[CrawlSiteWorkflow],
            activities=[
                CrawlSiteActivities(crawl_executor).execute,
                WorkflowTerminalActivities(
                    PostgresTerminalStore(lambda: managed_connection(workflow_dsn))
                ).record,
            ],
        ):
            delivery_task = asyncio.create_task(outbox_worker.run_forever(stop_event))
            await wait_for_url(f"{API_ORIGIN}/health/ready")
            dashboard_environment = dict(
                os.environ,
                SIGNAL_API_BASE_URL=API_ORIGIN,
                SIGNAL_DASHBOARD_ORIGIN=dashboard_origin,
                SIGNAL_IDENTITY_PROVIDER_ORIGIN=provider_origin,
                SIGNAL_LOCAL_PILOT="1",
                SIGNAL_LOCAL_PILOT_VERIFIED_CRAWL="1" if verified_crawl else "0",
                SIGNAL_LOCAL_PILOT_COOKIE_GENERATION=cookie_generation,
            )
            dashboard = subprocess.Popen(
                [
                    "npm",
                    "--workspace",
                    "@signal/dashboard",
                    "run",
                    "dev",
                    "--",
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    str(dashboard_port),
                ],
                cwd=ROOT,
                env=dashboard_environment,
            )
            await wait_for_url(dashboard_origin, dashboard)
            print("\nSignal local pilot is ready.", flush=True)
            print(f"Dashboard: {dashboard_origin}", flush=True)
            print(f"Username:  {PILOT_USERNAME}", flush=True)
            print(f"Password:  {PILOT_PASSWORD}", flush=True)
            print(
                "Work, Pages, Signal Chat, and Approvals form the local supervised walkthrough.",
                flush=True,
            )
            print(
                "Data and authority are disposable; production writes remain disabled.\n",
                flush=True,
            )
            while not stop_event.is_set() and not api_task.done():
                if dashboard.poll() is not None:
                    api.should_exit = True
                    raise LocalPilotError("The local pilot dashboard stopped unexpectedly.")
                await asyncio.sleep(0.25)
            if api_task.done():
                await api_task
    finally:
        stop_event.set()
        api.should_exit = True
        if dashboard is not None and dashboard.poll() is None:
            dashboard.terminate()
            try:
                dashboard.wait(timeout=10)
            except subprocess.TimeoutExpired:
                dashboard.kill()
                dashboard.wait(timeout=5)
        if not api_task.done():
            await api_task
        if delivery_task is not None:
            await delivery_task
        await temporal.shutdown()
        for handled_signal in handled_signals:
            loop.remove_signal_handler(handled_signal)


def build_and_run() -> None:
    model_api_key = os.environ.pop("SIGNAL_OPENAI_API_KEY", None)
    crawl_mode = os.environ.get("SIGNAL_LOCAL_PILOT_VERIFIED_CRAWL", "0")
    if crawl_mode not in {"0", "1"}:
        raise LocalPilotError("Local verified crawl mode must be 0 or 1.")
    verified_crawl = crawl_mode == "1"
    dashboard_port = require_available_ports()
    dashboard_origin = f"http://localhost:{dashboard_port}"
    dashboard_redirect_uri = f"{dashboard_origin}/auth/callback"
    with ExitStack() as stack:
        brand_directory = stack.enter_context(
            tempfile.TemporaryDirectory(prefix="signal-pilot-brand-")
        )
        artifact_directory = (
            stack.enter_context(tempfile.TemporaryDirectory(prefix="signal-pilot-crawl-"))
            if verified_crawl
            else None
        )
        artifact_key = (
            ArtifactEncryptionKey("local-pilot-invocation", secrets.token_bytes(32))
            if verified_crawl
            else None
        )
        admin_dsn, common = stack.enter_context(database_lab.isolated_postgres())
        database_environment, _ = database_lab.provision(admin_dsn, common)
        migrate_database(database_environment)
        openbao = stack.enter_context(openbao_lab.isolated_openbao())
        writer, consumer, recovery, _ = openbao_lab.provision(openbao)
        brand_key_reader = openbao_lab.provision_brand_artifact_key(
            openbao, secrets.token_bytes(32)
        )
        model_adapter = None
        if model_api_key is not None:
            model_credential = openbao_lab.provision_model_credential(openbao, model_api_key)
            model_adapter = OpenAIResponsesAdapter(
                credential=model_credential,
                credential_verify=openbao.tls_context,
            )
        model_api_key = None
        provider_origin = stack.enter_context(isolated_pilot_keycloak())
        issuer = f"{provider_origin}/realms/{PILOT_REALM}"
        registration = OidcClientRegistration(
            issuer=issuer,
            client_id=PILOT_CLIENT_ID,
            redirect_uri=dashboard_redirect_uri,
        )
        seed_pilot_owner(admin_dsn, issuer)
        identity_dsn = database_environment["SIGNAL_TEST_IDENTITY_DSN"]
        login = ComposedBrowserLogin(
            connection_factory=connection_factory(identity_dsn),
            writer_connection_factory=connection_factory(
                database_environment["SIGNAL_TEST_API_DSN"]
            ),
            registration=registration,
            pkce_writer=writer,
            pkce_consumer=consumer,
            recovery_authority=recovery,
            origin_fetcher=build_pilot_origin_fetcher(),
            oidc_verify=True,
            pkce_verify=openbao.tls_context,
            recovery_verify=openbao.tls_context,
            model_adapter=model_adapter,
            brand_store=EncryptedLocalArtifactStore(Path(brand_directory).resolve(strict=True)),
            brand_key_reader=brand_key_reader,
        )

        async def ready() -> bool:
            try:
                await to_thread.run_sync(_check_database, identity_dsn)
                await recovery.current_generation(verify=openbao.tls_context)
                await discover_keycloak(registration)
                return True
            except Exception:
                return False

        cookie_generation = secrets.token_hex(4)
        app = LocalPilotCookieTransport(
            create_app(
                settings=ApiSettings(environment="development", expose_docs=False),
                readiness_probe=ready,
                browser_security=BrowserSecurity(
                    csrf_hmac_key=secrets.token_bytes(32),
                    allowed_origins=frozenset({dashboard_origin}),
                ),
                browser_login=login,
                browser_fixture_analysis=login,
                browser_proposals=login,
                browser_documents=login,
                browser_brain=login,
                browser_pagespeed=login,
                browser_writer=login,
                browser_strategy=login,
                browser_visibility=login,
                browser_candidate_inbox=login,
                browser_github_pr_operations=login,
                browser_github_delivery=login,
            ),
            generation=cookie_generation,
        )
        asyncio.run(
            serve_local_pilot(
                app,
                provider_origin,
                cookie_generation=cookie_generation,
                dashboard_origin=dashboard_origin,
                dashboard_port=dashboard_port,
                scheduler_dsn=database_environment["SIGNAL_TEST_SCHEDULER_DSN"],
                workflow_dsn=database_environment["SIGNAL_TEST_WORKFLOW_DSN"],
                verified_crawl=verified_crawl,
                crawl_admission_dsn=database_environment["SIGNAL_TEST_CRAWL_ADMISSION_DSN"],
                crawl_ingest_dsn=database_environment["SIGNAL_TEST_CRAWL_INGEST_DSN"],
                artifact_directory=artifact_directory,
                artifact_key=artifact_key,
            )
        )


def _check_database(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True, connect_timeout=2) as connection:
        connection.execute("SELECT 1").fetchone()


def main() -> int:
    try:
        build_and_run()
        return 0
    except KeyboardInterrupt:
        return 130
    except (
        LocalPilotError,
        database_lab.LabError,
        keycloak_lab.LabError,
        openbao_lab.LabError,
    ) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
