"""Compare shared binding decisions with the actual pre-refactor SQL chain."""

from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb
from test_sql_dispatch import BASELINE, OWNER_ROUTES
from test_sql_dispatch import before_functions as baseline_fixture


@pytest.fixture(name="before_functions")
def dispatch_baseline(admin):
    yield from baseline_fixture.__wrapped__(admin)


def test_every_shared_profile_and_request_boundary(admin, before_functions):
    # Isolate the already-admitted input. Unchanged network labs qualify real
    # admission, foreign keys, immutable operations, and tenant RLS separately.
    admin.execute(
        "CREATE TEMP TABLE dispatch_operations AS SELECT * FROM app.egress_operations WITH NO DATA"
    )
    admin.execute("GRANT ALL ON pg_temp.dispatch_operations TO signal_migrator")
    names = [f[0] for f in BASELINE["functions"]]
    for name, _, definition in BASELINE["functions"]:
        if not name.startswith("bind_"):
            continue
        for target in sorted(names, key=len, reverse=True):
            definition = definition.replace(
                f"control.{target}(", f"control.dispatch_before_{target}("
            )
        definition = definition.replace("app.egress_operations", "pg_temp.dispatch_operations")
        admin.execute(definition)
    definition = admin.execute(
        "SELECT "
        "pg_get_functiondef('control.bind_shared_egress_profile(uuid,uuid,uuid,bytea,text)'::regprocedure)"
    ).fetchone()[0]
    definition = definition.replace(
        "control.bind_shared_egress_profile(", "control.dispatch_after_bind("
    ).replace("app.egress_operations", "pg_temp.dispatch_operations")
    admin.execute(definition)
    tenant, site, operation = uuid4(), uuid4(), uuid4()
    digest = bytes.fromhex("ab" * 32)
    routes = {p: (m, u) for p, m, u in OWNER_ROUTES}
    routes.update(
        {
            "crawl_page": ("GET", "https://example.invalid/page"),
            "crawl_robots": ("GET", "https://example.invalid/robots.txt"),
            "browser_read": ("GET", "https://example.invalid/page"),
            "model_json": ("POST", "https://model.example.invalid/decision"),
            "jev": ("POST", "https://api.typesafe.ai/decision"),
            "openai_model": ("POST", "https://api.openai.com/v1/responses"),
            "openai_assistant": ("POST", "https://api.openai.com/v1/responses"),
            "perplexity_assistant": ("POST", "https://api.perplexity.ai/v1/agent"),
            "gemini_assistant": (
                "POST",
                "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
            ),
            "crawl_key_file": ("GET", "https://example.invalid/synthetic-key.txt"),
            "indexnow_submit": ("POST", "https://api.indexnow.org/indexnow"),
            "ga4_admin": (
                "GET",
                "https://analyticsadmin.googleapis.com/v1beta/accountSummaries?pageSize=50",
            ),
            "ga4_data": (
                "POST",
                "https://analyticsdata.googleapis.com/v1beta/properties/1:runReport",
            ),
            "telegram_bot": ("POST", "https://api.telegram.org/sendMessage"),
            "wordpress_rest": (
                "GET",
                "https://example.invalid/wp-json/wp/v2/users/me?context=edit",
            ),
            "npm_registry": ("GET", "https://registry.npmjs.org/example/-/example-1.2.3.tgz"),
        }
    )
    profiles = [
        r[0]
        for r in admin.execute(
            "SELECT value FROM control.sql_dispatch_values WHERE "
            "contract_key='egress_operations_egress_profile_check'"
        )
    ]
    for profile in [*profiles, "unknown", None]:
        method, url = routes.get(
            profile, ("POST", "https://webflow.com/oauth/revoke_authorization")
        )
        parts = urlsplit(url)
        purpose = "connector"
        if profile in {"crawl_page", "crawl_robots", "crawl_key_file"}:
            purpose = "crawl"
        elif profile == "browser_read":
            purpose = "browser"
        elif profile in {"jev", "model_json", "openai_model"}:
            purpose = "model"
        row = {
            "tenant_id": str(tenant),
            "site_id": str(site),
            "id": str(operation),
            "state": "dispatched",
            "egress_profile": "legacy_unqualified",
            "purpose": purpose,
            "method": method,
            "request_url": url,
            "origin": f"{parts.scheme}://{parts.netloc}",
            "request_sha256": "\\x" + digest.hex(),
            "request_body_sha256": None if method == "GET" else "\\x" + digest.hex(),
            "request_bytes": 0 if method == "GET" else 1,
            "max_response_bytes": 1024,
            "credentialed": profile
            not in {"crawl_page", "crawl_robots", "crawl_key_file", "browser_read", "npm_registry"},
        }
        changes = [
            ("state", "observed"),
            ("egress_profile", "unknown"),
            ("egress_profile", profile),
            ("purpose", "unknown"),
            ("method", "DELETE"),
            ("method", "POST"),
            ("method", "GET"),
            ("credentialed", not row["credentialed"]),
            ("request_bytes", 65537),
            ("max_response_bytes", 5242881),
            ("request_url", url + "#extra"),
            ("request_url", "prefix" + url),
            ("origin", "https://example.invalid"),
            ("request_sha256", "\\x" + "cd" * 32),
        ]
        for variant in [row, *[{**row, key: value} for key, value in changes]]:
            decisions = []
            for function in ["dispatch_before_bind_shared_egress_profile", "dispatch_after_bind"]:
                with admin.transaction(force_rollback=True):
                    admin.execute("TRUNCATE pg_temp.dispatch_operations")
                    admin.execute(
                        "INSERT INTO pg_temp.dispatch_operations SELECT * FROM "
                        "jsonb_populate_record(NULL::pg_temp.dispatch_operations,%s)",
                        (Jsonb(variant),),
                    )
                    try:
                        with admin.transaction():
                            result = admin.execute(
                                sql.SQL("SELECT control.{}(%s,%s,%s,%s,%s)").format(
                                    sql.Identifier(function)
                                ),
                                (tenant, site, operation, digest, profile),
                            ).fetchone()[0]
                            decisions.append(result)
                    except psycopg.Error:
                        decisions.append("rejected")
            assert decisions[0] == decisions[1], (profile, variant, decisions)
    admin.execute(
        "INSERT INTO control.shared_egress_profiles(profile,predicate_sql,failure_message) "
        "VALUES('synthetic_future','o.purpose=''connector'' AND NOT "
        "o.credentialed','synthetic_denied')"
    )
    admin.execute(
        "INSERT INTO control.shared_egress_request_rules "
        "VALUES('synthetic_future','GET','https://api.example.invalid/read',NULL)"
    )
    row.update(
        purpose="connector",
        method="GET",
        credentialed=False,
        request_url="https://api.example.invalid/read",
        egress_profile="legacy_unqualified",
    )
    admin.execute(
        "INSERT INTO pg_temp.dispatch_operations SELECT * FROM "
        "jsonb_populate_record(NULL::pg_temp.dispatch_operations,%s)",
        (Jsonb(row),),
    )
    assert admin.execute(
        "SELECT control.dispatch_after_bind(%s,%s,%s,%s,'synthetic_future')",
        (tenant, site, operation, digest),
    ).fetchone() == ("bound",)
