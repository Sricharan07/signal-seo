from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from signal_core.crawl_artifacts import EncryptedLocalArtifactStore
from signal_core.database import scoped_transaction
from test_shared_egress import authority


@pytest.fixture
def browser_session(api, scheduler, workflow, crawl_admission, crawl_ingest, scopes, tmp_path):
    run, *_ = authority(
        api,
        scheduler,
        workflow,
        crawl_admission,
        crawl_ingest,
        scopes[0],
        uuid4().hex,
        EncryptedLocalArtifactStore(tmp_path / "objects"),
    )
    session_id = uuid4()
    with scoped_transaction(workflow, scopes[0]):
        workflow.execute(
            "SELECT control.open_browser_session(" + ",".join(["%s"] * 12) + ")",
            (
                scopes[0].tenant_id,
                scopes[0].site_id,
                session_id,
                run.run_id,
                "render",
                "Synthetic read",
                "https://product.example.invalid/",
                "sha256:" + "a" * 64,
                1,
                60,
                1024,
                None,
            ),
        )
    return session_id


def record(
    workflow,
    scope,
    session_id,
    *,
    sequence=1,
    byte_count=0,
    action="finish",
    outcome="complete",
    operations=None,
):
    with scoped_transaction(workflow, scope):
        workflow.execute(
            "SELECT control.record_browser_step(" + ",".join(["%s"] * 10) + ")",
            (
                scope.tenant_id,
                scope.site_id,
                session_id,
                sequence,
                action,
                outcome,
                Jsonb(
                    {
                        "bytes": byte_count,
                        "goal": "Synthetic read",
                        "resulting_url": "https://product.example.invalid/",
                        "element_digest": "a" * 64,
                        "snapshot_digest": "b" * 64,
                    }
                ),
                Jsonb([]),
                None,
                operations or [],
            ),
        )


def test_browser_records_terminal_is_append_only(browser_session, workflow, scopes, admin):
    record(workflow, scopes[0], browser_session)
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        record(workflow, scopes[0], browser_session, sequence=2)
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        admin.execute(
            "UPDATE app.browser_steps SET outcome='failed' WHERE session_id=%s", (browser_session,)
        )


@pytest.mark.parametrize("which", [1, 2])
def test_same_tenant_wrong_site_and_wrong_tenant_cannot_record(
    browser_session, workflow, scopes, which
):
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        record(workflow, scopes[which], browser_session)


@pytest.mark.parametrize(
    "changes",
    [
        {"sequence": 2},
        {"byte_count": 1025},
        {"action": "click"},
        {"operations": [uuid4()]},
        {"action": "navigate", "outcome": "observed"},
    ],
)
def test_browser_record_sequence_budget_action_references_and_missing_snapshot_denied(
    browser_session, workflow, scopes, changes
):
    with pytest.raises((psycopg.errors.InvalidParameterValue, psycopg.errors.CheckViolation)):
        record(workflow, scopes[0], browser_session, **changes)


def test_browser_tables_are_forced_rls_and_runtime_cannot_mutate(admin, workflow):
    for table in ("browser_sessions", "browser_steps", "browser_step_egress"):
        assert admin.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass",
            ("app." + table,),
        ).fetchone() == (True, True)
        for role in (
            "signal_workflow",
            "signal_api",
            "signal_crawl_ingest",
            "signal_crawl_admission",
        ):
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                assert not admin.execute(
                    "SELECT has_table_privilege(%s,%s,%s)", (role, "app." + table, privilege)
                ).fetchone()[0]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            workflow.execute("SELECT * FROM app." + table)
