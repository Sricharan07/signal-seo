import asyncio
import hashlib
import os
from contextlib import contextmanager
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

import psycopg
from signal_core.commands import accept_snapshot
from signal_core.crawl_workflow import CrawlSiteWorkflow
from signal_core.crawl_workflow_activities import (
    CrawlSiteActivities,
    PostgresTerminalStore,
    WorkflowTerminalActivities,
)
from signal_core.database import Scope, scoped_transaction
from signal_core.outbox_worker import (
    AsyncDeliveryWorker,
    DeliveryCycleReport,
    DeliveryWorkerConfig,
    PostgresOutboxStore,
)
from signal_core.recipe_releases import VERIFIED_HOMEPAGE_METADATA_RELEASE_ID
from signal_core.recovery_authority import RecoveryGeneration
from signal_core.weekly_loop import WeeklySite
from signal_core.weekly_schedule import WeeklyScheduleReconciler
from signal_core.workflow_consumer import PostgresWorkflowStore, WorkflowEventPublisher
from signal_core.workflow_contracts import CrawlManifestReference, CrawlTerminalProjection
from signal_core.workflow_start import TemporalWorkflowStarter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker


class SyntheticCrawlExecutor:
    async def execute(self, command, *, heartbeat):
        manifest_id = uuid5(NAMESPACE_URL, f"signal:crawl:{command.command_id}")
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


@contextmanager
def connection(variable):
    with psycopg.connect(os.environ[variable], autocommit=True) as current:
        yield current


def create_scope() -> Scope:
    scope = Scope(uuid4(), uuid4())
    with connection("SIGNAL_TEST_BOOTSTRAP_DSN") as bootstrap:
        with scoped_transaction(bootstrap, scope):
            bootstrap.execute(
                "INSERT INTO app.tenants (tenant_id, name, home_region) "
                "VALUES (%s, 'Synthetic consumer tenant', 'test')",
                (scope.tenant_id,),
            )
            bootstrap.execute(
                "INSERT INTO control.tenant_directory VALUES (%s, 'active', 1)",
                (scope.tenant_id,),
            )
            bootstrap.execute(
                "INSERT INTO app.sites (tenant_id, id, name, primary_origin, "
                "timezone, reporting_currency) VALUES "
                "(%s, %s, 'Synthetic consumer site', "
                "'https://consumer.example.invalid', 'UTC', 'USD')",
                (scope.tenant_id, scope.site_id),
            )
    return scope


def accept(scope: Scope, key: str):
    with connection("SIGNAL_TEST_API_DSN") as api:
        return accept_snapshot(
            api,
            scope,
            actor_service="consumer-integration-test",
            idempotency_key=key,
        )


async def wait_for_outbox_lease(command_id) -> None:
    with connection("SIGNAL_TEST_ADMIN_DSN") as admin:
        remaining = admin.execute(
            "SELECT GREATEST(0, EXTRACT(EPOCH FROM lease_until - clock_timestamp())) "
            "FROM app.outbox WHERE aggregate_id = %s",
            (command_id,),
        ).fetchone()[0]
    await asyncio.sleep(float(remaining) + 0.02)


class FailFirstAcknowledgement:
    def __init__(self, delegate):
        self.delegate = delegate
        self.failed = False

    def list_tenants(self, **kwargs):
        return self.delegate.list_tenants(**kwargs)

    def claim(self, **kwargs):
        return self.delegate.claim(**kwargs)

    def acknowledge(self, envelope, *, worker_key):
        if not self.failed:
            self.failed = True
            raise RuntimeError("synthetic acknowledgement loss")
        return self.delegate.acknowledge(envelope, worker_key=worker_key)

    def reschedule(self, envelope, *, worker_key, delay_seconds):
        return self.delegate.reschedule(
            envelope,
            worker_key=worker_key,
            delay_seconds=delay_seconds,
        )


