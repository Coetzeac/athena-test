from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from athena.evidence import EvidenceLedger, canonical_json, sha256_text


SCHEMA_VERSION = 1
RECORD_ID_PATTERN = re.compile(r"^ATH-[A-Z]{3}-[0-9A-F]{24}$")


class RecordType(StrEnum):
    AUTHOR = "author"
    PAPER = "paper"
    RESEARCH_CARD = "research_card"
    HYPOTHESIS = "hypothesis"
    FORMULA = "formula"
    DATASET = "dataset"
    EXPERIMENT = "experiment"
    FACTOR = "factor"
    INDICATOR = "indicator"
    STRATEGY = "strategy"
    VALIDATION_RESULT = "validation_result"


RECORD_SPECS: dict[RecordType, dict[str, Any]] = {
    RecordType.AUTHOR: {"prefix": "AUT", "identity_fields": ("author_key",)},
    RecordType.PAPER: {"prefix": "PAP", "identity_fields": ("canonical_locator",)},
    RecordType.RESEARCH_CARD: {"prefix": "RSC", "identity_fields": ("source_record_ids", "version")},
    RecordType.HYPOTHESIS: {"prefix": "HYP", "identity_fields": ("hypothesis_key", "version")},
    RecordType.FORMULA: {"prefix": "FRM", "identity_fields": ("formula_key", "version")},
    RecordType.DATASET: {"prefix": "DAT", "identity_fields": ("fingerprint_sha256",)},
    RecordType.EXPERIMENT: {"prefix": "EXP", "identity_fields": ("experiment_key", "specification_sha256")},
    RecordType.FACTOR: {"prefix": "FAC", "identity_fields": ("factor_key", "version")},
    RecordType.INDICATOR: {"prefix": "IND", "identity_fields": ("indicator_key", "version")},
    RecordType.STRATEGY: {"prefix": "STR", "identity_fields": ("strategy_key", "specification_sha256")},
    RecordType.VALIDATION_RESULT: {"prefix": "VAL", "identity_fields": ("experiment_record_id", "result_sha256")},
}


class RecordValidationError(ValueError):
    """Raised when an evidence-foundation record violates its contract."""


class RegisterIntegrityError(RuntimeError):
    """Raised when the append-only register or its ledger links are invalid."""


def _is_sha256(value: str) -> bool:
    digest = value.lower()
    return len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)


def _timestamp_error(value: str, field_name: str) -> str | None:
    if not value.strip():
        return f"{field_name} is required"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return f"{field_name} must be an ISO-8601 timestamp"
    if parsed.tzinfo is None:
        return f"{field_name} must include a timezone"
    return None


def stable_record_id(record_type: RecordType | str, identity: dict[str, Any]) -> str:
    kind = RecordType(record_type)
    if not identity:
        raise RecordValidationError("identity cannot be empty")
    material = {
        "namespace": "ATHENA",
        "schema_version": SCHEMA_VERSION,
        "record_type": kind.value,
        "identity": identity,
    }
    digest = sha256_text(canonical_json(material))[:24].upper()
    return f"ATH-{RECORD_SPECS[kind]['prefix']}-{digest}"


