CREATE TABLE control.invitation_routes (
    invitation_id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    UNIQUE (tenant_id, site_id, invitation_id),
    FOREIGN KEY (tenant_id, site_id, invitation_id)
        REFERENCES app.invitations (tenant_id, site_id, id)
);
CREATE INDEX invitation_routes_scope
ON control.invitation_routes (tenant_id, site_id, invitation_id);
REVOKE ALL ON control.invitation_routes
FROM PUBLIC, signal_identity, signal_bootstrap, signal_api, signal_scheduler;

ALTER TABLE app.invitations NO FORCE ROW LEVEL SECURITY;
INSERT INTO control.invitation_routes (invitation_id, tenant_id, site_id, created_at)
SELECT id, tenant_id, site_id, created_at
FROM app.invitations;
ALTER TABLE app.invitations FORCE ROW LEVEL SECURITY;

CREATE FUNCTION control.route_site_invitation() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
BEGIN
    INSERT INTO control.invitation_routes (
        invitation_id,
        tenant_id,
        site_id,
        created_at
    ) VALUES (
        NEW.id,
        NEW.tenant_id,
        NEW.site_id,
        NEW.created_at
    );
    RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION control.route_site_invitation() FROM PUBLIC;
CREATE TRIGGER invitation_route_after_insert
AFTER INSERT ON app.invitations
FOR EACH ROW EXECUTE FUNCTION control.route_site_invitation();

ALTER TABLE app.invitations
    ADD COLUMN accepted_user_id uuid,
    DROP CONSTRAINT invitations_consumed_at_check,
    ADD CONSTRAINT invitations_consumption_shape CHECK (
        (
            consumed_at IS NULL
            AND accepted_user_id IS NULL
        )
        OR
        (
            consumed_at IS NOT NULL
            AND accepted_user_id IS NOT NULL
            AND consumed_at >= created_at
            AND consumed_at <= expires_at
        )
    ),
    ADD FOREIGN KEY (tenant_id, accepted_user_id)
        REFERENCES app.memberships (tenant_id, user_id);
CREATE INDEX invitations_accepted_user
ON app.invitations (tenant_id, accepted_user_id)
WHERE accepted_user_id IS NOT NULL;

DROP TRIGGER invitations_immutable ON app.invitations;
CREATE FUNCTION app.guard_invitation_transition() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $$
BEGIN
    IF TG_OP = 'UPDATE'
       AND CURRENT_USER = 'signal_migrator'
       AND OLD.consumed_at IS NULL
       AND OLD.accepted_user_id IS NULL
       AND NEW.consumed_at IS NOT NULL
       AND NEW.consumed_at IS NOT DISTINCT FROM transaction_timestamp()
       AND NEW.accepted_user_id IS NOT NULL
       AND ROW(
            NEW.tenant_id,
            NEW.id,
            NEW.site_id,
            NEW.email_normalized,
            NEW.role_key,
            NEW.token_hash,
            NEW.inviter_user_id,
            NEW.inviter_membership_epoch,
            NEW.inviter_site_authorization_epoch,
            NEW.expires_at,
            NEW.revoked_at,
            NEW.created_at
       ) IS NOT DISTINCT FROM ROW(
            OLD.tenant_id,
            OLD.id,
            OLD.site_id,
            OLD.email_normalized,
            OLD.role_key,
            OLD.token_hash,
            OLD.inviter_user_id,
            OLD.inviter_membership_epoch,
            OLD.inviter_site_authorization_epoch,
            OLD.expires_at,
            OLD.revoked_at,
            OLD.created_at
       )
    THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'invitation records are immutable outside guarded acceptance'
        USING ERRCODE = '55000';
END
$$;
REVOKE ALL ON FUNCTION app.guard_invitation_transition() FROM PUBLIC;
CREATE TRIGGER invitations_guarded_transition
BEFORE UPDATE OR DELETE ON app.invitations
FOR EACH ROW EXECUTE FUNCTION app.guard_invitation_transition();

CREATE POLICY invitation_acceptance_update ON app.invitations
FOR UPDATE TO signal_migrator
USING (
    tenant_id = app.current_tenant_id()
    AND site_id = app.current_site_id()
)
WITH CHECK (
    tenant_id = app.current_tenant_id()
    AND site_id = app.current_site_id()
);

ALTER TABLE app.audit_events
    DROP CONSTRAINT audit_events_aggregate_sequence_check,
    DROP CONSTRAINT audit_events_event_type_check,
    DROP CONSTRAINT audit_events_previous_hash_check,
    ADD CONSTRAINT audit_events_aggregate_sequence_check CHECK (
        aggregate_sequence IN (1, 2)
    ),
    ADD CONSTRAINT audit_events_event_type_check CHECK (
        (aggregate_sequence = 1 AND event_type = 'invitation.created')
        OR
        (aggregate_sequence = 2 AND event_type = 'invitation.accepted')
    ),
    ADD CONSTRAINT audit_events_previous_hash_check CHECK (
        (aggregate_sequence = 1 AND previous_hash IS NULL)
        OR
        (aggregate_sequence = 2 AND octet_length(previous_hash) = 32)
    );

