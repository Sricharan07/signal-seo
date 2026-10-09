CREATE FUNCTION app.current_session_hash() RETURNS bytea
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog
AS $$
    SELECT CASE
        WHEN current_setting('signal.session_hash', true) ~ '^[0-9a-f]{64}$'
        THEN decode(current_setting('signal.session_hash', true), 'hex')
        ELSE NULL
    END
$$;
GRANT EXECUTE ON FUNCTION app.current_session_hash() TO signal_identity;
GRANT EXECUTE ON FUNCTION app.current_tenant_id(), app.current_site_id() TO signal_identity;

CREATE TABLE control.users (
    id uuid PRIMARY KEY,
    oidc_issuer text NOT NULL CHECK (length(oidc_issuer) BETWEEN 1 AND 2048),
    oidc_subject text NOT NULL CHECK (length(oidc_subject) BETWEEN 1 AND 512),
    display_name text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 200),
    contact_email text CHECK (contact_email IS NULL OR length(contact_email) BETWEEN 3 AND 320),
    disabled_at timestamptz,
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (oidc_issuer, oidc_subject)
);

CREATE TABLE control.identity_sessions (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES control.users (id),
    token_hash bytea NOT NULL CHECK (octet_length(token_hash) = 32),
    auth_time timestamptz NOT NULL,
    authentication_level text NOT NULL CHECK (authentication_level IN ('primary', 'mfa')),
    recovery_generation text NOT NULL CHECK (length(recovery_generation) BETWEEN 1 AND 128),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    session_version bigint NOT NULL DEFAULT 1 CHECK (session_version > 0),
    last_seen_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (token_hash),
    UNIQUE (id, user_id),
    CHECK (expires_at > auth_time),
    CHECK (last_seen_at >= auth_time)
);
CREATE INDEX identity_sessions_user_expiry
ON control.identity_sessions (user_id, expires_at DESC);

CREATE TABLE app.memberships (
    tenant_id uuid NOT NULL REFERENCES app.tenants (tenant_id),
    id uuid NOT NULL,
    user_id uuid NOT NULL REFERENCES control.users (id),
    role_key text NOT NULL CHECK (
        role_key IN ('viewer', 'analyst', 'editor', 'approver', 'admin', 'owner')
    ),
    state text NOT NULL CHECK (state IN ('invited', 'active', 'suspended', 'removed')),
    authorization_epoch bigint NOT NULL CHECK (authorization_epoch > 0),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, user_id)
);
CREATE INDEX memberships_state_role
ON app.memberships (tenant_id, state, role_key, id);

CREATE TABLE app.sessions (
    tenant_id uuid NOT NULL REFERENCES app.tenants (tenant_id),
    id uuid NOT NULL,
    identity_session_id uuid NOT NULL,
    user_id uuid NOT NULL,
    session_token_hash bytea NOT NULL CHECK (octet_length(session_token_hash) = 32),
    oidc_session_ref text,
    auth_time timestamptz NOT NULL,
    mfa_level text NOT NULL CHECK (mfa_level IN ('primary', 'mfa')),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    last_seen_at timestamptz NOT NULL,
    session_version bigint NOT NULL DEFAULT 1 CHECK (session_version > 0),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (session_token_hash),
    FOREIGN KEY (tenant_id, user_id) REFERENCES app.memberships (tenant_id, user_id),
    FOREIGN KEY (identity_session_id, user_id)
        REFERENCES control.identity_sessions (id, user_id),
    CHECK (expires_at > auth_time),
    CHECK (last_seen_at >= auth_time)
);
CREATE INDEX sessions_user_expiry
ON app.sessions (tenant_id, user_id, expires_at DESC);

CREATE TABLE app.site_memberships (
    tenant_id uuid NOT NULL,
    site_id uuid NOT NULL,
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    permission_set jsonb NOT NULL CHECK (
        permission_set =
        '{"permissions":["site.snapshot.request"],"schema_version":1}'::jsonb
    ),
    authorization_epoch bigint NOT NULL CHECK (authorization_epoch > 0),
    state text NOT NULL CHECK (state IN ('active', 'suspended', 'removed')),
    row_version bigint NOT NULL DEFAULT 1 CHECK (row_version > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, site_id, id),
    UNIQUE (tenant_id, site_id, user_id),
    FOREIGN KEY (tenant_id, site_id) REFERENCES app.sites (tenant_id, id),
    FOREIGN KEY (tenant_id, user_id) REFERENCES app.memberships (tenant_id, user_id)
);
CREATE INDEX site_memberships_user_state
ON app.site_memberships (tenant_id, user_id, state, site_id);

ALTER TABLE app.memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.memberships FORCE ROW LEVEL SECURITY;
CREATE POLICY membership_scope ON app.memberships
USING (tenant_id = app.current_tenant_id())
WITH CHECK (tenant_id = app.current_tenant_id());

ALTER TABLE app.sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.sessions FORCE ROW LEVEL SECURITY;
CREATE POLICY session_scope ON app.sessions
USING (
    tenant_id = app.current_tenant_id()
    OR (
        CURRENT_USER = 'signal_identity'
        AND session_token_hash = app.current_session_hash()
    )
)
WITH CHECK (tenant_id = app.current_tenant_id());

ALTER TABLE app.site_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.site_memberships FORCE ROW LEVEL SECURITY;
CREATE POLICY site_membership_scope ON app.site_memberships
USING (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id())
WITH CHECK (tenant_id = app.current_tenant_id() AND site_id = app.current_site_id());

GRANT SELECT ON control.users, control.identity_sessions TO signal_identity;
GRANT SELECT ON app.tenants, app.sites, app.memberships, app.sessions, app.site_memberships
TO signal_identity;
GRANT SELECT, INSERT ON app.memberships, app.site_memberships TO signal_bootstrap;