@dataclass(frozen=True)
class Provenance:
    source_type: str
    source_locator: str
    source_sha256: str
    observed_at: str
    acquisition_method: str
    usage_rights: str
    evidence_ids: tuple[str, ...]

    def validate(self) -> list[str]:
        failures: list[str] = []
        required = {
            "source_type": self.source_type,
            "source_locator": self.source_locator,
            "acquisition_method": self.acquisition_method,
            "usage_rights": self.usage_rights,
        }
        failures.extend(f"provenance.{name} is required" for name, value in required.items() if not value.strip())
        if not _is_sha256(self.source_sha256):
            failures.append("provenance.source_sha256 must be 64 hexadecimal characters")
        timestamp_failure = _timestamp_error(self.observed_at, "provenance.observed_at")
        if timestamp_failure:
            failures.append(timestamp_failure)
        if not self.evidence_ids:
            failures.append("provenance.evidence_ids must contain immutable evidence references")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            failures.append("provenance.evidence_ids must be unique")
        if any(not item.strip() for item in self.evidence_ids):
            failures.append("provenance.evidence_ids cannot contain blank values")
        return failures

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "source_locator": self.source_locator,
            "source_sha256": self.source_sha256,
            "observed_at": self.observed_at,
            "acquisition_method": self.acquisition_method,
            "usage_rights": self.usage_rights,
            "evidence_ids": list(self.evidence_ids),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Provenance:
        return cls(
            source_type=str(value.get("source_type", "")),
            source_locator=str(value.get("source_locator", "")),
            source_sha256=str(value.get("source_sha256", "")),
            observed_at=str(value.get("observed_at", "")),
            acquisition_method=str(value.get("acquisition_method", "")),
            usage_rights=str(value.get("usage_rights", "")),
            evidence_ids=tuple(str(item) for item in value.get("evidence_ids", [])),
        )

    @property
    def sha256(self) -> str:
        return sha256_text(canonical_json(self.to_dict()))


@dataclass(frozen=True)
class DatasetFingerprint:
    schema_version: int
    dataset_id: str
    dataset_name: str
    source: str
    source_locator: str
    content_sha256: str
    extraction_config_sha256: str
    row_count: int
    fields: tuple[str, ...]
    universe: tuple[str, ...]
    timeframe: str
    period_start: str | None
    period_end: str | None
    acquired_at: str
    fingerprint_sha256: str

    @classmethod
    def create(
        cls,
        *,
        dataset_name: str,
        source: str,
        source_locator: str,
        content_sha256: str,
        extraction_config_sha256: str,
        row_count: int,
        fields: tuple[str, ...],
        universe: tuple[str, ...],
        timeframe: str,
        acquired_at: str,
        period_start: str | None = None,
        period_end: str | None = None,
    ) -> DatasetFingerprint:
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "dataset_name": dataset_name,
            "source": source,
            "source_locator": source_locator,
            "content_sha256": content_sha256,
            "extraction_config_sha256": extraction_config_sha256,
            "row_count": row_count,
            "fields": list(fields),
            "universe": list(universe),
            "timeframe": timeframe,
            "period_start": period_start,
            "period_end": period_end,
            "acquired_at": acquired_at,
        }
        fingerprint_sha256 = sha256_text(canonical_json(manifest))
        dataset_id = stable_record_id(RecordType.DATASET, {"fingerprint_sha256": fingerprint_sha256})
        result = cls(
            schema_version=SCHEMA_VERSION,
            dataset_name=dataset_name,
            source=source,
            source_locator=source_locator,
            content_sha256=content_sha256,
            extraction_config_sha256=extraction_config_sha256,
            row_count=row_count,
            fields=fields,
            universe=universe,
            timeframe=timeframe,
            period_start=period_start,
            period_end=period_end,
            acquired_at=acquired_at,
            dataset_id=dataset_id,
            fingerprint_sha256=fingerprint_sha256,
        )
        failures = result.validate()
        if failures:
            raise RecordValidationError("; ".join(failures))
        return result

    def _manifest(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "dataset_name": self.dataset_name,
            "source": self.source,
            "source_locator": self.source_locator,
            "content_sha256": self.content_sha256,
            "extraction_config_sha256": self.extraction_config_sha256,
            "row_count": self.row_count,
            "fields": list(self.fields),
            "universe": list(self.universe),
            "timeframe": self.timeframe,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "acquired_at": self.acquired_at,
        }

    def validate(self) -> list[str]:
        failures: list[str] = []
        if self.schema_version != SCHEMA_VERSION:
            failures.append(f"dataset schema_version must be {SCHEMA_VERSION}")
        for name, value in {
            "dataset_name": self.dataset_name,
            "source": self.source,
            "source_locator": self.source_locator,
            "timeframe": self.timeframe,
        }.items():
            if not value.strip():
                failures.append(f"dataset.{name} is required")
        if not _is_sha256(self.content_sha256):
            failures.append("dataset.content_sha256 must be 64 hexadecimal characters")
        if not _is_sha256(self.extraction_config_sha256):
            failures.append("dataset.extraction_config_sha256 must be 64 hexadecimal characters")
        if self.row_count < 1:
            failures.append("dataset.row_count must be at least 1")
        if not self.fields or len(self.fields) != len(set(self.fields)) or any(not item.strip() for item in self.fields):
            failures.append("dataset.fields must contain unique non-blank field names")
        if not self.universe or len(self.universe) != len(set(self.universe)) or any(not item.strip() for item in self.universe):
            failures.append("dataset.universe must contain unique non-blank instruments")
        timestamp_failure = _timestamp_error(self.acquired_at, "dataset.acquired_at")
        if timestamp_failure:
            failures.append(timestamp_failure)
        expected_fingerprint = sha256_text(canonical_json(self._manifest()))
        if self.fingerprint_sha256 != expected_fingerprint:
            failures.append("dataset.fingerprint_sha256 does not match its canonical manifest")
        expected_id = stable_record_id(RecordType.DATASET, {"fingerprint_sha256": expected_fingerprint})
        if self.dataset_id != expected_id:
            failures.append("dataset.dataset_id does not match its fingerprint")
        return failures

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._manifest(),
            "dataset_id": self.dataset_id,
            "fingerprint_sha256": self.fingerprint_sha256,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> DatasetFingerprint:
        return cls(
            schema_version=int(value.get("schema_version", 0)),
            dataset_id=str(value.get("dataset_id", "")),
            dataset_name=str(value.get("dataset_name", "")),
            source=str(value.get("source", "")),
            source_locator=str(value.get("source_locator", "")),
            content_sha256=str(value.get("content_sha256", "")),
            extraction_config_sha256=str(value.get("extraction_config_sha256", "")),
            row_count=int(value.get("row_count", 0)),
            fields=tuple(str(item) for item in value.get("fields", [])),
            universe=tuple(str(item) for item in value.get("universe", [])),
            timeframe=str(value.get("timeframe", "")),
            period_start=value.get("period_start"),
            period_end=value.get("period_end"),
            acquired_at=str(value.get("acquired_at", "")),
            fingerprint_sha256=str(value.get("fingerprint_sha256", "")),
        )