CREATE OR REPLACE FUNCTION app.validate_invitation_audit_event() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.actor_identifier !~
       '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    THEN
        RAISE EXCEPTION 'audit event reference is invalid' USING ERRCODE = '23503';
    END IF;

    IF NEW.event_type = 'invitation.created'
       AND EXISTS (
            SELECT 1
            FROM app.invitations invitation
            WHERE invitation.tenant_id = NEW.tenant_id
              AND invitation.site_id = NEW.site_id
              AND invitation.id = NEW.aggregate_id
              AND invitation.inviter_user_id = NEW.actor_identifier::uuid
              AND invitation.role_key = NEW.facts ->> 'role_key'
       )
    THEN
        RETURN NEW;
    END IF;

    IF NEW.event_type = 'invitation.accepted'
       AND EXISTS (
            SELECT 1
            FROM app.invitations invitation
            JOIN app.audit_events created
              ON created.tenant_id = invitation.tenant_id
             AND created.site_id = invitation.site_id
             AND created.aggregate_kind = 'invitation'
             AND created.aggregate_id = invitation.id
             AND created.aggregate_sequence = 1
            WHERE invitation.tenant_id = NEW.tenant_id
              AND invitation.site_id = NEW.site_id
              AND invitation.id = NEW.aggregate_id
              AND invitation.accepted_user_id = NEW.actor_identifier::uuid
              AND invitation.consumed_at IS NOT DISTINCT FROM NEW.occurred_at
              AND invitation.role_key = NEW.facts ->> 'role_key'
              AND created.event_hash = NEW.previous_hash
       )
    THEN
        RETURN NEW;
    END IF;

    RAISE EXCEPTION 'audit event reference is invalid' USING ERRCODE = '23503';
END
$$;

CREATE POLICY audit_acceptance_read ON app.audit_events
FOR SELECT TO signal_migrator
USING (
    tenant_id = app.current_tenant_id()
    AND site_id = app.current_site_id()
);
CREATE POLICY audit_acceptance_insert ON app.audit_events
FOR INSERT TO signal_migrator
WITH CHECK (
    tenant_id = app.current_tenant_id()
    AND site_id = app.current_site_id()
    AND actor_identifier = app.current_actor_user_id()::text
);

CREATE FUNCTION control.accept_site_invitation(
    p_invitation_id uuid,
    p_token_hash bytea,
    p_oidc_issuer text,
    p_oidc_subject text,
    p_verified_email text,
    p_display_name text,
    p_candidate_user_id uuid,
    p_membership_id uuid,
    p_site_membership_id uuid,
    p_event_id uuid
) RETURNS TABLE (
    accepted_user_id uuid,
    accepted_tenant_id uuid,
    accepted_site_id uuid,
    accepted_role_key text,
    accepted_at timestamptz,
    accepted_event_hash bytea
)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
DECLARE
    v_tenant_id uuid;
    v_site_id uuid;
    v_role_key text;
    v_user_id uuid;
    v_disabled_at timestamptz;
    v_previous_hash bytea;
    v_now timestamptz := transaction_timestamp();
    v_event_hash bytea;
    v_identity_exists boolean;
