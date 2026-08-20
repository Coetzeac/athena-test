from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urlparse

from athena.evidence import canonical_json, sha256_bytes, sha256_text
from athena.models import utc_now


RESOLUTION_ID = "ATHENA-RPR-001"
EXPECTED_EVIDENCE_IDS = ("EF-002", "EF-006", "EF-015")
EXPECTED_REQUIREMENTS = ("FR-011", "FR-015", "FR-017")
POSTGRES_COLLECTIONS = (
    "audit_ledger",
    "evidence_register",
    "historical_manifests",
    "historical_checkpoints",
    "quota_reservations",
    "quarantine_index",
    "object_catalog",
    "runtime_status",
    "recovery_catalog",
)
OBJECT_KINDS = (
    "market_raw",
    "market_normalized",
    "research_source",
    "chart",
    "manifest",
    "report",
    "backup",
    "recovery_manifest",
)
REQUIRED_ENVIRONMENT_VARIABLES = (
    "ATHENA_POSTGRES_DSN",
    "ATHENA_REDIS_URL",
    "ATHENA_S3_ENDPOINT_URL",
    "ATHENA_OBJECT_BUCKET",
    "ATHENA_S3_ACCESS_KEY_ID",
    "ATHENA_S3_SECRET_ACCESS_KEY",
)
REQUIRED_POSTGRES_TABLES = (
    "schema_migrations",
    "audit_ledger_entries",
    "evidence_records",
    "historical_manifests",
    "historical_checkpoints",
    "quota_reservations",
    "quarantine_index",
    "object_catalog",
    "runtime_status",
    "recovery_catalog",
)
EXPECTED_MIGRATION_DIGESTS = (
    ("0001_runtime_state.sql", "7778a7bc9d2c50f59369e0aee751548857b562bb9575cc6a0ebac40ac6b13839"),
    ("0002_recovery_catalog.sql", "26c8cf911a0098576b0dc5ef31803de307802259f1202adf87b1e475ddb5122a"),
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MIGRATION_PATTERN = re.compile(r"^(?P<version>[0-9]{4})_[a-z0-9_]+\.sql$")
QUEUE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,47}$")
PROHIBITED_REDIS_KEYS = {
    "api_key",
    "credential",
    "credentials",
    "ledger",
    "password",
    "private_key",
    "raw_bytes",
    "secret",
    "source_bytes",
    "token",
}


class PersistenceControlError(RuntimeError):
    """Raised when a runtime-persistence control fails closed."""