@dataclass(frozen=True)
class KnowledgeRecord:
    schema_version: int
    record_id: str
    record_type: RecordType
    title: str
    identity: dict[str, Any]
    provenance: Provenance
    evidence_ids: tuple[str, ...]
    related_record_ids: tuple[str, ...]
    content: dict[str, Any]
    content_sha256: str
    recorded_at: str
    record_sha256: str

    @classmethod
    def create(
        cls,
        *,
        record_type: RecordType | str,
        title: str,
        identity: dict[str, Any],
        provenance: Provenance,
        evidence_ids: tuple[str, ...],
        related_record_ids: tuple[str, ...],
        content: dict[str, Any],
        recorded_at: str | None = None,
    ) -> KnowledgeRecord:
        kind = RecordType(record_type)
        record_id = stable_record_id(kind, identity)
        content_sha256 = sha256_text(canonical_json(content))
        body = {
            "schema_version": SCHEMA_VERSION,
            "record_id": record_id,
            "record_type": kind.value,
            "title": title,
            "identity": identity,
            "provenance": provenance.to_dict(),
            "evidence_ids": list(evidence_ids),
            "related_record_ids": list(related_record_ids),
            "content": content,
            "content_sha256": content_sha256,
            "recorded_at": recorded_at or provenance.observed_at,
        }
        result = cls(
            schema_version=SCHEMA_VERSION,
            record_id=record_id,
            record_type=kind,
            title=title,
            identity=identity,
            provenance=provenance,
            evidence_ids=evidence_ids,
            related_record_ids=related_record_ids,
            content=content,
            content_sha256=content_sha256,
            recorded_at=body["recorded_at"],
            record_sha256=sha256_text(canonical_json(body)),
        )
        failures = result.validate()
        if failures:
            raise RecordValidationError("; ".join(failures))
        return result

    def _body(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "record_id": self.record_id,
            "record_type": self.record_type.value,
            "title": self.title,
            "identity": self.identity,
            "provenance": self.provenance.to_dict(),
            "evidence_ids": list(self.evidence_ids),
            "related_record_ids": list(self.related_record_ids),
            "content": self.content,
            "content_sha256": self.content_sha256,
            "recorded_at": self.recorded_at,
        }

    def validate(self) -> list[str]:
        failures = self.provenance.validate()
        if self.schema_version != SCHEMA_VERSION:
            failures.append(f"record schema_version must be {SCHEMA_VERSION}")
        if not self.title.strip():
            failures.append("record.title is required")
        required_identity = RECORD_SPECS[self.record_type]["identity_fields"]
        missing = [field for field in required_identity if field not in self.identity or self.identity[field] in (None, "", [])]
        if missing:
            failures.append(f"record.identity missing required fields: {missing}")
        expected_id = stable_record_id(self.record_type, self.identity)
        if self.record_id != expected_id:
            failures.append("record.record_id does not match its canonical identity")
        if not RECORD_ID_PATTERN.fullmatch(self.record_id):
            failures.append("record.record_id has an invalid format")
        if not self.evidence_ids:
            failures.append("record.evidence_ids must contain immutable evidence references")
        if len(self.evidence_ids) != len(set(self.evidence_ids)) or any(not item.strip() for item in self.evidence_ids):
            failures.append("record.evidence_ids must be unique and non-blank")
        if not set(self.evidence_ids).issubset(set(self.provenance.evidence_ids)):
            failures.append("record.evidence_ids must be supported by provenance.evidence_ids")
        if len(self.related_record_ids) != len(set(self.related_record_ids)):
            failures.append("record.related_record_ids must be unique")
        if any(not RECORD_ID_PATTERN.fullmatch(item) for item in self.related_record_ids):
            failures.append("record.related_record_ids contains an invalid stable ID")
        expected_content_sha = sha256_text(canonical_json(self.content))
        if self.content_sha256 != expected_content_sha:
            failures.append("record.content_sha256 does not match content")
        timestamp_failure = _timestamp_error(self.recorded_at, "record.recorded_at")
        if timestamp_failure:
            failures.append(timestamp_failure)
        expected_record_sha = sha256_text(canonical_json(self._body()))
        if self.record_sha256 != expected_record_sha:
            failures.append("record.record_sha256 does not match the canonical record")

        if self.record_type == RecordType.DATASET:
            raw_fingerprint = self.content.get("dataset_fingerprint")
            if not isinstance(raw_fingerprint, dict):
                failures.append("dataset record must contain dataset_fingerprint")
            else:
                try:
                    fingerprint = DatasetFingerprint.from_dict(raw_fingerprint)
                    failures.extend(fingerprint.validate())
                    if fingerprint.dataset_id != self.record_id:
                        failures.append("dataset record ID must equal the embedded dataset ID")
                    if self.identity.get("fingerprint_sha256") != fingerprint.fingerprint_sha256:
                        failures.append("dataset record identity must equal the embedded fingerprint")
                except (TypeError, ValueError) as error:
                    failures.append(f"dataset record fingerprint is invalid: {error}")

        if self.record_type in {RecordType.STRATEGY, RecordType.EXPERIMENT}:
            if self.identity.get("specification_sha256") != expected_content_sha:
                failures.append(f"{self.record_type.value} specification digest must match record content")

        if self.record_type == RecordType.VALIDATION_RESULT:
            experiment_record_id = self.identity.get("experiment_record_id")
            if experiment_record_id not in self.related_record_ids:
                failures.append("validation result must relate to its experiment record")
            if self.content.get("experiment_record_id") != experiment_record_id:
                failures.append("validation result content must identify its experiment record")
            decision = self.content.get("decision")
            if not isinstance(decision, dict):
                failures.append("validation result must contain the Decision Court result")
            elif self.identity.get("result_sha256") != sha256_text(canonical_json(decision)):
                failures.append("validation result digest must match the Decision Court result")
        return failures

    def to_dict(self) -> dict[str, Any]:
        return {**self._body(), "record_sha256": self.record_sha256}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> KnowledgeRecord:
        try:
            kind = RecordType(value.get("record_type", ""))
        except ValueError as error:
            raise RecordValidationError(f"unsupported record_type: {value.get('record_type')}") from error
        result = cls(
            schema_version=int(value.get("schema_version", 0)),
            record_id=str(value.get("record_id", "")),
            record_type=kind,
            title=str(value.get("title", "")),
            identity=dict(value.get("identity", {})),
            provenance=Provenance.from_dict(dict(value.get("provenance", {}))),
            evidence_ids=tuple(str(item) for item in value.get("evidence_ids", [])),
            related_record_ids=tuple(str(item) for item in value.get("related_record_ids", [])),
            content=dict(value.get("content", {})),
            content_sha256=str(value.get("content_sha256", "")),
            recorded_at=str(value.get("recorded_at", "")),
            record_sha256=str(value.get("record_sha256", "")),
        )
        failures = result.validate()
        if failures:
            raise RecordValidationError("; ".join(failures))
        return result