BEGIN
    IF p_invitation_id IS NULL
       OR p_token_hash IS NULL
       OR octet_length(p_token_hash) != 32
       OR p_oidc_issuer IS NULL
       OR length(p_oidc_issuer) NOT BETWEEN 1 AND 2048
       OR p_oidc_issuer ~ '[[:cntrl:]]'
       OR p_oidc_subject IS NULL
       OR length(p_oidc_subject) NOT BETWEEN 1 AND 512
       OR p_oidc_subject ~ '[[:cntrl:]]'
       OR p_verified_email IS NULL
       OR p_verified_email != lower(p_verified_email)
       OR p_display_name IS NULL
       OR length(p_display_name) NOT BETWEEN 1 AND 200
       OR p_display_name != btrim(p_display_name)
       OR p_display_name ~ '[[:cntrl:]]'
       OR EXISTS (
            SELECT 1
            FROM unnest(ARRAY[
                p_invitation_id,
                p_candidate_user_id,
                p_membership_id,
                p_site_membership_id,
                p_event_id
            ]) identifier
            WHERE identifier IS NULL
               OR identifier::text !~
                  '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       )
    THEN
        RETURN;
    END IF;

    SELECT route.tenant_id, route.site_id
    INTO v_tenant_id, v_site_id
    FROM control.invitation_routes route
    WHERE route.invitation_id = p_invitation_id;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    PERFORM set_config('signal.tenant_id', v_tenant_id::text, true);
    PERFORM set_config('signal.site_id', v_site_id::text, true);

    PERFORM 1
    FROM app.tenants tenant
    WHERE tenant.tenant_id = v_tenant_id
      AND tenant.lifecycle = 'active'
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    PERFORM 1
    FROM app.sites site
    WHERE site.tenant_id = v_tenant_id
      AND site.id = v_site_id
      AND site.state != 'archived'
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    SELECT invitation.role_key
    INTO v_role_key
    FROM app.invitations invitation
    WHERE invitation.tenant_id = v_tenant_id
      AND invitation.site_id = v_site_id
      AND invitation.id = p_invitation_id
      AND invitation.token_hash = p_token_hash
      AND invitation.email_normalized = p_verified_email
      AND invitation.consumed_at IS NULL
      AND invitation.revoked_at IS NULL
      AND invitation.expires_at > v_now
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    SELECT created.event_hash
    INTO v_previous_hash
    FROM app.audit_events created
    WHERE created.tenant_id = v_tenant_id
      AND created.site_id = v_site_id
      AND created.aggregate_kind = 'invitation'
      AND created.aggregate_id = p_invitation_id
      AND created.aggregate_sequence = 1;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            'signal:invitation-identity:' || p_oidc_issuer || chr(31) || p_oidc_subject,
            0
        )
    );
    SELECT identity_user.id, identity_user.disabled_at
    INTO v_user_id, v_disabled_at
    FROM control.users identity_user
    WHERE identity_user.oidc_issuer = p_oidc_issuer
      AND identity_user.oidc_subject = p_oidc_subject
    FOR UPDATE;
    v_identity_exists := FOUND;
    IF v_identity_exists AND v_disabled_at IS NOT NULL THEN
        RETURN;
    END IF;

    IF v_identity_exists AND EXISTS (
        SELECT 1
        FROM app.memberships membership
        WHERE membership.tenant_id = v_tenant_id
          AND membership.user_id = v_user_id
    )
    THEN
        RETURN;
    END IF;

    IF NOT v_identity_exists THEN
        v_user_id := p_candidate_user_id;
        INSERT INTO control.users (
            id,
            oidc_issuer,
            oidc_subject,
            display_name,
            contact_email,
            created_at,
            updated_at
        ) VALUES (
            v_user_id,
            p_oidc_issuer,
            p_oidc_subject,
            p_display_name,
            p_verified_email,
            v_now,
            v_now
        );
    END IF;

    INSERT INTO app.memberships (
        tenant_id,
        id,
        user_id,
        role_key,
        state,
        authorization_epoch,
        created_at,
        updated_at
    ) VALUES (
        v_tenant_id,
        p_membership_id,
        v_user_id,
        v_role_key,
        'active',
        1,
        v_now,
        v_now
    );
    INSERT INTO app.site_memberships (
        tenant_id,
        site_id,
        id,
        user_id,
        permission_set,
        authorization_epoch,
        state,
        created_at,
        updated_at
    ) VALUES (
        v_tenant_id,
        v_site_id,
        p_site_membership_id,
        v_user_id,
        '{"permissions":["site.snapshot.request"],"schema_version":1}'::jsonb,
        1,
        'active',
        v_now,
        v_now
    );

    PERFORM set_config('signal.actor_user_id', v_user_id::text, true);
    UPDATE app.invitations invitation
    SET consumed_at = v_now,
        accepted_user_id = v_user_id
    WHERE invitation.tenant_id = v_tenant_id
      AND invitation.site_id = v_site_id
      AND invitation.id = p_invitation_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'invitation acceptance lost its locked row' USING ERRCODE = '40001';
    END IF;

    v_event_hash := sha256(
        convert_to(
            'actor_identifier=' || v_user_id::text || E'\n'
            || 'actor_kind=user' || E'\n'
            || 'aggregate_id=' || p_invitation_id::text || E'\n'
            || 'aggregate_kind=invitation' || E'\n'
            || 'aggregate_sequence=2' || E'\n'
            || 'event_id=' || p_event_id::text || E'\n'
            || 'event_type=invitation.accepted' || E'\n'
            || 'facts.role_key=' || v_role_key || E'\n'
            || 'facts.schema_version=1' || E'\n'
            || 'occurred_at='
            || to_char(v_now AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
            || E'\n'
            || 'previous_hash=' || encode(v_previous_hash, 'hex') || E'\n'
            || 'site_id=' || v_site_id::text || E'\n'
            || 'tenant_id=' || v_tenant_id::text,
            'UTF8'
        )
    );
    INSERT INTO app.audit_events (
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
    ) VALUES (
        v_tenant_id,
        p_event_id,
        v_site_id,
        'invitation',
        p_invitation_id,
        2,
        'user',
        v_user_id::text,
        'invitation.accepted',
        jsonb_build_object('schema_version', 1, 'role_key', v_role_key),
        v_previous_hash,
        v_event_hash,
        v_now
    );

    RETURN QUERY SELECT
        v_user_id,
        v_tenant_id,
        v_site_id,
        v_role_key,
        v_now,
        v_event_hash;
END
$$;
REVOKE ALL ON FUNCTION control.accept_site_invitation(
    uuid,
    bytea,
    text,
    text,
    text,
    text,
    uuid,
    uuid,
    uuid,
    uuid
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.accept_site_invitation(
    uuid,
    bytea,
    text,
    text,
    text,
    text,
    uuid,
    uuid,
    uuid,
    uuid
) TO signal_identity;
