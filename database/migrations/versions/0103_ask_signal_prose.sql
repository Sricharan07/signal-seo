-- One additional deterministic call identity, never an arbitrary child-call grant.
CREATE FUNCTION control.assistant_model_pending(p_id uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(
  SELECT 1 FROM app.assistant_messages m
  WHERE m.role='signal' AND NOT m.finalized
   AND m.user_id=nullif(current_setting('signal.actor_user_id',true),'')::uuid
   AND (m.id=p_id OR (
    overlay(overlay(encode(substr(sha256(convert_to('ask-signal-entailment:'||m.id::text,'UTF8')),1,16),'hex')
      placing '4' from 13 for 1) placing '8' from 17 for 1)::uuid=p_id
    AND EXISTS(SELECT 1 FROM app.model_budget_calls c JOIN app.model_budget_receipts r
     ON r.tenant_id=c.tenant_id AND r.site_id=c.site_id AND r.call_id=c.id
     WHERE c.tenant_id=m.tenant_id AND c.site_id=m.site_id AND c.id=m.id
      AND c.role='owner_answers')
   ))
 );
$$;
ALTER FUNCTION control.assistant_model_pending(uuid) OWNER TO signal_migrator;
REVOKE ALL ON FUNCTION control.assistant_model_pending(uuid) FROM PUBLIC;

-- Preserve the existing shared bodies and all admission/permission/ledger checks.
DO $$ DECLARE n text; d text; old_guard text :=
 'EXISTS(SELECT 1 FROM app.assistant_messages WHERE id=p_id AND role=''signal'' AND NOT finalized)';
BEGIN
 FOREACH n IN ARRAY ARRAY['reserve','dispatch','finish'] LOOP
  SELECT pg_get_functiondef(p.oid) INTO STRICT d FROM pg_proc p
   JOIN pg_namespace s ON s.oid=p.pronamespace
   WHERE s.nspname='control' AND p.proname='port_model_budget_'||n;
  IF strpos(d,old_guard)=0 THEN RAISE EXCEPTION 'assistant budget guard missing'; END IF;
  EXECUTE replace(d,old_guard,'control.assistant_model_pending(p_id)');
 END LOOP;
END; $$;