def make_worker(client, *, store=None, observations=None):
    outbox = store or PostgresOutboxStore(lambda: connection("SIGNAL_TEST_SCHEDULER_DSN"))
    publisher = WorkflowEventPublisher(
        store=PostgresWorkflowStore(lambda: connection("SIGNAL_TEST_WORKFLOW_DSN")),
        starter=TemporalWorkflowStarter(client),
        observer=None if observations is None else observations.append,
    )
    return AsyncDeliveryWorker(
        store=outbox,
        publisher=publisher,
        config=DeliveryWorkerConfig(
            worker_key="consumer.integration",
            tenant_page_size=100,
            outbox_batch_size=1,
            lease_seconds=1,
            retry_delay_seconds=1,
            idle_delay_seconds=0.05,
            active_delay_seconds=0.01,
        ),
        observer=None if observations is None else observations.append,
    )


async def listed_executions(client, workflow_id):
    return [item async for item in client.list_workflows(f'WorkflowId = "{workflow_id}"')]


async def exercise_real_boundaries(scopes, commands):
    environment = await WorkflowEnvironment.start_local(
        ip="127.0.0.1",
        ui=False,
        download_dest_dir=os.environ["SIGNAL_TEMPORAL_DOWNLOAD_DIR"],
        dev_server_download_version="default",
        dev_server_log_level="error",
    )
    observations = []
    tenant_by_command = {
        command.id: scope.tenant_id for command, scope in zip(commands, scopes, strict=True)
    }
    try:
        async with Worker(
            environment.client,
            task_queue="signal.crawl.v1",
            workflows=[CrawlSiteWorkflow],
            activities=[
                CrawlSiteActivities(SyntheticCrawlExecutor()).execute,
                WorkflowTerminalActivities(
                    PostgresTerminalStore(lambda: connection("SIGNAL_TEST_WORKFLOW_DSN"))
                ).record,
            ],
        ):
            first_report = await asyncio.wait_for(
                make_worker(environment.client, observations=observations).run_once(),
                timeout=30,
            )
            assert first_report == DeliveryCycleReport(
                active_tenants=2,
                claimed=2,
                delivered=2,
            )

            retry_command = accept(scopes[0], "consumer-ack-loss")
            tenant_by_command[retry_command.id] = scopes[0].tenant_id
            real_store = PostgresOutboxStore(lambda: connection("SIGNAL_TEST_SCHEDULER_DSN"))
            failed_store = FailFirstAcknowledgement(real_store)
            failed_report = await asyncio.wait_for(
                make_worker(
                    environment.client,
                    store=failed_store,
                    observations=observations,
                ).run_once(),
                timeout=30,
            )
            assert failed_report == DeliveryCycleReport(
                active_tenants=2,
                claimed=1,
                storage_failures=1,
            )

            await wait_for_outbox_lease(retry_command.id)
            retry_report = await asyncio.wait_for(
                make_worker(environment.client, observations=observations).run_once(),
                timeout=30,
            )
            assert retry_report == DeliveryCycleReport(
                active_tenants=2,
                claimed=1,
                delivered=1,
            )

            all_commands = [*commands, retry_command]
            results = []
            for command in all_commands:
                workflow_id = f"signal:CrawlSite:{tenant_by_command[command.id]}:{command.id}"
                handle = environment.client.get_workflow_handle(
                    workflow_id, result_type=CrawlTerminalProjection
                )
                results.append(await asyncio.wait_for(handle.result(), timeout=30))
            assert all(result.command_status == "succeeded" for result in results)
            assert all(result.workflow_state == "succeeded" for result in results)

            with connection("SIGNAL_TEST_ADMIN_DSN") as admin:
                rows = admin.execute(
                    "SELECT command.id, command.status, workflow.workflow_id, "
                    "workflow.first_run_id, workflow.state_projection, outbox.delivered_at, "
                    "outbox.attempt_count "
                    "FROM app.commands AS command "
                    "JOIN app.workflow_refs AS workflow "
                    "ON workflow.tenant_id = command.tenant_id "
                    "AND workflow.command_id = command.id "
                    "JOIN app.outbox AS outbox "
                    "ON outbox.tenant_id = command.tenant_id "
                    "AND outbox.aggregate_id = command.id "
                    "WHERE command.id = ANY(%s) ORDER BY command.id",
                    ([command.id for command in all_commands],),
                ).fetchall()
                assert len(rows) == 3
                assert all(row[1] == "succeeded" for row in rows)
                assert all(row[4] == "succeeded" for row in rows)
                assert all(row[5] is not None for row in rows)
                retry_row = next(row for row in rows if row[0] == retry_command.id)
                assert retry_row[6] == 2
                event_count = admin.execute(
                    "SELECT count(*) FROM app.command_events WHERE command_id = %s",
                    (retry_command.id,),
                ).fetchone()[0]
                assert event_count == 4

            executions = await asyncio.wait_for(
                listed_executions(environment.client, retry_row[2]),
                timeout=15,
            )
            assert len(executions) == 1
            assert executions[0].run_id == retry_row[3]
            assert any(getattr(item, "outcome", None) == "ack_failed" for item in observations)
            assert observations[-1].outcome == "delivered"
    finally:
        await environment.shutdown()


