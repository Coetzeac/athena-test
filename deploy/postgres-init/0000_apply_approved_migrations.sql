\set ON_ERROR_STOP on

BEGIN;

\ir /athena-migrations/0001_runtime_state.sql
INSERT INTO athena.schema_migrations(version, filename, content_sha256)
VALUES (1, '0001_runtime_state.sql', '7778a7bc9d2c50f59369e0aee751548857b562bb9575cc6a0ebac40ac6b13839');

\ir /athena-migrations/0002_recovery_catalog.sql
INSERT INTO athena.schema_migrations(version, filename, content_sha256)
VALUES (2, '0002_recovery_catalog.sql', '26c8cf911a0098576b0dc5ef31803de307802259f1202adf87b1e475ddb5122a');

COMMIT;
