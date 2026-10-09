CREATE FUNCTION app.current_actor_user_id() RETURNS uuid
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog
AS $$ SELECT NULLIF(current_setting('signal.actor_user_id', true), '')::uuid $$;
REVOKE ALL ON FUNCTION app.current_actor_user_id() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.current_actor_user_id() TO signal_api;

CREATE FUNCTION app.current_membership_epoch() RETURNS bigint
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog
AS $$
    SELECT CASE
        WHEN current_setting('signal.membership_epoch', true) ~ '^[1-9][0-9]{0,18}$'
        THEN current_setting('signal.membership_epoch', true)::bigint
        ELSE NULL
    END
$$;
REVOKE ALL ON FUNCTION app.current_membership_epoch() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.current_membership_epoch() TO signal_api;

CREATE FUNCTION app.current_site_authorization_epoch() RETURNS bigint
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog
AS $$
    SELECT CASE
        WHEN current_setting('signal.site_authorization_epoch', true) ~ '^[1-9][0-9]{0,18}$'
        THEN current_setting('signal.site_authorization_epoch', true)::bigint
        ELSE NULL
    END
$$;
REVOKE ALL ON FUNCTION app.current_site_authorization_epoch() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.current_site_authorization_epoch() TO signal_api;

CREATE FUNCTION app.lock_invitation_authority() RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
BEGIN
    PERFORM 1
    FROM app.tenants tenant
    WHERE tenant.tenant_id = app.current_tenant_id()
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN false;
    END IF;

    PERFORM 1
    FROM app.sites site
    WHERE site.tenant_id = app.current_tenant_id()
      AND site.id = app.current_site_id()
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN false;
    END IF;

    PERFORM 1
    FROM app.memberships membership
    WHERE membership.tenant_id = app.current_tenant_id()
      AND membership.user_id = app.current_actor_user_id()
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN false;
    END IF;

    PERFORM 1
    FROM app.site_memberships site_membership
    WHERE site_membership.tenant_id = app.current_tenant_id()
      AND site_membership.site_id = app.current_site_id()
      AND site_membership.user_id = app.current_actor_user_id()
    FOR UPDATE;
    RETURN FOUND;
END
$$;
REVOKE ALL ON FUNCTION app.lock_invitation_authority() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.lock_invitation_authority() TO signal_api;

CREATE TABLE app.invitations (
    tenant_id uuid NOT NULL,
    id uuid NOT NULL,
    site_id uuid NOT NULL,
    email_normalized text NOT NULL CHECK (
        length(email_normalized) BETWEEN 3 AND 320
        AND email_normalized = lower(email_normalized)
        AND length(split_part(email_normalized, '@', 1)) <= 64
        AND length(split_part(email_normalized, '@', 2)) <= 253
        AND left(split_part(email_normalized, '@', 1), 1) <> '.'
        AND right(split_part(email_normalized, '@', 1), 1) <> '.'
        AND email_normalized ~
            '^[a-z0-9.!#$%%&''*+/=?^_`{|}~-]+@[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$'
        AND position('..' IN email_normalized) = 0
    ),
    role_key text NOT NULL CHECK (
        role_key IN ('viewer', 'analyst', 'editor', 'approver', 'admin')
    ),
    token_hash bytea NOT NULL CHECK (octet_length(token_hash) = 32),
    inviter_user_id uuid NOT NULL,
    inviter_membership_epoch bigint NOT NULL CHECK (inviter_membership_epoch > 0),
    inviter_site_authorization_epoch bigint NOT NULL CHECK (
        inviter_site_authorization_epoch > 0
    ),
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (token_hash),
    UNIQUE (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id) REFERENCES app.sites (tenant_id, id),
    FOREIGN KEY (tenant_id, inviter_user_id)
        REFERENCES app.memberships (tenant_id, user_id),
    FOREIGN KEY (tenant_id, site_id, inviter_user_id)
        REFERENCES app.site_memberships (tenant_id, site_id, user_id),
    CHECK (expires_at >= created_at + interval '15 minutes'),
    CHECK (expires_at <= created_at + interval '7 days'),
    CHECK (consumed_at IS NULL),
    CHECK (revoked_at IS NULL)
);
CREATE INDEX invitations_recipient_expiry
ON app.invitations (tenant_id, email_normalized, expires_at DESC, id);

CREATE FUNCTION app.guard_invitation_insert() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.tenant_id IS DISTINCT FROM app.current_tenant_id()
       OR NEW.site_id IS DISTINCT FROM app.current_site_id()
       OR NEW.inviter_user_id IS DISTINCT FROM app.current_actor_user_id()
       OR NEW.inviter_membership_epoch IS DISTINCT FROM app.current_membership_epoch()
       OR NEW.inviter_site_authorization_epoch IS DISTINCT FROM
          app.current_site_authorization_epoch()
       OR NOT EXISTS (
            SELECT 1
            FROM app.memberships membership
            JOIN app.site_memberships site_membership
              ON site_membership.tenant_id = membership.tenant_id
             AND site_membership.user_id = membership.user_id
            JOIN app.tenants tenant
              ON tenant.tenant_id = membership.tenant_id
            JOIN app.sites site
              ON site.tenant_id = site_membership.tenant_id
             AND site.id = site_membership.site_id
            WHERE membership.tenant_id = NEW.tenant_id
              AND membership.user_id = NEW.inviter_user_id
              AND membership.state = 'active'
              AND membership.authorization_epoch = NEW.inviter_membership_epoch
              AND site_membership.site_id = NEW.site_id
              AND site_membership.state = 'active'
              AND site_membership.authorization_epoch =
                  NEW.inviter_site_authorization_epoch
              AND tenant.lifecycle = 'active'
              AND site.state != 'archived'
              AND (
                  (
                      membership.role_key = 'owner'
                      AND NEW.role_key IN (
                          'viewer', 'analyst', 'editor', 'approver', 'admin'
                      )
                  )
                  OR
                  (
                      membership.role_key = 'admin'
                      AND NEW.role_key IN ('viewer', 'analyst', 'editor', 'approver')
                  )
              )
       )
    THEN
        RAISE EXCEPTION 'invitation authority is invalid' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION app.guard_invitation_insert() FROM PUBLIC;
