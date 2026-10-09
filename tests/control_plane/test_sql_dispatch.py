"""Equivalence against independently captured, accepted 0099 SQL definitions."""

import json
import os
import re
import subprocess
import sys
from hashlib import sha256
from itertools import product
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

BASELINE = json.loads((Path(__file__).parent / "fixtures/sql_dispatch_0099.json").read_text())
ENUMS = [c for c in BASELINE["constraints"] if "ANY (ARRAY" in c[3] and " OR " not in c[3]]
ENUMS += [
    c for c in BASELINE["constraints"] if c[2] == "owner_connector_egress_operations_profile_check"
]
CATALOGS = (
    "sql_dispatch_values",
    "sql_dispatch_shapes",
    "authority_restriction_kinds",
    "platform_event_references",
    "owner_egress_profiles",
    "owner_egress_request_rules",
    "shared_egress_profiles",
    "shared_egress_request_rules",
    "strategy_source_dispatch",
)


def old_expression(admin, expression, table, row):
    return admin.execute(
        sql.SQL("SELECT ({}) FROM jsonb_populate_record(NULL::{},%s)").format(
            sql.SQL(expression), sql.Identifier(*table.split("."))
        ),
        (Jsonb(row),),
    ).fetchone()[0]


@pytest.mark.parametrize("constraint", ENUMS, ids=lambda c: c[2])
def test_every_old_enum_value_and_denial_is_equivalent(admin, constraint):
    schema, table, name, expression = constraint
    column = re.search(r"\b(\w+) =", expression)[1]
    values = set(re.findall(r"'([^']*)'::text", expression))
    for value in [
        *sorted(values),
        None,
        "",
        "unknown",
        " synthetic ",
        *[v.upper() for v in values],
    ]:
        before = old_expression(admin, expression, f"{schema}.{table}", {column: value})
        after = admin.execute(
            "SELECT control.sql_dispatch_value_allowed(%s,%s)", (name, value)
        ).fetchone()[0]
        assert after is before, (name, value)


def test_every_authority_pair_and_unknown_is_equivalent(admin):
    constraints = [c for c in BASELINE["constraints"] if c[1].startswith("authority_")]
    kinds = [
        r[0] for r in admin.execute("SELECT target_kind FROM control.authority_restriction_kinds")
    ]
    restrictions = [
        r[0]
        for r in admin.execute("SELECT restriction_kind FROM control.authority_restriction_kinds")
    ]
    for target, restriction in product([*kinds, None, "unknown"], [*restrictions, None, "unknown"]):
        for schema, table, name, expression in constraints:
            before = old_expression(
                admin,
                expression,
                f"{schema}.{table}",
                {
                    "target_kind": target,
                    "restriction_kind": restriction,
                },
            )
            after = admin.execute(
                "SELECT control.authority_kind_allowed(%s,%s,%s)",
                (target, restriction, "_target_" in name),
            ).fetchone()[0]
            assert after is before, (name, target, restriction)