class ObjectStoreClient(Protocol):
    def head_object(self, **kwargs: Any) -> dict[str, Any]: ...
    def put_object(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_object(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class RuntimePersistencePolicy:
    raw: dict[str, Any]

    @classmethod
    def from_file(cls, path: str | Path) -> RuntimePersistencePolicy:
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise PersistenceControlError(f"cannot load runtime persistence policy: {error}") from error
        policy = cls(raw=raw)
        policy.validate()
        return policy

    @property
    def rpo_minutes(self) -> int:
        return int(self.raw["recovery"]["recovery_point_objective_minutes"])

    @property
    def rto_minutes(self) -> int:
        return int(self.raw["recovery"]["recovery_time_objective_minutes"])

    @property
    def max_redis_ttl(self) -> int:
        return int(self.raw["stores"]["redis"]["maximum_ttl_seconds"])

    @property
    def max_redis_payload_bytes(self) -> int:
        return int(self.raw["stores"]["redis"]["maximum_payload_bytes"])

    def validate(self) -> dict[str, Any]:
        failures: list[str] = []
        raw = self.raw
        exact_keys = {
            "schema_version",
            "resolution_id",
            "status",
            "approved_at",
            "authority",
            "evidence_ids",
            "requirements",
            "stores",
            "recovery",
            "secrets",
            "production_ready",
            "decision_court_bypass",
            "live_execution",
        }
        if set(raw) != exact_keys:
            failures.append("runtime persistence policy must remain a closed contract")
        if raw.get("schema_version") != 1:
            failures.append("schema_version must be 1")
        if raw.get("resolution_id") != RESOLUTION_ID:
            failures.append(f"resolution_id must be {RESOLUTION_ID}")
        if raw.get("status") != "approved_implementation_control":
            failures.append("status must remain approved_implementation_control")
        if raw.get("approved_at") != "2026-08-14":
            failures.append("approved_at must remain 2026-08-14")
        if raw.get("authority") != "Owner/CIO":
            failures.append("authority must remain Owner/CIO")
        if tuple(raw.get("evidence_ids", [])) != EXPECTED_EVIDENCE_IDS:
            failures.append(f"evidence_ids must remain {list(EXPECTED_EVIDENCE_IDS)}")
        if tuple(raw.get("requirements", [])) != EXPECTED_REQUIREMENTS:
            failures.append(f"requirements must remain {list(EXPECTED_REQUIREMENTS)}")

        stores = raw.get("stores", {})
        if set(stores) != {"postgresql", "object_store", "redis"}:
            failures.append("stores must contain exactly PostgreSQL, object_store, and Redis")
        postgres = stores.get("postgresql", {})
        if set(postgres) != {
            "authority", "schema", "minimum_major_version", "tls_at_external_boundary",
            "migration_sha256", "canonical_collections",
        }:
            failures.append("PostgreSQL policy contains missing or unknown fields")
        if postgres.get("authority") != "canonical_operational_state":
            failures.append("PostgreSQL authority must remain canonical_operational_state")
        if postgres.get("schema") != "athena" or postgres.get("minimum_major_version", 0) < 16:
            failures.append("PostgreSQL must use athena schema on version 16 or later")
        if postgres.get("tls_at_external_boundary") is not True:
            failures.append("PostgreSQL external boundaries must require TLS")
        migration_digests = tuple(
            (str(item.get("filename", "")), str(item.get("sha256", "")))
            for item in postgres.get("migration_sha256", [])
            if isinstance(item, dict)
        )
        if migration_digests != EXPECTED_MIGRATION_DIGESTS:
            failures.append("PostgreSQL migration digests differ from ATHENA-RPR-001")
        if tuple(postgres.get("canonical_collections", [])) != POSTGRES_COLLECTIONS:
            failures.append("PostgreSQL canonical collections differ from the approved mapping")

        objects = stores.get("object_store", {})
        if set(objects) != {
            "authority", "interface", "bucket_environment_variable",
            "endpoint_environment_variable", "content_addressed", "versioning_required",
            "public_access", "server_side_encryption", "tls_at_external_boundary",
            "canonical_kinds",
        }:
            failures.append("object-store policy contains missing or unknown fields")
        expected_object_controls = {
            "authority": "immutable_runtime_bytes",
            "interface": "S3-compatible",
            "bucket_environment_variable": "ATHENA_OBJECT_BUCKET",
            "endpoint_environment_variable": "ATHENA_S3_ENDPOINT_URL",
            "content_addressed": True,
            "versioning_required": True,
            "public_access": False,
            "server_side_encryption": "AES256",
            "tls_at_external_boundary": True,
        }
        for field, expected in expected_object_controls.items():
            if objects.get(field) != expected:
                failures.append(f"object_store.{field} must remain {expected!r}")
        if tuple(objects.get("canonical_kinds", [])) != OBJECT_KINDS:
            failures.append("object-store canonical kinds differ from the approved mapping")

        redis = stores.get("redis", {})
        if set(redis) != {
            "authority", "canonical_record_permitted", "maximum_ttl_seconds",
            "maximum_payload_bytes", "tls_at_external_boundary",
        }:
            failures.append("Redis policy contains missing or unknown fields")
        if redis.get("authority") != "transient_coordination_only":
            failures.append("Redis authority must remain transient_coordination_only")
        if redis.get("canonical_record_permitted") is not False:
            failures.append("Redis cannot hold canonical records")
        if redis.get("maximum_ttl_seconds") != 86400:
            failures.append("Redis maximum TTL must remain 86400 seconds")
        if redis.get("maximum_payload_bytes") != 65536:
            failures.append("Redis maximum payload must remain 65536 bytes")
        if redis.get("tls_at_external_boundary") is not True:
            failures.append("Redis external boundaries must require TLS")

        recovery = raw.get("recovery", {})
        if set(recovery) != {
            "recovery_point_objective_minutes", "recovery_time_objective_minutes",
            "offsite_separate_failure_domain_required", "encrypted_backup_required",
            "backup_retention_days", "restore_test_cadence_days",
            "production_acceptance_requires_observed_restore",
        }:
            failures.append("recovery policy contains missing or unknown fields")
        expected_recovery = {
            "recovery_point_objective_minutes": 60,
            "recovery_time_objective_minutes": 240,
            "offsite_separate_failure_domain_required": True,
            "encrypted_backup_required": True,
            "backup_retention_days": 35,
            "restore_test_cadence_days": 30,
            "production_acceptance_requires_observed_restore": True,
        }
        for field, expected in expected_recovery.items():
            if recovery.get(field) != expected:
                failures.append(f"recovery.{field} must remain {expected!r}")

        secrets = raw.get("secrets", {})
        if set(secrets) != {
            "permitted_source", "repository_values_prohibited", "required_environment_variables",
        }:
            failures.append("secrets policy contains missing or unknown fields")
        if secrets.get("permitted_source") != "runtime_environment_or_secret_store":
            failures.append("secrets must come only from runtime environment or a secret store")
        if secrets.get("repository_values_prohibited") is not True:
            failures.append("repository secret values must remain prohibited")
        if tuple(secrets.get("required_environment_variables", [])) != REQUIRED_ENVIRONMENT_VARIABLES:
            failures.append("required secret environment variables differ from the approved contract")
        if raw.get("production_ready") is not False:
            failures.append("policy cannot claim production readiness before observed restore evidence")
        if raw.get("decision_court_bypass") != "prohibited":
            failures.append("Decision Court bypass must remain prohibited")
        if raw.get("live_execution") != "prohibited":
            failures.append("live execution must remain prohibited")
        if failures:
            raise PersistenceControlError("; ".join(failures))
        return {
            "valid": True,
            "resolution_id": RESOLUTION_ID,
            "postgres_collections": len(POSTGRES_COLLECTIONS),
            "object_kinds": len(OBJECT_KINDS),
            "redis_maximum_ttl_seconds": self.max_redis_ttl,
            "rpo_minutes": self.rpo_minutes,
            "rto_minutes": self.rto_minutes,
            "production_ready": False,
            "live_execution": "prohibited",
        }


def canonical_object_key(kind: str, content_sha256: str) -> str:
    if kind not in OBJECT_KINDS:
        raise PersistenceControlError(f"unapproved object kind: {kind}")
    if not SHA256_PATTERN.fullmatch(content_sha256):
        raise PersistenceControlError("content_sha256 must be 64 lowercase hexadecimal characters")
    return f"objects/{kind}/sha256/{content_sha256[:2]}/{content_sha256}"


@dataclass(frozen=True)
class ObjectReference:
    kind: str
    bucket: str
    key: str
    version_id: str
    content_sha256: str
    content_length: int
    content_type: str
    server_side_encryption: str = "AES256"

    def validate(self) -> None:
        failures: list[str] = []
        if self.kind not in OBJECT_KINDS:
            failures.append("kind is not approved")
        if len(self.bucket.strip()) < 3:
            failures.append("bucket is required")
        if self.key != canonical_object_key(self.kind, self.content_sha256):
            failures.append("key is not the canonical content-addressed key")
        if not self.version_id.strip():
            failures.append("version_id is required because object versioning is mandatory")
        if self.content_length < 1:
            failures.append("content_length must be positive")
        if not self.content_type.strip():
            failures.append("content_type is required")
        if self.server_side_encryption != "AES256":
            failures.append("server-side encryption must be AES256")
        if failures:
            raise PersistenceControlError("; ".join(failures))

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": 1,
            "resolution_id": RESOLUTION_ID,
            "kind": self.kind,
            "bucket": self.bucket,
            "key": self.key,
            "version_id": self.version_id,
            "content_sha256": self.content_sha256,
            "content_length": self.content_length,
            "content_type": self.content_type,
            "server_side_encryption": self.server_side_encryption,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ObjectReference:
        expected_keys = {
            "schema_version", "resolution_id", "kind", "bucket", "key", "version_id",
            "content_sha256", "content_length", "content_type", "server_side_encryption",
        }
        if set(value) != expected_keys:
            raise PersistenceControlError("object reference must remain a closed contract")
        if value.get("schema_version") != 1 or value.get("resolution_id") != RESOLUTION_ID:
            raise PersistenceControlError("object reference contract does not match ATHENA-RPR-001")
        if not isinstance(value.get("content_length"), int) or isinstance(
            value.get("content_length"), bool
        ):
            raise PersistenceControlError("object reference content_length must be an integer")
        reference = cls(
            kind=str(value.get("kind", "")),
            bucket=str(value.get("bucket", "")),
            key=str(value.get("key", "")),
            version_id=str(value.get("version_id", "")),
            content_sha256=str(value.get("content_sha256", "")),
            content_length=int(value.get("content_length", 0)),
            content_type=str(value.get("content_type", "")),
            server_side_encryption=str(value.get("server_side_encryption", "")),
        )
        reference.validate()
        return reference


def _response_body_bytes(response: dict[str, Any]) -> bytes:
    body = response.get("Body", b"")
    if hasattr(body, "read"):
        body = body.read()
    if not isinstance(body, (bytes, bytearray)):
        raise PersistenceControlError("object-store response body is not bytes")
    return bytes(body)


def _is_missing_object(error: Exception) -> bool:
    if isinstance(error, (FileNotFoundError, KeyError)):
        return True
    response = getattr(error, "response", {})
    code = str(response.get("Error", {}).get("Code", "")) if isinstance(response, dict) else ""
    return code in {"404", "NoSuchKey", "NotFound"}


class S3CompatibleObjectStore:
    def __init__(self, client: ObjectStoreClient, bucket: str) -> None:
        if len(bucket.strip()) < 3:
            raise PersistenceControlError("object bucket is required")
        self.client = client
        self.bucket = bucket

    def put_immutable(self, kind: str, payload: bytes, content_type: str) -> ObjectReference:
        if not payload:
            raise PersistenceControlError("immutable object payload cannot be empty")
        digest = sha256_bytes(payload)
        key = canonical_object_key(kind, digest)
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception as error:
            if not _is_missing_object(error):
                raise PersistenceControlError(f"object head failed: {type(error).__name__}") from error
        else:
            reference = self._reference_from_head(kind, digest, key, content_type, head)
            if self.get_verified(reference) != payload:
                raise PersistenceControlError("existing immutable object bytes differ from their digest")
            return reference

        response = self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=payload,
            ContentLength=len(payload),
            ContentType=content_type,
            Metadata={"sha256": digest, "athena-resolution": RESOLUTION_ID},
            ServerSideEncryption="AES256",
        )
        version_id = str(response.get("VersionId", ""))
        if not version_id:
            raise PersistenceControlError("object store did not return a version ID; versioning is required")
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=key, VersionId=version_id)
        except Exception as error:
            raise PersistenceControlError(f"stored object could not be verified: {type(error).__name__}") from error
        reference = self._reference_from_head(kind, digest, key, content_type, head, version_id)
        if self.get_verified(reference) != payload:
            raise PersistenceControlError("stored immutable object failed byte verification")
        return reference

    def _reference_from_head(
        self,
        kind: str,
        digest: str,
        key: str,
        content_type: str,
        head: dict[str, Any],
        version_id: str | None = None,
    ) -> ObjectReference:
        metadata = {str(k).lower(): str(v) for k, v in head.get("Metadata", {}).items()}
        observed_digest = metadata.get("sha256", "")
        if observed_digest != digest:
            raise PersistenceControlError("object metadata digest mismatch")
        length = int(head.get("ContentLength", 0))
        encryption = str(head.get("ServerSideEncryption", ""))
        reference = ObjectReference(
            kind=kind,
            bucket=self.bucket,
            key=key,
            version_id=version_id or str(head.get("VersionId", "")),
            content_sha256=digest,
            content_length=length,
            content_type=str(head.get("ContentType", content_type)),
            server_side_encryption=encryption,
        )
        reference.validate()
        return reference

    def get_verified(self, reference: ObjectReference) -> bytes:
        reference.validate()
        if reference.bucket != self.bucket:
            raise PersistenceControlError("object reference bucket differs from configured bucket")
        try:
            response = self.client.get_object(
                Bucket=self.bucket,
                Key=reference.key,
                VersionId=reference.version_id,
            )
        except Exception as error:
            raise PersistenceControlError(f"object retrieval failed: {type(error).__name__}") from error
        payload = _response_body_bytes(response)
        if len(payload) != reference.content_length:
            raise PersistenceControlError("retrieved object length mismatch")
        if sha256_bytes(payload) != reference.content_sha256:
            raise PersistenceControlError("retrieved object digest mismatch")
        return payload


