from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from athena.evidence import EvidenceLedger, canonical_json, sha256_bytes, sha256_text
from athena.models import utc_now
from athena.records import (
    EvidenceRegister,
    KnowledgeRecord,
    Provenance,
    RecordType,
    RegisterIntegrityError,
)


TOP_LEVEL_FIELDS = {
    "schema_version",
    "source_file",
    "declared_source_sha256",
    "usage_rights",
    "evidence_ids",
    "paper",
    "research_card",
}
PAPER_FIELDS = {
    "title",
    "canonical_locator",
    "publication_date",
    "publisher",
    "doi",
    "authors",
    "bibliography",
}
AUTHOR_FIELDS = {"name", "orcid", "affiliation"}
CARD_FIELDS = {
    "version",
    "summary",
    "research_question",
    "method",
    "findings",
    "limitations",
    "assumptions",
    "counter_evidence",
    "claims",
    "hypotheses",
    "formulas",
}
CLAIM_FIELDS = {"claim_id", "statement", "relationship", "source_locator"}
HYPOTHESIS_FIELDS = {"hypothesis_key", "version", "claim", "mechanism", "falsification", "claim_ids"}
FORMULA_FIELDS = {
    "formula_key",
    "version",
    "expression",
    "notation",
    "applicability",
    "assumptions",
    "source_locator",
    "python_status",
    "pine_status",
}


class ResearchIntakeError(ValueError):
    """Raised when an intake operation cannot be evaluated safely."""


@dataclass(frozen=True)
class IntakePolicy:
    policy_id: str
    maximum_source_bytes: int
    allowed_source_suffixes: frozenset[str]
    allowed_usage_rights: frozenset[str]
    prohibited_usage_rights: frozenset[str]
    required_paper_fields: tuple[str, ...]
    required_research_card_fields: tuple[str, ...]
    required_claim_fields: tuple[str, ...]
    allowed_claim_relationships: frozenset[str]
    require_hypothesis: bool
    require_formula: bool
    require_counter_evidence: bool
    evidence_ids: tuple[str, ...]

    @classmethod
    def from_file(cls, path: str | Path) -> IntakePolicy:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if raw.get("schema_version") != 1:
            raise ResearchIntakeError("intake policy schema_version must be 1")
        if raw.get("live_execution") != "prohibited":
            raise ResearchIntakeError("intake policy must preserve the live-execution prohibition")
        return cls(
            policy_id=str(raw.get("policy_id", "")),
            maximum_source_bytes=int(raw.get("maximum_source_bytes", 0)),
            allowed_source_suffixes=frozenset(str(item).lower() for item in raw.get("allowed_source_suffixes", [])),
            allowed_usage_rights=frozenset(str(item) for item in raw.get("allowed_usage_rights", [])),
            prohibited_usage_rights=frozenset(str(item) for item in raw.get("prohibited_usage_rights", [])),
            required_paper_fields=tuple(str(item) for item in raw.get("required_paper_fields", [])),
            required_research_card_fields=tuple(str(item) for item in raw.get("required_research_card_fields", [])),
            required_claim_fields=tuple(str(item) for item in raw.get("required_claim_fields", [])),
            allowed_claim_relationships=frozenset(str(item) for item in raw.get("allowed_claim_relationships", [])),
            require_hypothesis=bool(raw.get("require_hypothesis")),
            require_formula=bool(raw.get("require_formula")),
            require_counter_evidence=bool(raw.get("require_counter_evidence")),
            evidence_ids=tuple(str(item) for item in raw.get("evidence_ids", [])),
        )

    def validate(self) -> list[str]:
        failures: list[str] = []
        if not self.policy_id.strip():
            failures.append("intake policy_id is required")
        if self.maximum_source_bytes < 1:
            failures.append("intake maximum_source_bytes must be positive")
        if not self.allowed_source_suffixes:
            failures.append("intake allowed_source_suffixes cannot be empty")
        if not self.allowed_usage_rights:
            failures.append("intake allowed_usage_rights cannot be empty")
        if self.allowed_usage_rights & self.prohibited_usage_rights:
            failures.append("intake usage-rights allow and prohibit lists overlap")
        if not {"EF-002", "EF-009", "EF-010"}.issubset(set(self.evidence_ids)):
            failures.append("intake policy must cite EF-002, EF-009, and EF-010")
        return failures