def event_rows():
    identifier = str(uuid4())
    rows = []
    for event, kind, reason, facts in [
        (
            "identity.session.issued",
            "identity_session",
            None,
            {"schema_version": 1, "authentication_level": "mfa"},
        ),
        ("identity.login.failed", "oidc_login_attempt", "pkce_unavailable", {"schema_version": 1}),
        (
            "identity.session.revoked",
            "identity_session",
            "user_logout",
            {"schema_version": 1, "presented_session_kind": "tenant"},
        ),
        (
            "authority.restriction.acknowledged",
            "authority_restriction",
            None,
            {
                "schema_version": 1,
                "original_event_id": identifier,
                "journal_record_id": identifier,
                "stream_generation": identifier,
                "stream_position": "1",
                "payload_hash": "a" * 64,
                "verified_at": "2026-01-01T00:00:00Z",
            },
        ),
        (
            "recipe.release.revoked",
            "recipe_release",
            "operator_revocation",
            {
                "schema_version": 1,
                "status_event_id": identifier,
                "restriction_kind": "recipe_release_revoked",
            },
        ),
        (
            "standing.grant.revoked",
            "standing_grant",
            "owner_revocation",
            {
                "schema_version": 1,
                "grant_id": identifier,
                "restriction_kind": "standing_grant_revoked",
            },
        ),
        *[
            (
                event,
                kind,
                "owner_revocation",
                {
                    "schema_version": 1,
                    "restriction_kind": kind + "_revoked",
                    "target_id": identifier,
                },
            )
            for event, kind in [
                ("slack.binding.revoked", "slack_binding"),
                ("slack.link.revoked", "slack_link"),
                ("telegram.binding.revoked", "telegram_binding"),
                ("telegram.link.revoked", "telegram_link"),
                ("ga4.binding.revoked", "ga4_binding"),
                ("wordpress.binding.revoked", "wordpress_binding"),
                ("docs.binding.revoked", "docs_binding"),
                ("webflow.binding.revoked", "webflow_binding"),
                ("github.pr.revoked", "github_pr_extension"),
                ("invitation.revoked", "invitation"),
            ]
        ],
    ]:
        row = {
            "id": identifier,
            "object_id": identifier,
            "actor_user_id": identifier,
            "event_type": event,
            "object_kind": kind,
            "reason": reason,
            "facts": facts,
        }
        if event == "identity.login.failed":
            row["actor_user_id"] = None
        rows.append(row)
        for column in ["event_type", "object_kind", "reason", "actor_user_id", "object_id"]:
            rows.append({**row, column: None})
            if column in {"event_type", "object_kind", "reason"}:
                rows.append({**row, column: "unknown"})
        for key in facts:
            rows.append({**row, "facts": {k: v for k, v in facts.items() if k != key}})
            rows.append({**row, "facts": {**facts, key: None}})
            rows.append({**row, "facts": {**facts, key: "invalid"}})
        rows.extend({**row, "facts": f} for f in [None, {}, [], {**facts, "extra": True}])
    # Preserve the old cross-product of chat event names and object kinds, not
    # a seemingly nicer one-to-one mapping that would change the CHECK contract.
    chat = [r for r in rows if r["event_type"] == "slack.binding.revoked"]
    for row in chat:
        for kind in ["slack_binding", "slack_link", "telegram_binding", "telegram_link"]:
            if isinstance(row["facts"], dict):
                rows.append(
                    {
                        **row,
                        "object_kind": kind,
                        "facts": {
                            **row["facts"],
                            "restriction_kind": kind + "_revoked",
                        },
                    }
                )
    for reason in [
        "provider_configuration_failed",
        "pkce_unavailable",
        "pkce_consume_failed",
        "provider_assertion_failed",
        "identity_not_authorized",
        "session_persistence_failed",
        "invitation_identity_not_verified",
        "invitation_proof_persistence_failed",
    ]:
        rows.append(
            {
                "id": identifier,
                "object_id": identifier,
                "actor_user_id": None,
                "event_type": "identity.login.failed",
                "object_kind": "oidc_login_attempt",
                "reason": reason,
                "facts": {"schema_version": 1},
            }
        )
    return rows


def test_platform_and_audit_shapes_preserve_three_valued_checks(admin):
    for schema, table, name, expression in BASELINE["constraints"]:
        if name == "platform_events_contract_check":
            rows = event_rows()
        elif name == "audit_events_event_type_check":
            rows = [
                {"event_type": e, "aggregate_sequence": n}
                for e, n in product(
                    [
                        "invitation.created",
                        "invitation.accepted",
                        "invitation.revoked",
                        "unknown",
                        None,
                    ],
                    [None, 0, 1, 2, 3],
                )
            ]
        else:
            continue
        for row in rows:
            before = old_expression(admin, expression, f"{schema}.{table}", row)
            after = admin.execute(
                "SELECT control.sql_dispatch_shape_allowed(%s,%s)", (name, Jsonb(row))
            ).fetchone()[0]
            assert after is before, (name, row)