class FilesystemProofStore:
    """Synthetic recovery proof only; never a production persistence backend."""

    def __init__(self, root: str | Path, bucket: str = "synthetic-proof") -> None:
        self.root = Path(root)
        self.bucket = bucket

    def put_immutable(self, kind: str, payload: bytes, content_type: str) -> ObjectReference:
        if not payload:
            raise PersistenceControlError("immutable object payload cannot be empty")
        digest = sha256_bytes(payload)
        key = canonical_object_key(kind, digest)
        path = self.root / key
        metadata_path = path.with_suffix(".metadata.json")
        version_id = sha256_text(f"synthetic-proof:{kind}:{digest}")[:32]
        reference = ObjectReference(
            kind=kind,
            bucket=self.bucket,
            key=key,
            version_id=version_id,
            content_sha256=digest,
            content_length=len(payload),
            content_type=content_type,
        )
        reference.validate()
        if path.exists():
            if path.read_bytes() != payload:
                raise PersistenceControlError("content-addressed proof object collision")
            retained = ObjectReference.from_dict(json.loads(metadata_path.read_text(encoding="utf-8")))
            if retained != reference:
                raise PersistenceControlError("proof object metadata mismatch")
            return retained
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        metadata_path.write_text(canonical_json(reference.to_dict()) + "\n", encoding="utf-8")
        return reference

    def get_verified(self, reference: ObjectReference) -> bytes:
        reference.validate()
        if reference.bucket != self.bucket:
            raise PersistenceControlError("proof reference bucket mismatch")
        path = self.root / reference.key
        metadata_path = path.with_suffix(".metadata.json")
        if not path.is_file() or not metadata_path.is_file():
            raise PersistenceControlError("proof object or metadata is missing")
        retained = ObjectReference.from_dict(json.loads(metadata_path.read_text(encoding="utf-8")))
        if retained != reference:
            raise PersistenceControlError("proof object reference was altered")
        payload = path.read_bytes()
        if len(payload) != reference.content_length or sha256_bytes(payload) != reference.content_sha256:
            raise PersistenceControlError("proof object bytes failed verification")
        return payload


