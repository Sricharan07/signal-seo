"""Synthetic imported-generation fixtures; no customer/provider state."""

from datetime import UTC, datetime, time, timedelta
from hashlib import sha256
from uuid import uuid4

from psycopg import sql
from psycopg.types.json import Jsonb

from tests.control_plane.test_gsc_binding import session_args


def bind_sources(admin, identity, scope, context):
    origin = admin.execute(
        "SELECT primary_origin FROM app.sites WHERE id=%s", (scope.site_id,)
    ).fetchone()[0]
    bindings = {}
    for provider in ("gsc", "bing"):
        attempt, binding = uuid4(), uuid4()
        args = (*session_args(context, scope.site_id), attempt)
        callback = f"https://dashboard.example.invalid/oauth/{provider}/callback"
        state = sha256(b"synthetic-measurement-state" + attempt.bytes).digest()
        begin_args = (
            (*args, state, callback, "A" * 43) if provider == "gsc" else (*args, state, callback)
        )
        assert (
            identity.execute(
                f"SELECT control.begin_{provider}_oauth_attempt("
                + ",".join(["%s"] * len(begin_args))
                + ")",
                begin_args,
            ).fetchone()[0]
            == "created"
        )
        assert (
            identity.execute(
                f"SELECT outcome FROM control.consume_{provider}_oauth_attempt(%s,%s,%s,%s,%s,%s)",
                (*args, state, callback),
            ).fetchone()[0]
            == "consumed"
        )
        candidates = (
            [{"resource_name": origin + "/", "property_type": "url_prefix", "eligible": True}]
            if provider == "gsc"
            else [{"url": origin + "/", "eligible": True}]
        )
        assert (
            identity.execute(
                f"SELECT control.stage_{provider}_oauth_attempt(%s,%s,%s,%s,%s,%s)",
                (*args, f"secret://{provider}/{attempt}", Jsonb(candidates)),
            ).fetchone()[0]
            == "staged"
        )
        assert (
            identity.execute(
                f"SELECT control.confirm_{provider}_binding(%s,%s,%s,%s,%s,%s,%s)",
                (*args, binding, uuid4(), origin + "/"),
            ).fetchone()[0]
            == "bound"
        )
        bindings[provider] = binding
    return bindings, origin + "/"


def import_fixture(
    admin,
    scope,
    bindings,
    page,
    start,
    end,
    *,
    lag=False,
    clicks=10,
    dimensions=None,
    imported_at=None,
):
    """Seed immutable, deliberately incomplete domain inputs, not provider qualification."""
    dimensions = dimensions or ["date", "page"]
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    rows = [
        {
            "keys": [day.isoformat() if d == "date" else page for d in dimensions],
            "clicks": clicks,
            "impressions": 100,
            "ctr": clicks / 100,
            "position": 4,
        }
        for day in days
    ]
    coverage = {
        "complete": False,
        "missing_data": "unknown_not_zero",
        "top_rows_only": True,
        "source_timezone": "America/Los_Angeles",
        "first_incomplete_date": end.isoformat() if lag else None,
    }
    generation = uuid4()
    admin.execute(
        "INSERT INTO app.gsc_import_generations(tenant_id,site_id,id,binding_id,"
        "property_resource_name,search_type,dimensions,start_date,end_date,data_state,"
        "aggregation_type,rows,coverage,response_sha256,egress_operation_id,imported_at) "
        "VALUES(%s,%s,%s,%s,%s,'web',%s,%s,%s,'all','byPage',%s,%s,%s,%s,"
        "COALESCE(%s,transaction_timestamp()))",
        (
            scope.tenant_id,
            scope.site_id,
            generation,
            bindings["gsc"],
            page,
            Jsonb(dimensions),
            start,
            end,
            Jsonb(rows),
            Jsonb(coverage),
            sha256(b"synthetic-gsc-measurement").digest(),
            uuid4(),
            imported_at,
        ),
    )
    bing_rows = [
        {
            "date": f"/Date({int(datetime.combine(day, time(), UTC).timestamp() * 1000)}+0000)/",
            "clicks": clicks * 2,
            "impressions": 200,
        }
        for day in days
    ]
    bing_coverage = {
        "complete": False,
        "missing_data": "unknown",
        "source": "bing_webmaster",
        "verticals": "all_provider_verticals",
    }
    bing_generation = uuid4()
    admin.execute(
        "INSERT INTO app.bing_import_generations(tenant_id,site_id,id,binding_id,"
        "property_resource_name,kind,rows,coverage,response_sha256,egress_operation_id,"
        "imported_at) VALUES(%s,%s,%s,%s,%s,'performance',%s,%s,%s,%s,"
        "COALESCE(%s,transaction_timestamp()))",
        (
            scope.tenant_id,
            scope.site_id,
            bing_generation,
            bindings["bing"],
            page,
            Jsonb(bing_rows),
            Jsonb(bing_coverage),
            sha256(b"synthetic-bing-measurement").digest(),
            uuid4(),
            imported_at,
        ),
    )
    return generation, bing_generation, coverage, bing_coverage