@pytest.mark.parametrize(
    "name",
    [
        "candidate_recipe_evidence_kind",
        "command_events_shape_check",
        "admission_leases_workload_id_check",
    ],
)
def test_other_growing_shapes_preserve_values_nulls_and_failures(admin, name):
    schema, table, _, expression = next(c for c in BASELINE["constraints"] if c[2] == name)
    if name == "admission_leases_workload_id_check":
        suffix = f"{uuid4()}:{uuid4()}"
        rows = [
            {"workload_id": v}
            for v in [
                None,
                "",
                "unknown",
                *[f"{prefix}:{suffix}" for prefix in ["crawl", "egress", "owner", "psi"]],
                f"other:{suffix}",
                f"crawl:{suffix}\n",
                f"crawl:{suffix.upper()}",
            ]
        ]
    elif name == "candidate_recipe_evidence_kind":
        rows = []
        for finding, approval, eligible, placement, report in product(
            ["indexnow.key.required", "links.internal.add", "unknown", None],
            ["owner_review", "A4", "A2", None],
            [False, True, None],
            [False, True],
            [None, str(uuid4())],
        ):
            manifest = {
                "evidence": {"finding": {"key": finding}},
                "approval_class": approval,
                "autonomy_eligible": eligible,
            }
            if placement:
                manifest["static_key_placement"] = None
            rows.append(
                {
                    "audit_report_id": report,
                    "canonical_manifest": "\\x" + json.dumps(manifest).encode().hex(),
                }
            )
        rows.extend(
            {"audit_report_id": None, "canonical_manifest": v} for v in [None, "\\xff", "\\x7b"]
        )
    else:
        tenant, command = str(uuid4()), str(uuid4())
        workflow = f"signal:CrawlSite:{tenant}:{command}"
        run = str(uuid4())
        result = {
            "schema_version": 1,
            "kind": "crawl_manifest",
            "manifest_id": str(uuid4()),
            "manifest_sha256": "a" * 64,
            "coverage": "complete",
            "discovered_count": 1,
            "terminal_count": 1,
            "scope_version": 1,
            "crawl_policy_version": 1,
        }
        samples = [
            (1, "command.accepted", {"schema_version": 1}),
            (
                2,
                "command.workflow_admitted",
                {
                    "schema_version": 1,
                    "consumer_key": "workflow.command-start.v1",
                    "workflow_id": workflow,
                },
            ),
            *[
                (
                    3,
                    "command.workflow_started",
                    {
                        "schema_version": 1,
                        "first_run_id": run,
                        "start_evidence": evidence,
                        "workflow_id": workflow,
                    },
                )
                for evidence in ["start_acknowledged", "already_started"]
            ],
            (
                4,
                "command.workflow_succeeded",
                {
                    "schema_version": 1,
                    "first_run_id": run,
                    "result_reference": result,
                    "workflow_id": workflow,
                },
            ),
            *[
                (
                    4,
                    kind,
                    {
                        "schema_version": 1,
                        "first_run_id": run,
                        "reason": reason,
                        "workflow_id": workflow,
                    },
                )
                for kind, reason in [
                    ("command.workflow_failed", "crawl_activity_failed"),
                    ("command.workflow_cancelled", "crawl_cancelled"),
                ]
            ],
        ]
        rows = []
        for number, event, facts in samples:
            row = {
                "event_number": number,
                "event_type": event,
                "facts": facts,
                "tenant_id": tenant,
                "command_id": command,
            }
            rows.append(row)
            rows.extend({**row, key: value} for key in row for value in [None])
            rows.extend({**row, "facts": f} for f in [None, {}, [], {**facts, "extra": True}])
            rows.extend(
                {**row, "facts": {**facts, key: v}} for key in facts for v in [None, "unknown", 0]
            )
            rows.extend({**row, "event_number": v} for v in [0, 1, 2, 3, 4, 5])
        for key in result:
            for value in [None, "unknown", -1, 0, 2147483648]:
                row = rows[0].copy()
                row.update(
                    event_number=4,
                    event_type="command.workflow_succeeded",
                    facts={
                        "schema_version": 1,
                        "first_run_id": run,
                        "workflow_id": workflow,
                        "result_reference": {**result, key: value},
                    },
                )
                rows.append(row)
    for row in rows:
        outcomes = []
        for old in [True, False]:
            try:
                with admin.transaction():
                    outcomes.append(
                        (
                            "value",
                            old_expression(admin, expression, f"{schema}.{table}", row)
                            if old
                            else admin.execute(
                                "SELECT control.sql_dispatch_shape_allowed(%s,%s)",
                                (name, Jsonb(row)),
                            ).fetchone()[0],
                        )
                    )
            except psycopg.Error as error:
                outcomes.append(("error", error.sqlstate))
        assert outcomes[0] == outcomes[1], (name, row, outcomes)


