CREATE SCHEMA IF NOT EXISTS athena;

CREATE TABLE IF NOT EXISTS athena.schema_migrations (
    version integer PRIMARY KEY CHECK (version > 0),
    filename text NOT NULL UNIQUE CHECK (length(filename) > 0),
    content_sha256 char(64) NOT NULL UNIQUE CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE athena.audit_ledger_entries (
    sequence bigint PRIMARY KEY CHECK (sequence > 0),
    recorded_at timestamptz NOT NULL,
    event_type text NOT NULL CHECK (length(event_type) > 0),
    actor text NOT NULL CHECK (length(actor) > 0),
    payload jsonb NOT NULL,
    previous_hash char(64) NOT NULL CHECK (previous_hash ~ '^[0-9a-f]{64}$'),
    hash char(64) NOT NULL UNIQUE CHECK (hash ~ '^[0-9a-f]{64}$'),
    inserted_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE athena.evidence_records (
    record_id text PRIMARY KEY CHECK (record_id ~ '^ATH-[A-Z]{3}-[0-9A-F]{24}$'),
    record_type text NOT NULL,
    record_sha256 char(64) NOT NULL UNIQUE CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    content_sha256 char(64) NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    provenance_sha256 char(64) NOT NULL CHECK (provenance_sha256 ~ '^[0-9a-f]{64}$'),
    record jsonb NOT NULL,
    ledger_hash char(64) NOT NULL UNIQUE REFERENCES athena.audit_ledger_entries(hash),
    recorded_at timestamptz NOT NULL
);

CREATE TABLE athena.historical_manifests (
    manifest_id text PRIMARY KEY,
    manifest_sha256 char(64) NOT NULL UNIQUE CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    object_key text NOT NULL UNIQUE,
    object_version_id text NOT NULL,
    scope text NOT NULL CHECK (scope IN ('FULL_APPROVED_HISTORY', 'BOUNDED_APPROVED_SCOPE')),
    manifest jsonb NOT NULL,
    recorded_at timestamptz NOT NULL
);

CREATE TABLE athena.historical_checkpoints (
    checkpoint_id text PRIMARY KEY,
    manifest_id text NOT NULL REFERENCES athena.historical_manifests(manifest_id),
    window_id text NOT NULL,
    status text NOT NULL,
    previous_hash char(64) NOT NULL CHECK (previous_hash ~ '^[0-9a-f]{64}$'),
    hash char(64) NOT NULL UNIQUE CHECK (hash ~ '^[0-9a-f]{64}$'),
    payload jsonb NOT NULL,
    recorded_at timestamptz NOT NULL,
    UNIQUE (manifest_id, window_id)
);

CREATE TABLE athena.quota_reservations (
    reservation_id text PRIMARY KEY,
    manifest_id text NOT NULL REFERENCES athena.historical_manifests(manifest_id),
    window_id text NOT NULL,
    credits integer NOT NULL CHECK (credits > 0),
    previous_hash char(64) NOT NULL CHECK (previous_hash ~ '^[0-9a-f]{64}$'),
    hash char(64) NOT NULL UNIQUE CHECK (hash ~ '^[0-9a-f]{64}$'),
    reserved_at timestamptz NOT NULL,
    UNIQUE (manifest_id, window_id)
);

CREATE TABLE athena.quarantine_index (
    quarantine_id text PRIMARY KEY,
    subject_type text NOT NULL,
    subject_id text NOT NULL,
    reasons jsonb NOT NULL,
    retained_object_key text,
    retained_object_version_id text,
    ledger_hash char(64) NOT NULL UNIQUE REFERENCES athena.audit_ledger_entries(hash),
    quarantined_at timestamptz NOT NULL
);

CREATE TABLE athena.object_catalog (
    content_sha256 char(64) PRIMARY KEY CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    kind text NOT NULL,
    bucket text NOT NULL,
    object_key text NOT NULL,
    object_version_id text NOT NULL,
    content_length bigint NOT NULL CHECK (content_length > 0),
    content_type text NOT NULL,
    server_side_encryption text NOT NULL CHECK (server_side_encryption = 'AES256'),
    recorded_at timestamptz NOT NULL,
    UNIQUE (bucket, object_key, object_version_id)
);

CREATE TABLE athena.runtime_status (
    status_id bigserial PRIMARY KEY,
    observed_at timestamptz NOT NULL,
    state text NOT NULL,
    status jsonb NOT NULL,
    status_sha256 char(64) NOT NULL UNIQUE CHECK (status_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE OR REPLACE FUNCTION athena.reject_immutable_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'ATHENA immutable table % does not permit %', TG_TABLE_NAME, TG_OP;
END;
$$;

CREATE TRIGGER audit_ledger_entries_immutable
BEFORE UPDATE OR DELETE ON athena.audit_ledger_entries
FOR EACH ROW EXECUTE FUNCTION athena.reject_immutable_mutation();

CREATE TRIGGER evidence_records_immutable
BEFORE UPDATE OR DELETE ON athena.evidence_records
FOR EACH ROW EXECUTE FUNCTION athena.reject_immutable_mutation();

CREATE TRIGGER historical_manifests_immutable
BEFORE UPDATE OR DELETE ON athena.historical_manifests
FOR EACH ROW EXECUTE FUNCTION athena.reject_immutable_mutation();

CREATE TRIGGER historical_checkpoints_immutable
BEFORE UPDATE OR DELETE ON athena.historical_checkpoints
FOR EACH ROW EXECUTE FUNCTION athena.reject_immutable_mutation();

CREATE TRIGGER quota_reservations_immutable
BEFORE UPDATE OR DELETE ON athena.quota_reservations
FOR EACH ROW EXECUTE FUNCTION athena.reject_immutable_mutation();

CREATE TRIGGER quarantine_index_immutable
BEFORE UPDATE OR DELETE ON athena.quarantine_index
FOR EACH ROW EXECUTE FUNCTION athena.reject_immutable_mutation();

CREATE TRIGGER object_catalog_immutable
BEFORE UPDATE OR DELETE ON athena.object_catalog
FOR EACH ROW EXECUTE FUNCTION athena.reject_immutable_mutation();

CREATE TRIGGER runtime_status_immutable
BEFORE UPDATE OR DELETE ON athena.runtime_status
FOR EACH ROW EXECUTE FUNCTION athena.reject_immutable_mutation();

CREATE TRIGGER schema_migrations_immutable
BEFORE UPDATE OR DELETE ON athena.schema_migrations
FOR EACH ROW EXECUTE FUNCTION athena.reject_immutable_mutation();
