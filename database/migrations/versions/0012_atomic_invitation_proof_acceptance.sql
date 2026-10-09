ALTER TABLE control.invitation_identity_proofs
    DROP CONSTRAINT invitation_identity_proofs_consumed_at_check,
    ADD CONSTRAINT invitation_identity_proofs_consumption_shape CHECK (
        consumed_at IS NULL
        OR (
            consumed_at >= created_at
            AND consumed_at <= expires_at
        )
    );

DROP TRIGGER invitation_identity_proofs_immutable
ON control.invitation_identity_proofs;

CREATE FUNCTION control.guard_invitation_identity_proof_transition() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
AS $$
BEGIN
    IF TG_OP = 'UPDATE'
       AND CURRENT_USER = 'signal_migrator'
       AND OLD.consumed_at IS NULL
       AND NEW.consumed_at IS NOT NULL
       AND NEW.consumed_at IS NOT DISTINCT FROM transaction_timestamp()
       AND ROW(
            NEW.id,
            NEW.token_hash,
            NEW.oidc_issuer,
            NEW.oidc_subject,
            NEW.verified_email,
            NEW.identity_issued_at,
            NEW.identity_expires_at,
            NEW.expires_at,
            NEW.created_at
       ) IS NOT DISTINCT FROM ROW(
            OLD.id,
            OLD.token_hash,
            OLD.oidc_issuer,
            OLD.oidc_subject,
            OLD.verified_email,
            OLD.identity_issued_at,
            OLD.identity_expires_at,
            OLD.expires_at,
            OLD.created_at
       )
    THEN
        RETURN NEW;
    END IF;

    IF TG_OP = 'DELETE'
       AND CURRENT_USER = 'signal_migrator'
       AND OLD.expires_at <= transaction_timestamp()
    THEN
        RETURN OLD;
    END IF;

    RAISE EXCEPTION 'invitation identity proofs are immutable outside guarded consumption or cleanup'
        USING ERRCODE = '55000';
END
$$;
REVOKE ALL ON FUNCTION control.guard_invitation_identity_proof_transition() FROM PUBLIC;

CREATE TRIGGER invitation_identity_proofs_guarded_transition
BEFORE UPDATE OR DELETE ON control.invitation_identity_proofs
FOR EACH ROW EXECUTE FUNCTION control.guard_invitation_identity_proof_transition();

CREATE POLICY invitation_identity_proof_cleanup_read
ON control.invitation_identity_proofs
FOR SELECT TO signal_migrator
USING (expires_at <= transaction_timestamp());

CREATE POLICY invitation_identity_proof_cleanup_delete
ON control.invitation_identity_proofs
FOR DELETE TO signal_migrator
USING (expires_at <= transaction_timestamp());

CREATE POLICY invitation_identity_proof_cleanup_lock
ON control.invitation_identity_proofs
FOR UPDATE TO signal_migrator
USING (expires_at <= transaction_timestamp())
WITH CHECK (expires_at <= transaction_timestamp());

CREATE FUNCTION control.accept_site_invitation_with_proof(
    p_identity_proof_hash bytea,
    p_invitation_id uuid,
    p_invitation_token_hash bytea,
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
    v_oidc_issuer text;
    v_oidc_subject text;
    v_verified_email text;
    v_now timestamptz := transaction_timestamp();
    v_result record;
BEGIN
    IF p_identity_proof_hash IS NULL
       OR octet_length(p_identity_proof_hash) != 32
       OR p_invitation_token_hash IS NULL
       OR octet_length(p_invitation_token_hash) != 32
    THEN
        RETURN;
    END IF;

    PERFORM set_config(
        'signal.invitation_identity_proof_hash',
        encode(p_identity_proof_hash, 'hex'),
        true
    );

    SELECT
        proof.oidc_issuer,
        proof.oidc_subject,
        proof.verified_email
    INTO
        v_oidc_issuer,
        v_oidc_subject,
        v_verified_email
    FROM control.invitation_identity_proofs proof
    WHERE proof.token_hash = p_identity_proof_hash
      AND proof.consumed_at IS NULL
      AND proof.expires_at > v_now
      AND proof.identity_expires_at > v_now
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    SELECT accepted.*
    INTO v_result
    FROM control.accept_site_invitation(
        p_invitation_id,
        p_invitation_token_hash,
        v_oidc_issuer,
        v_oidc_subject,
        v_verified_email,
        p_display_name,
        p_candidate_user_id,
        p_membership_id,
        p_site_membership_id,
        p_event_id
    ) accepted;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    UPDATE control.invitation_identity_proofs proof
    SET consumed_at = v_now
    WHERE proof.token_hash = p_identity_proof_hash
      AND proof.consumed_at IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'invitation proof acceptance lost its locked row'
            USING ERRCODE = '40001';
    END IF;

    RETURN QUERY SELECT
        v_result.accepted_user_id,
        v_result.accepted_tenant_id,
        v_result.accepted_site_id,
        v_result.accepted_role_key,
        v_result.accepted_at,
        v_result.accepted_event_hash;
END
$$;
REVOKE ALL ON FUNCTION control.accept_site_invitation_with_proof(
    bytea,
    uuid,
    bytea,
    text,
    uuid,
    uuid,
    uuid,
    uuid
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.accept_site_invitation_with_proof(
    bytea,
    uuid,
    bytea,
    text,
    uuid,
    uuid,
    uuid,
    uuid
) TO signal_identity;

REVOKE EXECUTE ON FUNCTION control.accept_site_invitation(
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
) FROM signal_identity;

CREATE FUNCTION control.cleanup_invitation_identity_proofs(
    p_batch_size integer DEFAULT 500
) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
DECLARE
    v_deleted integer;
BEGIN
    IF p_batch_size IS NULL OR p_batch_size NOT BETWEEN 1 AND 1000 THEN
        RAISE EXCEPTION 'invitation identity proof cleanup batch is invalid'
            USING ERRCODE = '22023';
    END IF;

    WITH candidates AS (
        SELECT proof.id
        FROM control.invitation_identity_proofs proof
        WHERE proof.expires_at <= transaction_timestamp()
        ORDER BY proof.expires_at, proof.id
        FOR UPDATE SKIP LOCKED
        LIMIT p_batch_size
    )
    DELETE FROM control.invitation_identity_proofs proof
    USING candidates
    WHERE proof.id = candidates.id;

    GET DIAGNOSTICS v_deleted = ROW_COUNT;
    RETURN v_deleted;
END
$$;
REVOKE ALL ON FUNCTION control.cleanup_invitation_identity_proofs(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.cleanup_invitation_identity_proofs(integer)
TO signal_scheduler;