def test_consumer_crosses_real_postgresql_and_temporal_with_ack_loss_recovery():
    scopes = sorted((create_scope(), create_scope()), key=lambda scope: scope.tenant_id)
    commands = [accept(scope, f"consumer-happy-{index}") for index, scope in enumerate(scopes)]

    asyncio.run(exercise_real_boundaries(scopes, commands))


def create_weekly_observation() -> tuple[Scope, object, object]:
    scope = create_scope()
    user_id, grant_id = uuid4(), uuid4()
    with connection("SIGNAL_TEST_ADMIN_DSN") as admin:
        now = admin.execute("SELECT transaction_timestamp()").fetchone()[0]
        admin.execute(
            "UPDATE app.sites SET state='active', ownership_status='verified' "
            "WHERE tenant_id=%s AND id=%s",
            (scope.tenant_id, scope.site_id),
        )
        admin.execute(
            "INSERT INTO control.users "
            "(id,oidc_issuer,oidc_subject,display_name,contact_email) "
            "VALUES (%s,'https://identity.example.invalid',%s,'Synthetic weekly owner',%s)",
            (user_id, f"weekly-{user_id}", f"{user_id}@example.invalid"),
        )
        admin.execute(
            "INSERT INTO app.memberships "
            "(tenant_id,id,user_id,role_key,state,authorization_epoch) "
            "VALUES (%s,%s,%s,'owner','active',1)",
            (scope.tenant_id, uuid4(), user_id),
        )
        admin.execute(
            "INSERT INTO app.site_memberships "
            "(tenant_id,site_id,id,user_id,permission_set,authorization_epoch,state) "
            "VALUES (%s,%s,%s,%s,%s,1,'active')",
            (
                scope.tenant_id,
                scope.site_id,
                uuid4(),
                user_id,
                '{"permissions":["site.snapshot.request"],"schema_version":1}',
            ),
        )
        admin.execute(
            "INSERT INTO app.standing_authorizations "
            "(tenant_id,site_id,id,owner_user_id,membership_epoch,site_epoch,"
            "recovery_generation,recipe_release_ids,work_types,thresholds,"
            "weekly_volume_caps,weekly_total_cap,weekly_spend_cents,starts_at,ends_at,"
            "recovery_window_hours) VALUES "
            "(%s,%s,%s,%s,1,1,'test-generation-1',%s,ARRAY['draft_patch'],"
            "'{\"draft_patch\":0.8}'::jsonb,'{\"draft_patch\":1}'::jsonb,1,100,%s,%s,24)",
            (
                scope.tenant_id,
                scope.site_id,
                grant_id,
                user_id,
                [VERIFIED_HOMEPAGE_METADATA_RELEASE_ID],
                now,
                now + timedelta(days=7),
            ),
        )
        week = admin.execute(
            "SELECT date_trunc('week',transaction_timestamp() AT TIME ZONE 'UTC')::date"
        ).fetchone()[0]
    cycle_id = uuid4()
    with connection("SIGNAL_TEST_WORKFLOW_DSN") as workflow:
        assert (
            workflow.execute(
                "SELECT control.open_weekly_cycle(%s,%s,%s,%s,%s,%s,%s)",
                (
                    scope.tenant_id,
                    scope.site_id,
                    grant_id,
                    week,
                    cycle_id,
                    str(uuid4()),
                    "test-generation-1",
                ),
            ).fetchone()[0]
            == "opened"
        )
        command_id = workflow.execute(
            "SELECT control.admit_weekly_observation(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                scope.tenant_id,
                scope.site_id,
                week,
                cycle_id,
                grant_id,
                "test-generation-1",
                uuid4(),
                uuid4(),
                uuid4(),
            ),
        ).fetchone()[0]
    return scope, command_id, grant_id


