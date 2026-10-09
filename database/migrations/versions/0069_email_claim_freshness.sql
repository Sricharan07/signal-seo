ALTER TABLE control.email_identity_claims ADD COLUMN outcome text NOT NULL DEFAULT 'recorded'
    CHECK (outcome IN ('recorded','unverified','stale','outside_window'));

CREATE OR REPLACE FUNCTION control.record_email_identity_claim(
    p_session uuid,p_issuer text,p_subject text,p_address text,p_issued timestamptz
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s control.identity_sessions%%ROWTYPE; h control.email_claim_heads%%ROWTYPE;
    v_digest bytea; v_epoch bigint; v_address text:=p_address;
    v_outcome text:='recorded'; v_head_time timestamptz:=p_issued;
BEGIN
    IF session_user<>'signal_identity' THEN RAISE EXCEPTION 'email_claim_denied'; END IF;
    SELECT * INTO s FROM control.identity_sessions WHERE id=p_session
        AND token_hash=control.current_identity_session_hash() AND revoked_at IS NULL;
    IF NOT FOUND OR p_issued IS NULL THEN RAISE EXCEPTION 'email_claim_denied'; END IF;
    PERFORM 1 FROM control.users WHERE id=s.user_id AND oidc_issuer=p_issuer
        AND oidc_subject=p_subject AND disabled_at IS NULL FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'email_claim_denied'; END IF;
    SELECT * INTO h FROM control.email_claim_heads WHERE user_id=s.user_id FOR UPDATE;
    IF p_issued>transaction_timestamp()+interval '30 seconds'
        OR p_issued<transaction_timestamp()-interval '11 minutes' THEN
        v_outcome:='outside_window';
    ELSIF h.user_id IS NOT NULL AND p_issued<h.issued_at THEN
        v_outcome:='stale';
    ELSIF p_address IS NULL THEN
        v_outcome:='unverified';
    END IF;
    IF v_outcome IN ('stale','outside_window') THEN
        -- Invalidate prior opt-ins without backdating or accepting a future watermark.
        v_address:=NULL;
        v_head_time:=GREATEST(h.issued_at,LEAST(p_issued,transaction_timestamp()));
    END IF;
    v_digest:=CASE WHEN v_address IS NULL THEN NULL
        ELSE sha256(convert_to(v_address,'UTF8')) END;
    v_epoch:=COALESCE(h.claim_epoch,0)+CASE WHEN h.user_id IS NULL
        OR h.address_sha256 IS DISTINCT FROM v_digest
        OR v_outcome IN ('stale','outside_window') THEN 1 ELSE 0 END;
    INSERT INTO control.email_claim_heads VALUES(s.user_id,v_digest,v_epoch,v_head_time)
        ON CONFLICT(user_id) DO UPDATE SET address_sha256=EXCLUDED.address_sha256,
            claim_epoch=EXCLUDED.claim_epoch,issued_at=EXCLUDED.issued_at;
    INSERT INTO control.email_identity_claims(session_id,user_id,address,address_sha256,
        claim_epoch,issued_at,outcome)
        VALUES(s.id,s.user_id,v_address,v_digest,v_epoch,p_issued,v_outcome);
END $$;