class EvidenceRegister:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _records(self) -> list[KnowledgeRecord]:
        if not self.path.exists():
            return []
        records: list[KnowledgeRecord] = []
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                records.append(KnowledgeRecord.from_dict(raw))
            except (json.JSONDecodeError, RecordValidationError, TypeError, ValueError) as error:
                raise RegisterIntegrityError(f"invalid register record at line {line_number}: {error}") from error
        return records

    def records(self) -> tuple[KnowledgeRecord, ...]:
        """Return validated immutable records for controlled reconciliation."""
        return tuple(self._records())

    @staticmethod
    def _ledger_links(ledger: EvidenceLedger) -> dict[str, list[dict[str, Any]]]:
        links: dict[str, list[dict[str, Any]]] = {}
        for entry in ledger.entries():
            if entry.get("event_type") != "evidence_record_registered":
                continue
            payload = entry.get("payload", {})
            links.setdefault(str(payload.get("record_id", "")), []).append(entry)
        return links

    def append(self, record: KnowledgeRecord, ledger: EvidenceLedger) -> dict[str, Any]:
        failures = record.validate()
        if failures:
            raise RecordValidationError("; ".join(failures))
        ledger.validate()
        records = self._records()
        known_ids = {item.record_id for item in records}
        missing_related = set(record.related_record_ids) - known_ids
        if missing_related:
            raise RegisterIntegrityError(
                f"{record.record_id}: related records must be registered first: {sorted(missing_related)}"
            )
        matches = [item for item in records if item.record_id == record.record_id]
        if matches and matches[0].record_sha256 != record.record_sha256:
            raise RegisterIntegrityError(f"immutable record conflict for {record.record_id}")

        created = not matches
        if created:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(canonical_json(record.to_dict()) + "\n")

        links = self._ledger_links(ledger)
        matching_links = [
            entry for entry in links.get(record.record_id, [])
            if entry.get("payload", {}).get("record_sha256") == record.record_sha256
        ]
        ledger_entry = matching_links[0] if matching_links else ledger.append(
            "evidence_record_registered",
            "athena.evidence_register",
            {
                "record_id": record.record_id,
                "record_type": record.record_type.value,
                "record_sha256": record.record_sha256,
                "content_sha256": record.content_sha256,
                "provenance_sha256": record.provenance.sha256,
                "evidence_ids": list(record.evidence_ids),
            },
        )
        return {"created": created, "record": record.to_dict(), "ledger_hash": ledger_entry["hash"]}

    def validate(self, ledger: EvidenceLedger | None = None) -> dict[str, Any]:
        records = self._records()
        by_id: dict[str, KnowledgeRecord] = {}
        for record in records:
            if record.record_id in by_id:
                raise RegisterIntegrityError(f"duplicate record_id: {record.record_id}")
            by_id[record.record_id] = record
        for record in records:
            missing_related = set(record.related_record_ids) - set(by_id)
            if missing_related:
                raise RegisterIntegrityError(
                    f"{record.record_id}: missing related records: {sorted(missing_related)}"
                )

        linked_events = 0
        if ledger is not None:
            ledger.validate()
            links = self._ledger_links(ledger)
            for record_id, record in by_id.items():
                matching = [
                    entry for entry in links.get(record_id, [])
                    if entry.get("payload", {}).get("record_sha256") == record.record_sha256
                ]
                if len(matching) != 1:
                    raise RegisterIntegrityError(f"{record_id}: expected exactly one matching ledger link")
                linked_events += 1
            for record_id, entries in links.items():
                if record_id not in by_id:
                    raise RegisterIntegrityError(f"ledger references missing register record: {record_id}")
                expected_sha = by_id[record_id].record_sha256
                if any(entry.get("payload", {}).get("record_sha256") != expected_sha for entry in entries):
                    raise RegisterIntegrityError(f"ledger digest mismatch for register record: {record_id}")

        counts = {kind.value: 0 for kind in RecordType}
        for record in records:
            counts[record.record_type.value] += 1
        return {
            "valid": True,
            "schema_version": SCHEMA_VERSION,
            "entries": len(records),
            "record_ids": sorted(by_id),
            "record_type_counts": counts,
            "register_sha256": sha256_text(canonical_json([record.to_dict() for record in records])),
            "ledger_links": linked_events,
        }