@pytest.fixture
def before_functions(admin):
    with admin.transaction(force_rollback=True):
        names = [f[0] for f in BASELINE["functions"]]
        functions = {f[0]: f for f in BASELINE["functions"]}
        ordered = []
        visited = set()

        def visit(name):
            if name in visited:
                return
            visited.add(name)
            for dependency in names:
                if dependency != name and f"control.{dependency}(" in functions[name][2]:
                    visit(dependency)
            ordered.append(functions[name])

        for name in names:
            visit(name)
        for name, args, definition in ordered:
            if name == "begin_owner_connector_egress":
                continue
            for target in sorted(names, key=len, reverse=True):
                definition = definition.replace(
                    f"control.{target}(", f"control.dispatch_before_{target}("
                )
            admin.execute(definition)
            admin.execute(
                sql.SQL("ALTER FUNCTION control.{}({}) OWNER TO signal_migrator").format(
                    sql.Identifier("dispatch_before_" + name), sql.SQL(args)
                )
            )
        yield


OWNER_ROUTES = [
    ("github_rest", "POST", "https://api.github.com/app/installations/1/access_tokens"),
    ("github_rest", "GET", "https://api.github.com/app/installations/1"),
    *[
        ("github_rest", "GET", "https://api.github.com/repos/example-owner/repository" + path)
        for path in [
            "",
            "/branches/main",
            "/git/commits/" + "a" * 40,
            "/git/blobs/" + "a" * 40,
            "/git/trees/" + "a" * 40 + "?recursive=1",
        ]
    ],
    ("google_oauth_token", "POST", "https://oauth2.googleapis.com/token"),
    ("google_oauth_revoke", "POST", "https://oauth2.googleapis.com/revoke"),
    ("gsc_api", "GET", "https://www.googleapis.com/webmasters/v3/sites"),
    (
        "gsc_api",
        "POST",
        "https://www.googleapis.com/webmasters/v3/sites/https%3A%2F%2Fexample.invalid/searchAnalytics/query",
    ),
    ("slack_oauth", "POST", "https://slack.com/api/oauth.v2.access"),
    ("slack_bot", "POST", "https://slack.com/api/chat.postMessage"),
    ("slack_bot", "POST", "https://slack.com/api/auth.revoke"),
    *[
        (
            "pagespeed",
            "GET",
            "https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url=https%3A%2F%2Fexample.invalid&strategy="
            + strategy
            + "&category=performance",
        )
        for strategy in ["mobile", "desktop"]
    ],
    ("webflow_oauth", "POST", "https://api.webflow.com/oauth/access_token"),
    ("webflow_revoke", "POST", "https://webflow.com/oauth/revoke_authorization"),
    ("webflow", "GET", "https://api.webflow.com/v2/token/introspect"),
    *[
        ("webflow", "GET", "https://api.webflow.com/v2/sites/" + "a" * 24 + "/" + path)
        for path in ["custom_domains", "collections"]
    ],
    ("webflow", "GET", "https://api.webflow.com/v2/collections/" + "a" * 24),
    ("bing_oauth_token", "POST", "https://www.bing.com/webmasters/oauth/token"),
    ("bing_api", "GET", "https://www.bing.com/webmaster/api.svc/json/GetUserSites"),
    *[
        (
            "bing_api",
            "GET",
            "https://www.bing.com/webmaster/api.svc/json/"
            + path
            + "?siteUrl=https%3A%2F%2Fexample.invalid",
        )
        for path in ["GetRankAndTrafficStats", "GetPageStats"]
    ],
    *[
        ("dataforseo", "POST", "https://api.dataforseo.com/v3/" + path)
        for path in [
            "serp/google/organic/live/advanced",
            "keywords_data/google_ads/search_volume/live",
            "backlinks/summary/live",
        ]
    ],
]


