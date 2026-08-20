import json
import tempfile
import unittest
from pathlib import Path

from athena.persistence import (
    FilesystemProofStore,
    ObjectReference,
    PersistenceControlError,
    PostgresMigrationCatalog,
    RecoveryManifest,
    RecoveryController,
    RedisTransientQueue,
    RuntimePersistencePolicy,
    S3CompatibleObjectStore,
    build_redis_queue_from_environment,
    build_s3_store_from_environment,
    canonical_object_key,
    connect_postgres_from_environment,
    validate_compose_contract,
    validate_postgres_init_contract,
    validate_runtime_persistence_policy,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "runtime_persistence_policy.json"
MIGRATIONS = ROOT / "migrations" / "postgres"
COMPOSE = ROOT / "deploy" / "docker-compose.persistence.yml"
NOW = "2026-08-14T20:30:00+00:00"


class FakeS3:
    def __init__(self, *, return_version: bool = True) -> None:
        self.return_version = return_version
        self.objects: dict[tuple[str, str], dict] = {}
        self.latest: dict[str, str] = {}
        self.put_calls: list[dict] = []

    def head_object(self, **kwargs):
        key = kwargs["Key"]
        version = kwargs.get("VersionId") or self.latest.get(key)
        if not version or (key, version) not in self.objects:
            raise KeyError(key)
        item = self.objects[(key, version)]
        return {
            "VersionId": version,
            "ContentLength": len(item["Body"]),
            "ContentType": item["ContentType"],
            "Metadata": item["Metadata"],
            "ServerSideEncryption": item["ServerSideEncryption"],
        }

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        version = f"version-{len(self.put_calls)}" if self.return_version else ""
        key = kwargs["Key"]
        retained_version = version or "unversioned"
        self.objects[(key, retained_version)] = dict(kwargs)
        self.latest[key] = retained_version
        return {"VersionId": version}

    def get_object(self, **kwargs):
        item = self.objects[(kwargs["Key"], kwargs["VersionId"])]
        return {"Body": item["Body"]}


class FakeRedis:
    def __init__(self) -> None:
        self.items: dict[str, tuple[str, int]] = {}

    def set(self, key, value, *, ex, nx):
        if nx and key in self.items:
            return False
        self.items[key] = (value, ex)
        return True


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return list(self.rows)


class FakeConnection:
    def __init__(self) -> None:
        self.applied: list[tuple[int, str, str]] = []
        self.executions: list[tuple[str, object]] = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql, params=None):
        self.executions.append((sql, params))
        if sql.startswith("SELECT version"):
            return FakeResult(self.applied)
        if sql.startswith("INSERT INTO athena.schema_migrations"):
            self.applied.append(tuple(params))
        return FakeResult([])

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class RuntimePersistenceTests(unittest.TestCase):
    def test_approved_policy_migrations_schemas_and_compose_validate(self) -> None:
        status = validate_runtime_persistence_policy(POLICY_PATH, ROOT)
        self.assertTrue(status["valid"])
        self.assertEqual(status["resolution_id"], "ATHENA-RPR-001")
        self.assertEqual(status["postgres_collections"], 9)
        self.assertEqual(status["object_kinds"], 8)
        self.assertEqual(status["schemas"], 3)
        self.assertEqual(status["migrations"], 2)
        self.assertEqual(status["postgres_tables"], 10)
        self.assertEqual(status["development_init_migrations"], 2)
        self.assertEqual(status["development_services"], 4)
        self.assertEqual(status["rpo_minutes"], 60)
        self.assertEqual(status["rto_minutes"], 240)
        self.assertFalse(status["production_ready"])
        self.assertEqual(status["live_execution"], "prohibited")

    def test_protected_authority_recovery_and_live_controls_cannot_be_weakened(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "weakened.json"
            raw = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
            raw["stores"]["redis"]["canonical_record_permitted"] = True
            raw["recovery"]["recovery_point_objective_minutes"] = 1440
            raw["production_ready"] = True
            raw["live_execution"] = "permitted"
            raw["silent_override"] = True
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(PersistenceControlError):
                RuntimePersistencePolicy.from_file(path)

    def test_content_addressed_keys_reject_unapproved_kinds_and_digests(self) -> None:
        digest = "a" * 64
        self.assertEqual(
            canonical_object_key("market_raw", digest),
            f"objects/market_raw/sha256/aa/{digest}",
        )
        with self.assertRaises(PersistenceControlError):
            canonical_object_key("../../public", digest)
        with self.assertRaises(PersistenceControlError):
            canonical_object_key("market_raw", "A" * 64)

    def test_s3_adapter_requires_encryption_versioning_and_byte_verification(self) -> None:
        client = FakeS3()
        store = S3CompatibleObjectStore(client, "athena-private")
        payload = b"synthetic immutable market bytes"
        reference = store.put_immutable("market_raw", payload, "application/json")
        self.assertEqual(reference.server_side_encryption, "AES256")
        self.assertTrue(reference.version_id)
        self.assertEqual(store.get_verified(reference), payload)
        self.assertEqual(client.put_calls[0]["ServerSideEncryption"], "AES256")
        self.assertEqual(client.put_calls[0]["Metadata"]["athena-resolution"], "ATHENA-RPR-001")
        duplicate = store.put_immutable("market_raw", payload, "application/json")
        self.assertEqual(duplicate, reference)
        self.assertEqual(len(client.put_calls), 1)

    def test_s3_adapter_fails_when_versioning_or_retained_bytes_are_invalid(self) -> None:
        with self.assertRaises(PersistenceControlError):
            S3CompatibleObjectStore(FakeS3(return_version=False), "athena-private").put_immutable(
                "report", b"report", "application/json"
            )

        client = FakeS3()
        store = S3CompatibleObjectStore(client, "athena-private")
        reference = store.put_immutable("report", b"valid report", "application/json")
        client.objects[(reference.key, reference.version_id)]["Body"] = b"tampered"
        with self.assertRaises(PersistenceControlError):
            store.get_verified(reference)

    def test_redis_is_transient_bounded_and_cannot_hold_canonical_or_secret_material(self) -> None:
        policy = RuntimePersistencePolicy.from_file(POLICY_PATH)
        client = FakeRedis()
        queue = RedisTransientQueue(client, policy, clock=lambda: NOW)
        key = queue.enqueue("history-window", {"manifest_id": "M-1", "window_id": "W-1"}, 60)
        self.assertTrue(key.startswith("athena:transient:v1:history-window:"))
        self.assertEqual(client.items[key][1], 60)
        with self.assertRaises(PersistenceControlError):
            queue.enqueue("history-window", {"manifest_id": "M-1", "window_id": "W-1"}, 60)
        with self.assertRaises(PersistenceControlError):
            queue.enqueue("history-window", {"api_key": "must-not-enter-redis"}, 60)
        with self.assertRaises(PersistenceControlError):
            queue.enqueue("history-window", {"window_id": "W-2"}, 86401)

    def test_synthetic_backup_restore_is_content_addressed_but_not_production_evidence(self) -> None:
        policy = RuntimePersistencePolicy.from_file(POLICY_PATH)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = FilesystemProofStore(root)
            result = RecoveryController(policy, store, clock=lambda: NOW).prove_synthetic_round_trip(
                "postgresql_runtime_state",
                b"synthetic pg_dump bytes",
            )
            self.assertTrue(result["valid"])
            self.assertTrue(result["synthetic_control_proof"])
            self.assertFalse(result["production_restore_observed"])
            self.assertFalse(result["production_ready"])
            manifest_reference = ObjectReference.from_dict(result["manifest_object"])
            manifest_path = root / manifest_reference.key
            manifest_path.write_bytes(b"tampered manifest bytes")
            with self.assertRaises(PersistenceControlError):
                store.get_verified(manifest_reference)

    def test_object_and_recovery_records_reject_unknown_fields_and_type_coercion(self) -> None:
        policy = RuntimePersistencePolicy.from_file(POLICY_PATH)
        with tempfile.TemporaryDirectory() as directory:
            store = FilesystemProofStore(directory)
            controller = RecoveryController(policy, store, clock=lambda: NOW)
            manifest, _ = controller.create_backup(
                component="postgresql_runtime_state",
                payload=b"synthetic pg_dump bytes",
                source_terminal_sha256="f" * 64,
                synthetic_control_proof=True,
            )
            reference = manifest.backup_object.to_dict()
            reference["unapproved"] = True
            with self.assertRaises(PersistenceControlError):
                ObjectReference.from_dict(reference)
            raw_manifest = manifest.to_dict()
            raw_manifest["synthetic_control_proof"] = "true"
            with self.assertRaises(PersistenceControlError):
                RecoveryManifest.from_dict(raw_manifest)

    def test_postgres_migrations_are_ordered_atomic_immutable_and_idempotent(self) -> None:
        catalog = PostgresMigrationCatalog.from_directory(MIGRATIONS)
        status = catalog.validate()
        self.assertEqual(status["latest_version"], 2)
        self.assertEqual(status["tables"], 10)
        connection = FakeConnection()
        first = catalog.apply(connection)
        self.assertEqual(first["applied_now"], [1, 2])
        self.assertEqual(connection.commits, 1)
        second = catalog.apply(connection)
        self.assertEqual(second["applied_now"], [])
        self.assertEqual(connection.commits, 2)
        sql = "\n".join(item.sql for item in catalog.migrations)
        self.assertIn("BEFORE UPDATE OR DELETE", sql)
        self.assertNotIn("COMMIT;", sql)
        init = validate_postgres_init_contract(
            ROOT / "deploy" / "postgres-init" / "0000_apply_approved_migrations.sql",
            catalog,
        )
        self.assertTrue(init["atomic"])

    def test_applied_migration_digest_mismatch_fails_closed(self) -> None:
        catalog = PostgresMigrationCatalog.from_directory(MIGRATIONS)
        connection = FakeConnection()
        connection.applied.append((1, catalog.migrations[0].filename, "0" * 64))
        with self.assertRaises(PersistenceControlError):
            catalog.apply(connection)
        self.assertEqual(connection.rollbacks, 1)

    def test_migration_catalog_rejects_changed_or_missing_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for path in MIGRATIONS.glob("*.sql"):
                (root / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            second = root / "0002_recovery_catalog.sql"
            second.write_text(
                second.read_text(encoding="utf-8").replace("athena.recovery_catalog", "athena.removed"),
                encoding="utf-8",
            )
            with self.assertRaises(PersistenceControlError):
                PostgresMigrationCatalog.from_directory(root)

    def test_compose_is_internal_requires_secrets_and_has_no_default_credentials(self) -> None:
        status = validate_compose_contract(COMPOSE)
        self.assertEqual(status["host_ports"], 0)
        self.assertFalse(status["production_ready"])
        compose = COMPOSE.read_text(encoding="utf-8")
        self.assertIn("http://localhost:9000/minio/health/live", compose)
        self.assertNotIn('mc", "ready", "local', compose)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "compose.yml"
            path.write_text(compose + "\nports:\n  - 5432:5432\n", encoding="utf-8")
            with self.assertRaises(PersistenceControlError):
                validate_compose_contract(path)

    def test_external_runtime_endpoints_fail_without_transport_encryption(self) -> None:
        with self.assertRaises(PersistenceControlError):
            build_s3_store_from_environment({
                "ATHENA_S3_ENDPOINT_URL": "http://objects.example.invalid",
                "ATHENA_OBJECT_BUCKET": "athena-private",
                "ATHENA_S3_ACCESS_KEY_ID": "fixture",
                "ATHENA_S3_SECRET_ACCESS_KEY": "fixture",
            })
        with self.assertRaises(PersistenceControlError):
            connect_postgres_from_environment({
                "ATHENA_POSTGRES_DSN": "postgresql://db.example.invalid/athena"
            })
        with self.assertRaises(PersistenceControlError):
            build_redis_queue_from_environment(
                RuntimePersistencePolicy.from_file(POLICY_PATH),
                {"ATHENA_REDIS_URL": "redis://cache.example.invalid:6379/0"},
            )

    def test_runtime_adapter_versions_are_pinned(self) -> None:
        project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"boto3==1.43.72"', project)
        self.assertIn('"psycopg[binary]==3.3.4"', project)
        self.assertIn('"redis==8.1.0"', project)


if __name__ == "__main__":
    unittest.main()
