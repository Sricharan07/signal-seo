-- Provision on a separate PostgreSQL cluster, never the Signal primary cluster.
-- The deployment operator supplies distinct credentials and backup/retention policy.
CREATE ROLE signal_journal_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE signal_journal_writer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE signal_journal_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO signal_journal_owner;
CREATE SCHEMA authority_journal AUTHORIZATION signal_journal_owner;
GRANT USAGE ON SCHEMA authority_journal TO signal_journal_writer, signal_journal_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE signal_journal_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

SET ROLE signal_journal_owner;
CREATE TABLE authority_journal.stream (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    generation uuid NOT NULL,
    head_position bigint NOT NULL CHECK (head_position >= 0),
    head_hash bytea NOT NULL CHECK (octet_length(head_hash) = 32)
);
INSERT INTO authority_journal.stream VALUES (true, gen_random_uuid(), 0, decode(repeat('00', 32), 'hex'));

CREATE TABLE authority_journal.entries (
    position bigint PRIMARY KEY CHECK (position > 0),
    generation uuid NOT NULL,
    event_id uuid NOT NULL UNIQUE,
    body bytea NOT NULL CHECK (octet_length(body) BETWEEN 1 AND 8192),
    body_hash bytea NOT NULL CHECK (octet_length(body_hash) = 32),
    writer_signature bytea NOT NULL CHECK (octet_length(writer_signature) = 64),
    previous_hash bytea NOT NULL CHECK (octet_length(previous_hash) = 32),
    entry_hash bytea NOT NULL CHECK (octet_length(entry_hash) = 32),
    appended_at timestamptz NOT NULL DEFAULT now()
);
CREATE FUNCTION authority_journal.deny_entry_mutation() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
BEGIN RAISE EXCEPTION 'journal entries are immutable' USING ERRCODE = '55000'; END
$$;
CREATE TRIGGER entries_immutable BEFORE UPDATE OR DELETE ON authority_journal.entries
FOR EACH ROW EXECUTE FUNCTION authority_journal.deny_entry_mutation();

CREATE FUNCTION authority_journal.append(
    p_event_id uuid, p_body bytea, p_signature bytea
) RETURNS TABLE (generation uuid, stream_position bigint, body_hash bytea)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $$
DECLARE v_stream authority_journal.stream%ROWTYPE;
        v_existing authority_journal.entries%ROWTYPE;
        v_body_hash bytea;
        v_next_hash bytea;
BEGIN
    IF session_user != 'signal_journal_writer' OR p_event_id IS NULL
       OR octet_length(p_body) NOT BETWEEN 1 AND 8192
       OR octet_length(p_signature) != 64 THEN
        RAISE EXCEPTION 'journal append denied' USING ERRCODE = '42501';
    END IF;
    v_body_hash := public.digest(p_body, 'sha256');
    SELECT * INTO v_stream FROM authority_journal.stream WHERE singleton FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'journal head unavailable' USING ERRCODE = 'XX001';
    END IF;
    SELECT * INTO v_existing FROM authority_journal.entries WHERE event_id = p_event_id;
    IF FOUND THEN
        IF v_existing.body_hash != v_body_hash OR v_existing.body != p_body
           OR v_existing.writer_signature != p_signature THEN
            RAISE EXCEPTION 'journal event identity conflict' USING ERRCODE = '23505';
        END IF;
        RETURN QUERY SELECT v_existing.generation, v_existing.position, v_existing.body_hash;
        RETURN;
    END IF;
    v_next_hash := public.digest(v_stream.head_hash || int8send(v_stream.head_position + 1)
                                 || v_body_hash || p_signature, 'sha256');
    INSERT INTO authority_journal.entries
        (position, generation, event_id, body, body_hash, writer_signature,
         previous_hash, entry_hash)
    VALUES (v_stream.head_position + 1, v_stream.generation, p_event_id,
            p_body, v_body_hash, p_signature, v_stream.head_hash, v_next_hash);
    UPDATE authority_journal.stream
    SET head_position = v_stream.head_position + 1, head_hash = v_next_hash
    WHERE singleton;
    RETURN QUERY SELECT v_stream.generation, v_stream.head_position + 1, v_body_hash;
