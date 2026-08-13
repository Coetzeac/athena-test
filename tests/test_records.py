import json
import tempfile
import unittest
from pathlib import Path

from athena.evidence import EvidenceLedger, canonical_json, sha256_text
from athena.records import (
    DatasetFingerprint,
    EvidenceRegister,
    KnowledgeRecord,
    Provenance,
    RecordType,
    RecordValidationError,
    RegisterIntegrityError,
    stable_record_id,
    validate_record_contract,
)


ROOT = Path(__file__).resolve().parents[1]
OBSERVED_AT = "2026-08-13T00:00:00+00:00"


def provenance() -> Provenance:
    return Provenance(
        source_type="synthetic_fixture",
        source_locator="memory://fixture",
        source_sha256="a" * 64,
        observed_at=OBSERVED_AT,
        acquisition_method="unit_test",
        usage_rights="synthetic-test-only",
        evidence_ids=("E-TEST-001",),
    )


def hypothesis_record(content: dict[str, str]) -> KnowledgeRecord:
    return KnowledgeRecord.create(
        record_type=RecordType.HYPOTHESIS,
        title="Test hypothesis",
        identity={"hypothesis_key": "TEST-HYPOTHESIS", "version": 1},
        provenance=provenance(),
        evidence_ids=("E-TEST-001",),
        related_record_ids=(),
        content=content,
    )


class RecordContractTests(unittest.TestCase):
    def test_stable_ids_are_canonical_and_type_scoped(self) -> None:
        first = stable_record_id(RecordType.HYPOTHESIS, {"hypothesis_key": "H-1", "version": 1})
        reordered = stable_record_id(RecordType.HYPOTHESIS, {"version": 1, "hypothesis_key": "H-1"})
        different_type = stable_record_id(RecordType.FACTOR, {"factor_key": "H-1", "version": 1})
        self.assertEqual(first, reordered)
        self.assertNotEqual(first, different_type)
        self.assertRegex(first, r"^ATH-HYP-[0-9A-F]{24}$")

    def test_dataset_fingerprint_detects_manifest_tampering(self) -> None:
        fingerprint = DatasetFingerprint.create(
            dataset_name="Synthetic outcomes",
            source="synthetic_fixture",
            source_locator="memory://outcomes",
            content_sha256="c" * 64,
            extraction_config_sha256="d" * 64,
            row_count=3,
            fields=("outcome_r",),
            universe=("TEST",),
            timeframe="5m",
            acquired_at=OBSERVED_AT,
        )
        raw = fingerprint.to_dict()
        raw["row_count"] = 4
        tampered = DatasetFingerprint.from_dict(raw)
        self.assertIn(
            "dataset.fingerprint_sha256 does not match its canonical manifest",
            tampered.validate(),
        )
        with self.assertRaises(RecordValidationError):
            KnowledgeRecord.create(
                record_type=RecordType.DATASET,
                title="Tampered dataset",
                identity={"fingerprint_sha256": raw["fingerprint_sha256"]},
                provenance=provenance(),
                evidence_ids=("E-TEST-001",),
                related_record_ids=(),
                content={"dataset_fingerprint": raw},
            )

    def test_register_is_idempotent_and_reconciles_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = EvidenceLedger(root / "ledger.jsonl", clock=lambda: OBSERVED_AT)
            register = EvidenceRegister(root / "register.jsonl")
            record = hypothesis_record({"claim": "test claim"})

            first = register.append(record, ledger)
            second = register.append(record, ledger)

            self.assertTrue(first["created"])
            self.assertFalse(second["created"])
            status = register.validate(ledger)
            self.assertEqual(status["entries"], 1)
            self.assertEqual(status["ledger_links"], 1)
            self.assertEqual(ledger.validate()["entries"], 1)

    def test_register_rejects_identity_reuse_and_record_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = EvidenceLedger(root / "ledger.jsonl", clock=lambda: OBSERVED_AT)
            register = EvidenceRegister(root / "register.jsonl")
            record = hypothesis_record({"claim": "test claim"})
            register.append(record, ledger)

            conflicting = hypothesis_record({"claim": "silently rewritten"})
            with self.assertRaises(RegisterIntegrityError):
                register.append(conflicting, ledger)

            raw = json.loads(register.path.read_text(encoding="utf-8"))
            raw["content"]["claim"] = "tampered"
            register.path.write_text(canonical_json(raw) + "\n", encoding="utf-8")
            with self.assertRaises(RegisterIntegrityError):
                register.validate(ledger)

    def test_register_rejects_unresolved_record_relationships(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = EvidenceLedger(root / "ledger.jsonl", clock=lambda: OBSERVED_AT)
            register = EvidenceRegister(root / "register.jsonl")
            missing_id = stable_record_id(
                RecordType.DATASET,
                {"fingerprint_sha256": "f" * 64},
            )
            strategy = KnowledgeRecord.create(
                record_type=RecordType.STRATEGY,
                title="Unresolved strategy",
                identity={"strategy_key": "S-2", "specification_sha256": sha256_text(canonical_json({"claim": "x"}))},
                provenance=provenance(),
                evidence_ids=("E-TEST-001",),
                related_record_ids=(missing_id,),
                content={"claim": "x"},
            )
            with self.assertRaises(RegisterIntegrityError):
                register.append(strategy, ledger)

    def test_record_contract_matches_executable_types_and_schemas(self) -> None:
        status = validate_record_contract(ROOT / "config" / "evidence_registers.json", ROOT)
        self.assertTrue(status["valid"])
        self.assertEqual(status["record_types"], 9)
        self.assertEqual(status["schemas"], 2)

    def test_non_finite_identity_is_rejected_by_canonical_json(self) -> None:
        with self.assertRaises(ValueError):
            stable_record_id(RecordType.HYPOTHESIS, {"hypothesis_key": "H-1", "version": float("nan")})


if __name__ == "__main__":
    unittest.main()
