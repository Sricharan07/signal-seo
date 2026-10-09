CREATE FUNCTION app.current_tenant_id() RETURNS uuid
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog
AS $$ SELECT NULLIF(current_setting('signal.tenant_id', true), '')::uuid $$;
CREATE FUNCTION app.current_site_id() RETURNS uuid
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog
AS $$ SELECT NULLIF(current_setting('signal.site_id', true), '')::uuid $$;
GRANT EXECUTE ON FUNCTION app.current_tenant_id(), app.current_site_id()
TO signal_bootstrap, signal_api, signal_scheduler;

CREATE FUNCTION app.reject_mutation() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $$ BEGIN RAISE EXCEPTION 'immutable_record' USING ERRCODE = '55000'; END $$;

CREATE TABLE app.tenants (
    tenant_id uuid PRIMARY KEY,
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    lifecycle text NOT NULL DEFAULT 'active' CHECK (lifecycle IN ('active', 'suspended', 'deleting', 'deleted')),
    home_region text NOT NULL,
    settings_schema_version integer NOT NULL DEFAULT 1 CHECK (settings_schema_version = 1),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version > 0),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX tenants_lifecycle ON app.tenants (lifecycle, created_at);

CREATE TABLE app.sites (
    tenant_id uuid NOT NULL REFERENCES app.tenants (tenant_id),
    id uuid NOT NULL,
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    primary_origin text NOT NULL,
    timezone text NOT NULL,
    reporting_currency text NOT NULL CHECK (reporting_currency ~ '^[A-Z]{3}$'),
    state text NOT NULL DEFAULT 'onboarding' CHECK (state IN ('onboarding', 'active', 'archived')),
    ownership_status text NOT NULL DEFAULT 'unverified' CHECK (ownership_status = 'unverified'),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id)
);
CREATE INDEX sites_state ON app.sites (tenant_id, state, created_at);

CREATE TABLE control.tenant_directory (
    tenant_id uuid PRIMARY KEY REFERENCES app.tenants (tenant_id),
    lifecycle text NOT NULL CHECK (lifecycle IN ('active', 'suspended', 'deleting', 'deleted')),
    provisioning_generation bigint NOT NULL CHECK (provisioning_generation > 0)
);
CREATE INDEX tenant_directory_lifecycle ON control.tenant_directory (lifecycle, tenant_id);

CREATE TABLE app.commands (
    tenant_id uuid NOT NULL,
    id uuid NOT NULL,
    site_id uuid NOT NULL,
    actor_service text NOT NULL CHECK (actor_service ~ '^[a-z][a-z0-9_.-]{0,63}$'),
    kind text NOT NULL CHECK (kind = 'site.snapshot'),
    schema_version integer NOT NULL CHECK (schema_version = 1),
    principal_key text NOT NULL CHECK (principal_key = 'service:' || actor_service),
    route_key text NOT NULL CHECK (route_key = 'internal.site.snapshot'),
    scope_kind text NOT NULL CHECK (scope_kind = 'site'),
    idempotency_key text NOT NULL CHECK (idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
    request_fingerprint bytea NOT NULL CHECK (octet_length(request_fingerprint) = 32),
    payload jsonb NOT NULL CHECK (payload = '{"schema_version":1}'::jsonb),
    accepted_at timestamptz NOT NULL DEFAULT now(),
    status text NOT NULL DEFAULT 'accepted' CHECK (status = 'accepted'),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version = 1),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, site_id, id),
    UNIQUE (tenant_id, principal_key, route_key, idempotency_key),
    FOREIGN KEY (tenant_id, site_id) REFERENCES app.sites (tenant_id, id)
);
CREATE INDEX commands_accepted ON app.commands (tenant_id, accepted_at DESC, id);
CREATE INDEX commands_site_status ON app.commands (tenant_id, site_id, status);

CREATE TABLE app.command_events (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    command_id uuid NOT NULL,
    event_number bigint NOT NULL CHECK (event_number > 0),
    event_type text NOT NULL CHECK (event_type = 'command.accepted'),
    facts jsonb NOT NULL CHECK (facts = '{"schema_version":1}'::jsonb),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, command_id, event_number),
    UNIQUE (tenant_id, site_id, command_id, id),
    FOREIGN KEY (tenant_id, site_id, command_id) REFERENCES app.commands (tenant_id, site_id, id)
);
CREATE INDEX command_events_scope ON app.command_events (tenant_id, site_id, command_id);

CREATE TABLE app.outbox (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    event_id uuid NOT NULL,
    aggregate_kind text NOT NULL CHECK (aggregate_kind = 'command'),
    aggregate_id uuid NOT NULL,
    event_type text NOT NULL CHECK (event_type = 'command.accepted'),
    schema_version integer NOT NULL CHECK (schema_version = 1),
    payload jsonb NOT NULL CHECK (payload = '{"schema_version":1}'::jsonb),
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_owner text,
    lease_until timestamptz,
    delivered_at timestamptz,
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, event_id),
    FOREIGN KEY (tenant_id, site_id, aggregate_id) REFERENCES app.commands (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id, aggregate_id, event_id)
        REFERENCES app.command_events (tenant_id, site_id, command_id, id),
    CHECK ((lease_owner IS NULL) = (lease_until IS NULL))
);
CREATE INDEX outbox_pending ON app.outbox (tenant_id, site_id, available_at, id) WHERE delivered_at IS NULL;
CREATE INDEX outbox_command ON app.outbox (tenant_id, site_id, aggregate_id);

-- Intent and event payloads are immutable; only future reviewed processing may add progress.
CREATE TRIGGER commands_immutable BEFORE UPDATE OR DELETE ON app.commands
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER command_events_immutable BEFORE UPDATE OR DELETE ON app.command_events
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();
CREATE TRIGGER outbox_immutable BEFORE UPDATE OR DELETE ON app.outbox
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

ALTER TABLE app.tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.tenants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON app.tenants
USING (tenant_id = app.current_tenant_id()) WITH CHECK (tenant_id = app.current_tenant_id());

ALTER TABLE app.sites ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.sites FORCE ROW LEVEL SECURITY;
CREATE POLICY site_scope ON app.sites
USING (tenant_id = app.current_tenant_id() AND id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND id = app.current_site_id());

ALTER TABLE app.commands ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.commands FORCE ROW LEVEL SECURITY;
CREATE POLICY command_scope ON app.commands
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

ALTER TABLE app.command_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.command_events FORCE ROW LEVEL SECURITY;
CREATE POLICY event_scope ON app.command_events
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

ALTER TABLE app.outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.outbox FORCE ROW LEVEL SECURITY;
CREATE POLICY outbox_scope ON app.outbox
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

-- This is the initial privilege manifest, not a blanket grant on future tables.
GRANT SELECT, INSERT ON app.tenants, app.sites TO signal_bootstrap;
GRANT SELECT, INSERT ON control.tenant_directory TO signal_bootstrap;
GRANT SELECT ON app.tenants, app.sites TO signal_api;
GRANT SELECT, INSERT ON app.commands, app.command_events, app.outbox TO signal_api;
GRANT SELECT ON control.tenant_directory, app.outbox TO signal_scheduler;