def test_all_owner_routes_methods_regex_boundaries_and_nulls(admin, before_functions):
    profiles = [r[0] for r in admin.execute("SELECT profile FROM control.owner_egress_profiles")]
    for route_profile, _, url in OWNER_ROUTES:
        for profile, method, candidate in product(
            [route_profile, "unknown", None],
            ["GET", "POST", "PUT", "DELETE", "get", None],
            [
                url,
                url + "/",
                url + "#fragment",
                "prefix" + url,
                url.replace("https://", "http://"),
                url.replace(".com/", ".com.evil.invalid/"),
                None,
            ],
        ):
            before, after = admin.execute(
                "SELECT "
                "control.dispatch_before_owner_connector_request_allowed(%s,%s,%s),control.owner_connector_request_allowed(%s,%s,%s)",
                (profile, method, candidate, profile, method, candidate),
            ).fetchone()
            assert after is before, (profile, method, candidate)
        for profile in profiles:
            before, after = admin.execute(
                "SELECT "
                "control.dispatch_before_owner_connector_request_allowed(%s,'GET',%s),control.owner_connector_request_allowed(%s,'GET',%s)",
                (profile, url, profile, url),
            ).fetchone()
            assert after is before
    for profile, url in product(
        [*profiles, "unknown", None], [r[2] for r in OWNER_ROUTES] + [None]
    ):
        expected = (
            "GET"
            if profile in {"github_rest", "gsc_api", "pagespeed", "bing_api", "webflow"}
            and url is not None
            and not re.search(r"/access_tokens$|/searchAnalytics/query$", url)
            else "POST"
        )
        assert (
            admin.execute(
                "SELECT control.owner_connector_robots_method(%s,%s)", (profile, url)
            ).fetchone()[0]
            == expected
        )