async def exercise_weekly_consumer(scope, command_id):
    environment = await WorkflowEnvironment.start_local(
        ip="127.0.0.1",
        ui=False,
        download_dest_dir=os.environ["SIGNAL_TEMPORAL_DOWNLOAD_DIR"],
        dev_server_download_version="default",
        dev_server_log_level="error",
    )
    try:
        async with Worker(
            environment.client,
            task_queue="signal.crawl.v1",
            workflows=[CrawlSiteWorkflow],
            activities=[
                CrawlSiteActivities(SyntheticCrawlExecutor()).execute,
                WorkflowTerminalActivities(
                    PostgresTerminalStore(lambda: connection("SIGNAL_TEST_WORKFLOW_DSN"))
                ).record,
            ],
        ):
            result = await make_worker(environment.client).run_once()
            assert result.delivered == 1
            handle = environment.client.get_workflow_handle(
                f"signal:CrawlSite:{scope.tenant_id}:{command_id}",
                result_type=CrawlTerminalProjection,
            )
            terminal = await asyncio.wait_for(handle.result(), timeout=30)
            assert terminal.command_status == "succeeded"
            with connection("SIGNAL_TEST_ADMIN_DSN") as admin:
                assert (
                    admin.execute(
                        "SELECT status FROM app.commands WHERE tenant_id=%s AND id=%s",
                        (scope.tenant_id, command_id),
                    ).fetchone()[0]
                    == "succeeded"
                )
    finally:
        await environment.shutdown()


def test_weekly_observation_uses_existing_joint_consumer_without_duplicate_command():
    scope, command_id, _ = create_weekly_observation()
    asyncio.run(exercise_weekly_consumer(scope, command_id))


class WeeklyGeneration:
    async def current_generation(self):
        return RecoveryGeneration("test-generation-1", 1)


async def exercise_schedule_reconciliation(scope, grant_id):
    environment = await WorkflowEnvironment.start_local(
        ip="127.0.0.1",
        ui=False,
        download_dest_dir=os.environ["SIGNAL_TEMPORAL_DOWNLOAD_DIR"],
        dev_server_download_version="default",
        dev_server_log_level="error",
    )
    try:
        site = WeeklySite(str(scope.tenant_id), str(scope.site_id), str(grant_id))
        with connection("SIGNAL_TEST_SCHEDULER_DSN") as scheduler:
            reconciler = WeeklyScheduleReconciler(scheduler, environment.client, WeeklyGeneration())
            assert await reconciler.reconcile(site) == "created"
            handle = environment.client.get_schedule_handle(
                f"signal:weekly:{scope.tenant_id}:{scope.site_id}"
            )
            assert not (await handle.describe()).schedule.state.paused
            with connection("SIGNAL_TEST_ADMIN_DSN") as admin:
                admin.execute(
                    "INSERT INTO app.site_weekly_control(tenant_id,site_id,paused) "
                    "VALUES (%s,%s,true)",
                    (scope.tenant_id, scope.site_id),
                )
            assert await reconciler.reconcile(site) == "paused"
            assert (await handle.describe()).schedule.state.paused
    finally:
        await environment.shutdown()


def test_verified_grant_schedule_is_paused_when_site_authority_disappears():
    scope, _, grant_id = create_weekly_observation()
    asyncio.run(exercise_schedule_reconciliation(scope, grant_id))