END
$$;
REVOKE ALL ON FUNCTION authority_journal.append(uuid, bytea, bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION authority_journal.append(uuid, bytea, bytea)
TO signal_journal_writer;
GRANT SELECT ON authority_journal.stream, authority_journal.entries
TO signal_journal_reader, signal_journal_writer;
RESET ROLE;

CREATE SCHEMA write_journal AUTHORIZATION signal_journal_owner;
GRANT USAGE ON SCHEMA write_journal TO signal_journal_writer, signal_journal_reader;
SET ROLE signal_journal_owner;
CREATE TABLE write_journal.stream (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    generation uuid NOT NULL,
    head_position bigint NOT NULL CHECK (head_position >= 0),
    head_hash bytea NOT NULL CHECK (octet_length(head_hash) = 32)
);
INSERT INTO write_journal.stream VALUES (true, gen_random_uuid(), 0, decode(repeat('00', 32), 'hex'));
CREATE TABLE write_journal.entries (
    position bigint PRIMARY KEY CHECK (position > 0),
    generation uuid NOT NULL,
    operation_id uuid NOT NULL UNIQUE,
    body bytea NOT NULL CHECK (octet_length(body) BETWEEN 1 AND 8192),
    body_hash bytea NOT NULL CHECK (octet_length(body_hash) = 32),
    writer_signature bytea NOT NULL CHECK (octet_length(writer_signature) = 64),
    previous_hash bytea NOT NULL CHECK (octet_length(previous_hash) = 32),
    entry_hash bytea NOT NULL CHECK (octet_length(entry_hash) = 32),
    appended_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER write_entries_immutable BEFORE UPDATE OR DELETE ON write_journal.entries
FOR EACH ROW EXECUTE FUNCTION authority_journal.deny_entry_mutation();
CREATE FUNCTION write_journal.append(
    p_operation_id uuid, p_body bytea, p_signature bytea
) RETURNS TABLE (generation uuid, stream_position bigint, body_hash bytea)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_stream write_journal.stream%ROWTYPE;
        v_existing write_journal.entries%ROWTYPE;
        v_body_hash bytea;
        v_next_hash bytea;
BEGIN
    IF session_user != 'signal_journal_writer' OR p_operation_id IS NULL
       OR octet_length(p_body) NOT BETWEEN 1 AND 8192
       OR octet_length(p_signature) != 64 THEN
        RAISE EXCEPTION 'write intent append denied' USING ERRCODE = '42501';
    END IF;
    v_body_hash := public.digest(p_body, 'sha256');
    SELECT * INTO v_stream FROM write_journal.stream WHERE singleton FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'write intent head unavailable' USING ERRCODE = 'XX001'; END IF;
    SELECT * INTO v_existing FROM write_journal.entries WHERE operation_id = p_operation_id;
    IF FOUND THEN
        IF v_existing.body_hash != v_body_hash OR v_existing.body != p_body
           OR v_existing.writer_signature != p_signature THEN
            RAISE EXCEPTION 'write intent identity conflict' USING ERRCODE = '23505';
        END IF;
        RETURN QUERY SELECT v_existing.generation, v_existing.position, v_existing.body_hash;
        RETURN;
    END IF;
    v_next_hash := public.digest(v_stream.head_hash || int8send(v_stream.head_position + 1)
                                 || v_body_hash || p_signature, 'sha256');
    INSERT INTO write_journal.entries
        (position, generation, operation_id, body, body_hash, writer_signature,
         previous_hash, entry_hash)
    VALUES (v_stream.head_position + 1, v_stream.generation, p_operation_id,
            p_body, v_body_hash, p_signature, v_stream.head_hash, v_next_hash);
    UPDATE write_journal.stream
    SET head_position = v_stream.head_position + 1, head_hash = v_next_hash WHERE singleton;
    RETURN QUERY SELECT v_stream.generation, v_stream.head_position + 1, v_body_hash;
END $$;
REVOKE ALL ON FUNCTION write_journal.append(uuid, bytea, bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION write_journal.append(uuid, bytea, bytea) TO signal_journal_writer;
GRANT SELECT ON write_journal.stream, write_journal.entries
TO signal_journal_reader, signal_journal_writer;
RESET ROLE;