def clone_fixture_row(admin, table, predicate, arguments, changes):
    """Append related synthetic records without disabling constraints or triggers."""
    row = admin.execute(
        sql.SQL("SELECT to_jsonb(t) FROM app.{} t WHERE ").format(sql.Identifier(table))
        + sql.SQL(predicate),
        arguments,
    ).fetchone()[0]
    row.update({k: str(v) if hasattr(v, "hex") else v for k, v in changes.items()})
    columns = [
        r[0]
        for r in admin.execute(
            "SELECT attname FROM pg_attribute WHERE attrelid=%s::regclass "
            "AND attnum>0 AND NOT attisdropped AND attgenerated='' ORDER BY attnum",
            (f"app.{table}",),
        ).fetchall()
    ]
    fields = sql.SQL(",").join(sql.Identifier(c) for c in columns)
    with admin.transaction():
        admin.execute(
            "SELECT set_config('signal.tenant_id',%s,true),set_config('signal.site_id',%s,true)",
            (row["tenant_id"], row["site_id"]),
        )
        admin.execute(
            sql.SQL(
                "INSERT INTO app.{} ({}) SELECT {} FROM jsonb_populate_record(NULL::app.{},%s)"
            ).format(sql.Identifier(table), fields, fields, sql.Identifier(table)),
            (Jsonb(row),),
        )


def import_bing_page_fixture(admin, scope, bindings, page, start, end, *, clicks):
    coverage = {
        "complete": False,
        "source": "bing_webmaster",
        "coverage": "provider_returned_top_pages",
        "date_granularity": "unknown",
        "provider_update_frequency": "weekly",
    }
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    rows = [
        {
            "page_url": page,
            "date": f"/Date({int(datetime.combine(day, time(), UTC).timestamp() * 1000)}+0000)/",
            "clicks": clicks,
            "impressions": 200,
            "avg_click_position": 2,
            "avg_impression_position": 3,
        }
        for day in days
    ]
    identifier = uuid4()
    admin.execute(
        "INSERT INTO app.bing_import_generations(tenant_id,site_id,id,binding_id,"
        "property_resource_name,kind,rows,coverage,response_sha256,egress_operation_id) "
        "VALUES(%s,%s,%s,%s,%s,'page_performance',%s,%s,%s,%s)",
        (
            scope.tenant_id,
            scope.site_id,
            identifier,
            bindings["bing"],
            page,
            Jsonb(rows),
            Jsonb(coverage),
            sha256(b"synthetic-bing-page-measurement").digest(),
            uuid4(),
        ),
    )
    return identifier, coverage


def add_pending_revision(admin, operation):
    op = admin.execute(
        "SELECT to_jsonb(o) FROM app.github_pr_operations o WHERE id=%s", (operation,)
    ).fetchone()[0]
    revision = admin.execute(
        "SELECT to_jsonb(r) FROM app.candidate_recipe_revisions r WHERE id=%s",
        (op["candidate_revision_id"],),
    ).fetchone()[0]
    build, revision_id = uuid4(), uuid4()
    clone_fixture_row(
        admin,
        "candidate_build_intents",
        "id=%s",
        (revision["build_id"],),
        {"id": build, "idempotency_key": uuid4()},
    )
    clone_fixture_row(
        admin,
        "candidate_build_receipts",
        "build_id=%s",
        (revision["build_id"],),
        {"build_id": build},
    )
    clone_fixture_row(
        admin,
        "candidate_recipe_revisions",
        "id=%s",
        (revision["id"],),
        {
            "id": revision_id,
            "build_id": build,
            "idempotency_key": uuid4(),
            "sealed_at": datetime.now(UTC).isoformat(),
        },
    )
    return revision_id


def add_confounding_change(admin, operation):
    """A second same-page fixture shares observed bytes, but has separate records."""
    op = admin.execute(
        "SELECT to_jsonb(o) FROM app.github_pr_operations o WHERE id=%s", (operation,)
    ).fetchone()[0]
    revision_id = add_pending_revision(admin, operation)
    decision, other, attempt = [uuid4() for _ in range(3)]
    admin.execute(
        "INSERT INTO app.candidate_recipe_review_decisions(tenant_id,site_id,id,"
        "candidate_revision_id,revision_sha256,decision,decided_by_user_id,decision_channel,"
        "membership_epoch,site_authorization_epoch,recovery_generation,authentication_level,"
        "actor_role,decided_at) VALUES(%s,%s,%s,%s,%s,'approved',%s,'dashboard',%s,%s,%s,"
        "'primary','owner',transaction_timestamp())",
        (
            op["tenant_id"],
            op["site_id"],
            decision,
            revision_id,
            bytes.fromhex(op["revision_sha256"][2:]),
            op["requested_by_user_id"],
            op["membership_epoch"],
            op["site_epoch"],
            op["recovery_generation"],
        ),
    )
    clone_fixture_row(
        admin,
        "github_pr_operations",
        "id=%s",
        (operation,),
        {
            "id": other,
            "candidate_revision_id": revision_id,
            "decision_id": decision,
            "authority_kind": "owner_inbox",
            "standing_dispatch_id": None,
            "decision_channel": "dashboard",
            "branch_name": "signal/" + other.hex,
        },
    )
    source = admin.execute(
        "SELECT attempt_id FROM app.github_delivery_receipts WHERE operation_id=%s "
        "ORDER BY recorded_at LIMIT 1",
        (operation,),
    ).fetchone()[0]
    clone_fixture_row(
        admin,
        "github_delivery_attempts",
        "id=%s",
        (source,),
        {"id": attempt, "operation_id": other},
    )
    clone_fixture_row(
        admin,
        "github_delivery_receipts",
        "attempt_id=%s",
        (source,),
        {"attempt_id": attempt, "operation_id": other},
    )
    return other