@dataclass(frozen=True)
class RecoveryManifest:
    component: str
    created_at: str
    source_terminal_sha256: str
    backup_object: ObjectReference
    rpo_minutes: int
    rto_minutes: int
    evidence_ids: tuple[str, ...]
    synthetic_control_proof: bool
    production_restore_observed: bool
    manifest_sha256: str

    @classmethod
    def create(
        cls,
        *,
        component: str,
        created_at: str,
        source_terminal_sha256: str,
        backup_object: ObjectReference,
        policy: RuntimePersistencePolicy,
        synthetic_control_proof: bool,
        production_restore_observed: bool = False,
    ) -> RecoveryManifest:
        body = {
            "schema_version": 1,
            "resolution_id": RESOLUTION_ID,
            "component": component,
            "created_at": created_at,
            "source_terminal_sha256": source_terminal_sha256,
            "backup_object": backup_object.to_dict(),
            "recovery_point_objective_minutes": policy.rpo_minutes,
            "recovery_time_objective_minutes": policy.rto_minutes,
            "evidence_ids": list(EXPECTED_EVIDENCE_IDS),
            "synthetic_control_proof": synthetic_control_proof,
            "production_restore_observed": production_restore_observed,
        }
        manifest = cls(
            component=component,
            created_at=created_at,
            source_terminal_sha256=source_terminal_sha256,
            backup_object=backup_object,
            rpo_minutes=policy.rpo_minutes,
            rto_minutes=policy.rto_minutes,
            evidence_ids=EXPECTED_EVIDENCE_IDS,
            synthetic_control_proof=synthetic_control_proof,
            production_restore_observed=production_restore_observed,
            manifest_sha256=sha256_text(canonical_json(body)),
        )
        manifest.validate()
        return manifest

    def _body(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "resolution_id": RESOLUTION_ID,
            "component": self.component,
            "created_at": self.created_at,
            "source_terminal_sha256": self.source_terminal_sha256,
            "backup_object": self.backup_object.to_dict(),
            "recovery_point_objective_minutes": self.rpo_minutes,
            "recovery_time_objective_minutes": self.rto_minutes,
            "evidence_ids": list(self.evidence_ids),
            "synthetic_control_proof": self.synthetic_control_proof,
            "production_restore_observed": self.production_restore_observed,
        }

    def validate(self) -> None:
        failures: list[str] = []
        if not self.component.strip():
            failures.append("component is required")
        try:
            parsed = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                failures.append("created_at must include a timezone")
        except ValueError:
            failures.append("created_at must be ISO-8601")
        if not SHA256_PATTERN.fullmatch(self.source_terminal_sha256):
            failures.append("source_terminal_sha256 is invalid")
        self.backup_object.validate()
        if self.backup_object.kind != "backup":
            failures.append("backup_object kind must be backup")
        if self.rpo_minutes != 60 or self.rto_minutes != 240:
            failures.append("recovery objectives differ from ATHENA-RPR-001")
        if self.evidence_ids != EXPECTED_EVIDENCE_IDS:
            failures.append("recovery evidence IDs differ from ATHENA-RPR-001")
        if self.synthetic_control_proof and self.production_restore_observed:
            failures.append("a synthetic proof cannot claim an observed production restore")
        expected = sha256_text(canonical_json(self._body()))
        if self.manifest_sha256 != expected:
            failures.append("recovery manifest digest mismatch")
        if failures:
            raise PersistenceControlError("; ".join(failures))

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {**self._body(), "manifest_sha256": self.manifest_sha256}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RecoveryManifest:
        expected_keys = {
            "schema_version", "resolution_id", "component", "created_at",
            "source_terminal_sha256", "backup_object", "recovery_point_objective_minutes",
            "recovery_time_objective_minutes", "evidence_ids", "synthetic_control_proof",
            "production_restore_observed", "manifest_sha256",
        }
        if set(value) != expected_keys:
            raise PersistenceControlError("recovery manifest must remain a closed contract")
        if value.get("schema_version") != 1 or value.get("resolution_id") != RESOLUTION_ID:
            raise PersistenceControlError("recovery manifest contract does not match ATHENA-RPR-001")
        for field in ("synthetic_control_proof", "production_restore_observed"):
            if not isinstance(value.get(field), bool):
                raise PersistenceControlError(f"recovery manifest {field} must be boolean")
        for field in ("recovery_point_objective_minutes", "recovery_time_objective_minutes"):
            if not isinstance(value.get(field), int) or isinstance(value.get(field), bool):
                raise PersistenceControlError(f"recovery manifest {field} must be integer")
        manifest = cls(
            component=str(value.get("component", "")),
            created_at=str(value.get("created_at", "")),
            source_terminal_sha256=str(value.get("source_terminal_sha256", "")),
            backup_object=ObjectReference.from_dict(dict(value.get("backup_object", {}))),
            rpo_minutes=int(value.get("recovery_point_objective_minutes", 0)),
            rto_minutes=int(value.get("recovery_time_objective_minutes", 0)),
            evidence_ids=tuple(str(item) for item in value.get("evidence_ids", [])),
            synthetic_control_proof=value["synthetic_control_proof"],
            production_restore_observed=value["production_restore_observed"],
            manifest_sha256=str(value.get("manifest_sha256", "")),
        )
        manifest.validate()
        return manifest


