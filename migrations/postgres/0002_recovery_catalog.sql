CREATE TABLE athena.recovery_catalog (
    recovery_id text PRIMARY KEY,
    component text NOT NULL,
    manifest_sha256 char(64) NOT NULL UNIQUE CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    manifest_object_key text NOT NULL,
    manifest_object_version_id text NOT NULL,
    backup_content_sha256 char(64) NOT NULL REFERENCES athena.object_catalog(content_sha256),
    source_terminal_sha256 char(64) NOT NULL CHECK (source_terminal_sha256 ~ '^[0-9a-f]{64}$'),
    backup_created_at timestamptz NOT NULL,
    restore_observed_at timestamptz,
    restored_content_sha256 char(64) CHECK (restored_content_sha256 ~ '^[0-9a-f]{64}$'),
    production_restore boolean NOT NULL DEFAULT false,
    CHECK ((restore_observed_at IS NULL) = (restored_content_sha256 IS NULL))
);

CREATE TRIGGER recovery_catalog_immutable
BEFORE UPDATE OR DELETE ON athena.recovery_catalog
FOR EACH ROW EXECUTE FUNCTION athena.reject_immutable_mutation();