class QuarantineRegister:
    def __init__(self, path: str | Path, clock: Callable[[], str] = utc_now) -> None:
        self.path = Path(path)
        self.clock = clock

    def _entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ResearchIntakeError(f"invalid quarantine JSON at line {line_number}") from error
        return entries

    def append(
        self,
        *,
        manifest_sha256: str,
        source_locator: str,
        source_sha256: str | None,
        reasons: list[str],
        ledger: EvidenceLedger,
    ) -> dict[str, Any]:
        unique_reasons = sorted(set(reason for reason in reasons if reason))
        if not unique_reasons:
            raise ResearchIntakeError("quarantine reasons are required")
        entries = self._entries()
        body = {
            "sequence": len(entries) + 1,
            "recorded_at": self.clock(),
            "manifest_sha256": manifest_sha256,
            "source_locator": source_locator,
            "source_sha256": source_sha256,
            "reasons": unique_reasons,
            "disposition": "QUARANTINED_NO_COURT_SUBMISSION",
        }
        entry = {**body, "hash": sha256_text(canonical_json(body))}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(canonical_json(entry) + "\n")
        ledger_entry = ledger.append(
            "research_intake_quarantined",
            "athena.research_intake",
            {
                "quarantine_hash": entry["hash"],
                "manifest_sha256": manifest_sha256,
                "source_sha256": source_sha256,
                "reasons": unique_reasons,
            },
        )
        return {**entry, "ledger_hash": ledger_entry["hash"]}

    def validate(self, ledger: EvidenceLedger | None = None) -> dict[str, Any]:
        entries = self._entries()
        for expected_sequence, entry in enumerate(entries, 1):
            body = {key: value for key, value in entry.items() if key != "hash"}
            if entry.get("sequence") != expected_sequence:
                raise ResearchIntakeError(f"invalid quarantine sequence at entry {expected_sequence}")
            if entry.get("hash") != sha256_text(canonical_json(body)):
                raise ResearchIntakeError(f"invalid quarantine hash at entry {expected_sequence}")
            if entry.get("disposition") != "QUARANTINED_NO_COURT_SUBMISSION":
                raise ResearchIntakeError(f"invalid quarantine disposition at entry {expected_sequence}")
        links = 0
        if ledger is not None:
            ledger_entries = ledger.entries()
            events: dict[str, list[dict[str, Any]]] = {}
            for item in ledger_entries:
                if item.get("event_type") == "research_intake_quarantined":
                    key = str(item.get("payload", {}).get("quarantine_hash", ""))
                    events.setdefault(key, []).append(item)
            for entry in entries:
                if len(events.get(entry["hash"], [])) != 1:
                    raise ResearchIntakeError(f"quarantine {entry['hash']}: expected exactly one ledger link")
                links += 1
            unknown = set(events) - {entry["hash"] for entry in entries}
            if unknown:
                raise ResearchIntakeError(f"ledger references missing quarantine records: {sorted(unknown)}")
        return {
            "valid": True,
            "entries": len(entries),
            "ledger_links": links,
            "terminal_hash": entries[-1]["hash"] if entries else None,
        }


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _nonblank(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    return value is not None


def _unknown_fields(value: Any, allowed: set[str], label: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{label} must be an object"]
    unknown = sorted(set(value) - allowed)
    return [f"{label} contains unknown fields: {unknown}"] if unknown else []


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


class ResearchIntake:
    def __init__(self, policy: IntakePolicy, clock: Callable[[], str] = utc_now) -> None:
        failures = policy.validate()
        if failures:
            raise ResearchIntakeError("; ".join(failures))
        self.policy = policy
        self.clock = clock

    @classmethod
    def from_policy_file(
        cls,
        path: str | Path,
        clock: Callable[[], str] = utc_now,
    ) -> ResearchIntake:
        return cls(IntakePolicy.from_file(path), clock=clock)

    def _validate_request(self, raw: Any) -> list[str]:
        failures = _unknown_fields(raw, TOP_LEVEL_FIELDS, "intake")
        if not isinstance(raw, dict):
            return failures
        if raw.get("schema_version") != 1:
            failures.append("intake schema_version must be 1")
        if not _nonblank(raw.get("source_file")):
            failures.append("intake.source_file is required")
        if not _is_sha256(raw.get("declared_source_sha256")):
            failures.append("intake.declared_source_sha256 must be 64 lowercase hexadecimal characters")
        rights = raw.get("usage_rights")
        if rights in self.policy.prohibited_usage_rights or rights not in self.policy.allowed_usage_rights:
            failures.append("intake.usage_rights is prohibited, uncertain, or unsupported")
        evidence_ids = raw.get("evidence_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids or any(not _nonblank(item) for item in evidence_ids):
            failures.append("intake.evidence_ids must contain immutable evidence references")
        elif len(evidence_ids) != len(set(evidence_ids)):
            failures.append("intake.evidence_ids must be unique")

        paper = raw.get("paper")
        failures.extend(_unknown_fields(paper, PAPER_FIELDS, "paper"))
        if isinstance(paper, dict):
            for field in self.policy.required_paper_fields:
                if not _nonblank(paper.get(field)):
                    failures.append(f"paper.{field} is required")
            if not _nonblank(paper.get("bibliography")):
                failures.append("paper.bibliography is required")
            authors = paper.get("authors")
            if not isinstance(authors, list) or not authors:
                failures.append("paper.authors must be a non-empty array")
            else:
                for index, author in enumerate(authors):
                    failures.extend(_unknown_fields(author, AUTHOR_FIELDS, f"paper.authors[{index}]"))
                    if isinstance(author, dict) and not _nonblank(author.get("name")):
                        failures.append(f"paper.authors[{index}].name is required")

        card = raw.get("research_card")
        failures.extend(_unknown_fields(card, CARD_FIELDS, "research_card"))
        if isinstance(card, dict):
            for field in self.policy.required_research_card_fields:
                if not _nonblank(card.get(field)):
                    failures.append(f"research_card.{field} is required")
            if not isinstance(card.get("version"), int) or card.get("version", 0) < 1:
                failures.append("research_card.version must be a positive integer")
            for field in ("findings", "limitations", "assumptions", "counter_evidence"):
                items = card.get(field)
                if not isinstance(items, list) or not items or any(not isinstance(item, str) or not item.strip() for item in items):
                    failures.append(f"research_card.{field} must be a non-empty array of non-blank strings")
            if self.policy.require_counter_evidence and not _nonblank(card.get("counter_evidence")):
                failures.append("research_card.counter_evidence is required")
            claims = card.get("claims")
            claim_ids: set[str] = set()
            if not isinstance(claims, list) or not claims:
                failures.append("research_card.claims must be a non-empty array")
            else:
                for index, claim in enumerate(claims):
                    failures.extend(_unknown_fields(claim, CLAIM_FIELDS, f"research_card.claims[{index}]"))
                    if isinstance(claim, dict):
                        for field in self.policy.required_claim_fields:
                            if not _nonblank(claim.get(field)):
                                failures.append(f"research_card.claims[{index}].{field} is required")
                        relationship = claim.get("relationship")
                        if relationship not in self.policy.allowed_claim_relationships:
                            failures.append(f"research_card.claims[{index}].relationship is unsupported")
                        claim_id = str(claim.get("claim_id", ""))
                        if claim_id in claim_ids:
                            failures.append(f"duplicate claim_id: {claim_id}")
                        claim_ids.add(claim_id)

            hypotheses = card.get("hypotheses")
            if self.policy.require_hypothesis and not _nonblank(hypotheses):
                failures.append("research_card.hypotheses is required")
            if not isinstance(hypotheses, list) or not hypotheses:
                failures.append("research_card.hypotheses must be a non-empty array")
            else:
                for index, hypothesis in enumerate(hypotheses):
                    failures.extend(_unknown_fields(hypothesis, HYPOTHESIS_FIELDS, f"research_card.hypotheses[{index}]"))
                    if isinstance(hypothesis, dict):
                        for field in HYPOTHESIS_FIELDS:
                            if not _nonblank(hypothesis.get(field)):
                                failures.append(f"research_card.hypotheses[{index}].{field} is required")
                        if not isinstance(hypothesis.get("version"), int) or hypothesis.get("version", 0) < 1:
                            failures.append(f"research_card.hypotheses[{index}].version must be a positive integer")
                        if not isinstance(hypothesis.get("claim_ids"), list) or not hypothesis.get("claim_ids"):
                            failures.append(f"research_card.hypotheses[{index}].claim_ids must be a non-empty array")
                        references = set(str(item) for item in hypothesis.get("claim_ids", []))
                        unknown = references - claim_ids
                        if unknown:
                            failures.append(f"research_card.hypotheses[{index}] references unknown claims: {sorted(unknown)}")

            formulas = card.get("formulas")
            if self.policy.require_formula and not _nonblank(formulas):
                failures.append("research_card.formulas is required")
            if not isinstance(formulas, list) or not formulas:
                failures.append("research_card.formulas must be a non-empty array")
            else:
                for index, formula in enumerate(formulas):
                    failures.extend(_unknown_fields(formula, FORMULA_FIELDS, f"research_card.formulas[{index}]"))
                    if isinstance(formula, dict):
                        for field in FORMULA_FIELDS:
                            if not _nonblank(formula.get(field)):
                                failures.append(f"research_card.formulas[{index}].{field} is required")
                        if not isinstance(formula.get("version"), int) or formula.get("version", 0) < 1:
                            failures.append(f"research_card.formulas[{index}].version must be a positive integer")
                        notation = formula.get("notation")
                        if not isinstance(notation, dict) or not notation or any(
                            not isinstance(key, str) or not key.strip() or not isinstance(value, str) or not value.strip()
                            for key, value in notation.items()
                        ):
                            failures.append(f"research_card.formulas[{index}].notation must map non-blank symbols to meanings")
                        assumptions = formula.get("assumptions")
                        if not isinstance(assumptions, list) or not assumptions or any(
                            not isinstance(item, str) or not item.strip() for item in assumptions
                        ):
                            failures.append(f"research_card.formulas[{index}].assumptions must be a non-empty string array")
                        if formula.get("python_status") != "not_implemented":
                            failures.append(f"research_card.formulas[{index}].python_status must be not_implemented")
                        if formula.get("pine_status") != "not_implemented":
                            failures.append(f"research_card.formulas[{index}].pine_status must be not_implemented")
        return sorted(set(failures))

    @staticmethod
    def _resolve_source(manifest_path: Path, source_file: str) -> Path:
        root = manifest_path.resolve().parent
        candidate = (root / source_file).resolve()
        if not candidate.is_relative_to(root):
            raise ResearchIntakeError("intake.source_file must remain inside the manifest directory")
        return candidate

    @staticmethod
    def _persist_object(objects_root: Path, source_bytes: bytes, digest: str) -> str:
        relative = Path("sha256") / digest[:2] / digest
        target = objects_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if sha256_bytes(target.read_bytes()) != digest:
                raise ResearchIntakeError(f"object-store digest conflict: {relative}")
            return relative.as_posix()
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(source_bytes)
        temporary.replace(target)
        return relative.as_posix()

    def _quarantine(
        self,
        *,
        raw: dict[str, Any],
        manifest_sha256: str,
        source_sha256: str | None,
        reasons: list[str],
        quarantine: QuarantineRegister,
        ledger: EvidenceLedger,
    ) -> dict[str, Any]:
        paper = raw.get("paper") if isinstance(raw.get("paper"), dict) else {}
        entry = quarantine.append(
            manifest_sha256=manifest_sha256,
            source_locator=str(paper.get("canonical_locator", raw.get("source_file", "unknown"))),
            source_sha256=source_sha256,
            reasons=reasons,
            ledger=ledger,
        )
        return {
            "status": "QUARANTINED",
            "policy_id": self.policy.policy_id,
            "reasons": entry["reasons"],
            "quarantine_hash": entry["hash"],
            "ledger_hash": entry["ledger_hash"],
        }

    def ingest(
        self,
        manifest_path: str | Path,
        *,
        objects_root: str | Path,
        register: EvidenceRegister,
        quarantine: QuarantineRegister,
        ledger: EvidenceLedger,
    ) -> dict[str, Any]:
        path = Path(manifest_path)
        manifest_bytes = path.read_bytes()
        manifest_sha256 = sha256_bytes(manifest_bytes)
        try:
            manifest_text = manifest_bytes.decode("utf-8")
            raw = json.loads(manifest_text)
        except UnicodeDecodeError:
            return self._quarantine(
                raw={},
                manifest_sha256=manifest_sha256,
                source_sha256=None,
                reasons=["intake manifest must be UTF-8 JSON"],
                quarantine=quarantine,
                ledger=ledger,
            )
        except json.JSONDecodeError as error:
            return self._quarantine(
                raw={},
                manifest_sha256=manifest_sha256,
                source_sha256=None,
                reasons=[f"intake manifest is invalid JSON: {error.msg}"],
                quarantine=quarantine,
                ledger=ledger,
            )
        if not isinstance(raw, dict):
            raw = {}
        failures = self._validate_request(raw)

        source_bytes: bytes | None = None
        source_sha256: str | None = None
        source_path: Path | None = None
        try:
            source_path = self._resolve_source(path, str(raw.get("source_file", "")))
            if not source_path.is_file():
                failures.append("intake source file does not exist")
            elif source_path.suffix.lower() not in self.policy.allowed_source_suffixes:
                failures.append("intake source suffix is not allowed")
            else:
                source_bytes = source_path.read_bytes()
                if len(source_bytes) > self.policy.maximum_source_bytes:
                    failures.append("intake source exceeds maximum_source_bytes")
                try:
                    source_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    failures.append("intake source must be UTF-8 text")
                else:
                    source_sha256 = sha256_bytes(source_bytes)
                    if source_sha256 != raw.get("declared_source_sha256"):
                        failures.append("declared source digest does not match retained bytes")
        except (OSError, ResearchIntakeError) as error:
            failures.append(str(error))

        if failures:
            return self._quarantine(
                raw=raw,
                manifest_sha256=manifest_sha256,
                source_sha256=source_sha256,
                reasons=failures,
                quarantine=quarantine,
                ledger=ledger,
            )
        assert source_bytes is not None and source_sha256 is not None and source_path is not None

        existing = register.records()
        paper = raw["paper"]
        locator = paper["canonical_locator"]
        existing_by_locator = [
            record for record in existing
            if record.record_type == RecordType.PAPER and record.identity.get("canonical_locator") == locator
        ]
        existing_by_digest = [
            record for record in existing
            if record.record_type == RecordType.PAPER and record.provenance.source_sha256 == source_sha256
        ]
        if existing_by_locator:
            registered = existing_by_locator[0]
            if registered.provenance.source_sha256 != source_sha256:
                return self._quarantine(
                    raw=raw,
                    manifest_sha256=manifest_sha256,
                    source_sha256=source_sha256,
                    reasons=["canonical locator is already registered with different source bytes"],
                    quarantine=quarantine,
                    ledger=ledger,
                )
            duplicate = ledger.append(
                "research_intake_duplicate_detected",
                "athena.research_intake",
                {"paper_record_id": registered.record_id, "source_sha256": source_sha256},
            )
            return {
                "status": "DUPLICATE",
                "policy_id": self.policy.policy_id,
                "paper_record_id": registered.record_id,
                "source_sha256": source_sha256,
                "ledger_hash": duplicate["hash"],
            }
        if existing_by_digest:
            return self._quarantine(
                raw=raw,
                manifest_sha256=manifest_sha256,
                source_sha256=source_sha256,
                reasons=["source bytes are already registered under a different canonical locator"],
                quarantine=quarantine,
                ledger=ledger,
            )

        recorded_at = self.clock()
        evidence_ids = tuple(str(item) for item in raw["evidence_ids"])
        provenance = Provenance(
            source_type="research_source",
            source_locator=locator,
            source_sha256=source_sha256,
            observed_at=recorded_at,
            acquisition_method="controlled_repository_intake",
            usage_rights=raw["usage_rights"],
            evidence_ids=evidence_ids,
        )
        author_records: list[KnowledgeRecord] = []
        for author in paper["authors"]:
            author_key = (
                f"orcid:{_normalize(author['orcid'])}"
                if _nonblank(author.get("orcid"))
                else f"source:{_normalize(locator)}|name:{_normalize(author['name'])}|affiliation:{_normalize(author.get('affiliation') or '')}"
            )
            author_records.append(KnowledgeRecord.create(
                record_type=RecordType.AUTHOR,
                title=author["name"],
                identity={"author_key": author_key},
                provenance=provenance,
                evidence_ids=evidence_ids,
                related_record_ids=(),
                content={
                    "name": author["name"],
                    "orcid": author.get("orcid"),
                    "affiliation": author.get("affiliation"),
                    "source_canonical_locator": locator,
                },
                recorded_at=recorded_at,
            ))

        object_path = (Path("sha256") / source_sha256[:2] / source_sha256).as_posix()
        paper_record = KnowledgeRecord.create(
            record_type=RecordType.PAPER,
            title=paper["title"],
            identity={"canonical_locator": locator},
            provenance=provenance,
            evidence_ids=evidence_ids,
            related_record_ids=tuple(record.record_id for record in author_records),
            content={
                "title": paper["title"],
                "publication_date": paper["publication_date"],
                "publisher": paper["publisher"],
                "doi": paper.get("doi"),
                "bibliography": paper["bibliography"],
                "author_record_ids": [record.record_id for record in author_records],
                "source_object": {
                    "path": object_path,
                    "sha256": source_sha256,
                    "bytes": len(source_bytes),
                    "retained": True,
                },
            },
            recorded_at=recorded_at,
        )
        card = raw["research_card"]
        claims = [
            {
                **claim,
                "evidence_record_ids": [paper_record.record_id],
            }
            for claim in card["claims"]
        ]
        card_record = KnowledgeRecord.create(
            record_type=RecordType.RESEARCH_CARD,
            title=f"Research Card: {paper['title']}",
            identity={"source_record_ids": [paper_record.record_id], "version": card["version"]},
            provenance=provenance,
            evidence_ids=evidence_ids,
            related_record_ids=(paper_record.record_id,),
            content={
                "summary": card["summary"],
                "research_question": card["research_question"],
                "method": card["method"],
                "findings": card["findings"],
                "limitations": card["limitations"],
                "assumptions": card["assumptions"],
                "counter_evidence": card["counter_evidence"],
                "claims": claims,
                "status": "extracted_not_validated",
            },
            recorded_at=recorded_at,
        )
        hypothesis_records = [
            KnowledgeRecord.create(
                record_type=RecordType.HYPOTHESIS,
                title=hypothesis["hypothesis_key"],
                identity={"hypothesis_key": hypothesis["hypothesis_key"], "version": hypothesis["version"]},
                provenance=provenance,
                evidence_ids=evidence_ids,
                related_record_ids=(paper_record.record_id, card_record.record_id),
                content={
                    **hypothesis,
                    "evidence_claims": [claim for claim in claims if claim["claim_id"] in hypothesis["claim_ids"]],
                    "status": "proposed_not_validated",
                },
                recorded_at=recorded_at,
            )
            for hypothesis in card["hypotheses"]
        ]
        formula_records = [
            KnowledgeRecord.create(
                record_type=RecordType.FORMULA,
                title=formula["formula_key"],
                identity={"formula_key": formula["formula_key"], "version": formula["version"]},
                provenance=provenance,
                evidence_ids=evidence_ids,
                related_record_ids=(paper_record.record_id, card_record.record_id),
                content={**formula, "status": "extracted_not_validated"},
                recorded_at=recorded_at,
            )
            for formula in card["formulas"]
        ]
        new_records = [*author_records, paper_record, card_record, *hypothesis_records, *formula_records]
        existing_by_id = {record.record_id: record for record in existing}
        conflicts = [
            record.record_id for record in new_records
            if record.record_id in existing_by_id
            and record.record_sha256 != existing_by_id[record.record_id].record_sha256
        ]
        if conflicts:
            return self._quarantine(
                raw=raw,
                manifest_sha256=manifest_sha256,
                source_sha256=source_sha256,
                reasons=[f"immutable record conflicts: {sorted(conflicts)}"],
                quarantine=quarantine,
                ledger=ledger,
            )

        retained_path = self._persist_object(Path(objects_root), source_bytes, source_sha256)
        if retained_path != object_path:
            raise AssertionError("object-store path contract mismatch")
        registration_hashes: list[str] = []
        try:
            for record in new_records:
                registration_hashes.append(register.append(record, ledger)["ledger_hash"])
        except RegisterIntegrityError as error:
            raise ResearchIntakeError(f"register write failed after preflight: {error}") from error
        intake_event = ledger.append(
            "research_intake_accepted",
            "athena.research_intake",
            {
                "policy_id": self.policy.policy_id,
                "paper_record_id": paper_record.record_id,
                "record_ids": [record.record_id for record in new_records],
                "source_sha256": source_sha256,
                "object_path": object_path,
            },
        )
        register_status = register.validate(ledger)
        return {
            "status": "ACCEPTED",
            "policy_id": self.policy.policy_id,
            "paper_record_id": paper_record.record_id,
            "record_ids": [record.record_id for record in new_records],
            "record_type_counts": register_status["record_type_counts"],
            "source_sha256": source_sha256,
            "object_path": object_path,
            "registration_ledger_hashes": registration_hashes,
            "ledger_hash": intake_event["hash"],
        }


def validate_intake_policy(path: str | Path) -> dict[str, Any]:
    policy = IntakePolicy.from_file(path)
    failures = policy.validate()
    if failures:
        raise ResearchIntakeError("; ".join(failures))
    required_card_fields = {
        "summary",
        "research_question",
        "method",
        "findings",
        "limitations",
        "assumptions",
        "counter_evidence",
        "claims",
        "hypotheses",
        "formulas",
    }
    if set(policy.required_research_card_fields) != required_card_fields:
        raise ResearchIntakeError("intake policy must preserve every required Research Card field")
    if policy.allowed_claim_relationships != {"supports", "contradicts", "context"}:
        raise ResearchIntakeError("intake policy claim relationships are not controlled")
    if not policy.require_hypothesis or not policy.require_formula or not policy.require_counter_evidence:
        raise ResearchIntakeError("intake policy cannot disable hypothesis, formula, or counter-evidence controls")
    return {
        "valid": True,
        "policy_id": policy.policy_id,
        "maximum_source_bytes": policy.maximum_source_bytes,
        "allowed_usage_rights": sorted(policy.allowed_usage_rights),
        "required_card_fields": len(policy.required_research_card_fields),
    }


def validate_intake_state(
    *,
    objects_root: str | Path,
    register: EvidenceRegister,
    quarantine: QuarantineRegister,
    ledger: EvidenceLedger,
) -> dict[str, Any]:
    register_status = register.validate(ledger)
    quarantine_status = quarantine.validate(ledger)
    records = register.records()
    by_id = {record.record_id: record for record in records}
    objects_verified = 0
    claims_linked = 0
    for record in records:
        if record.record_type == RecordType.PAPER:
            source_object = record.content.get("source_object", {})
            object_path = Path(objects_root) / str(source_object.get("path", ""))
            if not object_path.is_file():
                raise ResearchIntakeError(f"{record.record_id}: retained source object is missing")
            try:
                source_bytes = object_path.read_bytes()
                source_bytes.decode("utf-8")
                actual_sha = sha256_bytes(source_bytes)
            except UnicodeDecodeError as error:
                raise ResearchIntakeError(f"{record.record_id}: retained source is not UTF-8") from error
            if actual_sha != source_object.get("sha256") or actual_sha != record.provenance.source_sha256:
                raise ResearchIntakeError(f"{record.record_id}: retained source digest mismatch")
            author_ids = set(record.content.get("author_record_ids", []))
            if not author_ids or author_ids != set(record.related_record_ids):
                raise ResearchIntakeError(f"{record.record_id}: author relationships are incomplete")
            if any(by_id[item].record_type != RecordType.AUTHOR for item in author_ids):
                raise ResearchIntakeError(f"{record.record_id}: non-author relationship in author_record_ids")
            objects_verified += 1
        if record.record_type == RecordType.RESEARCH_CARD:
            claims = record.content.get("claims", [])
            for claim in claims:
                evidence_record_ids = claim.get("evidence_record_ids", [])
                if not evidence_record_ids:
                    raise ResearchIntakeError(f"{record.record_id}: unsupported claim {claim.get('claim_id')}")
                if any(item not in by_id or by_id[item].record_type != RecordType.PAPER for item in evidence_record_ids):
                    raise ResearchIntakeError(f"{record.record_id}: invalid claim evidence relationship")
                claims_linked += 1
    return {
        "valid": True,
        "register": register_status,
        "quarantine": quarantine_status,
        "objects_verified": objects_verified,
        "claims_linked": claims_linked,
    }