class RecoveryController:
    def __init__(
        self,
        policy: RuntimePersistencePolicy,
        store: Any,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self.policy = policy
        self.store = store
        self.clock = clock

    def create_backup(
        self,
        *,
        component: str,
        payload: bytes,
        source_terminal_sha256: str,
        synthetic_control_proof: bool,
    ) -> tuple[RecoveryManifest, ObjectReference]:
        backup = self.store.put_immutable("backup", payload, "application/octet-stream")
        manifest = RecoveryManifest.create(
            component=component,
            created_at=self.clock(),
            source_terminal_sha256=source_terminal_sha256,
            backup_object=backup,
            policy=self.policy,
            synthetic_control_proof=synthetic_control_proof,
        )
        manifest_bytes = (canonical_json(manifest.to_dict()) + "\n").encode("utf-8")
        manifest_object = self.store.put_immutable(
            "recovery_manifest",
            manifest_bytes,
            "application/json",
        )
        return manifest, manifest_object

    def restore_verified(self, manifest: RecoveryManifest) -> bytes:
        manifest.validate()
        payload = self.store.get_verified(manifest.backup_object)
        if sha256_bytes(payload) != manifest.backup_object.content_sha256:
            raise PersistenceControlError("restored backup digest mismatch")
        return payload

    def prove_synthetic_round_trip(self, component: str, payload: bytes) -> dict[str, Any]:
        source_terminal_sha256 = sha256_bytes(payload)
        manifest, manifest_object = self.create_backup(
            component=component,
            payload=payload,
            source_terminal_sha256=source_terminal_sha256,
            synthetic_control_proof=True,
        )
        restored = self.restore_verified(manifest)
        return {
            "valid": restored == payload,
            "resolution_id": RESOLUTION_ID,
            "component": component,
            "backup_sha256": sha256_bytes(restored),
            "manifest_sha256": manifest.manifest_sha256,
            "manifest_object": manifest_object.to_dict(),
            "synthetic_control_proof": True,
            "production_restore_observed": False,
            "production_ready": False,
            "live_execution": "prohibited",
        }


@dataclass(frozen=True)
class PostgresMigration:
    version: int
    filename: str
    content_sha256: str
    sql: str


class PostgresMigrationCatalog:
    def __init__(self, migrations: tuple[PostgresMigration, ...]) -> None:
        self.migrations = migrations
        self.validate()

    @classmethod
    def from_directory(cls, root: str | Path) -> PostgresMigrationCatalog:
        directory = Path(root)
        migrations: list[PostgresMigration] = []
        for path in sorted(directory.glob("*.sql")):
            match = MIGRATION_PATTERN.fullmatch(path.name)
            if not match:
                raise PersistenceControlError(f"invalid PostgreSQL migration filename: {path.name}")
            sql = path.read_text(encoding="utf-8")
            migrations.append(PostgresMigration(
                version=int(match.group("version")),
                filename=path.name,
                content_sha256=sha256_text(sql),
                sql=sql,
            ))
        return cls(tuple(migrations))

    def validate(self) -> dict[str, Any]:
        failures: list[str] = []
        versions = [item.version for item in self.migrations]
        if versions != list(range(1, len(versions) + 1)):
            failures.append("PostgreSQL migrations must be contiguous from version 1")
        if not self.migrations:
            failures.append("at least one PostgreSQL migration is required")
        combined = "\n".join(item.sql for item in self.migrations)
        for migration in self.migrations:
            upper = migration.sql.upper()
            if "BEGIN;" in upper or "COMMIT;" in upper or "ROLLBACK;" in upper:
                failures.append(
                    f"{migration.filename} cannot control transactions; the migration runner is atomic"
                )
        for table in REQUIRED_POSTGRES_TABLES:
            if f"athena.{table}" not in combined:
                failures.append(f"missing PostgreSQL table contract: athena.{table}")
        if "reject_immutable_mutation" not in combined:
            failures.append("PostgreSQL immutable mutation trigger is missing")
        if "BEFORE UPDATE OR DELETE" not in combined:
            failures.append("PostgreSQL mutation rejection is not installed")
        if failures:
            raise PersistenceControlError("; ".join(failures))
        return {
            "valid": True,
            "migrations": len(self.migrations),
            "latest_version": self.migrations[-1].version,
            "tables": len(REQUIRED_POSTGRES_TABLES),
        }

    def apply(self, connection: Any) -> dict[str, Any]:
        self.validate()
        bootstrap = """
        CREATE SCHEMA IF NOT EXISTS athena;
        CREATE TABLE IF NOT EXISTS athena.schema_migrations (
            version integer PRIMARY KEY CHECK (version > 0),
            filename text NOT NULL UNIQUE CHECK (length(filename) > 0),
            content_sha256 char(64) NOT NULL UNIQUE CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
            applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        """
        try:
            connection.execute("SELECT pg_advisory_xact_lock(42843611)")
            connection.execute(bootstrap)
            rows = connection.execute(
                "SELECT version, filename, content_sha256 FROM athena.schema_migrations ORDER BY version"
            ).fetchall()
            applied = {int(row[0]): (str(row[1]), str(row[2])) for row in rows}
            applied_now: list[int] = []
            for migration in self.migrations:
                retained = applied.get(migration.version)
                expected = (migration.filename, migration.content_sha256)
                if retained is not None:
                    if retained != expected:
                        raise PersistenceControlError(
                            f"applied migration {migration.version} differs from repository bytes"
                        )
                    continue
                connection.execute(migration.sql)
                connection.execute(
                    "INSERT INTO athena.schema_migrations(version, filename, content_sha256) VALUES (%s, %s, %s)",
                    (migration.version, migration.filename, migration.content_sha256),
                )
                applied_now.append(migration.version)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        return {
            "valid": True,
            "latest_version": self.migrations[-1].version,
            "applied_now": applied_now,
        }


def _collect_mapping_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            keys.add(str(key).lower())
            keys.update(_collect_mapping_keys(nested))
    elif isinstance(value, list):
        for nested in value:
            keys.update(_collect_mapping_keys(nested))
    return keys


class RedisTransientQueue:
    def __init__(
        self,
        client: Any,
        policy: RuntimePersistencePolicy,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self.client = client
        self.policy = policy
        self.clock = clock

    def enqueue(self, queue: str, payload: dict[str, Any], ttl_seconds: int) -> str:
        if not QUEUE_PATTERN.fullmatch(queue):
            raise PersistenceControlError("Redis queue name is invalid")
        if ttl_seconds < 1 or ttl_seconds > self.policy.max_redis_ttl:
            raise PersistenceControlError("Redis TTL exceeds the approved transient boundary")
        prohibited = sorted(_collect_mapping_keys(payload) & PROHIBITED_REDIS_KEYS)
        if prohibited:
            raise PersistenceControlError(
                f"Redis payload contains prohibited canonical or secret fields: {prohibited}"
            )
        payload_json = canonical_json(payload)
        payload_bytes = payload_json.encode("utf-8")
        if len(payload_bytes) > self.policy.max_redis_payload_bytes:
            raise PersistenceControlError("Redis payload exceeds the approved byte limit")
        digest = sha256_bytes(payload_bytes)
        key = f"athena:transient:v1:{queue}:{digest}"
        wrapper = canonical_json({
            "schema_version": 1,
            "resolution_id": RESOLUTION_ID,
            "enqueued_at": self.clock(),
            "expires_in_seconds": ttl_seconds,
            "payload_sha256": digest,
            "payload": payload,
        })
        stored = self.client.set(key, wrapper, ex=ttl_seconds, nx=True)
        if not stored:
            raise PersistenceControlError("Redis transient item already exists or was not stored")
        return key


def _required_environment(environ: dict[str, str], names: tuple[str, ...]) -> None:
    missing = [name for name in names if not environ.get(name, "").strip()]
    if missing:
        raise PersistenceControlError(f"required runtime environment variables are absent: {missing}")


def _endpoint_is_private_development(url: str, service_names: set[str]) -> bool:
    parsed = urlparse(url)
    return parsed.hostname in service_names and parsed.scheme in {"http", "redis", "postgresql"}


def build_s3_store_from_environment(
    environ: dict[str, str] | None = None,
) -> S3CompatibleObjectStore:
    runtime = dict(os.environ if environ is None else environ)
    names = (
        "ATHENA_S3_ENDPOINT_URL",
        "ATHENA_OBJECT_BUCKET",
        "ATHENA_S3_ACCESS_KEY_ID",
        "ATHENA_S3_SECRET_ACCESS_KEY",
    )
    _required_environment(runtime, names)
    endpoint = runtime["ATHENA_S3_ENDPOINT_URL"]
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" and not _endpoint_is_private_development(
        endpoint, {"localhost", "127.0.0.1", "minio"}
    ):
        raise PersistenceControlError("S3 endpoint must use HTTPS outside private development")
    try:
        import boto3
    except ImportError as error:
        raise PersistenceControlError(
            "S3 runtime dependency is absent; install the pinned persistence extra"
        ) from error
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=runtime["ATHENA_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=runtime["ATHENA_S3_SECRET_ACCESS_KEY"],
        region_name=runtime.get("ATHENA_S3_REGION", "us-east-1"),
        use_ssl=parsed.scheme == "https",
    )
    return S3CompatibleObjectStore(client, runtime["ATHENA_OBJECT_BUCKET"])


def connect_postgres_from_environment(environ: dict[str, str] | None = None) -> Any:
    runtime = dict(os.environ if environ is None else environ)
    _required_environment(runtime, ("ATHENA_POSTGRES_DSN",))
    dsn = runtime["ATHENA_POSTGRES_DSN"]
    parsed = urlparse(dsn)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise PersistenceControlError("ATHENA_POSTGRES_DSN must be a PostgreSQL URL")
    local = parsed.hostname in {"localhost", "127.0.0.1", "postgres"}
    if not local and "sslmode=require" not in dsn and "sslmode=verify-full" not in dsn:
        raise PersistenceControlError("external PostgreSQL DSN must require TLS")
    try:
        import psycopg
    except ImportError as error:
        raise PersistenceControlError(
            "PostgreSQL runtime dependency is absent; install the pinned persistence extra"
        ) from error
    return psycopg.connect(dsn, autocommit=False)


def build_redis_queue_from_environment(
    policy: RuntimePersistencePolicy,
    environ: dict[str, str] | None = None,
    clock: Callable[[], str] = utc_now,
) -> RedisTransientQueue:
    runtime = dict(os.environ if environ is None else environ)
    _required_environment(runtime, ("ATHENA_REDIS_URL",))
    url = runtime["ATHENA_REDIS_URL"]
    parsed = urlparse(url)
    local = parsed.hostname in {"localhost", "127.0.0.1", "redis"}
    if parsed.scheme != "rediss" and not local:
        raise PersistenceControlError("external Redis URL must use TLS through rediss://")
    try:
        import redis
    except ImportError as error:
        raise PersistenceControlError(
            "Redis runtime dependency is absent; install the pinned persistence extra"
        ) from error
    client = redis.Redis.from_url(url, decode_responses=True)
    return RedisTransientQueue(client, policy, clock=clock)


def validate_compose_contract(path: str | Path) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    failures: list[str] = []
    required_tokens = (
        "postgres:16.15-alpine3.24",
        "redis:7.4.10-alpine",
        "minio/minio:RELEASE.2025-09-07T16-13-09Z",
        "minio/mc:RELEASE.2025-08-13T08-35-41Z",
        "ATHENA_POSTGRES_PASSWORD:?required",
        "ATHENA_REDIS_PASSWORD:?required",
        "ATHENA_MINIO_ROOT_PASSWORD:?required",
        "ATHENA_OBJECT_BUCKET:?required",
        "../migrations/postgres:/athena-migrations:ro",
        "./postgres-init/0000_apply_approved_migrations.sql:/docker-entrypoint-initdb.d/0000_apply_approved_migrations.sql:ro",
        "internal: true",
        "postgres-data:",
        "minio-data:",
        "mc version enable",
        "mc anonymous set none",
    )
    for token in required_tokens:
        if token not in text:
            failures.append(f"compose contract is missing {token}")
    if re.search(r"^\s*ports\s*:", text, flags=re.MULTILINE):
        failures.append("development persistence services must not expose host ports")
    lowered = text.lower()
    for forbidden in ("minioadmin", "changeme", "password123"):
        if forbidden in lowered:
            failures.append(f"compose contract contains forbidden default credential: {forbidden}")
    if "Development and contract testing only" not in text:
        failures.append("compose contract must state its non-production boundary")
    if failures:
        raise PersistenceControlError("; ".join(failures))
    return {"valid": True, "services": 4, "host_ports": 0, "production_ready": False}


def validate_postgres_init_contract(
    path: str | Path,
    migrations: PostgresMigrationCatalog,
) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    failures: list[str] = []
    if text.count("BEGIN;") != 1 or text.count("COMMIT;") != 1:
        failures.append("PostgreSQL development initialization must be one atomic transaction")
    if "\\set ON_ERROR_STOP on" not in text:
        failures.append("PostgreSQL development initialization must fail on the first SQL error")
    for migration in migrations.migrations:
        if f"\\ir /athena-migrations/{migration.filename}" not in text:
            failures.append(f"PostgreSQL initialization does not include {migration.filename}")
        if migration.content_sha256 not in text:
            failures.append(f"PostgreSQL initialization does not record {migration.filename} digest")
    if failures:
        raise PersistenceControlError("; ".join(failures))
    return {"valid": True, "recorded_migrations": len(migrations.migrations), "atomic": True}


def validate_runtime_persistence_policy(
    policy_path: str | Path,
    repository_root: str | Path = ".",
) -> dict[str, Any]:
    root = Path(repository_root)
    policy = RuntimePersistencePolicy.from_file(policy_path)
    policy_status = policy.validate()
    schema_paths = (
        root / "schemas" / "runtime-persistence-policy.schema.json",
        root / "schemas" / "storage-object-reference.schema.json",
        root / "schemas" / "recovery-manifest.schema.json",
    )
    for path in schema_paths:
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise PersistenceControlError(f"cannot load persistence schema {path}: {error}") from error
        if schema.get("additionalProperties") is not False:
            raise PersistenceControlError(f"persistence schema is not closed: {path}")
    migrations = PostgresMigrationCatalog.from_directory(root / "migrations" / "postgres")
    migration_status = migrations.validate()
    observed_migrations = tuple(
        (item.filename, item.content_sha256) for item in migrations.migrations
    )
    if observed_migrations != EXPECTED_MIGRATION_DIGESTS:
        raise PersistenceControlError("repository migration bytes differ from approved policy digests")
    init_status = validate_postgres_init_contract(
        root / "deploy" / "postgres-init" / "0000_apply_approved_migrations.sql",
        migrations,
    )
    compose_status = validate_compose_contract(root / "deploy" / "docker-compose.persistence.yml")
    return {
        **policy_status,
        "configuration_sha256": sha256_text(canonical_json(policy.raw)),
        "schemas": len(schema_paths),
        "migrations": migration_status["migrations"],
        "postgres_tables": migration_status["tables"],
        "development_init_migrations": init_status["recorded_migrations"],
        "development_services": compose_status["services"],
    }