def test_future_rows_need_no_shared_ddl_and_runtime_cannot_edit_catalogs(
    admin, api, workflow, crawl_admission
):
    with admin.transaction(force_rollback=True):
        admin.execute(
            "INSERT INTO control.sql_dispatch_values "
            "VALUES('model_budget_calls_role_check','synthetic_future_role')"
        )
        assert admin.execute(
            "SELECT "
            "control.sql_dispatch_value_allowed('model_budget_calls_role_check','synthetic_future_role')"
        ).fetchone() == (True,)
        admin.execute(
            "INSERT INTO control.authority_restriction_kinds "
            "VALUES('synthetic_future_binding','synthetic_future_revoked',false)"
        )
        assert admin.execute(
            "SELECT "
            "control.authority_kind_allowed('synthetic_future_binding','synthetic_future_revoked',false)"
        ).fetchone() == (True,)
        admin.execute(
            "INSERT INTO "
            "control.owner_egress_profiles(profile,robots_get,robots_post_pattern,"
            "nullable_request_result) VALUES('synthetic_future_profile',true,'a^',false)"
        )
        admin.execute(
            "INSERT INTO control.owner_egress_request_rules "
            "VALUES('synthetic_future_profile','GET','https://api.example.invalid/read',NULL)"
        )
        assert admin.execute(
            "SELECT "
            "control.owner_connector_request_allowed('synthetic_future_profile','GET','https://api.example.invalid/read')"
        ).fetchone() == (True,)
        assert admin.execute(
            "SELECT "
            "control.owner_connector_request_allowed('synthetic_future_profile','POST','https://api.example.invalid/read')"
        ).fetchone() == (False,)
        admin.execute(
            "INSERT INTO control.sql_dispatch_shapes "
            "VALUES('platform_events_contract_check','synthetic_future_event',"
            "'control.platform_events', $rule$event_type='synthetic.future' "
            "AND object_kind='synthetic_future' AND actor_user_id IS NOT NULL "
            "AND reason='synthetic' AND facts='{\"schema_version\":1}'::jsonb$rule$)"
        )
        row = {
            "event_type": "synthetic.future",
            "object_kind": "synthetic_future",
            "actor_user_id": str(uuid4()),
            "reason": "synthetic",
            "facts": {"schema_version": 1},
        }
        assert admin.execute(
            "SELECT control.sql_dispatch_shape_allowed('platform_events_contract_check',%s)",
            (Jsonb(row),),
        ).fetchone() == (True,)
        admin.execute(
            "INSERT INTO control.platform_event_references "
            "VALUES('synthetic.future',NULL,NULL,'synthetic invalid')"
        )
        admin.execute(
            "CREATE FUNCTION control.synthetic_strategy_source(bytea,text,uuid) RETURNS jsonb "
            "LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$ SELECT "
            "'{\"synthetic_future_source\":[]}'::jsonb $$"
        )
        admin.execute(
            "REVOKE ALL ON FUNCTION control.synthetic_strategy_source(bytea,text,uuid) FROM PUBLIC"
        )
        admin.execute(
            "INSERT INTO control.strategy_source_dispatch "
            "VALUES('synthetic_future_source',50,'control.synthetic_strategy_source(bytea,text,uuid)','control.synthetic_strategy_source(bytea,text,uuid)')"
        )
        assert admin.execute(
            "SELECT source_key FROM control.strategy_source_dispatch WHERE ordinal=50"
        ).fetchone() == ("synthetic_future_source",)
        for catalog in CATALOGS:
            assert admin.execute(
                "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=%s::regclass",
                ("control." + catalog,),
            ).fetchone() == ("signal_migrator",)
            for connection in [api, workflow, crawl_admission]:
                with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
                    connection.execute(
                        sql.SQL("SELECT * FROM control.{}").format(sql.Identifier(catalog))
                    )
                for command in ["INSERT", "UPDATE", "DELETE", "TRUNCATE"]:
                    assert connection.execute(
                        "SELECT has_table_privilege(current_user,%s,%s)",
                        ("control." + catalog, command),
                    ).fetchone() == (False,)


def test_strategy_packet_is_identical_and_future_reader_is_only_a_row(
    admin, before_functions, scopes, identity_context
):
    admin.execute(
        "UPDATE app.memberships SET role_key='owner' WHERE id=%s",
        (identity_context["membership_id"],),
    )
    args = (
        sha256(identity_context["session_token"].encode("ascii")).digest(),
        identity_context["generation"],
        scopes[0].site_id,
    )
    before, after = admin.execute(
        "SELECT "
        "control.dispatch_before_seo_strategy_sources(%s,%s,%s),control.seo_strategy_sources(%s,%s,%s)",
        (*args, *args),
    ).fetchone()
    assert before is not None and before == after
    admin.execute(
        "CREATE FUNCTION control.synthetic_strategy_reader(bytea,text,uuid) RETURNS jsonb "
        "LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$ SELECT "
        "'{\"synthetic_future\":[]}'::jsonb $$"
    )
    admin.execute(
        "REVOKE ALL ON FUNCTION control.synthetic_strategy_reader(bytea,text,uuid) FROM PUBLIC"
    )
    admin.execute(
        "ALTER FUNCTION control.synthetic_strategy_reader(bytea,text,uuid) OWNER TO signal_migrator"
    )
    admin.execute(
        "INSERT INTO control.strategy_source_dispatch "
        "VALUES('synthetic_future',50,'control.synthetic_strategy_reader(bytea,text,uuid)','control.synthetic_strategy_reader(bytea,text,uuid)')"
    )
    packet = admin.execute("SELECT control.seo_strategy_sources(%s,%s,%s)", args).fetchone()[0]
    assert packet == {**before, "synthetic_future": []}
    assert admin.execute(
        "SELECT control.seo_strategy_sources(%s,%s,%s)", (args[0], "wrong-generation", args[2])
    ).fetchone() == (None,)