def validate_record_contract(
    manifest_path: str | Path,
    repository_root: str | Path = ".",
) -> dict[str, Any]:
    path = Path(manifest_path)
    root = Path(repository_root)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    failures: list[str] = []
    if manifest.get("schema_version") != SCHEMA_VERSION:
        failures.append(f"evidence-register schema_version must be {SCHEMA_VERSION}")
    if manifest.get("status") != "executable_phase_0_contract":
        failures.append("evidence-register status must be executable_phase_0_contract")
    authority_ids = set(manifest.get("authority", {}).get("evidence_ids", []))
    if not {"EF-002", "EF-009", "EF-010"}.issubset(authority_ids):
        failures.append("evidence-register authority must cite EF-002, EF-009, and EF-010")
    if manifest.get("register", {}).get("ledger_event") != "evidence_record_registered":
        failures.append("evidence-register ledger event is not controlled")

    declared: dict[str, dict[str, Any]] = {
        str(item.get("record_type")): item for item in manifest.get("record_types", [])
    }
    if len(declared) != len(RECORD_SPECS):
        failures.append("evidence-register must declare every controlled record type exactly once")
    for kind, spec in RECORD_SPECS.items():
        item = declared.get(kind.value)
        if item is None:
            failures.append(f"evidence-register missing record type: {kind.value}")
            continue
        if item.get("prefix") != spec["prefix"]:
            failures.append(f"{kind.value}: prefix differs from executable contract")
        if tuple(item.get("required_identity_fields", [])) != spec["identity_fields"]:
            failures.append(f"{kind.value}: identity fields differ from executable contract")

    schema_paths = manifest.get("schemas", {})
    required_schemas = {
        "knowledge_record",
        "dataset_fingerprint",
        "research_intake",
        "research_card",
        "formula_extraction",
        "market_data_policy",
    }
    if set(schema_paths) != required_schemas:
        failures.append(f"evidence schemas must be exactly: {sorted(required_schemas)}")
    for name in sorted(required_schemas):
        schema_path = root / str(schema_paths.get(name, ""))
        if not schema_path.is_file():
            failures.append(f"missing evidence schema: {name}")
            continue
        try:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            failures.append(f"invalid JSON evidence schema: {name}")
            continue
        if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
            failures.append(f"{name}: schema must be a closed object contract")

    if failures:
        raise RecordValidationError("; ".join(failures))
    return {
        "valid": True,
        "contract_id": manifest.get("contract_id"),
        "record_types": len(declared),
        "schemas": len(schema_paths),
        "ledger_event": manifest["register"]["ledger_event"],
    }