CREATE TRIGGER invitation_insert_guard
BEFORE INSERT ON app.invitations
FOR EACH ROW EXECUTE FUNCTION app.guard_invitation_insert();

CREATE TRIGGER invitations_immutable
BEFORE UPDATE OR DELETE ON app.invitations
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

ALTER TABLE app.invitations ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.invitations FORCE ROW LEVEL SECURITY;
CREATE POLICY invitation_scope ON app.invitations
USING (
    tenant_id = app.current_tenant_id()
    AND site_id = app.current_site_id()
)
WITH CHECK (
    tenant_id = app.current_tenant_id()
    AND site_id = app.current_site_id()
    AND inviter_user_id = app.current_actor_user_id()
);

CREATE TABLE app.audit_events (
    tenant_id uuid NOT NULL,
    id uuid NOT NULL,
    site_id uuid NOT NULL,
    aggregate_kind text NOT NULL CHECK (aggregate_kind = 'invitation'),
    aggregate_id uuid NOT NULL,
    aggregate_sequence bigint NOT NULL CHECK (aggregate_sequence = 1),
    actor_kind text NOT NULL CHECK (actor_kind = 'user'),
    actor_identifier text NOT NULL CHECK (
        actor_identifier ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    event_type text NOT NULL CHECK (event_type = 'invitation.created'),
    facts jsonb NOT NULL CHECK (
        jsonb_typeof(facts) = 'object'
        AND facts - 'schema_version' - 'role_key' = '{}'::jsonb
        AND facts -> 'schema_version' = '1'::jsonb
        AND facts ->> 'role_key' IN ('viewer', 'analyst', 'editor', 'approver', 'admin')
    ),
    previous_hash bytea CHECK (previous_hash IS NULL),
    event_hash bytea NOT NULL CHECK (octet_length(event_hash) = 32),
    occurred_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, site_id, aggregate_kind, aggregate_id, aggregate_sequence),
    FOREIGN KEY (tenant_id, site_id, aggregate_id)
        REFERENCES app.invitations (tenant_id, site_id, id)
);
CREATE INDEX audit_events_site_history
ON app.audit_events (tenant_id, site_id, occurred_at DESC, id);
CREATE INDEX audit_events_aggregate_history
ON app.audit_events (tenant_id, aggregate_kind, aggregate_id, aggregate_sequence);

CREATE FUNCTION app.validate_invitation_audit_event() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.actor_identifier !~
       '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR NOT EXISTS (
            SELECT 1
            FROM app.invitations invitation
            WHERE invitation.tenant_id = NEW.tenant_id
              AND invitation.site_id = NEW.site_id
              AND invitation.id = NEW.aggregate_id
              AND invitation.inviter_user_id = NEW.actor_identifier::uuid
              AND invitation.role_key = NEW.facts ->> 'role_key'
       )
    THEN
        RAISE EXCEPTION 'audit event reference is invalid' USING ERRCODE = '23503';
    END IF;
    RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION app.validate_invitation_audit_event() FROM PUBLIC;
CREATE TRIGGER invitation_audit_reference_guard
BEFORE INSERT ON app.audit_events
FOR EACH ROW EXECUTE FUNCTION app.validate_invitation_audit_event();

CREATE TRIGGER audit_events_immutable
BEFORE UPDATE OR DELETE ON app.audit_events
FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

ALTER TABLE app.audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.audit_events FORCE ROW LEVEL SECURITY;
CREATE POLICY audit_event_scope ON app.audit_events
FOR INSERT TO signal_api
WITH CHECK (
    tenant_id = app.current_tenant_id()
    AND site_id = app.current_site_id()
    AND actor_identifier = app.current_actor_user_id()::text
);

GRANT SELECT (
    tenant_id,
    user_id,
    role_key,
    state,
    authorization_epoch
) ON app.memberships TO signal_api;
GRANT SELECT (
    tenant_id,
    site_id,
    user_id,
    state,
    authorization_epoch
) ON app.site_memberships TO signal_api;
GRANT SELECT (
    tenant_id,
    id,
    site_id,
    email_normalized,
    role_key,
    inviter_user_id,
    inviter_membership_epoch,
    inviter_site_authorization_epoch,
    expires_at,
    consumed_at,
    revoked_at,
    created_at
) ON app.invitations TO signal_api;
GRANT INSERT (
    tenant_id,
    id,
    site_id,
    email_normalized,
    role_key,
    token_hash,
    inviter_user_id,
    inviter_membership_epoch,
    inviter_site_authorization_epoch,
    expires_at,
    created_at
) ON app.invitations TO signal_api;
GRANT INSERT (
    tenant_id,
    id,
    site_id,
    aggregate_kind,
    aggregate_id,
    aggregate_sequence,
    actor_kind,
    actor_identifier,
    event_type,
    facts,
    previous_hash,
    event_hash,
    occurred_at
) ON app.audit_events TO signal_api;