def test_reference_dispatch_matches_old_branches_and_exemptions(
    admin, before_functions, identity_context
):
    rows = {}
    for row in event_rows():
        rows.setdefault(row["event_type"], row)
    for row in rows.values():
        row["actor_user_id"] = (
            None
            if row["event_type"] == "identity.login.failed"
            else str(identity_context["user_id"])
        )
        for old in [True, False]:
            with admin.transaction(force_rollback=True):
                admin.execute(
                    "DROP TRIGGER platform_event_reference_guard ON control.platform_events"
                )
                condition = (
                    "WHEN (NEW.event_type NOT IN "
                    "('webflow.binding.revoked','github.pr.revoked','invitation.revoked'))"
                    if old
                    else "WHEN (NEW.event_type IS NOT NULL)"
                )
                function = (
                    "dispatch_before_validate_platform_event_reference"
                    if old
                    else "validate_platform_event_reference"
                )
                admin.execute(
                    sql.SQL(
                        "CREATE TRIGGER platform_event_reference_guard BEFORE INSERT ON "
                        "control.platform_events FOR EACH ROW {} EXECUTE FUNCTION "
                        "control.{}()"
                    ).format(sql.SQL(condition), sql.Identifier(function))
                )
                try:
                    with admin.transaction():
                        admin.execute(
                            "INSERT INTO "
                            "control.platform_events(id,event_type,actor_user_id,object_kind,"
                            "object_id,reason,facts) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                            tuple(
                                row[k]
                                for k in [
                                    "id",
                                    "event_type",
                                    "actor_user_id",
                                    "object_kind",
                                    "object_id",
                                    "reason",
                                ]
                            )
                            + (Jsonb(row["facts"]),),
                        )
                        result = "accepted"
                except psycopg.Error as error:
                    result = error.sqlstate
            if old:
                before = result
            else:
                assert result == before, row["event_type"]
    with admin.transaction(force_rollback=True):
        admin.execute(
            "INSERT INTO control.platform_event_references "
            "VALUES('synthetic.future',NULL,NULL,'synthetic_invalid')"
        )
        admin.execute(
            "INSERT INTO control.sql_dispatch_shapes "
            "VALUES('platform_events_contract_check','synthetic_future','control.platform_events',"
            "$rule$event_type='synthetic.future' AND object_kind='synthetic_future' "
            "AND facts='{\"schema_version\":1}'::jsonb "
            "AND actor_user_id IS NOT NULL AND reason IS NULL$rule$)"
        )
        admin.execute(
            "INSERT INTO "
            "control.platform_events(id,event_type,actor_user_id,object_kind,object_id,facts) "
            "VALUES(%s,'synthetic.future',%s,'synthetic_future',%s,'{\"schema_version\":1}')",
            (uuid4(), identity_context["user_id"], uuid4()),
        )


def test_mid_migration_failure_restores_the_exact_old_contract(admin):
    name = "sql_dispatch_failure_" + uuid4().hex
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=name)
        migrator_dsn = make_conninfo(os.environ["SIGNAL_MIGRATION_DSN"], dbname=name)
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(os.environ, SIGNAL_MIGRATION_DSN=migrator_dsn)
        command = [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade"]
        prior = subprocess.run(
            [*command, "0099"], env=env, capture_output=True, text=True, timeout=30
        )
        assert prior.returncode == 0, prior.stderr
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            before = connection.execute(
                "SELECT "
                "pg_get_functiondef('control.owner_connector_request_allowed(text,text,text)'::regprocedure)"
            ).fetchone()[0]
            connection.execute(
                "CREATE FUNCTION control.owner_connector_robots_method(text,text) RETURNS "
                "text LANGUAGE sql AS $$ SELECT 'synthetic-collision' $$"
            )
        failed = subprocess.run(
            [*command, "0100"], env=env, capture_output=True, text=True, timeout=30
        )
        assert failed.returncode != 0
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            assert connection.execute(
                "SELECT version_num FROM control.alembic_version"
            ).fetchone() == ("0099",)
            assert connection.execute(
                "SELECT to_regclass('control.sql_dispatch_values')"
            ).fetchone() == (None,)
            assert (
                connection.execute(
                    "SELECT "
                    "pg_get_functiondef('control.owner_connector_request_allowed(text,text,text)'::regprocedure)"
                ).fetchone()[0]
                == before
            )
    finally:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_dispatch_catalogs_survive_real_dump_restore(admin):
    connection_info = conninfo_to_dict(os.environ["SIGNAL_TEST_ADMIN_DSN"])
    containers = subprocess.run(
        ["docker", "ps", "--filter", "label=io.signal.lab.run", "--format", "{{.Names}}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.splitlines()
    matches = []
    for container in containers:
        description = json.loads(
            subprocess.run(
                ["docker", "inspect", container],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout
        )[0]
        if any(
            binding["HostIp"] == connection_info["host"]
            and binding["HostPort"] == connection_info["port"]
            for binding in description["NetworkSettings"]["Ports"].get("5432/tcp", [])
        ):
            matches.append(container)
    assert len(matches) == 1, "Only the runner-owned loopback database is accepted"
    source = "dispatch_source_" + uuid4().hex
    restored = "dispatch_restore_" + uuid4().hex
    created = []
    try:
        for name in [source, restored]:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            created.append(name)
        source_dsn = make_conninfo(os.environ["SIGNAL_TEST_ADMIN_DSN"], dbname=source)
        with psycopg.connect(source_dsn, autocommit=True) as connection:
            connection.execute("CREATE SCHEMA app AUTHORIZATION signal_migrator")
            connection.execute("CREATE SCHEMA control AUTHORIZATION signal_migrator")
        env = dict(
            os.environ,
            SIGNAL_MIGRATION_DSN=make_conninfo(
                os.environ["SIGNAL_MIGRATION_DSN"],
                dbname=source,
            ),
        )
        migrated = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "database/alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert migrated.returncode == 0, migrated.stderr
        with psycopg.connect(source_dsn, autocommit=True) as connection:
            connection.execute(
                "INSERT INTO control.platform_event_references "
                "VALUES('synthetic.restore',NULL,NULL,'synthetic_invalid')"
            )
            connection.execute(
                "INSERT INTO control.sql_dispatch_shapes VALUES "
                "('platform_events_contract_check','synthetic_restore','control.platform_events',"
                "$rule$event_type='synthetic.restore' AND object_kind='synthetic_restore' "
                "AND facts='{\"schema_version\":1}'::jsonb$rule$)"
            )
            connection.execute(
                "INSERT INTO control.platform_events(id,event_type,object_kind,object_id,facts) "
                "VALUES(%s,'synthetic.restore','synthetic_restore',%s,'{\"schema_version\":1}')",
                (uuid4(), uuid4()),
            )
        command = ["docker", "exec", "-u", "postgres", matches[0]]
        snapshot = subprocess.run(
            [*command, "pg_dump", "-U", "postgres", "-Fc", source],
            check=True,
            capture_output=True,
            timeout=30,
        ).stdout
        restore = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                "-u",
                "postgres",
                matches[0],
                "pg_restore",
                "-U",
                "postgres",
                "--exit-on-error",
                "-d",
                restored,
            ],
            input=snapshot,
            capture_output=True,
            timeout=30,
        )
        assert restore.returncode == 0, restore.stderr.decode()
        with psycopg.connect(
            make_conninfo(
                os.environ["SIGNAL_TEST_ADMIN_DSN"],
                dbname=restored,
            ),
            autocommit=True,
        ) as connection:
            assert connection.execute(
                "SELECT count(*) FROM control.platform_events WHERE event_type='synthetic.restore'"
            ).fetchone() == (1,)
            with pytest.raises(psycopg.errors.CheckViolation) as rejected:
                connection.execute(
                    "INSERT INTO control.platform_events"
                    "(id,event_type,object_kind,object_id,facts) "
                    "VALUES(%s,'synthetic.restore','synthetic_restore',%s,'{}')",
                    (uuid4(), uuid4()),
                )
            assert rejected.value.diag.constraint_name == "platform_events_contract_check"
    finally:
        for name in reversed(created):
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
